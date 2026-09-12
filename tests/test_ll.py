"""``LL`` sends a subcommand to the CLI and anything else to the app."""

from __future__ import annotations

from limelight import cli, ll


def test_a_cli_subcommand_goes_to_the_cli(monkeypatch) -> None:
    seen: list[list[str]] = []
    monkeypatch.setattr(ll.cli, "main", lambda args: seen.append(list(args)) or 0)
    monkeypatch.setattr(ll.gui_cli, "main", lambda args: (_ for _ in ()).throw(AssertionError("gui called")))

    for command in cli.COMMANDS:
        assert ll.main([command, "pkg.limelight"]) == 0
    assert seen == [[command, "pkg.limelight"] for command in cli.COMMANDS]


def test_a_package_path_or_nothing_goes_to_the_app(monkeypatch) -> None:
    seen: list[list[str]] = []
    monkeypatch.setattr(ll.gui_cli, "main", lambda args: seen.append(list(args)) or 0)
    monkeypatch.setattr(ll.cli, "main", lambda args: (_ for _ in ()).throw(AssertionError("cli called")))

    assert ll.main(["pkg.limelight"]) == 0
    assert ll.main(["pkg.limelight", "--debug-timing"]) == 0
    assert ll.main([]) == 0
    assert seen == [["pkg.limelight"], ["pkg.limelight", "--debug-timing"], []]


def test_help_describes_both_forms(capsys) -> None:
    assert ll.main(["--help"]) == 0
    out = capsys.readouterr().out
    assert "LL example.limelight" in out
    for command in cli.COMMANDS:
        assert command in out
