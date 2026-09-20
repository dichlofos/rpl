from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from tracks.permissions import require_manage

from .models import ReportTrack, TravelReport


@transaction.atomic
def set_report_tracks(user, report_id, track_ids):
    """Replace a report's ordered track selection with tracks from its source group."""
    report = TravelReport.objects.select_for_update().get(pk=report_id)
    require_manage(user, report)
    track_ids = list(track_ids)
    if len(track_ids) != len(set(track_ids)):
        raise ValidationError("Один трек нельзя добавить в отчёт дважды.")

    current_ids = list(report.track_links.values_list("track_id", flat=True))
    allowed_ids = set(current_ids)
    allowed_ids.update(
        set(
            report.track_group.memberships.filter(track_id__in=track_ids).values_list(
                "track_id", flat=True
            )
        )
        if report.track_group_id
        else set()
    )
    if not set(track_ids) <= allowed_ids:
        raise ValidationError("Все треки отчёта должны входить в его исходную группу.")

    if current_ids == track_ids:
        return report
    report.track_links.all().delete()
    ReportTrack.objects.bulk_create(
        [
            ReportTrack(report=report, track_id=track_id, position=position)
            for position, track_id in enumerate(track_ids)
        ]
    )
    TravelReport.objects.filter(pk=report.pk).update(updated_at=timezone.now())
    return report
