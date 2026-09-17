from pathlib import Path
from tempfile import TemporaryDirectory

import pandas as pd
from django.test import TestCase
from unittest.mock import patch

from products.models import ImportBatch, Product, ProductImage
from products.services.importer import EXPECTED_COLUMNS, ProductImportService


class ProductImportServiceTests(TestCase):
    def workbook(self, rows):
        values = {column: [row.get(column) for row in rows] for column in EXPECTED_COLUMNS}
        handle = TemporaryDirectory()
        path = Path(handle.name) / "products.xlsx"
        pd.DataFrame(values).to_excel(path, index=False)
        self.addCleanup(handle.cleanup)
        return path

    def test_imports_products_images_and_normalizes_values(self):
        row = {"Product Number": "P-001", "Product Name": "Chair", "Assembly Required": "Yes",
               "Is a Set": 0, "Stackable": "unknown", "Item Cost": "$1,234.50", "MAP": "",
               "MSRP": float("nan"), "Total CBM": 1.25, "Image 1": "https://example.com/1.jpg",
               "Image 20": "https://example.com/20.jpg"}
        batch = ImportBatch.objects.create(source_name="test.xlsx")
        service = ProductImportService(self.workbook([row]))
        service.import_file(batch)
        product = Product.objects.get()
        self.assertEqual(product.product_description, "")
        self.assertTrue(product.assembly_required)
        self.assertFalse(product.is_set)
        self.assertIsNone(product.stackable)
        self.assertEqual(str(product.item_cost), "1234.50")
        self.assertEqual(product.total_cbm, 1.25)
        self.assertEqual(list(ProductImage.objects.values_list("position", flat=True)), [1, 20])
        batch.refresh_from_db()
        self.assertEqual((batch.total_products, batch.processed_products, batch.successful_products, batch.failed_products), (1, 1, 1, 0))
        self.assertEqual(batch.status, ImportBatch.Status.COMPLETED)

    def test_bad_and_duplicate_rows_continue_and_mark_batch_with_errors(self):
        rows = [{"Product Number": "P-001"}, {"Product Number": "P-001"}, {"Product Number": " "}]
        batch = ImportBatch.objects.create(source_name="test.xlsx")
        service = ProductImportService(self.workbook(rows))
        service.import_file(batch)
        batch.refresh_from_db()
        self.assertEqual(Product.objects.count(), 1)
        self.assertEqual((batch.total_products, batch.processed_products, batch.successful_products, batch.failed_products), (3, 3, 1, 2))
        self.assertEqual(batch.status, ImportBatch.Status.COMPLETED_WITH_ERRORS)
        self.assertEqual([error["row"] for error in service.errors], [3, 4])

    def test_missing_columns_are_rejected(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "invalid.xlsx"
            pd.DataFrame([{"Product Number": "P-001"}]).to_excel(path, index=False)
            batch = ImportBatch.objects.create(source_name=path.name)
            with self.assertRaises(ValueError):
                ProductImportService(path).import_file(batch)

    def test_trailing_whitespace_in_header_is_normalized(self):
        columns = list(EXPECTED_COLUMNS)
        columns[columns.index("Product Description")] = "Product Description "
        with TemporaryDirectory() as directory:
            path = Path(directory) / "whitespace-header.xlsx"
            row = {column: None for column in columns}
            row["Product Number"] = "P-001"
            pd.DataFrame([row], columns=columns).to_excel(path, index=False)
            batch = ImportBatch.objects.create(source_name=path.name)
            ProductImportService(path).import_file(batch)
        self.assertEqual(Product.objects.count(), 1)
        self.assertEqual(Product.objects.get().product_number, "P-001")

    def test_invalid_numeric_values_are_null_and_warn(self):
        row = {"Product Number": "P-001", "Total Box Count": "many", "Pallet Count": "?",
               "Item Cost": "not-a-price", "Total CBM": "not-a-volume"}
        batch = ImportBatch.objects.create(source_name="test.xlsx")
        service = ProductImportService(self.workbook([row]))
        service.import_file(batch)
        product = Product.objects.get()
        self.assertIsNone(product.total_box_count)
        self.assertIsNone(product.pallet_count)
        self.assertIsNone(product.item_cost)
        self.assertIsNone(product.total_cbm)
        self.assertEqual(len(service.warnings), 4)

    def test_unexpected_row_failure_rolls_back_and_continues(self):
        rows = [{"Product Number": "P-001", "Image 1": "https://example.com/1.jpg"},
                {"Product Number": "P-002"}]
        batch = ImportBatch.objects.create(source_name="test.xlsx")
        service = ProductImportService(self.workbook(rows))
        original_bulk_create = ProductImage.objects.bulk_create

        def fail_first_image_insert(images, *args, **kwargs):
            if images:
                raise RuntimeError("simulated row failure")
            return original_bulk_create(images, *args, **kwargs)

        with patch.object(ProductImage.objects, "bulk_create", side_effect=fail_first_image_insert):
            service.import_file(batch)
        batch.refresh_from_db()
        self.assertEqual(Product.objects.count(), 1)
        self.assertEqual(Product.objects.get().product_number, "P-002")
        self.assertEqual(ProductImage.objects.count(), 0)
        self.assertEqual((batch.processed_products, batch.successful_products, batch.failed_products), (2, 1, 1))
        self.assertEqual(batch.status, ImportBatch.Status.COMPLETED_WITH_ERRORS)
        self.assertEqual(service.errors[0]["error"], "RuntimeError: simulated row failure")
