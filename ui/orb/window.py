"""
window.py — Frameless, transparent, always-on-top floating window.

Houses the premium HoloOverlay component, handling window flags,
translucency, hover interactions, mouse dragging, and tap click animations.
"""
import os
from collections import deque
from typing import Optional

from PyQt5.QtWidgets import QWidget
from PyQt5.QtCore import Qt, QPoint, QTimer, QObject, pyqtSignal

from orb.config import (WIN_SIZE, WIN_OPACITY_IDLE, WIN_OPACITY_HOVER)
from orb.state import state as orb_state
from orb.holo_overlay import HoloOverlay

from orb.helio_space import HelioSpaceOverlay
from voice.wakeword_listener import WakeWordListener
from voice.voice_manager import VoiceManager
from voice.agent_bridge import AgentBridge
from voice.tts import HelioTTS
from services.reminder_service import ReminderService
from services.schedule_service import ScheduleService
from services.reflection_service import ReflectionService
from gesture.thread import GestureThread
from gesture.desktop_mouse import DesktopHoloOverlay
from PyQt5.QtGui import QWheelEvent
import time


class _WorkshopCall(QObject):
    """Carries UI requests from a worker thread to the GUI thread."""

    wanted = pyqtSignal()
    planet = pyqtSignal(int)


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

        # ── The workshop ──────────────────────
        # Helio can be told "open the workshop" mid-sentence, and that arrives
        # on the agent's worker thread. Opening a top-level window there is a
        # crash, so the tool only ever emits this signal and Qt delivers it
        # back here on the GUI thread.
        self._workshop_call = _WorkshopCall()
        self._workshop_call.wanted.connect(self._open_workshop)
        self._workshop_call.planet.connect(self._open_planet)
        try:
            from tools.system import workshop_tool
            workshop_tool.ON_WORKSHOP_OPEN = self._workshop_call.wanted.emit
            workshop_tool.ON_PLANET_OPEN = self._workshop_call.planet.emit
        except Exception as e:
            print(f"[Window] workshop hook failed: {e}")

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

        # ── Reminders / timers ─────────────────
        # Reminders are written to disk by the agent's tools; this service polls
        # the store on the GUI thread and speaks whatever comes due.
        self._pending_announcements = deque()
        self.reminders = ReminderService(self)
        self.reminders.reminder_due.connect(self._on_reminder_due)
        self.reminders.start()

        # ── Schedule ───────────────────────────
        # Leads events rather than firing on them, and opens the day once.
        # Shares the same announcement queue so a timer and a meeting never
        # talk over each other.
        self.schedule = ScheduleService(self)
        self.schedule.announce.connect(self._on_schedule_announce)
        self.schedule.start()

        # ── Reflection ─────────────────────────
        # Mines the behaviour log for patterns on a slow timer, and only while
        # nothing else is happening. Deliberately silent: what it learns shows
        # up in MEMORY and answers "what have you learned", rather than
        # interrupting to announce that it noticed something.
        self.reflection = ReflectionService(
            self, is_busy=lambda: orb_state.name in ("listening", "thinking", "speaking"))
        self.reflection.learned.connect(self._on_learned)
        self.reflection.suggestion.connect(self._on_pattern_suggestion)
        self.reflection.start()

        self.listener.start()
        
        # ── Gesture Listener ───────────────────
        self._holo_mouse = DesktopHoloOverlay()
        self._holo_mouse.hide()
        self._holo_mouse_active = False
        self._holo_mouse_lock_t = 0.0
        self._holo_mouse_lock_dur = 3.0

        self.gesture_thread = GestureThread(self)
        self.gesture_thread.gesture_event.connect(self._on_gesture_event)
        self.gesture_thread.hands_updated.connect(self._on_hands_updated)
        self.gesture_thread.start()

    # ── Gesture & Air Mouse Callbacks ───────────────────

    def toggle_air_mouse(self, force_state: Optional[bool] = None) -> bool:
        """
        Toggle or set the Desktop Holographic Air Mouse state.
        Returns the new active state (True/False).
        """
        now = time.time()
        elapsed = now - self._holo_mouse_lock_t
        if force_state is None and elapsed < self._holo_mouse_lock_dur:
            rem = self._holo_mouse_lock_dur - elapsed
            print(f"[{time.strftime('%H:%M:%S')}] [Air Mouse] Toggle ignored: locked for {rem:.1f}s more to prevent accidental toggle.")
            return self._holo_mouse_active

        if force_state is not None:
            self._holo_mouse_active = force_state
        else:
            self._holo_mouse_active = not self._holo_mouse_active

        self._holo_mouse_lock_t = now
        if self._holo_mouse_active:
            self._holo_mouse.show()
            self._holo_mouse.raise_()
            print(f"[{time.strftime('%H:%M:%S')}] [Air Mouse] >> DESKTOP HOLOGRAPHIC AIR MOUSE ACTIVATED! << Gestures paused (clap or type 'airmouse' to close)")
        else:
            self._holo_mouse.hide()
            self._holo_mouse.release_all()
            print(f"[{time.strftime('%H:%M:%S')}] [Air Mouse] >> DESKTOP HOLOGRAPHIC AIR MOUSE DEACTIVATED! << Gestures back on")

        # The air mouse and the gestures read the same hands, and pinching or
        # pointing looks like a peace sign or a fist. While the air mouse is
        # on, only a clap (to turn it off) is listened for.
        thread = getattr(self, "gesture_thread", None)
        if thread is not None:
            thread.set_gestures_enabled(not self._holo_mouse_active)

        return self._holo_mouse_active

    def _on_hands_updated(self, slots):
        # Always take the newest frame (this also lets the thread send the
        # next one), so a busy UI skips stale frames instead of replaying them.
        slots = self.gesture_thread.take_hands()
        if self._holo_mouse_active:
            now = time.time()
            elapsed = now - self._holo_mouse_lock_t
            is_locked = elapsed < self._holo_mouse_lock_dur
            rem = max(0.0, self._holo_mouse_lock_dur - elapsed)
            slot1 = slots[0] if len(slots) > 0 else None
            slot2 = slots[1] if len(slots) > 1 else None
            # Inside the workshop both hands get their own pointer.
            overlay = self._space_overlay
            workshop = getattr(overlay, "workshop", None)
            if not (workshop is not None and overlay.isVisible() and workshop.isVisible()):
                workshop = None
            self._holo_mouse.update_tracking(slot1, slot2, is_locked, rem, workshop=workshop)

    def _on_gesture_event(self, name: str, extra: dict):
        print(f"[Gesture Event] {name} | {extra}")
        # Gestures are paused while the air mouse is on. This also drops one
        # that was already queued from just before it switched on.
        if self._holo_mouse_active and name != "CLAP":
            return
        
        overlay = self._space_overlay
        workshop = getattr(overlay, "workshop", None)
        in_workshop = workshop is not None and workshop.isVisible()

        if name == "CLAP":
            # Two-hand clap toggles the Desktop Holographic Air Mouse
            self.toggle_air_mouse()

        elif name == "open_or_expand":
            if in_workshop:
                pass                            # already as open as it gets
            elif not overlay.isVisible():
                # 1. Desktop orb -> the solar system
                overlay.open_space()
            elif not overlay.panel_mode:
                # 2. Solar system -> the planet's panel
                overlay.toggle_panel()
            else:
                # 3. Panel -> the workshop, full screen
                overlay.open_workshop()

        elif name == "close_or_collapse":
            if in_workshop:
                # 1. Workshop -> back to the Forge panel it belongs to
                overlay.close_workshop()
                self._show_forge_panel()
            elif overlay.panel_mode:
                # 2. Panel -> the solar system
                overlay.toggle_panel()
            elif overlay.isVisible():
                # 3. Solar system -> desktop orb
                overlay.close_space()

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
        # Where in the audio stream the wake word was, so the recording starts
        # there and not whenever this thread got round to it. Taken once: the
        # typed 'helio' command has no position and records from now.
        self._wake_from = getattr(self.listener, "wake_start_sample", None)
        self.listener.wake_start_sample = None
        self._idle_timer.start(30000)  # 30s max safety timeout
        self.set_ai_state("listening")

    def _on_vad_speech_ended(self):
        """Fired by Silero VAD the instant end-of-speech is detected."""
        if orb_state.name != "listening":
            return
        self._idle_timer.stop()
        print("[VAD] Speech ended — triggering transcription.")
        self.set_ai_state("thinking")

    def _show_forge_panel(self):
        """Land on the Forge panel after leaving the workshop."""
        overlay = self._space_overlay
        try:
            if overlay.active_planet_index != 7 or not overlay.panel_mode:
                overlay.trigger_planet(7, auto_open=True)
        except Exception as e:
            print(f"[Window] could not open the Forge panel: {e}")

    def _open_planet(self, slot):
        """Swing the ring to one planet and open it. GUI thread only."""
        try:
            if not self._space_overlay.isVisible():
                self._space_overlay.open_space()
            self._space_overlay.trigger_planet(int(slot), auto_open=True)
        except Exception as e:
            print(f"[Window] could not open planet {slot}: {e}")

    def _open_workshop(self):
        """
        Bring the workshop up inside Helio. GUI thread only.

        It used to close the solar system and open a second full-screen
        window; now it is a panel over the same overlay, so Helio never goes
        away and there is only one full screen.
        """
        try:
            if not self._space_overlay.isVisible():
                self._space_overlay.open_space()
            return self._space_overlay.open_workshop()
        except Exception as e:
            print(f"[Window] could not open workshop: {e}")
            return None

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
        if hasattr(self, "_holo_mouse") and self._holo_mouse:
            self._holo_mouse.close()
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
            self.voice_manager.start_listening(from_sample=getattr(self, "_wake_from", None))
            self._wake_from = None
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
                
            # If a visual artifact was forged or updated, refresh Forge widget and launch live preview
            if hasattr(self, '_space_overlay') and hasattr(self._space_overlay, 'forge_widget'):
                self._space_overlay.forge_widget.refresh()
                resp_lower = response.lower()
                # The Forge opens itself when a build *starts* (see
                # helio_space), so by now the user has already watched it
                # land. Nothing to launch here — a floating window thrown on
                # top of the panel showing the same page was just noise.
                if any(kw in resp_lower for kw in
                       ["forge", "chart", "built", "on the bench",
                        "forge canvas", "website"]):
                    if self._space_overlay.isVisible():
                        self._space_overlay.trigger_planet(7, auto_open=True)

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

        # A reminder that came due mid-sentence waited for this moment.
        if self._pending_announcements:
            QTimer.singleShot(400, self._flush_announcements)

    # ── Reminders ───────────────────────────

    def _on_learned(self, records):
        """Something was inferred. Noted, not spoken — see ReflectionService."""
        for record in records:
            print(f"[Learned] {record.get('fact', '')}")

    def _on_pattern_suggestion(self, suggestion):
        """
        A pattern that maps onto a routine worth offering.

        Written into the chat transcript rather than spoken, so it's waiting
        the next time CHAT is opened instead of interrupting whatever is
        happening now.
        """
        steps = " → ".join(suggestion.get("steps", []))
        line = ("{} Want me to make that a routine? It would be: {}"
                .format(suggestion.get("fact", ""), steps))
        if hasattr(self, "_space_overlay") and hasattr(self._space_overlay, "chat_widget"):
            self._space_overlay.chat_widget.add_message("Helio", line, is_user=False)
        print(f"[Learned] Suggestion queued: {line}")

    def _on_schedule_announce(self, message: str):
        """The schedule speaking on its own. Queued like any other reminder."""
        self._pending_announcements.append(message)
        if orb_state.name in ("speaking", "listening", "thinking"):
            return
        self._flush_announcements()

    def _on_reminder_due(self, message: str):
        """A reminder came due. Speak it — but never over Helio's own voice."""
        self._pending_announcements.append(f"Reminder: {message}")

        # Cutting into a reply, or into the user mid-sentence, is what makes an
        # alarm clock feel different from an assistant. Wait for a quiet moment.
        if orb_state.name in ("speaking", "listening", "thinking"):
            return
        self._flush_announcements()

    def _flush_announcements(self):
        if not self._pending_announcements:
            return
        if orb_state.name in ("speaking", "listening", "thinking"):
            return

        line = " Also, ".join(self._pending_announcements)
        self._pending_announcements.clear()

        if hasattr(self, '_space_overlay') and hasattr(self._space_overlay, 'chat_widget'):
            self._space_overlay.chat_widget.add_message("Helio", line, is_user=False)
            self._space_overlay.chat_widget.indicator_orb.set_state("speaking")

        self.set_ai_state("speaking")
        self.tts.speak(line)
