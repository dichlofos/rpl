import json
import uuid
from pathlib import Path
from typing import ClassVar

from django.conf import settings
from django.contrib.gis.db import models
from django.contrib.gis.geos import GEOSGeometry, LineString, MultiLineString
from django.core.exceptions import ValidationError
from django.core.files.base import ContentFile
from django.core.validators import FileExtensionValidator
from django.db import transaction
from django.urls import reverse

from .services import InvalidGPX, parse_gpx


def anonymized_gpx_path(instance, _filename):
    token = uuid.uuid4().hex
    return f"tracks/{token[:3]}/{token[3:6]}/{token}.gpx"


def original_gpx_path(instance, _filename):
    token = uuid.uuid4().hex
    return f"originals/{token[:3]}/{token[3:6]}/{token}.gpx"


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
    original_gpx_file = models.FileField(
        "архивный GPX", upload_to=original_gpx_path, default="", editable=False
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
        constraints: ClassVar[list] = [
            models.CheckConstraint(
                condition=~models.Q(original_gpx_file=""), name="track_has_original_gpx"
            ),
        ]
        ordering: ClassVar[list[str]] = ["-uploaded_at"]
        verbose_name = "трек"
        verbose_name_plural = "треки"

    def __str__(self):
        return self.name or Path(self.gpx_file.name).stem

    def get_absolute_url(self):
        return reverse("tracks:detail", kwargs={"public_id": self.public_id})

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

    @transaction.atomic
    def save(self, *args, **kwargs):
        stored = (
            type(self).objects.filter(pk=self.pk).values("gpx_file", "original_gpx_file").first()
            if self.pk
            else None
        )
        old_file_name = stored["gpx_file"] if stored else ""
        if stored and (
            self.original_gpx_file.name != stored["original_gpx_file"]
            or not self.original_gpx_file._committed
        ):
            raise ValidationError("Архивный GPX нельзя заменять или удалять.")
        file_changed = self.gpx_file and (
            not self.gpx_file._committed or old_file_name != self.gpx_file.name
        )
        if file_changed:
            self._parse_file()
            # All derived fields must be saved together with the working file.
            kwargs.pop("update_fields", None)
        if not stored:
            self.gpx_file.open("rb")
            try:
                content = self.gpx_file.read()
            finally:
                self.gpx_file.seek(0)
            self.original_gpx_file.save("original.gpx", ContentFile(content), save=False)
        super().save(*args, **kwargs)
        if file_changed:
            from regions.services import classify_track

            classify_track(self)
        if old_file_name and old_file_name != self.gpx_file.name:
            storage = self.gpx_file.storage
            transaction.on_commit(lambda: storage.delete(old_file_name), robust=True)

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


class TrackGroup(models.Model):
    name = models.CharField("название", max_length=200)
    description = models.TextField("описание", blank=True)
    public_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="track_groups",
        verbose_name="владелец",
    )
    created_at = models.DateTimeField("создана", auto_now_add=True)
    updated_at = models.DateTimeField("изменена", auto_now=True)
    tracks = models.ManyToManyField(Track, through="TrackGroupMembership", related_name="groups")

    class Meta:
        ordering: ClassVar[list[str]] = ["-updated_at", "-pk"]
        verbose_name = "группа треков"
        verbose_name_plural = "группы треков"

    def __str__(self):
        return self.name

    def get_absolute_url(self):
        return reverse("tracks:group-detail", kwargs={"public_id": self.public_id})


class TrackGroupMembership(models.Model):
    group = models.ForeignKey(TrackGroup, on_delete=models.CASCADE, related_name="memberships")
    track = models.ForeignKey(Track, on_delete=models.CASCADE, related_name="group_memberships")
    position = models.PositiveIntegerField("порядок", default=0)

    class Meta:
        ordering: ClassVar[list[str]] = ["position", "pk"]
        constraints: ClassVar[list] = [
            models.UniqueConstraint(fields=["group", "track"], name="unique_group_track"),
            # Remove this constraint when the UI supports several groups per track.
            models.UniqueConstraint(fields=["track"], name="one_group_per_track"),
        ]
        indexes: ClassVar[list] = [models.Index(fields=["group", "position"])]
        verbose_name = "участие трека в группе"
        verbose_name_plural = "состав групп"
