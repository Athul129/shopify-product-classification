from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, Sequence


@dataclass(frozen=True)
class ProviderClassification:
    category_id: str
    confidence: float
    reasoning: str = ""


class ClassificationProvider(Protocol):
    def classify(self, product_context: str, candidates: Sequence[str]) -> ProviderClassification:
        """Return a provider result for a bounded candidate list."""
