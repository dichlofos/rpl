import json
import sqlite3
import threading
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from .metadata import JPEG_SUFFIXES, InvalidPhoto, read_photo_clock

SCHEMA_VERSION = 1


@dataclass(frozen=True)
class IndexedPhoto:
    id: str
    path: Path
    relative_path: str
    file_size: int
    file_mtime_ns: int
    captured_at: datetime | None
    timezone_explicit: bool
    camera: str
    latitude: float | None
    longitude: float | None


@dataclass(frozen=True)
class IndexedFailure:
    path: Path
    error: str


def _datetime(value):
    return datetime.fromisoformat(value) if value else None


class LocalIndex:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(self.path, check_same_thread=False)
        self.connection.row_factory = sqlite3.Row
        self.lock = threading.RLock()
        with self.connection:
            self.connection.execute("PRAGMA foreign_keys = ON")
            self.connection.execute("PRAGMA journal_mode = WAL")
        self._migrate()

    def close(self):
        with self.lock:
            self.connection.close()

    def _migrate(self):
        with self.lock, self.connection:
            version = self.connection.execute("PRAGMA user_version").fetchone()[0]
            if version > SCHEMA_VERSION:
                raise RuntimeError("Локальный индекс создан более новой версией LRPL.")
            if version == 0:
                self.connection.executescript(
                    """
                    CREATE TABLE photo_roots (
                        id INTEGER PRIMARY KEY,
                        path TEXT NOT NULL UNIQUE,
                        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                        scanned_at TEXT
                    );
                    CREATE TABLE photos (
                        id TEXT PRIMARY KEY,
                        root_id INTEGER NOT NULL REFERENCES photo_roots(id) ON DELETE CASCADE,
                        relative_path TEXT NOT NULL,
                        file_size INTEGER NOT NULL,
                        file_mtime_ns INTEGER NOT NULL,
                        captured_at TEXT,
                        timezone_explicit INTEGER NOT NULL,
                        camera TEXT NOT NULL,
                        latitude REAL,
                        longitude REAL,
                        present INTEGER NOT NULL DEFAULT 1,
                        scan_error TEXT NOT NULL DEFAULT '',
                        UNIQUE(root_id, relative_path)
                    );
                    CREATE TABLE batches (
                        id TEXT PRIMARY KEY,
                        root_id INTEGER NOT NULL REFERENCES photo_roots(id) ON DELETE CASCADE,
                        report_id TEXT NOT NULL,
                        calibration_json TEXT NOT NULL DEFAULT '{}',
                        server_id TEXT,
                        updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                        UNIQUE(root_id, report_id)
                    );
                    CREATE TABLE batch_photos (
                        batch_id TEXT NOT NULL REFERENCES batches(id) ON DELETE CASCADE,
                        photo_id TEXT NOT NULL REFERENCES photos(id) ON DELETE CASCADE,
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

    def _root(self, root):
        root = str(Path(root).resolve())
        self.connection.execute("INSERT OR IGNORE INTO photo_roots(path) VALUES (?)", (root,))
        return self.connection.execute(
            "SELECT id FROM photo_roots WHERE path = ?", (root,)
        ).fetchone()["id"]

    def scan(self, root):
        root = Path(root).resolve()
        photos = []
        failures = []
        with self.lock, self.connection:
            root_id = self._root(root)
            existing = {
                row["relative_path"]: row
                for row in self.connection.execute(
                    "SELECT * FROM photos WHERE root_id = ?", (root_id,)
                )
            }
            self.connection.execute("UPDATE photos SET present = 0 WHERE root_id = ?", (root_id,))
            paths = sorted(item for item in root.rglob("*") if item.is_file())
            for path in paths:
                if path.suffix.lower() not in JPEG_SUFFIXES:
                    continue
                relative = path.relative_to(root).as_posix()
                stat = path.stat()
                previous = existing.get(relative)
                unchanged = previous and (
                    previous["file_size"] == stat.st_size
                    and previous["file_mtime_ns"] == stat.st_mtime_ns
                    and not previous["scan_error"]
                )
                if unchanged:
                    photo_id = previous["id"]
                    captured_at = previous["captured_at"]
                    timezone_explicit = previous["timezone_explicit"]
                    camera = previous["camera"]
                    latitude = previous["latitude"]
                    longitude = previous["longitude"]
                else:
                    photo_id = previous["id"] if previous else str(uuid.uuid4())
                    try:
                        metadata = read_photo_clock(path)
                    except InvalidPhoto as exc:
                        self.connection.execute(
                            """INSERT INTO photos(
                                id, root_id, relative_path, file_size, file_mtime_ns,
                                timezone_explicit, camera, present, scan_error
                            ) VALUES (?, ?, ?, ?, ?, 0, '', 1, ?)
                            ON CONFLICT(root_id, relative_path) DO UPDATE SET
                                file_size=excluded.file_size,
                                file_mtime_ns=excluded.file_mtime_ns,
                                present=1,
                                scan_error=excluded.scan_error
                            """,
                            (
                                photo_id,
                                root_id,
                                relative,
                                stat.st_size,
                                stat.st_mtime_ns,
                                str(exc),
                            ),
                        )
                        failures.append(IndexedFailure(path, str(exc)))
                        continue
                    captured_at = metadata.captured_at.isoformat() if metadata.captured_at else None
                    timezone_explicit = int(metadata.timezone_explicit)
                    camera = metadata.camera
                    latitude = metadata.latitude
                    longitude = metadata.longitude
                self.connection.execute(
                    """INSERT INTO photos(
                        id, root_id, relative_path, file_size, file_mtime_ns,
                        captured_at, timezone_explicit, camera, latitude, longitude,
                        present, scan_error
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, '')
                    ON CONFLICT(root_id, relative_path) DO UPDATE SET
                        file_size=excluded.file_size,
                        file_mtime_ns=excluded.file_mtime_ns,
                        captured_at=excluded.captured_at,
                        timezone_explicit=excluded.timezone_explicit,
                        camera=excluded.camera,
                        latitude=excluded.latitude,
                        longitude=excluded.longitude,
                        present=1,
                        scan_error=''
                    """,
                    (
                        photo_id,
                        root_id,
                        relative,
                        stat.st_size,
                        stat.st_mtime_ns,
                        captured_at,
                        timezone_explicit,
                        camera,
                        latitude,
                        longitude,
                    ),
                )
                photos.append(
                    IndexedPhoto(
                        id=photo_id,
                        path=path,
                        relative_path=relative,
                        file_size=stat.st_size,
                        file_mtime_ns=stat.st_mtime_ns,
                        captured_at=_datetime(captured_at),
                        timezone_explicit=bool(timezone_explicit),
                        camera=camera,
                        latitude=latitude,
                        longitude=longitude,
                    )
                )
            self.connection.execute(
                "UPDATE photo_roots SET scanned_at = CURRENT_TIMESTAMP WHERE id = ?", (root_id,)
            )
        return photos, failures

    def save_results(self, root, report_id, calibration, photos, results):
        root = Path(root).resolve()
        result_by_id = {result["id"]: result for result in results if result["id"]}
        with self.lock, self.connection:
            root_id = self._root(root)
            row = self.connection.execute(
                "SELECT * FROM batches WHERE root_id = ? AND report_id = ?",
                (root_id, report_id),
            ).fetchone()
            batch_id = row["id"] if row else str(uuid.uuid4())
            self.connection.execute(
                """INSERT INTO batches(id, root_id, report_id, calibration_json)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(root_id, report_id) DO UPDATE SET
                    calibration_json=excluded.calibration_json,
                    updated_at=CURRENT_TIMESTAMP
                """,
                (batch_id, root_id, report_id, json.dumps(calibration, ensure_ascii=False)),
            )
            for photo in photos:
                result = result_by_id.get(photo.id, {})
                self.connection.execute(
                    """INSERT INTO batch_photos(
                        batch_id, photo_id, time_offset_seconds, normalized_at, result_json
                    ) VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT(batch_id, photo_id) DO UPDATE SET
                        time_offset_seconds=excluded.time_offset_seconds,
                        normalized_at=excluded.normalized_at,
                        result_json=excluded.result_json
                    """,
                    (
                        batch_id,
                        photo.id,
                        calibration.get(photo.camera, 0),
                        result.get("normalized_at_iso"),
                        json.dumps(result.get("api_result", {}), ensure_ascii=False),
                    ),
                )
        return batch_id

    def save_server_response(self, batch_id, response):
        with self.lock, self.connection:
            self.connection.execute(
                "UPDATE batches SET server_id = ?, updated_at=CURRENT_TIMESTAMP WHERE id = ?",
                (response["batch"]["id"], batch_id),
            )
            for item in response["photos"]:
                self.connection.execute(
                    """UPDATE batch_photos SET server_id = ?, photo_key = ?
                    WHERE batch_id = ? AND photo_id = ?""",
                    (item["id"], item["photo_key"], batch_id, item["client_id"]),
                )
