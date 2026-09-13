from PyQt5.QtCore import QObject, pyqtSignal, QThread
from voice.recorder import SpeechRecorder
from voice.stt import load_speech_gate, transcribe_array


class TranscribeThread(QThread):
    # Transcribed text. Not called "finished": that would shadow QThread's own.
    transcribed = pyqtSignal(str)

    def __init__(self, audio, after_wake=False):
        super().__init__()
        self.audio = audio
        self.after_wake = after_wake

    def run(self):
        try:
            text = transcribe_array(self.audio, after_wake=self.after_wake)
        except Exception as e:
            print(f"TranscribeThread Error: {e}")
            text = ""
        self.transcribed.emit(text)


class VoiceManager(QObject):
    # Signal emitted when transcription is successfully completed
    transcribed = pyqtSignal(str)

    MIN_SAMPLES = 4000          # 0.25 s

    def __init__(self, parent=None):
        super().__init__(parent)
        self.recorder = SpeechRecorder()
        self._thread = None
        # Threads that were abandoned mid-transcription. Qt aborts the process
        # if a QThread object is destroyed while still running, so they are
        # kept alive here until they end.
        self._abandoned = []
        self._after_wake = False
        load_speech_gate()

    def start_listening(self, from_sample=None):
        self.abort_active_transcription()
        self._after_wake = from_sample is not None
        self.recorder.start_recording(from_sample)

    def add_audio_data(self, data, start=None):
        self.recorder.add_frames(data, start)

    def stop_listening_and_transcribe(self):
        self.abort_active_transcription()
        audio = self.recorder.stop_recording()
        if audio is None or len(audio) < self.MIN_SAMPLES:
            self.transcribed.emit("")
            return

        print("[STT] Manager: Starting background transcription thread...")
        self._thread = TranscribeThread(audio, after_wake=self._after_wake)
        self._thread.transcribed.connect(self._on_transcription_finished)
        self._thread.start()

    def abort_active_transcription(self):
        """Ignore the running transcription's result, if there is one."""
        thread = self._thread
        self._thread = None
        if thread is None or not thread.isRunning():
            return
        try:
            thread.transcribed.disconnect(self._on_transcription_finished)
            print("[STT] Active transcription thread disconnected.")
        except TypeError:
            pass
        self._abandoned.append(thread)
        thread.finished.connect(lambda t=thread: self._abandoned.remove(t) if t in self._abandoned else None)

    def _on_transcription_finished(self, text):
        print(f"[STT] Manager: Transcription completed! Result: '{text}'")
        self.transcribed.emit(text)
