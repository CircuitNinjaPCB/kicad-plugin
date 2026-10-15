import importlib
import json
import os
import subprocess
import tempfile
import threading
import time
from pathlib import Path

from .plugin_environment import check_environment, child_environment, environment_path

pcbnew = importlib.import_module("pcbnew")
wx = importlib.import_module("wx")


class ManufacturingConstraintsSwig(pcbnew.ActionPlugin):
    def defaults(self) -> None:
        self.name = "CircuitNinja: Set manufacturing rules"
        self.window_title = "CircuitNinja · Set manufacturing rules"
        self.category = "CircuitNinja"
        self.description = (
            "Check the plugin environment and open manufacturing constraints"
        )
        self.show_toolbar_button = True
        self.icon_file_name = str(Path(__file__).parent / "icons" / "constraints.png")
        self.dark_icon_file_name = str(
            Path(__file__).parent / "icons" / "constraints_dark.png"
        )
        self._checking = False
        self._progress = None

    def Run(self) -> None:
        if self._checking:
            wx.MessageBox(
                "The plugin environment is being checked. Please wait.",
                self.window_title,
            )
            return
        try:
            board = pcbnew.GetBoard()
            filename = board.GetFileName() if board else ""
            if not filename or not Path(filename).is_file():
                raise RuntimeError(
                    "Save the board in a KiCad project before using this action."
                )
            board_path = Path(filename).resolve()
            project_path = board_path.with_suffix(".kicad_pro")
            if not project_path.is_file():
                raise RuntimeError(
                    f"A matching saved project is required:\n{project_path}"
                )
            executable = str(wx.StandardPaths.Get().GetExecutablePath())
            context = {
                "project_path": str(project_path),
                "board_path": str(board_path),
                "binaries": {
                    "kicad"
                    if Path(executable).stem == "kicad"
                    else "pcbnew": executable
                },
                "appimage": os.environ.get("APPIMAGE", ""),
                "origin_pid": os.getpid(),
                "observed_origin": True,
                "monitor_error": "",
            }
            environment = environment_path(pcbnew.GetBuildVersion())
        except (OSError, RuntimeError, ValueError, KeyError, AttributeError) as exc:
            wx.MessageBox(str(exc), self.window_title, wx.OK | wx.ICON_ERROR)
            return
        self._checking = True
        self._progress = wx.BusyInfo(
            "Checking the CircuitNinja environment and opening constraints…"
        )
        threading.Thread(
            target=self._launch, args=(environment, context), daemon=True
        ).start()

    def _launch(self, environment: Path, context: dict[str, object]) -> None:
        error = ""
        try:
            plugin_dir = Path(__file__).resolve().parent
            python = check_environment(environment, plugin_dir)
            with tempfile.TemporaryDirectory(prefix="circuitninja-launch-") as scratch:
                ready = Path(scratch) / "ready"
                with tempfile.NamedTemporaryFile(
                    prefix="circuitninja-constraints-", suffix=".log", delete=False
                ) as log:
                    context["ready_file"] = str(ready)
                    process = subprocess.Popen(
                        [
                            str(python),
                            "-E",
                            "-s",
                            str(plugin_dir / "manufacturing_constraints_ipc.py"),
                            "--window",
                            json.dumps(context),
                        ],
                        env=child_environment(),
                        stdin=subprocess.DEVNULL,
                        stdout=log,
                        stderr=log,
                        close_fds=True,
                        start_new_session=os.name != "nt",
                        creationflags=getattr(subprocess, "DETACHED_PROCESS", 8)
                        | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 512)
                        if os.name == "nt"
                        else 0,
                    )
                deadline = time.monotonic() + 15
                while not ready.exists():
                    if process.poll() is not None:
                        detail = Path(log.name).read_text(errors="replace")[-3000:]
                        raise RuntimeError(
                            f"The constraints window could not start.\n{detail}\nLog: {log.name}"
                        )
                    if time.monotonic() >= deadline:
                        raise RuntimeError(
                            f"The constraints window has not confirmed startup.\nLog: {log.name}"
                        )
                    time.sleep(0.1)
        except subprocess.TimeoutExpired:
            error = "The plugin environment check timed out. Please try again after KiCad finishes setting up dependencies."
        except (
            OSError,
            RuntimeError,
            ValueError,
            KeyError,
            TypeError,
            subprocess.SubprocessError,
        ) as exc:
            error = str(exc)
        wx.CallAfter(self._finished, error)

    def _finished(self, error: str) -> None:
        self._progress = None
        self._checking = False
        if error:
            wx.MessageBox(error, self.window_title, wx.OK | wx.ICON_ERROR)
