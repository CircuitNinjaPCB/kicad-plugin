import json
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from models import ManufacturingConstraints

DRC_CHECKS = [
    "annular_width",
    "clearance",
    "connection_width",
    "copper_edge_clearance",
    "copper_sliver",
    "courtyards_overlap",
    "creepage",
    "diff_pair_gap_out_of_range",
    "diff_pair_uncoupled_length_too_long",
    "drill_out_of_range",
    "duplicate_footprints",
    "extra_footprint",
    "footprint",
    "footprint_filters_mismatch",
    "footprint_symbol_field_mismatch",
    "footprint_symbol_mismatch",
    "footprint_type_mismatch",
    "hole_clearance",
    "hole_near_hole",
    "hole_to_hole",
    "holes_co_located",
    "invalid_outline",
    "isolated_copper",
    "item_on_disabled_layer",
    "items_not_allowed",
    "length_out_of_range",
    "lib_footprint_issues",
    "lib_footprint_mismatch",
    "malformed_courtyard",
    "microvia_drill_out_of_range",
    "mirrored_text_on_front_layer",
    "missing_courtyard",
    "missing_footprint",
    "missing_tuning_profile",
    "net_conflict",
    "nonmirrored_text_on_back_layer",
    "npth_inside_courtyard",
    "padstack",
    "pth_inside_courtyard",
    "shorting_items",
    "silk_edge_clearance",
    "silk_over_copper",
    "silk_overlap",
    "skew_out_of_range",
    "solder_mask_bridge",
    "starved_thermal",
    "text_height",
    "text_on_edge_cuts",
    "text_thickness",
    "through_hole_pad_without_hole",
    "too_many_vias",
    "track_angle",
    "track_dangling",
    "track_not_centered_on_via",
    "track_on_post_machined_layer",
    "track_segment_length",
    "track_width",
    "tracks_crossing",
    "tuning_profile_track_geometries",
    "unconnected_items",
    "unresolved_variable",
    "via_dangling",
    "via_diameter",
    "zones_intersect",
]
MANUFACTURING_ERRORS = {
    "annular_width",
    "clearance",
    "connection_width",
    "drill_out_of_range",
    "hole_clearance",
    "hole_to_hole",
    "track_width",
    "via_diameter",
}
LIMITATIONS = (
    "Check board minimum/maximum dimensions manually. They are retained in the backup's service payload but are not enforced by this DRC policy.",
)


@dataclass(frozen=True)
class OverrideResult:
    project_path: Path
    rules_path: Path
    backup_directory: Path
    limitations: tuple[str, ...] = LIMITATIONS


CONSTRAINT_LABELS = {
    "min_clearance_mm": "Copper spacing",
    "min_trace_width_mm": "Track width",
    "min_pth_mm": "Plated hole diameter",
    "min_npth_mm": "Non-plated hole diameter",
    "min_annular_ring_mm": "Annular ring",
    "min_hole_to_hole_mm": "Hole spacing",
    "min_hole_to_edge_mm": "Hole to board edge",
}


@dataclass(frozen=True)
class OverrideOptions:
    custom_rules: Literal["replace", "append"] = "replace"
    severity: Literal["error", "warning"] = "error"
    other_severities: Literal["keep", "silence"] = "keep"
    selected_fields: frozenset[str] = frozenset(CONSTRAINT_LABELS)

    def __post_init__(self) -> None:
        if self.custom_rules not in {"replace", "append"}:
            raise ValueError("Unknown custom rule mode")
        if self.severity not in {"error", "warning"}:
            raise ValueError("Unknown manufacturing severity")
        if self.other_severities not in {"keep", "silence"}:
            raise ValueError("Unknown other severity mode")
        if self.selected_fields - CONSTRAINT_LABELS.keys():
            raise ValueError("Unknown manufacturing constraint field")


def _selected_rules(
    c: ManufacturingConstraints, options: OverrideOptions
) -> list[tuple[str, str, float, str | None]]:
    rows: list[tuple[str, str, float, str | None]] = []

    def add(
        field: str, name: str, kind: str, value: float, condition: str | None = None
    ) -> None:
        if field in options.selected_fields:
            rows.append((name, kind, value, condition))

    add("min_clearance_mm", "copper spacing", "clearance", c.min_clearance_mm)
    add("min_trace_width_mm", "track width", "track_width", c.min_trace_width_mm)
    add("min_trace_width_mm", "copper neck", "connection_width", c.min_trace_width_mm)
    add("min_pth_mm", "plated drill", "hole_size", c.min_pth_mm)
    if options.selected_fields & {"min_pth_mm", "min_annular_ring_mm"}:
        rows.append(
            (
                "via diameter",
                "via_diameter",
                c.min_pth_mm + 2 * c.min_annular_ring_mm,
                None,
            )
        )
    add(
        "min_annular_ring_mm",
        "via annular ring",
        "annular_width",
        c.min_annular_ring_mm,
        "A.Type == 'Via'",
    )
    add("min_hole_to_hole_mm", "hole spacing", "hole_to_hole", c.min_hole_to_hole_mm)
    add(
        "min_npth_mm",
        "NPTH diameter or slot width",
        "hole_size",
        c.min_npth_mm,
        "A.Type == 'Pad' && A.Pad_Type == 'NPTH, mechanical'",
    )
    add(
        "min_annular_ring_mm",
        "PTH pad annular ring",
        "annular_width",
        c.min_annular_ring_mm,
        "A.Type == 'Pad' && A.Pad_Type == 'Through-hole'",
    )
    add(
        "min_hole_to_edge_mm",
        "hole to profile",
        "physical_hole_clearance",
        c.min_hole_to_edge_mm,
        "A.Layer == 'Edge.Cuts' || B.Layer == 'Edge.Cuts'",
    )
    return rows


def render_manufacturing_rules(
    constraints: ManufacturingConstraints, options: OverrideOptions | None = None
) -> str:
    options = options or OverrideOptions()
    blocks = ["(version 1)"]
    for name, kind, value, condition in _selected_rules(constraints, options):
        lines = [
            f"(rule {json.dumps('CircuitNinja: ' + name)}",
            f"  (severity {options.severity})",
        ]
        if condition is not None:
            lines.append(f"  (condition {json.dumps(condition)})")
        lines.append(f"  (constraint {kind} (min {value:.12g}mm)))")
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks) + "\n"


def _without_owned_rules(source: str) -> str:
    depth = 0
    start = 0
    tokens: list[str] = []
    removals: list[tuple[int, int]] = []
    index = 0
    while index < len(source):
        char = source[index]
        if char.isspace():
            index += 1
        elif char == "#":
            newline = source.find("\n", index)
            index = len(source) if newline < 0 else newline + 1
        elif char == "(":
            if depth == 0:
                start = index
                tokens = []
            depth += 1
            index += 1
        elif char == ")":
            depth -= 1
            if depth < 0:
                raise ValueError("Unbalanced custom rule parentheses")
            index += 1
            if (
                depth == 0
                and len(tokens) >= 2
                and (tokens[0] == "rule")
                and ("circuitninja" in tokens[1].casefold())
            ):
                removals.append((start, index))
        elif char == '"':
            index += 1
            value: list[str] = []
            while index < len(source) and source[index] != '"':
                if source[index] == "\\":
                    index += 1
                    if index == len(source):
                        raise ValueError("Unterminated custom rule string")
                value.append(source[index])
                index += 1
            if index == len(source):
                raise ValueError("Unterminated custom rule string")
            index += 1
            if depth == 1:
                tokens.append("".join(value))
        else:
            end = index + 1
            while (
                end < len(source)
                and (not source[end].isspace())
                and (source[end] not in '()"#')
            ):
                end += 1
            if depth == 1:
                tokens.append(source[index:end])
            index = end
    if depth:
        raise ValueError("Unbalanced custom rule parentheses")
    for first, last in reversed(removals):
        source = source[:first] + source[last:]
    return source


def _append_rules(source: str, generated: str) -> str:
    preserved = _without_owned_rules(source)
    preserved = preserved.rstrip()
    generated_rules = generated.partition("\n")[2].strip()
    if not preserved:
        preserved = "(version 1)"
    return preserved + ("\n\n" + generated_rules if generated_rules else "") + "\n"


def _replace(path: Path, content: bytes) -> None:
    (fd, temporary) = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(content)
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def _object(parent: dict[str, Any], key: str) -> dict[str, Any]:
    value = parent.setdefault(key, {})
    if not isinstance(value, dict):
        raise TypeError(f"Expected a JSON object for project setting {key!r}")
    return value


def override_manufacturing_constraints(
    constraints: ManufacturingConstraints,
    project_path: str | Path,
    options: OverrideOptions | None = None,
) -> OverrideResult:
    options = options or OverrideOptions()
    constraints = ManufacturingConstraints.model_validate(constraints.model_dump())
    path = Path(project_path).expanduser().absolute()
    if path.suffix not in {".kicad_pro", ".kicad_pcb"}:
        raise ValueError("Expected a .kicad_pro or .kicad_pcb path")
    board = path.with_suffix(".kicad_pcb")
    if not board.is_file():
        raise FileNotFoundError(board)
    project = path.with_suffix(".kicad_pro")
    rules = path.with_suffix(".kicad_dru")
    if project.is_symlink() or rules.is_symlink():
        raise ValueError("Project and rule files must not be symbolic links")
    saved = {p: p.read_bytes() if p.exists() else None for p in (project, rules)}
    project_content = saved[project]
    data: dict[str, Any] = (
        json.loads(project_content)
        if project_content is not None
        else {"meta": {"version": 1}}
    )
    if not isinstance(data, dict):
        raise TypeError("Expected a JSON object in the KiCad project file")
    settings = _object(_object(data, "board"), "design_settings")
    thresholds = _object(settings, "rules")
    c = constraints
    selected = options.selected_fields
    scalar_thresholds = {
        "min_clearance_mm": {"min_clearance": c.min_clearance_mm},
        "min_trace_width_mm": {
            "min_track_width": c.min_trace_width_mm,
            "min_connection": c.min_trace_width_mm,
        },
        "min_pth_mm": {"min_through_hole_diameter": c.min_pth_mm},
        "min_annular_ring_mm": {"min_via_annular_width": c.min_annular_ring_mm},
        "min_hole_to_hole_mm": {"min_hole_to_hole": c.min_hole_to_hole_mm},
    }
    for field in selected:
        thresholds.update(scalar_thresholds.get(field, {}))
    if selected & {"min_pth_mm", "min_annular_ring_mm"}:
        thresholds["min_via_diameter"] = c.min_pth_mm + 2 * c.min_annular_ring_mm
    severities = _object(settings, "rule_severities")
    if options.other_severities == "silence":
        for name in set(DRC_CHECKS) | set(severities):
            severities[name] = "ignore"
    checks_by_kind = {
        "hole_size": "drill_out_of_range",
        "physical_hole_clearance": "hole_clearance",
    }
    for _, kind, _, _ in _selected_rules(c, options):
        severities[checks_by_kind.get(kind, kind)] = options.severity
    rendered = render_manufacturing_rules(c, options)
    saved_rules = saved[rules]
    if options.custom_rules == "append" and saved_rules is not None:
        rendered = _append_rules(saved_rules.decode("utf-8"), rendered)
    updated = {
        project: (json.dumps(data, indent=2, ensure_ascii=False) + "\n").encode(
            "utf-8"
        ),
        rules: rendered.encode("utf-8"),
    }
    backup = Path(
        tempfile.mkdtemp(
            prefix=f"{project.stem}.constraints-backup-", dir=project.parent
        )
    )
    for original, content in saved.items():
        if content is not None:
            (backup / original.name).write_bytes(content)
    (backup / "presence.json").write_text(
        json.dumps({p.name: content is not None for (p, content) in saved.items()}),
        encoding="utf-8",
    )
    (backup / "manufacturing_constraints.json").write_text(
        c.model_dump_json(indent=2), encoding="utf-8"
    )
    written = []
    try:
        for target, content in updated.items():
            _replace(target, content)
            written.append(target)
    except OSError:
        for target in written:
            original_content = saved[target]
            if original_content is None:
                target.unlink(missing_ok=True)
            else:
                _replace(target, original_content)
        raise
    return OverrideResult(project, rules, backup)
