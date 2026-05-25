#!/usr/bin/env python3
"""
TIAMAT — Synth Studio  v3
Deep parameter control · ping-pong feedback · VIDEO LIFE export
"""
import sys, os, json, math, random, colorsys
from pathlib import Path

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
for _p in (_HERE, _ROOT):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import numpy as np
from OpenGL.GL import *

from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QPushButton, QSlider, QScrollArea, QFrame, QButtonGroup,
    QRadioButton, QColorDialog, QFileDialog, QSizePolicy,
)
from PyQt6.QtCore import Qt, QTimer, pyqtSignal, QDateTime
from PyQt6.QtGui  import QColor, QPainter, QLinearGradient, QBrush, QKeySequence, QShortcut

from core.gl_base      import GLBase
from signal_router     import SignalRouter
from core.audio_engine import AudioEngine

# ── Theme ─────────────────────────────────────────────────────────────────────
BG     = '#0a0000'
PANEL  = '#130000'
PANEL2 = '#1e0000'
ACCENT = '#FF2020'
TEXT   = '#d4aaaa'
DIM    = '#5c2a2a'
BORDER = 'rgba(255,30,30,0.12)'
MONO   = "'Menlo','Monaco','SF Mono','Courier New',monospace"

_PRESETS_DIR  = Path.home() / 'Documents' / 'tiamat_presets'
_PRESETS_DIR.mkdir(parents=True, exist_ok=True)
_VL_PRESETS   = Path.home() / 'video-synth' / 'presets'

# ── Parameter defaults ────────────────────────────────────────────────────────
DEFAULTS = {
    'cam_idx':        0,
    # Warp
    'u_warp_mode':    0,      # 0=off 1=scan 2=magnetic 3=flow
    'u_warp_amt':     0.0,    # 0–1.5
    'u_warp_spd':     0.4,    # 0–1
    'u_warp_freq':    0.5,    # 0–1  → spatial freq 0.5×–4×
    'u_warp_twist':   0.0,    # 0–1  rotational spiral component
    # Feedback
    'u_feedback':     0.0,    # 0–0.97
    'u_fb_mode':      0,      # 0=blend 1=displace 2=multiply 3=streak
    'u_fb_zoom':      0.5,    # 0–1  → 0.97–1.03 per-frame zoom
    'u_fb_rotate':    0.5,    # 0–1  → −0.04..+0.04 rad/frame
    'u_fb_hue_shift': 0.0,    # 0–1  per-frame hue drift
    # Glitch
    'u_glitch_mode':  0,      # 0=off 1=corrupt 2=burn 3=static
    'u_glitch_amt':   0.0,    # 0–1.5
    'u_chroma':       0.0,    # 0–1
    'u_glitch_rate':  0.5,    # 0–1  temporal firing density
    'u_glitch_scale': 0.3,    # 0–1  block coarseness
    # Spatial
    'u_zoom':         0.5,    # 0–1  → 0.5×–2× (0.5 = 1:1)
    'u_mirror':       0,      # 0=off 1=H 2=V 3=quad
    # Palette
    'u_pal_mode':     0,      # 0=natural 1=custom 2=green 3=ir 4=negative
    'u_pal_0':  [0.04, 0.00, 0.12],
    'u_pal_1':  [0.00, 0.22, 0.38],
    'u_pal_2':  [0.12, 0.72, 0.88],
    'u_pal_3':  [1.00, 0.92, 0.90],
    # Color
    'u_hue':          0.0,    # −1..1
    'u_sat':          1.0,    # 0–4
    'u_contrast':     1.0,    # 0–3
    'u_posterize':    0.0,    # 0–1
    # Channels
    'u_rgb_r':        0.5,    # 0–1  → 0–2 gain (0.5 = unity)
    'u_rgb_g':        0.5,
    'u_rgb_b':        0.5,
    'u_invert':       0.0,    # 0–1
    # Edge / mix
    'u_edge_mix':     0.0,    # 0–1
    'u_edge_hue':     0.0,    # 0–1  hue of edges
    # Texture
    'u_grain':        0.0,
    'u_rf':           0.0,
    'u_scanlines':    0.0,
    # LFO
    'u_lfo_rate':     0.3,    # 0–1  → 0–4 Hz
    'u_lfo_depth':    0.0,    # 0–1
    'u_lfo_target':   0,      # 0=warp 1=feedback 2=glitch 3=chroma
    # Audio react
    'u_react':        0.5,
    'u_beat_punch':   0.5,
    # Output
    'u_mix':          1.0,
    'u_gain':         0.65,
}

QUICK_PRESETS = {
    'Raw':      dict(DEFAULTS),
    'Ghosted':  {**DEFAULTS, 'u_feedback': 0.82, 'u_fb_mode': 0,
                 'u_fb_hue_shift': 0.12, 'u_warp_mode': 3, 'u_warp_amt': 0.4,
                 'u_pal_mode': 2, 'u_grain': 0.2},
    'Melt':     {**DEFAULTS, 'u_feedback': 0.88, 'u_fb_mode': 1,
                 'u_fb_zoom': 0.62, 'u_warp_mode': 1, 'u_warp_amt': 0.7,
                 'u_react': 0.8, 'u_pal_mode': 3},
    'Corrupt':  {**DEFAULTS, 'u_glitch_mode': 1, 'u_glitch_amt': 0.9,
                 'u_glitch_rate': 0.8, 'u_glitch_scale': 0.6,
                 'u_chroma': 0.8, 'u_rf': 0.5,
                 'u_feedback': 0.6, 'u_fb_mode': 3, 'u_react': 0.9},
    'Burn':     {**DEFAULTS, 'u_glitch_mode': 2, 'u_glitch_amt': 0.7,
                 'u_feedback': 0.75, 'u_fb_mode': 2, 'u_contrast': 2.0,
                 'u_react': 0.8, 'u_beat_punch': 1.0},
    'Noir':     {**DEFAULTS, 'u_sat': 0.0, 'u_contrast': 2.2,
                 'u_grain': 0.5, 'u_scanlines': 0.8, 'u_posterize': 0.3,
                 'u_edge_mix': 0.35, 'u_rgb_r': 0.4, 'u_rgb_g': 0.5, 'u_rgb_b': 0.4},
    'Magnetic': {**DEFAULTS, 'u_warp_mode': 2, 'u_warp_amt': 1.2,
                 'u_warp_twist': 0.4, 'u_feedback': 0.70, 'u_fb_mode': 1,
                 'u_fb_rotate': 0.65, 'u_chroma': 0.7,
                 'u_pal_mode': 1, 'u_react': 0.7},
    'Prism':    {**DEFAULTS, 'u_mirror': 3, 'u_warp_mode': 3,
                 'u_warp_amt': 0.5, 'u_warp_freq': 0.7,
                 'u_feedback': 0.5, 'u_fb_mode': 0, 'u_fb_hue_shift': 0.15,
                 'u_sat': 2.5, 'u_react': 0.7},
    'Drift':    {**DEFAULTS, 'u_lfo_rate': 0.5, 'u_lfo_depth': 0.7,
                 'u_lfo_target': 0, 'u_warp_mode': 3, 'u_warp_amt': 0.3,
                 'u_feedback': 0.65, 'u_fb_mode': 0, 'u_contrast': 1.5},
}

# ── GLSL ──────────────────────────────────────────────────────────────────────

_VERT = """
#version 330 core
in  vec2 in_position;
in  vec2 in_uv;
out vec2 uv;
void main() {
    gl_Position = vec4(in_position, 0.0, 1.0);
    uv = in_uv;
}
"""

_FRAG = """
#version 330 core

uniform float u_time;
uniform vec2  u_resolution;
uniform float u_rms, u_bass, u_mid, u_treble, u_beat;

uniform sampler2D u_src0;
uniform int       u_has_src0;
uniform sampler2D u_prev_frame;
uniform int       u_has_prev;

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

// ── Utility ──────────────────────────────────────────────────────────────────
float hash1(float n) { return fract(sin(n)*43758.5453); }
float hash2(vec2  v) { return fract(sin(dot(v,vec2(127.1,311.7)))*43758.5); }
vec3  hash3(vec2  v) {
    return fract(sin(vec3(dot(v,vec2(127.1,311.7)),
                          dot(v,vec2(269.5,183.3)),
                          dot(v,vec2(419.2,371.9))))*43758.5);
}
vec4 cam(vec2 st)  { return u_has_src0==1 ? texture(u_src0, clamp(st,0.001,0.999)) : vec4(0.12); }
vec4 prev(vec2 st) { return u_has_prev==1 ? texture(u_prev_frame, clamp(st,0.001,0.999)) : vec4(0.0); }

vec3 hue_rot(vec3 c, float h) {
    float Y=dot(c,vec3(0.299,0.587,0.114));
    float I=dot(c,vec3(0.596,-0.275,-0.321));
    float Q=dot(c,vec3(0.212,-0.523,0.311));
    float cs=cos(h); float sn=sin(h);
    return clamp(vec3(
        Y+0.956*(cs*I-sn*Q)+0.621*(sn*I+cs*Q),
        Y-0.272*(cs*I-sn*Q)-0.647*(sn*I+cs*Q),
        Y-1.107*(cs*I-sn*Q)+1.704*(sn*I+cs*Q)),0.0,1.0);
}

vec3 custom_pal(vec3 c) {
    float l=dot(c,vec3(0.299,0.587,0.114));
    if      (l<0.333) return mix(u_pal_0,u_pal_1,l/0.333);
    else if (l<0.667) return mix(u_pal_1,u_pal_2,(l-0.333)/0.334);
    else              return mix(u_pal_2,u_pal_3,(l-0.667)/0.333);
}

// ── Main ─────────────────────────────────────────────────────────────────────
void main() {
    vec2  st    = gl_FragCoord.xy / u_resolution;
    float lineY = floor(st.y * u_resolution.y);
    float t     = u_time;
    float beat  = u_beat  * u_react * u_beat_punch;
    float audio = u_rms   * u_react;

    // ── LFO ──────────────────────────────────────────────────────────────────
    float lfo = sin(t * u_lfo_rate * 18.8496) * u_lfo_depth;  // 0–6 Hz range

    float eff_warp   = clamp(u_warp_amt   + (u_lfo_target==0 ? lfo*u_warp_amt   : 0.0), 0.0, 2.0);
    float eff_fb     = clamp(u_feedback   + (u_lfo_target==1 ? lfo*0.3           : 0.0), 0.0, 0.97);
    float eff_glitch = clamp(u_glitch_amt + (u_lfo_target==2 ? lfo*u_glitch_amt  : 0.0), 0.0, 2.0);
    float eff_chroma = clamp(u_chroma     + (u_lfo_target==3 ? lfo*u_chroma      : 0.0), 0.0, 1.0);

    // ── Spatial zoom (camera scale, centred) ──────────────────────────────────
    float cam_scale = pow(2.0, u_zoom * 2.0 - 1.0);   // 0.5 → 2.0
    vec2  wuv = (st - 0.5) / cam_scale + 0.5;

    // ── Warp ──────────────────────────────────────────────────────────────────
    float wfreq = 0.5 + u_warp_freq * 3.5;             // 0.5 → 4.0

    if (u_warp_mode == 1) {                             // SCAN
        float spd = u_warp_spd * 2.0;
        float amp = eff_warp * (0.08 + audio*0.10);
        float d   = sin(lineY*0.031*wfreq + t*spd*0.7)*amp
                  + sin(lineY*0.073*wfreq + t*spd*1.3)*amp*0.55
                  + sin(lineY*0.19 *wfreq + t*spd*2.1)*amp*0.28
                  + sin(lineY*0.007*wfreq + t*spd*0.2)*amp*1.2;
        d += u_bass*u_react*0.04*sin(lineY*0.05+t*5.0);
        d += beat*0.05*sin(lineY*0.03+t*3.0);
        float sT  = floor(t*1.5*u_warp_spd + hash1(floor(t*0.3))*3.0);
        float sTp = hash1(sT);
        float sW  = 0.04 + hash1(sT+0.5)*0.18;
        d += (hash1(sT+2.0)-0.5)*eff_warp*0.40*step(sTp,st.y)*step(st.y,sTp+sW);
        vec2 disp = vec2(d, 0.0);
        if (u_warp_twist > 0.005) {
            float ang = (st.x-0.5)*u_warp_twist*6.28318;
            float ca=cos(ang); float sa=sin(ang);
            disp = vec2(disp.x*ca - disp.y*sa, disp.x*sa + disp.y*ca);
        }
        wuv += disp;

    } else if (u_warp_mode == 2) {                      // MAGNETIC
        float str = eff_warp*(0.25+u_bass*u_react*0.30+beat*0.15);
        float asp = u_resolution.x/u_resolution.y;
        float spd = u_warp_spd;
        vec2 m0=vec2(0.5+sin(t*0.23*spd)*0.35, 0.5+cos(t*0.17*spd)*0.30);
        vec2 m1=vec2(0.5+sin(t*0.31*spd+2.1)*0.30, 0.5+cos(t*0.19*spd+1.1)*0.35);
        vec2 m2=vec2(0.5+sin(t*0.13*spd+4.2)*0.20, 0.5+cos(t*0.29*spd+3.0)*0.22);
        vec2 d0=st-m0; d0.x*=asp;
        vec2 d1=st-m1; d1.x*=asp;
        vec2 d2=st-m2; d2.x*=asp;
        vec2 warp=(normalize(d0)/(length(d0)+0.001))*str*0.055
                 -(normalize(d1)/(length(d1)+0.001))*str*0.040
                 +(normalize(d2)/(length(d2)+0.001))*str*0.025;
        if (u_warp_twist > 0.005) {
            float ang = length(st-0.5)*u_warp_twist*12.566;
            float ca=cos(ang); float sa=sin(ang);
            warp = vec2(warp.x*ca-warp.y*sa, warp.x*sa+warp.y*ca);
        }
        wuv += clamp(warp,-0.5,0.5);

    } else if (u_warp_mode == 3) {                      // FLOW
        float amp = eff_warp*(0.12+audio*0.10);
        vec2 q=wuv;
        q+=0.05*vec2(sin(q.y*3.1*wfreq+t*u_warp_spd*1.4), cos(q.x*2.9*wfreq+t*u_warp_spd));
        q+=0.04*vec2(cos(q.x*5.3*wfreq-t*u_warp_spd*2.2), sin(q.y*4.7*wfreq+t*u_warp_spd*1.8));
        q+=0.03*vec2(sin(q.y*7.1*wfreq+q.x*2.0+t*u_warp_spd),
                     cos(q.x*6.3*wfreq-q.y*1.7+t*u_warp_spd*0.8));
        if (u_warp_twist > 0.005) {
            vec2 cen = q-0.5;
            float ang = length(cen)*u_warp_twist*8.0;
            float ca=cos(ang); float sa=sin(ang);
            q = 0.5 + vec2(cen.x*ca-cen.y*sa, cen.x*sa+cen.y*ca);
        }
        wuv = mix(wuv, q, amp*3.0);
    }

    // ── Mirror (applied to warped UV) ─────────────────────────────────────────
    if (u_mirror==1 || u_mirror==3) { if (wuv.x>0.5) wuv.x = 1.0-wuv.x; }
    if (u_mirror==2 || u_mirror==3) { if (wuv.y>0.5) wuv.y = 1.0-wuv.y; }

    // ── Camera sample with chroma aberration ──────────────────────────────────
    float chr = eff_chroma*0.025*(1.0+u_treble*u_react);
    vec3 col = vec3(
        cam(fract(wuv+vec2(chr,0.0))).r,
        cam(fract(wuv)).g,
        cam(fract(wuv-vec2(chr,0.0))).b
    ) * u_mix;

    // ── Edge detection (Sobel) ────────────────────────────────────────────────
    vec3 edge_col = vec3(0.0);
    if (u_edge_mix > 0.005) {
        float dx=1.5/u_resolution.x; float dy=1.5/u_resolution.y;
        vec3 n2=cam(wuv+vec2(0,dy)).rgb;  vec3 s2=cam(wuv-vec2(0,dy)).rgb;
        vec3 e2=cam(wuv+vec2(dx,0)).rgb;  vec3 w2=cam(wuv-vec2(dx,0)).rgb;
        float el = length(abs(n2-s2)+abs(e2-w2));
        edge_col = u_edge_hue>0.005 ? hue_rot(vec3(el*2.0),u_edge_hue*6.28318)
                                     : vec3(el*2.0);
    }

    // ── Glitch ────────────────────────────────────────────────────────────────
    float gscale = max(2.0, 32.0 - u_glitch_scale*28.0);  // 4..32 scan-line blocks

    if (u_glitch_mode==1) {                             // CORRUPT
        float corr = eff_glitch*(1.0+audio*2.0+beat*1.0);
        float bg   = floor(st.y*u_resolution.y/gscale);
        float rs   = hash1(bg+floor(t*(3.0+eff_glitch*15.0)));
        float thr  = 1.0 - u_glitch_rate*0.6;
        float rd   = (rs-0.5)*corr*0.18*step(thr,rs);
        col.r = cam(fract(wuv+vec2(rd+eff_glitch*0.030,0.0))).r*u_mix;
        col.b = cam(fract(wuv-vec2(eff_glitch*0.030,0.0))).b*u_mix;
        float bn = hash2(floor(st*u_resolution)+floor(t*40.0*u_glitch_rate));
        if (step(1.0-eff_glitch*0.22*u_glitch_rate,bn)>0.0) col=1.0-col;
        if (eff_glitch>0.4) {
            vec2 bk=floor(st*u_resolution/max(1.0,gscale));
            float bh=hash2(bk+floor(t*2.0));
            if (bh > 1.0-eff_glitch*0.25*u_glitch_rate) col=hash3(bk+1.3);
        }

    } else if (u_glitch_mode==2) {                      // BURN / SOLARIZE
        float base=1.0-eff_glitch*(0.65+audio*0.50);
        float tR=clamp(base*(1.0+eff_chroma*0.22),0.03,0.97);
        float tG=clamp(base,0.03,0.97);
        float tB=clamp(base*(1.0-eff_chroma*0.18),0.03,0.97);
        col.r=mix(col.r,1.0-col.r,smoothstep(tR-0.05,tR+0.05,col.r));
        col.g=mix(col.g,1.0-col.g,smoothstep(tG-0.05,tG+0.05,col.g));
        col.b=mix(col.b,1.0-col.b,smoothstep(tB-0.05,tB+0.05,col.b));
        col=mix(col,1.0-col,beat*0.9);
        float lm=dot(col,vec3(0.299,0.587,0.114));
        col=mix(col,vec3(1.0),smoothstep(0.5,1.0,lm)*eff_glitch*0.8);

    } else if (u_glitch_mode==3) {                      // STATIC
        float str=eff_glitch*(0.55+audio*0.50+beat*0.40)*u_glitch_rate;
        float n=hash2(floor(st*u_resolution)+floor(t*60.0));
        col=mix(col,hash3(floor(st*180.0)+floor(t*60.0)),str*step(0.4,n));
    }

    // ── Feedback ──────────────────────────────────────────────────────────────
    if (eff_fb > 0.005 && u_has_prev==1) {
        // Transform prev-frame UV: per-frame zoom + rotation accumulates over time
        float fbz = 0.97 + u_fb_zoom*0.06;           // 0.97..1.03
        float fbr = (u_fb_rotate-0.5)*0.08;           // −0.04..+0.04 rad/frame
        vec2 fbv  = st;
        vec2 dv   = fbv-0.5;
        float cs  = cos(fbr); float sn = sin(fbr);
        fbv = 0.5 + vec2(dv.x*cs-dv.y*sn, dv.x*sn+dv.y*cs);
        fbv = (fbv-0.5)/fbz + 0.5;

        vec3 pv = prev(fbv).rgb;
        if (u_fb_hue_shift > 0.005) pv = hue_rot(pv, u_fb_hue_shift*0.3);

        if (u_fb_mode==0) {                            // BLEND
            col = mix(col, pv, eff_fb);

        } else if (u_fb_mode==1) {                     // DISPLACE: prev colors drive warp
            vec2  disp = (pv.rg-0.5)*eff_fb*0.55;
            vec3  pcam = cam(fract(wuv+disp)).rgb*u_mix;
            col = mix(pcam, prev(fbv+disp*0.5).rgb, eff_fb*0.7)
                + col*(1.0-eff_fb*0.6);

        } else if (u_fb_mode==2) {                     // MULTIPLY: burn-in
            vec3 burned = clamp(col*pv*2.5,0.0,1.0);
            col = mix(col, burned, eff_fb);

        } else {                                        // STREAK: directional smear
            float angle = t*u_warp_spd*0.5;
            vec2  dir   = vec2(cos(angle),sin(angle))*eff_fb*0.018;
            col = mix(col, prev(fract(fbv-dir)).rgb, eff_fb*0.85);
        }
    }

    // ── Mix in edges ──────────────────────────────────────────────────────────
    if (u_edge_mix > 0.005) col = mix(col, col*0.3 + edge_col, u_edge_mix);

    // ── Palette ───────────────────────────────────────────────────────────────
    if      (u_pal_mode==1) { col = custom_pal(col); }
    else if (u_pal_mode==2) { float l=dot(col,vec3(0.299,0.587,0.114));
                              col=vec3(l*0.10,l*1.05,l*0.18); }
    else if (u_pal_mode==3) { float l=dot(col,vec3(0.299,0.587,0.114));
                              col=mix(vec3(0.05,0.07,0.55),vec3(1.0,0.22,0.04),l); }
    else if (u_pal_mode==4) { col=1.0-col; }

    // ── Hue / sat / contrast / posterize ─────────────────────────────────────
    if (abs(u_hue)>0.005) col=hue_rot(col,u_hue*3.14159);
    float lum=dot(col,vec3(0.299,0.587,0.114));
    col=mix(vec3(lum),col,u_sat);
    col=(col-0.5)*u_contrast+0.5;
    if (u_posterize>0.005) {
        float steps=max(2.0,floor(mix(32.0,2.0,u_posterize)));
        col=floor(col*steps+0.5)/steps;
    }
    col=clamp(col,0.0,1.0);

    // ── Per-channel gains (0.5→unity, 0→black, 1→2× boost) ───────────────────
    col.r *= u_rgb_r*2.0;
    col.g *= u_rgb_g*2.0;
    col.b *= u_rgb_b*2.0;
    if (u_invert>0.005) col=mix(col,1.0-col,u_invert);
    col=clamp(col,0.0,1.0);

    // ── Texture ───────────────────────────────────────────────────────────────
    col += (hash2(st*u_resolution+t*71.0)-0.5)*u_grain*(0.18+audio*0.12);
    if (u_rf>0.005) {
        float rfB=sin(st.y*75.0+t*7.0)*0.5+0.5;
        float rfN=hash2(vec2(st.x*512.0,lineY)+floor(t*60.0));
        col += rfB*rfN*u_rf*(0.20+audio*0.20);
    }
    if (u_scanlines>0.005) {
        float sl=0.80+0.20*sin(st.y*u_resolution.y*3.14159*2.0);
        col *= mix(1.0,sl,u_scanlines);
    }

    // ── Output ────────────────────────────────────────────────────────────────
    col *= mix(0.3,2.2,u_gain);
    fragColor = vec4(clamp(col,0.0,1.0),1.0);
}
"""

_UNIFORM_NAMES = [
    'u_src0','u_has_src0','u_prev_frame','u_has_prev',
    'u_warp_mode','u_warp_amt','u_warp_spd','u_warp_freq','u_warp_twist',
    'u_feedback','u_fb_mode','u_fb_zoom','u_fb_rotate','u_fb_hue_shift',
    'u_glitch_mode','u_glitch_amt','u_chroma','u_glitch_rate','u_glitch_scale',
    'u_zoom','u_mirror',
    'u_pal_mode','u_pal_0','u_pal_1','u_pal_2','u_pal_3',
    'u_hue','u_sat','u_contrast','u_posterize',
    'u_rgb_r','u_rgb_g','u_rgb_b','u_invert',
    'u_edge_mix','u_edge_hue',
    'u_grain','u_rf','u_scanlines',
    'u_lfo_rate','u_lfo_depth','u_lfo_target',
    'u_react','u_beat_punch','u_mix','u_gain',
]


# ── BuilderCanvas ─────────────────────────────────────────────────────────────

class BuilderCanvas(GLBase):

    def __init__(self, parent=None):
        super().__init__(parent)
        self._prog        = None
        self._src_tex     = None
        self._src_pending = None
        self._src_active  = False
        self._bp          = dict(DEFAULTS)
        self._fb_fbo  = [None, None]
        self._fb_tex  = [None, None]
        self._fb_idx  = 0
        self._fb_size = (0, 0)

    def initShaders(self):
        try:
            prog = self._link_program(_VERT, _FRAG)
            self._prog = prog
            self._cache_locs(prog)
            locs = self._uniform_locs.setdefault(prog, {})
            for name in _UNIFORM_NAMES:
                loc = glGetUniformLocation(prog, name.encode())
                if loc >= 0:
                    locs[name] = loc
            print('[Studio] shader compiled OK')
        except Exception as e:
            print(f'[Studio] shader error: {e}')
            return
        for i in range(2):
            tex = int(glGenTextures(1))
            fbo = int(glGenFramebuffers(1))
            glBindTexture(GL_TEXTURE_2D, tex)
            glTexImage2D(GL_TEXTURE_2D,0,GL_RGB,2,2,0,GL_RGB,GL_UNSIGNED_BYTE,None)
            for p,v in [(GL_TEXTURE_MIN_FILTER,GL_LINEAR),(GL_TEXTURE_MAG_FILTER,GL_LINEAR),
                        (GL_TEXTURE_WRAP_S,GL_CLAMP_TO_EDGE),(GL_TEXTURE_WRAP_T,GL_CLAMP_TO_EDGE)]:
                glTexParameteri(GL_TEXTURE_2D,p,v)
            glBindTexture(GL_TEXTURE_2D,0)
            glBindFramebuffer(GL_FRAMEBUFFER,fbo)
            glFramebufferTexture2D(GL_FRAMEBUFFER,GL_COLOR_ATTACHMENT0,GL_TEXTURE_2D,tex,0)
            glBindFramebuffer(GL_FRAMEBUFFER,0)
            self._fb_fbo[i]=fbo; self._fb_tex[i]=tex

    def set_source_frame(self, arr):
        self._src_pending = arr
        if arr is not None: self._src_active = True

    def clear_source(self):
        self._src_pending = None; self._src_active = False

    def set_builder_params(self, bp):
        self._bp = bp

    def clear_feedback_fbos(self):
        """Clear ping-pong FBOs to black — call on preset load for clean start."""
        if not any(self._fb_fbo):
            return
        self.makeCurrent()
        dfbo = self._dfbo()
        for fbo in self._fb_fbo:
            if fbo is not None:
                glBindFramebuffer(GL_FRAMEBUFFER, fbo)
                glClearColor(0.0, 0.0, 0.0, 1.0)
                glClear(GL_COLOR_BUFFER_BIT)
        glBindFramebuffer(GL_FRAMEBUFFER, dfbo)
        self.doneCurrent()

    def _upload(self):
        arr = self._src_pending
        if arr is None: return
        self._src_pending = None
        try:
            arr = np.ascontiguousarray(arr[::-1])
            h,w = arr.shape[:2]
            if self._src_tex is None:
                self._src_tex = int(glGenTextures(1))
            glBindTexture(GL_TEXTURE_2D,self._src_tex)
            glTexImage2D(GL_TEXTURE_2D,0,GL_RGB,w,h,0,GL_RGB,GL_UNSIGNED_BYTE,arr)
            for p,v in [(GL_TEXTURE_MIN_FILTER,GL_LINEAR),(GL_TEXTURE_MAG_FILTER,GL_LINEAR),
                        (GL_TEXTURE_WRAP_S,GL_CLAMP_TO_EDGE),(GL_TEXTURE_WRAP_T,GL_CLAMP_TO_EDGE)]:
                glTexParameteri(GL_TEXTURE_2D,p,v)
            glBindTexture(GL_TEXTURE_2D,0)
        except Exception as e:
            print(f'[Studio] upload error: {e}')

    def _ensure_fb_size(self, w, h):
        if self._fb_size==(w,h): return
        for tex in self._fb_tex:
            glBindTexture(GL_TEXTURE_2D,tex)
            glTexImage2D(GL_TEXTURE_2D,0,GL_RGB,w,h,0,GL_RGB,GL_UNSIGNED_BYTE,None)
        glBindTexture(GL_TEXTURE_2D,0)
        self._fb_size=(w,h)

    def _paint_frame(self, t, w, h):
        self._upload()
        self._ensure_fb_size(w,h)
        if self._prog is None:
            glBindFramebuffer(GL_FRAMEBUFFER,self._dfbo())
            glClear(GL_COLOR_BUFFER_BIT); return

        prog = self._prog
        locs = self._uniform_locs.get(prog,{})
        cur_fbo  = self._fb_fbo[self._fb_idx]
        prev_tex = self._fb_tex[1-self._fb_idx]

        glBindFramebuffer(GL_FRAMEBUFFER,cur_fbo)
        glViewport(0,0,w,h)
        glUseProgram(prog)
        self._set_uniforms(prog,t,w,h)

        # Camera → unit 2
        glActiveTexture(GL_TEXTURE2)
        active = self._src_tex is not None and self._src_active
        glBindTexture(GL_TEXTURE_2D, self._src_tex if active else (self._blank_tex or 0))
        if 'u_src0'     in locs: glUniform1i(locs['u_src0'],2)
        if 'u_has_src0' in locs: glUniform1i(locs['u_has_src0'],1 if active else 0)

        # Prev frame → unit 3
        glActiveTexture(GL_TEXTURE3)
        glBindTexture(GL_TEXTURE_2D,prev_tex)
        prev_ready = self._fb_size[0]>2
        if 'u_prev_frame' in locs: glUniform1i(locs['u_prev_frame'],3)
        if 'u_has_prev'   in locs: glUniform1i(locs['u_has_prev'],1 if prev_ready else 0)

        # Builder params
        bp = self._bp
        def si(n,v):
            if n in locs: glUniform1i(locs[n],int(v))
        def sf(n,v):
            if n in locs: glUniform1f(locs[n],float(v))
        def sv(n,v):
            if n in locs: glUniform3f(locs[n],float(v[0]),float(v[1]),float(v[2]))

        g = bp.get
        si('u_warp_mode',    g('u_warp_mode',0));     sf('u_warp_amt',   g('u_warp_amt',0))
        sf('u_warp_spd',     g('u_warp_spd',0.4));    sf('u_warp_freq',  g('u_warp_freq',0.5))
        sf('u_warp_twist',   g('u_warp_twist',0))
        sf('u_feedback',     g('u_feedback',0));       si('u_fb_mode',    g('u_fb_mode',0))
        sf('u_fb_zoom',      g('u_fb_zoom',0.5));      sf('u_fb_rotate',  g('u_fb_rotate',0.5))
        sf('u_fb_hue_shift', g('u_fb_hue_shift',0))
        si('u_glitch_mode',  g('u_glitch_mode',0));    sf('u_glitch_amt', g('u_glitch_amt',0))
        sf('u_chroma',       g('u_chroma',0));          sf('u_glitch_rate',g('u_glitch_rate',0.5))
        sf('u_glitch_scale', g('u_glitch_scale',0.3))
        sf('u_zoom',         g('u_zoom',0.5));          si('u_mirror',     g('u_mirror',0))
        si('u_pal_mode',     g('u_pal_mode',0))
        sv('u_pal_0',        g('u_pal_0',[0,0,0]));    sv('u_pal_1',g('u_pal_1',[0.3,0.3,0.3]))
        sv('u_pal_2',        g('u_pal_2',[0.6,0.6,0.6])); sv('u_pal_3',g('u_pal_3',[1,1,1]))
        sf('u_hue',          g('u_hue',0));             sf('u_sat',       g('u_sat',1))
        sf('u_contrast',     g('u_contrast',1));        sf('u_posterize', g('u_posterize',0))
        sf('u_rgb_r',        g('u_rgb_r',0.5));         sf('u_rgb_g',     g('u_rgb_g',0.5))
        sf('u_rgb_b',        g('u_rgb_b',0.5));         sf('u_invert',    g('u_invert',0))
        sf('u_edge_mix',     g('u_edge_mix',0));        sf('u_edge_hue',  g('u_edge_hue',0))
        sf('u_grain',        g('u_grain',0));           sf('u_rf',        g('u_rf',0))
        sf('u_scanlines',    g('u_scanlines',0))
        sf('u_lfo_rate',     g('u_lfo_rate',0.3));      sf('u_lfo_depth', g('u_lfo_depth',0))
        si('u_lfo_target',   g('u_lfo_target',0))
        sf('u_react',        g('u_react',0.5));         sf('u_beat_punch',g('u_beat_punch',0.5))
        sf('u_mix',          g('u_mix',1));             sf('u_gain',      g('u_gain',0.65))

        glActiveTexture(GL_TEXTURE0)
        glBindVertexArray(self._vao)
        glDrawArrays(GL_TRIANGLE_STRIP,0,4)

        # Blit → display
        dfbo = self._dfbo()
        glBindFramebuffer(GL_READ_FRAMEBUFFER,cur_fbo)
        glBindFramebuffer(GL_DRAW_FRAMEBUFFER,dfbo)
        glBlitFramebuffer(0,0,w,h,0,0,w,h,GL_COLOR_BUFFER_BIT,GL_LINEAR)
        self._fb_idx = 1-self._fb_idx


# ── Widgets ───────────────────────────────────────────────────────────────────

class Slider(QWidget):
    changed = pyqtSignal(float)

    def __init__(self, label, lo, hi, value, fmt='{:.2f}', parent=None):
        super().__init__(parent)
        self._lo, self._hi = lo, hi
        self._fmt = fmt
        lay = QHBoxLayout(self); lay.setContentsMargins(0,0,0,0); lay.setSpacing(6)
        lbl = QLabel(label); lbl.setFixedWidth(82)
        lbl.setStyleSheet(f'color:{DIM};font-family:{MONO};font-size:9px;')
        lay.addWidget(lbl)
        self._sl = QSlider(Qt.Orientation.Horizontal)
        self._sl.setRange(0,1000); self._sl.setValue(self._v2i(value))
        self._sl.setStyleSheet(f"""
            QSlider::groove:horizontal{{height:3px;background:{PANEL2};border-radius:1px;}}
            QSlider::handle:horizontal{{width:12px;height:12px;margin:-4px 0;
                background:{ACCENT};border-radius:6px;}}
            QSlider::sub-page:horizontal{{background:{ACCENT};border-radius:1px;}}
        """)
        self._sl.valueChanged.connect(self._emit)
        lay.addWidget(self._sl,1)
        self._vl = QLabel(fmt.format(value))
        self._vl.setFixedWidth(38); self._vl.setAlignment(Qt.AlignmentFlag.AlignRight)
        self._vl.setStyleSheet(f'color:{TEXT};font-family:{MONO};font-size:9px;')
        lay.addWidget(self._vl)

    def _v2i(self,v): return int((v-self._lo)/(self._hi-self._lo)*1000)
    def _i2v(self,i): return self._lo+i/1000*(self._hi-self._lo)
    def _emit(self,i):
        v=self._i2v(i); self._vl.setText(self._fmt.format(v)); self.changed.emit(v)
    def get_value(self): return self._i2v(self._sl.value())
    def set_value(self,v):
        self._sl.blockSignals(True)
        self._sl.setValue(self._v2i(v)); self._vl.setText(self._fmt.format(v))
        self._sl.blockSignals(False)


class ColorSwatch(QFrame):
    changed = pyqtSignal(list)

    def __init__(self, rgb, label='', parent=None):
        super().__init__(parent)
        self._rgb=list(rgb); self.setFixedSize(38,30)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip(label or 'Click to change'); self._refresh()

    def _refresh(self):
        r,g,b=[int(c*255) for c in self._rgb]
        self.setStyleSheet(f"""
            QFrame{{background:rgb({r},{g},{b});
                border:1px solid rgba(255,255,255,0.15);border-radius:3px;}}
            QFrame:hover{{border:1px solid {ACCENT};}}
        """)

    def set_rgb(self,rgb): self._rgb=list(rgb); self._refresh()
    def get_rgb(self): return list(self._rgb)

    def mousePressEvent(self,e):
        r,g,b=[int(c*255) for c in self._rgb]
        c=QColorDialog.getColor(QColor(r,g,b),self,'Pick colour')
        if c.isValid():
            self._rgb=[c.redF(),c.greenF(),c.blueF()]
            self._refresh(); self.changed.emit(self._rgb)


class GradientBar(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent); self.setFixedHeight(16)
        self._stops=[(0,0,0),(0.3,0.3,0.3),(0.7,0.7,0.7),(1,1,1)]

    def set_stops(self,stops): self._stops=stops; self.update()

    def paintEvent(self,_):
        p=QPainter(self)
        g=QLinearGradient(0,0,self.width(),0)
        for pos,(r,bg,b) in zip([0,.333,.667,1.0],self._stops):
            g.setColorAt(pos,QColor(int(r*255),int(bg*255),int(b*255)))
        p.fillRect(0,0,self.width(),self.height(),QBrush(g)); p.end()


# ── Section / radio helpers ───────────────────────────────────────────────────

def _sec(title):
    w=QWidget(); vl=QVBoxLayout(w)
    vl.setContentsMargins(0,0,0,8); vl.setSpacing(4)
    h=QLabel(title); h.setFixedHeight(24); h.setContentsMargins(10,0,0,0)
    h.setStyleSheet(f'background:{PANEL2};color:{ACCENT};'
                    f'font-family:{MONO};font-size:9px;font-weight:700;letter-spacing:2px;')
    vl.addWidget(h)
    body=QWidget(); bl=QVBoxLayout(body)
    bl.setContentsMargins(10,2,10,2); bl.setSpacing(5)
    vl.addWidget(body); return w,bl


def _radios(labels, parent=None):
    w=QWidget(parent); lay=QHBoxLayout(w)
    lay.setContentsMargins(0,0,0,0); lay.setSpacing(6)
    grp=QButtonGroup(w); btns=[]
    style=f"""
        QRadioButton{{color:{TEXT};font-family:{MONO};font-size:8px;spacing:3px;}}
        QRadioButton::indicator{{width:8px;height:8px;border-radius:4px;}}
        QRadioButton::indicator:checked{{background:{ACCENT};}}
        QRadioButton::indicator:unchecked{{background:{PANEL2};border:1px solid {DIM};}}
    """
    for i,lbl in enumerate(labels):
        rb=QRadioButton(lbl); rb.setStyleSheet(style)
        if i==0: rb.setChecked(True)
        grp.addButton(rb,i); lay.addWidget(rb); btns.append(rb)
    lay.addStretch(); return w,grp,btns


def _btn(label, highlight=False):
    b=QPushButton(label); b.setFixedHeight(26)
    col=ACCENT if highlight else DIM; bdr=ACCENT if highlight else BORDER
    b.setStyleSheet(f"""
        QPushButton{{background:{PANEL2};color:{col};border:1px solid {bdr};
            border-radius:3px;padding:0 9px;font-family:{MONO};font-size:9px;}}
        QPushButton:hover{{color:{ACCENT};border-color:{ACCENT};}}
    """); return b


# ── Main window ───────────────────────────────────────────────────────────────

class SynthStudio(QMainWindow):

    def __init__(self):
        super().__init__()
        self.setWindowTitle('Tiamat')
        self.resize(1340, 880)
        self.setStyleSheet(f'QMainWindow,QWidget{{background:{BG};color:{TEXT};}}')

        self._canvas = BuilderCanvas()
        self._router = SignalRouter()
        self._audio  = AudioEngine()
        self._bp     = dict(DEFAULTS)
        self._canvas.set_builder_params(self._bp)

        central=QWidget(); self.setCentralWidget(central)
        root=QVBoxLayout(central); root.setContentsMargins(0,0,0,0); root.setSpacing(0)
        root.addWidget(self._build_header())
        body=QHBoxLayout(); body.setContentsMargins(0,0,0,0); body.setSpacing(0)
        body.addWidget(self._build_panel())
        body.addWidget(self._canvas,1)
        root.addLayout(body,1)
        root.addWidget(self._build_status())

        QTimer(self,timeout=self._push,       interval=33).start()
        QTimer(self,timeout=self._poll_audio, interval=33).start()
        self._canvas.fps_updated.connect(lambda f: self._fps.setText(f'{f:.0f} fps'))
        QShortcut(QKeySequence('Ctrl+S'), self).activated.connect(self._screenshot)

        cams = self._router.scan_cameras()
        self._router.set_source(0,'camera',0)
        if self._audio.input_devices:
            self._audio.start_input(0)

    # ── Header ────────────────────────────────────────────────────────────────

    def _build_header(self):
        bar=QWidget(); bar.setFixedHeight(46)
        bar.setStyleSheet(f'background:{PANEL};border-bottom:1px solid {BORDER};')
        lay=QHBoxLayout(bar); lay.setContentsMargins(18,0,18,0); lay.setSpacing(10)

        t=QLabel('TIAMAT')
        t.setStyleSheet(f'color:{ACCENT};font-family:{MONO};font-size:13px;'
                        f'font-weight:700;letter-spacing:4px;')
        lay.addWidget(t); lay.addStretch()

        # Camera selector
        cam_lbl=QLabel('CAM')
        cam_lbl.setStyleSheet(f'color:{DIM};font-family:{MONO};font-size:8px;letter-spacing:2px;')
        lay.addWidget(cam_lbl)
        self._cam_grp=QButtonGroup(self)
        cam_style=f"""
            QRadioButton{{color:{TEXT};font-family:{MONO};font-size:9px;spacing:5px;}}
            QRadioButton::indicator{{width:9px;height:9px;border-radius:4px;}}
            QRadioButton::indicator:checked{{background:{ACCENT};}}
            QRadioButton::indicator:unchecked{{background:{PANEL2};border:1px solid {DIM};}}
        """
        for i,nm in enumerate(['FaceTime','iPhone']):
            rb=QRadioButton(nm); rb.setChecked(i==0)
            rb.setStyleSheet(cam_style)
            self._cam_grp.addButton(rb,i); lay.addWidget(rb)
        self._cam_grp.idClicked.connect(self._on_cam)

        lay.addSpacing(12)
        sep=QLabel('│'); sep.setStyleSheet(f'color:{DIM};font-size:14px;'); lay.addWidget(sep)
        lay.addSpacing(4)

        # Quick presets
        for name in QUICK_PRESETS:
            b=_btn(name)
            b.clicked.connect(lambda _,n=name: self._load_preset(QUICK_PRESETS[n]))
            lay.addWidget(b)

        lay.addSpacing(8)
        rand=_btn('RANDOM',highlight=True); rand.clicked.connect(self._randomize); lay.addWidget(rand)
        save=_btn('SAVE');   save.clicked.connect(self._save);          lay.addWidget(save)
        load=_btn('LOAD');   load.clicked.connect(self._load);          lay.addWidget(load)

        lay.addSpacing(4)
        sep2=QLabel('│'); sep2.setStyleSheet(f'color:{DIM};font-size:14px;'); lay.addWidget(sep2)
        lay.addSpacing(4)

        vl_btn=_btn('→ VIDEO LIFE',highlight=True)
        vl_btn.setToolTip('Export current preset as a VIDEO LIFE preset (Video FX engine)')
        vl_btn.clicked.connect(self._export_videolife)
        lay.addWidget(vl_btn)

        lay.addSpacing(4)
        shot_btn=_btn('📷 SHOT')
        shot_btn.setToolTip('Save screenshot of current frame  (⌘S)')
        shot_btn.clicked.connect(self._screenshot)
        lay.addWidget(shot_btn)

        return bar

    # ── Left panel ────────────────────────────────────────────────────────────

    def _build_panel(self):
        scroll=QScrollArea(); scroll.setFixedWidth(282)
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet(f"""
            QScrollArea,QScrollBar{{border:none;background:{PANEL};}}
            QScrollBar:vertical{{width:4px;background:{BG};}}
            QScrollBar::handle:vertical{{background:{PANEL2};border-radius:2px;}}
        """)
        inner=QWidget(); vl=QVBoxLayout(inner)
        vl.setContentsMargins(0,0,0,16); vl.setSpacing(0)

        vl.addWidget(self._sec_warp())
        vl.addWidget(self._sec_feedback())
        vl.addWidget(self._sec_glitch())
        vl.addWidget(self._sec_spatial())
        vl.addWidget(self._sec_palette())
        vl.addWidget(self._sec_channels())
        vl.addWidget(self._sec_edge())
        vl.addWidget(self._sec_texture())
        vl.addWidget(self._sec_lfo())
        vl.addWidget(self._sec_react())
        vl.addWidget(self._sec_output())
        vl.addStretch()

        scroll.setWidget(inner); return scroll

    # ── Section builders ──────────────────────────────────────────────────────

    def _sec_warp(self):
        w,bl=_sec('WARP')
        row,grp,_=_radios(['Off','Scan','Magnetic','Flow'])
        grp.idClicked.connect(lambda i: self._set('u_warp_mode',i))
        bl.addWidget(row); self._warp_grp=grp
        self._warp_sl={
            'amt':   Slider('Amount',  0.0, 1.5, DEFAULTS['u_warp_amt']),
            'spd':   Slider('Speed',   0.0, 1.0, DEFAULTS['u_warp_spd']),
            'freq':  Slider('Freq',    0.0, 1.0, DEFAULTS['u_warp_freq']),
            'twist': Slider('Twist',   0.0, 1.0, DEFAULTS['u_warp_twist']),
        }
        self._warp_sl['amt'].changed.connect(  lambda v: self._set('u_warp_amt',v))
        self._warp_sl['spd'].changed.connect(  lambda v: self._set('u_warp_spd',v))
        self._warp_sl['freq'].changed.connect( lambda v: self._set('u_warp_freq',v))
        self._warp_sl['twist'].changed.connect(lambda v: self._set('u_warp_twist',v))
        for s in self._warp_sl.values(): bl.addWidget(s)
        return w

    def _sec_feedback(self):
        w,bl=_sec('FEEDBACK')
        row,grp,_=_radios(['Blend','Displace','Multiply','Streak'])
        grp.idClicked.connect(lambda i: self._set('u_fb_mode',i))
        bl.addWidget(row); self._fb_grp=grp
        self._fb_sl={
            'amt':   Slider('Amount',    0.0, 0.97, DEFAULTS['u_feedback']),
            'zoom':  Slider('Zoom',      0.0, 1.0,  DEFAULTS['u_fb_zoom']),
            'rot':   Slider('Rotate',    0.0, 1.0,  DEFAULTS['u_fb_rotate']),
            'hue':   Slider('Hue Drift', 0.0, 1.0,  DEFAULTS['u_fb_hue_shift']),
        }
        self._fb_sl['amt'].changed.connect( lambda v: self._set('u_feedback',v))
        self._fb_sl['zoom'].changed.connect(lambda v: self._set('u_fb_zoom',v))
        self._fb_sl['rot'].changed.connect( lambda v: self._set('u_fb_rotate',v))
        self._fb_sl['hue'].changed.connect( lambda v: self._set('u_fb_hue_shift',v))
        for s in self._fb_sl.values(): bl.addWidget(s)
        hint=QLabel('  ↑ Zoom/Rotate accumulate each frame')
        hint.setStyleSheet(f'color:{DIM};font-family:{MONO};font-size:8px;')
        bl.addWidget(hint); return w

    def _sec_glitch(self):
        w,bl=_sec('GLITCH')
        row,grp,_=_radios(['Off','Corrupt','Burn','Static'])
        grp.idClicked.connect(lambda i: self._set('u_glitch_mode',i))
        bl.addWidget(row); self._glitch_grp=grp
        self._glitch_sl={
            'amt':   Slider('Amount', 0.0, 1.5, DEFAULTS['u_glitch_amt']),
            'chm':   Slider('Chroma', 0.0, 1.0, DEFAULTS['u_chroma']),
            'rate':  Slider('Rate',   0.0, 1.0, DEFAULTS['u_glitch_rate']),
            'scale': Slider('Scale',  0.0, 1.0, DEFAULTS['u_glitch_scale']),
        }
        self._glitch_sl['amt'].changed.connect(  lambda v: self._set('u_glitch_amt',v))
        self._glitch_sl['chm'].changed.connect(  lambda v: self._set('u_chroma',v))
        self._glitch_sl['rate'].changed.connect( lambda v: self._set('u_glitch_rate',v))
        self._glitch_sl['scale'].changed.connect(lambda v: self._set('u_glitch_scale',v))
        for s in self._glitch_sl.values(): bl.addWidget(s)
        return w

    def _sec_spatial(self):
        w,bl=_sec('SPATIAL')
        zoom=Slider('Zoom', 0.0, 1.0, DEFAULTS['u_zoom'])
        zoom.changed.connect(lambda v: self._set('u_zoom',v))
        bl.addWidget(zoom); self._zoom_sl=zoom
        row,grp,_=_radios(['Off','H Mirror','V Mirror','Quad'])
        grp.idClicked.connect(lambda i: self._set('u_mirror',i))
        bl.addWidget(row); self._mirror_grp=grp
        return w

    def _sec_palette(self):
        w,bl=_sec('PALETTE')
        row,grp,_=_radios(['Natural','Custom','Green','IR','Neg'])
        grp.idClicked.connect(self._on_pal_mode)
        bl.addWidget(row); self._pal_grp=grp

        swatch_row=QWidget(); srl=QHBoxLayout(swatch_row)
        srl.setContentsMargins(0,4,0,0); srl.setSpacing(5)
        self._swatches=[]
        for key,lbl in [('u_pal_0','Shadows'),('u_pal_1','Low'),
                        ('u_pal_2','High'),('u_pal_3','Lights')]:
            cw=QWidget(); cl=QVBoxLayout(cw); cl.setContentsMargins(0,0,0,0); cl.setSpacing(2)
            sw=ColorSwatch(DEFAULTS[key],lbl)
            sw.changed.connect(lambda rgb,k=key: self._on_swatch(k,rgb))
            self._swatches.append(sw)
            cl.addWidget(sw,alignment=Qt.AlignmentFlag.AlignHCenter)
            ll=QLabel(lbl); ll.setAlignment(Qt.AlignmentFlag.AlignHCenter)
            ll.setStyleSheet(f'color:{DIM};font-family:{MONO};font-size:7px;')
            cl.addWidget(ll); srl.addWidget(cw)
        bl.addWidget(swatch_row)

        self._grad=GradientBar()
        self._grad.set_stops([DEFAULTS[k] for k in ['u_pal_0','u_pal_1','u_pal_2','u_pal_3']])
        bl.addWidget(self._grad)

        self._pal_sl={
            'hue': Slider('Hue',      -1.0, 1.0, DEFAULTS['u_hue']),
            'sat': Slider('Sat',       0.0, 4.0, DEFAULTS['u_sat']),
            'con': Slider('Contrast',  0.0, 3.0, DEFAULTS['u_contrast']),
            'pos': Slider('Posterize', 0.0, 1.0, DEFAULTS['u_posterize']),
        }
        self._pal_sl['hue'].changed.connect(lambda v: self._set('u_hue',v))
        self._pal_sl['sat'].changed.connect(lambda v: self._set('u_sat',v))
        self._pal_sl['con'].changed.connect(lambda v: self._set('u_contrast',v))
        self._pal_sl['pos'].changed.connect(lambda v: self._set('u_posterize',v))
        for s in self._pal_sl.values(): bl.addWidget(s)
        return w

    def _sec_channels(self):
        w,bl=_sec('CHANNELS')
        self._chan_sl={
            'r':   Slider('Red',    0.0, 1.0, DEFAULTS['u_rgb_r']),
            'g':   Slider('Green',  0.0, 1.0, DEFAULTS['u_rgb_g']),
            'b':   Slider('Blue',   0.0, 1.0, DEFAULTS['u_rgb_b']),
            'inv': Slider('Invert', 0.0, 1.0, DEFAULTS['u_invert']),
        }
        self._chan_sl['r'].changed.connect(  lambda v: self._set('u_rgb_r',v))
        self._chan_sl['g'].changed.connect(  lambda v: self._set('u_rgb_g',v))
        self._chan_sl['b'].changed.connect(  lambda v: self._set('u_rgb_b',v))
        self._chan_sl['inv'].changed.connect(lambda v: self._set('u_invert',v))
        for s in self._chan_sl.values(): bl.addWidget(s)
        hint=QLabel('  Red/Green/Blue: 0.5 = unity, 0 = cut, 1 = 2× boost')
        hint.setStyleSheet(f'color:{DIM};font-family:{MONO};font-size:8px;')
        bl.addWidget(hint); return w

    def _sec_edge(self):
        w,bl=_sec('EDGE / CONTOUR')
        self._edge_sl={
            'mix': Slider('Edge Mix', 0.0, 1.0, DEFAULTS['u_edge_mix']),
            'hue': Slider('Edge Hue', 0.0, 1.0, DEFAULTS['u_edge_hue']),
        }
        self._edge_sl['mix'].changed.connect(lambda v: self._set('u_edge_mix',v))
        self._edge_sl['hue'].changed.connect(lambda v: self._set('u_edge_hue',v))
        for s in self._edge_sl.values(): bl.addWidget(s)
        return w

    def _sec_texture(self):
        w,bl=_sec('TEXTURE')
        self._tex_sl={
            'grain': Slider('Grain',     0.0, 1.0, DEFAULTS['u_grain']),
            'rf':    Slider('RF Noise',  0.0, 1.0, DEFAULTS['u_rf']),
            'sl':    Slider('Scanlines', 0.0, 1.0, DEFAULTS['u_scanlines']),
        }
        self._tex_sl['grain'].changed.connect(lambda v: self._set('u_grain',v))
        self._tex_sl['rf'].changed.connect(   lambda v: self._set('u_rf',v))
        self._tex_sl['sl'].changed.connect(   lambda v: self._set('u_scanlines',v))
        for s in self._tex_sl.values(): bl.addWidget(s)
        return w

    def _sec_lfo(self):
        w,bl=_sec('LFO')
        self._lfo_sl={
            'rate':  Slider('Rate',  0.0, 1.0, DEFAULTS['u_lfo_rate']),
            'depth': Slider('Depth', 0.0, 1.0, DEFAULTS['u_lfo_depth']),
        }
        self._lfo_sl['rate'].changed.connect( lambda v: self._set('u_lfo_rate',v))
        self._lfo_sl['depth'].changed.connect(lambda v: self._set('u_lfo_depth',v))
        for s in self._lfo_sl.values(): bl.addWidget(s)
        row,grp,_=_radios(['Warp','Feedback','Glitch','Chroma'])
        grp.idClicked.connect(lambda i: self._set('u_lfo_target',i))
        bl.addWidget(row); self._lfo_grp=grp
        hint=QLabel('  LFO modulates the chosen target over time')
        hint.setStyleSheet(f'color:{DIM};font-family:{MONO};font-size:8px;')
        bl.addWidget(hint); return w

    def _sec_react(self):
        w,bl=_sec('AUDIO REACT')
        self._react_sl={
            'react': Slider('React',      0.0, 1.0, DEFAULTS['u_react']),
            'beat':  Slider('Beat Punch', 0.0, 1.0, DEFAULTS['u_beat_punch']),
        }
        self._react_sl['react'].changed.connect(lambda v: self._set('u_react',v))
        self._react_sl['beat'].changed.connect( lambda v: self._set('u_beat_punch',v))
        for s in self._react_sl.values(): bl.addWidget(s)
        return w

    def _sec_output(self):
        w,bl=_sec('OUTPUT')
        self._out_sl={
            'mix':  Slider('Mix',  0.0, 1.0, DEFAULTS['u_mix']),
            'gain': Slider('Gain', 0.0, 1.0, DEFAULTS['u_gain']),
        }
        self._out_sl['mix'].changed.connect( lambda v: self._set('u_mix',v))
        self._out_sl['gain'].changed.connect(lambda v: self._set('u_gain',v))
        for s in self._out_sl.values(): bl.addWidget(s)
        return w

    # ── Status bar ────────────────────────────────────────────────────────────

    def _build_status(self):
        bar=QWidget(); bar.setFixedHeight(22)
        bar.setStyleSheet(f'background:{PANEL2};border-top:1px solid {BORDER};')
        lay=QHBoxLayout(bar); lay.setContentsMargins(14,0,14,0)
        self._fps=QLabel('-- fps')
        self._fps.setStyleSheet(f'color:{DIM};font-family:{MONO};font-size:9px;')
        lay.addWidget(self._fps); lay.addStretch()
        hint=QLabel('scroll left panel · RANDOM to discover · → VIDEO LIFE to export')
        hint.setStyleSheet(f'color:{DIM};font-family:{MONO};font-size:9px;')
        lay.addWidget(hint); return bar

    # ── Callbacks ─────────────────────────────────────────────────────────────

    def _set(self,key,val):
        self._bp[key]=val; self._canvas.set_builder_params(self._bp)

    def _on_cam(self,idx):
        self._router.set_source(0,'camera',idx)

    def _on_pal_mode(self,idx):
        self._set('u_pal_mode',idx)

    def _on_swatch(self,key,rgb):
        self._set(key,rgb)
        self._grad.set_stops([self._bp[k] for k in ['u_pal_0','u_pal_1','u_pal_2','u_pal_3']])
        self._pal_grp.button(1).setChecked(True); self._set('u_pal_mode',1)

    def _load_preset(self,preset):
        bp=dict(DEFAULTS); bp.update(preset)
        self._bp=bp; self._canvas.set_builder_params(self._bp)
        self._canvas.clear_feedback_fbos()   # clean start = matches VIDEO LIFE load
        self._canvas.reset_clock()           # t=0 so time-based oscillations sync
        self._sync()

    def _sync(self):
        bp=self._bp
        g=bp.get
        self._warp_grp.button(g('u_warp_mode',0)).setChecked(True)
        self._warp_sl['amt'].set_value(g('u_warp_amt',0))
        self._warp_sl['spd'].set_value(g('u_warp_spd',0.4))
        self._warp_sl['freq'].set_value(g('u_warp_freq',0.5))
        self._warp_sl['twist'].set_value(g('u_warp_twist',0))
        self._fb_grp.button(g('u_fb_mode',0)).setChecked(True)
        self._fb_sl['amt'].set_value(g('u_feedback',0))
        self._fb_sl['zoom'].set_value(g('u_fb_zoom',0.5))
        self._fb_sl['rot'].set_value(g('u_fb_rotate',0.5))
        self._fb_sl['hue'].set_value(g('u_fb_hue_shift',0))
        self._glitch_grp.button(g('u_glitch_mode',0)).setChecked(True)
        self._glitch_sl['amt'].set_value(g('u_glitch_amt',0))
        self._glitch_sl['chm'].set_value(g('u_chroma',0))
        self._glitch_sl['rate'].set_value(g('u_glitch_rate',0.5))
        self._glitch_sl['scale'].set_value(g('u_glitch_scale',0.3))
        self._zoom_sl.set_value(g('u_zoom',0.5))
        self._mirror_grp.button(g('u_mirror',0)).setChecked(True)
        self._pal_grp.button(g('u_pal_mode',0)).setChecked(True)
        for sw,k in zip(self._swatches,['u_pal_0','u_pal_1','u_pal_2','u_pal_3']):
            sw.set_rgb(g(k,[0,0,0]))
        self._grad.set_stops([g(k,[0,0,0]) for k in ['u_pal_0','u_pal_1','u_pal_2','u_pal_3']])
        self._pal_sl['hue'].set_value(g('u_hue',0))
        self._pal_sl['sat'].set_value(g('u_sat',1))
        self._pal_sl['con'].set_value(g('u_contrast',1))
        self._pal_sl['pos'].set_value(g('u_posterize',0))
        self._chan_sl['r'].set_value(g('u_rgb_r',0.5))
        self._chan_sl['g'].set_value(g('u_rgb_g',0.5))
        self._chan_sl['b'].set_value(g('u_rgb_b',0.5))
        self._chan_sl['inv'].set_value(g('u_invert',0))
        self._edge_sl['mix'].set_value(g('u_edge_mix',0))
        self._edge_sl['hue'].set_value(g('u_edge_hue',0))
        self._tex_sl['grain'].set_value(g('u_grain',0))
        self._tex_sl['rf'].set_value(g('u_rf',0))
        self._tex_sl['sl'].set_value(g('u_scanlines',0))
        self._lfo_sl['rate'].set_value(g('u_lfo_rate',0.3))
        self._lfo_sl['depth'].set_value(g('u_lfo_depth',0))
        self._lfo_grp.button(g('u_lfo_target',0)).setChecked(True)
        self._react_sl['react'].set_value(g('u_react',0.5))
        self._react_sl['beat'].set_value(g('u_beat_punch',0.5))
        self._out_sl['mix'].set_value(g('u_mix',1))
        self._out_sl['gain'].set_value(g('u_gain',0.65))

    # ── Randomize ─────────────────────────────────────────────────────────────

    def _randomize(self):
        def rnd(lo,hi): return lo+random.random()*(hi-lo)
        def rcol(v_lo,v_hi,s_lo=0.4,s_hi=1.0):
            return list(colorsys.hsv_to_rgb(random.random(),rnd(s_lo,s_hi),rnd(v_lo,v_hi)))

        hb=random.random()
        bp={
            'cam_idx':        self._bp['cam_idx'],
            'u_warp_mode':    random.choice([0,0,1,1,2,3]),
            'u_warp_amt':     rnd(0.2,1.2),
            'u_warp_spd':     rnd(0.1,0.8),
            'u_warp_freq':    rnd(0.2,0.8),
            'u_warp_twist':   rnd(0.0,0.6) if random.random()<0.4 else 0.0,
            'u_feedback':     rnd(0.0,0.88),
            'u_fb_mode':      random.randint(0,3),
            'u_fb_zoom':      rnd(0.3,0.7),
            'u_fb_rotate':    rnd(0.3,0.7),
            'u_fb_hue_shift': rnd(0.0,0.4) if random.random()<0.5 else 0.0,
            'u_glitch_mode':  random.choice([0,0,0,1,2,3]),
            'u_glitch_amt':   rnd(0.0,0.9),
            'u_chroma':       rnd(0.0,0.7),
            'u_glitch_rate':  rnd(0.3,0.8),
            'u_glitch_scale': rnd(0.1,0.7),
            'u_zoom':         rnd(0.3,0.7),
            'u_mirror':       random.choice([0,0,0,1,2,3]),
            'u_pal_mode':     random.choice([0,1,1,2,3,4]),
            'u_pal_0': rcol(0.0,0.20,0.5,1.0),
            'u_pal_1': list(colorsys.hsv_to_rgb(hb,rnd(0.6,1.0),rnd(0.3,0.6))),
            'u_pal_2': list(colorsys.hsv_to_rgb((hb+0.3)%1,rnd(0.5,1.0),rnd(0.6,0.9))),
            'u_pal_3': rcol(0.80,1.00,0.0,0.3),
            'u_hue':          rnd(-0.5,0.5),
            'u_sat':          rnd(0.5,3.0),
            'u_contrast':     rnd(0.8,2.5),
            'u_posterize':    rnd(0.0,0.5) if random.random()<0.3 else 0.0,
            'u_rgb_r':        rnd(0.3,0.7),
            'u_rgb_g':        rnd(0.3,0.7),
            'u_rgb_b':        rnd(0.3,0.7),
            'u_invert':       rnd(0.0,0.5) if random.random()<0.2 else 0.0,
            'u_edge_mix':     rnd(0.0,0.5) if random.random()<0.35 else 0.0,
            'u_edge_hue':     rnd(0.0,1.0),
            'u_grain':        rnd(0.0,0.5),
            'u_rf':           rnd(0.0,0.4),
            'u_scanlines':    rnd(0.0,0.6) if random.random()<0.4 else 0.0,
            'u_lfo_rate':     rnd(0.1,0.7),
            'u_lfo_depth':    rnd(0.0,0.6) if random.random()<0.5 else 0.0,
            'u_lfo_target':   random.randint(0,3),
            'u_react':        rnd(0.3,0.9),
            'u_beat_punch':   rnd(0.3,1.0),
            'u_mix':          rnd(0.7,1.0),
            'u_gain':         rnd(0.5,0.8),
        }
        self._load_preset(bp)

    # ── Save / Load ───────────────────────────────────────────────────────────

    def _build_vl_payload(self):
        """Build a dual-compatible JSON payload: loads in Studio AND VIDEO LIFE."""
        bp = self._bp
        g  = bp.get
        # VIDEO LIFE "Tiamat" engine — same shader, same p[0-7] layout
        params = [
            min(g('u_warp_amt',  0)    / 1.5,  1.0),   # p[0] Warp
            min(g('u_feedback',  0)    / 0.97, 1.0),   # p[1] Feedback
            min(g('u_glitch_amt',0)    / 1.5,  1.0),   # p[2] Glitch
            g('u_chroma', 0),                            # p[3] Chroma
            min(g('u_sat',1)           / 4.0,  1.0),   # p[4] Sat
            (g('u_hue',0) + 1.0) / 2.0,                # p[5] Hue (−1..1 → 0..1)
            g('u_gain', 0.65),                           # p[6] Gain
            g('u_react', 0.5),                           # p[7] React
        ]
        params = [max(0.0, min(1.0, p)) for p in params]
        studio_params = {k: (list(v) if isinstance(v, list) else v) for k,v in bp.items()}
        return {
            'mode':            'Tiamat',  # VIDEO LIFE engine (exact same shader)
            'params':          params,        # VIDEO LIFE 8-param array
            'cc_map':          {},
            '_studio_params':  studio_params, # full round-trip data for Studio
        }

    def _save(self):
        # Default to VIDEO LIFE presets folder so the file is available in both apps
        dest = str(_VL_PRESETS) if _VL_PRESETS.exists() else str(_PRESETS_DIR)
        p, _ = QFileDialog.getSaveFileName(self, 'Save Preset', dest, 'JSON (*.json)')
        if p:
            Path(p).write_text(json.dumps(self._build_vl_payload(), indent=2))
            self._fps.setText(f'saved: {Path(p).name}')
            print(f'[Studio] saved: {p}')

    def _load(self):
        dest = str(_VL_PRESETS) if _VL_PRESETS.exists() else str(_PRESETS_DIR)
        p, _ = QFileDialog.getOpenFileName(self, 'Load Preset', dest, 'JSON (*.json)')
        if p:
            try:
                data = json.loads(Path(p).read_text())
                # Prefer full studio params; fall back to raw dict (old Studio saves)
                self._load_preset(data.get('_studio_params', data))
            except Exception as e:
                print(f'[Studio] load error: {e}')

    # ── Quick export → VIDEO LIFE (no dialog) ─────────────────────────────────

    def _export_videolife(self):
        if not _VL_PRESETS.exists():
            # Fall back to dialog if VIDEO LIFE presets folder isn't found
            self._save(); return
        ts   = QDateTime.currentDateTime().toString('yyyyMMdd_HHmmss')
        path = _VL_PRESETS / f'tiamat_{ts}.json'
        path.write_text(json.dumps(self._build_vl_payload(), indent=2))
        msg = f'→ VIDEO LIFE: {path.name}'
        self._fps.setText(msg)
        print(f'[Studio] {msg}')

    # ── Screenshot ────────────────────────────────────────────────────────────

    def _screenshot(self):
        img = self._canvas.grabFramebuffer()   # QImage of the GL widget
        ts  = QDateTime.currentDateTime().toString('yyyyMMdd_HHmmss')
        default_path = str(Path.home() / 'Desktop' / f'tiamat_{ts}.png')
        path, _ = QFileDialog.getSaveFileName(
            self, 'Save Screenshot', default_path,
            'PNG (*.png);;JPEG (*.jpg);;TIFF (*.tiff)')
        if path:
            ok = img.save(path)
            msg = f'screenshot saved: {Path(path).name}' if ok else 'screenshot failed'
            self._fps.setText(msg)
            print(f'[Studio] {msg}')

    # ── Timers ────────────────────────────────────────────────────────────────

    def _push(self):
        f=self._router.get_frame(0); self._canvas.set_source_frame(f)

    def _poll_audio(self):
        fft,rms,bass,mid,treble,beat=self._audio.get_data()
        self._canvas.set_audio_data(fft,rms,bass,mid,treble,beat)

    def closeEvent(self,e):
        self._router.close(); self._audio.stop(); e.accept()


# ── Entry point ───────────────────────────────────────────────────────────────

def main():
    try:
        QApplication.setHighDpiScaleFactorRoundingPolicy(
            Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    except AttributeError:
        pass
    app=QApplication(sys.argv)
    win=SynthStudio(); win.show()
    sys.exit(app.exec())

if __name__=='__main__':
    main()
