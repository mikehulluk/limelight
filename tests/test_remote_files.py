"""An hdf source can name its file by URL, with its SHA-256 and size, rather
than carry it. The file is downloaded only with the reader's say-so, kept only
if it is exactly the bytes described, and stored by digest so it is fetched
once. These tests never touch the network: the opener is replaced, or the
request goes to the local https test server (conftest.py)."""

from __future__ import annotations

import hashlib
import io
import os
from pathlib import Path

import h5py
import numpy as np
import pytest

from limelight import remote_files, settings
from limelight.app import LimelightRuntime
from limelight.hdf_sources import verify_hdf_sources
from limelight.reader import LimelightError, open_limelight
from limelight.remote_files import RemoteFile, RemoteFileError, RemoteFileNotDownloaded
from limelight.settings import ALLOWED, BLOCKED
from limelight.semantic import validate_manifest_semantics
from limelight.signing import generate_ed25519_keypair, verify_manifest_signatures
from limelight.writer import Index, LimelightProject, TimeSeriesArtist, hdf_array

URL = "https://data.example.org/recordings/rec.h5"


@pytest.fixture(autouse=True)
def isolated_dirs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(remote_files.DOWNLOAD_DIR_ENV, str(tmp_path / "downloads"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("APPDATA", str(tmp_path / "config"))
    monkeypatch.chdir(tmp_path)


def _h5(path: Path) -> Path:
    with h5py.File(path, "w") as handle:
        handle["/v"] = np.arange(1000, dtype=np.float64)
    return path


def _package(tmp_path: Path, *, signers=()) -> tuple[Path, Path]:
    h5 = _h5(tmp_path / "rec.h5")
    project = LimelightProject(title="remote", authors=["Test"])
    project.add_hdf_dataset(
        id="rec", hdf5_path=h5, y_arrays=[hdf_array("v", "/v")], index=Index.regular_int(0, 1), url=URL, signers=signers
    )
    project.add_line_figure(id="f", title="f", data="rec", x="", y=[], time_series=[TimeSeriesArtist(data="rec", array="v")])
    project.write_folder(tmp_path / "pkg")
    return tmp_path / "pkg", h5


class _Opener:
    """Serves `body` for any request, and counts the requests."""

    def __init__(self, body: bytes) -> None:
        self.body = body
        self.calls = 0

    def __call__(self, request, timeout):
        self.calls += 1
        assert request.full_url == URL
        return io.BytesIO(self.body)


def _remote(body: bytes) -> RemoteFile:
    return RemoteFile(url=URL, sha256=hashlib.sha256(body).hexdigest(), size_bytes=len(body))


def test_the_writer_records_url_digest_and_size_and_leaves_the_file_out(tmp_path: Path) -> None:
    folder, h5 = _package(tmp_path)
    assert not (folder / "data" / "rec.h5").exists()
    with open_limelight(folder) as package:
        manifest = package.manifest_json()
        validate_manifest_semantics(manifest)
    [source] = manifest["sources"]
    assert source["remote"] == {
        "url": URL,
        "sha256": hashlib.sha256(h5.read_bytes()).hexdigest(),
        "sizeBytes": h5.stat().st_size,
    }


def test_a_bundled_source_has_no_remote(tmp_path: Path) -> None:
    project = LimelightProject(title="local", authors=["Test"])
    project.add_hdf_dataset(id="rec", hdf5_path=_h5(tmp_path / "rec.h5"), y_arrays=[hdf_array("v", "/v")])
    project.write_folder(tmp_path / "pkg")
    with open_limelight(tmp_path / "pkg") as package:
        [source] = package.manifest_json()["sources"]
    assert "remote" not in source


def test_only_an_https_url_is_accepted(tmp_path: Path) -> None:
    project = LimelightProject(title="x", authors=["Test"])
    with pytest.raises(LimelightError, match="https"):
        project.add_hdf_dataset(
            id="rec", hdf5_path=_h5(tmp_path / "rec.h5"), y_arrays=[hdf_array("v", "/v")], url="http://example.org/a.h5"
        )


def test_a_download_is_kept_by_digest_and_fetched_once(tmp_path: Path) -> None:
    body = b"x" * 3_000_000
    remote = _remote(body)
    opener = _Opener(body)
    seen: list[int] = []
    path = remote_files.download(remote, opener=opener, progress=lambda done, total: seen.append(done))
    assert path == tmp_path / "downloads" / f"{remote.sha256}.h5"
    assert path.read_bytes() == body
    assert seen[-1] == len(body) and len(seen) > 1
    assert remote_files.download(remote, opener=opener) == path
    assert opener.calls == 1


@pytest.mark.parametrize(
    ("served", "match"),
    [(b"y" * 100, "SHA-256"), (b"x" * 99, "not the 100"), (b"x" * 101, "larger than")],
)
def test_a_download_that_is_not_the_described_file_is_not_kept(tmp_path: Path, served: bytes, match: str) -> None:
    remote = _remote(b"x" * 100)
    with pytest.raises(RemoteFileError, match=match):
        remote_files.download(remote, opener=_Opener(served))
    assert remote.cached_path() is None
    assert list((tmp_path / "downloads").iterdir()) == []


def test_a_cancelled_download_leaves_nothing(tmp_path: Path) -> None:
    remote = _remote(b"x" * 100)
    with pytest.raises(remote_files.DownloadCancelled):
        remote_files.download(remote, opener=_Opener(b"x" * 100), cancelled=lambda: True)
    assert list((tmp_path / "downloads").iterdir()) == []


def test_the_runtime_fetches_only_with_permission(tmp_path: Path, https_server) -> None:
    from conftest import Reply

    folder, h5 = _package(tmp_path)
    https_server.route("GET", URL, Reply(200, h5.read_bytes()))
    with open_limelight(folder) as package:
        runtime = LimelightRuntime(package, package.manifest_json())
        try:
            [(source, remote)] = runtime.remote_files()
            assert remote.host == "data.example.org"
            with pytest.raises(RemoteFileNotDownloaded, match="has not been downloaded"):
                runtime.hdf_path(source)

            runtime.allow_download = lambda _remote: False
            with pytest.raises(RemoteFileNotDownloaded):
                runtime.hdf_path(source)

            assert https_server.seen == []
            runtime.allow_download = lambda _remote: True
            path = runtime.hdf_path(source)
            assert path.read_bytes() == h5.read_bytes()
        finally:
            runtime.close()


def test_a_table_of_undownloaded_data_says_so(tmp_path: Path) -> None:
    folder, _ = _package(tmp_path)
    with open_limelight(folder) as package:
        runtime = LimelightRuntime(package, package.manifest_json())
        try:
            preview = runtime.table_preview("rec", None)
        finally:
            runtime.close()
    assert preview.rows == [] and preview.columns == ["v"]
    assert URL in preview.unavailable


def test_verify_leaves_an_undownloaded_file_unchecked_unless_allowed(tmp_path: Path) -> None:
    folder, h5 = _package(tmp_path)
    with open_limelight(folder) as package:
        manifest = package.manifest_json()
        [note] = verify_hdf_sources(package, manifest)
        assert URL in note
        remote_files.download(remote_files.remote_file_of(manifest["sources"][0]), opener=_Opener(h5.read_bytes()))
        assert verify_hdf_sources(package, manifest) == []


def test_signatures_on_a_remote_file_verify_without_downloading_it(tmp_path: Path) -> None:
    private_key, _ = generate_ed25519_keypair()
    folder, _ = _package(tmp_path, signers=[("Tester", private_key)])
    with open_limelight(folder) as package:
        [check] = verify_manifest_signatures(package, package.manifest_json())
    assert check.ok and check.document_path == URL


def test_a_typed_domain_or_url_becomes_a_host() -> None:
    assert remote_files.normalize_host(" Data.Example.org ") == "data.example.org"
    assert remote_files.normalize_host("https://data.example.org/x.h5") == "data.example.org"
    assert remote_files.normalize_host("bücher.example.org") == "xn--bcher-kva.example.org"
    assert remote_files.normalize_host("not a host") is None
    assert remote_files.normalize_host("") is None


def test_host_decisions_are_exact_hosts_and_blocked_wins(tmp_path: Path) -> None:
    assert settings.host_decisions() == {}
    settings.set_host_decision("example.org", ALLOWED)
    assert settings.host_decision("example.org") == ALLOWED
    assert settings.host_decision("data.example.org") is None
    assert settings.host_decision("badexample.org") is None
    # Both lists naming one host (a hand-edited file, say): Blocked wins.
    app = settings.load_app_settings()
    app[settings.DOWNLOAD_HOSTS_KEY] = {"allowed": ["data.example.org"], "blocked": ["data.example.org"]}
    settings.save_app_settings(app)
    assert settings.host_decision("data.example.org") == BLOCKED
    settings.set_host_decision("data.example.org", None)
    assert settings.host_decision("data.example.org") is None


def test_trusted_hosts_migrate_to_allowed_exact_hosts(tmp_path: Path) -> None:
    settings.save_app_settings({"trustedDownloadHosts": ["Example.org.", "files.example.com"], "other": 1})
    assert settings.host_decisions() == {"example.org": ALLOWED, "files.example.com": ALLOWED}
    # A subdomain is no longer covered.
    assert settings.host_decision("data.example.org") is None
    saved = settings.load_app_settings()
    assert "trustedDownloadHosts" not in saved and saved["other"] == 1


def test_a_settings_file_in_the_working_folder_cannot_allow_a_host(tmp_path: Path) -> None:
    (tmp_path / settings.SETTINGS_FILENAME).write_text(
        '{"trustedDownloadHosts": ["data.example.org"], "downloadHosts": {"allowed": ["data.example.org"]}}'
    )
    assert settings.host_decision("data.example.org") is None
    assert settings.host_decisions() == {}


def test_sizes_read_as_people_say_them() -> None:
    assert remote_files.format_size(999) == "999 bytes"
    assert remote_files.format_size(1_500_000) == "1.5 MB"
    assert remote_files.format_size(2_000_000_000) == "2.0 GB"
    assert remote_files.format_size(5_000_000_000_000) == "5000.0 GB"


@pytest.fixture
def qt_app():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    import limelight.qt_app  # noqa: F401
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


def test_the_settings_dialog_lists_and_forgets_hosts(qt_app) -> None:
    from limelight.qt_downloads import SettingsDialog

    settings.set_host_decision("a.example.org", ALLOWED)
    settings.set_host_decision("b.example.org", BLOCKED)
    dialog = SettingsDialog()
    texts = [dialog.host_list.item(row).text() for row in range(dialog.host_list.count())]
    assert texts == ["a.example.org · Allowed", "b.example.org · Blocked"]
    dialog.host_list.setCurrentRow(1)
    dialog.forget_selected_host()
    assert settings.host_decisions() == {"a.example.org": ALLOWED}


def test_the_settings_dialog_deletes_downloaded_data(qt_app, tmp_path: Path) -> None:
    body = b"x" * 100
    remote = _remote(body)
    remote_files.download(remote, opener=_Opener(body))
    other = tmp_path / "downloads" / "keep-me.txt"
    other.write_text("not ours")
    assert remote_files.delete_downloaded_data() == 1
    assert remote.cached_path() is None and other.exists()


def test_an_allowed_host_is_downloaded_without_asking(qt_app, tmp_path: Path, monkeypatch, https_server) -> None:
    from conftest import Reply

    from limelight import qt_downloads

    folder, h5 = _package(tmp_path)
    settings.set_host_decision("data.example.org", ALLOWED)
    https_server.route("GET", URL, Reply(200, h5.read_bytes()))
    monkeypatch.setattr(qt_downloads.ConsentDialog, "exec", lambda self: pytest.fail("asked"))
    with open_limelight(folder) as package:
        runtime = LimelightRuntime(package, package.manifest_json())
        try:
            bar = qt_downloads.RemoteDataBar()
            assert qt_downloads.fetch_remote_files(None, runtime, bar=bar) is True
            [(_, remote)] = runtime.remote_files()
            assert remote.cached_path() is not None
            assert "Data from data.<b>example.org</b>" in bar.text()
        finally:
            runtime.close()


def test_an_allowed_parent_domain_does_not_cover_a_subdomain(qt_app, tmp_path: Path, monkeypatch) -> None:
    from PySide6.QtWidgets import QDialog

    from limelight import qt_downloads

    folder, _ = _package(tmp_path)
    settings.set_host_decision("example.org", ALLOWED)
    asked: list[list[str]] = []

    def decline(dialog) -> int:
        asked.append(sorted(dialog.remember_boxes))
        return QDialog.DialogCode.Rejected

    monkeypatch.setattr(qt_downloads.ConsentDialog, "exec", decline)
    with open_limelight(folder) as package:
        runtime = LimelightRuntime(package, package.manifest_json())
        try:
            assert qt_downloads.fetch_remote_files(None, runtime) is False
        finally:
            runtime.close()
    assert asked == [["data.example.org"]]


def test_the_reader_is_asked_and_can_remember_allow(qt_app, tmp_path: Path, monkeypatch, https_server) -> None:
    from conftest import Reply
    from PySide6.QtWidgets import QDialog

    from limelight import qt_downloads

    folder, h5 = _package(tmp_path)
    https_server.route("GET", URL, Reply(200, h5.read_bytes()))
    seen_before_consent: list[int] = []

    def accept_and_remember(dialog) -> int:
        seen_before_consent.append(len(https_server.seen))
        for box in dialog.remember_boxes.values():
            box.setChecked(True)
        return QDialog.DialogCode.Accepted

    monkeypatch.setattr(qt_downloads.ConsentDialog, "exec", accept_and_remember)
    with open_limelight(folder) as package:
        runtime = LimelightRuntime(package, package.manifest_json())
        try:
            qt_downloads.fetch_remote_files(None, runtime)
            [(_, remote)] = runtime.remote_files()
            assert remote.cached_path() is not None
        finally:
            runtime.close()
    assert seen_before_consent == [0]  # nothing left the machine before the answer
    assert settings.host_decisions() == {"data.example.org": ALLOWED}


def test_dont_download_and_remember_blocks_the_host(qt_app, tmp_path: Path, monkeypatch, https_server) -> None:
    from PySide6.QtWidgets import QDialog

    from limelight import qt_downloads

    folder, _ = _package(tmp_path)

    def decline_and_remember(dialog) -> int:
        for box in dialog.remember_boxes.values():
            box.setChecked(True)
        return QDialog.DialogCode.Rejected

    monkeypatch.setattr(qt_downloads.ConsentDialog, "exec", decline_and_remember)
    with open_limelight(folder) as package:
        runtime = LimelightRuntime(package, package.manifest_json())
        try:
            qt_downloads.fetch_remote_files(None, runtime)
            # Blocked now: not asked, and nothing contacted.
            monkeypatch.setattr(qt_downloads.ConsentDialog, "exec", lambda self: pytest.fail("asked"))
            bar = qt_downloads.RemoteDataBar()
            assert qt_downloads.fetch_remote_files(None, runtime, bar=bar) is False
            assert "Blocked" in bar.text()
        finally:
            runtime.close()
    assert https_server.seen == []
    assert settings.host_decisions() == {"data.example.org": BLOCKED}


def test_a_declined_download_fetches_nothing(qt_app, tmp_path: Path, monkeypatch, https_server) -> None:
    from PySide6.QtWidgets import QDialog

    from limelight import qt_downloads

    folder, _ = _package(tmp_path)
    monkeypatch.setattr(qt_downloads.ConsentDialog, "exec", lambda self: QDialog.DialogCode.Rejected)
    with open_limelight(folder) as package:
        runtime = LimelightRuntime(package, package.manifest_json())
        try:
            qt_downloads.fetch_remote_files(None, runtime)
            [(_, remote)] = runtime.remote_files()
            assert remote.cached_path() is None
        finally:
            runtime.close()
    assert https_server.seen == []
    assert settings.host_decisions() == {}


def test_the_window_is_shown_before_anything_is_fetched(qt_app, tmp_path: Path, monkeypatch, https_server) -> None:
    """RV-1: the first open, and Reload, show the window (and its bar) before asking or fetching."""
    import time

    from conftest import Reply
    from PySide6.QtWidgets import QDialog

    from limelight import qt_downloads
    from limelight.qt_app import LimelightWindow

    folder, h5 = _package(tmp_path)
    https_server.route("GET", URL, Reply(200, h5.read_bytes()))
    visible_when_asked: list[tuple[bool, bool]] = []

    def accept(dialog) -> int:
        window = dialog.parent()
        visible_when_asked.append((window.isVisible(), window._remote_bar_toolbar.isVisible()))
        return QDialog.DialogCode.Accepted

    monkeypatch.setattr(qt_downloads.ConsentDialog, "exec", accept)
    with open_limelight(folder) as package:
        window = LimelightWindow(package, package.manifest_json())
        try:
            assert visible_when_asked == [] and https_server.seen == []
            window.show()
            deadline = time.monotonic() + 10
            while not visible_when_asked and time.monotonic() < deadline:
                qt_app.processEvents()
            assert visible_when_asked == [(True, True)]
            assert "Data from data.<b>example.org</b>" in window.remote_bar.text()
        finally:
            window.close()
            window.runtime.close()
