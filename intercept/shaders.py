"""
INTERCEPT — GLSL signal interception engines.

Influenced by:
  · Ant Farm "Media Burn" (1975)       — media as sabotage material
  · Negativland / Culture Jamming      — appropriation and signal piracy
  · Nam June Paik TV interventions     — magnet/circuit distortion of broadcast
  · Jodi.org early internet glitch art — corrupted signal as aesthetic
  · SIGINT / surveillance aesthetics   — multiple feeds, ghost imagery, tap artifacts
  · VHS piracy and bootleg culture     — degraded but charged with stolen energy

Each shader receives up to 3 live source textures (u_src0, u_src1, u_src2)
plus the full standard uniform set from GLBase._set_uniforms.

Parameter mapping (p[0]–p[7]):
  p[0]  Mix-A    — source 0 weight / primary signal level
  p[1]  Mix-B    — source 1 weight
  p[2]  Mix-C    — source 2 weight
  p[3]  Corrupt  — glitch / corruption intensity
  p[4]  Noise    — RF / static noise injection
  p[5]  React    — audio reactivity multiplier
  p[6]  Palette  — 0.0=natural  0.33=phosphor-green  0.67=infrared  1.0=negative
  p[7]  Gain     — output brightness
"""

VERT = """
#version 330 core
in  vec2 in_position;
in  vec2 in_uv;
out vec2 uv;
void main() {
    gl_Position = vec4(in_position, 0.0, 1.0);
    uv = in_uv;
}
"""

# ── Shared uniform declarations + helper functions ────────────────────────────

_UNIFORMS = """
uniform float     u_time;
uniform vec2      u_resolution;
uniform float     u_rms;
uniform float     u_bass;
uniform float     u_mid;
uniform float     u_treble;
uniform float     u_beat;
uniform float     u_audio[512];
uniform float     p[8];

// Source textures (from SignalRouter slots 0-2)
uniform sampler2D u_src0;
uniform sampler2D u_src1;
uniform sampler2D u_src2;
uniform int       u_has_src0;
uniform int       u_has_src1;
uniform int       u_has_src2;

out vec4 fragColor;
"""

_COMMON = """
// ── PRNG / hash utilities ────────────────────────────────────────────────────
float hash1(float n) { return fract(sin(n) * 43758.5453); }
float hash2(vec2  v) { return fract(sin(dot(v, vec2(127.1, 311.7))) * 43758.5); }
vec3  hash3(vec2  v) {
    return fract(sin(vec3(dot(v, vec2(127.1,311.7)),
                          dot(v, vec2(269.5,183.3)),
                          dot(v, vec2(419.2,371.9)))) * 43758.5);
}

// ── Safe source sampling (returns mid-grey when source absent) ───────────────
vec4 src0(vec2 st) { return u_has_src0==1 ? texture(u_src0, clamp(st,0.001,0.999)) : vec4(0.18); }
vec4 src1(vec2 st) { return u_has_src1==1 ? texture(u_src1, clamp(st,0.001,0.999)) : vec4(0.18); }
vec4 src2(vec2 st) { return u_has_src2==1 ? texture(u_src2, clamp(st,0.001,0.999)) : vec4(0.18); }

// ── Palette / colour mode ────────────────────────────────────────────────────
// p[6]: 0.00 = natural   0.33 = phosphor green   0.67 = infrared   1.00 = negative
vec3 applyPalette(vec3 col, float mode) {
    float lum = dot(col, vec3(0.299, 0.587, 0.114));
    if (mode < 0.33) {
        return col;
    } else if (mode < 0.50) {
        float t = (mode - 0.33) / 0.17;
        vec3 green = vec3(lum * 0.15, lum * 1.10, lum * 0.25);
        return mix(col, green, t);
    } else if (mode < 0.67) {
        // phosphor green
        return vec3(lum * 0.12, lum * 1.05, lum * 0.22);
    } else if (mode < 0.84) {
        float t = (mode - 0.67) / 0.17;
        // infrared: hot = bright, cold = blue
        vec3 ir = mix(vec3(0.1, 0.15, 0.6), vec3(1.0, 0.3, 0.05),
                      smoothstep(0.0, 1.0, lum));
        return mix(vec3(lum * 0.12, lum * 1.05, lum * 0.22), ir, t);
    } else {
        float t = (mode - 0.84) / 0.16;
        vec3 ir = mix(vec3(0.1, 0.15, 0.6), vec3(1.0, 0.3, 0.05),
                      smoothstep(0.0, 1.0, lum));
        return mix(ir, 1.0 - col, t);  // infrared → negative
    }
}

// ── CRT scanline darkening ───────────────────────────────────────────────────
float scanline(float y) {
    return 0.88 + 0.12 * sin(y * u_resolution.y * 3.14159 * 2.0);
}
"""

def _frag(body: str) -> str:
    return "#version 330 core\n" + _UNIFORMS + _COMMON + body


# ─────────────────────────────────────────────────────────────────────────────
# TAP — Signal interception with RF interference artifacts
# ─────────────────────────────────────────────────────────────────────────────
# Simulates tapping an analog video line.  The primary source feeds through
# with VHS-like tracking errors, horizontal roll artifacts, RF interference
# bands, and colour bleeding — the aesthetic of surveillance footage taped
# from a monitor, or a wiretapped broadcast signal.
# ─────────────────────────────────────────────────────────────────────────────
_TAP_BODY = """
void main() {
    vec2  st  = gl_FragCoord.xy / u_resolution;
    float corr= p[3] * (1.0 + u_rms * p[5] * 2.0);
    float beat= u_beat * p[5] * 0.3;

    // ── Horizontal sync roll: occasional full-line horizontal displacement ──
    float lineY   = floor(st.y * u_resolution.y);
    float rowJump = step(0.94, hash1(lineY * 0.1 + floor(u_time * 3.0))) * corr;
    float roll    = (hash1(lineY + floor(u_time * 17.0)) - 0.5) * rowJump * 0.08;

    // ── VHS tracking: slow horizontal drift on a band ──────────────────────
    float trackBand = abs(st.y - fract(u_time * 0.11 + 0.4));
    float tracking  = exp(-trackBand * 80.0) * corr * 0.06
                    + exp(-trackBand * 20.0) * corr * 0.03;

    vec2 uvD = st + vec2(roll + tracking, 0.0);

    // ── Colour bleeding: red channel ahead, blue behind ─────────────────────
    float bleed    = corr * 0.008;
    float r        = src0(uvD + vec2( bleed, 0.0)).r;
    float g        = src0(uvD).g;
    float b        = src0(uvD + vec2(-bleed, 0.0)).b;
    vec3  col      = vec3(r, g, b);

    // Blend in source 1 via p[1]
    col = mix(col, src1(uvD).rgb, p[1] * float(u_has_src1));

    // ── RF interference bands: horizontal noise stripes ─────────────────────
    float rfFreq  = 60.0 + p[3] * 200.0;
    float rfBand  = sin(st.y * rfFreq + u_time * 7.3) * 0.5 + 0.5;
    float rfNoise = hash2(vec2(st.x * 512.0, lineY) + floor(u_time * 60.0));
    float rf      = rfBand * rfNoise * p[4] * (1.0 + u_rms * p[5] * 3.0);

    // Beat flash: full-frame brightness spike on transient
    rf += beat * hash2(st * u_resolution) * 0.5;

    col.rgb += vec3(rf * 0.4, rf * 0.5, rf * 0.3);

    // ── Vertical sync flicker: brief bright line at random Y ────────────────
    float syncY = fract(u_time * 0.19 + 0.6);
    col.rgb    += exp(-abs(st.y - syncY) * 500.0) * corr * vec3(0.8, 1.0, 0.6) * 0.5;

    col.rgb *= scanline(st.y);
    col.rgb  = applyPalette(col.rgb, p[6]);
    col.rgb *= mix(0.4, 1.8, p[7]);

    // Vignette
    float vig = 1.0 - smoothstep(0.35, 0.85, length(st - 0.5) * 1.5);
    col.rgb  *= 0.75 + vig * 0.25;

    fragColor = vec4(col, 1.0);
}
"""

# ─────────────────────────────────────────────────────────────────────────────
# GHOST — Surveillance palimpsest / long-exposure accumulation
# ─────────────────────────────────────────────────────────────────────────────
# Three sources are layered at time-varying weights.  A spatial blur
# approximates temporal averaging — the effect of a long-exposure surveillance
# camera or the accretion of multiple shifted signals in the same space.
# Beat transients push the sources through each other.
# ─────────────────────────────────────────────────────────────────────────────
_GHOST_BODY = """
void main() {
    vec2  st   = gl_FragCoord.xy / u_resolution;
    float beat = u_beat * p[5];

    // ── Source weights (p[0-2] + audio-reactive push) ────────────────────────
    float w0 = p[0] * (0.8 + sin(u_time * 0.37) * 0.2 + u_bass   * p[5] * 0.4);
    float w1 = p[1] * (0.8 + sin(u_time * 0.53) * 0.2 + u_mid    * p[5] * 0.4);
    float w2 = p[2] * (0.8 + sin(u_time * 0.71) * 0.2 + u_treble * p[5] * 0.4);
    float wSum = max(w0 + w1 + w2, 0.001);

    // ── Displacement: sources drift slowly past each other ───────────────────
    float driftAmt = p[3] * 0.015;
    vec2 off0 = vec2(sin(u_time * 0.13) * driftAmt,
                     cos(u_time * 0.17) * driftAmt);
    vec2 off1 = vec2(sin(u_time * 0.19 + 2.1) * driftAmt,
                     cos(u_time * 0.23 + 1.3) * driftAmt);
    vec2 off2 = vec2(sin(u_time * 0.11 + 4.2) * driftAmt,
                     cos(u_time * 0.29 + 3.1) * driftAmt);

    // Beat kicks each source in a different direction
    off0 += vec2( beat * 0.02, -beat * 0.01);
    off1 += vec2(-beat * 0.015, beat * 0.02);
    off2 += vec2( beat * 0.01,  beat * 0.015);

    vec3 c0 = src0(st + off0).rgb;
    vec3 c1 = src1(st + off1).rgb;
    vec3 c2 = src2(st + off2).rgb;

    vec3 col = (c0 * w0 + c1 * w1 + c2 * w2) / wSum;

    // ── Ghost blur: approximate persistence with small spatial blur ──────────
    float blurAmt = p[3] * 0.6;
    if (blurAmt > 0.01) {
        vec3  blurred = vec3(0.0);
        float total   = 0.0;
        for (int dx = -2; dx <= 2; dx++) {
            for (int dy = -1; dy <= 1; dy++) {
                vec2  off = vec2(float(dx), float(dy)) / u_resolution * 4.0;
                float w   = exp(-float(dx*dx + dy*dy) * 0.4);
                blurred  += src0(st + off).rgb * w;
                total    += w;
            }
        }
        blurred /= total;
        col = mix(col, blurred * w0 / max(w0, 0.001), blurAmt);
    }

    // ── Film-grain noise ─────────────────────────────────────────────────────
    float grain = (hash2(st * u_resolution + u_time * 100.0) - 0.5)
                * p[4] * 0.12 * (1.0 + u_rms * p[5]);
    col += grain;

    col = applyPalette(col, p[6]);
    col *= mix(0.4, 1.8, p[7]);

    // Hard vignette — surveillance camera crop aesthetic
    float vig = 1.0 - smoothstep(0.3, 0.7, length((st - 0.5) * vec2(1.0, 1.3)));
    col *= 0.6 + vig * 0.4;

    fragColor = vec4(col, 1.0);
}
"""

# ─────────────────────────────────────────────────────────────────────────────
# CORRUPT — Digital signal corruption
# ─────────────────────────────────────────────────────────────────────────────
# The signal is deliberately mangled: RGB channel separation, scanline row
# displacement, coarse block glitching, and XOR-like bit-error overlay.
# Inspired by datamoshing, codec corruption, and the deliberate destruction
# of signal integrity in circuit-bending practice.
# ─────────────────────────────────────────────────────────────────────────────
_CORRUPT_BODY = """
void main() {
    vec2  st     = gl_FragCoord.xy / u_resolution;
    float corr   = p[3] * (1.0 + u_rms * p[5] * 2.5 + u_beat * p[5] * 0.8);

    // ── Scanline row displacement (horizontal block glitch) ──────────────────
    float rowGroup = floor(st.y * u_resolution.y / max(1.0, 8.0 - p[3] * 6.0));
    float rowSeed  = hash1(rowGroup + floor(u_time * (4.0 + p[3] * 12.0)));
    float rowDisp  = (rowSeed - 0.5) * corr * 0.12 * step(0.80, rowSeed);

    // ── Coarse block / macro-block corruption ────────────────────────────────
    float blockSize= max(1.0, 24.0 - p[3] * 20.0);
    vec2  tile     = floor(st * u_resolution / blockSize);
    float tileSeed = hash2(tile + floor(u_time * 3.0));
    vec2  tileDisp = (hash3(tile + 0.5).xy - 0.5) * corr * 0.06
                   * step(0.88, tileSeed);

    // ── RGB channel separation ────────────────────────────────────────────────
    float chroma   = corr * 0.022;
    vec2  uvR      = st + vec2(rowDisp + tileDisp.x + chroma,  tileDisp.y);
    vec2  uvG      = st + vec2(rowDisp + tileDisp.x,           tileDisp.y);
    vec2  uvB      = st + vec2(rowDisp + tileDisp.x - chroma,  tileDisp.y);

    float r0 = src0(fract(uvR)).r;
    float g0 = src0(fract(uvG)).g;
    float b0 = src0(fract(uvB)).b;
    vec3  col= vec3(r0, g0, b0);

    // Blend secondary source via p[1]
    col = mix(col, src1(fract(uvG)).rgb, p[1] * float(u_has_src1));

    // ── Bit-error noise: random pixel inversions ─────────────────────────────
    float bitNoise  = hash2(floor(st * 160.0) + floor(u_time * 40.0));
    float bitMask   = step(1.0 - p[4] * 0.18, bitNoise);
    float bitSelect = hash2(floor(st * 200.0) + 7.0);
    if (bitMask > 0.0) {
        if (bitSelect < 0.4)       col.r = 1.0 - col.r;
        else if (bitSelect < 0.7)  col.g = col.g * 2.0;
        else                       col   = 1.0 - col;
    }

    // ── Sync pulse: bright horizontal line sweeping down ────────────────────
    float syncY    = fract(u_time * 0.21 + 0.3);
    float syncDist = abs(st.y - syncY);
    col           += exp(-syncDist * 300.0) * corr * vec3(0.2, 1.0, 0.4) * 0.6;

    // ── Data dump aesthetic: vertical stripe of pure noise ───────────────────
    float dumpX    = fract(u_time * 0.07 + 0.5);
    float dumpDist = abs(st.x - dumpX);
    float dumpMask = exp(-dumpDist * 200.0) * corr * 0.5;
    col           = mix(col, hash3(st + floor(u_time * 60.0)), dumpMask);

    col = applyPalette(col, p[6]);
    col *= mix(0.4, 1.8, p[7]);
    fragColor = vec4(col, 1.0);
}
"""

# ─────────────────────────────────────────────────────────────────────────────
# SPLICE — Signal hijacking / hard-cut switching
# ─────────────────────────────────────────────────────────────────────────────
# Sources are hard-cut together at audio-reactive intervals with glitch
# artifacts at each transition point — as if a pirate broadcaster is
# hijacking multiple feeds, cutting between them with an imperfect switch.
# When only one source is active, the switching manifests as self-referential
# glitch loops: the signal cuts against itself.
# ─────────────────────────────────────────────────────────────────────────────
_SPLICE_BODY = """
// Glitch at a specific scanline height
vec3 glitchSlice(vec2 st, float amount) {
    float lineY = floor(st.y * 40.0);
    float disp  = (hash1(lineY + floor(u_time * 30.0)) - 0.5)
                * amount * 0.15
                * step(0.75, hash1(lineY * 0.3));
    vec2  uv    = st + vec2(disp, 0.0);
    return src0(fract(uv)).rgb;
}

void main() {
    vec2  st   = gl_FragCoord.xy / u_resolution;
    float beat = u_beat * p[5];

    // ── Beat-rate source switching ────────────────────────────────────────────
    float bpm      = 2.0 + beat * 4.0;                    // faster with beats
    float phase    = u_time * bpm;
    float beat16   = fract(phase);                         // 0..1 within current beat
    int   beatIdx  = int(mod(floor(phase), 16.0));         // which 1/16 note

    // Assign sources to beat slots via hash
    float slot0End = p[0];
    float slot1End = p[0] + p[1];
    float pick     = hash1(float(beatIdx) + floor(u_time * 0.5));
    int   srcSel   = (pick < slot0End) ? 0 : (pick < slot1End) ? 1 : 2;

    // ── Transition window glitch ──────────────────────────────────────────────
    float transW   = 0.06;
    float inTrans  = 1.0 - smoothstep(0.0, transW, beat16)          // at start
                   + smoothstep(1.0 - transW, 1.0, beat16);         // near end
    float corrInst = p[3] * inTrans * (1.0 + u_rms * p[5] * 2.0);

    // Scanline displacement
    float lineY = floor(st.y * u_resolution.y / 4.0);
    float ldisp = (hash1(lineY + floor(u_time * 20.0)) - 0.5)
                * corrInst * 0.12 * step(0.7, hash1(lineY * 0.17));
    vec2 uvD    = st + vec2(ldisp, 0.0);

    // ── Sample active source ──────────────────────────────────────────────────
    vec3 col;
    if      (srcSel == 0) col = src0(fract(uvD)).rgb;
    else if (srcSel == 1) col = src1(fract(uvD)).rgb;
    else                  col = src2(fract(uvD)).rgb;

    // ── Hard cut flash: white frame at beat ───────────────────────────────────
    float flash = smoothstep(0.0, 0.02, beat16) * (1.0 - smoothstep(0.02, 0.06, beat16));
    col = mix(col, vec3(1.0), flash * beat * 0.7);

    // ── Composite noise during transition ─────────────────────────────────────
    float transNoise = hash2(st * u_resolution + floor(u_time * 60.0)) * inTrans;
    col = mix(col, vec3(transNoise), corrInst * 0.5 + p[4] * 0.08);

    // ── RF noise stripe across cut point ─────────────────────────────────────
    float rfBand = sin(st.y * 80.0 + u_time * 11.0) * 0.5 + 0.5;
    col.rgb     += rfBand * p[4] * 0.12 * (1.0 + u_rms * p[5]);

    col  = applyPalette(col, p[6]);
    col *= scanline(st.y);
    col *= mix(0.4, 1.8, p[7]);
    fragColor = vec4(col, 1.0);
}
"""


# ─────────────────────────────────────────────────────────────────────────────
# Assembled shader dict
# ─────────────────────────────────────────────────────────────────────────────

SHADERS: dict[str, str] = {
    'Tap':     _frag(_TAP_BODY),
    'Ghost':   _frag(_GHOST_BODY),
    'Corrupt': _frag(_CORRUPT_BODY),
    'Splice':  _frag(_SPLICE_BODY),
}

PARAM_NAMES = ['Mix-A', 'Mix-B', 'Mix-C', 'Corrupt', 'Noise', 'React', 'Palette', 'Gain']

PARAM_DEFAULTS: dict[str, list[float]] = {
    'Tap':     [0.80, 0.00, 0.00, 0.35, 0.25, 0.50, 0.00, 0.65],
    'Ghost':   [0.70, 0.40, 0.20, 0.30, 0.20, 0.55, 0.33, 0.65],
    'Corrupt': [0.80, 0.20, 0.00, 0.40, 0.30, 0.55, 0.00, 0.60],
    'Splice':  [0.60, 0.40, 0.20, 0.45, 0.25, 0.65, 0.00, 0.65],
}
