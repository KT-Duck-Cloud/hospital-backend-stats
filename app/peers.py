"""다른 서비스의 /readyz 확인 (S1 시스템 상태용). 서비스끼리 호출하는 유일한 예외 (docs/api-spec.md 1장).

SERVICE_URLS 형식: "이름=기본주소" 를 쉼표로 구분
    SERVICE_URLS=auth=http://auth:8000,reservation=http://reservation:8000
- 기본주소 뒤에 /readyz 를 붙여 GET 한다. 200 이면 ok, 그 밖(응답 코드·연결 실패·시간 초과)은 fail.
- 전체를 동시에 확인하고 1초까지만 기다린다. 늦은 서비스는 fail.
- 주소는 응답·로그에 내보내지 않는다 (내부 접속 정보).
"""
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlsplit

CHECK_TIMEOUT_SECONDS = 1.0
OK, FAIL = "ok", "fail"

_pool = ThreadPoolExecutor(max_workers=8, thread_name_prefix="peer-readyz")


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """리다이렉트는 따라가지 않는다 (정해진 주소만 확인)."""

    def redirect_request(self, *args, **kwargs):
        return None


# 환경 변수의 프록시 설정을 쓰지 않고 바로 붙는다.
_opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect)


def parse_service_urls(raw: str) -> dict[str, str]:
    """'auth=http://auth:8000,reservation=...' → {'auth': 'http://auth:8000', ...}. 형식이 틀리면 ValueError."""
    services: dict[str, str] = {}
    for item in (part.strip() for part in raw.split(",")):
        if not item:
            continue
        name, sep, url = item.partition("=")
        name, url = name.strip(), url.strip().rstrip("/")
        parsed = urlsplit(url)
        if not sep or not name or parsed.scheme not in ("http", "https") or not parsed.netloc:
            # 주소 자체는 오류 문구에 넣지 않는다.
            raise ValueError(f"SERVICE_URLS 형식이 잘못됐습니다 (항목: {name or '?'}). 예: auth=http://auth:8000")
        services[name] = url
    return services


def _check_one(base_url: str) -> str:
    try:
        with _opener.open(f"{base_url}/readyz", timeout=CHECK_TIMEOUT_SECONDS) as resp:
            return OK if resp.status == 200 else FAIL
    except (urllib.error.URLError, OSError, ValueError):
        return FAIL


def check_services(services: dict[str, str]) -> dict[str, str]:
    """모든 서비스를 동시에 확인. 전체 1초 안에 답이 없으면 fail."""
    futures = {name: _pool.submit(_check_one, url) for name, url in services.items()}
    deadline = time.monotonic() + CHECK_TIMEOUT_SECONDS
    results = {}
    for name, future in futures.items():
        try:
            results[name] = future.result(timeout=max(0.0, deadline - time.monotonic()))
        except Exception:  # 시간 초과 포함
            results[name] = FAIL
    return results
