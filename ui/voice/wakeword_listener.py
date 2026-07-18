from PyQt5.QtCore import QObject, pyqtSignal, QTimer
import sounddevice as sd
import numpy as np
from openwakeword.model import Model
from voice.vad import SileroVAD


class WakeWordListener(QObject):
    # Signal emitted when wakeword is detected (wakeword_name, score)
    detected = pyqtSignal(str, float)
    # Signal emitted to transmit real-time audio volume levels (0.0 to 1.0)
    volume_updated = pyqtSignal(float)
    # Signal emitted to transmit raw mono audio chunks for speech-to-text recording
    audio_recorded = pyqtSignal(np.ndarray)
    # Signal emitted by Silero VAD when end of speech is detected
    vad_speech_ended = pyqtSignal()

    def __init__(self, model_path, device_index=None, parent=None):
        super().__init__(parent)
        
        # Load your custom model
        self.model = Model(
            wakeword_models=[model_path],
            inference_framework="onnx"
        )

        self.SAMPLE_RATE = 16000
        self.CHUNK_SIZE = 1280
        self.THRESHOLD = 0.6  # Lowered to 0.6 to make triggering more responsive and easy
        self.stream = None
        self._prev_state = None  # Tracks state transitions
        self.max_rms = 500.0  # Dynamic auto-gain control baseline peak RMS
        
        # Debounce and cooldown to prevent sliding-window double triggers
        self.last_detection_time = 0.0
        self.cooldown_period = 4.0  # Ignore subsequent detections for 4 seconds
        
        # Keep track of active audio hardware fingerprint for dynamic hot-swapping
        self.current_devices_fingerprint = self._get_devices_fingerprint()
        
        # User defined device index or intelligent name-based ranking
        self.audio_gain = 1.0
        self.device_index = device_index
        if self.device_index is None:
            self.device_index = self._select_best_microphone()

        # Dynamic hardware monitoring timer (checks for new/removed devices every 3 seconds)
        self.poll_timer = QTimer(self)
        self.poll_timer.timeout.connect(self._check_for_hardware_changes)
        self.poll_timer.start(3000)

        # Silero VAD — loaded once at startup for instant end-of-speech detection
        self.vad = SileroVAD()
        self.vad.load()

    def _get_devices_fingerprint(self):
        """Creates a unique fingerprint of the current audio devices to detect plug/unplug events."""
        try:
            devices = sd.query_devices()
            return tuple((d['name'], d['max_input_channels']) for d in devices)
        except Exception:
            return ()

    def _select_best_microphone(self):
        """
        Intelligently ranks available recording devices, prioritizing headsets/earbuds (like Zenith)
        over built-in microphone arrays, and falling back to the OS default input device.
        """
        try:
            devices = sd.query_devices()
        except Exception as e:
            print(f"WakeWordListener: Error querying audio devices: {e}")
            self.audio_gain = 1.0
            return sd.default.device[0]

        inputs = []
        for idx, dev in enumerate(devices):
            if dev['max_input_channels'] > 0:
                inputs.append((idx, dev['name'], dev['name'].lower()))

        if not inputs:
            self.audio_gain = 1.0
            return sd.default.device[0]

        best_idx = None
        best_rank = -1

        for idx, orig_name, name in inputs:
            if "mapper" in name or "primary" in name or "stereo mix" in name or "driver" in name:
                rank = 0
            elif any(k in name for k in ["headset", "headphone", "hands-free", "handsfree", "wireless", "bluetooth", "zenith"]):
                rank = 3
            elif any(k in name for k in ["microphone", "mic", "array", "realtek", "intel", "smart sound"]):
                rank = 2
            else:
                rank = 1

            if rank > best_rank:
                best_rank = rank
                best_idx = idx
            elif rank == best_rank:
                # If same rank, prefer the OS default input device
                if idx == sd.default.device[0]:
                    best_idx = idx

        if best_idx is None:
            best_idx = sd.default.device[0]

        # Set audio gain based on the rank of the chosen device
        # Bluetooth/headsets (rank 3) get a substantial digital boost (e.g., 8.0x)
        # Built-in arrays (rank 2) get a mild boost (e.g., 1.5x)
        # Defaults (rank <= 1) get 1.0x
        try:
            chosen_name = devices[best_idx]['name'].lower()
            if any(k in chosen_name for k in ["headset", "headphone", "hands-free", "handsfree", "wireless", "bluetooth", "zenith"]):
                self.audio_gain = 8.0
            elif any(k in chosen_name for k in ["microphone", "mic", "array", "realtek", "intel", "smart sound"]):
                self.audio_gain = 1.5
            else:
                self.audio_gain = 1.0
        except Exception:
            self.audio_gain = 1.0

        try:
            dev_name = devices[best_idx]['name']
        except Exception:
            dev_name = "Unknown"

        print(f"WakeWordListener: Intelligently selected microphone: Device #{best_idx} ({dev_name}) with digital gain boost={self.audio_gain}x")
        return best_idx

    def _check_for_hardware_changes(self):
        """Checks if microphone devices have changed (e.g. plugging/unplugging headphones)."""
        new_fingerprint = self._get_devices_fingerprint()
        if new_fingerprint != self.current_devices_fingerprint:
            print("\nWakeWordListener: Audio hardware change detected! Re-routing microphone...")
            self.current_devices_fingerprint = new_fingerprint
            if self.stream:
                self.stop()
            try:
                sd._terminate()
                sd._initialize()
            except Exception:
                pass
            self.device_index = self._select_best_microphone()
            self.start()

    def process_audio(self, indata, frames, time_info, status):
        # Import the state singleton to read the active UI stage
        from orb.state import state as orb_state
        
        # Convert multi-channel input to mono by averaging all channels.
        # This prevents channel-locking on unused or noisy channels.
        if indata.ndim > 1 and indata.shape[1] > 1:
            mono_indata = np.mean(indata, axis=1, keepdims=True)
        else:
            mono_indata = indata.copy()
            
        # Apply digital gain boost if needed
        if self.audio_gain != 1.0:
            mono_indata = mono_indata.astype(np.float32) * self.audio_gain
            mono_indata = np.clip(mono_indata, -32768, 32767).astype(np.int16)
        else:
            mono_indata = mono_indata.astype(np.int16)
        
        # Always feed raw (boosted) data to the manager so the SpeechRecorder's 1.6s pre-roll is constantly populated
        self.audio_recorded.emit(mono_indata)

        # ─── Self-Cleaning State Transition Handler ───
        if orb_state.name != self._prev_state:
            resets = []
            # We ONLY reset the wake word model when we stop active listening (to clear speech data)
            if self._prev_state == "listening":
                self.model.reset()
                resets.append("model")
            # We ONLY reset the VAD when we start active listening (to start with a clean VAD state)
            if orb_state.name == "listening":
                self.vad.reset()
                resets.append("VAD")
            
            reset_str = " & ".join(resets) if resets else "none"
            print(f"[State Transition] WakeWordListener: State changed ({self._prev_state} -> {orb_state.name}), reset: {reset_str}.")
            self._prev_state = orb_state.name
        
        # ─── Case 1: Active Listening ───────────
        if orb_state.name == "listening":
            audio_data = mono_indata.flatten().astype(np.float64)
            rms = np.sqrt(np.mean(audio_data**2)) if len(audio_data) > 0 else 0.0
 
            self.max_rms = max(self.max_rms * 0.995, rms)
            normalized_vol = min(1.0, rms / max(150.0, self.max_rms))
            self.volume_updated.emit(normalized_vol)
 
            # Feed chunk to Silero VAD — fires vad_speech_ended when silence follows speech
            audio_int16 = mono_indata.flatten().astype(np.int16)
            if self.vad.process_chunk(audio_int16):
                print("[VAD] End of speech detected.")
                self.vad_speech_ended.emit()
            
            return
 
        # ─── Case 2: Wake Word Recognition (Idle, Hover, Speaking, Thinking) ───
        # Note: No VAD checks during thinking anymore! We ONLY wake up by Wake Word ("Hey Helio").
        if status:
            print(f"Audio Stream Status: {status}")
 
        audio = mono_indata.flatten().astype(np.int16)
        prediction = self.model.predict(audio)
 
        import time
        current_time = time.time()
 
        for wakeword, score in prediction.items():
            if score > 0.01:
                print(f"{wakeword} score: {score:.3f}")
 
            if score > self.THRESHOLD:
                if current_time - self.last_detection_time > self.cooldown_period:
                    self.last_detection_time = current_time
                    try:
                        print(f"\n[*] WAKEWORD DETECTED: {wakeword} ({score:.3f}) [*]\n")
                    except Exception:
                        pass
                    self.detected.emit(wakeword, score)

    def start(self):
        try:
            # Always request mono (1 channel). PortAudio/Windows drivers will handle
            # the conversion and automatic hardware beamforming/noise cancellation.
            channels = 1

            self.stream = sd.InputStream(
                device=self.device_index,
                samplerate=self.SAMPLE_RATE,
                channels=channels,
                dtype='int16',
                blocksize=self.CHUNK_SIZE,
                callback=self.process_audio
            )
            self.stream.start()
            print(f"Wakeword listener stream started successfully using Device #{self.device_index} with channels={channels}.\n")
        except Exception as e:
            print(f"Error starting audio stream on Device #{self.device_index}: {e}")
            if self.device_index is not None:
                print("WakeWordListener: Attempting safety fallback to system default input device...")
                self.device_index = None
                self.start()

    def stop(self):
        if self.stream:
            try:
                self.stream.stop()
                self.stream.close()
            except Exception as e:
                print(f"Error stopping stream: {e}")
            self.stream = None
            print("Wakeword listener stream stopped.")
