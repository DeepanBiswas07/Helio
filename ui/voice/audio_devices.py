"""
audio_devices.py — which microphone Helio listens on, and noticing when it hears nothing.

Windows lists one physical mic several times (MME, DirectSound, WASAPI,
WDM-KS) next to virtual ones. The old ranking scored every name containing
"microphone" the same and took the first, which on this laptop was the
virtual "Microphone (tranScreen Audio)": it only carries sound while its phone
app is streaming, so most of the time Helio was listening to nothing and the
real laptop mic was never opened.

Now virtual inputs rank last, and each candidate is listened to for half a
second before it is trusted. A device delivering pure digital silence — a peak
of 1 or 2 out of 32767, which no working mic produces even in a silent room —
is passed over for one that actually carries sound.
"""
import os
from dataclasses import dataclass

import numpy as np
import sounddevice as sd

SAMPLE_RATE = 16000
PROBE_S = 0.5
# Peak (out of 32767) at or below which a device is sending digital silence.
DEAD_PEAK = 4

VIRTUAL = ("transcreen", "virtual", "vb-audio", "cable output", "voicemeeter", "obs ",
           "droidcam", "iriun", "camo", "steam streaming", "stereo mix", "wave link",
           "loopback", "what u hear")
HEADSET = ("headset", "headphone", "hands-free", "handsfree", "wireless", "bluetooth",
           "airpods", "buds", "zenith")
BUILTIN = ("microphone array", "internal", "built-in", "smart sound", "realtek", "intel",
           "microphone", "mic")
SKIP = ("mapper", "primary sound")


@dataclass
class Candidate:
    index: int
    name: str
    rank: int
    peak: int = -1


@dataclass
class Choice:
    index: object
    name: str
    live: bool
    warning: str = ""


def fingerprint():
    """Changes when a device is plugged in or removed."""
    try:
        return tuple((d["name"], d["max_input_channels"]) for d in sd.query_devices())
    except Exception:
        return ()


def device_name(index):
    try:
        if index is None:
            index = sd.default.device[0]
        return sd.query_devices(index)["name"]
    except Exception:
        return "system default"


def rank(name):
    low = name.lower()
    if any(k in low for k in VIRTUAL):
        return 0
    if any(k in low for k in HEADSET):
        return 3
    if any(k in low for k in BUILTIN):
        return 2
    return 1


def candidates():
    """
    One entry per microphone, best first.

    MME entries only (when there are any): MME opens at 16 kHz mono and lets
    Windows resample, which is how Helio has always opened the mic; the other
    host APIs just repeat the same devices.
    """
    try:
        devices = sd.query_devices()
        apis = sd.query_hostapis()
    except Exception as e:
        print(f"Microphone: could not list audio devices: {e}")
        return []

    inputs = [(i, d, apis[d["hostapi"]]["name"]) for i, d in enumerate(devices)
              if d["max_input_channels"] > 0
              and not any(k in d["name"].lower() for k in SKIP)]
    if any(api == "MME" for _, _, api in inputs):
        inputs = [x for x in inputs if x[2] == "MME"]

    try:
        default = sd.default.device[0]
    except Exception:
        default = None
    found = [Candidate(i, d["name"], rank(d["name"])) for i, d, _ in inputs]
    return sorted(found, key=lambda c: (-c.rank, c.index != default, c.index))


def probe(index, seconds=PROBE_S):
    """
    Peak sample level over a short listen, or -1 if the device won't open.

    A stream of its own rather than sd.rec(): sd.rec shares sounddevice's one
    global stream with sd.play, so probing would cut off Helio mid-sentence.
    """
    frames = []
    try:
        with sd.InputStream(device=index, samplerate=SAMPLE_RATE, channels=1, dtype="int16",
                            blocksize=1024, callback=lambda i, f, t, s: frames.append(i.copy())):
            sd.sleep(int(seconds * 1000))
    except Exception:
        return -1
    if not frames:
        return 0
    return int(np.max(np.abs(np.concatenate(frames).astype(np.int32))))


def silence_warning(index, others=()):
    name = device_name(index)
    lines = [
        "",
        "=" * 70,
        f"[MIC] '{name}' is sending pure silence — Helio can't hear you.",
        "      That silence is coming from below Helio. Check:",
        "        • the mic-mute key on the keyboard (often F4 / F8 / F10, with a small LED)",
        "        • an audio app such as Nahimic or Intel's audio console muting the mic",
        "        • Settings > System > Sound > Input > your mic > 'Test your microphone'",
        "        • or plug in a headset — Helio switches to a working mic by itself.",
    ]
    if others:
        lines.append(f"      Also silent right now: {', '.join(others)}")
    lines += ["      To force a device, set HELIO_MIC=<name or index> in .env.", "=" * 70, ""]
    return "\n".join(lines)


def _match_override(value):
    try:
        index = int(value)
        return index if sd.query_devices(index)["max_input_channels"] > 0 else None
    except (ValueError, TypeError):
        pass
    except Exception:
        return None
    wanted = value.lower()
    matches = [c for c in candidates() if wanted in c.name.lower()]
    if not matches:
        try:
            matches = [Candidate(i, d["name"], 0) for i, d in enumerate(sd.query_devices())
                       if d["max_input_channels"] > 0 and wanted in d["name"].lower()]
        except Exception:
            matches = []
    return matches[0].index if matches else None


def choose_microphone():
    """The best microphone that is actually delivering sound."""
    override = os.environ.get("HELIO_MIC", "").strip()
    if override:
        index = _match_override(override)
        if index is not None:
            print(f"Microphone: HELIO_MIC={override!r} -> #{index} {device_name(index)}")
            return Choice(index, device_name(index), True)
        print(f"Microphone: HELIO_MIC={override!r} matched no input device; choosing automatically.")

    ranked = candidates()
    if not ranked:
        return Choice(None, "system default", True)

    chosen = None
    for c in ranked:
        c.peak = probe(c.index)
        state = "won't open" if c.peak < 0 else ("silent" if c.peak <= DEAD_PEAK else "live")
        print(f"Microphone: #{c.index:<3d} {c.name[:44]:44s} rank {c.rank}  peak {c.peak:>6d}  {state}")
        if c.peak > DEAD_PEAK:
            chosen = c
            break

    if chosen is not None:
        print(f"Microphone: listening on #{chosen.index} ({chosen.name})")
        return Choice(chosen.index, chosen.name, True)

    best = ranked[0]
    print(f"Microphone: nothing is delivering sound; listening on #{best.index} ({best.name}) anyway")
    return Choice(best.index, best.name, False,
                  silence_warning(best.index, [c.name for c in ranked[1:]]))


def find_live_microphone(exclude=None):
    """Index of another microphone that carries sound, or None."""
    for c in candidates():
        if c.index != exclude and probe(c.index) > DEAD_PEAK:
            return c.index
    return None
