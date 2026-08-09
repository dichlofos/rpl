from django import forms

from .models import Track
from .services import InvalidGPX, parse_gpx


class TrackAdminForm(forms.ModelForm):
    class Meta:
        model = Track
        fields = "__all__"

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
