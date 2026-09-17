from types import SimpleNamespace
from unittest.mock import patch

from django.core.management import call_command
from django.test import TestCase

from classification.models import ClassificationAlternative, ClassificationResult, ProcessingAttempt
from classification.services.confidence import ConfidenceEvaluator
from classification.services.engine import ClassificationEngine
from classification.services.ai_fallback import AIFallbackOrchestrator
from classification.services.candidate_generator import CandidateGenerator
from classification.services.text_builder import ProductTextBuilder
from classification.services.providers.base import ProviderClassification
from classification.services.providers.mock import MockClassificationProvider
from classification.services.providers.factory import ProviderConfigurationError, get_classification_provider
from classification.services.providers.gemini import GeminiClassificationProvider
from classification.management.commands.classify_products import CountingProvider
from classification.services.taxonomy_index import TaxonomyCandidateIndex
from products.models import ImportBatch, Product
from taxonomy.models import TaxonomyCategory


class ClassificationFoundationTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        home = TaxonomyCategory.objects.create(taxonomy_id="home", name="Home & Garden", full_path="Home & Garden")
        furniture = TaxonomyCategory.objects.create(taxonomy_id="furniture", name="Furniture", full_path="Home & Garden > Furniture", parent=home)
        cls.sofas = TaxonomyCategory.objects.create(taxonomy_id="sofas", name="Sofas and Armchairs", full_path="Home & Garden > Furniture > Living Room > Sofas and Armchairs", parent=furniture)
        cls.sectionals = TaxonomyCategory.objects.create(taxonomy_id="sectionals", name="Sectional Sofas", full_path="Home & Garden > Furniture > Living Room > Sectional Sofas", parent=furniture)
        outdoor = TaxonomyCategory.objects.create(taxonomy_id="outdoor", name="Outdoor Furniture", full_path="Home & Garden > Outdoor Furniture", parent=home)
        cls.outdoor_chairs = TaxonomyCategory.objects.create(taxonomy_id="outdoor-chairs", name="Outdoor Chairs", full_path="Home & Garden > Outdoor Furniture > Outdoor Chairs", parent=outdoor)
        cls.dining_chairs = TaxonomyCategory.objects.create(taxonomy_id="dining-chairs", name="Dining Chairs", full_path="Home & Garden > Furniture > Dining Chairs", parent=furniture)
        cls.dining_tables = TaxonomyCategory.objects.create(taxonomy_id="dining-tables", name="Dining Tables", full_path="Home & Garden > Furniture > Tables > Kitchen & Dining Room Tables > Dining Tables", parent=furniture)
        cls.outdoor_tables = TaxonomyCategory.objects.create(taxonomy_id="outdoor-tables", name="Outdoor Dining Tables", full_path="Home & Garden > Furniture > Outdoor Furniture > Outdoor Tables > Dining Tables", parent=furniture)
        cls.furniture_sets = TaxonomyCategory.objects.create(taxonomy_id="furniture-sets", name="Kitchen & Dining Furniture Sets", full_path="Home & Garden > Furniture > Furniture Sets > Kitchen & Dining Furniture Sets", parent=furniture)
        cls.sectional_branch = TaxonomyCategory.objects.create(taxonomy_id="sectional-branch", name="Sectional Sofas", full_path="Home & Garden > Furniture > Outdoor Furniture > Outdoor Sofas > Sectional Sofas", parent=furniture)
        cls.loveseat = TaxonomyCategory.objects.create(taxonomy_id="loveseat", name="Loveseat Sofas", full_path="Home & Garden > Furniture > Outdoor Sofas > Loveseat Sofas", parent=furniture)
        cls.trash_cans = TaxonomyCategory.objects.create(taxonomy_id="trash-cans", name="Trash Cans", full_path="Home & Garden > Household Supplies > Waste Containment > Trash Cans", parent=home)
        cls.wastebaskets = TaxonomyCategory.objects.create(taxonomy_id="wastebaskets", name="Wastebaskets", full_path="Home & Garden > Household Supplies > Waste Containment > Wastebaskets", parent=home)

    def product(self, **values):
        batch, _ = ImportBatch.objects.get_or_create(source_name="classification-test.xlsx")
        defaults = {field: "" for field in ("product_name", "product_category", "product_sub_category", "collection_name", "product_description", "bullets", "materials", "set_includes", "product_color", "model_number")}
        defaults.update(values)
        return Product.objects.create(import_batch=batch, product_number=f"P-{Product.objects.count() + 1}", **defaults)

    def engine(self):
        return ClassificationEngine(TaxonomyCandidateIndex.from_database())

    def test_obvious_sofa_is_classified(self):
        product = self.product(product_name="Sofas and Armchairs", product_category="Living Room", product_sub_category="Sofas and Armchairs", product_description="Bonded leather sofa")
        decision = self.engine().classify(product)
        self.assertEqual(decision.category, self.sofas)
        self.assertEqual(decision.status, "classified")

    def test_outdoor_chair_is_classified(self):
        product = self.product(product_name="Outdoor Chairs", product_category="Outdoor Furniture", product_sub_category="Outdoor Chairs", product_description="Patio chair")
        decision = self.engine().classify(product)
        self.assertEqual(decision.category, self.outdoor_chairs)
        self.assertEqual(decision.status, "classified")

    def test_missing_description_and_images_do_not_block_classification(self):
        product = self.product(product_name="Outdoor Chairs", product_category="Outdoor Furniture", product_sub_category="Outdoor Chairs")
        decision = self.engine().classify(product)
        self.assertEqual(decision.category, self.outdoor_chairs)

    def test_ambiguous_product_requires_review_and_has_alternatives(self):
        product = self.product(product_name="Sofa", product_category="Living Room")
        decision = self.engine().classify(product)
        self.assertEqual(decision.status, "needs_review")
        self.assertTrue(decision.needs_manual_review)
        self.assertGreaterEqual(len(decision.alternatives), 1)

    def test_sparse_product_has_no_candidate(self):
        decision = self.engine().classify(self.product())
        self.assertIsNone(decision.category)
        self.assertEqual(decision.status, "needs_review")
        self.assertTrue(decision.needs_manual_review)

    def test_exact_category_and_subcategory_matching(self):
        product = self.product(product_name="Dining Chairs", product_category="Furniture", product_sub_category="Dining Chairs")
        decision = self.engine().classify(product)
        self.assertEqual(decision.category, self.dining_chairs)

    def test_confidence_threshold_and_margin_rules(self):
        evaluator = ConfidenceEvaluator()
        self.assertEqual(evaluator.evaluate(0.80, 0.10).status, "classified")
        self.assertEqual(evaluator.evaluate(0.79, 0.20).status, "needs_review")
        self.assertEqual(evaluator.evaluate(0.90, 0.09).status, "needs_review")
        self.assertEqual(evaluator.evaluate(0.20, 1.00).status, "needs_review")

    def test_mock_provider_implements_provider_interface(self):
        provider = MockClassificationProvider(ProviderClassification("sofas", 0.9, "test"))
        result = provider.classify("sofa", ["sofas"])
        self.assertEqual(result.category_id, "sofas")
        self.assertEqual(len(provider.calls), 1)

    def test_provider_factory_returns_none_when_disabled_or_unconfigured(self):
        self.assertIsNone(get_classification_provider(False, "mock"))
        self.assertIsNone(get_classification_provider(True, ""))

    def test_provider_factory_supports_mock(self):
        self.assertIsInstance(get_classification_provider(True, "mock"), MockClassificationProvider)

    def test_provider_factory_rejects_unknown_provider(self):
        with self.assertRaises(ProviderConfigurationError):
            get_classification_provider(True, "unknown")

    def gemini_provider(self, response_text):
        class FakeTypes:
            GenerateContentConfig = staticmethod(lambda **kwargs: kwargs)
        client = SimpleNamespace(models=SimpleNamespace(generate_content=lambda **kwargs: SimpleNamespace(text=response_text)))
        return GeminiClassificationProvider(api_key="test-only", client=client, types_module=FakeTypes)

    def test_gemini_valid_response_and_bounded_prompt(self):
        provider = self.gemini_provider('{"category_id":"gid-sofa","confidence":0.91,"reasoning":"Sofa evidence."}')
        result = provider.classify("normalized sofa context", ["gid-sofa | Home > Sofas"])
        self.assertEqual(result.category_id, "gid-sofa")
        self.assertEqual(result.confidence, 0.91)

    @patch("classification.services.providers.gemini.time.sleep")
    def test_gemini_retries_rate_limit_then_succeeds(self, sleep):
        class FakeTypes:
            GenerateContentConfig = staticmethod(lambda **kwargs: kwargs)
        calls = [0]
        def generate(**kwargs):
            calls[0] += 1
            if calls[0] == 1:
                raise RuntimeError("429 RESOURCE_EXHAUSTED retryDelay: 3s")
            return SimpleNamespace(text='{"category_id":"gid-sofa","confidence":0.9,"reasoning":"ok"}')
        provider = GeminiClassificationProvider(api_key="test-only", client=SimpleNamespace(models=SimpleNamespace(generate_content=generate)), types_module=FakeTypes, max_retries=2)
        self.assertEqual(provider.classify("context", ["gid-sofa | Home > Sofas"]).category_id, "gid-sofa")
        self.assertEqual(calls[0], 2); sleep.assert_called_once_with(3.0)

    @patch("classification.services.providers.gemini.time.sleep")
    def test_gemini_rate_limit_stops_after_max_retries(self, sleep):
        class FakeTypes:
            GenerateContentConfig = staticmethod(lambda **kwargs: kwargs)
        client = SimpleNamespace(models=SimpleNamespace(generate_content=lambda **kwargs: (_ for _ in ()).throw(RuntimeError("429 RESOURCE_EXHAUSTED"))))
        provider = GeminiClassificationProvider(api_key="test-only", client=client, types_module=FakeTypes, max_retries=2)
        with self.assertRaises(RuntimeError): provider.classify("context", ["gid-sofa | Home > Sofas"])
        self.assertEqual(sleep.call_count, 2)

    @patch("classification.services.providers.gemini.time.sleep")
    def test_gemini_non_rate_limit_error_is_not_retried(self, sleep):
        class FakeTypes:
            GenerateContentConfig = staticmethod(lambda **kwargs: kwargs)
        client = SimpleNamespace(models=SimpleNamespace(generate_content=lambda **kwargs: (_ for _ in ()).throw(RuntimeError("invalid model"))))
        provider = GeminiClassificationProvider(api_key="test-only", client=client, types_module=FakeTypes, max_retries=2)
        with self.assertRaises(RuntimeError): provider.classify("context", ["gid-sofa | Home > Sofas"])
        sleep.assert_not_called()

    def test_gemini_rejects_invalid_responses(self):
        for response in (
            '{"category_id":"gid-other","confidence":0.9,"reasoning":"x"}',
            '{"category_id":"gid-sofa","confidence":1.1,"reasoning":"x"}',
            "not json",
            '{"category_id":"gid-sofa","confidence":0.9,"reasoning":""}',
        ):
            with self.assertRaises(ValueError):
                self.gemini_provider(response).classify("context", ["gid-sofa | Home > Sofas"])

    def test_gemini_api_exception_and_timeout_propagate_to_fallback_safely(self):
        class FakeTypes:
            GenerateContentConfig = staticmethod(lambda **kwargs: kwargs)
        for error in (RuntimeError("api error"), TimeoutError("timeout")):
            client = SimpleNamespace(models=SimpleNamespace(generate_content=lambda **kwargs: (_ for _ in ()).throw(error)))
            provider = GeminiClassificationProvider(api_key="test-only", client=client, types_module=FakeTypes)
            with self.assertRaises(type(error)):
                provider.classify("context", ["gid-sofa | Home > Sofas"])

    def test_provider_factory_selects_gemini_without_calling_api(self):
        with patch("classification.services.providers.factory.GeminiClassificationProvider") as provider_class:
            provider = get_classification_provider(True, "gemini")
        provider_class.assert_called_once_with()
        self.assertEqual(provider, provider_class.return_value)

    def test_ai_disabled_keeps_deterministic_result_and_does_not_call_provider(self):
        product = self.product(product_name="Sofa", product_category="Living Room")
        index = TaxonomyCandidateIndex.from_database()
        deterministic = ClassificationEngine(index).classify(product)
        provider = MockClassificationProvider(ProviderClassification("sofas", 0.95, "provider"))
        decision = ClassificationEngine(index, ai_fallback=AIFallbackOrchestrator(index, provider, enabled=False)).classify(product)
        self.assertEqual(decision, deterministic)
        self.assertEqual(provider.calls, [])

    def test_low_confidence_invokes_provider_with_only_candidates(self):
        product = self.product(product_name="Sofa", product_category="Living Room")
        index = TaxonomyCandidateIndex.from_database()
        first_candidate = CandidateGenerator(index).generate(ProductTextBuilder().build(product))[0].category_id
        first_candidate_gid = index.categories[first_candidate].taxonomy_id
        provider = MockClassificationProvider(ProviderClassification(first_candidate_gid, 0.91, "Provider selected a supplied candidate."))
        decision = ClassificationEngine(index, ai_fallback=AIFallbackOrchestrator(index, provider, enabled=True)).classify(product)
        self.assertEqual(decision.classification_method, "ai")
        self.assertEqual(decision.category.taxonomy_id, first_candidate_gid)
        self.assertEqual(len(provider.calls), 1)
        self.assertLessEqual(len(provider.calls[0][1]), 40)

    def test_high_confidence_does_not_invoke_provider(self):
        product = self.product(product_name="Outdoor Chairs", product_category="Outdoor Furniture", product_sub_category="Outdoor Chairs")
        index = TaxonomyCandidateIndex.from_database()
        provider = MockClassificationProvider(ProviderClassification("sofas", 0.95, "must not be used"))
        decision = ClassificationEngine(index, ai_fallback=AIFallbackOrchestrator(index, provider, enabled=True)).classify(product)
        self.assertEqual(decision.classification_method, "rule")
        self.assertEqual(provider.calls, [])

    def test_low_score_margin_invokes_provider(self):
        product = self.product(product_name="Sofa", product_category="Living Room")
        index = TaxonomyCandidateIndex.from_database()
        first_candidate = CandidateGenerator(index).generate(ProductTextBuilder().build(product))[0].category_id
        provider = MockClassificationProvider(ProviderClassification(index.categories[first_candidate].taxonomy_id, 0.70, "Low-margin review."))
        decision = ClassificationEngine(index, ai_fallback=AIFallbackOrchestrator(index, provider, enabled=True)).classify(product)
        self.assertEqual(decision.classification_method, "ai")
        self.assertEqual(len(provider.calls), 1)

    def test_invalid_provider_response_and_exception_retain_deterministic_result(self):
        product = self.product(product_name="Sofa", product_category="Living Room")
        index = TaxonomyCandidateIndex.from_database()
        invalid = MockClassificationProvider(ProviderClassification("not-supplied", 0.9, "bad"))
        engine = ClassificationEngine(index, ai_fallback=AIFallbackOrchestrator(index, invalid, enabled=True))
        self.assertEqual(engine.classify(product).classification_method, "rule")
        failing = MockClassificationProvider()
        engine = ClassificationEngine(index, ai_fallback=AIFallbackOrchestrator(index, failing, enabled=True))
        self.assertEqual(engine.classify(product).classification_method, "rule")

    def test_ai_decision_alternatives_are_from_supplied_candidates(self):
        product = self.product(product_name="Sofa", product_category="Living Room")
        index = TaxonomyCandidateIndex.from_database()
        provider = MockClassificationProvider(ProviderClassification("sofas", 0.65, "Needs review."))
        decision = ClassificationEngine(index, ai_fallback=AIFallbackOrchestrator(index, provider, enabled=True)).classify(product)
        supplied = {option.split(" | ", 1)[1] for option in provider.calls[0][1]}
        self.assertEqual(decision.status, "needs_review")
        self.assertTrue(all(item.category.full_path in supplied for item in decision.alternatives))

    def test_ai_result_is_stored_by_command_when_fallback_is_injected(self):
        product = self.product(product_name="Sofa", product_category="Living Room")
        index = TaxonomyCandidateIndex.from_database()
        first_candidate = CandidateGenerator(index).generate(ProductTextBuilder().build(product))[0].category_id
        provider = MockClassificationProvider(ProviderClassification(index.categories[first_candidate].taxonomy_id, 0.91, "Mock provider result."))
        fallback = AIFallbackOrchestrator(index, provider, enabled=True)
        with patch("classification.management.commands.classify_products.build_ai_fallback", return_value=fallback):
            call_command("classify_products", batch_id=product.import_batch_id, limit=1)
        result = ClassificationResult.objects.get(product=product)
        self.assertEqual(result.classification_method, ClassificationResult.Method.AI)
        self.assertEqual(float(result.confidence_score), 0.91)
        self.assertEqual(result.reasoning, "Mock provider result.")

    def test_candidate_limit_retains_specific_dining_table_category(self):
        product = self.product(product_name="Round Dining Table", product_category="Bar and Dining", product_sub_category="Bar and Dining Tables")
        index = TaxonomyCandidateIndex.from_database()
        candidates = CandidateGenerator(index, limit=3, pool_limit=3).generate(ProductTextBuilder().build(product))
        self.assertIn(self.dining_tables.id, [candidate.category_id for candidate in candidates])

    def test_generic_furniture_ai_selection_is_rejected_for_specific_table_evidence(self):
        product = self.product(product_name="Round Dining Table", product_category="Bar and Dining", product_sub_category="Bar and Dining Tables")
        index = TaxonomyCandidateIndex.from_database()
        provider = MockClassificationProvider(ProviderClassification("furniture", 0.99, "generic"))
        decision = ClassificationEngine(index, ai_fallback=AIFallbackOrchestrator(index, provider, enabled=True)).classify(product)
        self.assertNotEqual(decision.category, TaxonomyCategory.objects.get(taxonomy_id="furniture"))
        self.assertEqual(decision.classification_method, "rule")

    def test_product_type_conflicts_are_rejected(self):
        cases = (("Outdoor Patio Armchair", "sectional-branch"), ("Individual Dining Table", "furniture-sets"))
        for name, taxonomy_id in cases:
            product = self.product(product_name=name, product_category="Outdoor Furniture" if "Outdoor" in name else "Bar and Dining", product_sub_category="Sofa Sectionals" if "Outdoor" in name else "Bar and Dining Tables")
            index = TaxonomyCandidateIndex.from_database()
            provider = MockClassificationProvider(ProviderClassification(taxonomy_id, 0.99, "conflicting"))
            decision = ClassificationEngine(index, ai_fallback=AIFallbackOrchestrator(index, provider, enabled=True)).classify(product)
            self.assertEqual(decision.classification_method, "rule")

    def test_specific_loveseat_and_trash_categories_are_compatible(self):
        for name, taxonomy_id in (("Outdoor Patio Loveseat", "loveseat"), ("Trash Can", "trash-cans")):
            product = self.product(product_name=name, product_category="Outdoor Furniture" if "Outdoor" in name else "Decor")
            index = TaxonomyCandidateIndex.from_database()
            provider = MockClassificationProvider(ProviderClassification(taxonomy_id, 0.95, "specific type"))
            decision = ClassificationEngine(index, ai_fallback=AIFallbackOrchestrator(index, provider, enabled=True)).classify(product)
            self.assertEqual(decision.classification_method, "ai")
            self.assertEqual(decision.category.taxonomy_id, taxonomy_id)

    def test_explicit_indoor_dining_table_beats_outdoor_table(self):
        product = self.product(product_name="Indoor Dining Table", product_description="Indoor kitchen dining table")
        index = TaxonomyCandidateIndex.from_database()
        text = ProductTextBuilder().build(product)
        scorer = ClassificationEngine(index).scorer
        self.assertGreater(scorer.score(text, type("C", (), {"category_id": self.dining_tables.id})()), scorer.score(text, type("C", (), {"category_id": self.outdoor_tables.id})()))

    def test_source_merchandising_label_does_not_override_table_type(self):
        product = self.product(product_name="Dining Table", product_category="Bar and Dining", product_sub_category="Bar and Dining Tables")
        index = TaxonomyCandidateIndex.from_database()
        text = ProductTextBuilder().build(product)
        scorer = ClassificationEngine(index).scorer
        self.assertGreater(scorer.score(text, type("C", (), {"category_id": self.dining_tables.id})()), scorer.score(text, type("C", (), {"category_id": self.furniture_sets.id})()))

    def test_near_tied_candidates_use_explicit_product_type(self):
        product = self.product(product_name="Outdoor Patio Armchair", product_description="Teak armchair for outdoor patio use")
        index = TaxonomyCandidateIndex.from_database()
        text = ProductTextBuilder().build(product)
        scorer = ClassificationEngine(index).scorer
        self.assertGreater(scorer.score(text, type("C", (), {"category_id": self.outdoor_chairs.id})()), scorer.score(text, type("C", (), {"category_id": self.sectional_branch.id})()))

    def test_trash_bin_and_trash_can_prefer_trash_cans_over_wastebaskets(self):
        for name in ("Trash Bin", "Trash Can"):
            product = self.product(product_name=name)
            index = TaxonomyCandidateIndex.from_database()
            text = ProductTextBuilder().build(product)
            scorer = ClassificationEngine(index).scorer
            self.assertGreater(scorer.score(text, type("C", (), {"category_id": self.trash_cans.id})()), scorer.score(text, type("C", (), {"category_id": self.wastebaskets.id})()))

    def test_explicit_wastebasket_prefers_wastebaskets(self):
        product = self.product(product_name="Wastebasket")
        index = TaxonomyCandidateIndex.from_database()
        text = ProductTextBuilder().build(product)
        scorer = ClassificationEngine(index).scorer
        self.assertGreater(scorer.score(text, type("C", (), {"category_id": self.wastebaskets.id})()), scorer.score(text, type("C", (), {"category_id": self.trash_cans.id})()))

    def test_loveseat_beats_sectional_even_with_sectional_source_label(self):
        product = self.product(product_name="Outdoor Patio Loveseat", product_category="Outdoor Furniture", product_sub_category="Sofa Sectionals")
        index = TaxonomyCandidateIndex.from_database()
        text = ProductTextBuilder().build(product)
        scorer = ClassificationEngine(index).scorer
        self.assertGreater(scorer.score(text, type("C", (), {"category_id": self.loveseat.id})()), scorer.score(text, type("C", (), {"category_id": self.sectional_branch.id})()))

    def test_command_rerun_updates_result_without_duplicate(self):
        product = self.product(product_name="Outdoor Chairs", product_category="Outdoor Furniture", product_sub_category="Outdoor Chairs")
        batch = product.import_batch
        call_command("classify_products", batch_id=batch.id, overwrite=True)
        alternative_count = ClassificationAlternative.objects.filter(classification_result__product=product).count()
        call_command("classify_products", batch_id=batch.id, overwrite=True)
        self.assertEqual(ClassificationResult.objects.filter(product=product).count(), 1)
        self.assertEqual(ClassificationAlternative.objects.filter(classification_result__product=product).count(), alternative_count)
        self.assertEqual(ProcessingAttempt.objects.filter(product=product).count(), 2)

    def test_command_skips_existing_result_by_default(self):
        product = self.product(product_name="Outdoor Chairs", product_category="Outdoor Furniture", product_sub_category="Outdoor Chairs")
        ClassificationResult.objects.create(product=product, status=ClassificationResult.Status.CLASSIFIED, classification_method=ClassificationResult.Method.RULE)
        call_command("classify_products", batch_id=product.import_batch_id)
        self.assertEqual(ProcessingAttempt.objects.filter(product=product).count(), 0)

    def test_command_scope_is_fixed_and_processes_each_selected_product_once(self):
        first = self.product(product_name="Outdoor Chairs", product_category="Outdoor Furniture", product_sub_category="Outdoor Chairs")
        self.product(product_name="Outdoor Chairs", product_category="Outdoor Furniture", product_sub_category="Outdoor Chairs")
        with patch("classification.management.commands.classify_products.ClassificationEngine.classify", wraps=self.engine().classify) as classify:
            call_command("classify_products", batch_id=first.import_batch_id, limit=1)
        self.assertEqual(classify.call_count, 1)
        self.assertEqual(ProcessingAttempt.objects.filter(product=first).count(), 1)

    def test_counting_provider_tracks_success_and_failure_calls(self):
        class Provider:
            def __init__(self): self.n = 0
            def classify(self, context, candidates):
                self.n += 1
                if self.n == 2: raise TimeoutError("test timeout")
                return ProviderClassification("id", 0.8, "ok")
        counter = CountingProvider(Provider())
        counter.classify("x", ["id"])
        with self.assertRaises(TimeoutError): counter.classify("x", ["id"])
        self.assertEqual((counter.calls, counter.successes, counter.failures), (2, 1, 1))

    def test_command_records_processing_failure_and_continues(self):
        product = self.product(product_name="Outdoor Chairs", product_category="Outdoor Furniture", product_sub_category="Outdoor Chairs")
        with patch("classification.management.commands.classify_products.ClassificationEngine.classify", side_effect=RuntimeError("test failure")):
            call_command("classify_products", batch_id=product.import_batch_id)
        attempt = ProcessingAttempt.objects.get(product=product)
        self.assertEqual(attempt.status, ProcessingAttempt.Status.FAILED)
