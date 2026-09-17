from __future__ import annotations

import json
import os
import re
import time
from numbers import Real
from typing import Sequence

from .base import ProviderClassification


class GeminiClassificationProvider:
    """Gemini provider using bounded candidates and strict JSON validation."""

    def __init__(self, api_key: str | None = None, model: str | None = None, timeout_seconds: float | None = None, client=None, types_module=None, max_retries: int | None = None):
        self.api_key = api_key or os.getenv("GEMINI_API_KEY", "").strip()
        if not self.api_key and client is None:
            raise ValueError("GEMINI_API_KEY is required for the Gemini provider")
        self.model = model or os.getenv("GEMINI_MODEL", "gemini-2.5-flash").strip()
        self.timeout_seconds = timeout_seconds or float(os.getenv("GEMINI_TIMEOUT_SECONDS", "30"))
        self.client = client
        self.types_module = types_module
        self.max_retries = max(0, max_retries if max_retries is not None else int(os.getenv("GEMINI_MAX_RETRIES", "2")))

    @staticmethod
    def _is_rate_limited(error: Exception) -> bool:
        text = str(error).upper()
        return "429" in text or "RESOURCE_EXHAUSTED" in text

    @staticmethod
    def _retry_delay(error: Exception, attempt: int) -> float:
        # Google API errors may expose retryDelay as  "2s" in details or text.
        for value in re.findall(r"retryDelay[^0-9]*(\d+(?:\.\d+)?)\s*s", str(error), flags=re.IGNORECASE):
            return max(0.0, float(value))
        return float(2 ** attempt)

    def _client(self):
        if self.client is None:
            from google import genai
            from google.genai import types
            self.client = genai.Client(
                api_key=self.api_key,
                http_options=types.HttpOptions(timeout=int(self.timeout_seconds * 1000)),
            )
        return self.client

    def classify(self, product_context: str, candidates: Sequence[str]) -> ProviderClassification:
        if not candidates:
            raise ValueError("Gemini requires at least one candidate")
        prompt = (
            "Classify this product into exactly one of the supplied Shopify Product Taxonomy candidates. "
            "Return only JSON with keys category_id, confidence, and reasoning. category_id must be the "
            "Shopify taxonomy_id/GID copied exactly from the candidate list. confidence must be a number "
            "between 0 and 1. reasoning must be concise and non-empty. Never invent or return a category "
            "outside the supplied candidates.\n\n"
            f"Product context:\n{product_context}\n\n"
            "Candidates (ID | full path):\n" + "\n".join(f"- {candidate}" for candidate in candidates)
        )
        types = self.types_module
        if types is None:
            from google.genai import types
        config = types.GenerateContentConfig(response_mime_type="application/json", response_schema={"type": "OBJECT", "properties": {"category_id": {"type": "STRING"}, "confidence": {"type": "NUMBER"}, "reasoning": {"type": "STRING"}}, "required": ["category_id", "confidence", "reasoning"]})
        for attempt in range(self.max_retries + 1):
            try:
                response = self._client().models.generate_content(model=self.model, contents=prompt, config=config)
                break
            except Exception as exc:
                if not self._is_rate_limited(exc) or attempt >= self.max_retries:
                    raise
                time.sleep(self._retry_delay(exc, attempt + 1))
        raw = getattr(response, "text", None)
        if not isinstance(raw, str) or not raw.strip():
            raise ValueError("Gemini returned an empty response")
        try:
            data = json.loads(raw)
        except (TypeError, json.JSONDecodeError) as exc:
            raise ValueError("Gemini returned malformed JSON") from exc
        if not isinstance(data, dict):
            raise ValueError("Gemini response must be a JSON object")
        category_id = data.get("category_id")
        confidence = data.get("confidence")
        reasoning = data.get("reasoning")
        supplied_ids = {candidate.split(" | ", 1)[0] for candidate in candidates}
        if not isinstance(category_id, str) or category_id not in supplied_ids:
            raise ValueError("Gemini returned a category outside the supplied candidates")
        if isinstance(confidence, bool) or not isinstance(confidence, Real) or not 0 <= confidence <= 1:
            raise ValueError("Gemini returned an invalid confidence")
        if not isinstance(reasoning, str) or not reasoning.strip():
            raise ValueError("Gemini returned empty reasoning")
        return ProviderClassification(category_id, float(confidence), reasoning.strip())
