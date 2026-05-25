"""GLSL shaders for each synthesis engine — modeled on iconic video synth hardware."""

VERT = """
#version 330 core
layout(location = 0) in vec2 in_position;
layout(location = 1) in vec2 in_uv;
out vec2 uv;
void main() {
    gl_Position = vec4(in_position, 0.0, 1.0);
    uv = in_uv;
}
"""

# ── Common uniforms available in every shader ────────────────────────────────
# uniform float u_time;
# uniform vec2  u_resolution;
# uniform float u_audio[512];    // FFT bins 0..511
# uniform float u_rms;           // overall amplitude 0..1
# uniform float u_bass;          // bass energy 0..1
# uniform float u_mid;           // mid energy 0..1
# uniform float u_treble;        // treble energy 0..1
# uniform float u_beat;          // beat impulse 0..1 decaying
# uniform float p[8];            // 8 user / MIDI-controllable params 0..1

UNIFORM_BLOCK = """
uniform float u_time;
uniform vec2  u_resolution;
uniform float u_audio[512];
uniform float u_rms;
uniform float u_bass;
uniform float u_mid;
uniform float u_treble;
uniform float u_beat;
uniform float p[8];
"""

# ── 1. LISSAJOUS (EMS VCS3 / oscilloscope aesthetic) ─────────────────────────
LISSAJOUS = """
#version 330 core
""" + UNIFORM_BLOCK + """
out vec4 fragColor;

vec3 hsv2rgb(vec3 c) {
    vec4 K = vec4(1.0, 2.0/3.0, 1.0/3.0, 3.0);
    vec3 p = abs(fract(c.xxx + K.xyz) * 6.0 - K.www);
    return c.z * mix(K.xxx, clamp(p - K.xxx, 0.0, 1.0), c.y);
}

void main() {
    vec2 st = (gl_FragCoord.xy / u_resolution) * 2.0 - 1.0;
    st.x *= u_resolution.x / u_resolution.y;

    float freqX  = 1.0 + p[0] * 7.0;
    float freqY  = 1.0 + p[1] * 7.0;
    float phase  = p[2] * 6.283185;
    float thick  = 0.004 + p[3] * 0.03;
    float glow   = 0.5  + p[4] * 2.0;
    float zoom   = 0.3  + p[5] * 0.7;
    float hue    = p[6];
    float sat    = 0.6  + p[7] * 0.4;

    float t = u_time;
    float brightness = 0.0;
    int   SAMPLES = 512;

    for (int i = 0; i < SAMPLES; i++) {
        float fi     = float(i) / float(SAMPLES);
        float ang    = fi * 6.283185 * 2.0;
        float audio  = u_audio[int(fi * 511.0)] * (0.4 + u_bass * 0.6);

        float ox = sin(freqX * ang + phase + t * 0.5) * zoom * (1.0 + audio * 0.5);
        float oy = sin(freqY * ang             + t * 0.3) * zoom * (1.0 + u_mid * 0.3);

        float d = length(st - vec2(ox, oy));
        brightness += (thick * glow) / (d + 0.001);
    }
    brightness /= float(SAMPLES);
    brightness  = clamp(brightness, 0.0, 1.0);

    float h = hue + u_treble * 0.15 + t * 0.02;
    vec3 col = hsv2rgb(vec3(h, sat, brightness));
    col += hsv2rgb(vec3(h + 0.5, sat * 0.5, brightness * u_beat * 0.5));

    fragColor = vec4(col, 1.0);
}
"""

# ── 2. PLASMA (interference patterns — demoscene / LZX Mapper-style) ─────────
PLASMA = """
#version 330 core
""" + UNIFORM_BLOCK + """
out vec4 fragColor;

vec3 hsv2rgb(vec3 c) {
    vec4 K = vec4(1.0, 2.0/3.0, 1.0/3.0, 3.0);
    vec3 p = abs(fract(c.xxx + K.xyz) * 6.0 - K.www);
    return c.z * mix(K.xxx, clamp(p - K.xxx, 0.0, 1.0), c.y);
}

void main() {
    vec2 st = gl_FragCoord.xy / u_resolution;
    vec2 pos = st * 2.0 - 1.0;
    pos.x *= u_resolution.x / u_resolution.y;

    float scale   = 1.0 + p[0] * 6.0;
    float speed   = 0.2 + p[1] * 2.0;
    float freq1   = 1.0 + p[2] * 5.0;
    float freq2   = 1.0 + p[3] * 5.0;
    float hueBase = p[4];
    float hueSpd  = p[5] * 0.5;
    float sat     = 0.5 + p[6] * 0.5;
    float brtMod  = 0.5 + p[7] * 0.5;
    float t = u_time * speed;

    float v  = sin(pos.x * scale * freq1 + t + u_bass * 2.0);
          v += sin(pos.y * scale * freq2 + t * 1.1 + u_mid * 1.5);
          v += sin((pos.x + pos.y) * scale * 0.7 + t * 0.9 + u_treble);
          v += sin(length(pos) * scale * 1.5 - t * 1.3 + u_rms * 3.0);

    // audio bands modulate additional interference
    float binsSum = 0.0;
    for (int i = 0; i < 16; i++) {
        float f = float(i) / 16.0;
        binsSum += u_audio[i * 8] * sin(pos.x * f * 8.0 + t);
    }
    v += binsSum * 0.5;

    float hue = hueBase + v * 0.1 + u_time * hueSpd;
    float brt = (sin(v * 3.14159) * 0.5 + 0.5) * brtMod * (0.7 + u_rms * 0.3);
    brt += u_beat * 0.3;

    fragColor = vec4(hsv2rgb(vec3(hue, sat, clamp(brt, 0.0, 1.0))), 1.0);
}
"""

# ── 3. RAMP_COLORIZER (LZX Cadet / Visual Cortex — horizontal/vertical ramps) ─
RAMP_COLORIZER = """
#version 330 core
""" + UNIFORM_BLOCK + """
out vec4 fragColor;

void main() {
    vec2 st = gl_FragCoord.xy / u_resolution;

    float hRamp   = st.x;
    float vRamp   = st.y;
    float dRamp   = length(st - 0.5) * 1.414;

    float hFreq   = 1.0 + p[0] * 8.0;
    float vFreq   = 1.0 + p[1] * 8.0;
    float hPhase  = p[2] * 6.283185 + u_time * 0.3;
    float vPhase  = p[3] * 6.283185 + u_time * 0.2;
    float rMix    = p[4];
    float gMix    = p[5];
    float bMix    = p[6];
    float lum     = 0.5 + p[7] * 0.5;

    float h = sin(hRamp * hFreq * 6.283185 + hPhase + u_bass  * 1.5) * 0.5 + 0.5;
    float v = sin(vRamp * vFreq * 6.283185 + vPhase + u_mid   * 1.5) * 0.5 + 0.5;
    float d = sin(dRamp * 4.0  * 6.283185           + u_treble * 2.0 + u_time * 0.5) * 0.5 + 0.5;

    float r = mix(h, d, rMix) * lum * (0.8 + u_bass   * 0.4);
    float g = mix(v, h, gMix) * lum * (0.8 + u_mid    * 0.4);
    float b = mix(d, v, bMix) * lum * (0.8 + u_treble * 0.4);

    r = clamp(r + u_beat * 0.2, 0.0, 1.0);
    g = clamp(g, 0.0, 1.0);
    b = clamp(b, 0.0, 1.0);

    fragColor = vec4(r, g, b, 1.0);
}
"""

# ── 4. FEEDBACK (video feedback loop — classic Paik-Abe / Jonas Bers) ────────
FEEDBACK_INIT = """
#version 330 core
""" + UNIFORM_BLOCK + """
out vec4 fragColor;

void main() {
    vec2 st = gl_FragCoord.xy / u_resolution;
    fragColor = vec4(st, 0.5, 1.0);
}
"""

FEEDBACK = """
#version 330 core
""" + UNIFORM_BLOCK + """
uniform sampler2D u_prev;
out vec4 fragColor;

vec3 hsv2rgb(vec3 c) {
    vec4 K = vec4(1.0, 2.0/3.0, 1.0/3.0, 3.0);
    vec3 p = abs(fract(c.xxx + K.xyz) * 6.0 - K.www);
    return c.z * mix(K.xxx, clamp(p - K.xxx, 0.0, 1.0), c.y);
}

vec2 rotate2D(vec2 p, float a) {
    float c = cos(a); float s = sin(a);
    return vec2(c*p.x - s*p.y, s*p.x + c*p.y);
}

void main() {
    vec2 st   = gl_FragCoord.xy / u_resolution;
    vec2 pos  = st - 0.5;
    pos.x    *= u_resolution.x / u_resolution.y;

    float zoom    = 0.992 + p[0] * 0.008;
    float rot     = (p[1] - 0.5) * 0.04 + u_bass * 0.005;
    float fbDecay = 0.92 + p[2] * 0.07;
    float hueShft = (p[3] - 0.5) * 0.02 + u_mid * 0.01;
    float noiseAmt= p[4] * 0.05;
    float injectR = p[5];
    float injectG = p[6];
    float injectB = p[7];

    vec2 feedUV = rotate2D(pos * zoom, rot) + 0.5;
    feedUV.y += (feedUV.y - 0.5) * (u_resolution.y / u_resolution.x - 1.0);

    vec4 prev = vec4(0.0);
    if (feedUV.x > 0.0 && feedUV.x < 1.0 && feedUV.y > 0.0 && feedUV.y < 1.0)
        prev = texture(u_prev, feedUV) * fbDecay;

    // Hue shift on feedback
    float lum = dot(prev.rgb, vec3(0.299, 0.587, 0.114));
    prev.rgb = mix(prev.rgb, vec3(lum), 0.05);
    prev.b = clamp(prev.b + hueShft * prev.r, 0.0, 1.0);
    prev.r = clamp(prev.r - hueShft * prev.b, 0.0, 1.0);

    // Inject audio-reactive sources
    float t = u_time;
    float src = 0.0;
    src += sin(st.x * 6.283185 * (2.0 + u_bass * 4.0) + t) * 0.5 + 0.5;
    src *= u_rms * (0.3 + injectR);

    prev.r += src * injectR * u_bass;
    prev.g += src * injectG * u_mid;
    prev.b += src * injectB * u_treble;
    prev.rgb += u_beat * 0.1 * vec3(injectR, injectG, injectB);

    fragColor = clamp(prev, 0.0, 1.0);
}
"""

# ── 5. KALEIDOSCOPE (geometric symmetry — C&G Eyesy inspired) ────────────────
KALEIDOSCOPE = """
#version 330 core
""" + UNIFORM_BLOCK + """
out vec4 fragColor;

vec3 hsv2rgb(vec3 c) {
    vec4 K = vec4(1.0, 2.0/3.0, 1.0/3.0, 3.0);
    vec3 p = abs(fract(c.xxx + K.xyz) * 6.0 - K.www);
    return c.z * mix(K.xxx, clamp(p - K.xxx, 0.0, 1.0), c.y);
}

void main() {
    vec2 st  = gl_FragCoord.xy / u_resolution;
    vec2 pos = st - 0.5;
    pos.x   *= u_resolution.x / u_resolution.y;

    float segments = 3.0 + floor(p[0] * 10.0);
    float zoom     = 0.5 + p[1] * 2.0;
    float rot      = p[2] * 6.283185 + u_time * (0.1 + u_mid * 0.3);
    float detail   = 1.0 + p[3] * 5.0;
    float hue      = p[4] + u_time * p[5] * 0.1;
    float sat      = 0.5 + p[6] * 0.5;
    float mirror   = p[7];

    float ang = atan(pos.y, pos.x);
    float rad = length(pos) * zoom;
    float seg = 6.283185 / segments;
    ang  = mod(ang + rot, seg);
    if (mirror > 0.5) ang = abs(ang - seg * 0.5);

    vec2 kpos = vec2(cos(ang), sin(ang)) * rad;

    float v  = sin(kpos.x * detail * 4.0 + u_time + u_bass * 2.0) * 0.5 + 0.5;
          v += sin(kpos.y * detail * 3.0 + u_time * 1.2 + u_mid) * 0.5 + 0.5;
          v += sin(rad * detail * 6.0 - u_time * 0.8 + u_treble) * 0.5 + 0.5;
          v /= 3.0;

    // FFT-modulated detail rings
    int binIdx = int(rad * 16.0) * 16;
    binIdx = clamp(binIdx, 0, 511);
    v += u_audio[binIdx] * 0.3;

    vec3 col = hsv2rgb(vec3(hue + v * 0.3, sat, v * (0.8 + u_rms * 0.4)));
    col += u_beat * 0.2;

    fragColor = vec4(col, 1.0);
}
"""

# ── 6. WAVEFORM_3D (spectral landscape — oscilloscope + FFT) ─────────────────
WAVEFORM_3D = """
#version 330 core
""" + UNIFORM_BLOCK + """
out vec4 fragColor;

vec3 hsv2rgb(vec3 c) {
    vec4 K = vec4(1.0, 2.0/3.0, 1.0/3.0, 3.0);
    vec3 p = abs(fract(c.xxx + K.xyz) * 6.0 - K.www);
    return c.z * mix(K.xxx, clamp(p - K.xxx, 0.0, 1.0), c.y);
}

void main() {
    vec2 st  = gl_FragCoord.xy / u_resolution;
    float x  = st.x;
    float y  = st.y;

    float hScroll  = p[0];
    float vStretch = 0.3 + p[1] * 1.5;
    float lineW    = 0.001 + p[2] * 0.02;
    float hue      = p[3];
    float sat      = 0.6 + p[4] * 0.4;
    float glowAmt  = 1.0 + p[5] * 3.0;
    float history  = 4.0 + p[6] * 12.0;
    float tiltAmt  = (p[7] - 0.5) * 0.5;

    float brightness = 0.0;
    int   LINES = 32;

    for (int line = 0; line < LINES; line++) {
        float fi   = float(line) / float(LINES);
        float tOff = fi * history;
        float yOff = fi * tiltAmt;

        // Map x to FFT bin
        int bin = int(x * 511.0);
        float h = u_audio[bin] * vStretch;

        // Also include time-delayed waveform
        float scrollX = mod(x + hScroll * fi, 1.0);
        int   binS    = int(scrollX * 511.0);
        h += u_audio[binS] * vStretch * 0.3;

        float lineY  = fi + yOff;
        float dist   = abs(y - lineY - h);
        float alpha  = (lineW * glowAmt) / (dist + 0.002);
        alpha       *= (1.0 - fi * 0.8);
        brightness  += alpha;
    }

    brightness = clamp(brightness, 0.0, 1.0);
    float h = hue + x * 0.3 + brightness * 0.1;
    vec3 col = hsv2rgb(vec3(h, sat, brightness));
    col *= 0.8 + u_rms * 0.6;
    col += u_beat * vec3(0.1, 0.05, 0.2);

    fragColor = vec4(col, 1.0);
}
"""

# ── 7. CIRCUIT_BENT (glitch / datamosh — Jonas Bers / Sandin IP aesthetic) ───
CIRCUIT_BENT = """
#version 330 core
""" + UNIFORM_BLOCK + """
out vec4 fragColor;

float hash(float n) { return fract(sin(n) * 43758.5453123); }

void main() {
    vec2 st  = gl_FragCoord.xy / u_resolution;
    vec2 pos = st;

    float glitchRate = p[0];
    float blockSize  = 0.01 + p[1] * 0.15;
    float rgbShift   = p[2] * 0.05;
    float scanMix    = p[3];
    float palIndex   = p[4];
    float noiseAmt   = p[5] * 0.3;
    float syncBreak  = p[6];
    float beatFlash  = p[7];

    // Horizontal sync tear
    float row = floor(pos.y / blockSize);
    float tearOff = hash(row + floor(u_time * 20.0 * glitchRate)) * glitchRate;
    tearOff *= u_bass;
    pos.x = fract(pos.x + tearOff * syncBreak);

    // Pixel block glitch
    vec2 blockUV   = floor(pos / blockSize) * blockSize;
    float blockRnd = hash(blockUV.x * 7.3 + blockUV.y * 13.7 + floor(u_time * 5.0));
    if (blockRnd < glitchRate * 0.1 * u_rms) pos = blockUV + blockSize * 0.5;

    // Audio-driven RGB shift
    float rShift = rgbShift * u_bass   * hash(floor(u_time * 30.0));
    float bShift = rgbShift * u_treble * hash(floor(u_time * 17.0) + 99.0);

    // Derive color from position + audio FFT
    int   binR = int(clamp(pos.x + rShift, 0.0, 1.0) * 511.0);
    int   binG = int(clamp(pos.x,          0.0, 1.0) * 511.0);
    int   binB = int(clamp(pos.x - bShift, 0.0, 1.0) * 511.0);

    float r = u_audio[binR] + sin(pos.y * 20.0 + u_time * 3.0) * 0.1;
    float g = u_audio[binG] * (0.5 + u_mid) + pos.y * 0.3;
    float b = u_audio[binB] + cos(pos.x * 15.0 + u_time * 2.0) * 0.1;

    // Palette index shift (maps luma to color palette)
    float luma = (r + g + b) / 3.0;
    float palH = fract(palIndex + luma * 0.5 + u_time * 0.05);
    vec3 pal   = vec3(sin(palH * 6.283185) * 0.5 + 0.5,
                      sin(palH * 6.283185 + 2.094) * 0.5 + 0.5,
                      sin(palH * 6.283185 + 4.189) * 0.5 + 0.5);

    // Scanlines
    float scan = 1.0 - scanMix * (mod(gl_FragCoord.y, 2.0) < 1.0 ? 0.4 : 0.0);

    // Noise
    float noise = (hash(pos.x * 1000.0 + pos.y * 337.0 + u_time * 50.0) - 0.5) * noiseAmt;

    vec3 col = mix(vec3(r, g, b), pal, 0.5) * scan + noise;
    col += u_beat * beatFlash * 0.3;

    fragColor = vec4(clamp(col, 0.0, 1.0), 1.0);
}
"""

# ── 8. HARMONIC_WEB (Rutt/Etra scan deflection lines — vector display) ───────
HARMONIC_WEB = """
#version 330 core
""" + UNIFORM_BLOCK + """
out vec4 fragColor;

vec3 hsv2rgb(vec3 c) {
    vec4 K = vec4(1.0, 2.0/3.0, 1.0/3.0, 3.0);
    vec3 p = abs(fract(c.xxx + K.xyz) * 6.0 - K.www);
    return c.z * mix(K.xxx, clamp(p - K.xxx, 0.0, 1.0), c.y);
}

void main() {
    vec2 st  = gl_FragCoord.xy / u_resolution;
    vec2 pos = st * 2.0 - 1.0;
    pos.x   *= u_resolution.x / u_resolution.y;

    float harm1  = 1.0 + p[0] * 8.0;
    float harm2  = 1.0 + p[1] * 8.0;
    float harm3  = 0.5 + p[2] * 4.0;
    float thick  = 0.003 + p[3] * 0.02;
    float hue    = p[4] + u_time * 0.02;
    float sat    = 0.5 + p[5] * 0.5;
    float travel = p[6];
    float modAmt = p[7] * 0.5;

    float t = u_time;
    float brightness = 0.0;

    // Horizontal harmonic lines deflected by audio
    int HLINES = 24;
    for (int i = 0; i < HLINES; i++) {
        float fi   = float(i) / float(HLINES);
        float lineY = fi * 2.0 - 1.0;

        int binIdx = int(fi * 511.0);
        float amod = u_audio[binIdx] * (0.3 + modAmt);

        float deflect  = sin(pos.x * harm1 * 3.14159 + t * (0.5 + fi) + u_bass * 1.5) * amod;
              deflect += sin(pos.x * harm2 * 3.14159 - t * 0.3 + u_mid) * amod * 0.5;

        float dist = abs(pos.y - lineY - deflect);
        brightness += thick / (dist + 0.001) * (0.5 + fi * 0.5);
    }

    // Vertical harmonic lines
    int VLINES = 16;
    for (int i = 0; i < VLINES; i++) {
        float fi   = float(i) / float(VLINES);
        float lineX = fi * 2.0 - 1.0;

        int binIdx = int((1.0 - fi) * 255.0);
        float amod = u_audio[binIdx + 256] * modAmt;

        float deflect = sin(pos.y * harm3 * 3.14159 + t * 0.4 + u_treble) * amod;
        float dist    = abs(pos.x - lineX - deflect);
        brightness   += (thick * 0.6) / (dist + 0.001) * (0.3 + fi * 0.3);
    }

    brightness = clamp(brightness / 8.0, 0.0, 1.0);

    float h  = hue + pos.y * 0.1 + brightness * 0.2;
    vec3 col = hsv2rgb(vec3(h, sat, brightness));
    col     += u_beat * 0.15;

    fragColor = vec4(col, 1.0);
}
"""

# ── 9. VIDEO FX (applies synthesis effects to a live video texture) ───────────
VIDEO_FX = """
#version 330 core
""" + UNIFORM_BLOCK + """
uniform sampler2D u_video;
uniform int       u_has_video;
out vec4 fragColor;

float hash(float n) { return fract(sin(n) * 43758.5453123); }

vec3 hsv2rgb(vec3 c) {
    vec4 K = vec4(1.0, 2.0/3.0, 1.0/3.0, 3.0);
    vec3 q = abs(fract(c.xxx + K.xyz) * 6.0 - K.www);
    return c.z * mix(K.xxx, clamp(q - K.xxx, 0.0, 1.0), c.y);
}

void main() {
    vec2 st  = gl_FragCoord.xy / u_resolution;
    vec2 uv  = vec2(st.x, 1.0 - st.y);    // flip Y for video coords

    float glitch  = p[0] * 0.08;
    float rgbSep  = p[1] * 0.025;
    float warp    = p[2] * 0.15;
    float hue     = (p[3] - 0.5) * 2.0;
    float sat     = p[4] * 2.0;
    float bright  = 0.3 + p[5] * 1.4;
    float edgeMix = p[6];
    float beatFlash = p[7];

    // Audio-reactive warp
    float wx = sin(uv.y * 18.0 + u_time * 2.5) * warp * u_bass  * 0.12;
    float wy = sin(uv.x * 14.0 + u_time * 1.8) * warp * u_mid   * 0.08;

    // Glitch scan tear
    float row  = floor(uv.y * 80.0);
    float tear = hash(row + floor(u_time * 15.0)) * glitch * (0.5 + u_rms);
    if (hash(row + 0.5 + floor(u_time * 8.0)) < glitch * u_rms * 2.0) tear *= 3.0;

    vec2 uvW = vec2(fract(uv.x + tear + wx), clamp(uv.y + wy, 0.0, 1.0));

    vec3 col;
    if (u_has_video != 0) {
        // Chromatic aberration on R/B channels
        float ra = rgbSep * (0.5 + u_bass);
        float ba = rgbSep * (0.5 + u_treble);
        col.r = texture(u_video, vec2(fract(uvW.x + ra), uvW.y)).r;
        col.g = texture(u_video, uvW).g;
        col.b = texture(u_video, vec2(fract(uvW.x - ba), uvW.y)).b;
    } else {
        // Plasma fallback — shows something useful before video is loaded
        vec2 pos = uv * 2.0 - 1.0;
        float v  = sin(pos.x * 4.0 + u_time)
                 + sin(pos.y * 3.0 + u_time * 1.1)
                 + sin(length(pos) * 6.0 - u_time * 0.8);
        col = hsv2rgb(vec3(v * 0.15 + u_time * 0.05, 0.85, 0.5 + v * 0.25));
    }

    // Edge detection
    if (edgeMix > 0.01 && u_has_video != 0) {
        float dx = 1.5 / u_resolution.x;
        float dy = 1.5 / u_resolution.y;
        vec3 n2 = texture(u_video, uvW + vec2(0,  dy)).rgb;
        vec3 s2 = texture(u_video, uvW - vec2(0,  dy)).rgb;
        vec3 e2 = texture(u_video, uvW + vec2(dx, 0 )).rgb;
        vec3 w2 = texture(u_video, uvW - vec2(dx, 0 )).rgb;
        vec3 edges = abs(n2 - s2) + abs(e2 - w2);
        col = mix(col, col * 0.2 + edges * 2.5, edgeMix);
    }

    // Hue rotate
    float angle = hue + u_time * 0.05 * sign(hue);
    float cosA  = cos(angle); float sinA = sin(angle);
    mat3 R = mat3(
        0.299 + 0.701*cosA + 0.168*sinA,  0.587 - 0.587*cosA + 0.330*sinA,  0.114 - 0.114*cosA - 0.497*sinA,
        0.299 - 0.299*cosA - 0.328*sinA,  0.587 + 0.413*cosA + 0.035*sinA,  0.114 - 0.114*cosA + 0.292*sinA,
        0.299 - 0.300*cosA + 1.250*sinA,  0.587 - 0.588*cosA - 1.050*sinA,  0.114 + 0.886*cosA - 0.203*sinA
    );
    col = R * col;

    // Saturation + brightness
    float luma = dot(col, vec3(0.299, 0.587, 0.114));
    col = mix(vec3(luma), col, sat) * bright;

    col += u_beat * beatFlash * 0.25 * vec3(0.9, 0.5, 0.2);

    fragColor = vec4(clamp(col, 0.0, 1.0), 1.0);
}
"""

# ── Compositor blend shader ───────────────────────────────────────────────────
# Used as a second pass to mix any synthesis engine output with the video layer.
# u_synth  = synthesis FBO texture (unit 0)
# u_video  = live video texture    (unit 1)
BLEND_FRAG = """
#version 330 core
uniform sampler2D u_bg;       // accumulated chain so far (main engine + previous layers)
uniform sampler2D u_layer;    // this layer's engine output (standalone)
uniform float     u_mix;      // 0 = bg unchanged, 1 = full layer
uniform int       u_blend_mode;
in vec2 uv;
out vec4 fragColor;

void main() {
    vec3 bg = texture(u_bg,    uv).rgb;
    vec3 ly = texture(u_layer, uv).rgb;
    float m = clamp(u_mix, 0.0, 1.0);
    vec3 r;

    if (u_blend_mode == 0) {
        r = bg + ly * m;                                         // add
    } else if (u_blend_mode == 1) {
        r = mix(bg, ly, m);                                      // mix
    } else if (u_blend_mode == 2) {
        r = mix(bg, bg * ly, m);                                 // multiply
    } else if (u_blend_mode == 3) {
        r = mix(bg, 1.0 - (1.0 - bg) * (1.0 - ly), m);         // screen
    } else if (u_blend_mode == 4) {
        vec3 ov;
        ov.r = bg.r < 0.5 ? 2.0*bg.r*ly.r : 1.0 - 2.0*(1.0-bg.r)*(1.0-ly.r);
        ov.g = bg.g < 0.5 ? 2.0*bg.g*ly.g : 1.0 - 2.0*(1.0-bg.g)*(1.0-ly.g);
        ov.b = bg.b < 0.5 ? 2.0*bg.b*ly.b : 1.0 - 2.0*(1.0-bg.b)*(1.0-ly.b);
        r = mix(bg, ov, m);                                      // overlay
    } else {
        r = mix(bg, abs(bg - ly), m);                            // difference
    }

    fragColor = vec4(clamp(r, 0.0, 1.0), 1.0);
}
"""

# ── Tiamat engine ────────────────────────────────────────────────────────────
# Exact copy of the Synth Studio shader — identical uniform names and logic —
# so presets load with pixel-perfect matching output in both apps.
# Only changes from the Studio _FRAG:
#   u_video/u_has_video  instead of  u_src0/u_has_src0
#   u_prev               instead of  u_prev_frame/u_has_prev
#   cam() flips Y: VIDEO LIFE uploads frames without Y-flip (Studio pre-flips)
# ALL params sent as named uniforms from _studio_params — p[] not used.
INTERCEPT = """
#version 330 core

uniform float u_time;
uniform vec2  u_resolution;
uniform float u_rms, u_bass, u_mid, u_treble, u_beat;

uniform sampler2D u_video;
uniform int       u_has_video;
uniform sampler2D u_prev;

// Warp
uniform int   u_warp_mode;
uniform float u_warp_amt, u_warp_spd, u_warp_freq, u_warp_twist;

// Feedback
uniform float u_feedback;
uniform int   u_fb_mode;
uniform float u_fb_zoom, u_fb_rotate, u_fb_hue_shift;

// Glitch
uniform int   u_glitch_mode;
uniform float u_glitch_amt, u_chroma, u_glitch_rate, u_glitch_scale;

// Spatial
uniform float u_zoom;
uniform int   u_mirror;

// Palette
uniform int   u_pal_mode;
uniform vec3  u_pal_0, u_pal_1, u_pal_2, u_pal_3;

// Color
uniform float u_hue, u_sat, u_contrast, u_posterize;

// Channels
uniform float u_rgb_r, u_rgb_g, u_rgb_b, u_invert;

// Edge
uniform float u_edge_mix, u_edge_hue;

// Texture
uniform float u_grain, u_rf, u_scanlines;

// LFO
uniform float u_lfo_rate, u_lfo_depth;
uniform int   u_lfo_target;

// React / output
uniform float u_react, u_beat_punch, u_mix, u_gain;

out vec4 fragColor;

float hash1(float n){return fract(sin(n)*43758.5453);}
float hash2(vec2 v){return fract(sin(dot(v,vec2(127.1,311.7)))*43758.5);}
vec3  hash3(vec2 v){
    return fract(sin(vec3(dot(v,vec2(127.1,311.7)),dot(v,vec2(269.5,183.3)),
                          dot(v,vec2(419.2,371.9))))*43758.5);
}
// VIDEO LIFE uploads frames without Y-flip; flip here so orientation matches Studio.
vec4 cam(vec2 st) {
    vec2 uv=vec2(st.x, 1.0-st.y);
    return u_has_video==1?texture(u_video,clamp(uv,0.001,0.999)):vec4(0.12);
}
// Prev-frame is a rendered FBO (OpenGL orientation), sample as-is.
vec4 prv(vec2 st) {return texture(u_prev, clamp(st,0.001,0.999));}

vec3 hue_rot(vec3 c,float h){
    float Y=dot(c,vec3(0.299,0.587,0.114));
    float I=dot(c,vec3(0.596,-0.275,-0.321));
    float Q=dot(c,vec3(0.212,-0.523,0.311));
    float cs=cos(h);float sn=sin(h);
    return clamp(vec3(Y+0.956*(cs*I-sn*Q)+0.621*(sn*I+cs*Q),
                      Y-0.272*(cs*I-sn*Q)-0.647*(sn*I+cs*Q),
                      Y-1.107*(cs*I-sn*Q)+1.704*(sn*I+cs*Q)),0.0,1.0);
}
vec3 custom_pal(vec3 c){
    float l=dot(c,vec3(0.299,0.587,0.114));
    if(l<0.333)return mix(u_pal_0,u_pal_1,l/0.333);
    else if(l<0.667)return mix(u_pal_1,u_pal_2,(l-0.333)/0.334);
    else return mix(u_pal_2,u_pal_3,(l-0.667)/0.333);
}

void main(){
    vec2  st    = gl_FragCoord.xy/u_resolution;
    float lineY = floor(st.y*u_resolution.y);
    float t     = u_time;

    // Named uniforms — loaded directly from _studio_params, same as Studio shader
    float warp_amt = u_warp_amt;
    float fb_amt   = u_feedback;
    float glitch_a = u_glitch_amt;
    float chroma   = u_chroma;
    float sat      = u_sat;
    float hue_v    = u_hue;
    float gain     = u_gain;
    float react    = u_react;

    float beat  = u_beat*react*u_beat_punch;
    float audio = u_rms*react;

    float lfo = sin(t*u_lfo_rate*18.8496)*u_lfo_depth;
    float ew  = clamp(warp_amt +(u_lfo_target==0?lfo*warp_amt :0.0),0.0,2.0);
    float ef  = clamp(fb_amt   +(u_lfo_target==1?lfo*0.3      :0.0),0.0,0.97);
    float eg  = clamp(glitch_a +(u_lfo_target==2?lfo*glitch_a :0.0),0.0,2.0);
    float ec  = clamp(chroma   +(u_lfo_target==3?lfo*chroma   :0.0),0.0,1.0);

    float cam_scale = pow(2.0,u_zoom*2.0-1.0);
    vec2  wuv = (st-0.5)/cam_scale+0.5;
    float wfreq = 0.5+u_warp_freq*3.5;

    if(u_warp_mode==1){
        float spd=u_warp_spd*2.0;
        float amp=ew*(0.08+audio*0.10);
        float d=sin(lineY*0.031*wfreq+t*spd*0.7)*amp
               +sin(lineY*0.073*wfreq+t*spd*1.3)*amp*0.55
               +sin(lineY*0.19 *wfreq+t*spd*2.1)*amp*0.28
               +sin(lineY*0.007*wfreq+t*spd*0.2)*amp*1.2;
        d+=u_bass*react*0.04*sin(lineY*0.05+t*5.0)+beat*0.05*sin(lineY*0.03+t*3.0);
        float sT=floor(t*1.5*u_warp_spd+hash1(floor(t*0.3))*3.0);
        float sTp=hash1(sT);float sW=0.04+hash1(sT+0.5)*0.18;
        d+=(hash1(sT+2.0)-0.5)*ew*0.40*step(sTp,st.y)*step(st.y,sTp+sW);
        vec2 disp=vec2(d,0.0);
        if(u_warp_twist>0.005){float ang=(st.x-0.5)*u_warp_twist*6.28318;
            float ca=cos(ang);float sa=sin(ang);
            disp=vec2(disp.x*ca-disp.y*sa,disp.x*sa+disp.y*ca);}
        wuv+=disp;
    }else if(u_warp_mode==2){
        float str=ew*(0.25+u_bass*react*0.30+beat*0.15);
        float asp=u_resolution.x/u_resolution.y;float spd=u_warp_spd;
        vec2 m0=vec2(0.5+sin(t*0.23*spd)*0.35,0.5+cos(t*0.17*spd)*0.30);
        vec2 m1=vec2(0.5+sin(t*0.31*spd+2.1)*0.30,0.5+cos(t*0.19*spd+1.1)*0.35);
        vec2 m2=vec2(0.5+sin(t*0.13*spd+4.2)*0.20,0.5+cos(t*0.29*spd+3.0)*0.22);
        vec2 d0=st-m0;d0.x*=asp;vec2 d1=st-m1;d1.x*=asp;vec2 d2=st-m2;d2.x*=asp;
        vec2 warp=(normalize(d0)/(length(d0)+0.001))*str*0.055
                 -(normalize(d1)/(length(d1)+0.001))*str*0.040
                 +(normalize(d2)/(length(d2)+0.001))*str*0.025;
        if(u_warp_twist>0.005){float ang=length(st-0.5)*u_warp_twist*12.566;
            float ca=cos(ang);float sa=sin(ang);
            warp=vec2(warp.x*ca-warp.y*sa,warp.x*sa+warp.y*ca);}
        wuv+=clamp(warp,-0.5,0.5);
    }else if(u_warp_mode==3){
        float amp=ew*(0.12+audio*0.10);vec2 q=wuv;float wf=wfreq;
        q+=0.05*vec2(sin(q.y*3.1*wf+t*u_warp_spd*1.4),cos(q.x*2.9*wf+t*u_warp_spd));
        q+=0.04*vec2(cos(q.x*5.3*wf-t*u_warp_spd*2.2),sin(q.y*4.7*wf+t*u_warp_spd*1.8));
        q+=0.03*vec2(sin(q.y*7.1*wf+q.x*2.0+t*u_warp_spd),
                     cos(q.x*6.3*wf-q.y*1.7+t*u_warp_spd*0.8));
        if(u_warp_twist>0.005){vec2 cen=q-0.5;float ang=length(cen)*u_warp_twist*8.0;
            float ca=cos(ang);float sa=sin(ang);
            q=0.5+vec2(cen.x*ca-cen.y*sa,cen.x*sa+cen.y*ca);}
        wuv=mix(wuv,q,amp*3.0);
    }

    if(u_mirror==1||u_mirror==3){if(wuv.x>0.5)wuv.x=1.0-wuv.x;}
    if(u_mirror==2||u_mirror==3){if(wuv.y>0.5)wuv.y=1.0-wuv.y;}

    float chr=ec*0.025*(1.0+u_treble*react);
    vec3 col=vec3(cam(fract(wuv+vec2(chr,0.0))).r,
                  cam(fract(wuv)).g,
                  cam(fract(wuv-vec2(chr,0.0))).b)*u_mix;

    vec3 edge_col=vec3(0.0);
    if(u_edge_mix>0.005){
        float dx=1.5/u_resolution.x;float dy=1.5/u_resolution.y;
        vec3 n2=cam(wuv+vec2(0,dy)).rgb;vec3 s2=cam(wuv-vec2(0,dy)).rgb;
        vec3 e2=cam(wuv+vec2(dx,0)).rgb;vec3 w2=cam(wuv-vec2(dx,0)).rgb;
        float el=length(abs(n2-s2)+abs(e2-w2));
        edge_col=u_edge_hue>0.005?hue_rot(vec3(el*2.0),u_edge_hue*6.28318):vec3(el*2.0);
    }

    float gscale=max(2.0,32.0-u_glitch_scale*28.0);
    if(u_glitch_mode==1){
        float corr=eg*(1.0+audio*2.0+beat); float bg=floor(st.y*u_resolution.y/gscale);
        float rs=hash1(bg+floor(t*(3.0+eg*15.0)));float thr=1.0-u_glitch_rate*0.6;
        float rd=(rs-0.5)*corr*0.18*step(thr,rs);
        col.r=cam(fract(wuv+vec2(rd+eg*0.030,0.0))).r*u_mix;
        col.b=cam(fract(wuv-vec2(eg*0.030,0.0))).b*u_mix;
        float bn=hash2(floor(st*u_resolution)+floor(t*40.0*u_glitch_rate));
        if(step(1.0-eg*0.22*u_glitch_rate,bn)>0.0)col=1.0-col;
        if(eg>0.4){vec2 bk=floor(st*u_resolution/max(1.0,gscale));
            float bh=hash2(bk+floor(t*2.0));
            if(bh>1.0-eg*0.25*u_glitch_rate)col=hash3(bk+1.3);}
    }else if(u_glitch_mode==2){
        float base=1.0-eg*(0.65+audio*0.50);
        float tR=clamp(base*(1.0+ec*0.22),0.03,0.97);
        float tG=clamp(base,0.03,0.97);float tB=clamp(base*(1.0-ec*0.18),0.03,0.97);
        col.r=mix(col.r,1.0-col.r,smoothstep(tR-0.05,tR+0.05,col.r));
        col.g=mix(col.g,1.0-col.g,smoothstep(tG-0.05,tG+0.05,col.g));
        col.b=mix(col.b,1.0-col.b,smoothstep(tB-0.05,tB+0.05,col.b));
        col=mix(col,1.0-col,beat*0.9);
        float lm=dot(col,vec3(0.299,0.587,0.114));
        col=mix(col,vec3(1.0),smoothstep(0.5,1.0,lm)*eg*0.8);
    }else if(u_glitch_mode==3){
        float str=eg*(0.55+audio*0.50+beat*0.40)*u_glitch_rate;
        float n=hash2(floor(st*u_resolution)+floor(t*60.0));
        col=mix(col,hash3(floor(st*180.0)+floor(t*60.0)),str*step(0.4,n));
    }

    if(ef>0.005){
        float fbz=0.97+u_fb_zoom*0.06;float fbr=(u_fb_rotate-0.5)*0.08;
        vec2 fbv=st;vec2 dv=fbv-0.5;
        float cs=cos(fbr);float sn=sin(fbr);
        fbv=0.5+vec2(dv.x*cs-dv.y*sn,dv.x*sn+dv.y*cs);
        fbv=(fbv-0.5)/fbz+0.5;
        vec3 pv=prv(fbv).rgb;
        if(u_fb_hue_shift>0.005)pv=hue_rot(pv,u_fb_hue_shift*0.3);
        if(u_fb_mode==0){col=mix(col,pv,ef);}
        else if(u_fb_mode==1){vec2 disp=(pv.rg-0.5)*ef*0.55;
            vec3 pcam=cam(fract(wuv+disp)).rgb*u_mix;
            col=mix(pcam,prv(fbv+disp*0.5).rgb,ef*0.7)+col*(1.0-ef*0.6);}
        else if(u_fb_mode==2){vec3 burned=clamp(col*pv*2.5,0.0,1.0);col=mix(col,burned,ef);}
        else{float angle=t*u_warp_spd*0.5;vec2 dir=vec2(cos(angle),sin(angle))*ef*0.018;
            col=mix(col,prv(fract(fbv-dir)).rgb,ef*0.85);}
    }

    if(u_edge_mix>0.005)col=mix(col,col*0.3+edge_col,u_edge_mix);

    if(u_pal_mode==1){col=custom_pal(col);}
    else if(u_pal_mode==2){float l=dot(col,vec3(0.299,0.587,0.114));col=vec3(l*0.10,l*1.05,l*0.18);}
    else if(u_pal_mode==3){float l=dot(col,vec3(0.299,0.587,0.114));
        col=mix(vec3(0.05,0.07,0.55),vec3(1.0,0.22,0.04),l);}
    else if(u_pal_mode==4){col=1.0-col;}

    if(abs(hue_v)>0.005)col=hue_rot(col,hue_v*3.14159);
    float lum=dot(col,vec3(0.299,0.587,0.114));
    col=mix(vec3(lum),col,sat);
    col=(col-0.5)*u_contrast+0.5;
    if(u_posterize>0.005){float steps=max(2.0,floor(mix(32.0,2.0,u_posterize)));
        col=floor(col*steps+0.5)/steps;}
    col=clamp(col,0.0,1.0);

    col.r*=u_rgb_r*2.0;col.g*=u_rgb_g*2.0;col.b*=u_rgb_b*2.0;
    if(u_invert>0.005)col=mix(col,1.0-col,u_invert);
    col=clamp(col,0.0,1.0);

    col+=(hash2(st*u_resolution+t*71.0)-0.5)*u_grain*(0.18+audio*0.12);
    if(u_rf>0.005){float rfB=sin(st.y*75.0+t*7.0)*0.5+0.5;
        float rfN=hash2(vec2(st.x*512.0,lineY)+floor(t*60.0));
        col+=rfB*rfN*u_rf*(0.20+audio*0.20);}
    if(u_scanlines>0.005){float sl=0.80+0.20*sin(st.y*u_resolution.y*3.14159*2.0);
        col*=mix(1.0,sl,u_scanlines);}

    col*=mix(0.3,2.2,gain);
    fragColor=vec4(clamp(col,0.0,1.0),1.0);
}
"""

SHADERS = {
    "Lissajous":      LISSAJOUS,
    "Plasma":         PLASMA,
    "Ramp Colorizer": RAMP_COLORIZER,
    "Feedback":       FEEDBACK,
    "Kaleidoscope":   KALEIDOSCOPE,
    "Waveform 3D":    WAVEFORM_3D,
    "Circuit Bent":   CIRCUIT_BENT,
    "Harmonic Web":   HARMONIC_WEB,
    "Video FX":       VIDEO_FX,
    "Tiamat":         INTERCEPT,
}

PARAM_NAMES = {
    "Lissajous":      ["Freq X", "Freq Y", "Phase", "Thickness", "Glow", "Zoom", "Hue", "Saturation"],
    "Plasma":         ["Scale", "Speed", "H-Freq", "V-Freq", "Hue", "Hue Speed", "Saturation", "Brightness"],
    "Ramp Colorizer": ["H-Freq", "V-Freq", "H-Phase", "V-Phase", "Red Mix", "Green Mix", "Blue Mix", "Luma"],
    "Feedback":       ["Zoom", "Rotation", "Decay", "Hue Shift", "Noise", "Inject R", "Inject G", "Inject B"],
    "Kaleidoscope":   ["Segments", "Zoom", "Rotation", "Detail", "Hue", "Hue Speed", "Saturation", "Mirror"],
    "Waveform 3D":    ["H-Scroll", "V-Stretch", "Line Width", "Hue", "Saturation", "Glow", "History", "Tilt"],
    "Circuit Bent":   ["Glitch Rate", "Block Size", "RGB Shift", "Scanlines", "Palette", "Noise", "Sync Break", "Beat Flash"],
    "Harmonic Web":   ["Harm 1", "Harm 2", "Harm 3", "Thickness", "Hue", "Saturation", "Travel", "Modulation"],
    "Video FX":       ["Glitch", "RGB Sep", "Warp", "Hue Rotate", "Saturation", "Brightness", "Edge Mix", "Beat Flash"],
    "Tiamat":         ["Warp", "Feedback", "Glitch", "Chroma", "Sat", "Hue", "Gain", "React"],
}

PARAM_DEFAULTS = {
    "Lissajous":      [0.3, 0.4, 0.15, 0.3, 0.4, 0.4, 0.65, 0.8],
    "Plasma":         [0.3, 0.3, 0.4, 0.3, 0.6, 0.2, 0.8, 0.7],
    "Ramp Colorizer": [0.3, 0.3, 0.0, 0.0, 0.7, 0.4, 0.5, 0.8],
    "Feedback":       [0.5, 0.45, 0.7, 0.5, 0.2, 0.6, 0.4, 0.5],
    "Kaleidoscope":   [0.5, 0.3, 0.0, 0.4, 0.7, 0.2, 0.8, 1.0],
    "Waveform 3D":    [0.0, 0.4, 0.3, 0.3, 0.8, 0.5, 0.4, 0.5],
    "Circuit Bent":   [0.3, 0.2, 0.3, 0.5, 0.4, 0.15, 0.6, 0.5],
    "Harmonic Web":   [0.4, 0.5, 0.3, 0.3, 0.4, 0.8, 0.3, 0.5],
    "Video FX":       [0.0, 0.0, 0.0, 0.5, 0.5, 0.6, 0.0, 0.3],
    "Tiamat":         [0.0, 0.0, 0.0, 0.0, 0.25, 0.5, 0.65, 0.5],
    #                  warp fb  glt  chr   sat  hue  gain react
    #                  0=off    0=none     0.25→sat=1  0.5→hue=0
}
