from __future__ import annotations

import re
from dataclasses import dataclass

from products.models import Product

TOKEN_RE = re.compile(r"[a-z0-9]+")
STOPWORDS = frozenset("a an and are as at be by can come for from has have in into is it of on one or or that than their this to was we with would your".split())
SYNONYMS = {
    "trash bin": "trash can", "trash bins": "trash can", "garbage can": "trash can", "garbage cans": "trash can", "waste bin": "trash can", "waste bins": "trash can",
    "pencil holder": "pen holder", "pencil holders": "pen holder", "barstool": "bar stool", "barstools": "bar stool",
}


def _normalize_words(value: str | None) -> list[str]:
    text = (value or "").lower().replace("&", " and ").replace("x000d", " ")
    for source, target in sorted(SYNONYMS.items(), key=lambda item: -len(item[0])):
        text = text.replace(source, target)
    words = []
    for word in TOKEN_RE.findall(text):
        if word in STOPWORDS:
            continue
        if len(word) > 3 and word.endswith("ies"):
            word = word[:-3] + "y"
        elif len(word) > 4 and word.endswith("s") and not word.endswith(("ss", "us", "is")):
            word = word[:-1]
        words.append(word)
    return words


def normalize_text(value: str | None) -> str:
    return " ".join(_normalize_words(value))


@dataclass(frozen=True)
class ProductText:
    fields: dict[str, str]
    field_tokens: dict[str, frozenset[str]]
    combined: str
    tokens: frozenset[str]


class ProductTextBuilder:
    FIELD_NAMES = (
        "product_name", "product_category", "product_sub_category", "collection_name",
        "product_description", "bullets", "materials", "set_includes", "product_color", "model_number",
    )

    def build(self, product: Product) -> ProductText:
        fields = {name: normalize_text(getattr(product, name, "")) for name in self.FIELD_NAMES}
        field_tokens = {name: frozenset(value.split()) for name, value in fields.items()}
        combined = " ".join(value for value in fields.values() if value)
        return ProductText(fields=fields, field_tokens=field_tokens, combined=combined, tokens=frozenset(combined.split()))
