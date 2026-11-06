"""Company: small, end-to-end encrypted group chat for people on the same local network.

What this is
    A *company* is a shared secret (the join code).  Everyone who has the code can read and write in it.  There are no
    accounts and no server.  Every message is encrypted with AES-256-GCM under a key derived from the code, so other
    people on the same Wi-Fi who do not have the code see only random bytes.

What is shared
    Only the display name you chose and the message text (plus a timestamp and a random message id).  Never tasks, notes,
    journal, calendar, settings, e-mail, device name or file names.  (A network peer necessarily sees the sender's IP address
    at the network level; the app never shows or stores it.)

Trust model, stated plainly
    Anyone holding the code can post under any display name; there is no per-person identity.  Treat the code like a
    shared password.  To remove someone, create a new company and share the new code.

This module holds the crypto, the rules and the storage.  It does no networking; see ``company_net.py``.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import re
import secrets
import uuid
from dataclasses import dataclass
from typing import List, Optional, Tuple

from .base import AppError

MAGIC = b"CC1"
ALPHABET = "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567"
CODE_LEN = 20
MAX_BODY_BYTES = 1000            # keeps a whole packet inside one Ethernet frame (no fragmentation)
MAX_NAME_BYTES = 48
KEEP_MESSAGES = 2000
SYNC_BATCH = 50


def crypto_available() -> bool:
    try:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM  # noqa: F401
        return True
    except Exception:
        return False


def new_code() -> str:
    raw = "".join(secrets.choice(ALPHABET) for _ in range(CODE_LEN))
    return "-".join(raw[i:i + 5] for i in range(0, CODE_LEN, 5))


def normalize_code(code: str) -> str:
    c = re.sub(r"[\s-]+", "", str(code or "")).upper()
    if len(c) != CODE_LEN or any(ch not in ALPHABET for ch in c):
        raise AppError("That join code is not valid. It has 20 letters and digits, like ABCDE-FGHIJ-KLMNO-PQRST.")
    return c


def derive(code_norm: str) -> Tuple[str, bytes]:
    """-> (company id, 32-byte key). The id is public (sent in the clear to find the right key); it reveals nothing
    usable about the secret because both values are one-way HMAC outputs of a 100-bit random secret."""
    cid = hmac.new(b"controlled-chaos/company/id/v1", code_norm.encode(), hashlib.sha256).digest()[:8].hex()
    key = hmac.new(b"controlled-chaos/company/key/v1", code_norm.encode(), hashlib.sha256).digest()
    return cid, key


def clean_name(name: str) -> str:
    n = re.sub(r"[\x00-\x1f\x7f]+", " ", str(name or "")).strip()
    n = re.sub(r"\s+", " ", n)
    while len(n.encode("utf-8")) > MAX_NAME_BYTES:
        n = n[:-1]
    return n


def seal(cid: str, key: bytes, obj: dict) -> bytes:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    nonce = secrets.token_bytes(12)
    head = MAGIC + bytes.fromhex(cid)
    ct = AESGCM(key).encrypt(nonce, json.dumps(obj, separators=(",", ":"), ensure_ascii=False).encode("utf-8"), head)
    return head + nonce + ct


def peek_company_id(packet: bytes) -> Optional[str]:
    if len(packet) < 3 + 8 + 12 + 16 or packet[:3] != MAGIC:
        return None
    return packet[3:11].hex()


def open_packet(key: bytes, packet: bytes) -> Optional[dict]:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    try:
        pt = AESGCM(key).decrypt(packet[11:23], packet[23:], packet[:11])
        obj = json.loads(pt.decode("utf-8"))
        return obj if isinstance(obj, dict) else None
    except Exception:                       # wrong key, damaged or forged packet: ignore silently
        return None


@dataclass
class Company:
    id: str
    name: str
    secret: str
    created_at: int

    @property
    def code(self) -> str:
        return "-".join(self.secret[i:i + 5] for i in range(0, CODE_LEN, 5))


@dataclass
class ChatMessage:
    id: str
    company_id: str
    sender: str
    body: str
    ts: int
    mine: bool


@dataclass
class Incoming:
    kind: str                               # "message" | "sync_request"
    company_id: str
    message: Optional[ChatMessage] = None
    since: int = 0


class CompanyService:
    def __init__(self, ctx, db, platform: str = ""):
        self.ctx, self.db = ctx, db
        self.platform = platform
        self._keys: dict = {}
        self._last_sync_reply: dict = {}

    # ------------------------------------------------------------ availability / settings
    def supported(self) -> Tuple[bool, str]:
        if self.platform == "android":
            return False, "Company chat needs network access, which the Android build deliberately does not request."
        if not crypto_available():
            return False, "The encryption package (cryptography) is not installed, so Company chat is switched off."
        return True, ""

    def enabled(self) -> bool:
        return self.supported()[0] and self.ctx.setting("company_on") == "1"

    def set_enabled(self, on: bool) -> None:
        if on and not self.supported()[0]:
            raise AppError(self.supported()[1])
        if on and not self.display_name():
            raise AppError("Choose a display name first.")
        self._put("company_on", "1" if on else "0")

    def display_name(self) -> str:
        return clean_name(self.ctx.setting("company_name") or "")

    def set_display_name(self, name: str) -> str:
        n = clean_name(name)
        if not n:
            raise AppError("A display name is required. It is the only thing about you that others will see.")
        self._put("company_name", n)
        return n

    def _one(self, sql: str, params=()):
        rows = self.db.query(sql, params)
        return rows[0] if rows else None

    def _put(self, key: str, value: str) -> None:
        self.db.execute("INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                        (key, value))

    # ------------------------------------------------------------ companies
    def _row(self, r) -> Company:
        return Company(r["id"], r["name"], r["secret"], r["created_at"])

    def list(self) -> List[Company]:
        return [self._row(r) for r in self.db.query("SELECT * FROM company ORDER BY created_at, name")]

    def get(self, cid: str) -> Optional[Company]:
        r = self._one("SELECT * FROM company WHERE id=?", (cid,))
        return self._row(r) if r else None

    def _add(self, name: str, secret: str) -> Company:
        cid, _ = derive(secret)
        if self.get(cid):
            raise AppError("You are already in that company.")
        n = clean_name(name) or f"Company {secret[:4]}"
        self.db.execute("INSERT INTO company(id,name,secret,created_at) VALUES(?,?,?,?)", (cid, n, secret, self.ctx.now()))
        return self.get(cid)

    def create(self, name: str) -> Company:
        if not clean_name(name):
            raise AppError("Give the company a name.")
        return self._add(name, "".join(c for c in new_code() if c != "-"))

    def join(self, code: str, name: str = "") -> Company:
        return self._add(name, normalize_code(code))

    def rename(self, cid: str, name: str) -> None:
        if not clean_name(name):
            raise AppError("Give the company a name.")
        self.db.execute("UPDATE company SET name=? WHERE id=?", (clean_name(name), cid))

    def leave(self, cid: str) -> None:
        """Forgets the code and deletes this device's copy of the messages. Others keep theirs."""
        self.db.execute("DELETE FROM company_message WHERE company_id=?", (cid,))
        self.db.execute("DELETE FROM company WHERE id=?", (cid,))
        self._keys.pop(cid, None)

    def _key(self, c: Company) -> bytes:
        if c.id not in self._keys:
            self._keys[c.id] = derive(c.secret)[1]
        return self._keys[c.id]

    # ------------------------------------------------------------ messages
    def messages(self, cid: str, limit: int = 200) -> List[ChatMessage]:
        rows = self.db.query("SELECT * FROM (SELECT * FROM company_message WHERE company_id=? ORDER BY ts DESC, id DESC LIMIT ?) "
                             "ORDER BY ts, id", (cid, limit))
        return [ChatMessage(r["id"], r["company_id"], r["sender"], r["body"], r["ts"], bool(r["mine"])) for r in rows]

    def last_ts(self, cid: str) -> int:
        r = self._one("SELECT COALESCE(MAX(ts),0) AS t FROM company_message WHERE company_id=?", (cid,))
        return int(r["t"])

    def _store(self, m: ChatMessage) -> bool:
        with self.db.transaction() as c:
            cur = c.execute("INSERT OR IGNORE INTO company_message(id,company_id,sender,body,ts,mine,created_at) VALUES(?,?,?,?,?,?,?)",
                            (m.id, m.company_id, m.sender, m.body, m.ts, 1 if m.mine else 0, self.ctx.now()))
            inserted = cur.rowcount == 1
            if inserted:
                c.execute("DELETE FROM company_message WHERE company_id=? AND id NOT IN "
                          "(SELECT id FROM company_message WHERE company_id=? ORDER BY ts DESC, id DESC LIMIT ?)",
                          (m.company_id, m.company_id, KEEP_MESSAGES))
        return inserted

    def send(self, cid: str, text: str) -> Tuple[ChatMessage, bytes]:
        c = self.get(cid)
        if c is None:
            raise AppError("That company no longer exists on this device.")
        body = str(text or "").replace("\r\n", "\n").strip()
        if not body:
            raise AppError("Type a message first.")
        if len(body.encode("utf-8")) > MAX_BODY_BYTES:
            raise AppError("That message is too long. Keep it under about 1000 bytes (roughly 330 characters in Hindi, 1000 in English).")
        name = self.display_name()
        if not name:
            raise AppError("Choose a display name first.")
        # ts is in milliseconds and strictly increases per company on this device, so messages sent within the
        # same second keep their order everywhere
        m = ChatMessage(uuid.uuid4().hex, cid, name, body, max(self.ctx.now() * 1000, self.last_ts(cid) + 1), True)
        self._store(m)
        pkt = seal(cid, self._key(c), {"t": "m", "id": m.id, "n": m.sender, "b": m.body, "ts": m.ts})
        return m, pkt

    def sync_request(self, cid: str) -> Optional[bytes]:
        c = self.get(cid)
        return seal(cid, self._key(c), {"t": "s", "since": self.last_ts(cid)}) if c else None

    def sync_reply(self, cid: str, since: int, now: Optional[float] = None) -> List[bytes]:
        """Answer a peer who joined late. Rate-limited so a noisy peer cannot make this device flood the network."""
        import time
        t = time.monotonic() if now is None else now
        if t - self._last_sync_reply.get(cid, -1e9) < 15:
            return []
        self._last_sync_reply[cid] = t
        c = self.get(cid)
        if not c:
            return []
        rows = self.db.query("SELECT * FROM company_message WHERE company_id=? AND ts>? ORDER BY ts, id LIMIT ?",
                             (cid, int(since), SYNC_BATCH))
        return [seal(cid, self._key(c), {"t": "m", "id": r["id"], "n": r["sender"], "b": r["body"], "ts": r["ts"]}) for r in rows]

    def receive(self, packet: bytes) -> Optional[Incoming]:
        cid = peek_company_id(packet)
        if cid is None:
            return None
        c = self.get(cid)
        if c is None:
            return None                                     # not our company: ignore without even trying to decrypt
        obj = open_packet(self._key(c), packet)
        if obj is None:
            return None
        if obj.get("t") == "s":
            since = obj.get("since")
            return Incoming("sync_request", cid, since=int(since) if isinstance(since, int) and since >= 0 else 0)
        if obj.get("t") != "m":
            return None
        mid, name, body, ts = obj.get("id"), obj.get("n"), obj.get("b"), obj.get("ts")
        if not (isinstance(mid, str) and re.fullmatch(r"[0-9a-f]{32}", mid) and isinstance(name, str) and isinstance(body, str)
                and isinstance(ts, int) and 0 < ts < 4_102_444_800_000):
            return None
        name = clean_name(name) or "Someone"
        if not body.strip() or len(body.encode("utf-8")) > MAX_BODY_BYTES:
            return None
        m = ChatMessage(mid, cid, name, body, ts, False)
        return Incoming("message", cid, message=m) if self._store(m) else None
