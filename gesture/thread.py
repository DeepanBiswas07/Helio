from PyQt5.QtCore import QThread, pyqtSignal
import threading
import cv2
import mediapipe as mp
from typing import Optional

try:
    from gesture.detector import HandFeatures
    from gesture.recognizer import GestureRecognizer
    from gesture.desktop_mouse import HandSlotManager
except (ImportError, ModuleNotFoundError):
    from detector import HandFeatures
    from recognizer import GestureRecognizer
    from desktop_mouse import HandSlotManager

class GestureThread(QThread):
    gesture_event = pyqtSignal(str, dict)  # Emits (event_name, extra_dict)
    hands_updated = pyqtSignal(list)       # Emits [slot1, slot2] for Desktop Holo Overlay

    def __init__(self, parent=None):
        super().__init__(parent)
        self._running = True
        # Newest hand frame, handed over by take_hands(). Only one delivery is
        # ever in flight: when Helio's UI is busy the queued signals used to
        # pile up and then replay in a burst, which read as the hands shaking.
        self._hands_lock = threading.Lock()
        self._latest_hands = [None, None]
        self._hands_pending = False
        # True while the air mouse is on: only a clap is recognised.
        self._clap_only = False

    def set_gestures_enabled(self, enabled: bool):
        """Pause every gesture except the clap (used while the air mouse is on)."""
        self._clap_only = not enabled

    def take_hands(self) -> list:
        """The newest [slot1, slot2]; call from the hands_updated slot."""
        with self._hands_lock:
            self._hands_pending = False
            return list(self._latest_hands)

    def run(self):
        import traceback
        import time
        try:
            cap = None
            print("[GestureThread] Initializing camera...")

            # Retry loop if camera is occupied or locked by another app
            while self._running:
                # Define candidate camera configs to check sequentially.
                # Prioritizing Index 1 to bypass virtual cameras (like OBS) on Index 0
                candidates = [
                    (1, cv2.CAP_DSHOW),
                    (1, None),
                    (0, cv2.CAP_DSHOW),
                    (0, None)
                ]

                success = False
                for idx, backend in candidates:
                    try:
                        if backend is not None:
                            cap = cv2.VideoCapture(idx, backend)
                        else:
                            cap = cv2.VideoCapture(idx)
                    except Exception:
                        continue

                    if cap.isOpened():
                        # Read a test frame to ensure it actually streams data
                        ret, frame = cap.read()
                        if ret and frame is not None:
                            print(f"[GestureThread] Camera successfully opened on index {idx} with backend {backend}!")
                            success = True
                            break

                    # If this configuration failed to stream, release and try next
                    cap.release()
                    cap = None

                if success:
                    break

                print("[GestureThread Warning] Camera is occupied by another app or not connected. Retrying in 2s...")
                QThread.msleep(2000)

            if not self._running:
                if cap:
                    cap.release()
                return

            # 640x480, not main.py's 1280x720: this camera delivers 18 fps here
            # against 10 fps at 720p, and MediaPipe costs the same either way.
            # The overlay filters and sizes the hologram so it matches main.py.
            cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
            cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
            # Always process the newest frame rather than one queued in the driver.
            cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)

            recognizer = GestureRecognizer()
            hand_slot_mgr = HandSlotManager()

            mp_hands = mp.solutions.hands

            with mp_hands.Hands(
                model_complexity=1,  # Balanced model (0 is too inaccurate for Peace Sign)
                min_detection_confidence=0.55,
                min_tracking_confidence=0.50,
                max_num_hands=2,
            ) as hands:
                while self._running and cap.isOpened():
                    ret, frame = cap.read()
                    if not ret:
                        QThread.msleep(10)
                        continue
                    # Capture time, so the smoothing filter measures real motion
                    # rather than when the UI got round to the frame.
                    t_frame = time.monotonic()

                    frame = cv2.flip(frame, 1)
                    h, w = frame.shape[:2]
                    rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                    rgb.flags.writeable = False
                    result = hands.process(rgb)

                    raw_feats = []
                    if result.multi_hand_landmarks:
                        for idx, lms in enumerate(result.multi_hand_landmarks):
                            h_label = "Right"
                            if result.multi_handedness and idx < len(result.multi_handedness):
                                h_label = result.multi_handedness[idx].classification[0].label
                            raw_feats.append(HandFeatures(lms.landmark, w, h, handedness=h_label,
                                                          timestamp=t_frame))

                    slot1, slot2 = hand_slot_mgr.update(raw_feats)
                    active_feats = [f for f in (slot1, slot2) if f is not None]

                    active_recognizer_feats = raw_feats if len(raw_feats) >= 2 else active_feats
                    recognizer.clap_only = self._clap_only
                    events = recognizer.update(active_recognizer_feats)

                    with self._hands_lock:
                        self._latest_hands = [slot1, slot2]
                        send = not self._hands_pending
                        self._hands_pending = True
                    if send:
                        self.hands_updated.emit([slot1, slot2])

                    for ev in events:
                        self.gesture_event.emit(ev.name, ev.extra)
                    # No sleep: cap.read() already waits for the next frame, and
                    # the old 10ms nap only made every frame 10ms staler.

            cap.release()
            print("[GestureThread] Stopped cleanly.")
        except Exception as e:
            print(f"[GestureThread ERROR] Exception in thread run: {e}")
            traceback.print_exc()

    def stop(self):
        self._running = False
        self.wait()
