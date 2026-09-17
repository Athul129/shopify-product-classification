import json
from pathlib import Path
from tempfile import TemporaryDirectory

from django.test import TestCase

from taxonomy.models import TaxonomyAttribute, TaxonomyAttributeValue, TaxonomyCategory
from taxonomy.services.importer import TaxonomyImportService


class TaxonomyImportServiceTests(TestCase):
    def write_source(self, categories, attributes):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        root = Path(directory.name)
        (root / "categories.json").write_text(json.dumps({"version": "2026-08", "categories": categories}), encoding="utf-8")
        (root / "attributes.json").write_text(json.dumps({"version": "2026-08", "attributes": attributes}), encoding="utf-8")
        return root

    def test_imports_categories_parent_attributes_and_values(self):
        source = self.write_source([
            {"id": "gid://shopify/TaxonomyCategory/1", "name": "Home", "full_name": "Home", "children": [
                {"id": "gid://shopify/TaxonomyCategory/2", "name": "Furniture", "full_name": "Home > Furniture", "attributes": [{"id": "gid://shopify/TaxonomyAttribute/10", "name": "Material"}]}
            ]}
        ], [{"id": "gid://shopify/TaxonomyAttribute/10", "name": "Material", "values": [{"id": "gid://shopify/TaxonomyValue/20", "name": "Wood"}]}])
        TaxonomyImportService(source).import_source()
        child = TaxonomyCategory.objects.get(taxonomy_id="gid://shopify/TaxonomyCategory/2")
        self.assertEqual(child.parent.taxonomy_id, "gid://shopify/TaxonomyCategory/1")
        attribute = TaxonomyAttribute.objects.get(category=child, external_id="gid://shopify/TaxonomyAttribute/10")
        self.assertEqual(attribute.name, "Material")
        self.assertEqual(TaxonomyAttributeValue.objects.get(attribute=attribute).external_id, "gid://shopify/TaxonomyValue/20")

    def test_rerun_updates_without_duplicates(self):
        source = self.write_source([{ "id": "c1", "name": "Home", "full_name": "Home" }], [])
        TaxonomyImportService(source).import_source()
        (source / "categories.json").write_text(json.dumps({"categories": [{"id": "c1", "name": "Updated Home", "full_name": "Updated Home"}]}), encoding="utf-8")
        TaxonomyImportService(source).import_source()
        self.assertEqual(TaxonomyCategory.objects.count(), 1)
        self.assertEqual(TaxonomyCategory.objects.get().name, "Updated Home")

    def test_malformed_category_does_not_stop_import(self):
        source = self.write_source([{ "name": "Invalid" }, { "id": "valid", "name": "Valid", "full_name": "Valid" }], [])
        service = TaxonomyImportService(source).import_source()
        self.assertEqual(TaxonomyCategory.objects.count(), 1)
        self.assertEqual(len(service.errors), 1)
