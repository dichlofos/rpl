import uuid

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    initial = True

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("tracks", "0008_tracktimeline"),
    ]

    operations = [
        migrations.CreateModel(
            name="TravelReport",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True, primary_key=True, serialize=False, verbose_name="ID"
                    ),
                ),
                ("name", models.CharField(max_length=200, verbose_name="название")),
                ("description", models.TextField(blank=True, verbose_name="описание")),
                (
                    "public_id",
                    models.UUIDField(
                        default=uuid.uuid4,
                        editable=False,
                        unique=True,
                        verbose_name="публичный идентификатор",
                    ),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True, verbose_name="создан")),
                ("updated_at", models.DateTimeField(auto_now=True, verbose_name="изменён")),
                (
                    "owner",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="travel_reports",
                        to=settings.AUTH_USER_MODEL,
                        verbose_name="владелец",
                    ),
                ),
                (
                    "track_group",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="travel_reports",
                        to="tracks.trackgroup",
                        verbose_name="исходная группа треков",
                    ),
                ),
            ],
            options={
                "verbose_name": "отчёт о путешествии",
                "verbose_name_plural": "отчёты о путешествиях",
                "ordering": ["-updated_at", "-pk"],
            },
        ),
        migrations.CreateModel(
            name="ReportTrack",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True, primary_key=True, serialize=False, verbose_name="ID"
                    ),
                ),
                ("position", models.PositiveIntegerField(default=0, verbose_name="порядок")),
                (
                    "report",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="track_links",
                        to="reports.travelreport",
                        verbose_name="отчёт",
                    ),
                ),
                (
                    "track",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="report_links",
                        to="tracks.track",
                        verbose_name="трек",
                    ),
                ),
            ],
            options={
                "verbose_name": "трек отчёта",
                "verbose_name_plural": "треки отчёта",
                "ordering": ["position", "pk"],
                "constraints": [
                    models.UniqueConstraint(fields=("report", "track"), name="unique_report_track"),
                    models.UniqueConstraint(
                        fields=("report", "position"), name="unique_report_track_position"
                    ),
                ],
            },
        ),
        migrations.AddField(
            model_name="travelreport",
            name="tracks",
            field=models.ManyToManyField(
                related_name="travel_reports", through="reports.ReportTrack", to="tracks.track"
            ),
        ),
    ]
