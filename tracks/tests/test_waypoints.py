import json
import tempfile

import gpxpy
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse

from tracks.archive_services import restore_original
from tracks.group_services import assign_track
from tracks.models import Track, TrackGroup
from tracks.services import InvalidGPX, edit_gpx, parse_gpx
from tracks.tests.test_tracks import GPX

WPT = b"""<wpt lat="55.8" lon="37.8"><ele>123.4</ele><name>Camp</name>
<desc>Water</desc><sym>Flag</sym><time>2026-08-10T10:00:00Z</time>
<extensions><custom xmlns="urn:test">keep</custom></extensions></wpt>"""
ONLY_WPT = (
    b'<gpx version="1.1" creator="tests" xmlns="http://www.topografix.com/GPX/1/1">'
    + WPT
    + b"</gpx>"
)
MIXED = GPX.replace(b"</gpx>", WPT + b"</gpx>")


class WaypointTests(TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        settings = override_settings(MEDIA_ROOT=directory.name)
        settings.enable()
        self.addCleanup(settings.disable)
        self.owner = get_user_model().objects.create_user("owner")

    def make_track(self, content=ONLY_WPT):
        return Track.objects.create(
            owner=self.owner, gpx_file=SimpleUploadedFile("points.gpx", content)
        )

    def test_waypoint_only_upload_and_public_geometry(self):
        self.client.force_login(self.owner)
        response = self.client.post(
            reverse("tracks:upload"), {"gpx_file": SimpleUploadedFile("camp.gpx", ONLY_WPT)}
        )
        self.assertEqual(response.status_code, 302)
        track = Track.objects.get()
        self.assertIsNone(track.geometry)
        self.assertEqual((track.points_count, track.waypoints_count, track.distance_m), (0, 1, 0))
        self.assertIsNone(track.duration_s)
        self.assertFalse(track.region_links.exists())
        feature = self.client.get(reverse("tracks:geojson", args=[track.public_id])).json()
        self.assertIsNone(feature["geometry"])
        self.assertEqual(feature["waypoints"][0]["name"], "Camp")
        page = self.client.get(track.get_absolute_url())
        self.assertContains(page, "Путевых точек")
        self.assertContains(page, "Показывать имена точек")

    def test_mixed_waypoints_do_not_change_route_statistics(self):
        route = parse_gpx(GPX)
        mixed = parse_gpx(MIXED)
        self.assertEqual(mixed.points_count, 3)
        self.assertEqual(mixed.waypoints_count, 1)
        self.assertEqual(mixed.distance_m, route.distance_m)
        self.assertEqual(mixed.duration_s, route.duration_s)
        self.assertEqual(mixed.geojson["geometry"], route.geojson["geometry"])
        self.assertEqual(mixed.max_latitude, 55.8)

    def test_single_track_point_and_singleton_segments_are_supported(self):
        data = b'<gpx version="1.1"><trk><trkseg><trkpt lat="55" lon="37"/></trkseg></trk></gpx>'
        track = self.make_track(data)
        self.assertEqual(track.points_count, 1)
        self.assertEqual(track.geojson["geometry"]["type"], "Point")
        self.assertIsNone(track.geometry)
        mixed = GPX.replace(b"</trk>", b'<trkseg><trkpt lat="56" lon="38"/></trkseg></trk>')
        track = self.make_track(mixed)
        self.assertEqual(track.geojson["geometry"]["type"], "GeometryCollection")
        self.assertEqual(len(track.geometry), 1)

    def test_empty_file_is_rejected(self):
        with self.assertRaises(InvalidGPX):
            parse_gpx(b'<gpx version="1.1"><trk><trkseg/></trk></gpx>')

    def test_edit_preserves_waypoint_metadata_and_archive(self):
        track = self.make_track()
        self.client.force_login(self.owner)
        waypoint = {
            **track.geojson["waypoints"][0],
            "name": "New camp",
            "description": "Lake",
            "coordinates": [38, 56],
            "elevation": 140,
        }
        response = self.client.post(
            reverse("tracks:edit", args=[track.public_id]),
            json.dumps({"waypoints": [waypoint]}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)
        track.refresh_from_db()
        with track.gpx_file.open("rb") as current:
            point = gpxpy.parse(current.read()).waypoints[0]
        self.assertEqual(
            (point.name, point.description, point.longitude, point.latitude, point.elevation),
            ("New camp", "Lake", 38, 56, 140),
        )
        self.assertEqual(point.symbol, "Flag")
        self.assertEqual(point.extensions[0].text, "keep")
        self.assertIsNotNone(point.time)
        with track.original_gpx_file.open("rb") as original:
            self.assertEqual(original.read(), ONLY_WPT)
        restore_original(self.owner, track.pk)
        track.refresh_from_db()
        self.assertEqual(track.geojson["waypoints"][0]["name"], "Camp")

    def test_add_delete_and_empty_result_protection(self):
        data = edit_gpx(
            ONLY_WPT, None, waypoints=[{"index": None, "coordinates": [38, 56], "name": "New"}]
        )
        self.assertEqual(gpxpy.parse(data).waypoints[0].name, "New")
        with self.assertRaises(InvalidGPX):
            edit_gpx(ONLY_WPT, [], retained_indices=[], waypoints=[])
        route_only = edit_gpx(MIXED, None, waypoints=[])
        self.assertEqual(parse_gpx(route_only).waypoints_count, 0)
        waypoints_only = edit_gpx(MIXED, [], retained_indices=[])
        self.assertEqual(parse_gpx(waypoints_only).points_count, 0)
        self.assertEqual(parse_gpx(waypoints_only).waypoints_count, 1)

    def test_legacy_edit_keeps_waypoints(self):
        data = edit_gpx(MIXED, [[37.61, 55.75], [37.63, 55.752]], retained_indices=[0, 2])
        self.assertEqual(gpxpy.parse(data).waypoints[0].symbol, "Flag")

    def test_invalid_waypoints_are_rejected(self):
        valid = parse_gpx(ONLY_WPT).geojson["waypoints"][0]
        for changes in [
            {"index": -1},
            {"index": True},
            {"index": 7},
            {"coordinates": [0, 91]},
            {"coordinates": [True, 10]},
            {"elevation": float("inf")},
            {"name": []},
        ]:
            with self.subTest(changes=changes), self.assertRaises(InvalidGPX):
                edit_gpx(ONLY_WPT, None, waypoints=[{**valid, **changes}])
        with self.assertRaises(InvalidGPX):
            edit_gpx(ONLY_WPT, None, waypoints=[valid, valid])

    def test_group_includes_waypoints_and_permissions_still_apply(self):
        track = self.make_track()
        group = TrackGroup.objects.create(name="Trip", owner=self.owner)
        assign_track(self.owner, track.pk, group.pk)
        feature = self.client.get(reverse("tracks:group-geojson", args=[group.public_id])).json()[
            "features"
        ][0]
        self.assertEqual(feature["waypoints"][0]["name"], "Camp")
        self.client.force_login(get_user_model().objects.create_user("stranger"))
        self.assertEqual(
            self.client.post(
                reverse("tracks:edit", args=[track.public_id]),
                json.dumps({"waypoints": []}),
                content_type="application/json",
            ).status_code,
            403,
        )

    def test_group_waypoints_are_merged_and_empty_tracks_are_soft_deleted(self):
        mixed = self.make_track(MIXED)
        mixed.name = "Route with camp"
        mixed.save(update_fields=["name"])
        waypoint_only = self.make_track(ONLY_WPT.replace(b"Camp", b"Spring"))
        waypoint_only.name = "Only spring"
        waypoint_only.save(update_fields=["name"])
        group = TrackGroup.objects.create(name="Trip", owner=self.owner)
        assign_track(self.owner, mixed.pk, group.pk)
        assign_track(self.owner, waypoint_only.pk, group.pk)
        with mixed.original_gpx_file.open("rb") as source:
            mixed_original = source.read()
        with waypoint_only.original_gpx_file.open("rb") as source:
            waypoint_original = source.read()
        self.client.force_login(self.owner)

        response = self.client.post(
            reverse("tracks:group-merge-waypoints", args=[group.public_id]),
            {"name": "Все стоянки"},
        )

        self.assertRedirects(response, group.get_absolute_url())
        mixed.refresh_from_db()
        waypoint_only.refresh_from_db()
        merged = Track.objects.get(name="Все стоянки")
        self.assertIsNone(mixed.deleted_at)
        self.assertIsNotNone(waypoint_only.deleted_at)
        self.assertEqual(parse_gpx(_read_file(mixed.gpx_file)).waypoints_count, 0)
        merged_gpx = gpxpy.parse(_read_file(merged.gpx_file))
        self.assertEqual([point.name for point in merged_gpx.waypoints], ["Camp", "Spring"])
        self.assertEqual(merged_gpx.waypoints[0].symbol, "Flag")
        self.assertEqual(merged_gpx.waypoints[0].extensions[0].text, "keep")
        self.assertEqual(
            list(group.memberships.values_list("track_id", flat=True)), [mixed.pk, merged.pk]
        )
        self.assertEqual(_read_file(mixed.original_gpx_file), mixed_original)
        self.assertEqual(_read_file(waypoint_only.original_gpx_file), waypoint_original)
        track_list = self.client.get(reverse("tracks:list"))
        self.assertNotContains(track_list, "Only spring")
        self.assertContains(track_list, "Все стоянки")
        self.assertEqual(self.client.get(reverse("tracks:dashboard")).context["tracks_count"], 2)
        group_list = self.client.get(reverse("tracks:group-list"))
        listed_group = next(item for item in group_list.context["groups"] if item.pk == group.pk)
        self.assertEqual(listed_group.track_count, 2)

    def test_merge_waypoints_requires_points_and_group_owner(self):
        route = self.make_track(GPX)
        group = TrackGroup.objects.create(name="Route", owner=self.owner)
        assign_track(self.owner, route.pk, group.pk)
        url = reverse("tracks:group-merge-waypoints", args=[group.public_id])
        self.client.force_login(self.owner)
        response = self.client.post(url, {"name": "Points"})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "нет маршрутных точек")
        self.assertEqual(Track.objects.count(), 1)
        self.client.force_login(get_user_model().objects.create_user("outsider"))
        self.assertEqual(self.client.get(url).status_code, 403)


def _read_file(field):
    field.open("rb")
    try:
        return field.read()
    finally:
        field.close()
