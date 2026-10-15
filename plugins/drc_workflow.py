import json
import re
import subprocess
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

from drc_profile import ProfileReport, profile_messages
from kicad_constraints import (
    MANUFACTURING_ERRORS,
    OverrideOptions,
    override_manufacturing_constraints,
)
from kipy import KiCad
from kipy.board import Board
from kipy.board_types import BoardItem
from kipy.proto.common.commands.editor_commands_pb2 import RAS_OK
from models import ManufacturingConstraints
from plugin_environment import child_environment
from pydantic import BaseModel, ConfigDict, Field


class ReportItem(BaseModel):
    model_config = ConfigDict(extra="ignore")
    uuid: str
    description: str = ""
    pos: dict[str, float] = Field(default_factory=dict)


class Finding(BaseModel):
    model_config = ConfigDict(extra="ignore")
    type: str
    description: str
    severity: str
    items: list[ReportItem] = Field(default_factory=list)


class DrcReport(BaseModel):
    model_config = ConfigDict(extra="ignore")
    coordinate_units: str
    violations: list[Finding]
    unconnected_items: list[Finding]
    schematic_parity: list[Finding]


def read_report(path: Path) -> list[Finding]:
    report = DrcReport.model_validate_json(path.read_text(encoding="utf-8"))
    if report.coordinate_units != "mm":
        raise ValueError("KiCad returned unexpected DRC coordinate units.")
    findings = report.violations + report.unconnected_items + report.schematic_parity
    if any(
        f.type not in MANUFACTURING_ERRORS | {"invalid_outline"}
        or f.severity != "error"
        for f in findings
    ):
        raise ValueError(
            "KiCad returned checks outside the manufacturing policy. See the report."
        )
    return findings


def prepare_policy(board: Path, constraints: ManufacturingConstraints) -> None:
    project = board.with_suffix(".kicad_pro")
    data = json.loads(project.read_text(encoding="utf-8"))
    settings = data.setdefault("board", {}).setdefault("design_settings", {})
    settings["drc_exclusions"] = []
    settings["rules"] = {
        "min_clearance": 0,
        "min_hole_clearance": 0,
        "min_copper_edge_clearance": 0,
    }
    project.write_text(json.dumps(data), encoding="utf-8")
    override_manufacturing_constraints(
        constraints,
        board,
        OverrideOptions(custom_rules="replace", other_severities="silence"),
    )
    data = json.loads(project.read_text(encoding="utf-8"))
    data["board"]["design_settings"]["rule_severities"]["invalid_outline"] = "error"
    project.write_text(json.dumps(data), encoding="utf-8")


def run_cli(cli: Path, args: list[str], *, timeout: int = 180) -> str:
    result = subprocess.run(
        [str(cli), *args],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        env=child_environment(),
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    if result.returncode:
        raise RuntimeError(
            f"KiCad CLI failed ({result.returncode}).\n{(result.stdout + result.stderr)[-4000:]}"
        )
    return result.stdout


def run_drc(cli: Path, board: Path, *, refill: bool) -> list[Finding]:
    report = board.parent / "drc.json"
    run_cli(
        cli,
        [
            "pcb",
            "drc",
            "--format",
            "json",
            "--units",
            "mm",
            "--severity-error",
            "--all-track-errors",
            "--output",
            str(report),
            *(["--refill-zones"] if refill else []),
            str(board),
        ],
    )
    return read_report(report)


def check_profile(
    command: list[str], board: Path, constraints: ManufacturingConstraints, version: str
) -> list[Finding]:
    try:
        if not command:
            raise ValueError(
                "The hub did not supply KiCad's Python interpreter. Reopen the hub from its SWIG button."
            )
        result = subprocess.run(
            [
                *command,
                "-B",
                str(Path(__file__).with_name("profile_swig_probe.py")),
                str(board),
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=60,
            check=False,
            env=child_environment(),
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        (board.parent / "profile-probe.log").write_text(result.stderr, encoding="utf-8")
        if result.returncode:
            raise ValueError(
                f"SWIG probe exited with {result.returncode}: {result.stderr[-2000:]}"
            )
        try:
            report = ProfileReport.model_validate_json(result.stdout)
        except ValueError as exc:
            raise ValueError(
                "SWIG probe returned invalid profile JSON. " + result.stdout[-1000:]
            ) from exc
        if not report.version.startswith(version) or (
            len(report.version) > len(version)
            and report.version[len(version)].isdigit()
        ):
            raise ValueError(
                f"SWIG/editor version mismatch: {report.version} / {version}"
            )
        (board.parent / "profile.json").write_text(
            report.model_dump_json(indent=2), encoding="utf-8"
        )
        messages = profile_messages(report, constraints)
    except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
        messages = [
            ("board_profile_check", f"Cannot verify board shape or size: {exc}")
        ]
    return [
        Finding(type=kind, description=message, severity="error")
        for (kind, message) in messages
    ]


def refill_board(command: list[str], board: Path, version: str) -> None:
    result = subprocess.run(
        [
            *command,
            "-B",
            str(Path(__file__).with_name("refill_swig.py")),
            str(board),
            version,
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=180,
        check=False,
        env=child_environment(),
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    (board.parent / "refill.log").write_text(
        result.stdout + result.stderr, encoding="utf-8"
    )
    if result.returncode:
        raise RuntimeError(
            f"Zone refill failed ({result.returncode}): {result.stderr[-2000:]}"
        )
    try:
        valid = json.loads(result.stdout) == {"refilled": True}
    except ValueError:
        valid = False
    if not valid:
        raise RuntimeError(
            "Zone refill returned an invalid completion message; see refill.log."
        )


def board_path(board: Board) -> Path:
    return (Path(board.document.project.path) / board.name).resolve()


class DrcWorkflow:
    def __init__(self, api: KiCad, context: dict[str, Any]) -> None:
        self.api = api
        self.swig_command = list(context.get("swig_command", []))
        self.source = Path(context["board_path"]).resolve()
        self.project = Path(context["project_path"]).resolve()
        self.board = self.current_board()
        self.document = self.board.document.SerializeToString()
        version = api.get_version()
        self.version = f"{version.major}.{version.minor}.{version.patch}"
        if version.major not in (9, 10):
            raise RuntimeError("Manufacturing DRC currently supports KiCad 9 and 10.")
        self.cli = Path(api.get_kicad_binary_path("kicad-cli"))
        appimage = str(context.get("appimage", ""))
        if appimage.endswith("_pcbnew"):
            candidate = Path(appimage.removesuffix("_pcbnew") + "_kicad-cli")
            if candidate.is_file():
                self.cli = candidate
        cli_version = run_cli(self.cli, ["version"], timeout=15).strip()
        if not re.match(
            f"^{version.major}\\.{version.minor}\\.{version.patch}(?:\\D|$)",
            cli_version,
        ):
            raise RuntimeError(
                f"Editor/CLI version mismatch: {version.full_version} / {cli_version}"
            )
        self.can_refill = bool(self.swig_command)
        self.root: Path | None = None

    def current_board(self) -> Board:
        board = self.api.get_board()
        if board_path(board) != self.source:
            raise RuntimeError(
                "The active PCB changed. Reopen Run DRC from the intended board."
            )
        if (
            hasattr(self, "document")
            and board.document.SerializeToString() != self.document
        ):
            raise RuntimeError("The PCB document changed. Run manufacturing DRC again.")
        return board

    def capture(self, progress: Callable[[int, str], None]) -> Path:
        board = self.current_board()
        self.root = Path(tempfile.mkdtemp(prefix="circuitninja-drc-"))
        progress(0, str(self.root))
        snapshot = self.root / self.source.name
        text = board.get_as_string()
        if not text.lstrip().startswith("(kicad_pcb"):
            raise ValueError("KiCad did not return a complete PCB snapshot.")
        snapshot.write_text(text, encoding="utf-8")
        snapshot.with_suffix(".kicad_pro").write_bytes(self.project.read_bytes())
        progress(1, str(self.root))
        return snapshot

    def prepare(
        self,
        constraints: ManufacturingConstraints,
        refill: bool,
        progress: Callable[[int, str], None],
    ) -> Path:
        if refill and (not self.can_refill):
            raise RuntimeError(
                "Reopen the hub from its SWIG button to enable zone refill."
            )
        snapshot = self.capture(progress)
        prepare_policy(snapshot, constraints)
        if refill:
            refill_board(self.swig_command, snapshot, self.version)
        progress(2, str(self.root))
        return snapshot

    def run(
        self,
        constraints: ManufacturingConstraints,
        refill: bool,
        progress: Callable[[int, str], None],
    ) -> list[Finding]:
        snapshot = self.prepare(constraints, refill, progress)
        assert self.root is not None
        findings = run_drc(self.cli, snapshot, refill=False)
        findings.extend(
            check_profile(self.swig_command, snapshot, constraints, self.version)
        )
        (self.root / "findings.json").write_text(
            json.dumps([f.model_dump() for f in findings], indent=2), encoding="utf-8"
        )
        progress(3, str(self.root))
        return findings

    def show(self, finding: Finding) -> None:
        board = self.current_board()
        ids = {item.uuid for item in finding.items if item.uuid}
        if not ids:
            raise ValueError("This finding has no selectable PCB objects.")
        items = [
            item
            for getter in (
                board.get_tracks,
                board.get_vias,
                board.get_pads,
                board.get_shapes,
                board.get_zones,
                board.get_footprints,
            )
            for item in getter()
            if isinstance(item, BoardItem) and item.id.value in ids
        ]
        if ids != {item.id.value for item in items}:
            raise ValueError(
                "An affected object was removed or is unavailable. Run DRC again."
            )
        board.clear_selection()
        board.add_to_selection(items)
        selected = {
            item.id.value
            for item in board.get_selection()
            if isinstance(item, BoardItem)
        }
        if not ids.issubset(selected):
            raise RuntimeError(
                "KiCad could not select all objects. Check layer visibility and selection filters."
            )
        if self.api.run_action("common.Control.zoomFitSelection").status != RAS_OK:
            raise RuntimeError("KiCad did not accept zoom-to-selection.")


def connect(context: dict[str, Any]) -> DrcWorkflow:
    root = Path(tempfile.gettempdir()) / "kicad"
    pid_socket = root / f"api-{context.get('origin_pid')}.sock"
    socket = pid_socket if pid_socket.exists() else root / "api.sock"
    api = KiCad(socket_path=f"ipc://{socket}", kicad_token="", timeout_ms=5000)
    try:
        return DrcWorkflow(api, context)
    except Exception as exc:
        raise RuntimeError(
            f"Cannot prepare manufacturing DRC. Enable KiCad's IPC API, keep the original PCB Editor open and close any modal dialogs.\n\n{exc}"
        ) from exc
