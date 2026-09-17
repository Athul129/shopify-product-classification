from django.db import models


class ImportBatch(models.Model):
    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        PROCESSING = "processing", "Processing"
        COMPLETED = "completed", "Completed"
        COMPLETED_WITH_ERRORS = "completed_with_errors", "Completed with errors"
        FAILED = "failed", "Failed"

    source_name = models.CharField(max_length=255)
    status = models.CharField(max_length=30, choices=Status.choices, default=Status.PENDING, db_index=True)
    total_products = models.PositiveIntegerField(default=0)
    processed_products = models.PositiveIntegerField(default=0)
    successful_products = models.PositiveIntegerField(default=0)
    failed_products = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return self.source_name


class Product(models.Model):
    import_batch = models.ForeignKey(ImportBatch, on_delete=models.CASCADE, related_name="products")
    product_number = models.CharField(max_length=255)
    model_number = models.CharField(max_length=255, blank=True)
    product_category = models.CharField(max_length=255, blank=True)
    product_sub_category = models.CharField(max_length=255, blank=True)
    collection_name = models.CharField(max_length=255, blank=True)
    color_collection = models.CharField(max_length=255, blank=True)
    product_color = models.CharField(max_length=255, blank=True)
    product_name = models.CharField(max_length=500, blank=True)
    product_description = models.TextField(blank=True)
    bullets = models.TextField(blank=True)
    set_includes = models.TextField(blank=True)
    product_weight = models.CharField(max_length=100, blank=True)
    materials = models.TextField(blank=True)
    product_dimensions = models.TextField(blank=True)
    assembly_required = models.BooleanField(null=True, blank=True)
    is_set = models.BooleanField(null=True, blank=True)
    stackable = models.BooleanField(null=True, blank=True)
    country_of_origin = models.CharField(max_length=100, blank=True)
    item_cost = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    map = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    msrp = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    shipping_method = models.CharField(max_length=255, blank=True)
    total_box_count = models.PositiveIntegerField(null=True, blank=True)
    pallet_count = models.PositiveIntegerField(null=True, blank=True)
    shipping_weight = models.CharField(max_length=100, blank=True)
    total_cbm = models.DecimalField(max_digits=12, decimal_places=4, null=True, blank=True)
    package_dimensions = models.CharField(max_length=255, blank=True)
    product_url = models.URLField(max_length=1000, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["import_batch", "product_number"], name="unique_product_number_per_batch"),
        ]
        indexes = [
            models.Index(fields=["import_batch", "product_category"]),
            models.Index(fields=["product_number"]),
            models.Index(fields=["model_number"]),
        ]
        ordering = ["id"]

    def __str__(self):
        return self.product_name or self.product_number


class ProductImage(models.Model):
    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name="images")
    image_url = models.URLField(max_length=2000)
    position = models.PositiveSmallIntegerField()
    is_valid = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["product", "position"], name="unique_image_position_per_product"),
        ]
        indexes = [models.Index(fields=["product", "position"]), models.Index(fields=["is_valid"])]
        ordering = ["product", "position"]
