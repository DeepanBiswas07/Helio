from PyQt5.QtCore import QThread, pyqtSignal
import cv2
import mediapipe as mp
from typing import Optional

from gesture.detector import HandFeatures
from gesture.recognizer import GestureRecognizer

class GestureThread(QThread):
    gesture_event = pyqtSignal(str, dict)  # Emits (event_name, extra_dict)
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self._running = True

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

            # Lower resolution for background processing
            cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
            cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)

            recognizer = GestureRecognizer()

            mp_hands = mp.solutions.hands

            with mp_hands.Hands(
                model_complexity=1,  # Balanced model (0 is too inaccurate for Peace Sign)
                min_detection_confidence=0.75,
                min_tracking_confidence=0.75,
                max_num_hands=2,
            ) as hands:
                while self._running and cap.isOpened():
                    ret, frame = cap.read()
                    if not ret:
                        QThread.msleep(10)
                        continue

                    frame = cv2.flip(frame, 1)
                    h, w = frame.shape[:2]
                    rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                    rgb.flags.writeable = False
                    result = hands.process(rgb)

                    feats = []
                    if result.multi_hand_landmarks:
                        for lms in result.multi_hand_landmarks:
                            feats.append(HandFeatures(lms.landmark, w, h))

                    events = recognizer.update(feats)
                    for ev in events:
                        self.gesture_event.emit(ev.name, ev.extra)

                    # Small sleep to yield (cv2.waitKey isn't needed without imshow)
                    QThread.msleep(10)

            cap.release()
            print("[GestureThread] Stopped cleanly.")
        except Exception as e:
            print(f"[GestureThread ERROR] Exception in thread run: {e}")
            traceback.print_exc()

    def stop(self):
        self._running = False
        self.wait()
