"""
window.py — Frameless, transparent, always-on-top floating window.

Houses the premium HoloOverlay component, handling window flags,
translucency, hover interactions, mouse dragging, and tap click animations.
"""
import os
from PyQt5.QtWidgets import QWidget
from PyQt5.QtCore import Qt, QPoint, QTimer

from orb.config import (WIN_SIZE, WIN_OPACITY_IDLE, WIN_OPACITY_HOVER)
from orb.state import state as orb_state
from orb.holo_overlay import HoloOverlay

from orb.helio_space import HelioSpaceOverlay
from voice.wakeword_listener import WakeWordListener
from voice.voice_manager import VoiceManager
from voice.agent_bridge import AgentBridge
from voice.tts import HelioTTS
from gesture.thread import GestureThread
from PyQt5.QtGui import QWheelEvent


class MainWindow(QWidget):
    def __init__(self):
        super().__init__()

        # ── Window flags ─────────────────────
        self.setWindowFlags(
            Qt.FramelessWindowHint   |
            Qt.WindowStaysOnTopHint  |
            Qt.Tool                   # hides from taskbar
        )
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setFixedSize(WIN_SIZE, WIN_SIZE)
        self.setWindowOpacity(WIN_OPACITY_IDLE)



        # ── Holographic overlay (full window) ─
        # WA_TransparentForMouseEvents is set inside HoloOverlay,
        # allowing mouse clicks to fall through to this MainWindow
        self._overlay = HoloOverlay(self)
        self._overlay.move(0, 0)
        self._overlay.raise_()
        
        # ── Helio Space Overlay ───────────────
        self._space_overlay = HelioSpaceOverlay(self)

        # ── Drag state ────────────────────────
        self._drag_pos = QPoint()
        self._mouse_press_global = QPoint()

        # ── Max-duration safety timer (30s) — fallback if VAD misses end-of-speech
        self._idle_timer = QTimer(self)
        self._idle_timer.setSingleShot(True)
        self._idle_timer.timeout.connect(self._on_idle_timeout)

        # ── Wake Word Listener ────────────────
        ui_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        project_root = os.path.dirname(ui_dir)
        model_path = os.path.join(project_root, "models", "helio.onnx")

        self.listener = WakeWordListener(model_path)
        self.listener.detected.connect(self._on_wakeword_detected)
        self.listener.volume_updated.connect(self._on_volume_updated)
        # VAD end-of-speech signal — triggers transcription instantly when you stop talking
        self.listener.vad_speech_ended.connect(self._on_vad_speech_ended)
        
        # ── Voice Speech-To-Text Manager ──────
        self.voice_manager = VoiceManager(self)
        self.voice_manager.transcribed.connect(self._on_speech_transcribed)
        
        # Connect listener raw audio recording stream to voice manager
        self.listener.audio_recorded.connect(self.voice_manager.add_audio_data)

        # ── Helio Agent Bridge ─────────────────
        self.agent_bridge = AgentBridge(self)
        self.agent_bridge.response_ready.connect(self._on_agent_response)

        # ── Helio TTS (Kokoro am_adam voice) ────────
        self.tts = HelioTTS(self)
        self.tts.playback_finished.connect(self._on_tts_finished)

        self.listener.start()
        
        # ── Gesture Listener ───────────────────
        self.gesture_thread = GestureThread(self)
        self.gesture_thread.gesture_event.connect(self._on_gesture_event)
        self.gesture_thread.start()

    # ── Gesture Callbacks ───────────────────

    def _on_gesture_event(self, name: str, extra: dict):
        print(f"[Gesture Event] {name} | {extra}")
        
        if name == "open_or_expand":
            if not self._space_overlay.isVisible():
                # 1. Desktop Orb -> Fullscreen Space
                self._space_overlay.open_space()
            elif not self._space_overlay.panel_mode:
                # 2. Fullscreen Space -> Open Panel
                self._space_overlay.toggle_panel()

        elif name == "close_or_collapse":
            if self._space_overlay.panel_mode:
                # 1. Panel -> Fullscreen Space
                self._space_overlay.toggle_panel()
            elif self._space_overlay.isVisible():
                # 2. Fullscreen Space -> Desktop Orb
                self._space_overlay.close_space()

        elif name == "SWIPE_LEFT":
            if self._space_overlay.isVisible():
                self._space_overlay.shift_planet(1)
                
        elif name == "SWIPE_RIGHT":
            if self._space_overlay.isVisible():
                self._space_overlay.shift_planet(-1)

        elif name in ["SWIPE_UP", "SWIPE_DOWN"]:
            # Synthesize a mouse wheel event to scroll the panel content
            if self._space_overlay.panel_mode and self._space_overlay.active_panel:
                dy = 120 if name == "SWIPE_UP" else -120
                
                # We need to dispatch the scroll event to the active QGraphicsProxyWidget's underlying QWidget
                from PyQt5.QtWidgets import QApplication
                import PyQt5.QtCore as QtCore
                
                # Get the actual QWidget inside the active panel proxy
                target_widget = self._space_overlay.active_panel.widget()
                if target_widget:
                    wheel_event = QWheelEvent(
                        QtCore.QPointF(0, 0),
                        QtCore.QPointF(0, 0),
                        QtCore.QPoint(0, dy),  # pixelDelta
                        QtCore.QPoint(0, dy),  # angleDelta
                        Qt.NoButton,
                        Qt.NoModifier,
                        Qt.ScrollUpdate,
                        False
                    )
                    QApplication.postEvent(target_widget, wheel_event)

    # ── Wake Word & Timer Callbacks ─────────

    def _on_wakeword_detected(self, wakeword, score):
        print(f"UI Callback: Wakeword '{wakeword}' triggered UI state change to 'listening' (score: {score:.2f})")
        self._idle_timer.start(30000)  # 30s max safety timeout
        self.set_ai_state("listening")

    def _on_vad_speech_ended(self):
        """Fired by Silero VAD the instant end-of-speech is detected."""
        if orb_state.name != "listening":
            return
        self._idle_timer.stop()
        print("[VAD] Speech ended — triggering transcription.")
        self.set_ai_state("thinking")

    def _on_idle_timeout(self):
        """30-second max-duration safety fallback in case VAD doesn't fire."""
        if orb_state.name == "listening":
            print("[VAD] Max duration reached. Transcribing...")
            self.set_ai_state("thinking")

    # ── Hover interaction ────────────────────

    def enterEvent(self, event):
        self.setWindowOpacity(WIN_OPACITY_HOVER)
        # Only switch to hover profile if the current state is idle
        if orb_state.name == "idle":
            orb_state.set("hover")
            self._overlay.set_scale_target(1.08)

    def leaveEvent(self, event):
        self.setWindowOpacity(WIN_OPACITY_IDLE)
        # Only switch back to idle if we were in hover state
        if orb_state.name == "hover":
            orb_state.set("idle")
            self._overlay.set_scale_target(1.0)

    # ── Mouse Interaction ────────────────────

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            # Capture relative coordinates for window dragging
            self._drag_pos = event.globalPos() - self.frameGeometry().topLeft()
            self._mouse_press_global = event.globalPos()
            
            # Trigger holographic ripple and particle burst click animation
            self._overlay.trigger_click(event.pos())
            event.accept()

    def mouseMoveEvent(self, event):
        if event.buttons() == Qt.LeftButton:
            self.move(event.globalPos() - self._drag_pos)
            event.accept()

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton:
            dist = (event.globalPos() - self._mouse_press_global).manhattanLength()
            if dist < 5:
                # It's a clean click (no drag). Trigger immersive space expansion!
                self._space_overlay.open_space()
        event.accept()

    # ── Lifecycle ────────────────────────────

    def closeEvent(self, event):
        print("Closing application window. Stopping audio listener and gesture streams...")
        if hasattr(self, "listener") and self.listener:
            self.listener.stop()
        if hasattr(self, "gesture_thread") and self.gesture_thread:
            self.gesture_thread.stop()
        event.accept()

    # ── Public API ───────────────────────────

    def set_ai_state(self, state: str):
        """External hook — call this from the agent layer."""
        prev_state = orb_state.name
        orb_state.set(state)
        
        # Stop any active TTS audio output and abort running background queries
        if state in ["listening", "idle"]:
            self.tts.stop()
            self.agent_bridge.abort_active_query()
            self.voice_manager.abort_active_transcription()

        # Handle voice recording transitions!
        if state == "listening":
            self.voice_manager.start_listening()
            self._idle_timer.start(30000)  # 30s max safety fallback — VAD handles normal end-of-speech
        elif prev_state == "listening" and state != "listening":
            self.voice_manager.stop_listening_and_transcribe()
            self._idle_timer.stop()
            
        scale_map = {
            "idle":      1.00,
            "hover":     1.08,
            "listening": 1.05,
            "thinking":  1.04,
            "speaking":  1.06,
        }
        self._overlay.set_scale_target(scale_map.get(state, 1.00))


    def _on_volume_updated(self, vol):
        # Update the holographic overlay liquid wave amplitude
        self._overlay.set_audio_volume(vol)

    def _on_speech_transcribed(self, text: str):
        if not text.strip():
            print("\n[STT] Silence detected or transcription empty.\n")
            self.set_ai_state("idle")
            return

        # Strip punctuation Whisper tends to add (e.g. "Open." → "open")
        clean = text.strip().lower().rstrip(".,!?;:")
        print(f"\n[STT SUCCESS] User said: \"{text.strip()}\"\n")

        # ── Voice shortcut: exact phrases only → fullscreen space ─────────
        # Only trigger on very specific phrases to avoid false positives
        # like "open notepad" accidentally opening fullscreen Helio.
        _OPEN_EXACT  = {"open", "expand", "open helio", "expand helio",
                        "helio open", "helio expand"}
        _CLOSE_EXACT = {"close", "collapse", "close helio", "collapse helio",
                        "helio close"}

        if clean in _OPEN_EXACT:
            print("[Voice Command] → Open / Expand fullscreen space")
            self.set_ai_state("idle")
            if not self._space_overlay.isVisible():
                self._space_overlay.open_space()
            return

        if clean in _CLOSE_EXACT:
            print("[Voice Command] → Close / Collapse space")
            self.set_ai_state("idle")
            if self._space_overlay.isVisible():
                self._space_overlay.close_space()
            return
        # ─────────────────────────────────────────────────────────────────

        # Forward to Chat UI if instantiated
        if hasattr(self, '_space_overlay') and hasattr(self._space_overlay, 'chat_widget'):
            self._space_overlay.chat_widget.add_message("You", text.strip(), is_user=True)
            self._space_overlay.chat_widget.indicator_orb.set_state("thinking")

        # Stay in 'thinking' while the agent processes the query
        self.set_ai_state("thinking")
        self.agent_bridge.on_transcription(text)

    def _on_agent_response(self, response: str):
        if response.strip():
            # Forward to Chat UI if instantiated
            if hasattr(self, '_space_overlay') and hasattr(self._space_overlay, 'chat_widget'):
                self._space_overlay.chat_widget.add_message("Helio", response.strip(), is_user=False)
                self._space_overlay.chat_widget.indicator_orb.set_state("speaking")
                
            self.set_ai_state("speaking")
            self.tts.speak(response)
        else:
            self.set_ai_state("idle")
            if hasattr(self, '_space_overlay') and hasattr(self._space_overlay, 'chat_widget'):
                self._space_overlay.chat_widget.indicator_orb.set_state("idle")

    def _on_tts_finished(self):
        """After TTS ends: always return to idle. Wake word re-engages listening."""
        if orb_state.name != "speaking":
            return
        self.set_ai_state("idle")
        if hasattr(self, '_space_overlay') and hasattr(self._space_overlay, 'chat_widget'):
            self._space_overlay.chat_widget.indicator_orb.set_state("idle")
