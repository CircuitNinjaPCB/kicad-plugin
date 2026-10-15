from PySide6.QtCore import QTimer
from PySide6.QtGui import QCursor
from PySide6.QtWidgets import QApplication, QWidget


def show_centered(window: QWidget) -> None:
    active = QApplication.activeWindow()
    screen = (
        active.screen()
        if active is not None and active.isVisible()
        else QApplication.screenAt(QCursor.pos())
    ) or window.screen()
    window.show()

    def center():
        frame = window.frameGeometry()
        frame.moveCenter(screen.availableGeometry().center())
        window.move(frame.topLeft())

    QTimer.singleShot(0, center)
