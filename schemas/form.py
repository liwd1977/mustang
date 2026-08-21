"""表单与任务表数据模型。"""

from __future__ import annotations

from pydantic import BaseModel, Field


class RawFormField(BaseModel):
    label: str
    input_type: str = ""
    tag: str = ""
    field_id: str = ""


class RawFormRecord(BaseModel):
    template_name: str
    template_id: str = ""
    template_url: str = ""
    oa_category: str = ""
    source: str = "oa"
    success: bool = False
    error: str = ""
    start_marker: str = "基本信息"
    end_marker: str = ""
    fields: list[RawFormField] = Field(default_factory=list)
    raw: dict = Field(default_factory=dict)
    extracted_at: str = ""
    task_table_seq: str = ""
    sheet_name: str = ""
    linked_workflows: list[str] = Field(default_factory=list, description="任务表 C/D 列关联流程名")


class TaskTableRow(BaseModel):
    row_index: int
    sheet_name: str = Field(default="", description="Excel 工作表名")
    seq: str = ""
    sector: str = ""
    form_name: str = Field(description="B 列：OA 表单名称")
    backend_flow: str = Field(default="", description="C 列：后端拉取流程")
    field_flow: str = Field(default="", description="D 列：现场拉取流程")
    remark: str = ""
    completion_status: str = Field(default="", description="F 列：完成状态")


class WorkflowMatchItem(BaseModel):
    excel_name: str
    excel_source: str = Field(description="C 或 D 列")
    matched_doc_name: str = ""
    confidence: float = 0.0
    reason: str = ""
    status: str = Field(description="matched | unmatched | low_confidence")


class MissingOaFormItem(BaseModel):
    task_seq: str = ""
    sheet_name: str = ""
    form_name: str
    sector: str = ""
    oa_status: str = Field(description="not_found | fuzzy | exact")
    oa_hint: str = ""
    linked_workflows: list[str] = Field(default_factory=list)


class ReconciliationReport(BaseModel):
    generated_at: str = ""
    doc_workflow_total: int = 0
    excel_workflow_total: int = 0
    workflow_matches: list[WorkflowMatchItem] = Field(default_factory=list)
    unmatched_excel: list[str] = Field(default_factory=list)
    unmatched_doc: list[str] = Field(default_factory=list)
    missing_oa_forms: list[MissingOaFormItem] = Field(default_factory=list)
    oa_template_total: int = 0
