import json
import os
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any

if __package__:
    from .plugin_environment import check_environment, child_environment
else:
    from plugin_environment import check_environment, child_environment


def launch_window(environment: Path, entrypoint: Path, context: dict[str, Any]) -> None:
    python = check_environment(environment, plugin_dir=entrypoint.parent)
    with tempfile.TemporaryDirectory(prefix="circuitninja-launch-") as scratch:
        ready = Path(scratch) / "ready"
        with tempfile.NamedTemporaryFile(
            prefix="circuitninja-window-", suffix=".log", delete=False
        ) as log:
            child_context = {**context, "ready_file": str(ready)}
            try:
                process = subprocess.Popen(
                    [
                        str(python),
                        "-E",
                        "-s",
                        str(entrypoint),
                        "--window",
                        json.dumps(child_context),
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
            except OSError as exc:
                raise RuntimeError(
                    f"The window could not start: {exc}\nLog: {log.name}"
                ) from exc
        deadline = time.monotonic() + 15
        while not ready.exists():
            if process.poll() is not None:
                detail = Path(log.name).read_text(errors="replace")[-3000:]
                raise RuntimeError(
                    f"The window could not start.\n{detail}\nLog: {log.name}"
                )
            if time.monotonic() >= deadline:
                raise RuntimeError(
                    f"The window has not confirmed startup.\nLog: {log.name}"
                )
            time.sleep(0.1)
