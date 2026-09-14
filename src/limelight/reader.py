from __future__ import annotations

import csv
import json
import os
import shutil
import subprocess
import zipfile
from pathlib import Path, PurePosixPath
from tempfile import TemporaryDirectory
from typing import Any

from . import dhall_subset


class LimelightError(RuntimeError):
    """Raised when a Limelight package cannot be opened or interpreted."""


def dhall_tool_path(name: str) -> str:
    """Resolve a Dhall command line tool: an environment override, else PATH."""

    override = os.environ.get(f"LIMELIGHT_{name.replace('-', '_').upper()}")
    if override:
        return override
    return name


# The file extensions a package may carry, in either folder or archive form:
# the long one names the format, the short one is quicker to type. The reader
# itself goes by content (a folder, or a zip), never by the name.
PACKAGE_SUFFIXES: tuple[str, ...] = (".limelight", ".ll")


def open_limelight(path: str | Path) -> "LimelightPackage":
    return LimelightPackage.open(path)


class LimelightPackage:
    def __init__(
        self,
        *,
        path: Path,
        root: Path,
        temporary_directory: TemporaryDirectory[str] | None = None,
    ) -> None:
        self.path = path.resolve()
        self.root = root.resolve()
        self._temporary_directory = temporary_directory

    @classmethod
    def open(cls, path: str | Path) -> "LimelightPackage":
        package_path = Path(path)
        if package_path.is_dir():
            root = package_path.resolve()
            _require_manifest(root)
            return cls(path=root, root=root)

        if package_path.is_file():
            if not zipfile.is_zipfile(package_path):
                raise LimelightError(f"{package_path} is not a Limelight folder or zip archive")

            temporary_directory = TemporaryDirectory(prefix="limelight-open-")
            extraction_root = Path(temporary_directory.name)
            try:
                _extract_zip_safely(package_path, extraction_root)
                root = _find_package_root(extraction_root)
            except Exception:
                temporary_directory.cleanup()
                raise

            return cls(path=package_path.resolve(), root=root, temporary_directory=temporary_directory)

        raise FileNotFoundError(package_path)

    def close(self) -> None:
        if self._temporary_directory is not None:
            self._temporary_directory.cleanup()
            self._temporary_directory = None

    def __enter__(self) -> "LimelightPackage":
        return self

    def __exit__(self, *args: object) -> None:
        self.close()

    def __del__(self) -> None:
        self.close()

    def list_files(self) -> list[str]:
        return sorted(item.relative_to(self.root).as_posix() for item in self.root.rglob("*") if item.is_file())

    def package_path(self, relative_path: str | Path) -> Path:
        root = self.root.resolve()
        target = (root / relative_path).resolve()
        try:
            target.relative_to(root)
        except ValueError as error:
            raise LimelightError(f"{relative_path} escapes the Limelight package root") from error
        return target

    def read_bytes(self, relative_path: str | Path) -> bytes:
        return self.package_path(relative_path).read_bytes()

    def read_text(self, relative_path: str | Path, *, encoding: str = "utf-8") -> str:
        return self.package_path(relative_path).read_text(encoding=encoding)

    def read_csv(self, relative_path: str | Path, *, encoding: str = "utf-8") -> list[dict[str, str]]:
        with self.package_path(relative_path).open(newline="", encoding=encoding) as handle:
            return list(csv.DictReader(handle))

    def manifest_text(self) -> str:
        return self.read_text("project.dhall")

    def manifest_json(self, *, dhall_to_json: str | None = None) -> dict[str, Any]:
        """Evaluate project.dhall to the JSON-shaped dict the rest of the app reads.

        The built-in evaluator handles every manifest the writer produces and
        needs no external tool. A hand-written manifest that reaches beyond
        that subset, or trips the evaluator, is passed to ``dhall-to-json`` if
        one is on PATH or named by ``LIMELIGHT_DHALL_TO_JSON``; otherwise the
        evaluator's error is reported as-is.
        Passing ``dhall_to_json`` explicitly bypasses the evaluator.
        """

        manifest_path = self.package_path("project.dhall")
        if dhall_to_json is not None:
            return self._manifest_json_via_tool(dhall_to_json, manifest_path)

        try:
            manifest = dhall_subset.load(manifest_path)
        except dhall_subset.DhallError as error:
            tool = dhall_tool_path("dhall-to-json")
            if not _dhall_tool_available(tool):
                raise LimelightError(str(error)) from error
            try:
                return self._manifest_json_via_tool(tool, manifest_path)
            except LimelightError as tool_error:
                raise LimelightError(f"{error}\n{tool_error}") from tool_error

        if not isinstance(manifest, dict):
            raise LimelightError(f"{manifest_path} does not evaluate to a record")
        return manifest

    def _manifest_json_via_tool(self, tool: str, manifest_path: Path) -> dict[str, Any]:
        output = _run_dhall_tool([tool, "--file", str(manifest_path)], cwd=self.root)
        return json.loads(output)


def _require_manifest(root: Path) -> None:
    if not (root / "project.dhall").is_file():
        raise LimelightError(f"{root} does not contain project.dhall")


def _find_package_root(extraction_root: Path) -> Path:
    if (extraction_root / "project.dhall").is_file():
        return extraction_root

    candidates = [item.parent for item in extraction_root.rglob("project.dhall")]
    if len(candidates) == 1:
        return candidates[0]
    if not candidates:
        raise LimelightError("Limelight archive does not contain project.dhall")
    raise LimelightError("Limelight archive contains multiple project.dhall files")


def _extract_zip_safely(archive_path: Path, destination: Path) -> None:
    destination = destination.resolve()
    with zipfile.ZipFile(archive_path) as archive:
        for info in archive.infolist():
            normalized = info.filename.replace("\\", "/")
            member = PurePosixPath(normalized)
            if member.is_absolute() or ".." in member.parts:
                raise LimelightError(f"Unsafe archive member path: {info.filename}")

            target = (destination / Path(*member.parts)).resolve()
            try:
                target.relative_to(destination)
            except ValueError as error:
                raise LimelightError(f"Unsafe archive member path: {info.filename}") from error

            if info.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue

            target.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(info) as source, target.open("wb") as handle:
                shutil.copyfileobj(source, handle)


def _dhall_tool_available(tool: str) -> bool:
    return Path(tool).is_file() or shutil.which(tool) is not None


def _run_dhall_tool(command: list[str], *, cwd: Path) -> str:
    env = os.environ.copy()
    with TemporaryDirectory(prefix="limelight-dhall-cache-") as cache_dir:
        env["XDG_CACHE_HOME"] = cache_dir
        try:
            completed = subprocess.run(
                command,
                cwd=cwd,
                env=env,
                capture_output=True,
                check=False,
                text=True,
            )
        except FileNotFoundError as error:
            raise LimelightError(
                f"{command[0]} is not on PATH; install it or pass an explicit tool path"
            ) from error

    if completed.returncode != 0:
        detail = completed.stderr.strip() or completed.stdout.strip()
        raise LimelightError(f"{command[0]} failed: {detail}")

    return completed.stdout
