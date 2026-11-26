"""改号交接单恢复服务。

受损批次的生命周期：
    done（结果被误删、digest 仍在）
        -- mark_damaged --> repairing（待修复，不可查看明细/导出）
        -- restore --> done（结果补回，修复记录留痕，原签署照旧）

所有写操作都在单事务内完成并对批次行加锁；run_repairs 以 run_id 为主键，
中断后重试要么补回结果并落一条修复记录，要么直接返回既有记录，不会重复留痕。
"""

from time import time

from fastapi import HTTPException
from sqlalchemy import insert, select, update

from src.constants.job_state import JobState
from src.domain.restore import RestoreError, verify_backup
from src.models.repairs import run_repairs
from src.models.runs import runs
from .audit import record


def get_record(db, run_id):
    row = db.execute(select(run_repairs).where(run_repairs.c.run_id == run_id)).mappings().first()
    return dict(row) if row else None


def _locked_run(db, run_id):
    row = db.execute(select(runs).where(runs.c.id == run_id).with_for_update()).mappings().first()
    if row is None:
        raise HTTPException(404, "执行批次不存在")
    return dict(row)


def mark_damaged(db, run_id, actor):
    """把结果丢失的已完成批次标为待修复。重复标记保持幂等。"""
    run = _locked_run(db, run_id)
    if run["status"] == JobState.REPAIRING:
        return get_record(db, run_id)
    if run["status"] != JobState.DONE:
        raise HTTPException(409, "只有已完成批次能标记为待修复")
    if run["result"] is not None:
        raise HTTPException(409, "该批次改号结果仍在，无需修复")
    if not run["digest"]:
        raise HTTPException(409, "库内没有留存校验值，无法据此核验恢复")
    db.execute(update(runs).where(runs.c.id == run_id).values(status=JobState.REPAIRING))
    record(db, "mark_repair", actor, "run", run_id)
    return get_record(db, run_id)


def restore(db, run_id, backup, source, actor):
    """用备份补回改号结果；校验值核验通过后与修复记录在同一事务生效。"""
    run = _locked_run(db, run_id)

    # 已修好的批次再次提交（中断重试/重复点击）：直接返回既有记录，不再写任何痕迹。
    existing = get_record(db, run_id)
    if existing is not None and run["status"] == JobState.DONE and run["result"] is not None:
        return existing

    if run["status"] != JobState.REPAIRING:
        raise HTTPException(409, "批次未处于待修复状态，不能恢复")
    if run["result"] is not None:
        raise HTTPException(409, "该批次改号结果仍在，无需恢复")

    try:
        result, digest = verify_backup(run_id, run["digest"], backup)
    except RestoreError as exc:
        raise HTTPException(422, str(exc)) from exc

    now = int(time())
    db.execute(update(runs).where(runs.c.id == run_id).values(
        status=JobState.DONE, result=result, digest=digest, finished_at=now
    ))
    # 主键冲突意味着同一批次已落过修复记录，属于并发/重试，交由事务回滚保护。
    db.execute(insert(run_repairs).values(
        run_id=run_id, actor=actor, source=source[:120], digest=digest, created_at=now
    ))
    record(db, "restore", actor, "run", run_id)
    return get_record(db, run_id)
