"""
FUGUE — video file player.

Each VideoPlayer decodes one file in a background thread at native FPS,
exposing the latest frame through get_frame().  Drift-compensated sleep
keeps timing accurate without blocking the UI.
"""

import threading
import time
import numpy as np

try:
    import cv2
    CV2_OK = True
except ImportError:
    CV2_OK = False


class VideoPlayer:
    """Threaded playback for one video file. Thread-safe frame access."""

    def __init__(self):
        self._lock          = threading.Lock()
        self._stop_evt      = threading.Event()
        self._pause_evt     = threading.Event()   # set = paused
        self._thread        = None

        self._frame         = None      # latest RGB uint8 ndarray
        self._path          = None
        self._name          = ''
        self._fps           = 30.0
        self._total_frames  = 0
        self._cur_frame     = 0
        self._seek_target   = None      # int frame index, consumed by thread

        self._loop          = True
        self._speed         = 1.0
        self._loaded        = False

    # ── Public API ────────────────────────────────────────────────────────────

    def load(self, path: str) -> bool:
        """Load a video file and start playback. Returns True on success."""
        self.stop()
        if not CV2_OK:
            print('[Fugue] cv2 not available — install opencv-python')
            return False
        cap = cv2.VideoCapture(path)
        if not cap.isOpened():
            cap.release()
            return False
        fps = cap.get(cv2.CAP_PROP_FPS)
        self._fps          = fps if fps > 0 else 30.0
        self._total_frames = max(1, int(cap.get(cv2.CAP_PROP_FRAME_COUNT)))
        # Grab first frame immediately so the slot isn't blank on load
        ok, frame = cap.read()
        if ok:
            with self._lock:
                self._frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        cap.release()

        self._path      = path
        self._name      = path.replace('\\', '/').rsplit('/', 1)[-1]
        self._loaded    = True
        self._cur_frame = 0
        self._stop_evt.clear()
        self._pause_evt.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        return True

    def get_frame(self) -> np.ndarray | None:
        with self._lock:
            return self._frame

    def get_progress(self) -> float:
        """0.0–1.0 playback position."""
        with self._lock:
            return self._cur_frame / max(self._total_frames, 1)

    def get_position(self) -> tuple[float, float]:
        """Returns (current_secs, total_secs)."""
        with self._lock:
            fps = max(self._fps, 1.0)
            return self._cur_frame / fps, self._total_frames / fps

    @property
    def name(self) -> str:
        return self._name

    def is_loaded(self) -> bool:
        return self._loaded

    def is_paused(self) -> bool:
        return self._pause_evt.is_set()

    def pause(self):    self._pause_evt.set()
    def resume(self):   self._pause_evt.clear()

    def toggle_pause(self):
        if self._pause_evt.is_set():
            self._pause_evt.clear()
        else:
            self._pause_evt.set()

    def set_speed(self, s: float):
        self._speed = max(0.05, float(s))

    def set_loop(self, v: bool):
        self._loop = bool(v)

    def seek(self, fraction: float):
        """Seek to 0.0–1.0 of total duration."""
        with self._lock:
            self._seek_target = int(
                max(0.0, min(1.0, fraction)) * self._total_frames)

    def stop(self):
        self._stop_evt.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=1.5)
        self._thread   = None
        self._loaded   = False
        with self._lock:
            self._frame = None
        self._cur_frame = 0

    # ── Playback thread ───────────────────────────────────────────────────────

    def _run(self):
        cap = cv2.VideoCapture(self._path)
        if not cap.isOpened():
            return

        interval  = 1.0 / max(self._fps, 1.0)
        next_tick = time.perf_counter()

        while not self._stop_evt.is_set():
            # Consume pending seek
            with self._lock:
                tgt = self._seek_target
                self._seek_target = None
            if tgt is not None:
                cap.set(cv2.CAP_PROP_POS_FRAMES, tgt)
                next_tick = time.perf_counter()

            if self._pause_evt.is_set():
                time.sleep(0.02)
                next_tick = time.perf_counter()
                continue

            ok, frame = cap.read()
            if not ok:
                if self._loop:
                    cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
                    with self._lock:
                        self._cur_frame = 0
                    next_tick = time.perf_counter()
                    continue
                else:
                    break

            rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            with self._lock:
                self._frame     = rgb
                self._cur_frame = int(cap.get(cv2.CAP_PROP_POS_FRAMES))

            # Drift-compensated sleep
            next_tick += interval / max(self._speed, 0.05)
            sleep = next_tick - time.perf_counter()
            if sleep > 0:
                time.sleep(sleep)
            else:
                next_tick = time.perf_counter()   # fell behind — reset

        cap.release()
