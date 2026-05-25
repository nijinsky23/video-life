"""Live camera capture — PyObjC AVFoundation for devices (macOS), OpenCV for RTSP.

On macOS: AVFoundation path handles built-in, Continuity, and External (iPhone) cameras
          via PyObjC; Qt Multimedia handles standard built-in cameras as fallback.
On Windows/Linux: Qt Multimedia + OpenCV/RTSP paths only (AVFoundation unavailable).
"""

import ctypes
import sys
import threading
import time

import cv2
import numpy as np

from PyQt6.QtCore import QObject, pyqtSignal
from PyQt6.QtMultimedia import QMediaDevices

# ── macOS-only: PyObjC / AVFoundation ─────────────────────────────────────────
_MACOS = sys.platform == 'darwin'

if _MACOS:
    try:
        import objc
        from Foundation import NSObject
        from AVFoundation import (
            AVCaptureDeviceDiscoverySession, AVMediaTypeVideo,
            AVCaptureDeviceTypeBuiltInWideAngleCamera,
            AVCaptureDeviceTypeContinuityCamera,
            AVCaptureDeviceTypeExternalUnknown,
            AVCaptureSession, AVCaptureDeviceInput,
            AVCaptureVideoDataOutput,
            AVCaptureSessionPreset1280x720,
        )
        from CoreMedia import CMSampleBufferGetImageBuffer
        from Quartz.CoreVideo import (
            CVPixelBufferLockBaseAddress, CVPixelBufferUnlockBaseAddress,
            CVPixelBufferGetWidth, CVPixelBufferGetHeight,
            CVPixelBufferGetBytesPerRow,
            kCVPixelFormatType_32BGRA, kCVPixelBufferPixelFormatTypeKey,
        )

        # GCD serial queue for AVCaptureVideoDataOutput callbacks.
        # objc.objc_object(c_void_p=...) wraps the raw ctypes int as a typed ObjC object —
        # required because PyObjC's setSampleBufferDelegate_queue_ expects an ObjC type,
        # not a plain Python integer.
        _libdispatch = ctypes.CDLL('/usr/lib/system/libdispatch.dylib')
        _libdispatch.dispatch_queue_create.restype  = ctypes.c_void_p
        _libdispatch.dispatch_queue_create.argtypes = [ctypes.c_char_p, ctypes.c_void_p]
        _CAPTURE_QUEUE = objc.objc_object(
            c_void_p=_libdispatch.dispatch_queue_create(b'com.videolife.camera', None))

        # PyObjC's CVPixelBufferGetBaseAddress returns an objc.varlist (no buffer protocol).
        # Call it directly via ctypes to get a usable integer address.
        _CoreVideo = ctypes.CDLL('/System/Library/Frameworks/CoreVideo.framework/CoreVideo')
        _CoreVideo.CVPixelBufferGetBaseAddress.restype  = ctypes.c_void_p
        _CoreVideo.CVPixelBufferGetBaseAddress.argtypes = [ctypes.c_void_p]

        def _cv_pb_raw_ptr(pb_pyobj):
            """Extract the raw ObjC/CF pointer from a PyObjC CVPixelBufferRef.

            CPython lays out PyObjC objects as {ob_refcnt[8], ob_type[8], cobject[8], ...},
            so the ObjC pointer is always at index [2] in a void-pointer view of id(obj).
            """
            return ctypes.cast(id(pb_pyobj), ctypes.POINTER(ctypes.c_void_p))[2]

        class _VideoLifeFrameDelegate(NSObject):
            """AVCaptureVideoDataOutputSampleBufferDelegate in Python."""

            def initWithCallback_(self, callback):
                self = objc.super(_VideoLifeFrameDelegate, self).init()
                if self is None:
                    return None
                self._callback = callback
                return self

            @objc.typedSelector(b'v@:@@@')
            def captureOutput_didOutputSampleBuffer_fromConnection_(
                    self, output, sample_buffer, connection):
                try:
                    pb = CMSampleBufferGetImageBuffer(sample_buffer)
                    if pb is None:
                        return
                    CVPixelBufferLockBaseAddress(pb, 1)   # 1 = kCVPixelBufferLock_ReadOnly
                    try:
                        w      = CVPixelBufferGetWidth(pb)
                        h      = CVPixelBufferGetHeight(pb)
                        bpr    = CVPixelBufferGetBytesPerRow(pb)
                        pb_ptr = _cv_pb_raw_ptr(pb)
                        base   = _CoreVideo.CVPixelBufferGetBaseAddress(pb_ptr)
                        raw    = (ctypes.c_uint8 * (h * bpr)).from_address(base)
                        bgra   = np.frombuffer(raw, dtype=np.uint8).reshape(h, bpr)[:, :w * 4].reshape(h, w, 4)
                        arr    = cv2.cvtColor(bgra, cv2.COLOR_BGRA2RGB)
                    finally:
                        CVPixelBufferUnlockBaseAddress(pb, 1)
                    self._callback(arr)
                except Exception as e:
                    print(f'[Camera] frame error: {e}')

        def _discover_av_devices():
            """Return all AVFoundation camera devices (built-in, Continuity, External)."""
            types = [
                AVCaptureDeviceTypeBuiltInWideAngleCamera,
                AVCaptureDeviceTypeContinuityCamera,
                AVCaptureDeviceTypeExternalUnknown,
            ]
            session = AVCaptureDeviceDiscoverySession.discoverySessionWithDeviceTypes_mediaType_position_(
                types, AVMediaTypeVideo, 0)
            return list(session.devices())

        _AVFOUNDATION_AVAILABLE = True

    except ImportError as _e:
        print(f'[Camera] AVFoundation unavailable: {_e}')
        _AVFOUNDATION_AVAILABLE = False

        def _discover_av_devices():
            return []

else:
    # Non-macOS — stubs so the rest of the module compiles cleanly
    _AVFOUNDATION_AVAILABLE = False

    def _discover_av_devices():
        return []


class CameraEngine(QObject):
    frame_ready    = pyqtSignal(object)  # np.ndarray (H,W,3) RGB uint8
    status_changed = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        # AVFoundation capture path (macOS only)
        self._av_session  = None
        self._av_output   = None
        self._av_delegate = None
        # Qt camera path (all platforms)
        self._qt_camera   = None
        self._qt_session  = None
        self._qt_sink     = None
        # OpenCV RTSP path (all platforms)
        self._cap     = None
        self._running = False
        self._thread  = None
        self._target_fps = 30
        self._frame_interval = 1.0 / 30
        self._last_emit      = 0.0

    # ── Permission ────────────────────────────────────────────────────────────

    @staticmethod
    def auth_status() -> str:
        if not _AVFOUNDATION_AVAILABLE:
            return 'authorized'   # non-macOS: camera access via Qt, no separate permission
        try:
            from AVFoundation import (AVCaptureDevice, AVMediaTypeVideo,
                                      AVAuthorizationStatusAuthorized,
                                      AVAuthorizationStatusDenied,
                                      AVAuthorizationStatusRestricted)
            s = AVCaptureDevice.authorizationStatusForMediaType_(AVMediaTypeVideo)
            if s == AVAuthorizationStatusAuthorized: return 'authorized'
            if s == AVAuthorizationStatusDenied:     return 'denied'
            if s == AVAuthorizationStatusRestricted: return 'restricted'
            return 'not_determined'
        except Exception:
            return 'unknown'

    @staticmethod
    def request_permission(callback):
        """Must be called from the main thread."""
        if not _AVFOUNDATION_AVAILABLE:
            callback(True)
            return
        try:
            from AVFoundation import (AVCaptureDevice, AVMediaTypeVideo,
                                      AVAuthorizationStatusAuthorized)
            if AVCaptureDevice.authorizationStatusForMediaType_(AVMediaTypeVideo) \
                    == AVAuthorizationStatusAuthorized:
                callback(True)
                return
            AVCaptureDevice.requestAccessForMediaType_completionHandler_(
                AVMediaTypeVideo, callback)
        except Exception as e:
            print(f'[Camera] permission: {e}')
            callback(False)

    # ── Device discovery ──────────────────────────────────────────────────────

    # Shared registry populated by scan_devices — maps dropdown index → AV device.
    _av_device_map: dict = {}  # index → AVCaptureDevice (for AV-path cameras)

    @staticmethod
    def scan_devices() -> list[tuple[int, str]]:
        """Return [(index, display_name), …]. Must be called from the main thread."""
        CameraEngine._av_device_map.clear()

        qt_devs = QMediaDevices.videoInputs()
        result: list[tuple[int, str]] = []

        for i, d in enumerate(qt_devs):
            result.append((i, d.description()))

        if _AVFOUNDATION_AVAILABLE:
            av_devs = _discover_av_devices()
            qt_uids = {bytes(d.id()) for d in qt_devs}
            for av in av_devs:
                uid_bytes = av.uniqueID().encode() if isinstance(av.uniqueID(), str) \
                            else bytes(av.uniqueID())
                if uid_bytes not in qt_uids:
                    idx = len(result)
                    CameraEngine._av_device_map[idx] = av
                    result.append((idx, av.localizedName()))
                    print(f'[Camera] AV-only: {av.localizedName()} at index {idx}')

        print(f'[Camera] scan_devices → {[n for _, n in result]}')
        return result

    # ── Start: AV capture session (macOS External / non-Qt cameras) ──────────

    def _start_av_device(self, av_device) -> bool:
        if not _AVFOUNDATION_AVAILABLE:
            return False
        self.stop()
        try:
            session = AVCaptureSession.alloc().init()
            session.beginConfiguration()
            if session.canSetSessionPreset_(AVCaptureSessionPreset1280x720):
                session.setSessionPreset_(AVCaptureSessionPreset1280x720)

            inp, err = AVCaptureDeviceInput.deviceInputWithDevice_error_(av_device, None)
            if err or not session.canAddInput_(inp):
                self.status_changed.emit(f'Cannot open: {av_device.localizedName()}')
                return False
            session.addInput_(inp)

            output = AVCaptureVideoDataOutput.alloc().init()
            output.setVideoSettings_({
                kCVPixelBufferPixelFormatTypeKey: kCVPixelFormatType_32BGRA
            })
            output.setAlwaysDiscardsLateVideoFrames_(True)

            delegate = _VideoLifeFrameDelegate.alloc().initWithCallback_(self._on_av_frame)
            output.setSampleBufferDelegate_queue_(delegate, _CAPTURE_QUEUE)

            if not session.canAddOutput_(output):
                self.status_changed.emit('Cannot add video output')
                return False
            session.addOutput_(output)
            session.commitConfiguration()
            session.startRunning()

            self._av_session  = session
            self._av_output   = output
            self._av_delegate = delegate
            self.status_changed.emit(f'Live: {av_device.localizedName()}')
            return True
        except Exception as e:
            self.status_changed.emit(f'AV session error: {e}')
            print(f'[Camera] _start_av_device: {e}')
            return False

    def _on_av_frame(self, rgb_arr: np.ndarray):
        now = time.monotonic()
        if now - self._last_emit < self._frame_interval:
            return
        self._last_emit = now
        self.frame_ready.emit(rgb_arr)

    # ── Start: Qt camera (standard built-in cameras, all platforms) ──────────

    def _start_qt_device(self, qt_index: int) -> bool:
        from PyQt6.QtMultimedia import QCamera, QMediaCaptureSession, QVideoSink
        from PyQt6.QtGui import QImage
        self.stop()
        devices = QMediaDevices.videoInputs()
        if qt_index >= len(devices):
            self.status_changed.emit(f'Camera {qt_index} not found')
            return False
        self._qt_sink    = QVideoSink(self)
        self._qt_camera  = QCamera(devices[qt_index], self)
        self._qt_session = QMediaCaptureSession(self)
        self._qt_session.setCamera(self._qt_camera)
        self._qt_session.setVideoSink(self._qt_sink)
        self._qt_sink.videoFrameChanged.connect(self._on_qt_frame)
        self._qt_camera.start()
        self.status_changed.emit(f'Live: {devices[qt_index].description()}')
        return True

    def _on_qt_frame(self, frame):
        from PyQt6.QtGui import QImage
        image = frame.toImage().convertToFormat(QImage.Format.Format_RGB888)
        if image.isNull():
            return
        w, h = image.width(), image.height()
        ptr  = image.constBits()
        ptr.setsize(h * w * 3)
        arr  = np.frombuffer(ptr, dtype=np.uint8).reshape((h, w, 3)).copy()
        self.frame_ready.emit(arr)

    # ── Public: start device by dropdown index ────────────────────────────────

    def start_device(self, index: int) -> bool:
        if index in CameraEngine._av_device_map:
            return self._start_av_device(CameraEngine._av_device_map[index])
        return self._start_qt_device(index)

    # ── Start: RTSP (OpenCV/FFmpeg, all platforms) ────────────────────────────

    def start_rtsp(self, url: str) -> bool:
        self.stop()
        cap = cv2.VideoCapture(url, cv2.CAP_FFMPEG)
        if not cap.isOpened():
            self.status_changed.emit(f'Cannot connect: {url}')
            return False
        self._cap     = cap
        self._running = True
        self._thread  = threading.Thread(
            target=self._capture_loop, daemon=True, name='CameraRTSP')
        self._thread.start()
        self.status_changed.emit('Live: RTSP stream')
        return True

    def _capture_loop(self):
        min_dt = 1.0 / max(1, self._target_fps)
        last_t = 0.0
        while self._running:
            now  = time.monotonic()
            wait = min_dt - (now - last_t)
            if wait > 0:
                time.sleep(wait)
            ret, frame = self._cap.read()
            if not ret:
                self.status_changed.emit('Stream ended or lost')
                break
            last_t = time.monotonic()
            self.frame_ready.emit(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
        self._running = False

    # ── Stop ─────────────────────────────────────────────────────────────────

    def stop(self):
        # AV session (macOS only)
        if self._av_session is not None:
            self._av_session.stopRunning()
            self._av_output.setSampleBufferDelegate_queue_(None, None)
            self._av_session  = None
            self._av_output   = None
            self._av_delegate = None
        # Qt camera
        if self._qt_camera is not None:
            self._qt_sink.videoFrameChanged.disconnect(self._on_qt_frame)
            self._qt_camera.stop()
            self._qt_session = None
            self._qt_sink    = None
            self._qt_camera  = None
        # RTSP
        self._running = False
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=1.5)
        if self._cap:
            self._cap.release()
            self._cap = None

    def is_running(self) -> bool:
        if self._av_session is not None:
            return self._av_session.isRunning()
        if self._qt_camera is not None:
            return self._qt_camera.isActive()
        return self._running and self._thread is not None and self._thread.is_alive()

    def set_target_fps(self, fps: int):
        self._target_fps    = max(1, fps)
        self._frame_interval = 1.0 / self._target_fps
