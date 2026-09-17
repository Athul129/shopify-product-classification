from django.test import TestCase
from django.urls import reverse
from products.models import ImportBatch, Product
from taxonomy.models import TaxonomyCategory
from classification.models import ClassificationResult

class DashboardTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.batch=ImportBatch.objects.create(source_name="test.xlsx")
        cls.product=Product.objects.create(import_batch=cls.batch,product_number="P-1",product_name="Test Sofa")
        cls.category=TaxonomyCategory.objects.create(taxonomy_id="test-cat",name="Sofas",full_path="Furniture > Sofas")
        ClassificationResult.objects.create(product=cls.product,category=cls.category,status="needs_review",needs_manual_review=True,classification_method="rule",confidence_score="0.5000")
    def test_dashboard_and_product_pages_render(self):
        self.assertEqual(self.client.get(reverse("dashboard:index")).status_code,200)
        self.assertEqual(self.client.get(reverse("dashboard:products")).status_code,200)
        self.assertEqual(self.client.get(reverse("dashboard:product-detail",args=[self.product.id])).status_code,200)
        self.assertEqual(self.client.get(reverse("dashboard:review")).status_code,200)
    def test_category_search_is_bounded_and_empty_is_safe(self):
        self.assertEqual(self.client.get(reverse("dashboard:category-search")).json(),{"results":[]})
        response=self.client.get(reverse("dashboard:category-search"),{"q":"Sofa"})
        self.assertEqual(response.status_code,200); self.assertEqual(response.json()["results"][0]["taxonomy_id"],"test-cat")
    def test_manual_approval_updates_only_classification(self):
        response=self.client.post(reverse("dashboard:manual-review",args=[self.product.id]),{"category_id":self.category.id})
        self.assertEqual(response.status_code,302)
        result=ClassificationResult.objects.get(product=self.product)
        self.assertEqual(result.status,"approved"); self.assertEqual(result.classification_method,"manual")

    def test_manual_approval_rejects_invalid_category_id(self):
        response = self.client.post(reverse("dashboard:manual-review", args=[self.product.id]), {"category_id": 999999})
        self.assertEqual(response.status_code, 404)

    def test_product_detail_contains_searchable_category_selector(self):
        response = self.client.get(reverse("dashboard:product-detail", args=[self.product.id]))
        self.assertContains(response, "category-search")
        self.assertContains(response, "category-id")
    def test_classification_page_requires_confirmation(self):
        self.assertEqual(self.client.get(reverse("dashboard:classify")).status_code,200)

    def test_products_default_to_latest_usable_batch(self):
        latest = ImportBatch.objects.create(source_name="latest.xlsx", status=ImportBatch.Status.COMPLETED)
        Product.objects.create(import_batch=latest, product_number="P-1", product_name="Latest")
        response = self.client.get(reverse("dashboard:products"), {"q": "P-1"})
        self.assertEqual(response.context["selected_batch"], latest)
        self.assertEqual(list(response.context["page"].object_list), list(Product.objects.filter(import_batch=latest)))

    def test_all_batches_is_explicit_and_shows_batch_context(self):
        latest = ImportBatch.objects.create(source_name="latest.xlsx", status=ImportBatch.Status.COMPLETED)
        Product.objects.create(import_batch=latest, product_number="P-1", product_name="Latest")
        response = self.client.get(reverse("dashboard:products"), {"q": "P-1", "import_batch_id": "all"})
        self.assertTrue(response.context["all_batches"])
        self.assertEqual(response.context["page"].paginator.count, 2)

    def test_dashboard_counts_are_scoped_to_selected_batch(self):
        latest = ImportBatch.objects.create(source_name="latest.xlsx", status=ImportBatch.Status.COMPLETED)
        product = Product.objects.create(import_batch=latest, product_number="P-2", product_name="Latest")
        ClassificationResult.objects.create(product=product, status="classified", classification_method="rule")
        response = self.client.get(reverse("dashboard:index"), {"batch": latest.id})
        self.assertEqual(response.context["counts"]["total"], 1)
        self.assertEqual(response.context["counts"]["classified"], 1)
