from datetime import datetime

from lrpl.index import LocalIndex
from tests.test_lrpl_probe import make_jpeg


def test_scan_reuses_stable_id_and_cached_metadata(tmp_path, monkeypatch):
    root = tmp_path / "photos"
    root.mkdir()
    make_jpeg(root / "photo.jpg", captured_at="2026:07:18 12:05:00")
    index = LocalIndex(tmp_path / "state" / "lrpl.sqlite3")
    first, failures = index.scan(root)

    def unexpected_read(_path):
        raise AssertionError("Неизменившийся файл не должен читаться повторно")

    monkeypatch.setattr("lrpl.index.read_photo_clock", unexpected_read)
    second, second_failures = index.scan(root)
    index.close()

    assert not failures
    assert not second_failures
    assert second == first
    assert first[0].relative_path == "photo.jpg"
    assert first[0].captured_at == datetime(2026, 7, 18, 12, 5)  # noqa: DTZ001


def test_results_and_server_ids_survive_reopening_index(tmp_path):
    root = tmp_path / "photos"
    root.mkdir()
    make_jpeg(root / "photo.jpg", captured_at="2026:07:18 12:05:00")
    database = tmp_path / "state" / "lrpl.sqlite3"
    index = LocalIndex(database)
    photos, _ = index.scan(root)
    photo = photos[0]
    batch_id = index.save_results(
        root,
        "report-id",
        {photo.camera: -10_800},
        photos,
        [
            {
                "id": photo.id,
                "normalized_at_iso": "2026-07-18T09:05:00+00:00",
                "api_result": {"id": photo.id, "status": "matched"},
            }
        ],
    )
    index.save_day_assignments(batch_id, {photo.id: (4, True)})
    index.save_server_response(
        batch_id,
        {
            "batch": {"id": "server-batch"},
            "photos": [{"id": "server-photo", "client_id": photo.id, "photo_key": "4c7f1234"}],
        },
    )
    index.close()

    reopened = LocalIndex(database)
    batch = reopened.connection.execute("SELECT * FROM batches").fetchone()
    stored = reopened.connection.execute("SELECT * FROM batch_photos").fetchone()
    reopened.close()

    assert batch["id"] == batch_id
    assert batch["server_id"] == "server-batch"
    assert stored["server_id"] == "server-photo"
    assert stored["photo_key"] == "4c7f1234"
    assert stored["time_offset_seconds"] == -10_800
    assert stored["logical_day"] == 4
    assert stored["day_confirmed"] == 1


def test_version_one_index_is_migrated(tmp_path):
    import sqlite3

    database = tmp_path / "lrpl.sqlite3"
    connection = sqlite3.connect(database)
    connection.executescript(
        """
        CREATE TABLE photos (
            id TEXT PRIMARY KEY,
            relative_path TEXT NOT NULL
        );
        CREATE TABLE batches (
            id TEXT PRIMARY KEY
        );
        CREATE TABLE batch_photos (
            batch_id TEXT NOT NULL,
            photo_id TEXT NOT NULL,
            time_offset_seconds INTEGER NOT NULL DEFAULT 0,
            normalized_at TEXT,
            result_json TEXT NOT NULL DEFAULT '{}',
            server_id TEXT,
            photo_key TEXT,
            PRIMARY KEY(batch_id, photo_id)
        );
        PRAGMA user_version = 1;
        """
    )
    connection.close()

    index = LocalIndex(database)
    columns = {row["name"] for row in index.connection.execute("PRAGMA table_info(batch_photos)")}
    version = index.connection.execute("PRAGMA user_version").fetchone()[0]
    index.close()

    assert version == 3
    assert {"logical_day", "day_confirmed", "filter_status"} <= columns


def test_heavy_analysis_is_cached(tmp_path, monkeypatch):
    root = tmp_path / "photos"
    root.mkdir()
    make_jpeg(root / "photo.jpg")
    index = LocalIndex(tmp_path / "lrpl.sqlite3")
    photos, _ = index.scan(root)

    first, failures = index.analyze(photos)

    def unexpected_read(_path):
        raise AssertionError("Сохранённые отпечатки не должны вычисляться повторно")

    monkeypatch.setattr("lrpl.index.read_photo", unexpected_read)
    second, second_failures = index.analyze(photos)
    index.close()

    assert not failures
    assert not second_failures
    assert first == second
    assert first[0].content_sha256
    assert first[0].sharpness > 0


def test_similarity_decisions_survive_reanalysis(tmp_path):
    root = tmp_path / "photos"
    root.mkdir()
    make_jpeg(root / "first.jpg", quality=95)
    make_jpeg(root / "second.jpg", quality=55)
    index = LocalIndex(tmp_path / "lrpl.sqlite3")
    photos, _ = index.scan(root)
    analyzed, _ = index.analyze(photos)
    results = [{"id": photo.id, "normalized_at_iso": None, "api_result": {}} for photo in photos]
    batch_id = index.save_results(root, "report", {}, photos, results)
    recommended = photos[max(range(2), key=lambda item: analyzed[item].sharpness)].id
    stack = {
        "kind": "visual",
        "recommended": recommended,
        "members": [photo.id for photo in photos],
    }

    stack_id = index.save_similarity(batch_id, [photo.id for photo in photos], [stack])[0]
    assert set(index.filter_statuses(batch_id).values()) == {"unreviewed"}

    index.decide_similarity(batch_id, stack_id, "chosen", recommended)
    statuses = index.filter_statuses(batch_id)
    assert statuses[recommended] == "selected"
    assert sorted(statuses.values()) == ["rejected", "selected"]

    index.save_similarity(batch_id, [photo.id for photo in photos], [stack])
    assert index.similarity_stacks(batch_id)[0]["decision"] == "chosen"
    assert index.filter_statuses(batch_id) == statuses

    index.decide_similarity(batch_id, stack_id, "keep_all")
    assert set(index.filter_statuses(batch_id).values()) == {"selected"}
    index.close()
