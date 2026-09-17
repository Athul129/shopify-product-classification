from django.contrib import messages
from django.db.models import Count, Q
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_http_methods
from products.models import ImportBatch, Product
from taxonomy.models import TaxonomyCategory
from classification.models import ClassificationResult
from products.services.importer import ProductImportService
from classification.services.execution import execute_classification
from .forms import ClassificationForm, ManualReviewForm, ProductImportForm

def latest_usable_batch():
    return ImportBatch.objects.filter(status__in=[ImportBatch.Status.COMPLETED, ImportBatch.Status.COMPLETED_WITH_ERRORS], products__isnull=False).distinct().order_by("-created_at", "-id").first()

def selected_batch(request):
    value = request.GET.get("batch") or request.GET.get("import_batch_id")
    if value == "all":
        return None, True
    if value:
        return get_object_or_404(ImportBatch, pk=value), False
    return latest_usable_batch(), False

def index(request):
    batch, all_batches = selected_batch(request)
    qs = ClassificationResult.objects.filter(product__import_batch=batch) if batch else ClassificationResult.objects.all()
    counts = qs.aggregate(classified=Count("id", filter=Q(status="classified")), review=Count("id", filter=Q(needs_manual_review=True)), **{m: Count("id", filter=Q(classification_method=m)) for m in ("rule", "ai", "manual", "hybrid")})
    counts["total"] = (batch.products.count() if batch else Product.objects.count())
    return render(request, "dashboard/index.html", {"counts": counts, "batches": ImportBatch.objects.all(), "selected_batch": batch, "all_batches": all_batches})

def product_list(request):
    batch, all_batches = selected_batch(request)
    qs = Product.objects.select_related("import_batch", "classification_result__category").all()
    if batch:
        qs = qs.filter(import_batch=batch)
    if request.GET.get("q"): qs = qs.filter(Q(product_number__icontains=request.GET["q"]) | Q(product_name__icontains=request.GET["q"]))
    for field in ("import_batch_id", "classification_result__status", "classification_result__classification_method"):
        value = request.GET.get(field)
        if value and not (field == "import_batch_id" and value == "all"):
            qs = qs.filter(**{field: value})
    if request.GET.get("review"): qs = qs.filter(classification_result__needs_manual_review=True)
    from django.core.paginator import Paginator
    page = Paginator(qs, 25).get_page(request.GET.get("page"))
    return render(request, "dashboard/product_list.html", {"page": page, "batches": ImportBatch.objects.all(), "selected_batch": batch, "all_batches": all_batches})

def product_detail(request, pk):
    product = get_object_or_404(Product.objects.select_related("classification_result__category").prefetch_related("images", "attributes__attribute", "processing_attempts", "classification_result__alternatives__category"), pk=pk)
    return render(request, "dashboard/product_detail.html", {"product": product, "form": ManualReviewForm()})

@require_http_methods(["GET", "POST"])
def manual_review(request, pk):
    product = get_object_or_404(Product, pk=pk)
    if request.method == "POST":
        form = ManualReviewForm(request.POST)
        if form.is_valid() and product.classification_result:
            category = get_object_or_404(TaxonomyCategory, pk=form.cleaned_data["category_id"], is_active=True)
            result = product.classification_result
            result.category = category
            result.classification_method = ClassificationResult.Method.MANUAL
            result.status = ClassificationResult.Status.APPROVED
            result.needs_manual_review = False
            result.reasoning = "Category manually selected by reviewer."
            result.save(update_fields=["category", "classification_method", "status", "needs_manual_review", "reasoning", "updated_at"])
            messages.success(request, "Classification approved manually.")
    return redirect("dashboard:product-detail", pk=pk)

def category_search(request):
    q = request.GET.get("q", "").strip()
    if not q: return JsonResponse({"results": []})
    cats = TaxonomyCategory.objects.filter(is_active=True).filter(Q(name__icontains=q)|Q(full_path__icontains=q)).values("id","taxonomy_id","name","full_path")[:25]
    return JsonResponse({"results": list(cats)})

@require_http_methods(["GET", "POST"])
def import_products(request):
    result = None
    if request.method == "POST":
        form = ProductImportForm(request.POST, request.FILES)
        if form.is_valid() and form.cleaned_data["excel_file"].size <= 25*1024*1024:
            upload=form.cleaned_data["excel_file"]; batch=ImportBatch.objects.create(source_name=upload.name)
            import tempfile
            with tempfile.NamedTemporaryFile(suffix=".xlsx") as f:
                for chunk in upload.chunks(): f.write(chunk)
                f.flush(); service=ProductImportService(f.name); service.import_file(batch)
            result={"batch":batch,"service":service}
    else: form=ProductImportForm()
    return render(request,"dashboard/import.html",{"form":form,"result":result})

def classify(request):
    result=None; form=ClassificationForm(request.POST or None)
    if request.method=="POST" and form.is_valid() and request.POST.get("confirm"):
        result=execute_classification(form.cleaned_data["batch"], form.cleaned_data["limit"], form.cleaned_data["overwrite"])
    return render(request,"dashboard/classify.html",{"form":form,"result":result})

def review_queue(request):
    from django.core.paginator import Paginator
    qs=Product.objects.filter(classification_result__needs_manual_review=True).select_related("classification_result__category")
    return render(request,"dashboard/review.html",{"page":Paginator(qs,25).get_page(request.GET.get("page"))})
