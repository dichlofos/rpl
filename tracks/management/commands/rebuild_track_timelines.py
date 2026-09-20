import uuid

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from tracks.models import Track, TrackTimeline
from tracks.services import InvalidGPX, parse_gpx


class Command(BaseCommand):
    help = "Build temporal geometry for existing tracks from their working GPX files."

    def add_arguments(self, parser):
        parser.add_argument(
            "--track",
            action="append",
            type=uuid.UUID,
            dest="track_ids",
            help="Process only this public track UUID; may be specified more than once.",
        )
        parser.add_argument(
            "--missing-only",
            action="store_true",
            help="Skip tracks that already have temporal geometry.",
        )

    def handle(self, *args, **options):
        queryset = Track.objects.exclude(gpx_file="").order_by("pk")
        if options["track_ids"]:
            queryset = queryset.filter(public_id__in=options["track_ids"])
        if options["missing_only"]:
            queryset = queryset.filter(timeline__isnull=True)

        rebuilt = 0
        failures = []
        for track in queryset.iterator():
            try:
                with track.gpx_file.open("rb") as source:
                    parsed = parse_gpx(source.read(), track.gpx_file.name)
                with transaction.atomic():
                    TrackTimeline.objects.update_or_create(
                        track=track,
                        defaults={
                            "data": parsed.timeline,
                            "points_count": parsed.timed_points_count,
                        },
                    )
            except (InvalidGPX, OSError) as exc:
                failures.append(str(track.public_id))
                self.stderr.write(f"{track.public_id}: {exc}")
                continue
            rebuilt += 1
            self.stdout.write(f"{track.public_id}: {parsed.timed_points_count} timed point(s)")

        self.stdout.write(self.style.SUCCESS(f"{rebuilt} timeline(s) rebuilt."))
        if failures:
            raise CommandError(f"Failed to rebuild {len(failures)} timeline(s).")
