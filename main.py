#!/usr/bin/env python3
"""
VIDEO LIFE — Video Synthesizer
────────────────────────────
A real-time digital video synthesizer inspired by:
  · EMS Spectron (1973)  — colorizer & keying
  · Rutt/Etra (1972)     — scan-line deflection
  · LZX Industries       — modular ramp/CV synthesis
  · Critter & Guitari EYESY — Python-mode audio-reactive visuals
  · Sandin Image Processor   — analog modular patching aesthetic

Controls
  · 8 parameter knobs (MIDI-learnable, CC-mappable)
  · Audio input: live or file
  · MIDI in: CC + note velocity
  · CV gate: detected from audio transients
  · Video editor: trim & export recordings
"""

import sys
import os
import time
import json
import math
import threading
from pathlib import Path
from datetime import datetime

# Ensure local modules resolve
sys.path.insert(0, os.path.dirname(__file__))

from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QPushButton, QComboBox, QSlider, QDial, QTabWidget,
    QFileDialog, QFrame, QSizePolicy, QGridLayout, QCheckBox,
    QStackedWidget, QSplitter, QScrollArea, QToolButton, QGroupBox,
    QSpinBox, QMessageBox, QLineEdit,
)
from PyQt6.QtCore    import Qt, QTimer, pyqtSignal, QThread, QEvent, QRectF, QPointF
from PyQt6.QtGui     import (
    QFont, QColor, QPalette, QCursor, QPainter, QPen, QBrush,
    QLinearGradient, QRadialGradient, QKeySequence, QShortcut,
    QSurfaceFormat,
)

from PyQt6.QtOpenGLWidgets import QOpenGLWidget
from OpenGL.GL import (
    GL_TEXTURE_2D, GL_TEXTURE0, GL_TRIANGLE_STRIP, GL_VERTEX_SHADER,
    GL_FRAGMENT_SHADER, GL_COLOR_BUFFER_BIT, GL_FRAMEBUFFER,
    GL_NO_ERROR, GL_LINK_STATUS,
    GL_RGB, GL_UNSIGNED_BYTE, GL_LINEAR, GL_CLAMP_TO_EDGE,
    GL_TEXTURE_MIN_FILTER, GL_TEXTURE_MAG_FILTER,
    GL_TEXTURE_WRAP_S, GL_TEXTURE_WRAP_T,
    glBindFramebuffer, glViewport, glClearColor, glClear,
    glUseProgram, glActiveTexture, glBindTexture, glUniform1i,
    glGetUniformLocation, glBindVertexArray, glDrawArrays, glGenVertexArrays,
    glGetError, glCreateProgram, glAttachShader, glLinkProgram,
    glGetProgramiv, glGetProgramInfoLog,
    glGenTextures, glTexImage2D, glTexParameteri,
)
from OpenGL.GL import shaders as _glshaders
import numpy as np

from gl_canvas     import SynthCanvas
from audio_engine  import AudioEngine
from midi_engine   import MidiEngine
from link_engine   import LinkEngine
from recorder      import VideoRecorder
from video_editor  import VideoEditor, LiveSplitter
from camera_engine import CameraEngine
from shaders       import SHADERS, PARAM_NAMES, PARAM_DEFAULTS


def _fmt_t(secs: float) -> str:
    s = int(secs)
    return f'{s // 60}:{s % 60:02d}'


# ── Computer keyboard → MIDI note map (Ableton QWERTY layout) ────────────────
# Lower two rows of keys span two octaves starting at the base octave.
# Z=octave down, X=octave up, C=vel down, V=vel up (handled separately).
#
#  W  E     T  Y  U     O  P        ← black keys (sharps/flats)
# A  S  D  F  G  H  J  K  L  ;     ← white keys
#
# Semitone offsets from root of current octave:
_KBD_NOTE = {
    # White keys — bottom row
    Qt.Key.Key_A: 0,   # C
    Qt.Key.Key_S: 2,   # D
    Qt.Key.Key_D: 4,   # E
    Qt.Key.Key_F: 5,   # F
    Qt.Key.Key_G: 7,   # G
    Qt.Key.Key_H: 9,   # A
    Qt.Key.Key_J: 11,  # B
    Qt.Key.Key_K: 12,  # C (oct+1)
    Qt.Key.Key_L: 14,  # D (oct+1)
    Qt.Key.Key_Semicolon: 16,  # E (oct+1)
    # Black keys — top row
    Qt.Key.Key_W: 1,   # C#
    Qt.Key.Key_E: 3,   # D#
    Qt.Key.Key_T: 6,   # F#
    Qt.Key.Key_Y: 8,   # G#
    Qt.Key.Key_U: 10,  # A#
    Qt.Key.Key_O: 13,  # C# (oct+1)
    Qt.Key.Key_P: 15,  # D# (oct+1)
}

# ── Palette ───────────────────────────────────────────────────────────────────
ACCENT   = '#FF2020'    # neon red
ACCENT2  = '#CC0000'    # darker red
RED      = '#FF3B30'
BG       = '#0a0000'
PANEL    = '#130000'
PANEL2   = '#1e0000'
BORDER   = 'rgba(255,30,30,0.15)'
TEXT     = '#d4aaaa'
TEXT_DIM = '#5c2a2a'
MONO_FONT = '"Menlo","Monaco","Courier New"'   # safe in single-quoted f-strings

STYLESHEET = f"""
QMainWindow, QWidget {{
    background: {BG};
    color: {TEXT};
    font-family: 'Menlo', 'Monaco', 'SF Mono', 'Courier New';
    font-size: 12px;
}}
QTabWidget::pane {{
    border: 1px solid {BORDER};
    background: {PANEL};
    border-radius: 8px;
}}
QTabBar::tab {{
    background: {PANEL};
    color: {TEXT_DIM};
    padding: 7px 18px;
    border: 1px solid {BORDER};
    border-bottom: none;
    border-radius: 6px 6px 0 0;
    font-size: 11px;
    font-weight: 600;
    letter-spacing: 0.5px;
}}
QTabBar::tab:selected {{ color: {ACCENT}; border-color: {ACCENT}40; background: {PANEL2}; }}
QTabBar::tab:hover {{ color: {TEXT}; }}
QPushButton {{
    background: {PANEL};
    border: 1px solid {BORDER};
    border-radius: 6px;
    color: {TEXT};
    padding: 5px 14px;
}}
QPushButton:hover {{ border-color: {ACCENT}; color: {ACCENT}; }}
QPushButton:pressed {{ background: {ACCENT}20; }}
QPushButton:checked {{ background: {ACCENT}25; border-color: {ACCENT}; color: {ACCENT}; }}
QPushButton:disabled {{ color: {TEXT_DIM}; border-color: {BORDER}; }}
QComboBox {{
    background: {PANEL};
    border: 1px solid {BORDER};
    border-radius: 6px;
    color: {TEXT};
    padding: 4px 8px;
}}
QComboBox QAbstractItemView {{
    background: {PANEL2};
    border: 1px solid {BORDER};
    color: {TEXT};
    selection-background-color: {ACCENT}30;
}}
QSlider::groove:horizontal {{
    background: {PANEL2};
    border: 1px solid {BORDER};
    height: 4px;
    border-radius: 2px;
}}
QSlider::handle:horizontal {{
    background: {ACCENT};
    width: 12px; height: 12px;
    border-radius: 6px;
    margin: -4px 0;
}}
QSlider::sub-page:horizontal {{ background: {ACCENT}50; border-radius: 2px; }}
QSlider::groove:vertical {{
    background: {PANEL2};
    border: 1px solid {BORDER};
    width: 4px;
    border-radius: 2px;
}}
QSlider::handle:vertical {{
    background: {ACCENT};
    width: 12px; height: 12px;
    border-radius: 6px;
    margin: 0 -4px;
}}
QSlider::sub-page:vertical {{ background: {ACCENT}50; }}
QLabel {{ color: {TEXT}; }}
QGroupBox {{
    border: 1px solid {BORDER};
    border-radius: 8px;
    margin-top: 16px;
    font-size: 10px;
    font-weight: 700;
    color: {ACCENT};
    letter-spacing: 1px;
}}
QGroupBox::title {{
    subcontrol-origin: margin;
    left: 10px;
    padding: 0 4px;
}}
QScrollArea {{ border: none; background: transparent; }}
QFrame[frameShape="4"], QFrame[frameShape="5"] {{
    color: {BORDER};
}}
"""


# ── Knob widget ───────────────────────────────────────────────────────────────

class SynthKnob(QDial):
    """Arc-ring style synth knob — custom painted, no CSS."""

    learn_requested = pyqtSignal(int)
    map_mode: bool = False

    _MIN_ANGLE = 210   # 7 o'clock, degrees CW from 12
    _SWEEP     = 300   # total sweep degrees

    def __init__(self, index: int, name: str, value: float = 0.5, parent=None):
        super().__init__(parent)
        self.index = index
        self.setRange(0, 1000)
        self.setValue(int(value * 1000))
        self.setWrapping(False)
        self.setNotchesVisible(False)
        self.setFixedSize(54, 54)
        self._midi_cc       = None
        self._learning      = False
        self._map_highlight = False
        self._hovered       = False
        self._drag_y        = None
        self._drag_val      = 0
        self.setToolTip(f'{name}\nRight-click → MIDI learn')
        self.valueChanged.connect(self.update)

    def enterEvent(self, e):
        self._hovered = True
        self.update()

    def leaveEvent(self, e):
        self._hovered = False
        self.update()

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.RightButton or SynthKnob.map_mode:
            self.learn_requested.emit(self.index)
        elif e.button() == Qt.MouseButton.LeftButton:
            self._drag_y   = e.position().y()
            self._drag_val = self.value()
            e.accept()

    def mouseMoveEvent(self, e):
        if self._drag_y is None:
            return
        dy  = self._drag_y - e.position().y()   # up = positive
        self.setValue(max(0, min(1000, self._drag_val + int(dy * 4))))
        e.accept()

    def mouseReleaseEvent(self, e):
        self._drag_y = None
        e.accept()

    def set_midi_cc(self, cc: int | None):
        self._midi_cc   = cc
        self._learning  = False
        self.update()

    def get_value(self) -> float:
        return self.value() / 1000.0

    def paintEvent(self, _event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)

        w, h   = self.width(), self.height()
        cx, cy = w / 2.0, h / 2.0
        r      = min(w, h) / 2.0 - 2.5
        val    = self.value() / 1000.0

        # ── Body ──────────────────────────────────────────────────────────
        grad = QRadialGradient(cx * 0.6, cy * 0.5, r * 1.2)
        grad.setColorAt(0, QColor('#221018'))
        grad.setColorAt(1, QColor('#090408'))
        p.setBrush(QBrush(grad))

        if self._learning:
            border_clr = QColor(RED)
        elif self._map_highlight:
            border_clr = QColor('#FF9900')
        elif self._midi_cc is not None:
            border_clr = QColor(ACCENT)
        elif self._hovered:
            border_clr = QColor(ACCENT)
            border_clr.setAlpha(90)
        else:
            border_clr = QColor(80, 20, 20, 55)

        p.setPen(QPen(border_clr, 1.5))
        p.drawEllipse(QRectF(cx - r, cy - r, r * 2, r * 2))

        # ── Arc track & value arc ─────────────────────────────────────────
        arc_r    = r * 0.76
        arc_rect = QRectF(cx - arc_r, cy - arc_r, arc_r * 2, arc_r * 2)
        start_qt = int((90 - self._MIN_ANGLE) * 16)   # Qt angle for 7 o'clock

        dim = QColor(ACCENT)
        dim.setAlpha(28)
        trk = QPen(dim, 2.5)
        trk.setCapStyle(Qt.PenCapStyle.FlatCap)
        p.setPen(trk)
        p.drawArc(arc_rect, start_qt, int(-self._SWEEP * 16))

        if val > 0.001:
            val_pen = QPen(QColor(ACCENT), 2.5)
            val_pen.setCapStyle(Qt.PenCapStyle.FlatCap)
            p.setPen(val_pen)
            p.drawArc(arc_rect, start_qt, int(-val * self._SWEEP * 16))

        # ── Pointer line ──────────────────────────────────────────────────
        angle = math.radians(self._MIN_ANGLE + val * self._SWEEP)
        sa, ca = math.sin(angle), math.cos(angle)
        x1 = cx + r * 0.22 * sa;  y1 = cy - r * 0.22 * ca
        x2 = cx + r * 0.58 * sa;  y2 = cy - r * 0.58 * ca

        ptr = QPen(QColor('#cccccc'), 1.8)
        ptr.setCapStyle(Qt.PenCapStyle.RoundCap)
        p.setPen(ptr)
        p.drawLine(QPointF(x1, y1), QPointF(x2, y2))

        # ── Centre dot ────────────────────────────────────────────────────
        p.setPen(Qt.PenStyle.NoPen)
        dot = QColor(255, 255, 255, 45)
        p.setBrush(QBrush(dot))
        p.drawEllipse(QPointF(cx, cy), 2.2, 2.2)

        p.end()


# ── Audio scope mini widget ───────────────────────────────────────────────────

class MiniScope(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(50)
        self._data = [0.0] * 64
        self._color = QColor(ACCENT)

    def update_data(self, fft: list, rms: float):
        stride = max(1, len(fft) // 64)
        self._data = [float(fft[i * stride]) for i in range(64)]
        self.update()

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()

        # Background
        p.fillRect(0, 0, w, h, QColor(PANEL))

        # Bars
        n = len(self._data)
        bw = w / n
        for i, v in enumerate(self._data):
            bh = int(v * h * 0.9)
            alpha = int(100 + v * 155)
            color = QColor(ACCENT)
            color.setAlpha(alpha)
            p.fillRect(int(i * bw), h - bh, max(1, int(bw - 1)), bh, color)

        p.end()


# ── Parameter panel (8 knobs per mode) ───────────────────────────────────────

class ParamPanel(QWidget):
    param_changed = pyqtSignal(int, float)   # index, value

    def __init__(self, midi: MidiEngine | None = None, parent=None):
        super().__init__(parent)
        self._midi      = midi
        self._mode      = ''
        self._knobs:     list[SynthKnob] = []
        self._labels:    list[QLabel]    = []
        self._val_edits: list[QLineEdit] = []
        self._build_ui()

    def _build_ui(self):
        layout = QGridLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(12)

        for i in range(8):
            col   = i % 4
            row   = (i // 4) * 3

            knob  = SynthKnob(i, f'P{i}')
            knob.valueChanged.connect(lambda v, idx=i: self.param_changed.emit(idx, v / 1000.0))
            knob.learn_requested.connect(self._start_learn)

            name  = QLabel(f'P{i}')
            name.setAlignment(Qt.AlignmentFlag.AlignCenter)
            name.setStyleSheet(f'font-size:9px;color:{TEXT_DIM};letter-spacing:0.5px;')

            val_edit = QLineEdit('0.50')
            val_edit.setAlignment(Qt.AlignmentFlag.AlignCenter)
            val_edit.setFixedWidth(40)
            val_edit.setStyleSheet(
                f'font-size:9px;color:{ACCENT};font-family:{MONO_FONT};'
                f'background:transparent;border:none;padding:0;'
            )
            knob.valueChanged.connect(lambda v, e=val_edit: e.setText(f'{v/1000:.2f}'))
            val_edit.editingFinished.connect(lambda e=val_edit, k=knob: self._apply_val_edit(e, k))

            layout.addWidget(knob,     row,     col, alignment=Qt.AlignmentFlag.AlignCenter)
            layout.addWidget(val_edit, row + 1, col, alignment=Qt.AlignmentFlag.AlignCenter)
            layout.addWidget(name,     row + 2, col, alignment=Qt.AlignmentFlag.AlignCenter)

            self._knobs.append(knob)
            self._labels.append(name)
            self._val_edits.append(val_edit)

    def load_mode(self, mode: str):
        self._mode = mode
        names    = PARAM_NAMES.get(mode, [f'P{i}' for i in range(8)])
        defaults = PARAM_DEFAULTS.get(mode, [0.5] * 8)
        for i, (knob, lbl) in enumerate(zip(self._knobs, self._labels)):
            n = names[i] if i < len(names) else f'P{i}'
            lbl.setText(n.upper()[:6])
            lbl.setToolTip(n)
            knob.setValue(int(defaults[i] * 1000))
            cc = self._midi.get_mapping(i) if self._midi else None
            knob.set_midi_cc(cc)

    def _apply_val_edit(self, edit: QLineEdit, knob: SynthKnob):
        try:
            v = max(0.0, min(1.0, float(edit.text())))
            knob.setValue(int(v * 1000))
            edit.setText(f'{v:.2f}')
        except ValueError:
            edit.setText(f'{knob.value() / 1000:.2f}')

    def set_param_from_midi(self, index: int, value: float):
        if 0 <= index < len(self._knobs):
            self._knobs[index].blockSignals(True)
            self._knobs[index].setValue(int(value * 1000))
            self._knobs[index].blockSignals(False)
            if index < len(self._val_edits):
                self._val_edits[index].setText(f'{value:.2f}')

    def get_values(self) -> list:
        return [k.get_value() for k in self._knobs]

    def set_map_mode(self, on: bool):
        SynthKnob.map_mode = on
        for knob in self._knobs:
            knob._map_highlight = on
            knob.update()

    def _start_learn(self, index: int):
        if self._midi is None:
            return
        self._midi.start_learn(index)
        self._knobs[index]._learning = True
        self._knobs[index].update()
        QTimer.singleShot(10000, lambda i=index: self._cancel_learn(i))

    def _cancel_learn(self, index: int):
        if self._midi is None:
            return
        self._midi.cancel_learn()
        cc = self._midi.get_mapping(index)
        self._knobs[index].set_midi_cc(cc)


# ── Collapsible section ───────────────────────────────────────────────────────

class CollapsibleSection(QWidget):
    """A toggle button that shows/hides an inner widget."""

    def __init__(self, title: str, inner: QWidget, parent=None):
        super().__init__(parent)
        self._title = title
        self._inner = inner
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 2, 0, 0)
        layout.setSpacing(2)

        self._btn = QPushButton(f'▶  {title}')
        self._btn.setCheckable(True)
        self._btn.setChecked(False)
        self._btn.setStyleSheet(f"""
            QPushButton {{
                background: {PANEL2};
                border: 1px solid {BORDER};
                border-radius: 4px;
                color: {TEXT_DIM};
                font-size: 9px;
                font-weight: 700;
                letter-spacing: 0.5px;
                padding: 3px 8px;
                text-align: left;
            }}
            QPushButton:checked {{
                background: {ACCENT}12;
                border-color: {ACCENT}50;
                color: {ACCENT};
            }}
            QPushButton:hover {{ color: {TEXT}; }}
        """)
        self._btn.clicked.connect(self._toggle)
        layout.addWidget(self._btn)

        inner.setVisible(False)
        layout.addWidget(inner)

    def _toggle(self, checked: bool):
        self._inner.setVisible(checked)
        self._btn.setText(f'{"▼" if checked else "▶"}  {self._title}')


# ── Output Window (standalone second-screen display) ─────────────────────────

_OUT_VERT = """
#version 330 core
out vec2 vUV;
void main() {
    const vec2 pos[4] = vec2[](vec2(-1,-1), vec2(1,-1), vec2(-1,1), vec2(1,1));
    const vec2 uvs[4] = vec2[](vec2(0,0),   vec2(1,0),  vec2(0,1),  vec2(1,1));
    gl_Position = vec4(pos[gl_VertexID], 0.0, 1.0);
    vUV = uvs[gl_VertexID];
}
"""

_OUT_FRAG = """
#version 330 core
uniform sampler2D uTex;
in  vec2 vUV;
out vec4 fragColor;
void main() { fragColor = texture(uTex, vUV); }
"""


class OutputGLView(QOpenGLWidget):
    """Renders the output.

    Primary path: shared _output_tex blitted by SynthCanvas each frame.
    Fallback path: direct video frames via set_video_frame(), used when the
    main window is occluded (macOS throttles SynthCanvas.paintGL) so the
    shared texture goes stale.  The fallback activates after 150 ms of
    staleness and deactivates as soon as SynthCanvas resumes blitting.
    """

    def __init__(self, canvas: SynthCanvas, parent=None):
        super().__init__(parent)
        self._canvas        = canvas
        self._prog          = None
        self._vao           = None
        # direct-video fallback
        self._vid_tex       = None
        self._pending_video = None
        # Repaint driven by canvas frame signal; slow fallback for edge cases
        canvas.frame_ready.connect(self.update)
        self._fallback_timer = QTimer(self)
        self._fallback_timer.setInterval(100)  # 10 Hz fallback
        self._fallback_timer.timeout.connect(self.update)
        self._fallback_timer.start()

    def set_video_frame(self, arr):
        """Receive a video frame directly (fallback when SynthCanvas is occluded)."""
        try:
            # Flip rows: numpy arrays are top-to-bottom; GL textures are bottom-to-top.
            self._pending_video = np.ascontiguousarray(arr[::-1])
        except Exception as e:
            print(f'[Output GL] set_video_frame: {e}')

    def _upload_pending_video(self):
        arr = self._pending_video
        if arr is None:
            return
        self._pending_video = None
        try:
            h, w = arr.shape[:2]
            if self._vid_tex is None:
                self._vid_tex = int(glGenTextures(1))
            glBindTexture(GL_TEXTURE_2D, self._vid_tex)
            glTexImage2D(GL_TEXTURE_2D, 0, GL_RGB, w, h, 0,
                         GL_RGB, GL_UNSIGNED_BYTE, arr)
            glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, GL_LINEAR)
            glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MAG_FILTER, GL_LINEAR)
            glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_S, GL_CLAMP_TO_EDGE)
            glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_T, GL_CLAMP_TO_EDGE)
            glBindTexture(GL_TEXTURE_2D, 0)
        except Exception as e:
            print(f'[Output GL] video upload: {e}')

    def initializeGL(self):
        try:
            # Flush any stale GL errors from the shared context before compiling
            while glGetError() != GL_NO_ERROR:
                pass
            vert = _glshaders.compileShader(_OUT_VERT, GL_VERTEX_SHADER)
            frag = _glshaders.compileShader(_OUT_FRAG, GL_FRAGMENT_SHADER)
            # Link manually — skip glValidateProgram which always fails when no
            # draw framebuffer is bound during initializeGL.
            self._prog = glCreateProgram()
            glAttachShader(self._prog, vert)
            glAttachShader(self._prog, frag)
            glLinkProgram(self._prog)
            if not glGetProgramiv(self._prog, GL_LINK_STATUS):
                raise RuntimeError(glGetProgramInfoLog(self._prog))
            self._vao  = glGenVertexArrays(1)
        except Exception as e:
            print(f'[Output GL] initializeGL error: {e}')
            self._prog = None
            self._vao  = None

    def paintGL(self):
        try:
            self._paint_impl()
        except Exception as e:
            print(f'[Output GL] paintGL error: {e}')

    def _paint_impl(self):
        import time as _time
        self._upload_pending_video()

        # Choose texture: prefer the shared output tex when fresh; fall back
        # to the direct video tex when SynthCanvas hasn't blitted for >150 ms.
        shared_tex = self._canvas.get_output_texture()
        age = _time.time() - getattr(self._canvas, '_output_tex_updated_at', 0)
        if shared_tex is not None and age < 0.15:
            tex = shared_tex
        elif self._vid_tex is not None:
            tex = self._vid_tex
        else:
            tex = shared_tex  # might be None — cleared below

        dpr = self.devicePixelRatio()
        pw  = int(self.width()  * dpr)
        ph  = int(self.height() * dpr)

        glBindFramebuffer(GL_FRAMEBUFFER, self.defaultFramebufferObject())
        glViewport(0, 0, pw, ph)
        glClearColor(0.0, 0.0, 0.0, 1.0)
        glClear(GL_COLOR_BUFFER_BIT)

        if tex is None or self._prog is None:
            return

        glUseProgram(self._prog)
        glActiveTexture(GL_TEXTURE0)
        glBindTexture(GL_TEXTURE_2D, tex)
        glUniform1i(glGetUniformLocation(self._prog, b'uTex'), 0)
        glBindVertexArray(self._vao)
        glDrawArrays(GL_TRIANGLE_STRIP, 0, 4)
        glBindVertexArray(0)


class OutputWindow(QWidget):
    """Floating output window; toggles in-place to borderless fullscreen (F / Esc).

    Avoids spawning a second window so there is never more than one OutputGLView
    alive at a time — eliminates duplicate-window and shared-context conflicts.
    Uses FramelessWindowHint + screen geometry rather than showFullScreen() to
    stay in the same macOS Space and keep SynthCanvas.paintGL running.
    """

    closed = pyqtSignal()

    def __init__(self, canvas: SynthCanvas, frame_signal=None, parent=None):
        super().__init__(parent, Qt.WindowType.Window)
        self.setWindowTitle('Video Output')
        self.setStyleSheet('background: black;')
        self.setMinimumSize(320, 180)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self._canvas       = canvas
        self._frame_signal = frame_signal
        self._view         = OutputGLView(canvas)
        self._view.setFocusPolicy(Qt.FocusPolicy.NoFocus)  # key events go to OutputWindow
        layout.addWidget(self._view)
        self._fullscreen  = False
        self._normal_geom = None

        if frame_signal is not None:
            frame_signal.connect(self._view.set_video_frame)

    def keyPressEvent(self, e):
        if e.key() == Qt.Key.Key_F:
            self._toggle_fullscreen()
        elif e.key() == Qt.Key.Key_Escape and self._fullscreen:
            self._toggle_fullscreen()
        else:
            super().keyPressEvent(e)

    def _toggle_fullscreen(self):
        if self._fullscreen:
            self.hide()
            self.setWindowFlags(Qt.WindowType.Window)
            self.setWindowTitle('Video Output')
            if self._normal_geom:
                self.setGeometry(self._normal_geom)
            self.show()
            self.raise_()
            self._fullscreen = False
        else:
            self._normal_geom = self.geometry()
            screen = self.screen() or QApplication.primaryScreen()
            self.hide()
            self.setWindowFlags(
                Qt.WindowType.Window | Qt.WindowType.FramelessWindowHint
            )
            if screen:
                self.setGeometry(screen.geometry())
            self.show()
            self.raise_()
            self.activateWindow()
            self.setFocus()
            self._fullscreen = True

    def closeEvent(self, e):
        if self._frame_signal is not None:
            try:
                self._frame_signal.disconnect(self._view.set_video_frame)
            except Exception:
                pass
            self._frame_signal = None
        self.closed.emit()
        super().closeEvent(e)


# ── Main Window ───────────────────────────────────────────────────────────────

class MainWindow(QMainWindow):
    _scan_results_ready = pyqtSignal(list)

    def __init__(self):
        super().__init__()
        self.setWindowTitle('VIDEO LIFE — Video Synthesizer')
        self.setMinimumSize(1280, 800)
        self.resize(1440, 900)

        _here = os.path.dirname(os.path.abspath(__file__))
        self._rec_dir = os.path.join(_here, 'recordings')

        self.audio   = AudioEngine()
        self.midi    = MidiEngine()
        self.link    = LinkEngine(initial_bpm=120.0)
        self.rec     = VideoRecorder(self._rec_dir)
        self.camera  = CameraEngine()

        self._params    = [0.5] * 8
        self._mode      = list(SHADERS.keys())[0]
        self._full_mode = False
        self._preset_path = os.path.join(os.path.dirname(__file__), 'presets')
        self.canvas     = None   # set before _build_ui so early signals don't crash
        self._out_win   = None

        self._midi_map_mode   = False
        self._kbd_midi_active = False
        self._kbd_octave      = 4          # default: C4 = MIDI 60
        self._kbd_velocity    = 100        # MIDI 1–127, steps of 20
        self._kbd_held: set   = set()

        self.setStyleSheet(STYLESHEET)
        self._build_ui()
        self.midi.set_learn_complete_cb(self._on_learn_complete)
        self._setup_shortcuts()
        self._start_audio_poll()
        self._start_midi_poll()
        self._start_link_poll()

        # Default audio: start with silence generator so visuals react on open
        threading.Thread(target=self.audio.silence, daemon=True).start()

    # ── UI construction ───────────────────────────────────────────────────────

    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # ── Title bar ─────────────────────────────────────────────────────
        titlebar = QWidget()
        titlebar.setFixedHeight(44)
        titlebar.setStyleSheet(f'background:{PANEL};border-bottom:1px solid {BORDER};')
        tb_l = QHBoxLayout(titlebar)
        tb_l.setContentsMargins(14, 0, 14, 0)

        logo = QLabel('VIDEO LIFE')
        logo.setStyleSheet(f"font-size:15px;font-weight:700;color:{ACCENT};letter-spacing:4px;font-family:'Menlo','Monaco','SF Mono';")
        tb_l.addWidget(logo)

        sub = QLabel('VIDEO SYNTHESIZER / LIFE')
        sub.setStyleSheet(f'font-size:10px;color:{TEXT_DIM};letter-spacing:2px;margin-left:10px;')
        tb_l.addWidget(sub)
        tb_l.addStretch()

        self.fps_lbl = QLabel('-- fps')
        self.fps_lbl.setStyleSheet(f'font-size:11px;color:{TEXT_DIM};font-family:{MONO_FONT};')
        tb_l.addWidget(self.fps_lbl)

        root.addWidget(titlebar)

        # ── Main content ──────────────────────────────────────────────────
        splitter = LiveSplitter(Qt.Orientation.Horizontal)
        splitter.setHandleWidth(6)
        splitter.setChildrenCollapsible(False)
        splitter.setStyleSheet('QSplitter::handle { background: ' + BORDER + '; }')
        root.addWidget(splitter, 1)

        # Left: controls
        self._left_panel = QWidget()
        self._left_panel.setMinimumWidth(260)
        self._left_panel.setStyleSheet(f'background:{PANEL};')
        self._build_left_panel()
        splitter.addWidget(self._left_panel)

        # Center: video output
        center = QWidget()
        center.setStyleSheet(f'background:{BG};')
        c_l = QVBoxLayout(center)
        c_l.setContentsMargins(10, 10, 10, 10)
        c_l.setSpacing(8)

        # Canvas
        self.canvas = SynthCanvas()
        self.canvas.fps_updated.connect(lambda f: self.fps_lbl.setText(f'{f:.0f} fps'))
        self.canvas.video_active.connect(self._on_video_active)
        self.canvas.set_recorder(self.rec)
        for _i, _bp in enumerate(self._blend_param_panels):
            self.canvas.set_blend_layer_params(_i, _bp.get_values())
        c_l.addWidget(self.canvas, 1)

        # Output toolbar
        c_l.addWidget(self._build_output_toolbar())

        splitter.addWidget(center)

        # Right: tabs
        right = QWidget()
        right.setMinimumWidth(220)
        right.setStyleSheet(f'background:{PANEL};')
        self._build_right_panel(right)
        splitter.addWidget(right)

        splitter.setSizes([300, 900, 310])

        # ── Status bar ────────────────────────────────────────────────────
        statusbar = QWidget()
        statusbar.setFixedHeight(26)
        statusbar.setStyleSheet(f'background:{PANEL};border-top:1px solid {BORDER};')
        sb_l = QHBoxLayout(statusbar)
        sb_l.setContentsMargins(14, 0, 14, 0)
        self.status_lbl = QLabel('Ready')
        self.status_lbl.setStyleSheet(f'font-size:11px;color:{TEXT_DIM};')
        sb_l.addWidget(self.status_lbl)
        sb_l.addStretch()
        self.rms_bar = QSlider(Qt.Orientation.Horizontal)
        self.rms_bar.setRange(0, 100)
        self.rms_bar.setEnabled(False)
        self.rms_bar.setFixedWidth(80)
        sb_l.addWidget(QLabel('RMS'))
        sb_l.addWidget(self.rms_bar)
        root.addWidget(statusbar)

    def _build_left_panel(self):
        outer = QVBoxLayout(self._left_panel)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setStyleSheet('QScrollArea { border: none; background: transparent; }')
        outer.addWidget(scroll)

        _inner = QWidget()
        _inner.setStyleSheet(f'background: {PANEL};')
        scroll.setWidget(_inner)

        layout = QVBoxLayout(_inner)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(10)

        # ── Engine selector ────────────────────────────────────────────
        eng_grp = QGroupBox('SYNTHESIS ENGINE')
        eng_l   = QVBoxLayout(eng_grp)
        self.mode_combo = QComboBox()
        self.mode_combo.addItems(list(SHADERS.keys()))
        self.mode_combo.setStyleSheet(f'font-weight:600;color:{ACCENT};')
        self.mode_combo.currentTextChanged.connect(self._on_mode_change)
        eng_l.addWidget(self.mode_combo)

        # Mode descriptions
        self.mode_desc = QLabel()
        self.mode_desc.setWordWrap(True)
        self.mode_desc.setStyleSheet(f'font-size:10px;color:{TEXT_DIM};margin-top:4px;')
        eng_l.addWidget(self.mode_desc)
        layout.addWidget(eng_grp)
        self._update_mode_desc(list(SHADERS.keys())[0])

        # ── Parameters ────────────────────────────────────────────────
        param_grp = QGroupBox('PARAMETERS  ·  right-click knob = MIDI learn')
        param_l   = QVBoxLayout(param_grp)
        self.param_panel = ParamPanel(self.midi)
        self.param_panel.param_changed.connect(self._on_param_change)
        self.param_panel.load_mode(self._mode)
        param_l.addWidget(self.param_panel)
        layout.addWidget(param_grp)

        # ── Video blend (3-layer chain) ────────────────────────────────
        blend_grp = QGroupBox('VIDEO BLEND')
        blend_v   = QVBoxLayout(blend_grp)
        blend_v.setSpacing(5)
        blend_v.setContentsMargins(6, 16, 6, 6)

        _modes   = ['Add', 'Mix', 'Multiply', 'Screen', 'Overlay', 'Difference']
        _engines = list(SHADERS.keys())

        self._blend_eng_combos:   list[QComboBox]  = []
        self._blend_mode_combos:  list[QComboBox]  = []
        self._blend_mix_sliders:  list[QSlider]    = []
        self._blend_param_panels: list[ParamPanel] = []

        for i in range(3):
            # Row 1: layer label + engine selector (full width — no text cut-off)
            eng_row = QHBoxLayout()
            eng_row.setSpacing(4)
            lbl = QLabel(f'L{i + 1}')
            lbl.setStyleSheet(f'font-size:9px;font-weight:700;color:{ACCENT};min-width:16px;')
            eng = QComboBox()
            eng.addItems(_engines)
            eng.setStyleSheet('font-size:10px;')
            eng_row.addWidget(lbl)
            eng_row.addWidget(eng, 1)

            # Row 2: blend mode + mix slider + percentage
            mix_row = QHBoxLayout()
            mix_row.setSpacing(4)
            md = QComboBox()
            md.addItems(_modes)
            md.setStyleSheet('font-size:10px;')
            sl = QSlider(Qt.Orientation.Horizontal)
            sl.setRange(0, 100)
            sl.setValue(0)
            pl = QLabel('0%')
            pl.setFixedWidth(28)
            pl.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            pl.setStyleSheet(f'font-size:9px;color:{ACCENT};font-family:{MONO_FONT};')
            mix_row.addWidget(md)
            mix_row.addWidget(sl, 1)
            mix_row.addWidget(pl)

            eng.currentTextChanged.connect(lambda _, ii=i: self._on_blend_layer_change(ii))
            md.currentIndexChanged.connect(lambda _, ii=i: self._on_blend_layer_change(ii))
            sl.valueChanged.connect(lambda v, lbl=pl: lbl.setText(f'{v}%'))
            sl.valueChanged.connect(lambda _, ii=i: self._on_blend_layer_change(ii))

            # Per-layer param panel (no MIDI learn)
            bp = ParamPanel()
            bp.load_mode(_engines[0])
            bp.param_changed.connect(lambda _idx, _val, ii=i: self._on_blend_param_change(ii))
            eng.currentTextChanged.connect(lambda text, ii=i: self._blend_param_panels[ii].load_mode(text))
            self._blend_param_panels.append(bp)

            self._blend_eng_combos.append(eng)
            self._blend_mode_combos.append(md)
            self._blend_mix_sliders.append(sl)

            layer_w = QWidget()
            layer_l = QVBoxLayout(layer_w)
            layer_l.setContentsMargins(0, 0, 0, 0)
            layer_l.setSpacing(3)
            layer_l.addLayout(eng_row)
            layer_l.addLayout(mix_row)
            layer_l.addWidget(CollapsibleSection(f'L{i + 1} PARAMS', bp))
            blend_v.addWidget(layer_w)

            if i < 2:
                sep = QFrame()
                sep.setFrameShape(QFrame.Shape.HLine)
                sep.setStyleSheet(f'color:{BORDER};')
                blend_v.addWidget(sep)

        layout.addWidget(blend_grp)

        # ── Preset buttons ─────────────────────────────────────────────
        preset_grp = QGroupBox('PRESETS')
        preset_l   = QHBoxLayout(preset_grp)
        save_btn = QPushButton('Save')
        save_btn.clicked.connect(self._save_preset)
        load_btn = QPushButton('Load')
        load_btn.clicked.connect(self._load_preset)
        preset_l.addWidget(save_btn)
        preset_l.addWidget(load_btn)
        layout.addWidget(preset_grp)

        layout.addStretch()

    def _build_right_panel(self, parent: QWidget):
        layout = QVBoxLayout(parent)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(0)

        tabs = QTabWidget()
        layout.addWidget(tabs)

        # ── Audio tab ─────────────────────────────────────────────────
        audio_widget = QWidget()
        audio_l      = QVBoxLayout(audio_widget)
        audio_l.setSpacing(10)

        src_grp = QGroupBox('AUDIO SOURCE')
        src_l   = QVBoxLayout(src_grp)

        # Source type
        src_type_row = QHBoxLayout()
        self.src_live_btn = QPushButton('Live Input')
        self.src_live_btn.setCheckable(True)
        self.src_live_btn.setChecked(True)
        self.src_file_btn = QPushButton('Audio File')
        self.src_file_btn.setCheckable(True)
        src_type_row.addWidget(self.src_live_btn)
        src_type_row.addWidget(self.src_file_btn)
        self.src_live_btn.clicked.connect(lambda: self._set_audio_source('live'))
        self.src_file_btn.clicked.connect(lambda: self._set_audio_source('file'))
        src_l.addLayout(src_type_row)

        # Device selector
        dev_row = QHBoxLayout()
        self.audio_device_combo = QComboBox()
        self.audio_device_combo.addItem('Default Input')
        devs = self.audio.get_device_names()
        _VIRTUAL_KEYWORDS = ('blackhole', 'soundflower', 'loopback',
                             'virtual', 'aggregate', 'multi-output')
        for d in devs:
            tag = '  [virtual]' if any(k in d.lower() for k in _VIRTUAL_KEYWORDS) else ''
            self.audio_device_combo.addItem(d[:36] + tag)
        self.audio_device_combo.currentIndexChanged.connect(self._on_audio_device_change)
        dev_row.addWidget(self.audio_device_combo, 1)

        refresh_audio_btn = QPushButton('↻')
        refresh_audio_btn.setFixedWidth(28)
        refresh_audio_btn.setToolTip('Rescan audio input devices (plug in interface first)')
        refresh_audio_btn.clicked.connect(self._refresh_audio_devices)
        dev_row.addWidget(refresh_audio_btn)

        detect_btn = QPushButton('Detect')
        detect_btn.setFixedWidth(56)
        detect_btn.setToolTip('Auto-select first virtual audio device (BlackHole, Loopback…)')
        detect_btn.clicked.connect(self._detect_virtual_audio)
        dev_row.addWidget(detect_btn)
        src_l.addLayout(dev_row)

        # Input gain
        gain_row = QHBoxLayout()
        gain_lbl = QLabel('Gain:')
        gain_lbl.setStyleSheet(f'font-size:10px;color:{TEXT_DIM};')
        gain_lbl.setFixedWidth(32)
        self._gain_slider = QSlider(Qt.Orientation.Horizontal)
        self._gain_slider.setRange(0, 400)
        self._gain_slider.setValue(100)
        self._gain_slider.setToolTip('Input gain (100 = unity, 200 = 2×, useful for line-level or instrument inputs)')
        self._gain_val_lbl = QLabel('1.0×')
        self._gain_val_lbl.setFixedWidth(34)
        self._gain_val_lbl.setStyleSheet(f'font-size:10px;color:{ACCENT};font-family:{MONO_FONT};')
        self._gain_slider.valueChanged.connect(self._on_gain_change)
        gain_row.addWidget(gain_lbl)
        gain_row.addWidget(self._gain_slider, 1)
        gain_row.addWidget(self._gain_val_lbl)
        src_l.addLayout(gain_row)

        # Ableton routing hint
        ableton_hint = QWidget()
        ah_l = QVBoxLayout(ableton_hint)
        ah_l.setContentsMargins(0, 2, 0, 0)
        ah_l.setSpacing(2)
        ah_title = QLabel('Ableton Live audio feed:')
        ah_title.setStyleSheet(f'font-size:10px;font-weight:600;color:{ACCENT};')
        ah_l.addWidget(ah_title)
        ah_steps = QLabel(
            '1. Install BlackHole 2ch  (brew install blackhole-2ch)\n'
            '2. Ableton Prefs → Audio → Output: BlackHole 2ch\n'
            '   (use Multi-Output Device to keep speakers too)\n'
            '3. Select BlackHole 2ch above  →  Live Input'
        )
        ah_steps.setWordWrap(True)
        ah_steps.setStyleSheet(f'font-size:10px;color:{TEXT_DIM};line-height:150%;')
        ah_l.addWidget(ah_steps)
        src_l.addWidget(ableton_hint)

        # File row
        self.file_row = QWidget()
        file_l = QHBoxLayout(self.file_row)
        file_l.setContentsMargins(0, 0, 0, 0)
        self.file_lbl = QLabel('No file loaded')
        self.file_lbl.setStyleSheet(f'font-size:10px;color:{TEXT_DIM};')
        self.file_lbl.setWordWrap(True)
        file_l.addWidget(self.file_lbl, 1)
        browse_btn = QPushButton('Browse')
        browse_btn.clicked.connect(self._browse_audio)
        file_l.addWidget(browse_btn)
        src_l.addWidget(self.file_row)
        self.file_row.hide()

        # Transport controls (shown after a file is loaded)
        self._transport_row = QWidget()
        tp_v = QVBoxLayout(self._transport_row)
        tp_v.setContentsMargins(0, 4, 0, 0)
        tp_v.setSpacing(4)

        # Buttons row
        tp_btns = QHBoxLayout()
        tp_btns.setSpacing(4)

        self._rewind_btn = QPushButton('|<')
        self._rewind_btn.setFixedHeight(26)
        self._rewind_btn.clicked.connect(self._audio_rewind)

        self._play_btn = QPushButton('Play')
        self._play_btn.setFixedHeight(26)
        self._play_btn.setCheckable(True)
        self._play_btn.setChecked(False)
        self._play_btn.clicked.connect(self._audio_play_pause)

        self._loop_chk = QPushButton('Loop')
        self._loop_chk.setCheckable(True)
        self._loop_chk.setChecked(True)
        self._loop_chk.setFixedHeight(26)
        self._loop_chk.setToolTip('Loop')
        self._loop_chk.clicked.connect(lambda c: self.audio.set_loop(c))

        self._time_lbl = QLabel('0:00 / 0:00')
        self._time_lbl.setStyleSheet(f'font-size:10px;color:{ACCENT};font-family:{MONO_FONT};')

        tp_btns.addWidget(self._rewind_btn)
        tp_btns.addWidget(self._play_btn)
        tp_btns.addWidget(self._loop_chk)
        tp_btns.addStretch()
        tp_btns.addWidget(self._time_lbl)
        tp_v.addLayout(tp_btns)

        # Scrub slider
        self._scrub_slider = QSlider(Qt.Orientation.Horizontal)
        self._scrub_slider.setRange(0, 1000)
        self._scrub_slider.setValue(0)
        self._scrub_slider.sliderPressed.connect(self._scrub_pressed)
        self._scrub_slider.sliderReleased.connect(self._scrub_released)
        tp_v.addWidget(self._scrub_slider)

        self._scrub_dragging = False
        src_l.addWidget(self._transport_row)
        self._transport_row.hide()

        # Timer to refresh scrub position
        self._transport_timer = QTimer(self)
        self._transport_timer.timeout.connect(self._update_transport)
        self._transport_timer.start(100)
        audio_l.addWidget(src_grp)

        # Spectrum scope
        scope_grp = QGroupBox('SPECTRUM')
        scope_l   = QVBoxLayout(scope_grp)
        self.scope = MiniScope()
        scope_l.addWidget(self.scope)

        # Band meters
        meters = QHBoxLayout()
        for name, attr in [('BASS', '_bass_lbl'), ('MID', '_mid_lbl'), ('TRB', '_treble_lbl'), ('BEAT', '_beat_lbl')]:
            col = QVBoxLayout()
            lbl = QLabel('0.0')
            lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            lbl.setStyleSheet(f'font-size:10px;font-family:{MONO_FONT};color:{ACCENT};')
            setattr(self, attr, lbl)
            bar = QSlider(Qt.Orientation.Vertical)
            bar.setRange(0, 100)
            bar.setEnabled(False)
            bar.setFixedHeight(50)
            setattr(self, attr + '_bar', bar)
            col.addWidget(bar, alignment=Qt.AlignmentFlag.AlignHCenter)
            col.addWidget(QLabel(name, alignment=Qt.AlignmentFlag.AlignCenter))
            col.addWidget(lbl)
            meters.addLayout(col)
        scope_l.addLayout(meters)
        audio_l.addWidget(scope_grp)
        audio_l.addStretch()
        tabs.addTab(audio_widget, 'AUDIO')

        # ── MIDI tab ──────────────────────────────────────────────────
        midi_widget = QWidget()
        midi_l      = QVBoxLayout(midi_widget)
        midi_l.setSpacing(10)

        # ── MIDI INPUT ────────────────────────────────────────────────
        midi_grp = QGroupBox('MIDI INPUT')
        mg_l     = QVBoxLayout(midi_grp)

        port_row = QHBoxLayout()
        self.midi_port_combo = QComboBox()
        self.midi_port_combo.addItem('-- No MIDI --')
        for p in self.midi.get_port_names():
            self.midi_port_combo.addItem(p[:40])
        self.midi_port_combo.currentTextChanged.connect(self._on_midi_port_change)
        port_row.addWidget(self.midi_port_combo, 1)

        refresh_btn = QPushButton('Refresh')
        refresh_btn.setFixedWidth(64)
        refresh_btn.setToolTip('Scan for new MIDI devices (Bluetooth, Network, USB)')
        refresh_btn.clicked.connect(self._refresh_midi_ports)
        port_row.addWidget(refresh_btn)
        mg_l.addLayout(port_row)

        self.midi_status = QLabel('Not connected')
        self.midi_status.setStyleSheet(f'font-size:11px;color:{TEXT_DIM};')
        mg_l.addWidget(self.midi_status)

        hint = QLabel('Bluetooth LE / Network MIDI devices must be paired in macOS\n'
                      'Audio MIDI Setup first, then click Refresh.')
        hint.setWordWrap(True)
        hint.setStyleSheet(f'font-size:10px;color:{TEXT_DIM};margin-top:2px;')
        mg_l.addWidget(hint)
        midi_l.addWidget(midi_grp)

        # ── ABLETON LINK ──────────────────────────────────────────────
        link_grp = QGroupBox('ABLETON LINK')
        link_l   = QVBoxLayout(link_grp)

        link_top = QHBoxLayout()
        self.link_btn = QPushButton('Enable Link')
        self.link_btn.setCheckable(True)
        self.link_btn.setEnabled(self.link.available)
        self.link_btn.setStyleSheet(
            f'QPushButton{{background:{PANEL};border:1px solid {BORDER};color:{TEXT};border-radius:6px;padding:5px 10px;}}'
            f'QPushButton:checked{{background:{ACCENT}25;border-color:{ACCENT};color:{ACCENT};}}'
            f'QPushButton:disabled{{color:{TEXT_DIM};border-color:{BORDER};}}'
        )
        self.link_btn.clicked.connect(self._toggle_link)
        link_top.addWidget(self.link_btn)

        self.link_peers_lbl = QLabel('Peers: 0')
        self.link_peers_lbl.setStyleSheet(f'font-size:11px;color:{TEXT_DIM};')
        link_top.addWidget(self.link_peers_lbl)
        link_top.addStretch()
        link_l.addLayout(link_top)

        bpm_row = QHBoxLayout()
        bpm_row.addWidget(QLabel('BPM'))
        self.link_bpm_spin = QSpinBox()
        self.link_bpm_spin.setRange(20, 999)
        self.link_bpm_spin.setValue(120)
        self.link_bpm_spin.setFixedWidth(68)
        self.link_bpm_spin.valueChanged.connect(lambda v: self.link.set_bpm(v))
        bpm_row.addWidget(self.link_bpm_spin)

        self.link_bpm_lbl = QLabel('120.0')
        self.link_bpm_lbl.setStyleSheet(f'font-size:11px;color:{ACCENT};font-family:{MONO_FONT};')
        bpm_row.addWidget(self.link_bpm_lbl)
        bpm_row.addStretch()
        link_l.addLayout(bpm_row)

        # Beat phase bar (shows position in bar)
        self.link_phase_bar = QSlider(Qt.Orientation.Horizontal)
        self.link_phase_bar.setRange(0, 1000)
        self.link_phase_bar.setValue(0)
        self.link_phase_bar.setEnabled(False)
        self.link_phase_bar.setToolTip('Beat phase — position in current bar')
        link_l.addWidget(self.link_phase_bar)

        if not self.link.available:
            na = QLabel('Link unavailable — run the app via run.sh (requires Python 3.12 venv)')
            na.setWordWrap(True)
            na.setStyleSheet(f'font-size:10px;color:{TEXT_DIM};')
            link_l.addWidget(na)
        else:
            link_hint = QLabel('Syncs BPM + beat with Ableton Live, Ableton Note,\n'
                               'and any Link-enabled app on the same WiFi network.')
            link_hint.setWordWrap(True)
            link_hint.setStyleSheet(f'font-size:10px;color:{TEXT_DIM};margin-top:2px;')
            link_l.addWidget(link_hint)

        midi_l.addWidget(link_grp)

        # ── CC MAPPING ────────────────────────────────────────────────
        cc_grp = QGroupBox('CC MAPPING  ·  right-click a knob to learn')
        cc_l   = QGridLayout(cc_grp)
        self._cc_labels = []
        for i in range(8):
            row_lbl = QLabel(f'P{i}:')
            row_lbl.setStyleSheet(f'font-size:10px;color:{TEXT_DIM};')
            cc_lbl  = QLabel('—')
            cc_lbl.setStyleSheet(f'font-size:10px;color:{ACCENT};font-family:{MONO_FONT};')
            clr_btn = QPushButton('X')
            clr_btn.setFixedSize(18, 18)
            clr_btn.setStyleSheet(f'font-size:9px;border:none;color:{TEXT_DIM};background:transparent;padding:0;')
            clr_btn.clicked.connect(lambda _, idx=i: self._clear_midi_map(idx))
            cc_l.addWidget(row_lbl, i, 0)
            cc_l.addWidget(cc_lbl,  i, 1)
            cc_l.addWidget(clr_btn, i, 2)
            self._cc_labels.append(cc_lbl)
        midi_l.addWidget(cc_grp)

        cv_grp = QGroupBox('CV GATE')
        cv_l   = QVBoxLayout(cv_grp)
        cv_note = QLabel('Transient detection → virtual CC 127 (Gate) + 126 (Pitch CV)')
        cv_note.setWordWrap(True)
        cv_note.setStyleSheet(f'font-size:10px;color:{TEXT_DIM};')
        cv_l.addWidget(cv_note)
        midi_l.addWidget(cv_grp)

        midi_l.addStretch()
        tabs.addTab(midi_widget, 'MIDI')

        # ── Video Editor tab ──────────────────────────────────────────
        self.vid_editor = VideoEditor(recordings_dir=self._rec_dir)
        self.vid_editor.frame_ready.connect(self._relay_video_frame)
        # Auto-switch to Video FX engine when playback starts
        self.vid_editor.player.playbackStateChanged.connect(self._on_video_playback_state)
        tabs.addTab(self.vid_editor, 'VIDEO EDITOR')

        # ── Camera tab ────────────────────────────────────────────────
        tabs.addTab(self._build_camera_tab(), 'CAMERA')

    def _build_camera_tab(self) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(10)

        # ── Source: Device ─────────────────────────────────────────────
        dev_grp = QGroupBox('DEVICE  (Continuity Camera / Webcam)')
        dev_l   = QVBoxLayout(dev_grp)

        dev_row = QHBoxLayout()
        self._cam_device_combo = QComboBox()
        self._cam_device_combo.addItem('— scan for cameras —')
        self._cam_device_combo.setStyleSheet('font-size:10px;')
        dev_row.addWidget(self._cam_device_combo, 1)

        scan_btn = QPushButton('Scan')
        scan_btn.setFixedWidth(46)
        scan_btn.clicked.connect(self._scan_cameras)
        dev_row.addWidget(scan_btn)
        dev_l.addLayout(dev_row)

        hint = QLabel(
            'iPhone via Continuity Camera appears here automatically.\n'
            'Requires macOS 13 + iOS 16, same Apple ID, Bluetooth on.'
        )
        hint.setWordWrap(True)
        hint.setStyleSheet(f'font-size:10px;color:{TEXT_DIM};margin-top:2px;')
        dev_l.addWidget(hint)

        start_dev_btn = QPushButton('Start Camera')
        start_dev_btn.setCheckable(True)
        start_dev_btn.clicked.connect(self._toggle_camera_device)
        self._cam_start_dev_btn = start_dev_btn
        dev_l.addWidget(start_dev_btn)
        layout.addWidget(dev_grp)

        # ── Source: Network / RTSP ─────────────────────────────────────
        net_grp = QGroupBox('NETWORK  (RTSP / WiFi streaming)')
        net_l   = QVBoxLayout(net_grp)

        self._cam_rtsp_edit = QLineEdit()
        self._cam_rtsp_edit.setPlaceholderText('rtsp://192.168.x.x:8554/live')
        self._cam_rtsp_edit.setStyleSheet(
            f'font-size:10px;font-family:{MONO_FONT};'
            f'background:{PANEL};border:1px solid {BORDER};'
            f'border-radius:4px;color:{TEXT};padding:4px 6px;'
        )
        net_l.addWidget(self._cam_rtsp_edit)

        rtsp_hint = QLabel(
            'iPhone apps: Larix Broadcaster (free), RTSP Camera Server.\n'
            'Open the app → tap Stream → enter the URL shown above.'
        )
        rtsp_hint.setWordWrap(True)
        rtsp_hint.setStyleSheet(f'font-size:10px;color:{TEXT_DIM};margin-top:2px;')
        net_l.addWidget(rtsp_hint)

        start_rtsp_btn = QPushButton('Connect Stream')
        start_rtsp_btn.setCheckable(True)
        start_rtsp_btn.clicked.connect(self._toggle_camera_rtsp)
        self._cam_start_rtsp_btn = start_rtsp_btn
        net_l.addWidget(start_rtsp_btn)
        layout.addWidget(net_grp)

        # ── Permission status ─────────────────────────────────────────
        self._cam_perm_lbl = QLabel('')
        self._cam_perm_lbl.setWordWrap(True)
        self._cam_perm_lbl.setStyleSheet(f'font-size:10px;color:{TEXT_DIM};')
        layout.addWidget(self._cam_perm_lbl)

        # ── Status ────────────────────────────────────────────────────
        self._cam_status_lbl = QLabel('Not connected')
        self._cam_status_lbl.setStyleSheet(f'font-size:11px;color:{TEXT_DIM};')
        self._cam_status_lbl.setWordWrap(True)
        layout.addWidget(self._cam_status_lbl)

        layout.addStretch()

        # Wire up engine signals
        self.camera.frame_ready.connect(self._relay_video_frame)
        self.camera.status_changed.connect(self._on_camera_status)
        self._scan_results_ready.connect(self._apply_camera_devices)

        # Auto-rescan when cameras connect/disconnect (e.g. Continuity Camera appearing)
        from PyQt6.QtMultimedia import QMediaDevices
        self._qt_media_devices = QMediaDevices(self)
        self._qt_media_devices.videoInputsChanged.connect(self._scan_cameras)

        # Request camera permission from the main thread on tab build
        QTimer.singleShot(0, self._check_camera_permission)

        return w

    def _scan_cameras(self):
        # Qt multimedia enumeration is instant — no background thread needed.
        devices = CameraEngine.scan_devices()
        self._apply_camera_devices(devices)

    def _apply_camera_devices(self, devices: list):
        self._cam_device_combo.clear()
        if not devices:
            self._cam_device_combo.addItem('No cameras found')
            self._cam_status_lbl.setText('No cameras found — check permissions')
            return
        for idx, name in devices:
            self._cam_device_combo.addItem(f'{name}  [{idx}]', userData=idx)
        self._cam_status_lbl.setText(f'Found {len(devices)} camera(s)')

    def _toggle_camera_device(self, checked: bool):
        if checked:
            idx_data = self._cam_device_combo.currentData()
            if idx_data is None:
                self._cam_start_dev_btn.setChecked(False)
                self._cam_status_lbl.setText('Scan for cameras first')
                return
            self._cam_start_rtsp_btn.setChecked(False)
            ok = self.camera.start_device(int(idx_data))
            if not ok:
                self._cam_start_dev_btn.setChecked(False)
            else:
                self._cam_start_dev_btn.setText('Stop Camera')
        else:
            self.camera.stop()
            self._cam_start_dev_btn.setText('Start Camera')
            self._cam_status_lbl.setText('Stopped')

    def _toggle_camera_rtsp(self, checked: bool):
        if checked:
            url = self._cam_rtsp_edit.text().strip()
            if not url:
                self._cam_start_rtsp_btn.setChecked(False)
                self._cam_status_lbl.setText('Enter an RTSP URL first')
                return
            self._cam_start_dev_btn.setChecked(False)
            ok = self.camera.start_rtsp(url)
            if not ok:
                self._cam_start_rtsp_btn.setChecked(False)
            else:
                self._cam_start_rtsp_btn.setText('Disconnect')
        else:
            self.camera.stop()
            self._cam_start_rtsp_btn.setText('Connect Stream')
            self._cam_status_lbl.setText('Stopped')

    def _on_camera_status(self, msg: str):
        self._cam_status_lbl.setText(msg)
        # Reset buttons if the stream died unexpectedly
        if msg in ('Stream ended or lost',):
            self._cam_start_dev_btn.setChecked(False)
            self._cam_start_dev_btn.setText('Start Camera')
            self._cam_start_rtsp_btn.setChecked(False)
            self._cam_start_rtsp_btn.setText('Connect Stream')

    def _check_camera_permission(self):
        status = CameraEngine.auth_status()
        if status == 'authorized':
            self._cam_perm_lbl.setText('Camera access: granted')
            self._scan_cameras()
        elif status == 'denied':
            self._cam_perm_lbl.setText(
                'Camera access denied — open System Settings > Privacy & Security > Camera'
                ' and allow this app, then restart.')
        elif status == 'not_determined':
            self._cam_perm_lbl.setText('Requesting camera permission…')
            CameraEngine.request_permission(self._on_camera_permission)
        else:
            self._cam_perm_lbl.setText(f'Camera access: {status}')

    def _on_camera_permission(self, granted: bool):
        # Callback fires on a background thread — bounce to main thread.
        QTimer.singleShot(0, lambda: self._apply_camera_permission(granted))

    def _apply_camera_permission(self, granted: bool):
        if granted:
            self._cam_perm_lbl.setText('Camera access: granted')
            # Auto-scan now that permission is confirmed.
            self._scan_cameras()
        else:
            self._cam_perm_lbl.setText(
                'Camera access denied — open System Settings > Privacy & Security > Camera'
                ' and allow this app, then restart.')

    def _build_output_toolbar(self) -> QWidget:
        bar = QWidget()
        bar.setFixedHeight(44)
        bar.setStyleSheet(f'background:{PANEL};border-radius:8px;')
        l   = QHBoxLayout(bar)
        l.setContentsMargins(10, 4, 10, 4)
        l.setSpacing(8)

        # Record
        self.rec_btn = QPushButton('REC')
        self.rec_btn.setCheckable(True)
        self.rec_btn.setStyleSheet(
            f'QPushButton{{background:{PANEL};border:1px solid {RED}40;color:{RED};border-radius:6px;padding:5px 14px;font-weight:600;}}'
            f'QPushButton:checked{{background:{RED}30;border-color:{RED};color:{RED};}}'
        )
        self.rec_btn.clicked.connect(self._toggle_record)
        l.addWidget(self.rec_btn)

        # Screenshot
        ss_btn = QPushButton('Screenshot')
        ss_btn.clicked.connect(self._screenshot)
        l.addWidget(ss_btn)

        # Fullscreen
        fs_btn = QPushButton('Fullscreen')
        fs_btn.clicked.connect(self._toggle_fullscreen)
        l.addWidget(fs_btn)

        # Output window
        self.out_btn = QPushButton('Output')
        self.out_btn.setCheckable(True)
        self.out_btn.setStyleSheet(
            f'QPushButton{{background:{PANEL};border:1px solid {BORDER};color:{TEXT};border-radius:6px;padding:5px 14px;}}'
            f'QPushButton:checked{{background:{ACCENT}25;border-color:{ACCENT};color:{ACCENT};}}'
        )
        self.out_btn.clicked.connect(self._toggle_output_window)
        l.addWidget(self.out_btn)

        l.addStretch()

        # FPS target
        fps_lbl = QLabel('FPS:')
        fps_lbl.setStyleSheet(f'font-size:10px;color:{TEXT_DIM};')
        l.addWidget(fps_lbl)
        fps_combo = QComboBox()
        fps_combo.addItems(['24', '30', '45', '60'])
        fps_combo.setCurrentText('30')
        fps_combo.setFixedWidth(46)
        fps_combo.setStyleSheet('font-size:10px;')
        fps_combo.currentTextChanged.connect(
            lambda v: self.canvas.set_target_fps(int(v)) if self.canvas else None
        )
        l.addWidget(fps_combo)

        # Render resolution scale
        scale_lbl = QLabel('Res:')
        scale_lbl.setStyleSheet(f'font-size:10px;color:{TEXT_DIM};')
        l.addWidget(scale_lbl)
        scale_combo = QComboBox()
        scale_combo.addItems(['50%', '75%', '100%'])
        scale_combo.setCurrentText('100%')
        scale_combo.setFixedWidth(54)
        scale_combo.setStyleSheet('font-size:10px;')
        _scale_map = {'50%': 0.5, '75%': 0.75, '100%': 1.0}
        scale_combo.currentTextChanged.connect(
            lambda v, m=_scale_map: self.canvas.set_render_scale(m[v]) if self.canvas else None
        )
        l.addWidget(scale_combo)

        # Mode label
        self.mode_lbl = QLabel(self._mode.upper())
        self.mode_lbl.setStyleSheet(f'font-size:11px;font-weight:700;color:{ACCENT};letter-spacing:1px;')
        l.addWidget(self.mode_lbl)

        # Beat indicator
        self.beat_dot = QLabel('●')
        self.beat_dot.setStyleSheet(f'font-size:16px;color:{TEXT_DIM};')
        l.addWidget(self.beat_dot)

        return bar

    # ── Mode change ───────────────────────────────────────────────────────────

    def _on_mode_change(self, mode: str):
        self._mode = mode
        self.canvas.set_mode(mode)
        self.param_panel.load_mode(mode)
        self._params = list(PARAM_DEFAULTS.get(mode, [0.5] * 8))
        self.canvas.set_params(self._params)
        self.mode_lbl.setText(mode.upper())
        self._update_mode_desc(mode)

    def _update_mode_desc(self, mode: str):
        descs = {
            'Lissajous':      'XY scan-line oscilloscope — EMS VCS3 / Rutt-Etra deflection',
            'Plasma':         'Interference pattern synthesis — LZX Mapper colorizer',
            'Ramp Colorizer': 'H/V ramp colorization — LZX Cadet / Visual Cortex',
            'Feedback':       'Video feedback loop — Paik-Abe / Jonas Bers style',
            'Kaleidoscope':   'Geometric symmetry engine — C&G EYESY mode',
            'Waveform 3D':    'FFT spectral landscape — oscilloscope / waterfall display',
            'Circuit Bent':   'Glitch datamosh — Sandin IP / circuit bending aesthetic',
            'Harmonic Web':   'Harmonic deflection lines — Rutt-Etra scan modulation',
            'Video FX':       'Live video effects — glitch, warp, RGB separation, edge detect',
        }
        self.mode_desc.setText(descs.get(mode, ''))

    def _on_video_active(self):
        """Switch to Video FX mode when the first video frame arrives."""
        try:
            self.mode_combo.setCurrentText('Video FX')
        except Exception as e:
            print(f'[video active] mode switch error: {e}')

    def _relay_video_frame(self, arr):
        """Route VideoEditor frames to the canvas, guaranteeing Video FX mode."""
        try:
            self.canvas.set_video_frame(arr)
            if self._mode != 'Video FX':
                self.mode_combo.setCurrentText('Video FX')
        except Exception as e:
            print(f'[relay] video frame error: {e}')

    def _on_video_playback_state(self, state):
        from PyQt6.QtMultimedia import QMediaPlayer
        if state == QMediaPlayer.PlaybackState.PlayingState:
            self.mode_combo.setCurrentText('Video FX')

    def _on_blend_layer_change(self, idx: int):
        if self.canvas is None:
            return
        engine = self._blend_eng_combos[idx].currentText()
        mode   = self._blend_mode_combos[idx].currentIndex()
        mix    = self._blend_mix_sliders[idx].value() / 100.0
        self.canvas.set_blend_layer(idx, engine, mix, mode)

    def _on_blend_param_change(self, idx: int):
        if self.canvas is None:
            return
        self.canvas.set_blend_layer_params(idx, self._blend_param_panels[idx].get_values())

    def _on_param_change(self, index: int, value: float):
        if 0 <= index < len(self._params):
            self._params[index] = value
            if self.canvas is not None:
                self.canvas.set_params(self._params)

    # ── Audio ─────────────────────────────────────────────────────────────────

    def _set_audio_source(self, src: str):
        if src == 'live':
            self.src_live_btn.setChecked(True)
            self.src_file_btn.setChecked(False)
            self.file_row.hide()
            self._transport_row.hide()
            idx = self.audio_device_combo.currentIndex() - 1
            self.audio.stop_file()
            self.audio.start_input(idx if idx >= 0 else None)
            self.status_lbl.setText('Live audio input active')
        else:
            self.src_live_btn.setChecked(False)
            self.src_file_btn.setChecked(True)
            self.file_row.show()
            self.audio.stop()

    def _detect_virtual_audio(self):
        _VIRTUAL_KEYWORDS = ('blackhole', 'soundflower', 'loopback',
                             'virtual', 'aggregate', 'multi-output')
        devs = self.audio.get_device_names()
        for i, d in enumerate(devs):
            if any(k in d.lower() for k in _VIRTUAL_KEYWORDS):
                self.audio_device_combo.setCurrentIndex(i + 1)  # +1 for "Default Input"
                self.src_live_btn.setChecked(True)
                self.src_file_btn.setChecked(False)
                self.file_row.hide()
                self._set_audio_source('live')
                self.status_lbl.setText(f'Virtual audio: {d}')
                return
        self.status_lbl.setText('No virtual audio device found — install BlackHole 2ch')

    def _on_audio_device_change(self, idx: int):
        if self.src_live_btn.isChecked():
            dev_idx = idx - 1
            self.audio.stop()
            self.audio.start_input(dev_idx if dev_idx >= 0 else None)

    def _refresh_audio_devices(self):
        devs = self.audio.refresh_devices()
        self.audio_device_combo.blockSignals(True)
        prev_text = self.audio_device_combo.currentText()
        self.audio_device_combo.clear()
        self.audio_device_combo.addItem('Default Input')
        _VIRTUAL_KEYWORDS = ('blackhole', 'soundflower', 'loopback',
                             'virtual', 'aggregate', 'multi-output')
        for d in devs:
            tag = '  [virtual]' if any(k in d.lower() for k in _VIRTUAL_KEYWORDS) else ''
            self.audio_device_combo.addItem(d[:36] + tag)
        # Restore previous selection if still present
        idx = self.audio_device_combo.findText(prev_text)
        self.audio_device_combo.setCurrentIndex(idx if idx >= 0 else 0)
        self.audio_device_combo.blockSignals(False)
        self.status_lbl.setText(f'Audio devices refreshed — {len(devs)} input(s) found')

    def _on_gain_change(self, val: int):
        gain = val / 100.0
        self.audio.set_gain(gain)
        self._gain_val_lbl.setText(f'{gain:.1f}×')

    def _browse_audio(self):
        path, _ = QFileDialog.getOpenFileName(
            self, 'Open Audio File', '',
            'Audio Files (*.mp3 *.wav *.flac *.ogg *.aac *.m4a *.aiff);;All Files (*)'
        )
        if path:
            self._current_audio_path = path
            self.file_lbl.setText(Path(path).name)
            self.audio.stop()
            self.audio.set_loop(self._loop_chk.isChecked())
            self.audio.play_file(path)
            self._play_btn.setChecked(True)
            self._play_btn.setText('Pause')
            self._scrub_slider.setValue(0)
            self._transport_row.show()
            self.status_lbl.setText(f'Playing: {Path(path).name}')

    def _audio_play_pause(self, checked: bool):
        if checked:
            if not self.audio.is_file_active():
                path = getattr(self, '_current_audio_path', None)
                if path:
                    self.audio.set_loop(self._loop_chk.isChecked())
                    self.audio.play_file(path)
                    self._scrub_slider.setValue(0)
            else:
                self.audio.resume_file()
            self._play_btn.setText('Pause')
        else:
            self.audio.pause_file()
            self._play_btn.setText('Play')

    def _audio_rewind(self):
        if self.audio.is_file_active():
            self.audio.seek_file(0.0)
            self.audio.resume_file()
        else:
            path = getattr(self, '_current_audio_path', None)
            if path:
                self.audio.set_loop(self._loop_chk.isChecked())
                self.audio.play_file(path)
        self._play_btn.setChecked(True)
        self._play_btn.setText('Pause')

    def _scrub_pressed(self):
        self._scrub_dragging = True

    def _scrub_released(self):
        self._scrub_dragging = False
        pos = self._scrub_slider.value() / 1000.0
        self.audio.seek_file(pos)

    def _update_transport(self):
        active = self.audio.is_file_active()
        if not active:
            # File finished (non-looping) — reset button if transport is visible
            if self._transport_row.isVisible() and self._play_btn.isChecked():
                self._play_btn.setChecked(False)
                self._play_btn.setText('Play')
            return
        cur, dur = self.audio.get_file_position()
        self._time_lbl.setText(f'{_fmt_t(cur)} / {_fmt_t(dur)}')
        if not self._scrub_dragging and dur > 0:
            self._scrub_slider.blockSignals(True)
            self._scrub_slider.setValue(int(cur / dur * 1000))
            self._scrub_slider.blockSignals(False)

    # ── MIDI ──────────────────────────────────────────────────────────────────

    def _on_midi_port_change(self, name: str):
        if name == '-- No MIDI --':
            self.midi.close()
            self.midi_status.setText('Not connected')
            return
        self.midi.open_port(name)
        self.midi_status.setText(f'Connected: {name[:30]}')
        self.midi.set_callback(cc_cb=self._on_midi_cc)

    def _refresh_midi_ports(self):
        self.midi_status.setText('Scanning…')
        self.midi.refresh_ports(callback=self._on_ports_updated)

    def _on_ports_updated(self, names: list):
        # Called from MIDI reader thread — schedule UI update on main thread
        QTimer.singleShot(0, lambda: self._apply_ports_update(names))

    def _apply_ports_update(self, names: list):
        current = self.midi_port_combo.currentText()
        self.midi_port_combo.blockSignals(True)
        self.midi_port_combo.clear()
        self.midi_port_combo.addItem('-- No MIDI --')
        for p in names:
            self.midi_port_combo.addItem(p[:40])
        # Restore previous selection if still present
        idx = self.midi_port_combo.findText(current)
        self.midi_port_combo.setCurrentIndex(max(0, idx))
        self.midi_port_combo.blockSignals(False)
        self.midi_status.setText(f'Found {len(names)} port(s)')

    # ── Ableton Link ──────────────────────────────────────────────────────────

    def _toggle_link(self, checked: bool):
        self.link.enabled = checked
        if checked:
            self.link.set_beat_callback(self._on_link_beat)
            self.midi_status.setText('Ableton Link active — waiting for peers…')
        else:
            self.link.set_beat_callback(None)
            self.midi_status.setText('Ableton Link disabled')

    def _on_link_beat(self, bpm: float):
        # Called on each bar downbeat from Link poll timer — inject beat into audio engine
        self.audio.inject_beat(1.0)

    def _start_link_poll(self):
        self._link_timer = QTimer(self)
        self._link_timer.timeout.connect(self._poll_link)
        self._link_timer.start(33)   # ~30 Hz

    def _poll_link(self):
        if not self.link.enabled:
            return
        peers, bpm, beat, phase = self.link.poll()
        self.link_peers_lbl.setText(f'Peers: {peers}')
        self.link_bpm_lbl.setText(f'{bpm:.1f}')
        self.link_phase_bar.setValue(int(phase * 1000))
        # Keep BPM spinbox in sync without triggering set_bpm feedback loop
        self.link_bpm_spin.blockSignals(True)
        self.link_bpm_spin.setValue(int(round(bpm)))
        self.link_bpm_spin.blockSignals(False)
        # Feed beat phase into canvas as a fast-pulsing beat signal
        if phase < 0.1:
            self.audio.inject_beat(1.0 - phase * 10)

    def _on_midi_cc(self, cc: int, value: float):
        # Called from MIDI reader thread — collect affected slots, then
        # marshal widget updates to the main thread via QTimer.singleShot.
        updates = []
        for slot, mapped_cc in list(self.midi._learn_map.items()):
            if mapped_cc == cc and slot < len(self._params):
                self._params[slot] = value
                updates.append(slot)
        if updates:
            self.canvas.set_params(self._params)
            QTimer.singleShot(0, lambda slots=updates, v=value: [
                self.param_panel.set_param_from_midi(s, v) for s in slots
            ])

    def _on_learn_complete(self, slot: int, cc: int):
        # Called from MIDI thread — schedule UI update on main thread
        QTimer.singleShot(0, lambda: self._apply_learn_complete(slot, cc))

    def _apply_learn_complete(self, slot: int, cc: int):
        if slot < len(self._cc_labels):
            self._cc_labels[slot].setText(f'CC {cc}')
        if slot < len(self.param_panel._knobs):
            self.param_panel._knobs[slot].set_midi_cc(cc)
            if self._midi_map_mode:
                self.status_lbl.setText(f'Mapped P{slot} → CC {cc}  —  click another knob or Cmd+K to exit')

    def _clear_midi_map(self, slot: int):
        self.midi.clear_mapping(slot)
        if slot < len(self._cc_labels):
            self._cc_labels[slot].setText('—')
        self.param_panel._knobs[slot].set_midi_cc(None)

    # ── Recording ─────────────────────────────────────────────────────────────

    def _toggle_record(self, checked: bool):
        if checked:
            ts   = datetime.now().strftime('%Y%m%d_%H%M%S')
            path = os.path.join(self._rec_dir, f'videolife_{ts}.mp4')
            dpr  = self.canvas.devicePixelRatio()
            w    = int(self.canvas.width()  * dpr)
            h    = int(self.canvas.height() * dpr)
            self.rec.start(path, fps=30, size=(w, h))
            self.rec_btn.setText('STOP')
            self.status_lbl.setText(f'Recording → {path}')
        else:
            self.rec.stop()
            self.rec_btn.setText('REC')
            self.status_lbl.setText('Recording saved')
            self.vid_editor.add_clip('')

    def _screenshot(self):
        frame = self.canvas.grab_frame()
        ts    = datetime.now().strftime('%Y%m%d_%H%M%S')
        path  = os.path.join(self._rec_dir, f'screenshot_{ts}.png')
        try:
            import cv2
            cv2.imwrite(path, cv2.cvtColor(frame, cv2.COLOR_RGB2BGR))
            self.status_lbl.setText(f'Screenshot saved: {Path(path).name}')
        except Exception as e:
            self.status_lbl.setText(f'Screenshot error: {e}')

    def _setup_shortcuts(self):
        QShortcut(QKeySequence('Ctrl+F'), self).activated.connect(self._toggle_fullscreen)
        QShortcut(QKeySequence(Qt.Key.Key_Escape), self).activated.connect(self._esc_pressed)
        QShortcut(QKeySequence('Ctrl+M'), self).activated.connect(self._toggle_midi_map_mode)
        QShortcut(QKeySequence('Ctrl+K'), self).activated.connect(self._toggle_kbd_midi_mode)
        QShortcut(QKeySequence('Ctrl+2'), self).activated.connect(
            lambda: self.out_btn.click()
        )

    def _esc_pressed(self):
        if self._kbd_midi_active:
            self._toggle_kbd_midi_mode()
        elif self._midi_map_mode:
            self._toggle_midi_map_mode()
        elif self.isFullScreen():
            self.showNormal()

    def _toggle_midi_map_mode(self):
        self._midi_map_mode = not self._midi_map_mode
        self.param_panel.set_map_mode(self._midi_map_mode)
        if self._midi_map_mode:
            self.status_lbl.setText('MIDI MAP MODE  —  click a knob, then move a controller  (Cmd+M or ESC to exit)')
        else:
            self.midi.cancel_learn()
            self.status_lbl.setText('MIDI map mode off')

    def _kbd_status(self) -> str:
        return (
            f'KEYBOARD MIDI  —  oct {self._kbd_octave}  vel {self._kbd_velocity}'
            f'  ·  Z/X = oct  C/V = vel  ·  Cmd+K or ESC to exit'
        )

    def _toggle_kbd_midi_mode(self):
        self._kbd_midi_active = not self._kbd_midi_active
        if self._kbd_midi_active:
            QApplication.instance().installEventFilter(self)
            self.status_lbl.setText(self._kbd_status())
        else:
            QApplication.instance().removeEventFilter(self)
            for note in list(self._kbd_held):
                self.midi.inject_note(note, 0.0)
            self._kbd_held.clear()
            self.status_lbl.setText('Keyboard MIDI off')

    def eventFilter(self, obj, event):
        if self._kbd_midi_active:
            t = event.type()
            if t == QEvent.Type.KeyPress and not event.isAutoRepeat():
                mods = event.modifiers() & ~Qt.KeyboardModifier.ShiftModifier
                if not mods and self._handle_kbd_midi_key(event.key(), True):
                    return True
            elif t == QEvent.Type.KeyRelease and not event.isAutoRepeat():
                mods = event.modifiers() & ~Qt.KeyboardModifier.ShiftModifier
                if not mods and self._handle_kbd_midi_key(event.key(), False):
                    return True
        return super().eventFilter(obj, event)

    def _handle_kbd_midi_key(self, key: Qt.Key, pressed: bool) -> bool:
        # ── Octave down / up (Z / X) ─────────────────────────────────────────
        if key == Qt.Key.Key_Z:
            if pressed:
                self._kbd_octave = max(0, self._kbd_octave - 1)
                self.status_lbl.setText(self._kbd_status())
            return True
        if key == Qt.Key.Key_X:
            if pressed:
                self._kbd_octave = min(8, self._kbd_octave + 1)
                self.status_lbl.setText(self._kbd_status())
            return True

        # ── Velocity down / up (C / V) ───────────────────────────────────────
        if key == Qt.Key.Key_C:
            if pressed:
                self._kbd_velocity = max(1, self._kbd_velocity - 20)
                self.status_lbl.setText(self._kbd_status())
            return True
        if key == Qt.Key.Key_V:
            if pressed:
                self._kbd_velocity = min(127, self._kbd_velocity + 20)
                self.status_lbl.setText(self._kbd_status())
            return True

        # ── Note keys ────────────────────────────────────────────────────────
        if key in _KBD_NOTE:
            note = 12 * self._kbd_octave + _KBD_NOTE[key]
            # Clamp to valid MIDI range 0–127
            note = max(0, min(127, note))
            if pressed:
                vel = self._kbd_velocity / 127.0
                self._kbd_held.add(note)
                self.audio.inject_beat(vel)
                self.midi.inject_note(note, vel)
            else:
                self._kbd_held.discard(note)
                self.midi.inject_note(note, 0.0)   # note-off
            return True

        return False

    def _toggle_fullscreen(self):
        if self.isFullScreen():
            self.showNormal()
        else:
            self.showFullScreen()

    # ── Output window ─────────────────────────────────────────────────────────

    def _toggle_output_window(self, checked: bool):
        if checked:
            if self._out_win is None:
                self._out_win = OutputWindow(
                    self.canvas,
                    frame_signal=self.vid_editor.frame_ready,
                )
                self._out_win.closed.connect(self._on_output_closed)
            self._out_win.resize(960, 540)
            self._out_win.show()
            self.canvas.set_output_active(True)
        else:
            if self._out_win is not None:
                self._out_win.close()

    def _on_output_closed(self):
        self.out_btn.setChecked(False)
        self.canvas.set_output_active(False)
        self._out_win = None

    # ── Presets ───────────────────────────────────────────────────────────────

    def _save_preset(self):
        path, _ = QFileDialog.getSaveFileName(
            self, 'Save Preset', self._preset_path, 'JSON Preset (*.json)'
        )
        if not path:
            return
        data = {
            'mode':   self._mode,
            'params': self._params,
            'cc_map': self.midi._learn_map,
        }
        with open(path, 'w') as f:
            json.dump(data, f, indent=2)
        self.status_lbl.setText(f'Preset saved: {Path(path).name}')

    def _load_preset(self):
        path, _ = QFileDialog.getOpenFileName(
            self, 'Load Preset', self._preset_path, 'JSON Preset (*.json)'
        )
        if not path:
            return
        try:
            with open(path) as f:
                data = json.load(f)
            self.mode_combo.setCurrentText(data.get('mode', self._mode))
            params = data.get('params', [0.5] * 8)
            for i, v in enumerate(params[:8]):
                self.param_panel.set_param_from_midi(i, v)
                if i < len(self._params):
                    self._params[i] = v
            self.canvas.set_params(self._params)
            cc_map = data.get('cc_map', {})
            self.midi._learn_map = {int(k): v for k, v in cc_map.items()}
            self.status_lbl.setText(f'Preset loaded: {Path(path).name}')
        except Exception as e:
            self.status_lbl.setText(f'Preset error: {e}')

    # ── Polling loops ─────────────────────────────────────────────────────────

    def _start_audio_poll(self):
        self._audio_timer = QTimer(self)
        self._audio_timer.timeout.connect(self._poll_audio)
        self._audio_timer.start(20)   # 50 Hz

    def _poll_audio(self):
        try:
            fft, rms, bass, mid, treble, beat = self.audio.get_data()
        except Exception:
            return

        # Clamp float values — NaN/Inf from FFT would raise ValueError inside Qt slots
        def _safe(v):
            return max(0.0, min(1.0, v)) if math.isfinite(v) else 0.0

        rms    = _safe(rms)
        bass   = _safe(bass)
        mid    = _safe(mid)
        treble = _safe(treble)
        beat   = _safe(beat)

        try:
            self.canvas.set_audio_data(fft, rms, bass, mid, treble, beat)
            self.scope.update_data(fft.tolist(), rms)

            # blockSignals prevents valueChanged from reaching any connected Python slots
            for widget, val in [
                (self.rms_bar,          rms),
                (self._bass_lbl_bar,    bass),
                (self._mid_lbl_bar,     mid),
                (self._treble_lbl_bar,  treble),
                (self._beat_lbl_bar,    beat),
            ]:
                widget.blockSignals(True)
                widget.setValue(int(val * 100))
                widget.blockSignals(False)

            self._bass_lbl.setText(f'{bass:.2f}')
            self._mid_lbl.setText(f'{mid:.2f}')
            self._treble_lbl.setText(f'{treble:.2f}')
            self._beat_lbl.setText(f'{beat:.2f}')

            if beat > 0.3:
                self.beat_dot.setStyleSheet(f'font-size:16px;color:{ACCENT};')
            else:
                self.beat_dot.setStyleSheet(f'font-size:16px;color:{TEXT_DIM};')

            self.midi.inject_cv_gate(beat, bass)
        except Exception:
            pass

    def _start_midi_poll(self):
        self._midi_timer = QTimer(self)
        self._midi_timer.timeout.connect(self._poll_midi)
        self._midi_timer.start(16)

    def _poll_midi(self):
        for slot, mapped_cc in list(self.midi._learn_map.items()):
            if slot < len(self._cc_labels):
                self._cc_labels[slot].setText(f'CC {mapped_cc}')
            if slot < 8:
                self.param_panel._knobs[slot].set_midi_cc(mapped_cc)

    # ── Cleanup ───────────────────────────────────────────────────────────────

    def closeEvent(self, e):
        self.audio.stop()
        self.midi.shutdown()
        self.camera.stop()
        if self.rec.is_recording():
            self.rec.stop()
        e.accept()


# ── Entry point ───────────────────────────────────────────────────────────────

def main():
    # Must be set before QApplication on macOS for Core Profile to apply everywhere,
    # and for texture sharing to work between widgets in different top-level windows.
    fmt = QSurfaceFormat()
    fmt.setVersion(3, 3)
    fmt.setProfile(QSurfaceFormat.OpenGLContextProfile.CoreProfile)
    fmt.setSwapInterval(1)
    QSurfaceFormat.setDefaultFormat(fmt)
    QApplication.setAttribute(Qt.ApplicationAttribute.AA_ShareOpenGLContexts)

    app = QApplication(sys.argv)
    app.setApplicationName('VIDEO LIFE')
    app.setApplicationDisplayName('VIDEO LIFE — Video Synthesizer')

    # Try native macOS style
    try:
        app.setStyle('macos')
    except Exception:
        pass

    win = MainWindow()
    win.show()
    sys.exit(app.exec())


if __name__ == '__main__':
    main()
