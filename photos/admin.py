from django.contrib import admin

from .models import PhotoAsset, PhotoBatch, PhotoPlacement


@admin.register(PhotoBatch)
class PhotoBatchAdmin(admin.ModelAdmin):
    list_display = ("name", "report", "owner", "updated_at")
    search_fields = ("name", "report__name", "owner__username")
    readonly_fields = ("public_id", "client_id", "created_at", "updated_at")


@admin.register(PhotoAsset)
class PhotoAssetAdmin(admin.ModelAdmin):
    list_display = (
        "photo_key",
        "relative_path",
        "batch",
        "logical_day",
        "day_confirmed",
        "filter_status",
        "captured_at_normalized",
    )
    list_filter = ("filter_status", "day_confirmed", "logical_day")
    search_fields = ("photo_key", "relative_path", "camera")
    readonly_fields = ("public_id", "photo_key", "client_id", "created_at", "updated_at")


@admin.register(PhotoPlacement)
class PhotoPlacementAdmin(admin.ModelAdmin):
    list_display = ("photo", "source", "track", "confirmed", "updated_at")
    list_filter = ("source", "confirmed")
    readonly_fields = ("updated_at",)
