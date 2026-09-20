import uuid

import django.contrib.gis.db.models.fields
import django.core.validators
import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models

import photos.models


class Migration(migrations.Migration):
    initial = True

    dependencies = [
        ("reports", "0002_personalapitoken"),
        ("tracks", "0008_tracktimeline"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="PhotoBatch",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True, primary_key=True, serialize=False, verbose_name="ID"
                    ),
                ),
                ("public_id", models.UUIDField(default=uuid.uuid4, editable=False, unique=True)),
                ("client_id", models.UUIDField(verbose_name="локальный идентификатор")),
                ("name", models.CharField(max_length=200, verbose_name="название")),
                (
                    "calibration",
                    models.JSONField(blank=True, default=dict, verbose_name="калибровка времени"),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True, verbose_name="создана")),
                ("updated_at", models.DateTimeField(auto_now=True, verbose_name="изменена")),
                (
                    "owner",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="photo_batches",
                        to=settings.AUTH_USER_MODEL,
                        verbose_name="ответственный",
                    ),
                ),
                (
                    "report",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="photo_batches",
                        to="reports.travelreport",
                        verbose_name="отчёт",
                    ),
                ),
            ],
            options={
                "verbose_name": "пачка фотографий",
                "verbose_name_plural": "пачки фотографий",
                "ordering": ["-updated_at", "-pk"],
            },
        ),
        migrations.CreateModel(
            name="PhotoAsset",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True, primary_key=True, serialize=False, verbose_name="ID"
                    ),
                ),
                ("public_id", models.UUIDField(default=uuid.uuid4, editable=False, unique=True)),
                ("client_id", models.UUIDField(verbose_name="локальный идентификатор")),
                (
                    "photo_key",
                    models.CharField(
                        default=photos.models.generate_photo_key,
                        editable=False,
                        max_length=8,
                        unique=True,
                        validators=[django.core.validators.RegexValidator("^[0-9a-f]{8}$")],
                        verbose_name="ключ фотографии",
                    ),
                ),
                (
                    "relative_path",
                    models.CharField(max_length=1024, verbose_name="исходное имя"),
                ),
                ("file_size", models.PositiveBigIntegerField(verbose_name="размер файла")),
                (
                    "file_mtime_ns",
                    models.PositiveBigIntegerField(verbose_name="время изменения файла, нс"),
                ),
                (
                    "captured_at_raw",
                    models.CharField(blank=True, max_length=64, verbose_name="исходное время EXIF"),
                ),
                (
                    "timezone_explicit",
                    models.BooleanField(default=False, verbose_name="часовой пояс задан"),
                ),
                (
                    "time_offset_seconds",
                    models.IntegerField(default=0, verbose_name="поправка времени, с"),
                ),
                (
                    "captured_at_normalized",
                    models.DateTimeField(
                        blank=True, null=True, verbose_name="нормализованное время"
                    ),
                ),
                ("camera", models.CharField(blank=True, max_length=300, verbose_name="камера")),
                (
                    "content_sha256",
                    models.CharField(blank=True, max_length=64, verbose_name="SHA-256 оригинала"),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True, verbose_name="создана")),
                ("updated_at", models.DateTimeField(auto_now=True, verbose_name="изменена")),
                (
                    "batch",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="photos",
                        to="photos.photobatch",
                        verbose_name="пачка",
                    ),
                ),
            ],
            options={
                "verbose_name": "фотография",
                "verbose_name_plural": "фотографии",
                "ordering": ["pk"],
            },
        ),
        migrations.CreateModel(
            name="PhotoPlacement",
            fields=[
                (
                    "photo",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.CASCADE,
                        primary_key=True,
                        related_name="placement",
                        serialize=False,
                        to="photos.photoasset",
                        verbose_name="фотография",
                    ),
                ),
                (
                    "point",
                    django.contrib.gis.db.models.fields.PointField(
                        srid=4326, verbose_name="координаты"
                    ),
                ),
                ("elevation", models.FloatField(blank=True, null=True, verbose_name="высота")),
                (
                    "source",
                    models.CharField(
                        choices=[
                            ("exif", "GPS из EXIF"),
                            ("track", "интерполяция по треку"),
                            ("manual", "вручную"),
                        ],
                        max_length=10,
                        verbose_name="источник",
                    ),
                ),
                (
                    "algorithm_version",
                    models.PositiveIntegerField(
                        blank=True, null=True, verbose_name="версия алгоритма"
                    ),
                ),
                (
                    "details",
                    models.JSONField(blank=True, default=dict, verbose_name="детали"),
                ),
                ("confirmed", models.BooleanField(default=False, verbose_name="подтверждено")),
                ("updated_at", models.DateTimeField(auto_now=True, verbose_name="изменено")),
                (
                    "track",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="photo_placements",
                        to="tracks.track",
                        verbose_name="трек",
                    ),
                ),
            ],
            options={
                "verbose_name": "геопривязка фотографии",
                "verbose_name_plural": "геопривязки фотографий",
            },
        ),
        migrations.AddConstraint(
            model_name="photobatch",
            constraint=models.UniqueConstraint(
                fields=("owner", "client_id"), name="unique_owner_photo_batch_client_id"
            ),
        ),
        migrations.AddConstraint(
            model_name="photoasset",
            constraint=models.UniqueConstraint(
                fields=("batch", "client_id"), name="unique_batch_photo_client_id"
            ),
        ),
    ]
