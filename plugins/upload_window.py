import os
import shutil
import tempfile
import webbrowser
from collections.abc import Callable
from pathlib import Path

from drc_window import Worker
from drc_workflow import DrcWorkflow
from export_window import ExportWindow
from hub_service import AccountState, HubService, ordering_problem
from hub_window import HubController
from manufacturing_constraints_window import label
from models import ManufacturingConstraints
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor, QStandardItemModel
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFrame,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
)
from service_models import BoardStoredResponse


class AccountWorker(Worker):
    def run(self) -> None:
        try:
            value = self.function()
        except Exception as exc:
            self.finished.emit(None, exc)
        else:
            self.finished.emit(value, None)


def save_zip(source: Path, destination: Path) -> None:
    if source.resolve() == destination.resolve():
        return
    (fd, name) = tempfile.mkstemp(
        prefix=".circuitninja-", suffix=".zip", dir=destination.parent
    )
    os.close(fd)
    try:
        shutil.copyfile(source, name)
        os.replace(name, destination)
    finally:
        Path(name).unlink(missing_ok=True)


class UploadWindow(ExportWindow):
    def __init__(
        self,
        workflow: DrcWorkflow,
        constraints: ManufacturingConstraints,
        account: HubService,
        *,
        invalid_default: bool = False,
        browser: Callable[[str], bool] = webbrowser.open,
    ) -> None:
        self.account = account
        self.account_state = AccountState()
        self.invalid_default = invalid_default
        self.browser = browser
        self.receipt: BoardStoredResponse | None = None
        self.organization_id: str | None = None
        self.user_id = ""
        super().__init__(workflow, constraints)
        self.setWindowTitle("CircuitNinja · Upload to web app")
        self.title_label.setText("Upload to web app")
        self.steps.setText("1 Configure  ·  2 Check & review  ·  3 Export  ·  4 Order")
        self.output_controls.hide()
        self.override_org = QCheckBox(
            "Override the default organization for this upload"
        )
        self.override_org.setEnabled(False)
        self.organizations = QComboBox()
        self.organizations.setAccessibleName("Ordering account")
        self.organizations.setPlaceholderText("Loading accounts…")
        self.organizations.setEnabled(False)
        self.config_layout.insertWidget(0, self.organizations)
        self.config_layout.insertWidget(1, self.override_org)
        self.organization_message = label("", "organizationMessage")
        self.organization_message.setStyleSheet(
            "QLabel#organizationMessage { color: #bb3030; }"
        )
        self.organization_message.hide()
        self.config_layout.insertWidget(2, self.organization_message)
        separator = QFrame()
        separator.setFrameShape(QFrame.Shape.HLine)
        separator.setFrameShadow(QFrame.Shadow.Sunken)
        self.config_layout.insertWidget(3, separator)
        self.override_org.toggled.connect(self.organization_changed)
        self.organizations.currentIndexChanged.connect(self.organization_changed)
        self.save_button = QPushButton("Save ZIP manually…")
        self.save_button.hide()
        self.save_button.clicked.connect(self.save_manually)
        self.buttons_layout.insertWidget(
            self.buttons_layout.count() - 1, self.save_button
        )
        panel = QFrame()
        panel.setObjectName("step")
        self.pages.addWidget(panel)
        layout = QVBoxLayout(panel)
        self.order_summary = label("Ready to upload", "section")
        layout.addWidget(self.order_summary)
        self.order_files = QListWidget()
        layout.addWidget(self.order_files, 1)
        self.archive_path = label("", "muted")
        self.archive_path.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        layout.addWidget(self.archive_path)
        self.order_url = label("", "muted")
        self.order_url.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        layout.addWidget(self.order_url)
        self.upload_button = QPushButton("Upload board and open order form")
        self.upload_button.setObjectName("primary")
        self.upload_button.clicked.connect(self.upload)
        layout.addWidget(self.upload_button)
        self.begin.setEnabled(False)
        QTimer.singleShot(0, self.load_account)

    def load_account(self) -> None:
        self.start_worker(AccountWorker(self.account.load_account), self.account_loaded)

    def account_loaded(self, value: object, error: object) -> None:
        if (
            error is not None
            or not isinstance(value, AccountState)
            or value.profile is None
        ):
            message = (
                HubController.error_message(error)
                if isinstance(error, BaseException)
                else "Log in from the hub before uploading a board."
            )
            self.account_state = AccountState()
            self.organization_message.setText(message)
            self.organization_message.show()
            self.organizations.setEnabled(False)
            self.organizations.setPlaceholderText("Account unavailable")
            self.override_org.setEnabled(False)
            self.begin.setEnabled(False)
            return
        self.account_state = value
        profile = value.profile
        self.organizations.blockSignals(True)
        self.organizations.clear()
        self.organizations.setPlaceholderText("")
        self.organizations.addItem(
            f"Personal account ({('can order' if profile.can_order else 'cannot order')})",
            None,
        )
        for org in profile.organizations:
            self.organizations.addItem(
                f"{org.name} ({('can order' if org.can_order else 'cannot order')})",
                org.id,
            )
        model = self.organizations.model()
        if isinstance(model, QStandardItemModel):
            for index in range(self.organizations.count()):
                item = model.item(index)
                if item is not None:
                    item.setEnabled(
                        not ordering_problem(
                            profile, self.organizations.itemData(index)
                        )
                    )
        problem = ordering_problem(profile, value.default_org_id)
        self.invalid_default = (
            self.invalid_default or value.invalid_default_org or bool(problem)
        )
        self.organizations.setCurrentIndex(
            -1
            if self.invalid_default
            else self.organizations.findData(value.default_org_id)
        )
        self.organizations.blockSignals(False)
        self.override_org.setEnabled(True)
        if self.invalid_default:
            self.override_org.setChecked(True)
        self.organization_changed()

    def organization_changed(self) -> None:
        enabled = (
            self.override_org.isChecked() and self.account_state.profile is not None
        )
        self.organizations.setEnabled(enabled)
        if not enabled:
            self.organizations.blockSignals(True)
            self.organizations.setCurrentIndex(
                -1
                if self.invalid_default
                else self.organizations.findData(self.account_state.default_org_id)
            )
            self.organizations.blockSignals(False)
        self.validate_output()

    def validate_output(self) -> None:
        if not hasattr(self, "override_org"):
            return
        profile = self.account_state.profile
        problem = "Log in from the hub before uploading." if profile is None else ""
        if profile is not None:
            org = (
                self.organizations.currentData()
                if self.override_org.isChecked()
                else self.account_state.default_org_id
            )
            no_selection = self.organizations.currentIndex() < 0
            if self.override_org.isChecked() and no_selection:
                problem = "Choose an ordering identity."
            else:
                problem = ordering_problem(profile, org)
            if self.invalid_default and (
                not self.override_org.isChecked() or no_selection
            ):
                problem = "The default organization is unavailable or cannot order. Select an override."
        self.organization_message.setText(problem)
        self.organization_message.setVisible(bool(problem))
        self.begin.setEnabled(not problem and (not self.busy))

    def start(self) -> None:
        self.validate_output()
        if not self.begin.isEnabled() or self.account_state.profile is None:
            return
        self.organization_id = (
            self.organizations.currentData()
            if self.override_org.isChecked()
            else self.account_state.default_org_id
        )
        self.user_id = self.account_state.profile.user.id
        super().start()

    def start_export(self) -> None:
        if self.workflow.root is not None:
            self.destination = (
                self.workflow.root / self.workflow.source.with_suffix(".zip").name
            )
        super().start_export()
        self.steps.setText(
            "✓ Configure  ·  DRC skipped  ·  3 Export  ·  4 Order"
            if self.skip_drc.isChecked()
            else "✓ Configure  ·  ✓ Check & review  ·  3 Export  ·  4 Order"
        )

    def export_completed(self, value: object, error: object) -> None:
        super().export_completed(value, error)
        if error is not None:
            return
        self.pages.setCurrentIndex(4)
        self.steps.setText(
            "✓ Configure  ·  DRC skipped  ·  ✓ Export  ·  4 Order"
            if self.skip_drc.isChecked()
            else "✓ Configure  ·  ✓ Check & review  ·  ✓ Export  ·  4 Order"
        )
        for index in range(self.export_items.count()):
            item = self.export_items.item(index)
            if item is not None:
                self.order_files.addItem(item.clone())
        self.archive_path.setText(self.export_note.text())
        self.order_summary.setText(
            "Ready to upload · " + self.organizations.currentText()
        )
        QTimer.singleShot(0, self.order_files.scrollToBottom)

    def upload(self) -> None:
        if self.busy or self.destination is None or self.pages.currentIndex() != 4:
            return
        if self.receipt is not None:
            self.open_order()
            return
        self.upload_button.setEnabled(False)
        self.save_button.setEnabled(False)
        self.order_summary.setText("Uploading board…")
        archive = self.destination
        self.start_worker(
            AccountWorker(
                lambda: self.account.upload_board(
                    archive, self.organization_id, self.user_id
                )
            ),
            self.upload_completed,
        )

    def upload_completed(self, value: object, error: object) -> None:
        self.upload_button.setEnabled(True)
        self.save_button.setEnabled(True)
        if error is not None or not isinstance(value, BoardStoredResponse):
            message = (
                HubController.error_message(error)
                if isinstance(error, BaseException)
                else "The service did not return an order receipt."
            )
            self.order_summary.setText("Upload failed · ZIP retained")
            self.add_status("Upload failed: " + message, failed=True)
            self.save_button.show()
            self.upload_button.setText("Retry upload and open order form")
            QMessageBox.warning(
                self,
                "CircuitNinja · Upload failed",
                message
                + "\n\nThe ZIP can be saved for manual upload. If the connection failed, check your orders before retrying; the server may have accepted the upload.",
            )
            return
        self.receipt = value
        self.add_status("Board uploaded successfully")
        self.order_summary.setText("Board uploaded")
        self.order_url.setText(value.order_url)
        self.upload_button.setText("Open order form")
        self.open_order()

    def open_order(self) -> None:
        if self.receipt is None:
            return
        try:
            if not self.browser(self.receipt.order_url):
                raise RuntimeError("The browser did not open.")
        except (OSError, RuntimeError, webbrowser.Error) as exc:
            self.add_status("Uploaded; browser could not open", failed=True)
            QMessageBox.warning(
                self,
                "CircuitNinja · Cannot open order form",
                f"{exc}\n\nOpen this address manually:\n{self.receipt.order_url}",
            )
        else:
            self.add_status("Order form opened in browser")

    def add_status(self, message: str, *, failed: bool = False) -> None:
        item = QListWidgetItem(("!  " if failed else "✓  ") + message)
        item.setForeground(QColor("#bb3030" if failed else "#2d9257"))
        self.order_files.addItem(item)
        self.order_files.scrollToBottom()

    def save_manually(self) -> None:
        if self.busy or self.destination is None:
            return
        (path, _) = QFileDialog.getSaveFileName(
            self,
            "CircuitNinja · Save manufacturing ZIP",
            str(self.workflow.source.with_suffix(".zip")),
            "ZIP archives (*.zip)",
        )
        if not path:
            return
        self.save_button.setEnabled(False)
        self.upload_button.setEnabled(False)
        source = self.destination

        def complete(value: object, error: object) -> None:
            self.save_button.setEnabled(True)
            self.upload_button.setEnabled(True)
            if error is not None:
                QMessageBox.warning(self, "CircuitNinja · Cannot save ZIP", str(error))
            else:
                self.add_status("ZIP saved to " + path)

        self.start_worker(Worker(lambda: save_zip(source, Path(path))), complete)
