from typing import ClassVar

from django import forms

from .models import Track
from .services import InvalidGPX, parse_gpx


class GPXValidationMixin:
    def clean_gpx_file(self):
        uploaded = self.cleaned_data["gpx_file"]
        if not self.instance.pk or uploaded.name != self.instance.gpx_file.name:
            try:
                parse_gpx(uploaded.read(), uploaded.name)
            except InvalidGPX as exc:
                raise forms.ValidationError(str(exc)) from exc
            finally:
                uploaded.seek(0)
        return uploaded


class TrackAdminForm(GPXValidationMixin, forms.ModelForm):
    class Meta:
        model = Track
        fields = "__all__"


class TrackUploadForm(GPXValidationMixin, forms.ModelForm):
    class Meta:
        model = Track
        fields = ("name", "description", "gpx_file")
        widgets: ClassVar[dict[str, forms.Widget]] = {
            "name": forms.TextInput(
                attrs={"class": "form-control", "placeholder": "Например, Конобеево — Коломна"}
            ),
            "description": forms.Textarea(
                attrs={
                    "class": "form-control",
                    "rows": 4,
                    "placeholder": "Коротко расскажите о маршруте",
                }
            ),
            "gpx_file": forms.ClearableFileInput(
                attrs={"class": "form-control", "accept": ".gpx,application/gpx+xml"}
            ),
        }
