"""ensure_guide_parse_fresh 双模式测试。"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from core.config.settings import Settings, get_settings
from schemas.workflow import GuideParseResult


@pytest.fixture
def dev_settings(monkeypatch: pytest.MonkeyPatch, tmp_path):
    monkeypatch.setenv("GUIDE_PARSE_USE_CACHE", "0")
    monkeypatch.setenv("OUTPUT_DIR", str(tmp_path / "output"))
    get_settings.cache_clear()
    yield get_settings()
    get_settings.cache_clear()


@pytest.fixture
def batch_settings(monkeypatch: pytest.MonkeyPatch, tmp_path):
    monkeypatch.setenv("GUIDE_PARSE_USE_CACHE", "1")
    monkeypatch.setenv("OUTPUT_DIR", str(tmp_path / "output"))
    get_settings.cache_clear()
    yield get_settings()
    get_settings.cache_clear()


def _sample_result() -> GuideParseResult:
    return GuideParseResult(source_file="guide.docx", saved_at="2026-08-12T00:00:00", source_mtime=1.0)


def test_dev_mode_reuses_fresh_complete_cache(dev_settings: Settings):
    from core.workflow import result_store

    cached = _sample_result()
    cached.parse_finished_at = "2026-08-15T12:00:00"

    with patch.object(result_store, "load_result", return_value=cached), patch.object(
        result_store, "is_result_stale", return_value=False
    ), patch("core.workflow.pipeline.run_guide_pipeline") as run_pipeline:
        assert result_store.ensure_guide_parse_fresh(dev_settings) is cached
        run_pipeline.assert_not_called()


def test_dev_mode_reparses_when_cache_incomplete(dev_settings: Settings):
    from core.workflow import result_store

    cached = _sample_result()
    cached.parse_finished_at = ""
    reparsed = _sample_result()
    reparsed.parse_finished_at = "2026-08-15T12:00:00"

    with patch.object(result_store, "load_result", return_value=cached), patch.object(
        result_store, "is_result_stale", return_value=False
    ), patch("core.workflow.pipeline.run_guide_pipeline", return_value=reparsed) as run_pipeline:
        assert result_store.ensure_guide_parse_fresh(dev_settings) is reparsed
        run_pipeline.assert_called_once()


def test_dev_mode_reuses_fresh_cache(dev_settings: Settings):
    test_dev_mode_reuses_fresh_complete_cache(dev_settings)


def test_dev_mode_auto_reparses_when_stale(dev_settings: Settings):
    from core.workflow import result_store

    cached = _sample_result()
    reparsed = _sample_result()
    reparsed.saved_at = "2026-08-14T12:00:00"

    with patch.object(result_store, "load_result", return_value=cached), patch.object(
        result_store, "is_result_stale", return_value=True
    ), patch("core.workflow.pipeline.run_guide_pipeline", return_value=reparsed) as run_pipeline:
        assert result_store.ensure_guide_parse_fresh(dev_settings) is reparsed
        run_pipeline.assert_called_once()


def test_batch_mode_raises_when_stale(batch_settings: Settings):
    from core.workflow import result_store

    cached = _sample_result()

    with patch.object(result_store, "load_result", return_value=cached), patch.object(
        result_store, "is_result_stale", return_value=True
    ):
        with pytest.raises(ValueError, match="重新解析"):
            result_store.ensure_guide_parse_fresh(batch_settings)


def test_batch_mode_raises_when_missing(batch_settings: Settings):
    from core.workflow import result_store

    with patch.object(result_store, "load_result", return_value=None):
        with pytest.raises(ValueError, match="流程解析"):
            result_store.ensure_guide_parse_fresh(batch_settings)
