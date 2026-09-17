from django import forms
from products.models import ImportBatch

class ClassificationForm(forms.Form):
    batch = forms.ModelChoiceField(queryset=ImportBatch.objects.all())
    limit = forms.IntegerField(min_value=1, required=False)
    overwrite = forms.BooleanField(required=False)

class ManualReviewForm(forms.Form):
    category_id = forms.IntegerField(min_value=1)

class ProductImportForm(forms.Form):
    excel_file = forms.FileField()
