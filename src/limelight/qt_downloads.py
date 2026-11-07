"""The app's side of remote hdf files: asking, signing in, downloading, the bar, Settings.

The window is shown first, then `fetch_remote_files` runs (first open, Open
and Reload alike). Files already downloaded need nothing. For the rest, by
host: a Blocked host is never contacted; an Allowed one is fetched; any other
is shown in the consent dialog (§4.1) before any request at all. A host that
answers 401 is signed in to on the GUI thread (§4.2), and that file is tried
once more. The window's bar (§4.3) says where the data comes from for as long
as the package is open. A file that is not fetched leaves its figures and
tables saying so in its place; reloading the package asks again.
"""

from __future__ import annotations

import html
import logging
import threading
from dataclasses import dataclass, field
from typing import Any, Callable

from PySide6.QtCore import QEventLoop, QObject, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QDesktopServices, QFont, QGuiApplication
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from . import remote_auth
from .remote_auth import (
    DeviceAuthorization,
    SignInDenied,
    SignInError,
    SignInExpired,
    SignInPlan,
    Token,
    TokenStore,
    describe_host,
)
from .remote_files import (
    MAX_TRIES,
    DownloadCancelled,
    NeedsSignIn,
    RemoteFile,
    RemoteFileError,
    delete_downloaded_data,
    download_dir,
    fetch,
    format_size,
)
from .settings import ALLOWED, BLOCKED, host_decision, host_decisions, set_host_decision

logger = logging.getLogger("limelight.downloads")

# (source title or id, the file it names)
Wanted = tuple[str, RemoteFile]


# ---------------------------------------------------------------- decisions (no widgets)


@dataclass
class HostPlan:
    """What to do with each host's missing files before anything is fetched."""

    blocked: dict[str, list[Wanted]] = field(default_factory=dict)
    allowed: dict[str, list[Wanted]] = field(default_factory=dict)
    to_ask: dict[str, list[Wanted]] = field(default_factory=dict)


def plan_hosts(wanted: list[Wanted], decision_of: Callable[[str], str | None] = host_decision) -> HostPlan:
    """Sort missing files by their host's remembered decision; Blocked beats Allowed (settings)."""
    plan = HostPlan()
    for item in wanted:
        host = item[1].host
        decision = decision_of(host)
        bucket = plan.blocked if decision == BLOCKED else plan.allowed if decision == ALLOWED else plan.to_ask
        bucket.setdefault(host, []).append(item)
    return plan


def consent_decisions(download: bool, remembered_hosts: list[str]) -> dict[str, str]:
    """What the consent dialog's answer remembers: only for the hosts ticked, exact hosts only."""
    decision = ALLOWED if download else BLOCKED
    return {host: decision for host in remembered_hosts}


def host_label_html(host: str) -> str:
    """The exact host, its registrable domain in bold; an IDN as punycode with its Unicode form after."""
    ascii_form, unicode_form, domain = describe_host(host)
    if domain and ascii_form.endswith(domain):
        head = ascii_form[: len(ascii_form) - len(domain)]
        text = f"{html.escape(head)}<b>{html.escape(domain)}</b>"
    else:
        text = f"<b>{html.escape(ascii_form)}</b>"
    if unicode_form:
        text += f" ({html.escape(unicode_form)})"
    return text


# ---------------------------------------------------------------- running work off the GUI thread


class _Finished(QObject):
    finished = Signal(object)


def run_off_gui_thread(work: Callable[[], Any]) -> Any:
    """Run `work` in a thread while the GUI keeps painting; return its result or raise its error."""
    outcome: dict[str, Any] = {}
    loop = QEventLoop()
    signals = _Finished()
    signals.finished.connect(lambda _: loop.quit(), Qt.ConnectionType.QueuedConnection)

    def run() -> None:
        try:
            outcome["value"] = work()
        except BaseException as error:  # handed to the GUI thread
            outcome["error"] = error
        signals.finished.emit(None)

    thread = threading.Thread(target=run, name="limelight-remote", daemon=True)
    thread.start()
    loop.exec()
    thread.join()
    if "error" in outcome:
        raise outcome["error"]
    return outcome.get("value")


# ---------------------------------------------------------------- §4.1 consent


class ConsentDialog(QDialog):
    """Before anything leaves the machine: one row per host, the files behind a toggle."""

    def __init__(self, parent: QWidget | None, by_host: dict[str, list[Wanted]]) -> None:
        super().__init__(parent)
        self.setWindowTitle("Download Data?")
        self.setModal(True)
        layout = QVBoxLayout(self)

        heading = QLabel("This package loads its data from the internet")
        heading_font = heading.font()
        heading_font.setBold(True)
        heading.setFont(heading_font)
        layout.addWidget(heading)

        self.remember_boxes: dict[str, QCheckBox] = {}
        self.host_labels: dict[str, QLabel] = {}
        for host in sorted(by_host):
            wanted = by_host[host]
            row = QFrame()
            row.setFrameShape(QFrame.Shape.StyledPanel)
            row_layout = QVBoxLayout(row)

            host_label = QLabel(host_label_html(host))
            host_label.setTextFormat(Qt.TextFormat.RichText)
            font = host_label.font()
            font.setPointSizeF(font.pointSizeF() * 1.5)
            host_label.setFont(font)
            host_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            self.host_labels[host] = host_label
            row_layout.addWidget(host_label)

            total = sum(remote.size_bytes for _, remote in wanted)
            noun = "file" if len(wanted) == 1 else "files"
            summary = QHBoxLayout()
            summary.addWidget(QLabel(f"{len(wanted)} {noun} · {format_size(total)}"))
            summary.addStretch(1)
            toggle = QPushButton("▸ Show files and URLs")
            toggle.setAutoDefault(False)
            toggle.setFlat(True)
            summary.addWidget(toggle)
            row_layout.addLayout(summary)

            tree = QTreeWidget()
            tree.setHeaderLabels(["Source", "Size", "URL"])
            tree.setRootIsDecorated(False)
            for title, remote in wanted:
                item = QTreeWidgetItem([title, format_size(remote.size_bytes), remote.url])
                item.setToolTip(2, remote.url)
                tree.addTopLevelItem(item)
            for column in range(3):
                tree.resizeColumnToContents(column)
            tree.setVisible(False)
            row_layout.addWidget(tree)

            def flip(_checked: bool = False, tree: QTreeWidget = tree, toggle: QPushButton = toggle) -> None:
                tree.setVisible(not tree.isVisible())
                toggle.setText(("▾" if tree.isVisible() else "▸") + " Show files and URLs")

            toggle.clicked.connect(flip)
            row_layout.addWidget(QLabel("You may be asked to sign in to this site."))
            layout.addWidget(row)

        layout.addWidget(QLabel("Nothing has been downloaded yet."))
        for host in sorted(by_host):
            box = QCheckBox(f"Remember my decision for {describe_host(host)[0]}")
            self.remember_boxes[host] = box
            layout.addWidget(box)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        self.dont_button = QPushButton("Don't download")
        self.download_button = QPushButton("Download")
        # No default button: Enter does nothing, the reader must click.
        for button in (self.dont_button, self.download_button):
            button.setAutoDefault(False)
            button.setDefault(False)
            buttons.addWidget(button)
        self.dont_button.clicked.connect(self.reject)
        self.download_button.clicked.connect(self.accept)
        layout.addLayout(buttons)
        self.resize(560, self.sizeHint().height())

    def remembered_hosts(self) -> list[str]:
        return [host for host, box in self.remember_boxes.items() if box.isChecked()]

    def decisions(self, accepted: bool) -> dict[str, str]:
        return consent_decisions(accepted, self.remembered_hosts())


# ---------------------------------------------------------------- §4.2 sign-in


class _PollSignals(QObject):
    finished = Signal(object)


def open_in_browser(url: str) -> None:
    QDesktopServices.openUrl(QUrl(url))


class SignInDialog(QDialog):
    """Shows the code and the link, opens the browser, and polls off the GUI thread."""

    def __init__(
        self,
        parent: QWidget | None,
        plan: SignInPlan,
        authorization: DeviceAuthorization,
        *,
        keychain_available: bool,
        https_handler: Any = None,
        open_url: Callable[[str], None] | None = None,
        poll: Callable[..., Token] | None = None,
    ) -> None:
        super().__init__(parent)
        uri = authorization.verification_uri
        # Checked again here: the browser goes only to an https page on the
        # sign-in server's registrable domain, and never to _complete.
        if not remote_auth.is_https(uri) or not remote_auth.same_registrable_domain(
            remote_auth.origin(uri)[1], remote_auth.origin(plan.issuer)[1]
        ):
            raise remote_auth.SignInRefused(f"{plan.host} asks you to sign in somewhere Limelight does not allow")
        self.plan = plan
        self.authorization = authorization
        self.token: Token | None = None
        self.error: SignInError | None = None
        self._https_handler = https_handler
        self._open_url = open_url or open_in_browser
        self._poll = poll or remote_auth.poll_for_token
        self._cancel = threading.Event()
        self._started = False
        self._signals = _PollSignals()
        self._signals.finished.connect(self._on_finished, Qt.ConnectionType.QueuedConnection)

        self.setWindowTitle("Sign In")
        self.setModal(True)
        layout = QVBoxLayout(self)
        heading = QLabel(f"Sign in to {host_label_html(plan.host)}")
        heading.setTextFormat(Qt.TextFormat.RichText)
        font = heading.font()
        font.setPointSizeF(font.pointSizeF() * 1.3)
        heading.setFont(font)
        layout.addWidget(heading)

        layout.addWidget(QLabel("Limelight will open your browser at"))
        self.uri_label = QLabel(uri)
        self.uri_label.setTextFormat(Qt.TextFormat.PlainText)
        self.uri_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(self.uri_label)
        layout.addWidget(QLabel("Sign in there and type this code:"))
        self.code_label = QLabel(authorization.user_code)
        self.code_label.setTextFormat(Qt.TextFormat.PlainText)
        self.code_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.code_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        code_font = QFont("monospace")
        code_font.setStyleHint(QFont.StyleHint.Monospace)
        code_font.setPointSizeF(self.font().pointSizeF() * 2)
        code_font.setBold(True)
        self.code_label.setFont(code_font)
        layout.addWidget(self.code_label)
        warning = QLabel("Only type it on that site. Limelight will never show a code you did not ask for.")
        warning.setWordWrap(True)
        layout.addWidget(warning)

        self.remember_box = QCheckBox("Remember this sign-in on this computer")
        if not keychain_available:
            self.remember_box.setEnabled(False)
            self.remember_box.setToolTip("There is no keychain on this computer")
            note = QLabel("There is no keychain on this computer, so this sign-in lasts until Limelight quits.")
            note.setWordWrap(True)
            layout.addWidget(self.remember_box)
            layout.addWidget(note)
        else:
            layout.addWidget(self.remember_box)

        row = QHBoxLayout()
        self.status_label = QLabel("Waiting for you to approve…")
        row.addWidget(self.status_label, stretch=1)
        self.copy_button = QPushButton("Copy link")
        self.cancel_button = QPushButton("Cancel")
        for button in (self.copy_button, self.cancel_button):
            button.setAutoDefault(False)
            button.setDefault(False)
            row.addWidget(button)
        self.copy_button.clicked.connect(lambda: QGuiApplication.clipboard().setText(uri))
        self.cancel_button.clicked.connect(self.reject)
        layout.addLayout(row)

    def showEvent(self, event: Any) -> None:
        super().showEvent(event)
        if not self._started:
            self._started = True
            QTimer.singleShot(0, self, self.start)

    def start(self) -> None:
        """Open the browser and start polling (called once the dialog is up)."""
        self._open_url(self.authorization.verification_uri)

        def poll() -> None:
            try:
                result: Any = self._poll(
                    self.plan, self.authorization, https_handler=self._https_handler, wait=self._cancel.wait
                )
            except SignInError as error:
                result = error
            except Exception as error:  # a bug or a network surprise: never a token in it
                result = SignInError(f"The sign-in failed ({type(error).__name__})")
            self._signals.finished.emit(result)

        threading.Thread(target=poll, name="limelight-sign-in", daemon=True).start()

    def _on_finished(self, result: Any) -> None:
        if self._cancel.is_set():
            return
        if isinstance(result, Token):
            self.token = result
            self.accept()
            return
        self.error = result
        if isinstance(result, SignInDenied):
            self.status_label.setText("Denied")
        elif isinstance(result, SignInExpired):
            self.status_label.setText("Expired: start again")
        else:
            self.status_label.setText(str(result))
        self.cancel_button.setText("Close")

    def remember(self) -> bool:
        return self.remember_box.isEnabled() and self.remember_box.isChecked()

    def done(self, result: int) -> None:
        if result != QDialog.DialogCode.Accepted:
            self._cancel.set()
        super().done(result)


def sign_in(
    parent: QWidget | None,
    need: NeedsSignIn,
    store: TokenStore,
    *,
    https_handler: Any = None,
    bar: "RemoteDataBar | None" = None,
) -> Token | None:
    """Discovery (§5 rules 2-4) and the §4.2 dialog, on the GUI thread; the token, or None."""
    if bar is not None:
        bar.show_signing_in(need.host)
    try:
        plan = run_off_gui_thread(lambda: remote_auth.discover(need.url, need.challenge, https_handler=https_handler))
        authorization = run_off_gui_thread(
            lambda: remote_auth.start_device_authorization(plan, https_handler=https_handler)
        )
        dialog = SignInDialog(
            parent, plan, authorization, keychain_available=store.keychain_available(), https_handler=https_handler
        )
    except SignInError as error:
        logger.warning("Signing in for %s failed: %s", need.url, error)
        QMessageBox.warning(parent, "Sign In", str(error))
        return None
    if dialog.exec() != QDialog.DialogCode.Accepted or dialog.token is None:
        return None
    token = dialog.token
    remember = dialog.remember()
    if not store.put(token, remember=remember) and remember:
        QMessageBox.information(
            parent,
            "Sign In",
            "The keychain would not keep this sign-in, so it lasts until Limelight quits.",
        )
    return token


# ---------------------------------------------------------------- §4.3 the bar


class RemoteDataBar(QFrame):
    """Says where the open package's data comes from, for as long as it is open."""

    cancel_requested = Signal()
    sign_out_requested = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("remote-data-bar")
        self.setFrameShape(QFrame.Shape.StyledPanel)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 2, 8, 2)
        self.label = QLabel()
        self.label.setTextFormat(Qt.TextFormat.RichText)
        layout.addWidget(self.label, stretch=1)
        self._actions = QHBoxLayout()
        layout.addLayout(self._actions)
        self.cancel_button = QPushButton("Cancel")
        self.cancel_button.setAutoDefault(False)
        self.cancel_button.clicked.connect(self.cancel_requested.emit)
        layout.addWidget(self.cancel_button)
        self.cancel_button.hide()
        self.sign_out_buttons: dict[str, QPushButton] = {}

    def text(self) -> str:
        return self.label.text()

    def _clear_actions(self) -> None:
        for button in self.sign_out_buttons.values():
            button.setParent(None)
            button.deleteLater()
        self.sign_out_buttons = {}

    def show_loading(self, host: str, done: int, total: int) -> None:
        self._clear_actions()
        self.label.setText(f"Loading data from {host_label_html(host)}… {done} of {total} files")
        self.cancel_button.show()

    def show_waiting(self, host: str, seconds: float, attempt: int) -> None:
        self.label.setText(
            f"{host_label_html(host)} is busy: trying again in {seconds:.0f} s (try {attempt + 1} of {MAX_TRIES})"
        )
        self.cancel_button.show()

    def show_signing_in(self, host: str) -> None:
        self.label.setText(f"Signing in to {host_label_html(host)}…")
        self.cancel_button.hide()

    def show_summary(self, entries: list["HostSummary"]) -> None:
        self._clear_actions()
        self.cancel_button.hide()
        parts = []
        for entry in entries:
            text = f"Data from {host_label_html(entry.host)}"
            if entry.token is not None:
                text += " · signed in" + (f" as {html.escape(entry.token.account)}" if entry.token.account else "")
                resource = entry.token.resource
                if resource not in self.sign_out_buttons and not entry.token.from_environment:
                    button = QPushButton("Sign out")
                    button.setAutoDefault(False)
                    button.setToolTip(f"Sign out of {resource}")
                    button.clicked.connect(lambda _=False, resource=resource: self.sign_out_requested.emit(resource))
                    self.sign_out_buttons[resource] = button
                    self._actions.addWidget(button)
            if entry.blocked:
                text += " · Blocked in Settings, not downloaded"
            elif entry.missing:
                text += f" · {entry.missing} not downloaded"
            parts.append(text)
        self.label.setText(" &nbsp;|&nbsp; ".join(parts))


@dataclass
class HostSummary:
    host: str
    missing: int
    blocked: bool
    token: Token | None


def summarize(runtime: Any, store: TokenStore) -> list[HostSummary]:
    by_host: dict[str, list[RemoteFile]] = {}
    for _, remote in runtime.remote_files():
        by_host.setdefault(remote.host, []).append(remote)
    entries = []
    for host, remotes in sorted(by_host.items()):
        token = None
        for remote in remotes:
            token = store.peek(remote.url)
            if token is not None:
                break
        entries.append(
            HostSummary(
                host=host,
                missing=sum(1 for remote in remotes if remote.cached_path() is None),
                blocked=host_decision(host) == BLOCKED,
                token=token,
            )
        )
    return entries


# ---------------------------------------------------------------- the fetch


class _DownloadSignals(QObject):
    # object, not int: a file over 2 GB overflows a Qt int.
    progress = Signal(object, object)
    waiting = Signal(object, object, object)
    finished = Signal(object)


def fetch_remote_files(
    parent: QWidget | None,
    runtime: Any,
    *,
    bar: RemoteDataBar | None = None,
    store: TokenStore | None = None,
    https_handler: Any = None,
) -> bool:
    """Download what the open package needs, asking first; True if anything new was downloaded."""
    store = store if store is not None else remote_auth.default_store()
    missing: dict[str, Wanted] = {}
    for source, remote in runtime.remote_files():
        if remote.cached_path() is None and remote.sha256 not in missing:
            missing[remote.sha256] = (source.get("title") or source["id"], remote)
    if not missing:
        if bar is not None:
            bar.show_summary(summarize(runtime, store))
        return False

    plan = plan_hosts(list(missing.values()))
    for host in plan.blocked:
        logger.info("Not contacting %s: it is Blocked", host)
    approved = [item for items in plan.allowed.values() for item in items]
    if plan.to_ask:
        dialog = ConsentDialog(parent, plan.to_ask)
        accepted = dialog.exec() == QDialog.DialogCode.Accepted
        for host, decision in dialog.decisions(accepted).items():
            set_host_decision(host, decision)
        if accepted:
            approved.extend(item for items in plan.to_ask.values() for item in items)
        else:
            logger.info("Reader declined to download from %s", ", ".join(sorted(plan.to_ask)))

    downloaded = False
    failures: list[str] = []
    if approved:
        remotes = [remote for _, remote in approved]
        needs, errors, got = _download_in_background(remotes, store, bar, https_handler)
        downloaded = downloaded or got
        failures.extend(errors)
        # Sign in on the GUI thread, once per host, then try those files once more.
        retry: list[RemoteFile] = []
        signed_in_hosts: dict[str, bool] = {}
        for remote, need in needs:
            if need.host not in signed_in_hosts:
                signed_in_hosts[need.host] = sign_in(parent, need, store, https_handler=https_handler, bar=bar) is not None
            if signed_in_hosts[need.host]:
                retry.append(remote)
            else:
                failures.append(str(need))
        if retry:
            needs, errors, got = _download_in_background(retry, store, bar, https_handler)
            downloaded = downloaded or got
            failures.extend(errors)
            failures.extend(str(need) for _, need in needs)

    if bar is not None:
        bar.show_summary(summarize(runtime, store))
    if failures:
        QMessageBox.warning(parent, "Downloading Data", "Some data could not be downloaded:\n\n" + "\n".join(failures))
    return downloaded


def _download_in_background(
    remotes: list[RemoteFile], store: TokenStore, bar: RemoteDataBar | None, https_handler: Any
) -> tuple[list[tuple[RemoteFile, NeedsSignIn]], list[str], bool]:
    """Fetch `remotes` in a worker thread; (needing a sign-in, failures, anything downloaded)."""
    signals = _DownloadSignals()
    cancel = threading.Event()
    needs: list[tuple[RemoteFile, NeedsSignIn]] = []
    failures: list[str] = []
    got: list[bool] = []
    loop = QEventLoop()

    def on_progress(index: int, host: str) -> None:
        if bar is not None:
            bar.show_loading(host, index, len(remotes))

    def on_waiting(host: str, seconds: float, attempt: int) -> None:
        if bar is not None:
            bar.show_waiting(host, seconds, attempt)

    def work() -> None:
        for index, remote in enumerate(remotes):
            if cancel.is_set():
                break
            signals.progress.emit(index + 1, remote.host)
            try:
                fetch(
                    remote,
                    tokens=store,
                    cancelled=cancel.is_set,
                    on_wait=lambda seconds, attempt, host=remote.host: signals.waiting.emit(host, seconds, attempt),
                    https_handler=https_handler,
                )
                got.append(True)
            except DownloadCancelled:
                break
            except NeedsSignIn as need:
                needs.append((remote, need))
            except (RemoteFileError, OSError) as error:
                logger.warning("Download of %s failed: %s", remote.url, error)
                failures.append(str(error))
            except Exception as error:
                logger.warning("Download of %s failed: %s", remote.url, type(error).__name__)
                failures.append(f"Could not download {remote.url} ({type(error).__name__})")
        signals.finished.emit(None)

    signals.progress.connect(on_progress)
    signals.waiting.connect(on_waiting)
    signals.finished.connect(lambda _: loop.quit(), Qt.ConnectionType.QueuedConnection)
    connection = bar.cancel_requested.connect(cancel.set) if bar is not None else None
    thread = threading.Thread(target=work, name="limelight-download", daemon=True)
    thread.start()
    loop.exec()
    thread.join()
    if bar is not None and connection is not None:
        bar.cancel_requested.disconnect(connection)
    return needs, failures, bool(got)


# ---------------------------------------------------------------- §4.4 Settings


class SettingsDialog(QDialog):
    """Settings › Data from the internet: remembered hosts, sign-ins, the download cache."""

    def __init__(
        self, parent: QWidget | None = None, *, store: TokenStore | None = None, https_handler: Any = None
    ) -> None:
        super().__init__(parent)
        self.store = store if store is not None else remote_auth.default_store()
        self._https_handler = https_handler
        self.setWindowTitle("Settings")
        layout = QVBoxLayout(self)

        group = QGroupBox("Data from the internet")
        group_layout = QVBoxLayout(group)
        intro = QLabel(
            "A package can name data on the internet rather than hold it. Limelight asks before "
            "contacting a site, unless you told it to remember your decision for that exact site:"
        )
        intro.setWordWrap(True)
        group_layout.addWidget(intro)

        self.host_list = QListWidget()
        hosts_row = QHBoxLayout()
        hosts_row.addWidget(self.host_list, stretch=1)
        self.forget_button = QPushButton("Forget")
        self.forget_button.setAutoDefault(False)
        self.forget_button.clicked.connect(self.forget_selected_host)
        column = QVBoxLayout()
        column.addWidget(self.forget_button)
        column.addStretch(1)
        hosts_row.addLayout(column)
        group_layout.addLayout(hosts_row)

        group_layout.addWidget(QLabel("Sign-ins:"))
        self.sign_in_list = QListWidget()
        sign_in_row = QHBoxLayout()
        sign_in_row.addWidget(self.sign_in_list, stretch=1)
        self.sign_out_button = QPushButton("Sign out")
        self.sign_out_button.setAutoDefault(False)
        self.sign_out_button.clicked.connect(self.sign_out_selected)
        column = QVBoxLayout()
        column.addWidget(self.sign_out_button)
        column.addStretch(1)
        sign_in_row.addLayout(column)
        group_layout.addLayout(sign_in_row)

        cache_row = QHBoxLayout()
        location = QLabel(f"Downloads are kept in {download_dir()}")
        location.setWordWrap(True)
        location.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        cache_row.addWidget(location, stretch=1)
        self.delete_button = QPushButton("Delete downloaded data")
        self.delete_button.setAutoDefault(False)
        self.delete_button.clicked.connect(self._confirm_delete_downloads)
        cache_row.addWidget(self.delete_button)
        group_layout.addLayout(cache_row)
        layout.addWidget(group)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        buttons.accepted.connect(self.accept)
        layout.addWidget(buttons)

        self.host_list.currentRowChanged.connect(lambda row: self.forget_button.setEnabled(row >= 0))
        self.sign_in_list.currentRowChanged.connect(lambda row: self.sign_out_button.setEnabled(row >= 0))
        self.refresh()
        self.resize(560, self.sizeHint().height())

    def refresh(self) -> None:
        self.host_list.clear()
        for host, decision in host_decisions().items():
            item = QListWidgetItem(f"{host} · {'Allowed' if decision == ALLOWED else 'Blocked'}")
            item.setData(Qt.ItemDataRole.UserRole, host)
            self.host_list.addItem(item)
        self.sign_in_list.clear()
        for token in self.store.sign_ins():
            who = f" · signed in as {token.account}" if token.account else " · signed in"
            item = QListWidgetItem(token.resource + who)
            item.setData(Qt.ItemDataRole.UserRole, token.resource)
            self.sign_in_list.addItem(item)
        self.forget_button.setEnabled(False)
        self.sign_out_button.setEnabled(False)

    def hosts(self) -> dict[str, str]:
        return host_decisions()

    def forget_selected_host(self) -> None:
        item = self.host_list.currentItem()
        if item is not None:
            set_host_decision(item.data(Qt.ItemDataRole.UserRole), None)
            self.refresh()

    def sign_out_selected(self) -> None:
        item = self.sign_in_list.currentItem()
        if item is None:
            return
        resource = item.data(Qt.ItemDataRole.UserRole)
        try:
            run_off_gui_thread(lambda: self.store.sign_out(resource, https_handler=self._https_handler))
        except SignInError as error:
            QMessageBox.warning(self, "Sign Out", str(error))
        self.refresh()

    def _confirm_delete_downloads(self) -> None:
        answer = QMessageBox.question(
            self,
            "Delete Downloaded Data",
            f"Delete everything Limelight has downloaded, in {download_dir()}? Packages that need it will "
            "ask to download it again.",
        )
        if answer == QMessageBox.StandardButton.Yes:
            delete_downloaded_data()
