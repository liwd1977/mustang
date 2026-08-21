"""配置热更新测试。"""

from __future__ import annotations

from pathlib import Path

import pytest

from core.config.settings import get_settings, reload_settings, update_env_var


@pytest.fixture
def isolated_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    env_file = tmp_path / ".env"
    env_file.write_text("SHENBI_API_TOKEN=old-token\n", encoding="utf-8")
    monkeypatch.setattr("core.config.settings.PROJECT_ROOT", tmp_path)
    get_settings.cache_clear()
    yield env_file
    get_settings.cache_clear()


def test_update_env_var_replaces_and_reload(isolated_env: Path):
    update_env_var("SHENBI_API_TOKEN", "new-token")
    assert "SHENBI_API_TOKEN=new-token" in isolated_env.read_text(encoding="utf-8")
    assert "old-token" not in isolated_env.read_text(encoding="utf-8")
    assert reload_settings().shenbi_api_token == "new-token"
