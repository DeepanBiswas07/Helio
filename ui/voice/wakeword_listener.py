"""
wakeword_listener.py — the microphone: which one, and what it hears.

The PortAudio callback only copies each chunk onto a queue. Gain, the wake
word model, Silero and the onset check all run on a worker thread (see
wake_engine.py). They used to run inside the callback, so any stall on Helio's
busy UI thread overran the audio buffer — "Audio Stream Status: input
overflow" — and the audio that got dropped was often the wake word itself.
"""
import os
import queue
import threading
import time

import numpy as np
import sounddevice as sd
from PyQt5.QtCore import QObject, QTimer, pyqtSignal
from openwakeword.model import Model

from voice import audio_devices
from voice.vad import SileroVAD
from voice.wake_engine import CHUNK, SAMPLE_RATE, WakeEngine


class WakeWordListener(QObject):
    # (wakeword_name, score)
    detected = pyqtSignal(str, float)
    # real-time audio level while listening, 0.0 to 1.0
    volume_updated = pyqtSignal(float)
    # (int16 mono chunk, absolute sample index of its first sample)
    audio_recorded = pyqtSignal(object, int)
    # Silero: the speaker stopped (or never started)
    vad_speech_ended = pyqtSignal()
    # a plain-English explanation when the microphone hears nothing
    mic_problem = pyqtSignal(str)
    _switch_device = pyqtSignal(int)

    SAMPLE_RATE = SAMPLE_RATE
    CHUNK_SIZE = CHUNK
    DEAD_AFTER_S = 15.0
    REPROBE_EVERY_S = 30.0

    def __init__(self, model_path, device_index=None, parent=None):
        super().__init__(parent)

        self.model = Model(wakeword_models=[model_path], inference_framework="onnx")
        self.vad = SileroVAD()
        self.vad.load()
        speech_prob, check_name = self._onset_check()
        self.engine = WakeEngine(self.model, self.vad,
                                 speech_prob=speech_prob, check_name=check_name)

        # Where the recording for the latest wake should start. Read (and
        # cleared) by the window when it switches to listening.
        self.wake_start_sample = None

        self.stream = None
        self._queue = queue.Queue(maxsize=400)          # ~32 s of audio
        self._worker = None
        self._running = False
        self._quiet_chunks = 0
        self._dead_warned = False
        self._probing = False
        self._last_probe = 0.0
        self._pending_warning = None
        self._overflows = 0
        self._dropped = 0
        self._last_score_print = 0.0

        self.current_devices_fingerprint = audio_devices.fingerprint()
        self._explicit_device = device_index is not None
        self.device_index = device_index if self._explicit_device else self._choose()

        self._switch_device.connect(self._on_switch_device)

        # Plugging in / removing a device.
        self.poll_timer = QTimer(self)
        self.poll_timer.timeout.connect(self._check_for_hardware_changes)
        self.poll_timer.start(3000)

        # A device that goes silent mid-session.
        self.health_timer = QTimer(self)
        self.health_timer.timeout.connect(self._check_health)
        self.health_timer.start(5000)

    # ── setup ────────────────────────────────────────────────────────────────

    @staticmethod
    def _onset_check():
        """Silero + Whisper for the one-breath 'Hey Helio, do X' form."""
        if os.environ.get("HELIO_WAKE_WHISPER", "1").strip().lower() in ("0", "false", "off", "no"):
            print("WakeWordListener: speech-onset wake check is off (HELIO_WAKE_WHISPER=0).")
            return None, None
        try:
            import torch
            from silero_vad import load_silero_vad
            from voice.stt import check_name

            onset_model = load_silero_vad()

            def speech_prob(window):
                with torch.no_grad():
                    return float(onset_model(torch.from_numpy(window), SAMPLE_RATE).item())

            return speech_prob, check_name
        except Exception as e:
            print(f"WakeWordListener: speech-onset wake check unavailable ({e}).")
            return None, None

    def _choose(self):
        choice = audio_devices.choose_microphone()
        if not choice.live and choice.warning:
            print(choice.warning)
            self._pending_warning = choice.warning
        return choice.index

    # ── audio ────────────────────────────────────────────────────────────────

    def _on_audio(self, indata, frames, time_info, status):
        # Real-time thread: copy and go. Anything slower loses audio.
        if status and status.input_overflow:
            self._overflows += 1
        try:
            self._queue.put_nowait(indata[:, 0].copy())
        except queue.Full:
            self._dropped += 1

    def _work(self):
        from orb.state import state as orb_state

        while self._running:
            try:
                raw = self._queue.get(timeout=0.25)
            except queue.Empty:
                continue

            peak = int(np.max(np.abs(raw.astype(np.int32)))) if raw.size else 0
            self._quiet_chunks = self._quiet_chunks + 1 if peak <= audio_devices.DEAD_PEAK else 0

            try:
                events = self.engine.feed(raw, orb_state.name)
            except Exception as e:
                print(f"WakeWordListener: audio processing error: {e}")
                continue
            for event in events:
                self._dispatch(event)

            score = self.engine.last_score
            if 0.25 <= score < self.engine.threshold and time.time() - self._last_score_print > 1.0:
                self._last_score_print = time.time()
                print(f"helio score: {score:.3f} (below {self.engine.threshold})")

    def _dispatch(self, event):
        kind = event[0]
        if kind == "audio":
            self.audio_recorded.emit(event[2], event[1])
        elif kind == "volume":
            self.volume_updated.emit(event[1])
        elif kind == "speech_ended":
            print("[VAD] End of speech detected.")
            self.vad_speech_ended.emit()
        elif kind == "state":
            print(f"[State Transition] WakeWordListener: {event[1]} -> {event[2]}")
        elif kind == "wake":
            _, source, score, start = event
            self.wake_start_sample = start
            print(f"\n[*] WAKEWORD DETECTED: {source} ({score:.2f}) [*]\n")
            self.detected.emit(source, score)

    # ── start / stop ─────────────────────────────────────────────────────────

    def start(self):
        if self._worker is None or not self._worker.is_alive():
            self._running = True
            self._worker = threading.Thread(target=self._work, name="helio-audio", daemon=True)
            self._worker.start()

        try:
            # latency="high" gives PortAudio a bigger buffer to ride out stalls.
            self.stream = sd.InputStream(device=self.device_index, samplerate=SAMPLE_RATE,
                                         channels=1, dtype="int16", blocksize=CHUNK,
                                         latency="high", callback=self._on_audio)
            self.stream.start()
            print(f"Wakeword listener stream started on Device #{self.device_index} "
                  f"({audio_devices.device_name(self.device_index)}).\n")
        except Exception as e:
            print(f"Error starting audio stream on Device #{self.device_index}: {e}")
            if self.device_index is not None:
                print("WakeWordListener: falling back to the system default input device...")
                self.device_index = None
                self.start()
                return

        if self._pending_warning:
            self.mic_problem.emit(self._pending_warning)
            self._pending_warning = None

    def _stop_stream(self):
        if self.stream:
            try:
                self.stream.stop()
                self.stream.close()
            except Exception as e:
                print(f"Error stopping stream: {e}")
            self.stream = None
            print("Wakeword listener stream stopped.")

    def stop(self):
        self._stop_stream()
        self._running = False
        if self._worker is not None:
            self._worker.join(timeout=1.0)
            self._worker = None

    # ── devices ──────────────────────────────────────────────────────────────

    def _check_for_hardware_changes(self):
        new_fingerprint = audio_devices.fingerprint()
        if new_fingerprint == self.current_devices_fingerprint:
            return
        print("\nWakeWordListener: Audio hardware change detected! Re-routing microphone...")
        self.current_devices_fingerprint = new_fingerprint
        self._stop_stream()
        try:
            sd._terminate()
            sd._initialize()
        except Exception:
            pass
        if not self._explicit_device:
            self.device_index = self._choose()
        self._quiet_chunks = 0
        self._dead_warned = False
        self.start()

    def _check_health(self):
        if self.stream is None:
            return
        quiet_s = self._quiet_chunks * CHUNK / SAMPLE_RATE
        if quiet_s < 1.0:
            self._dead_warned = False
        if quiet_s < self.DEAD_AFTER_S:
            return

        if not self._dead_warned:
            self._dead_warned = True
            message = audio_devices.silence_warning(self.device_index)
            print(message)
            self.mic_problem.emit(message)

        if (self._probing or self._explicit_device
                or time.time() - self._last_probe < self.REPROBE_EVERY_S):
            return
        self._probing = True
        self._last_probe = time.time()
        threading.Thread(target=self._reprobe, name="helio-mic-probe", daemon=True).start()

    def _reprobe(self):
        try:
            live = audio_devices.find_live_microphone(exclude=self.device_index)
            if live is not None:
                self._switch_device.emit(live)
        finally:
            self._probing = False

    def _on_switch_device(self, index):
        print(f"WakeWordListener: #{self.device_index} is silent; switching to live microphone "
              f"#{index} ({audio_devices.device_name(index)}).")
        self._stop_stream()
        self.device_index = index
        self._quiet_chunks = 0
        self._dead_warned = False
        self.start()
