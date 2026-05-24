# video-life-core

Shared library extracted from [Video Life](../README.md).  
All future synthesizers in this collection build on this foundation.

## Contents

| Module | Purpose |
|---|---|
| `gl_base.py` | Abstract `GLBase(QOpenGLWidget)` — full GL infrastructure |
| `audio_engine.py` | Real-time FFT / RMS / beat detection via `sounddevice` |
| `midi_engine.py` | MIDI CC + note input via `mido` / `python-rtmidi` |
| `link_engine.py` | Ableton Link tempo sync |
| `recorder.py` | Frame-accurate MP4 recording via OpenCV |
| `camera_engine.py` | Camera capture (AVFoundation on macOS, Qt Multimedia elsewhere) |

## Building a new synthesizer

```python
from core import GLBase, AudioEngine, MidiEngine

VERT = """
#version 330 core
in vec2 in_position;
in vec2 in_uv;
out vec2 uv;
void main() { gl_Position = vec4(in_position, 0, 1); uv = in_uv; }
"""

FRAG = """
#version 330 core
uniform float u_time;
uniform vec2  u_resolution;
uniform float u_rms;
out vec4 fragColor;
void main() {
    vec2 st = gl_FragCoord.xy / u_resolution;
    fragColor = vec4(st.x + u_rms, st.y, sin(u_time) * 0.5 + 0.5, 1.0);
}
"""

class MySynth(GLBase):
    def initShaders(self):
        self._prog = self._link_program(VERT, FRAG)
        self._cache_locs(self._prog)

    def _paint_frame(self, t, w, h):
        from OpenGL.GL import *
        glBindFramebuffer(GL_FRAMEBUFFER, self._dfbo())
        glViewport(0, 0, w, h)
        glUseProgram(self._prog)
        self._set_uniforms(self._prog, t, w, h)
        glBindVertexArray(self._vao)
        glDrawArrays(GL_TRIANGLE_STRIP, 0, 4)
```

## GLBase public API

```python
# Audio
canvas.set_audio_data(fft, rms, bass, mid, treble, beat)

# Parameters (p[0]–p[7] uniforms)
canvas.set_params([0.5] * 8)
canvas.set_param(0, 0.8)

# Video input
canvas.set_video_frame(rgb_numpy_array)

# Recording
canvas.set_recorder(VideoRecorder(...))

# Second screen output
canvas.set_output_active(True)
tex_id = canvas.get_output_texture()

# Quality / performance
canvas.set_render_scale(0.5)   # 50% resolution
canvas.set_target_fps(60)

# Screenshot
arr = canvas.grab_frame()      # H×W×3 uint8 numpy array
```

## Planned synthesizers

| Name | Influence | Status |
|---|---|---|
| **Video Life** | EMS Spectron, LZX, Critter & Guitari | ✅ released |
| **Scan Processor** | Rutt-Etra (1972) | 🔜 next |
| **Image Processor** | Sandin IP (1971) | 🔜 planned |
| **Paik Machine** | Paik-Abe Video Synthesizer (1969) | 🔜 planned |
| **Flicker** | Tony Conrad, Peter Kubelka | 🔜 planned |
| **Noise Engine** | Throbbing Gristle, Hafler Trio | 🔜 planned |
