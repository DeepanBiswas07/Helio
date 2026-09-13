"""
recognizer.py  —  Stateful gesture recogniser for Helio.

State Machine
─────────────
  ANYTIME:
    CLAP (two hands collide) ────────▶ FIRE "CLAP"

  IDLE:
    PEACE held 0.2s ─────────────────▶ ACTIVE (8s action window opens)

  ACTIVE  (8-second action window):
    OPEN_PALM held briefly   ──────▶ FIRE "open_or_expand"
    CLOSED_FIST held briefly ──────▶ FIRE "close_or_collapse"
    SWIPE_SHAPE + motion     ──────▶ FIRE "SWIPE_LEFT/RIGHT/UP/DOWN"
    CLAP (two hands collide) ──────▶ FIRE "CLAP"
    Window expires           ──────▶ back to IDLE (no action)

  After ANY action fires → immediately back to IDLE.
  To do another action   → hold PEACE sign again for 0.2s.

Action semantics (handled by the caller, not here)
───────────────────────────────────────────────────
  "CLAP"
      • Toggle open/collapse or trigger sound/action
  "open_or_expand"
      • Desktop Orb mode  → expand to fullscreen
      • Fullscreen (space)→ open panel
  "close_or_collapse"
      • Panel open        → close panel, return to space
      • Space (no panel)  → collapse to desktop Orb
  "SWIPE_RIGHT/LEFT"  → navigate right/left in space
  "SWIPE_UP/DOWN"     → scroll panel content up/down
"""
from __future__ import annotations
import time
import math
from collections import deque
from typing import Dict, List, Optional, Tuple

try:
    from gesture.detector import HandFeatures
except (ImportError, ModuleNotFoundError):
    from detector import HandFeatures


# ── Timing ─────────────────────────────────────────────────────────────────
ACTIVATE_DWELL   = 0.20   # seconds PEACE SIGN must be held to activate
CLAW_DWELL       = ACTIVATE_DWELL  # Backwards compatibility alias
ACTION_WINDOW    = 8.00   # seconds the active window stays open (increased 4x)
HOLD_DWELL       = 0.45   # seconds OPEN_PALM / CLOSED_FIST must be held (gives time to accelerate for swipes)
SWIPE_WINDOW     = 0.15   # rolling velocity buffer duration (s) (lowered for quicker flicks)
SWIPE_VX_THRESH  = 250    # px/s to fire horizontal swipe (lowered to trigger with much less distance)
SWIPE_VY_THRESH  = 200    # px/s to fire vertical swipe
SWIPE_COOLDOWN   = 0.80   # anti-spam per direction
GLOBAL_COOLDOWN  = 0.40   # min gap between any two non-swipe events

# Clap detection timing
CLAP_CONTACT_THRESH    = 0.19   # max normalised distance between palms to count as contact (generous for reliable palm impact)
CLAP_SEPARATION_THRESH = 0.20   # min past normalised distance required to verify hands started apart
CLAP_MIN_SPEED         = 0.25   # min approach speed (normalised units / s)
CLAP_HISTORY_WINDOW    = 0.45   # rolling history buffer duration (s)
CLAP_COOLDOWN          = 0.45   # seconds between consecutive clap events



# ── GestureEvent ────────────────────────────────────────────────────────────

class GestureEvent:
    """Immutable value object emitted when a gesture is confirmed."""
    def __init__(self, name: str, extra: dict = None):
        self.name      = name
        self.extra     = extra or {}
        self.timestamp = time.time()

    def __repr__(self):
        return f"<GestureEvent '{self.name}' @ {self.timestamp:.3f}>"


# ── Internal hold tracker ───────────────────────────────────────────────────

class _HoldState:
    def __init__(self, dwell: float):
        self._dwell  = dwell
        self._start: Optional[float] = None
        self._fired  = False
        self._waiting_release = False

    def update(self, active: bool) -> bool:
        """Return True the *first* frame the hold is satisfied."""
        if not active:
            self._start = None
            self._fired = False
            self._waiting_release = False
            return False
            
        if self._waiting_release:
            return False

        if self._start is None:
            self._start = time.time()
            
        if not self._fired and (time.time() - self._start) >= self._dwell:
            self._fired = True
            self._waiting_release = True  # Prevent re-triggering until broken
            return True
            
        return False

    def reset(self):
        self._start = None
        self._fired = False
        # Note: We specifically do NOT reset _waiting_release here.
        # This forces the user to physically release the gesture before a new hold can start.


    @property
    def progress(self) -> float:
        """0.0–1.0 dwell progress for HUD display."""
        if self._start is None:
            return 0.0
        return min(1.0, (time.time() - self._start) / self._dwell)


# ── Velocity / swipe tracker ─────────────────────────────────────────────────

class _VelocityTracker:
    """Rolling (timestamp, x, y) buffer for velocity calculation."""
    def __init__(self, window: float = SWIPE_WINDOW):
        self._window = window
        self._buf: deque = deque()

    def push(self, x: int, y: int):
        now = time.time()
        self._buf.append((now, x, y))
        cutoff = now - self._window
        while self._buf and self._buf[0][0] < cutoff:
            self._buf.popleft()

    def velocity(self) -> Tuple[float, float]:
        if len(self._buf) < 3:
            return 0.0, 0.0
        t0, x0, y0 = self._buf[0]
        t1, x1, y1 = self._buf[-1]
        dt = t1 - t0
        if dt < 1e-4:
            return 0.0, 0.0
        return (x1 - x0) / dt, (y1 - y0) / dt

    def clear(self):
        self._buf.clear()


# ── Clap Detector ────────────────────────────────────────────────────────────

class _ClapDetector:
    """
    Detects two hands clapping / colliding in front of the camera.
    
    Tracks normalised distance between palm centres over a rolling time window.
    Fires when two hands that were separated rapidly converge and collide.
    """
    def __init__(
        self,
        contact_thresh: float = CLAP_CONTACT_THRESH,
        separation_thresh: float = CLAP_SEPARATION_THRESH,
        min_approach_speed: float = CLAP_MIN_SPEED,
        history_window: float = CLAP_HISTORY_WINDOW,
        cooldown: float = CLAP_COOLDOWN,
    ):
        self.contact_thresh = contact_thresh
        self.separation_thresh = separation_thresh
        self.min_approach_speed = min_approach_speed
        self.history_window = history_window
        self.cooldown = cooldown

        self._history: deque = deque()  # (timestamp, distance)
        self._last_clap_time = 0.0
        self._waiting_separation = False

    def update(self, feats: list) -> Optional[dict]:
        """
        Process current frame hand features.
        Returns dict with event details if a clap just occurred, otherwise None.
        """
        now = time.time()

        # Clean old entries from history buffer
        cutoff = now - self.history_window
        while self._history and self._history[0][0] < cutoff:
            self._history.popleft()

        if len(feats) >= 2:
            h1, h2 = feats[0], feats[1]
            c1 = getattr(h1, "palm_centre_norm", None)
            c2 = getattr(h2, "palm_centre_norm", None)
            if c1 is None or c2 is None:
                w = max(1, h1.frame_w)
                h = max(1, h1.frame_h)
                c1 = (h1.palm_centre_px[0] / w, h1.palm_centre_px[1] / h)
                c2 = (h2.palm_centre_px[0] / w, h2.palm_centre_px[1] / h)

            dist = math.hypot(c1[0] - c2[0], c1[1] - c2[1])
            self._history.append((now, dist))

            # If waiting for separation after a previous clap, check if hands moved apart
            if self._waiting_separation:
                if dist >= self.separation_thresh:
                    self._waiting_separation = False
                return None

            # Enforce cooldown
            if (now - self._last_clap_time) < self.cooldown:
                return None

            # Check if contact is established
            if dist <= self.contact_thresh:
                # Look back in history to find the maximum separation before impact
                if len(self._history) >= 2:
                    max_past_dist = 0.0
                    past_t = now
                    for t, past_d in self._history:
                        if past_d > max_past_dist:
                            max_past_dist = past_d
                            past_t = t

                    dt = now - past_t
                    if dt > 0.03 and max_past_dist >= self.separation_thresh:
                        approach_speed = (max_past_dist - dist) / dt
                        if approach_speed >= self.min_approach_speed:
                            self._last_clap_time = now
                            self._waiting_separation = True
                            self._history.clear()
                            return {
                                "speed": round(approach_speed, 2),
                                "dist": round(dist, 3),
                                "max_dist": round(max_past_dist, 3),
                            }

        elif len(feats) == 1 and self._history:
            # Handle edge case: very fast collision momentarily causes MediaPipe
            # to detect only 1 combined hand at the point of contact
            if not self._waiting_separation and (now - self._last_clap_time) >= self.cooldown:
                last_t, last_d = self._history[-1]
                if (now - last_t) < 0.20 and last_d <= (self.contact_thresh * 1.4):
                    max_past_dist = max((d for _, d in self._history), default=0.0)
                    if max_past_dist >= self.separation_thresh:
                        self._last_clap_time = now
                        self._waiting_separation = True
                        self._history.clear()
                        return {
                            "speed": round(self.min_approach_speed, 2),
                            "dist": round(last_d, 3),
                            "max_dist": round(max_past_dist, 3),
                        }
            self._history.clear()
        else:
            self._history.clear()
            self._waiting_separation = False

        return None


# ── Recogniser states ────────────────────────────────────────────────────────

_STATE_IDLE   = "IDLE"
_STATE_ACTIVE = "ACTIVE"


# ── Main recogniser ──────────────────────────────────────────────────────────

class GestureRecognizer:
    """
    Feed HandFeatures objects each frame via update().

    Gesture shapes are detected directly from MediaPipe landmark geometry.

    Emitted event names:
      CLAP                – both hands collided (clap)
      ACTIVATE            – peace sign held 0.2s; action window opened
      open_or_expand      – open-palm inside active window
      close_or_collapse   – closed-fist inside active window
      SWIPE_LEFT          – swipe left inside active window
      SWIPE_RIGHT         – swipe right inside active window
      SWIPE_UP            – swipe up inside active window
      SWIPE_DOWN          – swipe down inside active window
      WINDOW_EXPIRED      – active window timed out with no action
    """

    def __init__(self):
        # Clap detector
        self._clap = _ClapDetector()

        # Hold trackers
        self._hs_activate = _HoldState(ACTIVATE_DWELL)
        self._hs_open     = _HoldState(HOLD_DWELL)
        self._hs_fist     = _HoldState(HOLD_DWELL)

        # Velocity / swipe
        self._vel = _VelocityTracker()
        self._swipe_cool: Dict[str, float] = {}

        # State
        self._state: str          = _STATE_IDLE
        self._window_start: float = 0.0
        self._last_fire: float    = 0.0
        self._lost_time: Optional[float] = None

        # Public read-only accessors for HUD
        self.last_shape: str  = "NONE"
        self.activate_progress: float = 0.0 # 0-1 while holding peace sign
        self.claw_progress: float = 0.0     # backwards compatibility alias
        self.window_remaining: float = 0.0  # seconds left in action window

        # While the air mouse is on, only a clap counts: pinching and pointing
        # would otherwise read as peace signs, fists and swipes.
        self.clap_only: bool = False

    # ── Shape prediction ────────────────────────────────────────────────────

    def _get_shape(self, feat: HandFeatures) -> str:
        # Rule-based detection
        if feat.is_open_palm():    return "OPEN_PALM"
        if feat.is_closed_fist():  return "CLOSED_FIST"
        if feat.is_swipe_shape():  return "SWIPE_SHAPE"
        return "NONE"


    # ── Helpers ─────────────────────────────────────────────────────────────

    def _global_cooled(self) -> bool:
        return (time.time() - self._last_fire) >= GLOBAL_COOLDOWN

    def _swipe_cooled(self, direction: str) -> bool:
        return (time.time() - self._swipe_cool.get(direction, 0.0)) >= SWIPE_COOLDOWN

    def _emit(self, name: str, extra: dict = None) -> GestureEvent:
        self._last_fire = time.time()
        self._vel.clear()
        return GestureEvent(name, extra)

    def _reset_to_idle(self):
        self._state = _STATE_IDLE
        self._hs_open.reset()
        self._hs_fist.reset()
        self._vel.clear()
        self.window_remaining = 0.0

    # ── Public API ──────────────────────────────────────────────────────────

    def update(self, feats: list,
               raw_lm_list=None) -> List[GestureEvent]:
        """
        Process one frame. Returns list of GestureEvent objects fired.
        """
        events: List[GestureEvent] = []

        # ── Two-Hand Clap Detection (Colliding Hands) ─────────────────────────
        clap_data = self._clap.update(feats)
        if clap_data is not None:
            if self._state == _STATE_ACTIVE:
                self._hs_activate.reset()
                self._reset_to_idle()
            events.append(self._emit("CLAP", clap_data))
            return events

        if self.clap_only:
            # Stay idle with nothing half-held, so gestures work straight away
            # once the air mouse is turned off.
            self._hs_activate.update(False)
            self._reset_to_idle()
            self._lost_time = None
            self.last_shape = "NONE"
            self.activate_progress = 0.0
            self.claw_progress = 0.0
            return events

        if not feats:
            self.last_shape = "NONE"
            self.activate_progress = 0.0
            self.claw_progress = 0.0

            # Grace period for hand loss in ACTIVE state to prevent transient drops (motion blur)
            # from instantly aborting a fast swipe.
            if self._state == _STATE_ACTIVE:
                if self._lost_time is None:
                    self._lost_time = time.time()
                
                elapsed_lost = time.time() - self._lost_time
                if elapsed_lost < 0.35:
                    # Keep active, decrement remaining window time
                    elapsed = time.time() - self._window_start
                    self.window_remaining = max(0.0, ACTION_WINDOW - elapsed)
                    if self.window_remaining <= 0.0:
                        events.append(self._emit("WINDOW_EXPIRED"))
                        self._hs_activate.reset()
                        self._reset_to_idle()
                        self._lost_time = None
                    return events

            self._hs_activate.reset()
            self._reset_to_idle()
            self._lost_time = None
            return events

        self._lost_time = None

        # ── IDLE STATE ──────────────────────────────────────────────────────
        if self._state == _STATE_IDLE:
            # Reset action-related trackers
            self._hs_open.reset()
            self._hs_fist.reset()
            self._vel.clear()
            self.window_remaining = 0.0

            # In IDLE, look for ANY hand making the Peace Sign
            shapes = [self._get_shape(feat) for feat in feats]
            
            # Find the hand making the activation shape (if any)
            active_feat = None
            for feat, shape in zip(feats, shapes):
                if shape == "SWIPE_SHAPE":
                    active_feat = feat
                    break
            
            is_activate_shape = (active_feat is not None)
            self.activate_progress = self._hs_activate.progress if is_activate_shape else 0.0
            self.claw_progress = self.activate_progress
            self.last_shape = "SWIPE_SHAPE" if is_activate_shape else (shapes[0] if shapes else "NONE")

            if self._hs_activate.update(is_activate_shape):
                # Shape held long enough → activate!
                self._state = _STATE_ACTIVE
                self._window_start = time.time()
                self.activate_progress = 0.0
                self.claw_progress = 0.0
                events.append(self._emit("ACTIVATE"))
                
                # Seed the velocity tracker with this activating hand's position to lock onto it
                self._vel.push(*active_feat.palm_centre_px)

            return events

        # ── ACTIVE STATE ────────────────────────────────────────────────────
        self.activate_progress = 0.0
        self.claw_progress = 0.0  # not tracking activation shape while active

        # Check if the action window has expired
        elapsed = time.time() - self._window_start
        self.window_remaining = max(0.0, ACTION_WINDOW - elapsed)

        if self.window_remaining <= 0.0:
            events.append(self._emit("WINDOW_EXPIRED"))
            self._hs_activate.reset()
            self._reset_to_idle()
            return events

        # 1. Spatial Tracking: Find the hand closest to the last known position of the active hand
        best_feat = feats[0]
        if len(self._vel._buf) > 0:
            _, last_x, last_y = self._vel._buf[-1]
            min_dist = float('inf')
            for f in feats:
                cx, cy = f.palm_centre_px
                dist = (cx - last_x)**2 + (cy - last_y)**2
                if dist < min_dist:
                    min_dist = dist
                    best_feat = f

        # Lock onto this hand's shape exclusively
        active_shape = self._get_shape(best_feat)
        self.last_shape = active_shape

        self._vel.push(*best_feat.palm_centre_px)

        # 2. Check for swipes first (using the locked hand's velocity)
        swipe_ev = self._check_swipes()
        if swipe_ev:
            events.extend(swipe_ev)
            self._hs_activate.reset()
            self._reset_to_idle()
            return events

        # 3. If the locked hand is moving fast, suppress static shape holds
        vx, vy = self._vel.velocity()
        is_moving_fast = math.hypot(vx, vy) > 200

        if not is_moving_fast:
            # --- OPEN PALM → open or expand ---
            if self._hs_open.update(active_shape == "OPEN_PALM"):
                events.append(self._emit("open_or_expand"))
                self._hs_activate.reset()
                self._reset_to_idle()
                return events

            # --- CLOSED FIST → close or collapse ---
            if self._hs_fist.update(active_shape == "CLOSED_FIST"):
                events.append(self._emit("close_or_collapse"))
                self._hs_activate.reset()
                self._reset_to_idle()
                return events
        else:
            # Reset holds if moving fast so they don't fire instantly when you stop
            self._hs_open.reset()
            self._hs_fist.reset()

        return events

    def _check_swipes(self) -> List[GestureEvent]:
        events: List[GestureEvent] = []
        vx, vy = self._vel.velocity()

        if abs(vx) > abs(vy):
            if vx < -SWIPE_VX_THRESH and self._swipe_cooled("LEFT"):
                self._swipe_cool["LEFT"] = time.time()
                events.append(self._emit("SWIPE_LEFT",  {"vx": vx}))
            elif vx > SWIPE_VX_THRESH and self._swipe_cooled("RIGHT"):
                self._swipe_cool["RIGHT"] = time.time()
                events.append(self._emit("SWIPE_RIGHT", {"vx": vx}))
        else:
            if vy < -SWIPE_VY_THRESH and self._swipe_cooled("UP"):
                self._swipe_cool["UP"] = time.time()
                events.append(self._emit("SWIPE_UP",   {"vy": vy}))
            elif vy > SWIPE_VY_THRESH and self._swipe_cooled("DOWN"):
                self._swipe_cool["DOWN"] = time.time()
                events.append(self._emit("SWIPE_DOWN", {"vy": vy}))

        return events
