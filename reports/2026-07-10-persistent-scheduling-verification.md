# ADP 持久化 + 调度联调验证报告

**日期**：2026-07-10
**环境**：Linux / Python 3.12.3
**结论**：47 项单测全部通过（较上一份报告新增 3 项）；`run_persistent_demo.py` 的 `start` / `resume` / `scan` 三个子命令作为三个独立 OS 进程串联跑通，验证了 SQL 持久化跨进程恢复与 `TriggerScanner` 自动再决策两条此前"设计完成但从未联调"的路径。

本报告是 [`2026-07-10-verification.md`](2026-07-10-verification.md) 的后续，专门补齐该报告第4节"已知边界"里列出的第一条：*"示例未接入 `PlatformSettings` / SQL Adapter / `TriggerScanner` 定时再决策"*。

---

## 1. 本轮改动

| 文件 | 改动 |
|---|---|
| `examples/aquaculture_feeding_loop/loop.py` | `build_feeding_loop_environment` 新增 `persistence: PersistenceHandles \| None` 参数：不传保持原有 InMemory 行为不变；传入后 `Memory`/`DecisionRecorder`/`LoopRunStore` 全部换成 SQL 实现，其余装配代码零改动 |
| `examples/aquaculture_feeding_loop/persistence.py`（新增） | `build_sql_engine(settings)`：从 `PlatformSettings` 读取 DSN，创建 Engine 并调用三个模块各自的 `ensure_tables` |
| `examples/aquaculture_feeding_loop/run_persistent_demo.py`（新增） | `start` / `resume` / `scan` 三个子命令，各自独立进程运行，靠同一个 SQLite 文件传递状态；`scan` 内部真实接入 `AsyncScheduler` + `TriggerScanner` |
| `tests/test_examples_persistent_feeding_loop.py`（新增） | 3 个测试：跨实例持久化验证、`TriggerScanner` 触发验证、真实子进程端到端验证 |

## 2. 测试总览

| 指标 | 结果 |
|---|---|
| 测试总数 | 47（原 44 + 新增 3） |
| 通过 | 47 |
| 失败 | 0 |
| 耗时 | ~6s（含 1 个启动真实子进程的慢测试） |

新增的 3 个测试：

| 测试 | 验证要点 |
|---|---|
| `test_sql_persistence_survives_across_fresh_instances` | 用两个完全独立构建的 `FeedingLoopEnvironment`（只共享同一个 SQLite Engine）模拟"跨进程"：第一个实例跑到 `waiting_approval` 并退出，第二个实例从数据库读出待审批的 `Decision` 并完成审批/执行/学习全流程 |
| `test_trigger_scanner_picks_up_due_decision_and_starts_new_loop` | 验证 `next_trigger_at` 未到期时 `TriggerScanner.scan_once()` 返回空列表；到期后返回该记录并触发 `on_due`，回调用一个全新的 `trace_id` 重新调用 `LoopEngine.start(...)`，新一轮循环同样正确停在 `waiting_approval` |
| `test_run_persistent_demo_end_to_end_across_real_subprocesses`（标记 `slow`） | 唯一真正启动 3 个独立操作系统进程（`subprocess.run`）的测试，逐一执行 `start` → `resume` → `scan`，断言每一步的 stdout 里出现预期状态，是对"跨进程"这个卖点的最终证明 |

## 3. 端到端演示（真实进程）

**命令**（三次独立的 `python -m` 调用，之间无任何共享 Python 对象）：

```bash
cd /home/gmm/srv/ADP
rm -f adp_persistent_demo.db
python -m examples.aquaculture_feeding_loop.run_persistent_demo start
python -m examples.aquaculture_feeding_loop.run_persistent_demo resume
sleep 6
python -m examples.aquaculture_feeding_loop.run_persistent_demo scan
```

**实际输出**：

```
[start] status=waiting_approval (persisted to the DB; this process now exits)
[start] pending decision: action=feed params={'pond_id': 'pond-01', 'amount_kg': 4.8}
[start] rationale: [simulated rationale] Pond pond-01: recommend feeding 4.8kg given current conditions.
[start] run: python -m examples.aquaculture_feeding_loop.run_persistent_demo resume
```

```
[resume] status=completed (this is a brand-new process/objects; the approval came from the DB)
[resume] DecisionRun: status=success, next_trigger_at=2026-07-10 06:43:44.184719+00:00
[resume] feeder calls made (this process): [{'pond_id': 'pond-01', 'amount_kg': 4.8}]
[resume] memory node payload (loaded from DB): {'last_amount_kg': 4.8, 'last_outcome': 'as_expected'}
[resume] wait a few seconds for next_trigger_at to elapse, then run:
[resume]   python -m examples.aquaculture_feeding_loop.run_persistent_demo scan
```

```
[scan] due: trace_id=persistent-demo-1 next_trigger_at=2026-07-10 06:43:44.184719+00:00
[scan] auto-triggered new loop run trace_id=persistent-demo-1-rescan status=waiting_approval
[scan] new pending decision: action=feed params={'pond_id': 'pond-01', 'amount_kg': 4.8}
[scan] (still requires approval -- the scheduler starts new loops, it doesn't auto-approve them)
```

**流程验证**：

| 阶段 | 结果 |
|---|---|
| 进程A `start`：Sense→Understand→Decide | 正常完成，停在 `waiting_approval`，状态写入 SQLite；进程退出 |
| 进程B `resume`：从数据库读出待审批 Decision → 审批 → Resume | 全新 Python 进程/对象，仅凭数据库里的记录就完整恢复并跑完 Act→Observe→Learn |
| Decision Record | `status=success`，`next_trigger_at` 正确写入并可被后续进程读到 |
| Long-term Memory | 更新 `feeding_history` 节点，且能被进程B独立读到（不依赖进程A的内存对象） |
| 进程C `scan`：`AsyncScheduler` 轮询 → `TriggerScanner.scan_once()` → `DecisionRecorder.list_pending_triggers()` | 正确识别到期记录，调用 `on_due` 回调 |
| `on_due` 回调 | 用新 `trace_id` 重新调用 `LoopEngine.start(...)`，新一轮循环正确执行到 `waiting_approval`（符合预期：调度器负责"发起"新一轮决策，不负责"批准"它） |

## 4. 已知边界（本轮仍未覆盖，如实记录）

- `scan` 子命令对"同一到期记录被多次轮询重复捞出"的去重，只在单进程内用一个内存 `set` 处理；跨进程/多实例部署下仍会重复触发（`docs/10-Scheduling设计.md` §5 中已明确这是留给接入方的开放问题，本轮没有解决它，只是把"接入方需要自己做"这件事在示例里做了一次示范）。
- 到期后原始 `DecisionRun` 的 `next_trigger_at` 字段没有被清空/消费，理论上如果 `scan` 换一个全新进程再跑一次，仍会认为它"到期"并再触发一次同名 `-rescan` trace_id（此时会因为主键冲突而报错）。真实接入需要设计"消费"语义（例如触发后把 `next_trigger_at` 置空，或改成"下一次" `next_trigger_at`），这部分留给 Aquaculture 真实接入时处理，不在 ADP 平台职责内（观察者模式下"何时算处理完"是业务语义）。
- 依旧没有调用真实 LLM API、真实 deviceagent 设备；`FakeAquacultureLLMProvider` 与模拟 `trigger_feeder` 工具不变。
- A3（Act 执行网关、会话管理、WebSocket 协议）依旧未设计，不在本轮范围。

## 5. 复现步骤

```bash
cd /home/gmm/srv/ADP
python -m pytest -q                                                     # 全部 47 个测试
python -m pytest -q tests/test_examples_persistent_feeding_loop.py      # 只跑本轮新增的 3 个
rm -f adp_persistent_demo.db
python -m examples.aquaculture_feeding_loop.run_persistent_demo start --interval 2
python -m examples.aquaculture_feeding_loop.run_persistent_demo resume --interval 2
sleep 3
python -m examples.aquaculture_feeding_loop.run_persistent_demo scan --poll-interval 1
```
