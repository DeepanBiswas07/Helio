"""
main.py  —  Standalone gesture + face test harness for Helio.

Run:  python gesture/main.py
Press Q to quit.

Detects:
  • Hand gestures  (peace-sign activate, swipe, palm, fist)
  • Wink detection (left / right) — printed to terminal
"""
from __future__ import annotations
import sys, os, time, math

# Keep MediaPipe/TFLite's non-actionable C++ startup warnings out of the console.
os.environ.setdefault("GLOG_minloglevel", "2")
os.environ.setdefault("TF_CPP_MIN_LOG_LEVEL", "2")

import numpy as np
import cv2
import torch
import mediapipe as mp
from facenet_pytorch import InceptionResnetV1
from typing import Optional

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from gesture.detector   import HandFeatures
from gesture.recognizer import GestureRecognizer, ACTION_WINDOW, CLAW_DWELL

# ── MediaPipe ─────────────────────────────────────────────────────────────────
_mp_hands  = mp.solutions.hands
_mp_face   = mp.solutions.face_mesh
_mp_draw   = mp.solutions.drawing_utils
_hand_conn = _mp_hands.HAND_CONNECTIONS
_HAND_STYLE = _mp_draw.DrawingSpec(color=(0, 200, 255), thickness=2, circle_radius=3)
_CONN_STYLE = _mp_draw.DrawingSpec(color=(255, 140, 0), thickness=1)

# ── Colors (BGR) ──────────────────────────────────────────────────────────────
C_GOLD=(30,175,255); C_CYAN=(255,200,30);  C_GREEN=(60,220,60)
C_RED=(40,40,230);   C_WHITE=(240,240,240); C_SHADOW=(10,10,10)
C_PANEL=(18,12,8);   C_ORANGE=(0,140,255);  C_PURPLE=(200,80,220)

_GESTURE_DESC = {
    "ACTIVATE":          "[PEACE]       --> Activate!",
    "open_or_expand":    "[OPEN PALM]   --> Open/Expand",
    "close_or_collapse": "[CLOSED FIST] --> Close/Collapse",
    "SWIPE_LEFT":        "[SWIPE LEFT]  --> Navigate Left",
    "SWIPE_RIGHT":       "[SWIPE RIGHT] --> Navigate Right",
    "SWIPE_UP":          "[SWIPE UP]    --> Scroll Up",
    "SWIPE_DOWN":        "[SWIPE DOWN]  --> Scroll Down",
    "WINDOW_EXPIRED":    "[EXPIRED]     --> No action",
}

# ── EAR landmark indices (MediaPipe face mesh 6-point formula) ────────────────
# Left eye  (on screen after cv2.flip — person's right eye)
_L_EAR_PTS = (33, 160, 158, 133, 153, 144)
# Right eye (on screen after cv2.flip — person's left eye)
_R_EAR_PTS = (362, 385, 387, 263, 373, 380)

# ── Wink tuning ───────────────────────────────────────────────────────────────
WINK_RATIO    = 0.60   # winking eye EAR must be < other * this
WINK_OPEN_MIN = 0.20   # other eye must be at least this open
WINK_MIN_S    = 0.06   # min seconds eye must be closed to count
WINK_MAX_S    = 0.80   # max seconds (longer = blink, not wink)


# ── Helpers ───────────────────────────────────────────────────────────────────
def _ts():
    return time.strftime("%H:%M:%S")

def _put(frame, text, x, y, color=C_WHITE, scale=0.52, thick=1):
    cv2.putText(frame, text, (x+1,y+1), cv2.FONT_HERSHEY_SIMPLEX, scale, C_SHADOW, thick+1, cv2.LINE_AA)
    cv2.putText(frame, text, (x,  y  ), cv2.FONT_HERSHEY_SIMPLEX, scale, color,    thick,   cv2.LINE_AA)

def _panel(frame, x, y, w, h):
    ov = frame.copy()
    cv2.rectangle(ov, (x,y), (x+w,y+h), C_PANEL, -1)
    cv2.addWeighted(ov, 0.55, frame, 0.45, 0, frame)
    cv2.rectangle(frame, (x,y), (x+w,y+h), C_GOLD, 1)

def _bar(frame, x, y, w, h, pct, color):
    cv2.rectangle(frame, (x,y), (x+w,y+h), (50,50,50), -1)
    f = int(w * min(1.0, max(0.0, pct)))
    if f > 0:
        cv2.rectangle(frame, (x,y), (x+f,y+h), color, -1)
    cv2.rectangle(frame, (x,y), (x+w,y+h), C_GOLD, 1)

def _fbits(feat: Optional[HandFeatures]) -> str:
    if not feat: return "[ - - - - - ]"
    return (f"[{'T' if feat.thumb_up else '·'} {'I' if feat.index_up else '·'} "
            f"{'M' if feat.middle_up else '·'} {'R' if feat.ring_up else '·'} "
            f"{'P' if feat.pinky_up else '·'}]")


# ── Eye Aspect Ratio (6-point Soukupova formula) ──────────────────────────────
def _ear6(lms, p1, p2, p3, p4, p5, p6, W, H):
    def v(i):
        lm = lms[i]
        return np.array([lm.x * W, lm.y * H])
    return (np.linalg.norm(v(p2) - v(p6)) + np.linalg.norm(v(p3) - v(p5))) / (
            2.0 * np.linalg.norm(v(p1) - v(p4)))


# ── Wink tracker ──────────────────────────────────────────────────────────────
class _Wink:
    """Detects a single-eye wink using relative EAR ratio."""
    def __init__(self, label: str):
        self.label  = label
        self.count  = 0
        self._t0    = None
        self._armed = True

    def update(self, ear: float, other: float) -> bool:
        """Return True the frame the wink is confirmed (eye re-opens after close)."""
        now        = time.time()
        is_winking = (other > WINK_OPEN_MIN) and (ear < other * WINK_RATIO)

        if is_winking and self._armed:
            if self._t0 is None:
                self._t0 = now
        elif not is_winking:
            if self._t0 is not None and self._armed:
                d = now - self._t0
                if WINK_MIN_S <= d <= WINK_MAX_S:
                    self.count += 1
                    self._t0   = None
                    self._armed = False
                    return True
                self._t0 = None
            # re-arm only when both eyes are clearly open
            if ear > WINK_OPEN_MIN and other > WINK_OPEN_MIN:
                self._armed = True
        return False


# ── Face Recognition (Facenet) ────────────────────────────────────────────────
class FaceID:
    def __init__(self):
        print("Loading FaceNet model... (takes a moment)")
        self.model = InceptionResnetV1(pretrained='vggface2').eval()
        self.known_faces = {}
        self.db_path = os.path.join(_HERE, "faces.pt")
        if os.path.exists(self.db_path):
            self.known_faces = torch.load(self.db_path)
            print(f"Loaded {len(self.known_faces)} known faces.")

    def get_embedding(self, face_img):
        img = cv2.resize(face_img, (160, 160))
        img = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
        img_float = (img.astype(np.float32) - 127.5) / 128.0
        tensor = torch.from_numpy(img_float).permute(2, 0, 1).float().unsqueeze(0)
        with torch.no_grad():
            emb = self.model(tensor)
        return emb

    def identify(self, emb, threshold=1.0):
        if not self.known_faces:
            return "Unknown", float('inf')
        
        best_name = "Unknown"
        best_dist = float('inf')
        for name, k_emb in self.known_faces.items():
            dist = (emb - k_emb).norm().item()
            if dist < best_dist:
                best_dist = dist
                best_name = name
                
        if best_dist < threshold:
            return best_name, best_dist
        return "Unknown", best_dist

    def register(self, name, emb):
        self.known_faces[name] = emb
        torch.save(self.known_faces, self.db_path)


# ── Main ──────────────────────────────────────────────────────────────────────
def run():
    print("\n" + "="*50)
    print("[*] HELIO GESTURE VISUALIZER [*]")
    print("    Built by Deepan")
    print("    GitHub: https://github.com/DeepanBiswas07")
    print("="*50 + "\n")
    
    # Define candidate camera configs to check sequentially.
    # Prioritizing Index 1 to bypass virtual cameras (like OBS) on Index 0
    candidates = [
        (1, cv2.CAP_DSHOW),
        (1, None),
        (0, cv2.CAP_DSHOW),
        (0, None)
    ]
    
    cap = None
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
                print(f"[Gesture] Camera successfully opened on index {idx} with backend {backend}!")
                break
            
        # Release if it failed to stream
        cap.release()
        cap = None

    if not cap or not cap.isOpened():
        print("[ERROR] Cannot open webcam on index 0 or 1.")
        sys.exit(1)

    cap.set(cv2.CAP_PROP_FRAME_WIDTH,  1280)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT,  720)

    recognizer = GestureRecognizer()
    lw = _Wink("LEFT")
    rw = _Wink("RIGHT")
    face_id = FaceID()

    last_ev_name = "-"
    last_ev_t    = 0.0
    
    current_name = "Unknown"
    current_dist = float('inf')
    current_emb = None
    
    tracked_faces = [] # list of dicts: {"name", "dist", "emb", "last_eval", "cx", "cy"}

    print("\n" + "="*60)
    print("  HELIO GESTURE + WINK + FACE ID TEST")
    print(f"  1. Hold PEACE {CLAW_DWELL:.2f}s --> activate gesture window")
    print("  2. Open Palm / Fist / Swipe --> action")
    print("  3. Wink left or right eye --> printed to terminal")
    print("  4. Press 'R' to register a new face")
    print("  Press Q to quit")
    print("="*60 + "\n")

    with _mp_hands.Hands(
        model_complexity         = 1,
        min_detection_confidence = 0.80,
        min_tracking_confidence  = 0.75,
        max_num_hands            = 2,
    ) as hands, _mp_face.FaceMesh(
        max_num_faces            = 5,
        refine_landmarks         = False,   # no iris needed
        min_detection_confidence = 0.60,
        min_tracking_confidence  = 0.60,
    ) as face_mesh:

        while cap.isOpened():
            ret, frame = cap.read()
            if not ret:
                break

            frame = cv2.flip(frame, 1)
            h, w  = frame.shape[:2]
            rgb   = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            rgb.flags.writeable = False
            hand_res = hands.process(rgb)
            face_res = face_mesh.process(rgb)
            rgb.flags.writeable = True

            # ── Hands ─────────────────────────────────────────────────────────
            feats: list = []
            best: Optional[HandFeatures] = None

            if hand_res.multi_hand_landmarks:
                for lms in hand_res.multi_hand_landmarks:
                    feat = HandFeatures(lms.landmark, w, h)
                    feats.append(feat)
                    _mp_draw.draw_landmarks(frame, lms, _hand_conn, _HAND_STYLE, _CONN_STYLE)
                best = feats[0]

            evs = recognizer.update(feats, None)
            for ev in evs:
                print(f"[{_ts()}]  {_GESTURE_DESC.get(ev.name, ev.name)}")
                last_ev_name = ev.name
                last_ev_t    = time.time()

            # ── Face / Wink ───────────────────────────────────────────────────
            face_ok = False
            l_ear = r_ear = 1.0
            largest_face_area = 0

            if face_res.multi_face_landmarks:
                face_ok = True
                new_tracked_faces = []
                
                for idx, face_lms in enumerate(face_res.multi_face_landmarks):
                    lms = face_lms.landmark

                    # Only track winks for the first (primary) face
                    if idx == 0:
                        l_ear = _ear6(lms, *_L_EAR_PTS, w, h)
                        r_ear = _ear6(lms, *_R_EAR_PTS, w, h)

                        if lw.update(l_ear, r_ear):
                            print(f"[{_ts()}]  [WINK] LEFT  (#{lw.count})  L={l_ear:.3f} R={r_ear:.3f}")
                        if rw.update(r_ear, l_ear):
                            print(f"[{_ts()}]  [WINK] RIGHT (#{rw.count})  L={l_ear:.3f} R={r_ear:.3f}")

                    # ── Face Recognition logic ──
                    xs = [int(lm.x * w) for lm in lms]
                    ys = [int(lm.y * h) for lm in lms]
                    x1, x2 = max(0, min(xs)), min(w, max(xs))
                    y1, y2 = max(0, min(ys)), min(h, max(ys))
                    cx, cy = (x1 + x2) // 2, (y1 + y2) // 2
                    area = (x2 - x1) * (y2 - y1)
                    
                    pw = int((x2 - x1) * 0.2)
                    ph = int((y2 - y1) * 0.2)
                    x1, y1 = max(0, x1 - pw), max(0, y1 - ph)
                    x2, y2 = min(w, x2 + pw), min(h, y2 + ph)
                    
                    # Match with existing tracked face by nearest center
                    best_match = None
                    best_dist_sq = 150 * 150 # max pixel movement to be considered same face
                    for tf in tracked_faces:
                        d_sq = (tf['cx'] - cx)**2 + (tf['cy'] - cy)**2
                        if d_sq < best_dist_sq:
                            best_dist_sq = d_sq
                            best_match = tf
                            
                    if best_match:
                        face_data = best_match
                        face_data['cx'] = cx
                        face_data['cy'] = cy
                        tracked_faces.remove(best_match)
                    else:
                        face_data = {"name": "Unknown", "dist": float('inf'), "emb": None, "last_eval": 0.0, "cx": cx, "cy": cy}
                    
                    # Draw box and name
                    cv2.rectangle(frame, (x1, y1), (x2, y2), C_CYAN, 1)
                    _put(frame, f"{face_data['name']} ({face_data['dist']:.2f})", x1, y1 - 10, C_GOLD, 0.6, 2)
                    
                    if x2 > x1 and y2 > y1:
                        # Extract embedding if unknown or 1 second has passed
                        if time.time() - face_data['last_eval'] > 1.0 or face_data['name'] == "Unknown":
                            face_crop = frame[y1:y2, x1:x2].copy()
                            try:
                                emb = face_id.get_embedding(face_crop)
                                name, dist = face_id.identify(emb)
                                face_data['emb'] = emb
                                face_data['name'] = name
                                face_data['dist'] = dist
                                face_data['last_eval'] = time.time()
                            except Exception:
                                pass # crop might be too small or invalid
                                
                    new_tracked_faces.append(face_data)
                    
                    # Store info for registration (largest face)
                    if area > largest_face_area:
                        largest_face_area = area
                        current_emb = face_data.get('emb')
                        current_name = face_data['name']
                        
                tracked_faces = new_tracked_faces

            # ── HUD: gesture panel (top-left) ─────────────────────────────────
            state  = recognizer._state
            active = (state == "ACTIVE")
            sc     = C_ORANGE if active else C_GOLD
            sl     = "ACTIVE  -> DO GESTURE NOW" if active else "IDLE"

            _panel(frame, 10, 10, 395, 225)
            _put(frame, "HELIO GESTURE TEST", 20, 32, C_GOLD, 0.57)
            _put(frame, f"State:   {sl}", 20, 56, sc, 0.54)
            _put(frame, f"Shape:   {recognizer.last_shape}", 20, 80, C_WHITE)
            if best:
                _put(frame, f"Fingers: {_fbits(best)}", 20, 104, C_WHITE)

            if not active:
                _put(frame, f"PEACE hold {CLAW_DWELL:.2f}s:", 20, 128, C_GOLD, 0.44)
                _bar(frame, 20, 135, 355, 10, recognizer.claw_progress, C_ORANGE)
            else:
                wr = recognizer.window_remaining
                _put(frame, f"Window:  {wr:.2f}s remaining", 20, 128, C_CYAN, 0.49)
                _bar(frame, 20, 135, 355, 10, wr / ACTION_WINDOW, C_GREEN)

            ela = time.time() - last_ev_t
            es  = f"{ela:.1f}s ago" if last_ev_t > 0 else "-"
            _put(frame, f"Last:    {last_ev_name}  ({es})", 20, 162, C_GREEN, 0.46)
            _put(frame, "Detect:  landmark geometry", 20, 185, C_CYAN, 0.44)

            # ── HUD: face / wink panel (top-right) ────────────────────────────
            fx = w - 280
            _panel(frame, fx, 10, 265, 140)
            _put(frame, "FACE / WINK", fx+10, 32, C_PURPLE, 0.50)
            fs = "Detected" if face_ok else "Not found"
            _put(frame, f"Face:  {fs}", fx+10, 56, C_WHITE, 0.46)
            if face_ok:
                _put(frame, f"Identity: {current_name}", fx+10, 78, C_GOLD, 0.46)
                _put(frame, f"L-EAR: {l_ear:.3f}", fx+10, 98, C_WHITE, 0.44)
                _put(frame, f"R-EAR: {r_ear:.3f}", fx+10, 118, C_WHITE, 0.44)
                _put(frame, f"Winks: L={lw.count}  R={rw.count}", fx+10, 138, C_PURPLE, 0.46)

            # ── Bottom guide ───────────────────────────────────────────────────
            gy2 = h - 135
            _panel(frame, 10, gy2, 370, 125)
            _put(frame, "HOW TO USE:", 20, gy2+22, C_GOLD, 0.50)
            _put(frame, f"1. Hold PEACE {CLAW_DWELL:.2f}s  -> ACTIVATE", 20, gy2+42, C_WHITE, 0.42)
            _put(frame, "2. Open Palm / Fist / Swipe -> action",         20, gy2+62, C_WHITE, 0.42)
            _put(frame, "3. Wink either eye -> detected + printed",       20, gy2+82, C_PURPLE, 0.42)
            _put(frame, "4. Press 'R' to register a new face",            20, gy2+102, C_CYAN, 0.42)
            _put(frame, "Q to quit", w-90, h-18, C_WHITE, 0.44)

            # Active ring around active hand
            if active and best:
                cx, cy = best.palm_centre_px
                ring_c = C_GREEN if recognizer.window_remaining > 0.5 else C_RED
                cv2.circle(frame, (cx, cy), 60, ring_c, 3)

            cv2.imshow("Helio Gesture + Wink + Face ID Test", frame)
            key = cv2.waitKey(1) & 0xFF
            if key == ord('q'):
                break
            elif key == ord('r'):
                if current_emb is not None:
                    print("\n[Face Registration]")
                    name = input("Enter name for this face: ").strip()
                    if name:
                        face_id.register(name, current_emb)
                        current_name = name
                        print(f"Successfully registered as {name}!")
                else:
                    print("\n[Face Registration] No face detected to register!")

    cap.release()
    cv2.destroyAllWindows()
    print("\n[Helio Test] Exited cleanly.")


if __name__ == "__main__":
    run()
