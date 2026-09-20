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
from .logging_setup import product_event, setup_logging
from .metadata import scan_photo_clocks

MAX_FORM_SIZE = 1024 * 1024


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
    def __init__(self, photo_root, photos, failures, *, center_url=""):
        self.photo_root = Path(photo_root).resolve()
        self.photos = tuple(photos)
        self.failures = tuple(failures)
        self.center_url = center_url
        self.client = None
        self.reports = []
        self.selected_report_id = None
        self.calibrations = []
        self.results = []
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

    def select_report(self, report_id):
        if report_id not in {report["id"] for report in self.reports}:
            raise ValueError("Выбран неизвестный отчёт.")
        timeline = self.client.report_timelines(report_id)
        intervals = []
        for track in timeline["tracks"]:
            start = parse_iso_datetime(track.get("started_at"))
            finish = parse_iso_datetime(track.get("finished_at"))
            if start and finish:
                intervals.append((start, finish))

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

    def match(self, offset_values, max_gap_seconds):
        offsets = {
            calibration.name: parse_offset(offset_values[str(calibration.index)])
            for calibration in self.calibrations
        }
        items = []
        photo_by_id = {}
        for index, photo in enumerate(self.photos):
            if photo.captured_at is None:
                continue
            identifier = f"p{index}"
            moment = normalized_time(photo.captured_at, offsets[photo.camera])
            items.append({"id": identifier, "captured_at": moment.isoformat()})
            photo_by_id[identifier] = (photo, moment)
        results = self.client.interpolate_positions(
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
                }
            )
        for photo in self.photos:
            if photo.captured_at is None:
                enriched.append(
                    {
                        "id": "",
                        "status": "invalid",
                        "reason": "missing_time",
                        "path": str(photo.path.relative_to(self.photo_root)),
                        "camera": photo.camera,
                        "captured_at": None,
                        "normalized_at": None,
                    }
                )
        order = {"ambiguous": 0, "unmatched": 1, "invalid": 2, "matched": 3}
        enriched.sort(key=lambda item: (order.get(item["status"], 9), item["path"]))
        with self.lock:
            self.results = enriched
        return Counter(item["status"] for item in enriched)


def esc(value):
    return html.escape(str(value), quote=True)


def page(state):
    error = f'<div class="error">{esc(state.error)}</div>' if state.error else ""
    content = connect_form(state) if state.client is None else workspace(state)
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


def workspace(state):
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
    return report_form + calibration + results_table(state)


def results_table(state):
    if not state.results:
        return ""
    counts = Counter(item["status"] for item in state.results)
    summary = "".join(
        f'<span class="{esc(status)}">{esc(status)}: {count}</span>'
        for status, count in sorted(counts.items())
    )
    rows = []
    for item in state.results[:2000]:
        position = item.get("position") or {}
        coordinates = (
            f"{position.get('latitude', 0):.6f}, {position.get('longitude', 0):.6f}"
            if position
            else "—"
        )
        reason = item.get("reason") or position.get("method", "")
        rows.append(
            f'<tr><td class="{esc(item["status"])}">{esc(item["status"])}</td>'
            f"<td><code>{esc(item['path'])}</code></td><td>{esc(item['camera'])}</td>"
            f"<td>{esc(item['normalized_at'] or '—')}</td><td>{esc(reason)}</td>"
            f"<td>{esc(coordinates)}</td></tr>"
        )
    truncated = (
        f'<p class="muted">Показаны первые 2000 из {len(state.results)} результатов.</p>'
        if len(state.results) > 2000
        else ""
    )
    return f"""<section class="card"><h2>3. Результат</h2><p class="summary">{summary}</p>{truncated}
<table><thead><tr><th>Статус</th><th>Файл</th><th>Камера</th><th>UTC</th><th>Причина</th><th>Координаты</th></tr></thead>
<tbody>{"".join(rows)}</tbody></table></section>"""


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
            if not self.valid_host() or urlparse(self.path).path != "/":
                self.send_error(404)
                return
            self.send_html(page(state))

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
                else:
                    self.send_error(404)
                    return
                state.error = ""
            except (AttributeError, CenterError, KeyError, TypeError, ValueError) as exc:
                state.error = str(exc)
                logger.warning("Request %s failed: %s", path, exc)
            self.send_response(303)
            self.send_header("Location", "/")
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
    photos, failures = scan_photo_clocks(photo_root)
    state = AppState(photo_root, photos, failures, center_url=args.center_url)
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
    return 0
