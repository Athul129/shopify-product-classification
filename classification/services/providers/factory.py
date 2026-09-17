from __future__ import annotations

from .mock import MockClassificationProvider
from .gemini import GeminiClassificationProvider


class ProviderConfigurationError(ValueError):
    """Raised when an explicitly configured provider is unsupported."""


def get_classification_provider(enabled: bool, provider_name: str | None = None):
    """Build the configured provider without coupling the engine to providers."""
    if not enabled or not (provider_name or "").strip():
        return None

    name = provider_name.strip().lower()
    if name == "mock":
        return MockClassificationProvider()
    if name == "gemini":
        return GeminiClassificationProvider()
    raise ProviderConfigurationError(f"Unsupported classification AI provider: {provider_name}")
