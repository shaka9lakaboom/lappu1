# Intelligence benchmark

Labeled cases that gate every prompt, model, graph or threshold change
(architecture section 17). Empty until the intelligence pipeline exists (P3+);
the 120-case critical gate set is built in P8.

- `cases/` - input interactions (real or synthetic, no personal data)
- `labels/` - expected relevance, mapping, attribution, evidence and debt outcomes
- `runners/` - scripts that score the pipeline against the labels
