"""Generate the bundled focus-music loops (original synthesis; no samples, no third-party audio).

    python tools/make_music.py

Mono, 16 kHz, 16-bit WAV, 24 s each, built to loop without a click:
tonal tracks use frequencies that are whole numbers of cycles per loop; noise tracks are cross-faded end to start.
Levels are modest; the volume the listener hears is their device volume times the in-app slider.
"""
import math
import random
import struct
import wave
from pathlib import Path

SR = 16000
DUR = 24
N = SR * DUR
OUT = Path(__file__).resolve().parents[1] / "assets" / "music"


def write(name, buf, peak=0.5):
    m = max(abs(v) for v in buf) or 1.0
    OUT.mkdir(parents=True, exist_ok=True)
    with wave.open(str(OUT / f"{name}.wav"), "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(SR)
        w.writeframes(b"".join(struct.pack("<h", int(max(-1, min(1, v / m * peak)) * 32767)) for v in buf))


def loopable(buf, fade=SR * 2):
    """Cross-fade the tail into the head so the loop point is inaudible."""
    out = buf[:-fade]
    for i in range(fade):
        a = i / fade
        out[i] = out[i] * a + buf[len(buf) - fade + i] * (1 - a)
    return out


def brown(seed, lp=0.02):
    r = random.Random(seed); x = 0.0; out = []
    for _ in range(N + SR * 2):
        x += (r.uniform(-1, 1) - x * 0.02) * lp * 6
        out.append(x)
    return out


def rain(seed=1):
    r = random.Random(seed)
    a = [r.uniform(-1, 1) for _ in range(N + SR * 2)]
    out, lp, lp2 = [], 0.0, 0.0
    for v in a:                       # band-limited white noise = steady rain bed
        lp += (v - lp) * 0.35
        lp2 += (lp - lp2) * 0.6
        out.append(lp - lp2 * 0.6)
    return loopable(out)


def pad():
    chord = [110.0, 164.81, 220.0, 329.63, 246.94]          # A2 E3 A3 E4 B3 (A sus-ish, calm)
    out = []
    for i in range(N):
        t = i / SR
        s = 0.0
        for j, f in enumerate(chord):
            f = round(f * DUR) / DUR                         # whole cycles per loop
            lfo = 0.6 + 0.4 * math.sin(2 * math.pi * (j + 1) / DUR * t + j)
            s += lfo * (math.sin(2 * math.pi * f * t) + 0.3 * math.sin(2 * math.pi * 2 * f * t)) / (1 + j * 0.3)
        out.append(s)
    return out


def keys():
    notes = [220.0, 261.63, 329.63, 392.0, 329.63, 261.63, 293.66, 392.0]   # A minor pentatonic-ish figure
    step = DUR / 16                                                           # 16 slow notes per loop
    out = [0.0] * (N + SR * 6)
    for k in range(16):
        f = notes[k % len(notes)]
        start = int(k * step * SR)
        for i in range(int(SR * 5)):
            t = i / SR
            env = min(1.0, t / 0.01) * math.exp(-1.1 * t)
            out[start + i] += env * (math.sin(2 * math.pi * f * t) + 0.35 * math.sin(2 * math.pi * 2 * f * t) + 0.1 * math.sin(2 * math.pi * 3 * f * t))
    for i in range(N, len(out)):      # wrap the decay tails round to the start
        out[i - N] += out[i]
    return out[:N]


if __name__ == "__main__":
    write("rain", rain(), 0.55)
    write("brown", loopable(brown(7)), 0.5)
    write("pad", pad(), 0.45)
    write("keys", keys(), 0.45)
    print("wrote", sorted(p.name for p in OUT.glob("*.wav")))
