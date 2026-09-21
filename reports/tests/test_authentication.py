import io
import tempfile

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.test import Client, TestCase, override_settings
from django.urls import reverse

from reports.models import PersonalApiToken, TravelReport
from reports.services import set_report_tracks
from tracks.group_services import assign_track
from tracks.models import Track, TrackGroup
from tracks.tests.test_tracks import GPX


@override_settings(MEDIA_ROOT=tempfile.mkdtemp())
class ApiTokenTests(TestCase):
    def setUp(self):
        self.owner = get_user_model().objects.create_user("owner")
        self.stranger = get_user_model().objects.create_user("stranger")
        self.group = TrackGroup.objects.create(name="Trip", owner=self.owner)
        self.track = Track.objects.create(
            name="Day 1",
            owner=self.owner,
            gpx_file=SimpleUploadedFile("day.gpx", GPX),
        )
        assign_track(self.owner, self.track.pk, self.group.pk)
        self.report = TravelReport.objects.create(
            name="Trip", owner=self.owner, track_group=self.group
        )
        set_report_tracks(self.owner, self.report.pk, [self.track.pk])

    def bearer(self, token):
        return {"HTTP_AUTHORIZATION": f"Bearer {token}"}

    def test_issued_token_is_hashed_and_authenticates_report_list(self):
        stored, raw = PersonalApiToken.issue(self.owner, "Laptop")

        self.assertTrue(raw.startswith(f"rpl_{stored.token_id}_"))
        self.assertNotIn(raw, stored.secret_hash)
        response = self.client.get(reverse("reports:list"), **self.bearer(raw))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["reports"][0]["id"], str(self.report.public_id))
        stored.refresh_from_db()
        self.assertIsNotNone(stored.last_used_at)

    def test_token_cannot_see_foreign_reports_and_revocation_is_immediate(self):
        foreign_group = TrackGroup.objects.create(name="Foreign", owner=self.stranger)
        TravelReport.objects.create(name="Foreign", owner=self.stranger, track_group=foreign_group)
        stored, raw = PersonalApiToken.issue(self.owner, "Laptop")

        response = self.client.get(reverse("reports:list"), **self.bearer(raw))
        self.assertEqual([item["name"] for item in response.json()["reports"]], ["Trip"])

        stored.revoke()
        self.assertEqual(
            self.client.get(reverse("reports:list"), **self.bearer(raw)).status_code,
            401,
        )

    def test_bearer_token_can_call_interpolation_api(self):
        _, raw = PersonalApiToken.issue(self.owner, "Laptop")
        csrf_client = Client(enforce_csrf_checks=True)

        response = csrf_client.post(
            reverse("reports:interpolate-positions", args=[self.report.public_id]),
            data='{"items":[{"id":"photo","captured_at":"2026-08-09T08:02:30Z"}]}',
            content_type="application/json",
            **self.bearer(raw),
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["results"][0]["status"], "matched")

    def test_invalid_token_is_rejected(self):
        self.assertEqual(
            self.client.get(
                reverse("reports:list"),
                **self.bearer("rpl_0000000000000000_not-a-secret"),
            ).status_code,
            401,
        )

    def test_management_command_prints_usable_token_once(self):
        stdout = io.StringIO()
        stderr = io.StringIO()

        call_command(
            "create_lrpl_token",
            self.owner.username,
            "--name",
            "Desktop",
            stdout=stdout,
            stderr=stderr,
        )

        raw = stdout.getvalue().strip()
        self.assertTrue(raw.startswith("rpl_"))
        self.assertIn("повторно показать", stderr.getvalue())
        self.assertEqual(
            self.client.get(reverse("reports:list"), **self.bearer(raw)).status_code,
            200,
        )
