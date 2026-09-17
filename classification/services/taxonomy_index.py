from __future__ import annotations

from collections import defaultdict
import math
from dataclasses import dataclass
from typing import Iterable

from taxonomy.models import TaxonomyCategory

from .text_builder import normalize_text


@dataclass(frozen=True)
class IndexedCategory:
    id: int
    taxonomy_id: str
    name: str
    full_path: str
    tokens: frozenset[str]
    parent_id: int | None


class TaxonomyCandidateIndex:
    """Read-only in-memory inverted index for active taxonomy categories."""

    def __init__(self, categories: Iterable[TaxonomyCategory]):
        self.categories: dict[int, IndexedCategory] = {}
        self.category_objects: dict[int, TaxonomyCategory] = {}
        self.token_index: dict[str, set[int]] = defaultdict(set)
        self.phrase_index: dict[str, set[int]] = defaultdict(set)
        self.children: dict[int, set[int]] = defaultdict(set)
        for category in categories:
            path = normalize_text(category.full_path)
            name = normalize_text(category.name)
            indexed = IndexedCategory(
                id=category.id,
                taxonomy_id=category.taxonomy_id,
                name=category.name,
                full_path=category.full_path,
                tokens=frozenset(path.split()),
                parent_id=category.parent_id,
            )
            self.categories[category.id] = indexed
            self.category_objects[category.id] = category
            for token in indexed.tokens:
                self.token_index[token].add(category.id)
            for phrase in {name, path}:
                if phrase:
                    self.phrase_index[phrase].add(category.id)
            if category.parent_id:
                self.children[category.parent_id].add(category.id)
        category_count = max(1, len(self.categories))
        self.token_idf = {token: math.log((1 + category_count) / (1 + len(ids))) + 1 for token, ids in self.token_index.items()}

    @classmethod
    def from_database(cls) -> "TaxonomyCandidateIndex":
        categories = TaxonomyCategory.objects.filter(is_active=True).only(
            "id", "taxonomy_id", "name", "full_path", "parent_id"
        )
        return cls(categories)

    def ids_for_phrase(self, phrase: str) -> set[int]:
        return set(self.phrase_index.get(normalize_text(phrase), set()))

    def ids_for_tokens(self, tokens: Iterable[str]) -> set[int]:
        ids: set[int] = set()
        for token in tokens:
            ids.update(self.token_index.get(token, set()))
        return ids

    def token_matches(self, tokens: Iterable[str]) -> dict[int, float]:
        matches: dict[int, float] = defaultdict(float)
        for token in tokens:
            weight = self.token_idf.get(token, 0.0)
            for category_id in self.token_index.get(token, set()):
                matches[category_id] += weight
        return matches

    def ancestors(self, category_id: int) -> set[int]:
        result: set[int] = set()
        current = self.categories.get(category_id)
        while current and current.parent_id and current.parent_id not in result:
            result.add(current.parent_id)
            current = self.categories.get(current.parent_id)
        return result

    def descendants(self, category_id: int, limit: int = 100) -> set[int]:
        result: set[int] = set()
        pending = list(self.children.get(category_id, set()))
        while pending and len(result) < limit:
            child = pending.pop()
            if child in result:
                continue
            result.add(child)
            pending.extend(self.children.get(child, set()))
        return result
