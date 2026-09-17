from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from taxonomy.services.importer import RELEASE_VERSION, TaxonomyImportService


class Command(BaseCommand):
    help = f"Import Shopify Product Taxonomy release v{RELEASE_VERSION} JSON distribution files."

    def add_arguments(self, parser):
        parser.add_argument("taxonomy_source", type=str, help="Extracted taxonomy directory or categories/attributes JSON file")

    def handle(self, *args, **options):
        try:
            service = TaxonomyImportService(Path(options["taxonomy_source"])).import_source()
        except (OSError, ValueError) as exc:
            raise CommandError(str(exc)) from exc
        self.stdout.write(self.style.SUCCESS("Taxonomy import completed"))
        for label in ("categories_created", "categories_updated", "attributes_created", "attributes_updated", "values_created", "values_updated"):
            self.stdout.write(f"{label.replace('_', ' ').title()}: {getattr(service, label)}")
        self.stdout.write(f"Errors: {len(service.errors)}")
