import json
import sys
from pathlib import Path
from typing import Any

from manufacturing_constraints_ipc import prefer_environment_packages, window_guard


def notify_ready(context: dict[str, Any]) -> None:
    if ready := context.get("ready_file"):
        try:
            Path(ready).write_text("ready", encoding="utf-8")
        except OSError:
            pass


def show_hub(context: dict[str, Any]) -> None:
    from hub_service import HubService
    from hub_window import HubController
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication
    from service_config import DEFAULT_ORIGIN

    identity = Path(context.get("project_path") or context["environment_path"])
    identity = identity.with_name(identity.name + ".circuitninja-hub")
    with window_guard(identity) as (acquired, focus_request):
        if not acquired:
            focus_request.touch()
            notify_ready(context)
            return
        app = QApplication(sys.argv[:1])
        app.setApplicationName("CircuitNinja")
        app.setStyle("Fusion")
        service = HubService(context.get("service_origin", DEFAULT_ORIGIN))
        controller = HubController(app, context, service)
        focus = QTimer(controller)
        focus.setSingleShot(True)
        focus.timeout.connect(controller.focus_current)

        def requested_focus() -> None:
            try:
                focus_request.unlink()
            except OSError:
                return
            focus.start(750)

        requests = QTimer(controller)
        requests.timeout.connect(requested_focus)
        requests.start(250)
        app.aboutToQuit.connect(controller.shutdown)
        controller.show_hub()
        QTimer.singleShot(0, lambda: notify_ready(context))
        QTimer.singleShot(0, lambda: controller.account_action("load", quiet=True))
        focus.start(750)
        app.exec()


def main() -> None:
    prefer_environment_packages()
    if len(sys.argv) != 3 or sys.argv[1] != "--window":
        raise SystemExit(
            "Launch this window using the CircuitNinja hub button in KiCad."
        )
    show_hub(json.loads(sys.argv[2]))


if __name__ == "__main__":
    main()
