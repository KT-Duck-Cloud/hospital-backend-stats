"""현황 서비스: S1 시스템 상태, S2~S4 현황 (app/reports.py). 조회 전용 DB 계정(app_readonly)을 쓴다. 기준: docs/api-spec.md"""
import asyncio
import os
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, FastAPI
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel

from app import reports
from app.peers import FAIL, OK, check_services, parse_service_urls
from shared import config
from shared.auth import enforce_permissions, require_jwt_secret
from shared.db import use_read_only_account
from shared.errors import install_error_handlers
from shared.middleware import install_read_only_guard, install_request_id
from shared.ops import install_ops, ready_error

SERVICE_NAME = "stats"

# stats 는 조회 전용 DB 계정(app_readonly)을 쓴다. 세션 갱신·감사 로그는 DB 대신 표준 출력으로 남긴다.
use_read_only_account()
require_jwt_secret()  # JWT_SECRET 이 없으면 시작하지 않는다.

# 확인할 다른 서비스. 자기 자신(stats)은 HTTP 로 부르지 않고 직접 확인한다.
PEER_SERVICES = {name: url for name, url in parse_service_urls(os.getenv("SERVICE_URLS", "")).items()
                 if name != SERVICE_NAME}

app = FastAPI(title="stats 서비스", version=config.APP_VERSION)
api = APIRouter(prefix="/api/v1", dependencies=[Depends(enforce_permissions)])

install_ops(app, SERVICE_NAME)
install_error_handlers(app)
install_read_only_guard(app)
install_request_id(app)  # 마지막에 등록한 미들웨어가 가장 바깥이라, 오류 응답에도 request_id 가 실린다.


class SystemStatusOut(BaseModel):
    """주소·계정·비밀번호 같은 내부 접속 정보는 넣지 않는다."""
    site: str
    read_only_mode: bool
    version: str
    services: dict[str, str]   # 서비스 이름 → ok / fail
    db: str                    # ok / fail
    checked_at: str            # UTC, 2026-10-10T03:12:00Z


@api.get("/system/status", response_model=SystemStatusOut)
async def system_status():
    """S1: 세 서비스 /readyz 결과, 사이트, READ_ONLY_MODE, 앱 버전, DB 상태. 다른 서비스가 죽어도 200."""
    # 다른 서비스 확인과 내 DB 확인을 동시에 (각 1초 제한)
    services, own_error = await asyncio.gather(
        run_in_threadpool(check_services, PEER_SERVICES),
        run_in_threadpool(ready_error),
    )
    db = OK if own_error is None else FAIL
    return SystemStatusOut(
        site=config.SITE_NAME,
        read_only_mode=config.READ_ONLY_MODE,
        version=config.APP_VERSION,
        services={**services, SERVICE_NAME: db},  # 현황 서비스 자신 = 자기 /readyz 와 같은 검사
        db=db,
        checked_at=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    )


app.include_router(api)
app.include_router(reports.router)  # S2~S4
