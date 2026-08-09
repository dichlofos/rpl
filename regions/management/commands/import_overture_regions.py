from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from regions.importers import InvalidOvertureData, load_region_records
from regions.models import Region, RegionImport
from regions.services import classify_tracks_intersecting_many


class Command(BaseCommand):
    help = "Import or update Overture division and division_area GeoJSON files."

    def add_arguments(self, parser):
        parser.add_argument("--divisions", required=True)
        parser.add_argument("--areas", required=True)
        parser.add_argument("--release", required=True)
        parser.add_argument("--country", action="append", default=[])
        parser.add_argument("--admin-level", action="append", type=int, default=[])
        parser.add_argument("--authoritative", action="store_true")
        parser.add_argument("--dry-run", action="store_true")
        parser.add_argument("--skip-classification", action="store_true")

    def handle(self, *args, **options):
        scope = {
            "countries": options["country"],
            "admin_levels": options["admin_level"],
            "authoritative": options["authoritative"],
        }
        try:
            records = load_region_records(
                options["divisions"],
                options["areas"],
                countries=options["country"],
                admin_levels=options["admin_level"],
            )
        except (OSError, InvalidOvertureData) as exc:
            raise CommandError(str(exc)) from exc
        if not records:
            raise CommandError("No matching land division areas found; database was not changed.")

        seen = {record.division.external_id for record in records}
        existing = Region.objects.in_bulk(seen, field_name="external_id")
        added = sum(record.division.external_id not in existing for record in records)
        changed = sum(
            record.division.external_id in existing
            and (
                existing[record.division.external_id].overture_version != record.division.version
                or not existing[record.division.external_id].geometry.equals_exact(
                    record.geometry, 0
                )
            )
            for record in records
        )
        if options["dry_run"]:
            self.stdout.write(f"validated={len(records)} added={added} changed={changed}")
            return

        import_run = RegionImport.objects.create(
            release=options["release"], scope=scope, status=RegionImport.Status.RUNNING
        )
        affected_geometries = []
        try:
            with transaction.atomic():
                for record in records:
                    division = record.division
                    needs_reclassification = (
                        division.external_id not in existing
                        or existing[division.external_id].overture_version != division.version
                        or not existing[division.external_id].geometry.equals_exact(
                            record.geometry, 0
                        )
                    )
                    if needs_reclassification and division.external_id in existing:
                        affected_geometries.append(existing[division.external_id].geometry)
                    region, _created = Region.objects.update_or_create(
                        external_id=division.external_id,
                        defaults={
                            "area_external_id": record.area_external_id,
                            "parent_external_id": division.parent_external_id,
                            "name": division.name,
                            "names": division.names,
                            "country_code": division.country_code,
                            "region_code": division.region_code,
                            "subtype": division.subtype,
                            "admin_level": division.admin_level,
                            "geometry": record.geometry,
                            "overture_version": division.version,
                            "source_release": options["release"],
                            "is_active": True,
                        },
                    )
                    if needs_reclassification:
                        affected_geometries.append(region.geometry)

                regions_by_external_id = Region.objects.in_bulk(field_name="external_id")
                for region in Region.objects.filter(external_id__in=seen).exclude(
                    admin_level__isnull=True
                ):
                    parent = regions_by_external_id.get(region.parent_external_id)
                    if not parent and region.admin_level and region.geometry:
                        parent = (
                            Region.objects.filter(
                                is_active=True,
                                country_code=region.country_code,
                                admin_level__lt=region.admin_level,
                                geometry__covers=region.geometry.point_on_surface,
                            )
                            .exclude(pk=region.pk)
                            .order_by("-admin_level")
                            .first()
                        )
                    if region.parent_id != getattr(parent, "id", None):
                        region.parent = parent
                        region.save(update_fields=["parent"])

                deactivated = 0
                if options["authoritative"]:
                    stale = Region.objects.filter(is_active=True)
                    if options["country"]:
                        stale = stale.filter(country_code__in=options["country"])
                    if options["admin_level"]:
                        stale = stale.filter(admin_level__in=options["admin_level"])
                    stale = stale.exclude(external_id__in=seen)
                    affected_geometries.extend(stale.values_list("geometry", flat=True))
                    deactivated = stale.update(is_active=False)

                import_run.status = RegionImport.Status.COMPLETE
                import_run.finished_at = timezone.now()
                import_run.statistics = {
                    "validated": len(records),
                    "added": added,
                    "changed": changed,
                    "deactivated": deactivated,
                }
                import_run.save(update_fields=["status", "finished_at", "statistics"])
        except Exception as exc:
            import_run.status = RegionImport.Status.FAILED
            import_run.finished_at = timezone.now()
            import_run.error = str(exc)
            import_run.save(update_fields=["status", "finished_at", "error"])
            raise

        classified = 0
        if affected_geometries and not options["skip_classification"]:
            classified = classify_tracks_intersecting_many(affected_geometries)
        self.stdout.write(
            self.style.SUCCESS(
                f"validated={len(records)} added={added} changed={changed} "
                f"deactivated={deactivated} classifications={classified}"
            )
        )
