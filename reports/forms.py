from django import forms

from tracks.models import TrackGroup

from .models import TravelReport


class JourneyForm(forms.ModelForm):
    class Meta:
        model = TravelReport
        fields = ("name", "description", "track_group")
        widgets = {
            "name": forms.TextInput(attrs={"class": "form-control"}),
            "description": forms.Textarea(attrs={"class": "form-control", "rows": 3}),
            "track_group": forms.Select(attrs={"class": "form-select"}),
        }
        labels = {"track_group": "Исходная группа треков"}

    def __init__(self, *args, user, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["track_group"].queryset = TrackGroup.objects.filter(owner=user)
        if self.instance.pk:
            self.fields.pop("track_group")
        else:
            self.fields["track_group"].required = True
            self.instance.owner = user


class JourneyTracksForm(forms.Form):
    def __init__(self, *args, report, **kwargs):
        super().__init__(*args, **kwargs)
        current = list(report.track_links.select_related("track"))
        ordered_tracks = [link.track for link in current]
        selected_ids = {track.pk for track in ordered_tracks}
        if report.track_group_id:
            ordered_tracks.extend(
                link.track
                for link in report.track_group.memberships.select_related("track")
                .filter(track__deleted_at__isnull=True)
                if link.track_id not in selected_ids
            )
        self.tracks = ordered_tracks
        for index, track in enumerate(self.tracks):
            self.fields[f"selected_{track.pk}"] = forms.BooleanField(
                required=False, initial=track.pk in selected_ids,
                widget=forms.CheckboxInput(attrs={"class": "form-check-input"}),
            )
            self.fields[f"position_{track.pk}"] = forms.IntegerField(
                min_value=1, initial=index + 1, required=False,
                widget=forms.NumberInput(attrs={"class": "form-control", "style": "width:100px"}),
            )

    def clean(self):
        data = super().clean()
        chosen = []
        for track in self.tracks:
            if data.get(f"selected_{track.pk}"):
                position = data.get(f"position_{track.pk}")
                if position is None:
                    self.add_error(f"position_{track.pk}", "Укажите порядок выбранного трека.")
                else:
                    chosen.append((position, track.pk))
        if len({position for position, _ in chosen}) != len(chosen):
            raise forms.ValidationError("У выбранных треков должны быть разные номера порядка.")
        data["track_ids"] = [pk for _, pk in sorted(chosen)]
        return data

    def entries(self):
        return [
            {"track": track, "selected": self[f"selected_{track.pk}"],
             "position": self[f"position_{track.pk}"]}
            for track in self.tracks
        ]
