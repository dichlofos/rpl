import django.contrib.gis.db.models.fields
import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    initial = True
    dependencies = [("tracks", "0004_track_geometry")]

    operations = [
        migrations.CreateModel(
            name="RegionImport",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True, primary_key=True, serialize=False, verbose_name="ID"
                    ),
                ),
                ("release", models.CharField(max_length=40, verbose_name="версия Overture")),
                ("started_at", models.DateTimeField(auto_now_add=True, verbose_name="начат")),
                (
                    "finished_at",
                    models.DateTimeField(blank=True, null=True, verbose_name="завершён"),
                ),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("running", "выполняется"),
                            ("complete", "завершён"),
                            ("failed", "ошибка"),
                        ],
                        default="running",
                        max_length=16,
                        verbose_name="статус",
                    ),
                ),
                ("scope", models.JSONField(default=dict, verbose_name="область импорта")),
                ("statistics", models.JSONField(default=dict, verbose_name="статистика")),
                ("error", models.TextField(blank=True, verbose_name="ошибка")),
            ],
            options={
                "verbose_name": "импорт регионов",
                "verbose_name_plural": "импорты регионов",
                "ordering": ["-started_at"],
            },
        ),
        migrations.CreateModel(
            name="Region",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True, primary_key=True, serialize=False, verbose_name="ID"
                    ),
                ),
                (
                    "external_id",
                    models.CharField(
                        max_length=64, unique=True, verbose_name="Overture division ID"
                    ),
                ),
                (
                    "area_external_id",
                    models.CharField(blank=True, max_length=64, verbose_name="Overture area ID"),
                ),
                (
                    "parent_external_id",
                    models.CharField(blank=True, max_length=64, verbose_name="Overture parent ID"),
                ),
                ("name", models.CharField(max_length=300, verbose_name="название")),
                ("names", models.JSONField(default=dict, verbose_name="локализованные названия")),
                (
                    "country_code",
                    models.CharField(db_index=True, max_length=2, verbose_name="код страны"),
                ),
                (
                    "region_code",
                    models.CharField(blank=True, max_length=16, verbose_name="код региона"),
                ),
                ("subtype", models.CharField(db_index=True, max_length=32, verbose_name="тип")),
                (
                    "admin_level",
                    models.PositiveSmallIntegerField(
                        db_index=True, null=True, verbose_name="административный уровень"
                    ),
                ),
                (
                    "geometry",
                    django.contrib.gis.db.models.fields.MultiPolygonField(
                        srid=4326, verbose_name="геометрия"
                    ),
                ),
                (
                    "overture_version",
                    models.PositiveIntegerField(default=0, verbose_name="версия объекта"),
                ),
                ("source_release", models.CharField(max_length=40, verbose_name="версия Overture")),
                (
                    "is_active",
                    models.BooleanField(db_index=True, default=True, verbose_name="активен"),
                ),
                ("imported_at", models.DateTimeField(auto_now=True, verbose_name="обновлён")),
                (
                    "parent",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="children",
                        to="regions.region",
                        verbose_name="родительский регион",
                    ),
                ),
            ],
            options={
                "verbose_name": "регион",
                "verbose_name_plural": "регионы",
                "ordering": ["country_code", "admin_level", "name"],
            },
        ),
        migrations.CreateModel(
            name="TrackRegion",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True, primary_key=True, serialize=False, verbose_name="ID"
                    ),
                ),
                ("distance_m", models.FloatField(verbose_name="длина в регионе, м")),
                ("coverage_ratio", models.FloatField(verbose_name="доля маршрута")),
                ("is_primary", models.BooleanField(default=False, verbose_name="основной")),
                ("calculated_at", models.DateTimeField(auto_now=True, verbose_name="рассчитано")),
                (
                    "region",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="track_links",
                        to="regions.region",
                    ),
                ),
                (
                    "track",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="region_links",
                        to="tracks.track",
                    ),
                ),
            ],
            options={
                "verbose_name": "регион трека",
                "verbose_name_plural": "регионы треков",
                "ordering": ["region__admin_level", "-coverage_ratio"],
            },
        ),
        migrations.AddIndex(
            model_name="region",
            index=models.Index(
                fields=["country_code", "admin_level", "is_active"],
                name="regions_reg_country_7f2d69_idx",
            ),
        ),
        migrations.AddConstraint(
            model_name="trackregion",
            constraint=models.UniqueConstraint(
                fields=("track", "region"), name="unique_track_region"
            ),
        ),
    ]
