"""
main.py  —  Standalone gesture + face test harness for Helio.

Run:  python gesture/main.py
Press Q to quit.

Detects:
  • Hand gestures  (peace-sign activate, swipe, palm, fist)
  • Wink detection (left / right) — printed to terminal
"""
from __future__ import annotations
import sys, os, time, math, queue, threading

# Keep MediaPipe/TFLite's non-actionable C++ startup warnings out of the console.
os.environ.setdefault("GLOG_minloglevel", "2")
os.environ.setdefault("TF_CPP_MIN_LOG_LEVEL", "2")

import numpy as np
import cv2
import torch
import mediapipe as mp
from facenet_pytorch import InceptionResnetV1
from typing import Optional

from PyQt5.QtWidgets import QApplication

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from gesture.detector   import HandFeatures
from gesture.recognizer import (
    GestureRecognizer,
    ACTION_WINDOW,
    ACTIVATE_DWELL,
    CLAW_DWELL
)
from gesture.desktop_mouse import DesktopHoloOverlay, HandSlotManager


# ── MediaPipe ─────────────────────────────────────────────────────────────────
_mp_hands  = mp.solutions.hands
_mp_face   = mp.solutions.face_mesh
_mp_draw   = mp.solutions.drawing_utils
_hand_conn = _mp_hands.HAND_CONNECTIONS
_HAND_STYLE_1 = _mp_draw.DrawingSpec(color=(0, 200, 255), thickness=2, circle_radius=3)
_CONN_STYLE_1 = _mp_draw.DrawingSpec(color=(255, 140, 0), thickness=2)
_HAND_STYLE_2 = _mp_draw.DrawingSpec(color=(50, 230, 120), thickness=2, circle_radius=3)
_CONN_STYLE_2 = _mp_draw.DrawingSpec(color=(220, 70, 220), thickness=2)

# ── Colors (BGR) ──────────────────────────────────────────────────────────────
C_GOLD=(30,175,255); C_CYAN=(255,200,30);  C_GREEN=(60,220,60)
C_RED=(40,40,230);   C_WHITE=(240,240,240); C_SHADOW=(10,10,10)
C_PANEL=(18,12,8);   C_ORANGE=(0,140,255);  C_PURPLE=(200,80,220)

_GESTURE_DESC = {
    "CLAP":              "[CLAP]        --> Hands Collided / Clap!",
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
    return (f"[{'T' if feat.thumb_up else '-'} {'I' if feat.index_up else '-'} "
            f"{'M' if feat.middle_up else '-'} {'R' if feat.ring_up else '-'} "
            f"{'P' if feat.pinky_up else '-'}]")


# ── Holographic Hand Skeleton Renderer ─────────────────────────────────────────
# ── Smooth Sci-Fi Hologram Hand Renderer ──────────────────────────────────────
def _draw_skeleton_hologram(frame, feat: HandFeatures, hand_idx: int, t: float):
    """
    Renders a sleek, smooth sci-fi holographic hand:
      - Translucent glowing palm energy shield (clean alpha blend)
      - Smooth triple-layer neon laser conduits along finger bones (no spiky mesh)
      - Orbital cyber rings at knuckles/wrist and target pips at fingertips
      - Pure floating hologram hand without any bottom floor grid or lattice
    """
    h, w = frame.shape[:2]
    cx, cy = feat.palm_centre_px
    wx, wy = feat.wrist_px

    # Palette (BGR): Hand 0 (Electric Cyan), Hand 1 (Neon Emerald)
    if hand_idx == 0:
        c_glow    = (255, 140, 0)     # Neon blue diffuse bloom
        c_beam    = (255, 235, 40)    # Electric cyan laser
        c_core    = (255, 255, 255)   # Laser core white
        c_ring    = (255, 220, 30)    # Orbital ring
        c_shield  = (220, 130, 0)     # Palm shield fill
    else:
        c_glow    = (40, 180, 50)     # Emerald diffuse bloom
        c_beam    = (80, 245, 100)    # Neon green laser
        c_core    = (255, 255, 255)   # Laser core white
        c_ring    = (90, 240, 110)    # Orbital ring
        c_shield  = (30, 160, 40)     # Palm shield fill

    pts = [feat._to_px(i) for i in range(21)]
    hand_span = math.hypot(cx - wx, cy - wy)
    scale = max(0.6, min(1.8, hand_span / 140.0))

    # Downward arm vector for projection column
    arm_dx = float(wx - cx)
    arm_dy = float(wy - cy)
    arm_len = math.hypot(arm_dx, arm_dy)
    if arm_len > 1.0:
        arm_ux = arm_dx / arm_len
        arm_uy = arm_dy / arm_len
    else:
        arm_ux, arm_uy = 0.0, 1.0
    arm_nx = -arm_uy
    arm_ny = arm_ux

    # ── 1. Wrist Holographic Projection Column & Emitter Base ─────────────────
    col_len = int(90 * scale)
    col_w   = int(30 * scale)
    base_cx = int(wx + arm_ux * col_len)
    base_cy = int(wy + arm_uy * col_len)

    w_lx, w_ly = int(wx + arm_nx * col_w), int(wy + arm_ny * col_w)
    w_rx, w_ry = int(wx - arm_nx * col_w), int(wy - arm_ny * col_w)
    b_lx, b_ly = int(base_cx + arm_nx * (col_w * 1.15)), int(base_cy + arm_ny * (col_w * 1.15))
    b_rx, b_ry = int(base_cx - arm_nx * (col_w * 1.15)), int(base_cy - arm_ny * (col_w * 1.15))

    col_poly = np.array([(w_lx, w_ly), (b_lx, b_ly), (b_rx, b_ry), (w_rx, w_ry)], dtype=np.int32)
    overlay = frame.copy()
    cv2.fillConvexPoly(overlay, col_poly, c_shield)
    cv2.addWeighted(overlay, 0.22, frame, 0.78, 0, frame)

    # Column guide beams
    cv2.line(frame, (w_lx, w_ly), (b_lx, b_ly), c_beam, 1, cv2.LINE_AA)
    cv2.line(frame, (w_rx, w_ry), (b_rx, b_ry), c_beam, 1, cv2.LINE_AA)

    # Emitter projector base rings
    r_base_w = int(col_w * 1.6)
    r_base_h = max(5, int(12 * scale))
    cv2.ellipse(frame, (base_cx, base_cy), (r_base_w, r_base_h), 0, 0, 360, c_ring, 2, cv2.LINE_AA)
    cv2.ellipse(frame, (base_cx, base_cy), (int(r_base_w * 0.55), int(r_base_h * 0.55)), 0, 0, 360, c_core, 1, cv2.LINE_AA)

    # ── 2. Translucent Palm Energy Shield ─────────────────────────────────────
    palm_indices = [0, 1, 2, 5, 9, 13, 17]
    palm_poly = np.array([pts[i] for i in palm_indices], dtype=np.int32)

    overlay = frame.copy()
    cv2.fillConvexPoly(overlay, palm_poly, c_shield)
    cv2.addWeighted(overlay, 0.28, frame, 0.72, 0, frame)
    # Lateral palm borders only
    cv2.line(frame, (w_lx, w_ly), pts[1], c_beam, max(1, int(1.8 * scale)), cv2.LINE_AA)
    cv2.line(frame, pts[17], (w_rx, w_ry), c_beam, max(1, int(1.8 * scale)), cv2.LINE_AA)

    # ── 3. Smooth Triple-Layer Laser Conduits (Finger Bones) ──────────────────
    # Layer 3a: Wide soft atmospheric glow
    glow_thick = max(3, int(8.0 * scale))
    for c in _hand_conn:
        p1, p2 = pts[c[0]], pts[c[1]]
        cv2.line(frame, p1, p2, c_glow, glow_thick, cv2.LINE_AA)

    # Layer 3b: Electric laser beam
    beam_thick = max(2, int(2.5 * scale))
    for c in _hand_conn:
        p1, p2 = pts[c[0]], pts[c[1]]
        cv2.line(frame, p1, p2, c_beam, beam_thick, cv2.LINE_AA)

    # Layer 3c: Core super-bright white beam
    for c in _hand_conn:
        p1, p2 = pts[c[0]], pts[c[1]]
        cv2.line(frame, p1, p2, c_core, 1, cv2.LINE_AA)

    # ── 4. Transverse Cylindrical Joint Rings (Reference Image Detail) ────────
    ring_pairs = [
        (1, 2), (2, 3), (3, 4),        # Thumb
        (5, 6), (6, 7), (7, 8),        # Index
        (9, 10), (10, 11), (11, 12),   # Middle
        (13, 14), (14, 15), (15, 16),  # Ring
        (17, 18), (18, 19), (19, 20),  # Pinky
    ]
    ring_w = 7.0 * scale
    for ja, jb in ring_pairs:
        pa = pts[ja]
        pb = pts[jb]
        mx = (pa[0] + pb[0]) * 0.5
        my = (pa[1] + pb[1]) * 0.5
        dx = pb[0] - pa[0]
        dy = pb[1] - pa[1]
        sl = math.hypot(dx, dy)
        if sl < 1.0:
            continue
        nx = -dy / sl
        ny = dx / sl
        p_left  = (int(mx + nx * ring_w), int(my + ny * ring_w))
        p_right = (int(mx - nx * ring_w), int(my - ny * ring_w))
        cv2.line(frame, p_left, p_right, c_ring, 1, cv2.LINE_AA)

    # ── 5. Orbital Joint Articulations & Knuckle Nodes ────────────────────────
    for idx, p in enumerate(pts):
        if idx in (4, 8, 12, 16, 20):
            # Fingertips: Sleek target energy ring + center dot
            r_tip = max(4, int(5.5 * scale))
            cv2.circle(frame, p, r_tip, c_ring, 1, cv2.LINE_AA)
            cv2.circle(frame, p, max(1, int(2.0 * scale)), c_core, -1, cv2.LINE_AA)
        elif idx in (0, 1, 5, 9, 13, 17):
            # Knuckles and Wrist: Orbital holographic ring
            r_joint = max(4, int(5.0 * scale))
            cv2.circle(frame, p, r_joint + 2, c_ring, 1, cv2.LINE_AA)
            cv2.circle(frame, p, max(2, int(2.2 * scale)), c_core, -1, cv2.LINE_AA)
        else:
            # Intermediate joints (PIP, DIP)
            cv2.circle(frame, p, max(2, int(2.2 * scale)), c_core, -1, cv2.LINE_AA)

    # Holographic palm center nexus ring
    nexus_r = max(5, int(8.0 * scale))
    cv2.circle(frame, (cx, cy), nexus_r, c_ring, 1, cv2.LINE_AA)
    cv2.circle(frame, (cx, cy), max(2, int(2.0 * scale)), c_core, -1, cv2.LINE_AA)

    # Pointer reticle on Index Tip (Landmark 8)
    ix, iy = pts[8]
    r_ret = max(8, int(11 * scale))
    cv2.circle(frame, (ix, iy), r_ret, c_beam, 1, cv2.LINE_AA)
    t_len = max(4, int(5 * scale))
    cv2.line(frame, (ix - r_ret - t_len, iy), (ix - r_ret + 2, iy), c_beam, 1, cv2.LINE_AA)
    cv2.line(frame, (ix + r_ret - 2, iy), (ix + r_ret + t_len, iy), c_beam, 1, cv2.LINE_AA)
    cv2.line(frame, (ix, iy - r_ret - t_len), (ix, iy - r_ret + 2), c_beam, 1, cv2.LINE_AA)
    cv2.line(frame, (ix, iy + r_ret - 2), (ix, iy + r_ret + t_len), c_beam, 1, cv2.LINE_AA)



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
        # Hybrid selection: FaceNet is a deep 23M-param CNN which runs 6x-8x faster on CUDA GPU
        self.device = torch.device('cuda' if torch.cuda.is_available() else 'cpu')
        self.device_label = torch.cuda.get_device_name(0) if self.device.type == 'cuda' else 'CPU'
        print(f"[FaceID] Initializing FaceNet on optimal device: [{self.device_label}]...")
        self.model = InceptionResnetV1(pretrained='vggface2').eval().to(self.device)
        self.known_faces = {}
        self.db_path = os.path.join(_HERE, "faces.pt")
        if os.path.exists(self.db_path):
            loaded = torch.load(self.db_path, map_location='cpu')
            self.known_faces = {k: v.cpu() for k, v in loaded.items()}
            print(f"[FaceID] Loaded {len(self.known_faces)} known faces.")

        # Background worker for zero-lag GPU face embedding inference
        self._queue = queue.Queue(maxsize=2)
        self._running = True
        self._worker = threading.Thread(target=self._process_queue, daemon=True)
        self._worker.start()

    def _process_queue(self):
        while self._running:
            try:
                task = self._queue.get(timeout=0.2)
            except queue.Empty:
                continue

            face_img, callback = task
            try:
                emb = self.get_embedding(face_img)
                name, dist = self.identify(emb)
                callback(name, dist, emb)
            except Exception:
                pass
            finally:
                self._queue.task_done()

    def request_identify(self, face_img, callback):
        """Submit face crop for async GPU identification without blocking the video stream."""
        if not self._queue.full():
            try:
                self._queue.put_nowait((face_img, callback))
                return True
            except queue.Full:
                pass
        return False

    def get_embedding(self, face_img):
        img = cv2.resize(face_img, (160, 160))
        img = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
        img_float = (img.astype(np.float32) - 127.5) / 128.0
        tensor = torch.from_numpy(img_float).permute(2, 0, 1).float().unsqueeze(0).to(self.device)
        with torch.no_grad():
            emb = self.model(tensor).cpu()
        return emb

    def identify(self, emb, threshold=1.0):
        if not self.known_faces:
            return "Unknown", float('inf')
        
        emb_cpu = emb.cpu()
        best_name = "Unknown"
        best_dist = float('inf')
        for name, k_emb in self.known_faces.items():
            dist = (emb_cpu - k_emb.cpu()).norm().item()
            if dist < best_dist:
                best_dist = dist
                best_name = name
                
        if best_dist < threshold:
            return best_name, best_dist
        return "Unknown", best_dist

    def register(self, name, emb):
        self.known_faces[name] = emb.cpu()
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

    # ── GPU Acceleration Initialization ─────────────────────────────────────────
    gpu_label = "CPU"
    if torch.cuda.is_available():
        gpu_name = torch.cuda.get_device_name(0)
        gpu_label = f"CUDA: {gpu_name}"
        print(f"[GPU] PyTorch CUDA Active -> {gpu_name}")

    if cv2.ocl.haveOpenCL():
        cv2.ocl.setUseOpenCL(True)
        ocl_dev = cv2.ocl.Device.getDefault()
        print(f"[GPU] OpenCV OpenCL Active -> {ocl_dev.name()} ({ocl_dev.vendorName()})")

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

    # ── Desktop Hologram Air Mouse Overlay ──────────────────────────────────────
    qt_app = QApplication.instance() or QApplication(sys.argv)
    desktop_overlay = DesktopHoloOverlay()
    desktop_overlay.hide()

    hand_slot_mgr = HandSlotManager()

    holograms_active = False
    last_clap_toggle_t = 0.0
    CLAP_LOCK_DURATION = 3.0

    print("\n" + "="*60)
    print("  HELIO OPTIMAL HYBRID (CPU + GPU) PIPELINE")
    print(f"  • Hands & Gestures:  CPU (MediaPipe XNNPACK AVX2)")
    print(f"  • Face Mesh & Wink:  CPU (Soukupova Geometry)")
    print(f"  • Face Recognition:  GPU ({face_id.device_label} - Async CUDA)")
    print("="*60)
    print(f"  1. Hold PEACE {ACTIVATE_DWELL:.2f}s --> activate gesture window")
    print("  2. Open Palm / Fist / Swipe --> action")
    print("  3. Collide hands (CLAP) --> instant CLAP event")
    print("  4. Wink left or right eye --> printed to terminal")
    print("  5. Press 'R' to register a new face")
    print("  Press Q to quit")
    print("="*60 + "\n")



    with _mp_hands.Hands(
        model_complexity         = 1,
        min_detection_confidence = 0.55,
        min_tracking_confidence  = 0.50,
        max_num_hands            = 2,
    ) as hands, _mp_face.FaceMesh(
        max_num_faces            = 3,
        refine_landmarks         = False,   # no iris needed
        min_detection_confidence = 0.55,
        min_tracking_confidence  = 0.55,
    ) as face_mesh:

        while cap.isOpened():
            ret, frame = cap.read()
            if not ret:
                break
            t_frame = time.monotonic()

            frame = cv2.flip(frame, 1)
            h, w  = frame.shape[:2]
            rgb   = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            rgb.flags.writeable = False
            hand_res = hands.process(rgb)
            face_res = face_mesh.process(rgb)
            rgb.flags.writeable = True

            # ── Hands (Persistent Slots: Hand 1 & Hand 2) ─────────────────────
            raw_feats = []
            if hand_res.multi_hand_landmarks:
                for idx, lms in enumerate(hand_res.multi_hand_landmarks):
                    h_label = "Right"
                    if hand_res.multi_handedness and idx < len(hand_res.multi_handedness):
                        h_label = hand_res.multi_handedness[idx].classification[0].label
                    raw_feats.append(HandFeatures(lms.landmark, w, h, handedness=h_label,
                                                  timestamp=t_frame))

            slot1, slot2 = hand_slot_mgr.update(raw_feats)

            # Draw Hand 1 (Fixed index 0 / Cyan)
            if slot1 is not None:
                _draw_skeleton_hologram(frame, slot1, 0, time.time())

            # Draw Hand 2 (Fixed index 1 / Emerald Green)
            if slot2 is not None:
                _draw_skeleton_hologram(frame, slot2, 1, time.time())

            best = slot1

            # Active feats for recognizer gestures / claps
            feats = [f for f in (slot1, slot2) if f is not None]

            # Draw collision tracking line if both hands are visible
            if slot1 is not None and slot2 is not None:
                p1 = slot1.palm_centre_px
                p2 = slot2.palm_centre_px
                line_c = C_GOLD if holograms_active else C_CYAN
                cv2.line(frame, p1, p2, line_c, 1, cv2.LINE_AA)
                mx, my = (p1[0] + p2[0]) // 2, (p1[1] + p2[1]) // 2
                norm_d = math.hypot(
                    slot1.palm_centre_norm[0] - slot2.palm_centre_norm[0],
                    slot1.palm_centre_norm[1] - slot2.palm_centre_norm[1]
                )
                _put(frame, f"Hands: {norm_d:.2f}", mx - 30, my - 8, line_c, 0.44, 1)


            # Use raw_feats for 2-hand gestures (claps) so full impact trajectory is tracked
            active_recognizer_feats = raw_feats if len(raw_feats) >= 2 else feats
            # Air mouse on -> gestures paused; only a clap (to close it) counts.
            recognizer.clap_only = holograms_active
            evs = recognizer.update(active_recognizer_feats, None)
            for ev in evs:
                if ev.name == "CLAP":
                    now = time.time()
                    elapsed = now - last_clap_toggle_t
                    if elapsed >= CLAP_LOCK_DURATION:
                        holograms_active = not holograms_active
                        last_clap_toggle_t = now
                        if holograms_active:
                            desktop_overlay.show()
                            status_str = "ACTIVATED"
                        else:
                            desktop_overlay.hide()
                            desktop_overlay.controller.reset()
                            status_str = "DEACTIVATED"
                        print(f"[{_ts()}]  [CLAP] --> DESKTOP AIR MOUSE {status_str}! (Clap locked for {CLAP_LOCK_DURATION:.0f}s)")
                        last_ev_name = f"CLAP ({status_str})"
                    else:
                        rem = CLAP_LOCK_DURATION - elapsed
                        print(f"[{_ts()}]  [CLAP IGNORED] Locked for {rem:.1f}s more to prevent accidental close.")
                        last_ev_name = f"CLAP (LOCKED {rem:.1f}s)"
                else:
                    print(f"[{_ts()}]  {_GESTURE_DESC.get(ev.name, ev.name)}")
                    last_ev_name = ev.name
                last_ev_t = time.time()


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
                        # Submit to async GPU worker without stalling the camera stream
                        if (time.time() - face_data['last_eval']) > 1.2:
                            face_crop = frame[y1:y2, x1:x2].copy()
                            face_data['last_eval'] = time.time()
                            
                            def make_cb(target_face):
                                def cb(name, dist, emb):
                                    target_face['name'] = name
                                    target_face['dist'] = dist
                                    target_face['emb'] = emb
                                return cb

                            face_id.request_identify(face_crop, make_cb(face_data))

                    new_tracked_faces.append(face_data)
                    
                    # Store info for registration (largest face)
                    if area > largest_face_area:
                        largest_face_area = area
                        current_emb = face_data.get('emb')
                        current_name = face_data['name']
                        if x2 > x1 and y2 > y1:
                            current_crop = frame[y1:y2, x1:x2].copy()
                        
                tracked_faces = new_tracked_faces

            # ── HUD: gesture panel (top-left) ─────────────────────────────────
            state  = recognizer._state
            active = (state == "ACTIVE")
            sc     = C_ORANGE if active else C_GOLD
            sl     = "ACTIVE  -> DO GESTURE NOW" if active else "IDLE"

            _panel(frame, 10, 10, 395, 295)
            _put(frame, "HELIO GESTURE TEST", 20, 32, C_GOLD, 0.57)
            _put(frame, f"State:   {sl}", 20, 54, sc, 0.52)
            _put(frame, f"Shape:   {recognizer.last_shape}", 20, 74, C_WHITE, 0.50)
            
            if slot1 is not None:
                _put(frame, f"Hand 1:  {_fbits(slot1)}", 20, 94, (0, 200, 255), 0.48)
            else:
                _put(frame, "Hand 1:  [ - - - - - ]", 20, 94, (120, 120, 120), 0.48)

            if slot2 is not None:
                _put(frame, f"Hand 2:  {_fbits(slot2)}", 20, 114, (50, 230, 120), 0.48)
            else:
                _put(frame, "Hand 2:  [ - - - - - ]", 20, 114, (120, 120, 120), 0.48)

            if not active:
                _put(frame, f"PEACE hold {ACTIVATE_DWELL:.2f}s:", 20, 138, C_GOLD, 0.44)
                _bar(frame, 20, 146, 355, 10, recognizer.activate_progress, C_ORANGE)
            else:
                wr = recognizer.window_remaining
                _put(frame, f"Window:  {wr:.2f}s remaining", 20, 138, C_CYAN, 0.49)
                _bar(frame, 20, 146, 355, 10, wr / ACTION_WINDOW, C_GREEN)

            ela = time.time() - last_ev_t
            es  = f"{ela:.1f}s ago" if last_ev_t > 0 else "-"
            _put(frame, f"Last:    {last_ev_name}  ({es})", 20, 175, C_GREEN, 0.46)
            _put(frame, f"Hands:   CPU (MediaPipe XNNPACK)", 20, 198, C_CYAN, 0.42)
            _put(frame, f"FaceNet: GPU ({face_id.device_label[:20]})", 20, 218, C_GOLD, 0.42)
            holo_str = "ACTIVE (Clap to close)" if holograms_active else "OFF (Clap to open)"
            holo_c = (255, 235, 30) if holograms_active else (150, 150, 150)
            _put(frame, f"Holo:    {holo_str}", 20, 238, holo_c, 0.42)
            m_state = desktop_overlay.controller.state
            m_color = (0, 255, 170) if m_state != "IDLE" else (170, 170, 170)
            _put(frame, f"Mouse:   {m_state} (Pinch: {desktop_overlay.controller.last_pinch_dist:.3f})", 20, 258, m_color, 0.42)

            # ── Top Holographic Matrix Banner & Lockout Countdown ─────────────
            now_t = time.time()
            lock_elapsed = now_t - last_clap_toggle_t
            is_locked = lock_elapsed < CLAP_LOCK_DURATION

            if holograms_active:
                bw, bh = 460, 58
                bx = (w - bw) // 2
                by = 12
                _panel(frame, bx, by, bw, bh)
                cv2.rectangle(frame, (bx, by), (bx + bw, by + bh), (255, 235, 30), 2)
                _put(frame, ">> DESKTOP AIR MOUSE: ACTIVE <<", bx + 36, by + 26, (255, 235, 30), 0.58, 2)

                if is_locked:
                    rem_lock = CLAP_LOCK_DURATION - lock_elapsed
                    _put(frame, f"CLAP LOCKED: {rem_lock:.1f}s (Protection)", bx + 22, by + 47, C_ORANGE, 0.44, 1)
                    _bar(frame, bx + 228, by + 39, 210, 8, rem_lock / CLAP_LOCK_DURATION, C_ORANGE)
                else:
                    _put(frame, "[CLAP UNLOCKED] Clap again to close Air Mouse", bx + 36, by + 47, C_GREEN, 0.45, 1)
            else:
                if is_locked:
                    rem_lock = CLAP_LOCK_DURATION - lock_elapsed
                    bw, bh = 390, 38
                    bx = (w - bw) // 2
                    by = 12
                    _panel(frame, bx, by, bw, bh)
                    _put(frame, f"CLAP COOLDOWN: {rem_lock:.1f}s", bx + 22, by + 25, C_GOLD, 0.46, 1)
                    _bar(frame, bx + 215, by + 17, 155, 8, rem_lock / CLAP_LOCK_DURATION, C_GOLD)

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
            gy2 = h - 145
            _panel(frame, 10, gy2, 380, 135)
            _put(frame, "HOW TO USE:", 20, gy2+22, C_GOLD, 0.50)
            _put(frame, f"1. Hold PEACE {ACTIVATE_DWELL:.2f}s  -> ACTIVATE", 20, gy2+42, C_WHITE, 0.42)
            _put(frame, "2. Open Palm / Fist / Swipe -> action",         20, gy2+62, C_WHITE, 0.42)
            _put(frame, "3. CLAP hands              -> AIR MOUSE (3s lock)", 20, gy2+82, (255, 235, 30), 0.42)
            _put(frame, "   Tap: Click  |  Hold 2s: Grab & Drag",         20, gy2+102, C_GREEN, 0.42)
            _put(frame, "4. 'R' to register face    | Q to quit",       20, gy2+122, C_CYAN, 0.42)
            _put(frame, "Q to quit", w-90, h-18, C_WHITE, 0.44)


            # Active ring around active hand
            if active and best:
                cx, cy = best.palm_centre_px
                ring_c = C_GREEN if recognizer.window_remaining > 0.5 else C_RED
                cv2.circle(frame, (cx, cy), 60, ring_c, 3)

            # Visual indicator when CLAP is detected
            if last_ev_name == "CLAP" and (time.time() - last_ev_t) < 0.9:
                bw, bh = 380, 52
                bx = (w - bw) // 2
                by = 25
                _panel(frame, bx, by, bw, bh)
                cv2.rectangle(frame, (bx, by), (bx + bw, by + bh), C_GREEN, 2)
                _put(frame, ">> CLAP DETECTED! <<", bx + 35, by + 34, C_GREEN, 0.80, 2)


            if holograms_active:
                desktop_overlay.update_tracking(slot1, slot2, is_locked, rem_lock if is_locked else 0.0)

            qt_app.processEvents()

            cv2.imshow("Helio Gesture + Wink + Face ID Test", frame)
            key = cv2.waitKey(1) & 0xFF
            if key == ord('q'):
                break
            elif key == ord('r'):
                target_crop = current_crop if 'current_crop' in locals() else None
                emb_to_reg = current_emb
                if emb_to_reg is None and target_crop is not None:
                    print("\n[Face Registration] Extracting embedding on GPU...")
                    emb_to_reg = face_id.get_embedding(target_crop)

                if emb_to_reg is not None:
                    print("\n[Face Registration]")
                    name = input("Enter name for this face: ").strip()
                    if name:
                        face_id.register(name, emb_to_reg)
                        current_name = name
                        print(f"Successfully registered as {name}!")
                else:
                    print("\n[Face Registration] No face detected to register!")

    desktop_overlay.controller.reset()
    desktop_overlay.hide()
    desktop_overlay.close()
    cap.release()
    cv2.destroyAllWindows()
    print("\n[Helio Test] Exited cleanly.")


if __name__ == "__main__":
    run()
