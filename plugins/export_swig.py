import csv
import importlib
import json
import sys
from pathlib import Path

from profile_swig_probe import inspect_profile


def prepare_export(source: Path, output: Path, plot_board: Path) -> dict:
    pcbnew = importlib.import_module("pcbnew")
    board = pcbnew.LoadBoard(str(source))
    if board is None:
        raise RuntimeError("Cannot load the export snapshot.")
    layers = list(board.GetEnabledLayers().CuStack())
    copper = [board.GetStandardLayerName(layer) for layer in layers]
    for layer in board.GetEnabledLayers().Seq():
        board.SetLayerName(layer, board.GetStandardLayerName(layer))
    if not pcbnew.SaveBoard(str(plot_board), board):
        raise RuntimeError("Cannot save the fabrication copy.")
    profile = inspect_profile(str(plot_board))
    metadata = {"copper_layer_count": len(copper), "copper_layers": copper}
    omitted = {}
    if profile["valid"] and len(profile["shapes"]) == 1:
        shape = profile["shapes"][0]
        metadata["board_size_mm"] = {
            "width": shape["width_mm"],
            "height": shape["height_mm"],
        }
    else:
        omitted["board_size_mm"] = (
            "The outline does not describe exactly one valid board shape."
        )
    tracks = list(board.GetTracks())
    vias = [item for item in tracks if isinstance(item, pcbnew.PCB_VIA)]
    traces = [item for item in tracks if not isinstance(item, pcbnew.PCB_VIA)]
    for name, values in (
        ("min_trace_width_mm", [item.GetWidth() for item in traces]),
        (
            "min_via_diameter_mm",
            [
                item.GetWidth(layer)
                for item in vias
                for layer in layers
                if item.IsOnLayer(layer)
            ],
        ),
        ("min_via_drill_mm", [item.GetDrillValue() for item in vias]),
    ):
        if values:
            metadata[name] = pcbnew.ToMM(min(values))
        else:
            omitted[name] = "No corresponding objects in the board."
    edges = [
        item
        for item in list(board.GetDrawings())
        + [g for fp in board.GetFootprints() for g in fp.GraphicalItems()]
        if item.GetLayer() == pcbnew.Edge_Cuts
    ]
    if edges and all(isinstance(item, pcbnew.PCB_SHAPE) for item in edges):
        widths = sorted({item.GetWidth() for item in edges})
        metadata["outline_line_widths_mm"] = [pcbnew.ToMM(w) for w in widths]
        metadata["outline_uniform_line_width"] = len(widths) == 1
    else:
        omitted["outline_uniform_line_width"] = (
            "Edge.Cuts is empty or contains non-shape objects."
        )
    bom = output / f"{source.stem}-bom.csv"
    with bom.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(["Reference", "Value", "Footprint", "Quantity", "DNP", "Side"])
        for fp in sorted(board.GetFootprints(), key=lambda f: f.GetReference()):
            if not fp.IsExcludedFromBOM():
                writer.writerow(
                    [
                        fp.GetReference(),
                        fp.GetValue(),
                        fp.GetFPIDAsString(),
                        1,
                        fp.IsDNP(),
                        "bottom" if fp.IsFlipped() else "top",
                    ]
                )
    netlist = output / f"{source.stem}.d356"
    if not pcbnew.IPC356D_WRITER(board).Write(str(netlist)):
        raise RuntimeError("Cannot export the IPC-D-356 PCB netlist.")
    return {
        "version": pcbnew.GetBuildVersion(),
        "metadata": metadata,
        "omitted": omitted,
        "copper_layers": copper,
    }


if __name__ == "__main__":
    result = prepare_export(Path(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3]))
    print(json.dumps(result, allow_nan=False))
