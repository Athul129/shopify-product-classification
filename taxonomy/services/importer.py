from __future__ import annotations

import gzip
import json
import logging
from pathlib import Path
from typing import Any, Iterable

from django.db import transaction
from django.utils import timezone

from taxonomy.models import TaxonomyAttribute, TaxonomyAttributeValue, TaxonomyCategory

logger = logging.getLogger(__name__)
RELEASE_VERSION = "2026-08"
BATCH_SIZE = 1000


def _read_json(path: Path) -> dict[str, Any]:
    opener = gzip.open if path.suffix == ".gz" else open
    with opener(path, "rt", encoding="utf-8") as stream:
        document = json.load(stream)
    if not isinstance(document, dict):
        raise ValueError(f"Expected a JSON object in {path.name}")
    return document


def _text(value: Any) -> str:
    return str(value).strip() if value is not None else ""


def _records(value: Any) -> Iterable[dict[str, Any]]:
    return (item for item in value if isinstance(item, dict)) if isinstance(value, list) else ()


class TaxonomyImportService:
    def __init__(self, source: str | Path):
        self.source = Path(source)
        self.errors: list[dict[str, str]] = []
        self.categories_created = self.categories_updated = 0
        self.attributes_created = self.attributes_updated = 0
        self.values_created = self.values_updated = 0

    def import_source(self) -> "TaxonomyImportService":
        categories_path, attributes_path = self._resolve_files()
        categories_doc = _read_json(categories_path)
        attributes_doc = _read_json(attributes_path) if attributes_path else {"attributes": []}
        for document, path in ((categories_doc, categories_path), (attributes_doc, attributes_path)):
            if document.get("version") and _text(document["version"]) != RELEASE_VERSION:
                raise ValueError(f"Expected Shopify taxonomy release {RELEASE_VERSION} in {path.name}")
        records = list(self._category_records(categories_doc))
        category_ids = self._import_categories(records)
        definitions = {_text(item.get("id")): item for item in _records(attributes_doc.get("attributes")) if _text(item.get("id"))}
        assignments = self._build_attribute_assignments(records, category_ids, definitions)
        self._import_attributes_and_values(assignments)
        return self

    def _import_categories(self, records):
        valid = {}
        for record in records:
            try:
                key, name = _text(record.get("id")), _text(record.get("name"))
                full_path = _text(record.get("full_name") or record.get("full_path") or name)
                if not key: raise ValueError("category has no id")
                if not name or not full_path: raise ValueError("category is missing name or full_name")
                valid[key] = {"name": name, "full_path": full_path, "is_active": record.get("is_active", True)}
            except Exception as exc:
                self._record_error("category", record, exc)
        ids = list(valid)
        existing = {obj.taxonomy_id: obj for obj in TaxonomyCategory.objects.filter(taxonomy_id__in=ids)}
        to_create = [TaxonomyCategory(taxonomy_id=key, **data) for key, data in valid.items() if key not in existing]
        with transaction.atomic():
            for start in range(0, len(to_create), BATCH_SIZE): TaxonomyCategory.objects.bulk_create(to_create[start:start + BATCH_SIZE])
        self.categories_created = len(to_create)
        current = {obj.taxonomy_id: obj for obj in TaxonomyCategory.objects.filter(taxonomy_id__in=ids)}
        now = timezone.now(); updates = []
        for key, data in valid.items():
            obj = current[key]
            if (obj.name, obj.full_path, obj.is_active) != (data["name"], data["full_path"], data["is_active"]):
                obj.name, obj.full_path, obj.is_active, obj.updated_at = data["name"], data["full_path"], data["is_active"], now
                updates.append(obj)
        with transaction.atomic():
            for start in range(0, len(updates), BATCH_SIZE): TaxonomyCategory.objects.bulk_update(updates[start:start + BATCH_SIZE], ["name", "full_path", "is_active", "updated_at"])
        self.categories_updated = len(valid) - self.categories_created
        parent_updates = []
        for record in records:
            obj = current.get(_text(record.get("id")))
            if not obj: continue
            parent = current.get(_text(record.get("parent_id") or record.get("parent_gid")))
            if obj.parent_id != (parent.id if parent else None): obj.parent, obj.updated_at = parent, now; parent_updates.append(obj)
        with transaction.atomic():
            for start in range(0, len(parent_updates), BATCH_SIZE): TaxonomyCategory.objects.bulk_update(parent_updates[start:start + BATCH_SIZE], ["parent", "updated_at"])
        return current

    def _build_attribute_assignments(self, records, category_ids, definitions):
        assignments = []
        for category_record in records:
            category = category_ids.get(_text(category_record.get("id")))
            if not category: continue
            for record in _records(category_record.get("attributes")):
                try:
                    external_id = _text(record.get("id")); definition = definitions.get(external_id, {})
                    name = _text(record.get("name") or definition.get("name"))
                    if not name: raise ValueError("attribute is missing name")
                    assignments.append((category, external_id, name, definition.get("values") or record.get("values") or []))
                except Exception as exc: self._record_error("attribute", record, exc)
        return assignments

    def _import_attributes_and_values(self, assignments):
        category_ids = {category.id for category, _, _, _ in assignments}
        existing = {(obj.category_id, obj.name): obj for obj in TaxonomyAttribute.objects.filter(category_id__in=category_ids)}
        to_create = [TaxonomyAttribute(category=category, name=name, external_id=external_id) for category, external_id, name, _ in assignments if (category.id, name) not in existing]
        with transaction.atomic():
            for start in range(0, len(to_create), BATCH_SIZE): TaxonomyAttribute.objects.bulk_create(to_create[start:start + BATCH_SIZE])
        self.attributes_created = len(to_create)
        attrs = {(obj.category_id, obj.name): obj for obj in TaxonomyAttribute.objects.filter(category_id__in=category_ids)}
        now = timezone.now(); updates = []
        for category, external_id, name, _ in assignments:
            obj = attrs[(category.id, name)]
            if obj.external_id != external_id: obj.external_id, obj.updated_at = external_id, now; updates.append(obj)
        with transaction.atomic():
            for start in range(0, len(updates), BATCH_SIZE): TaxonomyAttribute.objects.bulk_update(updates[start:start + BATCH_SIZE], ["external_id", "updated_at"])
        self.attributes_updated = len(assignments) - self.attributes_created
        value_records = []
        for category, _, name, values in assignments:
            attribute = attrs[(category.id, name)]
            for record in _records(values):
                try:
                    value = _text(record.get("name") or record.get("value"))
                    if not value: raise ValueError("attribute value is missing value/name")
                    value_records.append((attribute, value, _text(record.get("id"))))
                except Exception as exc: self._record_error("attribute value", record, exc)
        self._import_values(value_records)

    def _import_values(self, records):
        for start in range(0, len(records), BATCH_SIZE):
            batch = records[start:start + BATCH_SIZE]
            unique = {(attribute.id, value): (attribute, value, external_id) for attribute, value, external_id in batch}
            attribute_ids = {attribute_id for attribute_id, _ in unique}
            value_names = {value for _, value in unique}
            existing = {(obj.attribute_id, obj.value): obj for obj in TaxonomyAttributeValue.objects.filter(attribute_id__in=attribute_ids, value__in=value_names)}
            to_create = [TaxonomyAttributeValue(attribute=attribute, value=value, external_id=external_id)
                         for key, (attribute, value, external_id) in unique.items() if key not in existing]
            with transaction.atomic():
                for create_start in range(0, len(to_create), BATCH_SIZE): TaxonomyAttributeValue.objects.bulk_create(to_create[create_start:create_start + BATCH_SIZE])
            self.values_created += len(to_create)
            current = {(obj.attribute_id, obj.value): obj for obj in TaxonomyAttributeValue.objects.filter(attribute_id__in=attribute_ids, value__in=value_names)}
            now = timezone.now(); updates = []
            for key, (attribute, value, external_id) in unique.items():
                obj = current[key]
                if obj.external_id != external_id: obj.external_id, obj.updated_at = external_id, now; updates.append(obj)
            with transaction.atomic():
                for update_start in range(0, len(updates), BATCH_SIZE): TaxonomyAttributeValue.objects.bulk_update(updates[update_start:update_start + BATCH_SIZE], ["external_id", "updated_at"])
            self.values_updated += len(unique) - len(to_create)

    def _category_records(self, document, parent_id=""):
        if document.get("verticals"):
            for vertical in _records(document["verticals"]): yield from self._category_records({"categories": vertical.get("categories", [])}, parent_id)
            return
        for record in _records(document.get("categories") or []):
            current = dict(record)
            if parent_id and not current.get("parent_id"): current["parent_id"] = parent_id
            yield current
            yield from self._category_records({"categories": record.get("children") or record.get("categories") or []}, _text(record.get("id")))

    def _resolve_files(self):
        if self.source.is_file():
            if self.source.suffixes[-2:] == [".json", ".gz"] or self.source.suffix == ".json":
                if "categories" in self.source.name:
                    attrs = self.source.with_name(self.source.name.replace("categories", "attributes")); return self.source, attrs if attrs.exists() else None
                if "attributes" in self.source.name:
                    categories = self.source.with_name(self.source.name.replace("attributes", "categories"))
                    if not categories.exists(): raise ValueError("A categories JSON file is required with an attributes file")
                    return categories, self.source
            raise ValueError("Expected a Shopify categories or attributes JSON/JSON.GZ file")
        if not self.source.is_dir(): raise ValueError(f"Taxonomy source not found: {self.source}")
        categories = next(iter(list(self.source.rglob("categories*.json")) + list(self.source.rglob("categories*.json.gz"))), None)
        if not categories: raise ValueError("Taxonomy directory does not contain a categories JSON file")
        attributes = next(iter(list(self.source.rglob("attributes*.json")) + list(self.source.rglob("attributes*.json.gz"))), None)
        return categories, attributes

    def _record_error(self, kind, record, exc):
        logger.exception("Invalid taxonomy %s record %r", kind, record.get("id"))
        self.errors.append({"type": kind, "id": _text(record.get("id")), "error": f"{type(exc).__name__}: {exc}"[:500]})
