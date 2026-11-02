# ADP × Japan Aquaculture 真实环境验证报告

**日期**：2026-07-10
**环境**：Linux / Python 3.12.3，真实阿里云 RDS MySQL（隔离 schema），真实 OpenAI 兼容 LLM Provider
**结论**：ADP 的 Sense → Understand → Decide → Act(模拟) → Observe → Learn 六阶段闭环，在**真实数据库 + 真实 LLM**下完整跑通一次，全程零写入生产表、零真实设备触发。这是本平台第一次脱离"内存/Fake"环境的验证。

本报告是 [`2026-07-10-persistent-scheduling-verification.md`](2026-07-10-persistent-scheduling-verification.md) 之后的又一份补充验证，覆盖的是"是否能接真实系统"这个此前从未验证过的维度。

---

## 1. 验证目标与安全边界

用户提出的验证目标：用真实 LLM + 真实数据库，跑一次完整 ADP 六阶段闭环，同时满足两条硬约束：

1. **不触发真实投喂设备**——Act 阶段必须保持模拟。
2. **不写生产业务表**——所有写入必须落在与生产隔离的 schema。

采用的方案：

- 复用生产库同一个云 MySQL 实例，但新建独立 schema `adp_test`，ADP 自身的表（`decision_runs`／`loop_runs`／`memory_*`）和从生产拷贝来的业务表快照（`*_snapshot`）都只落在这个 schema。
- 用 `snapshot_copy.py` 从生产 `japan_aquaculture` 库**只读**拷贝一个池塘（`POND_005`）最近 30 天的数据到 `adp_test`，验证时全程读的是快照表，不再连生产库。
- Act 阶段沿用 `aquaculture_feeding_loop` 示例里的模拟 `trigger_feeder` 工具，只记录调用参数，不发出任何网络请求。

## 2. 本轮改动

| 文件 | 改动 |
|---|---|
| `examples/aquaculture_live_validation/real_feeding_rate.py`（新增） | 从 `backend/services/feeding_rate_service.py` 移植的纯函数版投喂量计算表（`FEEDING_RATE_TABLE` + `calculate_daily_feed_amount`），不引入业务项目的重依赖 |
| `examples/aquaculture_live_validation/real_data_source.py`（新增） | `RealPondDataSource`：对隔离 schema 里的快照表做只读查询，含业务字符串 ID → 内部数字 ID 的解析 |
| `examples/aquaculture_live_validation/snapshot_copy.py`（新增） | 从生产库只读拷贝一个池塘 + 时间窗口到隔离 schema；`LiveValidationSettings` 从 `.env` 读取源库 DSN、池塘 ID、窗口天数 |
| `examples/aquaculture_live_validation/stages.py`（新增） | `LivePondSenseStage` / `LiveFeedingUnderstandStage`：读快照表 + 调用移植后的投喂量公式 |
| `examples/aquaculture_live_validation/loop.py`（新增） | 真实 Sense/Understand 与既有 Decide/Act(模拟)/Observe/Learn 拼装，固定用 SQL 持久化 |
| `examples/aquaculture_live_validation/run_live_demo.py`（新增） | CLI：跑一次真实 LLM 决策 + 终端人工审批 |
| `pyproject.toml` | `sql`/`dev` extras 新增 `aiomysql`；`dev` extras 新增 `httpx` |
| `.gitignore` | 新增 `.env`，避免真实密钥被提交 |
| `tests/test_config.py` | 修复因本机存在真实 `.env` 导致 `test_platform_settings_defaults` 假失败的测试隔离问题（`monkeypatch.chdir(tmp_path)`） |

### 过程中修复的 bug

`_ensure_schema_exists` 里用 `url.set(database=None)` 试图清空 DSN 里的库名以拿到"无库名"的管理连接，实测 SQLAlchemy 的 `URL.set()` 把显式传入的 `None` 当作"不修改"而不是"清空"，导致连接时仍尝试 `USE adp_test`，若该 schema 还不存在会直接报 `Unknown database`（先有蛋还是先有鸡的问题：建库连接本身失败了）。改为 `url.set(database="")` 后行为正确。已在 `snapshot_copy.py` 中修复。

## 3. 第一步：快照拷贝（只读生产库）

```bash
cd /home/gmm/srv/ADP
python -m examples.aquaculture_live_validation.snapshot_copy
```

**实际输出**：

```
[snapshot_copy] pond: db_id=5 pond_id='POND_005' name='5号池'
[snapshot_copy] window: last 30 days (cutoff=2026-06-10T07:52:08.420290)
[snapshot_copy] copied rows: sensor_readings=20823, shrimp_stats=49, feeder_logs=2245, mortality_reports=0
[snapshot_copy] done -- production schema was only read, never written to
```

生产库 `japan_aquaculture` 全程只执行 `SELECT`；隔离 schema `adp_test` 里新建/覆盖了 `ponds_snapshot`、`sensor_readings_snapshot`、`shrimp_stats_snapshot`、`feeder_logs_snapshot`、`mortality_reports_snapshot` 五张表。

## 4. 第二步：真实 LLM 决策 + 模拟执行

```bash
cd /home/gmm/srv/ADP
python -m examples.aquaculture_live_validation.run_live_demo
```

**实际输出（关键部分）**：

```
================================================================================
 ADP live validation -- real MySQL data (isolated schema) + real LLM
 Act stage is SIMULATED: no device will be contacted, no real feed dispensed.
 This process only ever talks to ADP_DB_DSN (the isolated validation schema).
================================================================================

[live-demo] trace_id=live-validation-20260710T075403, pond_db_id=5
[live-demo] running sense -> understand -> decide (real LLM call)...
[live-demo] status=waiting_approval
[live-demo] pond_id=POND_005
[live-demo] recommended action=feed amount_kg=2.1
[live-demo] LLM rationale: (真实 LLM 返回的多段文字说明，见下方"已知边界")
[live-demo] confidence=0.9

Approve this decision? ... [y/N]: y
[live-demo] status=completed
[live-demo] simulated feeder call: [{'pond_id': 'POND_005', 'amount_kg': 2.1}]
[live-demo] DecisionRun: status=success, next_trigger_at=2026-07-10 08:54:12+00:00
[live-demo] memory node payload: {'last_amount_kg': 2.1, 'last_outcome': 'as_expected'}
```

**流程验证**：

| 阶段 | 实现 | 结果 |
|---|---|---|
| Sense | `LivePondSenseStage` 读隔离 schema 快照表（真实数据） | 正确取回 `POND_005` 最新一条 shrimp_stats / sensor_readings |
| Understand | `LiveFeedingUnderstandStage` 调用移植后的 `calculate_daily_feed_amount` | 算出 `amount_kg=2.1`，与业务规则表一致 |
| Decide | `FeedingDecideStage` 调用真实 LLM Provider（`ADP_LLM_*` 配置） | 真实 HTTP 请求成功返回，`rationale` 为真实模型输出，`requires_approval=True` |
| （人工审批中断点） | `LoopEngine` 停在 `waiting_approval`，等待外部输入 | 中断/恢复机制正常 |
| Act | 模拟 `trigger_feeder` 工具 | 只记录调用参数到内存列表，**未发出任何网络请求或设备指令** |
| Observe / Learn | 沿用既有阶段 | `DecisionRun.status=success`，写入 `long_term_memory` 节点，仅写隔离 schema |

## 5. 已知边界（如实记录）

- ~~**LLM 未拿到真实感知数据作为上下文**~~ —— 已在后续迭代中改为 `LiveFeedingDecideStage`：把传感器 / 虾况 / `feed_calculation` 显式塞进 prompt（参考生产 `feeding_task_prompt.md` 的核心约束），真实 LLM 回复已能引用具体数值（见同日二次连跑）。
- ~~**只验证了单次闭环，未验证 `TriggerScanner` 定时再决策接真实数据**~~ —— 已支持 `--rounds N --interval 15 --auto-approve`：默认 15s 后再决策，同进程内用 `TriggerScanner.scan_once` 等到期后启动下一轮。
- **快照会随生产继续而过期**：同一份快照可反复跑多轮验证，无需重拷；需要更新业务数据时用 `snapshot_copy --mode refresh`（只 TRUNCATE `*_snapshot`，**不碰** `adp_*` 表）。
- **`resolve_pond_db_id` 只处理了"业务字符串 ID → 内部数字 ID"这一种映射**，未覆盖生产库里可能存在的其它 ID 编码方式。
- 依旧没有触发任何真实设备；Act 阶段的模拟工具与 `aquaculture_feeding_loop` 示例完全一致。
- 本验证脚手架不要求生产侧完整 JSON task schema / execution_plan / 份数换算（Act 仍是模拟）。

## 6. 复现步骤

```bash
cd /home/gmm/srv/ADP
# 1) 在 .env 中配置：
#    ADP_LIVE_SOURCE_DB_DSN=mysql+aiomysql://<ro-user>:<pw>@<生产host>:3306/<生产库名>
#    ADP_DB_DSN=mysql+aiomysql://<user>:<pw>@<同host>:3306/adp_test   # 隔离 schema，可不存在，脚本会自动建
#    ADP_LLM_BASE_URL=... / ADP_LLM_API_KEY=... / ADP_LLM_DEFAULT_MODEL=...

# 2) 只读拷贝一个池塘的最近数据到隔离 schema
python -m examples.aquaculture_live_validation.snapshot_copy

# 3) 真实 LLM 决策 + 终端审批（Act 阶段模拟，不触发设备）
python -m examples.aquaculture_live_validation.run_live_demo
```
