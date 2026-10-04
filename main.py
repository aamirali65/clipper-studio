from __future__ import annotations

import argparse
import sys
import traceback

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication, QMessageBox

from app import APP_NAME
from app.ui.theme import APP_STYLESHEET
from app.utils.logging import configure_logging, format_exception, get_logger

log = get_logger("main")


def _install_excepthook(app: QApplication) -> None:
    def handler(exc_type, exc, tb) -> None:
        text = "".join(traceback.format_exception(exc_type, exc, tb))
        log.error("unhandled exception:\n%s", text)
        if app is None:
            return
        box = QMessageBox()
        box.setIcon(QMessageBox.Icon.Critical)
        box.setWindowTitle(f"{APP_NAME} - Unexpected Error")
        box.setText("An unexpected error occurred. The details were written to the log file.")
        box.setDetailedText(text)
        box.exec()

    sys.excepthook = handler


def main() -> int:
    parser = argparse.ArgumentParser(prog="clipper-studio")
    parser.add_argument(
        "--smoke",
        type=int,
        default=0,
        metavar="MS",
        help="auto-close after MS milliseconds (used for automated testing)",
    )
    args = parser.parse_args()

    configure_logging()
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setOrganizationName(APP_NAME)
    app.setStyle("Fusion")
    app.setStyleSheet(APP_STYLESHEET)
    _install_excepthook(app)

    from app.ui.main_window import MainWindow

    window = MainWindow()
    window.show()
    log.info("main window shown (%s)", APP_NAME)

    if args.smoke > 0:
        QTimer.singleShot(args.smoke, app.quit)

    code = app.exec()
    log.info("application exiting with code %s", code)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
