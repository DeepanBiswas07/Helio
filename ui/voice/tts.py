"""
tts.py — Kokoro TTS wrapper for Helio voice output.

Uses Hexgrad's Kokoro-82M model with the authoritative "am_adam" voice style
(equivalent to ElevenLabs' premium Adam deep voice). Runs synthesis + playback
in a background QThread so the UI event loop is never blocked.
"""
import os
import re
import numpy as np
import sounddevice as sd
import torch
from PyQt5.QtCore import QObject, QThread, pyqtSignal

# ── DLL & Environment setup ──────────────────────────────────────────────────
import espeakng_loader
os.environ["ESPEAK_DATA_PATH"] = espeakng_loader.get_data_path()
os.environ["PHONEMIZER_ESPEAK_LIBRARY"] = espeakng_loader.get_library_path()
# ────────────────────────────────────────────────────────────────────────────

# ── Voice style path ────────────────────────────────────────────────────────
_ui_dir    = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_proj_root = os.path.dirname(_ui_dir)
VOICE_STYLE_PATH = os.path.join(_proj_root, "models", "tts", "am_adam.pt")
# ────────────────────────────────────────────────────────────────────────────


def _clean_text(text: str) -> str:
    """
    Strip markdown, emoji, and formatting noise so Kokoro receives
    plain, speakable prose — the same way Siri or Google Assistant would.
    """
    # Remove markdown bold/italic/code
    text = re.sub(r"\*{1,3}(.+?)\*{1,3}", r"\1", text)
    text = re.sub(r"_{1,2}(.+?)_{1,2}", r"\1", text)
    text = re.sub(r"`{1,3}[^`]*`{1,3}", "", text)
    # Remove markdown headers
    text = re.sub(r"^#{1,6}\s+", "", text, flags=re.MULTILINE)
    # Remove markdown links [text](url)
    text = re.sub(r"\[([^\]]+)\]\([^\)]+\)", r"\1", text)
    # Remove horizontal rules
    text = re.sub(r"^[-*_]{3,}\s*$", "", text, flags=re.MULTILINE)
    # Remove bullet points
    text = re.sub(r"^\s*[-*+]\s+", "", text, flags=re.MULTILINE)
    # Remove numbered lists formatting
    text = re.sub(r"^\s*\d+\.\s+", "", text, flags=re.MULTILINE)
    # Strip emoji and non-ASCII symbols
    text = re.sub(r"[^\x00-\x7F]+", " ", text)
    # Collapse excessive whitespace
    text = re.sub(r"\s{2,}", " ", text).strip()
    return text


class TTSThread(QThread):
    """Synthesizes text with Kokoro and plays it directly through sounddevice."""
    finished = pyqtSignal()

    def __init__(self, text: str, pipeline, voice_pack):
        super().__init__()
        self.text = text
        self.pipeline = pipeline
        self.voice_pack = voice_pack

    def run(self):
        try:
            chunks = []
            # Kokoro synthesizes segments (sentences/clauses) iteratively
            for _, _, audio in self.pipeline(self.text, voice=self.voice_pack, speed=0.95):
                chunks.append(audio)

            if not chunks:
                return

            audio = np.concatenate(chunks).reshape(-1, 1)  # Mono 24kHz float32
            sd.play(audio, samplerate=24000)
            sd.wait()
        except Exception as e:
            print(f"[TTS] Playback error: {e}")
        finally:
            self.finished.emit()


class HelioTTS(QObject):
    """
    Public TTS coordinator. Call speak(text) to synthesize and play.
    Emits playback_finished when audio ends so the orb can return to idle.
    """
    playback_started  = pyqtSignal()
    playback_finished = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._voice_pack = None
        self._pipeline   = None
        self._thread     = None
        self._load_voice()

    def _load_voice(self):
        try:
            from kokoro import KPipeline
            # Initialize pipeline with English ('a') language code
            self._pipeline = KPipeline(lang_code='a')
            
            # Load the Adam style voicepack
            if os.path.exists(VOICE_STYLE_PATH):
                self._voice_pack = torch.load(VOICE_STYLE_PATH, weights_only=True)
                print("[TTS] Helio premium Kokoro voice loaded: am_adam (studio-grade deep voice).")
            else:
                print(f"[TTS] Warning: Voice style pack not found at {VOICE_STYLE_PATH}")
        except Exception as e:
            print(f"[TTS] Failed to load premium voice model: {e}")

    def speak(self, text: str):
        """Speak the given text. Strips markdown and runs synthesis off the main thread."""
        if not self._pipeline or self._voice_pack is None:
            print("[TTS] Premium voice model not loaded — skipping speech.")
            self.playback_finished.emit()
            return

        clean = _clean_text(text)
        if not clean:
            self.playback_finished.emit()
            return

        print(f"[TTS] Speaking: \"{clean[:80]}{'...' if len(clean) > 80 else ''}\"")
        self.playback_started.emit()

        self._thread = TTSThread(clean, self._pipeline, self._voice_pack)
        self._thread.finished.connect(self.playback_finished)
        self._thread.start()

    def stop(self):
        """Immediately stop any in-progress audio playback."""
        try:
            sd.stop()
        except Exception:
            pass
