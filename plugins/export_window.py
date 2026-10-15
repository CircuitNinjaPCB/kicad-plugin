from pathlib import Path

from drc_window import DrcWindow, Worker
from drc_workflow import DrcWorkflow
from export_workflow import ExportWorkflow, output_problem
from manufacturing_constraints_window import label
from models import ManufacturingConstraints
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QCheckBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
)


class ExportWindow(DrcWindow):
    def __init__(
        self, workflow: DrcWorkflow, constraints: ManufacturingConstraints
    ) -> None:
        super().__init__(workflow, constraints)
        self.exporter = ExportWorkflow(workflow)
        self.destination: Path | None = None
        self.setWindowTitle("CircuitNinja · Export manufacturing files")
        self.title_label.setText("Export manufacturing files")
        self.steps.setText("1 Configure  ·  2 Check & review  ·  3 Export")
        self.output_controls = QFrame()
        output_layout = QVBoxLayout(self.output_controls)
        output_layout.setContentsMargins(0, 0, 0, 0)
        output_layout.addWidget(label("Output ZIP archive", "section"))
        self.config_layout.insertWidget(0, self.output_controls)
        row = QHBoxLayout()
        self.output_path = QLineEdit(str(workflow.source.with_suffix(".zip")))
        self.output_path.setPlaceholderText("/existing/folder/board.zip")
        self.output_path.textChanged.connect(self.validate_output)
        row.addWidget(self.output_path, 1)
        browse = QPushButton("Browse…")
        browse.clicked.connect(self.browse)
        row.addWidget(browse)
        output_layout.addLayout(row)
        self.path_message = label("", "pathMessage")
        output_layout.addWidget(self.path_message)
        self.skip_drc = QCheckBox("Skip DRC (not recommended)")
        self.config_layout.insertWidget(self.config_layout.count() - 2, self.skip_drc)
        self.begin.setText("Continue")
        self.acknowledge = QCheckBox(
            "I acknowledge all errors and want to export anyway"
        )
        self.results_layout.addWidget(self.acknowledge)
        self.export_button = QPushButton("Continue to export")
        self.export_button.setObjectName("primary")
        self.export_button.setEnabled(False)
        self.export_button.hide()
        self.export_button.clicked.connect(self.start_export)
        self.acknowledge.toggled.connect(self.export_button.setEnabled)
        self.buttons_layout.insertWidget(1, self.export_button)
        panel = QFrame()
        panel.setObjectName("step")
        self.pages.addWidget(panel)
        layout = QVBoxLayout(panel)
        self.export_summary = label("Exporting manufacturing files…", "section")
        layout.addWidget(self.export_summary)
        self.export_items = QListWidget()
        layout.addWidget(self.export_items, 1)
        self.export_progress = QProgressBar()
        self.export_progress.setRange(0, 0)
        layout.addWidget(self.export_progress)
        self.export_note = label("", "muted")
        self.export_note.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        layout.addWidget(self.export_note)
        self.validate_output()

    def validate_output(self) -> None:
        (message, severity) = output_problem(self.output_path.text())
        self.path_message.setText(message)
        self.path_message.setStyleSheet(
            {
                "error": "QLabel#pathMessage { color: #bb3030; }",
                "warning": "QLabel#pathMessage { color: #a35b00; }",
                "info": "QLabel#pathMessage { color: #657184; }",
            }[severity]
        )
        self.begin.setEnabled(severity != "error")

    def browse(self) -> None:
        (path, _) = QFileDialog.getSaveFileName(
            self,
            "CircuitNinja · Export manufacturing ZIP",
            self.output_path.text(),
            "ZIP archives (*.zip)",
            options=QFileDialog.Option.DontConfirmOverwrite
            | QFileDialog.Option.DontUseNativeDialog,
        )
        if path:
            self.output_path.setText(path)

    def start(self) -> None:
        if self.busy or self.pages.currentIndex() != 0:
            return
        self.validate_output()
        if not self.begin.isEnabled():
            return
        self.destination = Path(self.output_path.text()).expanduser().absolute()
        self.pages.setCurrentIndex(1)
        (skip, refill) = (self.skip_drc.isChecked(), self.refill.isChecked())
        self.steps.setText(
            "✓ Configure  ·  Preparing snapshot"
            if skip
            else "✓ Configure  ·  2 Check & review  ·  3 Export"
        )
        worker = Worker(
            lambda: self.exporter.prepare(
                self.constraints, refill, skip, worker.progress.emit
            )
        )
        worker.progress.connect(
            self.update_progress, Qt.ConnectionType.QueuedConnection
        )
        self.start_worker(worker, self.completed)

    def completed(self, value: object, error: object) -> None:
        super().completed(value, error)
        if error is not None:
            return
        self.steps.setText("✓ Configure  ·  2 Review errors  ·  3 Export")
        if any(f.severity == "error" for f in self.findings):
            self.export_button.show()
        else:
            self.start_export()

    def start_export(self) -> None:
        if self.busy or self.destination is None:
            return
        if any(f.severity == "error" for f in self.findings) and (
            not self.acknowledge.isChecked()
        ):
            return
        self.export_button.hide()
        self.show_button.hide()
        self.pages.setCurrentIndex(3)
        self.steps.setText(
            "✓ Configure  ·  DRC skipped  ·  3 Export"
            if self.skip_drc.isChecked()
            else "✓ Configure  ·  ✓ Check & review  ·  3 Export"
        )
        destination = self.destination
        acknowledged = self.acknowledge.isChecked()
        self.export_note.setText(
            f"Output: {destination}\nTemporary project: {self.workflow.root}"
        )
        worker = Worker(
            lambda: self.exporter.export(
                destination, acknowledged, lambda name: worker.progress.emit(0, name)
            )
        )
        worker.progress.connect(self.exported_file, Qt.ConnectionType.QueuedConnection)
        self.start_worker(worker, self.export_completed)

    def exported_file(self, _index: int, name: str) -> None:
        item = QListWidgetItem("✓  " + name)
        item.setForeground(QColor("#2d9257"))
        self.export_items.addItem(item)
        self.export_items.scrollToBottom()

    def export_completed(self, value: object, error: object) -> None:
        self.export_progress.setRange(0, 1)
        if error is not None:
            self.export_summary.setText("Export failed")
            self.export_summary.setStyleSheet("QLabel#section { color: #bb3030; }")
            self.export_note.setText(
                f"{error}\n\nTemporary files: {self.workflow.root}"
            )
            return
        self.export_progress.setValue(1)
        self.export_summary.setText("Manufacturing archive saved")
        self.steps.setText(
            "✓ Configure  ·  DRC skipped  ·  ✓ Export"
            if self.skip_drc.isChecked()
            else "✓ Configure  ·  ✓ Check & review  ·  ✓ Export"
        )
