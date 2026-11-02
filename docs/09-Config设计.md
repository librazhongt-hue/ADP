# 09. 分层配置设计

> 状态：设计草案（已确认技术选型，见 `docs/08` A5）。
> 目的：为平台各模块（LLM Gateway、SQL Adapter、Scheduler 等）提供统一的配置加载机制，同时允许参考实现挂载自己的业务配置段，共享同一份加载/分层逻辑。
> 关联：`docs/00` §3.2（配置管理范式）；`docs/05` §6

---

## 1. 定位

`adp/config` 提供：

1. **平台基础设施配置**：数据库连接串、LLM Provider 密钥/BaseURL、调度器轮询间隔等平台默认值。
2. **业务配置扩展机制**：参考实现可以定义自己的配置类（如 `FeedingSettings`），与平台配置共享同一套加载方式（环境变量 + `.env` 文件），但字段完全由参考实现定义——平台代码本身不包含任何业务字段。

`adp/config` **不**：
- 不管理运行时的"动态配置"（如 DB 里存的开关项），只管进程启动时确定的配置；
- 不做配置的远程分发/热更新（现状各仓库也都是 `.env` 静态加载，一致）。

---

## 2. 技术选型

采用 `pydantic-settings`（`BaseSettings`），原因：

- 与现状全部仓库（`agent_new/config.py`、`deviceagent/config/settings.py`、`backend/config/settings.py`）的配置范式一致，迁移/对照成本最低；
- 天然支持"环境变量 + `.env` 文件"分层来源，以及类型校验。

## 3. 平台配置（`PlatformSettings`）

```python
from pydantic_settings import BaseSettings, SettingsConfigDict


class PlatformSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="ADP_", env_file=".env", extra="ignore")

    database_url: str = "sqlite+aiosqlite:///./adp.db"

    llm_api_key: str = ""
    llm_base_url: str = "https://api.openai.com/v1"
    llm_default_model: str = "gpt-4o-mini"

    scheduler_poll_interval_seconds: float = 30.0
    decision_record_default_trigger_interval_seconds: float = 3600.0
```

- 所有字段读取形如 `ADP_DATABASE_URL`、`ADP_LLM_API_KEY` 的环境变量（`env_prefix="ADP_"`），避免与参考实现自己的环境变量（如现状 `AIJ_FEEDER_*`）冲突。
- `database_url` 默认指向本地 SQLite（通过 `aiosqlite` 异步驱动），保证平台在没有配置任何外部服务的情况下也能跑起来（单测/演示场景），生产可覆盖为 MySQL/PostgreSQL 连接串。

## 4. 业务配置扩展机制

参考实现通过**子类化** `PlatformSettings` 来扩展配置，而不是让平台维护一个"扩展点注册表"——这样业务字段的类型校验、默认值、`.env` 加载都直接复用 `pydantic-settings` 本身的机制，平台不需要额外做合并逻辑：

```python
# 参考实现（Aquaculture）自己的配置模块，不属于 adp 包
from adp.config import PlatformSettings


class AquacultureSettings(PlatformSettings):
    feeding_risk_default_interval_seconds: float = 3600.0
    yolo_api_url: str = "http://localhost:5005"
    device_agent_url: str = "http://localhost:5004"


settings = AquacultureSettings()  # 同时具备平台字段 + 业务字段，一次加载
```

- 因为 `env_prefix="ADP_"` 是在基类里定义的，子类默认继承同一前缀；若参考实现希望业务字段用不同前缀（如 `AQUA_`），可以在子类里重新声明 `model_config`（`pydantic-settings` 支持子类覆盖 `SettingsConfigDict`），平台不做限制。
- 这满足"预留业务配置扩展机制"的要求：参考实现只需要写一个继承类，平台代码全程不知道 `AquacultureSettings` 的存在。

## 5. 各模块如何消费配置

| 模块 | 消费方式 |
|---|---|
| `adp/db`（见 `docs/11`） | `create_engine_from_settings(settings.database_url)` |
| `adp/llm` | `OpenAICompatibleProvider(base_url=settings.llm_base_url, api_key=settings.llm_api_key, default_model=settings.llm_default_model)` |
| `adp/scheduling` | `AsyncScheduler` 的默认轮询间隔可从 `settings.scheduler_poll_interval_seconds` 传入（不强制，构造参数仍可显式覆盖） |
| `adp/observability` | `FixedIntervalTriggerPolicy(interval_seconds=settings.decision_record_default_trigger_interval_seconds)` |

各模块本身不直接 import `PlatformSettings` 并实例化（避免每个模块各自读一遍环境变量、产生不一致）；由调用方（参考实现的启动脚本，或本仓库 `examples/` 里的组装代码）统一实例化一次 `PlatformSettings`（或其业务子类），再把具体字段值传给各模块的构造函数——这与现状"模块只接收依赖，不自己读全局配置"的方向一致，也便于测试时传入固定值而不依赖环境变量。

## 6. 待确认事项

- 是否需要支持"多环境配置文件"（如 `.env.production`/`.env.test`），目前只支持单一 `.env`，与现状大部分仓库一致。
- `PlatformSettings` 的字段是否需要进一步拆分为嵌套子模型（如 `settings.llm.api_key` 而不是扁平的 `settings.llm_api_key`），本轮为了和 `pydantic-settings` 的扁平环境变量映射保持简单，暂用扁平结构。
