from django.core.management.base import BaseCommand, CommandError
from products.models import ImportBatch
from classification.services.execution import CountingProvider, execute_classification
from classification.services.execution import build_ai_fallback
from classification.services.engine import ClassificationEngine

class Command(BaseCommand):
    help = "Classify products from an explicitly selected import batch."
    def add_arguments(self, parser):
        parser.add_argument("--batch-id", type=int, required=True)
        parser.add_argument("--limit", type=int)
        parser.add_argument("--overwrite", action="store_true")
    def handle(self, *args, **options):
        try: batch = ImportBatch.objects.get(pk=options["batch_id"])
        except ImportBatch.DoesNotExist as exc: raise CommandError("Import batch not found") from exc
        if options["limit"] is not None and options["limit"] <= 0: raise CommandError("--limit must be greater than zero")
        try: stats = execute_classification(batch, options["limit"], options["overwrite"], engine_class=ClassificationEngine, fallback_builder=build_ai_fallback)
        except Exception as exc: raise CommandError(str(exc)) from exc
        for product, decision, state in stats["outcomes"]:
            if decision: self.stdout.write(f"{product.product_number}: {decision.category.full_path if decision.category else 'None'} ({decision.confidence_score}) [{decision.status}]")
            elif state == "failed": self.stderr.write(f"Product {product.id}: classification failed")
        keys = ("selected", "processed", "skipped", "successful", "failed", "gemini_calls", "gemini_successes", "gemini_failures", "deterministic_fallbacks")
        self.stdout.write(self.style.SUCCESS(f"Classification completed for batch {batch.id}: {', '.join(f'{k}={stats[k]}' for k in keys)}"))
