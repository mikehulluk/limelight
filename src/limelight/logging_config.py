from __future__ import annotations

import logging
import os
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

LOG_ENV_VAR = "LIMELIGHT_LOG_FILE"
LOG_FORMAT = "%(asctime)s %(levelname)s [%(name)s] %(message)s"
CONSOLE_FORMAT = "%(levelname)s: %(message)s"

_configured = False
_log_file_path: Path | None = None
_previous_excepthook = sys.excepthook


def configure_logging(log_file: str | Path | None = None) -> Path:
    """Configure Limelight logging once and return the active log file path."""
    global _configured, _log_file_path

    path = _resolve_log_file(log_file)
    if _configured:
        return _log_file_path or path

    path.parent.mkdir(parents=True, exist_ok=True)

    root_logger = logging.getLogger()
    root_logger.setLevel(logging.WARNING)
    logging.getLogger("limelight").setLevel(logging.DEBUG)

    file_handler = RotatingFileHandler(
        path,
        maxBytes=2_000_000,
        backupCount=3,
        encoding="utf-8",
    )
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(logging.Formatter(LOG_FORMAT))
    root_logger.addHandler(file_handler)

    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.WARNING)
    console_handler.setFormatter(logging.Formatter(CONSOLE_FORMAT))
    root_logger.addHandler(console_handler)

    logging.captureWarnings(True)
    sys.excepthook = _log_unhandled_exception

    _configured = True
    _log_file_path = path
    logging.getLogger(__name__).info("Limelight logging initialized at %s", path)
    return path


def log_file_path() -> Path:
    return _log_file_path or _resolve_log_file(None)


def _resolve_log_file(log_file: str | Path | None) -> Path:
    if log_file is not None:
        return Path(log_file).expanduser().resolve()

    env_path = os.environ.get(LOG_ENV_VAR)
    if env_path:
        return Path(env_path).expanduser().resolve()

    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        return Path(local_app_data).expanduser().resolve() / "Limelight" / "limelight.log"

    state_home = os.environ.get("XDG_STATE_HOME")
    if state_home:
        return Path(state_home).expanduser().resolve() / "limelight" / "limelight.log"

    return Path.home() / ".local" / "state" / "limelight" / "limelight.log"


def _log_unhandled_exception(
    exc_type: type[BaseException],
    exc_value: BaseException,
    exc_traceback: object,
) -> None:
    if issubclass(exc_type, KeyboardInterrupt):
        _previous_excepthook(exc_type, exc_value, exc_traceback)
        return

    logging.getLogger("limelight").critical(
        "Unhandled exception",
        exc_info=(exc_type, exc_value, exc_traceback),
    )
