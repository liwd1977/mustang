"""运行期间阻止 Windows 系统休眠（批量 OA 提取等长任务）。"""

from __future__ import annotations

import sys
from contextlib import contextmanager


@contextmanager
def keep_awake(*, prevent_display_sleep: bool = False):
    """
    阻止系统在任务执行期间进入休眠/睡眠。

    Windows: SetThreadExecutionState(ES_SYSTEM_REQUIRED [| ES_DISPLAY_REQUIRED])
    其他系统: 无操作
    """
    if sys.platform != "win32":
        yield
        return

    import ctypes

    ES_CONTINUOUS = 0x80000000
    ES_SYSTEM_REQUIRED = 0x00000001
    ES_DISPLAY_REQUIRED = 0x00000002
    flags = ES_CONTINUOUS | ES_SYSTEM_REQUIRED
    if prevent_display_sleep:
        flags |= ES_DISPLAY_REQUIRED

    ctypes.windll.kernel32.SetThreadExecutionState(flags)
    try:
        yield
    finally:
        ctypes.windll.kernel32.SetThreadExecutionState(ES_CONTINUOUS)
