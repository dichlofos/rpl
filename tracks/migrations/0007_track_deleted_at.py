from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("tracks", "0006_track_waypoints_count")]

    operations = [
        migrations.AddField(
            model_name="track",
            name="deleted_at",
            field=models.DateTimeField(
                blank=True, editable=False, null=True, verbose_name="удалён"
            ),
        ),
    ]
