# hospital-backend-stats

병원 예약 시스템의 현황 서비스 (통계, 보고서, 시스템 상태). DB 는 읽기 전용 계정으로 접속한다. FastAPI 서비스이며 `shared/` 는 세 백엔드 저장소(auth · reservation · stats)에 같은 내용으로 들어 있다.

## 구조

```
app/            서비스 코드 (uvicorn app.main:app)
shared/         공통 코드 (DB, 인증, 로그, 설정 등)
tests/          pytest (임시 SQLite DB 로 돈다)
Dockerfile      이 폴더 하나로 빌드한다
```

## 테스트

```powershell
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements-dev.txt
.venv\Scripts\python -m pytest tests
```

## 이미지 빌드 · 실행

```powershell
docker build --build-arg APP_VERSION=$(git rev-parse --short HEAD) -t hospital-backend-stats:dev .
docker run --rm -p 8002:8000 -e DB_URL=... -e DB_USERNAME=... -e DB_PASSWORD=... -e JWT_SECRET=... hospital-backend-stats:dev
```

DB 와 다른 서비스를 함께 띄우는 로컬 개발 환경은 `hospital-gitops` 의 `docker-compose.yml` 을 쓴다.

## 환경변수

값(비밀번호·키)은 코드나 이미지에 넣지 않고 실행할 때 주입한다.

| 이름 | 필수 | 설명 |
|---|---|---|
| `DB_URL` | 필수 | 계정 없는 DB 주소 (예: `mysql://db-service:3306/hospital`) |
| `DB_USERNAME` | 필수 | DB 계정 이름 (이 서비스는 `app_readonly`) |
| `DB_PASSWORD` | 필수 | DB 비밀번호 (운영은 Kubernetes Secret 으로 주입) |
| `JWT_SECRET` | 필수 | JWT 서명 키 (세 서비스 같은 값, 32바이트 이상) |
| `JWT_ISSUER` | | 토큰 발급자 (기본 `hospital-reservation`) |
| `DB_SSL_CA` | | DB TLS CA 인증서 경로 (없으면 암호화만, 검증 안 함) |
| `DB_POOL_SIZE` · `DB_MAX_OVERFLOW` · `DB_POOL_TIMEOUT` · `DB_POOL_RECYCLE` | | DB 연결 풀 (기본 5 · 5 · 5초 · 1800초) |
| `READ_ONLY_MODE` | | `true` 면 쓰기 요청을 503 으로 막음 |
| `SITE_NAME` | | 사이트 이름 (hospital / krdr / aws) |
| `APP_VERSION` | | 버전 표시 (이미지 빌드 때 `--build-arg APP_VERSION`) |
| `FORWARDED_ALLOW_IPS` | | 믿을 프록시 IP (기본 `127.0.0.1`) |
| `SERVICE_URLS` | | 시스템 상태에서 `/readyz` 를 확인할 서비스 (`auth=http://auth:8000,reservation=http://reservation:8000`) |

## 코드 원본

이 저장소의 코드는 `reservation-app` 에서 `scripts/sync_to_repos.ps1` 로 복사한다.
README.md · .gitignore · .gitattributes · .github/ 는 이 저장소에서 직접 관리한다 (복사 대상 아님).
