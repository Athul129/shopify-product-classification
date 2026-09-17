from __future__ import annotations

from dataclasses import dataclass

from .taxonomy_index import TaxonomyCandidateIndex
from .text_builder import ProductText


@dataclass(frozen=True)
class Candidate:
    category_id: int
    retrieval_score: float
    pools: tuple[str, ...] = ()


class CandidateGenerator:
    POOL_FIELDS = {
        "source_subcategory": ("product_sub_category",),
        "source_category": ("product_category",),
        "product_name": ("product_name",),
        "description_bullets": ("product_description", "bullets"),
        "structured": ("materials", "set_includes", "product_color", "collection_name", "model_number"),
    }

    def __init__(self, index: TaxonomyCandidateIndex, limit: int = 40, pool_limit: int = 15):
        self.index, self.limit, self.pool_limit = index, limit, pool_limit

    def generate(self, product_text: ProductText) -> list[Candidate]:
        merged: dict[int, tuple[float, set[str]]] = {}
        priority_ids: set[int] = set()
        for pool_name, fields in self.POOL_FIELDS.items():
            tokens = set().union(*(product_text.field_tokens[field] for field in fields))
            scores = self.index.token_matches(tokens)
            phrases = [product_text.fields[field] for field in fields if product_text.fields[field]]
            for phrase in phrases:
                for category_id in self.index.ids_for_phrase(phrase):
                    scores[category_id] = scores.get(category_id, 0.0) + 4.0
            if pool_name in {"product_name", "source_subcategory"}:
                for token in tokens:
                    for category_id in self.index.ids_for_phrase(token):
                        scores[category_id] = scores.get(category_id, 0.0) + 3.0
                # Preserve the strongest product-type evidence for the final bounded set.
                priority_ids.update(
                    category_id for category_id, _ in sorted(
                        scores.items(), key=lambda item: (-item[1], self.index.categories[item[0]].full_path)
                    )[: self.pool_limit]
                )
            pool_ids = sorted(scores, key=lambda cid: (-scores[cid], self.index.categories[cid].full_path))[:self.pool_limit]
            for category_id in pool_ids:
                score, pools = merged.get(category_id, (0.0, set()))
                pools.add(pool_name)
                merged[category_id] = (max(score, scores[category_id]), pools)

        # Expand only around strong direct matches, not every generic parent token.
        direct_ids = set(merged)
        for category_id in list(direct_ids):
            score, pools = merged[category_id]
            category = self.index.categories[category_id]
            for related_id in self.index.ancestors(category_id):
                related_score, related_pools = merged.get(related_id, (0.0, set()))
                merged[related_id] = (max(related_score, score * 0.70), related_pools | {"ancestor"})
            if score >= 3.0 or len(pools & {"product_name", "source_subcategory"}) > 0:
                for related_id in self.index.descendants(category_id, limit=20):
                    related_score, related_pools = merged.get(related_id, (0.0, set()))
                    merged[related_id] = (max(related_score, score * 0.60), related_pools | {"descendant"})

        ranked_all = sorted(
            merged.items(),
            key=lambda item: (
                0 if item[0] in priority_ids else 1,
                -item[1][0],
                self.index.categories[item[0]].full_path,
            ),
        )
        ranked = ranked_all[:self.limit]
        return [Candidate(category_id=cid, retrieval_score=score, pools=tuple(sorted(pools))) for cid, (score, pools) in ranked]
