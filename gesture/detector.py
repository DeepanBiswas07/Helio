"""
detector.py  —  Low-level MediaPipe hand landmark analyser.

Extracts finger states, hand orientation, and velocity vectors from a single
frame's MediaPipe hand landmark result.  No gesture logic lives here — this
module is purely geometry / feature extraction so that the recognizer stays
clean and testable.
"""
import math
from typing import Optional
import mediapipe as mp

_HandLandmark = mp.solutions.hands.HandLandmark

# ── Landmark index constants (short aliases) ───────────────────────────────
WRIST       = _HandLandmark.WRIST
THUMB_CMC   = _HandLandmark.THUMB_CMC
THUMB_MCP   = _HandLandmark.THUMB_MCP
THUMB_IP    = _HandLandmark.THUMB_IP
THUMB_TIP   = _HandLandmark.THUMB_TIP

INDEX_MCP   = _HandLandmark.INDEX_FINGER_MCP
INDEX_PIP   = _HandLandmark.INDEX_FINGER_PIP
INDEX_DIP   = _HandLandmark.INDEX_FINGER_DIP
INDEX_TIP   = _HandLandmark.INDEX_FINGER_TIP

MIDDLE_MCP  = _HandLandmark.MIDDLE_FINGER_MCP
MIDDLE_PIP  = _HandLandmark.MIDDLE_FINGER_PIP
MIDDLE_DIP  = _HandLandmark.MIDDLE_FINGER_DIP
MIDDLE_TIP  = _HandLandmark.MIDDLE_FINGER_TIP

RING_MCP    = _HandLandmark.RING_FINGER_MCP
RING_PIP    = _HandLandmark.RING_FINGER_PIP
RING_DIP    = _HandLandmark.RING_FINGER_DIP
RING_TIP    = _HandLandmark.RING_FINGER_TIP

PINKY_MCP   = _HandLandmark.PINKY_MCP
PINKY_PIP   = _HandLandmark.PINKY_PIP
PINKY_DIP   = _HandLandmark.PINKY_DIP
PINKY_TIP   = _HandLandmark.PINKY_TIP


def _lm(lms, idx):
    """Return (x, y, z) tuple for a landmark index."""
    p = lms[idx]
    return (p.x, p.y, p.z)


def _dist2d(a, b) -> float:
    """Euclidean distance in normalised 2-D image space."""
    return math.hypot(a[0] - b[0], a[1] - b[1])


def _finger_extended(lms, tip_idx, pip_idx, mcp_idx, thumb=False) -> bool:
    """
    Return True when a finger is 'extended' (straight / open).

    For regular fingers: tip must be above (smaller y) the PIP joint by a
    threshold relative to the MCP→PIP segment length.

    For the thumb we compare X-axis displacement to avoid confusion from
    hand orientation — the tip must be sufficiently far from the MCP.
    """
    tip = _lm(lms, tip_idx)
    pip = _lm(lms, pip_idx)
    mcp = _lm(lms, mcp_idx)

    if thumb:
        # Use distance: tip far from MCP means extended
        return _dist2d(tip, mcp) > _dist2d(pip, mcp) * 1.15

    # Regular finger — tip above pip (in image coords y↓, so tip.y < pip.y)
    segment = _dist2d(pip, mcp)
    threshold = segment * 0.6
    return (pip[1] - tip[1]) > threshold


def _thumb_extended(lms) -> bool:
    return _finger_extended(lms, THUMB_TIP, THUMB_IP, THUMB_MCP, thumb=True)


class HandFeatures:
    """
    All geometric features extracted from one MediaPipe hand landmark set
    for a single frame.
    """

    def __init__(self, lms, frame_w: int, frame_h: int, handedness: str = "Right",
                 timestamp: Optional[float] = None):
        self.lms = lms
        self.frame_w = frame_w
        self.frame_h = frame_h
        self.handedness = handedness
        # time.monotonic() at capture, for the overlay's smoothing filter.
        self.timestamp = timestamp

        # ── Finger extended flags ──────────────────────────────────────────
        self.thumb_up   = _thumb_extended(lms)
        self.index_up   = _finger_extended(lms, INDEX_TIP,  INDEX_PIP,  INDEX_MCP)
        self.middle_up  = _finger_extended(lms, MIDDLE_TIP, MIDDLE_PIP, MIDDLE_MCP)
        self.ring_up    = _finger_extended(lms, RING_TIP,   RING_PIP,   RING_MCP)
        self.pinky_up   = _finger_extended(lms, PINKY_TIP,  PINKY_PIP,  PINKY_MCP)

        # Extended count (excluding thumb for gesture clarity)
        self.fingers_up = sum([
            self.index_up, self.middle_up, self.ring_up, self.pinky_up
        ])

        # ── Key position in pixel coords ──────────────────────────────────
        self.index_tip_px  = self._to_px(INDEX_TIP)
        self.middle_tip_px = self._to_px(MIDDLE_TIP)
        self.ring_tip_px   = self._to_px(RING_TIP)
        self.pinky_tip_px  = self._to_px(PINKY_TIP)
        self.thumb_tip_px  = self._to_px(THUMB_TIP)
        self.wrist_px      = self._to_px(WRIST)

        # Palm centre approximation (average of 5 MCPs + wrist)
        palm_pts = [
            _lm(lms, INDEX_MCP), _lm(lms, MIDDLE_MCP),
            _lm(lms, RING_MCP),  _lm(lms, PINKY_MCP),
            _lm(lms, WRIST),
        ]
        cx = sum(p[0] for p in palm_pts) / len(palm_pts)
        cy = sum(p[1] for p in palm_pts) / len(palm_pts)
        self.palm_centre_norm = (cx, cy)
        self.palm_centre_px = (int(cx * frame_w), int(cy * frame_h))

        # ── Pinch distance (thumb tip ↔ index tip, normalised) ──────────
        thumb_n  = _lm(lms, THUMB_TIP)
        index_n  = _lm(lms, INDEX_TIP)
        self.pinch_distance = _dist2d(thumb_n, index_n)

        # ── Spread distance (for reverse-pinch / spread gesture) ─────────
        pinky_n  = _lm(lms, PINKY_TIP)
        self.spread_distance = _dist2d(thumb_n, pinky_n)

    def _to_px(self, idx) -> tuple[int, int]:
        lm = self.lms[idx]
        return (int(lm.x * self.frame_w), int(lm.y * self.frame_h))

    # ── Named shape helpers ────────────────────────────────────────────────

    def is_open_palm(self) -> bool:
        """All four fingers AND thumb extended."""
        return (self.thumb_up and self.index_up and self.middle_up
                and self.ring_up and self.pinky_up)

    def is_closed_fist(self) -> bool:
        """
        No fingers extended (ignoring thumb for robustness).
        Must be tightly bunched (relative to knuckle width).
        """
        spread = _dist2d(
            _lm(self.lms, INDEX_TIP),
            _lm(self.lms, PINKY_TIP)
        )
        palm_width = _dist2d(
            _lm(self.lms, INDEX_MCP),
            _lm(self.lms, PINKY_MCP)
        )
        ratio = spread / max(0.01, palm_width)
        fingers_down = not (self.index_up or self.middle_up or self.ring_up or self.pinky_up)
        return fingers_down and ratio <= 0.95

    def is_pinch(self) -> bool:
        """
        Thumb and index tips touching/close. Middle, ring, and pinky folded.
        """
        return (
            self.pinch_distance < 0.05
            and not self.middle_up
            and not self.ring_up
            and not self.pinky_up
        )

    def is_reverse_pinch(self) -> bool:
        """
        Thumb and index extended and spread apart (L-shape). Middle, ring, pinky folded.
        """
        return (
            self.thumb_up
            and self.index_up
            and not self.middle_up
            and not self.ring_up
            and not self.pinky_up
            and self.pinch_distance > 0.10
        )


    def is_swipe_shape(self) -> bool:
        """
        Index and middle fingers extended. Ring and pinky folded.
        This is the base shape required for swiping.
        """
        return (
            self.index_up
            and self.middle_up
            and not self.ring_up
            and not self.pinky_up
        )

    def __repr__(self):
        bits = "".join([
            "T" if self.thumb_up  else "t",
            "I" if self.index_up  else "i",
            "M" if self.middle_up else "m",
            "R" if self.ring_up   else "r",
            "P" if self.pinky_up  else "p",
        ])
        return f"<HandFeatures [{bits}] pinch={self.pinch_distance:.3f}>"
