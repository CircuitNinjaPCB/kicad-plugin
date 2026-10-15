import logging
import threading
from collections.abc import Callable
from pathlib import Path

from drc_workflow import DrcWorkflow, Finding
from manufacturing_constraints_window import STYLE, label
from models import ManufacturingConstraints
from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtGui import QCloseEvent, QIcon, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QDialog,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)


class Worker(QObject):
    progress = Signal(int, str)
    finished = Signal(object, object)

    def __init__(self, function: Callable[[], object]) -> None:
        super().__init__()
        self.function = function

    def run(self) -> None:
        try:
            result = self.function()
        except Exception as exc:
            logging.getLogger(__name__).exception("DRC operation failed")
            self.finished.emit(None, exc)
        else:
            self.finished.emit(result, None)


class DrcWindow(QDialog):
    def __init__(
        self, workflow: DrcWorkflow, constraints: ManufacturingConstraints
    ) -> None:
        super().__init__()
        self.workflow = workflow
        self.constraints = constraints
        self.findings: list[Finding] = []
        self.busy = False
        self.worker: Worker | None = None
        self.setWindowTitle("CircuitNinja · Check manufacturability")
        self.setStyleSheet(STYLE)
        self.resize(620, 490)
        self.setMinimumSize(480, 360)
        icons = Path(__file__).parent / "icons"
        self.setWindowIcon(QIcon(str(icons / "icon.png")))
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 12)
        layout.setSpacing(8)
        brand = label("CircuitNinja")
        pixmap = QPixmap(str(icons / "logo.png"))
        if not pixmap.isNull():
            ratio = self.devicePixelRatioF()
            pixmap = pixmap.scaledToWidth(
                round(150 * ratio), Qt.TransformationMode.SmoothTransformation
            )
            pixmap.setDevicePixelRatio(ratio)
            brand.setPixmap(pixmap)
            brand.setFixedSize(150, round(pixmap.height() / ratio) + 2)
        layout.addWidget(brand)
        self.title_label = label("Check manufacturability", "title")
        layout.addWidget(self.title_label)
        layout.addWidget(label(str(workflow.source), "muted"))
        self.steps = label("1 Configure  ·  2 Run checks  ·  3 Results", "section")
        layout.addWidget(self.steps)
        self.pages = QStackedWidget()
        layout.addWidget(self.pages, 1)
        panels: list[QFrame] = []
        for _ in range(3):
            page = QFrame()
            page.setObjectName("step")
            self.pages.addWidget(page)
            panels.append(page)
        config = self.config_layout = QVBoxLayout(panels[0])
        config.addWidget(
            label(
                "Check a temporary copy against CircuitNinja manufacturing limits. Your board and project rules are not overwritten."
            )
        )
        config.addWidget(
            label(
                "Before continuing, save the board in KiCad PCB Editor (File → Save), especially after changing Board Setup. This action reads project settings from the saved files. Leave PCB Editor open while it runs.",
                "muted",
            )
        )
        self.refill = QCheckBox("Refill zones in the temporary copy")
        self.refill.setChecked(workflow.can_refill)
        self.refill.setEnabled(workflow.can_refill)
        config.addWidget(self.refill)
        if not workflow.can_refill:
            config.addWidget(
                label(
                    "KiCad Python is unavailable. Refill in the editor first if needed.",
                    "muted",
                )
            )
        config.addWidget(
            label(
                "Board shape and dimensions are checked against manufacturing limits.",
                "muted",
            )
        )
        config.addStretch()
        self.begin = QPushButton("Begin checks")
        self.begin.setObjectName("primary")
        self.begin.clicked.connect(self.start)
        config.addWidget(self.begin)
        running = QVBoxLayout(panels[1])
        self.stages: list[QLabel] = []
        for text in (
            "Copy current board and project",
            "Apply manufacturing rules and refill zones if selected",
            "Run DRC, check board profile and collect results",
        ):
            row = label("○  " + text)
            self.stages.append(row)
            running.addWidget(row)
        self.stage_names = [row.text()[3:] for row in self.stages]
        self.scratch = label("Preparing temporary project…", "muted")
        self.scratch.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        running.addWidget(self.scratch)
        self.progress = QProgressBar()
        self.progress.setRange(0, 0)
        running.addWidget(self.progress)
        running.addStretch()
        results = self.results_layout = QVBoxLayout(panels[2])
        self.summary = label("", "section")
        results.addWidget(self.summary)
        self.table = QTableWidget(0, 3)
        self.table.setStyleSheet(
            "QTableWidget { selection-background-color: #f3f1f8; selection-color: #472e76; gridline-color: #dfe3eb; }"
        )
        self.table.setHorizontalHeaderLabels(["Check", "Description", "Objects"])
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.verticalHeader().hide()
        self.table.horizontalHeader().setSectionResizeMode(
            1, QHeaderView.ResizeMode.Stretch
        )
        self.table.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeMode.ResizeToContents
        )
        self.table.setColumnWidth(2, 65)
        self.table.itemSelectionChanged.connect(self.selection_changed)
        self.table.itemDoubleClicked.connect(lambda _: self.show_selected())
        results.addWidget(self.table, 1)
        results.addWidget(
            label("Results describe the captured board. Rerun after edits.", "muted")
        )
        self.report_path = label("", "muted")
        self.report_path.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        results.addWidget(self.report_path)
        buttons = self.buttons_layout = QHBoxLayout()
        self.show_button = QPushButton("Show in KiCad")
        self.show_button.hide()
        self.show_button.setEnabled(False)
        self.show_button.clicked.connect(self.show_selected)
        buttons.addWidget(self.show_button)
        buttons.addStretch()
        self.close_button = QPushButton("Close")
        self.close_button.clicked.connect(self.close)
        buttons.addWidget(self.close_button)
        layout.addLayout(buttons)

    def start_worker(
        self, worker: Worker, complete: Callable[[object, object], None]
    ) -> None:
        self.busy = True
        self.close_button.setEnabled(False)
        self.show_button.setEnabled(False)
        self.worker = worker

        def finished(value: object, error: object) -> None:
            self.busy = False
            self.close_button.setEnabled(True)
            self.worker = None
            complete(value, error)

        worker.finished.connect(finished, Qt.ConnectionType.QueuedConnection)
        threading.Thread(target=worker.run, daemon=True).start()

    def start(self) -> None:
        if self.busy or self.pages.currentIndex() != 0:
            return
        self.pages.setCurrentIndex(1)
        self.steps.setText("✓ Configure  ·  2 Run checks  ·  3 Results")
        refill = self.refill.isChecked()
        worker = Worker(
            lambda: self.workflow.run(self.constraints, refill, worker.progress.emit)
        )
        worker.progress.connect(
            self.update_progress, Qt.ConnectionType.QueuedConnection
        )
        self.start_worker(worker, self.completed)

    def update_progress(self, completed: int, path: str) -> None:
        self.scratch.setText("Temporary project (retained):\n" + path)
        for i, row in enumerate(self.stages):
            row.setText(("✓  " if i < completed else "○  ") + self.stage_names[i])
            row.setObjectName("stageStatus")
            row.setStyleSheet(
                "QLabel#stageStatus { color: #2d9257; }" if i < completed else ""
            )

    def completed(self, value: object, error: object) -> None:
        self.progress.setRange(0, 1)
        if error is not None:
            self.steps.setText("✓ Configure  ·  Checks failed")
            QMessageBox.critical(
                self,
                "CircuitNinja · Manufacturing DRC failed",
                f"{error}\n\n{self.scratch.text()}",
            )
            return
        assert isinstance(value, list)
        self.findings = value
        self.show_button.show()
        self.pages.setCurrentIndex(2)
        self.steps.setText("✓ Configure  ·  ✓ Run checks  ·  3 Results")
        self.summary.setText(
            f"{len(value)} manufacturing finding{('s' if len(value) != 1 else '')}"
            if value
            else "All good · no manufacturing findings"
        )
        self.table.setRowCount(len(value))
        for row, finding in enumerate(self.findings):
            for column, text in enumerate(
                (
                    finding.type.replace("_", " ")
                    + (" (warning)" if finding.severity == "warning" else ""),
                    finding.description,
                    str(len(finding.items)),
                )
            ):
                item = QTableWidgetItem(text)
                item.setToolTip(
                    finding.description
                    + "\n"
                    + "\n".join(i.description for i in finding.items)
                )
                self.table.setItem(row, column, item)
        if self.workflow.root:
            self.report_path.setText(
                "Report retained at " + str(self.workflow.root / "findings.json")
            )
        if value:
            self.table.selectRow(0)

    def selection_changed(self) -> None:
        row = self.table.currentRow()
        self.show_button.setEnabled(
            not self.busy
            and 0 <= row < len(self.findings)
            and bool(self.findings[row].items)
        )

    def show_selected(self) -> None:
        row = self.table.currentRow()
        if self.busy or not 0 <= row < len(self.findings):
            return

        def complete(value: object, error: object) -> None:
            self.selection_changed()
            if error:
                QMessageBox.warning(
                    self, "CircuitNinja · Cannot show finding", str(error)
                )

        self.start_worker(
            Worker(lambda: self.workflow.show(self.findings[row])), complete
        )

    def reject(self) -> None:
        if not self.busy:
            super().reject()

    def closeEvent(self, event: QCloseEvent) -> None:
        if self.busy:
            event.ignore()
        else:
            event.accept()
