"""OpenGL rendering canvas — runs all GLSL synthesis shaders.

SynthCanvas extends GLBase (shared GL infrastructure) with Video Life's
9-engine synthesis architecture: single-engine rendering, Feedback ping-pong,
and a 3-layer blend chain with per-layer parameter control.
"""

from OpenGL.GL import *

from shaders    import SHADERS, VERT, BLEND_FRAG
from core.gl_base import GLBase


class SynthCanvas(GLBase):

    def __init__(self, parent=None):
        super().__init__(parent)

        # ── Synthesis state ───────────────────────────────────────────────────
        self._programs    = {}
        self._mode        = list(SHADERS.keys())[0]

        # Feedback ping-pong buffers
        self._fb_textures = [None, None]
        self._fb_fbos     = [None, None]
        self._fb_idx      = 0

        # Off-screen synth buffer (first pass of blend chain)
        self._synth_fbo  = None
        self._synth_tex  = None

        # Blend compositor
        self._blend_prog = None
        # 3 independent blend layers chained in order: output of N feeds into N+1
        self._blend_layers = [
            {'engine': None, 'mix': 0.0, 'mode': 0, 'params': [0.5] * 8}
            for _ in range(3)
        ]

        # 2 intermediate FBOs for ping-pong chaining between layers
        self._chain_fbos = [None, None]
        self._chain_texs = [None, None]

    # ── Qt OpenGL lifecycle ───────────────────────────────────────────────────

    def initShaders(self):
        """Compile all GLSL programs and allocate synthesis-specific buffers."""
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
        """Resize base infrastructure then rebuild synthesis-specific buffers."""
        super().resizeGL(w, h)
        dpr = self.devicePixelRatio()
        rw  = max(1, int(w * dpr * self._render_scale))
        rh  = max(1, int(h * dpr * self._render_scale))
        try:
            self._build_feedback_buffers(rw, rh)
            self._build_synth_buffer(rw, rh)
            self._build_chain_buffers(rw, rh)
        except Exception as e:
            print(f"[GL] SynthCanvas resizeGL error: {e}")

    # ── Frame rendering ───────────────────────────────────────────────────────

    def _paint_frame(self, t: float, w: int, h: int):
        """Render one synthesis frame into _dfbo()."""
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

            # Pass 2: blend(bg, layer_engine, fader) → chain FBO or output FBO
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

    def _compile_all(self):
        for name, frag_src in SHADERS.items():
            try:
                prog = self._link_program(VERT, frag_src)
                self._programs[name] = prog
                self._cache_locs(prog)
            except Exception as e:
                print(f"[GL] shader '{name}': {e}")

    # ── Public API ────────────────────────────────────────────────────────────

    def set_mode(self, mode: str):
        self._mode = mode

    def set_blend_layer(self, idx: int, engine, mix: float, mode: int):
        if 0 <= idx < len(self._blend_layers):
            self._blend_layers[idx]['engine'] = engine
            self._blend_layers[idx]['mix']    = float(mix)
            self._blend_layers[idx]['mode']   = int(mode)

    def set_blend_layer_params(self, idx: int, params: list):
        if 0 <= idx < len(self._blend_layers):
            self._blend_layers[idx]['params'] = list(params)[:8]
