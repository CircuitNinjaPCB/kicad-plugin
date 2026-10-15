import json
import logging
import sys
from pathlib import Path
from typing import Any

from hub import notify_ready
from manufacturing_constraints_ipc import prefer_environment_packages, window_guard


def show_window(
    context: dict[str, Any], focus_request: Path, action: str = "drc"
) -> None:
    from constraints_source import load_constraints
    from drc_window import DrcWindow
    from drc_workflow import connect
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication, QMessageBox
    from service_config import DEFAULT_ORIGIN
    from window_position import show_centered

    app = QApplication(sys.argv[:1])
    app.setApplicationName("CircuitNinja")
    app.setStyle("Fusion")
    app.setQuitOnLastWindowClosed(False)
    account = None
    try:
        notify_ready(context)
        result = load_constraints(context.get("service_origin", DEFAULT_ORIGIN))
        if result.warning:
            QMessageBox.warning(
                None,
                "CircuitNinja · Using saved manufacturing constraints",
                result.warning,
            )
        if action == "upload":
            from hub_service import HubService
            from upload_window import UploadWindow

            account = HubService(context.get("service_origin", DEFAULT_ORIGIN))
            window = UploadWindow(
                connect(context),
                result.constraints,
                account,
                invalid_default=bool(context.get("default_org_invalid")),
            )
        elif action == "export":
            from export_window import ExportWindow

            window = ExportWindow(connect(context), result.constraints)
        else:
            window = DrcWindow(connect(context), result.constraints)

        def focus() -> None:
            if window.isMinimized():
                window.showNormal()
            window.raise_()
            window.activateWindow()
            if handle := window.windowHandle():
                handle.requestActivate()

        def poll_focus() -> None:
            try:
                focus_request.unlink()
            except OSError:
                return
            QTimer.singleShot(750, focus)

        timer = QTimer(window)
        timer.timeout.connect(poll_focus)
        timer.start(250)
        show_centered(window)
        app.setQuitOnLastWindowClosed(True)
        QTimer.singleShot(750, focus)
        app.exec()
    except Exception as exc:
        logging.getLogger(__name__).exception(f"Cannot open manufacturing {action}")
        QMessageBox.critical(
            None, f"CircuitNinja · Cannot open manufacturing {action}", str(exc)
        )
    finally:
        if account is not None:
            account.close()
        app.quit()


def main(action: str = "drc") -> None:
    prefer_environment_packages()
    if len(sys.argv) != 3 or sys.argv[1] != "--window":
        raise SystemExit(f"Launch manufacturing {action} from the CircuitNinja hub.")
    context = json.loads(sys.argv[2])
    identity = Path(context["project_path"] + f".circuitninja-{action}")
    with window_guard(identity) as (acquired, focus_request):
        if not acquired:
            focus_request.touch()
            notify_ready(context)
            return
        show_window(context, focus_request, action)


if __name__ == "__main__":
    main()
