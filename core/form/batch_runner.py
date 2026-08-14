"""批量提取任务：防休眠、重试、进度持久化、后台子进程。"""

from __future__ import annotations

import json
import logging
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

from core.config.settings import PROJECT_ROOT, get_settings
from core.form.extraction_status import save_extraction_status, summarize_extraction
from core.form.form_store import filter_pending_targets, filter_unextracted_targets, get_forms_dir, save_form
from core.form.task_table import filter_targets_by_sheets, iter_oa_form_names, load_task_table
from core.system.keep_awake import keep_awake
from schemas.form import RawFormRecord
from schemas.form_batch import BatchExtractResult

logger = logging.getLogger(__name__)

PROGRESS_NAME = "batch_progress.json"
PID_NAME = "batch_worker.pid"
LOG_NAME = "batch_form_extract.log"

TRANSIENT_ERROR_TOKENS = (
    "Timeout",
    "ERR_ABORTED",
    "net::",
    "Connection",
    "ECONNRESET",
    "无法打开",
    "无法连接",
    "Navigation",
    "Target closed",
    "Browser closed",
)


def progress_path(settings=None) -> Path:
    return get_forms_dir(settings) / PROGRESS_NAME


def pid_path(settings=None) -> Path:
    return get_forms_dir(settings) / PID_NAME


def log_path(settings=None) -> Path:
    settings = settings or get_settings()
    settings.log_dir.mkdir(parents=True, exist_ok=True)
    return settings.log_dir / LOG_NAME


def is_transient_error(message: str) -> bool:
    return any(token in (message or "") for token in TRANSIENT_ERROR_TOKENS)


def load_batch_progress(settings=None) -> dict:
    path = progress_path(settings)
    if not path.is_file():
        return {"status": "idle"}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {"status": "unknown"}


def write_batch_progress(payload: dict, settings=None) -> None:
    path = progress_path(settings)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def is_batch_running(settings=None) -> bool:
    prog = load_batch_progress(settings)
    if prog.get("status") != "running":
        return False
    pid_file = pid_path(settings)
    if not pid_file.is_file():
        return False
    try:
        pid = int(pid_file.read_text(encoding="utf-8").strip())
    except ValueError:
        return False
    if not _pid_alive(pid):
        mark_worker_interrupted(settings, reason="后台进程已退出（可能因休眠/杀进程导致）")
        return False
    return True


def _pid_alive(pid: int) -> bool:
    if sys.platform == "win32":
        try:
            import ctypes

            handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)
            if not handle:
                return False
            ctypes.windll.kernel32.CloseHandle(handle)
            return True
        except Exception:
            return False
    try:
        import os

        os.kill(pid, 0)
        return True
    except OSError:
        return False


def mark_worker_interrupted(settings=None, *, reason: str) -> None:
    prog = load_batch_progress(settings)
    if prog.get("status") == "running":
        prog["status"] = "interrupted"
        prog["last_error"] = reason
        prog["finished_at"] = datetime.now().isoformat(timespec="seconds")
        write_batch_progress(prog, settings)


def run_batch_job(
    *,
    sheet_names: list[str] | None = None,
    skip_existing: bool = True,
    settings=None,
    max_retries: int = 3,
    retry_targets: list | None = None,
) -> BatchExtractResult:
    """同步/子进程批量提取：防休眠 + 网络重试 + 逐项进度落盘。"""
    settings = settings or get_settings()
    rows = load_task_table(settings.flow_task_table_path)
    if retry_targets is not None:
        pending = retry_targets
        skipped = 0
    else:
        targets = filter_targets_by_sheets(rows, sheet_names)
        if skip_existing:
            pending, skipped = filter_unextracted_targets(targets, settings=settings)
        else:
            pending, skipped = targets, 0

    cumulative = summarize_extraction(filter_targets_by_sheets(rows, sheet_names), settings=settings)

    state = {
        "status": "running",
        "done": 0,
        "total": len(pending),
        "success": 0,
        "failed": 0,
        "skipped": skipped,
        "current": "",
        "started_at": datetime.now().isoformat(timespec="seconds"),
        "sheet_names": sheet_names or [],
        "skip_existing": skip_existing,
        "keep_awake": True,
        "mode": "retry_failed" if retry_targets is not None else "batch",
        "cumulative_success_unique": cumulative["stored_success_unique"],
        "cumulative_failed_unique": cumulative["stored_failed_unique"],
        "cumulative_missing_unique": cumulative["stored_missing_unique"],
        "cumulative_extractable_targets": cumulative["total_targets"],
    }
    write_batch_progress(state, settings)

    batch = BatchExtractResult(total=len(pending), skipped=skipped)
    if not pending:
        state["status"] = "completed"
        state["finished_at"] = datetime.now().isoformat(timespec="seconds")
        write_batch_progress(state, settings)
        return batch

    logging.basicConfig(
        filename=str(log_path(settings)),
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        encoding="utf-8",
        force=True,
    )
    logger.info("批量提取开始 total=%s skipped=%s", len(pending), skipped)

    with keep_awake(prevent_display_sleep=True):
        for idx, row in enumerate(pending, start=1):
            linked: list[str] = []
            if row.backend_flow:
                linked.append(row.backend_flow)
            if row.field_flow:
                linked.append(row.field_flow)
            label = f"[{row.sheet_name}] {row.form_name}"
            state.update({"done": idx - 1, "current": label})
            write_batch_progress(state, settings)

            record = _extract_with_retry(
                row,
                linked=linked,
                settings=settings,
                max_retries=max_retries,
            )
            batch.records.append(record)
            if record.success:
                batch.success += 1
                state["success"] += 1
            else:
                batch.failed += 1
                state["failed"] += 1
                batch.errors.append(f"{row.sheet_name} / {row.form_name}: {record.error}")

            state["done"] = idx
            write_batch_progress(state, settings)
            logger.info("进度 %s/%s ok=%s %s", idx, len(pending), record.success, row.form_name)

    save_batch_report(batch, settings)
    cumulative_after = save_extraction_status(
        filter_targets_by_sheets(rows, sheet_names),
        settings=settings,
    )
    state.update(
        {
            "status": "completed",
            "done": len(pending),
            "current": "",
            "finished_at": datetime.now().isoformat(timespec="seconds"),
            "cumulative_success_unique": cumulative_after["stored_success_unique"],
            "cumulative_failed_unique": cumulative_after["stored_failed_unique"],
            "cumulative_missing_unique": cumulative_after["stored_missing_unique"],
            "cumulative_extractable_targets": cumulative_after["total_targets"],
        }
    )
    write_batch_progress(state, settings)
    pid_path(settings).unlink(missing_ok=True)
    logger.info("批量提取完成 success=%s failed=%s", batch.success, batch.failed)
    return batch


def save_batch_report(batch: BatchExtractResult, settings=None) -> None:
    settings = settings or get_settings()
    path = settings.forms_store_dir / "batch_extract_report.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "total": batch.total,
        "success": batch.success,
        "failed": batch.failed,
        "skipped": batch.skipped,
        "errors": batch.errors,
        "records": [
            {
                "template_name": r.template_name,
                "sheet_name": r.sheet_name,
                "success": r.success,
                "field_count": len(r.fields),
                "error": r.error,
            }
            for r in batch.records
        ],
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _extract_with_retry(row, *, linked: list[str], settings, max_retries: int) -> RawFormRecord:
    from core.form.form_extractor import extract_raw_form

    last_error = ""
    for attempt in range(max_retries):
        try:
            record = extract_raw_form(
                row.form_name,
                settings=settings,
                task_seq=row.seq,
                sheet_name=row.sheet_name,
                linked_workflows=linked,
            )
            if record.success:
                return record
            last_error = record.error or "提取未成功"
            if not is_transient_error(last_error):
                return record
        except Exception as exc:
            last_error = str(exc)
            logger.warning("提取失败 attempt=%s form=%s %s", attempt + 1, row.form_name, last_error)

        if attempt < max_retries - 1 and is_transient_error(last_error):
            time.sleep(min(15, 5 * (attempt + 1)))
            continue

        record = RawFormRecord(
            template_name=row.form_name,
            sheet_name=row.sheet_name,
            success=False,
            error=last_error,
            task_table_seq=row.seq,
            linked_workflows=linked,
            extracted_at=datetime.now().isoformat(timespec="seconds"),
        )
        save_form(record, settings)
        return record

    record = RawFormRecord(
        template_name=row.form_name,
        sheet_name=row.sheet_name,
        success=False,
        error=last_error,
        task_table_seq=row.seq,
        linked_workflows=linked,
        extracted_at=datetime.now().isoformat(timespec="seconds"),
    )
    save_form(record, settings)
    return record


def start_batch_worker(
    *,
    sheet_names: list[str] | None = None,
    skip_existing: bool = True,
    settings=None,
) -> subprocess.Popen | None:
    """独立子进程执行批量提取，避免 Streamlit 会话断开导致任务中止。"""
    settings = settings or get_settings()
    if is_batch_running(settings):
        return None

    script = PROJECT_ROOT / "scripts" / "run_batch_form_extract.py"
    cmd = [sys.executable, str(script)]
    if skip_existing:
        cmd.append("--skip-existing")
    else:
        cmd.append("--no-skip-existing")
    if sheet_names:
        cmd.extend(["--sheets", ",".join(sheet_names)])

    log_file = open(log_path(settings), "a", encoding="utf-8")
    creationflags = subprocess.CREATE_NEW_PROCESS_GROUP if sys.platform == "win32" else 0

    proc = subprocess.Popen(
        cmd,
        cwd=str(PROJECT_ROOT),
        stdout=log_file,
        stderr=subprocess.STDOUT,
        creationflags=creationflags,
    )
    pid_path(settings).write_text(str(proc.pid), encoding="utf-8")
    write_batch_progress(
        {
            "status": "running",
            "done": 0,
            "total": 0,
            "success": 0,
            "failed": 0,
            "skipped": 0,
            "current": "后台子进程启动中…",
            "started_at": datetime.now().isoformat(timespec="seconds"),
            "pid": proc.pid,
            "mode": "background",
            "keep_awake": True,
        },
        settings,
    )
    return proc


def run_retry_failed_job(*, settings=None, max_retries: int = 3) -> BatchExtractResult:
    """仅重试提取失败的表单（不含从未提取项）。"""
    from core.form.extraction_status import iter_failed_targets

    settings = settings or get_settings()
    rows = load_task_table(settings.flow_task_table_path)
    targets = iter_oa_form_names(rows)
    retry_list = iter_failed_targets(targets, settings=settings)
    return run_batch_job(
        skip_existing=False,
        settings=settings,
        max_retries=max_retries,
        retry_targets=retry_list,
    )


def start_retry_failed_worker(*, settings=None) -> subprocess.Popen | None:
    """后台子进程重试所有提取失败的表单。"""
    settings = settings or get_settings()
    if is_batch_running(settings):
        return None

    script = PROJECT_ROOT / "scripts" / "run_batch_form_extract.py"
    cmd = [sys.executable, str(script), "--retry-failed"]

    log_file = open(log_path(settings), "a", encoding="utf-8")
    creationflags = subprocess.CREATE_NEW_PROCESS_GROUP if sys.platform == "win32" else 0

    proc = subprocess.Popen(
        cmd,
        cwd=str(PROJECT_ROOT),
        stdout=log_file,
        stderr=subprocess.STDOUT,
        creationflags=creationflags,
    )
    pid_path(settings).write_text(str(proc.pid), encoding="utf-8")
    write_batch_progress(
        {
            "status": "running",
            "done": 0,
            "total": 0,
            "success": 0,
            "failed": 0,
            "skipped": 0,
            "current": "重试失败表单：后台子进程启动中…",
            "started_at": datetime.now().isoformat(timespec="seconds"),
            "pid": proc.pid,
            "mode": "retry_failed",
            "keep_awake": True,
        },
        settings,
    )
    return proc
