# VIDEO LIFE

**Real-time GPU video synthesizer — free & open source**

9 GLSL synthesis engines driven by audio, MIDI, and Ableton Link.
Ships with two companion apps: **TIAMAT Synth Studio** and **FUGUE** compositor.

---

## Apps

### VIDEO LIFE
Main synthesizer. 9 engines running live on the GPU — audio reactive, MIDI CC learn, Ableton Link beat sync, live camera input, direct MP4 recording, dual-screen output.

**Engines:** Lissajous · Plasma · Ramp Colorizer · Feedback · Kaleidoscope · Waveform 3D · Circuit Bent · Harmonic Web · Video FX · Tiamat (camera)

```
python main.py
```

---

### TIAMAT Synth Studio  `tiamat/`
Companion preset design studio for the camera engine. Deep parameter panel with real-time camera preview — build and save presets that load directly into Video Life.

| Feature | Detail |
|---------|--------|
| Warp | Flow, Scan, Magnetic — with Twist |
| Feedback | Blend, Displace, Burn, Streak |
| Glitch | Corrupt, Burn, Static |
| Color | Hue, sat, contrast, posterize, palette ×4 |
| LFO | Sine modulation on any parameter |
| Presets | JSON — load directly into Video Life |

```
cd tiamat && python main.py
```

---

### FUGUE  `fugue/`
Standalone multi-source video compositor. Load up to 4 clips, blend live on the GPU.

| Feature | Detail |
|---------|--------|
| Tracks | 4 simultaneous video clips (MP4, MOV, AVI, MKV) |
| Blend modes | Off, Mix, Add, Screen, Multiply, Difference, Hard Light |
| Luma key | Key darks or brights with adjustable threshold + softness |
| Chroma key | Colour-distance threshold with analog edge noise |
| **Diff key** | PhotoBooth-style background subtraction — grab reference plate, isolate subject over replacement video |
| Output | Gain, saturation, contrast, CRT scanlines |
| Transport | Per-track play/pause, scrub, speed (0.25×–4×), loop |

**Difference key workflow:**
1. Load subject clip into track A, replacement background into track B
2. Pause A on a frame with no subject (clean background)
3. Click **GRAB PLATE** in the DIFF KEY panel
4. Resume playback, enable **ON** — subject is isolated over track B live
5. Tune `thresh / soft / blur` to taste

```
cd fugue && python main.py
```

---

## Requirements

```bash
pip install PyQt6 PyOpenGL PyOpenGL_accelerate numpy opencv-python
# Optional: pip install python-rtmidi  (MIDI)
# Optional: pip install python-link-extension  (Ableton Link)
```

macOS 12+, Windows 10/11, Ubuntu 22.04+ · OpenGL 3.3+

---

## Download

Pre-built binaries (no Python required) →
**[github.com/nijinsky23/video-life/releases](https://github.com/nijinsky23/video-life/releases)**

macOS: right-click → Open on first launch to bypass Gatekeeper.

---

## Website

**[nijinsky23.github.io/video-life](https://nijinsky23.github.io/video-life)**
(or wherever it's deployed — see `website/`)

---

## License

MIT — free for personal, commercial, and performance use.
