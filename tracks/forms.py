from typing import ClassVar

from django import forms
from django.core.validators import FileExtensionValidator

from .models import Track, TrackGroup
from .services import InvalidGPX, parse_gpx


class GPXValidationMixin:
    def clean_gpx_file(self):
        uploaded_files = self.cleaned_data["gpx_file"]
        files = uploaded_files if isinstance(uploaded_files, list) else [uploaded_files]
        for uploaded in files:
            instance = getattr(self, "instance", None)
            if instance and instance.pk and uploaded.name == instance.gpx_file.name:
                continue
            try:
                parse_gpx(uploaded.read(), uploaded.name)
            except InvalidGPX as exc:
                raise forms.ValidationError(str(exc)) from exc
            finally:
                uploaded.seek(0)
        return uploaded_files


class MultipleFileInput(forms.ClearableFileInput):
    allow_multiple_selected = True


class MultipleFileField(forms.FileField):
    widget = MultipleFileInput

    def clean(self, data, initial=None):
        clean_one = super().clean
        if isinstance(data, (list, tuple)):
            if not data:
                raise forms.ValidationError(self.error_messages["required"], code="required")
            return [clean_one(item, initial) for item in data]
        return [clean_one(data, initial)]


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


class TrackUploadForm(GPXValidationMixin, forms.Form):
    name = forms.CharField(
        label="Название",
        max_length=200,
        required=False,
        widget=forms.TextInput(
            attrs={"class": "form-control", "placeholder": "Для загрузки одного файла"}
        ),
    )
    description = forms.CharField(
        label="Описание",
        required=False,
        widget=forms.Textarea(
            attrs={
                "class": "form-control",
                "rows": 4,
                "placeholder": "Общее описание для загружаемых треков",
            }
        ),
    )
    group = forms.ModelChoiceField(
        label="Группа",
        queryset=TrackGroup.objects.none(),
        required=False,
        empty_label="Без группы",
        to_field_name="public_id",
        widget=forms.Select(attrs={"class": "form-select"}),
    )
    new_group_name = forms.CharField(
        label="Название новой группы",
        max_length=200,
        required=False,
        widget=forms.TextInput(attrs={"class": "form-control"}),
    )
    new_group_description = forms.CharField(
        label="Описание новой группы",
        required=False,
        widget=forms.Textarea(attrs={"class": "form-control", "rows": 3}),
    )
    gpx_file = MultipleFileField(
        label="GPX-файлы",
        validators=[FileExtensionValidator(["gpx"])],
        widget=MultipleFileInput(
            attrs={"class": "form-control", "accept": ".gpx,application/gpx+xml"}
        ),
    )

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        if user is not None:
            self.fields["group"].queryset = TrackGroup.objects.filter(owner=user).order_by(
                "name", "pk"
            )

    def clean(self):
        data = super().clean()
        files = data.get("gpx_file") or []
        if len(files) > 1 and data.get("name"):
            self.add_error("name", "Название можно указать только при загрузке одного файла.")
        if data.get("group") and data.get("new_group_name"):
            self.add_error("new_group_name", "Выберите существующую группу или создайте новую.")
        if data.get("new_group_description") and not data.get("new_group_name"):
            self.add_error("new_group_name", "Укажите название новой группы.")
        return data


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
