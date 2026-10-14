"""테스트 설정: 임시 SQLite DB, 무작위 서명 키, 그리고 다른 서비스 대신 가짜 HTTP 서버를 띄운다.

가짜 서비스 (SERVICE_URLS 로 연결)
- auth        : /readyz 200        → ok
- reservation : /readyz 503        → fail
- slow        : 3초 뒤 200         → fail (1초 제한)
- down        : 아무도 듣지 않는 포트 → fail
"""
import os
import secrets
import socket
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

# 서비스 폴더 = tests/ 의 부모 (app/ 이 있는 곳). 저장소 루트 = shared/ 가 있는 가장 가까운 폴더.
# reservation-app(services/<이름>/tests) 과 서비스별 저장소(tests/) 두 구조에서 모두 동작한다.
SERVICE_DIR = Path(__file__).resolve().parents[1]
ROOT = next(p for p in [SERVICE_DIR, *SERVICE_DIR.parents] if (p / "shared").is_dir())
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(SERVICE_DIR))


def _serve(status: int, delay: float = 0.0) -> ThreadingHTTPServer:
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            time.sleep(delay)
            try:
                self.send_response(status if self.path == "/readyz" else 404)
                self.end_headers()
                self.wfile.write(b"{}")
            except OSError:  # 확인하는 쪽이 1초에 끊고 나간 경우
                pass

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def _closed_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


FAKE = {"auth": _serve(200), "reservation": _serve(503), "slow": _serve(200, delay=3)}
FAKE_URLS = {name: f"http://127.0.0.1:{srv.server_address[1]}" for name, srv in FAKE.items()}
FAKE_URLS["down"] = f"http://127.0.0.1:{_closed_port()}"

_db = Path(tempfile.mkdtemp()) / "stats-test.db"
os.environ["DB_URL"] = f"sqlite:///{_db}"
for _name in ("DB_USERNAME", "DB_PASSWORD"):  # SQLite 는 계정을 쓰지 않는다 (밖의 값이 섞이지 않게)
    os.environ.pop(_name, None)
os.environ["JWT_SECRET"] = secrets.token_urlsafe(48)
os.environ["SERVICE_URLS"] = ",".join(f"{k}={v}" for k, v in FAKE_URLS.items())
os.environ["SITE_NAME"] = "hospital"
os.environ["APP_VERSION"] = "a1b2c3d"
os.environ.pop("READ_ONLY_MODE", None)

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

ROLES = ["PATIENT", "NURSE", "DOCTOR", "MANAGER", "ADMIN", "DEPT_HEAD", "DIRECTOR", "DEVELOPER", "TESTER"]


@pytest.fixture()
def headers():
    """역할 → Authorization 헤더"""
    from shared.auth import issue_token, session_timeouts
    from shared.db import Base, SessionLocal, engine
    from shared.models import User, UserSession, utcnow

    Base.metadata.create_all(engine)
    result = {}
    with SessionLocal() as db:
        for role in ROLES:
            # 부서 범위로 일하는 역할은 진료과 1(IM) 소속
            dept = 1 if role in ("NURSE", "DOCTOR", "MANAGER", "DEPT_HEAD") else None
            user = User(login_id=role.lower(), password_hash="!", name=role, role=role, department_id=dept)
            db.add(user)
            db.flush()
            now = utcnow()
            idle, absolute = session_timeouts(role)
            session = UserSession(id=secrets.token_urlsafe(32), user_id=user.id, last_seen_at=now,
                                  idle_expires_at=now + idle, absolute_expires_at=now + absolute)
            db.add(session)
            db.flush()
            result[role] = {"Authorization": f"Bearer {issue_token(user, session.id, now, session.absolute_expires_at)}"}
        db.commit()
    yield result
    Base.metadata.drop_all(engine)


@pytest.fixture()
def client():
    from app.main import app

    with TestClient(app) as c:
        yield c
