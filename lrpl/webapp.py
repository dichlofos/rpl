import argparse
import html
import io
import logging
import secrets
import threading
import webbrowser
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from PIL import Image, ImageOps

from .calibration import suggest_time_offsets
from .center import CenterClient, CenterError
from .index import LocalIndex
from .logging_setup import product_event, setup_logging
from .similarity import group_similar_photos, group_temporal_episodes

MAX_FORM_SIZE = 1024 * 1024
RESULTS_PER_PAGE = 500
SIMILARITY_EPISODE_GAP_SECONDS = 10
SIMILARITY_DIFFERENCE_HASH_DISTANCE = 24
SIMILARITY_PERCEPTUAL_HASH_DISTANCE = 10


def parse_iso_datetime(value):
    if not value:
        return None
    if value.endswith("Z"):
        value = value[:-1] + "+00:00"
    return datetime.fromisoformat(value)


def format_offset(seconds):
    sign = "+" if seconds >= 0 else "-"
    hours, minutes = divmod(abs(int(seconds)) // 60, 60)
    return f"{sign}{hours:02d}:{minutes:02d}"


def parse_offset(value):
    value = value.strip()
    if len(value) != 6 or value[0] not in "+-" or value[3] != ":":
        raise ValueError("Поправка должна иметь вид +HH:MM или -HH:MM.")
    try:
        hours = int(value[1:3])
        minutes = int(value[4:6])
    except ValueError as exc:
        raise ValueError("Поправка должна иметь вид +HH:MM или -HH:MM.") from exc
    if hours > 23 or minutes > 59:
        raise ValueError("Некорректная поправка времени.")
    seconds = (hours * 60 + minutes) * 60
    return seconds if value[0] == "+" else -seconds


def normalized_time(value, offset_seconds):
    if value.tzinfo is None:
        base = value.replace(tzinfo=timezone.utc)
    else:
        base = value.astimezone(timezone.utc)
    return base + timedelta(seconds=offset_seconds)


def valid_origin(origin, port):
    return (
        not origin
        or origin == "null"
        or origin
        in {
            f"http://127.0.0.1:{port}",
            f"http://localhost:{port}",
        }
    )


@dataclass(frozen=True)
class CameraCalibration:
    index: int
    name: str
    photo_count: int
    timed_count: int
    explicit_timezone_count: int
    captured_from: datetime | None
    captured_to: datetime | None
    suggested_offset_seconds: int


class AppState:
    def __init__(
        self, photo_root, photos, failures, *, center_url="", index=None, events_path=None
    ):
        self.photo_root = Path(photo_root).resolve()
        self.photos = tuple(photos)
        self.failures = tuple(failures)
        self.center_url = center_url
        self.index = index
        self.events_path = events_path
        self.client = None
        self.reports = []
        self.selected_report_id = None
        self.calibrations = []
        self.results = []
        self.batch_id = None
        self.offsets = {}
        self.track_days = {}
        self.calendar_days = {}
        self.similarity_stacks = []
        self.similarity_job = {"status": "idle", "current": 0, "total": 0, "message": ""}
        self.error = ""
        self.csrf_token = secrets.token_urlsafe(32)
        self.lock = threading.RLock()

    def connect(self, center_url, token):
        client = CenterClient(center_url, token)
        reports = client.list_reports()
        with self.lock:
            self.center_url = center_url
            self.client = client
            self.reports = reports
            self.selected_report_id = None
            self.calibrations = []
            self.results = []
            self.batch_id = None
            self.offsets = {}
            self.track_days = {}
            self.calendar_days = {}
            self.similarity_stacks = []
            self.similarity_job = {"status": "idle", "current": 0, "total": 0, "message": ""}

    def select_report(self, report_id):
        if report_id not in {report["id"] for report in self.reports}:
            raise ValueError("Выбран неизвестный отчёт.")
        timeline = self.client.report_timelines(report_id)
        intervals = []
        track_dates = {}
        for track in timeline["tracks"]:
            start = parse_iso_datetime(track.get("started_at"))
            finish = parse_iso_datetime(track.get("finished_at"))
            if start and finish:
                intervals.append((start, finish))
                track_dates[track["id"]] = start.date()

        calendar_dates = sorted(set(track_dates.values()))
        if len(calendar_dates) > 99:
            raise ValueError("В выбранных треках найдено больше 99 календарных дней.")
        calendar_days = {value: index + 1 for index, value in enumerate(calendar_dates)}

        grouped = defaultdict(list)
        for photo in self.photos:
            grouped[photo.camera].append(photo)
        calibrations = []
        for index, (camera, photos) in enumerate(sorted(grouped.items())):
            times = [photo.captured_at for photo in photos if photo.captured_at]
            explicit_count = sum(photo.timezone_explicit for photo in photos if photo.captured_at)
            if times and explicit_count == len(times):
                offset = 0
            else:
                suggestions = suggest_time_offsets(times, intervals)
                offset = suggestions[0].offset_seconds if suggestions else 0
            calibrations.append(
                CameraCalibration(
                    index=index,
                    name=camera,
                    photo_count=len(photos),
                    timed_count=len(times),
                    explicit_timezone_count=explicit_count,
                    captured_from=min(times) if times else None,
                    captured_to=max(times) if times else None,
                    suggested_offset_seconds=offset,
                )
            )
        with self.lock:
            self.selected_report_id = report_id
            self.calibrations = calibrations
            self.results = []
            self.batch_id = None
            self.offsets = {}
            self.calendar_days = calendar_days
            self.track_days = {
                track_id: calendar_days[value] for track_id, value in track_dates.items()
            }
            self.similarity_stacks = []
            self.similarity_job = {"status": "idle", "current": 0, "total": 0, "message": ""}

    def match(self, offset_values, max_gap_seconds):
        offsets = {
            calibration.name: parse_offset(offset_values[str(calibration.index)])
            for calibration in self.calibrations
        }
        items = []
        photo_by_id = {}
        local_results = []
        for photo in self.photos:
            moment = (
                normalized_time(photo.captured_at, offsets[photo.camera])
                if photo.captured_at
                else None
            )
            photo_by_id[photo.id] = (photo, moment)
            if photo.latitude is not None and photo.longitude is not None:
                local_results.append(
                    {
                        "id": photo.id,
                        "status": "matched",
                        "position": {
                            "latitude": photo.latitude,
                            "longitude": photo.longitude,
                            "elevation": None,
                            "method": "exif",
                        },
                    }
                )
            elif moment is not None:
                items.append({"id": photo.id, "captured_at": moment.isoformat()})
            else:
                local_results.append(
                    {"id": photo.id, "status": "invalid", "reason": "missing_time"}
                )
        results = local_results + self.client.interpolate_positions(
            self.selected_report_id, items, max_gap_seconds=max_gap_seconds
        )
        enriched = []
        for result in results:
            photo, moment = photo_by_id[result["id"]]
            enriched.append(
                {
                    **result,
                    "path": str(photo.path.relative_to(self.photo_root)),
                    "camera": photo.camera,
                    "captured_at": photo.captured_at,
                    "normalized_at": moment,
                    "normalized_at_iso": moment.isoformat() if moment else None,
                    "api_result": result,
                }
            )
        if self.calendar_days:
            proposed_days = self.calendar_days
        else:
            dates = sorted({moment.date() for _photo, moment in photo_by_id.values() if moment})
            if len(dates) > 99:
                raise ValueError(
                    "После калибровки найдено больше 99 различных дат; исправьте исходные данные."
                )
            proposed_days = {value: index + 1 for index, value in enumerate(dates)}
        stored_days = (
            self.index.day_assignments(self.photo_root, self.selected_report_id)
            if self.index is not None
            else {}
        )
        for item in enriched:
            photo, moment = photo_by_id[item["id"]]
            stored_day, confirmed = stored_days.get(photo.id, (None, False))
            position = item.get("position") or {}
            track_day = self.track_days.get(position.get("track_id"))
            if confirmed:
                logical_day = stored_day
            elif track_day is not None:
                logical_day = track_day
            elif moment:
                logical_day = proposed_days.get(moment.date())
            else:
                logical_day = None
            item["logical_day"] = logical_day
            item["day_confirmed"] = confirmed
        enriched.sort(
            key=lambda item: (
                item["logical_day"] if item["logical_day"] is not None else 100,
                str(item["captured_at"] or ""),
                item["path"],
            )
        )
        with self.lock:
            self.results = enriched
            self.offsets = dict(offsets)
        calibration = dict(offsets)
        if self.index is not None:
            batch_id = self.index.save_results(
                self.photo_root,
                self.selected_report_id,
                calibration,
                self.photos,
                enriched,
            )
            self.index.save_day_assignments(
                batch_id,
                {item["id"]: (item["logical_day"], item["day_confirmed"]) for item in enriched},
            )
            self.batch_id = batch_id
            statuses = self.index.filter_statuses(batch_id)
            for item in enriched:
                item["filter_status"] = statuses.get(item["id"], "unreviewed")
            self.similarity_stacks = self.index.similarity_stacks(batch_id)
            if self.similarity_stacks:
                self.similarity_job = {
                    "status": "complete",
                    "current": len(self.photos),
                    "total": len(self.photos),
                    "message": "Ранее сохранённый анализ загружен.",
                }
            self._sync_results(enriched)
        return Counter(item["status"] for item in enriched)

    def set_logical_day(self, photo_id, logical_day):
        if not 1 <= logical_day <= 99:
            raise ValueError("Логический день должен быть от 1 до 99.")
        result = next((item for item in self.results if item["id"] == photo_id), None)
        if result is None:
            raise ValueError("Фотография не найдена в текущей пачке.")
        result["logical_day"] = logical_day
        result["day_confirmed"] = True
        self.index.save_day_assignments(self.batch_id, {photo_id: (logical_day, True)})
        self._sync_results([result])

    def confirm_days(self):
        assigned = [item for item in self.results if item["logical_day"] is not None]
        for item in assigned:
            item["day_confirmed"] = True
        assignments = {item["id"]: (item["logical_day"], True) for item in assigned}
        self.index.save_day_assignments(self.batch_id, assignments)
        self._sync_results(assigned)
        return len(assigned)

    def _sync_results(self, results, *, batch_id=None, report_id=None, offsets=None):
        batch_id = batch_id or self.batch_id
        report_id = report_id or self.selected_report_id
        offsets = offsets or self.offsets
        photo_by_id = {photo.id: photo for photo in self.photos}
        server_photos = [
            self._server_photo(
                photo_by_id[result["id"]],
                offsets[photo_by_id[result["id"]].camera],
                result,
            )
            for result in results
        ]
        response = self.client.register_photo_batch(
            report_id,
            batch_id,
            self.photo_root.name or "photos",
            offsets,
            server_photos,
        )
        self.index.save_server_response(batch_id, response)
        keys = {item["client_id"]: item["photo_key"] for item in response["photos"]}
        for item in results:
            if item["id"] in keys:
                item["photo_key"] = keys[item["id"]]

    def start_similarity_analysis(self):
        if self.index is None or self.batch_id is None:
            raise ValueError("Сначала выполните калибровку и привязку фотографий.")
        with self.lock:
            if self.similarity_job["status"] == "running":
                return
            self.similarity_job = {
                "status": "running",
                "current": 0,
                "total": len(self.photos),
                "message": "Подготовка анализа…",
            }
        thread = threading.Thread(
            target=self._analyze_similarity,
            args=(
                self.batch_id,
                self.selected_report_id,
                dict(self.offsets),
                self.results,
            ),
            daemon=True,
        )
        thread.start()

    def _analyze_similarity(self, batch_id=None, report_id=None, offsets=None, results=None):
        batch_id = batch_id or self.batch_id
        report_id = report_id or self.selected_report_id
        offsets = offsets or self.offsets
        results = results or self.results
        try:

            def progress(current, total, path):
                with self.lock:
                    if self.batch_id != batch_id:
                        return
                    self.similarity_job.update(
                        current=current,
                        total=total,
                        message=f"Анализируется {path}",
                    )

            metadata, failures = self.index.analyze(self.photos, progress)
            photo_id_by_path = {photo.path: photo.id for photo in self.photos}
            stack_data = []
            exact_groups = defaultdict(list)
            for item in metadata:
                exact_groups[item.content_sha256].append(item)
            exact_paths = set()
            for copies in exact_groups.values():
                if len(copies) < 2:
                    continue
                exact_paths.update(item.path for item in copies)
                sharpest = max(copies, key=lambda item: item.sharpness)
                stack_data.append(
                    {
                        "kind": "exact",
                        "recommended": photo_id_by_path[sharpest.path],
                        "members": [photo_id_by_path[item.path] for item in copies],
                    }
                )
            episodes = group_temporal_episodes(
                [item for item in metadata if item.path not in exact_paths],
                gap_seconds=SIMILARITY_EPISODE_GAP_SECONDS,
            )
            for episode in episodes:
                if len(episode.photos) < 2:
                    continue
                stacks = group_similar_photos(
                    episode.photos,
                    window_seconds=max(episode.duration_seconds, SIMILARITY_EPISODE_GAP_SECONDS),
                    difference_hash_distance=SIMILARITY_DIFFERENCE_HASH_DISTANCE,
                    perceptual_hash_distance=SIMILARITY_PERCEPTUAL_HASH_DISTANCE,
                )
                for stack in stacks:
                    if len(stack.photos) < 2:
                        continue
                    stack_data.append(
                        {
                            "kind": stack.kind,
                            "recommended": photo_id_by_path[stack.sharpest.path],
                            "members": [photo_id_by_path[item.path] for item in stack.photos],
                        }
                    )
            analyzed_ids = [photo_id_by_path[item.path] for item in metadata]
            self.index.save_similarity(batch_id, analyzed_ids, stack_data)
            stacks = self.index.similarity_stacks(batch_id)
            statuses = self.index.filter_statuses(batch_id)
            with self.lock:
                if self.batch_id != batch_id:
                    return
                self.similarity_stacks = stacks
                for item in results:
                    item["filter_status"] = statuses.get(item["id"], "unreviewed")
                self.similarity_job = {
                    "status": "complete",
                    "current": len(metadata),
                    "total": len(self.photos),
                    "message": (f"Найдено стопок: {len(stacks)}; ошибок анализа: {len(failures)}."),
                }
            self._sync_results(
                results,
                batch_id=batch_id,
                report_id=report_id,
                offsets=offsets,
            )
            if self.events_path:
                product_event(
                    self.events_path,
                    "similarity_analysis_completed",
                    photos=len(metadata),
                    stacks=len(stacks),
                    failures=len(failures),
                )
        except Exception as exc:  # Background failures must remain visible in the UI and logs.
            logging.getLogger("lrpl").exception("Similarity analysis failed")
            if self.events_path:
                product_event(self.events_path, "similarity_analysis_failed", error=str(exc))
            with self.lock:
                if self.batch_id != batch_id:
                    return
                self.similarity_job = {
                    "status": "error",
                    "current": self.similarity_job.get("current", 0),
                    "total": len(self.photos),
                    "message": str(exc),
                }

    def decide_similarity(self, stack_id, decision, photo_id=None):
        self.index.decide_similarity(self.batch_id, stack_id, decision, photo_id)
        self.similarity_stacks = self.index.similarity_stacks(self.batch_id)
        statuses = self.index.filter_statuses(self.batch_id)
        affected_ids = {
            member["id"]
            for stack in self.similarity_stacks
            if stack["id"] == stack_id
            for member in stack["members"]
        }
        affected = []
        for item in self.results:
            item["filter_status"] = statuses.get(item["id"], "unreviewed")
            if item["id"] in affected_ids:
                affected.append(item)
        self._sync_results(affected)

    def photo(self, photo_id):
        return next((photo for photo in self.photos if photo.id == photo_id), None)

    def _server_photo(self, photo, offset, result):
        api_result = result["api_result"]
        position = api_result.get("position")
        placement = None
        if position and position.get("method") == "exif":
            placement = {
                "source": "exif",
                "longitude": position["longitude"],
                "latitude": position["latitude"],
                "elevation": position.get("elevation"),
                "details": {},
                "confirmed": False,
            }
        elif position:
            placement = {
                "source": "track",
                "longitude": position["longitude"],
                "latitude": position["latitude"],
                "elevation": position.get("elevation"),
                "track_id": position["track_id"],
                "algorithm_version": 1,
                "details": {
                    key: value
                    for key, value in position.items()
                    if key
                    not in {
                        "longitude",
                        "latitude",
                        "elevation",
                        "track_id",
                        "track_name",
                    }
                },
                "confirmed": False,
            }
        analysis = self.index.analysis_values(photo.id) if self.index is not None else {}
        return {
            "client_id": photo.id,
            "relative_path": photo.relative_path,
            "file_size": photo.file_size,
            "file_mtime_ns": photo.file_mtime_ns,
            "captured_at_raw": photo.captured_at.isoformat() if photo.captured_at else "",
            "timezone_explicit": photo.timezone_explicit,
            "time_offset_seconds": offset,
            "captured_at_normalized": result["normalized_at_iso"],
            "logical_day": result["logical_day"],
            "day_confirmed": result["day_confirmed"],
            "camera": photo.camera,
            "content_sha256": analysis.get("content_sha256") or "",
            "filter_status": result.get("filter_status", "unreviewed"),
            "placement": placement,
        }


def esc(value):
    return html.escape(str(value), quote=True)


def page(state, result_page=1):
    error = f'<div class="error">{esc(state.error)}</div>' if state.error else ""
    content = connect_form(state) if state.client is None else workspace(state, result_page)
    refresh = (
        '<meta http-equiv="refresh" content="3">'
        if state.similarity_job["status"] == "running"
        else ""
    )
    return f"""<!doctype html>
<html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width">
<title>LRPL — привязка фотографий</title>{refresh}
<style>
body{{font:15px system-ui,sans-serif;margin:0;background:#f4f5f7;color:#20242a}}
main{{max-width:1180px;margin:0 auto;padding:28px}}h1,h2{{margin:.2em 0 .7em}}
.card{{background:white;border:1px solid #d9dde3;border-radius:10px;padding:20px;margin:16px 0}}
label{{display:block;margin:10px 0 4px}}input,select,button{{font:inherit;padding:8px}}
input[type=text],input[type=password],select{{box-sizing:border-box;width:100%;max-width:680px}}
button{{margin-top:14px;cursor:pointer}}table{{width:100%;border-collapse:collapse}}
th,td{{padding:7px;border-bottom:1px solid #e2e5e9;text-align:left;vertical-align:top}}
.error{{background:#ffe6e6;color:#8b1111;padding:12px;border-radius:8px}}.muted{{color:#626b76}}
.matched{{color:#176b34}}.ambiguous{{color:#8a5900}}.unmatched,.invalid{{color:#9b1c1c}}
code{{font-size:13px}}.summary span{{display:inline-block;margin-right:18px}}
.stacks{{display:grid;gap:18px}}.stack{{border:1px solid #d9dde3;border-radius:8px;padding:14px}}
.members{{display:flex;gap:12px;overflow-x:auto;padding:8px 0}}.member{{min-width:230px;max-width:230px}}
.member img{{display:block;width:230px;height:170px;object-fit:contain;background:#17191c;border-radius:6px}}
.member form{{margin:0}}.member button{{margin-top:6px}}small{{display:block}}
</style></head><body><main><h1>LRPL</h1>
<p class="muted">Каталог: <code>{esc(state.photo_root)}</code> · JPEG: {len(state.photos)} · ошибок: {len(state.failures)}</p>
{error}{content}</main></body></html>"""


def csrf(state):
    return f'<input type="hidden" name="csrf" value="{esc(state.csrf_token)}">'


def connect_form(state):
    return f"""<section class="card"><h2>Подключение к Центру</h2>
<form method="post" action="/connect">{csrf(state)}
<label>Адрес Центра</label><input type="text" name="center_url" value="{esc(state.center_url)}" required>
<label>Персональный токен</label><input type="password" name="token" autocomplete="off" required>
<button type="submit">Подключиться</button></form>
<p class="muted">Токен живёт только в памяти до завершения LRPL.</p></section>"""


def workspace(state, result_page=1):
    report_options = "".join(
        f'<option value="{esc(item["id"])}"'
        f"{' selected' if item['id'] == state.selected_report_id else ''}>"
        f"{esc(item['name'])} ({item['track_count']} тр.)</option>"
        for item in state.reports
    )
    report_form = f"""<section class="card"><h2>1. Путешествие</h2>
<form method="post" action="/select-report">{csrf(state)}
<select name="report_id" required><option value="">Выберите отчёт</option>{report_options}</select>
<button type="submit">Загрузить треки</button></form></section>"""
    if not state.calibrations:
        return report_form
    rows = "".join(
        f"<tr><td>{esc(item.name)}</td><td>{item.photo_count}</td><td>{item.timed_count}</td>"
        f'<td>{item.explicit_timezone_count}</td><td><input type="text" name="offset_{item.index}" '
        f'value="{format_offset(item.suggested_offset_seconds)}" required></td></tr>'
        for item in state.calibrations
    )
    calibration = f"""<section class="card"><h2>2. Калибровка времени</h2>
<form method="post" action="/match">{csrf(state)}
<table><thead><tr><th>Камера</th><th>Фото</th><th>Со временем</th><th>С timezone</th><th>Поправка</th></tr></thead>
<tbody>{rows}</tbody></table><label>Максимальный разрыв трека, секунд</label>
<input type="text" name="max_gap_seconds" value="900" required>
<button type="submit">Привязать фотографии</button></form></section>"""
    navigation = ""
    if state.results:
        navigation = (
            '<p><a href="#days">Логические дни</a> · <a href="#similarity">Похожие кадры</a></p>'
        )
    return (
        report_form
        + calibration
        + navigation
        + results_table(state, result_page)
        + similarity_panel(state)
    )


def similarity_panel(state):
    if not state.results:
        return ""
    job = state.similarity_job
    if job["status"] == "running":
        total = job["total"] or 1
        percent = round(job["current"] * 100 / total)
        return f"""<section class="card" id="similarity"><h2>4. Похожие кадры</h2>
<p>{esc(job["message"])}</p><progress max="{total}" value="{job["current"]}"></progress>
<span> {percent}% ({job["current"]}/{job["total"]})</span>
<p class="muted">Страница обновляется автоматически. LRPL можно оставить работать в фоне.</p></section>"""
    if job["status"] in {"idle", "error"}:
        message = f'<p class="error">{esc(job["message"])}</p>' if job["status"] == "error" else ""
        return f"""<section class="card" id="similarity"><h2>4. Похожие кадры</h2>{message}
<p>Полный локальный анализ вычислит SHA-256, визуальные отпечатки и относительную резкость. Оригиналы не загружаются.</p>
<form method="post" action="/analyze-similarity">{csrf(state)}
<button type="submit">Найти похожие кадры</button></form></section>"""

    pending = sum(stack["decision"] == "pending" for stack in state.similarity_stacks)
    selected = sum(item.get("filter_status") == "selected" for item in state.results)
    rejected = sum(item.get("filter_status") == "rejected" for item in state.results)
    cards = []
    for stack in state.similarity_stacks:
        members = []
        sharpest = max((item["sharpness"] or 0 for item in stack["members"]), default=0)
        for member in stack["members"]:
            recommended = member["id"] == stack["recommended"]
            sharpness = member["sharpness"] or 0
            relative = round(sharpness * 100 / sharpest) if sharpest else 0
            badge = " · рекомендован" if recommended else ""
            status = member["filter_status"]
            status_label = {
                "unreviewed": "не проверен",
                "selected": "оставлен",
                "rejected": "отклонён",
            }.get(status, status)
            members.append(
                f"""<div class="member"><a href="/preview/{esc(member["id"])}" target="_blank" rel="noreferrer"><img src="/thumbnail/{esc(member["id"])}" alt="{esc(member["relative_path"])}"></a>
<code>{esc(member["relative_path"])}</code><small>резкость: {relative}%{badge} · {esc(status_label)}</small>
<form method="post" action="/choose-similar">{csrf(state)}
<input type="hidden" name="stack_id" value="{esc(stack["id"])}">
<input type="hidden" name="photo_id" value="{esc(member["id"])}">
<button type="submit">Оставить этот кадр</button></form></div>"""
            )
        decision = {
            "pending": "требует решения",
            "chosen": "представитель выбран",
            "keep_all": "оставлены все",
        }.get(stack["decision"], stack["decision"])
        cards.append(
            f"""<div class="stack"><b>{"точные копии" if stack["kind"] == "exact" else "похожие кадры"}</b>
 · {esc(decision)} · {len(stack["members"])} шт.<div class="members">{"".join(members)}</div>
<form method="post" action="/keep-similar">{csrf(state)}
<input type="hidden" name="stack_id" value="{esc(stack["id"])}">
<button type="submit">Оставить все</button></form></div>"""
        )
    empty = "<p>Похожих групп не найдено: все проанализированные кадры оставлены.</p>"
    return f"""<section class="card" id="similarity"><h2>4. Похожие кадры</h2>
<p>{esc(job["message"])} Требуют решения: {pending}; оставлено: {selected}; отклонено: {rejected}.</p>
<form method="post" action="/analyze-similarity">{csrf(state)}
<button type="submit">Пересчитать анализ</button></form>
<div class="stacks">{"".join(cards) if cards else empty}</div></section>"""


def results_table(state, result_page=1):
    if not state.results:
        return ""
    counts = Counter(item["status"] for item in state.results)
    summary = "".join(
        f'<span class="{esc(status)}">{esc(status)}: {count}</span>'
        for status, count in sorted(counts.items())
    )
    day_items = defaultdict(list)
    for item in state.results:
        if item["logical_day"] is not None:
            day_items[item["logical_day"]].append(item)
    day_summaries = []
    for day, items in sorted(day_items.items()):
        source_dates = sorted({item["captured_at"].date() for item in items if item["captured_at"]})
        dates = ", ".join(value.strftime("%d.%m") for value in source_dates) or "без даты"
        day_summaries.append(f"день {day:02d} ({dates}): {len(items)}")
    days = " · ".join(day_summaries)
    unassigned = sum(item["logical_day"] is None for item in state.results)
    confirmed = sum(item["day_confirmed"] for item in state.results)
    total_pages = max(1, (len(state.results) + RESULTS_PER_PAGE - 1) // RESULTS_PER_PAGE)
    result_page = min(max(result_page, 1), total_pages)
    start = (result_page - 1) * RESULTS_PER_PAGE
    visible_results = state.results[start : start + RESULTS_PER_PAGE]
    rows = []
    for item in visible_results:
        position = item.get("position") or {}
        if position:
            latitude = position["latitude"]
            longitude = position["longitude"]
            coordinates = f"{latitude:.6f}, {longitude:.6f}"
            coordinate_cell = (
                f'<a href="https://nakarte.me/#m=17/{latitude:.6f}/{longitude:.6f}&amp;l=O" '
                f'target="_blank" rel="noreferrer">{esc(coordinates)}</a>'
            )
        else:
            coordinate_cell = "—"
        photo = state.photo(item["id"])
        file_uri = photo.path.resolve().as_uri() if photo is not None else ""
        file_cell = (
            f'<a href="{esc(file_uri)}" target="_blank" rel="noreferrer">'
            f"<code>{esc(item['path'])}</code></a>"
            if file_uri
            else f"<code>{esc(item['path'])}</code>"
        )
        reason = item.get("reason") or position.get("method", "")
        day_value = item["logical_day"] if item["logical_day"] is not None else ""
        if item["day_confirmed"]:
            day_mark = "подтверждён"
        elif item["logical_day"] is not None:
            day_mark = "предложен"
        else:
            day_mark = "не назначен"
        day_form = f"""<form method="post" action="/set-day">{csrf(state)}
<input type="hidden" name="photo_id" value="{esc(item["id"])}">
<input type="hidden" name="return_page" value="{result_page}">
<input type="number" name="logical_day" min="1" max="99" value="{day_value}" required
 aria-label="Логический день для {esc(item["path"])}">
<button type="submit">✓</button><small class="muted">{day_mark}</small></form>"""
        rows.append(
            f'<tr><td class="{esc(item["status"])}">{esc(item["status"])}</td>'
            f"<td><code>{esc(item.get('photo_key') or '—')}</code></td>"
            f"<td>{day_form}</td>"
            f"<td>{file_cell}</td><td>{esc(item['camera'])}</td>"
            f"<td>{esc(item['captured_at'] or '—')}</td>"
            f"<td>{esc(item['normalized_at'] or '—')}</td><td>{esc(reason)}</td>"
            f"<td>{coordinate_cell}</td></tr>"
        )
    navigation = ""
    if total_pages > 1:
        links = " ".join(
            f'<a href="/?page={number}">{number}</a>'
            if number != result_page
            else f"<b>{number}</b>"
            for number in range(1, total_pages + 1)
        )
        navigation = f'<p class="muted">Страницы: {links}</p>'
    return f"""<section class="card" id="days"><h2>3. Логические дни и результат</h2>
<p class="summary">{summary}</p><p>{esc(days) or "Нет предложенных дней"} · без дня: {unassigned} · подтверждено: {confirmed}</p>
<form method="post" action="/confirm-days">{csrf(state)}<button type="submit">Подтвердить все предложенные дни</button></form>
{navigation}<table><thead><tr><th>Статус</th><th>Ключ</th><th>День</th><th>Файл</th><th>Камера</th><th>EXIF</th><th>UTC</th><th>Причина</th><th>Координаты</th></tr></thead>
<tbody>{"".join(rows)}</tbody></table>{navigation}</section>"""


def handler_class(state, logger, events_path):
    class Handler(BaseHTTPRequestHandler):
        server_version = "LRPL/0.1"

        def secure_headers(self, cache_control="no-store"):
            self.send_header(
                "Content-Security-Policy",
                "default-src 'none'; img-src 'self'; style-src 'unsafe-inline'; "
                "form-action 'self'; base-uri 'none'",
            )
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("X-Frame-Options", "DENY")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("Cache-Control", cache_control)

        def send_html(self, body, status=200):
            content = body.encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(content)))
            self.secure_headers()
            self.end_headers()
            self.wfile.write(content)

        def valid_host(self):
            port = self.server.server_port
            return self.headers.get("Host") in {f"127.0.0.1:{port}", f"localhost:{port}"}

        def do_GET(self):
            parsed = urlparse(self.path)
            if not self.valid_host():
                self.send_error(404)
                return
            image_kind = next(
                (kind for kind in ("thumbnail", "preview") if parsed.path.startswith(f"/{kind}/")),
                None,
            )
            if image_kind:
                photo_id = parsed.path.removeprefix(f"/{image_kind}/")
                photo = state.photo(photo_id)
                if photo is None or "/" in photo_id:
                    self.send_error(404)
                    return
                try:
                    size = (420, 300) if image_kind == "thumbnail" else (1600, 1200)
                    content = thumbnail_bytes(photo.path, size)
                except OSError:
                    self.send_error(404)
                    return
                self.send_response(200)
                self.send_header("Content-Type", "image/jpeg")
                self.send_header("Content-Length", str(len(content)))
                self.secure_headers("private, max-age=3600")
                self.end_headers()
                self.wfile.write(content)
                return
            if parsed.path != "/":
                self.send_error(404)
                return
            try:
                result_page = int(parse_qs(parsed.query).get("page", ["1"])[0])
            except ValueError:
                result_page = 1
            self.send_html(page(state, result_page))

        def do_POST(self):
            if not self.valid_host():
                self.send_error(403)
                return
            port = self.server.server_port
            origin = self.headers.get("Origin")
            if not valid_origin(origin, port):
                logger.warning("Rejected POST origin: %r", origin)
                self.send_error(403)
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                self.send_error(400)
                return
            if not 0 < length <= MAX_FORM_SIZE:
                self.send_error(413)
                return
            if self.headers.get_content_type() != "application/x-www-form-urlencoded":
                self.send_error(415)
                return
            try:
                form = parse_qs(self.rfile.read(length).decode("utf-8"), max_num_fields=1000)
            except (UnicodeDecodeError, ValueError):
                self.send_error(400)
                return
            if form.get("csrf", [""])[0] != state.csrf_token:
                self.send_error(403)
                return
            path = urlparse(self.path).path
            location = "/"
            try:
                if path == "/connect":
                    state.connect(form.get("center_url", [""])[0], form.get("token", [""])[0])
                    product_event(events_path, "center_connected")
                elif path == "/select-report":
                    state.select_report(form.get("report_id", [""])[0])
                    product_event(
                        events_path, "report_selected", report_id=state.selected_report_id
                    )
                elif path == "/match":
                    offsets = {
                        str(item.index): form.get(f"offset_{item.index}", [""])[0]
                        for item in state.calibrations
                    }
                    max_gap = int(form.get("max_gap_seconds", [""])[0])
                    if not 0 < max_gap <= 86400:
                        raise ValueError("Разрыв должен быть от 1 до 86400 секунд.")
                    counts = state.match(offsets, max_gap)
                    product_event(events_path, "photos_matched", **dict(counts))
                elif path == "/set-day":
                    photo_id = form.get("photo_id", [""])[0]
                    logical_day = int(form.get("logical_day", [""])[0])
                    return_page = max(1, int(form.get("return_page", ["1"])[0]))
                    state.set_logical_day(photo_id, logical_day)
                    location = f"/?page={return_page}"
                    product_event(
                        events_path,
                        "photo_day_changed",
                        photo_id=photo_id,
                        logical_day=logical_day,
                    )
                elif path == "/confirm-days":
                    count = state.confirm_days()
                    product_event(events_path, "photo_days_confirmed", photos=count)
                elif path == "/analyze-similarity":
                    state.start_similarity_analysis()
                    product_event(events_path, "similarity_analysis_started")
                elif path == "/choose-similar":
                    stack_id = form.get("stack_id", [""])[0]
                    photo_id = form.get("photo_id", [""])[0]
                    state.decide_similarity(stack_id, "chosen", photo_id)
                    product_event(
                        events_path,
                        "similarity_representative_chosen",
                        stack_id=stack_id,
                        photo_id=photo_id,
                    )
                elif path == "/keep-similar":
                    stack_id = form.get("stack_id", [""])[0]
                    state.decide_similarity(stack_id, "keep_all")
                    product_event(events_path, "similarity_all_kept", stack_id=stack_id)
                else:
                    self.send_error(404)
                    return
                state.error = ""
            except (AttributeError, CenterError, KeyError, TypeError, ValueError) as exc:
                state.error = str(exc)
                logger.warning("Request %s failed: %s", path, exc)
            self.send_response(303)
            self.send_header("Location", location)
            self.secure_headers()
            self.end_headers()

        def log_message(self, message, *args):
            logger.info("HTTP %s - %s", self.address_string(), message % args)

    return Handler


def thumbnail_bytes(path, max_size=(420, 300)):
    with Image.open(path) as source:
        image = ImageOps.exif_transpose(source)
        image.thumbnail(max_size, getattr(Image, "Resampling", Image).LANCZOS)
        if image.mode != "RGB":
            image = image.convert("RGB")
        output = io.BytesIO()
        image.save(output, format="JPEG", quality=78, optimize=True)
        return output.getvalue()


def create_parser():
    parser = argparse.ArgumentParser(description="Локальная панель привязки фотографий LRPL.")
    parser.add_argument("directory", type=Path)
    parser.add_argument("--center-url", default="http://127.0.0.1:8000/")
    parser.add_argument("--port", type=int, default=0, help="Локальный порт; 0 выбирает свободный.")
    parser.add_argument("--no-browser", action="store_true")
    return parser


def main(argv=None):
    args = create_parser().parse_args(argv)
    if not args.directory.is_dir():
        raise SystemExit(f"Каталог не найден: {args.directory}")
    photo_root = args.directory.resolve()
    logger, events_path = setup_logging()
    logger.info("Scanning photo clocks in %s", photo_root)
    index = LocalIndex(events_path.parent / "lrpl.sqlite3")
    photos, failures = index.scan(photo_root)
    state = AppState(
        photo_root,
        photos,
        failures,
        center_url=args.center_url,
        index=index,
        events_path=events_path,
    )
    product_event(events_path, "photo_clocks_scanned", photos=len(photos), failures=len(failures))
    server = ThreadingHTTPServer(
        ("127.0.0.1", args.port), handler_class(state, logger, events_path)
    )
    url = f"http://127.0.0.1:{server.server_port}/"
    print(f"LRPL: {url}")
    print(f"Журналы: {events_path.parent}")
    if not args.no_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        logging.getLogger("lrpl").info("LRPL stopped by user")
    finally:
        server.server_close()
        index.close()
    return 0
