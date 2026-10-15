"""S2 부서별, S3 의사별, S4 병원 전체 현황 테스트"""
from datetime import datetime

import pytest
from sqlalchemy import insert

from conftest import ROLES

DAY = "2026-10-14"  # KST. UTC 2026-10-13 15:00 ~ 10-14 15:00


@pytest.fixture()
def data(headers):
    """진료과 IM(1)·OS(2)·ADM(3), 의사 1·2(IM)·3(OS), KST 10/14 예약과 범위 밖 예약"""
    from app.tables import departments, doctors, metadata, reservations
    from shared.db import engine

    metadata.create_all(engine)
    rows = [
        # (진료과, 의사, UTC 예약 시각, status, checkin_status)
        (1, 1, datetime(2026, 10, 14, 0, 0), "BOOKED", "CHECKED_IN"),
        (1, 1, datetime(2026, 10, 14, 1, 0), "BOOKED", "CHECKED_IN"),
        (1, 1, datetime(2026, 10, 14, 2, 0), "BOOKED", "NOT_CHECKED_IN"),
        (1, 1, datetime(2026, 10, 14, 3, 0), "CANCELED", "NOT_CHECKED_IN"),
        (1, 2, datetime(2026, 10, 14, 4, 0), "BOOKED", "NOT_CHECKED_IN"),
        (1, 2, datetime(2026, 10, 13, 15, 0), "BOOKED", "NOT_CHECKED_IN"),   # KST 10/14 00:00 (포함)
        (1, None, datetime(2026, 10, 14, 5, 0), "BOOKED", "NOT_CHECKED_IN"),  # 의사 없음: 부서에는 셈
        (2, 3, datetime(2026, 10, 14, 0, 0), "BOOKED", "CHECKED_IN"),
        (2, 3, datetime(2026, 10, 14, 1, 0), "CANCELED", "CHECKED_IN"),       # 접수 후 취소: 취소로만 셈
        (1, 1, datetime(2026, 10, 13, 14, 59), "BOOKED", "CHECKED_IN"),      # KST 10/13 23:59 (범위 밖)
        (1, 1, datetime(2026, 10, 15, 0, 0), "BOOKED", "NOT_CHECKED_IN"),    # KST 10/15 (범위 밖)
    ]
    with engine.begin() as conn:
        conn.execute(insert(departments), [{"id": 1, "code": "IM", "name": "내과"},
                                           {"id": 2, "code": "OS", "name": "정형외과"},
                                           {"id": 3, "code": "ADM", "name": "원무과"}])
        conn.execute(insert(doctors), [{"id": 1, "department_id": 1, "name": "김내과"},
                                       {"id": 2, "department_id": 1, "name": "이내과"},
                                       {"id": 3, "department_id": 2, "name": "최정형"}])
        conn.execute(insert(reservations), [
            {"department_id": d, "doctor_id": doc, "reserved_at": at, "status": st, "checkin_status": ci}
            for d, doc, at, st, ci in rows])
    yield
    metadata.drop_all(engine)


def counts(item):
    return {k: item[k] for k in ("total", "booked", "canceled", "checked_in", "not_checked_in")}


IM = {"total": 7, "booked": 6, "canceled": 1, "checked_in": 2, "not_checked_in": 4}
OS = {"total": 2, "booked": 1, "canceled": 1, "checked_in": 1, "not_checked_in": 0}
ZERO = {"total": 0, "booked": 0, "canceled": 0, "checked_in": 0, "not_checked_in": 0}


# ---------- S2 ----------

def test_department_counts_for_director(client, headers, data):
    r = client.get("/api/v1/stats/departments", params={"date_from": DAY, "date_to": DAY}, headers=headers["DIRECTOR"])
    assert r.status_code == 200
    body = r.json()
    assert body["date_from"] == DAY and body["date_to"] == DAY
    by_code = {i["code"]: counts(i) for i in body["items"]}
    assert by_code == {"IM": IM, "OS": OS, "ADM": ZERO}  # 예약 없는 부서도 0


def test_department_range_spans_days(client, headers, data):
    body = client.get("/api/v1/stats/departments", params={"date_from": "2026-10-13", "date_to": "2026-10-15"},
                      headers=headers["DIRECTOR"]).json()
    assert {i["code"]: i["total"] for i in body["items"]} == {"IM": 9, "OS": 2, "ADM": 0}


def test_dept_head_sees_only_own_department(client, headers, data):
    body = client.get("/api/v1/stats/departments", params={"date_from": DAY}, headers=headers["DEPT_HEAD"]).json()
    assert [(i["code"], counts(i)) for i in body["items"]] == [("IM", IM)]
    r = client.get("/api/v1/stats/departments", params={"date_from": DAY, "department_id": 2},
                   headers=headers["DEPT_HEAD"])
    assert r.status_code == 403
    # DIRECTOR 는 고를 수 있다
    body = client.get("/api/v1/stats/departments", params={"date_from": DAY, "department_id": 2},
                      headers=headers["DIRECTOR"]).json()
    assert [(i["code"], counts(i)) for i in body["items"]] == [("OS", OS)]


# ---------- S3 ----------

def test_treatment_states_count_as_checked_in_but_not_waiting(client, headers, data):
    """진료중·진료완료는 '접수됨'에는 들어가고 '대기(waiting)'에서는 빠진다. 접수 전은 그대로."""
    from app.tables import reservations
    from shared.db import engine

    with engine.begin() as conn:
        conn.execute(insert(reservations), [
            {"department_id": 3, "doctor_id": None, "reserved_at": datetime(2026, 10, 14, 0, 0),
             "status": "BOOKED", "checkin_status": s}
            for s in ("CHECKED_IN", "IN_TREATMENT", "COMPLETED", "NOT_CHECKED_IN")])
    body = client.get("/api/v1/stats/hospital", params={"date": DAY}, headers=headers["DIRECTOR"]).json()
    adm = next(d for d in body["departments"] if d["code"] == "ADM")
    assert counts(adm) == {"total": 4, "booked": 4, "canceled": 0, "checked_in": 3, "not_checked_in": 1}
    assert adm["waiting"] == 1


def test_doctor_counts(client, headers, data):
    body = client.get("/api/v1/stats/doctors", params={"date_from": DAY, "date_to": DAY},
                      headers=headers["DIRECTOR"]).json()
    by_doc = {i["doctor_name"]: counts(i) for i in body["items"]}
    assert by_doc == {
        "김내과": {"total": 4, "booked": 3, "canceled": 1, "checked_in": 2, "not_checked_in": 1},
        "이내과": {"total": 2, "booked": 2, "canceled": 0, "checked_in": 0, "not_checked_in": 2},
        "최정형": OS,
    }
    assert body["department_id"] is None


def test_doctor_counts_scope(client, headers, data):
    body = client.get("/api/v1/stats/doctors", params={"date_from": DAY}, headers=headers["DEPT_HEAD"]).json()
    assert body["department_id"] == 1 and [i["doctor_name"] for i in body["items"]] == ["김내과", "이내과"]
    assert client.get("/api/v1/stats/doctors", params={"department_id": 2},
                      headers=headers["DEPT_HEAD"]).status_code == 403
    body = client.get("/api/v1/stats/doctors", params={"date_from": DAY, "department_id": 2},
                      headers=headers["DIRECTOR"]).json()
    assert [i["doctor_name"] for i in body["items"]] == ["최정형"]


# ---------- S4 ----------

def test_hospital_day(client, headers, data):
    r = client.get("/api/v1/stats/hospital", params={"date": DAY}, headers=headers["DIRECTOR"])
    assert r.status_code == 200
    body = r.json()
    assert body["date"] == DAY
    assert body["totals"] == {"total": 9, "booked": 7, "canceled": 2, "checked_in": 3, "not_checked_in": 4}
    waiting = {d["code"]: d["waiting"] for d in body["departments"]}
    assert waiting == {"IM": 2, "OS": 1, "ADM": 0}  # 부서별 대기 수 = 접수된 예약


def test_defaults_to_today_kst(client, headers, data):
    from shared.timeutil import today_kst

    today = str(today_kst())
    assert client.get("/api/v1/stats/hospital", headers=headers["DIRECTOR"]).json()["date"] == today
    body = client.get("/api/v1/stats/departments", headers=headers["DIRECTOR"]).json()
    assert body["date_from"] == today and body["date_to"] == today


# ---------- 권한·입력 ----------

ALLOWED = {
    "/api/v1/stats/departments": {"DEPT_HEAD", "DIRECTOR"},
    "/api/v1/stats/doctors": {"DEPT_HEAD", "DIRECTOR"},
    "/api/v1/stats/hospital": {"DIRECTOR"},
}


@pytest.mark.parametrize("path", list(ALLOWED))
@pytest.mark.parametrize("role", ROLES)
def test_roles(client, headers, data, path, role):
    assert client.get(path, headers=headers[role]).status_code == (200 if role in ALLOWED[path] else 403)


@pytest.mark.parametrize("path", list(ALLOWED))
def test_no_token_401(client, headers, path):
    assert client.get(path).status_code == 401


def test_input_rules(client, headers, data):
    d = headers["DIRECTOR"]
    url = "/api/v1/stats/departments"
    assert client.get(url, params={"date_from": "2026-10-15", "date_to": "2026-10-14"}, headers=d).status_code == 422
    assert client.get(url, params={"date_from": "2025-01-01", "date_to": "2026-10-14"}, headers=d).status_code == 422
    assert client.get(url, params={"date_from": "2025-10-14", "date_to": "2026-10-14"}, headers=d).status_code == 200
    assert client.get(url, params={"role": "DIRECTOR"}, headers=d).status_code == 422
    assert client.get("/api/v1/stats/hospital", params={"date_from": DAY}, headers=d).status_code == 422


def test_stats_works_in_read_only_mode(client, headers, data, monkeypatch):
    from shared import config

    monkeypatch.setattr(config, "READ_ONLY_MODE", True)
    assert client.get("/api/v1/stats/hospital", params={"date": DAY}, headers=headers["DIRECTOR"]).status_code == 200
