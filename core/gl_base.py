"""
GLBase — abstract OpenGL widget shared by all Video Life synthesizers.

Subclass contract
─────────────────
  class MySynth(GLBase):
      def initShaders(self):
          \"\"\"Compile GLSL programs and allocate synthesis-specific GL buffers.
          Called once from initializeGL, after the fullscreen quad and blank
          texture are ready and a valid GL context exists.\"\"\"

      def _paint_frame(self, t: float, w: int, h: int):
          \"\"\"Render one frame into _dfbo().
          t = elapsed seconds since start.
          w, h = render-target pixel dimensions (may be scaled by render_scale).\"\"\"

Inherited helpers available in subclasses
─────────────────────────────────────────
  self._dfbo()                  → current off-screen FBO handle
  self._link_program(vert, frag)→ compile + link GLSL program
  self._cache_locs(prog)        → cache uniform locations for prog
  self._set_uniforms(prog,t,w,h)→ upload all standard uniforms
  self._vao                     → fullscreen quad VAO
  self._blank_tex               → 1×1 black texture (safe fallback)
  self._uniform_locs            → dict[prog_id → dict[name → loc]]
  self._params, _fft, _rms …    → current audio / parameter state
"""

import time
import ctypes
from abc import abstractmethod

import numpy as np

from PyQt6.QtOpenGLWidgets import QOpenGLWidget
from PyQt6.QtCore          import QTimer, pyqtSignal
from PyQt6.QtGui           import QSurfaceFormat

from OpenGL.GL import *
from OpenGL.GL import shaders as glshaders


class GLBase(QOpenGLWidget):
    """Abstract OpenGL widget — provides all GL infrastructure; subclasses
    implement initShaders() and _paint_frame()."""

    fps_updated  = pyqtSignal(float)   # emitted every second with current FPS
    video_active = pyqtSignal()        # emitted on first video frame
    frame_ready  = pyqtSignal()        # emitted each frame when output window active

    # ── Uniform names cached for every compiled program ───────────────────────
    _UNIFORM_NAMES = [
        'u_time', 'u_resolution',
        'u_rms', 'u_bass', 'u_mid', 'u_treble', 'u_beat',
        'u_audio[0]',
        'u_prev', 'u_video', 'u_has_video',
        'u_bg', 'u_layer', 'u_mix', 'u_blend_mode',
    ] + [f'p[{i}]' for i in range(8)]

    def __init__(self, parent=None):
        fmt = QSurfaceFormat()
        fmt.setVersion(3, 3)
        fmt.setProfile(QSurfaceFormat.OpenGLContextProfile.CoreProfile)
        fmt.setSwapInterval(1)
        QSurfaceFormat.setDefaultFormat(fmt)
        super().__init__(parent)

        # ── Synthesis parameter state ─────────────────────────────────────────
        self._params  = [0.5] * 8
        self._fft     = np.zeros(512, dtype=np.float32)
        self._rms     = 0.0
        self._bass    = 0.0
        self._mid     = 0.0
        self._treble  = 0.0
        self._beat    = 0.0
        self._start   = time.time()

        # ── GL objects ────────────────────────────────────────────────────────
        self._vao       = None
        self._blank_tex = None   # 1×1 black fallback for unbound texture units

        # ── Video input ───────────────────────────────────────────────────────
        self._video_tex     = None
        self._has_video     = False
        self._pending_frame = None   # numpy RGB array, uploaded on next GL tick

        # ── Recorder ─────────────────────────────────────────────────────────
        self._recorder = None

        # ── Second-screen output texture (GPU-only copy, no CPU readback) ─────
        self._output_active         = False
        self._output_tex            = None
        self._output_fbo_out        = None
        self._output_tex_w          = 0
        self._output_tex_h          = 0
        self._output_tex_updated_at = 0.0

        # ── Custom off-screen render FBO ──────────────────────────────────────
        # Synthesis writes here rather than directly to Qt's compositing FBO.
        # This decouples the render loop from Qt's paint cycle, keeping
        # synthesis running even when macOS occludes the window.
        self._render_fbo   = None
        self._render_tex   = None
        self._render_fbo_w = 0
        self._render_fbo_h = 0
        self._fbo_w        = 0
        self._fbo_h        = 0

        # ── Render quality ────────────────────────────────────────────────────
        self._render_scale = 1.0

        # ── Uniform location cache ────────────────────────────────────────────
        self._uniform_locs: dict = {}

        # ── FPS tracking ──────────────────────────────────────────────────────
        self._frame_count = 0
        self._last_fps_t  = time.time()

        # ── Synthesis timer (independent of Qt paint cycle) ───────────────────
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._frame)
        self._timer.start(33)  # 30 fps default

    # ─────────────────────────────────────────────────────────────────────────
    # Qt OpenGL lifecycle
    # ─────────────────────────────────────────────────────────────────────────

    def initializeGL(self):
        # Drain any stale GL error flags Qt may have set
        while glGetError() != GL_NO_ERROR:
            pass
        try:
            glClearColor(0, 0, 0, 1)
        except Exception as e:
            print(f"[GL] glClearColor: {e}")
        try:
            self._build_quad()
        except Exception as e:
            print(f"[GL] _build_quad: {e}")
        try:
            self._blank_tex = glGenTextures(1)
            glBindTexture(GL_TEXTURE_2D, self._blank_tex)
            glTexImage2D(GL_TEXTURE_2D, 0, GL_RGBA, 1, 1, 0,
                         GL_RGBA, GL_UNSIGNED_BYTE,
                         np.zeros((1, 1, 4), dtype=np.uint8))
            glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, GL_NEAREST)
            glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MAG_FILTER, GL_NEAREST)
            glBindTexture(GL_TEXTURE_2D, 0)
        except Exception as e:
            print(f"[GL] blank_tex: {e}")
        try:
            self.initShaders()
        except Exception as e:
            print(f"[GL] initShaders: {e}")

    def resizeGL(self, w, h):
        # resizeGL receives logical pixels on macOS/PyQt6; scale to device pixels.
        dpr = self.devicePixelRatio()
        fw  = int(w * dpr)
        fh  = int(h * dpr)
        rw  = max(1, int(fw * self._render_scale))
        rh  = max(1, int(fh * self._render_scale))
        self._fbo_w = fw
        self._fbo_h = fh
        try:
            glViewport(0, 0, fw, fh)
            self._ensure_render_fbo(rw, rh)
            print(f"[GL] resizeGL: logical={w}×{h} device={fw}×{fh} "
                  f"render={rw}×{rh} dpr={dpr}")
        except Exception as e:
            print(f"[GL] resizeGL error: {e}")

    def paintGL(self):
        """Blit the pre-rendered _render_fbo to Qt's compositing FBO for display.
        Safe to skip (macOS occlusion) — synthesis is driven by _frame() instead."""
        try:
            if not self._render_fbo:
                return
            qt_fbo = self.defaultFramebufferObject()
            dpr    = self.devicePixelRatio()
            disp_w = int(self.width()  * dpr)
            disp_h = int(self.height() * dpr)
            glBindFramebuffer(GL_READ_FRAMEBUFFER, self._render_fbo)
            glBindFramebuffer(GL_DRAW_FRAMEBUFFER, qt_fbo)
            glBlitFramebuffer(
                0, 0, self._render_fbo_w, self._render_fbo_h,
                0, 0, disp_w, disp_h,
                GL_COLOR_BUFFER_BIT, GL_LINEAR,
            )
            glBindFramebuffer(GL_FRAMEBUFFER, qt_fbo)
        except Exception as e:
            print(f"[GL] paintGL: {e}")

    # ─────────────────────────────────────────────────────────────────────────
    # Abstract interface — subclasses MUST implement these
    # ─────────────────────────────────────────────────────────────────────────

    @abstractmethod
    def initShaders(self):
        """Compile GLSL programs and allocate synthesis-specific GL resources.
        Called once from initializeGL after the quad and blank texture exist."""

    @abstractmethod
    def _paint_frame(self, t: float, w: int, h: int):
        """Render one synthesis frame into _dfbo().
        t = elapsed seconds; w, h = render-target pixel size."""

    # ─────────────────────────────────────────────────────────────────────────
    # Rendering infrastructure
    # ─────────────────────────────────────────────────────────────────────────

    def _dfbo(self) -> int:
        """Return the active off-screen render FBO (falls back to Qt's FBO)."""
        return self._render_fbo if self._render_fbo else self.defaultFramebufferObject()

    def _ensure_render_fbo(self, w: int, h: int):
        """Create or resize the custom off-screen render FBO."""
        if self._render_fbo is not None and self._render_fbo_w == w and self._render_fbo_h == h:
            return
        if self._render_fbo is not None:
            glDeleteFramebuffers(1, [self._render_fbo])
            glDeleteTextures([self._render_tex])
        self._render_tex = int(glGenTextures(1))
        glBindTexture(GL_TEXTURE_2D, self._render_tex)
        glTexImage2D(GL_TEXTURE_2D, 0, GL_RGBA8, w, h, 0, GL_RGBA, GL_UNSIGNED_BYTE, None)
        glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, GL_LINEAR)
        glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MAG_FILTER, GL_LINEAR)
        glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_S, GL_CLAMP_TO_EDGE)
        glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_T, GL_CLAMP_TO_EDGE)
        glBindTexture(GL_TEXTURE_2D, 0)
        self._render_fbo = int(glGenFramebuffers(1))
        glBindFramebuffer(GL_FRAMEBUFFER, self._render_fbo)
        glFramebufferTexture2D(GL_FRAMEBUFFER, GL_COLOR_ATTACHMENT0,
                               GL_TEXTURE_2D, self._render_tex, 0)
        glClearColor(0.0, 0.0, 0.0, 1.0)
        glClear(GL_COLOR_BUFFER_BIT)
        glBindFramebuffer(GL_FRAMEBUFFER, 0)
        self._render_fbo_w = w
        self._render_fbo_h = h
        print(f"[GL] render FBO: {w}×{h}")

    def _frame(self):
        """Timer-driven synthesis tick.  Uses makeCurrent() directly so GL
        rendering is decoupled from Qt's paint cycle."""
        try:
            self.makeCurrent()
            try:
                dpr    = self.devicePixelRatio()
                disp_w = int(self.width()  * dpr)
                disp_h = int(self.height() * dpr)
                if disp_w > 0 and disp_h > 0:
                    rw = max(1, int(disp_w * self._render_scale))
                    rh = max(1, int(disp_h * self._render_scale))
                    self._ensure_render_fbo(rw, rh)
                    self._run_frame()
            finally:
                self.doneCurrent()
        except Exception as e:
            print(f"[GL] frame: {e}")
        self.update()   # schedule paintGL → blit render FBO to display

    def _run_frame(self):
        """Upload pending video, dispatch to subclass render, handle
        recorder capture, output texture, and FPS counter."""
        self._upload_pending_video()

        t   = time.time() - self._start
        dpr = self.devicePixelRatio()
        w   = max(1, int(self.width()  * dpr * self._render_scale))
        h   = max(1, int(self.height() * dpr * self._render_scale))

        self._paint_frame(t, w, h)

        # Frame capture for video recorder
        if self._recorder and self._recorder.is_recording():
            buf = glReadPixels(0, 0, w, h, GL_RGB, GL_UNSIGNED_BYTE)
            arr = np.frombuffer(buf, dtype=np.uint8).reshape(h, w, 3)
            self._recorder.push_frame(np.flipud(arr))

        # Blit to shared output texture (zero CPU transfer — pure GPU)
        if self._output_active:
            self._blit_to_output_tex(w, h)
            self.frame_ready.emit()

        # FPS counter
        self._frame_count += 1
        now = time.time()
        if now - self._last_fps_t >= 1.0:
            self.fps_updated.emit(self._frame_count / (now - self._last_fps_t))
            self._frame_count = 0
            self._last_fps_t  = now

    # ─────────────────────────────────────────────────────────────────────────
    # Video texture
    # ─────────────────────────────────────────────────────────────────────────

    def _upload_pending_video(self):
        arr = self._pending_frame
        if arr is None:
            return
        self._pending_frame = None
        try:
            h, w  = arr.shape[:2]
            first = self._video_tex is None
            if first:
                self._video_tex = glGenTextures(1)
                print(f"[GL] first video frame {w}x{h}")
            glBindTexture(GL_TEXTURE_2D, self._video_tex)
            glTexImage2D(GL_TEXTURE_2D, 0, GL_RGB, w, h, 0,
                         GL_RGB, GL_UNSIGNED_BYTE, arr)
            glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, GL_LINEAR)
            glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MAG_FILTER, GL_LINEAR)
            glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_S, GL_CLAMP_TO_EDGE)
            glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_T, GL_CLAMP_TO_EDGE)
            glBindTexture(GL_TEXTURE_2D, 0)
        except Exception as e:
            print(f"[GL] video upload: {e}")

    # ─────────────────────────────────────────────────────────────────────────
    # Second-screen output texture
    # ─────────────────────────────────────────────────────────────────────────

    def _blit_to_output_tex(self, w: int, h: int):
        """Copy the current render FBO into a shared GPU texture for the
        second-screen output window (no CPU readback)."""
        if self._output_tex_w != w or self._output_tex_h != h:
            if self._output_fbo_out is not None:
                glDeleteFramebuffers(1, [self._output_fbo_out])
            if self._output_tex is not None:
                glDeleteTextures([self._output_tex])
            self._output_tex = int(glGenTextures(1))
            glBindTexture(GL_TEXTURE_2D, self._output_tex)
            glTexImage2D(GL_TEXTURE_2D, 0, GL_RGBA8, w, h, 0, GL_RGBA, GL_UNSIGNED_BYTE, None)
            glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, GL_LINEAR)
            glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MAG_FILTER, GL_LINEAR)
            glBindTexture(GL_TEXTURE_2D, 0)
            self._output_fbo_out = int(glGenFramebuffers(1))
            glBindFramebuffer(GL_FRAMEBUFFER, self._output_fbo_out)
            glFramebufferTexture2D(GL_FRAMEBUFFER, GL_COLOR_ATTACHMENT0,
                                   GL_TEXTURE_2D, self._output_tex, 0)
            glBindFramebuffer(GL_FRAMEBUFFER, self._dfbo())
            self._output_tex_w = w
            self._output_tex_h = h

        glBindFramebuffer(GL_READ_FRAMEBUFFER, self._dfbo())
        glBindFramebuffer(GL_DRAW_FRAMEBUFFER, self._output_fbo_out)
        glBlitFramebuffer(0, 0, w, h, 0, 0, w, h, GL_COLOR_BUFFER_BIT, GL_NEAREST)
        err = glGetError()
        if err:
            print(f"[GL] blit error: {err:#x}")
        glBindFramebuffer(GL_FRAMEBUFFER, self._dfbo())
        self._output_tex_updated_at = time.time()

    # ─────────────────────────────────────────────────────────────────────────
    # Shader compilation helpers
    # ─────────────────────────────────────────────────────────────────────────

    def _link_program(self, vert_src: str, frag_src: str) -> int:
        """Compile vertex + fragment shaders and link into a GL program."""
        vert = glshaders.compileShader(vert_src, GL_VERTEX_SHADER)
        frag = glshaders.compileShader(frag_src, GL_FRAGMENT_SHADER)
        prog = glCreateProgram()
        glAttachShader(prog, vert)
        glAttachShader(prog, frag)
        # Explicit attribute locations match the VAO bindings in _build_quad
        glBindAttribLocation(prog, 0, b'in_position')
        glBindAttribLocation(prog, 1, b'in_uv')
        glLinkProgram(prog)
        glDeleteShader(vert)
        glDeleteShader(frag)
        if not glGetProgramiv(prog, GL_LINK_STATUS):
            info = glGetProgramInfoLog(prog)
            glDeleteProgram(prog)
            raise RuntimeError(info)
        return prog

    def _cache_locs(self, prog: int):
        """Pre-fetch all standard uniform locations for a compiled program."""
        locs = {}
        for name in self._UNIFORM_NAMES:
            loc = glGetUniformLocation(prog, name.encode())
            if loc >= 0:
                locs[name] = loc
        self._uniform_locs[prog] = locs

    # ─────────────────────────────────────────────────────────────────────────
    # Fullscreen quad
    # ─────────────────────────────────────────────────────────────────────────

    def _build_quad(self):
        """Build a fullscreen triangle-strip VAO (NDC coords + UV [0,1])."""
        verts = np.array([
            -1, -1,  0, 0,
             1, -1,  1, 0,
            -1,  1,  0, 1,
             1,  1,  1, 1,
        ], dtype=np.float32)

        self._vao = glGenVertexArrays(1)
        vbo       = glGenBuffers(1)
        glBindVertexArray(self._vao)
        glBindBuffer(GL_ARRAY_BUFFER, vbo)
        glBufferData(GL_ARRAY_BUFFER, verts.nbytes, verts, GL_STATIC_DRAW)
        glEnableVertexAttribArray(0)
        glVertexAttribPointer(0, 2, GL_FLOAT, GL_FALSE, 16, ctypes.c_void_p(0))
        glEnableVertexAttribArray(1)
        glVertexAttribPointer(1, 2, GL_FLOAT, GL_FALSE, 16, ctypes.c_void_p(8))
        glBindVertexArray(0)

    # ─────────────────────────────────────────────────────────────────────────
    # Uniform upload
    # ─────────────────────────────────────────────────────────────────────────

    def _set_uniforms(self, prog: int, t: float, w: int, h: int):
        """Upload all standard synthesis uniforms to the given program."""
        locs = self._uniform_locs.get(prog, {})

        loc = locs.get('u_time',       -1); loc >= 0 and glUniform1f(loc, t)
        loc = locs.get('u_resolution', -1); loc >= 0 and glUniform2f(loc, w, h)
        loc = locs.get('u_rms',        -1); loc >= 0 and glUniform1f(loc, self._rms)
        loc = locs.get('u_bass',       -1); loc >= 0 and glUniform1f(loc, self._bass)
        loc = locs.get('u_mid',        -1); loc >= 0 and glUniform1f(loc, self._mid)
        loc = locs.get('u_treble',     -1); loc >= 0 and glUniform1f(loc, self._treble)
        loc = locs.get('u_beat',       -1); loc >= 0 and glUniform1f(loc, self._beat)

        loc = locs.get('u_audio[0]', -1)
        if loc >= 0:
            glUniform1fv(loc, 512, self._fft)

        for i, v in enumerate(self._params[:8]):
            loc = locs.get(f'p[{i}]', -1)
            if loc >= 0:
                glUniform1f(loc, float(v))

        loc = locs.get('u_video', -1)
        if loc >= 0 and self._video_tex is not None:
            glActiveTexture(GL_TEXTURE1)
            glBindTexture(GL_TEXTURE_2D, self._video_tex)
            glUniform1i(loc, 1)
            glActiveTexture(GL_TEXTURE0)
        loc = locs.get('u_has_video', -1)
        if loc >= 0:
            glUniform1i(loc, 1 if (self._has_video and self._video_tex) else 0)

    # ─────────────────────────────────────────────────────────────────────────
    # Public API
    # ─────────────────────────────────────────────────────────────────────────

    def set_audio_data(self, fft, rms, bass, mid, treble, beat):
        """Feed current audio analysis results into the synthesis uniforms."""
        self._fft    = fft
        self._rms    = rms
        self._bass   = bass
        self._mid    = mid
        self._treble = treble
        self._beat   = beat

    def reset_clock(self):
        """Reset u_time to 0 — call on preset load so time-based oscillations
        start from the same phase in both Studio and VIDEO LIFE."""
        self._start = time.time()

    def set_params(self, params: list):
        """Set all 8 synthesis parameters at once."""
        self._params = list(params)[:8]

    def set_param(self, index: int, value: float):
        """Set a single synthesis parameter by index (0–7)."""
        if 0 <= index < 8:
            self._params[index] = value

    def set_recorder(self, recorder):
        """Attach a VideoRecorder instance to capture frames."""
        self._recorder = recorder

    def set_output_active(self, active: bool):
        """Enable/disable blitting to the shared output texture."""
        self._output_active = active

    def get_output_texture(self) -> int | None:
        """Return the GL texture ID of the shared output texture, or None."""
        return self._output_tex

    def set_video_frame(self, rgb_array: np.ndarray):
        """Push a new RGB numpy frame from the camera engine."""
        self._pending_frame = rgb_array
        if not self._has_video:
            self._has_video = True
            self.video_active.emit()

    def set_target_fps(self, fps: int):
        """Change the synthesis timer interval."""
        self._timer.setInterval(max(1, int(1000 / fps)))

    def set_render_scale(self, scale: float):
        """Set render resolution as a fraction of display resolution (0.25–1.0)."""
        self._render_scale = max(0.25, min(1.0, scale))
        # Invalidate cached FBO size so _frame rebuilds at new resolution
        self._render_fbo_w = 0
        self._render_fbo_h = 0

    def grab_frame(self) -> np.ndarray:
        """Read the current render FBO into a flipped RGB numpy array."""
        self.makeCurrent()
        try:
            if self._render_fbo:
                glBindFramebuffer(GL_READ_FRAMEBUFFER, self._render_fbo)
            dpr = self.devicePixelRatio()
            w   = int(self.width()  * dpr)
            h   = int(self.height() * dpr)
            buf = glReadPixels(0, 0, w, h, GL_RGB, GL_UNSIGNED_BYTE)
            arr = np.frombuffer(buf, dtype=np.uint8).reshape(h, w, 3)
            return np.flipud(arr)
        finally:
            self.doneCurrent()
