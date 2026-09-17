from __future__ import annotations

from typing import Sequence

from .base import ProviderClassification


class MockClassificationProvider:
    def __init__(self, result: ProviderClassification | None = None):
        self.result = result
        self.calls: list[tuple[str, Sequence[str]]] = []

    def classify(self, product_context: str, candidates: Sequence[str]) -> ProviderClassification:
        self.calls.append((product_context, candidates))
        if self.result is None:
            raise NotImplementedError("Mock provider has no configured result")
        return self.result
