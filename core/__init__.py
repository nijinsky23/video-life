"""
video-life-core
───────────────
Shared infrastructure for all Video Life synthesizers.

Quick start for a new synthesizer:

    from core import GLBase, AudioEngine, MidiEngine, VideoRecorder

    class MySynth(GLBase):
        def initShaders(self):
            self._prog = self._link_program(VERT, MY_FRAG)
            self._cache_locs(self._prog)

        def _paint_frame(self, t, w, h):
            from OpenGL.GL import *
            glBindFramebuffer(GL_FRAMEBUFFER, self._dfbo())
            glViewport(0, 0, w, h)
            glUseProgram(self._prog)
            self._set_uniforms(self._prog, t, w, h)
            glBindVertexArray(self._vao)
            glDrawArrays(GL_TRIANGLE_STRIP, 0, 4)
"""

from .gl_base       import GLBase
from .audio_engine  import AudioEngine
from .midi_engine   import MidiEngine
from .link_engine   import LinkEngine
from .recorder      import VideoRecorder
from .camera_engine import CameraEngine

__all__ = [
    'GLBase',
    'AudioEngine',
    'MidiEngine',
    'LinkEngine',
    'VideoRecorder',
    'CameraEngine',
]
