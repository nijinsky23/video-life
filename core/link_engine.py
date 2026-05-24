"""Ableton Link engine — BPM and beat-phase sync over LAN/WiFi.

Requires:  pip install abletonlink
If the package is not installed the engine stays in 'unavailable' state
and the rest of the app continues unaffected.

Link is Ableton's open peer-to-peer sync protocol.  Any app on the same
WiFi network that supports Link (Ableton Live, Ableton Note on iOS,
Traktor, GarageBand, etc.) will automatically synchronise tempo and beat
phase with Video Life once Link is enabled here.
"""

import threading
import time
from typing import Callable, Optional

_QUANTUM = 4.0   # musical 'bar' length in beats (4/4 time)


class LinkEngine:
    def __init__(self, initial_bpm: float = 120.0):
        self._link        = None
        self._available   = False
        self._lock        = threading.Lock()
        self._bpm         = initial_bpm
        self._beat        = 0.0
        self._phase       = 0.0
        self._peers       = 0
        self._prev_phase  = 0.0   # used to detect downbeat crossing
        self._beat_cb: Optional[Callable] = None   # called on each downbeat

        try:
            import link as abletonlink
            self._link      = abletonlink.Link(initial_bpm)
            self._available = True
            print('[Link] available')
        except ImportError:
            print('[Link] link module not found — run via run.sh for Ableton Link support')
        except Exception as e:
            print(f'[Link] init error: {e}')

    # ── Properties ────────────────────────────────────────────────────────────

    @property
    def available(self) -> bool:
        return self._available

    @property
    def enabled(self) -> bool:
        if self._link is None:
            return False
        try:
            return bool(self._link.enabled)
        except Exception:
            return False

    @enabled.setter
    def enabled(self, val: bool):
        if self._link is None:
            return
        try:
            self._link.enabled = bool(val)
        except Exception:
            pass

    # ── State polling (call from a timer at ~30-60 Hz) ────────────────────────

    def poll(self) -> tuple[int, float, float, float]:
        """Return (peers, bpm, beat, phase_0_to_1).
        beat   = absolute beat counter (keeps incrementing)
        phase  = position within the bar, 0.0–1.0
        """
        if self._link is None or not self.enabled:
            return 0, self._bpm, 0.0, 0.0

        try:
            micros = self._link.clock().micros()

            # abletonlink exposes captureSessionState() or captureAudioSessionState()
            try:
                state = self._link.captureSessionState()
            except AttributeError:
                state = self._link.captureAudioSessionState()

            bpm   = float(state.tempo())
            beat  = float(state.beatAtTime(micros, _QUANTUM))
            phase = float(state.phaseAtTime(micros, _QUANTUM)) / _QUANTUM
            peers = int(self._link.numPeers())

            with self._lock:
                self._bpm        = bpm
                self._beat       = beat
                prev             = self._prev_phase
                self._phase      = phase
                self._peers      = peers
                self._prev_phase = phase

            # Downbeat callback: phase wrapped past 0 (e.g. 0.99 → 0.01)
            if prev > 0.8 and phase < 0.2 and self._beat_cb:
                self._beat_cb(bpm)

            return peers, bpm, beat, phase

        except Exception:
            return 0, self._bpm, 0.0, 0.0

    # ── Tempo control ─────────────────────────────────────────────────────────

    def set_bpm(self, bpm: float):
        self._bpm = max(20.0, min(999.0, float(bpm)))
        if self._link is None or not self.enabled:
            return
        try:
            micros = self._link.clock().micros()
            try:
                state = self._link.captureSessionState()
            except AttributeError:
                state = self._link.captureAudioSessionState()
            state.setTempo(self._bpm, micros)
            try:
                self._link.commitSessionState(state)
            except AttributeError:
                self._link.commitAudioSessionState(state)
        except Exception:
            pass

    # ── Downbeat callback ─────────────────────────────────────────────────────

    def set_beat_callback(self, cb: Optional[Callable]):
        """cb(bpm) is called on each bar downbeat when Link is running."""
        self._beat_cb = cb

    # ── Cached getters (last polled values) ───────────────────────────────────

    def get_bpm(self) -> float:
        with self._lock:
            return self._bpm

    def get_phase(self) -> float:
        """0.0 = downbeat, 1.0 = just before next downbeat."""
        with self._lock:
            return self._phase

    def get_peers(self) -> int:
        with self._lock:
            return self._peers
