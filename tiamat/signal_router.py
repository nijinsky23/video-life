"""
Signal Router — concurrent multi-source video capture.

Manages up to 4 independent video sources simultaneously:
  · Camera     — any OpenCV-visible device (USB cam, capture card, Continuity Camera)
  · Screen     — a monitor region via mss / PIL.ImageGrab
  · Stream     — any URL OpenCV can open: RTSP, MJPEG-over-HTTP, HLS, local file
  · Synthetic  — solid noise / test pattern when no real source is available

Each source runs in its own daemon thread; the router exposes the latest
frame via get_frame(slot) with no blocking.  Frames are normalised to
RGB uint8 numpy arrays.

Usage:
    router = SignalRouter()
    router.scan_cameras()                       # probe indices 0-7
    router.set_source(0, 'camera', 0)           # slot 0 → camera 0
    router.set_source(1, 'screen', 0)           # slot 1 → monitor 0
    router.set_source(2, 'stream', 'rtsp://...')# slot 2 → IP cam / TV
    frame = router.get_frame(1)                 # numpy H×W×3 or None
    router.close()
"""

import threading
import time
import queue

import cv2
import numpy as np

# ── Screen capture backend ────────────────────────────────────────────────────
try:
    import mss as _mss
    _MSS_OK = True
except ImportError:
    _MSS_OK = False

try:
    from PIL import ImageGrab as _ImageGrab
    _PIL_OK = True
except ImportError:
    _PIL_OK = False

_MAX_SLOTS = 4
_FRAME_W   = 640   # capture width (sources are resized to this)
_FRAME_H   = 360   # capture height


# ─────────────────────────────────────────────────────────────────────────────
# Individual capture workers
# ─────────────────────────────────────────────────────────────────────────────

class _BaseSource:
    """Background-thread video source.  Writes frames to _latest; thread-safe."""

    def __init__(self):
        self._latest : np.ndarray | None = None
        self._lock   = threading.Lock()
        self._stop   = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self.error   = None          # last error string, or None
        self.active  = False

    def start(self):
        self.active = True
        self._stop.clear()
        self._thread.start()

    def stop(self):
        self.active = False
        self._stop.set()
        self._thread.join(timeout=2.0)

    def get_frame(self) -> np.ndarray | None:
        with self._lock:
            return self._latest

    def _put(self, rgb: np.ndarray):
        """Resize and store a new RGB frame."""
        if rgb.shape[1] != _FRAME_W or rgb.shape[0] != _FRAME_H:
            rgb = cv2.resize(rgb, (_FRAME_W, _FRAME_H), interpolation=cv2.INTER_LINEAR)
        with self._lock:
            self._latest = rgb

    def _run(self):
        raise NotImplementedError


class _CameraSource(_BaseSource):
    def __init__(self, index: int):
        super().__init__()
        self._index = index

    def _open(self):
        """Open the capture device, suppressing OpenCV stderr. Returns cap or None."""
        import os
        devnull_fd    = os.open(os.devnull, os.O_WRONLY)
        saved_stderr  = os.dup(2)
        os.dup2(devnull_fd, 2)
        os.close(devnull_fd)
        try:
            cap = cv2.VideoCapture(self._index)
            ok  = cap.isOpened()
        finally:
            os.dup2(saved_stderr, 2)
            os.close(saved_stderr)
        if not ok:
            cap.release()
            return None
        cap.set(cv2.CAP_PROP_FRAME_WIDTH,  _FRAME_W)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, _FRAME_H)
        cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        return cap

    def _run(self):
        cap = self._open()
        if cap is None:
            self.error = f'camera {self._index} not available'
            return
        print(f'[Router] camera {self._index} opened')

        fail_count  = 0
        max_fails   = 10   # consecutive bad reads before reconnect attempt
        retry_delay = 1.0  # seconds before first reconnect; doubles each failure

        while not self._stop.is_set():
            ret, frame = cap.read()
            if ret:
                fail_count = 0
                self.error = None
                self._put(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
            else:
                fail_count += 1
                if fail_count >= max_fails:
                    print(f'[Router] camera {self._index}: {fail_count} consecutive '
                          f'read failures — reconnecting in {retry_delay:.0f}s…')
                    cap.release()
                    self.error = f'camera {self._index}: reconnecting…'
                    if self._stop.wait(retry_delay):
                        break
                    cap = self._open()
                    if cap is None:
                        retry_delay = min(retry_delay * 2, 16.0)
                        self.error  = f'camera {self._index}: disconnected'
                        # Keep trying until stop or success
                        continue
                    print(f'[Router] camera {self._index} reconnected')
                    self.error  = None
                    fail_count  = 0
                    retry_delay = 1.0
                else:
                    time.sleep(0.05)

        cap.release()
        print(f'[Router] camera {self._index} closed')


class _StreamSource(_BaseSource):
    """RTSP / MJPEG-HTTP / HLS / file path — anything OpenCV's VideoCapture accepts."""

    def __init__(self, url: str):
        super().__init__()
        self._url = url

    def _run(self):
        cap = cv2.VideoCapture(self._url)
        if not cap.isOpened():
            self.error = f'stream not available: {self._url}'
            return
        print(f'[Router] stream opened: {self._url[:60]}')
        while not self._stop.is_set():
            ret, frame = cap.read()
            if ret:
                self._put(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
            else:
                time.sleep(0.1)
                cap.release()
                cap = cv2.VideoCapture(self._url)  # reconnect
        cap.release()
        print(f'[Router] stream closed: {self._url[:60]}')


class _ScreenSource(_BaseSource):
    """Screen region capture.  monitor=0 = combined desktop, 1,2,… = individual."""

    def __init__(self, monitor: int = 1, fps: float = 30.0):
        super().__init__()
        self._monitor  = monitor
        self._interval = 1.0 / max(1.0, fps)

    def _run(self):
        print(f'[Router] screen capture: monitor {self._monitor}')
        fail_logged = False
        while not self._stop.is_set():
            frame = self._grab()
            if frame is not None:
                fail_logged = False
                self.error  = None
                self._put(frame)
                time.sleep(self._interval)
            else:
                if not fail_logged:
                    print(f'[Router] screen capture failed — grant Screen Recording permission '
                          f'in System Settings → Privacy & Security (retrying every 5 s)')
                    fail_logged = True
                # Back off — don't spam stderr at 30 fps
                self._stop.wait(5.0)

    def _grab(self) -> np.ndarray | None:
        if _MSS_OK:
            try:
                import mss
                with mss.MSS() as sct:
                    monitors = sct.monitors
                    idx = min(self._monitor, len(monitors) - 1)
                    img = np.array(sct.grab(monitors[idx]))
                    # mss returns BGRA; drop alpha, convert BGR→RGB
                    bgr = img[:, :, :3]
                    return cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
            except Exception as e:
                self.error = str(e)
        if _PIL_OK:
            try:
                from PIL import ImageGrab
                img = ImageGrab.grab()
                return np.array(img.convert('RGB'))
            except Exception:
                pass   # swallow — reported above on first failure
        return None


class _SyntheticSource(_BaseSource):
    """Animated noise pattern — used when a real source isn't available."""

    def _run(self):
        rng = np.random.default_rng()
        while not self._stop.is_set():
            noise = rng.integers(0, 255, (_FRAME_H, _FRAME_W, 3), dtype=np.uint8)
            t = time.time()
            # Add scanline structure to make it more TV-static-like
            bands = np.sin(np.linspace(0, 20 * np.pi, _FRAME_H) + t * 5) * 60
            noise[:, :, 1] = np.clip(noise[:, :, 1].astype(float)
                                     + bands[:, np.newaxis], 0, 255).astype(np.uint8)
            self._put(noise)
            time.sleep(1 / 25.0)


# ─────────────────────────────────────────────────────────────────────────────
# Signal Router
# ─────────────────────────────────────────────────────────────────────────────

class SignalRouter:
    """
    Manages up to MAX_SLOTS (4) concurrent signal sources.

    Slots are indexed 0–3 and correspond to shader uniforms
    u_src0, u_src1, u_src2, u_src3.

    Thread-safety: all public methods are safe to call from Qt's main thread
    while capture threads run independently.
    """

    MAX_SLOTS = _MAX_SLOTS

    def __init__(self):
        self._sources: dict[int, _BaseSource] = {}
        self._available_cameras: list[int]    = []

    # ── Discovery ─────────────────────────────────────────────────────────────

    def scan_cameras(self, max_index: int = 5) -> list[int]:
        """Probe camera indices 0..max_index.  Returns list of available indices.
        OpenCV logs are suppressed during the probe to keep the console clean."""
        import os, sys
        found = []
        print('[Router] scanning for cameras…', flush=True)

        # Suppress OpenCV's stderr chatter while probing non-existent indices
        try:
            cv2.setLogLevel(0)          # silence OpenCV internal messages
        except AttributeError:
            pass
        devnull_fd = os.open(os.devnull, os.O_WRONLY)
        saved_stderr = os.dup(2)
        os.dup2(devnull_fd, 2)
        os.close(devnull_fd)

        try:
            for i in range(max_index + 1):
                cap = cv2.VideoCapture(i)
                if cap.isOpened():
                    found.append(i)
                cap.release()
        finally:
            os.dup2(saved_stderr, 2)    # restore stderr
            os.close(saved_stderr)

        self._available_cameras = found
        print(f'[Router] found cameras: {found}', flush=True)
        return found

    def available_cameras(self) -> list[int]:
        return list(self._available_cameras)

    # ── Source management ─────────────────────────────────────────────────────

    def set_source(self, slot: int, kind: str, arg) -> bool:
        """
        Assign a source to a slot.
          kind='camera'    arg=<int index>
          kind='screen'    arg=<int monitor index, 0=all>
          kind='stream'    arg=<str URL>  (RTSP/HTTP/file)
          kind='noise'     arg=None
          kind='none'      arg=None  → removes source from slot
        Returns True on success.
        """
        self._remove_slot(slot)
        if kind == 'none' or kind is None:
            return True

        if kind == 'camera':
            src = _CameraSource(int(arg))
        elif kind == 'screen':
            src = _ScreenSource(monitor=int(arg))
        elif kind == 'stream':
            src = _StreamSource(str(arg))
        elif kind == 'noise':
            src = _SyntheticSource()
        else:
            print(f'[Router] unknown source kind: {kind}')
            return False

        src.start()
        self._sources[slot] = src
        return True

    def remove_source(self, slot: int):
        self._remove_slot(slot)

    def _remove_slot(self, slot: int):
        if slot in self._sources:
            self._sources[slot].stop()
            del self._sources[slot]

    # ── Frame access ──────────────────────────────────────────────────────────

    def get_frame(self, slot: int) -> np.ndarray | None:
        """Return latest RGB frame for slot, or None if no source / no frame yet."""
        src = self._sources.get(slot)
        return src.get_frame() if src else None

    def is_active(self, slot: int) -> bool:
        return slot in self._sources and self._sources[slot].active

    def slot_error(self, slot: int) -> str | None:
        src = self._sources.get(slot)
        return src.error if src else None

    # ── Cleanup ───────────────────────────────────────────────────────────────

    def close(self):
        for slot in list(self._sources.keys()):
            self._remove_slot(slot)
        print('[Router] all sources closed')
