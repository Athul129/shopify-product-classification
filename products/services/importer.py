from __future__ import annotations

from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any
import logging

import pandas as pd
from django.db import IntegrityError, transaction

from products.models import ImportBatch, Product, ProductImage


logger = logging.getLogger(__name__)


IMAGE_COLUMNS = [f"Image {number}" for number in range(1, 21)]
EXPECTED_COLUMNS = {
    "Product Number", "Model Number", "Product Category", "Product Sub Category",
    "Collection Name", "Color Collection", "Product Color", "Product Name",
    "Product Description", "Bullets", "Set Includes", "Product Weight", "Materials",
    "Product Dimensions", "Assembly Required", "Is a Set", "Stackable",
    "Country Of Origin", "Item Cost", "MAP", "MSRP", "Shipping Method",
    "Total Box Count", "Pallet Count", "Shipping Weight", "Total CBM",
    "Package Dimensions", "Product URL", *IMAGE_COLUMNS,
}

PRODUCT_FIELD_MAP = {
    "Model Number": "model_number",
    "Product Category": "product_category", "Product Sub Category": "product_sub_category",
    "Collection Name": "collection_name", "Color Collection": "color_collection",
    "Product Color": "product_color", "Product Name": "product_name",
    "Product Description": "product_description", "Bullets": "bullets",
    "Set Includes": "set_includes", "Product Weight": "product_weight",
    "Materials": "materials", "Product Dimensions": "product_dimensions",
    "Country Of Origin": "country_of_origin", "Shipping Method": "shipping_method",
    "Shipping Weight": "shipping_weight", "Package Dimensions": "package_dimensions",
    "Product URL": "product_url",
}
DECIMAL_FIELDS = {"Item Cost": "item_cost", "MAP": "map", "MSRP": "msrp", "Total CBM": "total_cbm"}
BOOLEAN_FIELDS = {"Assembly Required": "assembly_required", "Is a Set": "is_set", "Stackable": "stackable"}
INTEGER_FIELDS = {"Total Box Count": "total_box_count", "Pallet Count": "pallet_count"}


def _empty(value: Any) -> bool:
    return value is None or (isinstance(value, str) and not value.strip()) or pd.isna(value)


def normalize_text(value: Any) -> str:
    if _empty(value):
        return ""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value).strip()


def normalize_decimal(value: Any) -> Decimal | None:
    if _empty(value):
        return None
    try:
        return Decimal(str(value).strip().replace(",", "").replace("$", ""))
    except (InvalidOperation, ValueError):
        return None


def normalize_boolean(value: Any) -> bool | None:
    if _empty(value):
        return None
    if isinstance(value, bool):
        return value
    normalized = str(value).strip().lower()
    if normalized in {"true", "yes", "y", "1", "t"}:
        return True
    if normalized in {"false", "no", "n", "0", "f"}:
        return False
    return None


def normalize_integer(value: Any) -> int | None:
    if _empty(value):
        return None
    try:
        number = float(value)
        return int(number) if number.is_integer() else None
    except (TypeError, ValueError):
        return None


class ProductImportService:
    def __init__(self, file_path: str | Path):
        self.file_path = Path(file_path)
        self.errors: list[dict[str, str | int]] = []
        self.warnings: list[dict[str, str | int]] = []
        self.products_created = 0
        self.images_created = 0

    def import_file(self, batch: ImportBatch) -> ImportBatch:
        dataframe = pd.read_excel(self.file_path, engine="openpyxl")
        normalized_columns = [str(column).strip() for column in dataframe.columns]
        if len(normalized_columns) != len(set(normalized_columns)):
            duplicates = sorted({column for column in normalized_columns if normalized_columns.count(column) > 1})
            raise ValueError("Duplicate Excel columns after whitespace normalization: " + ", ".join(duplicates))
        dataframe.columns = normalized_columns
        missing = sorted(EXPECTED_COLUMNS - set(dataframe.columns))
        if missing:
            raise ValueError("Missing expected Excel columns: " + ", ".join(missing))

        batch.total_products = len(dataframe)
        batch.status = ImportBatch.Status.PROCESSING
        batch.save(update_fields=["total_products", "status", "updated_at"])
        seen_product_numbers: set[str] = set()

        for index, row in dataframe.iterrows():
            excel_row = index + 2
            try:
                product_number = normalize_text(row.get("Product Number"))
                if not product_number:
                    raise ValueError("Product Number is empty")
                if product_number in seen_product_numbers:
                    raise ValueError(f"duplicate Product Number '{product_number}' in this batch")
                seen_product_numbers.add(product_number)
                with transaction.atomic():
                    product = Product.objects.create(import_batch=batch, product_number=product_number, **self._product_data(row, excel_row))
                    images = [ProductImage(product=product, image_url=normalize_text(row.get(column)), position=position)
                              for position, column in enumerate(IMAGE_COLUMNS, 1) if normalize_text(row.get(column))]
                    ProductImage.objects.bulk_create(images)
                self.products_created += 1
                self.images_created += len(images)
                batch.successful_products += 1
            except (IntegrityError, ValueError, TypeError, OverflowError) as exc:
                self.errors.append({"row": excel_row, "error": str(exc)[:500]})
                batch.failed_products += 1
            except Exception as exc:
                # Keep the import moving, while retaining the traceback for developers.
                logger.exception("Unexpected error importing Excel row %s", excel_row)
                self.errors.append({
                    "row": excel_row,
                    "error": f"{type(exc).__name__}: {exc}",
                })
                batch.failed_products += 1
            finally:
                batch.processed_products += 1
                batch.save(update_fields=["processed_products", "successful_products", "failed_products", "updated_at"])

        batch.status = ImportBatch.Status.COMPLETED_WITH_ERRORS if self.errors else ImportBatch.Status.COMPLETED
        batch.save(update_fields=["status", "updated_at"])
        return batch

    def _product_data(self, row: Any, excel_row: int) -> dict[str, Any]:
        data = {field: normalize_text(row.get(column)) for column, field in PRODUCT_FIELD_MAP.items()}
        for column, field in DECIMAL_FIELDS.items():
            value = row.get(column)
            data[field] = normalize_decimal(value)
            if not _empty(value) and data[field] is None:
                self.warnings.append({"row": excel_row, "warning": f"Invalid decimal in {column}; stored as NULL"})
        for column, field in BOOLEAN_FIELDS.items():
            value = row.get(column)
            data[field] = normalize_boolean(value)
            if not _empty(value) and data[field] is None:
                self.warnings.append({"row": excel_row, "warning": f"Unrecognized boolean in {column}; stored as NULL"})
        for column, field in INTEGER_FIELDS.items():
            value = row.get(column)
            data[field] = normalize_integer(value)
            if not _empty(value) and data[field] is None:
                self.warnings.append({"row": excel_row, "warning": f"Invalid integer in {column}; stored as NULL"})
        return data
