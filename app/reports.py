"""S2 부서별, S3 의사별, S4 병원 전체 현황. reservations 를 세어서 만든다 (조회 전용).

세는 기준 (기간 안의 reserved_at, 날짜는 KST)
- total          : 예약 전체 (취소 포함)
- booked         : status=BOOKED
- canceled       : status=CANCELED
- checked_in     : BOOKED 이면서 접수됨 (CHECKED_IN)
- not_checked_in : BOOKED 이면서 아직 접수 안 됨
- waiting (S4)   : 접수된 예약 수 = 대기열 인원 (진료 완료 상태가 아직 없어서 접수 = 대기로 본다)
역할: DEPT_HEAD 는 자기 부서만 (다른 부서를 요청하면 403), DIRECTOR 는 전체.
"""
import datetime as dt
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import and_, case, func, select
from sqlalchemy.orm import Session

from app.tables import departments, doctors, reservations as R
from shared import audit
from shared.auth import CurrentUser, enforce_permissions, get_current_user
from shared.db import get_db
from shared.errors import forbidden
from shared.permissions import DEPT_HEAD, DIRECTOR
from shared.timeutil import kst_day_range_utc, today_kst

router = APIRouter(prefix="/api/v1/stats", dependencies=[Depends(enforce_permissions)])

MAX_RANGE_DAYS = 366
Id = Annotated[int, Field(ge=1, le=2**31 - 1)]


# ---------- 요청 ----------

class RangeFilter(BaseModel):
    """date_from·date_to 는 KST 날짜. 없으면 오늘."""
    model_config = ConfigDict(extra="forbid")

    date_from: dt.date | None = None
    date_to: dt.date | None = None
    department_id: Id | None = None

    @model_validator(mode="after")
    def fill_and_check(self):
        today = today_kst()
        self.date_from = self.date_from or self.date_to or today
        self.date_to = self.date_to or self.date_from
        if self.date_from > self.date_to:
            raise ValueError("date_from 은 date_to 보다 늦을 수 없습니다")
        if (self.date_to - self.date_from).days + 1 > MAX_RANGE_DAYS:
            raise ValueError(f"기간은 {MAX_RANGE_DAYS}일까지입니다")
        return self


class DayFilter(BaseModel):
    model_config = ConfigDict(extra="forbid")

    date: dt.date | None = None


# ---------- 응답 ----------

class Counts(BaseModel):
    total: int
    booked: int
    canceled: int
    checked_in: int
    not_checked_in: int


class DepartmentCounts(Counts):
    department_id: int
    code: str
    name: str


class DoctorCounts(Counts):
    doctor_id: int
    doctor_name: str
    department_id: int


class DepartmentReport(BaseModel):
    date_from: dt.date
    date_to: dt.date
    items: list[DepartmentCounts]


class DoctorReport(BaseModel):
    date_from: dt.date
    date_to: dt.date
    department_id: int | None
    items: list[DoctorCounts]


class HospitalDepartment(DepartmentCounts):
    waiting: int


class HospitalReport(BaseModel):
    date: dt.date
    totals: Counts
    departments: list[HospitalDepartment]


# ---------- 공통 ----------

def _count_columns():
    """기간 안 예약 수를 세는 칸들 (LEFT JOIN 이라 예약이 없으면 0)"""
    booked = R.c.status == "BOOKED"
    return (
        func.count(R.c.id).label("total"),
        func.count(case((booked, R.c.id))).label("booked"),
        func.count(case((R.c.status == "CANCELED", R.c.id))).label("canceled"),
        func.count(case((and_(booked, R.c.checkin_status == "CHECKED_IN"), R.c.id))).label("checked_in"),
        func.count(case((and_(booked, R.c.checkin_status == "NOT_CHECKED_IN"), R.c.id))).label("not_checked_in"),
    )


def _in_range(date_from: dt.date, date_to: dt.date):
    start, _ = kst_day_range_utc(date_from)
    _, end = kst_day_range_utc(date_to)
    return and_(R.c.reserved_at >= start, R.c.reserved_at < end)


def _scope_department(request: Request, user: CurrentUser, requested: int | None) -> int | None:
    """DEPT_HEAD 는 자기 부서로 고정 (다른 부서 요청은 403). DIRECTOR 는 요청한 부서 또는 전체(None)."""
    if user.role == DEPT_HEAD:
        if user.department_id is None or (requested is not None and requested != user.department_id):
            audit.write_audit(request, "ACCESS_DENIED", audit.DENIED, user_id=user.id,
                              target_type="department", target_id=requested)
            raise forbidden()
        return user.department_id
    if user.role == DIRECTOR:
        return requested
    raise forbidden()  # 권한 표에서 이미 막히지만 범위 규칙이 없는 역할은 기본 거부


def _counts(row) -> dict:
    return {k: int(getattr(row, k) or 0) for k in ("total", "booked", "canceled", "checked_in", "not_checked_in")}


# ---------- S2 부서별 ----------

@router.get("/departments", response_model=DepartmentReport)
def department_stats(
    request: Request,
    filters: Annotated[RangeFilter, Query()],
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """부서별 예약·취소·접수 건수. 예약이 없는 부서도 0 으로 나온다."""
    department_id = _scope_department(request, user, filters.department_id)
    stmt = (
        select(departments.c.id, departments.c.code, departments.c.name, *_count_columns())
        .select_from(departments.outerjoin(
            R, and_(R.c.department_id == departments.c.id, _in_range(filters.date_from, filters.date_to))))
        .group_by(departments.c.id, departments.c.code, departments.c.name)
        .order_by(departments.c.id)
    )
    if department_id is not None:
        stmt = stmt.where(departments.c.id == department_id)
    items = [DepartmentCounts(department_id=r.id, code=r.code, name=r.name, **_counts(r)) for r in db.execute(stmt)]
    return DepartmentReport(date_from=filters.date_from, date_to=filters.date_to, items=items)


# ---------- S3 의사별 ----------

@router.get("/doctors", response_model=DoctorReport)
def doctor_stats(
    request: Request,
    filters: Annotated[RangeFilter, Query()],
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """의사별 실적 (같은 기준으로 센다). DEPT_HEAD 는 자기 부서 의사만."""
    department_id = _scope_department(request, user, filters.department_id)
    stmt = (
        select(doctors.c.id, doctors.c.name, doctors.c.department_id, *_count_columns())
        .select_from(doctors.outerjoin(
            R, and_(R.c.doctor_id == doctors.c.id, _in_range(filters.date_from, filters.date_to))))
        .group_by(doctors.c.id, doctors.c.name, doctors.c.department_id)
        .order_by(doctors.c.department_id, doctors.c.id)
    )
    if department_id is not None:
        stmt = stmt.where(doctors.c.department_id == department_id)
    items = [DoctorCounts(doctor_id=r.id, doctor_name=r.name, department_id=r.department_id, **_counts(r))
             for r in db.execute(stmt)]
    return DoctorReport(date_from=filters.date_from, date_to=filters.date_to, department_id=department_id, items=items)


# ---------- S4 병원 전체 ----------

@router.get("/hospital", response_model=HospitalReport)
def hospital_stats(filters: Annotated[DayFilter, Query()], db: Session = Depends(get_db)):
    """그날(KST, 없으면 오늘) 병원 전체 예약·접수·취소와 부서별 대기 수. DIRECTOR 만."""
    day = filters.date or today_kst()
    stmt = (
        select(departments.c.id, departments.c.code, departments.c.name, *_count_columns())
        .select_from(departments.outerjoin(R, and_(R.c.department_id == departments.c.id, _in_range(day, day))))
        .group_by(departments.c.id, departments.c.code, departments.c.name)
        .order_by(departments.c.id)
    )
    rows = [HospitalDepartment(department_id=r.id, code=r.code, name=r.name, waiting=int(r.checked_in or 0),
                               **_counts(r)) for r in db.execute(stmt)]
    totals = Counts(**{k: sum(getattr(d, k) for d in rows)
                       for k in ("total", "booked", "canceled", "checked_in", "not_checked_in")})
    return HospitalReport(date=day, totals=totals, departments=rows)
