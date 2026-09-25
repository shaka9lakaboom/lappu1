"""Mapping confidence gate (architecture §9.4, Appendix B). Thresholds come from policy_config.

confidence >= accept_threshold                    -> ACCEPT
adjudicate_min <= confidence < accept_threshold   -> ADJUDICATE (second pass)
confidence < adjudicate_min                       -> ABSTAIN
"""

from typing import Literal

from app.intelligence.policy import MappingPolicy

GateDecision = Literal["ACCEPT", "ADJUDICATE", "ABSTAIN"]


def gate(confidence: float, policy: MappingPolicy) -> GateDecision:
    if confidence >= policy.accept_threshold:
        return "ACCEPT"
    if confidence >= policy.adjudicate_min:
        return "ADJUDICATE"
    return "ABSTAIN"
