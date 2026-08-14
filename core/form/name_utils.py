"""工作流名称规范化与字符串预匹配。"""

from __future__ import annotations

import re
from difflib import SequenceMatcher

STATUS_SUFFIXES = ("已核准", "已核对", "暂停", "暂未使用")
PUNCT_RE = re.compile(r"[：:（）()【】\[\]、，,.\s]+")


def normalize_workflow_name(name: str) -> str:
    text = (name or "").strip()
    for suffix in STATUS_SUFFIXES:
        text = text.replace(f"（{suffix}）", "").replace(f"({suffix})", "").replace(suffix, "")
    text = PUNCT_RE.sub("", text)
    for tail in ("备案表", "申请表", "审批表", "登记表", "报销单", "支付单", "借款单"):
        if text.endswith(tail):
            text = text[: -len(tail)]
            break
    if text.endswith("表"):
        text = text[:-1]
    if text.endswith("流程"):
        text = text[:-2]
    return text.lower()


def normalize_for_search(name: str) -> str:
    """搜索用：仅去状态标记与标点，保留「备案表」等业务词。"""
    text = (name or "").strip()
    for suffix in STATUS_SUFFIXES:
        text = text.replace(f"（{suffix}）", "").replace(f"({suffix})", "").replace(suffix, "")
    text = PUNCT_RE.sub("", text)
    return text.lower()


def workflow_name_matches_query(query: str, candidate: str) -> bool:
    """判断候选流程名是否匹配搜索词（支持「样车合同备案」匹配「…备案表」）。"""
    q = normalize_for_search(query)
    if not q:
        return True
    c = normalize_for_search(candidate)
    if q in c or c in q:
        return True
    q_strict = normalize_workflow_name(query)
    c_strict = normalize_workflow_name(candidate)
    if q_strict and (q_strict in c_strict or c_strict in q_strict):
        return True
    return False

def string_similarity(a: str, b: str) -> float:
    na, nb = normalize_workflow_name(a), normalize_workflow_name(b)
    if not na or not nb:
        return 0.0
    if na == nb:
        return 1.0
    if na in nb or nb in na:
        return 0.93
    return SequenceMatcher(None, na, nb).ratio()


def prematch_doc_name(excel_name: str, doc_names: list[str], *, threshold: float = 0.82) -> tuple[str, float]:
    best = ""
    best_score = 0.0
    for doc in doc_names:
        score = string_similarity(excel_name, doc)
        if score > best_score:
            best_score = score
            best = doc
    if best_score >= threshold:
        return best, best_score
    return "", best_score
