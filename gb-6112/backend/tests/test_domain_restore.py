"""恢复核验领域逻辑测试：批次号、备份自洽性、库内校验值三道关。"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.domain.restore import RestoreError, parse_backup, verify_backup
from src.utils.serial import content_digest

RESULT = {"summary": "ok", "metrics": {"冲突组": 1}, "rows": [{"工票 ID": "N001"}]}
DIGEST = content_digest(RESULT)


def payload(run_id=1, result=RESULT, digest=DIGEST):
    return {"id": run_id, "status": "done", "result": result, "digest": digest}


def expect_error(fn):
    try:
        fn()
    except RestoreError:
        return
    raise AssertionError("应抛出 RestoreError")


def test_parse_backup_accepts_complete_backup():
    run_id, result, digest = parse_backup(payload())
    assert (run_id, result, digest) == (1, RESULT, DIGEST)


def test_parse_backup_rejects_bad_shapes():
    expect_error(lambda: parse_backup([]))
    expect_error(lambda: parse_backup({"id": "1", "result": RESULT, "digest": DIGEST}))
    expect_error(lambda: parse_backup({"id": 1, "result": {}, "digest": DIGEST}))
    expect_error(lambda: parse_backup({"id": 1, "result": RESULT, "digest": "abc"}))
    expect_error(lambda: parse_backup({"id": 1, "result": RESULT}))


def test_verify_matches_stored_digest():
    result, digest = verify_backup(1, DIGEST, payload())
    assert result == RESULT and digest == DIGEST


def test_verify_rejects_run_id_mismatch():
    expect_error(lambda: verify_backup(2, DIGEST, payload(1)))


def test_verify_rejects_tampered_backup_digest():
    bad = payload(result=dict(RESULT, summary="tampered"))
    expect_error(lambda: verify_backup(1, DIGEST, bad))


def test_verify_rejects_when_stored_digest_differs():
    other = content_digest(dict(RESULT, summary="other"))
    # 备份内部自洽，但与库里留存的校验值不是同一个结果。
    expect_error(lambda: verify_backup(1, other, payload(digest=DIGEST)))


def test_verify_rejects_missing_stored_digest():
    expect_error(lambda: verify_backup(1, None, payload()))


TESTS = [value for name, value in sorted(globals().items()) if name.startswith("test_")]

if __name__ == "__main__":
    for test in TESTS:
        test()
        print(f"PASS {test.__name__}")
    print(f"\n{len(TESTS)} 个领域层用例全部通过")
