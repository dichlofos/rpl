import json
import uuid
from pathlib import Path
from typing import ClassVar

from django.conf import settings
from django.contrib.gis.db import models
from django.contrib.gis.geos import GEOSGeometry, LineString, MultiLineString
from django.core.exceptions import ValidationError
from django.core.validators import FileExtensionValidator
from django.urls import reverse

from .services import InvalidGPX, parse_gpx


def anonymized_gpx_path(instance, _filename):
    token = uuid.uuid4().hex
    return f"tracks/{token[:3]}/{token[3:6]}/{token}.gpx"


class Track(models.Model):
    name = models.CharField("название", max_length=200, blank=True)
    public_id = models.UUIDField(
        "публичный идентификатор", default=uuid.uuid4, unique=True, editable=False
    )
    description = models.TextField("описание", blank=True)
    gpx_file = models.FileField(
        "GPX-файл",
        upload_to=anonymized_gpx_path,
        validators=[FileExtensionValidator(["gpx"])],
    )
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name="владелец",
        related_name="tracks",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
    )
    uploaded_at = models.DateTimeField("загружен", auto_now_add=True)
    distance_m = models.FloatField("дистанция, м", default=0, editable=False)
    elevation_gain_m = models.FloatField("набор высоты, м", default=0, editable=False)
    elevation_loss_m = models.FloatField("сброс высоты, м", default=0, editable=False)
    min_elevation_m = models.FloatField("минимальная высота, м", null=True, editable=False)
    max_elevation_m = models.FloatField("максимальная высота, м", null=True, editable=False)
    points_count = models.PositiveIntegerField("точек", default=0, editable=False)
    started_at = models.DateTimeField("начало", null=True, editable=False)
    finished_at = models.DateTimeField("окончание", null=True, editable=False)
    duration_s = models.FloatField("продолжительность, с", null=True, editable=False)
    min_latitude = models.FloatField(null=True, editable=False)
    min_longitude = models.FloatField(null=True, editable=False)
    max_latitude = models.FloatField(null=True, editable=False)
    max_longitude = models.FloatField(null=True, editable=False)
    geojson = models.JSONField(default=dict, editable=False)
    geometry = models.MultiLineStringField("геометрия", srid=4326, null=True, editable=False)

    class Meta:
        ordering: ClassVar[list[str]] = ["-uploaded_at"]
        verbose_name = "трек"
        verbose_name_plural = "треки"

    def __str__(self):
        return self.name or Path(self.gpx_file.name).stem

    def get_absolute_url(self):
        return reverse("tracks:detail", kwargs={"public_id": self.public_id})

    def _stored_file_name(self):
        if not self.pk:
            return ""
        return type(self).objects.filter(pk=self.pk).values_list("gpx_file", flat=True).first()

    def _parse_file(self):
        should_close = self.gpx_file._committed
        try:
            self.gpx_file.open("rb")
            content = self.gpx_file.read()
            parsed = parse_gpx(content, self.gpx_file.name)
        except InvalidGPX as exc:
            raise ValidationError({"gpx_file": str(exc)}) from exc
        finally:
            if should_close:
                self.gpx_file.close()
            else:
                self.gpx_file.seek(0)

        if not self.name:
            self.name = parsed.name
        for field in (
            "geojson",
            "distance_m",
            "elevation_gain_m",
            "elevation_loss_m",
            "min_elevation_m",
            "max_elevation_m",
            "points_count",
            "started_at",
            "finished_at",
            "duration_s",
            "min_latitude",
            "min_longitude",
            "max_latitude",
            "max_longitude",
        ):
            setattr(self, field, getattr(parsed, field))
        geometry_data = parsed.geojson["geometry"]
        coordinates = geometry_data["coordinates"]
        if geometry_data["type"] == "LineString":
            coordinates = [[point[0], point[1]] for point in coordinates]
        else:
            coordinates = [[[point[0], point[1]] for point in segment] for segment in coordinates]
        geometry = GEOSGeometry(
            json.dumps({"type": geometry_data["type"], "coordinates": coordinates}),
            srid=4326,
        )
        if isinstance(geometry, LineString):
            geometry = MultiLineString(geometry, srid=4326)
        self.geometry = geometry

    def save(self, *args, **kwargs):
        old_file_name = self._stored_file_name()
        file_changed = self.gpx_file and old_file_name != self.gpx_file.name
        if file_changed:
            self._parse_file()
        super().save(*args, **kwargs)
        if old_file_name and old_file_name != self.gpx_file.name:
            self.gpx_file.storage.delete(old_file_name)
        if file_changed:
            from regions.services import classify_track

            classify_track(self)

    @property
    def distance_km(self):
        return self.distance_m / 1000

    @property
    def duration_display(self):
        if self.duration_s is None:
            return "—"
        total_minutes = round(self.duration_s / 60)
        hours, minutes = divmod(total_minutes, 60)
        return f"{hours} ч {minutes} мин" if hours else f"{minutes} мин"
