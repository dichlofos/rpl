import json
import tempfile
from io import StringIO
from pathlib import Path

from django.contrib.gis.geos import MultiPolygon, Polygon
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.test import TestCase, override_settings

from regions.models import Region, RegionImport, TrackRegion
from tracks.models import Track

GPX = b"""<?xml version="1.0" encoding="UTF-8"?>
<gpx version="1.1" creator="RPL tests" xmlns="http://www.topografix.com/GPX/1/1">
  <trk><name>Region test</name><trkseg>
    <trkpt lat="55.00" lon="37.00"><ele>100</ele></trkpt>
    <trkpt lat="55.01" lon="37.01"><ele>110</ele></trkpt>
  </trkseg></trk>
</gpx>"""


def polygon(west, south, east, north):
    return MultiPolygon(
        Polygon(((west, south), (east, south), (east, north), (west, north), (west, south))),
        srid=4326,
    )


@override_settings(MEDIA_ROOT=tempfile.mkdtemp())
class RegionClassificationTests(TestCase):
    def test_new_track_is_classified_by_intersection_length(self):
        region = Region.objects.create(
            external_id="region-1",
            name="Тестовая область",
            names={"primary": "Тестовая область"},
            country_code="RU",
            subtype="region",
            admin_level=1,
            geometry=polygon(36, 54, 38, 56),
            source_release="test",
        )

        track = Track.objects.create(
            gpx_file=SimpleUploadedFile("original-name.gpx", GPX),
        )

        link = TrackRegion.objects.get(track=track, region=region)
        self.assertTrue(link.is_primary)
        self.assertGreater(link.distance_m, 1000)
        self.assertAlmostEqual(link.coverage_ratio, 1.0, places=4)
        self.assertEqual(track.geometry.geom_type, "MultiLineString")
        self.assertFalse(track.geometry.hasz)
        self.assertContains(self.client.get(track.get_absolute_url()), "Тестовая область")


class OvertureCommandTests(TestCase):
    def write_geojson(self, name, features):
        path = Path(tempfile.mkdtemp()) / name
        path.write_text(json.dumps({"type": "FeatureCollection", "features": features}))
        return path

    def fixture_paths(self, version=1):
        divisions = self.write_geojson(
            "divisions.geojson",
            [
                {
                    "type": "Feature",
                    "id": "region-1",
                    "properties": {
                        "names": {"primary": "Тестовая область"},
                        "country": "RU",
                        "subtype": "region",
                        "admin_level": 1,
                        "version": version,
                    },
                    "geometry": {"type": "Point", "coordinates": [37, 55]},
                }
            ],
        )
        areas = self.write_geojson(
            "areas.geojson",
            [
                {
                    "type": "Feature",
                    "id": "area-1",
                    "properties": {"division_id": "region-1", "is_land": True},
                    "geometry": {
                        "type": "Polygon",
                        "coordinates": [[[36, 54], [38, 54], [38, 56], [36, 56], [36, 54]]],
                    },
                }
            ],
        )
        return divisions, areas

    def test_dry_run_does_not_change_database(self):
        divisions, areas = self.fixture_paths()
        output = StringIO()

        call_command(
            "import_overture_regions",
            divisions=divisions,
            areas=areas,
            release="test-release",
            country=["RU"],
            admin_level=[1],
            dry_run=True,
            stdout=output,
        )

        self.assertIn("added=1", output.getvalue())
        self.assertFalse(Region.objects.exists())
        self.assertFalse(RegionImport.objects.exists())

    def test_repeated_import_updates_in_place(self):
        divisions, areas = self.fixture_paths(version=1)
        command_options = {
            "divisions": divisions,
            "areas": areas,
            "release": "test-release",
            "country": ["RU"],
            "admin_level": [1],
            "skip_classification": True,
            "stdout": StringIO(),
        }

        call_command("import_overture_regions", **command_options)
        newer_divisions, newer_areas = self.fixture_paths(version=2)
        command_options.update(divisions=newer_divisions, areas=newer_areas)
        call_command("import_overture_regions", **command_options)

        region = Region.objects.get()
        self.assertEqual(region.overture_version, 2)
        self.assertEqual(RegionImport.objects.count(), 2)
