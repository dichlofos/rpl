import hashlib
import json
import sqlite3
import threading
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from .metadata import (
    DIFFERENCE_HASH_VERSION,
    JPEG_SUFFIXES,
    PERCEPTUAL_HASH_VERSION,
    SHARPNESS_VERSION,
    InvalidPhoto,
    PhotoMetadata,
    read_photo,
    read_photo_clock,
)

SCHEMA_VERSION = 3
ANALYSIS_VERSION = f"{DIFFERENCE_HASH_VERSION}|{PERCEPTUAL_HASH_VERSION}|{SHARPNESS_VERSION}"


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
                version = 1
            if version == 1:
                self.connection.executescript(
                    """
                    ALTER TABLE batch_photos ADD COLUMN logical_day INTEGER;
                    ALTER TABLE batch_photos ADD COLUMN day_confirmed INTEGER NOT NULL DEFAULT 0;
                    PRAGMA user_version = 2;
                    """
                )
                version = 2
            if version == 2:
                self.connection.executescript(
                    """
                    ALTER TABLE photos ADD COLUMN source_format TEXT;
                    ALTER TABLE photos ADD COLUMN width INTEGER;
                    ALTER TABLE photos ADD COLUMN height INTEGER;
                    ALTER TABLE photos ADD COLUMN content_sha256 TEXT;
                    ALTER TABLE photos ADD COLUMN difference_hash TEXT;
                    ALTER TABLE photos ADD COLUMN perceptual_hash TEXT;
                    ALTER TABLE photos ADD COLUMN sharpness REAL;
                    ALTER TABLE photos ADD COLUMN analysis_version TEXT;
                    ALTER TABLE batch_photos ADD COLUMN filter_status TEXT NOT NULL DEFAULT 'unreviewed';
                    CREATE TABLE similarity_stacks (
                        id TEXT PRIMARY KEY,
                        batch_id TEXT NOT NULL REFERENCES batches(id) ON DELETE CASCADE,
                        kind TEXT NOT NULL,
                        algorithm_version TEXT NOT NULL,
                        decision TEXT NOT NULL DEFAULT 'pending',
                        recommended_photo_id TEXT NOT NULL REFERENCES photos(id),
                        chosen_photo_id TEXT REFERENCES photos(id),
                        UNIQUE(batch_id, id)
                    );
                    CREATE TABLE similarity_members (
                        stack_id TEXT NOT NULL REFERENCES similarity_stacks(id) ON DELETE CASCADE,
                        photo_id TEXT NOT NULL REFERENCES photos(id) ON DELETE CASCADE,
                        PRIMARY KEY(stack_id, photo_id)
                    );
                    PRAGMA user_version = 3;
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
                        """UPDATE photos SET source_format=NULL, width=NULL, height=NULL,
                        content_sha256=NULL, difference_hash=NULL, perceptual_hash=NULL,
                        sharpness=NULL, analysis_version=NULL WHERE id=?""",
                        (photo_id,),
                    )
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

    def day_assignments(self, root, report_id):
        with self.lock:
            rows = self.connection.execute(
                """SELECT batch_photos.photo_id, batch_photos.logical_day,
                          batch_photos.day_confirmed
                FROM batch_photos
                JOIN batches ON batches.id = batch_photos.batch_id
                JOIN photo_roots ON photo_roots.id = batches.root_id
                WHERE photo_roots.path = ? AND batches.report_id = ?""",
                (str(Path(root).resolve()), report_id),
            )
            return {
                row["photo_id"]: (row["logical_day"], bool(row["day_confirmed"])) for row in rows
            }

    def save_day_assignments(self, batch_id, assignments):
        with self.lock, self.connection:
            for photo_id, (logical_day, confirmed) in assignments.items():
                updated = self.connection.execute(
                    """UPDATE batch_photos SET logical_day = ?, day_confirmed = ?
                    WHERE batch_id = ? AND photo_id = ?""",
                    (logical_day, int(confirmed), batch_id, photo_id),
                )
                if updated.rowcount != 1:
                    raise KeyError(f"Фотография не входит в локальную пачку: {photo_id}")

    def analyze(self, photos, progress=None):
        analyzed = []
        failures = []
        total = len(photos)
        for position, photo in enumerate(photos, 1):
            with self.lock:
                row = self.connection.execute(
                    "SELECT * FROM photos WHERE id = ?", (photo.id,)
                ).fetchone()
            if row is None:
                failures.append(IndexedFailure(photo.path, "Фотография отсутствует в индексе."))
                if progress:
                    progress(position, total, photo.relative_path)
                continue
            if row["analysis_version"] == ANALYSIS_VERSION and row["content_sha256"]:
                metadata = PhotoMetadata(
                    path=photo.path,
                    source_format=row["source_format"],
                    content_sha256=row["content_sha256"],
                    width=row["width"],
                    height=row["height"],
                    captured_at=photo.captured_at,
                    timezone_explicit=photo.timezone_explicit,
                    camera=photo.camera,
                    latitude=photo.latitude,
                    longitude=photo.longitude,
                    difference_hash=row["difference_hash"],
                    perceptual_hash=row["perceptual_hash"],
                    sharpness=row["sharpness"],
                )
            else:
                try:
                    stat = photo.path.stat()
                except OSError as exc:
                    failures.append(IndexedFailure(photo.path, f"Не удалось прочитать файл: {exc}"))
                    if progress:
                        progress(position, total, photo.relative_path)
                    continue
                if stat.st_size != photo.file_size or stat.st_mtime_ns != photo.file_mtime_ns:
                    failures.append(
                        IndexedFailure(
                            photo.path, "Файл изменился после сканирования; перезапустите LRPL."
                        )
                    )
                    if progress:
                        progress(position, total, photo.relative_path)
                    continue
                try:
                    metadata = read_photo(photo.path)
                except InvalidPhoto as exc:
                    failures.append(IndexedFailure(photo.path, str(exc)))
                    if progress:
                        progress(position, total, photo.relative_path)
                    continue
                with self.lock, self.connection:
                    self.connection.execute(
                        """UPDATE photos SET source_format=?, width=?, height=?,
                        content_sha256=?, difference_hash=?, perceptual_hash=?, sharpness=?,
                        analysis_version=? WHERE id=?""",
                        (
                            metadata.source_format,
                            metadata.width,
                            metadata.height,
                            metadata.content_sha256,
                            metadata.difference_hash,
                            metadata.perceptual_hash,
                            metadata.sharpness,
                            ANALYSIS_VERSION,
                            photo.id,
                        ),
                    )
            analyzed.append(metadata)
            if progress:
                progress(position, total, photo.relative_path)
        return analyzed, failures

    def save_similarity(self, batch_id, photo_ids, stacks):
        stack_ids = []
        with self.lock, self.connection:
            self.connection.execute(
                "UPDATE batch_photos SET filter_status='unreviewed' WHERE batch_id=?",
                (batch_id,),
            )
            for stack in stacks:
                members = sorted(stack["members"])
                signature = hashlib.sha256(
                    (batch_id + "\0" + ANALYSIS_VERSION + "\0" + "\0".join(members)).encode()
                ).hexdigest()[:32]
                stack_ids.append(signature)
                existing = self.connection.execute(
                    "SELECT decision, chosen_photo_id FROM similarity_stacks WHERE id = ?",
                    (signature,),
                ).fetchone()
                decision = existing["decision"] if existing else "pending"
                chosen = existing["chosen_photo_id"] if existing else None
                self.connection.execute(
                    """INSERT INTO similarity_stacks(
                        id, batch_id, kind, algorithm_version, decision,
                        recommended_photo_id, chosen_photo_id
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(id) DO UPDATE SET kind=excluded.kind,
                        algorithm_version=excluded.algorithm_version,
                        recommended_photo_id=excluded.recommended_photo_id""",
                    (
                        signature,
                        batch_id,
                        stack["kind"],
                        ANALYSIS_VERSION,
                        decision,
                        stack["recommended"],
                        chosen,
                    ),
                )
                self.connection.execute(
                    "DELETE FROM similarity_members WHERE stack_id = ?", (signature,)
                )
                self.connection.executemany(
                    "INSERT INTO similarity_members(stack_id, photo_id) VALUES (?, ?)",
                    [(signature, photo_id) for photo_id in members],
                )
            if stack_ids:
                placeholders = ",".join("?" for _item in stack_ids)
                self.connection.execute(
                    f"DELETE FROM similarity_stacks WHERE batch_id = ? AND id NOT IN ({placeholders})",
                    (batch_id, *stack_ids),
                )
            else:
                self.connection.execute(
                    "DELETE FROM similarity_stacks WHERE batch_id = ?", (batch_id,)
                )
            self.connection.executemany(
                """UPDATE batch_photos SET filter_status='selected'
                WHERE batch_id=? AND photo_id=?""",
                [(batch_id, photo_id) for photo_id in photo_ids],
            )
            for stack_id in stack_ids:
                stack = self.connection.execute(
                    "SELECT decision, chosen_photo_id FROM similarity_stacks WHERE id=?",
                    (stack_id,),
                ).fetchone()
                members = [
                    row["photo_id"]
                    for row in self.connection.execute(
                        "SELECT photo_id FROM similarity_members WHERE stack_id=?", (stack_id,)
                    )
                ]
                if stack["decision"] == "keep_all":
                    continue
                if stack["decision"] == "chosen" and stack["chosen_photo_id"] in members:
                    statuses = [
                        (
                            "selected" if photo_id == stack["chosen_photo_id"] else "rejected",
                            batch_id,
                            photo_id,
                        )
                        for photo_id in members
                    ]
                else:
                    statuses = [("unreviewed", batch_id, photo_id) for photo_id in members]
                self.connection.executemany(
                    """UPDATE batch_photos SET filter_status=?
                    WHERE batch_id=? AND photo_id=?""",
                    statuses,
                )
        return stack_ids

    def similarity_stacks(self, batch_id):
        with self.lock:
            result = []
            stacks = self.connection.execute(
                """SELECT * FROM similarity_stacks WHERE batch_id=?
                ORDER BY decision='pending' DESC, id""",
                (batch_id,),
            ).fetchall()
            for stack in stacks:
                members = self.connection.execute(
                    """SELECT photos.id, photos.relative_path, photos.sharpness,
                              batch_photos.filter_status
                    FROM similarity_members
                    JOIN photos ON photos.id=similarity_members.photo_id
                    JOIN batch_photos ON batch_photos.photo_id=photos.id
                                      AND batch_photos.batch_id=?
                    WHERE similarity_members.stack_id=?
                    ORDER BY photos.sharpness DESC, photos.relative_path""",
                    (batch_id, stack["id"]),
                ).fetchall()
                result.append(
                    {
                        "id": stack["id"],
                        "kind": stack["kind"],
                        "decision": stack["decision"],
                        "recommended": stack["recommended_photo_id"],
                        "chosen": stack["chosen_photo_id"],
                        "members": [dict(item) for item in members],
                    }
                )
            return result

    def decide_similarity(self, batch_id, stack_id, decision, photo_id=None):
        with self.lock, self.connection:
            stack = self.connection.execute(
                "SELECT * FROM similarity_stacks WHERE id=? AND batch_id=?", (stack_id, batch_id)
            ).fetchone()
            if stack is None:
                raise KeyError("Стопка похожих фотографий не найдена.")
            members = [
                row["photo_id"]
                for row in self.connection.execute(
                    "SELECT photo_id FROM similarity_members WHERE stack_id=?", (stack_id,)
                )
            ]
            if decision == "chosen":
                if photo_id not in members:
                    raise KeyError("Выбранная фотография не входит в стопку.")
                statuses = [
                    ("selected" if item == photo_id else "rejected", batch_id, item)
                    for item in members
                ]
            elif decision == "keep_all":
                photo_id = None
                statuses = [("selected", batch_id, item) for item in members]
            else:
                raise ValueError("Неизвестное решение по похожим фотографиям.")
            self.connection.execute(
                "UPDATE similarity_stacks SET decision=?, chosen_photo_id=? WHERE id=?",
                (decision, photo_id, stack_id),
            )
            self.connection.executemany(
                """UPDATE batch_photos SET filter_status=?
                WHERE batch_id=? AND photo_id=?""",
                statuses,
            )

    def filter_statuses(self, batch_id):
        with self.lock:
            return {
                row["photo_id"]: row["filter_status"]
                for row in self.connection.execute(
                    "SELECT photo_id, filter_status FROM batch_photos WHERE batch_id=?",
                    (batch_id,),
                )
            }

    def analysis_values(self, photo_id):
        with self.lock:
            row = self.connection.execute(
                "SELECT content_sha256, sharpness FROM photos WHERE id=?", (photo_id,)
            ).fetchone()
            return dict(row) if row else {}
