import json

import django.contrib.gis.db.models.fields
from django.contrib.gis.geos import GEOSGeometry, LineString, MultiLineString
from django.db import migrations


def populate_track_geometry(apps, schema_editor):
    track_model = apps.get_model("tracks", "Track")
    for track in track_model.objects.exclude(geojson={}).iterator():
        geometry_data = (track.geojson or {}).get("geometry")
        if not geometry_data:
            continue
        coordinates = geometry_data["coordinates"]
        if geometry_data["type"] == "LineString":
            coordinates = [[point[0], point[1]] for point in coordinates]
        else:
            coordinates = [[[point[0], point[1]] for point in segment] for segment in coordinates]
        geometry = GEOSGeometry(
            json.dumps({"type": geometry_data["type"], "coordinates": coordinates}),
            srid=4326,
        )
        if isinstance(geometry, LineString):
            geometry = MultiLineString(geometry, srid=4326)
        track.geometry = geometry
        track.save(update_fields=["geometry"])


class Migration(migrations.Migration):
    dependencies = [("tracks", "0003_anonymize_gpx_upload_path")]

    operations = [
        migrations.AddField(
            model_name="track",
            name="geometry",
            field=django.contrib.gis.db.models.fields.MultiLineStringField(
                editable=False,
                null=True,
                srid=4326,
                verbose_name="геометрия",
            ),
        ),
        migrations.RunPython(populate_track_geometry, migrations.RunPython.noop),
    ]
