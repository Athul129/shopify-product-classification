from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from products.models import ImportBatch
from products.services.importer import ProductImportService


class Command(BaseCommand):
    help = "Import products and image URLs from an Excel workbook."

    def add_arguments(self, parser):
        parser.add_argument("excel_file", type=str)

    def handle(self, *args, **options):
        file_path = Path(options["excel_file"])
        if not file_path.is_file():
            raise CommandError(f"Excel file not found: {file_path}")
        batch = ImportBatch.objects.create(source_name=file_path.name)
        service = ProductImportService(file_path)
        try:
            service.import_file(batch)
        except Exception as exc:
            batch.status = ImportBatch.Status.FAILED
            batch.save(update_fields=["status", "updated_at"])
            raise CommandError(str(exc)) from exc
        self.stdout.write(self.style.SUCCESS("\nImport completed"))
        self.stdout.write(f"Total rows: {batch.total_products}")
        self.stdout.write(f"Successful: {batch.successful_products}")
        self.stdout.write(f"Failed: {batch.failed_products}")
        self.stdout.write(f"Products created: {service.products_created}")
        self.stdout.write(f"Images created: {service.images_created}")
        self.stdout.write(f"Warnings: {len(service.warnings)}")
        self.stdout.write(f"Batch status: {batch.get_status_display()}")
        for error in service.errors:
            self.stderr.write(f"Row {error['row']}: {error['error']}")
