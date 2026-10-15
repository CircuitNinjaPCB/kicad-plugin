import csv
import errno
import hashlib
import io
import json
import logging
import os
import subprocess
import sys
import sysconfig
import tempfile
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

KICAD_NAMES = {"kicad", "pcbnew", "eeschema", "gerbview", "bitmap2component"}


def prefer_environment_packages() -> None:
    if sys.prefix != sys.base_prefix:
        packages = list(
            dict.fromkeys(sysconfig.get_path(name) for name in ("purelib", "platlib"))
        )
        sys.path[:] = packages + [p for p in sys.path if p not in packages]


def kicad_processes() -> dict[int, str]:
    found: dict[int, str] = {}
    if sys.platform.startswith("linux"):
        for entry in Path("/proc").iterdir():
            if not entry.name.isdecimal():
                continue
            try:
                name = (entry / "comm").read_text().strip().lower()
                state = (entry / "stat").read_text().rsplit(")", 1)[1].split()[0]
            except FileNotFoundError:
                continue
            if name in KICAD_NAMES and state != "Z":
                found[int(entry.name)] = name
    elif sys.platform == "win32":
        completed = subprocess.run(
            ["tasklist.exe", "/FO", "CSV", "/NH"],
            capture_output=True,
            text=True,
            check=True,
            timeout=5,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        rows = list(csv.reader(io.StringIO(completed.stdout)))
        if not rows or any(len(row) < 2 or not row[1].isdigit() for row in rows):
            raise RuntimeError("Windows did not return a complete process list.")
        for row in rows:
            name = Path(row[0]).stem.lower()
            if name in KICAD_NAMES:
                found[int(row[1])] = name
    else:
        raise RuntimeError("Automatic exit detection supports Linux and Windows only.")
    return found


def originating_process_observed(processes: dict[int, str]) -> bool:
    parent = os.getppid()
    visited: set[int] = set()
    while parent > 1 and parent not in visited:
        if parent in processes:
            return True
        visited.add(parent)
        if not sys.platform.startswith("linux"):
            return False
        try:
            fields = (
                (Path("/proc") / str(parent) / "stat")
                .read_text()
                .rsplit(")", 1)[1]
                .split()
            )
            parent = int(fields[1])
        except (OSError, ValueError, IndexError):
            return False
    return False


def detached_options() -> dict[str, Any]:
    if sys.platform == "win32":
        return {
            "creationflags": getattr(subprocess, "DETACHED_PROCESS", 8)
            | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 512)
        }
    return {"start_new_session": True}


def capture_context() -> dict[str, Any]:
    from kipy import KiCad
    from kipy.errors import ApiError, ConnectionError

    api = KiCad(timeout_ms=3000)
    board = api.get_board()
    project = board.get_project()
    if not project.path or not project.name:
        raise RuntimeError(
            "Save the board in a KiCad project before using this action."
        )
    project_path = Path(project.path) / (project.name + ".kicad_pro")
    board_path = Path(project.path) / Path(board.name).name
    if board_path.with_suffix(".kicad_pro").resolve() != project_path.resolve():
        raise RuntimeError(
            "This prototype requires the board and project to have matching names."
        )
    if not project_path.is_file():
        raise RuntimeError(f"The project file does not exist: {project_path}")
    binaries: dict[str, str] = {}
    for name in ("kicad", "pcbnew"):
        try:
            path = api.get_kicad_binary_path(name)
            if Path(path).is_file():
                binaries[name] = path
        except (ApiError, ConnectionError, OSError) as exc:
            logging.getLogger(__name__).warning("Cannot locate %s: %s", name, exc)
    try:
        processes = kicad_processes()
        observed = originating_process_observed(processes)
        error = "" if observed else "No originating KiCad process could be identified."
    except (OSError, RuntimeError, subprocess.SubprocessError, ValueError) as exc:
        (observed, error) = (False, str(exc))
    return {
        "project_path": str(project_path.resolve()),
        "board_path": str(board_path),
        "binaries": binaries,
        "appimage": os.environ.get("APPIMAGE", ""),
        "observed_origin": observed,
        "monitor_error": error,
    }


@contextmanager
def window_guard(project: Path) -> Generator[tuple[bool, Path], None, None]:
    identity = f"{Path.home()}\n{os.path.normcase(str(project.resolve()))}"
    key = hashlib.sha256(identity.encode()).hexdigest()
    directory = Path(tempfile.gettempdir()) / f"circuitninja-window-{key}"
    directory.mkdir(mode=448, exist_ok=True)
    focus_request = directory / "focus"
    with (directory / "lock").open("a+b") as lock:
        if sys.platform == "win32":
            import msvcrt

            if lock.seek(0, os.SEEK_END) == 0:
                lock.write(b"\x00")
                lock.flush()
            lock.seek(0)
            try:
                msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError as exc:
                if exc.errno not in (errno.EACCES, errno.EAGAIN, errno.EDEADLK):
                    raise
                acquired = False
            else:
                acquired = True
        else:
            import fcntl

            try:
                fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                acquired = False
            else:
                acquired = True
        yield (acquired, focus_request)


def show_window(context: dict[str, Any]) -> None:
    with window_guard(Path(context["project_path"])) as (acquired, focus_request):
        if not acquired:
            focus_request.touch()
            if ready_file := context.get("ready_file"):
                Path(ready_file).write_text("already open", encoding="utf-8")
            return
        _show_window(context, focus_request)


def reopen_project(context: dict[str, Any]) -> None:
    binaries = context["binaries"]
    binary = binaries.get("kicad") or binaries.get("pcbnew")
    target = Path(
        context["project_path"] if binaries.get("kicad") else context["board_path"]
    )
    if appimage := context.get("appimage"):
        (binary, target) = (appimage, Path(context["project_path"]))
    if not binary or not Path(binary).is_file() or (not target.is_file()):
        raise RuntimeError(
            "The captured KiCad program or project is missing. Reopen the project manually."
        )
    subprocess.Popen(
        [binary, str(target)],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        close_fds=True,
        **detached_options(),
    )


def _show_window(context: dict[str, Any], focus_request: Path) -> None:
    from constraints_source import load_constraints
    from manufacturing_constraints_window import ConstraintsWindow
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication, QMessageBox
    from service_config import DEFAULT_ORIGIN
    from window_position import show_centered

    os.environ.pop("KICAD_API_TOKEN", None)
    os.environ.pop("KICAD_API_SOCKET", None)
    app = QApplication(sys.argv[:1])
    app.setApplicationName("CircuitNinja")
    app.setStyle("Fusion")
    app.setQuitOnLastWindowClosed(False)
    handler = None
    log_path = None
    try:
        with tempfile.NamedTemporaryFile(
            prefix="circuitninja-constraints-", suffix=".log", delete=False
        ) as stream:
            log_path = Path(stream.name)
        handler = logging.FileHandler(log_path, encoding="utf-8")
        handler.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
        )
        logging.getLogger().addHandler(handler)
    except OSError:
        logging.getLogger(__name__).exception("Cannot create workflow log")
    if ready_file := context.get("ready_file"):
        try:
            Path(ready_file).write_text("loading constraints", encoding="utf-8")
        except OSError:
            pass
    root = None
    try:
        result = load_constraints(context.get("service_origin", DEFAULT_ORIGIN))
        if result.warning:
            QMessageBox.warning(
                None,
                "CircuitNinja · Using saved manufacturing constraints",
                result.warning,
            )
        root = ConstraintsWindow(
            result.constraints,
            context,
            processes=kicad_processes,
            reopen=lambda: reopen_project(context),
            log_path=log_path,
        )

        def focus_window() -> None:
            if root.isMinimized():
                root.showNormal()
            root.raise_()
            root.activateWindow()
            if handle := root.windowHandle():
                handle.requestActivate()

        focus_timer = QTimer(root)
        focus_timer.setSingleShot(True)
        focus_timer.timeout.connect(focus_window)

        def poll_focus_request() -> None:
            try:
                focus_request.unlink()
            except FileNotFoundError:
                return
            except OSError:
                logging.getLogger(__name__).exception(
                    "Cannot read window focus request"
                )
                return
            focus_timer.start(750)

        requests = QTimer(root)
        requests.timeout.connect(poll_focus_request)
        requests.start(250)
        show_centered(root)
        app.setQuitOnLastWindowClosed(True)
        focus_timer.start(750)
        app.exec()
    except Exception as exc:
        logging.getLogger(__name__).exception("Cannot open manufacturing constraints")
        if root is not None:
            root.failed = True
            root.close()
        message = str(exc) or type(exc).__name__
        if log_path is not None:
            message += f"\n\nLog: {log_path}"
        QMessageBox.critical(
            None, "CircuitNinja · Cannot open manufacturing constraints", message
        )
    finally:
        if handler is not None:
            logging.getLogger().removeHandler(handler)
            handler.close()
        app.quit()


def main() -> None:
    prefer_environment_packages()
    if len(sys.argv) == 3 and sys.argv[1] == "--window":
        show_window(json.loads(sys.argv[2]))
        return
    from kipy.errors import ApiError, ConnectionError

    try:
        context = capture_context()
        subprocess.Popen(
            [
                sys.executable,
                str(Path(__file__).resolve()),
                "--window",
                json.dumps(context),
            ],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            close_fds=True,
            **detached_options(),
        )
    except (ApiError, ConnectionError, OSError, RuntimeError, ValueError) as exc:
        from PySide6.QtWidgets import QApplication, QMessageBox

        app = QApplication(sys.argv[:1])
        QMessageBox.critical(
            None, "CircuitNinja · Cannot open manufacturing constraints", str(exc)
        )
        app.quit()


if __name__ == "__main__":
    main()
