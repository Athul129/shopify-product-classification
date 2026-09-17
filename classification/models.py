from django.db import models
from django.core.validators import MaxValueValidator, MinValueValidator

from products.models import Product
from taxonomy.models import TaxonomyAttribute, TaxonomyCategory


class ClassificationResult(models.Model):
    class Method(models.TextChoices):
        RULE = "rule", "Rule"
        AI = "ai", "AI"
        HYBRID = "hybrid", "Hybrid"
        MANUAL = "manual", "Manual"

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        CLASSIFIED = "classified", "Classified"
        NEEDS_REVIEW = "needs_review", "Needs review"
        APPROVED = "approved", "Approved"
        REJECTED = "rejected", "Rejected"

    product = models.OneToOneField(Product, on_delete=models.CASCADE, related_name="classification_result")
    category = models.ForeignKey(TaxonomyCategory, on_delete=models.SET_NULL, null=True, blank=True, related_name="classification_results")
    confidence_score = models.DecimalField(max_digits=5, decimal_places=4, validators=[MinValueValidator(0), MaxValueValidator(1)], null=True, blank=True)
    classification_method = models.CharField(max_length=20, choices=Method.choices, blank=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING, db_index=True)
    reasoning = models.TextField(blank=True)
    needs_manual_review = models.BooleanField(default=False, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [models.Index(fields=["category", "status"])]


class ClassificationAlternative(models.Model):
    classification_result = models.ForeignKey(ClassificationResult, on_delete=models.CASCADE, related_name="alternatives")
    category = models.ForeignKey(TaxonomyCategory, on_delete=models.CASCADE, related_name="classification_alternatives")
    confidence_score = models.DecimalField(max_digits=5, decimal_places=4, validators=[MinValueValidator(0), MaxValueValidator(1)])
    rank = models.PositiveIntegerField()

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["classification_result", "rank"], name="unique_alternative_rank"),
            models.UniqueConstraint(fields=["classification_result", "category"], name="unique_alternative_category"),
        ]
        indexes = [models.Index(fields=["classification_result", "rank"])]
        ordering = ["rank"]


class ProductAttribute(models.Model):
    class Source(models.TextChoices):
        RULE = "rule", "Rule"
        AI = "ai", "AI"
        MANUAL = "manual", "Manual"

    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name="attributes")
    attribute = models.ForeignKey(TaxonomyAttribute, on_delete=models.CASCADE, related_name="product_attributes")
    value = models.CharField(max_length=500)
    confidence_score = models.DecimalField(max_digits=5, decimal_places=4, validators=[MinValueValidator(0), MaxValueValidator(1)], null=True, blank=True)
    source = models.CharField(max_length=20, choices=Source.choices, default=Source.RULE)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["product", "attribute"], name="unique_attribute_per_product")]
        indexes = [models.Index(fields=["product", "attribute"]), models.Index(fields=["attribute", "value"])]


class ProcessingAttempt(models.Model):
    class Status(models.TextChoices):
        STARTED = "started", "Started"
        COMPLETED = "completed", "Completed"
        FAILED = "failed", "Failed"

    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name="processing_attempts")
    attempt_number = models.PositiveIntegerField()
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.STARTED, db_index=True)
    error_message = models.TextField(blank=True)
    started_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["product", "attempt_number"], name="unique_attempt_number_per_product")]
        indexes = [models.Index(fields=["product", "status"])]
        ordering = ["product", "attempt_number"]
