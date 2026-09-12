"""``LL``: the short command.

``LL <package>`` opens a package in the desktop app, and ``LL`` alone opens
the app and asks for one; ``LL verify <package>``, ``LL pdf <package>`` and the
other subcommands are ``limelight-cli``. The first argument decides which,
so the two never need to share a parser.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Sequence

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from limelight import cli, gui_cli
else:
    from . import cli, gui_cli

USAGE = f"""usage: LL [<package>] [--log-file PATH] [--debug-timing]
       LL {{{",".join(cli.COMMANDS)}}} ...

Open a Limelight package in the desktop app (the first form, as limelight-gui),
or inspect one from the command line (the second, as limelight-cli).

  LL example.limelight          open a package in the app
  LL                            open the app and choose a package there
  LL summary example.limelight  print a summary of a package
  LL verify example.limelight   validate one or more packages
  LL pdf example.limelight      render a package's story to PDF

Run `LL <command> --help` or `LL --gui-help` for the details of either form.
"""


def main(argv: Sequence[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if args and args[0] in cli.COMMANDS:
        return cli.main(args)
    if args and args[0] in ("-h", "--help"):
        print(USAGE, end="")
        return 0
    if args and args[0] == "--gui-help":
        args[0] = "--help"
    return gui_cli.main(args)


if __name__ == "__main__":
    raise SystemExit(main())
