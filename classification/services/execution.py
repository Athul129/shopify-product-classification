from django.conf import settings
from django.db import transaction
from django.db.models import Max
from django.utils import timezone

from classification.models import ClassificationAlternative, ClassificationResult, ProcessingAttempt
from .ai_fallback import AIFallbackOrchestrator
from .engine import ClassificationEngine
from .providers.factory import ProviderConfigurationError, get_classification_provider
from .taxonomy_index import TaxonomyCandidateIndex


class CountingProvider:
    def __init__(self, provider):
        self.provider, self.calls, self.successes, self.failures = provider, 0, 0, 0

    def classify(self, context, candidates):
        self.calls += 1
        try:
            result = self.provider.classify(context, candidates)
        except Exception:
            self.failures += 1
            raise
        self.successes += 1
        return result


def build_ai_fallback(index):
    try:
        provider = get_classification_provider(settings.CLASSIFICATION_AI_ENABLED, settings.CLASSIFICATION_AI_PROVIDER)
    except ProviderConfigurationError:
        raise
    if provider is None:
        return None
    counted = CountingProvider(provider)
    fallback = AIFallbackOrchestrator(index, counted, enabled=True)
    fallback.provider_stats = counted
    return fallback


def execute_classification(batch, limit=None, overwrite=False, progress=None, engine_class=ClassificationEngine, fallback_builder=build_ai_fallback):
    index = TaxonomyCandidateIndex.from_database()
    fallback = fallback_builder(index)
    engine = engine_class(index, ai_fallback=fallback)
    scope = batch.products.order_by("id")
    if limit is not None:
        scope = scope[:limit]
    selected_ids = list(scope.values_list("id", flat=True))
    products = list(batch.products.filter(id__in=selected_ids).annotate(last_attempt=Max("processing_attempts__attempt_number")).order_by("id"))
    outcomes = []
    for product in products:
        if not overwrite and ClassificationResult.objects.filter(product_id=product.id).exists():
            outcomes.append((product, None, "skipped")); continue
        attempt = None
        try:
            attempt = ProcessingAttempt.objects.create(product=product, attempt_number=(product.last_attempt or 0) + 1)
            decision = engine.classify(product)
            with transaction.atomic():
                result, _ = ClassificationResult.objects.update_or_create(product=product, defaults={
                    "category": decision.category, "confidence_score": decision.confidence_score,
                    "classification_method": decision.classification_method, "status": decision.status,
                    "reasoning": decision.reasoning, "needs_manual_review": decision.needs_manual_review,
                })
                result.alternatives.all().delete()
                ClassificationAlternative.objects.bulk_create([
                    ClassificationAlternative(classification_result=result, category=a.category, confidence_score=a.confidence, rank=a.rank)
                    for a in decision.alternatives
                ])
                attempt.status = ProcessingAttempt.Status.COMPLETED
                attempt.completed_at = timezone.now(); attempt.save(update_fields=["status", "completed_at"])
            outcomes.append((product, decision, "successful"))
            if progress: progress(product, decision)
        except Exception as exc:
            if attempt:
                attempt.status = ProcessingAttempt.Status.FAILED; attempt.error_message = f"{type(exc).__name__}: {exc}"[:500]
                attempt.completed_at = timezone.now(); attempt.save(update_fields=["status", "error_message", "completed_at"])
            outcomes.append((product, None, "failed"))
    stats = getattr(getattr(fallback, "provider_stats", None), "__dict__", {})
    return {"selected": len(products), "processed": sum(s != "skipped" for _,_,s in outcomes), "skipped": sum(s == "skipped" for _,_,s in outcomes), "successful": sum(s == "successful" for _,_,s in outcomes), "failed": sum(s == "failed" for _,_,s in outcomes), "outcomes": outcomes, "gemini_calls": stats.get("calls", 0), "gemini_successes": stats.get("successes", 0), "gemini_failures": stats.get("failures", 0), "deterministic_fallbacks": stats.get("failures", 0)}
