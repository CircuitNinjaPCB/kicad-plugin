import importlib
import json
import sys


def inspect_profile(board_path: str) -> dict:
    pcbnew = importlib.import_module("pcbnew")
    version = pcbnew.GetBuildVersion()
    major = int(version.split(".")[0])
    if major not in (9, 10):
        raise RuntimeError(f"Unsupported KiCad SWIG version: {version}")
    board = pcbnew.LoadBoard(board_path)
    if board is None:
        raise RuntimeError("KiCad could not load the board snapshot.")
    polygons = pcbnew.SHAPE_POLY_SET()
    valid = (
        board.GetBoardPolygonOutlines(polygons, False)
        if major == 10
        else board.GetBoardPolygonOutlines(polygons)
    )
    shapes = []
    if valid:
        for index in range(polygons.OutlineCount()):
            outline = polygons.Outline(index)
            bounds = outline.BBox()
            area = abs(outline.Area()) - sum(
                abs(polygons.Hole(index, hole).Area())
                for hole in range(polygons.HoleCount(index))
            )
            shapes.append(
                {
                    "area_mm2": area / 1000000000000.0,
                    "width_mm": pcbnew.ToMM(bounds.GetWidth()),
                    "height_mm": pcbnew.ToMM(bounds.GetHeight()),
                    "holes": polygons.HoleCount(index),
                }
            )
    return {"version": version, "valid": bool(valid), "shapes": shapes}


if __name__ == "__main__":
    print(json.dumps(inspect_profile(sys.argv[1]), allow_nan=False))
