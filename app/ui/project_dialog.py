from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
)
from app.services.project_service import ProjectError, ProjectService


class NewProjectDialog(QDialog):
    """Creates a new .clipper project workspace."""

    def __init__(self, service: ProjectService, parent=None):
        super().__init__(parent)
        self.setWindowTitle("New Project")
        self.setMinimumWidth(420)
        self._service = service

        root = QVBoxLayout(self)
        root.setContentsMargins(20, 18, 20, 18)
        root.setSpacing(12)

        title = QLabel("NEW PROJECT")
        title.setStyleSheet(
            "color: #9a9aa4; font-size: 11px; font-weight: 700; letter-spacing: 2px;"
        )
        root.addWidget(title)

        form = QFormLayout()
        form.setHorizontalSpacing(14)
        form.setVerticalSpacing(10)

        self.name_edit = QLineEdit()
        self.name_edit.setPlaceholderText("MyPodcast")
        self.name_edit.setClearButtonEnabled(True)
        form.addRow("Project name", self.name_edit)
        root.addLayout(form)

        info = QLabel(
            "A project workspace folder will be created under "
            "<b>projects/</b> containing media, thumbnails, exports and the "
            "<b>.clipper</b> project file (SQLite)."
        )
        info.setWordWrap(True)
        info.setStyleSheet(
            "QLabel { color: #63636e; background-color: #16161b;"
            " border: 1px solid #2a2a32; border-radius: 8px; padding: 10px 12px; }"
        )
        info.setTextFormat(Qt.TextFormat.RichText)
        root.addWidget(info)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self.reject)
        self.create_button = QPushButton("Create project")
        self.create_button.setObjectName("PrimaryButton")
        self.create_button.clicked.connect(self._create)
        buttons.addWidget(cancel)
        buttons.addWidget(self.create_button)
        root.addLayout(buttons)

        self.name_edit.returnPressed.connect(self._create)
        self.name_edit.setFocus()

    def project_name(self) -> str:
        return self.name_edit.text().strip()

    def _create(self) -> None:
        name = self.project_name()
        if not name:
            QMessageBox.warning(self, "New Project", "Enter a project name.")
            return
        try:
            self._result = self._service.create_project(name)
        except ProjectError as exc:
            QMessageBox.critical(self, "New Project", str(exc))
            return
        self.accept()

    @property
    def result_project(self):
        return getattr(self, "_result", None)
