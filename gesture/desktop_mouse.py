"""
desktop_mouse.py — Holographic Hand Overlay & Air Mouse for Helio.

Displays a transparent, click-through glowing hand skeleton directly on the
Windows desktop (and Helio Fullscreen UI), allowing hands to be used as an Air Mouse:
  • Move hand: Cursor tracks index fingertip, One Euro filtered (still = steady, fast = no lag).
  • Tap (Thumb + Index close then apart): Left Click!
  • Hold (Thumb + Index close and held/dragged): Grab & Drag (Left button held down).
  • Release (Thumb + Index apart after hold): Drop / release mouse button.
  • Transparent, always-on-top, click-through (clicks reach whatever app is underneath).
  • Inside Helio's workshop: BOTH hands point, click and drag (hold 1.0s),
    each with its own pointer, and the Windows cursor is left alone.
"""
import sys
import time
import math
import ctypes
import ctypes.wintypes
from typing import Optional, List, Tuple

from PyQt5.QtWidgets import QWidget, QApplication
from PyQt5.QtCore import Qt, QEvent, QPoint, QPointF, QRectF, QRect, QTimer
from PyQt5 import sip
from PyQt5.QtGui import (
    QPainter, QColor, QPen, QBrush, QRadialGradient, QLinearGradient,
    QFont, QPolygonF, QPainterPath, QMouseEvent
)
from collections import deque

try:
    from gesture.smoothing import HandSmoother
except (ImportError, ModuleNotFoundError):
    from smoothing import HandSmoother

# ── Win32 Mouse API ──────────────────────────────────────────────────────────
user32 = ctypes.windll.user32

# DPI Awareness to prevent coordinate mismatch on Windows scaling (125%, 150%)
try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except Exception:
    try:
        user32.SetProcessDPIAware()
    except Exception:
        pass

MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP   = 0x0004
MOUSEEVENTF_MOVE     = 0x0001

# A private handle on user32 so the argtypes set here don't leak into any other
# module that calls the same functions through ctypes.windll.user32.
_win = ctypes.WinDLL("user32")
_win.SetWindowPos.argtypes = [ctypes.wintypes.HWND, ctypes.wintypes.HWND,
                              ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                              ctypes.c_uint]
_win.SetWindowPos.restype = ctypes.wintypes.BOOL
_win.GetWindowLongW.argtypes = [ctypes.wintypes.HWND, ctypes.c_int]
_win.GetWindowLongW.restype = ctypes.c_long
_win.SetWindowLongW.argtypes = [ctypes.wintypes.HWND, ctypes.c_int, ctypes.c_long]
_win.SetWindowLongW.restype = ctypes.c_long

HWND_TOPMOST   = ctypes.wintypes.HWND(-1)
SWP_NOSIZE     = 0x0001
SWP_NOMOVE     = 0x0002
SWP_NOACTIVATE = 0x0010

# MediaPipe Hand Skeleton connections (21 joints)
HAND_CONNECTIONS = [
    (0, 1), (1, 2), (2, 3), (3, 4),        # Thumb
    (0, 5), (5, 6), (6, 7), (7, 8),        # Index
    (5, 9), (9, 10), (10, 11), (11, 12),   # Middle
    (9, 13), (13, 14), (14, 15), (15, 16), # Ring
    (13, 17), (17, 18), (18, 19), (19, 20),# Pinky
    (0, 17)                                # Wrist base
]


def make_window_click_through(hwnd: int):
    """Ensure the overlay is completely click-through to underlying Windows apps."""
    try:
        GWL_EXSTYLE = -20
        WS_EX_TRANSPARENT = 0x00000020
        WS_EX_LAYERED     = 0x00080000
        WS_EX_NOACTIVATE  = 0x08000000   # never take focus from the app underneath
        cur_style = _win.GetWindowLongW(hwnd, GWL_EXSTYLE)
        _win.SetWindowLongW(hwnd, GWL_EXSTYLE,
                            cur_style | WS_EX_TRANSPARENT | WS_EX_LAYERED | WS_EX_NOACTIVATE)
    except Exception as e:
        print(f"[DesktopHoloOverlay] Note: could not set WS_EX_TRANSPARENT: {e}")


def keep_window_on_top(hwnd: int):
    """
    Move the overlay to the top of the always-on-top band.

    WindowStaysOnTopHint alone is not enough: Helio's full screen is also an
    always-on-top window, and among those the one shown LAST wins — so opening
    the solar system after the air mouse put the hands behind it.
    """
    try:
        _win.SetWindowPos(hwnd, HWND_TOPMOST, 0, 0, 0, 0,
                          SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE)
    except Exception as e:
        print(f"[DesktopHoloOverlay] Note: could not raise overlay: {e}")


def _norm_dist(p1, p2) -> float:
    """Euclidean distance between two 2D normalized points."""
    return math.hypot(p1[0] - p2[0], p1[1] - p2[1])


class HandSlotManager:
    """
    Maintains Hand 1 (Primary / Air Mouse - Electric Cyan) and Hand 2 (Secondary - Emerald Green).

    Guarantees:
      1. Duplicate Suppression: while only one hand is tracked, two detections whose
         palm centres are closer than 0.12 (normalised) are the same hand; the
         smaller one is dropped.
      2. Single Hand on Screen: one hand is Slot 1 (Cyan) and drives the Air Mouse.
      3. Hand 2 Confirmation: a second hand is accepted once it has been seen in
         CONFIRM_FRAMES of the last CONFIRM_WINDOW frames. A one-frame false
         positive still never spawns Slot 2. (It used to need 2 frames IN A ROW;
         a real second hand that MediaPipe drops every other frame — at the edge
         of the view, in dim light, on a busy CPU — never qualified, so it never
         came up at all.)
      4. Two Hands on Screen: minimum-movement assignment, so slots never swap.
      5. Blink-out Grace: when one of two hands vanishes for up to GRACE_FRAMES,
         the one still visible keeps ITS OWN slot. It used to be forced into
         Slot 1 immediately, so a single dropped frame of the mouse hand
         teleported the cursor onto the other hand.
      6. Graceful Drop: after the grace, the remaining hand becomes Slot 1.
    """
    CONFIRM_WINDOW = 6
    CONFIRM_FRAMES = 2
    GRACE_FRAMES   = 6
    LOST_FRAMES    = 15

    def __init__(self):
        self.reset()

    def reset(self):
        self.slot1_pos: Optional[Tuple[float, float]] = None
        self.slot2_pos: Optional[Tuple[float, float]] = None
        self.slot2_candidate_frames: int = 0
        self.slot1_lost_frames: int = 0
        self.slot2_lost_frames: int = 0
        self._two_hand_recent = deque(maxlen=self.CONFIRM_WINDOW)
        self._since_both = self.GRACE_FRAMES + 1

    def _filter_duplicates(self, raw_feats: list) -> list:
        """Remove overlapping duplicate hand detections for the same physical hand."""
        if len(raw_feats) <= 1:
            return raw_feats

        # If Slot 2 is ALREADY active, user has 2 real hands on screen (or is clapping)!
        # NEVER filter them out — both hands are needed for clapping!
        if self.slot2_pos is not None:
            return raw_feats

        h0, h1 = raw_feats[0], raw_feats[1]
        dist = _norm_dist(h0.palm_centre_norm, h1.palm_centre_norm)
        if dist < 0.12:
            # Measure hand span from wrist (0) to middle MCP (9)
            span0 = math.hypot(h0.lms[9].x - h0.lms[0].x, h0.lms[9].y - h0.lms[0].y)
            span1 = math.hypot(h1.lms[9].x - h1.lms[0].x, h1.lms[9].y - h1.lms[0].y)
            return [h0] if span0 >= span1 else [h1]

        return raw_feats

    def update(self, raw_feats: list) -> Tuple[Optional[object], Optional[object]]:
        raw_feats = self._filter_duplicates(raw_feats)
        self._two_hand_recent.append(len(raw_feats) >= 2)
        self.slot2_candidate_frames = sum(self._two_hand_recent)

        if not raw_feats:
            self._since_both += 1
            self.slot1_lost_frames += 1
            self.slot2_lost_frames += 1
            if self.slot1_lost_frames > self.LOST_FRAMES:
                self.slot1_pos = None
            if self.slot2_lost_frames > self.LOST_FRAMES:
                self.slot2_pos = None
            return None, None

        # ── 1. Single Hand on Screen ─────────────────────────────────────────
        if len(raw_feats) == 1:
            feat = raw_feats[0]
            pos = feat.palm_centre_norm
            self._since_both += 1

            # Both hands were here a moment ago: the visible one keeps its slot.
            if (self.slot1_pos is not None and self.slot2_pos is not None
                    and self._since_both <= self.GRACE_FRAMES):
                if _norm_dist(pos, self.slot2_pos) < _norm_dist(pos, self.slot1_pos):
                    self.slot2_pos = pos
                    self.slot2_lost_frames = 0
                    self.slot1_lost_frames += 1
                    return None, feat
                self.slot1_pos = pos
                self.slot1_lost_frames = 0
                self.slot2_lost_frames += 1
                return feat, None

            # Otherwise one hand is the mouse, and Slot 2 is empty.
            self.slot2_pos = None
            self.slot2_lost_frames = self.LOST_FRAMES + 1
            self.slot1_pos = pos
            self.slot1_lost_frames = 0
            return feat, None

        # ── 2. Two (or more) Hands on Screen ─────────────────────────────────
        hA, hB = raw_feats[0], raw_feats[1]
        pA = hA.palm_centre_norm
        pB = hB.palm_centre_norm

        # Not confirmed yet: keep only the hand that is already the mouse.
        if self.slot2_pos is None and self.slot2_candidate_frames < self.CONFIRM_FRAMES:
            if self.slot1_pos is not None:
                s1 = hA if _norm_dist(pA, self.slot1_pos) <= _norm_dist(pB, self.slot1_pos) else hB
            else:
                s1 = hA
            self.slot1_pos = s1.palm_centre_norm
            self.slot1_lost_frames = 0
            return s1, None

        # Two confirmed distinct hands on screen!
        self._since_both = 0
        self.slot1_lost_frames = 0
        self.slot2_lost_frames = 0

        if self.slot1_pos is not None and self.slot2_pos is not None:
            # Both slots were already tracked: maintain continuity with min total movement
            cost_direct = _norm_dist(pA, self.slot1_pos) + _norm_dist(pB, self.slot2_pos)
            cost_cross  = _norm_dist(pB, self.slot1_pos) + _norm_dist(pA, self.slot2_pos)
            s1, s2 = (hA, hB) if cost_direct <= cost_cross else (hB, hA)
        elif self.slot1_pos is not None:
            # Hand 1 was already controlling mouse: match the hand closest to Hand 1
            s1, s2 = (hA, hB) if _norm_dist(pA, self.slot1_pos) <= _norm_dist(pB, self.slot1_pos) else (hB, hA)
        elif self.slot2_pos is not None:
            s2, s1 = (hA, hB) if _norm_dist(pA, self.slot2_pos) <= _norm_dist(pB, self.slot2_pos) else (hB, hA)
        else:
            # Both hands appeared simultaneously: order by screen X coordinate
            s1, s2 = (hA, hB) if pA[0] <= pB[0] else (hB, hA)

        self.slot1_pos = s1.palm_centre_norm
        self.slot2_pos = s2.palm_centre_norm
        return s1, s2


class PinchPointer:
    """
    One hand's pinch pointer: point with the index fingertip, tap thumb and
    index to click, hold the pinch to grab and drag.

    The gesture logic is shared; where the clicks go is not.
    AirMouseController drives the one Windows cursor. WorkshopPointer sends Qt
    mouse events into Helio's workshop, which is what lets two hands hold two
    things at once.

    Coordinates in and out are the overlay's logical pixels.
    """

    NAME = "POINTER"

    # While a pinch is pending the pointer holds still, so the fingers closing
    # and opening don't drag it off the thing being clicked. Moving further
    # than this is taken as meaning to move, and it follows again.
    FREEZE_RADIUS = 28.0

    # Hold the pinch this long to grab; let go sooner and it is a click.
    DRAG_DELAY_DEFAULT = 2.0

    def __init__(self, screen_w: int, screen_h: int):
        self.screen_w = screen_w
        self.screen_h = screen_h

        # Coordinate margins so user can reach all screen edges comfortably
        self.margin_x = 0.14
        self.margin_y = 0.16

        self.cursor_x = float(screen_w // 2)
        self.cursor_y = float(screen_h // 2)
        self.has_cursor = False

        self.DRAG_DELAY = self.DRAG_DELAY_DEFAULT

        self.state = "IDLE"        # "IDLE" | "PINCH_PENDING" | "DRAGGING"
        self.pinch_start_t = 0.0
        self.pinch_start_x = 0.0
        self.pinch_start_y = 0.0
        self._pending_moved = False
        self.is_mouse_down = False
        self.last_pinch_dist = 1.0
        self.lost_frames = 0

        # Click ripple events for visual feedback [(x, y, start_time)]
        self.ripples: List[Tuple[float, float, float]] = []

    # ── Where the pointer's actions go ───────────────────────────────────────

    def _move(self, x: float, y: float):
        """The pointer is at (x, y) this frame."""

    def _press(self, x: float, y: float):
        """Left button down at (x, y)."""

    def _release(self, x: float, y: float):
        """Left button up at (x, y)."""

    def _click(self, x: float, y: float):
        self._press(x, y)
        self._release(x, y)

    # ── Gesture logic ────────────────────────────────────────────────────────

    def reset(self):
        """Release any held button when tracking is lost or the mode changes."""
        if self.is_mouse_down:
            self._release(self.cursor_x, self.cursor_y)
            self.is_mouse_down = False
            print(f"[{self.NAME}] Reset -> Left button released")
        self.state = "IDLE"
        self.has_cursor = False

    def update(self, lms, target_x: float, target_y: float) -> Tuple[int, int, str]:
        """
        Process hand landmarks and index fingertip target screen position.
        Returns (cursor_x, cursor_y, state) in logical pixels.
        """
        now = time.time()

        idx_lm  = lms[8]
        thb_lm  = lms[4]
        wrist   = lms[0]
        mid_mcp = lms[9]

        # Calculate thumb ↔ index distance and palm scale for distance-invariance
        pinch_dist = math.hypot(idx_lm.x - thb_lm.x, idx_lm.y - thb_lm.y)
        hand_span  = math.hypot(mid_mcp.x - wrist.x, mid_mcp.y - wrist.y)
        pinch_ratio = pinch_dist / max(0.08, hand_span)
        self.last_pinch_dist = pinch_dist

        # Scale-invariant touch and release conditions
        is_touching = (pinch_dist < 0.058) or (pinch_ratio < 0.22)
        is_open     = (pinch_dist > 0.080) and (pinch_ratio > 0.30)
        # Generous hysteresis for drag release so moving hand does not drop prematurely
        is_drag_release = (pinch_dist > 0.115) or (pinch_ratio > 0.38)

        # Clamp target to screen bounds
        target_x = max(0.0, min(float(self.screen_w - 1), target_x))
        target_y = max(0.0, min(float(self.screen_h - 1), target_y))

        if not self.has_cursor:
            self.cursor_x = target_x
            self.cursor_y = target_y
            self.has_cursor = True

        # ── Pointer follow ────────────────────────────────────────────────────
        # The landmarks arrive One-Euro filtered, so the pointer can follow the
        # fingertip directly — a second smoothing stage here only added lag.
        frozen = self.state == "PINCH_PENDING" and not self._pending_moved
        if frozen and math.hypot(target_x - self.pinch_start_x,
                                 target_y - self.pinch_start_y) > self.FREEZE_RADIUS:
            self._pending_moved = True
            frozen = False
        if not frozen and math.hypot(target_x - self.cursor_x, target_y - self.cursor_y) >= 0.75:
            self.cursor_x = target_x
            self.cursor_y = target_y

        # ── State Machine: hold to Drag; release sooner to Click ──────────────
        if self.state == "IDLE":
            if is_touching:
                self.state = "PINCH_PENDING"
                self.pinch_start_t = now
                self.pinch_start_x = self.cursor_x
                self.pinch_start_y = self.cursor_y
                self._pending_moved = False

        elif self.state == "PINCH_PENDING":
            hold_time = now - self.pinch_start_t

            if is_open:
                # Released before the hold time -> ALWAYS A LEFT CLICK!
                self._click(self.cursor_x, self.cursor_y)
                print(f"[{self.NAME}] >> CLICK! << at ({int(self.cursor_x)}, {int(self.cursor_y)})")
                self.ripples.append((self.cursor_x, self.cursor_y, now))
                self.state = "IDLE"

            elif hold_time >= self.DRAG_DELAY:
                # Held long enough: deliberate grab.
                self._press(self.cursor_x, self.cursor_y)
                self.is_mouse_down = True
                self.state = "DRAGGING"
                print(f"[{self.NAME}] >> DRAG START (held {hold_time:.2f}s at "
                      f"{int(self.cursor_x)}, {int(self.cursor_y)}) <<")

        elif self.state == "DRAGGING":
            if is_drag_release:
                # Deliberate open fingers: DROP / RELEASE!
                self._release(self.cursor_x, self.cursor_y)
                self.is_mouse_down = False
                print(f"[{self.NAME}] >> DRAG DROP << at ({int(self.cursor_x)}, {int(self.cursor_y)})")
                self.ripples.append((self.cursor_x, self.cursor_y, now))
                self.state = "IDLE"

        self._move(self.cursor_x, self.cursor_y)

        # Cleanup old ripples (> 0.45s)
        self.ripples = [r for r in self.ripples if (now - r[2]) < 0.45]

        return int(round(self.cursor_x)), int(round(self.cursor_y)), self.state


class AirMouseController(PinchPointer):
    """
    Drives the Windows cursor.

    Coordinates are scaled to physical pixels only at SetCursorPos — the two
    differ whenever Windows display scaling is on and Qt's high-DPI scaling is
    enabled.
    """

    NAME = "AIR MOUSE"

    def __init__(self, screen_w: int, screen_h: int, phys_scale: Tuple[float, float] = (1.0, 1.0)):
        super().__init__(screen_w, screen_h)
        self.phys_sx, self.phys_sy = phys_scale

    def _set_cursor(self, x: float, y: float) -> Tuple[int, int]:
        px = int(round(x * self.phys_sx))
        py = int(round(y * self.phys_sy))
        user32.SetCursorPos(px, py)
        return px, py

    def _move(self, x, y):
        self._set_cursor(x, y)
        if self.is_mouse_down:
            user32.mouse_event(MOUSEEVENTF_MOVE, 0, 0, 0, 0)

    def _press(self, x, y):
        self._set_cursor(x, y)
        user32.mouse_event(MOUSEEVENTF_LEFTDOWN, 0, 0, 0, 0)

    def _release(self, x, y):
        user32.mouse_event(MOUSEEVENTF_LEFTUP, 0, 0, 0, 0)


class WorkshopPointer(PinchPointer):
    """
    One hand's own pointer inside Helio's workshop.

    There is only one Windows cursor, so two hands can't both hold something
    through it. Inside the workshop each hand instead sends its own Qt mouse
    events to the widget under its fingertip, and keeps sending to that widget
    until it lets go — the same implicit grab a real mouse button gets. The
    slabs' move and resize handlers read only the event's global position and
    buttons, so each hand can carry a different slab at the same time.
    """

    NAME = "WORKSHOP HAND"

    # On the workshop things get picked up and moved all the time; the 2 s
    # hold is for the desktop, where an accidental drag costs more.
    DRAG_DELAY_DEFAULT = 1.0

    def __init__(self, screen_w: int, screen_h: int):
        super().__init__(screen_w, screen_h)
        self.surface = None
        self._held = None

    @staticmethod
    def _alive(widget) -> bool:
        return widget is not None and not sip.isdeleted(widget)

    def _widget_at(self, x: float, y: float):
        surface = self.surface
        if not self._alive(surface) or not surface.isVisible():
            return None
        local = surface.mapFromGlobal(QPoint(int(round(x)), int(round(y))))
        if not surface.rect().contains(local):
            return None
        return surface.childAt(local) or surface

    def _send(self, widget, kind, x: float, y: float, button, buttons):
        if not self._alive(widget):
            return
        spot = QPoint(int(round(x)), int(round(y)))
        try:
            event = QMouseEvent(kind,
                                QPointF(widget.mapFromGlobal(spot)),
                                QPointF(widget.window().mapFromGlobal(spot)),
                                QPointF(spot),
                                button, buttons, Qt.NoModifier)
            # Goes through QApplication::notify, so a press on a label inside
            # a slab header propagates up to the header like a real one.
            QApplication.sendEvent(widget, event)
        except RuntimeError:
            self._held = None

    def _press(self, x, y):
        self._held = self._widget_at(x, y)
        self._send(self._held, QEvent.MouseButtonPress, x, y, Qt.LeftButton, Qt.LeftButton)

    def _move(self, x, y):
        if self.is_mouse_down and self._held is not None:
            self._send(self._held, QEvent.MouseMove, x, y, Qt.NoButton, Qt.LeftButton)

    def _release(self, x, y):
        held, self._held = self._held, None
        self._send(held, QEvent.MouseButtonRelease, x, y, Qt.LeftButton, Qt.NoButton)


class DesktopHoloOverlay(QWidget):
    """
    Frameless, transparent, click-through desktop overlay window that renders
    the glowing holographic hand skeleton and pointer reticles across the screen.

    On the desktop, Hand 1 drives the Windows cursor. While Helio's workshop is
    open, both hands get their own pointer inside it instead.
    """

    # The hologram is sized as if the camera were this many pixels wide, so it
    # comes out the same size whatever resolution the camera actually runs at.
    DRAW_REF_W = 1280.0
    # A hand that blinks out keeps being drawn in its last pose this long, so a
    # dropped detection doesn't flicker it off and on.
    HOLD_FRAMES = 5
    # A palm jump this big (normalised) means a different hand took the slot;
    # the filter restarts instead of gliding across the screen.
    SLOT_JUMP = 0.25
    TOP_GUARD_MS = 400
    # A workshop hand gone this many frames lets go of what it held.
    POINTER_LOST_FRAMES = 15
    # A lost workshop pointer that comes back this far away is the other hand
    # having taken its slot — it must not carry the first hand's slab over.
    POINTER_JUMP_PX = 250.0

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowFlags(
            Qt.FramelessWindowHint   |
            Qt.WindowStaysOnTopHint  |
            Qt.Tool                  |
            Qt.BypassWindowManagerHint
        )
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WA_ShowWithoutActivating, True)

        screen = QApplication.primaryScreen()
        geo = screen.geometry() if screen else None
        self.screen_w = geo.width() if geo else user32.GetSystemMetrics(0)
        self.screen_h = geo.height() if geo else user32.GetSystemMetrics(1)
        self.setGeometry(0, 0, self.screen_w, self.screen_h)

        # Qt paints in logical pixels once high-DPI scaling is on; SetCursorPos
        # always takes physical pixels. On a 125% display those differ by 1.25x.
        phys_w = user32.GetSystemMetrics(0) or self.screen_w
        phys_h = user32.GetSystemMetrics(1) or self.screen_h
        self.phys_sx = phys_w / float(max(1, self.screen_w))
        self.phys_sy = phys_h / float(max(1, self.screen_h))

        make_window_click_through(int(self.winId()))

        self.controller = AirMouseController(self.screen_w, self.screen_h,
                                             (self.phys_sx, self.phys_sy))
        # One pointer per hand, used only while the workshop is open.
        self._hand_pointers = {0: WorkshopPointer(self.screen_w, self.screen_h),
                               1: WorkshopPointer(self.screen_w, self.screen_h)}
        self._workshop = None

        # Hand render state: list of (slot_id, landmarks)
        self._hands_data: List[Tuple[int, list]] = []
        self._smoothers = {0: HandSmoother(), 1: HandSmoother()}
        self._last_px = {0: None, 1: None}
        self._last_mid = {0: None, 1: None}
        self._slot_missing = {0: 0, 1: 0}
        self._is_locked = False
        self._lock_rem = 0.0
        self._active_state = "IDLE"
        self._anim_t = 0.0
        self._missing_frames = 0
        self._last_dirty = QRect()

        # Reticle spin and click ripples between camera frames. Only the parts
        # of the screen that changed get repainted.
        self._timer = QTimer(self)
        self._timer.setInterval(33)
        self._timer.timeout.connect(self._tick)

        # Stay above Helio's full screen, which is also always-on-top.
        self._top_timer = QTimer(self)
        self._top_timer.setInterval(self.TOP_GUARD_MS)
        self._top_timer.timeout.connect(self.keep_on_top)
        app = QApplication.instance()
        if app is not None:
            app.focusWindowChanged.connect(self._on_focus_window_changed)

    def showEvent(self, event):
        super().showEvent(event)
        make_window_click_through(int(self.winId()))
        self.keep_on_top()
        self._timer.start()
        self._top_timer.start()

    def hideEvent(self, event):
        self.release_all()
        self._timer.stop()
        self._top_timer.stop()
        super().hideEvent(event)

    def release_all(self):
        """Let go of anything the cursor or either workshop hand is holding."""
        self.controller.reset()
        for pointer in self._hand_pointers.values():
            pointer.reset()
        self._active_state = "IDLE"

    def keep_on_top(self):
        """Put the hands back above every other always-on-top window."""
        if self.isVisible():
            keep_window_on_top(int(self.winId()))

    def _on_focus_window_changed(self, _window):
        try:
            if self.isVisible():
                QTimer.singleShot(0, self.keep_on_top)
        except RuntimeError:
            pass   # overlay already deleted

    def _pointers(self) -> list:
        """(pointer, hand index) for every pointer live in the current mode."""
        if self._workshop is None:
            return [(self.controller, 0)]
        return [(self._hand_pointers[0], 0), (self._hand_pointers[1], 1)]

    def _tick(self):
        self._anim_t += self._timer.interval() / 1000.0
        if self.isVisible() and any(p.has_cursor or p.ripples for p, _ in self._pointers()):
            self._refresh()

    # ── Dirty-region repaint ─────────────────────────────────────────────────

    HUD_W = 560

    def _hud_rect(self) -> QRectF:
        return QRectF((self.screen_w - self.HUD_W) // 2 - 4, 12, self.HUD_W + 8, 56)

    def _content_rect(self) -> QRect:
        rect = self._hud_rect()
        for _, lms_px in self._hands_data:
            xs = [p[0] for p in lms_px]
            ys = [p[1] for p in lms_px]
            span = math.hypot(lms_px[9][0] - lms_px[0][0], lms_px[9][1] - lms_px[0][1])
            pad = 190.0 * max(0.6, min(1.8, span / 160.0))
            rect = rect.united(QRectF(min(xs) - pad, min(ys) - pad,
                                      max(xs) - min(xs) + 2 * pad,
                                      max(ys) - min(ys) + 2 * pad))
        for pointer, _ in self._pointers():
            if pointer.has_cursor:
                cx, cy = pointer.cursor_x, pointer.cursor_y
                rect = rect.united(QRectF(cx - 40, cy - 40, 170, 80))
            for rx, ry, _ in pointer.ripples:
                rect = rect.united(QRectF(rx - 60, ry - 60, 120, 120))
        return rect.toAlignedRect().intersected(self.rect())

    def _refresh(self):
        rect = self._content_rect()
        self.update(rect.united(self._last_dirty))
        self._last_dirty = rect

    # ── Tracking ─────────────────────────────────────────────────────────────

    def _hand_to_screen(self, slot: int, feat) -> list:
        """Filtered landmarks for one hand, placed on screen in logical pixels."""
        raw_mid = feat.lms[9]
        last = self._last_mid[slot]
        if last is not None and math.hypot(raw_mid.x - last[0], raw_mid.y - last[1]) > self.SLOT_JUMP:
            self._smoothers[slot].reset()
        self._last_mid[slot] = (raw_mid.x, raw_mid.y)

        lms = self._smoothers[slot](feat.lms, getattr(feat, "timestamp", None))
        mid = lms[9]

        mx, my = self.controller.margin_x, self.controller.margin_y
        palm_sx = max(0.0, min(1.0, (mid.x - mx) / (1.0 - 2.0 * mx))) * self.screen_w
        palm_sy = max(0.0, min(1.0, (mid.y - my) / (1.0 - 2.0 * my))) * self.screen_h

        # Normalised offsets -> the size main.py draws at on a 1280px-wide
        # camera, in physical pixels, then back to logical for Qt.
        aspect = max(1, feat.frame_h) / float(max(1, feat.frame_w))
        kx = self.DRAW_REF_W / self.phys_sx
        ky = self.DRAW_REF_W * aspect / self.phys_sy
        return [(palm_sx + (lm.x - mid.x) * kx, palm_sy + (lm.y - mid.y) * ky) for lm in lms]

    def _set_workshop(self, surface):
        if surface is not None and sip.isdeleted(surface):
            surface = None
        if surface is self._workshop:
            return
        # Switching between the Windows cursor and the workshop hands: let go
        # of whatever either was holding so nothing is left pressed.
        self.release_all()
        for pointer in self._hand_pointers.values():
            pointer.surface = surface
        self._workshop = surface
        print(f"[DesktopHoloOverlay] {'Workshop: both hands point, hold 1s to drag' if surface is not None else 'Desktop: hand 1 drives the cursor'}")

    def update_tracking(self, slot1_feat, slot2_feat, is_locked: bool, lock_rem: float,
                        workshop=None):
        """
        Called every camera frame with persistent Hand 1 and Hand 2 slots.

        workshop: Helio's workshop widget while it is open. Then both hands get
        their own pointer inside it, and the Windows cursor is left alone.
        """
        self._is_locked = is_locked
        self._lock_rem = lock_rem
        self._set_workshop(workshop)

        feats = {0: slot1_feat, 1: slot2_feat}
        hands_list = []
        for slot, feat in feats.items():
            if feat is not None:
                px = self._hand_to_screen(slot, feat)
                self._last_px[slot] = px
                self._slot_missing[slot] = 0
                hands_list.append((slot, px))
                continue
            self._slot_missing[slot] += 1
            if self._last_px[slot] is not None and self._slot_missing[slot] <= self.HOLD_FRAMES:
                hands_list.append((slot, self._last_px[slot]))
            elif self._slot_missing[slot] > self.HOLD_FRAMES:
                self._last_px[slot] = None
                self._last_mid[slot] = None
                self._smoothers[slot].reset()

        if self._workshop is None:
            # Desktop: Hand 1 drives the mouse from its (filtered) index fingertip.
            if slot1_feat is not None:
                idx_screen_x, idx_screen_y = self._last_px[0][8]
                _, _, state = self.controller.update(slot1_feat.lms, idx_screen_x, idx_screen_y)
                self._active_state = state
                self._missing_frames = 0
            else:
                self._missing_frames += 1
                if self._missing_frames > 15:
                    self.controller.reset()
                    self._active_state = "IDLE"
        else:
            # Workshop: each hand has its own pointer.
            for slot, pointer in self._hand_pointers.items():
                feat = feats[slot]
                if feat is None:
                    pointer.lost_frames += 1
                    if pointer.lost_frames > self.POINTER_LOST_FRAMES:
                        pointer.reset()
                    continue
                tx, ty = self._last_px[slot][8]
                if (pointer.has_cursor and pointer.lost_frames >= 3 and
                        math.hypot(tx - pointer.cursor_x, ty - pointer.cursor_y) > self.POINTER_JUMP_PX):
                    pointer.reset()
                pointer.lost_frames = 0
                pointer.update(feat.lms, tx, ty)

        self._hands_data = hands_list
        self._refresh()

    def _draw_smooth_hologram_hand(self, painter: QPainter, lms_px: list, h_idx: int, now: float):
        """
        Renders a full organic glowing sci-fi hologram hand matching the user's reference image:
          - Full organic translucent glass/plasma hand body with smooth volumetric contours
          - Fresnel rim lighting with razor-sharp neon edge lines and ambient bloom
          - Transverse cybernetic cylindrical joint rings across finger segments
          - Anatomical palm crease lines (Life, Head, Heart lines) and center nexus ring
          - Wrist projection column and concentric circular emitter base
          - Floating ambient holographic bokeh motes
        """
        if len(lms_px) < 21:
            return

        wrist_pt = QPointF(lms_px[0][0], lms_px[0][1])
        mid_mcp  = QPointF(lms_px[9][0], lms_px[9][1])
        hand_span = math.hypot(mid_mcp.x() - wrist_pt.x(), mid_mcp.y() - wrist_pt.y())
        scale = max(0.6, min(1.8, hand_span / 160.0))

        # Vector along forearm/wrist downwards away from fingers
        arm_dx = wrist_pt.x() - mid_mcp.x()
        arm_dy = wrist_pt.y() - mid_mcp.y()
        arm_len = math.hypot(arm_dx, arm_dy)
        if arm_len > 1.0:
            arm_ux = arm_dx / arm_len
            arm_uy = arm_dy / arm_len
        else:
            arm_ux, arm_uy = 0.0, 1.0

        arm_nx = -arm_uy
        arm_ny = arm_ux

        # Color Palettes
        if h_idx == 0:
            # Electric Cyan / Neon Blue Hologram (Matching user reference image)
            c_glow_wide    = QColor(0, 140, 255, 32)
            c_body_plasma  = QColor(0, 190, 255, 60)
            c_body_core    = QColor(0, 230, 255, 120)
            c_rim_glow     = QColor(0, 170, 255, 80)
            c_rim_beam     = QColor(0, 240, 255, 230)
            c_rim_core     = QColor(220, 255, 255, 250)
            c_joint_ring   = QColor(0, 245, 255, 190)
            c_crease_line  = QColor(0, 225, 255, 130)
            c_emitter_base = QColor(0, 210, 255, 160)
            c_mote_glow    = QColor(0, 200, 255, 60)
            c_col_top      = QColor(0, 210, 255, 85)
            c_col_mid      = QColor(0, 160, 255, 40)
            c_col_bot      = QColor(0, 110, 255, 90)
        else:
            # Neon Emerald Hologram
            c_glow_wide    = QColor(30, 220, 100, 28)
            c_body_plasma  = QColor(50, 245, 130, 55)
            c_body_core    = QColor(120, 255, 170, 115)
            c_rim_glow     = QColor(40, 230, 110, 75)
            c_rim_beam     = QColor(80, 255, 150, 225)
            c_rim_core     = QColor(220, 255, 235, 250)
            c_joint_ring   = QColor(90, 255, 160, 185)
            c_crease_line  = QColor(70, 245, 140, 125)
            c_emitter_base = QColor(60, 240, 130, 155)
            c_mote_glow    = QColor(60, 240, 140, 60)
            c_col_top      = QColor(60, 240, 140, 85)
            c_col_mid      = QColor(40, 200, 110, 40)
            c_col_bot      = QColor(20, 160, 80, 90)

        painter.save()

        # ── 1. Wrist Hologram Projection Column & Emitter Base ────────────────
        col_len = 115.0 * scale
        col_w   = 36.0 * scale

        base_center = QPointF(wrist_pt.x() + arm_ux * col_len, wrist_pt.y() + arm_uy * col_len)
        w_l = QPointF(wrist_pt.x() + arm_nx * col_w, wrist_pt.y() + arm_ny * col_w)
        w_r = QPointF(wrist_pt.x() - arm_nx * col_w, wrist_pt.y() - arm_ny * col_w)
        b_l = QPointF(base_center.x() + arm_nx * (col_w * 1.12), base_center.y() + arm_ny * (col_w * 1.12))
        b_r = QPointF(base_center.x() - arm_nx * (col_w * 1.12), base_center.y() - arm_ny * (col_w * 1.12))

        col_poly = QPolygonF([w_l, b_l, b_r, w_r])

        col_grad = QLinearGradient(wrist_pt, base_center)
        col_grad.setColorAt(0.0, c_col_top)
        col_grad.setColorAt(0.55, c_col_mid)
        col_grad.setColorAt(1.0, c_col_bot)
        painter.setBrush(QBrush(col_grad))
        painter.setPen(Qt.NoPen)
        painter.drawPolygon(col_poly)

        # Outer column guide beams
        painter.setPen(QPen(c_rim_beam, 1.8 * scale, Qt.SolidLine, Qt.RoundCap))
        painter.drawLine(w_l, b_l)
        painter.drawLine(w_r, b_r)

        # Light filaments inside projection beam
        for fi in (-0.55, -0.25, 0.0, 0.25, 0.55):
            fl_top = QPointF(wrist_pt.x() + arm_nx * (col_w * fi), wrist_pt.y() + arm_ny * (col_w * fi))
            fl_bot = QPointF(base_center.x() + arm_nx * (col_w * 1.1 * fi), base_center.y() + arm_ny * (col_w * 1.1 * fi))
            f_pen = QPen(c_rim_glow, 1.0 * scale, Qt.DashLine)
            painter.setPen(f_pen)
            painter.drawLine(fl_top, fl_bot)

        # Circular emitter projector rings
        painter.setBrush(Qt.NoBrush)
        r_base_w = col_w * 1.8
        r_base_h = 14.0 * scale
        painter.setPen(QPen(QColor(c_emitter_base.red(), c_emitter_base.green(), c_emitter_base.blue(), 65), 4.0 * scale))
        painter.drawEllipse(base_center, r_base_w + 6, r_base_h + 3)
        painter.setPen(QPen(c_emitter_base, 2.2 * scale))
        painter.drawEllipse(base_center, r_base_w, r_base_h)
        painter.setPen(QPen(QColor(220, 255, 255, 230), 1.0 * scale))
        painter.drawEllipse(base_center, r_base_w * 0.55, r_base_h * 0.55)

        # ── 2. Full Organic Palm Energy Body ──────────────────────────────────
        palm_contour = [0, 1, 2, 5, 9, 13, 17]
        palm_poly = QPolygonF([QPointF(lms_px[i][0], lms_px[i][1]) for i in palm_contour])

        palm_cx = sum(lms_px[i][0] for i in [0, 5, 9, 13, 17]) / 5.0
        palm_cy = sum(lms_px[i][1] for i in [0, 5, 9, 13, 17]) / 5.0
        palm_center = QPointF(palm_cx, palm_cy)

        palm_grad = QRadialGradient(palm_center, 90.0 * scale)
        palm_grad.setColorAt(0.0, QColor(c_body_plasma.red(), c_body_plasma.green(), c_body_plasma.blue(), 85))
        palm_grad.setColorAt(0.55, QColor(c_body_plasma.red(), c_body_plasma.green(), c_body_plasma.blue(), 50))
        palm_grad.setColorAt(1.0, QColor(c_body_plasma.red(), c_body_plasma.green(), c_body_plasma.blue(), 20))
        painter.setBrush(QBrush(palm_grad))
        painter.setPen(Qt.NoPen)
        painter.drawPolygon(palm_poly)

        # ── 3. Smooth Volumetric Fingers (Layered Glow & Tapered Capsules) ────
        finger_chains = [
            ([1, 2, 3, 4],       [16, 14, 12, 10]),      # Thumb (starts at CMC 1)
            ([5, 6, 7, 8],       [15, 13.5, 11.5, 9.5]), # Index
            ([9, 10, 11, 12],    [16, 14.5, 12.5, 10]),  # Middle
            ([13, 14, 15, 16],   [15, 13.5, 11.5, 9.5]), # Ring
            ([17, 18, 19, 20],   [13, 11.5, 10, 8]),     # Pinky
        ]

        # Pass A: Broad outer ambient glow
        for indices, widths in finger_chains:
            path = QPainterPath()
            path.moveTo(lms_px[indices[0]][0], lms_px[indices[0]][1])
            for idx in indices[1:]:
                path.lineTo(lms_px[idx][0], lms_px[idx][1])
            painter.setBrush(Qt.NoBrush)
            painter.setPen(QPen(c_glow_wide, 24.0 * scale, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
            painter.drawPath(path)

        # Pass B: Translucent volumetric finger body
        for indices, widths in finger_chains:
            path = QPainterPath()
            path.moveTo(lms_px[indices[0]][0], lms_px[indices[0]][1])
            for idx in indices[1:]:
                path.lineTo(lms_px[idx][0], lms_px[idx][1])
            painter.setPen(QPen(c_body_plasma, 15.0 * scale, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
            painter.drawPath(path)

        # Pass C: Luminous inner core
        for indices, widths in finger_chains:
            path = QPainterPath()
            path.moveTo(lms_px[indices[0]][0], lms_px[indices[0]][1])
            for idx in indices[1:]:
                path.lineTo(lms_px[idx][0], lms_px[idx][1])
            painter.setPen(QPen(c_body_core, 6.5 * scale, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
            painter.drawPath(path)

        # ── 4. Fresnel Rim Light Contour (Outer Glass Silhouette) ─────────────
        for indices, widths in finger_chains:
            pts = [QPointF(lms_px[idx][0], lms_px[idx][1]) for idx in indices]
            left_edge = []
            right_edge = []
            for i in range(len(pts) - 1):
                pa, pb = pts[i], pts[i + 1]
                wa = widths[i] * scale * 0.52
                wb = widths[i + 1] * scale * 0.52
                dx = pb.x() - pa.x()
                dy = pb.y() - pa.y()
                sl = math.hypot(dx, dy)
                if sl < 1.0:
                    continue
                nx = -dy / sl
                ny = dx / sl
                if i == 0:
                    left_edge.append(QPointF(pa.x() + nx * wa, pa.y() + ny * wa))
                    right_edge.append(QPointF(pa.x() - nx * wa, pa.y() - ny * wa))
                left_edge.append(QPointF(pb.x() + nx * wb, pb.y() + ny * wb))
                right_edge.append(QPointF(pb.x() - nx * wb, pb.y() - ny * wb))

            tip = pts[-1]
            tip_w = widths[-1] * scale * 0.52
            tangent = pts[-1] - pts[-2]
            tl = math.hypot(tangent.x(), tangent.y())
            tx = (tangent.x() / tl) if tl > 0 else 0
            ty = (tangent.y() / tl) if tl > 0 else 1

            f_rim = QPainterPath()
            f_rim.moveTo(left_edge[0])
            for p in left_edge[1:]:
                f_rim.lineTo(p)
            tip_peak = QPointF(tip.x() + tx * tip_w * 0.9, tip.y() + ty * tip_w * 0.9)
            f_rim.quadTo(tip_peak, right_edge[-1])
            for p in reversed(right_edge[:-1]):
                f_rim.lineTo(p)

            painter.setPen(QPen(c_rim_glow, 4.5 * scale, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
            painter.drawPath(f_rim)
            painter.setPen(QPen(c_rim_beam, 1.8 * scale, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
            painter.drawPath(f_rim)
            painter.setPen(QPen(c_rim_core, 0.8 * scale, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
            painter.drawPath(f_rim)

        # Palm lateral borders (wrist -> thumb CMC, and pinky MCP -> wrist)
        painter.setPen(QPen(c_rim_beam, 2.0 * scale, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        painter.drawLine(w_l, QPointF(lms_px[1][0], lms_px[1][1]))
        painter.drawLine(QPointF(lms_px[17][0], lms_px[17][1]), w_r)

        # Subtle central bone conduit filaments
        for indices, widths in finger_chains:
            path = QPainterPath()
            path.moveTo(lms_px[indices[0]][0], lms_px[indices[0]][1])
            for idx in indices[1:]:
                path.lineTo(lms_px[idx][0], lms_px[idx][1])
            painter.setPen(QPen(QColor(220, 255, 255, 140), 1.0 * scale, Qt.SolidLine, Qt.RoundCap))
            painter.drawPath(path)

        # ── 5. Transverse Holographic Cylindrical Joint Rings ─────────────────
        ring_indices = [
            # Thumb
            (1, 2, 0.5, 15.0), (2, 3, 0.5, 13.0), (3, 4, 0.45, 11.0),
            # Index
            (5, 6, 0.45, 13.5), (6, 7, 0.5, 11.5), (7, 8, 0.45, 9.5),
            # Middle
            (9, 10, 0.45, 14.5), (10, 11, 0.5, 12.5), (11, 12, 0.45, 10.0),
            # Ring
            (13, 14, 0.45, 13.5), (14, 15, 0.5, 11.5), (15, 16, 0.45, 9.5),
            # Pinky
            (17, 18, 0.45, 11.5), (18, 19, 0.5, 10.0), (19, 20, 0.45, 8.0),
        ]

        for idx_a, idx_b, t_frac, rw in ring_indices:
            pa = QPointF(lms_px[idx_a][0], lms_px[idx_a][1])
            pb = QPointF(lms_px[idx_b][0], lms_px[idx_b][1])
            mx = pa.x() + (pb.x() - pa.x()) * t_frac
            my = pa.y() + (pb.y() - pa.y()) * t_frac
            dx = pb.x() - pa.x()
            dy = pb.y() - pa.y()
            sl = math.hypot(dx, dy)
            if sl < 1.0:
                continue
            nx = -dy / sl
            ny = dx / sl
            tx = dx / sl
            ty = dy / sl

            half_w = rw * scale * 0.55
            p_left  = QPointF(mx + nx * half_w, my + ny * half_w)
            p_right = QPointF(mx - nx * half_w, my - ny * half_w)
            arc_ctrl = QPointF(mx - tx * (half_w * 0.35), my - ty * (half_w * 0.35))

            ring_path = QPainterPath()
            ring_path.moveTo(p_left)
            ring_path.quadTo(arc_ctrl, p_right)

            painter.setPen(QPen(c_joint_ring, 1.4 * scale, Qt.SolidLine, Qt.RoundCap))
            painter.drawPath(ring_path)
            painter.setPen(QPen(c_rim_core, 0.6 * scale, Qt.SolidLine, Qt.RoundCap))
            painter.drawPath(ring_path)

        # ── 6. Anatomical Palm Creases (Life line, Head line, Heart line) ──────
        p_web = QPointF((lms_px[2][0] + lms_px[5][0]) * 0.5, (lms_px[2][1] + lms_px[5][1]) * 0.5)
        p_mid = QPointF(palm_cx - 15 * scale, palm_cy + 10 * scale)
        p_wrist_in = QPointF(wrist_pt.x() - 10 * scale, wrist_pt.y() - 8 * scale)

        life_path = QPainterPath()
        life_path.moveTo(p_web)
        life_path.quadTo(p_mid, p_wrist_in)
        painter.setPen(QPen(c_crease_line, 1.2 * scale, Qt.SolidLine, Qt.RoundCap))
        painter.drawPath(life_path)

        p_head_end = QPointF(palm_cx + 35 * scale, palm_cy + 15 * scale)
        head_path = QPainterPath()
        head_path.moveTo(p_web)
        head_path.quadTo(QPointF(palm_cx, palm_cy - 5 * scale), p_head_end)
        painter.drawPath(head_path)

        p_heart_start = QPointF(lms_px[17][0] - 8 * scale, lms_px[17][1] + 15 * scale)
        p_heart_end   = QPointF((lms_px[5][0] + lms_px[9][0]) * 0.5, (lms_px[5][1] + lms_px[9][1]) * 0.5 + 18 * scale)
        heart_path = QPainterPath()
        heart_path.moveTo(p_heart_start)
        heart_path.quadTo(QPointF(palm_cx + 10 * scale, palm_cy - 15 * scale), p_heart_end)
        painter.drawPath(heart_path)

        # Palm center nexus emitter ring
        nexus_r = 12.0 * scale
        painter.setPen(QPen(c_joint_ring, 1.4 * scale, Qt.DashLine))
        painter.drawEllipse(palm_center, nexus_r, nexus_r)
        painter.setPen(QPen(c_rim_core, 0.8 * scale))
        painter.drawEllipse(palm_center, 3.0 * scale, 3.0 * scale)

        # ── 7. Floating Holographic Bokeh / Dust Motes ────────────────────────
        mote_offsets = [
            (-65, -40, 0.8), (75, -60, 1.2), (-80, 50, 1.0), (85, 40, 0.7),
            (-40, 130, 1.1), (50, 110, 0.9), (-90, -100, 0.6), (95, -90, 1.3)
        ]
        for ox, oy, r in mote_offsets:
            m_x = palm_cx + ox * scale + math.sin(now * 2.0 + ox) * 4.0
            m_y = palm_cy + oy * scale + math.cos(now * 2.0 + oy) * 4.0
            painter.setPen(Qt.NoPen)
            painter.setBrush(QBrush(c_mote_glow))
            painter.drawEllipse(QPointF(m_x, m_y), (r + 2.5) * scale, (r + 2.5) * scale)
            painter.setBrush(QBrush(QColor(220, 255, 255, 220)))
            painter.drawEllipse(QPointF(m_x, m_y), r * scale, r * scale)

        painter.restore()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)

        now = time.time()
        in_workshop = self._workshop is not None
        pointers = self._pointers()

        # ── 1. Top HUD Status Banner ──────────────────────────────────────────
        bw, bh = self.HUD_W, 48
        bx = (self.screen_w - bw) // 2
        by = 16

        # Translucent dark HUD backing
        hud_bg = QColor(10, 16, 26, 195)
        painter.setBrush(QBrush(hud_bg))
        painter.setPen(QPen(QColor(0, 220, 255, 140), 1.5))
        painter.drawRoundedRect(QRectF(bx, by, bw, bh), 10, 10)

        # HUD Title
        painter.setFont(QFont("Consolas", 11, QFont.Bold))
        painter.setPen(QColor(0, 240, 255, 240))
        title = ("✦ HELIO WORKSHOP · BOTH HANDS ✦" if in_workshop
                 else "✦ HELIO HOLOGRAPHIC AIR MOUSE ✦")
        painter.drawText(QRectF(bx, by + 4, bw, 22), Qt.AlignCenter, title)

        # HUD Subtext
        hint = f"Tap: Click  |  Hold {pointers[0][0].DRAG_DELAY:.1f}s: Drag"
        if in_workshop:
            hint = "Either hand  |  " + hint
        painter.setFont(QFont("Segoe UI", 9, QFont.Normal))
        if self._is_locked:
            painter.setPen(QColor(255, 175, 40, 230))
            painter.drawText(QRectF(bx, by + 24, bw, 20), Qt.AlignCenter,
                             f"CLAP LOCKED: {self._lock_rem:.1f}s  |  {hint}")
        else:
            painter.setPen(QColor(60, 240, 130, 230))
            painter.drawText(QRectF(bx, by + 24, bw, 20), Qt.AlignCenter,
                             f"Gestures paused · Clap to close  |  {hint}")

        # ── 2. Render Smooth Hologram Hands (No bottom grid) ──────────────────
        for h_idx, lms_px in self._hands_data:
            self._draw_smooth_hologram_hand(painter, lms_px, h_idx, now)

        # ── 3. Pointer Reticles at Index Tips ─────────────────────────────────
        for pointer, hand in pointers:
            if pointer.has_cursor:
                label = f"HAND {hand + 1}" if in_workshop else "POINTER"
                self._draw_pointer(painter, pointer, hand, label, now)

        # ── 4. Click Ripples (Expands on Tap / Click) ──────────────────────────
        for pointer, _ in pointers:
            for rx, ry, rt in pointer.ripples:
                dt = now - rt
                pct = min(1.0, dt / 0.35)
                rip_r = 12.0 + pct * 34.0
                rip_a = int(255 * (1.0 - pct))
                painter.setPen(QPen(QColor(0, 255, 170, rip_a), 2.0))
                painter.setBrush(Qt.NoBrush)
                painter.drawEllipse(QPointF(rx, ry), rip_r, rip_r)

                # Small click text flash
                painter.setFont(QFont("Consolas", 9, QFont.Bold))
                painter.setPen(QColor(255, 255, 255, rip_a))
                painter.drawText(QRectF(rx - 25, ry - 30 - pct * 10, 50, 20), Qt.AlignCenter, "CLICK")

        painter.end()

    def _draw_pointer(self, painter: QPainter, pointer, hand: int, label: str, now: float):
        """One pointer's reticle, hold-progress arc and state badge."""
        cx = pointer.cursor_x
        cy = pointer.cursor_y
        state = pointer.state
        # Idle colour matches the hand: cyan for Hand 1, emerald for Hand 2.
        idle = (0, 220, 255) if hand == 0 else (80, 255, 150)

        if state == "DRAGGING":
            ring_color = QColor(255, 175, 20, 240)   # Gold / Amber for Grab
            badge_text = "DRAGGING"
            badge_color = QColor(255, 175, 20)
            r_base = 18.0
        elif state == "PINCH_PENDING":
            ring_color = QColor(0, 255, 170, 240)   # Bright Cyan/Mint
            hold_elapsed = max(0.0, now - pointer.pinch_start_t)
            pct = min(1.0, hold_elapsed / pointer.DRAG_DELAY)
            badge_text = f"HOLD {int(pct*100)}%" if pct < 1.0 else "GRAB READY"
            badge_color = QColor(0, 255, 170) if pct < 0.75 else QColor(255, 175, 20)
            r_base = 16.0
        else:
            ring_color = QColor(*idle, 210)
            badge_text = label
            badge_color = QColor(*idle)
            r_base = 16.0

        # Outer rotating dashed reticle
        rot_angle = (self._anim_t * 90.0) % 360.0
        painter.save()
        painter.translate(cx, cy)
        painter.rotate(rot_angle)

        painter.setPen(QPen(ring_color, 1.8, Qt.DashLine))
        painter.setBrush(Qt.NoBrush)
        painter.drawEllipse(QPointF(0, 0), r_base, r_base)

        # 4 Tick marks
        t_len = 5.0
        for a in (0, 90, 180, 270):
            rad_a = math.radians(a)
            x1 = math.cos(rad_a) * (r_base + 3.0)
            y1 = math.sin(rad_a) * (r_base + 3.0)
            x2 = math.cos(rad_a) * (r_base + 3.0 + t_len)
            y2 = math.sin(rad_a) * (r_base + 3.0 + t_len)
            painter.drawLine(QPointF(x1, y1), QPointF(x2, y2))

        painter.restore()

        # Charging progress arc while pinching, full when the grab is ready
        if state == "PINCH_PENDING":
            hold_elapsed = max(0.0, now - pointer.pinch_start_t)
            pct = min(1.0, hold_elapsed / pointer.DRAG_DELAY)
            arc_span = int(pct * 360.0 * 16.0)
            painter.setPen(QPen(QColor(255, 200, 30, 230), 2.5))
            painter.setBrush(Qt.NoBrush)
            painter.drawArc(QRectF(cx - r_base - 5, cy - r_base - 5, (r_base + 5) * 2, (r_base + 5) * 2), 90 * 16, -arc_span)

        # Center target pip
        painter.setBrush(QBrush(QColor(255, 255, 255, 240)))
        painter.setPen(QPen(ring_color, 1.0))
        painter.drawEllipse(QPointF(cx, cy), 3.0, 3.0)

        # Floating State Pill
        painter.setFont(QFont("Consolas", 8, QFont.Bold))
        painter.setPen(badge_color)
        painter.drawText(QRectF(cx + 24, cy - 8, 95, 18), Qt.AlignLeft | Qt.AlignVCenter, badge_text)
