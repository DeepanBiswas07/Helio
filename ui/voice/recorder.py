import os
import tempfile
import numpy as np
from scipy.io.wavfile import write

# Minimum RMS energy a recording must have to be considered real speech.
# Audio below this level is near-silence and will be discarded before Whisper
# sees it, preventing hallucinations like "Thank you." from silence/noise.
_MIN_RMS_THRESHOLD = 80

class SpeechRecorder:
    def __init__(self, sample_rate=16000):
        self.sample_rate = sample_rate
        self.frames = []
        self.preroll_buffer = []  # Keep rolling history of chunks before trigger
        self.is_recording = False

    def start_recording(self):
        # Start fresh after the wake word is detected to completely avoid wake word hallucinations (like "Hey, do you" or "And you")
        self.frames = []
        self.is_recording = True
        print("[STT] Recorder: Started recording speech buffer (fresh, starting after wake word detection)...")

    def add_frames(self, numpy_data):
        if numpy_data.ndim > 1:
            flat_data = numpy_data.flatten()
        else:
            flat_data = numpy_data
        
        # We always keep raw int16 data
        data_copy = flat_data.copy()

        if self.is_recording:
            self.frames.append(data_copy)
        else:
            # Keep the last 20 chunks (20 * 1280 samples = 25,600 samples ≈ 1.6 seconds)
            self.preroll_buffer.append(data_copy)
            if len(self.preroll_buffer) > 20:
                self.preroll_buffer.pop(0)

    def stop_recording(self) -> str:
        if not self.is_recording:
            return ""
        self.is_recording = False
        
        # Clear pre-roll buffer so next query starts fresh
        self.preroll_buffer = []

        if not self.frames:
            print("[STT] Recorder Warning: No frames collected.")
            return ""

        # Concatenate all collected frames
        full_audio = np.concatenate(self.frames, axis=0)

        # Noise gate: discard recordings that are pure silence/noise.
        # Whisper hallucinates phrases like "Thank you." from silence.
        rms = np.sqrt(np.mean(full_audio.astype(np.float32) ** 2))
        if rms < _MIN_RMS_THRESHOLD:
            print(f"[STT] Recorder: Audio too quiet (RMS={rms:.1f}) — discarding as silence.")
            return ""

        # Save to a temporary WAV file
        temp_file = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
        temp_path = temp_file.name
        temp_file.close()

        write(temp_path, self.sample_rate, full_audio)
        print(f"[STT] Recorder: Saved speech to temp file: {temp_path} ({len(full_audio)} samples, RMS={rms:.1f})")
        return temp_path
