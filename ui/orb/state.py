"""
state.py — AI state machine for the Helio orb.
Call set_state() from anywhere to transition between states.
"""
from orb.config import STATE_PROFILES


class OrbState:
    VALID = ("idle", "hover", "listening", "thinking", "speaking")

    def __init__(self):
        self._state   = "idle"
        self._profile = STATE_PROFILES["idle"]
        self._listeners = []          # callbacks: fn(new_state, profile)

    # ── Public API ────────────────────────────

    @property
    def name(self) -> str:
        return self._state

    @property
    def ring_speed(self) -> float:
        return self._profile["ring_speed"]

    @property
    def glow(self) -> float:
        return self._profile["glow"]

    @property
    def ring_alpha(self) -> float:
        return self._profile["ring_alpha"]

    @property
    def pulse_speed(self) -> float:
        return self._profile["pulse_speed"]

    def set(self, state: str):
        if state not in self.VALID:
            raise ValueError(f"Unknown state '{state}'. Valid: {self.VALID}")
        if state == self._state:
            return
        self._state   = state
        self._profile = STATE_PROFILES[state]
        for fn in self._listeners:
            fn(state, self._profile)

    def on_change(self, callback):
        """Register a callback: fn(state_name, profile_dict)"""
        self._listeners.append(callback)

    def __repr__(self):
        return f"<OrbState '{self._state}'>"


# Singleton — import and share across modules
state = OrbState()
