# Packaging Limelight

Limelight ships as a native installer per platform, built from a PyInstaller
**one-directory** bundle.

| Platform | Artifact | Built by |
| --- | --- | --- |
| Windows | `limelight-<version>-windows-x64.exe` | Inno Setup, `windows/limelight.iss` |
| macOS | `Limelight-<version>.dmg` | `macos/build_dmg.sh` |
| Linux | `limelight_<version>_amd64.deb`, `Limelight-<version>-x86_64.AppImage` | `linux/build_linux_packages.sh` |

Everything lands in `dist/installer/`.

## Building locally

Each installer must be built on its own operating system — PyInstaller freezes
the interpreter and shared libraries of the machine it runs on, so there is no
cross-compilation. `.github/workflows/build-installers.yml` runs all three on a
runner matrix: a pushed `v*` tag builds them and attaches them to a GitHub
Release with the tag's section of `CHANGELOG.md` as the notes, and a manual run
from the Actions tab builds them as workflow artifacts only.

The same tag push runs `.github/workflows/publish.yml`, which uploads the sdist
and wheel to PyPI as `limelight-app` through Trusted Publishing.

```bash
pip install -e . pyinstaller

python packaging/make_icons.py         # regenerate icons (needed on macOS for the .icns)
pyinstaller packaging/limelight.spec --noconfirm

# then, on the matching platform:
packaging/linux/build_linux_packages.sh 0.1.0
packaging/macos/build_dmg.sh 0.1.0
iscc /DAppVersion=0.1.0 packaging\windows\limelight.iss
```

## Things worth knowing

**No `dhall-to-json` in the bundle.** A `.limelight` manifest is Dhall, but
`limelight.dhall_subset` evaluates everything the writer emits in Python, so the
35 MB binary the installers used to vendor is gone. The reader only shells out to
`dhall-to-json` - from `PATH` or `LIMELIGHT_DHALL_TO_JSON` - for a hand-written
manifest that uses Dhall beyond that subset.

**One-directory, never one-file.** `--onefile` unpacks the whole bundle — Qt,
QtWebEngine, matplotlib, numpy, roughly 700 MB — into a temporary directory on
*every* launch, costing seconds each time. One-dir does no extraction. The
installers hide the directory inside `Program Files`, the `.app`, or
`/opt/limelight`, so there is no cosmetic cost.

**The matplotlib font cache is why `limelight_entry.py` exists.** PyInstaller's
matplotlib runtime hook points `MPLCONFIGDIR` at a *fresh temporary directory*
on every launch. That is correct for a one-file build, whose path changes each
run, but here it means matplotlib rebuilds its entire font list on every single
start — about 2.8 seconds, measured. `packaging/limelight_entry.py` runs after all
runtime hooks and before any Limelight import, and repoints `MPLCONFIGDIR` at a
persistent per-user cache. Measured on Linux, this took startup from **4.0 s to
1.15 s**, matching a plain `python -m limelight` from a virtualenv. The first launch
after installing still pays the ~4 s to build the cache once.

If you ever change the entry point, keep that fix ahead of the first `limelight`
import or the regression comes straight back.

**Size.** The bundle is around 700 MB uncompressed, roughly 215–260 MB
compressed. `libQt6WebEngineCore.so` alone is 194 MB. It cannot be dropped: the
story panel renders markdown and MathJax in a `QWebEngineView`, and PDF export
prints from the same engine. The spec already excludes the Qt modules the app
never touches.

**File associations target the archive form.** A package is a folder while it
is authored and a `.limelight` archive once it is shipped, so only the archive is
associated: the Windows registry entries, the macOS `CFBundleDocumentTypes`
entry, and the Linux MIME type all match `*.limelight` files. Folders are opened
through the app's *Open Package Folder...* command instead. On Linux the MIME
type must be installed to `/usr/share/mime/packages/` and the cache rebuilt, or
the `.desktop` entry's `MimeType=` line matches nothing; the `.deb` does both in
its `postinst`.

**Signing.** Nothing here is code-signed.

- *Windows*: users get a SmartScreen warning until the installer is signed with
  an EV or OV code-signing certificate.
- *macOS*: `build_dmg.sh` applies an ad-hoc signature so the app runs locally on
  Apple silicon, but Gatekeeper will still block it on another machine. Shipping
  properly needs a Developer ID plus notarisation; the command lines are in the
  header comment of `build_dmg.sh`.
- *Linux*: no signing expected.

**AppImage and FUSE.** An AppImage normally mounts itself through FUSE and
starts as fast as the `.deb`. On a machine without FUSE it falls back to
extracting all 257 MB on each run, which is slow. Prefer the `.deb` where you
can.

## Files

| File | Purpose |
| --- | --- |
| `limelight.spec` | PyInstaller build definition |
| `limelight_entry.py` | Frozen entry point; fixes the matplotlib cache directory |
| `make_icons.py` | Renders `limelight-icon.svg` into `.ico`, `.icns` and `.png` |
| `windows/limelight.iss` | Inno Setup installer, with `.limelight` file association |
| `macos/build_dmg.sh` | Drag-to-Applications disk image |
| `linux/build_linux_packages.sh` | `.deb` and AppImage |
| `linux/limelight.desktop` | Desktop entry used by both Linux packages |
| `linux/limelight-mime.xml` | Defines `application/x-limelight-package`, without which the association never fires |
