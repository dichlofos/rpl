import django.core.validators
import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    initial = True

    dependencies = [migrations.swappable_dependency(settings.AUTH_USER_MODEL)]

    operations = [
        migrations.CreateModel(
            name="Track",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True, primary_key=True, serialize=False, verbose_name="ID"
                    ),
                ),
                ("name", models.CharField(blank=True, max_length=200, verbose_name="название")),
                (
                    "slug",
                    models.SlugField(
                        blank=True, max_length=220, unique=True, verbose_name="идентификатор"
                    ),
                ),
                ("description", models.TextField(blank=True, verbose_name="описание")),
                (
                    "gpx_file",
                    models.FileField(
                        upload_to="tracks/%Y/%m/",
                        validators=[django.core.validators.FileExtensionValidator(["gpx"])],
                        verbose_name="GPX-файл",
                    ),
                ),
                ("uploaded_at", models.DateTimeField(auto_now_add=True, verbose_name="загружен")),
                (
                    "distance_m",
                    models.FloatField(default=0, editable=False, verbose_name="дистанция, м"),
                ),
                (
                    "elevation_gain_m",
                    models.FloatField(default=0, editable=False, verbose_name="набор высоты, м"),
                ),
                (
                    "elevation_loss_m",
                    models.FloatField(default=0, editable=False, verbose_name="сброс высоты, м"),
                ),
                (
                    "min_elevation_m",
                    models.FloatField(
                        editable=False, null=True, verbose_name="минимальная высота, м"
                    ),
                ),
                (
                    "max_elevation_m",
                    models.FloatField(
                        editable=False, null=True, verbose_name="максимальная высота, м"
                    ),
                ),
                (
                    "points_count",
                    models.PositiveIntegerField(default=0, editable=False, verbose_name="точек"),
                ),
                (
                    "started_at",
                    models.DateTimeField(editable=False, null=True, verbose_name="начало"),
                ),
                (
                    "finished_at",
                    models.DateTimeField(editable=False, null=True, verbose_name="окончание"),
                ),
                (
                    "duration_s",
                    models.FloatField(
                        editable=False, null=True, verbose_name="продолжительность, с"
                    ),
                ),
                ("min_latitude", models.FloatField(editable=False, null=True)),
                ("min_longitude", models.FloatField(editable=False, null=True)),
                ("max_latitude", models.FloatField(editable=False, null=True)),
                ("max_longitude", models.FloatField(editable=False, null=True)),
                ("geojson", models.JSONField(default=dict, editable=False)),
                (
                    "owner",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="tracks",
                        to=settings.AUTH_USER_MODEL,
                        verbose_name="владелец",
                    ),
                ),
            ],
            options={
                "verbose_name": "трек",
                "verbose_name_plural": "треки",
                "ordering": ["-uploaded_at"],
            },
        )
    ]
