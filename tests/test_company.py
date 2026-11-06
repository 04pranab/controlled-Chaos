"""Company (LAN chat): crypto, rules, storage, the two-device exchange over real UDP sockets, and the privacy limits."""
import json
import socket
import time

import pytest

from controlled_chaos import offline_guard
from controlled_chaos.database.connection import Database
from controlled_chaos.services import company_service as cs
from controlled_chaos.services.app import App
from controlled_chaos.services.base import AppError
from controlled_chaos.services.company_net import CompanyRuntime, LanTransport
from controlled_chaos.utilities.dates import FixedClock

from conftest import T0


def make_app(name, clock=None):
    a = App(Database(), clock or FixedClock(T0))
    a.settings.set("timezone", "UTC")
    a.company.set_display_name(name)
    return a


def free_port():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


# ------------------------------------------------------------------ codes and crypto
def test_code_format_and_normalisation():
    code = cs.new_code()
    assert len(code) == 23 and code.count("-") == 3
    assert cs.normalize_code(code.lower().replace("-", " ")) == code.replace("-", "")


@pytest.mark.parametrize("bad", ["", "ABC", "A" * 19, "A" * 21, "0" * 20, "ABCDE-FGHIJ-KLMNO-PQRS!"])
def test_bad_codes_rejected(bad):
    with pytest.raises(AppError):
        cs.normalize_code(bad)


def test_same_code_same_ids_different_code_different():
    a, b = cs.normalize_code(cs.new_code()), cs.normalize_code(cs.new_code())
    assert cs.derive(a) == cs.derive(a) and cs.derive(a)[0] != cs.derive(b)[0] and cs.derive(a)[1] != cs.derive(b)[1]


def test_packet_is_encrypted_and_tamper_proof():
    secret = cs.normalize_code(cs.new_code())
    cid, key = cs.derive(secret)
    pkt = cs.seal(cid, key, {"t": "m", "b": "very secret plan"})
    assert b"very secret plan" not in pkt and cs.peek_company_id(pkt) == cid
    assert cs.open_packet(key, pkt)["b"] == "very secret plan"
    assert cs.open_packet(cs.derive(cs.normalize_code(cs.new_code()))[1], pkt) is None          # wrong key
    bad = bytearray(pkt)
    bad[-1] ^= 1
    assert cs.open_packet(key, bytes(bad)) is None                                                # tampered
    swapped = bytearray(pkt)
    swapped[3] ^= 1
    assert cs.open_packet(key, bytes(swapped)) is None                                            # company id is authenticated
    assert cs.peek_company_id(b"junk") is None


# ------------------------------------------------------------------ service rules
def test_requires_display_name_and_support(app):
    with pytest.raises(AppError):
        app.company.set_enabled(True)
    with pytest.raises(AppError):
        app.company.set_display_name("   ")
    app.company.set_display_name("  Asha \n Rao ")
    assert app.company.display_name() == "Asha Rao"
    app.company.set_enabled(True)
    assert app.company.enabled()


def test_unsupported_on_android(app):
    app.company.platform = "android"
    app.company.set_display_name("x")
    with pytest.raises(AppError):
        app.company.set_enabled(True)
    assert app.company.enabled() is False


def test_off_by_default(app):
    assert app.company.enabled() is False and app.company.list() == []


def test_create_join_leave_and_duplicates():
    a = make_app("A")
    c = a.company.create("Study group")
    b = make_app("B")
    assert b.company.join(c.code).id == c.id
    with pytest.raises(AppError):
        b.company.join(c.code)
    m, _ = a.company.send(c.id, "hello")
    assert a.company.messages(c.id)[0].mine
    a.company.leave(c.id)
    assert a.company.list() == [] and a.db.query("SELECT * FROM company_message") == []


def test_message_limits_and_cleaning():
    a = make_app("A")
    c = a.company.create("g")
    with pytest.raises(AppError):
        a.company.send(c.id, "   ")
    with pytest.raises(AppError):
        a.company.send(c.id, "x" * 1001)
    _, pkt = a.company.send(c.id, "ok " + "ह" * 300)               # Hindi: 3 bytes per character, still fits
    assert len(pkt) < 1400


def test_receive_rejects_forged_fields_and_duplicates():
    a, b = make_app("A"), make_app("B")
    c = a.company.create("g")
    b.company.join(c.code)
    cid, key = cs.derive(c.secret)
    good = {"t": "m", "id": "a" * 32, "n": "A", "b": "hi", "ts": T0 * 1000}
    assert b.company.receive(cs.seal(cid, key, good)).message.body == "hi"
    assert b.company.receive(cs.seal(cid, key, good)) is None                                          # duplicate id
    for patch in ({"id": "short"}, {"b": ""}, {"ts": -1}, {"b": "x" * 1001, "id": "b" * 32}, {"n": 5, "id": "c" * 32}, {"t": "zzz"}):
        assert b.company.receive(cs.seal(cid, key, {**good, **patch})) is None
    assert b.company.receive(b"\x00" * 100) is None
    other = cs.derive(cs.normalize_code(cs.new_code()))
    assert b.company.receive(cs.seal(other[0], other[1], good)) is None                                # not our company


def test_history_is_bounded(monkeypatch):
    monkeypatch.setattr(cs, "KEEP_MESSAGES", 5)
    a = make_app("A")
    c = a.company.create("g")
    for i in range(12):
        a.company.send(c.id, f"m{i}")
    assert [m.body for m in a.company.messages(c.id)] == [f"m{i}" for i in range(7, 12)]


def test_sync_reply_is_rate_limited_and_since_filtered():
    a = make_app("A")
    c = a.company.create("g")
    for i in range(3):
        a.company.send(c.id, f"m{i}")
    assert len(a.company.sync_reply(c.id, 0, now=100.0)) == 3
    assert a.company.sync_reply(c.id, 0, now=101.0) == []
    assert len(a.company.sync_reply(c.id, 0, now=200.0)) == 3
    assert a.company.sync_reply(c.id, (T0 + 10) * 1000, now=500.0) == []


def test_chat_data_is_not_part_of_other_exports(app):
    """Company holds secrets: it must not leak into task/notes exports."""
    app.company.set_display_name("A")
    c = app.company.create("g")
    assert c.secret and "company" not in " ".join(__import__("controlled_chaos.utilities.export", fromlist=["x"]).__dict__.keys()).lower()


# ------------------------------------------------------------------ two devices over real UDP (loopback)
def pair():
    p1, p2 = free_port(), free_port()
    a, b = make_app("Asha"), make_app("Ben")
    ra = CompanyRuntime(a.company, LanTransport(p1, [("127.0.0.1", p2)], "127.0.0.1"))
    rb = CompanyRuntime(b.company, LanTransport(p2, [("127.0.0.1", p1)], "127.0.0.1"))
    return a, b, ra, rb


def wait(fn, secs=3.0):
    end = time.time() + secs
    while time.time() < end:
        v = fn()
        if v:
            return v
        time.sleep(0.05)
    return None


def test_two_devices_exchange_messages_end_to_end():
    a, b, ra, rb = pair()
    try:
        a.company.set_enabled(True)
        b.company.set_enabled(True)
        c = a.company.create("Team")
        b.company.join(c.code)
        ra.tick(); rb.tick()
        ra.send(c.id, "hello Ben")
        got = wait(lambda: rb.tick())
        assert got and got[0].body == "hello Ben" and got[0].sender == "Asha" and not got[0].mine
        rb.send(c.id, "hi Asha")
        got = wait(lambda: ra.tick())
        assert got and got[0].body == "hi Asha"
        time.sleep(2.2)                                      # the repeated copies arrive; they must not duplicate
        ra.tick(); rb.tick()
        assert [m.body for m in a.company.messages(c.id)] == ["hello Ben", "hi Asha"]
        assert [m.body for m in b.company.messages(c.id)] == ["hello Ben", "hi Asha"]
    finally:
        ra.stop(); rb.stop()


def test_late_joiner_gets_recent_history_and_outsider_gets_nothing():
    a, b, ra, rb = pair()
    try:
        a.company.set_enabled(True)
        c = a.company.create("Team")
        ra.tick()
        a.company.send(c.id, "before you joined")
        b.company.set_enabled(True)
        b.company.join(c.code)
        rb.tick()                                            # announces a sync request
        wait(lambda: ra.tick() or None, 1.0)                 # A answers
        got = wait(lambda: rb.tick())
        assert got and got[0].body == "before you joined"
        # a device that is not in the company stores nothing
        out = make_app("Eve")
        out.company.set_enabled(True)
        assert out.company.receive(cs.seal(*[c.id, cs.derive(c.secret)[1]], {"t": "m", "id": "e" * 32, "n": "x", "b": "y", "ts": T0 * 1000})) is None
        assert out.db.query("SELECT * FROM company_message") == []
    finally:
        ra.stop(); rb.stop()


def test_turning_off_stops_the_socket():
    a, _, ra, rb = pair()
    a.company.set_enabled(True)
    ra.tick()
    assert ra.running
    a.company.set_enabled(False)
    ra.tick()
    assert not ra.running
    rb.stop()


# ------------------------------------------------------------------ the offline guard still holds
@pytest.fixture
def guard():
    offline_guard.install()
    yield
    offline_guard.uninstall()


def test_guard_blocks_lan_udp_until_company_allows_it_and_never_the_internet(guard):
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    try:
        with pytest.raises(OSError):
            s.sendto(b"x", ("192.168.1.255", 9))                      # LAN blocked by default
        offline_guard.allow_lan(True)
        try:
            s.sendto(b"x", ("192.168.1.255", 9))                     # now allowed (may fail on the network, but not by the guard)
        except OfflineViolationLike as exc:                           # pragma: no cover
            raise
        except OSError as exc:
            assert not isinstance(exc, offline_guard.OfflineViolation)
        for public in ("8.8.8.8", "1.1.1.1", "142.250.1.1"):
            with pytest.raises(offline_guard.OfflineViolation):
                s.sendto(b"x", (public, 9))
        with pytest.raises(offline_guard.OfflineViolation):
            s.connect(("192.168.1.5", 80))                            # TCP connections stay blocked even on the LAN
        with pytest.raises(offline_guard.OfflineViolation):
            socket.getaddrinfo("example.com", 80)
    finally:
        offline_guard.allow_lan(False)
        s.close()


class OfflineViolationLike(Exception):
    pass


def test_lan_classifier():
    ok = ["192.168.0.7", "10.1.2.3", "172.16.5.5", "169.254.1.1", "255.255.255.255", "127.0.0.1"]
    bad = ["8.8.8.8", "1.1.1.1", "93.184.216.34", "example.com", "172.32.0.1", "100.64.0.1", None, 5]
    assert all(offline_guard._is_lan(h) for h in ok)
    assert not any(offline_guard._is_lan(h) for h in bad)
