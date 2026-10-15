import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
import zipfile
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from drc_workflow import DrcWorkflow, Finding, run_cli
from models import ManufacturingConstraints
from plugin_environment import child_environment
from pydantic import BaseModel, Field


class ExportProbe(BaseModel):
    version: str
    metadata: dict[str, Any]
    omitted: dict[str, str]
    copper_layers: list[str] = Field(min_length=2)


def output_problem(text: str) -> tuple[str, str]:
    if not text.strip():
        return ("Choose an output ZIP file.", "error")
    path = Path(text).expanduser()
    if path.suffix.lower() != ".zip":
        return ("The output filename must end in .zip.", "error")
    if not path.parent.is_dir():
        return ("The parent folder must already exist.", "error")
    if path.is_symlink() or (path.exists() and (not path.is_file())):
        return ("Choose a regular file, not a folder or symbolic link.", "error")
    if path.exists():
        return ("This file already exists and will be overwritten.", "warning")
    return ("A ZIP archive will be created here.", "info")


def export_files(
    cli: Path,
    python: list[str],
    snapshot: Path,
    output: Path,
    version: str,
    *,
    progress: Callable[[str], None],
) -> ExportProbe:
    output.mkdir(exist_ok=False)
    plot = output.parent / "plot"
    plot.mkdir(exist_ok=False)
    plot_board = plot / snapshot.name
    shutil.copyfile(
        snapshot.with_suffix(".kicad_pro"), plot_board.with_suffix(".kicad_pro")
    )
    if not python:
        raise RuntimeError(
            "Reopen the hub from its SWIG button to supply KiCad's Python."
        )
    result = subprocess.run(
        [
            *python,
            "-B",
            str(Path(__file__).with_name("export_swig.py")),
            str(snapshot),
            str(output),
            str(plot_board),
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
    (output.parent / "export-probe.log").write_text(
        result.stdout + result.stderr, encoding="utf-8"
    )
    if result.returncode:
        raise RuntimeError(
            f"KiCad export preparation failed ({result.returncode}): {result.stderr[-2000:]}"
        )
    try:
        probe = ExportProbe.model_validate_json(result.stdout)
    except ValueError as exc:
        raise RuntimeError(
            "KiCad returned invalid export metadata; see export-probe.log."
        ) from exc
    if not re.match(f"^{re.escape(version)}(?:\\D|$)", probe.version):
        raise RuntimeError(f"SWIG/editor version mismatch: {probe.version} / {version}")
    expected_copper = [
        "F.Cu",
        *[f"In{i}.Cu" for i in range(1, len(probe.copper_layers) - 1)],
        "B.Cu",
    ]
    if probe.copper_layers != expected_copper:
        raise RuntimeError("KiCad returned an unexpected copper layer stack.")

    def done(name: str) -> None:
        file = output / name
        if not file.is_file() or not file.stat().st_size:
            raise RuntimeError(f"KiCad did not produce {name}.")
        progress(name)

    done(f"{snapshot.stem}-bom.csv")
    done(f"{snapshot.stem}.d356")
    layers = [
        *probe.copper_layers,
        "F.Mask",
        "B.Mask",
        "F.Silkscreen",
        "B.Silkscreen",
        "F.Paste",
        "B.Paste",
        "Edge.Cuts",
    ]
    run_cli(
        cli,
        [
            "pcb",
            "export",
            "gerbers",
            "--output",
            str(output),
            "--layers",
            ",".join(layers),
            "--no-protel-ext",
            str(plot_board),
        ],
    )
    for layer in layers:
        done(f"{snapshot.stem}-{layer.replace('.', '_')}.gbr")
    done(f"{snapshot.stem}-job.gbrjob")
    run_cli(
        cli,
        [
            "pcb",
            "export",
            "drill",
            "--output",
            str(output),
            "--format",
            "excellon",
            "--excellon-units",
            "mm",
            "--excellon-separate-th",
            "--drill-origin",
            "absolute",
            str(plot_board),
        ],
    )
    for name in (f"{snapshot.stem}-PTH.drl", f"{snapshot.stem}-NPTH.drl"):
        if not (output / name).is_file():
            raise RuntimeError(
                f"KiCad did not produce {name} (required even when empty)."
            )
    for drill in sorted(output.glob("*.drl")):
        done(drill.name)
    return probe


def write_archive(files: Path, destination: Path) -> None:
    (message, severity) = output_problem(str(destination))
    if severity == "error":
        raise ValueError(message)
    (fd, temporary) = tempfile.mkstemp(
        prefix=".circuitninja-", suffix=".zip", dir=destination.parent
    )
    os.close(fd)
    try:
        with zipfile.ZipFile(temporary, "w", zipfile.ZIP_DEFLATED) as archive:
            for file in sorted(files.iterdir()):
                if file.is_file():
                    archive.write(file, file.name)
        os.replace(temporary, destination)
    finally:
        Path(temporary).unlink(missing_ok=True)


class ExportWorkflow:
    def __init__(self, drc: DrcWorkflow) -> None:
        self.drc = drc
        self.findings: list[Finding] = []
        self.prepared = False
        self.skipped = False
        self.refill = False
        self.constraints: ManufacturingConstraints | None = None

    def prepare(
        self,
        constraints: ManufacturingConstraints,
        refill: bool,
        skip: bool,
        progress: Callable[[int, str], None],
    ) -> list[Finding]:
        (self.skipped, self.refill, self.constraints) = (skip, refill, constraints)
        if skip:
            self.drc.prepare(constraints, refill, progress)
            self.findings = []
        else:
            self.findings = self.drc.run(constraints, refill, progress)
        self.prepared = True
        return self.findings

    def export(
        self, destination: Path, acknowledge: bool, progress: Callable[[str], None]
    ) -> dict:
        if not self.prepared or self.drc.root is None or self.constraints is None:
            raise RuntimeError("Capture the board before exporting.")
        if any(f.severity == "error" for f in self.findings) and (not acknowledge):
            raise RuntimeError("Acknowledge all DRC errors before exporting.")
        (message, severity) = output_problem(str(destination))
        if severity == "error":
            raise ValueError(message)
        root = self.drc.root
        snapshot = root / self.drc.source.name
        output = root / "fabrication"
        probe = export_files(
            self.drc.cli,
            self.drc.swig_command,
            snapshot,
            output,
            self.drc.version,
            progress=progress,
        )
        status = (
            "skipped"
            if self.skipped
            else "errors_acknowledged"
            if any(f.severity == "error" for f in self.findings)
            else "passed"
        )
        report = {"status": status, "findings": [f.model_dump() for f in self.findings]}
        (output / "findings.json").write_text(
            json.dumps(report, indent=2), encoding="utf-8"
        )
        progress("findings.json")
        if not self.skipped:
            shutil.copyfile(root / "drc.json", output / "drc.json")
            progress("drc.json")
        metadata = {
            "schema_version": 1,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "kicad_version": probe.version,
            "board": snapshot.name,
            "snapshot_sha256": hashlib.sha256(snapshot.read_bytes()).hexdigest(),
            "units": "mm",
            "measurements": probe.metadata,
            "omitted_measurements": probe.omitted,
            "drc": {
                "status": status,
                "error_count": sum(f.severity == "error" for f in self.findings)
                if not self.skipped
                else None,
                "constraints": self.constraints.model_dump(mode="json"),
            },
            "zones_refilled": self.refill,
            "drill_format": "excellon",
            "bom_source": "PCB footprints; one row per component, excluded-from-BOM footprints omitted, DNP included and flagged. Not a schematic BOM.",
            "netlist_format": "IPC-D-356 PCB connectivity",
        }
        (output / "circuitninja.json").write_text(
            json.dumps(metadata, indent=2), encoding="utf-8"
        )
        progress("circuitninja.json")
        write_archive(output, destination)
        progress("ZIP archive saved")
        return metadata
