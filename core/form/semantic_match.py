"""工作流名称语义匹配（LLM + 字符串预匹配）。"""

from __future__ import annotations

from core.form.name_utils import prematch_doc_name, string_similarity
from core.llm.qwen_client import QwenClient
from schemas.form import WorkflowMatchItem

MATCH_THRESHOLD = 0.75
LOW_CONFIDENCE = 0.6


def _llm_match_batch(
    client: QwenClient,
    excel_items: list[tuple[str, str]],
    doc_names: list[str],
) -> list[WorkflowMatchItem]:
    if not excel_items:
        return []

    excel_lines = "\n".join(f"- [{src}] {name}" for name, src in excel_items)
    doc_lines = "\n".join(f"- {name}" for name in doc_names)
    prompt = f"""你是企业 OA 流程治理专家。请将任务表中的流程名称与办事指南解析出的流程名称进行语义匹配。

## 任务表流程（excel）
{excel_lines}

## 办事指南流程（doc）
{doc_lines}

## 规则
1. 名称可能不完全一致，例如「集团费用报销单」对应「集团费用报销单（已核准）」。
2. 每个 excel 项最多匹配一个 doc 项；允许多个 excel 项匹配同一 doc 项。
3. 无法确定对应关系时 matched_doc_name 留空字符串。
4. confidence 为 0~1；低于 0.6 视为 unmatched。

## 输出 JSON（纯 JSON，无 markdown）
{{
  "matches": [
    {{
      "excel_name": "任务表流程名",
      "excel_source": "C 或 D",
      "matched_doc_name": "匹配到的 doc 名或空字符串",
      "confidence": 0.95,
      "reason": "简要说明"
    }}
  ]
}}"""
    data = client.chat_json(prompt)
    results: list[WorkflowMatchItem] = []
    for item in data.get("matches") or []:
        conf = float(item.get("confidence") or 0.0)
        matched = str(item.get("matched_doc_name") or "").strip()
        status = "matched"
        if not matched or conf < LOW_CONFIDENCE:
            status = "unmatched"
        elif conf < MATCH_THRESHOLD:
            status = "low_confidence"
        results.append(
            WorkflowMatchItem(
                excel_name=str(item.get("excel_name") or ""),
                excel_source=str(item.get("excel_source") or ""),
                matched_doc_name=matched,
                confidence=conf,
                reason=str(item.get("reason") or ""),
                status=status,
            )
        )
    return results


def match_excel_to_doc_workflows(
    excel_items: list[tuple[str, str]],
    doc_names: list[str],
    *,
    client: QwenClient | None = None,
    use_llm: bool = True,
) -> list[WorkflowMatchItem]:
    """将任务表 C/D 列流程名与文档解析流程名做语义匹配。"""
    unique_doc = sorted(set(doc_names))
    results: list[WorkflowMatchItem] = []
    pending: list[tuple[str, str]] = []

    for name, source in excel_items:
        matched, score = prematch_doc_name(name, unique_doc)
        if matched:
            status = "matched" if score >= MATCH_THRESHOLD else "low_confidence"
            results.append(
                WorkflowMatchItem(
                    excel_name=name,
                    excel_source=source,
                    matched_doc_name=matched,
                    confidence=score,
                    reason="字符串规范化预匹配",
                    status=status if matched else "unmatched",
                )
            )
        else:
            pending.append((name, source))

    if pending and use_llm and client and client.available:
        llm_results = _llm_match_batch(client, pending, unique_doc)
        known = {(r.excel_name, r.excel_source) for r in llm_results}
        results.extend(llm_results)
        for name, source in pending:
            if (name, source) not in known:
                results.append(
                    WorkflowMatchItem(
                        excel_name=name,
                        excel_source=source,
                        status="unmatched",
                        reason="LLM 未返回匹配结果",
                    )
                )
    else:
        for name, source in pending:
            best = ""
            best_score = 0.0
            for doc in unique_doc:
                score = string_similarity(name, doc)
                if score > best_score:
                    best_score = score
                    best = doc
            status = "unmatched"
            if best and best_score >= MATCH_THRESHOLD:
                status = "matched"
            elif best and best_score >= LOW_CONFIDENCE:
                status = "low_confidence"
            results.append(
                WorkflowMatchItem(
                    excel_name=name,
                    excel_source=source,
                    matched_doc_name=best if best_score >= LOW_CONFIDENCE else "",
                    confidence=best_score,
                    reason="字符串相似度兜底",
                    status=status,
                )
            )
    return results


def collect_unmatched_doc_names(matches: list[WorkflowMatchItem], doc_names: list[str]) -> list[str]:
    matched = {m.matched_doc_name for m in matches if m.matched_doc_name and m.status == "matched"}
    return sorted(set(doc_names) - matched)


def collect_unmatched_excel_names(matches: list[WorkflowMatchItem]) -> list[str]:
    return sorted({m.excel_name for m in matches if m.status == "unmatched"})
