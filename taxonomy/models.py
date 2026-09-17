from django.db import models


class TaxonomyCategory(models.Model):
    taxonomy_id = models.CharField(max_length=255, unique=True)
    name = models.CharField(max_length=255)
    full_path = models.CharField(max_length=1000)
    parent = models.ForeignKey("self", on_delete=models.SET_NULL, null=True, blank=True, related_name="children")
    is_active = models.BooleanField(default=True, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [models.Index(fields=["parent"]), models.Index(fields=["name"])]
        ordering = ["full_path"]

    def __str__(self):
        return self.full_path


class TaxonomyAttribute(models.Model):
    category = models.ForeignKey(TaxonomyCategory, on_delete=models.CASCADE, related_name="attributes")
    name = models.CharField(max_length=255)
    external_id = models.CharField(max_length=255, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["category", "name"], name="unique_attribute_name_per_category")]
        indexes = [models.Index(fields=["category", "name"]), models.Index(fields=["external_id"])]
        ordering = ["name"]

    def __str__(self):
        return self.name


class TaxonomyAttributeValue(models.Model):
    attribute = models.ForeignKey(TaxonomyAttribute, on_delete=models.CASCADE, related_name="values")
    value = models.CharField(max_length=255)
    external_id = models.CharField(max_length=255, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["attribute", "value"], name="unique_value_per_attribute")]
        indexes = [models.Index(fields=["attribute", "value"]), models.Index(fields=["external_id"])]
        ordering = ["value"]

    def __str__(self):
        return self.value
