"""长任务进度耗时格式化与展示。"""

from __future__ import annotations

from datetime import datetime

LONG_TASK_MIN_ITEMS = 10


def format_duration(seconds: float) -> str:
    """将秒数格式化为可读时长。"""
    total = max(0, int(seconds))
    hours, rem = divmod(total, 3600)
    minutes, secs = divmod(rem, 60)
    if hours:
        return f"{hours}小时{minutes}分{secs}秒"
    if minutes:
        return f"{minutes}分{secs}秒"
    return f"{secs}秒"


def _parse_iso(value: str | None) -> datetime | None:
    text = (value or "").strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def elapsed_seconds(*, started_at: str | None, finished_at: str | None = None) -> float | None:
    start = _parse_iso(started_at)
    if not start:
        return None
    end = _parse_iso(finished_at) if finished_at else datetime.now()
    if not end:
        end = datetime.now()
    return max(0.0, (end - start).total_seconds())


def timing_label(
    *,
    started_at: str | None,
    finished_at: str | None = None,
    running: bool = False,
) -> str:
    """返回「已耗时」或「总耗时」文案；无开始时间则返回空字符串。"""
    seconds = elapsed_seconds(started_at=started_at, finished_at=finished_at if not running else None)
    if seconds is None:
        return ""
    if running or not finished_at:
        return f"已耗时 {format_duration(seconds)}"
    return f"总耗时 {format_duration(seconds)}"


def should_show_timing(total: int) -> bool:
    return total > LONG_TASK_MIN_ITEMS
