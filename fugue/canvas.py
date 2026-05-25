"""
FUGUE — OpenGL compositor canvas.

Manages 4 video texture slots and drives the compositing shader.
Extends GLBase so it inherits the fullscreen quad, FBO chain,
shader linker, and fps_updated signal.
"""

import sys
import os

import numpy as np
from OpenGL.GL import *

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from core.gl_base import GLBase
from fugue_shaders import VERT, FRAG

_N = 4   # number of video tracks

# Mixer-specific uniform names beyond GLBase's standard set
_MIXER_UNIFORMS = [
    'u_src0', 'u_src1', 'u_src2', 'u_src3',
    'u_has0', 'u_has1', 'u_has2', 'u_has3',
    'u_mix0', 'u_mix1', 'u_mix2', 'u_mix3',
    'u_mode0', 'u_mode1', 'u_mode2', 'u_mode3',
    'u_key_src', 'u_key_mode', 'u_key_thresh', 'u_key_soft',
    'u_chroma_col',
    'u_gain', 'u_sat', 'u_contrast', 'u_scanlines',
    # Difference key (background subtraction)
    'u_bg_sub_on', 'u_bg_fg_src', 'u_bg_bg_src',
    'u_bg_ref', 'u_has_bg_ref',
    'u_bg_thresh', 'u_bg_soft', 'u_bg_blur',
]


class FugueCanvas(GLBase):

    def __init__(self, parent=None):
        super().__init__(parent)
        self._prog     = None
        self._textures = [None] * _N   # GPU texture handles
        self._pending  = [None] * _N   # RGB ndarray waiting for upload
        self._active   = [False] * _N  # True = slot has live content

        # Per-track compositor state
        self._mix   = [1.0, 1.0, 1.0, 1.0]
        self._modes = [1,   0,   0,   0  ]   # A=Mix, B/C/D=Off

        # Key state
        self._key_src    = -1      # -1 = no keying
        self._key_mode   =  0      # 0=none 1=luma_dark 2=luma_bright 3=chroma
        self._key_thresh =  0.30
        self._key_soft   =  0.06
        self._chroma_col = [0.0, 0.8, 0.0]   # default: green

        # Output
        self._gain      = 0.60
        self._sat       = 1.00
        self._contrast  = 1.00
        self._scanlines = 0.00

        # Difference key (background subtraction)
        self._bg_ref_tex     = None    # GPU texture handle for the reference plate
        self._bg_ref_pending = None    # RGB ndarray waiting to be uploaded
        self._has_bg_ref     = False
        self._bg_sub_on      = False
        self._bg_fg_src      = 0       # subject track index
        self._bg_bg_src      = 1       # replacement background track index
        self._bg_thresh      = 0.12
        self._bg_soft        = 0.05
        self._bg_blur        = 2.0

    # ── GLBase contract ───────────────────────────────────────────────────────

    def initShaders(self):
        try:
            self._prog = self._link_program(VERT, FRAG)
            self._cache_locs(self._prog)
            locs = self._uniform_locs.setdefault(self._prog, {})
            for name in _MIXER_UNIFORMS:
                loc = glGetUniformLocation(self._prog, name.encode())
                if loc >= 0:
                    locs[name] = loc
            print(f'[Fugue] shader OK  ({len(locs)} uniforms)')
        except Exception as e:
            print(f'[Fugue] shader error: {e}')
            self._prog = None

    def _paint_frame(self, t: float, w: int, h: int):
        # Upload any frames that arrived since last paint
        for i in range(_N):
            self._upload(i)
        self._upload_ref()

        if self._prog is None:
            glBindFramebuffer(GL_FRAMEBUFFER, self._dfbo())
            glClearColor(0.05, 0.04, 0.0, 1.0)
            glClear(GL_COLOR_BUFFER_BIT)
            return

        glBindFramebuffer(GL_FRAMEBUFFER, self._dfbo())
        glViewport(0, 0, w, h)
        glClearColor(0.0, 0.0, 0.0, 1.0)
        glClear(GL_COLOR_BUFFER_BIT)
        glUseProgram(self._prog)

        # Standard GLBase uniforms (u_time, u_resolution; audio uniforms → 0)
        self._set_uniforms(self._prog, t, w, h)

        locs = self._uniform_locs.get(self._prog, {})

        def si(n, v):
            if n in locs: glUniform1i(locs[n], int(v))

        def sf(n, v):
            if n in locs: glUniform1f(locs[n], float(v))

        def sv3(n, a, b, c):
            if n in locs: glUniform3f(locs[n], float(a), float(b), float(c))

        # Bind video textures to units 2-5 (units 0-1 reserved by GLBase)
        for i in range(_N):
            glActiveTexture(GL_TEXTURE2 + i)
            active = self._textures[i] is not None and self._active[i]
            if active:
                glBindTexture(GL_TEXTURE_2D, self._textures[i])
            elif self._blank_tex:
                glBindTexture(GL_TEXTURE_2D, self._blank_tex)
            si(f'u_src{i}',  2 + i)
            si(f'u_has{i}',  1 if active else 0)
            sf(f'u_mix{i}',  self._mix[i])
            si(f'u_mode{i}', self._modes[i])

        # Key
        si('u_key_src',    self._key_src)
        si('u_key_mode',   self._key_mode)
        sf('u_key_thresh', self._key_thresh)
        sf('u_key_soft',   self._key_soft)
        sv3('u_chroma_col', *self._chroma_col)

        # Output
        sf('u_gain',      self._gain)
        sf('u_sat',       self._sat)
        sf('u_contrast',  self._contrast)
        sf('u_scanlines', self._scanlines)

        # Difference key — reference plate on unit 6
        glActiveTexture(GL_TEXTURE6)
        ref_ready = self._bg_ref_tex is not None and self._has_bg_ref
        if ref_ready:
            glBindTexture(GL_TEXTURE_2D, self._bg_ref_tex)
        elif self._blank_tex:
            glBindTexture(GL_TEXTURE_2D, self._blank_tex)
        si('u_bg_ref',     6)
        si('u_has_bg_ref', 1 if ref_ready else 0)
        si('u_bg_sub_on',  1 if self._bg_sub_on else 0)
        si('u_bg_fg_src',  self._bg_fg_src)
        si('u_bg_bg_src',  self._bg_bg_src)
        sf('u_bg_thresh',  self._bg_thresh)
        sf('u_bg_soft',    self._bg_soft)
        sf('u_bg_blur',    self._bg_blur)

        glActiveTexture(GL_TEXTURE0)
        glBindVertexArray(self._vao)
        glDrawArrays(GL_TRIANGLE_STRIP, 0, 4)

    # ── Frame upload ──────────────────────────────────────────────────────────

    def set_frame(self, slot: int, rgb: np.ndarray | None):
        """Push a new video frame (RGB uint8) for a slot. Safe from main thread."""
        if 0 <= slot < _N:
            self._pending[slot] = rgb
            if rgb is not None:
                self._active[slot] = True

    def clear_slot(self, slot: int):
        if 0 <= slot < _N:
            self._pending[slot] = None
            self._active[slot]  = False

    def _upload(self, slot: int):
        arr = self._pending[slot]
        if arr is None:
            return
        self._pending[slot] = None
        try:
            # OpenCV gives top→bottom rows; OpenGL expects bottom→top
            arr = np.ascontiguousarray(arr[::-1])
            h, w = arr.shape[:2]
            if self._textures[slot] is None:
                self._textures[slot] = int(glGenTextures(1))
            tex = self._textures[slot]
            glBindTexture(GL_TEXTURE_2D, tex)
            glTexImage2D(GL_TEXTURE_2D, 0, GL_RGB, w, h, 0,
                         GL_RGB, GL_UNSIGNED_BYTE, arr)
            for p, v in [(GL_TEXTURE_MIN_FILTER, GL_LINEAR),
                         (GL_TEXTURE_MAG_FILTER, GL_LINEAR),
                         (GL_TEXTURE_WRAP_S, GL_CLAMP_TO_EDGE),
                         (GL_TEXTURE_WRAP_T, GL_CLAMP_TO_EDGE)]:
                glTexParameteri(GL_TEXTURE_2D, p, v)
            glBindTexture(GL_TEXTURE_2D, 0)
        except Exception as e:
            print(f'[Fugue] upload slot {slot}: {e}')

    def _upload_ref(self):
        """Upload a pending reference plate to the GPU."""
        arr = self._bg_ref_pending
        if arr is None:
            return
        self._bg_ref_pending = None
        try:
            arr = np.ascontiguousarray(arr[::-1])   # y-flip: OpenCV → OpenGL
            h, w = arr.shape[:2]
            if self._bg_ref_tex is None:
                self._bg_ref_tex = int(glGenTextures(1))
            tex = self._bg_ref_tex
            glBindTexture(GL_TEXTURE_2D, tex)
            glTexImage2D(GL_TEXTURE_2D, 0, GL_RGB, w, h, 0,
                         GL_RGB, GL_UNSIGNED_BYTE, arr)
            for p, v in [(GL_TEXTURE_MIN_FILTER, GL_LINEAR),
                         (GL_TEXTURE_MAG_FILTER, GL_LINEAR),
                         (GL_TEXTURE_WRAP_S, GL_CLAMP_TO_EDGE),
                         (GL_TEXTURE_WRAP_T, GL_CLAMP_TO_EDGE)]:
                glTexParameteri(GL_TEXTURE_2D, p, v)
            glBindTexture(GL_TEXTURE_2D, 0)
            self._has_bg_ref = True
            print(f'[Fugue] reference plate stored ({w}×{h})')
        except Exception as e:
            print(f'[Fugue] upload ref: {e}')

    # ── Compositor parameters (called from UI) ────────────────────────────────

    def set_mix(self, slot: int, v: float):
        if 0 <= slot < _N:
            self._mix[slot] = float(v)

    def set_mode(self, slot: int, mode: int):
        if 0 <= slot < _N:
            self._modes[slot] = int(mode)

    def set_key(self, src: int, mode: int, thresh: float, soft: float):
        self._key_src    = src
        self._key_mode   = mode
        self._key_thresh = float(thresh)
        self._key_soft   = float(soft)

    def set_chroma_col(self, r: float, g: float, b: float):
        self._chroma_col = [r, g, b]

    def set_output(self, gain: float, sat: float, contrast: float,
                   scanlines: float = 0.0):
        self._gain      = float(gain)
        self._sat       = float(sat)
        self._contrast  = float(contrast)
        self._scanlines = float(scanlines)

    # ── Difference key / background subtraction ───────────────────────────────

    def grab_reference(self, rgb: np.ndarray):
        """Copy an RGB frame as the background reference plate (thread-safe)."""
        self._bg_ref_pending = np.array(rgb, copy=True)

    def clear_reference(self):
        """Discard the reference plate and disable BG sub."""
        self._has_bg_ref     = False
        self._bg_ref_pending = None
        self._bg_sub_on      = False

    def set_bg_sub(self, on: bool, fg_src: int, bg_src: int,
                   thresh: float, soft: float, blur: float):
        self._bg_sub_on  = bool(on)
        self._bg_fg_src  = max(0, min(3, int(fg_src)))
        self._bg_bg_src  = max(0, min(3, int(bg_src)))
        self._bg_thresh  = float(thresh)
        self._bg_soft    = float(soft)
        self._bg_blur    = float(blur)
