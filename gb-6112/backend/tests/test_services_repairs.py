"""恢复服务层测试：待修复标记、校验值核验、断点续传与重试幂等。"""

import sys
from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(TESTS_DIR))
sys.path.insert(0, str(TESTS_DIR.parent))

import harness  # noqa: E402

harness.install_stubs()

from fastapi import HTTPException  # noqa: E402

from src.constants.job_state import JobState  # noqa: E402
from src.services import repairs  # noqa: E402
from src.utils.serial import content_digest  # noqa: E402
from harness import fresh_db  # noqa: E402

ACTOR = "operator"
RESULT = {
    "summary": "2 组同间冲突，改号 2 张后全卷通过目标唯一约束。",
    "metrics": {"冲突组": 2, "需改号": 2, "通过试写": 7},
    "rows": [
        {"工票 ID": "N001", "工作间": "北间", "原票号": "ÉCLAT-27", "目标票号": "ÉCLAT-27", "动作": "保留"},
        {"工票 ID": "N002", "工作间": "北间", "原票号": "eclat-27", "目标票号": "R-N002-2", "动作": "别名已验"},
    ],
}
DIGEST = content_digest(RESULT)


def add_run(db, run_id, status, result, digest=DIGEST):
    db.data["runs"].append({
        "id": run_id, "case_id": 1, "status": status, "parameter": 6, "actor": ACTOR,
        "created_at": 100, "finished_at": 200, "result": result, "failure": None, "digest": digest,
    })


def backup(run_id, result=RESULT, digest=DIGEST, review=None):
    # 模拟改号交接页导出的交接单；备份可以没有 review（缺签署也能用）。
    data = {"id": run_id, "case_id": 1, "status": "done", "parameter": 6,
            "actor": ACTOR, "created_at": 100, "finished_at": 200,
            "result": result, "failure": None, "digest": digest}
    if review is not None:
        data["review"] = review
    return data


def expect_http(status_code, fn):
    try:
        fn()
    except HTTPException as exc:
        assert exc.status_code == status_code, f"期望 {status_code}，实际 {exc.status_code}"
        return exc
    raise AssertionError(f"应抛出 HTTP {status_code}")


def test_mark_damaged_flow():
    db = fresh_db()
    add_run(db, 1, JobState.DONE, None)
    record = repairs.mark_damaged(db, 1, ACTOR)
    assert record is None  # 待修复期间尚无修复记录
    assert db.data["runs"][0]["status"] == JobState.REPAIRING
    events = [e for e in db.data["audit_events"] if e["entity_id"] == 1]
    assert [e["action"] for e in events] == ["mark_repair"]


def test_mark_requires_done_with_missing_result():
    db = fresh_db()
    add_run(db, 1, JobState.QUEUED, None)
    expect_http(409, lambda: repairs.mark_damaged(db, 1, ACTOR))
    add_run(db, 2, JobState.DONE, RESULT)
    expect_http(409, lambda: repairs.mark_damaged(db, 2, ACTOR))  # 结果仍在
    add_run(db, 3, JobState.DONE, None, digest=None)
    expect_http(409, lambda: repairs.mark_damaged(db, 3, ACTOR))  # 无留存校验值


def test_mark_unknown_run():
    db = fresh_db()
    expect_http(404, lambda: repairs.mark_damaged(db, 99, ACTOR))


def test_mark_is_idempotent():
    db = fresh_db()
    add_run(db, 1, JobState.DONE, None)
    repairs.mark_damaged(db, 1, ACTOR)
    repairs.mark_damaged(db, 1, ACTOR)  # 重复标记不报错、不重复留痕
    assert db.data["runs"][0]["status"] == JobState.REPAIRING
    assert sum(e["action"] == "mark_repair" for e in db.data["audit_events"]) == 1


def test_restore_happy_path_keeps_review_untouched():
    db = fresh_db()
    add_run(db, 1, JobState.DONE, None)
    # 原签署保留在 reviews 表，恢复流程不触碰它。
    db.data["reviews"].append({"run_id": 1, "actor": "reviewer", "note": "核对无误", "created_at": 300})
    repairs.mark_damaged(db, 1, ACTOR)
    record = repairs.restore(db, 1, backup(1), "evidence-1.json", ACTOR)
    run = db.data["runs"][0]
    assert run["status"] == JobState.DONE
    assert run["result"] == RESULT
    assert run["digest"] == DIGEST
    assert record == {"run_id": 1, "actor": ACTOR, "source": "evidence-1.json",
                      "digest": DIGEST, "created_at": record["created_at"]}
    assert db.data["reviews"] == [{"run_id": 1, "actor": "reviewer", "note": "核对无误", "created_at": 300}]
    assert [e["action"] for e in db.data["audit_events"] if e["entity_id"] == 1] == ["mark_repair", "restore"]


def test_restore_accepts_backup_without_review():
    db = fresh_db()
    add_run(db, 1, JobState.DONE, None)
    repairs.mark_damaged(db, 1, ACTOR)
    record = repairs.restore(db, 1, backup(1), "evidence-1.json", ACTOR)
    assert record["digest"] == DIGEST


def test_restore_rejects_batch_mismatch():
    db = fresh_db()
    add_run(db, 1, JobState.DONE, None)
    repairs.mark_damaged(db, 1, ACTOR)
    expect_http(422, lambda: repairs.restore(db, 1, backup(2), "evidence-2.json", ACTOR))
    assert db.data["runs"][0]["status"] == JobState.REPAIRING  # 核验失败不改状态
    assert db.data["run_repairs"] == []


def test_restore_rejects_tampered_result():
    db = fresh_db()
    add_run(db, 1, JobState.DONE, None)
    repairs.mark_damaged(db, 1, ACTOR)
    tampered = dict(RESULT, summary="被篡改的摘要")
    # 备份自带 digest 仍是原值，重算必然对不上。
    expect_http(422, lambda: repairs.restore(db, 1, backup(1, result=tampered), "x.json", ACTOR))
    # 伪造 digest 让备份自洽，但与库内留存校验值不一致。
    expect_http(422, lambda: repairs.restore(
        db, 1, backup(1, result=tampered, digest=content_digest(tampered)), "x.json", ACTOR))
    assert db.data["run_repairs"] == []


def test_restore_requires_repairing_state():
    db = fresh_db()
    add_run(db, 1, JobState.DONE, None)  # 尚未标记
    expect_http(409, lambda: repairs.restore(db, 1, backup(1), "x.json", ACTOR))


def test_restore_retry_is_idempotent_after_success():
    db = fresh_db()
    add_run(db, 1, JobState.DONE, None)
    repairs.mark_damaged(db, 1, ACTOR)
    first = repairs.restore(db, 1, backup(1), "evidence-1.json", ACTOR)
    # 模拟中断后重试：批次已修好，直接返回既有记录，不新增任何痕迹。
    second = repairs.restore(db, 1, backup(1), "evidence-1.json", ACTOR)
    assert first == second
    assert len(db.data["run_repairs"]) == 1
    assert sum(e["action"] == "restore" for e in db.data["audit_events"]) == 1


def test_retry_after_failed_attempt_then_succeed():
    db = fresh_db()
    add_run(db, 1, JobState.DONE, None)
    repairs.mark_damaged(db, 1, ACTOR)
    expect_http(422, lambda: repairs.restore(db, 1, backup(2), "wrong.json", ACTOR))
    assert len(db.data["run_repairs"]) == 0  # 失败尝试不留痕
    record = repairs.restore(db, 1, backup(1), "evidence-1.json", ACTOR)
    assert record is not None and len(db.data["run_repairs"]) == 1
    assert db.data["runs"][0]["result"] == RESULT


def test_other_batches_unaffected():
    db = fresh_db()
    add_run(db, 1, JobState.DONE, None)
    add_run(db, 2, JobState.DONE, RESULT)
    repairs.mark_damaged(db, 1, ACTOR)
    repairs.restore(db, 1, backup(1), "evidence-1.json", ACTOR)
    other = db.data["runs"][1]
    assert other["status"] == JobState.DONE and other["result"] == RESULT
    assert db.data["run_repairs"] == [db.data["run_repairs"][0]]
    assert db.data["run_repairs"][0]["run_id"] == 1


def test_get_record():
    db = fresh_db()
    add_run(db, 1, JobState.DONE, None)
    assert repairs.get_record(db, 1) is None
    repairs.mark_damaged(db, 1, ACTOR)
    assert repairs.get_record(db, 1) is None
    repairs.restore(db, 1, backup(1), "evidence-1.json", ACTOR)
    assert repairs.get_record(db, 1)["run_id"] == 1


TESTS = [value for name, value in sorted(globals().items()) if name.startswith("test_")]

if __name__ == "__main__":
    for test in TESTS:
        test()
        print(f"PASS {test.__name__}")
    print(f"\n{len(TESTS)} 个服务层用例全部通过")
