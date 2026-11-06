"""Generates assets/paper_texture.png: a tiny, seamless, low-contrast paper-grain tile (pure stdlib)."""
import random, struct, zlib
from pathlib import Path

W = H = 96
rnd = random.Random(7)
rows = []
for y in range(H):
    row = bytearray([0])
    for x in range(W):
        v = 128 + int(rnd.gauss(0, 16))
        row += bytes((max(0, min(255, v)),) * 3 + (255,))
    rows.append(bytes(row))
raw = b"".join(rows)
def chunk(t, d):
    c = struct.pack(">I", len(d)) + t + d
    return c + struct.pack(">I", zlib.crc32(t + d) & 0xFFFFFFFF)
png = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", W, H, 8, 6, 0, 0, 0)) + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b"")
Path(__file__).resolve().parents[1].joinpath("src/assets/paper_texture.png").write_bytes(png)
print("wrote", len(png), "bytes")
