"""通义千问 VLM / 文本 LLM 客户端（OpenAI 兼容模式）。"""

from __future__ import annotations

import base64
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from openai import OpenAI

from core.config.settings import get_settings, resolve_llm_api_key


@dataclass
class QwenConfig:
    api_key: str
    base_url: str
    vlm_model: str
    text_model: str
    request_timeout: float = 180.0


def _extract_json(text: str) -> dict[str, Any]:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{[\s\S]*\}", text)
        if match:
            return json.loads(match.group(0))
        raise


class QwenClient:
    def __init__(self, config: QwenConfig | None = None) -> None:
        settings = get_settings()
        api_key = resolve_llm_api_key() or settings.qwen_api_key or settings.openai_api_key
        self.config = config or QwenConfig(
            api_key=api_key,
            base_url=settings.llm_base_url,
            vlm_model=settings.vlm_model_name,
            text_model=settings.text_model_name,
            request_timeout=settings.llm_request_timeout,
        )
        self._client = OpenAI(
            api_key=self.config.api_key,
            base_url=self.config.base_url,
            timeout=self.config.request_timeout,
        )

    @property
    def available(self) -> bool:
        return bool(self.config.api_key and self.config.api_key != "your_dashscope_api_key_here")

    def recognize_flowchart(
        self,
        image_path: Path,
        *,
        context_text: str = "",
        prompt: str = "",
    ) -> dict[str, Any]:
        if not self.available:
            raise RuntimeError("未配置 QWEN_API_KEY / OPENAI_API_KEY，无法进行 VLM 流程图识别")

        image_bytes = image_path.read_bytes()
        ext = image_path.suffix.lower().lstrip(".") or "png"
        mime = "jpeg" if ext in {"jpg", "jpeg"} else ext
        b64 = base64.b64encode(image_bytes).decode("ascii")
        data_url = f"data:image/{mime};base64,{b64}"

        user_prompt = prompt or self._default_flowchart_prompt(context_text)
        response = self._client.chat.completions.create(
            model=self.config.vlm_model,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": user_prompt},
                        {"type": "image_url", "image_url": {"url": data_url}},
                    ],
                }
            ],
            temperature=0.1,
        )
        content = response.choices[0].message.content or ""
        return _extract_json(content)

    def chat_json(self, prompt: str, *, system: str = "") -> dict[str, Any]:
        """文本 LLM，返回 JSON 对象。"""
        if not self.available:
            raise RuntimeError("未配置 QWEN_API_KEY / OPENAI_API_KEY")
        messages: list[dict[str, str]] = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})
        response = self._client.chat.completions.create(
            model=self.config.text_model,
            messages=messages,
            temperature=0.1,
        )
        content = response.choices[0].message.content or ""
        return _extract_json(content)

    @staticmethod
    def _default_flowchart_prompt(context_text: str) -> str:
        ctx = f"\n\n文档上下文：\n{context_text[:800]}" if context_text else ""
        return f"""你是企业流程图解析专家。请识别图片中的 OA 审批流程图，按连线与节点位置输出结构化 JSON。

## 编号规则
- 为每个可见节点分配 seq（整数），按**从上到下、从左到右**排序。
- 通过**连线方向**判定 prev_seq / next_seq：连入当前节点的上游节点序号写入 prev_seq，连出当前节点的下游节点序号写入 next_seq。
- **并列节点**：同一层级、由同一上级节点分出、彼此无先后关系的分支节点，互相写入 parallel_seq（例如同一判断节点下方的左右两个「审计部」）。
- 无并列关系时 parallel_seq 为空数组 []。
- 无上游时 prev_seq 为空数组 []。

## 形状（shape）仅限
圆角矩形 | 长方形 | 菱形 | 波形

## 节点类型（node_type）仅限
开始节点 | 结束节点 | 处理节点 | 判断条件 | 抄送节点

形状与类型对应参考：
- 圆角矩形/长方形 → 处理节点（或开始/结束节点）
- 菱形 → 判断条件
- 波形 → 抄送节点

## 菱形（判断条件）特别注意
- content 必须填写菱形**内部**的文字，**不要**填写连线旁的是/否标签。
- 多层嵌套菱形是串行关系，不要把不同层级的分支混为同一菱形的子节点。
- 同一条件文字（如「审计部」）在每条路径上只对应一个菱形，不要重复创建。

## 连线与节点
- 侧边「知会/抄送」波形节点须识别；分支汇聚到下游节点时 prev_seq/next_seq 须完整。
- 不要遗漏出纳前的部门列表抄送及两个出纳终点。

## 禁止
- **不要**解析连线上的文字（条件标签一律忽略，留待后续处理）。

## 输出格式（纯 JSON，无 markdown）
{{
  "title": "流程标题，无法识别则空字符串",
  "confidence": 0.0到1.0,
  "nodes": [
    {{
      "seq": 1,
      "shape": "长方形",
      "node_type": "处理节点",
      "content": "节点内全部文字",
      "prev_seq": [],
      "next_seq": [2],
      "parallel_seq": []
    }}
  ]
}}{ctx}"""
