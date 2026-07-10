"""Industrial Qt styling for the dashboard."""

from __future__ import annotations


WINDOW_MIN_WIDTH = 1180
WINDOW_MIN_HEIGHT = 720


def stylesheet() -> str:
    return """
    * {
        font-family: "Microsoft YaHei UI", "Segoe UI", sans-serif;
        font-size: 13px;
    }

    QMainWindow, QWidget#Root {
        background: #f4f6f8;
        color: #202833;
    }

    QWidget#Sidebar {
        background: #ffffff;
        border-right: 1px solid #d8dee6;
    }

    QLabel#Brand {
        color: #1f2933;
        font-size: 20px;
        font-weight: 700;
    }

    QLabel#Subtle {
        color: #687586;
    }

    QLabel#PageTitle {
        color: #1f2933;
        font-size: 22px;
        font-weight: 700;
    }

    QLabel#MetricValue {
        color: #1f2933;
        font-size: 18px;
        font-weight: 700;
    }

    QLabel#MetricLabel {
        color: #687586;
        font-size: 12px;
    }

    QLabel#StatusGood {
        color: #16794c;
        font-weight: 600;
    }

    QLabel#StatusWarn {
        color: #b7791f;
        font-weight: 600;
    }

    QFrame#Panel {
        background: #ffffff;
        border: 1px solid #d8dee6;
        border-radius: 6px;
    }

    QFrame#Metric {
        background: #f8fafc;
        border: 1px solid #dbe3ec;
        border-radius: 6px;
    }

    QListWidget#Navigation {
        background: transparent;
        border: 0;
        outline: 0;
        color: #4d5b6a;
    }

    QListWidget#Navigation::item {
        min-height: 38px;
        padding: 8px 12px;
        border-radius: 5px;
        margin: 2px 10px;
    }

    QListWidget#Navigation::item:selected {
        background: #e7f1fb;
        color: #174a7c;
        border-left: 3px solid #2f80c8;
    }

    QListWidget#Navigation::item:hover {
        background: #f0f4f8;
        color: #1f2933;
    }

    QPushButton {
        background: #ffffff;
        color: #26313d;
        border: 1px solid #c7d1dc;
        border-radius: 5px;
        padding: 7px 12px;
        min-height: 18px;
    }

    QPushButton:hover {
        background: #f0f5fa;
        border-color: #9fb0c0;
    }

    QPushButton:pressed {
        background: #e4ebf2;
    }

    QPushButton#PrimaryButton {
        background: #256fa8;
        color: #ffffff;
        border-color: #256fa8;
    }

    QPushButton#PrimaryButton:hover {
        background: #1f5f91;
    }

    QPushButton#DangerButton {
        background: #b4233a;
        color: #ffffff;
        border-color: #b4233a;
    }

    QLineEdit, QSpinBox, QComboBox {
        background: #ffffff;
        color: #202833;
        border: 1px solid #c7d1dc;
        border-radius: 5px;
        padding: 6px 8px;
        min-height: 20px;
    }

    QLineEdit:focus, QSpinBox:focus, QComboBox:focus {
        border-color: #2f80c8;
    }

    QComboBox::drop-down {
        border: 0;
        width: 24px;
    }

    QCheckBox {
        color: #344253;
        spacing: 8px;
    }

    QCheckBox::indicator {
        width: 16px;
        height: 16px;
        border: 1px solid #9fb0c0;
        background: #ffffff;
        border-radius: 3px;
    }

    QCheckBox::indicator:checked {
        background: #256fa8;
        border-color: #256fa8;
    }

    QTableWidget {
        background: #ffffff;
        alternate-background-color: #f7f9fb;
        color: #202833;
        gridline-color: #e1e7ee;
        border: 1px solid #d8dee6;
        border-radius: 6px;
        selection-background-color: #d8ebfb;
        selection-color: #1f2933;
    }

    QHeaderView::section {
        background: #edf2f7;
        color: #4d5b6a;
        border: 0;
        border-right: 1px solid #d8dee6;
        border-bottom: 1px solid #d8dee6;
        padding: 7px 8px;
        font-weight: 600;
    }

    QTextEdit {
        background: #ffffff;
        color: #202833;
        border: 1px solid #d8dee6;
        border-radius: 6px;
        padding: 8px;
    }

    QScrollBar:vertical, QScrollBar:horizontal {
        background: #f4f6f8;
        border: 0;
        width: 12px;
        height: 12px;
    }

    QScrollBar::handle {
        background: #b8c5d2;
        border-radius: 5px;
        min-height: 28px;
    }

    QScrollBar::handle:hover {
        background: #8fa1b3;
    }

    QScrollBar::add-line, QScrollBar::sub-line {
        width: 0;
        height: 0;
    }
    """
