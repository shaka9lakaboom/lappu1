"""Explanation Service (architecture §5.2): stored provenance -> the learner's "Why?". No model call.

Pure functions over the ledger row, the evidence and the policy. They never invent a
judgement the ledger does not hold:

* UNKNOWN is explained as missing evidence, never as weakness; no mean is exposed and no
  mean gate is evaluated while a skill is UNKNOWN.
* AI Assistance Debt is explained as a reliance signal. It is NONE unless the eligibility
  guards passed (repeated recent delegation); an eligible debt is a qualitative band
  (LOW / MODERATE / HIGH) with its contributing factors, and the 0-100 score is never the
  headline.
"""

from typing import Any, Literal

from app.intelligence.policy import DebtBands, MasteryPolicy

Level = Literal["LOW", "MODERATE", "HIGH"]
DebtBandName = Literal["NONE", "LOW", "MODERATE", "HIGH"]


def debt_band(eligible: bool, score: float, bands: DebtBands) -> DebtBandName:
    """NONE without eligibility (not enough repeated delegation to infer reliance)."""
    if not eligible:
        return "NONE"
    if score >= bands.high_min:
        return "HIGH"
    if score >= bands.moderate_min:
        return "MODERATE"
    return "LOW"


def factor_level(value: float) -> Level:
    """A 0..1 debt component as a qualitative level (thirds)."""
    if value >= 2 / 3:
        return "HIGH"
    if value >= 1 / 3:
        return "MODERATE"
    return "LOW"


# Debt component (debt/engine.py) -> learner-facing factor code, in display order.
DEBT_FACTORS: tuple[tuple[str, str], ...] = (
    ("delegation_pressure", "DELEGATION_PRESSURE"),
    ("evidence_gap", "EVIDENCE_GAP"),
    ("importance", "IMPORTANCE"),
    ("confidence", "CONFIDENCE"),
    ("verification_factor", "VERIFICATION"),
)


def debt_factors(components: dict[str, Any]) -> list[dict[str, Any]]:
    """The contributing factors of an ELIGIBLE debt; empty otherwise."""
    if components.get("eligibility") != "ELIGIBLE":
        return []
    factors = []
    for key, code in DEBT_FACTORS:
        value = components.get(key)
        if value is None:
            continue
        factors.append({"code": code, "level": factor_level(float(value)), "value": float(value)})
    return factors


def mastery_explanation_code(
    state: str,
    *,
    evidence_count: int,
    performance_evidence_count: int,
    mastery_mean: float,
    support: float,
    has_application: bool,
    policy: MasteryPolicy,
    verification_standing: str = "NONE",
    recently_verified: bool = False,
) -> str:
    """Why the ledger holds this state, as a stable code the UI turns into plain language."""
    if state == "UNKNOWN":
        if evidence_count == 0:
            return "NO_EVIDENCE"
        if performance_evidence_count == 0:
            # Only exposure / observation: seeing an explanation is not a demonstration.
            return "NO_INDEPENDENT_PERFORMANCE"
        return "NOT_ENOUGH_SUPPORT"
    if state == "EMERGING":
        return "EARLY_DIFFICULTY"
    if state == "DEVELOPING":
        if mastery_mean < policy.demonstrated_min_mean:
            return "MIXED_RESULTS"
        if support < policy.demonstrated_min_support:
            return "NEEDS_MORE_EVIDENCE"
        if not has_application:
            return "NEEDS_INDEPENDENT_APPLICATION"
        return "MIXED_RESULTS"
    if state == "DEMONSTRATED":
        return "INDEPENDENT_EVIDENCE_SUPPORTS"
    if state == "VERIFIED":
        if recently_verified and verified_gates_met(mastery_mean, support, policy):
            return "RECENT_VERIFICATION"
        # Entered through the gates at a recent check and held: a later isolated result is
        # recorded (it lowers the estimate) but does not undo the check.
        return "VERIFICATION_HELD"
    if verification_standing == "CONTRADICTED":  # NEEDS_REVERIFICATION
        return "VERIFICATION_CONTRADICTED"
    return "VERIFICATION_STALE"


def verified_gates_met(mastery_mean: float, support: float, policy: MasteryPolicy) -> bool:
    return mastery_mean >= policy.verified_min_mean and support >= policy.verified_min_support


def mastery_gates(
    state: str,
    *,
    mastery_mean: float,
    support: float,
    has_application: bool,
    policy: MasteryPolicy,
    verification: bool = False,
    recently_verified: bool = False,
) -> list[dict[str, Any]]:
    """The DEMONSTRATED gates and whether each is met. While UNKNOWN only the evidence gate is
    shown: no mean is exposed or judged without enough evidence. Once the skill has a
    SkillMirror verification (or a verified state), the VERIFIED entry gates follow."""
    gates: list[dict[str, Any]] = [
        {
            "code": "ENOUGH_EVIDENCE",
            "met": support >= policy.unknown_min_support,
            "current": round(support, 6),
            "required": policy.unknown_min_support,
        }
    ]
    if state == "UNKNOWN":
        return gates
    gates += [
        {
            "code": "STRONG_RESULTS",
            "met": mastery_mean >= policy.demonstrated_min_mean,
            "current": round(mastery_mean, 6),
            "required": policy.demonstrated_min_mean,
        },
        {
            "code": "SUSTAINED_EVIDENCE",
            "met": support >= policy.demonstrated_min_support,
            "current": round(support, 6),
            "required": policy.demonstrated_min_support,
        },
        {
            "code": "INDEPENDENT_APPLICATION",
            "met": has_application,
            "current": None,
            "required": None,
        },
    ]
    if verification or state in ("VERIFIED", "NEEDS_REVERIFICATION"):
        gates += [
            {
                "code": "RECENT_CHECK_PASSED",
                "met": recently_verified,
                "current": None,
                "required": None,
            },
            {
                "code": "VERIFIED_RESULTS",
                "met": mastery_mean >= policy.verified_min_mean,
                "current": round(mastery_mean, 6),
                "required": policy.verified_min_mean,
            },
            {
                "code": "VERIFIED_EVIDENCE",
                "met": support >= policy.verified_min_support,
                "current": round(support, 6),
                "required": policy.verified_min_support,
            },
        ]
    return gates
