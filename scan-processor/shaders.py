"""
Scan Processor — GLSL shader engines.

Influenced by:
  · Rutt-Etra Video Synthesizer (1972)  — scan-line beam deflection
  · Sandin Image Processor (1971)       — analog modular raster processing
  · Nam June Paik / Shuya Abe (1969)   — magnet distortion of CRT scan
  · Steina Vasulka — raster manipulation as compositional material

All shaders share the standard Video Life uniform contract so they work
with GLBase._set_uniforms() without modification.

Parameter mapping (p[0]–p[7]):
  p[0]  Density   — scan line count
  p[1]  Width     — line thickness / softness
  p[2]  Deflect   — displacement amount
  p[3]  Freq      — spatial frequency of deflection signal
  p[4]  Speed     — time modulation rate
  p[5]  Audio     — audio reactivity (0 = off)
  p[6]  Color     — phosphor palette (green → amber → white → blue)
  p[7]  Gain      — output brightness
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

# ── Shared GLSL blocks (concatenated into every fragment shader) ─────────────

_UNIFORMS = """
uniform float     u_time;
uniform vec2      u_resolution;
uniform float     u_rms;
uniform float     u_bass;
uniform float     u_mid;
uniform float     u_treble;
uniform float     u_beat;
uniform float     u_audio[512];
uniform sampler2D u_video;
uniform int       u_has_video;
uniform float     p[8];
out vec4 fragColor;
"""

_COMMON = """
// ── Phosphor colour palette ──────────────────────────────────────────────────
// p[6] → 0.00 = green  0.33 = amber  0.67 = white  1.00 = blue
vec3 phosphor(float t) {
    vec3 green = vec3(0.04, 0.98, 0.24);
    vec3 amber = vec3(1.00, 0.62, 0.04);
    vec3 white = vec3(0.90, 0.93, 1.00);
    vec3 blue  = vec3(0.18, 0.42, 1.00);
    t = clamp(t, 0.0, 1.0) * 3.0;
    if (t < 1.0) return mix(green, amber, t);
    if (t < 2.0) return mix(amber, white, t - 1.0);
    return         mix(white, blue,  t - 2.0);
}

// ── Soft scan-line rendering ─────────────────────────────────────────────────
// minDist: pixel distance from the nearest displaced scan-line centre
// halfW  : half the desired line width (in UV space)
// returns: brightness [0,1]
float scanLine(float minDist, float halfW) {
    float core = 1.0 - smoothstep(halfW * 0.25, halfW, minDist);
    float glow = exp(-minDist / max(halfW * 2.2, 0.0001)) * 0.40;
    return core + glow;
}
"""

def _frag(body: str) -> str:
    return "#version 330 core\n" + _UNIFORMS + _COMMON + body


# ─────────────────────────────────────────────────────────────────────────────
# TERRAIN — Rutt-Etra scan-line terrain mapping
# ─────────────────────────────────────────────────────────────────────────────
# Horizontal scan lines are vertically displaced in proportion to the luminance
# of the video input (or a procedural animated terrain when no video is present).
# Bright areas lift lines upward; dark areas push them down — exactly as an
# electron beam deflected by an external voltage would behave.
# ─────────────────────────────────────────────────────────────────────────────
_TERRAIN_BODY = """
// Video luminance or procedural terrain signal
float terrain(float x, float nomY) {
    if (u_has_video == 1) {
        return dot(texture(u_video, vec2(x, nomY)).rgb, vec3(0.299, 0.587, 0.114));
    }
    float freq = 2.0 + p[3] * 12.0;
    float spd  = p[4] * 1.6;
    float a    = sin(x * freq       * 6.2832 + u_time * spd      );
    float b    = sin(x * freq * 2.7 * 6.2832 - u_time * spd * 0.6);
    float c    = sin(x * freq * 0.4 * 6.2832 + u_time * spd * 1.5);
    return 0.5 + (a * 0.50 + b * 0.30 + c * 0.20) * 0.44;
}

void main() {
    vec2  uv      = gl_FragCoord.xy / u_resolution;
    float nLines  = floor(mix(10.0, 140.0, p[0]));
    float spacing = 1.0 / nLines;
    float halfW   = spacing * mix(0.025, 0.46, p[1]);
    float audioRMS= 1.0 + u_rms  * p[5] * 2.5;
    float maxDisp = spacing * mix(0.4, 7.0, p[2]) * audioRMS;

    float minDist = 1.0;
    float hitLum  = 0.5;

    // Search nearby nominal scan lines — needed because large displacements
    // can move a line more than one spacing away.
    float base = floor(uv.y * nLines);
    for (int k = -5; k <= 5; k++) {
        float nomY = (base + float(k) + 0.5) / nLines;
        if (nomY < 0.0 || nomY > 1.0) continue;

        float lum = terrain(uv.x, nomY);

        // Per-column audio modulation: FFT energy at this X position
        int   bin  = clamp(int(uv.x * 510.0), 0, 511);
        float amod = u_audio[bin] * p[5] * 0.55;

        // Beat kick: global displacement impulse
        float kick = u_beat * p[5] * 0.14 * spacing;

        float disp  = (lum - 0.5) * maxDisp * 2.0 + amod * spacing + kick;
        float lineY = nomY - disp;

        float d = abs(uv.y - lineY);
        if (d < minDist) { minDist = d; hitLum = lum; }
    }

    float bright = scanLine(minDist, halfW)
                 * mix(0.3, 1.8, p[7])
                 * (0.45 + hitLum * 1.1);

    fragColor = vec4(phosphor(p[6]) * bright, 1.0);
}
"""

# ─────────────────────────────────────────────────────────────────────────────
# SPECTRUM — FFT spectrum as a displaced scan-line stack
# ─────────────────────────────────────────────────────────────────────────────
# Each horizontal scan line maps to a logarithmically-spaced frequency band.
# The line is displaced upward proportionally to the spectral energy of that
# band, producing a glowing scan-line spectrum analyser.  A parametric sine
# sweep rides on top of the FFT displacement, creating animated waterfall
# interference patterns.
# ─────────────────────────────────────────────────────────────────────────────
_SPECTRUM_BODY = """
void main() {
    vec2  uv      = gl_FragCoord.xy / u_resolution;
    float nLines  = floor(mix(8.0, 128.0, p[0]));
    float spacing = 1.0 / nLines;
    float halfW   = spacing * mix(0.025, 0.46, p[1]);
    float maxDisp = mix(0.02, 0.48, p[2]);

    float minDist = 1.0;
    float hitAmp  = 0.0;

    float base = floor(uv.y * nLines);
    for (int k = -3; k <= 3; k++) {
        float nomY = (base + float(k) + 0.5) / nLines;
        if (nomY < 0.0 || nomY > 1.0) continue;

        // Logarithmic frequency mapping: bottom line = bass, top = treble
        float logT  = 1.0 - nomY;                        // 0=low freq, 1=high
        float logBin= (exp(logT * log(513.0)) - 1.0);
        int   bin   = clamp(int(logBin), 0, 511);
        float amp   = u_audio[bin];

        // Animated sweep: horizontal phase modulation adds waterfall motion
        float sweep = sin( uv.x    * 6.2832 * (0.5 + p[3] * 7.0)
                         + u_time  * p[4]   * 5.0
                         + float(bin) * 0.008 )
                      * amp * p[5] * 0.06;

        // Displace: positive amplitude lifts the line above its resting Y
        float disp  = amp * maxDisp * p[5] + sweep;
        float lineY = nomY - disp + maxDisp * p[5] * 0.5;  // keep centred

        float d = abs(uv.y - lineY);
        if (d < minDist) { minDist = d; hitAmp = amp; }
    }

    float bright = scanLine(minDist, halfW)
                 * mix(0.3, 1.8, p[7])
                 * (0.25 + hitAmp * 2.2 + u_rms * p[5] * 0.8);

    fragColor = vec4(phosphor(p[6]) * bright, 1.0);
}
"""

# ─────────────────────────────────────────────────────────────────────────────
# WARP — Parametric multi-wave scan-line deflection
# ─────────────────────────────────────────────────────────────────────────────
# Three overlapping sine waves at different frequencies drive vertical
# displacement, producing flowing interference patterns with no video input
# required.  Audio energy modulates overall amplitude; beat pulses create
# sharp transient deflections — behaviour analogous to the Rutt-Etra's
# external voltage inputs driven by audio.
# ─────────────────────────────────────────────────────────────────────────────
_WARP_BODY = """
void main() {
    vec2  uv      = gl_FragCoord.xy / u_resolution;
    float nLines  = floor(mix(10.0, 120.0, p[0]));
    float spacing = 1.0 / nLines;
    float halfW   = spacing * mix(0.025, 0.46, p[1]);
    float maxDisp = spacing * mix(0.5, 7.0, p[2]);
    float freq    = 0.5 + p[3] * 9.0;
    float spd     = p[4] * 3.0;
    float audioMod= 1.0 + p[5] * u_rms * 3.5;

    float minDist = 1.0;
    float hitVal  = 0.0;
    float t       = u_time * spd;

    float base = floor(uv.y * nLines);
    for (int k = -5; k <= 5; k++) {
        float nomY = (base + float(k) + 0.5) / nLines;
        if (nomY < 0.0 || nomY > 1.0) continue;

        // Three overlapping waves — different frequencies and phases create
        // moiré-like beating interference when they align and cancel.
        float w1 = sin(uv.x * freq       * 6.2832 + t               );
        float w2 = sin(uv.x * freq * 1.6 * 6.2832 - t * 0.73 + 1.27);
        float w3 = sin(uv.x * freq * 0.3 * 6.2832 + t * 1.51
                                                   + nomY * 6.2832   );
        float wave = w1 * 0.50 + w2 * 0.30 + w3 * 0.20;

        // Bass drives an extra low-frequency kick on the whole raster
        float bassKick = u_bass * p[5] * spacing * 0.6;
        float disp  = wave * maxDisp * audioMod + bassKick;
        float lineY = nomY - disp;

        float d = abs(uv.y - lineY);
        if (d < minDist) { minDist = d; hitVal = abs(wave); }
    }

    float bright = scanLine(minDist, halfW)
                 * mix(0.3, 1.8, p[7])
                 * (0.5 + hitVal * 1.0);

    fragColor = vec4(phosphor(p[6]) * bright, 1.0);
}
"""

# ─────────────────────────────────────────────────────────────────────────────
# ETCH — Warped 2D mesh / raster scan
# ─────────────────────────────────────────────────────────────────────────────
# Both horizontal AND vertical scan lines are rendered simultaneously, each
# deflected by a different sine-wave phase.  Where the two line families
# intersect, a brighter node appears — mimicking the cathode-ray dot matrix
# of an analog image processor's cross-point switcher.
# ─────────────────────────────────────────────────────────────────────────────
_ETCH_BODY = """
void main() {
    vec2  uv      = gl_FragCoord.xy / u_resolution;
    float ar      = u_resolution.x / u_resolution.y;
    float nLines  = floor(mix(6.0, 50.0, p[0]));
    float spacH   = 1.0 / nLines;
    float spacV   = spacH / ar;               // equal pixel spacing on screen
    float halfW   = spacH * mix(0.02, 0.28, p[1]);
    float maxDisp = spacH * mix(0.3, 5.5, p[2]);
    float freq    = 1.0 + p[3] * 9.0;
    float spd     = p[4] * 1.8;
    float adrv    = 1.0 + p[5] * u_rms * 3.0;
    float t       = u_time * spd;

    // ── Horizontal scan lines ─────────────────────────────────────────────
    float hMinDist = 1.0;
    float baseH    = floor(uv.y * nLines);
    for (int k = -4; k <= 4; k++) {
        float nomY = (baseH + float(k) + 0.5) / nLines;
        if (nomY < 0.0 || nomY > 1.0) continue;
        float s1   = sin(uv.x * freq       * 6.2832 + t      + nomY * 3.14);
        float s2   = sin(uv.x * freq * 1.7 * 6.2832 - t * 0.8             );
        float disp = (s1 * 0.6 + s2 * 0.4) * maxDisp * adrv;
        hMinDist   = min(hMinDist, abs(uv.y - (nomY - disp)));
    }

    // ── Vertical scan lines ───────────────────────────────────────────────
    float nV       = nLines * ar;
    float vMinDist = 1.0;
    float baseV    = floor(uv.x * nV);
    for (int k = -4; k <= 4; k++) {
        float nomX = (baseV + float(k) + 0.5) / nV;
        if (nomX < 0.0 || nomX > 1.0) continue;
        float s1   = sin(uv.y * freq       * ar * 6.2832 + t * 1.1 + nomX * 3.14);
        float s2   = sin(uv.y * freq * 1.5 * ar * 6.2832 - t * 0.9                );
        float disp = (s1 * 0.6 + s2 * 0.4) * maxDisp * spacV / spacH * adrv;
        vMinDist   = min(vMinDist, abs(uv.x - (nomX - disp)));
    }

    // ── Combine lines + intersection nodes ───────────────────────────────
    float hBright  = scanLine(hMinDist, halfW);
    float vBright  = scanLine(vMinDist, halfW);
    float crossing = hBright * vBright;             // node highlight

    float bright   = (hBright + vBright + crossing * 0.8)
                   * mix(0.3, 1.8, p[7]);

    fragColor = vec4(phosphor(p[6]) * bright, 1.0);
}
"""

# ─────────────────────────────────────────────────────────────────────────────
# Assembled shader dict
# ─────────────────────────────────────────────────────────────────────────────

SHADERS: dict[str, str] = {
    'Terrain':  _frag(_TERRAIN_BODY),
    'Spectrum': _frag(_SPECTRUM_BODY),
    'Warp':     _frag(_WARP_BODY),
    'Etch':     _frag(_ETCH_BODY),
}

PARAM_NAMES = ['Density', 'Width', 'Deflect', 'Freq', 'Speed', 'Audio', 'Color', 'Gain']

# Default values per engine — a "neutral" starting point
PARAM_DEFAULTS: dict[str, list[float]] = {
    'Terrain':  [0.30, 0.30, 0.50, 0.28, 0.38, 0.55, 0.00, 0.60],
    'Spectrum': [0.40, 0.28, 0.45, 0.20, 0.42, 0.65, 0.00, 0.62],
    'Warp':     [0.32, 0.28, 0.42, 0.30, 0.40, 0.50, 0.00, 0.60],
    'Etch':     [0.28, 0.25, 0.38, 0.28, 0.35, 0.45, 0.00, 0.58],
}
