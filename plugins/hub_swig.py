import importlib
import os
import subprocess
import threading
from pathlib import Path
from typing import Any

from .plugin_environment import environment_path
from .swig_python import swig_command
from .window_launcher import launch_window

pcbnew = importlib.import_module("pcbnew")
wx = importlib.import_module("wx")


class HubSwig(pcbnew.ActionPlugin):
    title = "Menu"
    description_text = "Open CircuitNinja tools and account settings"
    entrypoint = "hub.py"
    icon = "menu.png"
    requires_project = False

    def defaults(self) -> None:
        self.name = f"CircuitNinja: {self.title}"
        self.window_title = f"CircuitNinja · {self.title}"
        self.category = "CircuitNinja"
        self.description = self.description_text
        self.show_toolbar_button = True
        self.icon_file_name = str(Path(__file__).parent / "icons" / self.icon)
        self.dark_icon_file_name = str(
            Path(self.icon_file_name).with_name(f"{Path(self.icon).stem}_dark.png")
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
            board_path = Path(filename).resolve() if filename else None
            project_path = board_path.with_suffix(".kicad_pro") if board_path else None
            project_error = ""
            if board_path is None or not board_path.is_file():
                project_error = (
                    "Save the board in a KiCad project before using this action."
                )
            elif project_path is not None and (not project_path.is_file()):
                project_error = f"A matching saved project is required:\n{project_path}"
            if self.requires_project and project_error:
                raise RuntimeError(project_error)
            executable = str(wx.StandardPaths.Get().GetExecutablePath())
            environment = environment_path(pcbnew.GetBuildVersion())
            context = {
                "project_path": str(project_path) if project_path else "",
                "board_path": str(board_path) if board_path else "",
                "project_error": project_error,
                "environment_path": str(environment),
                "binaries": {
                    "kicad"
                    if Path(executable).stem == "kicad"
                    else "pcbnew": executable
                },
                "appimage": os.environ.get("APPIMAGE", ""),
                "origin_pid": os.getpid(),
                "swig_command": swig_command(),
                "observed_origin": True,
                "monitor_error": "",
            }
        except (OSError, RuntimeError, ValueError, KeyError, AttributeError) as exc:
            wx.MessageBox(str(exc), self.window_title, wx.OK | wx.ICON_ERROR)
            return
        self._checking = True
        self._progress = wx.BusyInfo(
            f"Checking the CircuitNinja environment and opening {self.title.lower()}…"
        )
        threading.Thread(
            target=self._launch, args=(environment, context), daemon=True
        ).start()

    def _launch(self, environment: Path, context: dict[str, Any]) -> None:
        error = ""
        try:
            launch_window(
                environment,
                Path(__file__).resolve().with_name(self.entrypoint),
                context,
            )
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
