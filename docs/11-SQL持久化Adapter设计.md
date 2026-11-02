# 11. SQL 持久化 Adapter 设计

> 状态：设计草案（已确认范围：Memory + Decision Record + LoopRun，见 `docs/08` A1/A2）。
> 目的：让 Memory 的"可复用/自学习"能力和 Decision Record 的调度触发能力在进程重启后依然可用；同时让 Loop Engine 支持"进程重启后恢复 `waiting_approval` 的运行"。
> 关联：`docs/02-LoopEngine设计.md`、`docs/03-Memory设计.md`、`docs/04-DecisionRecord设计.md`、`docs/09-Config设计.md`

---

## 1. 技术选型

使用 **SQLAlchemy 2.0 异步引擎**（`create_async_engine` + `AsyncSession`）：

- 与现状各仓库使用的 SQLAlchemy（同步）风格接近，迁移经验可复用；
- 默认驱动用 `aiosqlite`（SQLite），保证平台在无外部数据库依赖时也能跑单测/演示；生产可替换为 `asyncmy`/`aiomysql` 等连到 MySQL，只需换 `database_url`，不改业务代码。
- 表结构定义采用 SQLAlchemy Core（`Table`/`MetaData`），不用 ORM `DeclarativeBase`，因为平台的数据结构本身是 dataclass（不是 SQLAlchemy 模型类），Core 更适合"dataclass <-> 行"的手动映射，避免维护两套模型定义。

## 2. 共享基础设施：`adp/db`

```python
# adp/db/engine.py
def create_engine_from_url(database_url: str) -> AsyncEngine: ...

# adp/db/session.py
def create_sessionmaker(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]: ...
```

- 各 SQL Adapter 通过构造函数接收一个 `AsyncEngine`（或 `async_sessionmaker`），不自己读取 `PlatformSettings`——延续 `docs/09` §5 "模块只接收依赖，不自己读全局配置"的原则。
- 提供一个共享的 `metadata = MetaData()`，所有 Adapter 的表定义都注册到同一个 `MetaData`，方便一次性 `metadata.create_all(engine)` 建表（供单测/演示使用；生产环境的 schema 迁移超出本轮范围，建议后续引入 Alembic）。

## 3. 序列化策略

平台的六阶段 dataclass（`Decision`/`ActResult`/`ObservationRecord`/`LearnSignal`）字段稳定、嵌套层次浅，适合手写 `to_dict`/`from_dict`：

```python
# adp/core/serialization.py
def decision_to_dict(d: Decision) -> dict: ...
def decision_from_dict(data: dict) -> Decision: ...
def act_result_to_dict(r: ActResult) -> dict: ...
def act_result_from_dict(data: dict) -> ActResult: ...
def observation_to_dict(o: ObservationRecord) -> dict: ...      # 内含 raw: ActResult，递归调用上面的函数
def observation_from_dict(data: dict) -> ObservationRecord: ...
def learn_signal_to_dict(s: LearnSignal) -> dict: ...
def learn_signal_from_dict(data: dict) -> LearnSignal: ...
```

这些函数放在 `adp.core`（而不是 `adp.observability`），因为它们是六阶段契约类型的序列化逻辑，`observability`/`core.adapters` 都会用到，符合依赖方向（大家都依赖 `core`，`core` 不反向依赖）。

`LoopRun.state`（`dict[str, Any]`，值可能是任意节点的输出，包括参考实现自定义的类型）**没有通用序列化方案**——这是一个已知限制（见 §6），本轮采用"最佳努力 JSON 序列化"：

```python
def best_effort_jsonable(value: Any) -> Any:
    """dataclass -> dict（递归处理 datetime/list/dict），
    否则尝试 json 直接序列化，都不行则退化为 repr(value) 字符串。
    解码后得到的是 dict/基本类型，不重建原始的dataclass类型——
    LoopRunStore 的持久化目的是"重启后知道有哪些运行、处于什么状态"，
    不是"精确还原每个节点的强类型输出"。
    """
```

## 4. 各 Adapter 设计

### 4.1 Memory（`adp/memory/adapters/sql.py`）

表结构：

```
short_term_entries(id PK autoincrement, session_id, role, content_json, occurred_at, trace_id)
memory_nodes(node_id PK, scope, master_topic, summary, payload_json, updated_at,
             UNIQUE(scope, master_topic))
```

```python
class SqlShortTermMemory:  # 实现 ShortTermMemory Protocol
    def __init__(self, sessionmaker: async_sessionmaker[AsyncSession]) -> None: ...
    async def append(self, entry: ShortTermEntry) -> None: ...
    async def load(self, session_id: str, limit: int = 50) -> list[ShortTermEntry]: ...
    async def clear(self, session_id: str) -> None: ...

class SqlLongTermMemory:  # 实现 LongTermMemory Protocol
    def __init__(self, sessionmaker, topic_resolver: TopicResolver | None = None) -> None: ...
    async def resolve_topic(self, candidate_topic: str, scope: str) -> str: ...
    async def get_or_create_node(self, master_topic: str, scope: str) -> MemoryNode: ...
    async def update_node(self, node_id: str, updates: dict[str, Any]) -> MemoryNode: ...
    async def search(self, query: str, scope: str, top_k: int = 5) -> list[MemoryNode]: ...
```

- `content`/`payload` 存为 JSON 列（SQLite/MySQL 均支持），复用现状"文本历史/元数据用 JSON 字段"的通用做法。
- `get_or_create_node` 使用 `(scope, master_topic)` 唯一约束 + `INSERT ... ON CONFLICT`（或先查后插的兜底实现，兼容不同数据库方言）避免并发创建重复节点。
- `search` 第一版仍是关键词匹配（`LIKE`），与 `docs/03` B4 的结论一致，不做语义检索。

### 4.2 Decision Record（`adp/observability/adapters/sql.py`）

表结构：

```
decision_runs(trace_id PK, loop_id, project_id, status, started_at, finished_at,
              decision_json, observation_json, learn_signal_json,
              next_trigger_at, error)
```

```python
class SqlDecisionRecorder:  # 实现 DecisionRecorder Protocol
    def __init__(self, sessionmaker) -> None: ...
    async def start(self, trace_id, loop_id, project_id) -> None: ...
    async def attach_decision(self, trace_id, decision: Decision) -> None: ...
    async def complete(self, trace_id, observation, learn_signal) -> None: ...
    async def fail(self, trace_id, error: str) -> None: ...
    async def get(self, trace_id) -> DecisionRun: ...
    async def list_pending_triggers(self, before: datetime) -> list[DecisionRun]: ...
```

- `decision_json`/`observation_json`/`learn_signal_json` 用 §3 的 `*_to_dict`/`*_from_dict` 转换，保持强类型往返（这三个字段的 dataclass 结构稳定，值得做精确序列化，区别于 `LoopRun.state` 的"最佳努力"策略）。
- `list_pending_triggers` 对应 SQL：`WHERE next_trigger_at IS NOT NULL AND next_trigger_at <= :before`，直接利用数据库索引，比 `InMemoryDecisionRecorder` 的线性扫描更适合生产规模。

### 4.3 LoopRunStore（`adp/core/adapters/sql.py`）

表结构：

```
loop_runs(trace_id PK, loop_id, project_id, status, current_node,
          started_at, error, state_json)
```

```python
class LoopRunStore(Protocol):
    async def save(self, run: LoopRun) -> None: ...
    async def load(self, trace_id: str) -> LoopRun | None: ...
    async def list_by_status(self, status: str) -> list[LoopRun]: ...
    async def delete(self, trace_id: str) -> None: ...

class SqlLoopRunStore:  # 实现 LoopRunStore
    def __init__(self, sessionmaker) -> None: ...
    ...  # upsert 语义：save() 对已存在的 trace_id 做更新，否则插入
```

`LoopEngine` 的变更（见 `docs/02-LoopEngine设计.md` 同步更新）：

- 构造函数新增可选参数 `store: LoopRunStore | None`；若提供，则在 `start()`/`resume()` 过程中每次 `run.status` 变化时调用 `store.save(run)`（写透，不做批量/异步刷盘优化）。
- 新增 `LoopEngine.rehydrate(store: LoopRunStore) -> None`：进程启动时调用，从 `store.list_by_status("waiting_approval")` 读回所有待审批的运行，重建内存态的 `self._runs`/`self._pending_node`，使得重启后仍可以对这些 `trace_id` 调用 `resume()`。
- `rehydrate` 不会、也不能重建"节点如何产生这些输出"的完整执行历史——它只保证"知道有哪些运行在等审批、等的是哪个节点"，`resume()` 的语义（把审批事件作为该节点的输出继续往下推）不受影响。

## 5. 数据库连接的获取方式

```python
from adp.config import PlatformSettings
from adp.db.engine import create_engine_from_url
from adp.db.session import create_sessionmaker

settings = PlatformSettings()
engine = create_engine_from_url(settings.database_url)
sessionmaker = create_sessionmaker(engine)

long_term_memory = SqlLongTermMemory(sessionmaker)
decision_recorder = SqlDecisionRecorder(sessionmaker)
loop_run_store = SqlLoopRunStore(sessionmaker)
```

三个 Adapter 共用同一个 `engine`/`sessionmaker`（同一个数据库连接池），这是"共用同一份数据库连接配置"这一确认结论的具体体现。

## 6. 已知限制（明确记录，不在本轮解决）

1. **`LoopRun.state` 的精确类型无法通用还原**：重启恢复后，`run.state` 里之前节点的输出会是 JSON 兼容的 dict/基本类型（或对无法序列化的字段退化为字符串），不是原始的 `Decision`/`Context` 等 dataclass 实例。这对"审批中断-恢复"场景足够（`resume()` 依赖的是节点名和状态，不是历史节点的强类型输出），但如果未来有节点逻辑依赖"读取更早期节点的强类型输出"，需要重新设计。
2. **无 schema 迁移工具**：本轮只提供 `metadata.create_all()`，生产环境的表结构演进（加字段、加索引）需要后续引入 Alembic 或等价工具。
3. **`get_or_create_node` 的并发安全**在不同数据库方言下的"upsert"实现细节未做穷尽测试（本轮以 SQLite 为主要验证目标）。

## 7. 待确认事项

- 是否需要为 `SqlLongTermMemory.search` 增加基于数据库的全文检索（如 SQLite FTS5 / MySQL FULLTEXT），当前用简单 `LIKE`，规模变大后可能需要升级。
- `SqlLoopRunStore` 的 `state_json` 字段随节点数增多可能变大，是否需要拆分为单独的"节点输出"表（每个节点一行）而不是整个 `state` 一个 JSON 字段，留待后续根据实际数据量决定。
