"""MIDI engine: runs all rtmidi/mido code in a subprocess to survive CoreMIDI crashes."""

import multiprocessing as _mp
import threading
import queue
import time
from typing import Callable


# ── Subprocess worker ─────────────────────────────────────────────────────────
# This function runs in a spawned child process.  If rtmidi calls std::terminate
# and aborts, only the child dies — the main app continues unaffected.

def _midi_worker(port_q, event_q, cmd_q):
    try:
        import mido
    except Exception as e:
        port_q.put(('error', str(e)))
        return

    try:
        names = mido.get_input_names()
        port_q.put(('ports', names))
    except Exception as e:
        port_q.put(('error', str(e)))
        return

    current_port = None
    while True:
        # Wait for a command
        try:
            cmd = cmd_q.get(timeout=0.5)
        except Exception:
            # Poll pending MIDI if a port is open
            if current_port is not None:
                _drain(current_port, event_q)
            continue

        if cmd is None:               # stop signal
            if current_port is not None:
                try:
                    current_port.close()
                except Exception:
                    pass
            return

        if cmd == '__close__':
            if current_port is not None:
                try:
                    current_port.close()
                except Exception:
                    pass
                current_port = None
            continue

        if cmd == '__refresh__':
            # Re-query CoreMIDI — picks up Bluetooth LE, Network MIDI, new USB devices
            try:
                names = mido.get_input_names()
                event_q.put(('ports', names))
            except Exception as e:
                event_q.put(('error', f'refresh: {e}'))
            continue

        # cmd is a port-name string → open it
        if current_port is not None:
            try:
                current_port.close()
            except Exception:
                pass
            current_port = None

        try:
            current_port = mido.open_input(cmd)
            event_q.put(('opened', cmd))
        except Exception as e:
            event_q.put(('error', str(e)))

        # Drain any already-queued MIDI
        if current_port is not None:
            _drain(current_port, event_q)


def _drain(port, event_q):
    try:
        for msg in port.iter_pending():
            event_q.put(('msg', _encode(msg)))
    except Exception:
        pass


def _encode(msg) -> dict:
    d: dict = {'type': msg.type}
    t = msg.type
    if t == 'control_change':
        d['control'] = msg.control
        d['value']   = msg.value
    elif t in ('note_on', 'note_off'):
        d['note']     = msg.note
        d['velocity'] = msg.velocity
    elif t == 'pitchwheel':
        d['pitch'] = msg.pitch
    return d


# ── Public MidiEngine ─────────────────────────────────────────────────────────

class MidiEngine:
    def __init__(self):
        self._lock       = threading.Lock()
        self._cc_values  = {}
        self._note_on    = {}
        self._cc_callback:     Callable | None = None
        self._note_callback:   Callable | None = None
        self._learn_complete:  Callable | None = None   # (slot, cc) when learn succeeds
        self._learn_map  = {}
        self._learn_slot = None
        self.params      = [0.5] * 8

        self._available = False
        self._port_names: list[str] = []
        self._ports_updated_cb: Callable | None = None

        # Spawn the child process
        ctx = _mp.get_context('spawn')
        self._port_q  = ctx.Queue()
        self._event_q = ctx.Queue()
        self._cmd_q   = ctx.Queue()
        self._proc    = ctx.Process(
            target=_midi_worker,
            args=(self._port_q, self._event_q, self._cmd_q),
            daemon=True
        )
        self._proc.start()

        # Collect the port list (give it up to 4 s; child may abort sooner)
        try:
            kind, payload = self._port_q.get(timeout=4.0)
            if kind == 'ports':
                self._port_names = payload
                self._available  = True
        except Exception:
            pass   # child crashed or timed out — MIDI unavailable

        # Background thread to read MIDI events from the child
        self._stop_evt = threading.Event()
        self._reader   = threading.Thread(target=self._read_loop, daemon=True)
        self._reader.start()

    # ── Port management ───────────────────────────────────────────────────────

    def get_port_names(self) -> list[str]:
        return list(self._port_names)

    def open_port(self, port_name: str):
        if not self._available or not port_name:
            return
        try:
            self._cmd_q.put(port_name)
        except Exception:
            pass

    def refresh_ports(self, callback: Callable | None = None):
        """Re-query CoreMIDI for connected devices (Bluetooth, Network, USB hotplug).
        Results arrive via the callback set with set_ports_updated_cb, or the one
        passed directly here (one-shot override)."""
        if callback:
            self._ports_updated_cb = callback
        if not self._available:
            return
        try:
            self._cmd_q.put('__refresh__')
        except Exception:
            pass

    def set_ports_updated_cb(self, cb: Callable | None):
        self._ports_updated_cb = cb

    def close(self):
        if not self._available:
            return
        try:
            self._cmd_q.put('__close__')
        except Exception:
            pass

    def shutdown(self):
        self._stop_evt.set()
        try:
            self._cmd_q.put(None)
        except Exception:
            pass
        if self._proc.is_alive():
            self._proc.join(timeout=2.0)
            if self._proc.is_alive():
                self._proc.kill()

    # ── Event reader (runs in main-process thread) ────────────────────────────

    def _read_loop(self):
        while not self._stop_evt.is_set():
            try:
                kind, payload = self._event_q.get(timeout=0.1)
            except Exception:
                if not (self._proc.is_alive() or self._stop_evt.is_set()):
                    # Child died unexpectedly
                    self._available = False
                    break
                continue

            if kind == 'msg':
                self._handle(payload)
            elif kind == 'ports':
                # Result of a __refresh__ command — update port list
                self._port_names = payload
                if self._ports_updated_cb:
                    self._ports_updated_cb(payload)
            elif kind == 'error':
                print(f"MIDI worker error: {payload}")

    def _handle(self, d: dict):
        t = d.get('type', '')
        if t == 'control_change':
            cc  = d['control']
            val = d['value'] / 127.0
            with self._lock:
                self._cc_values[cc] = val
            if self._learn_slot is not None:
                completed_slot = self._learn_slot
                self._learn_map[completed_slot] = cc
                self._learn_slot = None
                if self._learn_complete:
                    self._learn_complete(completed_slot, cc)
            with self._lock:
                for slot, mapped_cc in self._learn_map.items():
                    if mapped_cc == cc and slot < len(self.params):
                        self.params[slot] = val
            if self._cc_callback:
                self._cc_callback(cc, val)

        elif t in ('note_on', 'note_off'):
            note = d['note']
            vel  = (d['velocity'] / 127.0) if t == 'note_on' else 0.0
            with self._lock:
                self._note_on[note] = vel
            if self._note_callback:
                self._note_callback(note, vel)

        elif t == 'pitchwheel':
            val = (d['pitch'] + 8192) / 16383.0
            with self._lock:
                self._cc_values[128] = val
            if self._cc_callback:
                self._cc_callback(128, val)

    # ── CV gate simulation ────────────────────────────────────────────────────

    def inject_cv_gate(self, gate: float, pitch: float = 0.5):
        with self._lock:
            self._cc_values[127] = gate
            self._cc_values[126] = pitch

    # ── MIDI learn ────────────────────────────────────────────────────────────

    def inject_note(self, note: int, velocity: float):
        """Inject a synthetic note event (e.g. from computer keyboard MIDI mode)."""
        with self._lock:
            self._note_on[note] = velocity
        if self._note_callback:
            self._note_callback(note, velocity)

    def set_learn_complete_cb(self, cb: Callable | None):
        self._learn_complete = cb

    def start_learn(self, slot: int):
        self._learn_slot = slot

    def cancel_learn(self):
        self._learn_slot = None

    def clear_mapping(self, slot: int):
        self._learn_map.pop(slot, None)

    def get_mapping(self, slot: int) -> int | None:
        return self._learn_map.get(slot)

    # ── Getters ───────────────────────────────────────────────────────────────

    def get_params(self) -> list[float]:
        with self._lock:
            return list(self.params)

    def get_cc(self, cc_num: int) -> float:
        with self._lock:
            return self._cc_values.get(cc_num, 0.0)

    def set_callback(self, cc_cb=None, note_cb=None):
        self._cc_callback   = cc_cb
        self._note_callback = note_cb
