"""Stored analysis versions (the idempotency keys of P3A and P3B rows).

Kept free of engine imports so model-free readers (the admin API, ADR 0008) can use them without
loading the ModelGateway. `processing.persist` and `evidence.persist` re-export them.
"""

# activity_segments.analysis_version (P3A, ADR 0003/0004).
ANALYSIS_VERSION = "p3a-v1"
# attributions.attribution_version (P3B, ADR 0005).
ATTRIBUTION_VERSION = "p3b-v1"
