from typing import ClassVar

from django import forms

from .models import Track, TrackGroup
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
    def clean(self):
        data = super().clean()
        owner = data.get("owner")
        if (
            self.instance.pk
            and self.instance.groups.exclude(owner_id=owner.pk if owner else None).exists()
        ):
            raise forms.ValidationError(
                "Сначала исключите трек из группы, чтобы изменить владельца."
            )
        return data

    class Meta:
        model = Track
        fields = "__all__"


class TrackUploadForm(GPXValidationMixin, forms.ModelForm):
    group = forms.ModelChoiceField(
        label="Группа",
        queryset=TrackGroup.objects.none(),
        required=False,
        empty_label="Без группы",
        to_field_name="public_id",
        widget=forms.Select(attrs={"class": "form-select"}),
    )

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        if user is not None:
            self.fields["group"].queryset = TrackGroup.objects.filter(owner=user).order_by(
                "name", "pk"
            )

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


class TrackGroupForm(forms.ModelForm):
    class Meta:
        model = TrackGroup
        fields = ("name", "description")
        widgets: ClassVar[dict] = {
            "name": forms.TextInput(attrs={"class": "form-control"}),
            "description": forms.Textarea(attrs={"class": "form-control", "rows": 3}),
        }


class GroupChoiceForm(forms.Form):
    group = forms.ModelChoiceField(
        label="Группа",
        queryset=TrackGroup.objects.none(),
        required=False,
        empty_label="Без группы",
        to_field_name="public_id",
        widget=forms.Select(attrs={"class": "form-select"}),
    )

    def __init__(self, *args, owner_id, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["group"].queryset = TrackGroup.objects.filter(owner_id=owner_id).order_by(
            "name", "pk"
        )


class TrackChoiceField(forms.ModelChoiceField):
    def label_from_instance(self, obj):
        groups = list(obj.groups.all())
        return f"{obj} — сейчас в «{groups[0]}»" if groups else str(obj)


class GroupAddTrackForm(forms.Form):
    track = TrackChoiceField(
        label="Трек",
        queryset=Track.objects.none(),
        to_field_name="public_id",
        widget=forms.Select(attrs={"class": "form-select"}),
    )

    def __init__(self, *args, group, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["track"].queryset = (
            Track.objects.filter(owner_id=group.owner_id)
            .exclude(groups=group)
            .prefetch_related("groups")
            .order_by("name", "pk")
        )
