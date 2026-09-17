from __future__ import annotations

from dataclasses import dataclass

from taxonomy.models import TaxonomyCategory

from .candidate_generator import Candidate, CandidateGenerator
from .confidence import ConfidenceEvaluator
from .scorer import DeterministicCategoryScorer
from .taxonomy_index import TaxonomyCandidateIndex
from .text_builder import ProductTextBuilder


@dataclass(frozen=True)
class RankedAlternative:
    category: TaxonomyCategory
    confidence: float
    rank: int


@dataclass(frozen=True)
class ClassificationDecision:
    category: TaxonomyCategory | None
    confidence_score: float | None
    status: str
    needs_manual_review: bool
    classification_method: str
    reasoning: str
    alternatives: tuple[RankedAlternative, ...]


class ClassificationEngine:
    def __init__(self, index: TaxonomyCandidateIndex, candidate_limit: int = 40, ai_fallback=None):
        self.index = index
        self.text_builder = ProductTextBuilder()
        self.generator = CandidateGenerator(index, limit=candidate_limit)
        self.scorer = DeterministicCategoryScorer(index)
        self.confidence = ConfidenceEvaluator()
        self.ai_fallback = ai_fallback

    def classify(self, product) -> ClassificationDecision:
        text = self.text_builder.build(product)
        candidates = self.generator.generate(text)
        if not candidates:
            return ClassificationDecision(None, None, "needs_review", True, "rule", "No relevant taxonomy category candidates were found.", ())
        ranked = sorted(((candidate, self.scorer.score(text, candidate)) for candidate in candidates), key=lambda item: (-item[1], self.index.categories[item[0].category_id].full_path))
        best_candidate, best_score = ranked[0]
        second_score = ranked[1][1] if len(ranked) > 1 else 0.0
        margin = best_score - second_score
        confidence = self.confidence.evaluate(best_score, margin)
        best = self.index.categories[best_candidate.category_id]
        category = self.index.category_objects[best.id]
        alternatives = tuple(
            RankedAlternative(self.index.category_objects[candidate.category_id], score, rank)
            for rank, (candidate, score) in enumerate(ranked[1:4], 1)
        )
        signals = [field.replace("_", " ") for field, value in text.fields.items() if value and field in {"product_name", "product_category", "product_sub_category", "product_description", "materials"}]
        reasoning = f"Selected '{best.full_path}' from {len(candidates)} candidates using deterministic matches in {', '.join(signals) or 'available product data'}; score {best_score:.2f}, margin {margin:.2f}."
        deterministic = ClassificationDecision(category, best_score, confidence.status, confidence.needs_manual_review, "rule", reasoning, alternatives)
        if self.ai_fallback:
            return self.ai_fallback.classify_if_needed(text, ranked, deterministic)
        return deterministic
