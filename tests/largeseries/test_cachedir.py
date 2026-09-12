from __future__ import annotations

from pathlib import Path

import pytest

from limelight.largeseries.cachedir import resolve_cache_dir


@pytest.fixture(autouse=True)
def _clear_cache_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("LIMELIGHT_CACHE_DIR", raising=False)
    monkeypatch.delenv("LOCALAPPDATA", raising=False)
    monkeypatch.delenv("XDG_CACHE_HOME", raising=False)


def test_explicit_arg_wins(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LIMELIGHT_CACHE_DIR", str(tmp_path / "env-dir"))
    explicit = tmp_path / "explicit-dir"

    result = resolve_cache_dir(explicit)

    assert result == explicit.resolve()
    assert result.is_dir()


def test_env_var_wins_over_platform_defaults(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    env_dir = tmp_path / "env-dir"
    monkeypatch.setenv("LIMELIGHT_CACHE_DIR", str(env_dir))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local-app-data"))

    result = resolve_cache_dir()

    assert result == env_dir.resolve()
    assert result.is_dir()


def test_local_app_data_used_on_windows_style(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    local_app_data = tmp_path / "local-app-data"
    monkeypatch.setenv("LOCALAPPDATA", str(local_app_data))

    result = resolve_cache_dir()

    assert result == (local_app_data / "Limelight" / "Cache").resolve()
    assert result.is_dir()


def test_xdg_cache_home_used_when_no_local_app_data(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    xdg_cache_home = tmp_path / "xdg-cache"
    monkeypatch.setenv("XDG_CACHE_HOME", str(xdg_cache_home))

    result = resolve_cache_dir()

    assert result == (xdg_cache_home / "limelight").resolve()
    assert result.is_dir()


def test_home_fallback_when_nothing_set(monkeypatch: pytest.MonkeyPatch) -> None:
    result = resolve_cache_dir()

    assert result == (Path.home() / ".cache" / "limelight").resolve()
