import shutil
import subprocess
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = "Download a bounded Overture Divisions snapshot and import it into RPL."

    def add_arguments(self, parser):
        parser.add_argument("--bbox", required=True, help="west,south,east,north")
        parser.add_argument("--release", required=True, help="Release label recorded by RPL")
        parser.add_argument("--country", action="append", default=[])
        parser.add_argument("--admin-level", action="append", type=int, default=[])
        parser.add_argument("--dry-run", action="store_true")
        parser.add_argument("--skip-classification", action="store_true")

    def handle(self, *args, **options):
        venv_executable = Path(sys.executable).with_name("overturemaps")
        executable = shutil.which("overturemaps") or (
            str(venv_executable) if venv_executable.exists() else None
        )
        if not executable:
            raise CommandError("overturemaps is not installed; run: pip install -e '.[geo]'")

        bbox = options["bbox"]
        parts = bbox.split(",")
        if len(parts) != 4:
            raise CommandError("--bbox must contain west,south,east,north")
        try:
            west, south, east, north = (float(part) for part in parts)
        except ValueError as exc:
            raise CommandError("--bbox coordinates must be numbers") from exc
        if not (-180 <= west < east <= 180 and -90 <= south < north <= 90):
            raise CommandError("--bbox is outside WGS84 bounds or has invalid ordering")

        with TemporaryDirectory(prefix="rpl-overture-") as temp_dir:
            divisions = f"{temp_dir}/divisions.geojsonseq"
            areas = f"{temp_dir}/division_areas.geojsonseq"
            for feature_type, output in (("division", divisions), ("division_area", areas)):
                command = [
                    executable,
                    "download",
                    f"--bbox={bbox}",
                    "--type",
                    feature_type,
                    "-f",
                    "geojsonseq",
                    "-o",
                    output,
                    "--release",
                    options["release"],
                ]
                self.stdout.write(f"Downloading Overture {feature_type}...")
                try:
                    subprocess.run(command, check=True)
                except subprocess.CalledProcessError as exc:
                    raise CommandError(f"Overture download failed for {feature_type}") from exc

            call_command(
                "import_overture_regions",
                divisions=divisions,
                areas=areas,
                release=options["release"],
                country=options["country"],
                admin_level=options["admin_level"],
                dry_run=options["dry_run"],
                skip_classification=options["skip_classification"],
                stdout=self.stdout,
                stderr=self.stderr,
            )
