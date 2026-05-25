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

        # Full Studio style params for the Tiamat engine
        self._intercept_bp = {}

        # Per-layer ping-pong FBOs for Tiamat used as a blend layer.
        # 3 slots × 2 ping-pong buffers each — fully independent from the
        # main-engine feedback FBOs so they don't corrupt each other.
        self._ic_layer_texs: list[list] = [[None, None], [None, None], [None, None]]
        self._ic_layer_fbos: list[list] = [[None, None], [None, None], [None, None]]
        self._ic_layer_idx:  list[int]  = [0, 0, 0]

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
        try:
            self._build_ic_layer_buffers()
        except Exception as e:
            print(f"[GL] _build_ic_layer_buffers: {e}")

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
            self._build_ic_layer_buffers(rw, rh)
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
        elif self._mode == "Tiamat":
            prog = self._programs.get(self._mode)
            if prog is not None:
                self._render_intercept(prog, t, w, h)
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

    def _build_ic_layer_buffers(self, w=1280, h=720):
        """Allocate / resize per-layer ping-pong FBOs for Tiamat blend layers."""
        for slot in range(3):
            for buf in range(2):
                old_fbo = self._ic_layer_fbos[slot][buf]
                old_tex = self._ic_layer_texs[slot][buf]
                if old_fbo is not None:
                    glDeleteFramebuffers(1, [old_fbo])
                if old_tex is not None:
                    glDeleteTextures([old_tex])

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
                glClearColor(0.0, 0.0, 0.0, 1.0)
                glClear(GL_COLOR_BUFFER_BIT)

                self._ic_layer_texs[slot][buf] = tex
                self._ic_layer_fbos[slot][buf] = fbo

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
        # Preserve original slot index so per-layer Tiamat FBOs can be
        # looked up by position regardless of which layers are active.
        active = [(i, l) for i, l in enumerate(self._blend_layers)
                  if l['mix'] > 0.0]
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
        for j, (orig_idx, layer) in enumerate(active):
            is_last = (j == len(active) - 1)

            # Pass 1: render this layer's engine → offscreen synth FBO
            engine_name = layer['engine'] if layer['engine'] else self._mode
            prog = self._programs.get(engine_name) or self._programs.get(self._mode)
            if prog is not None:
                if engine_name == 'Tiamat' \
                        and self._ic_layer_fbos[orig_idx][0] is not None:
                    # Full Tiamat path: ping-pong + live source + Studio uniforms.
                    # Result lands in _synth_fbo via internal blit.
                    self._render_intercept_layer(prog, t, w, h, orig_idx, layer)
                else:
                    glBindFramebuffer(GL_FRAMEBUFFER, self._synth_fbo)
                    glViewport(0, 0, w, h)
                    glUseProgram(prog)
                    saved, self._params = self._params, layer.get('params', [0.5] * 8)
                    self._set_uniforms(prog, t, w, h)
                    self._params = saved
                    if engine_name == 'Feedback':
                        # Read-only: borrow main-engine prev-frame, don't advance idx
                        loc = self._uniform_locs.get(prog, {}).get('u_prev', -1)
                        if loc >= 0:
                            glActiveTexture(GL_TEXTURE0)
                            glBindTexture(GL_TEXTURE_2D,
                                          self._fb_textures[1 - self._fb_idx])
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

    # All named uniforms in the Tiamat shader that are NOT in GLBase._UNIFORM_NAMES.
    # These must be cached separately so _send_tiamat_uniforms can look them up.
    # Includes the direct intensity params (u_warp_amt etc.) which are now named
    # uniforms matching the Studio shader exactly — no p[] translation.
    _TIAMAT_STYLE_UNIFORMS = [
        # Intensity params — same names as Studio, sent directly from _studio_params
        'u_warp_amt', 'u_feedback', 'u_glitch_amt', 'u_chroma',
        'u_sat', 'u_hue', 'u_gain', 'u_react',
        # Style/mode params
        'u_warp_mode', 'u_warp_spd', 'u_warp_freq', 'u_warp_twist',
        'u_fb_mode', 'u_fb_zoom', 'u_fb_rotate', 'u_fb_hue_shift',
        'u_glitch_mode', 'u_glitch_rate', 'u_glitch_scale',
        'u_zoom', 'u_mirror',
        'u_pal_mode', 'u_pal_0', 'u_pal_1', 'u_pal_2', 'u_pal_3',
        'u_contrast', 'u_posterize',
        'u_rgb_r', 'u_rgb_g', 'u_rgb_b', 'u_invert',
        'u_edge_mix', 'u_edge_hue',
        'u_grain', 'u_rf', 'u_scanlines',
        'u_lfo_rate', 'u_lfo_depth', 'u_lfo_target',
        'u_beat_punch', 'u_mix',
        # Source samplers — unit index sent alongside these
        'u_src0', 'u_src1', 'u_src2',
        'u_has_src0', 'u_has_src1', 'u_has_src2',
    ]

    def _compile_all(self):
        for name, frag_src in SHADERS.items():
            try:
                prog = self._link_program(VERT, frag_src)
                self._programs[name] = prog
                self._cache_locs(prog)
                if name == 'Tiamat':
                    # _cache_locs only handles GLBase._UNIFORM_NAMES.
                    # Cache the Tiamat-specific style uniforms too so
                    # _send_intercept_uniforms can actually send them.
                    locs = self._uniform_locs.setdefault(prog, {})
                    found = 0
                    for uname in self._TIAMAT_STYLE_UNIFORMS:
                        loc = glGetUniformLocation(prog, uname.encode())
                        if loc >= 0:
                            locs[uname] = loc
                            found += 1
                    print(f"[GL] Tiamat: {found}/{len(self._TIAMAT_STYLE_UNIFORMS)} style uniforms cached")
            except Exception as e:
                print(f"[GL] shader '{name}': {e}")

    # ── Public API ────────────────────────────────────────────────────────────

    def set_mode(self, mode: str):
        self._mode = mode

    def set_intercept_params(self, bp: dict):
        """Store full Tiamat style params for the Tiamat engine."""
        self._intercept_bp = dict(bp)

    def clear_feedback_fbos(self):
        """Clear both ping-pong FBOs to black — call when loading a preset so
        both apps start from the same clean initial feedback state."""
        if not self._fb_fbos:
            return
        self.makeCurrent()
        dfbo = self._dfbo()
        for fbo in self._fb_fbos:
            glBindFramebuffer(GL_FRAMEBUFFER, fbo)
            glClearColor(0.0, 0.0, 0.0, 1.0)
            glClear(GL_COLOR_BUFFER_BIT)
        glBindFramebuffer(GL_FRAMEBUFFER, dfbo)
        self.doneCurrent()

    def _send_intercept_uniforms(self, prog):
        """Send all named style uniforms to the Tiamat shader."""
        bp = self._intercept_bp
        if not bp:
            return
        locs = self._uniform_locs.get(prog, {})
        g = bp.get
        def si(n, v):
            loc = locs.get(n, -1)
            if loc >= 0: glUniform1i(loc, int(v))
        def sf(n, v):
            loc = locs.get(n, -1)
            if loc >= 0: glUniform1f(loc, float(v))
        def sv(n, v):
            loc = locs.get(n, -1)
            if loc >= 0: glUniform3f(loc, float(v[0]), float(v[1]), float(v[2]))

        # Intensity params — sent directly, same names as Studio shader
        sf('u_warp_amt',     g('u_warp_amt',     0.0))
        sf('u_feedback',     g('u_feedback',     0.0))
        sf('u_glitch_amt',   g('u_glitch_amt',   0.0))
        sf('u_chroma',       g('u_chroma',       0.0))
        sf('u_sat',          g('u_sat',          1.0))
        sf('u_hue',          g('u_hue',          0.0))
        sf('u_gain',         g('u_gain',         0.65))
        sf('u_react',        g('u_react',        0.5))
        # Style / mode params
        si('u_warp_mode',    g('u_warp_mode',    0))
        sf('u_warp_spd',     g('u_warp_spd',     0.4))
        sf('u_warp_freq',    g('u_warp_freq',     0.5))
        sf('u_warp_twist',   g('u_warp_twist',    0.0))
        si('u_fb_mode',      g('u_fb_mode',       0))
        sf('u_fb_zoom',      g('u_fb_zoom',       0.5))
        sf('u_fb_rotate',    g('u_fb_rotate',     0.5))
        sf('u_fb_hue_shift', g('u_fb_hue_shift',  0.0))
        si('u_glitch_mode',  g('u_glitch_mode',   0))
        sf('u_glitch_rate',  g('u_glitch_rate',   0.5))
        sf('u_glitch_scale', g('u_glitch_scale',  0.3))
        sf('u_zoom',         g('u_zoom',          0.5))
        si('u_mirror',       g('u_mirror',        0))
        si('u_pal_mode',     g('u_pal_mode',      0))
        sv('u_pal_0',        g('u_pal_0',  [0.04, 0.00, 0.12]))
        sv('u_pal_1',        g('u_pal_1',  [0.00, 0.22, 0.38]))
        sv('u_pal_2',        g('u_pal_2',  [0.12, 0.72, 0.88]))
        sv('u_pal_3',        g('u_pal_3',  [1.00, 0.92, 0.90]))
        sf('u_contrast',     g('u_contrast',      1.0))
        sf('u_posterize',    g('u_posterize',     0.0))
        sf('u_rgb_r',        g('u_rgb_r',         0.5))
        sf('u_rgb_g',        g('u_rgb_g',         0.5))
        sf('u_rgb_b',        g('u_rgb_b',         0.5))
        sf('u_invert',       g('u_invert',        0.0))
        sf('u_edge_mix',     g('u_edge_mix',      0.0))
        sf('u_edge_hue',     g('u_edge_hue',      0.0))
        sf('u_grain',        g('u_grain',         0.0))
        sf('u_rf',           g('u_rf',            0.0))
        sf('u_scanlines',    g('u_scanlines',     0.0))
        sf('u_lfo_rate',     g('u_lfo_rate',      0.3))
        sf('u_lfo_depth',    g('u_lfo_depth',     0.0))
        si('u_lfo_target',   g('u_lfo_target',    0))
        sf('u_beat_punch',   g('u_beat_punch',    0.5))
        sf('u_mix',          g('u_mix',           1.0))

    def _render_intercept(self, prog, t: float, w: int, h: int):
        """Render Tiamat engine with ping-pong feedback + full style uniforms."""
        cur  = self._fb_idx
        prev = 1 - cur

        glBindFramebuffer(GL_FRAMEBUFFER, self._fb_fbos[cur])
        glViewport(0, 0, w, h)
        glUseProgram(prog)
        # _set_uniforms sends: p[0-7], u_time, u_resolution, audio, u_video (unit 1)
        self._set_uniforms(prog, t, w, h)

        # Bind prev frame → unit 0 as u_prev
        locs = self._uniform_locs.get(prog, {})
        loc = locs.get('u_prev', -1)
        if loc >= 0:
            glActiveTexture(GL_TEXTURE0)
            glBindTexture(GL_TEXTURE_2D, self._fb_textures[prev])
            glUniform1i(loc, 0)

        # Send all style uniforms from the loaded preset
        self._send_intercept_uniforms(prog)

        glBindVertexArray(self._vao)
        glDrawArrays(GL_TRIANGLE_STRIP, 0, 4)

        # Blit to display FBO
        glBindFramebuffer(GL_READ_FRAMEBUFFER, self._fb_fbos[cur])
        glBindFramebuffer(GL_DRAW_FRAMEBUFFER, self._dfbo())
        glBlitFramebuffer(0, 0, w, h, 0, 0, w, h, GL_COLOR_BUFFER_BIT, GL_LINEAR)
        glBindFramebuffer(GL_FRAMEBUFFER, self._dfbo())
        self._fb_idx = prev

    def _render_intercept_layer(self, prog, t: float, w: int, h: int,
                                layer_idx: int, layer: dict):
        """Render Tiamat as a blend layer with full feature support:
        - Own ping-pong feedback (independent from the main-engine FBOs)
        - Live video feed wired as u_src0 (camera / video-editor frame)
        - Synth Studio named uniforms (warp, glitch, chroma, etc.)
        Result is blitted to _synth_fbo so the blend compositor picks it up."""
        ic_cur  = self._ic_layer_idx[layer_idx]
        ic_prev = 1 - ic_cur
        locs    = self._uniform_locs.get(prog, {})

        # Render into this layer's current ping-pong buffer
        glBindFramebuffer(GL_FRAMEBUFFER, self._ic_layer_fbos[layer_idx][ic_cur])
        glViewport(0, 0, w, h)
        glUseProgram(prog)

        # Send standard uniforms (audio, time, resolution) with layer's p[0-7]
        saved, self._params = self._params, layer.get('params', [0.5] * 8)
        self._set_uniforms(prog, t, w, h)
        self._params = saved

        # u_prev → unit 0: previous frame from this layer's own ping-pong
        loc = locs.get('u_prev', -1)
        if loc >= 0:
            glActiveTexture(GL_TEXTURE0)
            glBindTexture(GL_TEXTURE_2D, self._ic_layer_texs[layer_idx][ic_prev])
            glUniform1i(loc, 0)

        # u_src0 → unit 2: live video feed (camera or video-editor)
        vid_tex = self._video_tex if self._video_tex else self._blank_tex
        loc = locs.get('u_src0', -1)
        if loc >= 0:
            glActiveTexture(GL_TEXTURE2)
            glBindTexture(GL_TEXTURE_2D, vid_tex)
            glUniform1i(loc, 2)
        loc = locs.get('u_has_src0', -1)
        if loc >= 0:
            glUniform1i(loc, 1 if (self._has_video and self._video_tex) else 0)

        # u_src1/2 → units 3/4: blank (Video Life carries only one source)
        for sname, hname, unit in (('u_src1', 'u_has_src1', 3),
                                    ('u_src2', 'u_has_src2', 4)):
            loc = locs.get(sname, -1)
            if loc >= 0:
                glActiveTexture(GL_TEXTURE0 + unit)
                glBindTexture(GL_TEXTURE_2D, self._blank_tex)
                glUniform1i(loc, unit)
            loc = locs.get(hname, -1)
            if loc >= 0:
                glUniform1i(loc, 0)

        # Synth Studio named params: warp, glitch, chroma, feedback depth, etc.
        self._send_intercept_uniforms(prog)

        glBindVertexArray(self._vao)
        glDrawArrays(GL_TRIANGLE_STRIP, 0, 4)

        # Copy result to _synth_fbo so the blend compositor can sample it
        glBindFramebuffer(GL_READ_FRAMEBUFFER, self._ic_layer_fbos[layer_idx][ic_cur])
        glBindFramebuffer(GL_DRAW_FRAMEBUFFER, self._synth_fbo)
        glBlitFramebuffer(0, 0, w, h, 0, 0, w, h, GL_COLOR_BUFFER_BIT, GL_LINEAR)

        # Restore texture unit and advance ping-pong index
        glActiveTexture(GL_TEXTURE0)
        self._ic_layer_idx[layer_idx] = ic_prev

    def set_blend_layer(self, idx: int, engine, mix: float, mode: int):
        if 0 <= idx < len(self._blend_layers):
            self._blend_layers[idx]['engine'] = engine
            self._blend_layers[idx]['mix']    = float(mix)
            self._blend_layers[idx]['mode']   = int(mode)

    def set_blend_layer_params(self, idx: int, params: list):
        if 0 <= idx < len(self._blend_layers):
            self._blend_layers[idx]['params'] = list(params)[:8]
