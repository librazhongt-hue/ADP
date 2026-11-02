# Example: Live Validation Against Real Data + Real LLM

Validates the ADP platform (`adp.core`, `adp.memory`, `adp.observability`,
`adp.llm`, `adp.tools`) against real Aquaculture production data and a real
LLM, while keeping the Act stage simulated and never touching production
tables.

This is a *validation scaffold*, not a template for a production
integration and not part of the `adp` package.

**Status: actually run against real production data (read-only) and a
real LLM.** See [`reports/2026-07-10-live-validation.md`](../../reports/2026-07-10-live-validation.md).

## Safety model (read this before running anything)

1. **Production is only ever read, never written**, and only by
   `snapshot_copy.py`. Every query against the production schema in that
   file is a hand-written `SELECT`; there is no `INSERT`/`UPDATE`/`DELETE`
   anywhere that touches it.
2. **Everything else only talks to an isolated schema** (`ADP_DB_DSN`), which
   holds two kinds of tables: `*_snapshot` tables (a copy of a few real
   business tables, filtered to exactly one pond and a recent time window)
   and ADP's own tables (`adp_loop_runs` / `adp_decision_runs` /
   `adp_short_term_entries` / `adp_memory_nodes`). No foreign keys back to
   production, no shared table names with production. The schema is created
   automatically on first `snapshot_copy` run (`CREATE DATABASE IF NOT
   EXISTS`) -- you can inspect it in the cloud RDS console under the
   database name in `ADP_DB_DSN` (currently `adp_test`).
3. **The Act stage is simulated.** `FeederActStage` (reused unchanged from
   `examples/aquaculture_feeding_loop/stages.py`) calls the same simulated
   `trigger_feeder` tool -- it never calls `deviceagent`, never reaches the
   real feeder's cloud API. `run_live_demo.py` prints a banner confirming
   this every time it runs.
4. **The LLM call is real.** It uses whatever `ADP_LLM_*` you configure --
   this does spend real API credits. Decide now injects real Sense/Understand
   facts into the prompt (adapted from production `feeding_task_prompt.md`);
   the numeric feed amount remains deterministic.

## Setup

Two databases are involved, both configured in `.env` at the ADP repo root
(already `.gitignore`d):

```bash
# Production MySQL -- read-only usage, only touched by snapshot_copy.py
ADP_LIVE_SOURCE_DB_DSN=mysql+aiomysql://<user>:<password>@<host>:3306/<production_schema>

# Isolated validation schema -- created automatically if it doesn't exist yet.
ADP_DB_DSN=mysql+aiomysql://<user>:<password>@<host>:3306/<validation_schema>

# Real LLM (OpenAI-compatible)
ADP_LLM_BASE_URL=<real base_url>
ADP_LLM_API_KEY=<real api key>
ADP_LLM_DEFAULT_MODEL=<real model name>

# Optional
ADP_LIVE_POND_ID=          # business pond_id to copy/validate; auto-picked if unset
ADP_LIVE_WINDOW_DAYS=30    # history window copied for sensor/shrimp/feeder tables
```

The account needs `CREATE DATABASE` privilege (to create the validation
schema on first run) plus `SELECT` on the production tables and full DML on
the validation schema.

## Run it

```bash
cd ADP
# step 1: copy one pond's recent data (read-only on prod). Use --mode refresh
# for later multi-round refreshes -- it TRUNCATEs *_snapshot only and never
# touches adp_* tables, so prior DecisionRun / Memory rows stay for comparison.
python -m examples.aquaculture_live_validation.snapshot_copy
python -m examples.aquaculture_live_validation.snapshot_copy --mode refresh

# step 2: real Sense/Understand/Decide, simulated Act.
# Default next_trigger_at is 15s after completion so you can validate
# TriggerScanner re-decision without waiting an hour.
python -m examples.aquaculture_live_validation.run_live_demo
python -m examples.aquaculture_live_validation.run_live_demo --rounds 2 --auto-approve
```

## Inspect the isolated schema in cloud MySQL

```sql
SHOW DATABASES LIKE 'adp_test';
USE adp_test;
SHOW TABLES;
SELECT COUNT(*) FROM sensor_readings_snapshot;
SELECT * FROM adp_decision_runs ORDER BY started_at DESC LIMIT 5;
SELECT topic, payload FROM adp_memory_nodes;
```

## What real business logic is reused here (and what isn't)

| File | Real logic |
|---|---|
| `real_feeding_rate.py` | `FEEDING_RATE_TABLE` and the biomass/feeding-rate formula, ported from `backend/services/feeding_rate_service.py` |
| `real_data_source.py` | Reads the same shape of data `build_pond_context` reads, from the copied snapshot tables |
| `stages.py` (Decide) | Prompt shape adapted from `feeding_task_prompt.md`: real facts in, Chinese rationale out; amount still from `feed_calculation` |
| Act/Observe/Learn | Reused from `examples/aquaculture_feeding_loop` -- Act remains a *simulated* feeder tool |

Not reused: the real Flask app, `WorkTaskService`/`deviceagent` device dispatch,
`decision_recordings`/`work_tasks` tables (ADP writes its own tables instead).

## Known limitations

- Snapshot data goes stale as production continues; re-run
  `snapshot_copy --mode refresh` to pull a fresh window. Same-snapshot
  multi-round validation does **not** require re-copying.
- `cumulative_mortality` is copied in full (not windowed) so it matches
  production's cumulative semantics.
- Same reject-handling limitation as elsewhere in ADP: rejecting a decision
  here just leaves the `LoopRun` in `waiting_approval`.
- Production JSON task schema / execution_plan / portion conversion are not
  required in this validation scaffold (Act is simulated).
