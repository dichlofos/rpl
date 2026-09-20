import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("tracks", "0007_track_deleted_at")]

    operations = [
        migrations.CreateModel(
            name="TrackTimeline",
            fields=[
                (
                    "track",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.CASCADE,
                        primary_key=True,
                        related_name="timeline",
                        serialize=False,
                        to="tracks.track",
                        verbose_name="трек",
                    ),
                ),
                (
                    "data",
                    models.JSONField(
                        default=dict, editable=False, verbose_name="временная геометрия"
                    ),
                ),
                (
                    "points_count",
                    models.PositiveIntegerField(
                        default=0, editable=False, verbose_name="точек со временем"
                    ),
                ),
                ("updated_at", models.DateTimeField(auto_now=True, verbose_name="обновлена")),
            ],
            options={
                "verbose_name": "временная геометрия трека",
                "verbose_name_plural": "временная геометрия треков",
            },
        ),
    ]
