# Intelligence benchmark

Labeled cases that gate every prompt, model, graph or threshold change
(architecture section 17). The 120-case critical gate set is built in P8; P2/P3A
ship the case/label contract and a small smoke set.

- `schema/` - JSON Schemas: `case.schema.json` (input turn + course) and
  `label.schema.json` (expected routes, segments, mapped skills, abstention)
- `cases/` - input interactions (real or synthetic, no personal data), JSON Lines
- `labels/` - expected outcomes, one per case, same order
- `runners/` - scripts that score the pipeline against the labels

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
