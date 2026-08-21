"""批量表单提取结果。"""

from __future__ import annotations

from pydantic import BaseModel, Field

from schemas.form import RawFormRecord


class BatchExtractResult(BaseModel):
    total: int = 0
    success: int = 0
    failed: int = 0
    skipped: int = 0
    records: list[RawFormRecord] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
