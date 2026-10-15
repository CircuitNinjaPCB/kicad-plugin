import os
import sys
from pathlib import Path


def swig_command() -> list[str]:
    version = f"python{sys.version_info.major}.{sys.version_info.minor}"
    if appimage := os.environ.get("APPIMAGE"):
        if appimage.endswith("_pcbnew"):
            return [appimage.removesuffix("_pcbnew") + "_" + version]
        return [appimage, version]
    prefix = Path(sys.base_prefix)
    python = (
        prefix / "python.exe" if sys.platform == "win32" else prefix / "bin" / version
    )
    return [str(python)]
