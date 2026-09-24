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
