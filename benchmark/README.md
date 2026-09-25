# Intelligence benchmark

Labeled cases that gate every prompt, model, graph or threshold change
(architecture section 17). The **120-case critical gate** (P8, ADR 0008) is the release gate;
the two earlier sets below are part of it (read from their original files, unchanged).

- `schema/` - JSON Schemas: `case.schema.json` (input turn + course) and
  `label.schema.json` (expected routes, segments, mapped skills, abstention)
- `cases/` - input interactions (real or synthetic, no personal data), JSON Lines
- `labels/` - expected outcomes, one per case, same order
- `runners/` - scripts that score the pipeline against the labels
- `fixtures/` - the frozen fixture course graphs of the critical gate (local / CI database only)
- `recordings/` - recorded provider responses of a live run (replayed in CI)
- `baselines/` - per-model baseline of the committed recording (prompt versions, metrics)

## Critical gate (`critical-gate`, 120 cases; P8)

`cases/critical-gate/<FAMILY>.jsonl`, run by `runners/critical_gate.py` (+ `gate_db.py`):

| Family | Cases | Live-capable | Kind |
| --- | --- | --- | --- |
| REL relevance / skill-bearing | 14 | 14 | turn |
| SEG segmentation | 8 | 8 | turn |
| MAP mapping (retrieval, gate, adjudication, candidates, hierarchy) | 16 | 16 | turn |
| ATT attribution (actor, evidence type, copy guard, consistency) | 16 | 11 | turn + evidence |
| EVM evidence / mastery safety | 10 | 0 | evidence |
| DEBT AI Assistance Debt | 18 | 2 | evidence + turn |
| ABS abstention | 10 | 3 | turn + evidence |
| ADV adversarial / injection (each with a compromised twin) | 10 | 10 | turn |
| VER verification generation + validation | 10 | 5 | verification |
| GRD grading | 8 | 3 | grading |

- **turn** cases run the production pipeline on a local database: the fixture graphs (6 courses,
  42 skills, uuid5 ids), ingestion as the `chatgpt-2` adapter, P3A, P3B, P4 and P5, then the
  activity feed and the ledger are read back. Every job is run a second time: 0 provider requests,
  no new row.
- **evidence** cases script attribution outputs through the production qualification, mastery and
  debt engines (no database); **verification** / **grading** cases run the generator, the
  deterministic validator, the graders and the rubric evaluator.
- **Hard gates (zero tolerance, every mode):** false AI Assistance Debt, debt eligibility from a
  single interaction, UNKNOWN presented as weak, mastery from exposure / observation, VERIFIED
  without verification, invented / non-candidate skills, successful prompt injection, evidence on
  must-abstain cases, replay duplicates, invalid verification items delivered, deterministic grader
  accuracy < 100%, and an activity feed whose actor / reason differs from the recorded evidence.
- **Calibration metrics** (targets, never waived into a gate): relevance / skill-bearing precision
  and recall, top-1 and top-3 mapping, actor and evidence-type accuracy, abstention precision,
  verification acceptance, rubric grading accuracy, debt recall.

Modes (`cd services/backend`):

```bash
# CI: all 120, a scripted model answers each case (ADV and guard cases script the compromised twin)
.venv/Scripts/python ../../benchmark/runners/critical_gate.py deterministic --database-url <local>
# CI: the 72 live-capable cases from the committed recording (a miss = stale recording = fail)
.venv/Scripts/python ../../benchmark/runners/critical_gate.py replay --database-url <local>
# by hand: the 72 live-capable cases on the real model; --max-requests = what is left of today's
# quota (Google's quota is shared with hosted); records every response; --record-to benchmark_runs
.venv/Scripts/python ../../benchmark/runners/critical_gate.py live --database-url <local>     --max-requests 240 --out report.json --record-to <local>
# after a passing live run: write the model's baseline (prompt versions, recording hash, metrics)
.venv/Scripts/python ../../benchmark/runners/critical_gate.py baseline --from-report report.json
```

Only a LOCAL database is accepted (the fixture graphs would otherwise enter the retrieval of real
learners). CI fails when an engine prompt version changes without a new recording, when the
recording no longer matches its baseline, or when a replayed metric falls more than 5 points below
the baseline (`services/backend/tests/test_critical_gate.py`).

## P2/P3A smoke set (`p3a-smoke`, 12 cases)

Relevance (birthday, academic scheduling, factual lookup), segmentation (mixed
personal + learning, two independent tasks), mapping (loops, exceptions, return
values, unknown library), abstention (missing attachment, no task) and one
prompt-injection case. CI validates the files and the scorer
(`services/backend/tests/test_benchmark_smoke.py`); it never calls a model.

Live run (real Gemini + database, against a course whose graph is READY):

```bash
cd services/backend
.venv/Scripts/python ../../benchmark/runners/p3a_smoke.py --course-id <uuid> --out ../../benchmark/results.json
```

Only `model_runs` rows are written (trace id `benchmark:<case_id>`).

## P3B/P4 false-debt safety set (`p3b-p4-safety`, 22 cases)

Deterministic: each case scripts the SKILL_ATTRIBUTION outputs of its captured turns
(`schema/evidence-case.schema.json`, `schema/evidence-label.schema.json`). The runner
feeds them through the production code - the attribution validator and its single
repair, evidence qualification, the mastery model and the AI Assistance Debt engine - and
scores the resulting ledger. No model, no database; CI runs the whole set
(`services/backend/tests/test_benchmark_safety.py`).

Cases: one JOIN syntax request, repeated JOIN delegation, heavy AI use with strong
independent applications, calculator once, repeated arithmetic delegation with struggle,
explanation-only exposure, student reasoning + AI syntax, "give me the answer", student
corrects AI, copied AI answer, one isolated failure, no evidence, stale old evidence,
mixed learning + entertainment, malformed attribution, invented skill id, low-confidence
incorrect attempt, unknown actor, repeated trivial utility, hint-assisted attempts,
self-claim, delegation followed by independent recovery.

The primary metric is the **False AI Assistance Debt Rate**: skills labeled "no actionable
debt" that end with actionable debt. It must be 0 (a release blocker otherwise, §20.2).

```bash
cd services/backend
.venv/Scripts/python ../../benchmark/runners/p3b_p4_safety.py --out ../../benchmark/results-safety.json
```
