"""工作流名称规范化与字符串预匹配。"""

from __future__ import annotations

import hashlib
import re
from difflib import SequenceMatcher

STATUS_SUFFIXES = ("已核准", "已核对", "暂停", "暂未使用")
STATUS_SUFFIX_PATTERNS = (
    "（已核对）",
    "（已核准）",
    "(已核对)",
    "(已核准)",
    "（暂停）",
    "(暂停)",
)
PUNCT_RE = re.compile(r"[：:（）()【】\[\]、，,.\s]+")
# 神笔 WF_SIMPLE_TASK_FROM_PROPERTY.fieldName 列长度上限（超长会 99999 且整单回滚）
SHENBI_FIELD_NAME_MAX_LEN = 50


def clamp_platform_field_name(name: str, *, max_len: int = SHENBI_FIELD_NAME_MAX_LEN) -> str:
    """截断平台 fieldName，避免 Data too long for column 'fieldName'。"""
    text = (name or "").strip()
    if not text:
        return ""
    if len(text) <= max_len:
        return text
    return text[: max(1, max_len - 1)] + "…"


def strip_status_suffix(name: str) -> str:
    """去掉办事指南标题中的状态后缀，保留业务全称（含单/表）。"""
    text = (name or "").strip()
    for suffix in STATUS_SUFFIX_PATTERNS:
        text = text.replace(suffix, "")
    for suffix in STATUS_SUFFIXES:
        text = text.replace(f"（{suffix}）", "").replace(f"({suffix})", "")
        if text.endswith(suffix):
            text = text[: -len(suffix)]
    return text.strip() or (name or "").strip()


def display_form_name(name: str) -> str:
    """表单/流程配置全称：去状态后缀，保留末尾「单/表」等业务词。"""
    return strip_status_suffix(name)


def display_app_name(name: str) -> str:
    """应用/菜单/流程显示名：去状态后缀，保留末尾「单/表」等业务词（与表单名一致）。"""
    return strip_status_suffix(name)


def display_workflow_name(name: str) -> str:
    """兼容别名：应用/流程显示名。"""
    return display_app_name(name)


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


VALID_ASCII_PROP_RE = re.compile(r"^[a-zA-Z][a-zA-Z0-9_]*$")


def is_valid_ascii_prop(prop: str) -> bool:
    """神笔字段 prop / 环节 taskKey 须为 ASCII 标识符且以字母开头。"""
    return bool(VALID_ASCII_PROP_RE.match((prop or "").strip()))


def task_key_from_name(name: str, *, index: int = 1, prefix: str = "task") -> str:
    """从中文/混合环节名生成合法 taskKey（避免「（2人）」等残留数字开头 key）。"""
    ascii_part = re.sub(r"[^a-zA-Z0-9]", "", name.encode("ascii", "ignore").decode())
    if ascii_part and ascii_part[0].isalpha():
        return f"{ascii_part[:20].lower()}_{index}"
    digest = hashlib.md5(name.encode("utf-8")).hexdigest()[:8]
    return f"{prefix}_{digest}"
