from django.core.files.base import ContentFile
from django.db import transaction

from .models import Track
from .permissions import require_manage


@transaction.atomic
def restore_original(user, track_id):
    track = Track.objects.select_for_update().get(pk=track_id)
    require_manage(user, track)
    with track.original_gpx_file.open("rb") as original:
        content = original.read()
    track.gpx_file = ContentFile(content, name="restored.gpx")
    track.save()
    return track
