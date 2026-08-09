from django.contrib import admin

from .models import Region, RegionImport, TrackRegion


@admin.register(Region)
class RegionAdmin(admin.ModelAdmin):
    list_display = ("name", "country_code", "subtype", "admin_level", "is_active", "source_release")
    list_filter = ("country_code", "admin_level", "subtype", "is_active")
    search_fields = ("name", "external_id", "region_code")
    readonly_fields = ("external_id", "area_external_id", "imported_at")


@admin.register(RegionImport)
class RegionImportAdmin(admin.ModelAdmin):
    list_display = ("release", "status", "started_at", "finished_at")
    list_filter = ("status", "release")
    readonly_fields = (
        "release",
        "status",
        "scope",
        "statistics",
        "error",
        "started_at",
        "finished_at",
    )


@admin.register(TrackRegion)
class TrackRegionAdmin(admin.ModelAdmin):
    list_display = ("track", "region", "coverage_ratio", "distance_m", "is_primary")
    list_filter = ("is_primary", "region__admin_level", "region__country_code")
    readonly_fields = (
        "track",
        "region",
        "coverage_ratio",
        "distance_m",
        "is_primary",
        "calculated_at",
    )
