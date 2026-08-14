"""pytest 全局 fixture。"""

from __future__ import annotations

import pytest

from core.config.settings import get_settings


@pytest.fixture(autouse=True)
def _guide_parse_use_cache_for_tests(monkeypatch: pytest.MonkeyPatch):
    """测试默认走批量缓存模式，并忽略 Word mtime，避免触发全量 VLM 解析。"""
    monkeypatch.setenv("GUIDE_PARSE_USE_CACHE", "1")
    monkeypatch.setattr(
        "core.workflow.result_store.is_result_stale",
        lambda *args, **kwargs: False,
    )
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()
