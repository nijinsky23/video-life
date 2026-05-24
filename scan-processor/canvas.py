"""
ScanCanvas — OpenGL rendering canvas for the Scan Processor.

Extends GLBase with a minimal engine dispatcher: compile all GLSL programs
from SHADERS, then on each frame simply bind + draw the active program.
No blend layers, no feedback ping-pong — just clean single-pass rendering.
"""

import sys
import os

# Ensure the video-synth root (parent of scan-processor/) is importable
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from OpenGL.GL import *

from core.gl_base import GLBase
from shaders import SHADERS, VERT


class ScanCanvas(GLBase):

    def __init__(self, parent=None):
        super().__init__(parent)
        self._programs: dict[str, int] = {}
        self._mode = list(SHADERS.keys())[0]

    # ── GLBase interface ──────────────────────────────────────────────────────

    def initShaders(self):
        for name, frag_src in SHADERS.items():
            try:
                prog = self._link_program(VERT, frag_src)
                self._programs[name] = prog
                self._cache_locs(prog)
            except Exception as e:
                print(f"[SP] shader '{name}': {e}")
        print(f"[SP] compiled {len(self._programs)} engines: {list(self._programs)}")

    def _paint_frame(self, t: float, w: int, h: int):
        prog = self._programs.get(self._mode)
        if prog is None:
            glBindFramebuffer(GL_FRAMEBUFFER, self._dfbo())
            glClear(GL_COLOR_BUFFER_BIT)
            return
        glBindFramebuffer(GL_FRAMEBUFFER, self._dfbo())
        glViewport(0, 0, w, h)
        glUseProgram(prog)
        self._set_uniforms(prog, t, w, h)
        glBindVertexArray(self._vao)
        glDrawArrays(GL_TRIANGLE_STRIP, 0, 4)

    # ── Public API ────────────────────────────────────────────────────────────

    def set_mode(self, mode: str):
        if mode in SHADERS:
            self._mode = mode
