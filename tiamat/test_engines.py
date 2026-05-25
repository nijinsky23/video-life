#!/usr/bin/env python3
"""
TIAMAT — Engine Test Bench
──────────────────────────────
Single FaceTime camera, one engine at a time.
← / → arrow keys (or dropdown) to cycle engines.
Knobs load per-engine defaults on switch.
"""

import sys, os, math
_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
for p in (_HERE, _ROOT):
    if p not in sys.path:
        sys.path.insert(0, p)

from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget,
    QVBoxLayout, QHBoxLayout, QLabel, QComboBox, QPushButton, QDial,
)
from PyQt6.QtCore  import Qt, QTimer, pyqtSignal, QRectF, QPointF
from PyQt6.QtGui   import QColor, QPainter, QPen, QBrush, QRadialGradient, QKeySequence, QShortcut

from canvas            import TiamatCanvas
from signal_router     import SignalRouter
from tiamat_shaders import SHADERS, PARAM_NAMES, PARAM_DEFAULTS
from core.audio_engine import AudioEngine

# ── Palette ───────────────────────────────────────────────────────────────────
BG       = '#0a0000'
PANEL    = '#130000'
PANEL2   = '#1e0000'
ACCENT   = '#FF2020'
TEXT     = '#d4aaaa'
TEXT_DIM = '#5c2a2a'
BORDER   = 'rgba(255,30,30,0.12)'
_MONO    = "'Menlo','Monaco','SF Mono','Courier New',monospace"


# ── Knob ──────────────────────────────────────────────────────────────────────

class Knob(QDial):
    _MIN_ANGLE, _SWEEP = 210, 300

    def __init__(self, name: str, value: float = 0.5, parent=None):
        super().__init__(parent)
        self._name    = name
        self._hovered = False
        self._drag_y  = None
        self._drag_v  = 0
        self.setRange(0, 1000)
        self.setValue(int(value * 1000))
        self.setWrapping(False)
        self.setNotchesVisible(False)
        self.setFixedSize(52, 52)
        self.setToolTip(name)
        self.valueChanged.connect(self.update)

    def get_value(self) -> float:
        return self.value() / 1000.0

    def set_value(self, v: float):
        self.blockSignals(True)
        self.setValue(int(v * 1000))
        self.blockSignals(False)
        self.update()

    def enterEvent(self, e): self._hovered = True;  self.update()
    def leaveEvent(self, e): self._hovered = False; self.update()

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self._drag_y, self._drag_v = e.position().y(), self.value()
            e.accept()

    def mouseMoveEvent(self, e):
        if self._drag_y is None: return
        dy = self._drag_y - e.position().y()
        self.setValue(max(0, min(1000, self._drag_v + int(dy * 4))))
        e.accept()

    def mouseReleaseEvent(self, e):
        self._drag_y = None; e.accept()

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
        border = QColor(ACCENT) if self._hovered else QColor(80, 20, 20, 55)
        if self._hovered: border.setAlpha(90)
        p.setPen(QPen(border, 1.5))
        p.drawEllipse(QRectF(cx - r, cy - r, r * 2, r * 2))

        ar    = r * 0.76
        arect = QRectF(cx - ar, cy - ar, ar * 2, ar * 2)
        start = int((90 - self._MIN_ANGLE) * 16)
        dim   = QColor(ACCENT); dim.setAlpha(28)
        p.setPen(QPen(dim, 2.5, cap=Qt.PenCapStyle.FlatCap)); p.drawArc(arect, start, int(-self._SWEEP * 16))
        if val > 0.001:
            p.setPen(QPen(QColor(ACCENT), 2.5, cap=Qt.PenCapStyle.FlatCap))
            p.drawArc(arect, start, int(-val * self._SWEEP * 16))

        angle = math.radians(self._MIN_ANGLE + val * self._SWEEP)
        sa, ca = math.sin(angle), math.cos(angle)
        p.setPen(QPen(QColor('#cccccc'), 1.8, cap=Qt.PenCapStyle.RoundCap))
        p.drawLine(QPointF(cx + r * 0.22 * sa, cy - r * 0.22 * ca),
                   QPointF(cx + r * 0.58 * sa, cy - r * 0.58 * ca))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(QColor(255, 255, 255, 45)))
        p.drawEllipse(QPointF(cx, cy), 2.2, 2.2)
        p.end()


# ── Main window ───────────────────────────────────────────────────────────────

class TestBench(QMainWindow):

    def __init__(self):
        super().__init__()
        self.setWindowTitle('Tiamat — Engine Test')
        self.resize(1100, 700)
        self.setStyleSheet(f'QMainWindow, QWidget {{ background:{BG}; color:{TEXT}; }}')

        self._canvas = TiamatCanvas()
        self._router = SignalRouter()
        self._audio  = AudioEngine()

        self._engines = list(SHADERS.keys())
        self._engine  = self._engines[0]
        self._params  = list(PARAM_DEFAULTS[self._engine])
        self._canvas.set_params(self._params)

        # ── Build layout ──────────────────────────────────────────────────────
        root = QWidget(); self.setCentralWidget(root)
        vlay = QVBoxLayout(root)
        vlay.setContentsMargins(0, 0, 0, 0)
        vlay.setSpacing(0)

        vlay.addWidget(self._build_topbar())
        vlay.addWidget(self._canvas, 1)
        vlay.addWidget(self._build_knob_strip())
        vlay.addWidget(self._build_statusbar())

        # ── Shortcuts ─────────────────────────────────────────────────────────
        QShortcut(QKeySequence(Qt.Key.Key_Left),  self).activated.connect(self._prev_engine)
        QShortcut(QKeySequence(Qt.Key.Key_Right), self).activated.connect(self._next_engine)

        # ── Timers ────────────────────────────────────────────────────────────
        self._src_timer = QTimer(self)
        self._src_timer.timeout.connect(self._push_frame)
        self._src_timer.start(33)

        self._audio_timer = QTimer(self)
        self._audio_timer.timeout.connect(self._poll_audio)
        self._audio_timer.start(33)

        # ── Start camera 0 ────────────────────────────────────────────────────
        cams = self._router.scan_cameras()
        if cams:
            self._router.set_source(0, 'camera', 0)
            self._cam_lbl.setText(f'Camera {cams[0]}  ●')
            self._cam_lbl.setStyleSheet(
                f'color:#20FF60; font-family:{_MONO}; font-size:9px;')
        else:
            self._cam_lbl.setText('no camera')

        if self._audio.input_devices:
            self._audio.start_input(0)

    # ── UI builders ───────────────────────────────────────────────────────────

    def _build_topbar(self) -> QWidget:
        bar = QWidget()
        bar.setFixedHeight(44)
        bar.setStyleSheet(f'background:{PANEL}; border-bottom:1px solid {BORDER};')
        lay = QHBoxLayout(bar)
        lay.setContentsMargins(18, 0, 18, 0)
        lay.setSpacing(14)

        title = QLabel('ENGINE TEST')
        title.setStyleSheet(
            f'color:{ACCENT}; font-family:{_MONO}; font-size:12px; '
            f'font-weight:700; letter-spacing:4px;')
        lay.addWidget(title)

        lay.addStretch()

        hint = QLabel('← →')
        hint.setStyleSheet(
            f'color:{TEXT_DIM}; font-family:{_MONO}; font-size:9px;')
        lay.addWidget(hint)

        self._prev_btn = QPushButton('◄')
        self._next_btn = QPushButton('►')
        for btn in (self._prev_btn, self._next_btn):
            btn.setFixedSize(28, 28)
            btn.setStyleSheet(self._btn_style())
        self._prev_btn.clicked.connect(self._prev_engine)
        self._next_btn.clicked.connect(self._next_engine)
        lay.addWidget(self._prev_btn)

        self._combo = QComboBox()
        for name in self._engines:
            self._combo.addItem(name)
        self._combo.setFixedWidth(120)
        self._combo.setStyleSheet(f"""
            QComboBox {{
                background:{PANEL2}; color:{TEXT}; border:1px solid {ACCENT};
                border-radius:3px; padding:2px 8px;
                font-family:{_MONO}; font-size:11px; font-weight:700;
            }}
            QComboBox QAbstractItemView {{
                background:{PANEL}; color:{TEXT}; border:1px solid {BORDER};
                selection-background-color:{PANEL2};
            }}
            QComboBox::drop-down {{ border:none; }}
        """)
        self._combo.currentTextChanged.connect(self._on_engine)
        lay.addWidget(self._combo)
        lay.addWidget(self._next_btn)

        lay.addSpacing(20)
        self._cam_lbl = QLabel('scanning…')
        self._cam_lbl.setStyleSheet(
            f'color:{TEXT_DIM}; font-family:{_MONO}; font-size:9px;')
        lay.addWidget(self._cam_lbl)

        return bar

    def _build_knob_strip(self) -> QWidget:
        strip = QWidget()
        strip.setFixedHeight(90)
        strip.setStyleSheet(f'background:{PANEL}; border-top:1px solid {BORDER};')
        lay = QHBoxLayout(strip)
        lay.setContentsMargins(24, 8, 24, 8)
        lay.setSpacing(0)

        self._knobs: list[Knob] = []
        for i, (name, val) in enumerate(zip(PARAM_NAMES, self._params)):
            col = QVBoxLayout()
            col.setSpacing(3)
            col.setAlignment(Qt.AlignmentFlag.AlignHCenter)

            knob = Knob(name, val)
            knob.valueChanged.connect(lambda v, idx=i: self._on_knob(idx, v))
            self._knobs.append(knob)
            col.addWidget(knob, alignment=Qt.AlignmentFlag.AlignHCenter)

            lbl = QLabel(name.upper())
            lbl.setAlignment(Qt.AlignmentFlag.AlignHCenter)
            lbl.setStyleSheet(
                f'color:{TEXT_DIM}; font-family:{_MONO}; font-size:7px; letter-spacing:1px;')
            col.addWidget(lbl)

            lay.addLayout(col)
            if i < len(PARAM_NAMES) - 1:
                lay.addStretch()

        return strip

    def _build_statusbar(self) -> QWidget:
        bar = QWidget()
        bar.setFixedHeight(22)
        bar.setStyleSheet(f'background:{PANEL2}; border-top:1px solid {BORDER};')
        lay = QHBoxLayout(bar)
        lay.setContentsMargins(14, 0, 14, 0)
        lay.setSpacing(14)

        self._fps_lbl = QLabel('-- fps')
        self._fps_lbl.setStyleSheet(
            f'color:{TEXT_DIM}; font-family:{_MONO}; font-size:9px;')
        lay.addWidget(self._fps_lbl)
        lay.addStretch()

        self._info_lbl = QLabel('← → cycle engines')
        self._info_lbl.setStyleSheet(
            f'color:{TEXT_DIM}; font-family:{_MONO}; font-size:9px;')
        lay.addWidget(self._info_lbl)

        self._canvas.fps_updated.connect(
            lambda fps: self._fps_lbl.setText(f'{fps:.0f} fps'))

        return bar

    # ── Engine switching ──────────────────────────────────────────────────────

    def _on_engine(self, name: str):
        self._engine = name
        self._canvas.set_mode(name)
        defaults = PARAM_DEFAULTS.get(name, [0.5] * 8)
        self._params = list(defaults)
        self._canvas.set_params(self._params)
        for knob, val in zip(self._knobs, self._params):
            knob.set_value(val)
        self._info_lbl.setText(
            f'{self._engines.index(name) + 1} / {len(self._engines)}  —  '
            f'{name.upper()}')

    def _prev_engine(self):
        i = self._engines.index(self._engine)
        self._combo.setCurrentIndex((i - 1) % len(self._engines))

    def _next_engine(self):
        i = self._engines.index(self._engine)
        self._combo.setCurrentIndex((i + 1) % len(self._engines))

    # ── Knobs ─────────────────────────────────────────────────────────────────

    def _on_knob(self, idx: int, raw: int):
        self._params[idx] = raw / 1000.0
        self._canvas.set_param(idx, self._params[idx])

    # ── Timers ────────────────────────────────────────────────────────────────

    def _push_frame(self):
        frame = self._router.get_frame(0)
        self._canvas.set_source_frame(0, frame)

    def _poll_audio(self):
        fft, rms, bass, mid, treble, beat = self._audio.get_data()
        self._canvas.set_audio_data(fft, rms, bass, mid, treble, beat)

    # ── Cleanup ───────────────────────────────────────────────────────────────

    def closeEvent(self, e):
        self._src_timer.stop()
        self._audio_timer.stop()
        self._router.close()
        self._audio.stop()
        e.accept()

    # ── Style helpers ─────────────────────────────────────────────────────────

    @staticmethod
    def _btn_style():
        return f"""
            QPushButton {{
                background:{PANEL2}; color:{TEXT_DIM}; border:1px solid {BORDER};
                border-radius:3px; font-size:10px; padding:0;
            }}
            QPushButton:hover {{ border-color:{ACCENT}; color:{ACCENT}; }}
        """


# ── Entry point ───────────────────────────────────────────────────────────────

def main():
    try:
        QApplication.setHighDpiScaleFactorRoundingPolicy(
            Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    except AttributeError:
        pass
    app = QApplication(sys.argv)
    win = TestBench()
    win.show()
    sys.exit(app.exec())


if __name__ == '__main__':
    main()
