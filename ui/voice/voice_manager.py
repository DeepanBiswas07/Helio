import os
from PyQt5.QtCore import QObject, pyqtSignal, QThread
from voice.recorder import SpeechRecorder
from voice.stt import transcribe_audio

class TranscribeThread(QThread):
    # Signal emitted with transcribed text
    finished = pyqtSignal(str)

    def __init__(self, audio_path):
        super().__init__()
        self.audio_path = audio_path

    def run(self):
        try:
            text = transcribe_audio(self.audio_path)
            self.finished.emit(text)
        except Exception as e:
            print(f"TranscribeThread Error: {e}")
            self.finished.emit("")
        finally:
            # Delete temporary WAV file to keep user space clean
            if self.audio_path and os.path.exists(self.audio_path):
                try:
                    os.remove(self.audio_path)
                except Exception:
                    pass


class VoiceManager(QObject):
    # Signal emitted when transcription is successfully completed
    transcribed = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.recorder = SpeechRecorder()
        self._thread = None

    def start_listening(self):
        self.abort_active_transcription()
        self.recorder.start_recording()

    def add_audio_data(self, data):
        self.recorder.add_frames(data)

    def stop_listening_and_transcribe(self):
        self.abort_active_transcription()
        audio_path = self.recorder.stop_recording()
        if not audio_path:
            self.transcribed.emit("")
            return

        print("[STT] Manager: Starting background transcription thread...")
        self._thread = TranscribeThread(audio_path)
        self._thread.finished.connect(self._on_transcription_finished)
        self._thread.start()

    def abort_active_transcription(self):
        """Safely ignore the active transcription thread if it is running."""
        if self._thread and self._thread.isRunning():
            try:
                self._thread.finished.disconnect(self._on_transcription_finished)
                print("[STT] Active transcription thread disconnected.")
            except TypeError:
                pass
            self._thread = None

    def _on_transcription_finished(self, text):
        print(f"[STT] Manager: Transcription completed! Result: '{text}'")
        self.transcribed.emit(text)

