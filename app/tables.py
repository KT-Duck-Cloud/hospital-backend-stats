"""현황 서비스가 읽는 표 (조회 전용, 원본: db/schema.sql). 현황 서비스는 이 표에 쓰지 않는다.

예약 서비스 모델을 가져올 수 없어(서비스마다 따로 배포) 필요한 칸만 따로 적는다.
표를 만드는 데는 쓰지 않는다 (테스트용 SQLite 에서만 create_all).
"""
from sqlalchemy import BigInteger, Column, DateTime, Integer, MetaData, String, Table

metadata = MetaData()

departments = Table(
    "departments", metadata,
    Column("id", Integer, primary_key=True),
    Column("code", String(20), nullable=False),
    Column("name", String(50), nullable=False),
)

doctors = Table(
    "doctors", metadata,
    Column("id", Integer, primary_key=True),
    Column("department_id", Integer, nullable=False),
    Column("name", String(50), nullable=False),
)

reservations = Table(
    "reservations", metadata,
    Column("id", BigInteger().with_variant(Integer, "sqlite"), primary_key=True),
    Column("department_id", Integer, nullable=False),
    Column("doctor_id", Integer, nullable=True),
    Column("reserved_at", DateTime, nullable=False),          # UTC
    Column("status", String(10), nullable=False),             # BOOKED / CANCELED
    Column("checkin_status", String(20), nullable=False),     # NOT_CHECKED_IN / CHECKED_IN / IN_TREATMENT / COMPLETED
)
