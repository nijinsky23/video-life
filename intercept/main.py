#!/usr/bin/env python3
"""
INTERCEPT — Signal Operator
────────────────────────────────────────────────────────
A video synthesizer for intercepting and corrupting live signal sources:
cameras, screens, RTSP streams from TVs and IP cameras, capture cards.

Up to 3 simultaneous sources feed into 4 GLSL glitch/surveillance engines.

Sources
  Camera  — any USB or built-in camera, capture card, Continuity Camera
  Screen  — full desktop or a specific monitor (for looping / screen-feeding)
  Stream  — any URL OpenCV can open: rtsp://  http://...mjpg  v4l2://

Engines
  Tap     — signal interception: VHS artifacts, RF noise, colour bleed
  Ghost   — surveillance palimpsest: multiple sources long-exposure layered
  Corrupt — digital corruption: block glitch, channel shift, bit errors
  Splice  — hard-cut signal hijacking: beat-driven switching with glitch flash

Controls
  p[0-2] Mix-A/B/C   — source weights
  p[3]   Corrupt      — glitch intensity
  p[4]   Noise        — RF/static injection
  p[5]   React        — audio reactivity
  p[6]   Palette      — natural / green / infrared / negative
  p[7]   Gain         — output brightness

  Ctrl+S  screenshot
  Ctrl+Q  quit
"""

import sys
import os
import math
import time
from pathlib import Path
from datetime import datetime

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
for p in (_HERE, _ROOT):
    if p not in sys.path:
        sys.path.insert(0, p)

from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QPushButton, QComboBox, QLineEdit, QFrame, QSizePolicy,
    QToolButton, QDial, QDialog, QDialogButtonBox, QScrollArea,
    QGroupBox, QGridLayout,
)
from PyQt6.QtCore  import Qt, QTimer, pyqtSignal, QRectF, QPointF
from PyQt6.QtGui   import (
    QColor, QPainter, QPen, QBrush, QRadialGradient,
    QKeySequence, QShortcut,
)

from canvas        import InterceptCanvas
from signal_router import SignalRouter
from shaders       import SHADERS, PARAM_NAMES, PARAM_DEFAULTS
from core.audio_engine import AudioEngine
from core.midi_engine  import MidiEngine

# ── Colour tokens ─────────────────────────────────────────────────────────────
BG       = '#0a0000'
PANEL    = '#130000'
PANEL2   = '#1e0000'
ACCENT   = '#FF2020'
TEXT     = '#d4aaaa'
TEXT_DIM = '#5c2a2a'
BORDER   = 'rgba(255,30,30,0.12)'
GREEN    = '#20FF60'       # source-active indicator

_MONO = "'Menlo','Monaco','SF Mono','Courier New',monospace"

_SCREENSHOTS = Path.home() / 'Pictures' / 'intercept'
_SCREENSHOTS.mkdir(parents=True, exist_ok=True)

_SOURCE_KINDS  = ['none', 'camera', 'screen', 'stream', 'noise']
_SOURCE_LABELS = ['—', 'Camera', 'Screen', 'Stream', 'Noise']


# ─────────────────────────────────────────────────────────────────────────────
# Knob (identical painting to Scan Processor)
# ─────────────────────────────────────────────────────────────────────────────

class Knob(QDial):
    learn_requested = pyqtSignal(int)

    _MIN_ANGLE, _SWEEP = 210, 300

    def __init__(self, index: int, name: str, value: float = 0.5, parent=None):
        super().__init__(parent)
        self.index = index
        self.setRange(0, 1000)
        self.setValue(int(value * 1000))
        self.setWrapping(False)
        self.setNotchesVisible(False)
        self.setFixedSize(50, 50)
        self._hovered  = False
        self._drag_y   = None
        self._drag_val = 0
        self._midi_cc  = None
        self.setToolTip(f'{name}\nRight-click → MIDI learn')
        self.valueChanged.connect(self.update)

    def enterEvent(self, e): self._hovered = True;  self.update()
    def leaveEvent(self, e): self._hovered = False; self.update()

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.RightButton:
            self.learn_requested.emit(self.index)
        elif e.button() == Qt.MouseButton.LeftButton:
            self._drag_y, self._drag_val = e.position().y(), self.value()
            e.accept()

    def mouseMoveEvent(self, e):
        if self._drag_y is None: return
        dy = self._drag_y - e.position().y()
        self.setValue(max(0, min(1000, self._drag_val + int(dy * 4))))
        e.accept()

    def mouseReleaseEvent(self, e):
        self._drag_y = None; e.accept()

    def get_value(self) -> float: return self.value() / 1000.0

    def paintEvent(self, _):
        p   = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h   = self.width(), self.height()
        cx, cy = w / 2.0, h / 2.0
        r      = min(w, h) / 2.0 - 2.5
        val    = self.value() / 1000.0

        gr = QRadialGradient(cx * 0.6, cy * 0.5, r * 1.2)
        gr.setColorAt(0, QColor('#221018')); gr.setColorAt(1, QColor('#090408'))
        p.setBrush(QBrush(gr))
        border = QColor(ACCENT) if (self._hovered or self._midi_cc) else QColor(80, 20, 20, 55)
        if self._hovered: border.setAlpha(90)
        p.setPen(QPen(border, 1.5))
        p.drawEllipse(QRectF(cx - r, cy - r, r * 2, r * 2))

        ar    = r * 0.76
        arect = QRectF(cx - ar, cy - ar, ar * 2, ar * 2)
        start = int((90 - self._MIN_ANGLE) * 16)
        dim   = QColor(ACCENT); dim.setAlpha(28)
        tp    = QPen(dim, 2.5); tp.setCapStyle(Qt.PenCapStyle.FlatCap)
        p.setPen(tp); p.drawArc(arect, start, int(-self._SWEEP * 16))
        if val > 0.001:
            vp = QPen(QColor(ACCENT), 2.5); vp.setCapStyle(Qt.PenCapStyle.FlatCap)
            p.setPen(vp); p.drawArc(arect, start, int(-val * self._SWEEP * 16))

        angle = math.radians(self._MIN_ANGLE + val * self._SWEEP)
        sa, ca = math.sin(angle), math.cos(angle)
        pp = QPen(QColor('#cccccc'), 1.8); pp.setCapStyle(Qt.PenCapStyle.RoundCap)
        p.setPen(pp)
        p.drawLine(QPointF(cx + r * 0.22 * sa, cy - r * 0.22 * ca),
                   QPointF(cx + r * 0.58 * sa, cy - r * 0.58 * ca))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(QColor(255, 255, 255, 45)))
        p.drawEllipse(QPointF(cx, cy), 2.2, 2.2)
        p.end()


# ─────────────────────────────────────────────────────────────────────────────
# Source slot widget  — KIND ↕ | ARG input | active indicator
# ─────────────────────────────────────────────────────────────────────────────

class SourceSlot(QFrame):
    """
    One source slot: a combo for kind, a text field for the arg
    (camera index / monitor index / URL), and a green active indicator.
    """
    source_changed = pyqtSignal(int, str, str)   # (slot, kind, arg)

    def __init__(self, slot: int, label: str, parent=None):
        super().__init__(parent)
        self._slot  = slot
        self._kind  = 'none'
        self._arg   = ''
        self.setFixedHeight(32)
        self.setStyleSheet(f'background:transparent;')

        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(5)

        # Slot label  A / B / C
        lbl = QLabel(label)
        lbl.setFixedWidth(16)
        lbl.setStyleSheet(f'color:{ACCENT}; font-family:{_MONO}; font-size:11px; font-weight:700;')
        lay.addWidget(lbl)

        # Kind selector
        self._kind_combo = QComboBox()
        self._kind_combo.setFixedWidth(80)
        for s in _SOURCE_LABELS:
            self._kind_combo.addItem(s)
        self._kind_combo.setStyleSheet(self._combo_style())
        self._kind_combo.currentIndexChanged.connect(self._on_kind)
        lay.addWidget(self._kind_combo)

        # Argument: camera index / monitor / URL
        self._arg_input = QLineEdit()
        self._arg_input.setPlaceholderText('index / rtsp://...')
        self._arg_input.setFixedWidth(200)
        self._arg_input.setStyleSheet(f"""
            QLineEdit {{
                background:{PANEL2}; color:{TEXT}; border:1px solid {BORDER};
                border-radius:3px; padding:1px 6px;
                font-family:{_MONO}; font-size:9px;
            }}
            QLineEdit:focus {{ border-color:{ACCENT}; }}
        """)
        self._arg_input.returnPressed.connect(self._on_apply)
        lay.addWidget(self._arg_input)

        # Apply button
        self._apply_btn = QPushButton('⏎')
        self._apply_btn.setFixedSize(24, 24)
        self._apply_btn.setToolTip('Connect source')
        self._apply_btn.setStyleSheet(f"""
            QPushButton {{
                background:{PANEL2}; color:{TEXT_DIM}; border:1px solid {BORDER};
                border-radius:3px; font-size:11px; padding:0;
            }}
            QPushButton:hover {{ border-color:{ACCENT}; color:{ACCENT}; }}
        """)
        self._apply_btn.clicked.connect(self._on_apply)
        lay.addWidget(self._apply_btn)

        # Active LED
        self._led = QLabel('●')
        self._led.setFixedWidth(14)
        self._led.setStyleSheet(f'color:{TEXT_DIM}; font-size:10px;')
        lay.addWidget(self._led)

        lay.addStretch()

    def set_active(self, active: bool):
        self._led.setStyleSheet(
            f'color:{"#20FF60" if active else TEXT_DIM}; font-size:10px;')

    def _on_kind(self, idx: int):
        self._kind = _SOURCE_KINDS[idx]
        # Auto-fill default arg for camera / screen
        if self._kind == 'camera' and not self._arg_input.text():
            self._arg_input.setText(str(self._slot))
        elif self._kind == 'screen' and not self._arg_input.text():
            self._arg_input.setText('1')
        elif self._kind == 'noise':
            self._arg_input.setText('')
            self._on_apply()
        elif self._kind == 'none':
            self._arg_input.setText('')
            self._on_apply()

    def _on_apply(self):
        self._arg = self._arg_input.text().strip()
        self.source_changed.emit(self._slot, self._kind, self._arg)

    @staticmethod
    def _combo_style():
        return f"""
            QComboBox {{
                background:{PANEL2}; color:{TEXT}; border:1px solid {BORDER};
                border-radius:3px; padding:1px 6px;
                font-family:{_MONO}; font-size:9px;
            }}
            QComboBox:hover {{ border-color:{ACCENT}; }}
            QComboBox QAbstractItemView {{
                background:{PANEL}; color:{TEXT}; border:1px solid {BORDER};
                selection-background-color:{PANEL2};
            }}
            QComboBox::drop-down {{ border:none; }}
        """


# ─────────────────────────────────────────────────────────────────────────────
# Mini FFT scope
# ─────────────────────────────────────────────────────────────────────────────

class MiniScope(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(90, 22)
        self._data = [0.0] * 32

    def update_data(self, fft, rms):
        n, stride = len(self._data), max(1, len(fft) // len(self._data))
        self._data = [max(fft[i * stride:(i + 1) * stride]) for i in range(n)]
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.fillRect(self.rect(), QColor(BG))
        w, h, n = self.width(), self.height(), len(self._data)
        bw = w / n
        for i, v in enumerate(self._data):
            bh = max(1.0, v * h)
            c = QColor(ACCENT); c.setAlpha(120 + int(v * 135))
            p.fillRect(QRectF(i * bw, h - bh, bw - 1, bh), c)
        p.end()


# ─────────────────────────────────────────────────────────────────────────────
# Main window
# ─────────────────────────────────────────────────────────────────────────────

class InterceptWindow(QMainWindow):

    def __init__(self):
        super().__init__()
        self.setWindowTitle('Video Life — Intercept')
        self.resize(1120, 760)
        self._apply_palette()

        # ── Core objects ──────────────────────────────────────────────────────
        self._canvas = InterceptCanvas()
        self._router = SignalRouter()
        self._audio  = AudioEngine()
        self._midi   = MidiEngine()
        self._midi.set_callback(cc_cb=self._on_midi_cc)

        # ── State ─────────────────────────────────────────────────────────────
        self._engine  = list(SHADERS.keys())[0]
        self._params  = list(PARAM_DEFAULTS[self._engine])
        self._canvas.set_params(self._params)
        self._learn_idx: int | None = None
        self._cc_map: dict[int, int] = {}

        # ── Build UI ──────────────────────────────────────────────────────────
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addWidget(self._build_header())
        root.addWidget(self._canvas, 1)
        root.addWidget(self._build_source_panel())
        root.addWidget(self._build_knob_strip())
        root.addWidget(self._build_status())

        # ── Timers ────────────────────────────────────────────────────────────
        self._audio_timer = QTimer(self)
        self._audio_timer.timeout.connect(self._poll_audio)
        self._audio_timer.start(33)

        self._source_timer = QTimer(self)
        self._source_timer.timeout.connect(self._push_source_frames)
        self._source_timer.start(33)   # ~30 fps source feed

        self._led_timer = QTimer(self)
        self._led_timer.timeout.connect(self._update_leds)
        self._led_timer.start(500)

        # ── Shortcuts ─────────────────────────────────────────────────────────
        QShortcut(QKeySequence('Ctrl+S'), self).activated.connect(self._screenshot)
        QShortcut(QKeySequence('Ctrl+Q'), self).activated.connect(self.close)

        # ── Signals ───────────────────────────────────────────────────────────
        self._canvas.fps_updated.connect(self._on_fps)

        # ── Scan cameras on startup ───────────────────────────────────────────
        cams = self._router.scan_cameras()
        self._update_camera_options(cams)

        # Auto-start audio
        if self._audio.input_devices:
            self._audio.start_input(0)

    # ── UI builders ───────────────────────────────────────────────────────────

    def _build_header(self) -> QWidget:
        bar = QWidget()
        bar.setFixedHeight(44)
        bar.setStyleSheet(f'background:{PANEL}; border-bottom:1px solid {BORDER};')
        lay = QHBoxLayout(bar)
        lay.setContentsMargins(18, 0, 18, 0)
        lay.setSpacing(14)

        title = QLabel('VIDEO LIFE — INTERCEPT')
        title.setStyleSheet(
            f'color:{ACCENT}; font-family:{_MONO}; font-size:13px; '
            f'font-weight:700; letter-spacing:4px;')
        lay.addWidget(title)

        sub = QLabel('SIGNAL OPERATOR')
        sub.setStyleSheet(
            f'color:{TEXT_DIM}; font-family:{_MONO}; font-size:9px; letter-spacing:3px;')
        lay.addWidget(sub)

        lay.addStretch()

        # Scan button
        scan_btn = QPushButton('SCAN CAMERAS')
        scan_btn.setStyleSheet(self._btn_style())
        scan_btn.clicked.connect(self._on_scan)
        lay.addWidget(scan_btn)

        # Engine selector
        eng_lbl = QLabel('ENGINE')
        eng_lbl.setStyleSheet(
            f'color:{TEXT_DIM}; font-family:{_MONO}; font-size:9px; letter-spacing:2px;')
        lay.addWidget(eng_lbl)

        self._engine_combo = QComboBox()
        for name in SHADERS:
            self._engine_combo.addItem(name)
        self._engine_combo.setFixedWidth(110)
        self._engine_combo.setStyleSheet(self._combo_style())
        self._engine_combo.currentTextChanged.connect(self._on_engine_change)
        lay.addWidget(self._engine_combo)

        return bar

    def _build_source_panel(self) -> QWidget:
        panel = QWidget()
        panel.setFixedHeight(46)
        panel.setStyleSheet(f'background:{PANEL2}; border-bottom:1px solid {BORDER};')
        lay = QHBoxLayout(panel)
        lay.setContentsMargins(18, 6, 18, 6)
        lay.setSpacing(20)

        self._source_slots: list[SourceSlot] = []
        for i, label in enumerate(['A', 'B', 'C']):
            slot = SourceSlot(i, label)
            slot.source_changed.connect(self._on_source_change)
            self._source_slots.append(slot)
            lay.addWidget(slot)

        lay.addStretch()

        # Audio device
        albl = QLabel('AUDIO IN')
        albl.setStyleSheet(f'color:{TEXT_DIM}; font-family:{_MONO}; font-size:8px; letter-spacing:1px;')
        lay.addWidget(albl)
        self._audio_combo = QComboBox()
        self._audio_combo.setFixedWidth(160)
        self._audio_combo.setStyleSheet(self._combo_style())
        for dev in self._audio.input_devices:
            self._audio_combo.addItem(dev['name'], dev['index'])
        self._audio_combo.currentIndexChanged.connect(self._on_audio_device)
        lay.addWidget(self._audio_combo)

        return panel

    def _build_knob_strip(self) -> QWidget:
        strip = QWidget()
        strip.setFixedHeight(86)
        strip.setStyleSheet(f'background:{PANEL}; border-top:1px solid {BORDER};')
        lay = QHBoxLayout(strip)
        lay.setContentsMargins(18, 8, 18, 8)
        lay.setSpacing(14)

        self._knobs: list[Knob] = []
        for i, (name, val) in enumerate(zip(PARAM_NAMES, self._params)):
            col = QVBoxLayout()
            col.setSpacing(2)
            col.setAlignment(Qt.AlignmentFlag.AlignHCenter)
            knob = Knob(i, name, val)
            knob.valueChanged.connect(lambda v, idx=i: self._on_knob(idx, v))
            knob.learn_requested.connect(self._on_learn_request)
            self._knobs.append(knob)
            col.addWidget(knob, alignment=Qt.AlignmentFlag.AlignHCenter)
            lbl = QLabel(name.upper())
            lbl.setAlignment(Qt.AlignmentFlag.AlignHCenter)
            lbl.setStyleSheet(f'color:{TEXT_DIM}; font-family:{_MONO}; font-size:8px; letter-spacing:1px;')
            col.addWidget(lbl)
            lay.addLayout(col)

        lay.addStretch()
        return strip

    def _build_status(self) -> QWidget:
        bar = QWidget()
        bar.setFixedHeight(24)
        bar.setStyleSheet(f'background:{PANEL2}; border-top:1px solid {BORDER};')
        lay = QHBoxLayout(bar)
        lay.setContentsMargins(12, 0, 12, 0)
        lay.setSpacing(14)

        self._scope = MiniScope()
        lay.addWidget(self._scope)

        self._fps_lbl = QLabel('-- fps')
        self._fps_lbl.setStyleSheet(f'color:{TEXT_DIM}; font-family:{_MONO}; font-size:9px;')
        lay.addWidget(self._fps_lbl)

        lay.addStretch()

        self._status_lbl = QLabel('Ctrl+S = screenshot  ·  right-click knob = MIDI learn  ·  add RTSP URL in Stream slots')
        self._status_lbl.setStyleSheet(f'color:{TEXT_DIM}; font-family:{_MONO}; font-size:9px;')
        lay.addWidget(self._status_lbl)
        return bar

    # ── Timer callbacks ───────────────────────────────────────────────────────

    def _push_source_frames(self):
        for i in range(3):
            frame = self._router.get_frame(i)
            self._canvas.set_source_frame(i, frame)

    def _poll_audio(self):
        fft, rms, bass, mid, treble, beat = self._audio.get_data()
        self._canvas.set_audio_data(fft, rms, bass, mid, treble, beat)
        self._scope.update_data(fft.tolist(), rms)

    def _update_leds(self):
        for i, slot in enumerate(self._source_slots):
            active = self._router.is_active(i) and self._router.get_frame(i) is not None
            slot.set_active(active)

    # ── Signal handlers ───────────────────────────────────────────────────────

    def _on_engine_change(self, name: str):
        self._engine = name
        self._canvas.set_mode(name)
        defaults = PARAM_DEFAULTS.get(name, [0.5] * 8)
        self._params = list(defaults)
        self._canvas.set_params(self._params)
        for i, knob in enumerate(self._knobs):
            knob.blockSignals(True)
            knob.setValue(int(self._params[i] * 1000))
            knob.blockSignals(False)

    def _on_knob(self, idx: int, raw: int):
        self._params[idx] = raw / 1000.0
        self._canvas.set_param(idx, self._params[idx])

    def _on_fps(self, fps: float):
        self._fps_lbl.setText(f'{fps:.0f} fps')

    def _on_source_change(self, slot: int, kind: str, arg: str):
        if kind == 'camera':
            try:
                idx = int(arg) if arg else slot
            except ValueError:
                idx = slot
            self._router.set_source(slot, 'camera', idx)
            self._status_lbl.setText(f'Slot {chr(65+slot)}: connecting camera {idx}…')
        elif kind == 'screen':
            try:
                mon = int(arg) if arg else 1
            except ValueError:
                mon = 1
            self._router.set_source(slot, 'screen', mon)
            self._status_lbl.setText(f'Slot {chr(65+slot)}: screen capture monitor {mon}')
        elif kind == 'stream':
            if arg:
                self._router.set_source(slot, 'stream', arg)
                self._status_lbl.setText(f'Slot {chr(65+slot)}: connecting {arg[:50]}…')
        elif kind == 'noise':
            self._router.set_source(slot, 'noise', None)
            self._status_lbl.setText(f'Slot {chr(65+slot)}: synthetic noise')
        elif kind == 'none':
            self._router.remove_source(slot)
            self._canvas.clear_source(slot)
            self._status_lbl.setText(f'Slot {chr(65+slot)}: disconnected')

    def _on_scan(self):
        self._status_lbl.setText('Scanning for cameras…')
        cams = self._router.scan_cameras()
        self._update_camera_options(cams)
        self._status_lbl.setText(f'Found cameras: {cams}' if cams else 'No cameras found')

    def _update_camera_options(self, cams: list[int]):
        # Update placeholder text for all camera-type slots
        if cams:
            hint = f'index 0-{max(cams)} available'
            for slot in self._source_slots:
                slot._arg_input.setPlaceholderText(hint)

    def _on_audio_device(self, idx: int):
        if 0 <= idx < len(self._audio.input_devices):
            self._audio.stop()
            self._audio.start_input(idx)

    # ── MIDI learn ────────────────────────────────────────────────────────────

    def _on_learn_request(self, idx: int):
        self._learn_idx = idx
        self._status_lbl.setText(f'MIDI LEARN: move a CC for {PARAM_NAMES[idx].upper()} …')

    def _on_midi_cc(self, cc: int, value: float):
        if self._learn_idx is not None:
            old = self._cc_map.get(cc)
            if old is not None:
                self._knobs[old]._midi_cc = None
            self._cc_map[cc] = self._learn_idx
            self._knobs[self._learn_idx]._midi_cc = cc
            self._learn_idx = None
            self._status_lbl.setText(f'Mapped CC{cc} → {PARAM_NAMES[self._cc_map[cc]]}')
            return
        if cc in self._cc_map:
            idx = self._cc_map[cc]
            self._knobs[idx].blockSignals(True)
            self._knobs[idx].setValue(int(value * 1000))
            self._knobs[idx].blockSignals(False)
            self._params[idx] = value
            self._canvas.set_param(idx, value)

    # ── Screenshot ────────────────────────────────────────────────────────────

    def _screenshot(self):
        import cv2
        arr = self._canvas.grab_frame()
        if arr is None: return
        ts   = datetime.now().strftime('%Y%m%d_%H%M%S')
        path = _SCREENSHOTS / f'intercept_{ts}.png'
        cv2.imwrite(str(path), cv2.cvtColor(arr, cv2.COLOR_RGB2BGR))
        self._status_lbl.setText(f'Saved {path.name}')
        QTimer.singleShot(3000, lambda: self._status_lbl.setText(
            'Ctrl+S = screenshot  ·  right-click knob = MIDI learn  ·  add RTSP URL in Stream slots'))

    # ── Cleanup ───────────────────────────────────────────────────────────────

    def closeEvent(self, e):
        self._audio_timer.stop()
        self._source_timer.stop()
        self._router.close()
        self._audio.stop()
        self._midi.close()
        e.accept()

    # ── Style helpers ─────────────────────────────────────────────────────────

    def _apply_palette(self):
        self.setStyleSheet(f"""
            QMainWindow, QWidget {{ background:{BG}; color:{TEXT}; }}
            QLabel {{ color:{TEXT}; font-family:{_MONO}; font-size:10px; }}
            QPushButton {{
                background:{PANEL2}; color:{TEXT}; border:1px solid {BORDER};
                border-radius:3px; padding:3px 10px;
                font-family:{_MONO}; font-size:9px; letter-spacing:1px;
            }}
            QPushButton:hover  {{ border-color:{ACCENT}; color:{ACCENT}; }}
        """)

    @staticmethod
    def _btn_style():
        return f"""
            QPushButton {{
                background:{PANEL2}; color:{TEXT_DIM}; border:1px solid {BORDER};
                border-radius:3px; padding:2px 10px;
                font-family:{_MONO}; font-size:9px; letter-spacing:1px;
            }}
            QPushButton:hover {{ border-color:{ACCENT}; color:{ACCENT}; }}
        """

    @staticmethod
    def _combo_style():
        return f"""
            QComboBox {{
                background:{PANEL2}; color:{TEXT}; border:1px solid {BORDER};
                border-radius:3px; padding:2px 7px;
                font-family:{_MONO}; font-size:9px;
            }}
            QComboBox:hover {{ border-color:{ACCENT}; }}
            QComboBox QAbstractItemView {{
                background:{PANEL}; color:{TEXT}; border:1px solid {BORDER};
                selection-background-color:{PANEL2};
            }}
            QComboBox::drop-down {{ border:none; }}
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
    app.setApplicationName('Intercept')

    win = InterceptWindow()
    win.show()
    sys.exit(app.exec())


if __name__ == '__main__':
    main()
