"""Runtime guarantee that this process never talks to anything except the local machine.

Defence in depth: the app has no network code, but a dependency (or a future change) could.
``install()`` wraps ``socket`` so any attempt to resolve or connect to a non-loopback address
raises ``OfflineViolation`` and is logged.  Flet itself only needs loopback / Unix sockets to
talk to its own UI process, which stay allowed.

Scope: this guards the Python process.  It cannot see the Flutter client process; that is
covered by the bundled-assets audit and by testing the packaged app with networking disabled.
"""
from __future__ import annotations

import ipaddress
import logging
import socket
from typing import Any, List

log = logging.getLogger("controlled_chaos.offline")


class OfflineViolation(OSError):
    pass


_installed = False
_attempts: List[str] = []
_orig = {}


def _is_local(host: Any) -> bool:
    if host is None or host == "":
        return True
    if isinstance(host, bytes):
        host = host.decode("ascii", "ignore")
    if not isinstance(host, str):
        return True
    if host in ("localhost", "localhost.localdomain", "0.0.0.0", "::", "::1"):
        return True
    h = host.split("%")[0]
    try:
        ip = ipaddress.ip_address(h)
    except ValueError:
        return False                      # a real hostname: would need DNS -> not local
    return ip.is_loopback or ip.is_unspecified


def _deny(what: str, target: Any) -> None:
    msg = f"blocked outbound network access ({what}: {target!r})"
    _attempts.append(msg)
    log.error(msg)
    raise OfflineViolation(msg)


def install() -> None:
    global _installed
    if _installed:
        return
    _orig["getaddrinfo"] = socket.getaddrinfo
    _orig["connect"] = socket.socket.connect
    _orig["connect_ex"] = socket.socket.connect_ex
    _orig["create_connection"] = socket.create_connection
    _orig["sendto"] = socket.socket.sendto

    def getaddrinfo(host, *a, **k):
        if not _is_local(host):
            _deny("dns", host)
        return _orig["getaddrinfo"](host, *a, **k)

    def _addr_ok(sock, address) -> bool:
        if sock.family in (getattr(socket, "AF_UNIX", -1),):
            return True
        if isinstance(address, tuple) and address:
            return _is_local(address[0])
        return True

    def connect(self, address):
        if not _addr_ok(self, address):
            _deny("connect", address)
        return _orig["connect"](self, address)

    def connect_ex(self, address):
        if not _addr_ok(self, address):
            _deny("connect_ex", address)
        return _orig["connect_ex"](self, address)

    def create_connection(address, *a, **k):
        if not _is_local(address[0]):
            _deny("create_connection", address)
        return _orig["create_connection"](address, *a, **k)

    def sendto(self, data, *args):
        addr = args[-1] if args else None
        if addr is not None and not _addr_ok(self, addr):
            _deny("sendto", addr)
        return _orig["sendto"](self, data, *args)

    socket.getaddrinfo = getaddrinfo
    socket.socket.connect = connect
    socket.socket.connect_ex = connect_ex
    socket.create_connection = create_connection
    socket.socket.sendto = sendto
    _installed = True


def uninstall() -> None:           # used by tests only
    global _installed
    if not _installed:
        return
    socket.getaddrinfo = _orig["getaddrinfo"]
    socket.socket.connect = _orig["connect"]
    socket.socket.connect_ex = _orig["connect_ex"]
    socket.create_connection = _orig["create_connection"]
    socket.socket.sendto = _orig["sendto"]
    _installed = False


def is_installed() -> bool:
    return _installed


def blocked_attempts() -> List[str]:
    return list(_attempts)


def probe() -> str:
    """Try to reach the outside world; return a description if (and only if) both attempts were refused."""
    install()
    for what, fn in (("connection", lambda: socket.create_connection(("example.com", 80), timeout=1)),
                     ("DNS lookup", lambda: socket.getaddrinfo("example.org", 443))):
        try:
            fn()
        except OfflineViolation:
            continue
        raise AssertionError(f"an outbound {what} was NOT refused")
    return "outbound connections and DNS lookups are refused inside this app"
