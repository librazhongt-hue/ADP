# ADP (Autonomous Decision Platform)

可复用的 AI 自主决策平台：Sense → Understand → Decide → Act → Observe → Learn（循环）。

日本陆上养殖项目（`japan-aquaculture-project` 及其关联仓库 `ai_japan` / `deviceagent` / `cognitive-center` / `japan_shrimp_detect`）是第一个参考实现（Reference Implementation），用于验证平台设计，而非平台本身。

## 当前状态

已完成现状架构分析、核心架构设计，搭建了 `core` / `memory` / `observability` / `llm` / `tools` / `scheduling` / `config` 七个模块的可运行骨架代码（含单测），Memory/Decision Record/LoopRun 三处均提供了 SQL 持久化 Adapter（默认仍是 InMemory，可插拔切换），并用一个模拟的 Aquaculture 喂食决策场景（`examples/aquaculture_feeding_loop`）验证了端到端集成——包括 SQL 持久化跨进程重启恢复、`AsyncScheduler` + `TriggerScanner` 自动再决策两条此前只有设计、未联调过的路径（见 `reports/` 下的验证报告）。此外还用真实阿里云 MySQL（隔离 schema + 生产数据只读快照）和真实 LLM Provider 跑通了一次完整六阶段闭环（`examples/aquaculture_live_validation`，Act 阶段保持模拟，全程零写生产表、零真实设备触发，见 `reports/2026-07-10-live-validation.md`）。

## 快速开始

```bash
cd ADP
pip install -e ".[dev]"
python -m pytest -q                                            # 全部测试
python -m examples.aquaculture_feeding_loop.run_demo             # 单进程 InMemory 端到端演示
python -m examples.aquaculture_feeding_loop.run_persistent_demo start   # 跨进程 SQL 持久化 + 定时再决策演示
python -m examples.aquaculture_feeding_loop.run_persistent_demo resume
python -m examples.aquaculture_feeding_loop.run_persistent_demo scan

# 接真实数据库 + 真实 LLM 的验证示例（需要在 .env 中配置真实凭据，见 examples/aquaculture_live_validation/README.md）
python -m examples.aquaculture_live_validation.snapshot_copy    # 只读拷贝一个池塘的生产数据到隔离 schema
python -m examples.aquaculture_live_validation.run_live_demo    # 真实 LLM 决策 + 终端审批，Act 阶段模拟

# ROS 机器人任务级决策验证（默认 dry-run，不控真车；见 examples/ros_robot_live_validation/README.md）
python -m examples.ros_robot_live_validation.run_demo --scenario a --utterance "去桌子" --auto-approve
python -m examples.ros_robot_live_validation.run_demo --scenario b --utterance "帮我找绿色瓶子" --auto-approve

# 车端最小迁移包（约几十 KB，不含 MySQL 养殖 live）
bash scripts/pack_ros_edge_bundle.sh   # → dist/adp-ros-edge-latest.tar.gz
```

## 文档索引

| 文档 | 内容 |
|---|---|
| [`docs/00-现状架构与平台化分析报告.md`](docs/00-现状架构与平台化分析报告.md) | 现状系统模块清单、业务/平台能力分类、迁移建议与平台边界定义 |
| [`docs/01-六阶段接口契约.md`](docs/01-六阶段接口契约.md) | Sense/Understand/Decide/Act/Observe/Learn 六阶段的最小数据结构与接口 Protocol |
| [`docs/02-LoopEngine设计.md`](docs/02-LoopEngine设计.md) | 决策图编排运行时：节点/路由/生命周期/插件注册机制/`LoopRunStore` 持久化 |
| [`docs/03-Memory设计.md`](docs/03-Memory设计.md) | 短期会话记忆 + 长期主题记忆的接口设计，含 SQL Adapter |
| [`docs/04-DecisionRecord设计.md`](docs/04-DecisionRecord设计.md) | 决策运行记录、下次触发策略、可观测性设计，含 SQL Adapter |
| [`docs/05-仓库目录结构规划.md`](docs/05-仓库目录结构规划.md) | ADP Python 包目录结构与模块依赖方向规划（已按实际代码更新） |
| [`docs/06-LLMGateway设计.md`](docs/06-LLMGateway设计.md) | LLM 统一调用接口、Provider 可插拔、PromptRegistry 设计 |
| [`docs/07-ToolFramework设计.md`](docs/07-ToolFramework设计.md) | 工具注册（静态/动态）与统一执行入口设计 |
| [`docs/08-待确认事项汇总.md`](docs/08-待确认事项汇总.md) | 全部设计文档中的待确认事项汇总 + 讨论结论记录 |
| [`docs/09-Config设计.md`](docs/09-Config设计.md) | 分层配置：平台基础设施配置 + 业务扩展子类化机制 |
| [`docs/10-Scheduling设计.md`](docs/10-Scheduling设计.md) | 通用调度器原语 + 与 Decision Record 联动的 `TriggerScanner` |

## 代码结构

```
adp/
├── core/            # 六阶段契约 + Loop Engine（中断/恢复、超时、重试、事件钩子、LoopRunStore 持久化）
├── memory/          # 短期/长期记忆（InMemory 默认 + SQL Adapter，长期记忆含可插拔 TopicResolver）
├── observability/   # 决策记录 + 下次触发策略（InMemory 默认 + SQL Adapter，可插拔 NextTriggerPolicy）
├── llm/             # LLM Gateway（可插拔 Provider + OpenAI 兼容默认实现）+ PromptRegistry
├── tools/           # 工具注册（静态/动态并存）+ 统一执行入口（超时/权限校验点）
├── scheduling/      # AsyncScheduler（进程内定时任务）+ TriggerScanner（联动 observability）
├── config/          # 分层配置（PlatformSettings，可被参考实现子类化扩展业务配置）
└── serialization.py # SQL Adapter 共用的 JSON 安全序列化工具

examples/
└── aquaculture_feeding_loop/   # 端到端集成示例：模拟养殖喂食决策场景，串联 core/memory/observability/llm/tools/scheduling
    ├── domain.py               # 简化的池塘数据与投喂率计算（非真实业务数值）
    ├── stages.py               # Sense/Understand/Decide/Act/Observe/Learn 具体实现
    ├── tools.py                # 模拟的喂食机工具（代表真实的 deviceagent 调用）
    ├── loop.py                 # 组装 LoopSpec + 连接 Loop Engine 事件钩子到 Decision Recorder；`PersistenceHandles` 可切换 InMemory/SQL
    ├── persistence.py          # 从 PlatformSettings 构建 SQL Engine + 建表
    ├── run_demo.py             # 单进程 InMemory 演示（含审批中断/恢复）
    └── run_persistent_demo.py  # 跨进程 SQL 持久化 + AsyncScheduler/TriggerScanner 定时再决策演示（start/resume/scan 三个子命令）

└── aquaculture_live_validation/  # 接真实数据库 + 真实 LLM 的验证示例，Act 阶段保持模拟
    ├── real_feeding_rate.py       # 从业务项目移植的纯函数投喂量计算表
    ├── real_data_source.py        # 隔离 schema 内快照表的只读查询
    ├── snapshot_copy.py           # 只读拷贝生产库一个池塘的近期数据到隔离 schema
    ├── stages.py                  # 真实 Sense/Understand 阶段
    ├── loop.py                    # 真实 Sense/Understand + 复用既有 Decide/Act(模拟)/Observe/Learn
    └── run_live_demo.py           # CLI：真实 LLM 决策 + 终端审批

└── ros_robot_live_validation/     # ROS 机器人场景 A(goto)/B(意图中枢)；默认 dry-run，--live 仅放开真机 goto
    ├── domain.py                  # 航点别名与 G5 启发式意图（不 import ros_ws）
    ├── ros_bridge.py              # dry-run / 可选 go_waypoint_test subprocess
    ├── stages.py / loop.py        # 六阶段装配
    └── run_demo.py                # CLI：--scenario a|b

tests/               # 单测 + examples 集成测试（含 ROS A/B dry-run）
reports/             # 阶段性验证报告（测试结果、演示输出、已知边界）
```

## 设计原则（摘要）

- ADP 只负责"闭环怎么转"（编排、契约、横切能力），不负责"闭环里装的是什么"（业务数据、业务算法、业务 Prompt）。
- 平台代码中不出现任何业务领域词汇（如"虾""池塘""喂食机"），业务逻辑全部作为插件由参考实现提供；`examples/` 目录本身也不是平台代码，只是验证集成路径的模拟场景。
- 优先抽取现状中复用信号最强、设计最成熟的模块：Loop Engine（被独立实现 3 次）、Memory（`hippocampus` 设计最完整）、Decision Record（Observe/Learn 阶段最具复用价值）、LLM Gateway 与工具框架（各被独立实现 2-3 次）。
- 持久化默认全部走可插拔 Protocol（`LoopRunStore`/`ShortTermMemory`/`LongTermMemory`/`DecisionRecorder`），InMemory 实现保证平台无需外部依赖即可运行/测试，SQL 实现按需切换，两者满足同一套接口。

## 已完成的设计与开发范围

- [x] 六阶段最小接口契约（设计文档 + 代码）
- [x] Loop Engine（设计文档 + 代码；支持静态边、动态路由、超时重试、节点事件钩子、可中断-可恢复、可插拔 `LoopRunStore`）
- [x] Memory（设计文档 + 代码，含可插拔 `TopicResolver` 与 SQL Adapter）
- [x] Decision Record（设计文档 + 代码，含可插拔 `NextTriggerPolicy` 与 SQL Adapter）
- [x] LLM Gateway（设计文档 + 代码：可插拔 Provider、OpenAI 兼容默认实现、PromptRegistry）
- [x] 工具调用框架（设计文档 + 代码：静态/动态注册并存、统一执行入口）
- [x] 分层配置 `adp/config`（设计文档 + 代码：平台基础设施配置 + 业务子类化扩展机制）
- [x] 通用调度器 `adp/scheduling`（设计文档 + 代码：`AsyncScheduler` + `TriggerScanner`）
- [x] Memory / Decision Record / LoopRun 三处的 SQL 持久化 Adapter（SQLAlchemy async + SQLite，可换成真实 MySQL/Postgres DSN）
- [x] 仓库目录结构规划（已按实际代码同步更新）
- [x] 全部设计文档中的"待确认事项"已收敛为 `docs/08-待确认事项汇总.md` 并完成讨论/确认
- [x] 端到端集成示例：`examples/aquaculture_feeding_loop`，验证核心模块协同工作（含审批中断/恢复演示）
- [x] 示例接入 SQL 持久化 + `AsyncScheduler`/`TriggerScanner`：`run_persistent_demo.py` 用三个真实独立进程（而非同进程模拟）证明状态确实经数据库往返、到期决策确实被自动重新送回 Loop Engine（见 `reports/`）
- [x] `examples/aquaculture_live_validation/`：接真实 Aquaculture MySQL 数据（隔离 schema，快照拷贝，不动生产表）+ 真实 LLM，Act 阶段保持模拟；已用真实凭据实际运行一次完整六阶段闭环并产出报告（见 `reports/2026-07-10-live-validation.md`）
- [x] `examples/ros_robot_live_validation/`：ROS 场景 A（定点导航决策）+ B（G5 意图中枢路由）；默认 dry-run + 物理意图审批；`--live` 仅放开真机 goto；场景 C（VLM 寻物）延后

## 后续规划（待讨论确认后再启动）

1. **A3（明确留待后续专题）**：Act 执行网关、会话/状态管理、WebSocket 通信协议三项的模块归属仍是开放问题。
2. 评估分布式部署场景：多实例部署下 `TriggerScanner` 可能重复扫到同一条到期记录并重复触发；当前 `run_persistent_demo.py::cmd_scan` 只做了单进程内的去重（内存 set），跨进程/多实例去重仍未解决。
3. 视情况将 `examples/aquaculture_feeding_loop` 的经验反馈到 Aquaculture 参考实现的真实接入工作中（真实数据库、真实 LLM Key、真实设备调用）。
4. 长期记忆语义检索（`VectorLongTermMemory`）作为后续迭代项，当前 `search` 仅做关键词/子串匹配。
