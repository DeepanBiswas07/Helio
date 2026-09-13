import os
import re
import sys
import time

import numpy as np


def _inject_nvidia_cuda_paths():
    """
    Inject pip-installed NVIDIA CUDA DLL directories into PATH so that
    ctranslate2 can locate cublas64_12.dll, cudnn_*.dll, etc. at runtime.
    These are installed under .venv/Lib/site-packages/nvidia/*/bin/
    """
    site_packages = None
    for path in sys.path:
        if "site-packages" in path and "nvidia" not in path:
            site_packages = path
            break

    if not site_packages:
        return

    nvidia_dir = os.path.join(site_packages, "nvidia")
    if not os.path.isdir(nvidia_dir):
        return

    # Walk all nvidia sub-packages (cublas, cudnn, cuda_runtime, etc.) and add their bin/ dirs
    injected = []
    for pkg in os.listdir(nvidia_dir):
        bin_dir = os.path.join(nvidia_dir, pkg, "bin")
        if os.path.isdir(bin_dir) and bin_dir not in os.environ.get("PATH", ""):
            os.environ["PATH"] = bin_dir + os.pathsep + os.environ.get("PATH", "")
            injected.append(pkg)

    if injected:
        print(f"STT: Injected CUDA DLL paths for: {', '.join(injected)}")

# Inject NVIDIA paths BEFORE importing faster_whisper / ctranslate2
_inject_nvidia_cuda_paths()

from faster_whisper import WhisperModel

SAMPLE_RATE = 16000

# Override in .env, e.g. HELIO_STT_MODEL=large-v3-turbo (downloads on first use).
STT_MODEL = os.environ.get("HELIO_STT_MODEL", "").strip() or "distil-medium.en"

_model_instance = None
_gate_model = None

def get_stt_model():
    global _model_instance
    if _model_instance is not None:
        return _model_instance

    print("\n" + "="*70)
    print(f"[STT INFO] INITIALIZING WHISPER '{STT_MODEL}'...")
    print("NOTE: Attempting NVIDIA GPU (float16)...  CPU fallback ready.")
    print("="*70 + "\n")

    try:
        # Primary: NVIDIA GPU with float16 precision — instantaneous transcription
        _model_instance = WhisperModel(STT_MODEL, device="cuda", compute_type="float16")
        print(f"STT: Successfully loaded WhisperModel '{STT_MODEL}' on NVIDIA GPU (float16).")
    except Exception as e:
        print(f"STT: GPU load failed ({e}).")
        print("STT: Falling back to CPU float32...")
        try:
            _model_instance = WhisperModel(STT_MODEL, device="cpu", compute_type="float32", cpu_threads=4)
            print(f"STT: Successfully loaded WhisperModel '{STT_MODEL}' on CPU (float32).")
        except Exception as e2:
            print(f"STT: {STT_MODEL} failed ({e2}). Trying 'small' CPU fallback...")
            try:
                _model_instance = WhisperModel("small", device="cpu", compute_type="float32", cpu_threads=2)
                print("STT: Successfully loaded WhisperModel 'small' on CPU emergency fallback.")
            except Exception as e3:
                print(f"STT CRITICAL ERROR: Failed to load any WhisperModel: {e3}")

    return _model_instance


# ── audio in, text out ───────────────────────────────────────────────────────

# Whisper produces these from silence, microphone noise or TTS bleed-through.
# Only discarded when they are the WHOLE transcript — "no" and "okay" are not
# here, because they are real answers to "should I delete that?".
_HALLUCINATIONS = {
    "thank you", "thanks", "thanks for watching", "you", "bye", "goodbye",
    "you're", "you're here", "you know", "",
}


def _prepare(audio):
    """
    int16 -> float32 at a consistent level.

    Whisper was fed the raw recording, so a quiet laptop mic reached it as a
    faint signal. Measured on test commands: word error 9.6% -> 6.5% on quiet
    speech and 8.2% -> 3.1% on clean speech once the level is normalised
    (together with skipping timestamps, which also halved the time taken).
    """
    x = np.asarray(audio).reshape(-1).astype(np.float32) / 32768.0
    if x.size == 0:
        return x, 0.0
    x = x - float(np.mean(x))
    peak = float(np.max(np.abs(x)))
    if peak > 0:
        x = x * min(0.9 / peak, 30.0)
    return x, peak


def load_speech_gate():
    """Silero for deciding whether a recording holds speech at all."""
    global _gate_model
    if _gate_model is None:
        try:
            from silero_vad import load_silero_vad
            _gate_model = load_silero_vad()
        except Exception as e:
            print(f"[STT] speech gate unavailable ({e}); every recording goes to Whisper.")
            _gate_model = False
    return _gate_model


def _has_speech(x):
    """
    Whether the recording holds any speech at all (True if the gate is unavailable).

    Normalising the level makes Whisper invent words from silence ("No.",
    "you're", "Thank you."), so silence must never reach it. The gate only
    decides; it does not crop. Cropping to Silero's speech span was tested and
    cut words off in noise — word error in a noisy room 8.1% uncropped against
    12-17% cropped — and Whisper's own vad_filter doubled it on quiet speech.
    """
    gate = load_speech_gate()
    if not gate:
        return True
    import torch
    from silero_vad import get_speech_timestamps
    spans = get_speech_timestamps(torch.from_numpy(x), gate, threshold=0.25,
                                  sampling_rate=SAMPLE_RATE, min_speech_duration_ms=150,
                                  min_silence_duration_ms=300, speech_pad_ms=100)
    return bool(spans)


def _clean(text):
    return re.sub(r"[^a-z' ]+", "", (text or "").lower()).strip()


def transcribe_array(audio, after_wake=False):
    """
    Transcribe int16 mono 16 kHz audio.

    after_wake: the recording began at the wake word, so a leading "Hey Helio"
    (or the tail of it) is removed from the result.
    """
    model = get_stt_model()
    if model is None:
        return ""

    started = time.perf_counter()
    x, peak = _prepare(audio)
    if x.size < SAMPLE_RATE // 4 or peak < 3e-4:
        print("[STT] Recording is silent — nothing to transcribe.")
        return ""

    if not _has_speech(x):
        print(f"[STT] No speech in {x.size / SAMPLE_RATE:.1f}s of audio — skipped.")
        return ""

    try:
        # Tested and left out: an initial_prompt or hotwords with the app's
        # vocabulary. With distil-medium they cut sentences short ("Open.")
        # and raised word error to 18-97%.
        segments, _ = model.transcribe(x, beam_size=5, language="en",
                                       condition_on_previous_text=False,
                                       without_timestamps=True)
        kept = [s for s in segments
                if not (s.no_speech_prob > 0.6 and s.avg_logprob < -1.0)]
        text = " ".join(s.text.strip() for s in kept).strip()
    except Exception as e:
        print(f"STT transcription failed: {e}")
        return ""

    if after_wake:
        from voice.wake_engine import strip_wake
        text = strip_wake(text)
        # The recording starts a frame before the command, which can catch the
        # very end of the name.
        text = re.sub(r"^\W*(?:lio|leo|io)\b[\s,.!?]*", "", text, flags=re.IGNORECASE).strip()

    if _clean(text) in _HALLUCINATIONS:
        if text:
            print(f"[STT] Hallucination filtered: '{text}'")
        return ""

    print(f"[STT] {x.size / SAMPLE_RATE:.1f}s of speech -> {text!r} "
          f"({(time.perf_counter() - started) * 1000:.0f} ms)")
    return text


def check_name(audio):
    """Quick transcription of the start of an utterance, for the onset wake check."""
    model = get_stt_model()
    if model is None:
        return ""
    x, peak = _prepare(audio)
    if x.size < SAMPLE_RATE // 4 or peak < 3e-4:
        return ""
    try:
        segments, _ = model.transcribe(x, beam_size=1, language="en",
                                       condition_on_previous_text=False,
                                       without_timestamps=True, max_new_tokens=12)
        return " ".join(s.text.strip() for s in segments).strip()
    except Exception as e:
        print(f"[Wake check] transcription failed: {e}")
        return ""


def transcribe_audio(audio_path: str) -> str:
    """Transcribe a WAV file (kept for anything that still has one)."""
    if not audio_path or not os.path.exists(audio_path):
        return ""
    from scipy.io.wavfile import read
    try:
        rate, data = read(audio_path)
    except Exception as e:
        print(f"STT could not read {audio_path}: {e}")
        return ""
    if data.ndim > 1:
        data = data.mean(axis=1)
    if data.dtype != np.int16:
        data = np.clip(data.astype(np.float32) * (32767 if np.abs(data).max() <= 1.0 else 1),
                       -32768, 32767).astype(np.int16)
    if rate != SAMPLE_RATE:
        from scipy.signal import resample_poly
        data = resample_poly(data.astype(np.float32), SAMPLE_RATE, rate).astype(np.int16)
    return transcribe_array(data)
