from __future__ import annotations

import argparse
import hashlib
import json
import logging
import sys
from pathlib import Path
from typing import Any, Sequence

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from limelight.app import LimelightRuntime
    from limelight.logging_config import configure_logging
    from limelight.metadata import metadata_entries
    from limelight.reader import LimelightError, LimelightPackage, open_limelight
    from limelight.hdf_sources import verify_hdf_sources
    from limelight.semantic import validate_manifest_semantics
    from limelight.settings import load_settings
else:
    from .app import LimelightRuntime
    from .logging_config import configure_logging
    from .metadata import metadata_entries
    from .reader import LimelightError, LimelightPackage, open_limelight
    from .hdf_sources import verify_hdf_sources
    from .semantic import validate_manifest_semantics
    from .settings import load_settings
    from .signing import verify_manifest_signatures

logger = logging.getLogger("limelight.cli")

# The subcommands, in the order they are shown. `LL` uses these to tell a
# request for the CLI from a package to open in the app.
COMMANDS = ("json", "summary", "verify", "pdf", "meta", "desktop-entry")


def main(argv: Sequence[str] | None = None) -> int:
    settings = load_settings()
    pdf_settings = settings.get("pdf", {})
    default_rolling_build = bool(pdf_settings.get("rollingBuild", False))

    parser = argparse.ArgumentParser(
        prog="limelight-cli",
        description="Inspect and validate Limelight packages without opening the app.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    json_parser = subparsers.add_parser("json", help="Print the normalized project manifest as JSON")
    json_parser.add_argument("package", help="Path to a .limelight or .ll folder or archive")

    summary_parser = subparsers.add_parser("summary", help="Print a project summary")
    summary_parser.add_argument("package", help="Path to a .limelight or .ll folder or archive")

    verify_parser = subparsers.add_parser("verify", help="Validate one or more Limelight packages")
    verify_parser.add_argument("packages", nargs="+", help="Paths to .limelight or .ll folders or archives")

    pdf_parser = subparsers.add_parser("pdf", help="Render each package's story document to a PDF")
    pdf_parser.add_argument("packages", nargs="+", help="Paths to .limelight or .ll folders or archives")
    pdf_parser.add_argument(
        "--rolling-build",
        action="store_true",
        default=default_rolling_build,
        help=(
            "Name output as <name>-<documentVersion>-<sha8>.pdf and maintain a <name>.pdf symlink "
            "pointing at it, instead of the default <input>.pdf. "
            f"Defaults to {default_rolling_build} (from limelight-setting.json if present)."
        ),
    )

    meta_parser = subparsers.add_parser(
        "meta",
        help="Print a package's metadata as a JSON list of {name, type, value}, values typed",
    )
    meta_parser.add_argument("package", help="Path to a .limelight or .ll folder or archive")

    desktop_parser = subparsers.add_parser(
        "desktop-entry",
        help="Install a Linux desktop entry for this install, so the app has a taskbar icon and owns .limelight and .ll files",
    )
    desktop_parser.add_argument("--remove", action="store_true", help="Remove the entry instead")

    args = parser.parse_args(argv)
    log_path = configure_logging()
    logger.info("Limelight CLI started with log file %s", log_path)

    if args.command == "desktop-entry":
        return _run_desktop_entry(remove=args.remove)
    if args.command == "verify":
        return _run_verify(args.packages)
    if args.command == "pdf":
        return _run_pdf(args.packages, rolling_build=args.rolling_build)

    try:
        with open_limelight(args.package) as package:
            logger.info("Reading Limelight package %s", package.path)
            manifest = package.manifest_json()
            validate_manifest_semantics(manifest)
            if args.command == "json":
                print(json.dumps(manifest, indent=2))
            elif args.command == "meta":
                print(json.dumps(metadata_entries(manifest), indent=2))
            else:
                print(_summarize(package, manifest))
    except (FileNotFoundError, LimelightError) as error:
        logger.exception("Limelight failed: %s", error)
        return 1
    except Exception:
        logger.exception("Unexpected Limelight failure")
        return 1

    return 0


def _run_desktop_entry(*, remove: bool) -> int:
    from . import desktop_entry

    try:
        paths = desktop_entry.remove() if remove else desktop_entry.install()
    except (RuntimeError, FileNotFoundError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    verb = "Removed" if remove else "Wrote"
    for path in paths:
        print(f"{verb} {path}")
    if not remove:
        print("Limelight now appears in the applications menu, and .limelight and .ll files open with it.")
    elif not paths:
        print("No desktop entry was installed.")
    return 0


def _summarize(package: LimelightPackage, manifest: dict[str, Any]) -> str:
    project = manifest["project"]
    story = manifest["story"]
    sources = manifest["sources"]
    figure_specs = manifest["figures"]
    figure_views = manifest["figureViews"]

    lines = [
        f"Package: {package.path}",
        f"Project: {project['title']}",
        f"Sources: {len(sources)}",
        f"Figure specs: {len(figure_specs)}",
        f"Figure views: {len(figure_views)}",
        f"Story: {story['documentPath']} ({story['format']})",
    ]

    metadata = metadata_entries(manifest)
    if metadata:
        lines.append("")
        lines.append("Metadata:")
        lines.extend(f"- {entry['name']} ({entry['type']}): {entry['value']}" for entry in metadata)

    if figure_specs:
        lines.append("")
        lines.append("Figure specs:")
        lines.extend(
            f"- Figure spec {index}. {figure_spec['id']}: {figure_spec['title']}"
            for index, figure_spec in enumerate(figure_specs, start=1)
        )

    return "\n".join(lines)


def _run_verify(paths: Sequence[str]) -> int:
    failed = False
    for raw_path in paths:
        try:
            with open_limelight(raw_path) as package:
                manifest = package.manifest_json()
                validate_manifest_semantics(manifest)
                verify_hdf_sources(package, manifest)
                signature_checks = verify_manifest_signatures(package, manifest)
            for line in _signature_check_lines(signature_checks):
                print(line)
                if line.startswith("INVALID"):
                    failed = True
            print(f"OK {raw_path}")
        except (FileNotFoundError, LimelightError) as error:
            print(f"INVALID {raw_path}: {error}")
            failed = True
    return 1 if failed else 0


def _signature_check_lines(checks: Sequence[Any]) -> list[str]:
    by_document: dict[str, list[Any]] = {}
    for check in checks:
        by_document.setdefault(check.document_path, []).append(check)

    lines: list[str] = []
    for document_path, document_checks in by_document.items():
        failures = [check for check in document_checks if not check.ok]
        if failures:
            reasons = ", ".join(f"signature from {check.signer} failed ({check.reason})" for check in failures)
            lines.append(f"INVALID {document_path}: {reasons}")
        else:
            signers = ", ".join(check.signer for check in document_checks)
            lines.append(f"OK {document_path}: signed by {signers}")
    return lines


def _default_pdf_output_path(input_path: Path) -> Path:
    # Shared with the app's File -> Export to PDF so both name the file the same
    # way. Imported here rather than at module scope because limelight.pdf_export
    # pulls in PySide6, which the other subcommands must not pay for.
    from .pdf_export import default_pdf_path

    return default_pdf_path(input_path)


def _rolling_build_pdf_output_paths(input_path: Path, version: str, manifest_text: str) -> tuple[Path, Path]:
    sha8 = hashlib.sha256(manifest_text.encode("utf-8")).hexdigest()[:8]
    parent = input_path.parent
    versioned_path = parent / f"{input_path.stem}-{version}-{sha8}.pdf"
    symlink_path = parent / f"{input_path.stem}.pdf"
    return versioned_path, symlink_path


def _create_or_replace_symlink(symlink_path: Path, target_path: Path) -> None:
    if symlink_path.is_symlink() or symlink_path.exists():
        symlink_path.unlink()
    try:
        symlink_path.symlink_to(target_path.name)
    except OSError as error:
        print(
            f"WARNING: could not create symlink {symlink_path} -> {target_path.name}: {error} "
            "(creating symlinks on Windows requires Developer Mode or admin rights)"
        )


def _run_pdf(paths: Sequence[str], *, rolling_build: bool = False) -> int:
    import os

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

    from PySide6.QtWidgets import QApplication

    # The same pipeline the app's File -> Export to PDF uses, so a story exported
    # here and one exported from the app are the same document.
    from .pdf_export import PdfExportError, build_story_pdf_html, printed_page, write_html_to_pdf
    from .qt_app import story_pdf_renderers

    QApplication.instance() or QApplication(sys.argv[:1])

    failed = False
    for raw_path in paths:
        input_path = Path(raw_path)
        try:
            package = open_limelight(raw_path)
        except (FileNotFoundError, LimelightError) as error:
            print(f"FAILED {raw_path}: {error}")
            failed = True
            continue

        try:
            manifest = package.manifest_json()
            validate_manifest_semantics(manifest)
            runtime = LimelightRuntime(package, manifest)
            try:
                document_html = build_story_pdf_html(runtime, story_pdf_renderers(runtime))
                if rolling_build:
                    document_version = manifest["project"].get("documentVersion") or "0"
                    output_path, symlink_path = _rolling_build_pdf_output_paths(
                        input_path, document_version, package.manifest_text()
                    )
                    write_html_to_pdf(document_html, output_path, printed_page(runtime))
                    _create_or_replace_symlink(symlink_path, output_path)
                else:
                    output_path = _default_pdf_output_path(input_path)
                    write_html_to_pdf(document_html, output_path, printed_page(runtime))
                print(f"OK {raw_path} -> {output_path}")
            finally:
                runtime.close()
        except (LimelightError, PdfExportError, OSError) as error:
            print(f"FAILED {raw_path}: {error}")
            failed = True

    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
