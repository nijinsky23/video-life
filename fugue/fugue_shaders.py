"""
FUGUE — GLSL compositing shaders.

Inspired by the analog video mixers and early digital compositors of the
late 1980s / early 1990s — Panasonic WJ series, Grass Valley switchers,
Video Toaster, early Quantel.

Deliberately imperfect:
  · Luma key edges carry noise and slight colour contamination
  · Chroma key has additive spill rather than clean suppression
  · Mix modes run at slightly different bit depths per channel
  · No anti-aliased edges — all cuts are hard until softened by the
    threshold envelope
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

FRAG = """
#version 330 core

uniform float u_time;
uniform vec2  u_resolution;

// ── Sources — video tracks A, B, C, D ────────────────────────────────────────
uniform sampler2D u_src0, u_src1, u_src2, u_src3;
uniform int       u_has0, u_has1, u_has2, u_has3;

// ── Per-track composite controls ──────────────────────────────────────────────
// mix: 0.0 = absent, 1.0 = full
// mode: 0=Off  1=Mix  2=Add  3=Screen  4=Multiply  5=Difference  6=Hard-light
uniform float u_mix0, u_mix1, u_mix2, u_mix3;
uniform int   u_mode0, u_mode1, u_mode2, u_mode3;

// ── Keying ─────────────────────────────────────────────────────────────────────
// u_key_src: which track carries the key signal (0-3, -1 = none)
// u_key_mode: 0=none  1=luma_dark (key shadows out)
//             2=luma_bright (key highlights out)  3=chroma
uniform int   u_key_src;
uniform int   u_key_mode;
uniform float u_key_thresh;    // 0.0–1.0 luminance / distance cut point
uniform float u_key_soft;      // 0.0–0.5 edge blend width
uniform vec3  u_chroma_col;    // target colour for chroma key

// ── Output processing ─────────────────────────────────────────────────────────
uniform float u_gain;       // output level (0–1 → 0.2–2.0)
uniform float u_sat;        // saturation (0–2, 1.0 = unity)
uniform float u_contrast;   // contrast   (0–2, 1.0 = unity)
uniform float u_scanlines;  // CRT scanline strength (0–1)

// ── Difference key (PhotoBooth-style background subtraction) ─────────────────
// Grab a reference plate (empty background), then subtract it live.
// Pixels that differ from the plate → foreground (kept); matching → background (replaced).
//
// Inspired by early Quantel and Grass Valley difference keyers — those units
// had about 6-bit reference memory, so you'd get banding and creeping noise at
// the boundary. We replicate that fidelity limit with the edge_noise primitive.
uniform int       u_bg_sub_on;   // 0 = bypass, 1 = active
uniform int       u_bg_fg_src;   // subject track 0-3
uniform int       u_bg_bg_src;   // replacement background track 0-3
uniform sampler2D u_bg_ref;      // grabbed reference plate (background without subject)
uniform int       u_has_bg_ref;  // 1 = plate has been grabbed
uniform float     u_bg_thresh;   // difference threshold  (0.0–0.5)
uniform float     u_bg_soft;     // smoothstep edge width (0.0–0.2)
uniform float     u_bg_blur;     // spatial blur radius in pixels (0.0–8.0)

out vec4 fragColor;

// ── Utilities ─────────────────────────────────────────────────────────────────

float lum(vec3 c) { return dot(c, vec3(0.299, 0.587, 0.114)); }

float hash1(float n) { return fract(sin(n) * 43758.5453); }
float hash2(vec2  v) { return fract(sin(dot(v, vec2(127.1, 311.7))) * 43758.5); }

vec3 safe(sampler2D tex, vec2 st, int has) {
    return has == 1 ? texture(tex, clamp(st, 0.001, 0.999)).rgb : vec3(0.0);
}

// Serial blend: composite 'over' on top of 'base' using the chosen mode
vec3 blend(vec3 base, vec3 over, int mode) {
    if (mode == 2) {
        // ADD — additive mix: the broadcast overload look
        return base + over;
    } else if (mode == 3) {
        // SCREEN — non-clipping add, gentler than pure add
        return 1.0 - (1.0 - base) * (1.0 - over);
    } else if (mode == 4) {
        // MULTIPLY — darkens, emphasises shadows; scale x2 so midtones survive
        return clamp(base * over * 2.0, 0.0, 1.0);
    } else if (mode == 5) {
        // DIFFERENCE — psychedelic colour inversion at crossing point
        return abs(base - over);
    } else if (mode == 6) {
        // HARD LIGHT — contrast-boosting overlay, favourite of 90s title cards
        vec3 r;
        r.r = over.r < 0.5 ? 2.0*base.r*over.r : 1.0-2.0*(1.0-base.r)*(1.0-over.r);
        r.g = over.g < 0.5 ? 2.0*base.g*over.g : 1.0-2.0*(1.0-base.g)*(1.0-over.g);
        r.b = over.b < 0.5 ? 2.0*base.b*over.b : 1.0-2.0*(1.0-base.b)*(1.0-over.b);
        return r;
    }
    // mode == 1: MIX / normal — just return 'over' (mix amount applied outside)
    return over;
}

// Luma key alpha: 1.0 = keep, 0.0 = cut
// mode 1 = key darks (shadows become transparent, foreground shows over brights)
// mode 2 = key brights (highlights become transparent)
float luma_alpha(vec3 col, int mode, float thresh, float soft) {
    float l    = lum(col);
    float edge = max(soft, 0.001);
    if (mode == 1) {
        // Key out dark areas — alpha goes 0 below thresh, rises to 1 above
        return smoothstep(thresh - edge, thresh + edge, l);
    } else {
        // Key out bright areas — alpha goes 1 below thresh, drops to 0 above
        return 1.0 - smoothstep(thresh - edge, thresh + edge, l);
    }
}

// Chroma key alpha: 1.0 = keep, 0.0 = cut (colour matches key colour)
float chroma_alpha(vec3 col, vec3 target, float thresh, float soft) {
    // Distance in colour space (unweighted — crude, intentionally analog)
    float dist = length(col - target);
    float edge = max(soft, 0.001);
    return smoothstep(thresh - edge, thresh + edge, dist);
}

// Analog edge noise: adds a little hash noise along the key boundary
// Simulates the low-bandwidth vertical interval of analog keyers
float edge_noise(vec2 st, float alpha, float t) {
    float boundary = 1.0 - abs(alpha * 2.0 - 1.0);   // peaks at alpha=0.5
    float n = hash2(floor(st * u_resolution * 0.5) + floor(t * 30.0));
    return boundary * (n - 0.5) * 0.18;
}

// ── Difference-key helpers ────────────────────────────────────────────────────

// Sample any of the four source tracks by runtime index.
// All four samplers are uniforms, so this is dynamically-uniform branching —
// every fragment takes the same path, which is valid in GLSL 3.30.
vec3 src_at(int i, vec2 st) {
    if (i == 0) return safe(u_src0, st, u_has0);
    if (i == 1) return safe(u_src1, st, u_has1);
    if (i == 2) return safe(u_src2, st, u_has2);
    return safe(u_src3, st, u_has3);
}

// Perceptual difference: luma-weighted per-channel absolute error.
// Matches how cheap CCD cameras responded — luminance dominated, colour secondary.
float rgb_diff(vec3 a, vec3 b) {
    vec3 d = abs(a - b);
    return dot(d, vec3(0.299, 0.587, 0.114));
}

// 5-tap cross-shaped spatial blur of the difference value.
// When u_bg_blur == 0, px == vec2(0) and all five taps collapse to centre —
// effectively a single sample, zero overhead.
float diff_mask(int fg_src, vec2 st) {
    vec2 px = max(u_bg_blur, 0.0) / u_resolution;

    vec3 fc = src_at(fg_src, st);
    vec3 f1 = src_at(fg_src, st + vec2( px.x, 0.0));
    vec3 f2 = src_at(fg_src, st + vec2(-px.x, 0.0));
    vec3 f3 = src_at(fg_src, st + vec2(0.0,  px.y));
    vec3 f4 = src_at(fg_src, st + vec2(0.0, -px.y));

    vec3 rc = texture(u_bg_ref, st).rgb;
    vec3 r1 = texture(u_bg_ref, st + vec2( px.x, 0.0)).rgb;
    vec3 r2 = texture(u_bg_ref, st + vec2(-px.x, 0.0)).rgb;
    vec3 r3 = texture(u_bg_ref, st + vec2(0.0,  px.y)).rgb;
    vec3 r4 = texture(u_bg_ref, st + vec2(0.0, -px.y)).rgb;

    float d = rgb_diff(fc, rc) + rgb_diff(f1, r1) + rgb_diff(f2, r2)
            + rgb_diff(f3, r3) + rgb_diff(f4, r4);
    d /= 5.0;

    float soft = max(u_bg_soft, 0.001);
    // Smooth threshold: 1.0 = foreground (changed), 0.0 = background (matches plate)
    return smoothstep(u_bg_thresh - soft, u_bg_thresh + soft, d);
}

// ── Main ──────────────────────────────────────────────────────────────────────

void main() {
    vec2 st = gl_FragCoord.xy / u_resolution;

    // ── Sample all four sources ───────────────────────────────────────────────
    vec3 s0 = safe(u_src0, st, u_has0);
    vec3 s1 = safe(u_src1, st, u_has1);
    vec3 s2 = safe(u_src2, st, u_has2);
    vec3 s3 = safe(u_src3, st, u_has3);

    // ── Compute key alpha for the designated source ───────────────────────────
    // Alpha = how much the keyed layer is kept (1.0 = fully opaque)
    float ka0 = 1.0, ka1 = 1.0, ka2 = 1.0, ka3 = 1.0;

    if (u_key_mode > 0 && u_key_src >= 0) {
        vec3 kc;
        if      (u_key_src == 0) kc = s0;
        else if (u_key_src == 1) kc = s1;
        else if (u_key_src == 2) kc = s2;
        else                      kc = s3;

        float raw_alpha;
        if (u_key_mode == 3) {
            raw_alpha = chroma_alpha(kc, u_chroma_col, u_key_thresh, u_key_soft);
            // Chroma spill: tint the transition edge with the key colour
            // — the classic 'green edge' you got from cheap chroma keyers
        } else {
            raw_alpha = luma_alpha(kc, u_key_mode, u_key_thresh, u_key_soft);
        }

        // Add analog noise at the boundary (deliberate keyer artifact)
        float noisy = clamp(raw_alpha + edge_noise(st, raw_alpha, u_time), 0.0, 1.0);

        if      (u_key_src == 0) ka0 = noisy;
        else if (u_key_src == 1) ka1 = noisy;
        else if (u_key_src == 2) ka2 = noisy;
        else                      ka3 = noisy;
    }

    // ── Serial layer composite A → B → C → D ─────────────────────────────────
    // Track A (slot 0) is always the base layer.
    // Subsequent tracks blend over the running composite.

    vec3 result = vec3(0.0);

    // Track A — base
    if (u_has0 == 1 && u_mode0 != 0) {
        result = s0 * (u_mix0 * ka0);
    }

    // Track B
    if (u_has1 == 1 && u_mode1 != 0) {
        float alpha = u_mix1 * ka1;
        vec3  over  = blend(result, s1, u_mode1);
        result      = mix(result, over, alpha);
    }

    // Track C
    if (u_has2 == 1 && u_mode2 != 0) {
        float alpha = u_mix2 * ka2;
        vec3  over  = blend(result, s2, u_mode2);
        result      = mix(result, over, alpha);
    }

    // Track D
    if (u_has3 == 1 && u_mode3 != 0) {
        float alpha = u_mix3 * ka3;
        vec3  over  = blend(result, s3, u_mode3);
        result      = mix(result, over, alpha);
    }

    // ── Difference key composite (overrides normal layer chain when active) ─────
    // When the plate is grabbed and the effect is on, ignore the blend-mode chain
    // above and instead composite subject-over-replacement using the live diff mask.
    // The output processing (sat / contrast / gain) still applies afterward.
    if (u_bg_sub_on == 1 && u_has_bg_ref == 1) {
        vec3  fg    = src_at(u_bg_fg_src, st);
        vec3  bg    = src_at(u_bg_bg_src, st);
        float mask  = diff_mask(u_bg_fg_src, st);

        // Analog-keyer boundary noise — mimics the jitter of a Quantel ref store
        mask = clamp(mask + edge_noise(st, mask, u_time) * 0.6, 0.0, 1.0);

        result = mix(bg, fg, mask);
    }

    // ── Output processing ─────────────────────────────────────────────────────

    // Saturation (mix towards grey)
    float l = lum(result);
    result  = mix(vec3(l), result, u_sat);

    // Contrast (pivot at 0.5)
    result  = (result - 0.5) * u_contrast + 0.5;

    // Gain (0 → 0.2 scale, 1 → 2.0 scale)
    result *= mix(0.2, 2.0, u_gain);

    // CRT scanlines — every other line darkened slightly
    if (u_scanlines > 0.005) {
        float sl = 0.80 + 0.20 * sin(st.y * u_resolution.y * 3.14159 * 2.0);
        result  *= mix(1.0, sl, u_scanlines);
    }

    fragColor = vec4(clamp(result, 0.0, 1.0), 1.0);
}
"""

# Blend mode labels (index matches u_mode* values in FRAG)
BLEND_MODES = ['Off', 'Mix', 'Add', 'Screen', 'Multiply', 'Difference', 'Hard Light']
