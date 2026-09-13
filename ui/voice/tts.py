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


# How a file extension should be said out loud, instead of letter-mangling
# (".docx" was being pronounced "dockecks").
_EXT_SPOKEN = {
    ".pdf": "PDF", ".doc": "Word document", ".docx": "Word document",
    ".ppt": "PowerPoint", ".pptx": "PowerPoint",
    ".xls": "Excel sheet", ".xlsx": "Excel sheet",
    ".txt": "text file", ".md": "markdown file", ".csv": "CSV file",
    ".json": "JSON file", ".py": "Python file", ".log": "log file",
    ".png": "image", ".jpg": "image", ".jpeg": "image", ".zip": "zip file",
}

_UNIT_SPOKEN = [
    (r"\bKB\b", "kilobytes"), (r"\bMB\b", "megabytes"),
    (r"\bGB\b", "gigabytes"), (r"\bTB\b", "terabytes"),
]

_MONTHS = ["January", "February", "March", "April", "May", "June", "July",
           "August", "September", "October", "November", "December"]

_EXT_PATTERN = "|".join(ext[1:] for ext in _EXT_SPOKEN)


def _soften_caps(word: str) -> str:
    """
    espeak spells any all-caps word letter by letter, so "DEEPAN-BISWAS-RESUME"
    comes out "D-E-E-P-A-N...". Title-case long all-caps words so they're spoken
    as words, while leaving short ones (ML, PDF, CSV) as real acronyms.
    """
    if len(word) >= 4 and word.isupper() and word.isalpha():
        return word.capitalize()
    return word


def _spoken_filename(name: str) -> str:
    """'DEEPAN-BISWAS-ML-RESUME.pdf' -> 'Deepan Biswas ML Resume, PDF'."""
    stem, ext = os.path.splitext(name)
    stem = stem.replace("_", " ").replace("-", " ")
    stem = " ".join(_soften_caps(w) for w in stem.split())
    stem = re.sub(r"\s{2,}", " ", stem).strip()
    spoken_ext = _EXT_SPOKEN.get(ext.lower())
    if not stem:
        return spoken_ext or name
    return f"{stem}, {spoken_ext}" if spoken_ext else stem


def _speakify(text: str) -> str:
    """
    Turn machine-shaped text into something a person would say.

    Helio's answers are full of file paths, sizes and timestamps, and a raw
    TTS pass reads those literally — "C backslash Users backslash deepa
    backslash..." for a single path. This rewrites those into spoken form.
    """
    # URLs -> just the site
    text = re.sub(
        r"https?://([^\s/]+)\S*",
        lambda m: f"a link on {m.group(1).replace('www.', '')}",
        text,
    )

    # Full Windows paths -> the filename, plus the folder it sits in
    def _path(match):
        raw = match.group(0).rstrip("\\/.,;:")
        base = os.path.basename(raw)
        if not base:
            return raw
        # A folder path has no extension — just name the folder, otherwise it
        # reads as "in Downloads, in deepa".
        if not os.path.splitext(base)[1]:
            return " ".join(_soften_caps(w) for w in base.replace("_", " ").split())
        parent = os.path.basename(os.path.dirname(raw))
        spoken = _spoken_filename(base)
        return f"{spoken}, in {parent}" if parent else spoken

    text = re.sub(r"[A-Za-z]:\\[^\s,;]*", _path, text)

    # A bare drive reference: "D:\" or "D:" -> "D drive"
    text = re.sub(r"\b([A-Za-z]):\\?(?=\s|$|[,.])", r"\1 drive", text)

    # ISO dates -> spoken date
    def _iso(match):
        year, month, day = match.groups()
        index = int(month) - 1
        if 0 <= index < 12:
            return f"{_MONTHS[index]} {int(day)}, {year}"
        return match.group(0)

    text = re.sub(r"\b(\d{4})-(\d{2})-(\d{2})\b", _iso, text)

    # Clock times: the colon otherwise becomes an odd hard break
    text = re.sub(r"\b(\d{1,2}):(\d{2})\b", r"\1 \2", text)

    for pattern, replacement in _UNIT_SPOKEN:
        text = re.sub(pattern, replacement, text)

    # Loose filenames that weren't part of a full path
    text = re.sub(
        rf"\b([\w\-]+\.(?:{_EXT_PATTERN}))\b",
        lambda m: _spoken_filename(m.group(1)),
        text,
        flags=re.IGNORECASE,
    )

    # Anything left that a voice shouldn't try to pronounce
    text = re.sub(r"[\\/|*<>{}\[\]`~^_]+", " ", text)
    return re.sub(r"\s{2,}", " ", text).strip()


def _clean_text(text: str) -> str:
    """
    Strip markdown, emoji, and formatting noise so Kokoro receives
    plain, speakable prose — the same way Siri or Google Assistant would.
    """
    # Remove markdown bold/italic/code
    text = re.sub(r"\*{1,3}(.+?)\*{1,3}", r"\1", text)
    # Underscore italics only at word boundaries — otherwise this eats the
    # underscores inside identifiers like file_name_v2.
    text = re.sub(r"(?<!\w)_{1,2}([^_]+?)_{1,2}(?!\w)", r"\1", text)
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
    # Rewrite paths, dates, sizes and filenames into spoken form
    text = _speakify(text)
    # Collapse excessive whitespace
    text = re.sub(r"\s{2,}", " ", text).strip()
    return text


def _split_sentences(text: str):
    """
    Break text into speakable sentences, merging very short fragments so we
    don't end up synthesizing choppy two-word snippets.
    """
    parts = re.split(r"(?<=[.!?])\s+", text.strip())
    merged = []
    for part in parts:
        part = part.strip()
        if not part:
            continue
        if merged and len(merged[-1]) < 25:
            merged[-1] = f"{merged[-1]} {part}"
        else:
            merged.append(part)
    return merged or [text]


class TTSThread(QThread):
    """
    Synthesizes with Kokoro and plays through sounddevice, one sentence ahead.

    Kokoro returns a whole short response as a single chunk, so synthesizing
    everything before playing anything meant ~4.5s of dead air before Helio
    said a word (measured on this machine, CPU torch). Instead we synthesize
    sentence by sentence and start playing the first one immediately — the
    next sentence is synthesized while the current one is still audible, so
    the only latency the user hears is the first sentence's synthesis.
    """
    finished = pyqtSignal()

    def __init__(self, text: str, pipeline, voice_pack):
        super().__init__()
        self.text = text
        self.pipeline = pipeline
        self.voice_pack = voice_pack
        self._stop = False

    def stop(self):
        self._stop = True

    def _synth(self, sentence: str):
        chunks = []
        for _, _, audio in self.pipeline(sentence, voice=self.voice_pack, speed=0.95):
            chunks.append(audio)
        if not chunks:
            return None
        return np.concatenate(chunks).reshape(-1, 1)  # Mono 24kHz float32

    def run(self):
        try:
            for sentence in _split_sentences(self.text):
                if self._stop:
                    break

                audio = self._synth(sentence)
                if audio is None:
                    continue

                # Let the previous sentence finish before queueing this one.
                sd.wait()
                if self._stop:
                    break
                sd.play(audio, samplerate=24000)

            if not self._stop:
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

            # Synthesis is the whole latency budget here, so use the GPU when
            # torch has CUDA. Falls back to CPU rather than failing to speak.
            device = "cuda" if torch.cuda.is_available() else "cpu"
            try:
                self._pipeline = KPipeline(lang_code='a', device=device)
            except Exception as gpu_error:
                if device == "cuda":
                    print(f"[TTS] GPU init failed ({gpu_error}); falling back to CPU.")
                    device = "cpu"
                    self._pipeline = KPipeline(lang_code='a', device="cpu")
                else:
                    raise

            # Load the Adam style voicepack
            if os.path.exists(VOICE_STYLE_PATH):
                self._voice_pack = torch.load(VOICE_STYLE_PATH, weights_only=True)
                print(f"[TTS] Helio premium Kokoro voice loaded: am_adam on {device.upper()}.")
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
        # Also tell the worker to stop, or it keeps synthesizing and playing
        # the remaining sentences after the audio device has been silenced.
        if self._thread is not None:
            try:
                self._thread.stop()
            except Exception:
                pass
        try:
            sd.stop()
        except Exception:
            pass
