"""交付工作台运行日志。"""

from __future__ import annotations

import logging
import time
from datetime import datetime
from pathlib import Path

_LOGGER_NAME = "mustang.delivery"
_session_logger: logging.Logger | None = None
_session_log_path: Path | None = None


def setup_delivery_logger(log_dir: Path) -> logging.Logger:
    """创建本次运行的文件日志（UTF-8）。"""
    global _session_logger, _session_log_path

    log_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_path = log_dir / f"delivery_{ts}.log"

    logger = logging.getLogger(_LOGGER_NAME)
    logger.setLevel(logging.INFO)
    logger.handlers.clear()
    logger.propagate = False

    formatter = logging.Formatter(
        fmt="%(asctime)s | %(levelname)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    file_handler = logging.FileHandler(log_path, encoding="utf-8")
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    _session_logger = logger
    _session_log_path = log_path
    logger.info("日志会话开始 | file=%s", log_path)
    return logger


def get_delivery_logger() -> logging.Logger:
    if _session_logger is None:
        raise RuntimeError("delivery logger 尚未初始化")
    return _session_logger


def get_session_log_path() -> Path | None:
    return _session_log_path


class DeliveryRunLogger:
    """记录板块与流程解析耗时。"""

    def __init__(self, logger: logging.Logger | None = None) -> None:
        self._logger = logger or get_delivery_logger()
        self._sector_started: dict[int, float] = {}

    def pipeline_begin(self, *, source: str, vlm_flow_limit: int) -> None:
        self._logger.info(
            "管线开始 | source=%s | vlm_flow_limit=%s",
            source,
            vlm_flow_limit if vlm_flow_limit > 0 else "不限",
        )

    def pipeline_end(self, *, parsed: int, skipped: int, total: int) -> None:
        self._logger.info(
            "管线结束 | parsed=%s | skipped=%s | total=%s",
            parsed,
            skipped,
            total,
        )

    def sector_begin(self, index: int, title: str) -> None:
        self._sector_started[index] = time.perf_counter()
        self._logger.info("板块开始 | index=%s | title=%s", index, title)

    def sector_end(self, index: int, title: str, *, flow_count: int) -> None:
        start = self._sector_started.pop(index, time.perf_counter())
        elapsed = time.perf_counter() - start
        self._logger.info(
            "板块结束 | index=%s | title=%s | flows=%s | duration=%.2fs",
            index,
            title,
            flow_count,
            elapsed,
        )

    def flow_begin(self, *, sector_index: int, sector_title: str, flow_title: str) -> float:
        self._logger.info(
            "流程解析开始 | sector=%s(%s) | flow=%s",
            sector_index,
            sector_title,
            flow_title,
        )
        return time.perf_counter()

    def flow_end(
        self,
        *,
        sector_index: int,
        flow_title: str,
        started_at: float,
        success: bool,
        error: str = "",
    ) -> None:
        elapsed = time.perf_counter() - started_at
        status = "成功" if success else "失败"
        msg = (
            f"流程解析结束 | sector={sector_index} | flow={flow_title} | "
            f"status={status} | duration={elapsed:.2f}s"
        )
        if error:
            msg += f" | error={error}"
        self._logger.info(msg)

    def flow_skipped(self, *, sector_index: int, flow_title: str, reason: str) -> None:
        self._logger.info(
            "流程跳过 | sector=%s | flow=%s | reason=%s",
            sector_index,
            flow_title,
            reason,
        )
