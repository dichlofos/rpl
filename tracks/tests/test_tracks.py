import tempfile
import uuid

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse

from tracks.models import Track

GPX = b"""<?xml version="1.0" encoding="UTF-8"?>
<gpx version="1.1" creator="RPL tests" xmlns="http://www.topografix.com/GPX/1/1">
  <trk><name>Test route</name><trkseg>
    <trkpt lat="55.7500" lon="37.6100"><ele>120</ele><time>2026-08-09T08:00:00Z</time></trkpt>
    <trkpt lat="55.7510" lon="37.6200"><ele>135</ele><time>2026-08-09T08:05:00Z</time></trkpt>
    <trkpt lat="55.7520" lon="37.6300"><ele>125</ele><time>2026-08-09T08:10:00Z</time></trkpt>
  </trkseg></trk>
</gpx>"""


@override_settings(MEDIA_ROOT=tempfile.mkdtemp())
class TrackTests(TestCase):
    def make_track(self, name=""):
        return Track.objects.create(
            name=name,
            gpx_file=SimpleUploadedFile("route.gpx", GPX, content_type="application/gpx+xml"),
        )

    def test_import_extracts_geometry_and_metadata(self):
        track = self.make_track()

        self.assertEqual(track.name, "Test route")
        self.assertIsInstance(track.public_id, uuid.UUID)
        self.assertEqual(track.points_count, 3)
        self.assertEqual(track.geojson["geometry"]["type"], "LineString")
        self.assertEqual(track.duration_s, 600)
        self.assertGreater(track.distance_m, 1000)
        self.assertEqual(track.min_elevation_m, 120)
        self.assertEqual(track.max_elevation_m, 135)

    def test_invalid_gpx_is_rejected(self):
        track = Track(gpx_file=SimpleUploadedFile("broken.gpx", b"not xml"))

        with self.assertRaises(ValidationError):
            track.save()

    def test_doctype_is_rejected(self):
        unsafe = b'<!DOCTYPE gpx [<!ENTITY xxe SYSTEM "file:///etc/passwd">]><gpx>&xxe;</gpx>'
        track = Track(gpx_file=SimpleUploadedFile("unsafe.gpx", unsafe))

        with self.assertRaises(ValidationError):
            track.save()

    def test_list_detail_and_geojson(self):
        track = self.make_track()

        list_response = self.client.get(reverse("tracks:list"))
        detail_response = self.client.get(track.get_absolute_url())
        geojson_response = self.client.get(reverse("tracks:geojson", args=[track.public_id]))

        self.assertContains(list_response, "Test route")
        self.assertContains(detail_response, "track-map")
        self.assertEqual(geojson_response.status_code, 200)
        self.assertEqual(geojson_response.json()["properties"]["name"], "Test route")

    def test_duplicate_names_get_unique_public_ids(self):
        first = self.make_track("Один маршрут")
        second = self.make_track("Один маршрут")

        self.assertNotEqual(first.public_id, second.public_id)

    def test_admin_reports_invalid_gpx_as_form_error(self):
        user = get_user_model().objects.create_superuser("admin", "admin@example.test", "password")
        self.client.force_login(user)

        response = self.client.post(
            reverse("admin:tracks_track_add"),
            {
                "name": "Broken",
                "description": "",
                "owner": "",
                "gpx_file": SimpleUploadedFile("broken.gpx", b"not xml"),
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Не удалось разобрать GPX-файл")
        self.assertEqual(Track.objects.count(), 0)

    def test_upload_requires_login(self):
        response = self.client.get(reverse("tracks:upload"))

        self.assertRedirects(response, f"{reverse('login')}?next={reverse('tracks:upload')}")

    def test_authenticated_user_can_upload_track(self):
        user = get_user_model().objects.create_user("rider", password="password")
        self.client.force_login(user)

        response = self.client.post(
            reverse("tracks:upload"),
            {
                "name": "Поход выходного дня",
                "description": "Тестовое описание",
                "gpx_file": SimpleUploadedFile(
                    "weekend.gpx", GPX, content_type="application/gpx+xml"
                ),
            },
        )

        track = Track.objects.get()
        self.assertRedirects(response, track.get_absolute_url(), fetch_redirect_response=False)
        self.assertEqual(track.owner, user)
        self.assertEqual(track.name, "Поход выходного дня")

    def test_upload_page_contains_only_public_fields(self):
        user = get_user_model().objects.create_user("rider", password="password")
        self.client.force_login(user)

        response = self.client.get(reverse("tracks:upload"))

        self.assertContains(response, 'name="name"')
        self.assertContains(response, 'name="description"')
        self.assertContains(response, 'name="gpx_file"')
        self.assertNotContains(response, 'name="owner"')
        self.assertNotContains(response, 'name="public_id"')
