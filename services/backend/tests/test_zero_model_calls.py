"""P5 makes no model call (ADR 0006), and neither does any P6 request (ADR 0007): these modules
never load the ModelGateway or a provider SDK. P6 generation and grading run in the worker.

Checked in a fresh interpreter, so modules imported by other tests cannot mask a dependency.
The database tests additionally assert that model_runs never grows across every P5 endpoint.
"""

import subprocess
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]

P5_MODULES = (
    "app.api.v1.skills",
    "app.api.v1.activity",
    "app.api.v1.feedback",
    "app.api.v1.recommendations",
    "app.api.v1.ledger",
    "app.experience.skills",
    "app.experience.activity",
    "app.experience.feedback",
    "app.experience.models",
    "app.intelligence.recommendations.engine",
    "app.intelligence.recommendations.service",
    "app.intelligence.explanation",
    # P6: every HTTP path of the Verification Center (planning included) is model-free.
    "app.api.v1.verifications",
    "app.experience.verifications",
    "app.intelligence.verification.planner",
    "app.intelligence.verification.graders",
    "app.intelligence.verification.validator",
    "app.intelligence.verification.evidence",
    "app.intelligence.mastery.engine",
    "app.intelligence.debt.engine",
)


def test_student_experience_modules_do_not_load_the_model_gateway() -> None:
    script = (
        "import importlib, sys\n"
        f"for name in {P5_MODULES!r}:\n"
        "    importlib.import_module(name)\n"
        "loaded = sorted(m for m in sys.modules\n"
        "                if m.startswith(('app.model_gateway', 'google.genai', 'google.generativeai')))\n"
        "print(','.join(loaded))\n"
    )
    result = subprocess.run(  # noqa: S603 - fixed interpreter and script
        [sys.executable, "-c", script],
        cwd=BACKEND,
        capture_output=True,
        text=True,
        check=True,
    )
    assert result.stdout.strip() == ""
