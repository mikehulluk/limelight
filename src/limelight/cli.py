from __future__ import annotations

import argparse
import hashlib
import json
import logging
import sys
from pathlib import Path
from typing import Any, Callable, Sequence

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from limelight.app import LimelightRuntime
    from limelight.logging_config import configure_logging
    from limelight.metadata import metadata_entries
    from limelight.reader import LimelightError, LimelightPackage, open_limelight
    from limelight.hdf_sources import verify_hdf_sources
    from limelight.typography import verify_fonts
    from limelight.story_syntax import verify_code_tokens
    from limelight.semantic import validate_manifest_semantics
    from limelight.settings import load_settings
else:
    from .app import LimelightRuntime
    from .logging_config import configure_logging
    from .metadata import metadata_entries
    from .reader import LimelightError, LimelightPackage, open_limelight
    from .hdf_sources import verify_hdf_sources
    from .typography import verify_fonts
    from .story_syntax import verify_code_tokens
    from .semantic import validate_manifest_semantics
    from .settings import load_settings
    from .signing import verify_manifest_signatures

logger = logging.getLogger("limelight.cli")

# The subcommands, in the order they are shown. `LL` uses these to tell a
# request for the CLI from a package to open in the app.
COMMANDS = ("json", "summary", "verify", "pdf", "meta", "login", "logout", "desktop-entry")


_ALLOW_DOWNLOADS_HELP = (
    "Download hdf files a package names by URL from hosts not yet Allowed in Settings. "
    "Hosts Blocked in Settings are refused even with this. A sign-in made with `LL login` is used."
)


def _download_policy(allow_all: bool) -> Callable[[Any], bool]:
    """Whether the CLI may fetch a remote file; fetches it (with any stored token) if so.

    Blocked hosts are refused even with --allow-downloads. Progress and every
    URL go to stderr.
    """
    from .remote_files import NeedsSignIn, RemoteFileError, fetch, format_size
    from .settings import ALLOWED, BLOCKED, host_decision

    def allow(remote: Any) -> bool:
        decision = host_decision(remote.host)
        if decision == BLOCKED:
            print(f"Not downloading {remote.url}: {remote.host} is Blocked in Settings", file=sys.stderr)
            return False
        if not (allow_all or decision == ALLOWED):
            return False
        print(f"Downloading {remote.url} ({format_size(remote.size_bytes)})", file=sys.stderr)
        last = [-1]

        def progress(done: int, total: int) -> None:
            tenth = 10 * done // max(total, 1)
            if tenth != last[0]:
                last[0] = tenth
                print(f"  {10 * tenth}% of {remote.url}", file=sys.stderr)

        def waiting(seconds: float, attempt: int) -> None:
            print(f"  {remote.host} is busy; trying again in {seconds:.0f} s (try {attempt + 1})", file=sys.stderr)

        try:
            fetch(remote, progress=progress, on_wait=waiting)
        except NeedsSignIn as need:
            raise RemoteFileError(f"{need}; run: LL login {remote.url}") from None
        except RemoteFileError:
            raise
        return True

    return allow


def _run_login(url: str) -> int:
    from . import remote_auth

    store = remote_auth.default_store()
    if not store.keychain_available():
        print("login: there is no keychain on this computer, so there is nowhere safe to keep a sign-in", file=sys.stderr)
        return 1
    try:
        plan = remote_auth.discover(url, None)
        authorization = remote_auth.start_device_authorization(plan)
        print(f"Sign in to {plan.host} at {authorization.verification_uri}", file=sys.stderr)
        print(f"and type this code: {authorization.user_code}", file=sys.stderr)
        print("Only type it on that site. Waiting for you to approve...", file=sys.stderr)
        token = remote_auth.poll_for_token(plan, authorization)
    except KeyboardInterrupt:
        print("login: cancelled", file=sys.stderr)
        return 1
    except remote_auth.SignInError as error:
        print(f"login: {error}", file=sys.stderr)
        return 1
    if not store.put(token, remember=True):
        print("login: the keychain would not keep the sign-in", file=sys.stderr)
        return 1
    who = f" as {token.account}" if token.account else ""
    print(f"Signed in{who} for {plan.resource}", file=sys.stderr)
    return 0


def _run_logout(resource: str) -> int:
    from . import remote_auth

    store = remote_auth.default_store()
    try:
        signed_out = store.sign_out(resource)
    except remote_auth.SignInError as error:
        print(f"logout: {error}", file=sys.stderr)
        return 1
    if not signed_out:
        known = ", ".join(token.resource for token in store.sign_ins()) or "none"
        print(f"logout: no sign-in for {resource} (signed in: {known})", file=sys.stderr)
        return 1
    print(f"Signed out of {resource}", file=sys.stderr)
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    settings = load_settings()
    pdf_settings = settings.get("pdf", {})
    default_rolling_build = bool(pdf_settings.get("rollingBuild", False))
    from .pdf_export import DEFAULT_RASTER_DPI, DEFAULT_RASTERIZE_ABOVE_POINTS

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
    verify_parser.add_argument("--allow-downloads", action="store_true", help=_ALLOW_DOWNLOADS_HELP)

    pdf_parser = subparsers.add_parser("pdf", help="Render each package's story document to a PDF")
    pdf_parser.add_argument("packages", nargs="+", help="Paths to .limelight or .ll folders or archives")
    pdf_parser.add_argument("--allow-downloads", action="store_true", help=_ALLOW_DOWNLOADS_HELP)
    pdf_parser.add_argument(
        "--rasterize-above",
        type=_rasterize_threshold,
        default=None,
        metavar="POINTS",
        help=(
            "Draw a figure's lines, scatters and bands as images when one has more than POINTS "
            "points, keeping axes and text vector; 'never' keeps every figure vector. Defaults to "
            "the pdf block's rasterizeAbovePoints in limelight-setting.json, else "
            f"{DEFAULT_RASTERIZE_ABOVE_POINTS}."
        ),
    )
    pdf_parser.add_argument(
        "--raster-dpi",
        type=int,
        default=None,
        metavar="DPI",
        help=(
            "Resolution of the rasterised artists. Defaults to the pdf block's rasterDpi in "
            f"limelight-setting.json, else {DEFAULT_RASTER_DPI}."
        ),
    )
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

    login_parser = subparsers.add_parser(
        "login",
        help="Sign in to a site that holds a package's data, keeping the sign-in in the keychain",
    )
    login_parser.add_argument("url", help="An https URL of the data (or of the site's API) to sign in for")

    logout_parser = subparsers.add_parser("logout", help="Sign out: revoke and forget a stored sign-in")
    logout_parser.add_argument("resource", help="The resource signed in to, as `LL login` printed it")

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
    if args.command == "login":
        return _run_login(args.url)
    if args.command == "logout":
        return _run_logout(args.resource)
    if args.command == "verify":
        return _run_verify(args.packages, allow_download=_download_policy(args.allow_downloads))
    if args.command == "pdf":
        try:
            figure_options = _figure_pdf_options(pdf_settings, args.rasterize_above, args.raster_dpi)
        except (TypeError, ValueError) as error:
            pdf_parser.error(str(error))
        return _run_pdf(
            args.packages,
            rolling_build=args.rolling_build,
            figure_options=figure_options,
            allow_download=_download_policy(args.allow_downloads),
        )

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


def _run_verify(paths: Sequence[str], *, allow_download: Callable[[Any], bool] | None = None) -> int:
    failed = False
    for raw_path in paths:
        try:
            with open_limelight(raw_path) as package:
                manifest = package.manifest_json()
                validate_manifest_semantics(manifest)
                unchecked = verify_hdf_sources(package, manifest, allow_download=allow_download)
                verify_fonts(package, manifest)
                verify_code_tokens(package, manifest)
                signature_checks = verify_manifest_signatures(package, manifest)
            for line in unchecked:
                print(f"UNCHECKED {line}")
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
    # way.
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


_NO_THRESHOLD = object()


def _rasterize_threshold(value: str) -> Any:
    if value.strip().lower() == "never":
        return _NO_THRESHOLD
    try:
        return int(value)
    except ValueError:
        raise argparse.ArgumentTypeError(f"{value!r} is not a number of points or 'never'") from None


def _figure_pdf_options(pdf_settings: dict[str, Any], rasterize_above: Any, raster_dpi: int | None) -> Any:
    """The settings file's figure options, with whatever the command line said on top."""

    from .pdf_export import FigurePdfOptions

    options = FigurePdfOptions.from_settings(pdf_settings)
    if rasterize_above is not None:
        threshold = None if rasterize_above is _NO_THRESHOLD else rasterize_above
        options = FigurePdfOptions(rasterize_above_points=threshold, raster_dpi=options.raster_dpi)
    if raster_dpi is not None:
        options = FigurePdfOptions(rasterize_above_points=options.rasterize_above_points, raster_dpi=raster_dpi)
    return options


def _run_pdf(
    paths: Sequence[str],
    *,
    rolling_build: bool = False,
    figure_options: Any = None,
    allow_download: Callable[[Any], bool] | None = None,
) -> int:
    import os

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

    try:
        from PySide6.QtWidgets import QApplication

        # The same pipeline the app's File -> Export to PDF uses, so a story exported
        # here and one exported from the app are the same document.
        from .pdf_export import PdfExportError, write_story_pdf
        from .qt_app import story_pdf_renderers
    except ImportError as error:
        from .app import GUI_MISSING_MESSAGE

        print(f"pdf: {GUI_MISSING_MESSAGE} ({error})", file=sys.stderr)
        return 1

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
            runtime.allow_download = allow_download
            try:
                renderers = story_pdf_renderers(runtime, figure_options)
                if rolling_build:
                    document_version = manifest["project"].get("documentVersion") or "0"
                    output_path, symlink_path = _rolling_build_pdf_output_paths(
                        input_path, document_version, package.manifest_text()
                    )
                    write_story_pdf(runtime, renderers, output_path)
                    _create_or_replace_symlink(symlink_path, output_path)
                else:
                    output_path = _default_pdf_output_path(input_path)
                    write_story_pdf(runtime, renderers, output_path)
                print(f"OK {raw_path} -> {output_path}")
            finally:
                runtime.close()
        except (LimelightError, PdfExportError, OSError) as error:
            print(f"FAILED {raw_path}: {error}")
            failed = True

    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
