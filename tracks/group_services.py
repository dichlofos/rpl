from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Max
from django.utils import timezone

from .models import Track, TrackGroup, TrackGroupMembership
from .permissions import require_manage


@transaction.atomic
def assign_track(user, track_id, group_id, *, expected_group_id=None):
    """The single-group policy lives here, not in Track's database layout."""
    track = Track.objects.select_for_update().get(pk=track_id)
    require_manage(user, track)
    links = list(track.group_memberships.all())
    previous_ids = {link.group_id for link in links}
    if expected_group_id is not None and previous_ids != {expected_group_id}:
        raise ValidationError("Состав группы уже изменился. Обновите страницу.")
    ids = previous_ids | ({group_id} if group_id is not None else set())
    groups = {
        group.pk: group
        for group in TrackGroup.objects.select_for_update().filter(pk__in=ids).order_by("pk")
    }
    if group_id is not None and group_id not in groups:
        raise ValidationError("Группа уже удалена. Выберите другую.")
    for group in groups.values():
        require_manage(user, group)
    if group_id is not None:
        group = groups[group_id]
        if track.owner_id != group.owner_id:
            raise ValidationError("У трека и группы должен быть один владелец.")
    if previous_ids == ({group_id} if group_id is not None else set()):
        return
    track.group_memberships.all().delete()
    if group_id is not None:
        last = groups[group_id].memberships.aggregate(last=Max("position"))["last"]
        TrackGroupMembership.objects.create(
            group_id=group_id, track=track, position=0 if last is None else last + 1
        )
    TrackGroup.objects.filter(pk__in=ids).update(updated_at=timezone.now())


@transaction.atomic
def move_track(user, group_id, track_id, direction):
    if direction not in {"up", "down"}:
        raise ValidationError("Неизвестное направление перемещения.")
    group = TrackGroup.objects.select_for_update().get(pk=group_id)
    require_manage(user, group)
    links = list(group.memberships.all())
    index = next((i for i, link in enumerate(links) if link.track_id == track_id), None)
    if index is None:
        raise ValidationError("Трек больше не входит в эту группу.")
    target = index + (-1 if direction == "up" else 1)
    if not 0 <= target < len(links):
        return
    links[index], links[target] = links[target], links[index]
    for position, link in enumerate(links):
        link.position = position
    TrackGroupMembership.objects.bulk_update(links, ["position"])
    group.save(update_fields=["updated_at"])


@transaction.atomic
def delete_group(user, group_id):
    group = TrackGroup.objects.select_for_update().get(pk=group_id)
    require_manage(user, group)
    group.delete()
