from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path
from typing import Sequence

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from limelight.app import run_app
    from limelight.logging_config import configure_logging
    from limelight.reader import LimelightError
else:
    from .app import run_app
    from .logging_config import configure_logging
    from .reader import LimelightError

logger = logging.getLogger("limelight.gui_cli")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="limelight-gui",
        description="Open a Limelight package in the desktop app.",
    )
    parser.add_argument(
        "package",
        nargs="?",
        help="Path to a .limelight or .ll folder or archive; omit it to be asked on startup",
    )
    parser.add_argument(
        "--log-file",
        help="Write diagnostic logs to this path instead of the default Limelight log file",
    )
    parser.add_argument(
        "--debug-timing",
        action="store_true",
        help="Log detailed UI/data rendering timings while the app is open",
    )

    args = parser.parse_args(argv)
    log_path = configure_logging(args.log_file)
    logger.info("Limelight GUI started with log file %s", log_path)

    try:
        run_app(args.package, debug_timing=args.debug_timing)
    except (FileNotFoundError, LimelightError) as error:
        logger.exception("Limelight failed: %s", error)
        return 1
    except Exception:
        logger.exception("Unexpected Limelight failure")
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
