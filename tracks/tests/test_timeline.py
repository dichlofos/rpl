import tempfile
from io import StringIO

from django.core.files.base import ContentFile
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.test import TestCase, override_settings

from tracks.models import Track, TrackTimeline
from tracks.services import parse_gpx
from tracks.tests.test_tracks import GPX

IRREGULAR_GPX = b"""<?xml version="1.0" encoding="UTF-8"?>
<gpx version="1.1" creator="RPL tests" xmlns="http://www.topografix.com/GPX/1/1">
  <trk><trkseg>
    <trkpt lat="55.0" lon="37.0"><ele>100</ele><time>2026-08-09T08:00:00.125Z</time></trkpt>
    <trkpt lat="55.1" lon="37.1"><ele>101</ele></trkpt>
    <trkpt lat="55.2" lon="37.2"><time>2026-08-09T08:02:00Z</time></trkpt>
    <trkpt lat="55.3" lon="37.3"><time>2026-08-09T08:01:00Z</time></trkpt>
    <trkpt lat="55.4" lon="37.4"><time>2026-08-09T09:03:00+01:00</time></trkpt>
  </trkseg></trk>
</gpx>"""


class TimelineParsingTests(TestCase):
    def test_timeline_is_compact_utc_milliseconds_and_preserves_breaks(self):
        parsed = parse_gpx(IRREGULAR_GPX)

        self.assertEqual(parsed.timeline["version"], 1)
        self.assertEqual(parsed.timeline["time_unit"], "ms")
        self.assertEqual(parsed.timed_points_count, 4)
        self.assertEqual([len(segment) for segment in parsed.timeline["segments"]], [1, 1, 2])
        first = parsed.timeline["segments"][0][0]
        self.assertEqual(first, [1786262400125, 37.0, 55.0, 100.0])
        self.assertIsNone(parsed.timeline["segments"][1][0][3])
        self.assertEqual(
            parsed.timeline["segments"][2][1][0],
            1786262580000,
        )


@override_settings(MEDIA_ROOT=tempfile.mkdtemp())
class TrackTimelineTests(TestCase):
    def make_track(self):
        return Track.objects.create(
            gpx_file=SimpleUploadedFile("route.gpx", GPX, content_type="application/gpx+xml")
        )

    def test_upload_creates_timeline(self):
        track = self.make_track()

        self.assertEqual(track.timeline.points_count, 3)
        self.assertEqual(len(track.timeline.data["segments"]), 1)
        self.assertEqual(track.timeline.data["segments"][0][0][1:3], [37.61, 55.75])

    def test_replacing_working_gpx_updates_timeline(self):
        track = self.make_track()
        track.gpx_file = ContentFile(IRREGULAR_GPX, name="edited.gpx")
        track.save()

        track.timeline.refresh_from_db()
        self.assertEqual(track.timeline.points_count, 4)
        self.assertEqual([len(item) for item in track.timeline.data["segments"]], [1, 1, 2])

    def test_command_backfills_and_can_skip_existing_timeline(self):
        track = self.make_track()
        TrackTimeline.objects.filter(track=track).delete()
        output = StringIO()

        call_command("rebuild_track_timelines", "--missing-only", stdout=output)

        timeline = TrackTimeline.objects.get(track=track)
        self.assertEqual(timeline.points_count, 3)
        self.assertIn("1 timeline(s) rebuilt.", output.getvalue())

        second_output = StringIO()
        call_command("rebuild_track_timelines", "--missing-only", stdout=second_output)
        self.assertIn("0 timeline(s) rebuilt.", second_output.getvalue())
