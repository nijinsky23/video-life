"""Audio engine: real-time mic/line input, file playback, FFT analysis."""

import threading
import time
import numpy as np
import queue
import os

try:
    import sounddevice as sd
    SD_OK = True
except Exception:
    SD_OK = False

try:
    import scipy.signal as signal
    SCIPY_OK = True
except Exception:
    SCIPY_OK = False


FFT_BINS   = 512
BLOCK_SIZE = 1024
SAMPLE_RATE = 44100


class AudioEngine:
    def __init__(self):
        self.fft_data   = np.zeros(FFT_BINS, dtype=np.float32)
        self.rms        = 0.0
        self.bass       = 0.0
        self.mid        = 0.0
        self.treble     = 0.0
        self.beat       = 0.0
        self._beat_decay = 0.0

        self._lock      = threading.Lock()
        self._stream    = None
        self._file_thread = None
        self._stop_file  = threading.Event()
        self._pause_evt  = threading.Event()   # set = paused
        self._seek_pos   = None                # float 0..1, consumed by worker
        self._file_pos   = 0                   # current sample index
        self._file_len   = 0                   # total samples in loaded file
        self._file_sr    = SAMPLE_RATE
        self._loop_file  = True
        self._buf       = np.zeros(BLOCK_SIZE, dtype=np.float32)

        # Smoothing state
        self._fft_smooth = np.zeros(FFT_BINS, dtype=np.float32)
        self._rms_smooth = 0.0
        self._prev_rms   = 0.0
        self._onset_thresh = 0.05

        self.input_gain    = 1.0
        self.input_devices = self._list_devices()
        self.active_device  = None

    # ── Device enumeration ────────────────────────────────────────────────────

    def _list_devices(self):
        if not SD_OK:
            return []
        try:
            devs = sd.query_devices()
            return [d for d in devs if d['max_input_channels'] > 0]
        except Exception:
            return []

    def get_device_names(self):
        return [d['name'] for d in self.input_devices] if self.input_devices else []

    def refresh_devices(self):
        self.input_devices = self._list_devices()
        return self.get_device_names()

    def set_gain(self, gain: float):
        self.input_gain = max(0.0, float(gain))

    # ── Live input ────────────────────────────────────────────────────────────

    def start_input(self, device_index=None):
        if not SD_OK:
            return
        self.stop()
        try:
            dev_id = None
            ch = 1
            if device_index is not None and device_index < len(self.input_devices):
                dev = self.input_devices[device_index]
                dev_id = dev['index']
                ch = min(int(dev['max_input_channels']), 2)

            self._stream = sd.InputStream(
                device=dev_id,
                samplerate=SAMPLE_RATE,
                channels=ch,
                blocksize=BLOCK_SIZE,
                dtype='float32',
                callback=self._audio_callback,
            )
            self._stream.start()
        except Exception as e:
            print(f"Audio input error: {e}")

    def _audio_callback(self, indata, frames, time_info, status):
        mono = indata.mean(axis=1) if indata.shape[1] > 1 else indata[:, 0].copy()
        if self.input_gain != 1.0:
            mono = mono * self.input_gain
        self._process(mono)

    # ── File playback ─────────────────────────────────────────────────────────

    def play_file(self, path: str):
        self.stop_file()
        self._stop_file.clear()
        self._pause_evt.clear()
        self._seek_pos  = None
        self._file_pos  = 0
        self._file_len  = 0
        self._file_sr   = SAMPLE_RATE
        self._file_thread = threading.Thread(
            target=self._play_file_worker, args=(path,), daemon=True)
        self._file_thread.start()

    def _load_audio(self, path: str):
        """Load entire file to a float32 mono array. Returns (data, sr)."""
        # 1. soundfile — handles wav/flac/ogg/aiff and more via libsndfile
        try:
            import soundfile as sf
            data, sr = sf.read(path, dtype='float32', always_2d=False)
            if data.ndim > 1:
                data = data[:, 0]
            return data, int(sr)
        except Exception:
            pass

        # 2. stdlib wave — WAV only
        try:
            import wave
            with wave.open(path, 'rb') as wf:
                sr = wf.getframerate()
                nc = wf.getnchannels()
                raw = wf.readframes(wf.getnframes())
            data = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
            if nc > 1:
                data = data[::nc]
            return data, sr
        except Exception:
            pass

        # 3. scipy wavfile — WAV only, but a different implementation
        try:
            from scipy.io import wavfile
            sr, data = wavfile.read(path)
            data = data.astype(np.float32)
            if data.ndim > 1:
                data = data[:, 0]
            if np.abs(data).max() > 1.0:
                data /= 32768.0
            return data, int(sr)
        except Exception:
            pass

        # 4. ffmpeg — handles mp3/mp4/m4a/aac/ogg/flac and virtually anything else
        try:
            import subprocess, tempfile, struct
            with tempfile.NamedTemporaryFile(suffix='.wav', delete=False) as tmp:
                tmp_path = tmp.name
            result = subprocess.run(
                ['ffmpeg', '-y', '-i', path,
                 '-ac', '1', '-ar', str(SAMPLE_RATE),
                 '-f', 's16le', tmp_path],
                capture_output=True, timeout=60,
            )
            if result.returncode == 0:
                raw = open(tmp_path, 'rb').read()
                os.remove(tmp_path)
                data = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
                return data, SAMPLE_RATE
            os.remove(tmp_path)
        except Exception:
            pass

        raise RuntimeError(f'Cannot decode audio: {path}')

    def _play_file_worker(self, path: str):
        try:
            data, sr = self._load_audio(path)
        except Exception as e:
            print(f"File load error: {e}")
            return

        n = len(data)
        with self._lock:
            self._file_len = n
            self._file_sr  = sr

        pos      = 0
        interval = BLOCK_SIZE / max(sr, 1)
        next_tick = time.perf_counter()

        while not self._stop_file.is_set():
            # Consume a pending seek
            if self._seek_pos is not None:
                pos = int(self._seek_pos * n)
                pos = max(0, min(pos, n - 1))
                self._seek_pos = None
                next_tick = time.perf_counter()  # reset clock after seek

            if self._pause_evt.is_set():
                time.sleep(0.02)
                next_tick = time.perf_counter()  # reset clock after pause
                continue

            end = pos + BLOCK_SIZE
            if end <= n:
                chunk = data[pos:end]
                pos = end
            elif self._loop_file:
                # Seamlessly stitch tail + head — no timing burst
                tail = data[pos:]
                head = data[:end - n]
                chunk = np.concatenate([tail, head])
                pos = end - n
            else:
                # Non-looping end of file
                tail = data[pos:]
                if len(tail) > 0:
                    self._process(tail)
                with self._lock:
                    self._file_pos = n
                break

            self._process(chunk)
            with self._lock:
                self._file_pos = pos

            # Drift-compensated sleep: account for processing time
            next_tick += interval
            sleep_time = next_tick - time.perf_counter()
            if sleep_time > 0:
                time.sleep(sleep_time)
            else:
                next_tick = time.perf_counter()  # fell behind, reset

    # ── Transport controls ────────────────────────────────────────────────────

    def pause_file(self):
        self._pause_evt.set()

    def resume_file(self):
        self._pause_evt.clear()

    def toggle_pause(self):
        if self._pause_evt.is_set():
            self._pause_evt.clear()
        else:
            self._pause_evt.set()

    def is_paused(self) -> bool:
        return self._pause_evt.is_set()

    def is_file_active(self) -> bool:
        return self._file_thread is not None and self._file_thread.is_alive()

    def seek_file(self, position: float):
        """position: 0.0 (start) … 1.0 (end)"""
        self._seek_pos = max(0.0, min(1.0, float(position)))

    def get_file_position(self):
        """Returns (current_seconds, total_seconds)."""
        with self._lock:
            sr  = max(self._file_sr, 1)
            pos = self._file_pos / sr
            dur = self._file_len / sr
        return pos, dur

    def set_loop(self, loop: bool):
        self._loop_file = bool(loop)

    def inject_beat(self, velocity: float):
        """Trigger a synthetic beat impulse (e.g. from keyboard MIDI note-on)."""
        v = max(0.0, min(1.0, float(velocity)))
        self._beat_decay = max(self._beat_decay, v)
        with self._lock:
            self.beat = self._beat_decay

    def stop_file(self):
        self._stop_file.set()
        self._pause_evt.clear()
        if self._file_thread:
            self._file_thread.join(timeout=0.5)

    # ── Signal processing ─────────────────────────────────────────────────────

    def _process(self, mono: np.ndarray):
        if len(mono) == 0:
            return

        # RMS
        rms = float(np.sqrt(np.mean(mono ** 2)))

        # Beat / onset detection (simple energy delta)
        beat = 0.0
        delta = rms - self._prev_rms
        if delta > self._onset_thresh:
            beat = min(delta * 8.0, 1.0)
        self._prev_rms = rms

        # FFT
        windowed = mono * np.hanning(len(mono))
        fft_raw  = np.abs(np.fft.rfft(windowed, n=FFT_BINS * 2))[:FFT_BINS]
        fft_norm = np.clip(fft_raw / (np.max(fft_raw) + 1e-6) * (1.0 + rms * 2.0), 0, 1).astype(np.float32)

        # Smooth FFT
        alpha = 0.3
        self._fft_smooth = alpha * fft_norm + (1 - alpha) * self._fft_smooth

        # Band energies
        b1 = int(FFT_BINS * 0.04)   # bass: 0 – 4%
        b2 = int(FFT_BINS * 0.20)   # mid:  4 – 20%
        bass   = float(np.mean(self._fft_smooth[:b1]) * 2.5)
        mid    = float(np.mean(self._fft_smooth[b1:b2]) * 2.0)
        treble = float(np.mean(self._fft_smooth[b2:]) * 3.0)

        # Beat decay
        self._beat_decay = max(self._beat_decay * 0.85 + beat, beat)

        with self._lock:
            self.fft_data = self._fft_smooth.copy()
            self.rms      = min(rms * 3.0, 1.0)
            self.bass     = min(bass, 1.0)
            self.mid      = min(mid,  1.0)
            self.treble   = min(treble, 1.0)
            self.beat     = min(self._beat_decay, 1.0)

    def get_data(self):
        with self._lock:
            return (
                self.fft_data.copy(),
                self.rms, self.bass, self.mid, self.treble, self.beat
            )

    # ── Cleanup ───────────────────────────────────────────────────────────────

    def stop(self):
        self.stop_file()
        if self._stream:
            try:
                self._stream.stop()
                self._stream.close()
            except Exception:
                pass
            self._stream = None

    def silence(self):
        """Feed a quiet tone on startup so visuals aren't dead before audio connects."""
        self._stop_file.clear()
        t = 0.0
        while not self._stop_file.is_set():
            t += BLOCK_SIZE / SAMPLE_RATE
            dummy = np.sin(2 * np.pi * 220 * np.linspace(t, t + BLOCK_SIZE / SAMPLE_RATE, BLOCK_SIZE))
            dummy *= 0.1
            self._process(dummy.astype(np.float32))
            time.sleep(0.02)
