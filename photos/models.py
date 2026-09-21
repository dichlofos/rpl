import secrets
import uuid
from typing import ClassVar

from django.conf import settings
from django.contrib.gis.db import models
from django.core.validators import RegexValidator


def generate_photo_key():
    return secrets.token_hex(4)


class PhotoBatch(models.Model):
    public_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    client_id = models.UUIDField("локальный идентификатор")
    report = models.ForeignKey(
        "reports.TravelReport",
        on_delete=models.CASCADE,
        related_name="photo_batches",
        verbose_name="отчёт",
    )
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="photo_batches",
        verbose_name="ответственный",
    )
    name = models.CharField("название", max_length=200)
    calibration = models.JSONField("калибровка времени", default=dict, blank=True)
    created_at = models.DateTimeField("создана", auto_now_add=True)
    updated_at = models.DateTimeField("изменена", auto_now=True)

    class Meta:
        ordering: ClassVar[list[str]] = ["-updated_at", "-pk"]
        constraints: ClassVar[list] = [
            models.UniqueConstraint(
                fields=["owner", "client_id"], name="unique_owner_photo_batch_client_id"
            )
        ]
        verbose_name = "пачка фотографий"
        verbose_name_plural = "пачки фотографий"

    def __str__(self):
        return self.name


class PhotoAsset(models.Model):
    public_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    client_id = models.UUIDField("локальный идентификатор")
    photo_key = models.CharField(
        "ключ фотографии",
        max_length=8,
        unique=True,
        default=generate_photo_key,
        editable=False,
        validators=[RegexValidator(r"^[0-9a-f]{8}$")],
    )
    batch = models.ForeignKey(
        PhotoBatch, on_delete=models.CASCADE, related_name="photos", verbose_name="пачка"
    )
    relative_path = models.CharField("исходное имя", max_length=1024)
    file_size = models.PositiveBigIntegerField("размер файла")
    file_mtime_ns = models.PositiveBigIntegerField("время изменения файла, нс")
    captured_at_raw = models.CharField("исходное время EXIF", max_length=64, blank=True)
    timezone_explicit = models.BooleanField("часовой пояс задан", default=False)
    time_offset_seconds = models.IntegerField("поправка времени, с", default=0)
    captured_at_normalized = models.DateTimeField("нормализованное время", null=True, blank=True)
    logical_day = models.PositiveSmallIntegerField("логический день", null=True, blank=True)
    day_confirmed = models.BooleanField("логический день подтверждён", default=False)
    camera = models.CharField("камера", max_length=300, blank=True)
    content_sha256 = models.CharField("SHA-256 оригинала", max_length=64, blank=True)
    created_at = models.DateTimeField("создана", auto_now_add=True)
    updated_at = models.DateTimeField("изменена", auto_now=True)

    class Meta:
        ordering: ClassVar[list[str]] = ["pk"]
        constraints: ClassVar[list] = [
            models.UniqueConstraint(
                fields=["batch", "client_id"], name="unique_batch_photo_client_id"
            ),
            models.CheckConstraint(
                condition=models.Q(logical_day__isnull=True)
                | models.Q(logical_day__gte=1, logical_day__lte=99),
                name="photo_logical_day_between_1_and_99",
            ),
            models.CheckConstraint(
                condition=models.Q(day_confirmed=False) | models.Q(logical_day__isnull=False),
                name="confirmed_photo_has_logical_day",
            ),
        ]
        verbose_name = "фотография"
        verbose_name_plural = "фотографии"

    def __str__(self):
        return f"{self.photo_key}: {self.relative_path}"


class PhotoPlacement(models.Model):
    class Source(models.TextChoices):
        EXIF = "exif", "GPS из EXIF"
        TRACK = "track", "интерполяция по треку"
        MANUAL = "manual", "вручную"

    photo = models.OneToOneField(
        PhotoAsset,
        on_delete=models.CASCADE,
        primary_key=True,
        related_name="placement",
        verbose_name="фотография",
    )
    point = models.PointField("координаты", srid=4326)
    elevation = models.FloatField("высота", null=True, blank=True)
    source = models.CharField("источник", max_length=10, choices=Source.choices)
    track = models.ForeignKey(
        "tracks.Track",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="photo_placements",
        verbose_name="трек",
    )
    algorithm_version = models.PositiveIntegerField("версия алгоритма", null=True, blank=True)
    details = models.JSONField("детали", default=dict, blank=True)
    confirmed = models.BooleanField("подтверждено", default=False)
    updated_at = models.DateTimeField("изменено", auto_now=True)

    class Meta:
        verbose_name = "геопривязка фотографии"
        verbose_name_plural = "геопривязки фотографий"

    def __str__(self):
        return f"{self.photo.photo_key}: {self.source}"
