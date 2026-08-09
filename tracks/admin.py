from django.contrib import admin

from .forms import TrackAdminForm
from .models import Track


@admin.register(Track)
class TrackAdmin(admin.ModelAdmin):
    form = TrackAdminForm
    list_display = ("name", "distance_km_display", "points_count", "owner", "uploaded_at")
    list_filter = ("uploaded_at",)
    search_fields = ("name", "description")
    readonly_fields = (
        "uploaded_at",
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
        "geojson",
        "geometry",
    )

    @admin.display(description="Дистанция, км", ordering="distance_m")
    def distance_km_display(self, obj):
        return f"{obj.distance_km:.1f}"

    def save_model(self, request, obj, form, change):
        if not obj.owner_id:
            obj.owner = request.user
        super().save_model(request, obj, form, change)
