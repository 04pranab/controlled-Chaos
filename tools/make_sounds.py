"""Generate the four bundled notification sounds (original synthesis, no samples, no third-party audio).

    python tools/make_sounds.py

Mono, 22.05 kHz, 16-bit PCM WAV.  Peak levels are deliberately modest (soft 0.35, classic 0.40,
focus 0.38, alarm 0.60) - the alarm is distinct by pattern (repeated two-tone pulses) rather than by
being painfully loud.  Volume is always the device's own volume; the app never raises it.
"""
import math
import struct
import wave
from pathlib import Path

SR = 22050
OUT = Path(__file__).resolve().parents[1] / "assets" / "sounds"


def tone(freq, dur, amp, attack=0.008, decay=6.0, harmonics=((1, 1.0), (2, 0.25), (3, 0.08))):
    n = int(SR * dur)
    out = []
    for i in range(n):
        t = i / SR
        env = min(1.0, t / attack) * math.exp(-decay * t)
        s = sum(a * math.sin(2 * math.pi * freq * h * t) for h, a in harmonics)
        out.append(amp * env * s)
    return out


def mix(parts, total):
    buf = [0.0] * int(SR * total)
    for start, samples in parts:
        o = int(SR * start)
        for i, v in enumerate(samples):
            if o + i < len(buf):
                buf[o + i] += v
    return buf


def normalise(buf, peak):
    m = max(abs(v) for v in buf) or 1.0
    k = peak / m
    fade = int(SR * 0.02)
    out = [v * k for v in buf]
    for i in range(fade):                 # no clicks at the end
        out[-1 - i] *= i / fade
    return out


def save(name, buf):
    OUT.mkdir(parents=True, exist_ok=True)
    with wave.open(str(OUT / f"{name}.wav"), "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(SR)
        w.writeframes(b"".join(struct.pack("<h", int(max(-1, min(1, v)) * 32767)) for v in buf))


E5, A5, C5, G5, B5, D6, A4 = 659.25, 880.0, 523.25, 783.99, 987.77, 1174.66, 440.0
save("soft", normalise(mix([(0.0, tone(E5, 1.3, 1, decay=4.5)), (0.22, tone(A5, 1.3, 1, decay=4.5))], 1.5), 0.35))
save("classic", normalise(mix([(0.0, tone(B5, 0.7, 1, decay=7, harmonics=((1, 1), (2.76, 0.45), (5.4, 0.2)))),
                               (0.28, tone(G5, 0.9, 1, decay=6, harmonics=((1, 1), (2.76, 0.45), (5.4, 0.2))))], 1.3), 0.40))
save("focus", normalise(mix([(0.0, tone(C5, 1.2, 1, attack=0.03, decay=3.2)), (0.18, tone(E5 * 1.0, 1.1, 1, attack=0.03, decay=3.2)),
                             (0.36, tone(G5, 1.1, 1, attack=0.03, decay=3.2)), (0.54, tone(C5 * 2, 1.4, 1, attack=0.03, decay=2.6))], 2.0), 0.38))
pulses = []
for i in range(4):                         # two-tone pulses, 4 per cycle
    f = D6 if i % 2 == 0 else A5
    pulses.append((i * 0.42, tone(f, 0.30, 1, attack=0.004, decay=2.0, harmonics=((1, 1), (3, 0.3)))))
save("alarm", normalise(mix(pulses, 1.9), 0.60))
print(sorted(p.name for p in OUT.glob("*.wav")))
