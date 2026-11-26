from pydantic import BaseModel, Field


class RepairRestoreInput(BaseModel):
    # 备份为导出的交接单 JSON；缺签署（review）也允许，原签署由库内保留。
    backup: dict
    source: str = Field(default="上传备份", max_length=120)


class RepairEntry(BaseModel):
    run_id: int
    actor: str
    source: str
    digest: str
    created_at: int
