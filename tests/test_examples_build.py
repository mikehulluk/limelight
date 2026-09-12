"""Build every example and check the package it produces still validates.

The examples are the only place several manifest shapes are exercised end to
end, and example00 writes its `project.dhall` by hand rather than through
`LimelightProject`, so it is the one package that does not follow the writer when
the schema moves. Both times the schema has gained a required field, that
example silently stopped typechecking.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from limelight.app import LimelightRuntime
from limelight.reader import open_limelight
from limelight.semantic import validate_manifest_semantics

REPO_ROOT = Path(__file__).resolve().parents[1]
EXAMPLES_DIR = REPO_ROOT / "examples"

# example30 needs a 400 MB HDF5 source it generates on first run. Building it
# inside a test would be antisocial, so it runs only where that file already
# exists.
LARGE_SOURCE = REPO_ROOT / "_build" / "examples" / "example30-largedataset-source.h5"
NEEDS_LARGE_SOURCE = {"create_example30_largedataset.py"}


def _example_scripts() -> list[Path]:
    return sorted(EXAMPLES_DIR.glob("create_example*.py"))


@pytest.mark.parametrize("script", _example_scripts(), ids=lambda path: path.stem)
def test_example_builds_and_validates(script: Path) -> None:
    if script.name in NEEDS_LARGE_SOURCE and not LARGE_SOURCE.exists():
        pytest.skip(f"{LARGE_SOURCE.name} has not been generated on this machine")

    result = subprocess.run(
        [sys.executable, str(script)],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
    )
    assert result.returncode == 0, f"{script.name} failed:\n{result.stderr}"

    # Every example prints the package it wrote as its last line of output.
    package_path = Path(result.stdout.strip().splitlines()[-1])
    assert package_path.exists(), f"{script.name} reported a package that is not there"

    # Evaluating the manifest catches a package whose Dhall no longer resolves;
    # the other two are what the application does before it shows anything,
    # and between them they read most of the manifest.
    with open_limelight(package_path) as package:
        manifest = package.manifest_json()
        validate_manifest_semantics(manifest)
        LimelightRuntime(package, manifest)
