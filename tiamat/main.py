#!/usr/bin/env python3
"""
TIAMAT — Signal Operator
────────────────────────────────────────────────────────
A video synthesizer for intercepting and corrupting live signal sources:
cameras, screens, RTSP streams from TVs and IP cameras, capture cards.

Up to 3 simultaneous sources feed into 8 GLSL synthesis engines.

Sources
  Camera  — any USB or built-in camera, capture card, Continuity Camera
  Screen  — full desktop or a specific monitor
  Stream  — RTSP / HTTP-MJPEG / HLS / local file
  Noise   — animated RF static (test / no-signal aesthetic)

Engines
  Tap     — VHS artifacts, RF noise, colour bleed
  Ghost   — surveillance palimpsest: long-exposure multi-source layer
  Corrupt — digital corruption: block glitch, channel shift, bit errors
  Splice  — beat-driven hard-cut signal switching with glitch flash
  Kaleid  — n-fold kaleidoscope mirror, beat-reactive symmetry
  Thermal — black-body heat vision with heat-haze shimmer
  Tunnel  — log-polar infinite zoom tunnel, chromatic aberration rings
  Melt    — organic trig flow-field dissolution, multi-source lava blend

Controls
  p[0-2] Mix-A/B/C   — source weights
  p[3]   Effect       — engine-specific intensity
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
    QToolButton, QDial, QDialog, QScrollArea, QGroupBox, QGridLayout,
)
from PyQt6.QtCore  import Qt, QTimer, pyqtSignal, QRectF, QPointF
from PyQt6.QtGui   import (
    QColor, QPainter, QPen, QBrush, QRadialGradient,
    QKeySequence, QShortcut,
)

from canvas           import TiamatCanvas
from signal_router    import SignalRouter
from tiamat_shaders import SHADERS, PARAM_NAMES, PARAM_DEFAULTS
from network_scanner  import NetworkScanner, DiscoveredDevice, _rtsp_patterns
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
GREEN    = '#20FF60'

_MONO = "'Menlo','Monaco','SF Mono','Courier New',monospace"

_SCREENSHOTS = Path.home() / 'Pictures' / 'tiamat'
_SCREENSHOTS.mkdir(parents=True, exist_ok=True)

_SOURCE_KINDS  = ['none', 'camera', 'screen', 'stream', 'noise']
_SOURCE_LABELS = ['—', 'Camera', 'Screen', 'Stream', 'Noise']


# ─────────────────────────────────────────────────────────────────────────────
# Knob
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
# Source slot widget
# ─────────────────────────────────────────────────────────────────────────────

class SourceSlot(QFrame):
    """One source slot: kind combo → arg field → auto-connects."""

    source_changed = pyqtSignal(int, str, str)   # (slot, kind, arg)

    def __init__(self, slot: int, label: str, parent=None):
        super().__init__(parent)
        self._slot  = slot
        self._kind  = 'none'
        self._arg   = ''
        self.setFixedHeight(32)
        self.setStyleSheet('background:transparent;')

        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(5)

        lbl = QLabel(label)
        lbl.setFixedWidth(16)
        lbl.setStyleSheet(f'color:{ACCENT}; font-family:{_MONO}; font-size:11px; font-weight:700;')
        lay.addWidget(lbl)

        self._kind_combo = QComboBox()
        self._kind_combo.setFixedWidth(80)
        for s in _SOURCE_LABELS:
            self._kind_combo.addItem(s)
        self._kind_combo.setStyleSheet(self._combo_style())
        self._kind_combo.currentIndexChanged.connect(self._on_kind)
        lay.addWidget(self._kind_combo)

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

        self._led = QLabel('●')
        self._led.setFixedWidth(14)
        self._led.setStyleSheet(f'color:{TEXT_DIM}; font-size:10px;')
        lay.addWidget(self._led)

        lay.addStretch()

    def set_active(self, active: bool):
        self._led.setStyleSheet(
            f'color:{"#20FF60" if active else TEXT_DIM}; font-size:10px;')

    def connect_device(self, kind: str, arg: str):
        """Programmatically connect a device (called from DeviceBrowserDialog)."""
        if kind not in _SOURCE_KINDS:
            return
        self._kind_combo.blockSignals(True)
        self._kind_combo.setCurrentIndex(_SOURCE_KINDS.index(kind))
        self._kind_combo.blockSignals(False)
        self._kind = kind
        self._arg_input.setText(str(arg))
        self._on_apply()

    def _on_kind(self, idx: int):
        self._kind = _SOURCE_KINDS[idx]
        if self._kind == 'camera':
            if not self._arg_input.text():
                self._arg_input.setText(str(self._slot))
            # Auto-connect immediately — camera index is always set
            self._on_apply()
        elif self._kind == 'screen':
            if not self._arg_input.text():
                self._arg_input.setText('1')
            self._on_apply()
        elif self._kind == 'noise':
            self._arg_input.setText('')
            self._on_apply()
        elif self._kind == 'none':
            self._arg_input.setText('')
            self._on_apply()
        # 'stream': don't auto-apply — user must enter URL first

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
# Device row (inside browser dialog)
# ─────────────────────────────────────────────────────────────────────────────

class _DeviceRow(QFrame):
    """One device entry in the browser: name · address · [editable URL] · [→A][→B][→C]"""

    def __init__(self, name: str, address: str, url: str,
                 kind: str, slots: 'list[SourceSlot]', parent=None):
        super().__init__(parent)
        self._kind  = kind
        self._slots = slots
        self.setFixedHeight(40)
        self.setStyleSheet(f"""
            QFrame          {{ background:transparent; border-bottom:1px solid {BORDER}; }}
            QFrame:hover    {{ background:{PANEL2}; }}
        """)

        lay = QHBoxLayout(self)
        lay.setContentsMargins(18, 0, 14, 0)
        lay.setSpacing(8)

        # Dot — colour by device kind
        dot = QLabel('●')
        if kind == 'local_camera':
            dot_colour = GREEN
        elif kind == 'onvif':
            dot_colour = ACCENT
        elif kind in ('ssdp',):
            dot_colour = '#20CCFF'   # cyan = TV / media device
        elif kind == 'mjpeg':
            dot_colour = '#FFB020'   # amber = HTTP camera
        else:
            dot_colour = TEXT_DIM
        dot.setStyleSheet(f'color:{dot_colour}; font-size:9px;')
        dot.setFixedWidth(12)
        lay.addWidget(dot)

        # Name + address
        info = QLabel(f'{name}  <span style="color:{TEXT_DIM}; font-size:8px;">{address}</span>')
        info.setTextFormat(Qt.TextFormat.RichText)
        info.setFixedWidth(210)
        info.setStyleSheet(f'color:{TEXT}; font-size:9px; font-family:{_MONO};')
        lay.addWidget(info)

        # Editable URL
        self._url_edit = QLineEdit(url)
        self._url_edit.setStyleSheet(f"""
            QLineEdit {{
                background:{PANEL}; color:{TEXT}; border:1px solid {BORDER};
                border-radius:2px; padding:1px 6px; font-family:{_MONO}; font-size:9px;
            }}
            QLineEdit:focus {{ border-color:{ACCENT}; }}
        """)
        lay.addWidget(self._url_edit, 1)

        # Slot connect buttons
        for i, lbl in enumerate(['→ A', '→ B', '→ C']):
            btn = QPushButton(lbl)
            btn.setFixedSize(40, 24)
            btn.setStyleSheet(f"""
                QPushButton {{
                    background:{PANEL}; color:{TEXT_DIM}; border:1px solid {BORDER};
                    border-radius:2px; font-family:{_MONO}; font-size:9px; padding:0;
                }}
                QPushButton:hover {{ border-color:{ACCENT}; color:{ACCENT}; background:{PANEL2}; }}
            """)
            btn.clicked.connect(lambda _, s=i: self._connect(s))
            lay.addWidget(btn)

    def _connect(self, slot_idx: int):
        url = self._url_edit.text().strip()
        if not url:
            return
        slot = self._slots[slot_idx]
        if self._kind == 'local_camera':
            slot.connect_device('camera', url)
        elif self._kind in ('onvif', 'rtsp', 'ssdp', 'mjpeg'):
            slot.connect_device('stream', url)
        else:
            slot.connect_device('stream', url)


# ─────────────────────────────────────────────────────────────────────────────
# Device Browser dialog
# ─────────────────────────────────────────────────────────────────────────────

def _camera_names(indices: list[int]) -> dict[int, str]:
    """Best-effort camera names via QMediaDevices; fallback to generic."""
    names = {i: f'Camera {i}' for i in indices}
    try:
        from PyQt6.QtMultimedia import QMediaDevices
        for i, dev in enumerate(QMediaDevices.videoInputs()):
            if i in names:
                desc = dev.description()
                if desc:
                    names[i] = desc
    except Exception:
        pass
    return names


class DeviceBrowserDialog(QDialog):
    """
    Non-modal device browser.
      · Local cameras listed immediately on open.
      · SCAN NETWORK discovers ONVIF + RTSP cameras on the LAN.
      · Click [→ A / B / C] to connect any device to a source slot.
    """

    _device_sig   = pyqtSignal(object)   # DiscoveredDevice  (thread → main)
    _scan_done_sig = pyqtSignal()         # scan finished

    def __init__(self, router: SignalRouter,
                 source_slots: list[SourceSlot],
                 local_cameras: list[int],
                 parent=None):
        super().__init__(parent)
        self.setWindowTitle('Video Life — Devices')
        self.setMinimumSize(700, 480)
        self.setModal(False)

        self._router       = router
        self._slots        = source_slots
        self._scanner      = NetworkScanner()
        self._scan_running = False
        self._net_rows: list[_DeviceRow] = []

        self._device_sig.connect(self._on_device_found)
        self._scan_done_sig.connect(self._on_scan_done)

        self._apply_style()
        self._build_ui(local_cameras)

    # ── Style ─────────────────────────────────────────────────────────────────

    def _apply_style(self):
        self.setStyleSheet(f"""
            QDialog, QWidget  {{ background:{BG}; color:{TEXT}; }}
            QLabel            {{ color:{TEXT}; font-family:{_MONO}; font-size:10px; }}
            QPushButton {{
                background:{PANEL2}; color:{TEXT_DIM}; border:1px solid {BORDER};
                border-radius:3px; padding:2px 10px;
                font-family:{_MONO}; font-size:9px; letter-spacing:1px;
            }}
            QPushButton:hover {{ border-color:{ACCENT}; color:{ACCENT}; }}
            QScrollArea, QScrollBar {{ border:none; background:{BG}; }}
            QScrollBar:vertical   {{ width:6px; }}
            QScrollBar::handle:vertical {{ background:{PANEL2}; border-radius:3px; }}
        """)

    # ── UI ────────────────────────────────────────────────────────────────────

    def _build_ui(self, local_cameras: list[int]):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # Header
        hdr = QWidget()
        hdr.setFixedHeight(42)
        hdr.setStyleSheet(f'background:{PANEL}; border-bottom:1px solid {BORDER};')
        hl = QHBoxLayout(hdr)
        hl.setContentsMargins(18, 0, 18, 0)
        hl.setSpacing(12)
        title = QLabel('DEVICE BROWSER')
        title.setStyleSheet(
            f'color:{ACCENT}; font-size:11px; font-weight:700; letter-spacing:3px;')
        hl.addWidget(title)
        hl.addStretch()
        self._scan_lbl = QLabel('')
        self._scan_lbl.setStyleSheet(f'color:{TEXT_DIM}; font-size:9px;')
        hl.addWidget(self._scan_lbl)
        scan_btn = QPushButton('SCAN NETWORK')
        scan_btn.clicked.connect(self._start_scan)
        hl.addWidget(scan_btn)
        root.addWidget(hdr)

        # Scrollable list
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        inner  = QWidget()
        self._list = QVBoxLayout(inner)
        self._list.setContentsMargins(0, 0, 0, 0)
        self._list.setSpacing(0)
        scroll.setWidget(inner)
        root.addWidget(scroll, 1)

        # Local cameras
        self._list.addWidget(self._section_header('LOCAL CAMERAS'))
        cam_names = _camera_names(local_cameras)
        if local_cameras:
            for idx in local_cameras:
                name = cam_names.get(idx, f'Camera {idx}')
                row  = _DeviceRow(name=name, address=f'index {idx}',
                                  url=str(idx), kind='local_camera',
                                  slots=self._slots)
                self._list.addWidget(row)
        else:
            self._list.addWidget(self._dim_label('  No local cameras detected — run SCAN CAMERAS'))

        # Network devices
        self._list.addWidget(self._section_header('NETWORK DEVICES'))
        self._net_empty = self._dim_label(
            '  Click SCAN NETWORK to discover cameras, TVs and media devices on the LAN')
        self._list.addWidget(self._net_empty)

        self._list.addStretch(1)

    def _section_header(self, text: str) -> QWidget:
        w = QWidget()
        w.setFixedHeight(28)
        w.setStyleSheet(f'background:{PANEL2}; border-top:1px solid {BORDER};')
        l = QHBoxLayout(w)
        l.setContentsMargins(18, 0, 18, 0)
        lbl = QLabel(text)
        lbl.setStyleSheet(f'color:{TEXT_DIM}; font-size:9px; letter-spacing:2px;')
        l.addWidget(lbl)
        l.addStretch()
        return w

    @staticmethod
    def _dim_label(text: str) -> QLabel:
        lbl = QLabel(text)
        lbl.setStyleSheet(f'color:{TEXT_DIM}; padding:10px 18px; font-size:9px;')
        return lbl

    # ── Scan ──────────────────────────────────────────────────────────────────

    def _start_scan(self):
        if self._scan_running:
            return
        self._scan_running = True
        self._scan_lbl.setText('scanning…')

        # Clear previous network results
        for row in list(self._net_rows):
            self._list.removeWidget(row)
            row.deleteLater()
        self._net_rows.clear()
        self._net_empty.hide()

        self._scanner.scan(
            found_cb=lambda d: self._device_sig.emit(d),
            done_cb =self._scan_done_sig.emit,
        )

    def _on_device_found(self, dev: DiscoveredDevice):
        if dev.kind == 'local_camera':
            return
        row = _DeviceRow(name=dev.name, address=dev.address,
                         url=dev.url, kind=dev.kind,
                         slots=self._slots)
        # Insert before the trailing stretch
        self._list.insertWidget(self._list.count() - 1, row)
        self._net_rows.append(row)

    def _on_scan_done(self):
        self._scan_running = False
        n = len(self._net_rows)
        self._scan_lbl.setText(f'{n} device{"s" if n != 1 else ""} found')
        if n == 0:
            self._net_empty.setText(
                '  No devices found — check LAN connection / firewall / port 554')
            self._net_empty.show()

    def closeEvent(self, e):
        self._scanner.stop()
        super().closeEvent(e)


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

class TiamatWindow(QMainWindow):

    def __init__(self):
        super().__init__()
        self.setWindowTitle('Video Life — Tiamat')
        self.resize(1120, 760)
        self._apply_palette()

        # ── Core objects ──────────────────────────────────────────────────────
        self._canvas = TiamatCanvas()
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
        self._device_browser: DeviceBrowserDialog | None = None

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
        self._source_timer.start(33)

        self._led_timer = QTimer(self)
        self._led_timer.timeout.connect(self._update_leds)
        self._led_timer.start(500)

        # ── Shortcuts ─────────────────────────────────────────────────────────
        QShortcut(QKeySequence('Ctrl+S'), self).activated.connect(self._screenshot)
        QShortcut(QKeySequence('Ctrl+Q'), self).activated.connect(self.close)

        # ── Signals ───────────────────────────────────────────────────────────
        self._canvas.fps_updated.connect(self._on_fps)

        # ── Startup camera scan ───────────────────────────────────────────────
        cams = self._router.scan_cameras()
        self._update_camera_options(cams)

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

        title = QLabel('VIDEO LIFE — TIAMAT')
        title.setStyleSheet(
            f'color:{ACCENT}; font-family:{_MONO}; font-size:13px; '
            f'font-weight:700; letter-spacing:4px;')
        lay.addWidget(title)

        sub = QLabel('SIGNAL OPERATOR')
        sub.setStyleSheet(
            f'color:{TEXT_DIM}; font-family:{_MONO}; font-size:9px; letter-spacing:3px;')
        lay.addWidget(sub)

        lay.addStretch()

        # ── Buttons ──────────────────────────────────────────────────────────
        devices_btn = QPushButton('DEVICES')
        devices_btn.setToolTip('Browse and connect cameras / network streams')
        devices_btn.setStyleSheet(self._btn_style(highlight=True))
        devices_btn.clicked.connect(self._on_devices)
        lay.addWidget(devices_btn)

        scan_btn = QPushButton('SCAN CAMERAS')
        scan_btn.setStyleSheet(self._btn_style())
        scan_btn.clicked.connect(self._on_scan)
        lay.addWidget(scan_btn)

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

        albl = QLabel('AUDIO IN')
        albl.setStyleSheet(
            f'color:{TEXT_DIM}; font-family:{_MONO}; font-size:8px; letter-spacing:1px;')
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
            lbl.setStyleSheet(
                f'color:{TEXT_DIM}; font-family:{_MONO}; font-size:8px; letter-spacing:1px;')
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

        self._status_lbl = QLabel(
            'DEVICES → discover cameras on LAN  ·  Ctrl+S screenshot  ·  right-click knob = MIDI learn')
        self._status_lbl.setStyleSheet(
            f'color:{TEXT_DIM}; font-family:{_MONO}; font-size:9px;')
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
            # Surface capture errors in the status bar
            err = self._router.slot_error(i)
            if err and not active:
                self._status_lbl.setText(f'Slot {chr(65 + i)}: {err}')

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
        label = chr(65 + slot)
        if kind == 'camera':
            try:
                idx = int(arg) if arg else slot
            except ValueError:
                idx = slot
            self._router.set_source(slot, 'camera', idx)
            self._status_lbl.setText(f'Slot {label}: connecting camera {idx}…')
        elif kind == 'screen':
            try:
                mon = int(arg) if arg else 1
            except ValueError:
                mon = 1
            self._router.set_source(slot, 'screen', mon)
            self._status_lbl.setText(f'Slot {label}: screen capture monitor {mon}')
        elif kind == 'stream':
            if arg:
                self._router.set_source(slot, 'stream', arg)
                self._status_lbl.setText(f'Slot {label}: connecting {arg[:50]}…')
            else:
                self._status_lbl.setText(
                    f'Slot {label}: enter rtsp:// or http:// URL then press ⏎  '
                    f'(or use DEVICES browser)')
        elif kind == 'noise':
            self._router.set_source(slot, 'noise', None)
            self._status_lbl.setText(f'Slot {label}: synthetic noise')
        elif kind == 'none':
            self._router.remove_source(slot)
            self._canvas.clear_source(slot)
            self._status_lbl.setText(f'Slot {label}: disconnected')

    def _on_scan(self):
        self._status_lbl.setText('Scanning for cameras…')
        cams = self._router.scan_cameras()
        self._update_camera_options(cams)
        self._status_lbl.setText(f'Found cameras: {cams}' if cams else 'No cameras found')
        # Refresh browser if open
        if self._device_browser and self._device_browser.isVisible():
            self._device_browser.close()
            self._device_browser = None
            self._on_devices()

    def _update_camera_options(self, cams: list[int]):
        if cams:
            hint = f'0 – {max(cams)}'
            for slot in self._source_slots:
                slot._arg_input.setPlaceholderText(hint)

    def _on_devices(self):
        """Open (or raise) the Device Browser dialog."""
        if self._device_browser is None or not self._device_browser.isVisible():
            self._device_browser = DeviceBrowserDialog(
                router=self._router,
                source_slots=self._source_slots,
                local_cameras=self._router.available_cameras(),
                parent=self,
            )
        self._device_browser.show()
        self._device_browser.raise_()
        self._device_browser.activateWindow()

    def _on_audio_device(self, idx: int):
        if 0 <= idx < len(self._audio.input_devices):
            self._audio.stop()
            self._audio.start_input(idx)

    # ── MIDI learn ────────────────────────────────────────────────────────────

    def _on_learn_request(self, idx: int):
        self._learn_idx = idx
        self._status_lbl.setText(
            f'MIDI LEARN: move a CC for {PARAM_NAMES[idx].upper()} …')

    def _on_midi_cc(self, cc: int, value: float):
        if self._learn_idx is not None:
            old = self._cc_map.get(cc)
            if old is not None:
                self._knobs[old]._midi_cc = None
                self._knobs[old].update()
            target = self._learn_idx
            self._cc_map[cc] = target
            self._knobs[target]._midi_cc = cc
            self._knobs[target].update()
            self._learn_idx = None
            self._status_lbl.setText(f'Mapped CC{cc} → {PARAM_NAMES[target]}')
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
        if arr is None:
            return
        ts   = datetime.now().strftime('%Y%m%d_%H%M%S')
        path = _SCREENSHOTS / f'tiamat_{ts}.png'
        cv2.imwrite(str(path), cv2.cvtColor(arr, cv2.COLOR_RGB2BGR))
        self._status_lbl.setText(f'Saved {path.name}')
        QTimer.singleShot(3000, lambda: self._status_lbl.setText(
            'DEVICES → discover cameras on LAN  ·  Ctrl+S screenshot  ·  right-click knob = MIDI learn'))

    # ── Cleanup ───────────────────────────────────────────────────────────────

    def closeEvent(self, e):
        if self._device_browser:
            self._device_browser.close()
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
    def _btn_style(highlight: bool = False) -> str:
        border = ACCENT if highlight else BORDER
        color  = ACCENT if highlight else TEXT_DIM
        return f"""
            QPushButton {{
                background:{PANEL2}; color:{color}; border:1px solid {border};
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
    app.setApplicationName('Tiamat')

    win = TiamatWindow()
    win.show()
    sys.exit(app.exec())


if __name__ == '__main__':
    main()
