"""Video recorder: writes frames to .mp4 using OpenCV."""

import threading
import queue
import time
import os
import numpy as np

try:
    import cv2
    CV2_OK = True
except Exception:
    CV2_OK = False


class VideoRecorder:
    def __init__(self, output_dir: str = 'recordings'):
        self.output_dir = output_dir
        os.makedirs(output_dir, exist_ok=True)
        self._queue  = queue.Queue(maxsize=120)
        self._writer = None
        self._thread = None
        self._active = False
        self._path   = ''
        self._fps    = 30
        self._size   = (1280, 720)

    def start(self, path: str, fps: int = 30, size: tuple = (1280, 720)):
        if not CV2_OK:
            print("opencv-python not available; recording disabled")
            return
        self.stop()
        self._path   = path
        self._fps    = fps
        self._size   = size
        self._active = True
        # mp4v (MPEG-4 Part 2) is reliably round-trippable by OpenCV on all
        # platforms.  avc1 on macOS encodes via VideoToolbox but OpenCV's own
        # VideoCapture often can't decode the result — so try mp4v first.
        for codec in ('mp4v', 'avc1'):
            fourcc = cv2.VideoWriter_fourcc(*codec)
            self._writer = cv2.VideoWriter(path, fourcc, fps, size)
            if self._writer.isOpened():
                break
        if not self._writer.isOpened():
            print(f"[Recorder] no working codec for {path} — recording aborted")
            self._active = False
            self._writer = None
            return
        self._thread = threading.Thread(target=self._worker, daemon=True)
        self._thread.start()

    def _worker(self):
        while self._active or not self._queue.empty():
            try:
                frame = self._queue.get(timeout=0.1)
                if self._writer:
                    bgr = cv2.cvtColor(frame, cv2.COLOR_RGB2BGR)
                    self._writer.write(bgr)
            except queue.Empty:
                continue
        if self._writer:
            self._writer.release()
            self._writer = None

    def push_frame(self, rgb_array: np.ndarray):
        if not self._active:
            return
        try:
            self._queue.put_nowait(rgb_array.copy())
        except queue.Full:
            pass

    def stop(self):
        self._active = False
        if self._thread:
            self._thread.join(timeout=3.0)
            self._thread = None

    def is_recording(self):
        return self._active

    def get_path(self):
        return self._path
