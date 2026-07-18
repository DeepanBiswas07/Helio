import os
import sys

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

_model_instance = None

def get_stt_model():
    global _model_instance
    if _model_instance is not None:
        return _model_instance
        
    print("\n" + "="*70)
    print("[STT INFO] INITIALIZING HIGH-ACCURACY DISTIL-WHISPER 'MEDIUM'...")
    print("NOTE: Attempting NVIDIA GPU (float16)...  CPU fallback ready.")
    print("="*70 + "\n")
    
    try:
        # Primary: NVIDIA GPU with float16 precision — instantaneous transcription
        _model_instance = WhisperModel("distil-medium.en", device="cuda", compute_type="float16")
        print("STT: Successfully loaded WhisperModel 'distil-medium.en' on NVIDIA GPU (float16).")
    except Exception as e:
        print(f"STT: GPU load failed ({e}).")
        print("STT: Falling back to CPU float32...")
        try:
            _model_instance = WhisperModel("distil-medium.en", device="cpu", compute_type="float32", cpu_threads=4)
            print("STT: Successfully loaded WhisperModel 'distil-medium.en' on CPU (float32).")
        except Exception as e2:
            print(f"STT: distil-medium.en failed ({e2}). Trying 'small' CPU fallback...")
            try:
                _model_instance = WhisperModel("small", device="cpu", compute_type="float32", cpu_threads=2)
                print("STT: Successfully loaded WhisperModel 'small' on CPU emergency fallback.")
            except Exception as e3:
                print(f"STT CRITICAL ERROR: Failed to load any WhisperModel: {e3}")
            
    return _model_instance


def transcribe_audio(audio_path: str) -> str:
    """
    Transcribes the given WAV file using faster-whisper.
    """
    if not audio_path or not os.path.exists(audio_path):
        return ""
        
    model = get_stt_model()
    if model is None:
        return ""
    try:
        # beam_size=5 and language="en" ensures maximum English accuracy and prevents hallucinations
        segments, info = model.transcribe(audio_path, beam_size=5, language="en")
        text = ""
        for segment in segments:
            text += segment.text + " "
        text = text.strip()

        # Whisper hallucination filter: these phrases are commonly generated from
        # silence, microphone noise, or TTS bleed-through. Discard them.
        _HALLUCINATIONS = {
            "thank you", "thank you.", "thanks", "thanks.",
            "thanks for watching", "thanks for watching.",
            "you", "you.", "bye", "bye.", "goodbye", "goodbye.",
            ".", "..", "...", " ",
        }
        if text.lower() in _HALLUCINATIONS:
            print(f"[STT] Hallucination filtered: '{text}'")
            return ""

        return text
    except Exception as e:
        print(f"STT transcription failed: {e}")
        return ""
