import tempfile
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import connection, connections
from django.test import TransactionTestCase, override_settings

from tracks.group_services import assign_track
from tracks.models import Track, TrackGroup
from tracks.tests.test_tracks import GPX


class GroupTransactionTests(TransactionTestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        settings = override_settings(MEDIA_ROOT=directory.name)
        settings.enable()
        self.addCleanup(settings.disable)
        self.owner = get_user_model().objects.create_user("owner")
        self.groups = [
            TrackGroup.objects.create(name=f"Group {i}", owner=self.owner) for i in range(2)
        ]
        self.tracks = [
            Track.objects.create(
                owner=self.owner,
                gpx_file=SimpleUploadedFile("track.gpx", GPX),
            )
            for _ in range(2)
        ]

    def assign_concurrently(self, pairs):
        barrier = Barrier(2)

        def worker(pair):
            try:
                with connection.cursor() as cursor:
                    cursor.execute("SET lock_timeout = '5s'")
                barrier.wait(timeout=5)
                assign_track(self.owner, *pair)
            finally:
                connections.close_all()

        with ThreadPoolExecutor(max_workers=2) as executor:
            list(executor.map(worker, pairs))

    def test_concurrent_transfers_keep_one_membership_without_errors(self):
        self.assign_concurrently([(self.tracks[0].pk, group.pk) for group in self.groups])
        self.assertEqual(self.tracks[0].group_memberships.count(), 1)

    def test_concurrent_additions_get_distinct_positions(self):
        self.assign_concurrently([(track.pk, self.groups[0].pk) for track in self.tracks])
        self.assertEqual(
            list(self.groups[0].memberships.values_list("position", flat=True)), [0, 1]
        )
