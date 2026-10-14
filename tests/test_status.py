"""S1 GET /api/v1/system/status 테스트"""
import re
import time

import pytest

from conftest import FAKE_URLS, ROLES

URL = "/api/v1/system/status"
ALLOWED = {"DIRECTOR", "DEVELOPER", "TESTER"}


@pytest.mark.parametrize("role", ROLES)
def test_only_director_developer_tester(client, headers, role):
    r = client.get(URL, headers=headers[role])
    assert r.status_code == (200 if role in ALLOWED else 403)


def test_no_token_is_401(client, headers):
    assert client.get(URL).status_code == 401


def test_status_body(client, headers):
    started = time.monotonic()
    r = client.get(URL, headers=headers["DIRECTOR"])
    elapsed = time.monotonic() - started
    assert r.status_code == 200  # 다른 서비스가 죽거나 늦어도 이 API 는 200
    body = r.json()
    assert body["site"] == "hospital" and body["version"] == "a1b2c3d" and body["read_only_mode"] is False
    assert body["services"] == {"auth": "ok", "reservation": "fail", "slow": "fail", "down": "fail", "stats": "ok"}
    assert body["db"] == "ok"
    assert re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ", body["checked_at"])
    assert elapsed < 2.0  # 느린 서비스는 1초에서 끊는다 (3초 기다리지 않음)


def test_db_failure_still_200(client, headers, monkeypatch):
    import app.main as main

    monkeypatch.setattr(main, "ready_error", lambda: "DB_UNAVAILABLE")
    r = client.get(URL, headers=headers["TESTER"])
    assert r.status_code == 200
    assert r.json()["db"] == "fail" and r.json()["services"]["stats"] == "fail"


def test_read_only_mode_is_reported_and_get_still_works(client, headers, monkeypatch):
    from shared import config

    monkeypatch.setattr(config, "READ_ONLY_MODE", True)
    r = client.get(URL, headers=headers["DEVELOPER"])
    assert r.status_code == 200 and r.json()["read_only_mode"] is True


def test_no_connection_info_in_status_or_metrics(client, headers):
    import os

    status = client.get(URL, headers=headers["DIRECTOR"]).text
    client.get("/readyz")
    metrics = client.get("/metrics").text
    secrets_to_hide = [os.environ["DATABASE_URL"], os.environ["JWT_SECRET"], "sqlite", "127.0.0.1"]
    secrets_to_hide += list(FAKE_URLS.values()) + [u.split("//")[1] for u in FAKE_URLS.values()]
    for text in (status, metrics):
        for hidden in secrets_to_hide:
            assert hidden not in text


def test_service_urls_parsing():
    from app.peers import parse_service_urls

    assert parse_service_urls(" auth=http://auth:8000/ , reservation=https://r:443 ,") == {
        "auth": "http://auth:8000", "reservation": "https://r:443"}
    assert parse_service_urls("") == {}
    for bad in ("auth", "auth=ftp://x", "=http://x", "auth=http://"):
        with pytest.raises(ValueError) as exc:
            parse_service_urls(bad)
        assert "ftp://x" not in str(exc.value)


def test_redirects_are_not_followed(monkeypatch):
    """리다이렉트를 따라가 엉뚱한 곳을 ok 로 보지 않는다."""
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    import threading

    from app.peers import check_services

    class Redirect(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(302)
            self.send_header("Location", FAKE_URLS["auth"] + "/readyz")
            self.end_headers()

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Redirect)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        assert check_services({"x": f"http://127.0.0.1:{server.server_address[1]}"}) == {"x": "fail"}
    finally:
        server.shutdown()


def test_health_endpoints_unchanged(client):
    r = client.get("/healthz")
    assert r.status_code == 200 and r.json()["service"] == "stats"
    assert client.get("/readyz").status_code == 200
