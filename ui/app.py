"""
app.py — Entry point. Run this directly:
    python app.py
"""
import sys
import os

# Ensure ui/ is on the path so all local modules resolve correctly
_ui_dir = os.path.dirname(os.path.abspath(__file__))
_project_root = os.path.dirname(_ui_dir)
sys.path.insert(0, _ui_dir)
# Ensure src/ and root are on the path so Helio agent and gesture modules resolve correctly
sys.path.insert(0, os.path.join(_project_root, "src"))
sys.path.insert(0, _project_root)

# Load environment configuration (.env) from project root if it exists
def load_dotenv():
    dot_env_path = os.path.join(_project_root, ".env")
    if os.path.exists(dot_env_path):
        with open(dot_env_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if "=" in line:
                    key, val = line.split("=", 1)
                    os.environ[key.strip()] = val.strip()

load_dotenv()

# ── DLL Ordering & STT Warm-up (Must run first!) ──────────────────────────
# Instantiating WhisperModel before importing PyQt5 avoids standard C++ DLL
# ordering and OpenMP threading conflicts on Windows systems.
from voice.stt import get_stt_model
get_stt_model()
# ──────────────────────────────────────────────────────────────────────────

import threading
import signal
from PyQt5.QtWidgets import QApplication
from PyQt5.QtCore import Qt, QObject, pyqtSignal, QTimer
from orb.window import MainWindow

import traceback

# Setup safe crash logging to prevent PyQt recursive excepthook crashes
def safe_excepthook(exc_type, exc_value, exc_tb):
    print("\n" + "="*50)
    print("[*] HELIO CRASH DETECTED [*]")
    print("="*50)
    traceback.print_exception(exc_type, exc_value, exc_tb)
    print("="*50 + "\n")
    
sys.excepthook = safe_excepthook

# Set up SIGINT handler for instant clean Ctrl+C exits on Windows
def sigint_handler(*args):
    print("\n[-] Clean exit initiated via Keyboard Interrupt (Ctrl+C).")
    os._exit(0)  # Force OS-level clean exit to instantly terminate background audio and input threads

signal.signal(signal.SIGINT, sigint_handler)


class ConsoleReader(QObject):
    """
    Background-threaded console input reader for testing Helio from the terminal.
    Type 'helio' to simulate a wake word detection and enter the full listen → agent flow.
    """
    wakeword_triggered = pyqtSignal(str, float)  # (wakeword_name, score)

    def __init__(self, parent=None):
        super().__init__(parent)

    def start(self):
        thread = threading.Thread(target=self._loop, daemon=True)
        thread.start()

    def _loop(self):
        import time
        time.sleep(1.2)
        print("\n" + "="*50)
        print("[*] HELIO TERMINAL CONSOLE ACTIVE [*]")
        print("    Built by Deepan")
        print("    GitHub: https://github.com/DeepanBiswas07")
        print("="*50)
        print("  Type 'helio' + Enter to trigger the wake word.")
        print("="*50 + "\n")

        while True:
            try:
                line = sys.stdin.readline()
                if not line:
                    break
                if line.strip().lower() == "helio":
                    print("[Console] Wake word triggered via terminal.")
                    self.wakeword_triggered.emit("helio", 1.0)
            except Exception:
                break


def run():
    # High-DPI support
    QApplication.setAttribute(Qt.AA_EnableHighDpiScaling)
    QApplication.setAttribute(Qt.AA_UseHighDpiPixmaps)

    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(True)

    win = MainWindow()
    win.show()

    # Start the office console input reader
    reader = ConsoleReader(win)
    reader.wakeword_triggered.connect(win._on_wakeword_detected)
    reader.start()

    # Tiny periodic timer to periodically yield event loop to Python interpreter
    # (Enables instantaneous Ctrl+C KeyboardInterrupt detection on Windows!)
    sig_timer = QTimer()
    sig_timer.start(500)
    sig_timer.timeout.connect(lambda: None)

    sys.exit(app.exec_())


if __name__ == "__main__":
    run()
