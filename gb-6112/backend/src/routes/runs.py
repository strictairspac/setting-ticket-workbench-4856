from fastapi import APIRouter, Depends, HTTPException, Response

from src.config.resources import engine
from src.constants.job_state import JobState
from src.controllers.access import identity, operator, reviewer
from src.controllers.workbench import mark_run_damaged, new_review, restore_run
from src.schemas.repairs import RepairEntry, RepairRestoreInput
from src.schemas.reviews import ReviewEntry, ReviewInput
from src.schemas.runs import RunEntry
from src.services import repairs, reviews, runs
from src.utils.serial import canonical_json

router = APIRouter(prefix="/runs", tags=["Execution"])


@router.get("", response_model=list[RunEntry], dependencies=[Depends(identity)])
def list_runs():
    with engine.connect() as db:
        return runs.list_entries(db)


@router.get("/{run_id}", response_model=RunEntry, dependencies=[Depends(identity)])
def run_detail(run_id: int):
    with engine.connect() as db:
        return runs.get(db, run_id)


@router.get("/{run_id}/review", response_model=ReviewEntry | None, dependencies=[Depends(identity)])
def review_detail(run_id: int):
    with engine.connect() as db:
        runs.get(db, run_id)
        return reviews.get(db, run_id)


@router.post("/{run_id}/review", response_model=ReviewEntry, status_code=201)
def sign(run_id: int, form: ReviewInput, user=Depends(reviewer)):
    return new_review(run_id, form, user)


@router.get("/{run_id}/repair", response_model=RepairEntry | None, dependencies=[Depends(identity)])
def repair_detail(run_id: int):
    with engine.connect() as db:
        runs.get(db, run_id)
        return repairs.get_record(db, run_id)


@router.post("/{run_id}/repair/mark", response_model=RepairEntry | None)
def mark_for_repair(run_id: int, user=Depends(operator)):
    # 待修复期间没有修复记录；恢复成功后 GET /repair 才能取到记录。
    return mark_run_damaged(run_id, user)


@router.post("/{run_id}/repair/restore", response_model=RepairEntry)
def restore_from_backup(run_id: int, form: RepairRestoreInput, user=Depends(operator)):
    return restore_run(run_id, form, user)


@router.get("/{run_id}/export", dependencies=[Depends(identity)])
def export(run_id: int):
    with engine.connect() as db:
        run = runs.get(db, run_id)
        if run["status"] == JobState.REPAIRING or run["result"] is None:
            raise HTTPException(409, "批次待修复，暂时无法导出交接单")
        if run["status"] != JobState.DONE:
            raise HTTPException(409, "没有可导出的执行结果")
        run["review"] = reviews.get(db, run_id)
        run["repair"] = repairs.get_record(db, run_id)
    return Response(canonical_json(run), media_type="application/json", headers={
        "Content-Disposition": f"attachment; filename=evidence-{run_id}.json",
    })
