# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project status

This repository is currently **design-only**: it contains a database schema, ER diagram, sequence diagrams, and a
design-decision log for an experimental sensor-data management system. There is no application code yet
(`pyproject.toml` has no dependencies, `README.md` is empty). When asked to implement functionality, treat
`docs/` as the spec to build against, and follow the phased approach described in DD-13/DD-14 below rather than
building everything at once.

- Python `3.12` (see `.python-version`), package metadata in `pyproject.toml`, no dependencies declared yet.
- No build, lint, or test commands exist yet — there is nothing to run. Once code is added, this file should be
  updated with the actual commands.

## What this system is

A PostgreSQL-backed database for managing experimental sensor data (e.g. radar vs. reference/ground-truth
devices such as PSG) through its full lifecycle: raw recording → time-range segment with experimental
conditions → per-sensor formatted data → algorithm runs → evaluation metrics. Source data files themselves live
in external storage (BOX/S3); the DB stores only URIs and metadata.

## Documentation map

- `docs/design-decisions.md` (DD-01..DD-18) — the **why**. Read this before proposing schema changes; it
  records rejected alternatives and the reasoning, so a change should only be made if the recorded reasoning no
  longer holds.
- `docs/er.md` — the **what** (entity-relationship diagram, mermaid).
- `docs/experiment_db_ddl_v2.sql` — the authoritative, runnable schema (tables, constraints, triggers).
- `docs/sequence_diagrams.md` — the **workflows** (4 scenarios: session/raw-file registration, segment
  cutting + formatting, algorithm run + evaluation, master-data addition/review).

These four files must stay mutually consistent — a schema change implies updating `er.md`, the DDL, and
(if it affects reasoning) `design-decisions.md`.

## Core data model (see `docs/er.md` / DDL for full detail)

The lifecycle is a chain of 1:N relations, deliberately *not* a single wide table (DD-01):

```
recording_sessions  (a day's continuous recording; setup jsonb, no experimental conditions)
  └─ raw_files      (one row per CSV; sensor_type, seq_no, started_at/ended_at auto-extracted from file contents)
  └─ segments       (a cut-out time range; THIS is where experimental conditions (jsonb) attach — DD-02)
       └─ formatted_data   (per-segment, per-sensor processed file; found via time-range overlap with raw_files)
       └─ algorithm_runs   (a processing attempt; segment_id is intentionally denormalized — DD-06)
            └─ run_inputs       (N:M — which formatted_data fed this run; same-segment enforced by trigger)
            └─ run_input_runs   (N:M — run-to-run dependency, e.g. an evaluation run depends on an estimation
                                  run + a ground-truth run; forms a DAG; same-segment enforced by trigger)
            └─ run_metrics      (scalar metrics only, e.g. mae/rmse, for cross-cutting SQL aggregation;
                                  non-scalar outputs like error time series stay in external storage)
```

Key conventions to preserve when extending this schema:

- **Existence = state** (DD-03): no status columns. Whether a segment has been formatted/processed is
  determined by whether rows exist in `formatted_data` / `algorithm_runs` (found via `LEFT JOIN ... IS NULL`).
  This is the basis for the reconciliation-loop automation model (DD-13).
- **Segments, not raw files, carry experimental conditions**; raw files can be long, session-spanning recordings
  (e.g. from a reference device) that get sliced into multiple segments purely by timestamp overlap
  (`tstzrange(started_at, ended_at) &&`), never by manual file-to-experiment assignment (DD-05, DD-17).
- **Reference/ground-truth devices are just another `sensor_type`**, and truth-derivation is just another
  `algorithm_runs` row (`algorithms.role = 'ground_truth'`); evaluation is likewise `role = 'evaluation'`, not a
  separate table (DD-16, DD-18).
- **Conditions are jsonb, controlled by a master vocabulary** (`condition_keys` / `condition_values`), not free
  text and not a fixed schema (DD-07, DD-08). `condition_keys.scope` (`session`/`segment`/`both`) determines
  which jsonb column (`recording_sessions.setup` vs `segments.conditions`) a key is valid for — there is one
  shared master, not two. `value_type` (`enum`/`enum_array`/`number`/`text`/`boolean`) drives validation; the
  variable-cardinality-subject case (0, 1, or 2+ subjects) uses `enum_array`, not a junction table (DD-15).
- **Master edits are additive + reviewed, never destructive**: new values are inserted immediately
  (`is_active=true`) with only a similarity check; deactivation is logical (`is_active=false`); wording
  duplicates are consolidated via `merged_into` pointing at the canonical value, never by deleting/rewriting
  history (DD-10).
- **Validation is deliberately doubled** — application layer (restrict what a user can even enter) and DB
  triggers (`trg_*` functions in the DDL) as the last line of defense against writes that bypass the app
  (DD-09). When adding a new constraint, consider whether it needs both layers or just one.
- **Surrogate PK + business-key UNIQUE** everywhere (e.g. `recording_sessions` has `session_id` PK but
  `(record_date, recorder_id, session_no)` UNIQUE) — foreign keys should always reference the surrogate key,
  never the business-key tuple (DD-12).
- **Segments are allowed to overlap in time** — there is no exclusion constraint, only bounds checks (DD-11).
  Code that aggregates over segments must treat multiple matches as expected and stratify by condition type
  rather than assuming disjoint time coverage.

## Implementation approach when writing code against this schema

Per DD-13/DD-14, future implementation should follow:

1. **Library-first**: implement operations as plain Python functions; CLI/GUI/automation workers are thin
   wrappers around the same library calls. Don't build a CLI-specific or GUI-specific code path.
2. **Walking skeleton**: get one thin, dummy-data version of the full pipeline (session → raw file → segment →
   formatted → algorithm run → search) working end-to-end before replacing any one stage with a real
   implementation. Formatting and algorithms should sit behind a registry (sensor type / algorithm name →
   implementation) so adding one doesn't touch the skeleton.
3. **Automation = reconciliation loop**, not message queues or file watchers: a periodic job that finds
   "segments missing formatted_data" or "formatted_data missing algorithm_runs" and calls the corresponding
   library function. This falls directly out of the existence-as-state convention above and is naturally
   idempotent.

Open decisions not yet resolved (see "未決事項" at the end of `docs/design-decisions.md`) include final storage
choice (BOX vs. S3), the initial `condition_keys` inventory, and promotion criteria for jsonb keys to generated
columns — check there before assuming a decision has already been made.
