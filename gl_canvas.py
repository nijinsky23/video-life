"""OpenGL rendering canvas — runs all GLSL synthesis shaders."""

import time
import ctypes
import numpy as np

from PyQt6.QtOpenGLWidgets import QOpenGLWidget
from PyQt6.QtCore    import QTimer, pyqtSignal
from PyQt6.QtGui     import QSurfaceFormat

from OpenGL.GL import *
from OpenGL.GL import shaders as glshaders

from shaders import SHADERS, VERT, BLEND_FRAG


class SynthCanvas(QOpenGLWidget):
    fps_updated  = pyqtSignal(float)
    video_active = pyqtSignal()
    frame_ready  = pyqtSignal()  # emitted each frame when output window is active

    def __init__(self, parent=None):
        fmt = QSurfaceFormat()
        fmt.setVersion(3, 3)
        fmt.setProfile(QSurfaceFormat.OpenGLContextProfile.CoreProfile)
        fmt.setSwapInterval(1)
        QSurfaceFormat.setDefaultFormat(fmt)
        super().__init__(parent)

        self._programs    = {}
        self._mode        = list(SHADERS.keys())[0]
        self._params      = [0.5] * 8
        self._fft         = np.zeros(512, dtype=np.float32)
        self._rms         = 0.0
        self._bass        = 0.0
        self._mid         = 0.0
        self._treble      = 0.0
        self._beat        = 0.0
        self._start       = time.time()
        self._vao         = None
        self._fb_textures = [None, None]
        self._fb_fbos     = [None, None]
        self._fb_idx      = 0
        self._recorder    = None

        self._video_tex     = None
        self._blank_tex     = None   # 1×1 black fallback for unbound texture units
        self._has_video     = False
        self._pending_frame = None

        self._synth_fbo  = None
        self._synth_tex  = None
        self._blend_prog = None
        # 3 independent blend layers chained in order: output of N feeds into N+1
        self._blend_layers = [{'engine': None, 'mix': 0.0, 'mode': 0, 'params': [0.5]*8} for _ in range(3)]
        # 2 intermediate FBOs for ping-pong chaining between layers
        self._chain_fbos = [None, None]
        self._chain_texs = [None, None]

        self._frame_count    = 0
        self._last_fps_t     = time.time()
        self._output_active        = False
        self._output_tex           = None
        self._output_fbo_out       = None
        self._output_tex_w         = 0
        self._output_tex_h         = 0
        self._output_tex_updated_at = 0.0

        # Custom off-screen render FBO — synthesis writes here, not to the Qt
        # compositing FBO. Created lazily in _ensure_render_fbo().
        self._render_fbo   = None
        self._render_tex   = None
        self._render_fbo_w = 0
        self._render_fbo_h = 0

        self._render_scale  = 1.0
        self._uniform_locs: dict = {}

        self._timer = QTimer(self)
        # _frame() uses makeCurrent() so synthesis keeps running even when
        # macOS occludes the main window behind a fullscreen output window.
        self._timer.timeout.connect(self._frame)
        self._timer.start(33)  # 30 fps default

    # ── Qt OpenGL lifecycle ───────────────────────────────────────────────────

    def initializeGL(self):
        # Drain any stale GL error flags Qt may have left
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
            self._compile_all()
            print(f"[GL] compiled {len(self._programs)} shaders: {list(self._programs)}")
        except Exception as e:
            print(f"[GL] _compile_all: {e}")
        try:
            self._build_feedback_buffers()
        except Exception as e:
            print(f"[GL] _build_feedback_buffers: {e}")
        try:
            self._build_synth_buffer()
        except Exception as e:
            print(f"[GL] _build_synth_buffer: {e}")
        try:
            self._build_chain_buffers()
        except Exception as e:
            print(f"[GL] _build_chain_buffers: {e}")
        try:
            self._compile_blend()
        except Exception as e:
            print(f"[GL] _compile_blend: {e}")

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
            self._build_feedback_buffers(rw, rh)
            self._build_synth_buffer(rw, rh)
            self._build_chain_buffers(rw, rh)
            print(f"[GL] resizeGL: logical={w}×{h} device={fw}×{fh} render={rw}×{rh} dpr={dpr}")
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

    # ── Rendering ─────────────────────────────────────────────────────────────

    def _dfbo(self):
        """Custom off-screen render FBO — decoupled from the native window surface
        so synthesis keeps running even when macOS occludes the main window."""
        return self._render_fbo if self._render_fbo else self.defaultFramebufferObject()

    def _ensure_render_fbo(self, w, h):
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
        """Timer-driven synthesis tick.  Uses makeCurrent() directly so GL rendering
        is decoupled from Qt's paint cycle and survives macOS window occlusion."""
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
                    self._paint()
            finally:
                self.doneCurrent()
        except Exception as e:
            print(f"[GL] frame: {e}")
        self.update()  # schedule paintGL to display _render_fbo in the main window

    def _paint(self):
        self._upload_pending_video()

        t   = time.time() - self._start
        dpr = self.devicePixelRatio()
        w   = max(1, int(self.width()  * dpr * self._render_scale))
        h   = max(1, int(self.height() * dpr * self._render_scale))

        any_blend_active = (
            any(l['mix'] > 0.0 for l in self._blend_layers)
            and self._blend_prog is not None
            and self._synth_fbo is not None
        )

        if any_blend_active:
            self._render_blend_chain(t, w, h)
        elif self._mode == "Feedback":
            prog = self._programs.get(self._mode)
            if prog is not None:
                self._render_feedback(prog, t, w, h)
            else:
                glBindFramebuffer(GL_FRAMEBUFFER, self._dfbo())
                glClear(GL_COLOR_BUFFER_BIT)
        else:
            prog = self._programs.get(self._mode)
            if prog is None:
                glBindFramebuffer(GL_FRAMEBUFFER, self._dfbo())
                glClear(GL_COLOR_BUFFER_BIT)
            else:
                glBindFramebuffer(GL_FRAMEBUFFER, self._dfbo())
                glViewport(0, 0, w, h)
                glUseProgram(prog)
                self._set_uniforms(prog, t, w, h)
                glBindVertexArray(self._vao)
                glDrawArrays(GL_TRIANGLE_STRIP, 0, 4)

        # Capture for recorder
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

    # ── Video texture ─────────────────────────────────────────────────────────

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

    # ── Feedback ping-pong ────────────────────────────────────────────────────

    def _build_feedback_buffers(self, w=1280, h=720):
        for fbo in self._fb_fbos:
            if fbo is not None:
                glDeleteFramebuffers(1, [fbo])
        for tex in self._fb_textures:
            if tex is not None:
                glDeleteTextures([tex])

        self._fb_textures = []
        self._fb_fbos     = []
        for _ in range(2):
            tex = glGenTextures(1)
            glBindTexture(GL_TEXTURE_2D, tex)
            glTexImage2D(GL_TEXTURE_2D, 0, GL_RGB, w, h, 0,
                         GL_RGB, GL_UNSIGNED_BYTE, None)
            glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, GL_LINEAR)
            glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MAG_FILTER, GL_LINEAR)
            glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_S, GL_CLAMP_TO_EDGE)
            glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_T, GL_CLAMP_TO_EDGE)

            fbo = glGenFramebuffers(1)
            glBindFramebuffer(GL_FRAMEBUFFER, fbo)
            glFramebufferTexture2D(GL_FRAMEBUFFER, GL_COLOR_ATTACHMENT0,
                                   GL_TEXTURE_2D, tex, 0)
            self._fb_textures.append(tex)
            self._fb_fbos.append(fbo)

        glBindFramebuffer(GL_FRAMEBUFFER, self._dfbo())

    def _render_feedback(self, prog, t, w, h):
        cur  = self._fb_idx
        prev = 1 - cur

        glBindFramebuffer(GL_FRAMEBUFFER, self._fb_fbos[cur])
        glViewport(0, 0, w, h)
        glUseProgram(prog)
        self._set_uniforms(prog, t, w, h)

        loc = self._uniform_locs.get(prog, {}).get('u_prev', -1)
        if loc >= 0:
            glActiveTexture(GL_TEXTURE0)
            glBindTexture(GL_TEXTURE_2D, self._fb_textures[prev])
            glUniform1i(loc, 0)

        glBindVertexArray(self._vao)
        glDrawArrays(GL_TRIANGLE_STRIP, 0, 4)

        # Blit to Qt's compositing FBO (not system framebuffer 0)
        glBindFramebuffer(GL_READ_FRAMEBUFFER, self._fb_fbos[cur])
        glBindFramebuffer(GL_DRAW_FRAMEBUFFER, self._dfbo())
        glBlitFramebuffer(0, 0, w, h, 0, 0, w, h, GL_COLOR_BUFFER_BIT, GL_LINEAR)
        glBindFramebuffer(GL_FRAMEBUFFER, self._dfbo())

        self._fb_idx = prev

    # ── Synthesis + blend two-pass ────────────────────────────────────────────

    def _build_synth_buffer(self, w=1280, h=720):
        if self._synth_fbo is not None:
            glDeleteFramebuffers(1, [self._synth_fbo])
        if self._synth_tex is not None:
            glDeleteTextures([self._synth_tex])

        self._synth_tex = glGenTextures(1)
        glBindTexture(GL_TEXTURE_2D, self._synth_tex)
        glTexImage2D(GL_TEXTURE_2D, 0, GL_RGB, w, h, 0,
                     GL_RGB, GL_UNSIGNED_BYTE, None)
        glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, GL_LINEAR)
        glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MAG_FILTER, GL_LINEAR)
        glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_S, GL_CLAMP_TO_EDGE)
        glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_T, GL_CLAMP_TO_EDGE)

        self._synth_fbo = glGenFramebuffers(1)
        glBindFramebuffer(GL_FRAMEBUFFER, self._synth_fbo)
        glFramebufferTexture2D(GL_FRAMEBUFFER, GL_COLOR_ATTACHMENT0,
                               GL_TEXTURE_2D, self._synth_tex, 0)
        glBindFramebuffer(GL_FRAMEBUFFER, self._dfbo())

    def _compile_blend(self):
        self._blend_prog = self._link_program(VERT, BLEND_FRAG)
        self._cache_locs(self._blend_prog)
        print("[GL] blend compositor compiled")

    def _build_chain_buffers(self, w=1280, h=720):
        for fbo in self._chain_fbos:
            if fbo is not None:
                glDeleteFramebuffers(1, [fbo])
        for tex in self._chain_texs:
            if tex is not None:
                glDeleteTextures([tex])

        self._chain_fbos = []
        self._chain_texs = []
        for _ in range(2):
            tex = glGenTextures(1)
            glBindTexture(GL_TEXTURE_2D, tex)
            glTexImage2D(GL_TEXTURE_2D, 0, GL_RGB, w, h, 0,
                         GL_RGB, GL_UNSIGNED_BYTE, None)
            glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, GL_LINEAR)
            glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MAG_FILTER, GL_LINEAR)
            glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_S, GL_CLAMP_TO_EDGE)
            glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_T, GL_CLAMP_TO_EDGE)

            fbo = glGenFramebuffers(1)
            glBindFramebuffer(GL_FRAMEBUFFER, fbo)
            glFramebufferTexture2D(GL_FRAMEBUFFER, GL_COLOR_ATTACHMENT0,
                                   GL_TEXTURE_2D, tex, 0)
            self._chain_texs.append(tex)
            self._chain_fbos.append(fbo)

        glBindFramebuffer(GL_FRAMEBUFFER, self._dfbo())

    def _render_blend_chain(self, t, w, h):
        if not self._chain_fbos or self._chain_fbos[0] is None:
            return

        bp     = self._blend_prog
        active = [l for l in self._blend_layers if l['mix'] > 0.0]
        if not active:
            return

        fbo_w = getattr(self, '_fbo_w', None)
        fbo_h = getattr(self, '_fbo_h', None)
        if not getattr(self, '_chain_diag_printed', False):
            print(f"[GL] blend chain: paint w×h={w}×{h}  FBO w×h={fbo_w}×{fbo_h}  "
                  f"dpr={self.devicePixelRatio()}")
            self._chain_diag_printed = True

        # ── Step 0: render main engine as the base signal ────────────────────
        if self._mode == 'Feedback':
            # Feedback needs its own ping-pong buffers; render there and use
            # the result texture as the chain base.
            cur  = self._fb_idx
            prev = 1 - cur
            prog = self._programs.get('Feedback')
            if prog:
                glBindFramebuffer(GL_FRAMEBUFFER, self._fb_fbos[cur])
                glViewport(0, 0, w, h)
                glUseProgram(prog)
                self._set_uniforms(prog, t, w, h)
                loc = self._uniform_locs.get(prog, {}).get('u_prev', -1)
                if loc >= 0:
                    glActiveTexture(GL_TEXTURE0)
                    glBindTexture(GL_TEXTURE_2D, self._fb_textures[prev])
                    glUniform1i(loc, 0)
                glBindVertexArray(self._vao)
                glDrawArrays(GL_TRIANGLE_STRIP, 0, 4)
                self._fb_idx = prev
                bg_tex = self._fb_textures[cur]
            else:
                bg_tex = self._blank_tex or 0
            chain_idx = 0      # first layer writes to chain[0]
        else:
            prog = self._programs.get(self._mode)
            if prog:
                glBindFramebuffer(GL_FRAMEBUFFER, self._chain_fbos[0])
                glViewport(0, 0, w, h)
                glUseProgram(prog)
                self._set_uniforms(prog, t, w, h)
                glBindVertexArray(self._vao)
                glDrawArrays(GL_TRIANGLE_STRIP, 0, 4)
            else:
                glBindFramebuffer(GL_FRAMEBUFFER, self._chain_fbos[0])
                glClearColor(0.0, 0.0, 0.0, 1.0)
                glClear(GL_COLOR_BUFFER_BIT)
            bg_tex    = self._chain_texs[0]
            chain_idx = 1      # first layer writes to chain[1]

        # ── Layer chain: each engine mixes into the accumulating signal ───────
        for i, layer in enumerate(active):
            is_last = (i == len(active) - 1)

            # Pass 1: render this layer's engine → offscreen synth FBO
            engine_name = layer['engine'] if layer['engine'] else self._mode
            prog = self._programs.get(engine_name) or self._programs.get(self._mode)
            if prog is not None:
                glBindFramebuffer(GL_FRAMEBUFFER, self._synth_fbo)
                glViewport(0, 0, w, h)
                glUseProgram(prog)
                saved, self._params = self._params, layer.get('params', [0.5] * 8)
                self._set_uniforms(prog, t, w, h)
                self._params = saved
                if engine_name == 'Feedback':
                    # Use the feedback prev-frame texture (read-only; don't advance idx)
                    loc = self._uniform_locs.get(prog, {}).get('u_prev', -1)
                    if loc >= 0:
                        glActiveTexture(GL_TEXTURE0)
                        glBindTexture(GL_TEXTURE_2D, self._fb_textures[1 - self._fb_idx])
                        glUniform1i(loc, 0)
                glBindVertexArray(self._vao)
                glDrawArrays(GL_TRIANGLE_STRIP, 0, 4)

            # Pass 2: blend(bg, layer_engine, fader) → chain FBO or Qt FBO
            target_fbo = self._dfbo() if is_last else self._chain_fbos[chain_idx]
            glBindFramebuffer(GL_FRAMEBUFFER, target_fbo)
            glViewport(0, 0, w, h)
            glUseProgram(bp)

            _bpl = self._uniform_locs.get(bp, {})
            glActiveTexture(GL_TEXTURE0)
            glBindTexture(GL_TEXTURE_2D, bg_tex)
            loc = _bpl.get('u_bg', -1);         loc >= 0 and glUniform1i(loc, 0)

            glActiveTexture(GL_TEXTURE1)
            glBindTexture(GL_TEXTURE_2D, self._synth_tex)
            loc = _bpl.get('u_layer', -1);      loc >= 0 and glUniform1i(loc, 1)

            loc = _bpl.get('u_mix', -1);        loc >= 0 and glUniform1f(loc, layer['mix'])
            loc = _bpl.get('u_blend_mode', -1); loc >= 0 and glUniform1i(loc, layer['mode'])

            glBindVertexArray(self._vao)
            glDrawArrays(GL_TRIANGLE_STRIP, 0, 4)
            glActiveTexture(GL_TEXTURE0)

            if not is_last:
                bg_tex    = self._chain_texs[chain_idx]
                chain_idx = 1 - chain_idx

    # ── Shader compilation ────────────────────────────────────────────────────

    _UNIFORM_NAMES = [
        'u_time', 'u_resolution', 'u_rms', 'u_bass', 'u_mid', 'u_treble', 'u_beat',
        'u_audio[0]', 'u_prev', 'u_video', 'u_has_video',
        'u_bg', 'u_layer', 'u_mix', 'u_blend_mode',
    ] + [f'p[{i}]' for i in range(8)]

    def _cache_locs(self, prog: int):
        locs = {}
        for name in self._UNIFORM_NAMES:
            loc = glGetUniformLocation(prog, name.encode())
            if loc >= 0:
                locs[name] = loc
        self._uniform_locs[prog] = locs

    def _compile_all(self):
        for name, frag_src in SHADERS.items():
            try:
                prog = self._link_program(VERT, frag_src)
                self._programs[name] = prog
                self._cache_locs(prog)
            except Exception as e:
                print(f"[GL] shader '{name}': {e}")

    def _link_program(self, vert_src: str, frag_src: str) -> int:
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

    # ── Fullscreen quad ───────────────────────────────────────────────────────

    def _build_quad(self):
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

    # ── Uniform upload ────────────────────────────────────────────────────────

    def _set_uniforms(self, prog, t, w, h):
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

    # ── Public API ────────────────────────────────────────────────────────────

    def set_mode(self, mode: str):
        self._mode = mode

    def set_params(self, params: list):
        self._params = list(params)[:8]

    def set_param(self, index: int, value: float):
        if 0 <= index < 8:
            self._params[index] = value

    def set_audio_data(self, fft, rms, bass, mid, treble, beat):
        self._fft    = fft
        self._rms    = rms
        self._bass   = bass
        self._mid    = mid
        self._treble = treble
        self._beat   = beat

    def set_recorder(self, recorder):
        self._recorder = recorder

    def set_output_active(self, active: bool):
        self._output_active = active

    def get_output_texture(self) -> int | None:
        return self._output_tex

    def _blit_to_output_tex(self, w: int, h: int):
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

    def set_video_frame(self, rgb_array: np.ndarray):
        self._pending_frame = rgb_array
        if not self._has_video:
            self._has_video = True
            self.video_active.emit()

    def set_blend_layer(self, idx: int, engine, mix: float, mode: int):
        if 0 <= idx < len(self._blend_layers):
            self._blend_layers[idx]['engine'] = engine
            self._blend_layers[idx]['mix']    = float(mix)
            self._blend_layers[idx]['mode']   = int(mode)

    def set_blend_layer_params(self, idx: int, params: list):
        if 0 <= idx < len(self._blend_layers):
            self._blend_layers[idx]['params'] = list(params)[:8]

    def set_target_fps(self, fps: int):
        self._timer.setInterval(max(1, int(1000 / fps)))

    def set_render_scale(self, scale: float):
        self._render_scale = max(0.25, min(1.0, scale))
        # Invalidate cached FBO size so _frame rebuilds at new resolution
        self._render_fbo_w = 0
        self._render_fbo_h = 0

    def grab_frame(self) -> np.ndarray:
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
