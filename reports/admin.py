from django.contrib import admin

from tracks.models import TrackGroup
from tracks.permissions import can_manage

from .models import PersonalApiToken, ReportTrack, TravelReport


class ReportTrackInline(admin.TabularInline):
    model = ReportTrack
    extra = 0
    ordering = ("position",)


@admin.register(TravelReport)
class TravelReportAdmin(admin.ModelAdmin):
    list_display = ("name", "track_group", "owner", "updated_at")
    list_filter = ("created_at", "updated_at")
    search_fields = ("name", "description")
    readonly_fields = ("public_id", "created_at", "updated_at")
    inlines = (ReportTrackInline,)

    def get_readonly_fields(self, request, obj=None):
        fields = self.readonly_fields
        return fields + (("owner",) if obj or not request.user.is_superuser else ())

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

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        if db_field.name == "track_group" and not request.user.is_superuser:
            kwargs["queryset"] = TrackGroup.objects.filter(owner=request.user)
        return super().formfield_for_foreignkey(db_field, request, **kwargs)

    def save_model(self, request, obj, form, change):
        if not obj.owner_id:
            obj.owner = obj.track_group.owner if obj.track_group_id else request.user
        super().save_model(request, obj, form, change)


@admin.register(PersonalApiToken)
class PersonalApiTokenAdmin(admin.ModelAdmin):
    list_display = ("name", "user", "created_at", "last_used_at", "revoked_at")
    list_filter = ("created_at", "revoked_at")
    search_fields = ("name", "user__username")
    readonly_fields = (
        "user",
        "name",
        "token_id",
        "secret_hash",
        "created_at",
        "last_used_at",
        "revoked_at",
    )

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return request.user.is_superuser

    def has_delete_permission(self, request, obj=None):
        return request.user.is_superuser
