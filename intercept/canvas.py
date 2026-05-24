"""
InterceptCanvas — OpenGL canvas for the INTERCEPT synthesizer.

Extends GLBase with:
  · Up to 3 concurrent source textures (u_src0, u_src1, u_src2)
  · Per-source has_src flags
  · Custom uniform caching for the extra source samplers
  · frame_available flag per slot so the GL thread only uploads on change
"""

import sys
import os

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import numpy as np
from OpenGL.GL import *

from core.gl_base import GLBase
from shaders import SHADERS, VERT

# Additional uniform names beyond the GLBase standard set
_EXTRA_UNIFORMS = [
    'u_src0', 'u_src1', 'u_src2',
    'u_has_src0', 'u_has_src1', 'u_has_src2',
]

_NUM_SRCS = 3


class InterceptCanvas(GLBase):

    def __init__(self, parent=None):
        super().__init__(parent)
        self._programs: dict[str, int] = {}
        self._mode = list(SHADERS.keys())[0]

        # Source textures — created lazily on first frame
        self._src_textures  = [None] * _NUM_SRCS
        self._src_pending   = [None] * _NUM_SRCS   # numpy RGB arrays
        self._src_active    = [False] * _NUM_SRCS

    # ── GLBase interface ──────────────────────────────────────────────────────

    def initShaders(self):
        for name, frag_src in SHADERS.items():
            try:
                prog = self._link_program(VERT, frag_src)
                self._programs[name] = prog
                self._cache_locs(prog)            # standard uniforms
                self._cache_extra_locs(prog)      # source uniforms
            except Exception as e:
                print(f"[IC] shader '{name}': {e}")
        print(f"[IC] compiled {len(self._programs)} engines: {list(self._programs)}")

    def _paint_frame(self, t: float, w: int, h: int):
        # Upload any pending source frames to GPU
        for i in range(_NUM_SRCS):
            self._upload_source(i)

        prog = self._programs.get(self._mode)
        if prog is None:
            glBindFramebuffer(GL_FRAMEBUFFER, self._dfbo())
            glClear(GL_COLOR_BUFFER_BIT)
            return

        glBindFramebuffer(GL_FRAMEBUFFER, self._dfbo())
        glViewport(0, 0, w, h)
        glUseProgram(prog)

        # Standard uniforms (time, resolution, audio, p[0-7])
        self._set_uniforms(prog, t, w, h)

        # Source textures — bind to units 2, 3, 4 (0 and 1 reserved by GLBase)
        locs = self._uniform_locs.get(prog, {})
        for i in range(_NUM_SRCS):
            unit = GL_TEXTURE2 + i
            glActiveTexture(unit)
            if self._src_textures[i] is not None and self._src_active[i]:
                glBindTexture(GL_TEXTURE_2D, self._src_textures[i])
                loc = locs.get(f'u_src{i}', -1)
                if loc >= 0:
                    glUniform1i(loc, 2 + i)
                loc = locs.get(f'u_has_src{i}', -1)
                if loc >= 0:
                    glUniform1i(loc, 1)
            else:
                # Bind blank tex so sampler is always valid
                if self._blank_tex:
                    glBindTexture(GL_TEXTURE_2D, self._blank_tex)
                loc = locs.get(f'u_has_src{i}', -1)
                if loc >= 0:
                    glUniform1i(loc, 0)

        glActiveTexture(GL_TEXTURE0)
        glBindVertexArray(self._vao)
        glDrawArrays(GL_TRIANGLE_STRIP, 0, 4)

    # ── Source texture management ─────────────────────────────────────────────

    def set_source_frame(self, slot: int, rgb_array: np.ndarray | None):
        """Called from the Qt main thread to push a new frame for a source slot."""
        if 0 <= slot < _NUM_SRCS:
            self._src_pending[slot] = rgb_array
            if rgb_array is not None:
                self._src_active[slot] = True

    def clear_source(self, slot: int):
        """Remove a source from a slot."""
        if 0 <= slot < _NUM_SRCS:
            self._src_pending[slot] = None
            self._src_active[slot]  = False

    def _upload_source(self, slot: int):
        arr = self._src_pending[slot]
        if arr is None:
            return
        self._src_pending[slot] = None
        try:
            h, w = arr.shape[:2]
            if self._src_textures[slot] is None:
                self._src_textures[slot] = glGenTextures(1)
                print(f"[IC] created texture for source {slot} ({w}×{h})")
            tex = self._src_textures[slot]
            glBindTexture(GL_TEXTURE_2D, tex)
            glTexImage2D(GL_TEXTURE_2D, 0, GL_RGB, w, h, 0,
                         GL_RGB, GL_UNSIGNED_BYTE, arr)
            glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, GL_LINEAR)
            glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MAG_FILTER, GL_LINEAR)
            glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_S, GL_CLAMP_TO_EDGE)
            glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_T, GL_CLAMP_TO_EDGE)
            glBindTexture(GL_TEXTURE_2D, 0)
        except Exception as e:
            print(f"[IC] source {slot} upload error: {e}")

    # ── Extra uniform caching ─────────────────────────────────────────────────

    def _cache_extra_locs(self, prog: int):
        locs = self._uniform_locs.setdefault(prog, {})
        for name in _EXTRA_UNIFORMS:
            loc = glGetUniformLocation(prog, name.encode())
            if loc >= 0:
                locs[name] = loc

    # ── Public API ────────────────────────────────────────────────────────────

    def set_mode(self, mode: str):
        if mode in SHADERS:
            self._mode = mode
