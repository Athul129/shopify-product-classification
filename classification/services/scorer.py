from __future__ import annotations

from dataclasses import dataclass

from .candidate_generator import Candidate
from .taxonomy_index import TaxonomyCandidateIndex
from .text_builder import ProductText, normalize_text


@dataclass(frozen=True)
class ScoringWeights:
    source_subcategory: float = 0.30
    product_name: float = 0.30
    source_category: float = 0.15
    description_bullets: float = 0.15
    materials_structured: float = 0.05
    hierarchy: float = 0.05


WEIGHTS = ScoringWeights()


@dataclass(frozen=True)
class ScoreBreakdown:
    source_subcategory: float
    product_name: float
    source_category: float
    description_bullets: float
    materials_structured: float
    hierarchy: float
    exact_phrase_bonus: float
    specificity_adjustment: float
    conflict_penalty: float
    product_type_adjustment: float
    environment_adjustment: float
    total: float


def _coverage(source: str, target: str) -> float:
    source_tokens, target_tokens = set(source.split()), set(target.split())
    if not source_tokens or not target_tokens:
        return 0.0
    return len(source_tokens & target_tokens) / len(target_tokens)


class DeterministicCategoryScorer:
    CONFLICT_TERMS = frozenset("accessories accessory legs cushions supports covers cases parts".split())
    PRODUCT_TERMS = frozenset("sofa armchair chair stool table desk bin can holder".split())
    TYPE_GROUPS = {
        "armchair": {"armchair", "recliner"},
        "chair": {"chair", "armchair", "recliner"},
        "sofa": {"sofa", "couch", "loveseat", "sectional"},
        "stool": {"stool"},
        "table": {"table"},
        "ottoman": {"ottoman"},
        "trash": {"trash", "can", "wastebasket", "receptacle"},
    }
    OUTDOOR_TERMS = frozenset("outdoor patio garden exterior weatherproof teak".split())
    INDOOR_TERMS = frozenset("indoor interior kitchen diningroom dining".split())

    def __init__(self, index: TaxonomyCandidateIndex, weights: ScoringWeights = WEIGHTS):
        self.index, self.weights = index, weights

    def breakdown(self, product_text: ProductText, candidate: Candidate) -> ScoreBreakdown:
        category = self.index.categories[candidate.category_id]
        path = normalize_text(category.full_path)
        name = normalize_text(category.name)
        fields = product_text.field_tokens
        sub = max(_coverage(" ".join(fields["product_sub_category"]), path), _coverage(" ".join(fields["product_sub_category"]), name))
        product_name = max(_coverage(" ".join(fields["product_name"]), path), _coverage(" ".join(fields["product_name"]), name))
        source_category = max(_coverage(" ".join(fields["product_category"]), path), _coverage(" ".join(fields["product_category"]), name))
        description = fields["product_description"] | fields["bullets"]
        desc = max(_coverage(" ".join(description), path), _coverage(" ".join(description), name))
        structured = set().union(*(fields[field] for field in ("materials", "set_includes", "product_color", "collection_name", "model_number")))
        material = max(_coverage(" ".join(structured), path), _coverage(" ".join(structured), name))
        hierarchy = _coverage(" ".join(fields["product_category"]), path)
        exact = 0.0
        for field in ("product_name", "product_sub_category"):
            if product_text.fields[field] and product_text.fields[field] in {name, path}:
                exact = max(exact, 0.10)
        # Prefer a specific taxonomy phrase explicitly present in the product
        # identity over a generic ancestor that only shares one token.  This
        # is intentionally taxonomy-driven (for example, "coffee table" vs
        # "tables") and applies to any multi-word product/category phrase.
        identity_terms = fields["product_name"] | fields["product_sub_category"]
        category_name_terms = set(name.split())
        if len(category_name_terms) >= 2 and category_name_terms <= identity_terms:
            exact += 0.25
        specificity = 0.03 if not self.index.children.get(category.id) else -0.03
        category_terms = set(category.tokens)
        product_identity_terms = fields["product_name"] | fields["product_sub_category"]
        conflict = 0.12 if category_terms & self.CONFLICT_TERMS and not (category_terms & product_identity_terms) else 0.0
        evidence = fields["product_name"] | fields["product_description"] | fields["bullets"] | fields["materials"]
        product_type_adjustment = 0.0
        for group, terms in self.TYPE_GROUPS.items():
            if evidence & terms:
                if category_terms & terms:
                    product_type_adjustment += 0.18 if group == "armchair" else 0.10 if group in {"stool", "table", "ottoman", "trash"} else 0.06
                elif group == "armchair" and category_terms & self.TYPE_GROUPS["sofa"]:
                    product_type_adjustment -= 0.35
                elif group == "stool" and category_terms & self.TYPE_GROUPS["sofa"]:
                    product_type_adjustment -= 0.12
                elif group == "table" and category_terms & {"set", "sets"}:
                    product_type_adjustment -= 0.15
        if "loveseat" in evidence or "loveseats" in evidence:
            if "loveseat" in category_terms:
                product_type_adjustment += 0.16
            elif category_terms & {"sectional", "sectionals"}:
                product_type_adjustment -= 0.10
            elif category_terms & {"sofa", "sofas"}:
                product_type_adjustment -= 0.04
        raw_category = category.name.lower()
        if "trash can" in raw_category or "trash cans" in raw_category:
            if evidence & {"trash", "can"}:
                product_type_adjustment += 0.08
        elif "wastebasket" in raw_category:
            product_type_adjustment += 0.02 if evidence & {"trash", "can"} else 0.0
        environment_evidence = evidence | fields["product_category"] | fields["product_sub_category"]
        environment_adjustment = 0.0
        category_is_outdoor = bool(category_terms & {"outdoor", "patio", "garden", "exterior"})
        category_is_indoor = bool(category_terms & {"indoor", "interior", "kitchen", "dining"})
        if environment_evidence & self.OUTDOOR_TERMS:
            environment_adjustment += 0.10 if category_is_outdoor else -0.05 if category_is_indoor else 0.0
        elif environment_evidence & self.INDOOR_TERMS and category_is_outdoor:
            environment_adjustment -= 0.30
        elif category_is_outdoor:
            # Source merchandising labels alone do not establish outdoor use.
            environment_adjustment -= 0.04
        weighted = (self.weights.source_subcategory * sub + self.weights.product_name * product_name + self.weights.source_category * source_category + self.weights.description_bullets * desc + self.weights.materials_structured * material + self.weights.hierarchy * hierarchy)
        total = min(1.0, max(0.0, weighted + exact + specificity - conflict + product_type_adjustment + environment_adjustment))
        return ScoreBreakdown(sub, product_name, source_category, desc, material, hierarchy, exact, specificity, conflict, product_type_adjustment, environment_adjustment, total)

    def score(self, product_text: ProductText, candidate: Candidate) -> float:
        return self.breakdown(product_text, candidate).total
