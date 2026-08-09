import uuid

from django.db import migrations, models


def populate_public_ids(apps, schema_editor):
    track_model = apps.get_model("tracks", "Track")
    for track in track_model.objects.filter(public_id__isnull=True).iterator():
        track.public_id = uuid.uuid4()
        track.save(update_fields=["public_id"])


class Migration(migrations.Migration):
    dependencies = [("tracks", "0001_initial")]

    operations = [
        migrations.AddField(
            model_name="track",
            name="public_id",
            field=models.UUIDField(
                editable=False, null=True, verbose_name="публичный идентификатор"
            ),
        ),
        migrations.RunPython(populate_public_ids, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="track",
            name="public_id",
            field=models.UUIDField(
                default=uuid.uuid4,
                editable=False,
                unique=True,
                verbose_name="публичный идентификатор",
            ),
        ),
        migrations.RemoveField(model_name="track", name="slug"),
    ]
