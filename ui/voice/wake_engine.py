"""
wake_engine.py — what Helio does with each slice of microphone audio.

Plain logic, no Qt and no sound device, so it can be driven offline with
synthesised or recorded audio and tested. wakeword_listener.py feeds it live
80 ms chunks on a worker thread and turns its events into Qt signals.

Two ways to wake:

  1. The openWakeWord model. Fast and cheap, and good at "Hey Helio" said on
     its own (0.85-0.90 on test clips). But say the command in the same breath
     — "Hey Helio, open Chrome" — and it scores 0.00-0.03 and never fires.
     That was most of "the wake word works half the time".

  2. A speech-onset check. When someone starts talking after a pause, the
     first 1.6 s go through Whisper (already loaded for commands) and wake
     Helio if they start with its name. That catches the one-breath form, and
     the command is already in the recording, so none of it is lost. The clip
     is transcribed in memory and dropped; nothing is kept or logged.
"""
import re
from collections import deque

import numpy as np

SAMPLE_RATE = 16000
CHUNK = 1280                      # 80 ms — openWakeWord's frame

WAKE_THRESHOLD = 0.5
COOLDOWN_S = 3.0

ONSET_GAP_S = 0.32                # silence before speech that makes it a fresh start
ONSET_PRE_S = 0.30                # kept from before the onset
ONSET_CHECK_S = 1.6               # how much of the start Whisper hears
ONSET_SPEECH_PROB = 0.5

NO_SPEECH_TIMEOUT_S = 6.0         # woken, then nothing said: give up
RING_S = 6.0

_NAME = r"(?:helio|helios|hilio|hileo|heleo|hellio|healio|heelio|hilo|helo)"
_HAIL = r"(?:hey|hi|hay|okay|ok|oh)"
_WAKE_AT_START = re.compile(rf"^\W*(?:{_HAIL}\W+)?{_NAME}\b|^\W*{_HAIL}\W+hello\b", re.IGNORECASE)
_WAKE_PREFIX = re.compile(rf"^\W*(?:(?:{_HAIL}\W+)?{_NAME}|{_HAIL}\W+hello)\b[\s,.!?:;-]*",
                          re.IGNORECASE)


def starts_with_wake(text):
    """'Hey Helio, open Chrome' -> True. A bare 'hello' does not count."""
    return bool(_WAKE_AT_START.search(text or ""))


def strip_wake(text):
    """'Hey Helio, open Chrome.' -> 'open Chrome.'"""
    return _WAKE_PREFIX.sub("", text or "", count=1).strip()


class AutoGain:
    """
    Bounded automatic gain: instant attack, slow release.

    A fixed boost suits one mic at best: the old 8x clipped a headset, and
    1.5x left a laptop array across the room barely registering.
    """

    def __init__(self, target_peak=20000.0, max_gain=8.0):
        self.target = target_peak
        self.max_gain = max_gain
        self.reset()

    def reset(self):
        self.peak = 0.0
        self.gain = 1.0

    def __call__(self, chunk):
        x = chunk.astype(np.float32)
        now = float(np.max(np.abs(x))) if x.size else 0.0
        # Includes this chunk's own peak, so a sudden loud word never clips.
        self.peak = max(now, self.peak * 0.975)
        want = float(np.clip(self.target / max(self.peak, 1.0), 1.0, self.max_gain))
        # Down at once, up gently, so the level doesn't pump between words.
        self.gain = want if want < self.gain else self.gain + (want - self.gain) * 0.2
        return np.clip(x * self.gain, -32768, 32767).astype(np.int16)


class _SpeechFlags:
    """Whether a chunk holds speech, from Silero's 32 ms windows."""

    def __init__(self, speech_prob):
        self.speech_prob = speech_prob
        self.reset()

    def reset(self):
        self.buf = np.zeros(0, np.float32)

    def __call__(self, chunk):
        self.buf = np.concatenate([self.buf, chunk.astype(np.float32) / 32768.0])
        speech = False
        while len(self.buf) >= 512:
            window, self.buf = self.buf[:512], self.buf[512:]
            if self.speech_prob(window) >= ONSET_SPEECH_PROB:
                speech = True
        return speech


class WakeEngine:
    """
    Feed it chunks in order with the orb's current state; it returns events:

      ("audio", start_sample, chunk)        every chunk, after gain
      ("state", old, new)                   the orb changed state
      ("volume", 0..1)                      while listening
      ("speech_ended",)                     listening and the speaker stopped
                                            (or never started)
      ("wake", source, score, from_sample)  from_sample: where the recording
                                            should begin
    """

    def __init__(self, wake_model, listen_vad, speech_prob=None, check_name=None,
                 threshold=WAKE_THRESHOLD, gain=None):
        self.model = wake_model
        self.key = next(iter(wake_model.models))
        self.vad = listen_vad
        self.check_name = check_name
        self.flags = _SpeechFlags(speech_prob) if (speech_prob and check_name) else None
        self.threshold = threshold
        self.gain = gain or AutoGain()

        self.sample = 0
        self.ring = deque(maxlen=int(RING_S * SAMPLE_RATE / CHUNK))
        self.prev_state = None
        self.last_wake = -10 ** 12
        self.last_score = 0.0

        self._listen_samples = 0
        self._timed_out = False
        self._arm_on_listen = False
        self._onset = None
        self._quiet = 0
        self._max_rms = 500.0

    # ── helpers ──────────────────────────────────────────────────────────────

    def _cooled(self, at):
        return at - self.last_wake >= COOLDOWN_S * SAMPLE_RATE

    def audio_between(self, start, end):
        """Processed audio for [start, end) from the last few seconds."""
        parts = []
        for s, c in self.ring:
            e = s + len(c)
            if e <= start or s >= end:
                continue
            parts.append(c[max(0, start - s):len(c) - max(0, e - end)])
        return np.concatenate(parts) if parts else np.zeros(0, np.int16)

    def _wake(self, out, source, score, from_sample, at):
        self.last_wake = at
        self._onset = None
        out.append(("wake", source, float(score), int(max(0, from_sample))))

    # ── the stream ───────────────────────────────────────────────────────────

    def feed(self, raw, state):
        start = self.sample
        chunk = self.gain(np.asarray(raw, dtype=np.int16).reshape(-1))
        self.sample += len(chunk)
        self.ring.append((start, chunk))
        out = [("audio", start, chunk)]

        if state != self.prev_state:
            out.append(("state", self.prev_state, state))
            if self.prev_state == "listening":
                self.model.reset()          # clear the command out of its memory
            if state == "listening":
                self.vad.reset()
                if self._arm_on_listen:
                    # Woken mid-sentence: the command is already under way.
                    self.vad.arm()
                self._listen_samples = 0
                self._timed_out = False
            self._arm_on_listen = False
            self._onset = None
            self._quiet = 0
            if self.flags is not None:
                self.flags.reset()
            self.prev_state = state

        if state == "listening":
            rms = float(np.sqrt(np.mean(chunk.astype(np.float64) ** 2))) if chunk.size else 0.0
            self._max_rms = max(self._max_rms * 0.995, rms)
            out.append(("volume", min(1.0, rms / max(150.0, self._max_rms))))

            self._listen_samples += len(chunk)
            if self.vad.process_chunk(chunk):
                out.append(("speech_ended",))
            elif (not self._timed_out and not self.vad.speech_active
                  and self._listen_samples >= NO_SPEECH_TIMEOUT_S * SAMPLE_RATE):
                self._timed_out = True
                out.append(("speech_ended",))
            return out

        # ── 1. the wake word model ──────────────────────────────────────────
        score = float(self.model.predict(chunk)[self.key])
        self.last_score = score
        if score >= self.threshold and self._cooled(start):
            # Recording starts one frame back, just before the command.
            self._wake(out, "helio", score, start - CHUNK, start)
            return out

        # ── 2. the onset check (not while Helio itself is talking) ──────────
        if self.flags is None or state not in ("idle", "hover"):
            return out

        speech = self.flags(chunk)
        if self._onset is None:
            if speech and self._quiet >= ONSET_GAP_S * SAMPLE_RATE and self._cooled(start):
                self._onset = start
            self._quiet = 0 if speech else self._quiet + len(chunk)
            return out

        if start + len(chunk) - self._onset < ONSET_CHECK_S * SAMPLE_RATE:
            return out

        onset, self._onset = self._onset, None
        if not self._cooled(start):
            return out
        begin = onset - int(ONSET_PRE_S * SAMPLE_RATE)
        heard = self.check_name(self.audio_between(begin, onset + int(ONSET_CHECK_S * SAMPLE_RATE))) or ""
        if starts_with_wake(heard):
            self._arm_on_listen = bool(strip_wake(heard))
            self._wake(out, "helio (heard)", 1.0, begin, start)
        return out
