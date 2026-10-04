from __future__ import annotations

APP_STYLESHEET = """
* {
    font-family: "Segoe UI", "Inter", sans-serif;
    font-size: 12px;
    color: #d4d4d8;
}
QMainWindow, QDialog, QWidget#DashboardRoot {
    background-color: #101013;
}
QMenuBar {
    background-color: #16161a;
    border-bottom: 1px solid #26262c;
    padding: 2px;
}
QMenuBar::item {
    padding: 5px 10px;
    background: transparent;
    border-radius: 4px;
}
QMenuBar::item:selected {
    background-color: #26262e;
}
QMenu {
    background-color: #1b1b21;
    border: 1px solid #2e2e36;
    padding: 4px;
}
QMenu::item {
    padding: 6px 24px 6px 12px;
    border-radius: 4px;
}
QMenu::item:selected {
    background-color: #2f6feb;
}
QMenu::item:disabled {
    color: #6b6b74;
}
QStatusBar {
    background-color: #16161a;
    border-top: 1px solid #26262c;
}

/* ---------- generic controls ---------- */
QPushButton {
    background-color: #26262e;
    border: 1px solid #33333d;
    border-radius: 6px;
    padding: 6px 14px;
    min-height: 18px;
}
QPushButton:hover {
    background-color: #2f2f39;
    border-color: #40404c;
}
QPushButton:pressed {
    background-color: #202027;
}
QPushButton:disabled {
    background-color: #1b1b20;
    color: #5a5a63;
    border-color: #232329;
}
QPushButton#PrimaryButton {
    background-color: #2f6feb;
    border: 1px solid #3b7cf0;
    color: #ffffff;
    font-weight: 600;
}
QPushButton#PrimaryButton:hover {
    background-color: #3b7cf0;
}
QPushButton#PrimaryButton:pressed {
    background-color: #2759c4;
}
QPushButton#PrimaryButton:disabled {
    background-color: #24365c;
    border-color: #24365c;
    color: #7f8ba3;
}
QPushButton#DangerButton {
    background-color: #2a1b1d;
    border: 1px solid #4a2a2e;
    color: #f0a1a8;
}
QPushButton#DangerButton:hover {
    background-color: #3a2126;
}
QPushButton#GhostButton {
    background-color: transparent;
    border: 1px solid transparent;
}
QPushButton#GhostButton:hover {
    background-color: #26262e;
    border-color: #33333d;
}

QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox {
    background-color: #1b1b21;
    border: 1px solid #30303a;
    border-radius: 6px;
    padding: 5px 8px;
    selection-background-color: #2f6feb;
}
QLineEdit:focus, QComboBox:focus, QSpinBox:focus {
    border-color: #2f6feb;
}
QLineEdit:disabled, QComboBox:disabled {
    color: #6b6b74;
    background-color: #17171c;
}
QComboBox::drop-down {
    border: none;
    width: 22px;
}
QComboBox::down-arrow {
    image: none;
    border-left: 4px solid transparent;
    border-right: 4px solid transparent;
    border-top: 5px solid #9a9aa4;
    margin-right: 8px;
}
QComboBox QAbstractItemView {
    background-color: #1b1b21;
    border: 1px solid #30303a;
    selection-background-color: #2f6feb;
    selection-color: #ffffff;
    outline: none;
}

QProgressBar {
    background-color: #1b1b21;
    border: 1px solid #30303a;
    border-radius: 6px;
    height: 14px;
    text-align: center;
    color: #cfcfd6;
}
QProgressBar::chunk {
    background-color: #2f6feb;
    border-radius: 5px;
}

QSlider::groove:horizontal {
    height: 4px;
    background: #2c2c34;
    border-radius: 2px;
}
QSlider::handle:horizontal {
    width: 12px;
    height: 12px;
    margin: -4px 0;
    border-radius: 6px;
    background: #d4d4d8;
}
QSlider::sub-page:horizontal {
    background: #2f6feb;
    border-radius: 2px;
}
QSlider:disabled {
    opacity: 0.4;
}

QScrollArea {
    border: none;
    background: transparent;
}
QScrollArea > QWidget > QWidget {
    background: transparent;
}

QListWidget, QPlainTextEdit, QTextEdit {
    background-color: #16161b;
    border: 1px solid #2a2a32;
    border-radius: 8px;
    padding: 4px;
    outline: none;
}
QListWidget::item {
    padding: 4px;
    border-radius: 6px;
}
QListWidget::item:selected {
    background-color: #23375e;
    color: #e8ecf5;
}
QListWidget::item:hover {
    background-color: #202027;
}

QToolTip {
    background-color: #26262e;
    color: #e4e4e8;
    border: 1px solid #3a3a44;
    padding: 4px 6px;
}

QScrollBar:vertical {
    background: transparent;
    width: 10px;
    margin: 0;
}
QScrollBar::handle:vertical {
    background: #34343e;
    border-radius: 5px;
    min-height: 30px;
}
QScrollBar::handle:vertical:hover {
    background: #454552;
}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
    height: 0;
}
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {
    background: transparent;
}
QScrollBar:horizontal {
    background: transparent;
    height: 10px;
    margin: 0;
}
QScrollBar::handle:horizontal {
    background: #34343e;
    border-radius: 5px;
    min-width: 30px;
}
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {
    width: 0;
}
QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal {
    background: transparent;
}

QSplitter::handle {
    background: #26262c;
}
"""

SIDEBAR_BUTTON = """
QPushButton {
    background-color: transparent;
    border: none;
    border-radius: 6px;
    padding: 8px 6px;
    text-align: left;
    color: #9a9aa4;
    font-size: 11px;
    font-weight: 600;
}
QPushButton:hover {
    background-color: #202027;
    color: #d4d4d8;
}
QPushButton:checked {
    background-color: #23375e;
    color: #9ec1ff;
}
QPushButton:disabled {
    color: #4d4d55;
    background-color: transparent;
}
"""

MEDIA_CARD = """
QFrame#MediaCard {
    background-color: #1b1b21;
    border: 1px solid #2a2a32;
    border-radius: 8px;
}
QFrame#MediaCard:hover {
    border-color: #3d3d48;
    background-color: #202027;
}
QFrame#MediaCard[selected="true"] {
    border-color: #2f6feb;
    background-color: #1c2333;
}
"""

RECENT_ROW = """
QPushButton#RecentRow {
    background-color: #1b1b21;
    border: 1px solid #2a2a32;
    border-radius: 8px;
    text-align: left;
    padding: 10px 14px;
}
QPushButton#RecentRow:hover {
    background-color: #202027;
    border-color: #3d3d48;
}
"""
