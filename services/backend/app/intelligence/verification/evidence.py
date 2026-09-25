"""Verification result -> VERIFICATION EvidenceEvent (architecture §9.6, §10.1, Appendix B, B.1, B.2;
ADR 0007). Deterministic, no model call.

A graded result becomes exactly one EvidenceEvent:

    source_type = VERIFICATION, source_id = verification_results.id
    actor = STUDENT, evidence_type = VERIFICATION
    outcome_signal / outcome           = the result's (deterministic from the grade)
    difficulty                         = the challenge's (inside the planned band)
    difficulty_multiplier              = multiplier_base + multiplier_slope * difficulty
    independence, base_weight          = the evidence policy's VERIFICATION values (1.0, 1.5)
    mapping / attribution confidence   = 1 (the challenge targets the skill; the learner answered)
    evidence_confidence                = min(1, 1, grading_confidence) = grading_confidence (B.2)
    strength                           = base_weight * multiplier * independence * evidence_confidence

It never goes through P3B attribution: it claims no attribution, mapping, segment or raw message.
Provenance runs result -> item -> session -> learner / course / skill, plus the model runs.
"""

from dataclasses import dataclass

from app.intelligence.policy import EvidencePolicy

QUALIFIER_VERSION = "verification/p6-v1"
REASONS = {
    "CORRECT": "VERIFICATION_PASSED",
    "PARTIAL": "VERIFICATION_PARTIAL",
    "INCORRECT": "VERIFICATION_FAILED",
}


@dataclass(frozen=True)
class VerificationEvidence:
    outcome_signal: str
    outcome: float
    difficulty: float
    difficulty_multiplier: float
    independence: float
    base_weight: float
    strength: float
    grading_confidence: float
    evidence_confidence: float
    qualification_reason: str


def verification_evidence(
    *,
    outcome_signal: str,
    outcome: float,
    difficulty: float,
    grading_confidence: float,
    policy: EvidencePolicy,
) -> VerificationEvidence:
    multiplier = policy.difficulty.multiplier_base + policy.difficulty.multiplier_slope * difficulty
    independence = policy.independence["VERIFICATION"]
    base_weight = policy.base_weights["VERIFICATION"]
    confidence = min(1.0, grading_confidence)
    return VerificationEvidence(
        outcome_signal=outcome_signal,
        outcome=outcome,
        difficulty=difficulty,
        difficulty_multiplier=multiplier,
        independence=independence,
        base_weight=base_weight,
        strength=base_weight * multiplier * independence * confidence,
        grading_confidence=grading_confidence,
        evidence_confidence=confidence,
        qualification_reason=REASONS[outcome_signal],
    )
