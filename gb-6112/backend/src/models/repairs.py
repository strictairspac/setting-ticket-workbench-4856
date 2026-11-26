from sqlalchemy import BigInteger, Column, ForeignKey, String, Table
from .base import metadata


# 每个批次至多一条修复记录：主键即 run_id，恢复重试不会重复留痕。
run_repairs = Table(
    "run_repairs", metadata,
    Column("run_id", ForeignKey("runs.id"), primary_key=True),
    Column("actor", String(48), nullable=False),
    Column("source", String(120), nullable=False),
    Column("digest", String(64), nullable=False),
    Column("created_at", BigInteger, nullable=False),
)
