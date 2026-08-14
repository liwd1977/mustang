"""办事指南与工作流解析数据模型。"""

from __future__ import annotations

import json

from pydantic import BaseModel, Field


class WorkflowImage(BaseModel):
    index: int = 0
    filename: str = ""
    path: str = ""
    width: int = 0
    height: int = 0
    title_hint: str = Field(default="", description="流程图前一行文字，用作流程标题提示")
    skipped: bool = Field(default=False, description="标题含暂停/暂未使用时废弃")
    skip_reason: str = ""


class SectorBlock(BaseModel):
    index: int = Field(description="板块序号（2-10）")
    title: str = ""
    text_content: str = ""
    images: list[WorkflowImage] = Field(default_factory=list)


class WorkflowNodeItem(BaseModel):
    seq: int = Field(description="节点序号，从上到下、从左到右")
    shape: str = Field(description="圆角矩形|长方形|菱形|波形")
    node_type: str = Field(description="开始节点|结束节点|处理节点|判断条件|抄送节点")
    content: str = Field(description="节点内文字")
    prev_seq: list[int] = Field(default_factory=list, description="前一个节点序号")
    next_seq: list[int] = Field(default_factory=list, description="下一个节点序号")
    parallel_seq: list[int] = Field(default_factory=list, description="并列节点序号")


class WorkflowFlowResult(BaseModel):
    image: WorkflowImage
    title: str = ""
    success: bool = False
    skipped: bool = False
    error: str = ""
    confidence: float = 0.0
    nodes: list[WorkflowNodeItem] = Field(default_factory=list)

    def to_json_text(self) -> str:
        if self.skipped:
            payload = {
                "success": False,
                "skipped": True,
                "title": self.title,
                "error": self.error or "已废弃",
                "nodes": [],
            }
            return json.dumps(payload, ensure_ascii=False, indent=2)
        if not self.success:
            payload = {
                "success": False,
                "title": self.title,
                "error": self.error or "解析失败",
                "nodes": [],
            }
            return json.dumps(payload, ensure_ascii=False, indent=2)
        payload = {
            "success": True,
            "title": self.title,
            "confidence": self.confidence,
            "nodes": [n.model_dump() for n in self.nodes],
        }
        return json.dumps(payload, ensure_ascii=False, indent=2)


class FlowNode(BaseModel):
    id: str
    node_type: str = Field(description="start/end/process/decision/subprocess/connector/other")
    shape: str = ""
    text: str = ""


class FlowEdge(BaseModel):
    source: str
    target: str
    label: str = Field(default="", description="连线上的条件或说明文字")


class FlowchartRecognition(BaseModel):
    title: str = ""
    nodes: list[FlowNode] = Field(default_factory=list)
    edges: list[FlowEdge] = Field(default_factory=list)
    conditions: list[str] = Field(default_factory=list)
    confidence: float = 0.0
    raw_notes: str = ""
    structured_text: str = ""


class SectorWorkflowResult(BaseModel):
    sector: SectorBlock
    flows: list[WorkflowFlowResult] = Field(default_factory=list)
    error: str = ""


class GuideParseResult(BaseModel):
    source_file: str
    output_dir: str = ""
    sectors: list[SectorBlock] = Field(default_factory=list)
    sector_results: list[SectorWorkflowResult] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
    total_flowcharts: int = 0
    skipped_flowcharts: int = 0
    parsed_flowcharts: int = 0
    parse_started_at: str = Field(default="", description="VLM 识别开始时间")
    parse_finished_at: str = Field(default="", description="VLM 识别结束时间")
    saved_at: str = Field(default="", description="结果写入磁盘时间")
    source_mtime: float = Field(default=0.0, description="源 Word 文档修改时间戳")
    section_from: int = 2
    section_to: int = 10
    vlm_flow_limit: int = 0
