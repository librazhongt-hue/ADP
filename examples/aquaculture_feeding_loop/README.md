# Example: Aquaculture Feeding Decision Loop

A minimal, runnable, offline demonstration that wires the platform modules
built so far (`adp.core`, `adp.memory`, `adp.observability`, `adp.llm`,
`adp.tools`) into one end-to-end Sense -> Understand -> Decide -> Act ->
Observe -> Learn loop, using a *simplified simulation* of the real feeding
decision flow in `japan-aquaculture-project/backend`.

This directory is a reference-implementation example, not part of the `adp`
package that would ship to other projects. Nothing here is imported by
`adp/*`.

## Run it

```bash
cd ADP
python -m examples.aquaculture_feeding_loop.run_demo
```

This uses the InMemory `Memory`/`DecisionRecorder`/`LoopRunStore` -- state
lives only in that one process and is gone when it exits.

## Run it with real persistence + scheduling

`run_persistent_demo.py` demonstrates the other half of the platform:
SQL-backed persistence (`adp.core.adapters.sql` / `adp.memory.adapters.sql` /
`adp.observability.adapters.sql`) and the `AsyncScheduler` +
`TriggerScanner` combo, by splitting the same flow across three *separate*
OS processes that only share a SQLite file:

```bash
cd ADP
python -m examples.aquaculture_feeding_loop.run_persistent_demo start   # pauses for approval, then exits
python -m examples.aquaculture_feeding_loop.run_persistent_demo resume  # a new process resumes from the DB, completes the loop
# wait a few seconds for next_trigger_at to elapse
python -m examples.aquaculture_feeding_loop.run_persistent_demo scan    # TriggerScanner finds the due decision, auto-starts a new loop run
```

State (the paused `LoopRun`, the `DecisionRun`, the long-term memory node)
round-trips through `./adp_persistent_demo.db` between invocations -- proving
persistence actually works, not just that the Protocol methods can be
called. Delete that file to start over. See `persistence.py` for how the
SQLite engine is built from `adp.config.PlatformSettings`.

## What it demonstrates

| Platform module | Where it is exercised |
|---|---|
| `adp.core` (Loop Engine, six-stage contracts) | `loop.py` builds a `LoopSpec` via `LoopRegistry` and runs it via `LoopEngine`; the `decide` node is registered in `interrupts`, so the run pauses in `waiting_approval` status until `run_demo.py` calls `engine.resume(...)` with an approved `Decision` -- exercising the interrupt/resume path described in `docs/02-LoopEngine设计.md` §5. |
| `adp.llm` | `stages.py::FeedingDecideStage` calls `LLMGateway.complete(...)` (with an in-repo `FakeAquacultureLLMProvider`, so the example needs no API key) to generate a rationale string, while the actual feed amount stays a deterministic calculation (`domain.py::calc_recommended_feed_kg`) -- mirroring the real split between LLM-authored wording and `FeedingRateService`'s deterministic number. |
| `adp.tools` | `tools.py` registers a `trigger_feeder` tool; `stages.py::FeederActStage` executes it via `ToolExecutor`, standing in for the real HTTP call to `deviceagent`. |
| `adp.observability` | `loop.py` wires `LoopEngine.on_node_start` / `on_node_end` / `on_loop_complete` hooks to an `InMemoryDecisionRecorder`, so every run automatically produces a `DecisionRun` without the stage implementations themselves calling the recorder. |
| `adp.memory` | `stages.py::FeedingLearnStage` writes a long-term memory node per pond (`pond:<id>:feeding_history`) via `InMemoryLongTermMemory`, standing in for the real `hippocampus`-style memory update. |
| `adp.scheduling` + SQL adapters | `run_persistent_demo.py`'s `scan` subcommand wires `AsyncScheduler` + `TriggerScanner` against a `SqlDecisionRecorder`, and `loop.py::PersistenceHandles` swaps every InMemory component for its SQL-backed equivalent -- see "Run it with real persistence + scheduling" above. |

## Mapping to the real system (for context, not a 1:1 port)

| Example file | Real-system counterpart |
|---|---|
| `domain.py::PondRepository.fetch_snapshot` | `backend/scripts/ai_feeding_task_generator.py::build_pond_context` |
| `domain.py::calc_recommended_feed_kg` | `backend/services/feeding_rate_service.py` |
| `stages.py::FeedingDecideStage` | LLM call inside `ai_feeding_task_generator.py::generate_tasks_from_context` |
| `tools.py::trigger_feeder` | `backend/services/work_task_service.py` calling `deviceagent`'s `/api/v1/chat` |
| `loop.py` Decision Recorder wiring | `backend/services/decision_recording_service.py` |
| `stages.py::FeedingLearnStage` next-trigger computation | `parse_and_clamp_next_decision` / `FeedingTaskSchedulerService` |

Numbers, thresholds, and prompts here are illustrative only -- see
`docs/00-现状架构与平台化分析报告.md` §3.1 for why real business values stay out
of the platform (and, by extension, out of this example too).
