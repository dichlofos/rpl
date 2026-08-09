from typing import ClassVar

from django.contrib.gis.db import models


class RegionImport(models.Model):
    class Status(models.TextChoices):
        RUNNING = "running", "выполняется"
        COMPLETE = "complete", "завершён"
        FAILED = "failed", "ошибка"

    release = models.CharField("версия Overture", max_length=40)
    started_at = models.DateTimeField("начат", auto_now_add=True)
    finished_at = models.DateTimeField("завершён", null=True, blank=True)
    status = models.CharField("статус", max_length=16, choices=Status, default=Status.RUNNING)
    scope = models.JSONField("область импорта", default=dict)
    statistics = models.JSONField("статистика", default=dict)
    error = models.TextField("ошибка", blank=True)

    class Meta:
        ordering: ClassVar[list[str]] = ["-started_at"]
        verbose_name = "импорт регионов"
        verbose_name_plural = "импорты регионов"


class Region(models.Model):
    external_id = models.CharField("Overture division ID", max_length=64, unique=True)
    area_external_id = models.CharField("Overture area ID", max_length=64, blank=True)
    parent_external_id = models.CharField("Overture parent ID", max_length=64, blank=True)
    parent = models.ForeignKey(
        "self",
        verbose_name="родительский регион",
        related_name="children",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
    )
    name = models.CharField("название", max_length=300)
    names = models.JSONField("локализованные названия", default=dict)
    country_code = models.CharField("код страны", max_length=2, db_index=True)
    region_code = models.CharField("код региона", max_length=16, blank=True)
    subtype = models.CharField("тип", max_length=32, db_index=True)
    admin_level = models.PositiveSmallIntegerField(
        "административный уровень", null=True, db_index=True
    )
    geometry = models.MultiPolygonField("геометрия", srid=4326)
    overture_version = models.PositiveIntegerField("версия объекта", default=0)
    source_release = models.CharField("версия Overture", max_length=40)
    is_active = models.BooleanField("активен", default=True, db_index=True)
    imported_at = models.DateTimeField("обновлён", auto_now=True)

    class Meta:
        ordering: ClassVar[list[str]] = ["country_code", "admin_level", "name"]
        indexes: ClassVar[list] = [
            models.Index(fields=["country_code", "admin_level", "is_active"]),
        ]
        verbose_name = "регион"
        verbose_name_plural = "регионы"

    def __str__(self):
        return self.name


class TrackRegion(models.Model):
    track = models.ForeignKey("tracks.Track", related_name="region_links", on_delete=models.CASCADE)
    region = models.ForeignKey(Region, related_name="track_links", on_delete=models.PROTECT)
    distance_m = models.FloatField("длина в регионе, м")
    coverage_ratio = models.FloatField("доля маршрута")
    is_primary = models.BooleanField("основной", default=False)
    calculated_at = models.DateTimeField("рассчитано", auto_now=True)

    class Meta:
        constraints: ClassVar[list] = [
            models.UniqueConstraint(fields=["track", "region"], name="unique_track_region"),
        ]
        ordering: ClassVar[list[str]] = ["region__admin_level", "-coverage_ratio"]
        verbose_name = "регион трека"
        verbose_name_plural = "регионы треков"

    @property
    def coverage_percent(self):
        return self.coverage_ratio * 100
