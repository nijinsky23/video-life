#!/usr/bin/env python3
"""
SCAN PROCESSOR — Raster Deflection Synthesizer
───────────────────────────────────────────────
Inspired by the Rutt-Etra Video Synthesizer (1972), Sandin Image Processor
(1971), and the analog raster-manipulation tradition of early video art.

Four GLSL engines:
  Terrain  — video / procedural luminance → vertical scan-line deflection
  Spectrum — FFT spectrum displayed as displaced glowing scan lines
  Warp     — overlapping sine-wave deflection without a signal source
  Etch     — warped 2D mesh with H + V scan lines and intersection nodes

Controls
  · 8 parameter knobs  (drag up/down, right-click → MIDI learn)
  · Audio input selector
  · MIDI input selector
  · Ctrl+S  → screenshot
  · Ctrl+Q  → quit
"""

import sys
import os
import math
import json
import time
from pathlib import Path
from datetime import datetime

# ── Path setup ────────────────────────────────────────────────────────────────
_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
for p in (_HERE, _ROOT):
    if p not in sys.path:
        sys.path.insert(0, p)

# ── Qt / OpenGL ───────────────────────────────────────────────────────────────
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QPushButton, QComboBox, QSizePolicy, QFrame, QToolButton,
)
from PyQt6.QtCore  import Qt, QTimer, pyqtSignal, QRectF, QPointF
from PyQt6.QtGui   import (
    QFont, QColor, QFontDatabase,
    QPainter, QPen, QBrush, QRadialGradient, QLinearGradient,
    QKeySequence, QShortcut,
)

# ── Local modules ─────────────────────────────────────────────────────────────
from canvas       import ScanCanvas
from core.audio_engine import AudioEngine
from core.midi_engine  import MidiEngine
from shaders      import SHADERS, PARAM_NAMES, PARAM_DEFAULTS

# ── Colour tokens (same family as Video Life) ─────────────────────────────────
BG       = '#0a0000'
PANEL    = '#130000'
PANEL2   = '#1e0000'
ACCENT   = '#FF2020'
TEXT     = '#d4aaaa'
TEXT_DIM = '#5c2a2a'
BORDER   = 'rgba(255,30,30,0.12)'

_MONO = "'Menlo','Monaco','SF Mono','Courier New',monospace"

# ─────────────────────────────────────────────────────────────────────────────
# Knob widget (identical rendering to Video Life's SynthKnob)
# ─────────────────────────────────────────────────────────────────────────────
from PyQt6.QtWidgets import QDial

class ScanKnob(QDial):
    """Arc-ring synth knob — custom painted, drag to change, right-click MIDI."""
    learn_requested = pyqtSignal(int)

    _MIN_ANGLE = 210
    _SWEEP     = 300

    def __init__(self, index: int, name: str, value: float = 0.5, parent=None):
        super().__init__(parent)
        self.index = index
        self.setRange(0, 1000)
        self.setValue(int(value * 1000))
        self.setWrapping(False)
        self.setNotchesVisible(False)
        self.setFixedSize(54, 54)
        self._hovered  = False
        self._drag_y   = None
        self._drag_val = 0
        self._midi_cc  = None
        self._learning = False
        self.setToolTip(f'{name}\nRight-click → MIDI learn')
        self.valueChanged.connect(self.update)

    def enterEvent(self, e): self._hovered = True;  self.update()
    def leaveEvent(self, e): self._hovered = False; self.update()

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.RightButton:
            self.learn_requested.emit(self.index)
        elif e.button() == Qt.MouseButton.LeftButton:
            self._drag_y   = e.position().y()
            self._drag_val = self.value()
            e.accept()

    def mouseMoveEvent(self, e):
        if self._drag_y is None:
            return
        dy = self._drag_y - e.position().y()
        self.setValue(max(0, min(1000, self._drag_val + int(dy * 4))))
        e.accept()

    def mouseReleaseEvent(self, e):
        self._drag_y = None
        e.accept()

    def get_value(self) -> float:
        return self.value() / 1000.0

    def set_midi_cc(self, cc: int | None):
        self._midi_cc  = cc
        self._learning = False
        self.update()

    def paintEvent(self, _):
        p   = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h   = self.width(), self.height()
        cx, cy = w / 2.0, h / 2.0
        r      = min(w, h) / 2.0 - 2.5
        val    = self.value() / 1000.0

        grad = QRadialGradient(cx * 0.6, cy * 0.5, r * 1.2)
        grad.setColorAt(0, QColor('#221018'))
        grad.setColorAt(1, QColor('#090408'))
        p.setBrush(QBrush(grad))

        if self._learning:    border = QColor(ACCENT)
        elif self._midi_cc:   border = QColor(ACCENT)
        elif self._hovered:   border = QColor(ACCENT); border.setAlpha(90)
        else:                 border = QColor(80, 20, 20, 55)
        p.setPen(QPen(border, 1.5))
        p.drawEllipse(QRectF(cx - r, cy - r, r * 2, r * 2))

        arc_r  = r * 0.76
        ar     = QRectF(cx - arc_r, cy - arc_r, arc_r * 2, arc_r * 2)
        start  = int((90 - self._MIN_ANGLE) * 16)

        dim = QColor(ACCENT); dim.setAlpha(28)
        tp  = QPen(dim, 2.5); tp.setCapStyle(Qt.PenCapStyle.FlatCap)
        p.setPen(tp)
        p.drawArc(ar, start, int(-self._SWEEP * 16))

        if val > 0.001:
            vp = QPen(QColor(ACCENT), 2.5); vp.setCapStyle(Qt.PenCapStyle.FlatCap)
            p.setPen(vp)
            p.drawArc(ar, start, int(-val * self._SWEEP * 16))

        angle = math.radians(self._MIN_ANGLE + val * self._SWEEP)
        sa, ca = math.sin(angle), math.cos(angle)
        x1 = cx + r * 0.22 * sa;  y1 = cy - r * 0.22 * ca
        x2 = cx + r * 0.58 * sa;  y2 = cy - r * 0.58 * ca
        pp  = QPen(QColor('#cccccc'), 1.8); pp.setCapStyle(Qt.PenCapStyle.RoundCap)
        p.setPen(pp)
        p.drawLine(QPointF(x1, y1), QPointF(x2, y2))

        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(QColor(255, 255, 255, 45)))
        p.drawEllipse(QPointF(cx, cy), 2.2, 2.2)
        p.end()


# ─────────────────────────────────────────────────────────────────────────────
# Mini FFT scope (bottom-left status strip)
# ─────────────────────────────────────────────────────────────────────────────
from PyQt6.QtWidgets import QWidget as _QW

class MiniScope(_QW):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(90, 28)
        self._data = [0.0] * 32
        self._rms  = 0.0

    def update_data(self, fft, rms):
        n = len(self._data)
        stride = max(1, len(fft) // n)
        self._data = [max(fft[i * stride:(i + 1) * stride]) for i in range(n)]
        self._rms  = rms
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.fillRect(self.rect(), QColor(BG))
        w, h = self.width(), self.height()
        n = len(self._data)
        bw = w / n
        for i, v in enumerate(self._data):
            bh = max(1.0, v * h)
            clr = QColor(ACCENT); clr.setAlpha(120 + int(v * 135))
            p.fillRect(QRectF(i * bw, h - bh, bw - 1, bh), clr)
        p.end()


# ─────────────────────────────────────────────────────────────────────────────
# Main window
# ─────────────────────────────────────────────────────────────────────────────

_SCREENSHOTS = Path.home() / 'Pictures' / 'scan-processor'
_SCREENSHOTS.mkdir(parents=True, exist_ok=True)


class ScanProcessorWindow(QMainWindow):

    def __init__(self):
        super().__init__()
        self.setWindowTitle('Scan Processor')
        self.resize(1100, 720)
        self._apply_palette()

        # ── Engines ───────────────────────────────────────────────────────────
        self._canvas = ScanCanvas()
        self._audio  = AudioEngine()
        self._midi   = MidiEngine()
        self._midi.set_callback(cc_cb=self._on_midi_cc)

        # Poll AudioEngine each frame (same pattern as Video Life)
        self._audio_timer = QTimer(self)
        self._audio_timer.timeout.connect(self._poll_audio)
        self._audio_timer.start(33)  # ~30 Hz

        # ── MIDI learn state ──────────────────────────────────────────────────
        self._learn_idx: int | None = None
        self._cc_map: dict[int, int] = {}   # cc → knob index

        # ── Current engine defaults ───────────────────────────────────────────
        self._engine = list(SHADERS.keys())[0]
        self._params = list(PARAM_DEFAULTS[self._engine])
        self._canvas.set_params(self._params)

        # ── Build UI ──────────────────────────────────────────────────────────
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        root.addWidget(self._build_header())
        root.addWidget(self._canvas, 1)
        root.addWidget(self._build_controls())
        root.addWidget(self._build_statusbar())

        # ── Keyboard shortcuts ────────────────────────────────────────────────
        QShortcut(QKeySequence('Ctrl+S'), self).activated.connect(self._screenshot)
        QShortcut(QKeySequence('Ctrl+Q'), self).activated.connect(self.close)

        # ── FPS display ───────────────────────────────────────────────────────
        self._canvas.fps_updated.connect(self._on_fps)

        # ── Start audio on first available device ─────────────────────────────
        if self._audio.input_devices:
            self._audio.start_input(0)

    # ── UI builders ──────────────────────────────────────────────────────────

    def _build_header(self) -> QWidget:
        bar = QWidget()
        bar.setFixedHeight(44)
        bar.setStyleSheet(f'background:{PANEL}; border-bottom:1px solid {BORDER};')
        lay = QHBoxLayout(bar)
        lay.setContentsMargins(18, 0, 18, 0)
        lay.setSpacing(14)

        title = QLabel('SCAN PROCESSOR')
        title.setStyleSheet(
            f'color:{ACCENT}; font-family:{_MONO}; font-size:13px; '
            f'font-weight:700; letter-spacing:4px;')
        lay.addWidget(title)

        sub = QLabel('RASTER DEFLECTION SYNTHESIZER')
        sub.setStyleSheet(
            f'color:{TEXT_DIM}; font-family:{_MONO}; font-size:9px; letter-spacing:3px;')
        lay.addWidget(sub)

        lay.addStretch()

        # Engine selector
        eng_lbl = QLabel('ENGINE')
        eng_lbl.setStyleSheet(
            f'color:{TEXT_DIM}; font-family:{_MONO}; font-size:9px; letter-spacing:2px;')
        lay.addWidget(eng_lbl)

        self._engine_combo = QComboBox()
        for name in SHADERS:
            self._engine_combo.addItem(name)
        self._engine_combo.setFixedWidth(120)
        self._engine_combo.setStyleSheet(self._combo_style())
        self._engine_combo.currentTextChanged.connect(self._on_engine_change)
        lay.addWidget(self._engine_combo)

        return bar

    def _build_controls(self) -> QWidget:
        strip = QWidget()
        strip.setFixedHeight(96)
        strip.setStyleSheet(f'background:{PANEL}; border-top:1px solid {BORDER};')
        lay = QHBoxLayout(strip)
        lay.setContentsMargins(18, 8, 18, 8)
        lay.setSpacing(12)

        self._knobs: list[ScanKnob] = []
        for i, (name, default_val) in enumerate(zip(PARAM_NAMES, self._params)):
            col = QVBoxLayout()
            col.setSpacing(2)
            col.setAlignment(Qt.AlignmentFlag.AlignHCenter)

            knob = ScanKnob(i, name, default_val)
            knob.valueChanged.connect(lambda v, idx=i: self._on_knob(idx, v))
            knob.learn_requested.connect(self._on_learn_request)
            self._knobs.append(knob)
            col.addWidget(knob, alignment=Qt.AlignmentFlag.AlignHCenter)

            lbl = QLabel(name.upper())
            lbl.setAlignment(Qt.AlignmentFlag.AlignHCenter)
            lbl.setStyleSheet(
                f'color:{TEXT_DIM}; font-family:{_MONO}; font-size:8px; letter-spacing:1px;')
            col.addWidget(lbl)

            lay.addLayout(col)

        lay.addStretch()

        # Right side: audio device + MIDI
        right = QVBoxLayout()
        right.setSpacing(4)

        # Audio device
        audio_row = QHBoxLayout()
        audio_row.setSpacing(6)
        albl = QLabel('AUDIO IN')
        albl.setStyleSheet(
            f'color:{TEXT_DIM}; font-family:{_MONO}; font-size:8px; letter-spacing:1px;')
        audio_row.addWidget(albl)
        self._audio_combo = QComboBox()
        self._audio_combo.setFixedWidth(200)
        self._audio_combo.setStyleSheet(self._combo_style())
        self._populate_audio_combo()
        self._audio_combo.currentIndexChanged.connect(self._on_audio_device)
        audio_row.addWidget(self._audio_combo)
        right.addLayout(audio_row)

        # MIDI device
        midi_row = QHBoxLayout()
        midi_row.setSpacing(6)
        mlbl = QLabel('MIDI IN')
        mlbl.setStyleSheet(
            f'color:{TEXT_DIM}; font-family:{_MONO}; font-size:8px; letter-spacing:1px;')
        midi_row.addWidget(mlbl)
        self._midi_combo = QComboBox()
        self._midi_combo.setFixedWidth(200)
        self._midi_combo.setStyleSheet(self._combo_style())
        self._populate_midi_combo()
        self._midi_combo.currentIndexChanged.connect(self._on_midi_device)
        midi_row.addWidget(self._midi_combo)
        right.addLayout(midi_row)

        lay.addLayout(right)
        return strip

    def _build_statusbar(self) -> QWidget:
        bar = QWidget()
        bar.setFixedHeight(24)
        bar.setStyleSheet(f'background:{PANEL2}; border-top:1px solid {BORDER};')
        lay = QHBoxLayout(bar)
        lay.setContentsMargins(12, 0, 12, 0)
        lay.setSpacing(14)

        self._scope = MiniScope()
        lay.addWidget(self._scope)

        self._fps_lbl = QLabel('-- fps')
        self._fps_lbl.setStyleSheet(
            f'color:{TEXT_DIM}; font-family:{_MONO}; font-size:9px;')
        lay.addWidget(self._fps_lbl)

        lay.addStretch()

        self._status_lbl = QLabel('Ctrl+S = screenshot  ·  right-click knob = MIDI learn')
        self._status_lbl.setStyleSheet(
            f'color:{TEXT_DIM}; font-family:{_MONO}; font-size:9px;')
        lay.addWidget(self._status_lbl)

        return bar

    # ── Style helpers ─────────────────────────────────────────────────────────

    def _apply_palette(self):
        self.setStyleSheet(f"""
            QMainWindow, QWidget {{ background:{BG}; color:{TEXT}; }}
            QLabel {{ color:{TEXT}; font-family:{_MONO}; font-size:10px; }}
            QPushButton {{
                background:{PANEL2}; color:{TEXT}; border:1px solid {BORDER};
                border-radius:3px; padding:3px 10px;
                font-family:{_MONO}; font-size:10px; letter-spacing:1px;
            }}
            QPushButton:hover  {{ border-color:{ACCENT}; color:{ACCENT}; }}
            QPushButton:pressed {{ background:{ACCENT}; color:#000; }}
        """)

    @staticmethod
    def _combo_style() -> str:
        return f"""
            QComboBox {{
                background:{PANEL2}; color:{TEXT}; border:1px solid {BORDER};
                border-radius:3px; padding:2px 8px;
                font-family:{_MONO}; font-size:9px;
            }}
            QComboBox:hover {{ border-color:{ACCENT}; }}
            QComboBox QAbstractItemView {{
                background:{PANEL}; color:{TEXT}; border:1px solid {BORDER};
                selection-background-color:{PANEL2};
            }}
            QComboBox::drop-down {{ border:none; }}
        """

    # ── Signal handlers ───────────────────────────────────────────────────────

    def _on_engine_change(self, name: str):
        self._engine = name
        self._canvas.set_mode(name)
        # Restore per-engine default params
        defaults = PARAM_DEFAULTS.get(name, [0.5] * 8)
        self._params = list(defaults)
        self._canvas.set_params(self._params)
        for i, knob in enumerate(self._knobs):
            knob.blockSignals(True)
            knob.setValue(int(self._params[i] * 1000))
            knob.blockSignals(False)
        self._chain_diag_reset()

    def _chain_diag_reset(self):
        # Reset the blend-chain diagnostic flag so the new engine prints diagnostics
        if hasattr(self._canvas, '_chain_diag_printed'):
            self._canvas._chain_diag_printed = False

    def _on_knob(self, idx: int, raw: int):
        self._params[idx] = raw / 1000.0
        self._canvas.set_param(idx, self._params[idx])

    def _on_fps(self, fps: float):
        self._fps_lbl.setText(f'{fps:.0f} fps')

    def _poll_audio(self):
        fft, rms, bass, mid, treble, beat = self._audio.get_data()
        self._canvas.set_audio_data(fft, rms, bass, mid, treble, beat)
        self._scope.update_data(fft.tolist(), rms)

    # ── MIDI learn ────────────────────────────────────────────────────────────

    def _on_learn_request(self, idx: int):
        self._learn_idx = idx
        self._knobs[idx]._learning = True
        self._knobs[idx].update()
        self._status_lbl.setText(f'MIDI LEARN: move a CC for knob {PARAM_NAMES[idx].upper()} …')

    def _on_midi_cc(self, cc: int, value: float):
        # value arrives as 0.0–1.0 from MidiEngine
        if self._learn_idx is not None:
            old_idx = self._cc_map.get(cc)
            if old_idx is not None and old_idx != self._learn_idx:
                self._knobs[old_idx].set_midi_cc(None)
            self._cc_map[cc] = self._learn_idx
            self._knobs[self._learn_idx].set_midi_cc(cc)
            self._learn_idx = None
            self._status_lbl.setText(f'Mapped CC{cc} → {PARAM_NAMES[self._cc_map[cc]].upper()}')
            return

        if cc in self._cc_map:
            idx  = self._cc_map[cc]
            self._knobs[idx].blockSignals(True)
            self._knobs[idx].setValue(int(value * 1000))
            self._knobs[idx].blockSignals(False)
            self._params[idx] = value
            self._canvas.set_param(idx, value)

    # ── Device population ─────────────────────────────────────────────────────

    def _populate_audio_combo(self):
        self._audio_combo.clear()
        for dev in self._audio.input_devices:
            self._audio_combo.addItem(dev['name'], dev['index'])

    def _populate_midi_combo(self):
        self._midi_combo.clear()
        self._midi_combo.addItem('— none —', None)
        for port in self._midi.get_port_names():
            self._midi_combo.addItem(port, port)

    def _on_audio_device(self, idx: int):
        if 0 <= idx < len(self._audio.input_devices):
            self._audio.stop()
            self._audio.start_input(idx)

    def _on_midi_device(self, _idx: int):
        port = self._midi_combo.currentData()
        if port:
            self._midi.open_port(port)

    # ── Screenshot ────────────────────────────────────────────────────────────

    def _screenshot(self):
        import cv2
        import numpy as np
        arr = self._canvas.grab_frame()
        if arr is None:
            return
        ts   = datetime.now().strftime('%Y%m%d_%H%M%S')
        path = _SCREENSHOTS / f'scan_{ts}.png'
        cv2.imwrite(str(path), cv2.cvtColor(arr, cv2.COLOR_RGB2BGR))
        self._status_lbl.setText(f'Saved {path.name}')
        QTimer.singleShot(3000, lambda: self._status_lbl.setText(
            'Ctrl+S = screenshot  ·  right-click knob = MIDI learn'))

    # ── Cleanup ───────────────────────────────────────────────────────────────

    def closeEvent(self, e):
        self._audio.stop()
        self._midi.close()
        e.accept()


# ─────────────────────────────────────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────────────────────────────────────

def main():
    # High-DPI support — must be called before QApplication
    try:
        QApplication.setHighDpiScaleFactorRoundingPolicy(
            Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    except AttributeError:
        pass

    app = QApplication(sys.argv)
    app.setApplicationName('Scan Processor')

    win = ScanProcessorWindow()
    win.show()
    sys.exit(app.exec())


if __name__ == '__main__':
    main()
