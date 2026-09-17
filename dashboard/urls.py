from django.urls import path
from . import views
app_name="dashboard"
urlpatterns=[path("",views.index,name="index"),path("products/",views.product_list,name="products"),path("products/<int:pk>/",views.product_detail,name="product-detail"),path("products/<int:pk>/review/",views.manual_review,name="manual-review"),path("review/",views.review_queue,name="review"),path("taxonomy/categories/",views.category_search,name="category-search"),path("import/",views.import_products,name="import"),path("classification/",views.classify,name="classify")]
