"""
smoothing.py  —  Jitter filtering for hand landmarks.

MediaPipe landmarks tremble by a few thousandths of the frame even when the
hand is perfectly still. Mapped onto a 1920px screen that is several pixels of
shake, and a plain moving average only trades that shake for lag.

The One Euro filter (Casiez et al., CHI 2012) adapts its cutoff to speed: a
still hand is smoothed hard, a moving hand barely at all. It also works from
real timestamps, so frames that arrive late or in bursts — which they do when
the Helio UI is busy — don't turn into jumps.
"""
import math
import time
from typing import List, Optional, Sequence


class OneEuro:
    """One Euro filter for a single scalar."""

    def __init__(self, min_cutoff: float = 1.0, beta: float = 0.0, d_cutoff: float = 1.0):
        self.min_cutoff = min_cutoff
        self.beta = beta
        self.d_cutoff = d_cutoff
        self._x: Optional[float] = None
        self._dx = 0.0
        self._t: Optional[float] = None

    @staticmethod
    def _alpha(cutoff: float, dt: float) -> float:
        tau = 1.0 / (2.0 * math.pi * cutoff)
        return 1.0 / (1.0 + tau / dt)

    def reset(self):
        self._x = None
        self._dx = 0.0
        self._t = None

    def __call__(self, x: float, t: float) -> float:
        if self._x is None or self._t is None:
            self._x, self._t = x, t
            return x
        dt = t - self._t
        if dt <= 1e-6:
            return self._x
        # A long gap means the hand was gone; don't glide in from the old pose.
        if dt > 0.5:
            self.reset()
            self._x, self._t = x, t
            return x
        dx = (x - self._x) / dt
        a_d = self._alpha(self.d_cutoff, dt)
        self._dx = a_d * dx + (1.0 - a_d) * self._dx
        cutoff = self.min_cutoff + self.beta * abs(self._dx)
        a = self._alpha(cutoff, dt)
        self._x = a * x + (1.0 - a) * self._x
        self._t = t
        return self._x


class SmoothLandmark:
    """Stand-in for a MediaPipe landmark: just x, y, z."""
    __slots__ = ("x", "y", "z")

    def __init__(self, x: float, y: float, z: float = 0.0):
        self.x = x
        self.y = y
        self.z = z


class HandSmoother:
    """
    One Euro filter over all 21 landmarks of one hand, in normalised units.

    Tuned for pointing: min_cutoff low enough that a resting fingertip stops
    shaking, beta high enough that a quick move is followed without lag.
    """

    # Tuned end-to-end against the old EMA + stepping cursor at 18 fps with
    # MediaPipe-like noise: resting shake halves (2.3 -> 1.2 px/frame), slow
    # moves track as closely as before, and a fast move lags a third as much
    # (51 -> 15 px). A lower beta steadies rest further but drags on fast moves.
    MIN_CUTOFF = 0.3
    BETA = 25.0
    D_CUTOFF = 1.0

    def __init__(self):
        self._fx: List[OneEuro] = []
        self._fy: List[OneEuro] = []
        self._fz: List[OneEuro] = []
        self._make()

    def _make(self):
        mk = lambda: OneEuro(self.MIN_CUTOFF, self.BETA, self.D_CUTOFF)
        self._fx = [mk() for _ in range(21)]
        self._fy = [mk() for _ in range(21)]
        self._fz = [mk() for _ in range(21)]

    def reset(self):
        for f in self._fx + self._fy + self._fz:
            f.reset()

    def __call__(self, lms: Sequence, t: Optional[float] = None) -> List[SmoothLandmark]:
        t = time.monotonic() if t is None else t
        return [
            SmoothLandmark(self._fx[i](lm.x, t), self._fy[i](lm.y, t),
                           self._fz[i](getattr(lm, "z", 0.0), t))
            for i, lm in enumerate(lms[:21])
        ]
