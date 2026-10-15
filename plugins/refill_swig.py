import importlib
import json
import sys


def refill_snapshot(path: str, version: str) -> None:
    pcbnew = importlib.import_module("pcbnew")
    actual = pcbnew.GetBuildVersion()
    if not actual.startswith(version) or (
        len(actual) > len(version) and actual[len(version)].isdigit()
    ):
        raise RuntimeError(f"SWIG/editor version mismatch: {actual} / {version}")
    board = pcbnew.LoadBoard(path)
    if board is None:
        raise RuntimeError("Cannot load the scratch board for refill.")
    board.BuildConnectivity()
    if not pcbnew.ZONE_FILLER(board).Fill(board.Zones()):
        raise RuntimeError("KiCad could not complete zone filling.")
    if not pcbnew.SaveBoard(path, board):
        raise RuntimeError("KiCad could not save the refilled scratch board.")


if __name__ == "__main__":
    refill_snapshot(sys.argv[1], sys.argv[2])
    print(json.dumps({"refilled": True}))
