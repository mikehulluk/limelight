"""The app's side of remote hdf files: asking, downloading, and the Settings dialog.

When a package that names data by URL is opened, the window calls
`fetch_remote_files` before it draws anything. Files already downloaded, and
files from a host the reader trusts, need no question; for the rest the
reader is shown each URL and its size, and asked. A file they decline is not
fetched, and the figures and tables drawn from it say so in its place;
reloading the package asks again.
"""

from __future__ import annotations

import logging
import threading
from typing import Any

from PySide6.QtCore import QEventLoop, QObject, Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QGroupBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QListWidget,
    QMessageBox,
    QProgressDialog,
    QPushButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from .remote_files import (
    DownloadCancelled,
    RemoteFile,
    RemoteFileError,
    download,
    download_dir,
    format_size,
    host_is_trusted,
    normalize_host,
)
from .settings import (
    TRUSTED_DOWNLOAD_HOSTS_KEY,
    add_trusted_download_host,
    load_app_settings,
    set_trusted_download_hosts,
    trusted_download_hosts,
)

logger = logging.getLogger("limelight.downloads")

# (source title or id, the file it names)
Wanted = tuple[str, RemoteFile]


def fetch_remote_files(parent: QWidget | None, runtime: Any) -> None:
    """Download what the open package needs from the web, asking the reader first."""
    missing: dict[str, Wanted] = {}
    for source, remote in runtime.remote_files():
        if remote.cached_path() is None and remote.sha256 not in missing:
            missing[remote.sha256] = (source.get("title") or source["id"], remote)
    if not missing:
        return

    trusted_hosts = trusted_download_hosts()
    approved = [wanted for wanted in missing.values() if host_is_trusted(wanted[1].host, trusted_hosts)]
    to_ask = [wanted for wanted in missing.values() if not host_is_trusted(wanted[1].host, trusted_hosts)]
    if to_ask:
        dialog = DownloadPermissionDialog(parent, to_ask)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            for host in dialog.hosts_to_always_allow():
                add_trusted_download_host(host)
            approved.extend(to_ask)
        else:
            logger.info("Reader declined to download %d remote file(s)", len(to_ask))
    if approved:
        _download_with_progress(parent, [remote for _, remote in approved])


class DownloadPermissionDialog(QDialog):
    """Lists the files a package would download, and asks whether it may."""

    def __init__(self, parent: QWidget | None, wanted: list[Wanted]) -> None:
        super().__init__(parent)
        self.setWindowTitle("Download Data?")
        layout = QVBoxLayout(self)

        total = sum(remote.size_bytes for _, remote in wanted)
        noun = "file" if len(wanted) == 1 else "files"
        intro = QLabel(
            f"Some of this document's data is not in the package. Limelight can download "
            f"{len(wanted)} {noun} ({format_size(total)}) from the web:"
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)

        tree = QTreeWidget()
        tree.setHeaderLabels(["Source", "Size", "URL"])
        tree.setRootIsDecorated(False)
        for title, remote in wanted:
            item = QTreeWidgetItem([title, format_size(remote.size_bytes), remote.url])
            item.setToolTip(2, remote.url)
            tree.addTopLevelItem(item)
        for column in range(3):
            tree.resizeColumnToContents(column)
        # As tall as its rows, up to eight, rather than a mostly empty box.
        rows_shown = min(len(wanted), 8)
        tree.setMaximumHeight(
            tree.header().sizeHint().height() + rows_shown * max(tree.sizeHintForRow(0), 1) + 2 * tree.frameWidth() + 4
        )
        layout.addWidget(tree)

        self._host_boxes: list[tuple[str, QCheckBox]] = []
        for host in sorted({remote.host for _, remote in wanted}):
            box = QCheckBox(f"Always allow downloads from {host}")
            self._host_boxes.append((host, box))
            layout.addWidget(box)

        note = QLabel(
            "Each file is checked against the SHA-256 the package gives for it. If you don't "
            "download, the figures drawn from it say so; reload the package to be asked again."
        )
        note.setWordWrap(True)
        note.setStyleSheet("color: palette(placeholder-text);")
        layout.addWidget(note)

        buttons = QDialogButtonBox()
        download_button = buttons.addButton("Download", QDialogButtonBox.ButtonRole.AcceptRole)
        buttons.addButton("Don't Download", QDialogButtonBox.ButtonRole.RejectRole)
        download_button.setDefault(True)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.resize(640, self.sizeHint().height())

    def hosts_to_always_allow(self) -> list[str]:
        return [host for host, box in self._host_boxes if box.isChecked()]


class _DownloadSignals(QObject):
    # object, not int: a file over 2 GB overflows a Qt int.
    progress = Signal(object, object)
    finished = Signal(object)


def _download_with_progress(parent: QWidget | None, remotes: list[RemoteFile]) -> None:
    total = sum(remote.size_bytes for remote in remotes) or 1
    dialog = QProgressDialog("Downloading data...", "Cancel", 0, 1000, parent)
    dialog.setWindowTitle("Downloading Data")
    dialog.setWindowModality(Qt.WindowModality.WindowModal)
    dialog.setMinimumDuration(300)
    dialog.setAutoClose(False)
    dialog.setAutoReset(False)

    signals = _DownloadSignals()
    cancel = threading.Event()
    failures: list[str] = []
    loop = QEventLoop()

    def on_progress(done: int, current_url: str) -> None:
        dialog.setValue(int(1000 * done / total))
        dialog.setLabelText(f"Downloading {current_url}\n{format_size(done)} of {format_size(total)}")

    def work() -> None:
        before = 0
        for remote in remotes:
            if cancel.is_set():
                break
            try:
                download(
                    remote,
                    progress=lambda done, _size, base=before, url=remote.url: signals.progress.emit(base + done, url),
                    cancelled=cancel.is_set,
                )
            except DownloadCancelled:
                break
            except (RemoteFileError, OSError) as error:
                logger.warning("Download of %s failed: %s", remote.url, error)
                failures.append(str(error))
            before += remote.size_bytes
        signals.finished.emit(None)

    signals.progress.connect(on_progress)
    signals.finished.connect(lambda _: loop.quit())
    dialog.canceled.connect(cancel.set)
    thread = threading.Thread(target=work, name="limelight-download", daemon=True)
    thread.start()
    loop.exec()
    thread.join()
    dialog.close()

    if failures:
        QMessageBox.warning(parent, "Downloading Data", "Some data could not be downloaded:\n\n" + "\n".join(failures))


class SettingsDialog(QDialog):
    """The reader's settings. For now: the hosts data is always downloaded from."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Settings")
        layout = QVBoxLayout(self)

        group = QGroupBox("Downloads")
        group_layout = QVBoxLayout(group)
        intro = QLabel(
            "A package can name data on the web rather than hold it. Limelight asks before "
            "downloading it, except from these domains (a domain covers its subdomains):"
        )
        intro.setWordWrap(True)
        group_layout.addWidget(intro)

        self.host_list = QListWidget()
        self.host_list.addItems(load_app_settings().get(TRUSTED_DOWNLOAD_HOSTS_KEY) or [])
        row = QHBoxLayout()
        row.addWidget(self.host_list, stretch=1)
        buttons_column = QVBoxLayout()
        add_button = QPushButton("Add...")
        add_button.clicked.connect(self._add_host)
        self.remove_button = QPushButton("Remove")
        self.remove_button.clicked.connect(self._remove_host)
        buttons_column.addWidget(add_button)
        buttons_column.addWidget(self.remove_button)
        buttons_column.addStretch(1)
        row.addLayout(buttons_column)
        group_layout.addLayout(row)

        self.host_list.currentRowChanged.connect(lambda index: self.remove_button.setEnabled(index >= 0))
        self.remove_button.setEnabled(False)

        from_file = sorted(set(trusted_download_hosts()) - set(load_app_settings().get(TRUSTED_DOWNLOAD_HOSTS_KEY) or []))
        if from_file:
            extra = QLabel("Also trusted, from limelight-setting.json: " + ", ".join(from_file))
            extra.setWordWrap(True)
            group_layout.addWidget(extra)

        location = QLabel(f"Downloads are kept in {download_dir()}")
        location.setWordWrap(True)
        location.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        group_layout.addWidget(location)
        layout.addWidget(group)

        dialog_buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        dialog_buttons.accepted.connect(self.accept)
        dialog_buttons.rejected.connect(self.reject)
        layout.addWidget(dialog_buttons)
        self.resize(520, self.sizeHint().height())

    def hosts(self) -> list[str]:
        return [self.host_list.item(index).text() for index in range(self.host_list.count())]

    def _add_host(self) -> None:
        text, ok = QInputDialog.getText(self, "Add Domain", "Always allow downloads from (e.g. data.example.org):")
        if not ok:
            return
        host = normalize_host(text)
        if host is None:
            QMessageBox.warning(self, "Add Domain", f"{text!r} is not a domain name.")
            return
        if host not in self.hosts():
            self.host_list.addItem(host)

    def _remove_host(self) -> None:
        row = self.host_list.currentRow()
        if row >= 0:
            self.host_list.takeItem(row)

    def accept(self) -> None:
        set_trusted_download_hosts(self.hosts())
        super().accept()
