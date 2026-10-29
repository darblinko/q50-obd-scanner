"""Dark technical theme: color tokens, fonts and the application stylesheet.

Widgets select styles through dynamic properties rather than inline styles:
- QLabel role: "title", "subtitle", "section", "dim", "value", "big", "mono"
- QLabel/QFrame state: "ok", "warn", "fault", "accent", "idle"
- QPushButton variant: "primary", "danger", "ghost"
- QFrame objectName "Card" for panels"""

from __future__ import annotations

import pyqtgraph as pg
from PySide6.QtGui import QColor, QFont, QFontDatabase, QPalette
from PySide6.QtWidgets import QApplication, QWidget

BG = "#0d1117"
BG_DEEP = "#090c10"
PANEL = "#161b22"
PANEL_ALT = "#1c232c"
PANEL_HOVER = "#212a35"
BORDER = "#2a313c"
BORDER_STRONG = "#3a4350"
TEXT = "#e6edf3"
TEXT_DIM = "#8b949e"
TEXT_FAINT = "#5c6672"
ACCENT = "#2dd4bf"
ACCENT_DIM = "#134e48"
OK = "#3fb950"
WARN = "#d29922"
FAULT = "#f85149"

STATE_COLORS = {"ok": OK, "warn": WARN, "fault": FAULT, "accent": ACCENT, "idle": TEXT_DIM}

# Distinct series colors for charts, accent first.
SERIES_COLORS = (ACCENT, "#58a6ff", "#e3b341", "#f778ba", "#a5d6ff", "#7ee787", "#ffa657", "#d2a8ff")

MONO_FAMILIES = ("JetBrains Mono", "Cascadia Mono", "Consolas", "DejaVu Sans Mono", "Liberation Mono")
SANS_FAMILIES = ("Inter", "Segoe UI", "Ubuntu", "Noto Sans", "DejaVu Sans")

_mono_family: str | None = None
_sans_family: str | None = None


def _first_available(candidates: tuple[str, ...], fallback: QFont.StyleHint) -> str:
    available = set(QFontDatabase.families())
    for family in candidates:
        if family in available:
            return family
    if fallback == QFont.StyleHint.Monospace:
        return QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont).family()
    return QFontDatabase.systemFont(QFontDatabase.SystemFont.GeneralFont).family()


def mono_family() -> str:
    global _mono_family
    if _mono_family is None:
        _mono_family = _first_available(MONO_FAMILIES, QFont.StyleHint.Monospace)
    return _mono_family


def sans_family() -> str:
    global _sans_family
    if _sans_family is None:
        _sans_family = _first_available(SANS_FAMILIES, QFont.StyleHint.SansSerif)
    return _sans_family


def mono_font(point_size: float = 10.0, bold: bool = False) -> QFont:
    font = QFont(mono_family())
    font.setStyleHint(QFont.StyleHint.Monospace)
    font.setPointSizeF(point_size)
    font.setBold(bold)
    return font


def state_color(state: str | None) -> str:
    return STATE_COLORS.get(state or "", TEXT)


def set_state(widget: QWidget, state: str | None) -> None:
    """Set the `state` property and re-polish so the stylesheet applies."""
    widget.setProperty("state", state or "")
    widget.style().unpolish(widget)
    widget.style().polish(widget)


def set_role(widget: QWidget, role: str) -> None:
    widget.setProperty("role", role)
    widget.style().unpolish(widget)
    widget.style().polish(widget)


def stylesheet() -> str:
    mono = mono_family()
    sans = sans_family()
    return f"""
* {{ font-family: "{sans}"; font-size: 10pt; color: {TEXT}; }}
QMainWindow, QWidget#Root, QStackedWidget, QWidget#Page {{ background: {BG}; }}
QToolTip {{ background: {PANEL_ALT}; color: {TEXT}; border: 1px solid {BORDER_STRONG}; padding: 6px; }}

QLabel {{ background: transparent; }}
QLabel[role="title"] {{ font-size: 17pt; font-weight: 600; color: {TEXT}; }}
QLabel[role="subtitle"] {{ color: {TEXT_DIM}; font-size: 10pt; }}
QLabel[role="section"] {{ color: {TEXT_DIM}; font-size: 8pt; font-weight: 700; letter-spacing: 1.5px; }}
QLabel[role="dim"] {{ color: {TEXT_DIM}; }}
QLabel[role="faint"] {{ color: {TEXT_FAINT}; }}
QLabel[role="mono"] {{ font-family: "{mono}"; }}
QLabel[role="value"] {{ font-family: "{mono}"; font-size: 15pt; font-weight: 600; }}
QLabel[role="big"] {{ font-family: "{mono}"; font-size: 30pt; font-weight: 600; }}
QLabel[state="ok"] {{ color: {OK}; }}
QLabel[state="warn"] {{ color: {WARN}; }}
QLabel[state="fault"] {{ color: {FAULT}; }}
QLabel[state="accent"] {{ color: {ACCENT}; }}
QLabel[state="idle"] {{ color: {TEXT_FAINT}; }}

QFrame#Card {{ background: {PANEL}; border: 1px solid {BORDER}; border-radius: 6px; }}
QFrame#Card[state="ok"] {{ border-left: 3px solid {OK}; }}
QFrame#Card[state="warn"] {{ border-left: 3px solid {WARN}; }}
QFrame#Card[state="fault"] {{ border-left: 3px solid {FAULT}; }}
QFrame#Card[state="accent"] {{ border-left: 3px solid {ACCENT}; }}
QFrame#Hint {{ background: {ACCENT_DIM}; border: 1px solid {ACCENT}; border-radius: 6px; }}
QFrame#Separator {{ background: {BORDER}; max-height: 1px; min-height: 1px; border: none; }}

QWidget#TopBar {{ background: {BG_DEEP}; border-bottom: 1px solid {BORDER}; }}
QWidget#TopBar QLabel[role="section"] {{ font-size: 7pt; }}
QWidget#TaskStrip {{ background: {PANEL}; border-bottom: 1px solid {BORDER}; }}

QListWidget#NavRail {{ background: {BG_DEEP}; border: none; border-right: 1px solid {BORDER};
    padding: 8px 0; outline: none; }}
QListWidget#NavRail::item {{ padding: 8px 18px; border-left: 2px solid transparent; color: {TEXT_DIM}; }}
QListWidget#NavRail::item:hover {{ background: {PANEL}; color: {TEXT}; }}
QListWidget#NavRail::item:selected {{ background: {PANEL}; color: {ACCENT}; border-left: 2px solid {ACCENT}; }}

QPushButton {{ background: {PANEL_ALT}; border: 1px solid {BORDER_STRONG}; border-radius: 4px;
    padding: 6px 14px; color: {TEXT}; }}
QPushButton:hover {{ background: {PANEL_HOVER}; border-color: {TEXT_FAINT}; }}
QPushButton:pressed {{ background: {BORDER}; }}
QPushButton:disabled {{ color: {TEXT_FAINT}; border-color: {BORDER}; background: {PANEL}; }}
QPushButton:checked {{ background: {ACCENT_DIM}; border-color: {ACCENT}; color: {ACCENT}; }}
QPushButton[variant="primary"] {{ background: {ACCENT}; border-color: {ACCENT}; color: {BG}; font-weight: 600; }}
QPushButton[variant="primary"]:hover {{ background: #5eead4; }}
QPushButton[variant="primary"]:disabled {{ background: {ACCENT_DIM}; color: {TEXT_DIM}; border-color: {ACCENT_DIM}; }}
QPushButton[variant="danger"] {{ background: transparent; border-color: {FAULT}; color: {FAULT}; }}
QPushButton[variant="danger"]:hover {{ background: #3d1416; }}
QPushButton[variant="ghost"] {{ background: transparent; border-color: transparent; color: {TEXT_DIM}; }}
QPushButton[variant="ghost"]:hover {{ color: {TEXT}; background: {PANEL}; }}
QPushButton#RecordButton:checked {{ background: #3d1416; border-color: {FAULT}; color: {FAULT}; }}

QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox, QPlainTextEdit, QTextEdit {{
    background: {BG_DEEP}; border: 1px solid {BORDER_STRONG}; border-radius: 4px; padding: 5px 8px;
    selection-background-color: {ACCENT_DIM}; selection-color: {TEXT}; }}
QLineEdit:focus, QSpinBox:focus, QComboBox:focus, QPlainTextEdit:focus {{ border-color: {ACCENT}; }}
QLineEdit[state="fault"] {{ border-color: {FAULT}; }}
QLineEdit[role="mono"], QPlainTextEdit[role="mono"], QSpinBox[role="mono"], QListWidget[role="mono"],
QPushButton[role="mono"], QTreeWidget[role="mono"] {{ font-family: "{mono}"; }}
QComboBox::drop-down {{ border: none; width: 20px; }}
QComboBox QAbstractItemView {{ background: {PANEL}; border: 1px solid {BORDER_STRONG};
    selection-background-color: {ACCENT_DIM}; }}

QTableView, QTreeView, QListView, QTableWidget, QTreeWidget, QListWidget {{
    background: {PANEL}; alternate-background-color: {PANEL_ALT}; border: 1px solid {BORDER};
    border-radius: 4px; gridline-color: {BORDER}; selection-background-color: {ACCENT_DIM};
    selection-color: {TEXT}; outline: none; }}
QTableView::item, QTreeView::item {{ padding: 3px 6px; border: none; }}
QTreeView::item:hover, QTableView::item:hover, QListView::item:hover {{ background: {PANEL_HOVER}; }}
QTreeView::item:selected, QTableView::item:selected, QListView::item:selected {{
    background: {ACCENT_DIM}; color: {TEXT}; }}
QHeaderView {{ background: {PANEL}; border: none; }}
QHeaderView::section {{ background: {PANEL}; color: {TEXT_DIM}; border: none;
    border-bottom: 1px solid {BORDER}; border-right: 1px solid {BORDER}; padding: 6px 8px;
    font-size: 8pt; font-weight: 700; }}
QTableCornerButton::section {{ background: {PANEL}; border: none; }}

QTabWidget::pane {{ border: 1px solid {BORDER}; border-radius: 4px; background: {PANEL}; top: -1px; }}
QTabBar::tab {{ background: transparent; color: {TEXT_DIM}; padding: 8px 16px; border: none;
    border-bottom: 2px solid transparent; }}
QTabBar::tab:hover {{ color: {TEXT}; }}
QTabBar::tab:selected {{ color: {ACCENT}; border-bottom: 2px solid {ACCENT}; }}

QProgressBar {{ background: {BG_DEEP}; border: 1px solid {BORDER}; border-radius: 3px; height: 8px;
    text-align: center; color: transparent; }}
QProgressBar::chunk {{ background: {ACCENT}; border-radius: 2px; }}

QCheckBox, QRadioButton {{ spacing: 8px; background: transparent; }}
QCheckBox::indicator, QTreeView::indicator, QListView::indicator {{ width: 14px; height: 14px;
    border: 1px solid {BORDER_STRONG}; border-radius: 3px; background: {BG_DEEP}; }}
QCheckBox::indicator:checked, QTreeView::indicator:checked, QListView::indicator:checked {{
    background: {ACCENT}; border-color: {ACCENT}; }}

QScrollBar:vertical {{ background: transparent; width: 10px; margin: 0; }}
QScrollBar::handle:vertical {{ background: {BORDER_STRONG}; border-radius: 4px; min-height: 30px; }}
QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 0; }}
QScrollBar::handle:horizontal {{ background: {BORDER_STRONG}; border-radius: 4px; min-width: 30px; }}
QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}
QScrollArea {{ background: transparent; border: none; }}
QScrollArea > QWidget > QWidget {{ background: transparent; }}

QSplitter::handle {{ background: {BORDER}; }}
QSplitter::handle:horizontal {{ width: 1px; }}
QSplitter::handle:vertical {{ height: 1px; }}
QStatusBar {{ background: {BG_DEEP}; border-top: 1px solid {BORDER}; color: {TEXT_DIM}; }}
QStatusBar QLabel {{ color: {TEXT_DIM}; }}
QMessageBox {{ background: {PANEL}; }}
QMenu {{ background: {PANEL}; border: 1px solid {BORDER_STRONG}; }}
QMenu::item:selected {{ background: {ACCENT_DIM}; }}
"""


def apply_theme(app: QApplication) -> None:
    app.setStyle("Fusion")
    palette = QPalette()
    palette.setColor(QPalette.ColorRole.Window, QColor(BG))
    palette.setColor(QPalette.ColorRole.Base, QColor(PANEL))
    palette.setColor(QPalette.ColorRole.AlternateBase, QColor(PANEL_ALT))
    palette.setColor(QPalette.ColorRole.Text, QColor(TEXT))
    palette.setColor(QPalette.ColorRole.WindowText, QColor(TEXT))
    palette.setColor(QPalette.ColorRole.Button, QColor(PANEL_ALT))
    palette.setColor(QPalette.ColorRole.ButtonText, QColor(TEXT))
    palette.setColor(QPalette.ColorRole.Highlight, QColor(ACCENT_DIM))
    palette.setColor(QPalette.ColorRole.HighlightedText, QColor(TEXT))
    palette.setColor(QPalette.ColorRole.PlaceholderText, QColor(TEXT_FAINT))
    app.setPalette(palette)
    app.setStyleSheet(stylesheet())
    pg.setConfigOptions(background=PANEL, foreground=TEXT_DIM, antialias=True)
