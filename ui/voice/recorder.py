"""
recorder.py — holds the audio of what was said after the wake word.

Recording used to begin when the UI thread got round to it after the wake
word, and everything before that was thrown away — so the first words of a
command said without a pause were lost. Chunks now carry their position in the
stream, the last few seconds are always kept, and a recording can begin from
the exact sample the wake detector points at.

It used to drop anything under a fixed loudness (RMS 80), which also dropped
real speech from a quiet laptop mic. Whether a recording holds speech is now
decided by Silero in stt.py.
"""
from collections import deque

import numpy as np


class SpeechRecorder:
    PREROLL_S = 6.0

    def __init__(self, sample_rate=16000):
        self.sample_rate = sample_rate
        self.frames = []
        self.preroll = deque()          # (start_sample, chunk)
        self._preroll_samples = 0
        self.is_recording = False
        self.latest = 0                 # sample index just past the newest chunk

    def add_frames(self, data, start=None):
        chunk = np.asarray(data).reshape(-1).astype(np.int16, copy=True)
        if start is None:
            start = self.latest
        self.latest = start + len(chunk)

        if self.is_recording:
            self.frames.append(chunk)
            return

        self.preroll.append((start, chunk))
        self._preroll_samples += len(chunk)
        while self._preroll_samples > self.PREROLL_S * self.sample_rate and len(self.preroll) > 1:
            _, old = self.preroll.popleft()
            self._preroll_samples -= len(old)

    def start_recording(self, from_sample=None):
        """Begin a recording; from_sample reaches back into the last few seconds."""
        self.frames = []
        if from_sample is not None:
            for start, chunk in self.preroll:
                if start + len(chunk) <= from_sample:
                    continue
                self.frames.append(chunk[max(0, from_sample - start):])
        self.preroll.clear()
        self._preroll_samples = 0
        self.is_recording = True
        back = sum(len(c) for c in self.frames) / self.sample_rate
        print(f"[STT] Recorder: recording" + (f" (from {back:.2f}s back, at the wake word)" if back else "") + "...")

    def stop_recording(self):
        """The recorded int16 audio, or None."""
        if not self.is_recording:
            return None
        self.is_recording = False
        frames, self.frames = self.frames, []
        if not frames:
            print("[STT] Recorder Warning: No frames collected.")
            return None
        audio = np.concatenate(frames)
        print(f"[STT] Recorder: {len(audio) / self.sample_rate:.2f}s recorded.")
        return audio
