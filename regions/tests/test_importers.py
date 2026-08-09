import json
import tempfile
from pathlib import Path

from django.test import SimpleTestCase

from regions.importers import iter_geojson, load_region_records


class OvertureImporterTests(SimpleTestCase):
    def write_geojson(self, name, features):
        path = Path(tempfile.mkdtemp()) / name
        path.write_text(json.dumps({"type": "FeatureCollection", "features": features}))
        return path

    def test_reads_geojson_sequence(self):
        path = Path(tempfile.mkdtemp()) / "features.geojsonseq"
        features = [
            {"type": "Feature", "id": "one", "properties": {}, "geometry": None},
            {"type": "Feature", "id": "two", "properties": {}, "geometry": None},
        ]
        path.write_text("\n".join(json.dumps(feature) for feature in features))

        self.assertEqual(list(iter_geojson(path)), features)

    def test_loads_land_polygon_and_hierarchy(self):
        divisions = self.write_geojson(
            "divisions.geojson",
            [
                {
                    "type": "Feature",
                    "id": "country-id",
                    "properties": {
                        "names": {"primary": "Россия", "common": {"en": "Russia"}},
                        "country": "RU",
                        "subtype": "country",
                        "admin_level": 0,
                        "version": 3,
                    },
                    "geometry": {"type": "Point", "coordinates": [37, 55]},
                },
                {
                    "type": "Feature",
                    "id": "region-id",
                    "properties": {
                        "names": {"primary": "Московская область"},
                        "country": "RU",
                        "region": "RU-MOS",
                        "subtype": "region",
                        "admin_level": 1,
                        "parent_division_id": "country-id",
                        "version": 7,
                    },
                    "geometry": {"type": "Point", "coordinates": [38, 55]},
                },
            ],
        )
        areas = self.write_geojson(
            "areas.geojson",
            [
                {
                    "type": "Feature",
                    "id": "area-id",
                    "properties": {"division_id": "region-id", "is_land": True},
                    "geometry": {
                        "type": "Polygon",
                        "coordinates": [[[37, 54], [39, 54], [39, 56], [37, 56], [37, 54]]],
                    },
                }
            ],
        )

        records = load_region_records(divisions, areas, countries=["RU"], admin_levels=[1])

        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].division.name, "Московская область")
        self.assertEqual(records[0].division.parent_external_id, "country-id")
        self.assertEqual(records[0].geometry.geom_type, "MultiPolygon")

    def test_falls_back_to_repeated_area_properties(self):
        divisions = self.write_geojson("divisions.geojson", [])
        areas = self.write_geojson(
            "areas.geojson",
            [
                {
                    "type": "Feature",
                    "id": "area-id",
                    "properties": {
                        "division_id": "region-id",
                        "names": {"primary": "Московская область"},
                        "country": "RU",
                        "subtype": "region",
                        "admin_level": 1,
                        "version": 2,
                        "is_land": True,
                    },
                    "geometry": {
                        "type": "Polygon",
                        "coordinates": [[[37, 54], [39, 54], [39, 56], [37, 56], [37, 54]]],
                    },
                }
            ],
        )

        records = load_region_records(divisions, areas)

        self.assertEqual(records[0].division.external_id, "region-id")
