from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from app.models.caption import LANGUAGES, WHISPER_MODELS
from app.models.clip import ASPECT_RATIOS, QUALITY_PRESETS, resolution_for
from app.models.settings import AppSettings
from app.services.settings_service import SettingsService
from app.utils.paths import open_in_explorer


class SettingsPanel(QWidget):
    """SETTINGS page: global application defaults, persisted as JSON."""

    settingsSaved = Signal(object)  # AppSettings
    openOutputFolderRequested = Signal()

    def __init__(self, settings_service: SettingsService, parent=None):
        super().__init__(parent)
        self._service = settings_service
        root = QVBoxLayout(self)
        root.setContentsMargins(14, 14, 10, 12)
        root.setSpacing(10)

        header = QLabel("SETTINGS")
        header.setStyleSheet(
            "color: #9a9aa4; font-size: 11px; font-weight: 700; letter-spacing: 2px;"
        )
        root.addWidget(header)

        intro = QLabel(
            "Global defaults for new projects and queue exports. "
            "Stored in settings.json next to the app."
        )
        intro.setWordWrap(True)
        intro.setStyleSheet("color: #5c5c66; font-size: 10px;")
        root.addWidget(intro)

        # ---- defaults card ----
        card = QFrame()
        card.setStyleSheet(
            "QFrame { background-color: #16161b; border: 1px solid #2a2a32;"
            " border-radius: 8px; }"
        )
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(12, 12, 12, 12)
        card_layout.setSpacing(10)

        def caption(text: str) -> QLabel:
            label = QLabel(text)
            label.setStyleSheet("color: #83838d; font-size: 11px;")
            return label

        card_layout.addWidget(caption("New projects start with aspect ratio"))
        self.aspect_box = QComboBox()
        for key in ASPECT_RATIOS:
            width, height = resolution_for(key)
            self.aspect_box.addItem(f"{key}  {width}x{height}", key)
        card_layout.addWidget(self.aspect_box)

        card_layout.addWidget(caption("Default export quality preset"))
        self.preset_box = QComboBox()
        for key, info in QUALITY_PRESETS.items():
            self.preset_box.addItem(
                f"{info['label']}  (CRF {info['crf']})", key
            )
        card_layout.addWidget(self.preset_box)

        root.addWidget(card)

        # ---- output folder card ----
        out_card = QFrame()
        out_card.setStyleSheet(
            "QFrame { background-color: #16161b; border: 1px solid #2a2a32;"
            " border-radius: 8px; }"
        )
        out_layout = QVBoxLayout(out_card)
        out_layout.setContentsMargins(12, 12, 12, 12)
        out_layout.setSpacing(8)

        out_layout.addWidget(caption("Export output folder"))
        row = QHBoxLayout()
        row.setSpacing(8)
        self.path_edit = QLineEdit()
        self.path_edit.setPlaceholderText("Leave empty to use each project's exports/ folder")
        browse = QPushButton("Browse…")
        browse.clicked.connect(self._browse)
        reset = QPushButton("Reset")
        reset.setToolTip("Use each project's exports/ folder")
        reset.clicked.connect(lambda: self.path_edit.setText(""))
        row.addWidget(self.path_edit, 1)
        row.addWidget(browse)
        row.addWidget(reset)
        out_layout.addLayout(row)

        open_folder = QPushButton("Open output folder")
        open_folder.clicked.connect(self.openOutputFolderRequested)
        out_layout.addWidget(open_folder)

        root.addWidget(out_card)

        # ---- captions card ----
        cap_card = QFrame()
        cap_card.setStyleSheet(
            "QFrame { background-color: #16161b; border: 1px solid #2a2a32;"
            " border-radius: 8px; }"
        )
        cap_layout = QVBoxLayout(cap_card)
        cap_layout.setContentsMargins(12, 12, 12, 12)
        cap_layout.setSpacing(10)

        cap_layout.addWidget(caption("Whisper model (captions)"))
        self.model_box = QComboBox()
        for key, label in WHISPER_MODELS.items():
            self.model_box.addItem(label, key)
        cap_layout.addWidget(self.model_box)

        cap_layout.addWidget(caption("Caption language"))
        self.language_box = QComboBox()
        for code, label in LANGUAGES.items():
            self.language_box.addItem(label, code)
        cap_layout.addWidget(self.language_box)

        self.burn_box = QCheckBox("Burn captions into exports when available")
        self.burn_box.setStyleSheet("color: #83838d;")
        cap_layout.addWidget(self.burn_box)

        root.addWidget(cap_card)

        # ---- AI card ----
        ai_card = QFrame()
        ai_card.setStyleSheet(
            "QFrame { background-color: #16161b; border: 1px solid #2a2a32;"
            " border-radius: 8px; }"
        )
        ai_layout = QVBoxLayout(ai_card)
        ai_layout.setContentsMargins(12, 12, 12, 12)
        ai_layout.setSpacing(8)

        ai_layout.addWidget(caption("Ollama server URL (AI assistant)"))
        self.url_edit = QLineEdit()
        self.url_edit.setPlaceholderText("http://127.0.0.1:11434")
        self.url_edit.setToolTip(
            "Local Ollama server. Install from ollama.com and run "
            "'ollama serve'; pick the model on the AI page."
        )
        ai_layout.addWidget(self.url_edit)

        ai_hint = QLabel(
            "Models are listed on the AI page once the server responds. "
            "Nothing is sent to the cloud."
        )
        ai_hint.setWordWrap(True)
        ai_hint.setStyleSheet("color: #4d4d55; font-size: 10px;")
        ai_layout.addWidget(ai_hint)

        root.addWidget(ai_card)

        # ---- save row ----
        actions = QHBoxLayout()
        actions.setSpacing(8)
        self.save_button = QPushButton("Save settings")
        self.save_button.setObjectName("PrimaryButton")
        self.save_button.clicked.connect(self._save)
        self.status_label = QLabel("")
        self.status_label.setStyleSheet("color: #7fd18a; font-size: 11px;")
        actions.addWidget(self.save_button)
        actions.addWidget(self.status_label, 1)
        root.addLayout(actions)

        note = QLabel(
            "Settings apply to new projects, queue exports, and the export "
            "dialog's default path. Existing projects keep their own values."
        )
        note.setWordWrap(True)
        note.setStyleSheet("color: #4d4d55; font-size: 10px;")
        root.addWidget(note)
        root.addStretch(1)

        self.load_values()

    # ------------------------------------------------------------- values
    def load_values(self) -> None:
        settings = self._service.settings
        index = self.aspect_box.findData(settings.default_aspect)
        self.aspect_box.setCurrentIndex(index if index >= 0 else 1)
        preset_index = self.preset_box.findData(settings.default_preset)
        self.preset_box.setCurrentIndex(preset_index if preset_index >= 0 else 1)
        self.path_edit.setText(settings.output_dir)
        model_index = self.model_box.findData(settings.whisper_model)
        self.model_box.setCurrentIndex(model_index if model_index >= 0 else 1)
        language_index = self.language_box.findData(settings.whisper_language)
        self.language_box.setCurrentIndex(language_index if language_index >= 0 else 0)
        self.burn_box.setChecked(bool(settings.burn_captions))
        self.url_edit.setText(settings.ollama_url)

    def _save(self) -> None:
        settings = AppSettings(
            default_aspect=self.aspect_box.currentData() or "9:16",
            default_preset=self.preset_box.currentData() or "balanced",
            output_dir=self.path_edit.text().strip(),
            whisper_model=self.model_box.currentData() or "base",
            whisper_language=self.language_box.currentData() or "auto",
            burn_captions=self.burn_box.isChecked(),
            ollama_url=self.url_edit.text().strip() or "http://127.0.0.1:11434",
            ollama_model=self._service.settings.ollama_model,
        )
        if settings.output_dir:
            target = Path(settings.output_dir)
            try:
                target.mkdir(parents=True, exist_ok=True)
            except OSError:
                self.status_label.setStyleSheet("color: #e57373; font-size: 11px;")
                self.status_label.setText(f"Cannot create folder: {target}")
                return
        self._service.save(settings)
        self.status_label.setStyleSheet("color: #7fd18a; font-size: 11px;")
        self.status_label.setText("Saved")
        self.settingsSaved.emit(settings)

    def _browse(self) -> None:
        initial = self.path_edit.text().strip() or str(Path.home())
        path = QFileDialog.getExistingDirectory(
            self, "Choose export output folder", initial
        )
        if path:
            self.path_edit.setText(path)
            self.status_label.setText("")

    def open_output_folder(self) -> None:
        target = self.path_edit.text().strip()
        if target and Path(target).exists():
            open_in_explorer(Path(target))
            return
        self.openOutputFolderRequested.emit()
