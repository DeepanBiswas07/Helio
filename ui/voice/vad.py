"""
vad.py — Silero VAD wrapper for real-time end-of-speech detection.

Processes audio in 512-sample windows at 16kHz, buffers incoming chunks,
and returns True the moment speech has ended (silence threshold crossed
after a minimum amount of detected speech).
"""
import numpy as np
import torch

# ── Tunable constants ────────────────────────────────────────────────────────
SAMPLE_RATE        = 16000
VAD_CHUNK          = 512    # samples — required by Silero at 16kHz
SPEECH_THRESHOLD   = 0.45   # probability to count as speech
# How many consecutive silent 512-sample chunks (≈ 32ms each) before we
# declare end-of-speech. 25 chunks ≈ 800ms — feels natural, not sluggish.
SILENCE_CHUNKS_END = 25
# Minimum speech chunks before we allow end-of-speech to fire.
# Prevents a single noise pop from triggering a transcription.
MIN_SPEECH_CHUNKS  = 6      # ≈ 192ms minimum speech
# ────────────────────────────────────────────────────────────────────────────


class SileroVAD:
    """
    Stateful, real-time Silero VAD that accumulates audio chunks and
    fires end-of-speech when silence follows detected speech.
    """

    def __init__(self):
        self._model   = None
        self._reset_state()

    def load(self):
        """Load the Silero VAD model. Call once at startup."""
        if self._model is not None:
            return
        try:
            from silero_vad import load_silero_vad
            self._model = load_silero_vad()
            self._model.eval()
            print("[VAD] Silero VAD model loaded successfully.")
        except Exception as e:
            print(f"[VAD] Failed to load Silero VAD: {e}")

    def reset(self):
        """Reset internal state — call when entering listening mode."""
        self._reset_state()

    @property
    def speech_active(self):
        """True once speech has been heard since the last reset."""
        return self._speech_active

    def arm(self):
        """
        Start as if speech were already under way.

        For a command said in the same breath as the wake word: it began
        before listening did, so a pause now means it has ended, rather than
        that it has yet to start.
        """
        self._speech_active = True
        self._speech_count = MIN_SPEECH_CHUNKS
        self._silence_count = 0

    def _reset_state(self):
        self._buffer        = np.array([], dtype=np.int16)
        self._speech_active = False   # True once first speech chunk detected
        self._speech_count  = 0       # consecutive / total speech chunks seen
        self._silence_count = 0       # consecutive silence chunks after speech

    def process_chunk(self, audio_int16: np.ndarray) -> bool:
        """
        Feed a raw int16 mono audio chunk.
        Returns True exactly once when end-of-speech is detected.
        """
        # Fall back to energy-based VAD if Silero model failed to load
        use_fallback = (self._model is None)

        # Accumulate incoming audio into the internal buffer
        self._buffer = np.concatenate([self._buffer, audio_int16.flatten()])

        end_of_speech = False

        # Drain the buffer in 512-sample windows
        while len(self._buffer) >= VAD_CHUNK:
            chunk = self._buffer[:VAD_CHUNK]
            self._buffer = self._buffer[VAD_CHUNK:]

            # Convert int16 → float32 normalised to [-1, 1]
            audio_f32 = chunk.astype(np.float32) / 32768.0

            if use_fallback:
                # Energy-based VAD fallback: calculate RMS of the chunk
                rms = np.sqrt(np.mean(audio_f32**2))
                # 0.0035 RMS threshold (roughly equivalent to 115 in int16 amplitude)
                is_speech = (rms > 0.0035)
            else:
                tensor = torch.from_numpy(audio_f32)
                try:
                    prob = self._model(tensor, SAMPLE_RATE).item()
                    is_speech = (prob >= SPEECH_THRESHOLD)
                except Exception:
                    rms = np.sqrt(np.mean(audio_f32**2))
                    is_speech = (rms > 0.0035)

            if is_speech:
                self._speech_active = True
                self._speech_count += 1
                self._silence_count = 0
            else:
                if self._speech_active:
                    self._silence_count += 1
                    # Give slightly more silence tolerance for the energy fallback VAD (35 chunks ≈ 1.1s)
                    silence_limit = SILENCE_CHUNKS_END + 10 if use_fallback else SILENCE_CHUNKS_END
                    if (self._silence_count >= silence_limit
                            and self._speech_count >= MIN_SPEECH_CHUNKS):
                        end_of_speech = True
                        self._reset_state()
                        break   # Signal fired — stop processing this batch

        return end_of_speech
