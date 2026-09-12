from __future__ import annotations

if __package__ in {None, ""}:
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from limelight.gui_cli import main
else:
    from .gui_cli import main


raise SystemExit(main())
