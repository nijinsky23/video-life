"""
Network device discovery for TIAMAT.

Discovers:
  · ONVIF cameras via WS-Discovery multicast (UDP 239.255.255.250:3702)
  · Smart TVs, media renderers via SSDP/UPnP  (UDP 239.255.255.250:1900)
  · Any host with RTSP port 554 / 8554 open on the local /24 subnet
  · HTTP MJPEG cameras on port 80 / 8080 with common path patterns

No external libraries required — pure Python stdlib.
"""

import re
import socket
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from typing import Callable

# ── Constants ─────────────────────────────────────────────────────────────────

ONVIF_ADDR      = ('239.255.255.250', 3702)
SSDP_ADDR       = ('239.255.255.250', 1900)
RTSP_PORTS      = [554, 8554]
HTTP_PORTS      = [80, 8080]
PROBE_TIMEOUT   = 0.25   # seconds per TCP probe
HTTP_TIMEOUT    = 0.5    # HTTP header read timeout

_ONVIF_PROBE = (
    '<?xml version="1.0" encoding="UTF-8"?>'
    '<e:Envelope'
    '  xmlns:e="http://www.w3.org/2003/05/soap-envelope"'
    '  xmlns:w="http://schemas.xmlsoap.org/ws/2004/08/addressing"'
    '  xmlns:d="http://schemas.xmlsoap.org/ws/2005/04/discovery"'
    '  xmlns:dn="http://www.onvif.org/ver10/network/wsdl">'
    '  <e:Header>'
    '    <w:MessageID>uuid:{mid}</w:MessageID>'
    '    <w:To>urn:schemas-xmlsoap-org:ws:2005:04:discovery</w:To>'
    '    <w:Action>http://schemas.xmlsoap.org/ws/2005/04/discovery/Probe</w:Action>'
    '  </e:Header>'
    '  <e:Body>'
    '    <d:Probe><d:Types>dn:NetworkVideoTransmitter</d:Types></d:Probe>'
    '  </e:Body>'
    '</e:Envelope>'
)

_SSDP_MSEARCH = (
    'M-SEARCH * HTTP/1.1\r\n'
    'HOST: 239.255.255.250:1900\r\n'
    'MAN: "ssdp:discover"\r\n'
    'MX: 2\r\n'
    'ST: ssdp:all\r\n'
    '\r\n'
)

# SSDP device-type keywords → friendly label
_SSDP_TYPE_MAP = [
    ('MediaRenderer',    'Smart TV / Media Renderer'),
    ('MediaServer',      'Media Server'),
    ('TVDevice',         'Smart TV'),
    ('urn:samsung',      'Samsung TV'),
    ('urn:lge',          'LG TV'),
    ('urn:sony',         'Sony Device'),
    ('urn:philips',      'Philips TV'),
    ('DigitalSecurityCamera', 'IP Camera'),
    ('NetworkVideoTransmitter','IP Camera'),
    ('rootdevice',       'Network Device'),
]


# ── Data model ────────────────────────────────────────────────────────────────

@dataclass
class DiscoveredDevice:
    name:       str
    address:    str          # IP address string (or 'index N' for local cameras)
    url:        str          # primary URL shown in the editable field
    kind:       str          # 'local_camera' | 'onvif' | 'rtsp' | 'ssdp' | 'mjpeg'
    extra_urls: list[str] = field(default_factory=list)


# ── Scanner ───────────────────────────────────────────────────────────────────

class NetworkScanner:
    """
    Background scanner.  scan() is non-blocking; results arrive via callbacks
    on a daemon thread — use a Qt signal to marshal onto the main thread.

    Example:
        self._scanner = NetworkScanner()
        self._scanner.scan(
            found_cb = lambda dev: self._sig.emit(dev),
            done_cb  = self._sig_done.emit,
        )
    """

    def __init__(self):
        self._stop   = threading.Event()
        self._thread: threading.Thread | None = None

    # ── Public API ────────────────────────────────────────────────────────────

    def scan(self,
             found_cb: Callable[[DiscoveredDevice], None],
             done_cb:  Callable[[], None] | None = None):
        """Start (or restart) a background scan."""
        self.stop()
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run, args=(found_cb, done_cb), daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=1.5)

    # ── Internal ──────────────────────────────────────────────────────────────

    def _run(self, found_cb, done_cb):
        seen: set[str] = set()

        def emit(dev: DiscoveredDevice):
            key = dev.address
            if key not in seen:
                seen.add(key)
                found_cb(dev)

        # 1. ONVIF WS-Discovery — fast, finds IP cameras in < 2 s
        for dev in self._onvif_discover():
            if self._stop.is_set():
                break
            emit(dev)

        # 2. SSDP/UPnP — finds smart TVs, media renderers, some cameras
        for dev in self._ssdp_discover():
            if self._stop.is_set():
                break
            emit(dev)

        # 3. TCP port scan local /24 subnet(s)
        for dev in self._port_scan(skip=seen):
            if self._stop.is_set():
                break
            emit(dev)

        if done_cb:
            done_cb()

    # ── ONVIF WS-Discovery ────────────────────────────────────────────────────

    def _onvif_discover(self) -> list[DiscoveredDevice]:
        probe = _ONVIF_PROBE.format(mid=uuid.uuid4()).encode()
        found: list[DiscoveredDevice] = []
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.setsockopt(socket.SOL_SOCKET,  socket.SO_REUSEADDR,     1)
            sock.setsockopt(socket.IPPROTO_IP,  socket.IP_MULTICAST_TTL, 4)
            sock.settimeout(2.0)
            sock.sendto(probe, ONVIF_ADDR)
            deadline  = time.time() + 2.0
            seen_ips: set[str] = set()
            while time.time() < deadline and not self._stop.is_set():
                try:
                    data, addr = sock.recvfrom(8192)
                    ip = addr[0]
                    if ip in seen_ips:
                        continue
                    seen_ips.add(ip)
                    text  = data.decode('utf-8', errors='ignore')
                    xaddr = self._parse_xaddrs(text)
                    rtsp  = xaddr if xaddr and xaddr.startswith('rtsp://') \
                            else f'rtsp://{ip}:554/stream1'
                    found.append(DiscoveredDevice(
                        name='ONVIF camera',
                        address=ip,
                        url=rtsp,
                        kind='onvif',
                        extra_urls=_rtsp_patterns(ip),
                    ))
                except socket.timeout:
                    break
                except OSError:
                    break
        except OSError:
            pass
        finally:
            try:
                sock.close()
            except Exception:
                pass
        return found

    @staticmethod
    def _parse_xaddrs(text: str) -> str | None:
        m = re.search(r'<[^:>]*:?XAddrs[^>]*>\s*([^<]+)\s*<', text)
        if m:
            urls = m.group(1).strip().split()
            if urls:
                return urls[0]
        return None

    # ── SSDP / UPnP Discovery ─────────────────────────────────────────────────

    def _ssdp_discover(self) -> list[DiscoveredDevice]:
        """Send SSDP M-SEARCH, collect replies from TVs and media devices."""
        msg = _SSDP_MSEARCH.encode()
        found: list[DiscoveredDevice] = []
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM,
                                 socket.IPPROTO_UDP)
            sock.setsockopt(socket.SOL_SOCKET,  socket.SO_REUSEADDR,     1)
            sock.setsockopt(socket.IPPROTO_IP,  socket.IP_MULTICAST_TTL, 4)
            sock.settimeout(2.0)
            sock.sendto(msg, SSDP_ADDR)
            deadline  = time.time() + 2.0
            seen_ips: set[str] = set()
            while time.time() < deadline and not self._stop.is_set():
                try:
                    data, addr = sock.recvfrom(4096)
                    ip   = addr[0]
                    if ip in seen_ips:
                        continue
                    seen_ips.add(ip)
                    text = data.decode('utf-8', errors='ignore')
                    name = self._ssdp_device_name(text)
                    if name is None:
                        continue   # skip uninteresting / router announcements
                    loc  = self._ssdp_location(text)
                    # Prefer RTSP if port 554 happens to be open; otherwise HTTP
                    url  = loc if loc else f'rtsp://{ip}:554/stream1'
                    found.append(DiscoveredDevice(
                        name=name,
                        address=ip,
                        url=url,
                        kind='ssdp',
                        extra_urls=_rtsp_patterns(ip) + _mjpeg_patterns(ip, 80),
                    ))
                except socket.timeout:
                    break
                except OSError:
                    break
        except OSError:
            pass
        finally:
            try:
                sock.close()
            except Exception:
                pass
        return found

    @staticmethod
    def _ssdp_device_name(text: str) -> str | None:
        """Return a friendly device label from an SSDP response, or None to skip."""
        # Parse ST: / NT: header
        st_m = re.search(r'^(?:ST|NT):\s*(.+)$', text, re.IGNORECASE | re.MULTILINE)
        st   = st_m.group(1).strip() if st_m else ''

        # Skip root UPnP infrastructure devices
        if 'InternetGatewayDevice' in st or 'WANDevice' in st or \
           'WANConnectionDevice' in st or 'ssdp:alive' in text.split('\r\n')[0]:
            pass  # still check if it's interesting by type map

        server_m = re.search(r'^SERVER:\s*(.+)$', text, re.IGNORECASE | re.MULTILINE)
        server   = server_m.group(1).strip() if server_m else ''

        combined = st + ' ' + server
        for keyword, label in _SSDP_TYPE_MAP:
            if keyword.lower() in combined.lower():
                # Try to extract a specific model from SERVER header
                if server and 'rootdevice' not in keyword:
                    # Extract the last segment of server string as model hint
                    parts = re.split(r'[\s/,]+', server)
                    model = next((p for p in reversed(parts)
                                  if len(p) > 3 and not p[0].isdigit()), '')
                    if model:
                        return f'{label} ({model})'
                return label

        return None   # uninteresting device

    @staticmethod
    def _ssdp_location(text: str) -> str | None:
        m = re.search(r'^LOCATION:\s*(.+)$', text, re.IGNORECASE | re.MULTILINE)
        return m.group(1).strip() if m else None

    # ── TCP port scan ─────────────────────────────────────────────────────────

    def _port_scan(self, skip: set[str]) -> list[DiscoveredDevice]:
        subnets = _local_subnets()
        if not subnets:
            return []
        hosts = [
            f'{sn}.{i}'
            for sn in subnets
            for i in range(1, 255)
            if f'{sn}.{i}' not in skip
        ]
        found: list[DiscoveredDevice] = []
        with ThreadPoolExecutor(max_workers=80) as pool:
            futures = {pool.submit(self._probe_host, h): h for h in hosts}
            for fut in as_completed(futures):
                if self._stop.is_set():
                    break
                dev = fut.result()
                if dev:
                    found.append(dev)
        return found

    def _probe_host(self, ip: str) -> DiscoveredDevice | None:
        # RTSP ports first
        for port in RTSP_PORTS:
            if _port_open(ip, port, PROBE_TIMEOUT):
                urls = _rtsp_patterns(ip, port)
                return DiscoveredDevice(
                    name='IP camera',
                    address=ip,
                    url=urls[0],
                    kind='rtsp',
                    extra_urls=urls,
                )
        # HTTP MJPEG probe
        for port in HTTP_PORTS:
            if _port_open(ip, port, PROBE_TIMEOUT):
                mjpeg = _probe_mjpeg(ip, port)
                if mjpeg:
                    return DiscoveredDevice(
                        name='MJPEG camera',
                        address=ip,
                        url=mjpeg,
                        kind='mjpeg',
                        extra_urls=_mjpeg_patterns(ip, port),
                    )
        return None


# ── Helpers ───────────────────────────────────────────────────────────────────

def _local_subnets() -> list[str]:
    """Return /24 prefix(es) for the default outbound interface."""
    subnets: list[str] = []
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.settimeout(0.1)
        s.connect(('8.8.8.8', 80))
        ip = s.getsockname()[0]
        s.close()
        if not ip.startswith('127.'):
            subnets.append('.'.join(ip.split('.')[:3]))
    except Exception:
        pass
    return subnets


def _port_open(ip: str, port: int, timeout: float) -> bool:
    try:
        with socket.create_connection((ip, port), timeout=timeout):
            return True
    except (OSError, socket.timeout, ConnectionRefusedError):
        return False


def _rtsp_patterns(ip: str, port: int = 554) -> list[str]:
    """Common RTSP URL patterns — one will work for most cameras."""
    b = f'rtsp://{ip}:{port}'
    return [
        f'{b}/stream1',
        f'{b}/live',
        f'{b}/stream',
        f'{b}/cam/realmonitor?channel=1&subtype=0',   # Dahua
        f'{b}/Streaming/Channels/101',                # Hikvision
        f'{b}/h264Preview_01_main',                   # Reolink
        f'{b}/',
    ]


def _mjpeg_patterns(ip: str, port: int = 80) -> list[str]:
    """Common HTTP MJPEG paths."""
    b = f'http://{ip}:{port}'
    return [
        f'{b}/video',
        f'{b}/mjpeg',
        f'{b}/stream',
        f'{b}/videostream.cgi',
        f'{b}/axis-cgi/mjpg/video.cgi',   # Axis
        f'{b}/webcam.mjpeg',
        f'{b}/mjpg/video.mjpg',            # FOSCAM
        f'{b}/?action=stream',             # many generic cams
    ]


def _probe_mjpeg(ip: str, port: int) -> str | None:
    """
    Try to detect an HTTP MJPEG camera by doing a quick HTTP HEAD / GET on
    common paths.  Returns the working URL, or None if none respond with
    a multipart/x-mixed-replace Content-Type.
    """
    paths = [
        '/video', '/mjpeg', '/stream',
        '/videostream.cgi', '/axis-cgi/mjpg/video.cgi',
        '/webcam.mjpeg', '/mjpg/video.mjpg', '/?action=stream',
    ]
    for path in paths:
        url = f'http://{ip}:{port}{path}'
        try:
            s = socket.create_connection((ip, port), timeout=HTTP_TIMEOUT)
            req = f'GET {path} HTTP/1.0\r\nHost: {ip}\r\nConnection: close\r\n\r\n'
            s.sendall(req.encode())
            s.settimeout(HTTP_TIMEOUT)
            resp = b''
            while b'\r\n\r\n' not in resp:
                chunk = s.recv(512)
                if not chunk:
                    break
                resp += chunk
                if len(resp) > 2048:
                    break
            s.close()
            header = resp.decode('utf-8', errors='ignore').lower()
            if 'multipart/x-mixed-replace' in header or \
               'mjpeg' in header or \
               'image/jpeg' in header:
                return url
        except Exception:
            pass
    return None
