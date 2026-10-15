import importlib
import importlib.metadata
import json
import os
import re
import subprocess
import sys
from pathlib import Path

IDENTIFIER = "com.circuitninja.kicad"
PROBE_TIMEOUT = 20
MINIMUM_PYTHON = (3, 10)
MODULES = (
    "httpx2",
    "keyring",
    "kipy",
    "pydantic",
    "PySide6.QtCore",
    "PySide6.QtGui",
    "PySide6.QtWidgets",
)


def environment_path(version: str) -> Path:
    match = re.match("(\\d+\\.\\d+)", version)
    if not match:
        raise RuntimeError(f"Cannot identify KiCad's configuration version: {version}")
    override = os.environ.get("KICAD_CACHE_HOME")
    if override:
        cache = Path(override)
    elif sys.platform == "win32":
        cache = Path(os.environ["LOCALAPPDATA"])
    elif sys.platform.startswith("linux"):
        cache = Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache")
    else:
        raise RuntimeError("This prototype supports Linux and Windows only.")
    return cache / "kicad" / match[1] / "python-environments" / IDENTIFIER


def child_environment() -> dict[str, str]:
    env = os.environ.copy()
    for name in (
        "PYTHONHOME",
        "PYTHONPATH",
        "VIRTUAL_ENV",
        "KICAD_API_SOCKET",
        "KICAD_API_TOKEN",
    ):
        env.pop(name, None)
    return env


def installer_running(environment: Path) -> bool:
    if not sys.platform.startswith("linux"):
        return False
    for process in Path("/proc").iterdir():
        if not process.name.isdecimal():
            continue
        try:
            state = (process / "stat").read_text().rsplit(")", 1)[1].split()[0]
            args = (
                (process / "cmdline")
                .read_bytes()
                .decode(errors="replace")
                .split("\x00")
            )
        except (OSError, IndexError):
            continue
        if state == "Z" or not any(str(environment) in arg for arg in args):
            continue
        if any(
            args[i : i + 2] in (["-m", "pip"], ["-m", "venv"])
            for i in range(len(args) - 1)
        ):
            return True
    return False


def inspect_environment(environment: Path, requirements: Path) -> dict[str, object]:
    if sys.version_info < MINIMUM_PYTHON:
        raise RuntimeError("Python 3.10 or newer is required.")
    if (
        sys.prefix == sys.base_prefix
        or Path(sys.prefix).resolve() != environment.resolve()
    ):
        raise RuntimeError(
            f"Expected virtual environment {environment}; got {sys.prefix}"
        )
    try:
        requirement_type = importlib.import_module("packaging.requirements").Requirement
    except ModuleNotFoundError:
        requirement_type = importlib.import_module(
            "pip._vendor.packaging.requirements"
        ).Requirement
    versions = {}
    for line in requirements.read_text(encoding="utf-8").splitlines():
        line = line.split("#", 1)[0].strip()
        if not line:
            continue
        requirement = requirement_type(line)
        if requirement.marker and (not requirement.marker.evaluate()):
            continue
        installed = importlib.metadata.version(requirement.name)
        if not requirement.specifier.contains(installed):
            raise RuntimeError(f"{requirement}: installed version is {installed}")
        versions[requirement.name] = installed
    origins = {}
    for name in MODULES:
        origins[name] = importlib.import_module(name).__file__
    return {"prefix": sys.prefix, "versions": versions, "modules": origins}


def check_environment(environment: Path, plugin_dir: Path) -> Path:
    python = environment / (
        "Scripts/python.exe" if sys.platform == "win32" else "bin/python"
    )
    if not (environment / "pyvenv.cfg").is_file() or not python.is_file():
        raise RuntimeError(
            f"KiCad has not created a usable plugin environment yet:\n{environment}\n\nEnable the KiCad API and let KiCad finish installing plugin dependencies, then try again. This button does not install packages."
        )
    if installer_running(environment):
        raise RuntimeError(
            "KiCad is still installing plugin dependencies. Try again when it finishes."
        )
    completed = subprocess.run(
        [
            str(python),
            "-E",
            "-s",
            str(plugin_dir / "plugin_environment.py"),
            str(environment),
            str(plugin_dir / "requirements.txt"),
        ],
        env=child_environment(),
        capture_output=True,
        text=True,
        timeout=PROBE_TIMEOUT,
        check=False,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    if completed.returncode:
        raise RuntimeError(
            f"The plugin environment is not ready:\n{environment}\n\n"
            + (
                completed.stderr
                or completed.stdout
                or f"Exit code {completed.returncode}"
            )[-4000:]
        )
    try:
        result = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            f"The plugin environment probe returned invalid JSON:\n{environment}\n\n{exc}\n\nStandard output:\n{completed.stdout[-2000:] or '(empty)'}\n\nStandard error:\n{completed.stderr[-2000:] or '(empty)'}"
        ) from exc
    if Path(result["prefix"]).resolve() != environment.resolve():
        raise RuntimeError("The environment probe returned an unexpected interpreter.")
    if installer_running(environment):
        raise RuntimeError(
            "Dependency installation started during the check. Try again later."
        )
    return python


if __name__ == "__main__":
    try:
        print(json.dumps(inspect_environment(Path(sys.argv[1]), Path(sys.argv[2]))))
    except Exception as exc:
        print(f"{type(exc).__name__}: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
