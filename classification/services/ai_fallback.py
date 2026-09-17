"""Optional second-stage provider orchestration for classification."""

from __future__ import annotations

import logging
from numbers import Real
from typing import TYPE_CHECKING

from .confidence import ConfidenceEvaluator
from .providers.base import ClassificationProvider
from .text_builder import normalize_text

if TYPE_CHECKING:
    from .engine import ClassificationDecision
    from .candidate_generator import Candidate
    from .taxonomy_index import TaxonomyCandidateIndex
    from .text_builder import ProductText

logger = logging.getLogger(__name__)

PRODUCT_TYPE_TERMS = {
    "table": {"table", "tables"},
    "chair": {"chair", "chairs", "armchair", "armchairs", "recliner", "recliners"},
    "sofa": {"sofa", "sofas", "couch", "couches", "loveseat", "loveseats", "sectional", "sectionals"},
    "stool": {"stool", "stools"},
    "ottoman": {"ottoman", "ottomans"},
    "waste": {"trash", "wastebasket", "wastebaskets", "waste", "receptacle", "receptacles"},
    "pen_holder": {"pen", "pens", "pencil", "pencils", "holder", "holders"},
}


def _product_type_terms(product_text: ProductText) -> set[str]:
    # Use product evidence fields for conflict checks; merchandising labels can be stale.
    fields = ("product_name", "product_description", "bullets", "materials")
    return set().union(*(product_text.field_tokens[field] for field in fields))


def _compatible(category_path: str, product_terms: set[str]) -> bool:
    category_terms = set(normalize_text(category_path).split())
    for terms in PRODUCT_TYPE_TERMS.values():
        if product_terms & terms and category_terms & terms:
            return True
    return False


def _conflicts(category_path: str, product_terms: set[str]) -> bool:
    category_terms = set(normalize_text(category_path).split())
    if "table" in product_terms or "tables" in product_terms:
        return bool(category_terms & {"set", "sets"})
    if product_terms & PRODUCT_TYPE_TERMS["chair"]:
        return bool(category_terms & PRODUCT_TYPE_TERMS["sofa"]) and not bool(product_terms & PRODUCT_TYPE_TERMS["sofa"])
    return bool(product_terms & PRODUCT_TYPE_TERMS["stool"] and category_terms & {"sofa", "sofas", "ottoman", "ottomans"})


def _generic(category_path: str) -> bool:
    terms = set(normalize_text(category_path).split())
    return terms in ({"furniture"}, {"home", "garden"})


class AIFallbackOrchestrator:
    """Call a provider only when deterministic evidence warrants a fallback."""

    def __init__(self, index: TaxonomyCandidateIndex, provider: ClassificationProvider, enabled: bool = False):
        self.index = index
        self.provider = provider
        self.enabled = enabled
        self.confidence = ConfidenceEvaluator()

    def classify_if_needed(
        self,
        product_text: ProductText,
        ranked_candidates: list[tuple[Candidate, float]],
        deterministic: ClassificationDecision,
    ) -> ClassificationDecision:
        if not self.enabled or not ranked_candidates:
            return deterministic

        best_score = ranked_candidates[0][1]
        second_score = ranked_candidates[1][1] if len(ranked_candidates) > 1 else 0.0
        margin = best_score - second_score
        if (
            deterministic.category is not None
            and best_score >= self.confidence.HIGH_CONFIDENCE
            and margin >= self.confidence.REVIEW_MARGIN
        ):
            return deterministic

        # Only bounded, already-generated candidates are sent to the provider.
        candidate_ids = [candidate.category_id for candidate, _ in ranked_candidates]
        candidate_gids = [self.index.categories[category_id].taxonomy_id for category_id in candidate_ids]
        candidate_options = [
            f"{self.index.categories[category_id].taxonomy_id} | {self.index.categories[category_id].full_path}"
            for category_id in candidate_ids
        ]
        try:
            response = self.provider.classify(product_text.combined, candidate_options)
            if not isinstance(response.category_id, str) or response.category_id not in candidate_gids:
                raise ValueError("provider returned a category outside the supplied candidates")
            if isinstance(response.confidence, bool) or not isinstance(response.confidence, Real) or not 0 <= response.confidence <= 1:
                raise ValueError("provider returned an invalid confidence")
            if not isinstance(response.reasoning, str):
                raise ValueError("provider returned invalid reasoning")

            from .engine import ClassificationDecision, RankedAlternative

            selected_internal_id = candidate_ids[candidate_gids.index(response.category_id)]
            selected = self.index.category_objects[selected_internal_id]
            product_terms = _product_type_terms(product_text)
            deterministic_candidate = self.index.category_objects[ranked_candidates[0][0].category_id]
            selected_is_compatible = _compatible(selected.full_path, product_terms)
            deterministic_is_compatible = _compatible(deterministic_candidate.full_path, product_terms)
            if _conflicts(selected.full_path, product_terms) or (not selected_is_compatible and deterministic_is_compatible):
                raise ValueError("provider category conflicts with strong product-type evidence")
            if _generic(selected.full_path) and deterministic_is_compatible and ranked_candidates[0][1] >= 0.55:
                raise ValueError("provider selected a generic parent over a supported specific category")
            status = "classified" if response.confidence >= self.confidence.HIGH_CONFIDENCE else "needs_review"
            alternatives = tuple(
                RankedAlternative(self.index.category_objects[candidate.category_id], score, rank)
                for rank, (candidate, score) in enumerate(ranked_candidates, 1)
                if candidate.category_id != selected_internal_id
            )[:3]
            reasoning = response.reasoning.strip() or f"Provider selected '{selected.full_path}' from the deterministic candidate set."
            return ClassificationDecision(
                selected,
                float(response.confidence),
                status,
                status != "classified",
                "ai",
                reasoning,
                alternatives,
            )
        except Exception:
            # Keep the deterministic result usable if a provider is unavailable or malformed.
            logger.exception("AI classification fallback failed; retaining deterministic result")
            return deterministic
