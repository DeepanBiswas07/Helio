"""
reflection_service.py — Helio thinking about what it has seen.

Pattern mining has to happen somewhere, and the two obvious places are both
wrong. Doing it on every request adds work to the one path that must stay
fast; doing it never means nothing is ever learned. So it runs on a slow timer,
and only while nothing else is happening.

It deliberately does not speak. An assistant that interrupts to announce it has
noticed you open two apps together is irritating within a day. What it learns
shows up in the MEMORY planet and answers "what have you learned about me" —
pull, not push. The one exception is a genuinely useful offer, which is queued
and surfaced quietly rather than spoken over you.
"""
import time

from PyQt5.QtCore import QObject, QTimer, pyqtSignal

try:
    from memory import pattern_miner
    from memory import observed_memory as observed
    from memory import observation_log as log
except ImportError:   # src/ not on the path — degrade quietly
    pattern_miner = None
    observed = None
    log = None

# Long on purpose. Patterns form over days; checking every few minutes would
# burn a model call to rediscover the same thing.
FIRST_PASS_MS = 90_000          # once, shortly after startup
INTERVAL_MS = 30 * 60 * 1000    # then every half hour

# Don't mine while the user is mid-conversation — the model call would compete
# with the one they're waiting on.
BUSY_STATES = ("listening", "thinking", "speaking")


class ReflectionService(QObject):
    """Mines the behaviour log for patterns, quietly."""

    learned = pyqtSignal(list)        # newly written observation records
    suggestion = pyqtSignal(dict)     # an inference worth offering as a routine

    def __init__(self, parent=None, is_busy=None):
        super().__init__(parent)
        # A callable returning True when Helio is mid-interaction. Injected so
        # this service doesn't need to know about orb state.
        self._is_busy = is_busy or (lambda: False)
        self._last_run = 0.0
        self._offered = set()

        self._timer = QTimer(self)
        self._timer.setInterval(INTERVAL_MS)
        self._timer.timeout.connect(self._tick)

    def start(self):
        if pattern_miner is None:
            print("[Reflection] Pattern mining unavailable.")
            return
        self._timer.start()
        QTimer.singleShot(FIRST_PASS_MS, self._tick)
        state = pattern_miner.readiness()
        print("[Reflection] Watching. {} interactions over {} days "
              "(need {} over {}).".format(
                  state["entries"], state["days"],
                  state["needed_entries"], state["needed_days"]))

    def stop(self):
        self._timer.stop()

    def readiness(self):
        return pattern_miner.readiness() if pattern_miner else {"ready": False}

    def observations(self):
        return observed.list_observations() if observed else []

    def run_now(self, use_model=True):
        """Mine immediately. Used by the MEMORY panel's refresh."""
        return self._mine(use_model=use_model, force=True)

    # ── internals ─────────────────────────────────────────────────────────
    def _tick(self):
        if self._is_busy():
            return
        self._mine(use_model=True, force=False)

    def _mine(self, use_model=True, force=False):
        if pattern_miner is None:
            return []

        # Nothing new to look at — mining the same entries again would only
        # spend a model call to reach the same conclusion.
        if not force and log and log.count() < 5:
            return []

        # Retire facts whose stated end date has passed, before mining. A
        # lapsed fact that is still being asserted would otherwise sit in the
        # prompt until the user happened to ask about it.
        try:
            from memory.semantic_memory import sweep_expired_memories
            for record in sweep_expired_memories():
                print(f"[Memory] Out of date, updated: {record['text']}")
        except Exception as e:
            print(f"[Reflection] Expiry sweep failed: {e}")

        try:
            written = pattern_miner.mine(use_model=use_model)
        except Exception as e:
            print(f"[Reflection] Mining failed: {e}")
            return []

        self._last_run = time.time()
        if not written:
            return []

        fresh = [r for r in written if r.get("times_seen", 1) == 1]
        if fresh:
            print("[Reflection] Noticed {} new thing(s): {}".format(
                len(fresh), "; ".join(r["fact"][:60] for r in fresh)))
            self.learned.emit(fresh)

        # A repeated app pairing is the one inference worth acting on: it maps
        # directly onto a SETUP assembly the user can accept in one click.
        for record in written:
            steps = record.get("suggest_assembly")
            if not steps or record["id"] in self._offered:
                continue
            if record.get("confidence", 0) < 0.6:
                continue
            self._offered.add(record["id"])
            self.suggestion.emit({
                "observation_id": record["id"],
                "fact": record["fact"],
                "steps": steps,
            })

        return written
