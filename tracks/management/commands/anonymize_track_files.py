import re
import uuid

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from tracks.models import Track

ANONYMIZED_PATH = re.compile(r"^tracks/[0-9a-f]{3}/[0-9a-f]{3}/[0-9a-f]{32}\.gpx$")


class Command(BaseCommand):
    help = "Rename existing GPX files to random, server-generated paths."

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true")

    def handle(self, *args, **options):
        renamed = 0
        for track in Track.objects.exclude(gpx_file="").iterator():
            old_name = track.gpx_file.name
            if ANONYMIZED_PATH.fullmatch(old_name):
                continue
            if not track.gpx_file.storage.exists(old_name):
                raise CommandError(f"GPX file does not exist: {old_name}")

            token = uuid.uuid4().hex
            target = f"tracks/{token[:3]}/{token[3:6]}/{token}.gpx"
            if options["dry_run"]:
                self.stdout.write(f"{old_name} -> {target}")
                renamed += 1
                continue

            with track.gpx_file.storage.open(old_name, "rb") as source:
                saved_name = track.gpx_file.storage.save(target, source)
            with transaction.atomic():
                Track.objects.filter(pk=track.pk).update(gpx_file=saved_name)
            track.gpx_file.storage.delete(old_name)
            self.stdout.write(f"{old_name} -> {saved_name}")
            renamed += 1

        suffix = " would be renamed" if options["dry_run"] else " renamed"
        self.stdout.write(self.style.SUCCESS(f"{renamed} file(s){suffix}."))
