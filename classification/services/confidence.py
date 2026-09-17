from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ConfidenceDecision:
    status: str
    needs_manual_review: bool


class ConfidenceEvaluator:
    HIGH_CONFIDENCE = 0.80
    REVIEW_MARGIN = 0.10
    REVIEW_FLOOR = 0.60

    def evaluate(self, score: float, margin: float) -> ConfidenceDecision:
        if score >= self.HIGH_CONFIDENCE and margin >= self.REVIEW_MARGIN:
            return ConfidenceDecision("classified", False)
        return ConfidenceDecision("needs_review", True)
