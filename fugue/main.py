#!/usr/bin/env python3
"""
FUGUE — multi-source video compositor

Four video tracks blended live through a GLSL compositor.
Luma keying, chroma keying, and per-layer blend modes inspired by
analog video mixers and early digital production desks of the late
1980s and early 1990s.

  Ctrl+O     load video into the selected (active) track
  Space      pause / resume all tracks
  Ctrl+S     save composite screenshot
  Ctrl+Q     quit
"""

import sys
import os
import math
from pathlib import Path
from datetime import datetime

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
for _p in (_HERE, _ROOT):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QPushButton, QSlider, QComboBox, QFrame, QFileDialog,
    QSizePolicy, QButtonGroup, QRadioButton, QColorDialog,
)
from PyQt6.QtCore import Qt, QTimer, pyqtSignal, QRectF, QPointF
from PyQt6.QtGui  import (
    QColor, QPainter, QPen, QBrush, QLinearGradient,
    QKeySequence, QShortcut,
)

from canvas       import FugueCanvas
from player       import VideoPlayer
from fugue_shaders import BLEND_MODES

# ── Colour tokens — warm amber / broadcast-monitor palette ────────────────────
BG       = '#080600'
PANEL    = '#100c00'
PANEL2   = '#1a1000'
ACCENT   = '#FFA020'
TEXT     = '#d4c0a0'
DIM      = '#6a5030'
BORDER   = 'rgba(255,160,32,0.15)'
GREEN    = '#40FF80'
MONO     = "'Menlo','Monaco','SF Mono','Courier New',monospace"

_TRACK_LABELS  = ['A', 'B', 'C', 'D']
_SPEED_OPTIONS = [('0.25×', 0.25), ('0.5×', 0.5), ('1×', 1.0),
                  ('2×', 2.0), ('4×', 4.0)]
_KEY_MODES     = ['None', 'Luma — key darks', 'Luma — key brights', 'Chroma']

_SCREENSHOTS = Path.home() / 'Pictures' / 'Fugue'
_SCREENSHOTS.mkdir(parents=True, exist_ok=True)


# ─────────────────────────────────────────────────────────────────────────────
# Compact horizontal slider with label + value readout
# ─────────────────────────────────────────────────────────────────────────────

class LabelSlider(QWidget):
    changed = pyqtSignal(float)

    def __init__(self, label: str, lo: float, hi: float, value: float,
                 fmt: str = '{:.2f}', width: int = 120, parent=None):
        super().__init__(parent)
        self._lo, self._hi = lo, hi
        self._fmt = fmt

        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(5)

        lbl = QLabel(label)
        lbl.setFixedWidth(62)
        lbl.setStyleSheet(f'color:{DIM}; font-family:{MONO}; font-size:8px;')
        lay.addWidget(lbl)

        self._sl = QSlider(Qt.Orientation.Horizontal)
        self._sl.setRange(0, 1000)
        self._sl.setValue(self._v2i(value))
        self._sl.setFixedWidth(width)
        self._sl.setStyleSheet(f"""
            QSlider::groove:horizontal {{
                height:3px; background:{PANEL2}; border-radius:1px;
            }}
            QSlider::handle:horizontal {{
                width:11px; height:11px; margin:-4px 0;
                background:{ACCENT}; border-radius:5px;
            }}
            QSlider::sub-page:horizontal {{
                background:{ACCENT}; border-radius:1px;
            }}
        """)
        self._sl.valueChanged.connect(self._emit)
        lay.addWidget(self._sl)

        self._vl = QLabel(fmt.format(value))
        self._vl.setFixedWidth(32)
        self._vl.setAlignment(Qt.AlignmentFlag.AlignRight)
        self._vl.setStyleSheet(f'color:{TEXT}; font-family:{MONO}; font-size:8px;')
        lay.addWidget(self._vl)

    def _v2i(self, v):
        return int((v - self._lo) / (self._hi - self._lo) * 1000)

    def _i2v(self, i):
        return self._lo + i / 1000 * (self._hi - self._lo)

    def _emit(self, i):
        v = self._i2v(i)
        self._vl.setText(self._fmt.format(v))
        self.changed.emit(v)

    def value(self) -> float:
        return self._i2v(self._sl.value())

    def set_value(self, v: float):
        self._sl.blockSignals(True)
        self._sl.setValue(self._v2i(v))
        self._vl.setText(self._fmt.format(v))
        self._sl.blockSignals(False)


# ─────────────────────────────────────────────────────────────────────────────
# Mini scrubber / progress bar for a single track
# ─────────────────────────────────────────────────────────────────────────────

class Scrubber(QWidget):
    """Clickable progress bar that emits a 0.0–1.0 seek fraction on click/drag."""
    seek_requested = pyqtSignal(float)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(6)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._progress = 0.0

    def set_progress(self, v: float):
        self._progress = max(0.0, min(1.0, v))
        self.update()

    def _emit_at(self, x: int):
        frac = max(0.0, min(1.0, x / max(self.width(), 1)))
        self.seek_requested.emit(frac)

    def mousePressEvent(self, e):
        self._emit_at(int(e.position().x()))

    def mouseMoveEvent(self, e):
        if e.buttons() & Qt.MouseButton.LeftButton:
            self._emit_at(int(e.position().x()))

    def paintEvent(self, _):
        p = QPainter(self)
        p.fillRect(self.rect(), QColor(PANEL2))
        w = int(self.width() * self._progress)
        if w > 0:
            p.fillRect(0, 0, w, self.height(), QColor(ACCENT))
        p.end()


# ─────────────────────────────────────────────────────────────────────────────
# Track strip — one row per video track (A / B / C / D)
# ─────────────────────────────────────────────────────────────────────────────

class TrackStrip(QFrame):
    """UI strip for one video track: load, transport, mix, blend mode."""

    load_requested  = pyqtSignal(int)           # slot index
    mix_changed     = pyqtSignal(int, float)    # slot, value
    mode_changed    = pyqtSignal(int, int)      # slot, mode index
    key_selected    = pyqtSignal(int)           # slot is now the key source
    seek_requested  = pyqtSignal(int, float)    # slot, fraction

    def __init__(self, slot: int, player: VideoPlayer, parent=None):
        super().__init__(parent)
        self._slot   = slot
        self._player = player
        self._is_key = False

        self.setFixedHeight(68)
        self.setStyleSheet(f"""
            QFrame {{
                background:{PANEL};
                border-bottom: 1px solid {BORDER};
            }}
        """)

        root = QVBoxLayout(self)
        root.setContentsMargins(10, 4, 10, 4)
        root.setSpacing(3)

        # ── Row 1: letter · filename · scrubber · transport · time ────────────
        r1 = QHBoxLayout()
        r1.setSpacing(6)

        self._letter = QLabel(_TRACK_LABELS[slot])
        self._letter.setFixedWidth(14)
        self._letter.setStyleSheet(
            f'color:{ACCENT}; font-family:{MONO}; font-size:12px; font-weight:700;')
        r1.addWidget(self._letter)

        self._name_lbl = QLabel('— empty —')
        self._name_lbl.setStyleSheet(
            f'color:{DIM}; font-family:{MONO}; font-size:9px;')
        self._name_lbl.setFixedWidth(180)
        r1.addWidget(self._name_lbl)

        self._scrubber = Scrubber()
        self._scrubber.seek_requested.connect(
            lambda f: self.seek_requested.emit(self._slot, f))
        r1.addWidget(self._scrubber, 1)

        self._play_btn = QPushButton('▶')
        self._play_btn.setFixedSize(24, 24)
        self._play_btn.setStyleSheet(self._btn_style())
        self._play_btn.clicked.connect(self._on_play)
        r1.addWidget(self._play_btn)

        self._time_lbl = QLabel('--:-- / --:--')
        self._time_lbl.setFixedWidth(86)
        self._time_lbl.setAlignment(Qt.AlignmentFlag.AlignRight)
        self._time_lbl.setStyleSheet(
            f'color:{DIM}; font-family:{MONO}; font-size:8px;')
        r1.addWidget(self._time_lbl)

        root.addLayout(r1)

        # ── Row 2: load · mix · blend mode · key · speed · loop ──────────────
        r2 = QHBoxLayout()
        r2.setSpacing(6)

        load_btn = QPushButton('LOAD')
        load_btn.setFixedHeight(20)
        load_btn.setStyleSheet(self._btn_style(accent=True))
        load_btn.clicked.connect(lambda: self.load_requested.emit(self._slot))
        r2.addWidget(load_btn)

        mix_lbl = QLabel('MIX')
        mix_lbl.setStyleSheet(
            f'color:{DIM}; font-family:{MONO}; font-size:8px;')
        r2.addWidget(mix_lbl)

        self._mix_sl = QSlider(Qt.Orientation.Horizontal)
        self._mix_sl.setRange(0, 1000)
        self._mix_sl.setValue(1000 if slot == 0 else 0)
        self._mix_sl.setFixedWidth(90)
        self._mix_sl.setStyleSheet(self._slider_style())
        self._mix_sl.valueChanged.connect(
            lambda v: self.mix_changed.emit(self._slot, v / 1000.0))
        r2.addWidget(self._mix_sl)

        self._mode_combo = QComboBox()
        for name in BLEND_MODES:
            self._mode_combo.addItem(name)
        self._mode_combo.setCurrentIndex(1 if slot == 0 else 0)
        self._mode_combo.setFixedWidth(100)
        self._mode_combo.setStyleSheet(self._combo_style())
        self._mode_combo.currentIndexChanged.connect(
            lambda idx: self.mode_changed.emit(self._slot, idx))
        r2.addWidget(self._mode_combo)

        self._key_btn = QPushButton('KEY')
        self._key_btn.setFixedSize(34, 20)
        self._key_btn.setCheckable(True)
        self._key_btn.setStyleSheet(self._key_btn_style(False))
        self._key_btn.clicked.connect(self._on_key)
        r2.addWidget(self._key_btn)

        r2.addStretch()

        self._speed_combo = QComboBox()
        for label, _ in _SPEED_OPTIONS:
            self._speed_combo.addItem(label)
        self._speed_combo.setCurrentIndex(2)   # default 1×
        self._speed_combo.setFixedWidth(56)
        self._speed_combo.setStyleSheet(self._combo_style())
        self._speed_combo.currentIndexChanged.connect(self._on_speed)
        r2.addWidget(self._speed_combo)

        self._loop_btn = QPushButton('↺')
        self._loop_btn.setFixedSize(24, 20)
        self._loop_btn.setCheckable(True)
        self._loop_btn.setChecked(True)
        self._loop_btn.setStyleSheet(self._btn_style())
        self._loop_btn.clicked.connect(
            lambda v: self._player.set_loop(v))
        r2.addWidget(self._loop_btn)

        root.addLayout(r2)

    # ── Public ────────────────────────────────────────────────────────────────

    def set_loaded(self, name: str):
        short = name if len(name) <= 24 else '…' + name[-22:]
        self._name_lbl.setText(short)
        self._name_lbl.setStyleSheet(
            f'color:{TEXT}; font-family:{MONO}; font-size:9px;')
        self._play_btn.setText('⏸')
        if self._slot != 0:
            self._mix_sl.setValue(750)   # bring up mix on load

    def set_key_active(self, active: bool):
        self._is_key = active
        self._key_btn.setChecked(active)
        self._key_btn.setStyleSheet(self._key_btn_style(active))

    def update_transport(self):
        """Called by the main timer — refresh progress and time."""
        if not self._player.is_loaded():
            return
        self._scrubber.set_progress(self._player.get_progress())
        cur, total = self._player.get_position()
        self._time_lbl.setText(f'{self._fmt_t(cur)} / {self._fmt_t(total)}')
        if self._player.is_paused():
            self._play_btn.setText('▶')
        else:
            self._play_btn.setText('⏸')

    # ── Slots ─────────────────────────────────────────────────────────────────

    def _on_play(self):
        if self._player.is_loaded():
            self._player.toggle_pause()

    def _on_key(self, checked: bool):
        self._key_btn.setStyleSheet(self._key_btn_style(checked))
        self.key_selected.emit(self._slot if checked else -1)

    def _on_speed(self, idx: int):
        self._player.set_speed(_SPEED_OPTIONS[idx][1])

    # ── Helpers ───────────────────────────────────────────────────────────────

    @staticmethod
    def _fmt_t(secs: float) -> str:
        s = int(secs)
        return f'{s // 60:02d}:{s % 60:02d}'

    def _btn_style(self, accent: bool = False) -> str:
        col = ACCENT if accent else DIM
        bdr = ACCENT if accent else BORDER
        return f"""
            QPushButton {{
                background:{PANEL2}; color:{col}; border:1px solid {bdr};
                border-radius:3px; font-family:{MONO}; font-size:9px;
                padding:0 4px;
            }}
            QPushButton:hover {{ border-color:{ACCENT}; color:{ACCENT}; }}
            QPushButton:checked {{ background:{ACCENT}; color:{BG}; }}
        """

    def _key_btn_style(self, active: bool) -> str:
        if active:
            return f"""
                QPushButton {{
                    background:{ACCENT}; color:{BG}; border:1px solid {ACCENT};
                    border-radius:3px; font-family:{MONO}; font-size:8px;
                    font-weight:700; padding:0;
                }}
            """
        return f"""
            QPushButton {{
                background:{PANEL2}; color:{DIM}; border:1px solid {BORDER};
                border-radius:3px; font-family:{MONO}; font-size:8px; padding:0;
            }}
            QPushButton:hover {{ border-color:{ACCENT}; color:{ACCENT}; }}
        """

    @staticmethod
    def _slider_style() -> str:
        return f"""
            QSlider::groove:horizontal {{
                height:3px; background:{PANEL2}; border-radius:1px;
            }}
            QSlider::handle:horizontal {{
                width:11px; height:11px; margin:-4px 0;
                background:{ACCENT}; border-radius:5px;
            }}
            QSlider::sub-page:horizontal {{
                background:{ACCENT}; border-radius:1px; opacity:0.6;
            }}
        """

    @staticmethod
    def _combo_style() -> str:
        return f"""
            QComboBox {{
                background:{PANEL2}; color:{TEXT}; border:1px solid {BORDER};
                border-radius:3px; padding:1px 6px;
                font-family:{MONO}; font-size:9px;
            }}
            QComboBox:hover {{ border-color:{ACCENT}; }}
            QComboBox QAbstractItemView {{
                background:{PANEL}; color:{TEXT}; border:1px solid {BORDER};
                selection-background-color:{PANEL2};
            }}
            QComboBox::drop-down {{ border:none; }}
        """


# ─────────────────────────────────────────────────────────────────────────────
# Chroma colour swatch (click to pick key colour)
# ─────────────────────────────────────────────────────────────────────────────

class ChromaSwatch(QWidget):
    color_changed = pyqtSignal(float, float, float)   # r, g, b  0-1

    def __init__(self, r=0.0, g=0.8, b=0.0, parent=None):
        super().__init__(parent)
        self._rgb = (r, g, b)
        self.setFixedSize(28, 20)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip('Click to pick chroma key colour')

    def mousePressEvent(self, _e):
        r8, g8, b8 = [int(c * 255) for c in self._rgb]
        col = QColorDialog.getColor(QColor(r8, g8, b8), self, 'Chroma key colour')
        if col.isValid():
            self._rgb = (col.redF(), col.greenF(), col.blueF())
            self.update()
            self.color_changed.emit(*self._rgb)

    def paintEvent(self, _):
        p = QPainter(self)
        r8, g8, b8 = [int(c * 255) for c in self._rgb]
        p.fillRect(self.rect(), QColor(r8, g8, b8))
        p.setPen(QPen(QColor(ACCENT), 1))
        p.drawRect(self.rect().adjusted(0, 0, -1, -1))
        p.end()


# ─────────────────────────────────────────────────────────────────────────────
# Key panel
# ─────────────────────────────────────────────────────────────────────────────

class KeyPanel(QFrame):
    """Controls for luma / chroma keying of a selected source."""

    key_changed = pyqtSignal(int, int, float, float)    # src, mode, thresh, soft
    chroma_col_changed = pyqtSignal(float, float, float)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(54)
        self.setStyleSheet(f"""
            QFrame {{
                background:{PANEL2};
                border-top: 1px solid {BORDER};
            }}
        """)

        lay = QHBoxLayout(self)
        lay.setContentsMargins(12, 4, 12, 4)
        lay.setSpacing(10)

        # "KEY" label
        kl = QLabel('KEY')
        kl.setStyleSheet(
            f'color:{ACCENT}; font-family:{MONO}; font-size:9px; font-weight:700;')
        lay.addWidget(kl)

        # Source combo
        src_lbl = QLabel('src')
        src_lbl.setStyleSheet(
            f'color:{DIM}; font-family:{MONO}; font-size:8px;')
        lay.addWidget(src_lbl)
        self._src_combo = QComboBox()
        for label in ['—'] + _TRACK_LABELS:
            self._src_combo.addItem(label)
        self._src_combo.setFixedWidth(48)
        self._src_combo.setStyleSheet(TrackStrip._combo_style())
        self._src_combo.currentIndexChanged.connect(self._emit)
        lay.addWidget(self._src_combo)

        # Mode combo
        mode_lbl = QLabel('mode')
        mode_lbl.setStyleSheet(
            f'color:{DIM}; font-family:{MONO}; font-size:8px;')
        lay.addWidget(mode_lbl)
        self._mode_combo = QComboBox()
        for m in _KEY_MODES:
            self._mode_combo.addItem(m)
        self._mode_combo.setFixedWidth(150)
        self._mode_combo.setStyleSheet(TrackStrip._combo_style())
        self._mode_combo.currentIndexChanged.connect(self._emit)
        lay.addWidget(self._mode_combo)

        # Threshold
        self._thresh_sl = LabelSlider('thresh', 0.0, 1.0, 0.30, '{:.2f}', 90)
        self._thresh_sl.changed.connect(self._emit)
        lay.addWidget(self._thresh_sl)

        # Softness
        self._soft_sl = LabelSlider('soft', 0.0, 0.5, 0.06, '{:.2f}', 70)
        self._soft_sl.changed.connect(self._emit)
        lay.addWidget(self._soft_sl)

        # Chroma colour
        chroma_lbl = QLabel('colour')
        chroma_lbl.setStyleSheet(
            f'color:{DIM}; font-family:{MONO}; font-size:8px;')
        lay.addWidget(chroma_lbl)
        self._swatch = ChromaSwatch()
        self._swatch.color_changed.connect(self.chroma_col_changed)
        lay.addWidget(self._swatch)

        lay.addStretch()

    def set_src(self, slot: int):
        """Set the source track (0-3) as selected, -1 = none."""
        self._src_combo.blockSignals(True)
        self._src_combo.setCurrentIndex(slot + 1)  # offset by 1 (index 0 = '—')
        self._src_combo.blockSignals(False)

    def _emit(self):
        src_idx = self._src_combo.currentIndex() - 1  # 0 = '—' → -1
        mode    = self._mode_combo.currentIndex()
        thresh  = self._thresh_sl.value()
        soft    = self._soft_sl.value()
        self.key_changed.emit(src_idx, mode, thresh, soft)


# ─────────────────────────────────────────────────────────────────────────────
# Output panel (gain, saturation, contrast, scanlines)
# ─────────────────────────────────────────────────────────────────────────────

class OutputPanel(QWidget):
    changed = pyqtSignal(float, float, float, float)  # gain, sat, contrast, scanlines

    def __init__(self, parent=None):
        super().__init__(parent)

        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(10)

        ol = QLabel('OUT')
        ol.setStyleSheet(
            f'color:{ACCENT}; font-family:{MONO}; font-size:9px; font-weight:700;')
        lay.addWidget(ol)

        self._gain = LabelSlider('gain', 0.0, 1.0, 0.60, '{:.2f}', 80)
        self._sat  = LabelSlider('sat',  0.0, 2.0, 1.00, '{:.2f}', 80)
        self._con  = LabelSlider('contr',0.0, 2.0, 1.00, '{:.2f}', 80)
        self._sl   = LabelSlider('lines',0.0, 1.0, 0.00, '{:.2f}', 60)

        for s in (self._gain, self._sat, self._con, self._sl):
            s.changed.connect(self._emit)
            lay.addWidget(s)

    def _emit(self):
        self.changed.emit(
            self._gain.value(), self._sat.value(),
            self._con.value(),  self._sl.value())


# ─────────────────────────────────────────────────────────────────────────────
# Difference key panel (PhotoBooth-style background subtraction)
# ─────────────────────────────────────────────────────────────────────────────

class BgSubPanel(QFrame):
    """
    Difference key — grab a clean background plate, subtract it live.

    Foreground (subject) pixels that differ from the plate stay visible;
    matching pixels (the background) are replaced by the nominated track.
    Inspired by early Quantel / Grass Valley difference-key units.
    """

    params_changed  = pyqtSignal(bool, int, int, float, float, float)
    #                             on,  fg,  bg, thresh, soft, blur
    grab_requested  = pyqtSignal(int)   # slot index to grab reference from
    clear_requested = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(54)
        self.setStyleSheet(f"""
            QFrame {{
                background:{PANEL2};
                border-top: 1px solid {BORDER};
            }}
        """)

        lay = QHBoxLayout(self)
        lay.setContentsMargins(12, 4, 12, 4)
        lay.setSpacing(10)

        # Section label
        lbl = QLabel('DIFF KEY')
        lbl.setStyleSheet(
            f'color:{ACCENT}; font-family:{MONO}; font-size:9px; font-weight:700;')
        lay.addWidget(lbl)

        # Subject (foreground) track selector
        subj_lbl = QLabel('subj')
        subj_lbl.setStyleSheet(
            f'color:{DIM}; font-family:{MONO}; font-size:8px;')
        lay.addWidget(subj_lbl)
        self._subj_combo = QComboBox()
        for label in _TRACK_LABELS:
            self._subj_combo.addItem(label)
        self._subj_combo.setCurrentIndex(0)
        self._subj_combo.setFixedWidth(46)
        self._subj_combo.setStyleSheet(TrackStrip._combo_style())
        self._subj_combo.currentIndexChanged.connect(self._emit)
        lay.addWidget(self._subj_combo)

        # Replacement background track selector
        bg_lbl = QLabel('bgnd')
        bg_lbl.setStyleSheet(
            f'color:{DIM}; font-family:{MONO}; font-size:8px;')
        lay.addWidget(bg_lbl)
        self._bg_combo = QComboBox()
        for label in _TRACK_LABELS:
            self._bg_combo.addItem(label)
        self._bg_combo.setCurrentIndex(1)   # default B
        self._bg_combo.setFixedWidth(46)
        self._bg_combo.setStyleSheet(TrackStrip._combo_style())
        self._bg_combo.currentIndexChanged.connect(self._emit)
        lay.addWidget(self._bg_combo)

        # GRAB PLATE button — captures the reference frame
        self._grab_btn = QPushButton('GRAB PLATE')
        self._grab_btn.setFixedHeight(22)
        self._grab_btn.setStyleSheet(self._grab_style(False))
        self._grab_btn.setToolTip(
            'Capture current frame of the subject track as background reference')
        self._grab_btn.clicked.connect(self._on_grab)
        lay.addWidget(self._grab_btn)

        # CLEAR button — discard reference
        self._clear_btn = QPushButton('CLR')
        self._clear_btn.setFixedSize(32, 22)
        self._clear_btn.setStyleSheet(f"""
            QPushButton {{
                background:{PANEL2}; color:{DIM}; border:1px solid {BORDER};
                border-radius:3px; font-family:{MONO}; font-size:8px; padding:0;
            }}
            QPushButton:hover {{ border-color:{ACCENT}; color:{ACCENT}; }}
        """)
        self._clear_btn.clicked.connect(self._on_clear)
        lay.addWidget(self._clear_btn)

        # Threshold slider
        self._thresh_sl = LabelSlider('thresh', 0.0, 0.5, 0.12, '{:.3f}', 80)
        self._thresh_sl.changed.connect(self._emit)
        lay.addWidget(self._thresh_sl)

        # Softness slider
        self._soft_sl = LabelSlider('soft', 0.0, 0.2, 0.05, '{:.3f}', 70)
        self._soft_sl.changed.connect(self._emit)
        lay.addWidget(self._soft_sl)

        # Blur radius slider
        self._blur_sl = LabelSlider('blur', 0.0, 8.0, 2.0, '{:.1f}', 60)
        self._blur_sl.changed.connect(self._emit)
        lay.addWidget(self._blur_sl)

        lay.addStretch()

        # ON / OFF toggle — disabled until a plate has been grabbed
        self._on_btn = QPushButton('ON')
        self._on_btn.setFixedSize(36, 22)
        self._on_btn.setCheckable(True)
        self._on_btn.setChecked(False)
        self._on_btn.setEnabled(False)
        self._on_btn.setStyleSheet(self._on_style(False))
        self._on_btn.clicked.connect(self._on_toggle)
        lay.addWidget(self._on_btn)

        self._plate_grabbed = False

    # ── Public ────────────────────────────────────────────────────────────────

    def set_plate_grabbed(self):
        """Call this after a reference plate is successfully captured."""
        self._plate_grabbed = True
        self._on_btn.setEnabled(True)
        self._grab_btn.setStyleSheet(self._grab_style(True))

    def reset_plate(self):
        """Called when the plate is cleared."""
        self._plate_grabbed = False
        self._on_btn.setEnabled(False)
        self._on_btn.setChecked(False)
        self._on_btn.setStyleSheet(self._on_style(False))
        self._grab_btn.setStyleSheet(self._grab_style(False))
        self._emit()

    # ── Private ───────────────────────────────────────────────────────────────

    def _on_grab(self):
        self.grab_requested.emit(self._subj_combo.currentIndex())

    def _on_clear(self):
        self.reset_plate()
        self.clear_requested.emit()

    def _on_toggle(self, checked: bool):
        self._on_btn.setStyleSheet(self._on_style(checked))
        self._emit()

    def _emit(self):
        on = self._on_btn.isChecked() and self._plate_grabbed
        self.params_changed.emit(
            on,
            self._subj_combo.currentIndex(),
            self._bg_combo.currentIndex(),
            self._thresh_sl.value(),
            self._soft_sl.value(),
            self._blur_sl.value(),
        )

    def _grab_style(self, grabbed: bool) -> str:
        col = GREEN if grabbed else ACCENT
        return f"""
            QPushButton {{
                background:{PANEL2}; color:{col}; border:1px solid {col};
                border-radius:3px; font-family:{MONO}; font-size:8px;
                letter-spacing:1px; padding:0 6px;
            }}
            QPushButton:hover {{ background:{col}; color:{BG}; }}
        """

    def _on_style(self, active: bool) -> str:
        if active:
            return f"""
                QPushButton {{
                    background:{GREEN}; color:{BG}; border:1px solid {GREEN};
                    border-radius:3px; font-family:{MONO}; font-size:9px;
                    font-weight:700; padding:0;
                }}
            """
        return f"""
            QPushButton {{
                background:{PANEL2}; color:{DIM}; border:1px solid {BORDER};
                border-radius:3px; font-family:{MONO}; font-size:9px; padding:0;
            }}
            QPushButton:hover {{ border-color:{GREEN}; color:{GREEN}; }}
            QPushButton:disabled {{ color:{DIM}; border-color:{BORDER}; opacity:0.5; }}
        """


# ─────────────────────────────────────────────────────────────────────────────
# Main window
# ─────────────────────────────────────────────────────────────────────────────

class FugueWindow(QMainWindow):

    def __init__(self):
        super().__init__()
        self.setWindowTitle('FUGUE')
        self.resize(1100, 900)
        self._apply_style()

        # ── Core objects ──────────────────────────────────────────────────────
        self._canvas  = FugueCanvas()
        self._players = [VideoPlayer() for _ in range(4)]
        self._active_key_slot = -1   # which slot's KEY button is active (-1=none)

        # ── Build UI ──────────────────────────────────────────────────────────
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        root.addWidget(self._build_header())
        root.addWidget(self._canvas, 1)
        root.addWidget(self._build_tracks())
        root.addWidget(self._build_key_panel())
        root.addWidget(self._build_bg_sub_panel())
        root.addWidget(self._build_status())

        # ── Timers ────────────────────────────────────────────────────────────
        self._push_timer = QTimer(self)
        self._push_timer.timeout.connect(self._push_frames)
        self._push_timer.start(16)   # ~60 fps frame push

        self._ui_timer = QTimer(self)
        self._ui_timer.timeout.connect(self._update_ui)
        self._ui_timer.start(100)    # 10 Hz UI refresh

        # ── Shortcuts ─────────────────────────────────────────────────────────
        QShortcut(QKeySequence('Ctrl+O'), self).activated.connect(
            lambda: self._load_video(self._find_active_slot()))
        QShortcut(QKeySequence('Space'),  self).activated.connect(
            self._toggle_all_pause)
        QShortcut(QKeySequence('Ctrl+S'), self).activated.connect(
            self._screenshot)
        QShortcut(QKeySequence('Ctrl+Q'), self).activated.connect(self.close)

        # ── FPS readout ───────────────────────────────────────────────────────
        self._canvas.fps_updated.connect(
            lambda fps: self._fps_lbl.setText(f'{fps:.0f} fps'))

    # ── UI builders ───────────────────────────────────────────────────────────

    def _build_header(self) -> QWidget:
        bar = QWidget()
        bar.setFixedHeight(46)
        bar.setStyleSheet(f'background:{PANEL}; border-bottom:1px solid {BORDER};')
        lay = QHBoxLayout(bar)
        lay.setContentsMargins(16, 0, 16, 0)
        lay.setSpacing(14)

        title = QLabel('FUGUE')
        title.setStyleSheet(
            f'color:{ACCENT}; font-family:{MONO}; font-size:15px; '
            f'font-weight:700; letter-spacing:5px;')
        lay.addWidget(title)

        sub = QLabel('multi-source compositor')
        sub.setStyleSheet(
            f'color:{DIM}; font-family:{MONO}; font-size:9px; letter-spacing:2px;')
        lay.addWidget(sub)

        lay.addStretch()

        self._output_panel = OutputPanel()
        self._output_panel.changed.connect(self._on_output_change)
        lay.addWidget(self._output_panel)

        lay.addSpacing(8)

        shot_btn = QPushButton('SHOT')
        shot_btn.setStyleSheet(self._hdr_btn_style())
        shot_btn.setToolTip('Save composite screenshot  (Ctrl+S)')
        shot_btn.clicked.connect(self._screenshot)
        lay.addWidget(shot_btn)

        return bar

    def _build_tracks(self) -> QWidget:
        container = QWidget()
        lay = QVBoxLayout(container)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)

        self._strips: list[TrackStrip] = []
        for i, player in enumerate(self._players):
            strip = TrackStrip(i, player)
            strip.load_requested.connect(self._load_video)
            strip.mix_changed.connect(
                lambda slot, v: self._canvas.set_mix(slot, v))
            strip.mode_changed.connect(
                lambda slot, m: self._canvas.set_mode(slot, m))
            strip.seek_requested.connect(
                lambda slot, f: self._players[slot].seek(f))
            strip.key_selected.connect(self._on_key_selected)
            self._strips.append(strip)
            lay.addWidget(strip)

        return container

    def _build_key_panel(self) -> QWidget:
        self._key_panel = KeyPanel()
        self._key_panel.key_changed.connect(
            lambda src, mode, thresh, soft:
                self._canvas.set_key(src, mode, thresh, soft))
        self._key_panel.chroma_col_changed.connect(
            lambda r, g, b: self._canvas.set_chroma_col(r, g, b))
        return self._key_panel

    def _build_bg_sub_panel(self) -> QWidget:
        self._bg_sub_panel = BgSubPanel()
        self._bg_sub_panel.params_changed.connect(
            lambda on, fg, bg, t, s, b:
                self._canvas.set_bg_sub(on, fg, bg, t, s, b))
        self._bg_sub_panel.grab_requested.connect(self._grab_bg_ref)
        self._bg_sub_panel.clear_requested.connect(self._clear_bg_ref)
        return self._bg_sub_panel

    def _build_status(self) -> QWidget:
        bar = QWidget()
        bar.setFixedHeight(22)
        bar.setStyleSheet(f'background:{PANEL2}; border-top:1px solid {BORDER};')
        lay = QHBoxLayout(bar)
        lay.setContentsMargins(12, 0, 12, 0)
        lay.setSpacing(14)

        self._fps_lbl = QLabel('-- fps')
        self._fps_lbl.setStyleSheet(
            f'color:{DIM}; font-family:{MONO}; font-size:9px;')
        lay.addWidget(self._fps_lbl)

        lay.addStretch()

        hint = QLabel(
            'Ctrl+O load · Space pause/resume all · KEY luma/chroma · DIFF KEY grab plate → isolate subject · Ctrl+S screenshot')
        hint.setStyleSheet(
            f'color:{DIM}; font-family:{MONO}; font-size:9px;')
        lay.addWidget(hint)

        self._status_lbl = QLabel('')
        self._status_lbl.setStyleSheet(
            f'color:{TEXT}; font-family:{MONO}; font-size:9px;')
        lay.addWidget(self._status_lbl)

        return bar

    # ── Timer callbacks ───────────────────────────────────────────────────────

    def _push_frames(self):
        """Pull latest frames from all players and push to the canvas."""
        for i, player in enumerate(self._players):
            if player.is_loaded():
                self._canvas.set_frame(i, player.get_frame())

    def _update_ui(self):
        """Refresh scrubbers and time labels."""
        for strip in self._strips:
            strip.update_transport()

    # ── Signal handlers ───────────────────────────────────────────────────────

    def _load_video(self, slot: int):
        if not (0 <= slot < 4):
            slot = 0
        path, _ = QFileDialog.getOpenFileName(
            self, f'Load video for track {_TRACK_LABELS[slot]}',
            str(Path.home() / 'Movies'),
            'Video files (*.mp4 *.mov *.avi *.mkv *.webm *.m4v *.mpg *.mpeg);;'
            'All files (*.*)')
        if not path:
            return
        ok = self._players[slot].load(path)
        if ok:
            self._strips[slot].set_loaded(self._players[slot].name)
            # First loaded track: ensure mode = Mix if it was Off
            if self._canvas._modes[slot] == 0:
                self._canvas.set_mode(slot, 1)
            self._set_status(f'Track {_TRACK_LABELS[slot]}: {self._players[slot].name}')
        else:
            self._set_status(f'Could not load: {path}  (is opencv-python installed?)')

    def _on_key_selected(self, slot: int):
        """A track's KEY button was clicked — update mutual exclusion."""
        if slot == -1:
            # deselected
            self._active_key_slot = -1
            for strip in self._strips:
                strip.set_key_active(False)
            self._key_panel.set_src(-1)
            self._canvas.set_key(-1, 0, 0.3, 0.06)
        else:
            if self._active_key_slot == slot:
                # toggle off
                slot = -1
                self._active_key_slot = -1
                for strip in self._strips:
                    strip.set_key_active(False)
                self._key_panel.set_src(-1)
                self._canvas.set_key(-1, 0, 0.3, 0.06)
            else:
                # activate new slot, deactivate others
                self._active_key_slot = slot
                for i, strip in enumerate(self._strips):
                    strip.set_key_active(i == slot)
                self._key_panel.set_src(slot)
                # key panel will emit key_changed which updates canvas

    def _grab_bg_ref(self, slot: int):
        """Grab the current frame from a track and store it as the reference plate."""
        player = self._players[slot]
        if not player.is_loaded():
            self._set_status(
                f'Track {_TRACK_LABELS[slot]} not loaded — load a video first')
            return
        frame = player.get_frame()
        if frame is None:
            self._set_status('No frame available yet — wait for playback to start')
            return
        self._canvas.grab_reference(frame)
        self._bg_sub_panel.set_plate_grabbed()
        self._set_status(
            f'Reference plate grabbed from track {_TRACK_LABELS[slot]}  '
            f'— enable DIFF KEY and adjust thresh to isolate subject')

    def _clear_bg_ref(self):
        """Discard the reference plate and disable BG sub."""
        self._canvas.clear_reference()
        self._set_status('Reference plate cleared')

    def _on_output_change(self, gain: float, sat: float,
                          contrast: float, scanlines: float):
        self._canvas.set_output(gain, sat, contrast, scanlines)

    def _toggle_all_pause(self):
        """Pause / resume all loaded players simultaneously."""
        any_playing = any(
            p.is_loaded() and not p.is_paused() for p in self._players)
        for p in self._players:
            if p.is_loaded():
                if any_playing:
                    p.pause()
                else:
                    p.resume()

    def _screenshot(self):
        arr = self._canvas.grab_frame()
        if arr is None:
            self._set_status('Screenshot failed — no frame available')
            return
        try:
            import cv2
            ts   = datetime.now().strftime('%Y%m%d_%H%M%S')
            path = _SCREENSHOTS / f'fugue_{ts}.png'
            cv2.imwrite(str(path), cv2.cvtColor(arr, cv2.COLOR_RGB2BGR))
            self._set_status(f'Saved {path.name}')
        except Exception as e:
            self._set_status(f'Screenshot error: {e}')

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _find_active_slot(self) -> int:
        """Return the first empty slot, or 0 if all are loaded."""
        for i, p in enumerate(self._players):
            if not p.is_loaded():
                return i
        return 0

    def _set_status(self, msg: str, timeout: int = 4000):
        self._status_lbl.setText(msg)
        QTimer.singleShot(timeout, lambda: self._status_lbl.setText(''))

    def closeEvent(self, e):
        self._push_timer.stop()
        self._ui_timer.stop()
        for p in self._players:
            p.stop()
        e.accept()

    # ── Style helpers ─────────────────────────────────────────────────────────

    def _apply_style(self):
        self.setStyleSheet(f"""
            QMainWindow, QWidget {{
                background:{BG}; color:{TEXT};
            }}
            QLabel {{
                color:{TEXT}; font-family:{MONO}; font-size:9px;
            }}
            QPushButton {{
                background:{PANEL2}; color:{DIM}; border:1px solid {BORDER};
                border-radius:3px; padding:2px 8px;
                font-family:{MONO}; font-size:9px;
            }}
            QPushButton:hover {{ border-color:{ACCENT}; color:{ACCENT}; }}
            QComboBox {{
                background:{PANEL2}; color:{TEXT}; border:1px solid {BORDER};
                border-radius:3px; padding:1px 6px; font-family:{MONO}; font-size:9px;
            }}
            QComboBox:hover {{ border-color:{ACCENT}; }}
            QScrollBar {{ background:{BG}; border:none; }}
        """)

    @staticmethod
    def _hdr_btn_style() -> str:
        return f"""
            QPushButton {{
                background:{PANEL2}; color:{ACCENT}; border:1px solid {ACCENT};
                border-radius:3px; padding:2px 10px;
                font-family:{MONO}; font-size:9px; letter-spacing:1px;
            }}
            QPushButton:hover {{ background:{ACCENT}; color:{BG}; }}
        """


# ─────────────────────────────────────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────────────────────────────────────

def main():
    try:
        QApplication.setHighDpiScaleFactorRoundingPolicy(
            Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    except AttributeError:
        pass
    app = QApplication(sys.argv)
    app.setApplicationName('Fugue')

    win = FugueWindow()
    win.show()
    sys.exit(app.exec())


if __name__ == '__main__':
    main()
