import json
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urljoin, urlparse
from urllib.request import HTTPRedirectHandler, Request, build_opener


class CenterError(RuntimeError):
    pass


class NoRedirects(HTTPRedirectHandler):
    def redirect_request(self, request, file_pointer, code, message, headers, new_url):
        return None


class CenterClient:
    def __init__(self, base_url, token, *, timeout=30, opener=None):
        parsed = urlparse(base_url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("Адрес Центра должен начинаться с http:// или https://.")
        if parsed.scheme == "http" and parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
            raise ValueError("Для удалённого Центра требуется HTTPS.")
        if parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError("Адрес Центра не должен содержать логин, пароль или параметры.")
        if not isinstance(token, str) or not token.startswith("rpl_"):
            raise ValueError("Неверный формат персонального токена.")
        self.base_url = base_url.rstrip("/") + "/"
        self.token = token
        self.timeout = timeout
        self.opener = opener or build_opener(NoRedirects())

    def request(self, path, *, payload=None):
        url = urljoin(self.base_url, path.lstrip("/"))
        data = None
        headers = {
            "Accept": "application/json",
            "Authorization": f"Bearer {self.token}",
            "User-Agent": "LRPL/0.1",
        }
        if payload is not None:
            data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = "application/json"
        request = Request(url, data=data, headers=headers, method="POST" if data else "GET")
        try:
            with self.opener.open(request, timeout=self.timeout) as response:
                body = response.read()
        except HTTPError as exc:
            try:
                message = json.loads(exc.read()).get("error")
            except (AttributeError, json.JSONDecodeError, UnicodeDecodeError):
                message = None
            if exc.code == 401:
                raise CenterError("Центр отклонил токен.") from exc
            if exc.code == 403:
                raise CenterError(message or "Центр отклонил запрос (HTTP 403).") from exc
            raise CenterError(message or f"Центр вернул HTTP {exc.code}.") from exc
        except (TimeoutError, URLError) as exc:
            reason = getattr(exc, "reason", exc)
            raise CenterError(f"Не удалось подключиться к Центру: {reason}") from exc
        try:
            return json.loads(body)
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise CenterError("Центр вернул ответ, отличный от JSON.") from exc

    def list_reports(self):
        return self.request("api/reports/")["reports"]

    def report_timelines(self, report_id):
        report_id = quote(str(report_id), safe="")
        return self.request(f"api/reports/{report_id}/track-timelines/")

    def interpolate_positions(self, report_id, items, *, max_gap_seconds=900):
        report_id = quote(str(report_id), safe="")
        results = []
        for start in range(0, len(items), 10_000):
            response = self.request(
                f"api/reports/{report_id}/interpolate-positions/",
                payload={
                    "max_gap_seconds": max_gap_seconds,
                    "items": items[start : start + 10_000],
                },
            )
            results.extend(response["results"])
        return results

    def register_photo_batch(self, report_id, client_id, name, calibration, photos):
        report_id = quote(str(report_id), safe="")
        combined = {"batch": None, "photos": []}
        chunks = [photos[start : start + 10_000] for start in range(0, len(photos), 10_000)]
        for chunk in chunks or [[]]:
            response = self.request(
                f"api/reports/{report_id}/photo-batches/",
                payload={
                    "client_id": client_id,
                    "name": name,
                    "calibration": calibration,
                    "photos": chunk,
                },
            )
            combined["batch"] = response["batch"]
            combined["photos"].extend(response["photos"])
        return combined
