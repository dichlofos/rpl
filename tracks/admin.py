from django.contrib import admin
from django.utils.html import format_html

from .forms import TrackAdminForm
from .models import Track, TrackGroup
from .permissions import can_manage


@admin.register(Track)
class TrackAdmin(admin.ModelAdmin):
    form = TrackAdminForm
    list_display = ("name", "distance_km_display", "points_count", "owner", "uploaded_at")
    list_filter = ("uploaded_at",)
    search_fields = ("name", "description")
    readonly_fields = (
        "original_gpx_file",
        "group_management",
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

    @admin.display(description="Группа трека")
    def group_management(self, obj):
        if not obj.pk:
            return "Сначала сохраните трек."
        from django.urls import reverse

        return format_html(
            '<a href="{}">Изменить группу</a>', reverse("tracks:group", args=[obj.public_id])
        )


@admin.register(TrackGroup)
class TrackGroupAdmin(admin.ModelAdmin):
    list_display = ("name", "owner", "updated_at")
    search_fields = ("name", "description")
    readonly_fields = ("public_id", "created_at", "updated_at", "manage_members")

    def get_readonly_fields(self, request, obj=None):
        return self.readonly_fields + (("owner",) if obj or not request.user.is_superuser else ())

    def has_change_permission(self, request, obj=None):
        return super().has_change_permission(request, obj) and (
            obj is None or can_manage(request.user, obj)
        )

    def has_delete_permission(self, request, obj=None):
        return super().has_delete_permission(request, obj) and (
            obj is None or can_manage(request.user, obj)
        )

    def get_queryset(self, request):
        queryset = super().get_queryset(request)
        return queryset if request.user.is_superuser else queryset.filter(owner=request.user)

    @admin.display(description="Состав и порядок треков")
    def manage_members(self, obj):
        if not obj.pk:
            return "Сначала сохраните группу."
        return format_html('<a href="{}">Управлять составом группы</a>', obj.get_absolute_url())

    def save_model(self, request, obj, form, change):
        if not obj.pk and not obj.owner_id:
            obj.owner = request.user
        super().save_model(request, obj, form, change)

    def delete_model(self, request, obj):
        from .group_services import delete_group

        delete_group(request.user, obj.pk)

    def delete_queryset(self, request, queryset):
        from .group_services import delete_group

        for pk in queryset.values_list("pk", flat=True):
            delete_group(request.user, pk)
