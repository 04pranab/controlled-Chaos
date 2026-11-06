"""Company chat transport: UDP on the local network. The ONLY module (besides the offline guard) allowed to use sockets.

Rules it enforces itself:
  * It starts only when the person has switched Company on, and stops when they switch it off.
  * It sends only to private-network / link-local / broadcast / loopback addresses (never the internet), and it tells the
    offline guard so; the guard still refuses everything else.
  * Everything on the wire is an encrypted packet from company_service.seal(); this module never sees message text.
  * Packets for companies this device has not joined are dropped before decryption.
Delivery is best effort (UDP): each message is sent three times and a late joiner asks peers for recent history.
"""
from __future__ import annotations

import logging
import os
import queue
import socket
import threading
import time
from typing import Iterable, List, Optional, Sequence, Tuple

from .. import offline_guard
from .company_service import ChatMessage, CompanyService

log = logging.getLogger("controlled_chaos.company")
DEFAULT_PORT = 47821
MAX_PACKET = 1400
RESEND_DELAYS = (0.4, 1.5)


class LanTransport:
    def __init__(self, port: int = DEFAULT_PORT, targets: Optional[Sequence[Tuple[str, int]]] = None, bind_host: str = ""):
        self.port, self.bind_host = port, bind_host
        self.targets = list(targets) if targets else [("255.255.255.255", port)]
        self._sock: Optional[socket.socket] = None
        self._q: "queue.Queue[bytes]" = queue.Queue(maxsize=500)
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self.running = False
        self.error = ""

    def start(self) -> bool:
        if self.running:
            return True
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            if hasattr(socket, "SO_REUSEPORT"):
                try:
                    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
                except OSError:
                    pass
            s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            s.bind((self.bind_host, self.port))
            s.settimeout(0.5)
        except OSError as exc:
            self.error = f"Could not open the chat port: {exc}"
            log.warning(self.error)
            return False
        self._sock = s
        self._stop.clear()
        offline_guard.allow_lan(True)
        self._thread = threading.Thread(target=self._loop, name="cc-company", daemon=True)
        self._thread.start()
        self.running = True
        self.error = ""
        return True

    def _loop(self) -> None:
        s = self._sock
        while not self._stop.is_set():
            try:
                data, _addr = s.recvfrom(2048)
            except socket.timeout:
                continue
            except OSError:
                break
            if 0 < len(data) <= MAX_PACKET:
                try:
                    self._q.put_nowait(data)
                except queue.Full:
                    pass                                    # flooded: drop

    def send(self, packet: bytes, repeats: bool = True) -> None:
        if not self.running or len(packet) > MAX_PACKET:
            return
        self._send_once(packet)
        if repeats:
            for d in RESEND_DELAYS:
                t = threading.Timer(d, self._send_once, args=(packet,))
                t.daemon = True
                t.start()

    def _send_once(self, packet: bytes) -> None:
        s = self._sock
        if s is None or self._stop.is_set():
            return
        for t in self.targets:
            try:
                s.sendto(packet, t)
            except OSError as exc:
                self.error = f"Send failed: {exc}"

    def drain(self, limit: int = 100) -> List[bytes]:
        out: List[bytes] = []
        while len(out) < limit:
            try:
                out.append(self._q.get_nowait())
            except queue.Empty:
                break
        return out

    def stop(self) -> None:
        self._stop.set()
        self.running = False
        s, self._sock = self._sock, None
        if s is not None:
            try:
                s.close()
            except OSError:
                pass
        if self._thread is not None:
            self._thread.join(timeout=2)
            self._thread = None
        offline_guard.allow_lan(False)


class CompanyRuntime:
    """Glue between the service (rules + storage) and the transport. Called once a second from the UI thread, so all
    database access stays on one thread."""

    def __init__(self, service: CompanyService, transport: Optional[LanTransport] = None):
        self.service = service
        if transport is None:
            # CC_COMPANY_* exist for testing two copies on one machine; the defaults are what real users get.
            port = int(os.environ.get("CC_COMPANY_PORT", DEFAULT_PORT))
            peers = [(h, int(pt)) for h, _, pt in (x.partition(":") for x in os.environ.get("CC_COMPANY_PEERS", "").split(",") if x)]
            transport = LanTransport(port, peers or None, os.environ.get("CC_COMPANY_BIND", ""))
        self.transport = transport
        self._announced: set = set()

    @property
    def running(self) -> bool:
        return self.transport.running

    def tick(self) -> List[ChatMessage]:
        """Start/stop to match the setting, ingest packets, answer sync requests. Returns newly received messages."""
        want = self.service.enabled()
        if want and not self.transport.running:
            if not self.transport.start():
                return []
            self._announced.clear()
        elif not want and self.transport.running:
            self.transport.stop()
            return []
        if not self.transport.running:
            return []
        for c in self.service.list():                       # ask for recent history once per company per start
            if c.id not in self._announced:
                self._announced.add(c.id)
                pkt = self.service.sync_request(c.id)
                if pkt:
                    self.transport.send(pkt, repeats=False)
        new: List[ChatMessage] = []
        for raw in self.transport.drain():
            inc = self.service.receive(raw)
            if inc is None:
                continue
            if inc.kind == "message" and inc.message is not None:
                new.append(inc.message)
            elif inc.kind == "sync_request":
                for pkt in self.service.sync_reply(inc.company_id, inc.since):
                    self.transport.send(pkt, repeats=False)
                    time.sleep(0.002)
        return new

    def send(self, company_id: str, text: str) -> ChatMessage:
        m, pkt = self.service.send(company_id, text)
        self.transport.send(pkt)
        return m

    def forget(self, company_id: str) -> None:
        self._announced.discard(company_id)

    def stop(self) -> None:
        self.transport.stop()
