"""改号结果备份恢复的领域逻辑。

备份来自制签组此前从改号交接页导出的交接单（evidence-{run_id}.json）。
备份里可以没有签署（review 字段缺失或为空），恢复时库里原签署照旧保留。
所有校验失败都抛 RestoreError，由服务层转成 422，且不会改动任何数据。
"""

from src.utils.serial import content_digest


class RestoreError(ValueError):
    """备份无法用于恢复时抛出，消息可直接展示给操作员。"""


def parse_backup(raw):
    if not isinstance(raw, dict):
        raise RestoreError("备份须为交接单 JSON 对象")
    backup_run_id = raw.get("id")
    if type(backup_run_id) is not int or backup_run_id <= 0:
        raise RestoreError("备份缺少有效的批次号 id")
    result = raw.get("result")
    if not isinstance(result, dict) or not isinstance(result.get("rows"), list):
        raise RestoreError("备份缺少改号结果 result 及其明细 rows")
    digest = raw.get("digest")
    if not isinstance(digest, str) or len(digest) != 64:
        raise RestoreError("备份缺少 64 位 SHA-256 校验值 digest")
    return backup_run_id, result, digest


def verify_backup(run_id, stored_digest, raw):
    """核验备份与目标批次、库内留存校验值是否一致。

    1. 批次号必须与待修复批次对得上；
    2. 备份自带的校验值必须确实由备份内结果算出（备份自身未被篡改）；
    3. 重算校验值必须与库里留存的校验值一致（恢复的就是原批次原结果）。
    """
    backup_run_id, result, claimed_digest = parse_backup(raw)
    if backup_run_id != run_id:
        raise RestoreError(f"备份批次号 #{backup_run_id} 与待修复批次 #{run_id} 不一致")
    actual_digest = content_digest(result)
    if actual_digest != claimed_digest:
        raise RestoreError("备份校验值与其改号结果不匹配，备份已被改动")
    if not stored_digest or actual_digest != stored_digest:
        raise RestoreError("备份校验值与库内留存的校验值不一致，不能恢复")
    return result, actual_digest
