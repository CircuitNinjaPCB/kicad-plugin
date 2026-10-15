import logging
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Any, Literal, cast

from kicad_constraints import (
    CONSTRAINT_LABELS,
    OverrideOptions,
    OverrideResult,
    override_manufacturing_constraints,
)
from models import ManufacturingConstraints
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QCloseEvent, QIcon, QPixmap
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

STYLE = '\nQWidget { background: white; color: #252b38; font-size: 12px; }\nQLabel#title { font-size: 20px; font-weight: 700; }\nQLabel#muted { color: #677083; }\nQLabel#section { font-weight: 700; }\nQLabel#number { color: #472e76; background: #f3f1f8; border-radius: 11px;\n    font-weight: 700; }\nQFrame#step { border: 1px solid #dfe3eb; border-radius: 6px; }\nQFrame#step[active="true"] { border-color: #472e76; }\nQPushButton, QComboBox { border: 1px solid #dfe3eb; border-radius: 5px;\n    padding: 5px 10px; }\nQPushButton:hover, QComboBox:hover { border-color: #472e76; background: #f3f1f8; }\nQPushButton#primary { background: #472e76; color: white; border-color: #472e76; }\nQPushButton#primary:hover { background: #3a2561; }\nQPushButton:disabled { color: #9298a4; background: #f0f1f4; border-color: #dfe3eb; }\nQPushButton#primary:disabled { color: #82798f; background: #e7e2ee; border-color: #d7cfdf; }\nQCheckBox { spacing: 7px; padding: 1px 0; }\nQCheckBox::indicator { width: 14px; height: 14px; border: 1px solid #aab1be;\n    border-radius: 3px; background: white; }\nQCheckBox::indicator:checked { background: #472e76; border-color: #472e76; }\nQScrollArea { border: none; }\nQProgressBar { background: #edf0f4; border: none; border-radius: 2px;\n    min-height: 4px; max-height: 4px; }\nQProgressBar::chunk { background: #472e76; border-radius: 2px; }\n'


def label(text: str, name: str = "") -> QLabel:
    widget = QLabel(text)
    widget.setTextFormat(Qt.TextFormat.PlainText)
    widget.setWordWrap(True)
    widget.setObjectName(name)
    return widget


class ConstraintsWindow(QDialog):
    def __init__(
        self,
        constraints: ManufacturingConstraints,
        context: dict[str, Any],
        *,
        processes: Callable[[], dict[int, str]],
        reopen: Callable[[], None],
        log_path: Path | None = None,
        writer: Callable[
            [ManufacturingConstraints, Path, OverrideOptions], OverrideResult
        ] = override_manufacturing_constraints,
    ) -> None:
        super().__init__()
        self.constraints = constraints
        self.context = context
        self.project_path = Path(context["project_path"])
        self.processes = processes
        self.reopen = reopen
        self.writer = writer
        self.log_path = log_path
        self.phase = 0
        self.applied = False
        self.failed = False
        self.busy = False
        self.closed = False
        self.result: OverrideResult | None = None
        self.setWindowTitle("CircuitNinja · Set manufacturing rules")
        self.setStyleSheet(STYLE)
        self.resize(550, 690)
        self.setMinimumSize(440, 470)
        icons = Path(__file__).parent / "icons"
        self.setWindowIcon(QIcon(str(icons / "icon.png")))
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 12)
        layout.setSpacing(8)
        brand = label("CircuitNinja")
        logo = QPixmap(str(icons / "logo.png"))
        if not logo.isNull():
            ratio = self.devicePixelRatioF()
            scaled = logo.scaledToWidth(
                round(150 * ratio), Qt.TransformationMode.SmoothTransformation
            )
            scaled.setDevicePixelRatio(ratio)
            brand.setPixmap(scaled)
            brand.setFixedSize(150, round(scaled.height() / ratio) + 2)
        layout.addWidget(brand)
        layout.addWidget(label("Set manufacturing rules", "title"))
        path = label(str(self.project_path), "muted")
        path.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(path)
        self.progress = QProgressBar()
        self.progress.setRange(0, 4)
        self.progress.setTextVisible(False)
        layout.addWidget(self.progress)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        content = QWidget()
        steps = QVBoxLayout(content)
        steps.setContentsMargins(0, 0, 4, 0)
        steps.setSpacing(6)
        self.cards: list[QFrame] = []
        self.numbers: list[QLabel] = []
        self.bodies: list[QWidget] = []
        self.headings: list[QLabel] = []
        for number, title in enumerate(
            (
                "Configure constraints",
                "Save and close KiCad",
                "Apply rules",
                "Reopen KiCad",
            )
        ):
            card = QFrame()
            card.setObjectName("step")
            card_layout = QVBoxLayout(card)
            card_layout.setContentsMargins(10, 6, 10, 6)
            card_layout.setSpacing(5)
            row = QHBoxLayout()
            badge = label(str(number + 1), "number")
            badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
            badge.setFixedSize(22, 22)
            row.addWidget(badge)
            heading = label(title, "section")
            row.addWidget(heading, 1)
            card_layout.addLayout(row)
            body = QWidget()
            card_layout.addWidget(body)
            steps.addWidget(card)
            self.cards.append(card)
            self.numbers.append(badge)
            self.bodies.append(body)
            self.headings.append(heading)
        steps.addStretch()
        scroll.setWidget(content)
        layout.addWidget(scroll, 1)
        self.build_config()
        close_layout = self.body_layout(1)
        close_layout.addWidget(
            label(
                "Save your work and close every KiCad window, including Project Manager."
            )
        )
        self.closure_status = label("Checking KiCad…", "muted")
        close_layout.addWidget(self.closure_status)
        self.force_close = QCheckBox("I confirm KiCad is closed · override detection")
        self.force_close.setToolTip(
            "Use only after saving and closing all KiCad windows. This bypasses process detection; an open editor could overwrite the new settings."
        )
        self.force_close.toggled.connect(self.refresh)
        close_layout.addWidget(self.force_close)
        apply_layout = self.body_layout(2)
        apply_layout.addWidget(
            label(
                "Review the changes. Original files will be backed up beside your project."
            )
        )
        self.review = label("")
        apply_layout.addWidget(self.review)
        reopen_layout = self.body_layout(3)
        self.receipt = label("Reopen your project to load the updated settings.")
        reopen_layout.addWidget(self.receipt)
        self.backup = label("", "muted")
        self.backup.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        reopen_layout.addWidget(self.backup)
        buttons = QHBoxLayout()
        self.back_button = QPushButton("Back")
        self.back_button.clicked.connect(self.back)
        buttons.addWidget(self.back_button)
        buttons.addStretch()
        self.next_button = QPushButton("Continue")
        self.next_button.setObjectName("primary")
        self.next_button.clicked.connect(self.advance)
        buttons.addWidget(self.next_button)
        layout.addLayout(buttons)
        self.monitor = QTimer(self)
        self.monitor.timeout.connect(self.poll)
        self.monitor.start(1500)
        self.done_timer = QTimer(self)
        self.done_timer.setSingleShot(True)
        self.done_timer.timeout.connect(self.close)
        self.poll()

    def body_layout(self, index: int) -> QVBoxLayout:
        layout = QVBoxLayout(self.bodies[index])
        layout.setContentsMargins(0, 3, 0, 0)
        layout.setSpacing(5)
        return layout

    def build_config(self) -> None:
        layout = self.body_layout(0)
        form = QFormLayout()
        form.setVerticalSpacing(5)
        self.custom_rules = QComboBox()
        self.custom_rules.addItems(["Replace", "Append"])
        self.custom_rules.setToolTip(
            "Replace removes all existing custom rules. Append preserves other rules, removes rules named CircuitNinja, and adds these rules last with higher priority."
        )
        self.severity = QComboBox()
        self.severity.addItems(["Error", "Warning"])
        self.severity.setToolTip(
            "Severity for selected manufacturing checks, including shared check types."
        )
        self.other_severities = QComboBox()
        self.other_severities.addItems(["Keep", "Silence"])
        self.other_severities.setToolTip(
            "Keep preserves unrelated DRC severities. Silence sets other project checks to ignore. Preserved custom rules may still specify their own severity."
        )
        form.addRow("Custom rules", self.custom_rules)
        form.addRow("Manufacturing checks", self.severity)
        form.addRow("Other checks", self.other_severities)
        layout.addLayout(form)
        self.checks: dict[str, QCheckBox] = {}
        for field, name in CONSTRAINT_LABELS.items():
            check = QCheckBox(f"{name} · {getattr(self.constraints, field):g} mm")
            check.setChecked(True)
            check.setToolTip(
                "Apply this service minimum to the corresponding project settings and custom rules. Shared checks stay enabled when another selected limit needs them."
            )
            if field in {"min_pth_mm", "min_annular_ring_mm"}:
                check.setToolTip(
                    check.toolTip()
                    + " Selecting either plated hole or annular ring also sets via diameter using both service minimums."
                )
            check.toggled.connect(self.refresh)
            self.checks[field] = check
            layout.addWidget(check)
        layout.addWidget(
            label(
                "Board size limits and other non-DRC checks belong to export, analysis or order upload.",
                "muted",
            )
        )

    def options(self) -> OverrideOptions:
        return OverrideOptions(
            custom_rules=cast(
                Literal["replace", "append"], self.custom_rules.currentText().lower()
            ),
            severity=cast(
                Literal["error", "warning"], self.severity.currentText().lower()
            ),
            other_severities=cast(
                Literal["keep", "silence"], self.other_severities.currentText().lower()
            ),
            selected_fields=frozenset(
                field for (field, check) in self.checks.items() if check.isChecked()
            ),
        )

    def poll(self) -> None:
        if self.applied or self.failed:
            return
        try:
            if not self.context.get("observed_origin", False):
                raise RuntimeError(
                    self.context.get("monitor_error")
                    or "The originating KiCad process was not identified."
                )
            running = self.processes()
            self.closed = not running
            self.closure_status.setText(
                "KiCad is closed."
                if self.closed
                else "Still running: " + ", ".join(sorted(set(running.values())))
            )
        except (OSError, RuntimeError, subprocess.SubprocessError, ValueError) as exc:
            self.closed = False
            self.closure_status.setText(f"Exit detection unavailable: {exc}")
        self.refresh()

    def refresh(self, *_args: object) -> None:
        if not hasattr(self, "next_button"):
            return
        for index, card in enumerate(self.cards):
            active = index == min(self.phase, 3)
            card.setProperty("active", active)
            card.style().unpolish(card)
            card.style().polish(card)
            self.numbers[index].setText("✓" if index < self.phase else str(index + 1))
            self.bodies[index].setVisible(active)
        self.progress.setValue(self.phase)
        self.back_button.setEnabled(0 < self.phase < 3 and (not self.busy))
        self.next_button.setText(
            ("Continue", "Continue", "Apply rules", "Reopen KiCad", "Close")[self.phase]
        )
        allowed = bool(self.options().selected_fields)
        if self.phase in (1, 2):
            allowed = allowed and (self.closed or self.force_close.isChecked())
        self.next_button.setEnabled(allowed and (not self.busy))

    def update_review(self) -> None:
        lines = [
            f"Custom rules: {self.custom_rules.currentText()}",
            f"Manufacturing checks: {self.severity.currentText()}",
            f"Other checks: {self.other_severities.currentText()}",
            "",
            *(check.text() for check in self.checks.values() if check.isChecked()),
        ]
        if self.options().selected_fields & {"min_pth_mm", "min_annular_ring_mm"}:
            diameter = (
                self.constraints.min_pth_mm + 2 * self.constraints.min_annular_ring_mm
            )
            lines.append(f"Via diameter (shared): {diameter:g} mm")
        if self.force_close.isChecked():
            lines.extend(["", "KiCad closure: manually confirmed."])
        self.review.setText("\n".join(lines))

    def advance(self) -> None:
        if self.busy or not self.next_button.isEnabled():
            return
        if self.phase in (1, 2):
            self.poll()
            if not (self.closed or self.force_close.isChecked()):
                self.phase = 1
                self.refresh()
                return
        try:
            if self.phase < 2:
                self.phase += 1
                self.update_review()
            elif self.phase == 2:
                self.busy = True
                self.refresh()
                self.result = self.writer(
                    self.constraints, self.project_path, self.options()
                )
                self.applied = True
                self.monitor.stop()
                self.backup.setText(f"Backup: {self.result.backup_directory}")
                self.phase = 3
            elif self.phase == 3:
                self.reopen()
                self.phase = 4
                self.headings[3].setText("Done")
                self.receipt.setText(
                    "KiCad has been launched. This dialog will close in 5 seconds."
                )
                self.done_timer.start(5000)
            else:
                self.close()
        except Exception as exc:
            logging.getLogger(__name__).exception("Manufacturing constraints failed")
            self.fail(exc)
        finally:
            self.busy = False
            self.refresh()

    def back(self) -> None:
        if 0 < self.phase < 3 and (not self.busy):
            self.phase -= 1
            self.refresh()

    def fail(self, exc: BaseException) -> None:
        self.failed = True
        self.monitor.stop()
        self.close()
        message = str(exc) or type(exc).__name__
        if self.log_path:
            message += f"\n\nLog: {self.log_path}"
        QMessageBox.critical(
            None, "CircuitNinja · Manufacturing constraints failed", message
        )

    def confirm_close(self) -> bool:
        if self.applied or self.failed:
            return True
        return (
            QMessageBox.warning(
                self,
                "CircuitNinja · Setup is not completed",
                "Close without applying the manufacturing constraints?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Cancel,
            )
            == QMessageBox.StandardButton.Yes
        )

    def closeEvent(self, event: QCloseEvent) -> None:
        if self.confirm_close():
            self.monitor.stop()
            self.done_timer.stop()
            event.accept()
        else:
            event.ignore()

    def reject(self) -> None:
        self.close()
