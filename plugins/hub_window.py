from __future__ import annotations

import math
import platform
import re
import threading
import webbrowser
from collections.abc import Callable
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any

from hub_service import AccountState, HealthStatus, HubService
from PySide6.QtCore import QByteArray, QObject, QPointF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import (
    QColor,
    QFontDatabase,
    QIcon,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
)
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QDialog,
    QFormLayout,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QToolButton,
    QVBoxLayout,
    QWidget,
)
from service_config import WEBSITE
from service_errors import ClientError, ConsentDenied, LoginRequired, LoginTimeout
from service_models import DEFAULT_CLIENT_NAME
from window_launcher import launch_window
from window_position import show_centered

ACCOUNT_AT_TOP = False
VERSION = "1.0.1"
ICONS = Path(__file__).parent / "icons"
STYLE = "\nQWidget { background: white; color: #252b38; font-size: 12px; }\nQLabel#title { font-size: 20px; font-weight: 700; }\nQLabel#muted { color: #677083; }\nQPushButton { border: 1px solid #dfe3eb; border-radius: 5px; padding: 6px 10px; }\nQPushButton:hover { background: #f3f1f8; border-color: #b9a8d0; }\nQPushButton#primary { background: #472e76; color: white; border-color: #472e76; }\nQPushButton#primary:hover { background: #3a2561; }\nQPushButton#quiet { border: none; color: #677083; padding: 3px; }\nQPushButton#forget { background: #fbe9e9; color: #9d3038; border-color: #ebc3c7; }\nQPushButton#forget:hover { background: #f5d7da; border-color: #ce929a; }\nQPushButton#forget:pressed { background: #efc7cc; }\nQPushButton:disabled, QPushButton#primary:disabled {\n    color: #9298a4; background: #f0f1f4; border-color: #dfe3eb; }\nQToolButton, QPushButton#account { background: #f3f1f8; color: #472e76; border: 1px solid transparent;\n    border-radius: 7px; font-weight: 600; }\nQToolButton:hover, QPushButton#account:hover { background: #e9e3f2; border-color: #b9a8d0; }\nQToolButton:pressed, QPushButton#account:pressed { background: #ddd3ec; }\nQToolButton:disabled, QPushButton#account:disabled { background: #f0f1f4; color: #9298a4; }\nQToolButton#workflow { padding: 10px 4px; }\nQPushButton#account { text-align: left; padding: 6px 42px 6px 12px; }\nQComboBox, QLineEdit { border: 1px solid #dfe3eb; border-radius: 4px; padding: 5px; }\nQLineEdit:disabled { background: #f7f8fa; color: #677083; }\n"


def label(value: str, kind: str = "") -> QLabel:
    widget = QLabel(value)
    widget.setObjectName(kind)
    widget.setWordWrap(True)
    widget.setTextFormat(Qt.TextFormat.PlainText)
    return widget


def logo() -> QLabel:
    widget = label("CircuitNinja")
    pixmap = QPixmap(str(ICONS / "logo.png"))
    if not pixmap.isNull():
        ratio = widget.devicePixelRatioF()
        pixmap = pixmap.scaledToWidth(
            round(150 * ratio), Qt.TransformationMode.SmoothTransformation
        )
        pixmap.setDevicePixelRatio(ratio)
        widget.setPixmap(pixmap)
        widget.setFixedSize(150, round(pixmap.height() / ratio) + 2)
    return widget


def dependency_versions() -> str:
    rows = []
    for line in (Path(__file__).parent / "requirements.txt").read_text().splitlines():
        match = re.match("[A-Za-z0-9][A-Za-z0-9._-]*", line.strip())
        if match is None:
            continue
        name = match.group()
        try:
            installed = version(name)
        except PackageNotFoundError:
            installed = "not installed"
        rows.append(f"{name}: {installed}")
    return "\n".join(rows)


def action_icon(kind: str) -> QIcon:
    shapes = {
        "constraints": '<path d="M8 10h16M8 16h16M8 22h16"/><g fill="#472e76" stroke="none"><circle cx="12" cy="10" r="2.5"/><circle cx="21" cy="16" r="2.5"/><circle cx="15" cy="22" r="2.5"/></g>',
        "export": '<path d="M16 7v12m-4-4 4 4 4-4M7 21v4h18v-4"/>',
        "drc": '<circle cx="14" cy="14" r="7"/><path d="m19 19 6 6m-15-11 3 3 5-6"/>',
        "upload": '<path d="M16 22V8m-5 5 5-5 5 5M7 21v4h18v-4"/>',
    }
    if kind in shapes:
        svg = f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32"><g fill="none" stroke="#472e76" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">{shapes[kind]}</g></svg>'
        pixmap = QPixmap(72, 72)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        QSvgRenderer(QByteArray(svg.encode())).render(painter)
        painter.end()
        pixmap.setDevicePixelRatio(2)
        return QIcon(pixmap)
    pixmap = QPixmap(80, 80)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.scale(2, 2)
    painter.setPen(QPen(QColor("#472e76"), 1.8))
    if kind == "about":
        painter.drawEllipse(5, 5, 30, 30)
        painter.drawLine(20, 18, 20, 28)
        painter.drawLine(17, 18, 23, 18)
        painter.drawLine(17, 28, 23, 28)
        painter.setBrush(QColor("#472e76"))
        painter.drawEllipse(QPointF(20, 12), 1, 1)
    else:
        outline = QPainterPath()
        for tooth in range(8):
            for offset, radius in ((-22.5, 11), (-12, 15), (12, 15), (22.5, 11)):
                angle = math.radians(tooth * 45 + offset)
                point = QPointF(
                    20 + radius * math.cos(angle), 20 + radius * math.sin(angle)
                )
                if tooth == 0 and offset == -22.5:
                    outline.moveTo(point)
                else:
                    outline.lineTo(point)
        outline.closeSubpath()
        painter.drawPath(outline)
        painter.drawEllipse(QPointF(20, 20), 5, 5)
    painter.end()
    pixmap.setDevicePixelRatio(2)
    return QIcon(pixmap)


class Task(QObject):
    finished = Signal(object, object)

    def __init__(self, function: Callable[[], object]) -> None:
        super().__init__()
        self.function = function

    def run(self) -> None:
        try:
            value = self.function()
        except Exception as exc:
            self.finished.emit(None, exc)
        else:
            self.finished.emit(value, None)


class HubController(QObject):
    def __init__(
        self,
        app: QApplication,
        context: dict[str, Any],
        service: HubService,
        *,
        launcher: Callable[[Path, Path, dict[str, Any]], None] = launch_window,
        browser: Callable[[str], object] = webbrowser.open,
        account_at_top: bool = ACCOUNT_AT_TOP,
    ) -> None:
        super().__init__()
        (self.app, self.context, self.service) = (app, context, service)
        (self.launcher, self.browser) = (launcher, browser)
        self.state = AccountState()
        self.busy = False
        self.health_busy = False
        self.closing = False
        self.tasks: list[Task] = []
        self.hub = self.window("CircuitNinja · Menu")
        self.account = self.window("CircuitNinja · Account")
        self.about = self.window("CircuitNinja · About")
        self.handoff: QDialog | None = None
        self.hub.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.build_hub(account_at_top)
        self.build_account()
        self.build_about()
        self.health_timer = QTimer(self)
        self.health_timer.setInterval(1000)
        self.health_timer.timeout.connect(self.poll_health)
        self.refresh_account()

    @staticmethod
    def window(title: str) -> QWidget:
        window = QWidget()
        window.setWindowTitle(title)
        window.setWindowIcon(QIcon(str(ICONS / "icon.png")))
        window.setStyleSheet(STYLE)
        return window

    def build_hub(self, account_at_top: bool) -> None:
        self.hub.setWindowFlag(Qt.WindowType.WindowMaximizeButtonHint, False)
        self.hub.setFixedWidth(380)
        layout = QVBoxLayout(self.hub)
        layout.setContentsMargins(16, 14, 16, 12)
        layout.setSpacing(10)
        header = QHBoxLayout()
        header.addWidget(logo())
        header.addStretch()
        self.health_dot = QLabel()
        self.health_dot.setObjectName("serviceHealth")
        self.health_dot.setFixedSize(12, 12)
        self.health_dot.setStyleSheet(
            "QLabel#serviceHealth { background: #a1a7b2; border-radius: 6px; }"
        )
        self.health_dot.setToolTip("Service health has not been checked yet.")
        self.health_dot.setAccessibleName("Service health")
        header.addWidget(self.health_dot)
        layout.addLayout(header)
        self.identity = QPushButton("Log in")
        self.identity.setObjectName("account")
        self.identity.clicked.connect(lambda: self.show_page(self.account))
        row = QHBoxLayout(self.identity)
        row.setContentsMargins(0, 0, 10, 0)
        row.addStretch()
        settings = QLabel()
        settings.setPixmap(action_icon("settings").pixmap(QSize(22, 22)))
        settings.setFixedSize(22, 22)
        settings.setObjectName("accountGear")
        settings.setStyleSheet("QLabel#accountGear { background: transparent; }")
        settings.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        row.addWidget(settings)
        self.about_button = QToolButton()
        self.about_button.setIcon(action_icon("about"))
        self.about_button.setIconSize(QSize(22, 22))
        self.about_button.setFixedWidth(42)
        self.about_button.setToolTip("About CircuitNinja")
        self.about_button.setAccessibleName("About CircuitNinja")
        self.about_button.clicked.connect(lambda: self.show_page(self.about))
        account_row = QHBoxLayout()
        account_row.setSpacing(8)
        account_row.addWidget(self.identity, 1)
        account_row.addWidget(self.about_button)
        self.identity.ensurePolished()
        self.about_button.ensurePolished()
        account_height = max(
            42, self.identity.sizeHint().height(), self.about_button.sizeHint().height()
        )
        self.identity.setFixedHeight(account_height)
        self.about_button.setFixedHeight(account_height)
        if account_at_top:
            layout.addLayout(account_row)
        grid = QGridLayout()
        grid.setSpacing(8)
        self.buttons: dict[str, QToolButton] = {}
        for index, (key, title) in enumerate(
            (
                ("constraints", "Set manufacturing\nrules"),
                ("drc", "Check\nmanufacturability"),
                ("export", "Export manufacturing\nfiles"),
                ("upload", "Upload to\nweb app"),
            )
        ):
            button = QToolButton()
            button.setObjectName("workflow")
            button.setText(title)
            button.setIcon(action_icon(key))
            button.setIconSize(QSize(36, 36))
            button.setAccessibleName(title.replace("\n", " "))
            button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextUnderIcon)
            button.setFixedSize(170, 92)
            button.clicked.connect(
                lambda checked=False, action=key: self.activate(action)
            )
            if key != "constraints":
                button.setToolTip("Not implemented yet.")
            self.buttons[key] = button
            grid.addWidget(button, index // 2, index % 2)
        layout.addLayout(grid)
        if not account_at_top:
            layout.addLayout(account_row)
        self.hub.ensurePolished()
        self.hub.setFixedSize(self.hub.sizeHint())

    def build_account(self) -> None:
        outer = QVBoxLayout(self.account)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.account_content = QWidget()
        scroll.setWidget(self.account_content)
        outer.addWidget(scroll)
        layout = self.account_layout = QVBoxLayout(self.account_content)
        layout.setContentsMargins(18, 16, 18, 14)
        layout.setSpacing(9)
        layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        layout.addWidget(logo())
        layout.addWidget(label("Account", "title"))
        self.account_message = label("")
        layout.addWidget(self.account_message)
        self.profile = label("Not logged in.")
        self.profile.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        layout.addWidget(self.profile)
        self.browser_info = label(
            "Sign-in uses your browser. If it does not come to the front automatically, select it from the taskbar."
        )
        layout.addWidget(self.browser_info)
        form = QFormLayout()
        self.client_name = QLineEdit()
        self.client_name.setPlaceholderText("Optional custom name")
        self.client_name.setToolTip(
            "Optional suffix, such as My Ubuntu. Forget the saved client before changing it."
        )
        client_row = QHBoxLayout()
        self.client_prefix = label(DEFAULT_CLIENT_NAME)
        self.client_prefix.setWordWrap(False)
        client_row.addWidget(self.client_prefix)
        client_row.addWidget(self.client_name, 1)
        self.client_name.textChanged.connect(
            lambda text: self.client_prefix.setText(
                DEFAULT_CLIENT_NAME + (":" if text.strip() else "")
            )
        )
        form.addRow("Client name:", client_row)
        self.client_id = label("No saved client")
        self.client_id.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        form.addRow("Client ID:", self.client_id)
        self.organizations = QComboBox()
        self.organizations.currentIndexChanged.connect(self.organization_changed)
        form.addRow("Organization:", self.organizations)
        layout.addLayout(form)
        self.default_org = label("Default organization: none")
        layout.addWidget(self.default_org)
        self.org_details = label("")
        layout.addWidget(self.org_details)
        buttons = QHBoxLayout()
        self.login_button = QPushButton("Log in")
        self.login_button.setObjectName("primary")
        self.login_button.clicked.connect(lambda: self.account_action("login"))
        self.logout_button = QPushButton("Log out")
        self.logout_button.clicked.connect(self.confirm_logout)
        self.forget_button = QPushButton("Forget client")
        self.forget_button.setObjectName("forget")
        self.forget_button.setToolTip(
            "Remove saved login, client details and all account preferences for this service."
        )
        self.forget_button.clicked.connect(self.confirm_forget_client)
        for button in (self.login_button, self.logout_button, self.forget_button):
            buttons.addWidget(button)
        layout.addLayout(buttons)
        website = QPushButton("Open web account settings")
        website.clicked.connect(
            lambda: self.open_url(self.service.origin + "/app/settings")
        )
        refresh = QPushButton("Refresh profile")
        refresh.clicked.connect(lambda: self.account_action("load"))
        links = QHBoxLayout()
        links.addWidget(refresh)
        links.addWidget(website)
        layout.addLayout(links)
        self.refresh_button = refresh
        footer = QHBoxLayout()
        footer.addStretch()
        close = QPushButton("Close")
        close.clicked.connect(self.account.close)
        footer.addWidget(close)
        layout.addLayout(footer)
        for text in self.account.findChildren(QLabel):
            text.setTextInteractionFlags(
                Qt.TextInteractionFlag.TextSelectableByMouse
                | Qt.TextInteractionFlag.TextSelectableByKeyboard
            )

    def confirm_logout(self) -> None:
        if self.busy or self.state.profile is None:
            return
        answer = QMessageBox.question(
            self.account,
            "CircuitNinja · Log out",
            "Do you want to log out? Your saved client and account preferences will be kept.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        if answer == QMessageBox.StandardButton.Yes:
            self.account_action("logout")

    def confirm_forget_client(self) -> None:
        if self.busy:
            return
        answer = QMessageBox.question(
            self.account,
            "CircuitNinja · Forget client",
            "Forget this client? This removes your saved login, client details and account preferences for this service. You will need to sign in again.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        if answer == QMessageBox.StandardButton.Yes:
            self.account_action("forget")

    def build_about(self) -> None:
        outer = QVBoxLayout(self.about)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        content = QWidget()
        scroll.setWidget(content)
        outer.addWidget(scroll)
        layout = QVBoxLayout(content)
        layout.setContentsMargins(18, 16, 18, 14)
        layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        layout.addWidget(logo())
        layout.addWidget(label("CircuitNinja for KiCad", "title"))
        layout.addWidget(label(f"Version {VERSION}", "muted"))
        layout.addWidget(
            label(
                "Manufacturing tools and a connection to CircuitNinja's PCB fabrication service."
            )
        )
        self.about_website = QLabel(
            f'<a href="{WEBSITE}" style="color: #2563b0;">{WEBSITE}</a>'
        )
        self.about_website.linkActivated.connect(self.open_url)
        layout.addWidget(self.about_website)
        layout.addWidget(label("© 2026 CircuitNinja", "muted"))
        layout.addSpacing(8)
        self.about_dependencies = label(
            f"Python: {platform.python_version()}\n\n{dependency_versions()}"
        )
        self.about_dependencies.setWordWrap(False)
        self.about_dependencies.setFont(
            QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont)
        )
        self.about_dependencies.setAccessibleName("Python and module versions")
        layout.addWidget(self.about_dependencies)
        for text in self.about.findChildren(QLabel):
            text.setTextInteractionFlags(
                Qt.TextInteractionFlag.TextSelectableByMouse
                | Qt.TextInteractionFlag.TextSelectableByKeyboard
                | Qt.TextInteractionFlag.LinksAccessibleByMouse
                | Qt.TextInteractionFlag.LinksAccessibleByKeyboard
            )
        content.ensurePolished()
        available = self.about.screen().availableGeometry()
        width = min(
            max(380, self.about_dependencies.sizeHint().width() + 36),
            available.width() - 40,
        )
        height = max(layout.totalHeightForWidth(width), layout.minimumSize().height())
        self.about.resize(width, min(height, available.height() - 80))

    def show_page(self, page: QWidget) -> None:
        self.health_timer.stop()
        show_centered(page)
        for window in (self.hub, self.account, self.about):
            if window is not page:
                window.hide()
        self.focus(page)

    @staticmethod
    def focus(window: QWidget) -> None:
        if window.isMinimized():
            window.showNormal()
        window.raise_()
        window.activateWindow()
        if handle := window.windowHandle():
            handle.requestActivate()

    def focus_current(self) -> None:
        for window in (self.handoff, self.account, self.about, self.hub):
            if window is not None and window.isVisible():
                self.focus(window)
                return

    def show_hub(self) -> None:
        self.show_page(self.hub)
        self.hub.setFocus(Qt.FocusReason.OtherFocusReason)
        self.health_timer.start()
        self.poll_health()

    def background(
        self,
        function: Callable[[], object],
        finished: Callable[[object, BaseException | None], None],
    ) -> None:
        task = Task(function)
        self.tasks.append(task)

        def complete(value: object, error: BaseException | None) -> None:
            self.tasks.remove(task)
            if not self.closing:
                finished(value, error)

        task.finished.connect(complete, Qt.ConnectionType.QueuedConnection)
        threading.Thread(target=task.run, daemon=True).start()

    def poll_health(self) -> None:
        if not self.hub.isVisible() or self.health_busy or self.closing:
            return
        self.health_busy = True

        def complete(value: object, error: BaseException | None) -> None:
            self.health_busy = False
            if isinstance(value, HealthStatus):
                color = "#2d9257" if value.healthy else "#c34d55"
                detail = f"{value.message}\nLast check: {value.checked_at.astimezone():%Y-%m-%d %H:%M:%S %Z}"
            else:
                (color, detail) = ("#c34d55", "Service health check failed.")
            self.health_dot.setStyleSheet(
                f"QLabel#serviceHealth {{ background: {color}; border-radius: 6px; }}"
            )
            self.health_dot.setToolTip(detail)

        self.background(self.service.check_health, complete)

    def account_action(self, action: str, *, quiet: bool = False) -> None:
        if self.busy:
            return
        suffix = self.client_name.text().strip()
        name = DEFAULT_CLIENT_NAME + (f": {suffix}" if suffix else "")
        operations: dict[str, Callable[[], object]] = {
            "load": self.service.load_account,
            "login": lambda: self.service.login(
                client_name=None if self.state.client_id else name
            ),
            "logout": self.service.logout,
            "forget": self.service.forget_client,
        }
        self.busy = True
        self.refresh_account()
        self.set_account_message(
            "Complete sign-in in your browser…"
            if action == "login"
            else "Updating account…"
        )

        def complete(value: object, error: BaseException | None) -> None:
            self.busy = False
            self.state = (
                value if isinstance(value, AccountState) else self.service.state
            )
            if action == "forget" and self.state.client_id is None:
                self.client_name.clear()
            self.refresh_account()
            if error is not None:
                message = self.error_message(error)
                self.set_account_message(message)
                self.identity.setToolTip(message)
                if not quiet:
                    QMessageBox.warning(self.account, "CircuitNinja · Account", message)

        self.background(operations[action], complete)

    @staticmethod
    def error_message(error: BaseException) -> str:
        if isinstance(error, LoginTimeout):
            return "Sign-in timed out. Check the browser and try logging in again."
        if isinstance(error, ConsentDenied):
            return (
                "Access was not approved in the browser. You can try logging in again."
            )
        if isinstance(error, LoginRequired):
            return "Please log in to continue."
        if isinstance(error, ClientError):
            detail = f"\nHTTP {error.status_code}" if error.status_code else ""
            if error.oauth_error:
                detail += f"\nOAuth: {error.oauth_error}"
            return str(error) + detail
        if isinstance(error, ValueError):
            return str(error)
        return f"The operation could not be completed ({type(error).__name__})."

    def refresh_account(self) -> None:
        state = self.state
        profile = state.profile
        self.identity.setText(profile.user.name if profile else "Log in")
        self.identity.setToolTip("Account settings")
        self.profile.setText(
            f"Name: {profile.user.name}\nEmail: {profile.user.email}\nUser ID: {profile.user.id}\nOrdering: {('allowed' if profile.can_order else 'not allowed')}"
            if profile
            else "Not logged in."
        )
        self.browser_info.setVisible(profile is None)
        if state.client_id:
            self.client_name.setText(
                state.client_name.removeprefix(DEFAULT_CLIENT_NAME)
                .removeprefix(":")
                .strip()
            )
        self.client_name.setEnabled(not self.busy and state.client_id is None)
        self.client_id.setText(state.client_id or "No saved client")
        self.login_button.setEnabled(not self.busy and profile is None)
        self.logout_button.setEnabled(not self.busy and profile is not None)
        self.forget_button.setEnabled(not self.busy)
        self.refresh_button.setEnabled(not self.busy)
        self.organizations.blockSignals(True)
        self.organizations.clear()
        self.organizations.addItem("No organization", None)
        if profile:
            for org in profile.organizations:
                self.organizations.addItem(
                    f"{org.name} ({('can order' if org.can_order else 'cannot order')})",
                    org.id,
                )
        self.organizations.setCurrentIndex(
            -1
            if state.invalid_default_org
            else max(0, self.organizations.findData(state.default_org_id))
        )
        self.organizations.blockSignals(False)
        self.organizations.setEnabled(not self.busy and profile is not None)
        selected = (
            next(
                (
                    org
                    for org in profile.organizations
                    if org.id == state.default_org_id
                ),
                None,
            )
            if profile
            else None
        )
        self.default_org.setText(
            "Default organization: "
            + (
                "unavailable — choose an ordering identity"
                if state.invalid_default_org
                else selected.name
                if selected
                else "none"
            )
        )
        self.org_details.setText(
            f"Slug: {selected.slug}\nRole: {getattr(selected.role, 'value', selected.role)}\nOrganization ID: {selected.id}"
            if selected
            else ""
        )
        self.set_account_message(state.notice or "")
        self.buttons["upload"].setEnabled(profile is not None)
        self.buttons["upload"].setToolTip(
            "Upload a board and open its order form."
            if profile
            else "Log in first to upload to the service."
        )
        project_error = self.context.get("project_error") or (
            "Save a board in a KiCad project first."
            if not self.context.get("project_path")
            else ""
        )
        self.buttons["constraints"].setEnabled(not project_error)
        self.buttons["constraints"].setToolTip(
            project_error or "Set manufacturing constraints for this project."
        )
        self.buttons["drc"].setEnabled(not project_error)
        self.buttons["drc"].setToolTip(
            project_error
            or "Check a temporary project copy against manufacturing limits."
        )
        self.buttons["export"].setEnabled(not project_error)
        self.buttons["export"].setToolTip(
            project_error or "Check and export a manufacturing ZIP archive."
        )

    def set_account_message(self, message: str) -> None:
        self.account_message.setText(message)
        self.account_message.setVisible(bool(message))
        self.org_details.setVisible(bool(self.org_details.text()))
        self.account_content.ensurePolished()
        self.account_layout.invalidate()
        self.account_layout.activate()
        available = self.account.screen().availableGeometry()
        width = min(
            max(470, self.account_layout.minimumSize().width()), available.width() - 40
        )
        height = max(
            self.account_layout.totalHeightForWidth(width),
            self.account_layout.minimumSize().height(),
        )
        self.account.resize(width, min(height, available.height() - 80))

    def organization_changed(self, index: int) -> None:
        if self.busy:
            return
        organization = self.organizations.itemData(index)
        self.busy = True
        self.refresh_account()

        def complete(value: object, error: BaseException | None) -> None:
            self.busy = False
            self.state = (
                value if isinstance(value, AccountState) else self.service.state
            )
            self.refresh_account()
            if error:
                QMessageBox.warning(
                    self.account,
                    "CircuitNinja · Organization unavailable",
                    self.error_message(error),
                )

        self.background(
            lambda: self.service.select_organization(organization), complete
        )

    def open_url(self, url: str) -> None:
        try:
            if not self.browser(url):
                raise RuntimeError("Browser did not open")
        except (OSError, RuntimeError, webbrowser.Error):
            QMessageBox.warning(
                self.account if self.account.isVisible() else self.about,
                "CircuitNinja · Cannot open browser",
                f"Open this address in your browser:\n{url}",
            )

    def activate(self, action: str) -> None:
        if (
            action not in ("constraints", "drc", "export", "upload")
            or not self.buttons[action].isEnabled()
            or self.handoff
        ):
            return
        self.health_timer.stop()
        progress = QDialog()
        progress.setWindowTitle("CircuitNinja · Opening action")
        progress.setWindowIcon(self.hub.windowIcon())
        progress.setStyleSheet(STYLE)
        layout = QVBoxLayout(progress)
        title = self.buttons[action].text().replace("\n", " ")
        layout.addWidget(label(f"Checking the environment and opening {title}…"))
        bar = QProgressBar()
        bar.setRange(0, 0)
        layout.addWidget(bar)
        self.handoff = progress
        show_centered(progress)
        self.hub.hide()
        context = {**self.context, "service_origin": self.service.origin}
        context.pop("ready_file", None)
        if action == "upload" and self.state.invalid_default_org:
            context["default_org_invalid"] = True

        def complete(value: object, error: BaseException | None) -> None:
            if error:
                self.show_hub()
                progress.close()
                self.handoff = None
                QMessageBox.critical(
                    self.hub, f"CircuitNinja · Cannot open {title}", str(error)
                )
            else:
                progress.close()
                self.app.quit()

        self.background(
            lambda: self.launcher(
                Path(self.context["environment_path"]),
                Path(__file__).with_name(
                    "manufacturing_constraints_ipc.py"
                    if action == "constraints"
                    else "upload_ipc.py"
                    if action == "upload"
                    else "export_ipc.py"
                    if action == "export"
                    else "drc_ipc.py"
                ),
                context,
            ),
            complete,
        )

    def shutdown(self) -> None:
        self.closing = True
        self.health_timer.stop()
        if not self.tasks:
            self.service.close()
