import argparse
import html
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

from .calibration import suggest_time_offsets
from .center import CenterClient, CenterError
from .index import LocalIndex
from .logging_setup import product_event, setup_logging

MAX_FORM_SIZE = 1024 * 1024
RESULTS_PER_PAGE = 500


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
    def __init__(self, photo_root, photos, failures, *, center_url="", index=None):
        self.photo_root = Path(photo_root).resolve()
        self.photos = tuple(photos)
        self.failures = tuple(failures)
        self.center_url = center_url
        self.index = index
        self.client = None
        self.reports = []
        self.selected_report_id = None
        self.calibrations = []
        self.results = []
        self.batch_id = None
        self.offsets = {}
        self.track_days = {}
        self.calendar_days = {}
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

    def _sync_results(self, results):
        photo_by_id = {photo.id: photo for photo in self.photos}
        server_photos = [
            self._server_photo(
                photo_by_id[result["id"]],
                self.offsets[photo_by_id[result["id"]].camera],
                result,
            )
            for result in results
        ]
        response = self.client.register_photo_batch(
            self.selected_report_id,
            self.batch_id,
            self.photo_root.name or "photos",
            self.offsets,
            server_photos,
        )
        self.index.save_server_response(self.batch_id, response)
        keys = {item["client_id"]: item["photo_key"] for item in response["photos"]}
        for item in results:
            if item["id"] in keys:
                item["photo_key"] = keys[item["id"]]

    @staticmethod
    def _server_photo(photo, offset, result):
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
            "placement": placement,
        }


def esc(value):
    return html.escape(str(value), quote=True)


def page(state, result_page=1):
    error = f'<div class="error">{esc(state.error)}</div>' if state.error else ""
    content = connect_form(state) if state.client is None else workspace(state, result_page)
    return f"""<!doctype html>
<html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width">
<title>LRPL — привязка фотографий</title>
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
    return report_form + calibration + results_table(state, result_page)


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
        coordinates = (
            f"{position.get('latitude', 0):.6f}, {position.get('longitude', 0):.6f}"
            if position
            else "—"
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
            f"<td><code>{esc(item['path'])}</code></td><td>{esc(item['camera'])}</td>"
            f"<td>{esc(item['captured_at'] or '—')}</td>"
            f"<td>{esc(item['normalized_at'] or '—')}</td><td>{esc(reason)}</td>"
            f"<td>{esc(coordinates)}</td></tr>"
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
    return f"""<section class="card"><h2>3. Логические дни и результат</h2>
<p class="summary">{summary}</p><p>{esc(days) or "Нет предложенных дней"} · без дня: {unassigned} · подтверждено: {confirmed}</p>
<form method="post" action="/confirm-days">{csrf(state)}<button type="submit">Подтвердить все предложенные дни</button></form>
{navigation}<table><thead><tr><th>Статус</th><th>Ключ</th><th>День</th><th>Файл</th><th>Камера</th><th>EXIF</th><th>UTC</th><th>Причина</th><th>Координаты</th></tr></thead>
<tbody>{"".join(rows)}</tbody></table>{navigation}</section>"""


def handler_class(state, logger, events_path):
    class Handler(BaseHTTPRequestHandler):
        server_version = "LRPL/0.1"

        def secure_headers(self):
            self.send_header(
                "Content-Security-Policy",
                "default-src 'none'; style-src 'unsafe-inline'; form-action 'self'; base-uri 'none'",
            )
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("X-Frame-Options", "DENY")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("Cache-Control", "no-store")

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
            if not self.valid_host() or parsed.path != "/":
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
            if origin and origin not in {f"http://127.0.0.1:{port}", f"http://localhost:{port}"}:
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
    state = AppState(photo_root, photos, failures, center_url=args.center_url, index=index)
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
