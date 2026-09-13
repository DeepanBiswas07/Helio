"""
setup_panel.py — The SETUP planet: Helio's workshop.

Not a list of saved items with a form behind a tab. A bench with two bays:

  BAY 01 · ASSEMBLIES   the things you've built, each drawn as a wired
                        schematic — nodes on a bus with callout leaders — and
                        energised along the bus while it runs.
  BAY 02 · FABRICATION  the bench itself: a live schematic of what you're
                        assembling, the step editor under it, and a parts bin.

Only one bay is on screen at a time; they are stations, not tabs.

The backend already existed and had no UI: save_workflow / get_workflow /
list_workflows / delete_workflow in memory.semantic_memory, driven by the
workflow tools. This panel is the front of it.

Steps run through the agent, so a step is a natural-language instruction, not a
button — which is why every node carries its kind in its silhouette (a solid
hexagon fires instantly, a hollow circle goes through the agent).
"""
import math
import time

from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton,
    QFrame, QScrollArea, QSizePolicy
)
from PyQt5.QtCore import Qt, QThread, QObject, QTimer, QRect, QPointF, pyqtSignal
from PyQt5.QtGui import QColor, QPainter, QPen, QPolygonF, QRegion, QFontMetrics

from .base import BasePanel, _content_alpha, _draw_header
from . import setup_style as S

ACCENT = "#96E146"

# Steps Helio can start instantly with no model call, versus steps that go
# through the agent. The distinction is worth showing per-node: it's the
# difference between an assembly that fires in a blink and one that takes a
# few seconds a step.
_LAUNCH_VERBS = ("open ", "launch ", "start ", "run ", "play ")

# The parts bin. Chosen to span what an assembly can actually do — launch
# something, ask something, look at something, set something.
PARTS = [
    "open ",
    "what's my CPU usage",
    "what reminders do I have",
    "what's on my screen",
    "set a timer for 25 minutes",
]

# Offered when the rack is empty. Each mixes a launcher with a step only the
# agent can do, which is the point of an assembly over a shortcut folder.
BLUEPRINTS = [
    ("work setup", "start a coding session",
     ["open vscode", "open chrome", "what's my CPU usage"]),
    ("wind down", "end of day",
     ["what reminders do I have", "open spotify"]),
    ("focus block", "25 minutes, no distractions",
     ["set a timer for 25 minutes", "open vscode"]),
]

MIN_COL_W = 148       # a callout narrower than this can't hold a readable label
LINE_H = 14           # 8pt Consolas needs this much box, or descenders clip
LEADER_H = 14
LABEL_H = LINE_H * 2
STATUS_H = LINE_H
ROW_H = LEADER_H + LABEL_H + STATUS_H + 16


def _step_kind(text):
    """LAUNCH (instant, no model) or ASK (goes through the agent)."""
    lowered = (text or "").strip().lower()
    return "LAUNCH" if lowered.startswith(_LAUNCH_VERBS) else "ASK"


# ── shared painters ──────────────────────────────────────────────────────────

def _registration(p, rect, color, arm=7):
    """Machined crosses at the corners — the bench's registration marks."""
    p.setPen(QPen(color, 1))
    for x, y in ((rect.left(), rect.top()), (rect.right(), rect.top()),
                 (rect.left(), rect.bottom()), (rect.right(), rect.bottom())):
        p.drawLine(x - arm, y, x + arm, y)
        p.drawLine(x, y - arm, x, y + arm)


def _tick_rule(p, x0, x1, y, color, every=9, height=3):
    """A hairline with fabrication ticks, instead of a plain divider."""
    p.setPen(QPen(color, 1))
    p.drawLine(x0, y, x1, y)
    x = x0
    while x < x1:
        p.drawLine(int(x), y, int(x), y - height)
        x += every


def _chain_layout(count, width):
    """Wrap N nodes onto as many bus rows as the width allows."""
    if count <= 0:
        return 1, 0
    cols = max(1, min(count, int(width // MIN_COL_W) or 1))
    rows = int(math.ceil(count / float(cols)))
    return cols, rows


def _draw_node(p, cx, cy, kind, state, radius=5.5):
    """
    Solid hexagon = a launch step (instant). Hollow circle = an agent step.
    Two silhouettes, readable at a glance without reading a word.
    """
    if state == "running":
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(150, 225, 70, 55))
        p.drawEllipse(QPointF(cx, cy), radius * 2.4, radius * 2.4)

    if state == "ok":
        edge = fill = S.C_ACCENT
    elif state == "failed":
        edge = fill = S.C_WARN
    elif state == "running":
        edge, fill = S.C_ACCENT, S.C_BRIGHT
    else:
        edge, fill = S.C_ACCENT_D, (S.C_ACCENT_D if kind == "LAUNCH" else None)

    p.setPen(QPen(edge, 1.4))
    p.setBrush(fill if fill else Qt.NoBrush)

    if kind == "LAUNCH":
        hexagon = QPolygonF([
            QPointF(cx + radius * math.cos(math.radians(a)),
                    cy + radius * math.sin(math.radians(a)))
            for a in range(0, 360, 60)
        ])
        p.drawPolygon(hexagon)
    else:
        p.drawEllipse(QPointF(cx, cy), radius, radius)


def _elide_two_lines(metrics, text, width):
    """Wrap into at most two lines, eliding the tail of the second."""
    words = text.split()
    lines, current = [], ""
    for word in words:
        trial = (current + " " + word).strip()
        if metrics.width(trial) <= width or not current:
            current = trial
        else:
            lines.append(current)
            current = word
            if len(lines) == 2:
                break
    if current and len(lines) < 2:
        lines.append(current)
    if len(lines) == 2 and metrics.width(lines[1]) > width:
        lines[1] = metrics.elidedText(lines[1], Qt.ElideRight, width)
    consumed = len(" ".join(lines))
    if consumed < len(text) and lines:
        lines[-1] = metrics.elidedText(lines[-1] + "…", Qt.ElideRight, width)
    return lines[:2]


def _paint_chain(p, rect, steps, states=None, details=None, dim=False):
    """
    Draw an assembly: nodes on a bus, leaders down to callouts.
    `states` is a per-step "idle|running|ok|failed"; `details` a per-step note.
    """
    if not steps:
        return
    states = states or ["idle"] * len(steps)
    details = details or [None] * len(steps)

    cols, rows = _chain_layout(len(steps), rect.width())
    col_w = rect.width() / float(cols)
    label_metrics = QFontMetrics(S.mono_font(8))
    kind_font = S.label_font(7, tracking=1.4)

    for index, step in enumerate(steps):
        row, col = divmod(index, cols)
        cx = rect.left() + col_w * (col + 0.5)
        bus_y = rect.top() + row * ROW_H + 8

        state = states[index]
        kind = _step_kind(step)

        # ── the bus segment into this node ────────────────────────────────
        if col > 0:
            prev_cx = rect.left() + col_w * (col - 0.5)
            energised = states[index - 1] in ("ok", "failed", "running")
            p.setPen(QPen(S.C_ACCENT if energised and not dim else S.C_WIRE,
                          1.5 if energised else 1.0))
            p.drawLine(int(prev_cx + 8), int(bus_y), int(cx - 8), int(bus_y))
        else:
            # Row terminator bracket, so a wrapped row reads as a continuation.
            p.setPen(QPen(S.C_WIRE, 1.0))
            p.drawLine(int(cx - 14), int(bus_y), int(cx - 8), int(bus_y))
            p.drawLine(int(cx - 14), int(bus_y - 4), int(cx - 14), int(bus_y + 4))

        _draw_node(p, cx, bus_y, kind, state)

        # ── callout leader ────────────────────────────────────────────────
        p.setPen(QPen(S.C_WIRE, 1))
        p.drawLine(int(cx), int(bus_y + 7), int(cx), int(bus_y + LEADER_H))

        # ── label ─────────────────────────────────────────────────────────
        text_w = int(col_w - 18)
        lines = _elide_two_lines(label_metrics, step, text_w)
        p.setFont(S.mono_font(8))
        p.setPen(QPen(S.C_BRIGHT if state == "running" else S.C_TEXT, 1))
        y = bus_y + LEADER_H + 2
        for line in lines:
            p.drawText(int(cx - text_w / 2), int(y), text_w, LINE_H,
                       Qt.AlignHCenter | Qt.AlignTop, line)
            y += LINE_H

        # ── status / kind ─────────────────────────────────────────────────
        note = details[index]
        p.setFont(S.mono_font(8) if note else kind_font)
        if state == "running":
            p.setPen(QPen(S.C_ACCENT, 1))
            note = note or "running…"
        elif state == "failed":
            p.setPen(QPen(S.C_WARN, 1))
            note = note or "failed"
        elif state == "ok":
            p.setPen(QPen(S.C_MUTED, 1))
            note = note or "done"
        else:
            p.setPen(QPen(QColor(78, 115, 44), 1))
            note = kind
        note = label_metrics.elidedText(note, Qt.ElideRight, text_w)
        p.drawText(int(cx - text_w / 2), int(bus_y + LEADER_H + LABEL_H + 3),
                   text_w, LINE_H, Qt.AlignHCenter | Qt.AlignTop, note)


def _chain_height(count, width):
    _cols, rows = _chain_layout(count, width)
    return rows * ROW_H + 10


# ── backend access ───────────────────────────────────────────────────────────
# Lazy and guarded, matching files.py: the panel must still load if src/ isn't
# on the path yet.

def _memory_backend():
    try:
        from memory.semantic_memory import (
            list_workflows, save_workflow, delete_workflow,
        )
        return list_workflows, save_workflow, delete_workflow
    except Exception:
        return None, None, None


def _reminder_backend():
    try:
        from memory.semantic_memory import list_reminders
        return list_reminders
    except Exception:
        return None


def _relative(ts):
    if not ts:
        return "never"
    delta = max(0, time.time() - ts)
    if delta < 90:
        return "just now"
    if delta < 3600:
        return f"{int(delta // 60)}m ago"
    if delta < 86400:
        return f"{int(delta // 3600)}h ago"
    return f"{int(delta // 86400)}d ago"


def _until(ts):
    delta = ts - time.time()
    if delta <= 0:
        return "due"
    if delta < 60:
        return f"in {int(delta)}s"
    if delta < 3600:
        return f"in {int(round(delta / 60))}m"
    return f"in {delta / 3600:.1f}h"


# ── worker ───────────────────────────────────────────────────────────────────

class _RunWorker(QObject):
    """
    Runs an assembly's steps one at a time on a background thread.

    Steps are executed here rather than through run_workflow so the bench can
    energise each node as it fires — an assembly with agent steps can take tens
    of seconds, and a frozen panel with no feedback would be worse than useless.
    """
    step_started = pyqtSignal(int)
    step_done = pyqtSignal(int, bool, str)
    finished_run = pyqtSignal(str)

    def __init__(self, name, steps):
        super().__init__()
        self.name = name
        self.steps = list(steps)
        self._cancelled = False

    def cancel(self):
        self._cancelled = True

    def run(self):
        try:
            from tools.memory_ops.workflow_tool import run_step
            from memory.semantic_memory import touch_workflow
        except Exception as e:
            self.finished_run.emit(f"Backend unavailable: {e}")
            return

        for index, step in enumerate(self.steps):
            if self._cancelled:
                self.finished_run.emit("Cancelled.")
                return

            self.step_started.emit(index)
            try:
                transcript = run_step(step)
            except Exception as e:
                self.step_done.emit(index, False, str(e))
                continue

            ok = not transcript.startswith("✗")
            detail = transcript.split("→", 1)[-1].strip() if "→" in transcript else ""
            self.step_done.emit(index, ok, detail)

        try:
            touch_workflow(self.name)
        except Exception:
            pass

        self.finished_run.emit("")


# ── the painter-only planet panel ────────────────────────────────────────────

class SetupPanel(BasePanel):
    def __init__(self):
        super().__init__("SETUP", QColor(150, 225, 70))

    def draw(self, painter, shape_rect, panel_progress, alpha):
        # Header only — SetupWidget paints the body.
        content_a = _content_alpha(panel_progress, alpha)
        _draw_header(painter, shape_rect, self.title, self.accent, content_a)


# ── one assembly on the rack ─────────────────────────────────────────────────

class _Assembly(QWidget):
    """A saved assembly, drawn as a schematic rather than listed as a row."""

    run_requested = pyqtSignal(str)
    edit_requested = pyqtSignal(str)
    delete_requested = pyqtSignal(str)

    HEAD_H = 66

    def __init__(self, name, data, ordinal, parent=None):
        super().__init__(parent)
        self.name = name
        self.ordinal = ordinal
        self.steps = data.get("steps", [])
        self.purpose = (data.get("purpose") or "").strip()
        self.run_count = data.get("run_count", 0) or 0
        self.last_run = data.get("last_run")
        self.states = ["idle"] * len(self.steps)
        self.details = [None] * len(self.steps)
        self.running = False
        self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)

        box = QVBoxLayout(self)
        box.setContentsMargins(20, 12, 18, 0)
        box.setSpacing(0)

        commands = QHBoxLayout()
        commands.setSpacing(4)
        commands.addStretch()

        self._run_btn = QPushButton("▶  RUN")
        self._run_btn.setFont(S.label_font(10, bold=True, tracking=2.0))
        self._run_btn.setCursor(Qt.PointingHandCursor)
        self._run_btn.setStyleSheet(S.COMMAND)
        self._run_btn.clicked.connect(lambda: self.run_requested.emit(self.name))
        commands.addWidget(self._run_btn)

        for text, signal, style in (
            ("REWIRE", self.edit_requested, S.ENGRAVED),
            ("SCRAP", self.delete_requested, S.ENGRAVED_WARN),
        ):
            button = QPushButton(text)
            button.setFont(S.label_font(8, tracking=1.7))
            button.setCursor(Qt.PointingHandCursor)
            button.setStyleSheet(style)
            button.clicked.connect(lambda _c=False, s=signal: s.emit(self.name))
            commands.addWidget(button)

        box.addLayout(commands)
        box.addStretch(1)

    def sizeHint_height(self, width):
        return self.HEAD_H + _chain_height(len(self.steps), width - 60) + 14

    def relayout(self, width):
        self.setFixedHeight(self.sizeHint_height(width))

    # ── run state ─────────────────────────────────────────────────────────
    def mark_running(self):
        self.running = True
        self.states = ["idle"] * len(self.steps)
        self.details = [None] * len(self.steps)
        self._run_btn.setText("■  RUNNING")
        self._run_btn.setEnabled(False)
        self.update()

    def mark_idle(self):
        self.running = False
        self._run_btn.setText("▶  RUN")
        self._run_btn.setEnabled(True)
        self.update()

    def step_started(self, index):
        if 0 <= index < len(self.states):
            self.states[index] = "running"
            self.update()

    def step_done(self, index, ok, detail):
        if 0 <= index < len(self.states):
            self.states[index] = "ok" if ok else "failed"
            self.details[index] = (detail or "").replace("\n", " ") or None
            self.update()

    # ── paint ─────────────────────────────────────────────────────────────
    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        w = self.width()

        # ── designator rule ───────────────────────────────────────────────
        p.setFont(S.mono_font(8))
        designator = f"ASM {self.ordinal:02d}"
        p.setPen(QPen(S.C_ACCENT_D if not self.running else S.C_ACCENT, 1))
        p.drawText(20, 10, 60, 12, Qt.AlignLeft | Qt.AlignVCenter, designator)
        _tick_rule(p, 66, w - 210, 16, QColor(36, 53, 21))

        # ── name ──────────────────────────────────────────────────────────
        p.setFont(S.label_font(13, bold=True, tracking=2.6))
        p.setPen(QPen(S.C_BRIGHT, 1))
        p.drawText(20, 20, w - 220, 20, Qt.AlignLeft | Qt.AlignVCenter, self.name)

        # ── spec line ─────────────────────────────────────────────────────
        launches = sum(1 for s in self.steps if _step_kind(s) == "LAUNCH")
        spec = "{} step{}   ·   {} instant   ·   {} run{}   ·   last {}".format(
            len(self.steps), "" if len(self.steps) == 1 else "s",
            launches, self.run_count, "" if self.run_count == 1 else "s",
            _relative(self.last_run))
        if self.purpose:
            spec = self.purpose + "   ·   " + spec
        p.setFont(S.mono_font(8))
        p.setPen(QPen(S.C_DIM, 1))
        p.drawText(20, 39, w - 220, 12, Qt.AlignLeft | Qt.AlignVCenter, spec)

        # ── the schematic ─────────────────────────────────────────────────
        chain = QRect(30, self.HEAD_H, w - 60,
                      _chain_height(len(self.steps), w - 60))
        _paint_chain(p, chain, self.steps, self.states, self.details)

        # A running assembly gets a lit left margin — the bay is powered.
        if self.running:
            p.setPen(QPen(S.C_ACCENT, 2))
            p.drawLine(4, 8, 4, self.height() - 8)
        p.end()


# ── one step on the fabrication bench ────────────────────────────────────────

class _StepEditor(QWidget):
    """An editable step: designator, field, live kind readout, reorder, remove."""

    moved = pyqtSignal(object, int)
    removed = pyqtSignal(object)
    changed = pyqtSignal()

    def __init__(self, text="", parent=None):
        super().__init__(parent)
        self.setFixedHeight(28)

        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(8)

        self._node = QLabel("01")
        self._node.setFont(S.mono_font(8))
        self._node.setFixedWidth(24)
        self._node.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self._node.setStyleSheet(f"color:{S.ACCENT_D};{S.TRANSPARENT_LABEL}")
        row.addWidget(self._node)

        self.edit = QLineEdit(text)
        self.edit.setFont(S.mono_font(10))
        self.edit.setStyleSheet(S.INPUT)
        self.edit.setPlaceholderText("say it the way you'd say it out loud…")
        self.edit.textChanged.connect(self._sync_kind)
        self.edit.textChanged.connect(self.changed.emit)
        row.addWidget(self.edit, 1)

        self._kind = QLabel("")
        self._kind.setFont(S.label_font(7, tracking=1.5))
        self._kind.setFixedWidth(54)
        self._kind.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        row.addWidget(self._kind)

        for glyph, delta in (("▲", -1), ("▼", 1)):
            button = QPushButton(glyph)
            button.setFont(S.mono_font(7))
            button.setFixedWidth(17)
            button.setCursor(Qt.PointingHandCursor)
            button.setStyleSheet(S.ENGRAVED)
            button.clicked.connect(
                lambda _c=False, d=delta: self.moved.emit(self, d))
            row.addWidget(button)

        remove = QPushButton("✕")
        remove.setFont(S.mono_font(9))
        remove.setFixedWidth(20)
        remove.setCursor(Qt.PointingHandCursor)
        remove.setStyleSheet(S.ENGRAVED_WARN)
        remove.clicked.connect(lambda: self.removed.emit(self))
        row.addWidget(remove)

        self._sync_kind()

    def _sync_kind(self):
        text = self.edit.text().strip()
        if not text:
            self._kind.setText("")
            return
        kind = _step_kind(text)
        self._kind.setText(kind)
        self._kind.setStyleSheet(
            f"color:{S.ACCENT_D if kind == 'LAUNCH' else S.DIM};"
            f"{S.TRANSPARENT_LABEL}")

    def set_number(self, index):
        self._node.setText(f"{index + 1:02d}")

    def text(self):
        return self.edit.text().strip()


# ── the live schematic on the bench ──────────────────────────────────────────

class _BenchPreview(QWidget):
    """
    The assembly taking shape, drawn the moment you type it. This is the piece
    that makes fabrication feel like building an object rather than filling in
    a form.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.steps = []
        self.setMinimumHeight(96)
        self._phase = 0.0

        # A slow sweep across the bench, so an idle workshop still reads as
        # powered rather than as a static screenshot.
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(60)

    def _tick(self):
        self._phase = (self._phase + 0.006) % 1.6
        if self.isVisible():
            self.update()

    def set_steps(self, steps):
        self.steps = [s for s in steps if s]
        height = 42 + _chain_height(max(1, len(self.steps)), max(1, self.width() - 40))
        self.setMinimumHeight(max(96, height))
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = self.rect().adjusted(0, 0, -1, -1)

        # Bench: a fine grid, not a flat fill.
        p.fillRect(r, QColor(8, 13, 7, 120))
        p.setPen(QPen(S.C_GRID, 1))
        for x in range(r.left(), r.right(), 16):
            p.drawLine(x, r.top(), x, r.bottom())
        for y in range(r.top(), r.bottom(), 16):
            p.drawLine(r.left(), y, r.right(), y)

        p.setPen(QPen(QColor(36, 53, 21), 1))
        p.drawRect(r)
        _registration(p, r, QColor(124, 187, 52, 150))

        p.setFont(S.label_font(7, tracking=2.0))
        p.setPen(QPen(S.C_DIM, 1))
        p.drawText(r.left() + 10, r.top() + 6, 200, 12,
                   Qt.AlignLeft | Qt.AlignVCenter, "SCHEMATIC · LIVE")

        if not self.steps:
            p.setFont(S.mono_font(9))
            p.setPen(QPen(QColor(78, 115, 44), 1))
            p.drawText(r, Qt.AlignCenter,
                       "nothing on the bench yet — add a step below")
            p.end()
            return

        chain = QRect(r.left() + 20, r.top() + 34, r.width() - 40,
                      _chain_height(len(self.steps), r.width() - 40))
        _paint_chain(p, chain, self.steps)

        # Sweep: a soft vertical line travelling across the bench.
        x = r.left() + (r.width() + 60) * min(1.0, self._phase) - 30
        if r.left() <= x <= r.right():
            p.setPen(QPen(QColor(150, 225, 70, 30), 2))
            p.drawLine(int(x), r.top() + 1, int(x), r.bottom() - 1)
        p.end()


# ── the interactive panel ────────────────────────────────────────────────────

class SetupWidget(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WA_TranslucentBackground)

        self._workflows = {}
        self._assemblies = {}
        self._bay = "assemblies"
        self._steps = []
        self._editing = None
        self._run_thread = None
        self._run_worker = None
        self._running_name = None

        self._build_ui()
        self.refresh()

    # ── construction ──────────────────────────────────────────────────────
    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(20, 20, 20, 14)
        root.setSpacing(0)

        # ── bay selector ──────────────────────────────────────────────────
        bays = QHBoxLayout()
        bays.setSpacing(8)
        bays.setContentsMargins(0, 0, 0, 10)

        self._bay_run = self._make_bay("01", "ASSEMBLIES", True)
        self._bay_fab = self._make_bay("02", "FABRICATION", False)
        self._bay_run.clicked.connect(lambda: self._set_bay("assemblies"))
        self._bay_fab.clicked.connect(lambda: self._set_bay("fabrication"))
        bays.addWidget(self._bay_run)
        bays.addWidget(self._bay_fab)
        bays.addStretch()

        self._status = QLabel("")
        self._status.setFont(S.mono_font(9))
        self._status.setStyleSheet(f"color:{S.MUTED};{S.TRANSPARENT_LABEL}")
        self._status.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        bays.addWidget(self._status)
        root.addLayout(bays)

        self._build_rack(root)
        self._build_bench(root)

        # ── active timers strip ───────────────────────────────────────────
        # Reminders are tools, not a planet — this is only a readout, and the
        # workshop is where "what has Helio been told to do" belongs.
        root.addWidget(self._rule(S.RULE))
        self._timers = QLabel("")
        self._timers.setFont(S.mono_font(9))
        self._timers.setStyleSheet(f"color:{S.DIM};{S.TRANSPARENT_LABEL}")
        self._timers.setContentsMargins(0, 6, 0, 0)
        root.addWidget(self._timers)

    def _build_rack(self, root):
        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._scroll.setStyleSheet(S.SCROLL)
        self._scroll.setAttribute(Qt.WA_TranslucentBackground)
        self._scroll.viewport().setAttribute(Qt.WA_TranslucentBackground)

        self._rack = QWidget()
        self._rack.setStyleSheet(f"background:{S.BENCH_GROUND};")
        self._rack_layout = QVBoxLayout(self._rack)
        self._rack_layout.setContentsMargins(0, 0, 0, 0)
        self._rack_layout.setSpacing(0)
        self._rack_layout.addStretch()
        self._scroll.setWidget(self._rack)
        root.addWidget(self._scroll, 1)

    def _build_bench(self, root):
        """
        The fabrication bay. Shown *instead of* the rack, never beneath it — a
        bench with the shelf stacked under it is two screens fighting for one
        space.
        """
        self._bench = QWidget()
        self._bench.setStyleSheet(f"background:{S.BENCH_GROUND};")
        bench = QVBoxLayout(self._bench)
        bench.setContentsMargins(20, 16, 20, 14)
        bench.setSpacing(0)

        self._bench_title = QLabel("NEW ASSEMBLY")
        self._bench_title.setFont(S.label_font(12, bold=True, tracking=2.8))
        self._bench_title.setStyleSheet(f"color:{S.ACCENT};{S.TRANSPARENT_LABEL}")
        bench.addWidget(self._bench_title)
        bench.addSpacing(10)

        ident = QHBoxLayout()
        ident.setSpacing(10)
        ident.addWidget(self._caption("DESIGNATION"))
        self._name_edit = QLineEdit()
        self._name_edit.setFont(S.mono_font(10))
        self._name_edit.setStyleSheet(S.INPUT)
        self._name_edit.setPlaceholderText("work setup")
        ident.addWidget(self._name_edit, 1)
        ident.addSpacing(16)
        ident.addWidget(self._caption("PURPOSE"))
        self._purpose_edit = QLineEdit()
        self._purpose_edit.setFont(S.mono_font(10))
        self._purpose_edit.setStyleSheet(S.INPUT)
        self._purpose_edit.setPlaceholderText("optional")
        ident.addWidget(self._purpose_edit, 1)
        bench.addLayout(ident)
        bench.addSpacing(14)

        self._preview = _BenchPreview()
        bench.addWidget(self._preview)
        bench.addSpacing(14)

        self._steps_box = QVBoxLayout()
        self._steps_box.setSpacing(3)
        bench.addLayout(self._steps_box)

        add = QPushButton("+  ADD STEP")
        add.setFont(S.label_font(9, tracking=1.8))
        add.setCursor(Qt.PointingHandCursor)
        add.setStyleSheet(S.ENGRAVED)
        add_row = QHBoxLayout()
        add_row.setContentsMargins(24, 4, 0, 0)
        add.clicked.connect(lambda: self._add_step(""))
        add_row.addWidget(add)
        add_row.addStretch()
        bench.addLayout(add_row)

        bench.addStretch(1)

        bench.addWidget(self._caption("PARTS BIN"))
        bench.addSpacing(6)
        bin_row = QHBoxLayout()
        bin_row.setSpacing(6)
        for text in PARTS:
            part = QPushButton(text.strip() + ("…" if text.endswith(" ") else ""))
            part.setFont(S.mono_font(8))
            part.setCursor(Qt.PointingHandCursor)
            part.setStyleSheet(S.PART)
            part.clicked.connect(
                lambda _c=False, t=text: self._add_step(t, focus=True))
            bin_row.addWidget(part)
        bin_row.addStretch()
        bench.addLayout(bin_row)
        bench.addSpacing(14)

        footer = QHBoxLayout()
        footer.setSpacing(10)
        self._bench_hint = QLabel("")
        self._bench_hint.setFont(S.mono_font(8))
        self._bench_hint.setStyleSheet(f"color:{S.DIM};{S.TRANSPARENT_LABEL}")
        footer.addWidget(self._bench_hint)
        footer.addStretch()

        discard = QPushButton("CLEAR BENCH")
        discard.setFont(S.label_font(9, tracking=1.8))
        discard.setCursor(Qt.PointingHandCursor)
        discard.setStyleSheet(S.ENGRAVED)
        discard.clicked.connect(self._discard)
        footer.addWidget(discard)

        commit = QPushButton("COMMIT TO RACK")
        commit.setFont(S.label_font(10, bold=True, tracking=2.0))
        commit.setCursor(Qt.PointingHandCursor)
        commit.setStyleSheet(S.COMMAND)
        commit.clicked.connect(self._commit)
        footer.addWidget(commit)
        bench.addLayout(footer)

        root.addWidget(self._bench, 1)
        self._bench.hide()

    def _make_bay(self, index, name, active):
        button = QPushButton(f"{index}   {name}")
        button.setFont(S.label_font(10, tracking=2.2))
        button.setCursor(Qt.PointingHandCursor)
        button.setStyleSheet(S.BAY_ACTIVE if active else S.BAY_IDLE)
        return button

    def _caption(self, text):
        label = QLabel(text)
        label.setFont(S.label_font(8, tracking=2.2))
        label.setStyleSheet(f"color:{S.DIM};{S.TRANSPARENT_LABEL}")
        return label

    def _rule(self, color):
        line = QFrame()
        line.setFixedHeight(1)
        line.setStyleSheet(f"background:{color};border:none;")
        return line

    # ── bays ──────────────────────────────────────────────────────────────
    def _set_bay(self, bay):
        """The two bays are exclusive: exactly one is ever on screen."""
        self._bay = bay
        self._bay_run.setStyleSheet(
            S.BAY_ACTIVE if bay == "assemblies" else S.BAY_IDLE)
        self._bay_fab.setStyleSheet(
            S.BAY_ACTIVE if bay == "fabrication" else S.BAY_IDLE)

        self._scroll.setVisible(bay == "assemblies")
        self._bench.setVisible(bay == "fabrication")

        if bay == "fabrication":
            if not self._steps:
                self._add_step("")
            self._sync_bench()
        else:
            self.refresh()
        self.update()

    # ── bench steps ───────────────────────────────────────────────────────
    def _add_step(self, text="", focus=False):
        editor = _StepEditor(text)
        editor.moved.connect(self._move_step)
        editor.removed.connect(self._remove_step)
        editor.changed.connect(self._sync_bench)
        editor.edit.returnPressed.connect(lambda: self._add_step("", focus=True))
        self._steps.append(editor)
        self._steps_box.addWidget(editor)
        self._renumber()
        if focus:
            editor.edit.setFocus()
            editor.edit.setCursorPosition(len(editor.edit.text()))
        self._sync_bench()

    def _remove_step(self, editor):
        if editor not in self._steps:
            return
        self._steps.remove(editor)
        self._steps_box.removeWidget(editor)
        editor.setParent(None)
        editor.deleteLater()
        if not self._steps:
            self._add_step("")
        self._renumber()
        self._sync_bench()

    def _move_step(self, editor, delta):
        if editor not in self._steps:
            return
        index = self._steps.index(editor)
        target = index + delta
        if not (0 <= target < len(self._steps)):
            return
        self._steps.insert(target, self._steps.pop(index))
        self._steps_box.removeWidget(editor)
        self._steps_box.insertWidget(target, editor)
        self._renumber()
        self._sync_bench()

    def _renumber(self):
        for index, editor in enumerate(self._steps):
            editor.set_number(index)

    def _sync_bench(self):
        """Keep the live schematic and the spec readout in step with the fields."""
        texts = [e.text() for e in self._steps if e.text()]
        self._preview.set_steps(texts)

        if not texts:
            self._bench_hint.setText("no steps on the bench")
            return
        launches = sum(1 for t in texts if _step_kind(t) == "LAUNCH")
        self._bench_hint.setText(
            "{} step{}   ·   {} instant, {} through the agent".format(
                len(texts), "" if len(texts) == 1 else "s",
                launches, len(texts) - launches))

    def _clear_bench(self):
        self._editing = None
        self._name_edit.clear()
        self._purpose_edit.clear()
        for editor in list(self._steps):
            self._steps_box.removeWidget(editor)
            editor.setParent(None)
            editor.deleteLater()
        self._steps = []
        self._bench_title.setText("NEW ASSEMBLY")
        self._sync_bench()

    def _discard(self):
        self._clear_bench()
        self._set_bay("assemblies")

    # ── data ──────────────────────────────────────────────────────────────
    def refresh(self):
        list_workflows, _, _ = _memory_backend()
        if list_workflows:
            try:
                self._workflows = list_workflows()
            except Exception:
                self._workflows = {}
        if self._bay == "assemblies":
            self._rebuild()
        self._refresh_timers()

    def refresh_timers(self):
        """Called by the overlay when the panel opens; cheap enough to repeat."""
        self._refresh_timers()

    def _refresh_timers(self):
        list_reminders = _reminder_backend()
        pending = []
        if list_reminders:
            try:
                pending = list_reminders()
            except Exception:
                pending = []

        if not pending:
            self._timers.setText(
                'NO ACTIVE TIMERS   ·   say "remind me in 20 minutes to…"')
            self._timers.setStyleSheet(f"color:{S.DIM};{S.TRANSPARENT_LABEL}")
            return

        shown = "   ".join(
            f"● {r.get('message', '')} {_until(r.get('due_ts', 0))}"
            for r in pending[:3]
        )
        extra = f"   +{len(pending) - 3} more" if len(pending) > 3 else ""
        self._timers.setText(shown + extra)
        self._timers.setStyleSheet(f"color:{S.MUTED};{S.TRANSPARENT_LABEL}")

    def _rebuild(self):
        while self._rack_layout.count():
            item = self._rack_layout.takeAt(0)
            # Bind it: setParent(None) detaches the item, so a second
            # item.widget() call would come back None.
            widget = item.widget()
            if widget:
                widget.setParent(None)
                widget.deleteLater()

        self._assemblies = {}

        if not self._workflows:
            self._rack_layout.addWidget(self._empty_rack())
            self._rack_layout.addStretch()
            self._status.setText("rack empty")
            return

        width = max(400, self._scroll.viewport().width())
        for ordinal, (name, data) in enumerate(sorted(self._workflows.items()), 1):
            card = _Assembly(name, data, ordinal)
            card.relayout(width)
            card.run_requested.connect(self._run_assembly)
            card.edit_requested.connect(self._rewire)
            card.delete_requested.connect(self._scrap)
            self._assemblies[name] = card
            self._rack_layout.addWidget(card)
            self._rack_layout.addWidget(self._rule(S.RULE))

        self._rack_layout.addStretch()
        count = len(self._workflows)
        self._status.setText(
            "{} assembl{}".format(count, "y" if count == 1 else "ies"))

    def resizeEvent(self, event):
        super().resizeEvent(event)
        width = max(400, self._scroll.viewport().width())
        for card in self._assemblies.values():
            card.relayout(width)

    def _empty_rack(self):
        """An honest empty state that hands you a working assembly to start from."""
        panel = QWidget()
        box = QVBoxLayout(panel)
        box.setContentsMargins(22, 26, 22, 20)
        box.setSpacing(6)

        title = QLabel("RACK EMPTY")
        title.setFont(S.label_font(13, tracking=3.0))
        title.setStyleSheet(f"color:{S.MUTED};{S.TRANSPARENT_LABEL}")
        box.addWidget(title)

        # Short and concrete. An empty state that explains itself in two
        # sentences is a manual; one line and three things to click is a shelf.
        detail = QLabel("Pull a blueprint onto the bench, or open BAY 02.")
        detail.setFont(S.mono_font(9))
        detail.setStyleSheet(f"color:{S.DIM};{S.TRANSPARENT_LABEL}")
        box.addWidget(detail)
        box.addSpacing(16)

        for name, purpose, steps in BLUEPRINTS:
            row = QHBoxLayout()
            row.setSpacing(12)

            button = QPushButton(name.upper())
            button.setFont(S.label_font(9, bold=True, tracking=1.8))
            button.setCursor(Qt.PointingHandCursor)
            button.setStyleSheet(S.PART_HOT)
            button.clicked.connect(
                lambda _c=False, n=name, p=purpose, s=steps:
                self._load_blueprint(n, p, s))
            row.addWidget(button)

            preview = QLabel("  ──  ".join(steps))
            preview.setFont(S.mono_font(8))
            preview.setStyleSheet(f"color:{S.DIM};{S.TRANSPARENT_LABEL}")
            row.addWidget(preview, 1)
            box.addLayout(row)

        return panel

    def _load_blueprint(self, name, purpose, steps):
        self._clear_bench()
        self._name_edit.setText(name)
        self._purpose_edit.setText(purpose)
        for step in steps:
            self._add_step(step)
        self._set_bay("fabrication")

    # ── actions ───────────────────────────────────────────────────────────
    def _commit(self):
        _, save_workflow, delete_workflow = _memory_backend()
        if not save_workflow:
            self._status.setText("memory unavailable")
            return

        name = self._name_edit.text().strip()
        steps = [e.text() for e in self._steps if e.text()]

        if not name:
            self._bench_hint.setText("give it a designation first")
            self._name_edit.setFocus()
            return
        if not steps:
            self._bench_hint.setText("nothing on the bench to commit")
            return

        # Renaming an assembly shouldn't leave the old one on the rack.
        if self._editing and self._editing != name.lower().strip() and delete_workflow:
            delete_workflow(self._editing)

        save_workflow(name, steps, self._purpose_edit.text().strip())
        self._clear_bench()
        self._set_bay("assemblies")
        self._status.setText(f"committed '{name}'")

    def _rewire(self, name):
        data = self._workflows.get(name, {})
        self._clear_bench()
        self._editing = name
        self._bench_title.setText(f"REWIRING · {name}")
        self._name_edit.setText(name)
        self._purpose_edit.setText(data.get("purpose", ""))
        for step in data.get("steps", []):
            self._add_step(step)
        self._set_bay("fabrication")

    def _scrap(self, name):
        _, _, delete_workflow = _memory_backend()
        if not delete_workflow:
            return
        delete_workflow(name)
        self._status.setText(f"scrapped '{name}'")
        self.refresh()

    def _run_assembly(self, name):
        if self._run_thread and self._run_thread.isRunning():
            self._status.setText("a run is already in progress")
            return

        data = self._workflows.get(name, {})
        steps = data.get("steps", [])
        if not steps:
            self._status.setText(f"'{name}' has no steps")
            return

        card = self._assemblies.get(name)
        if card:
            card.mark_running()
        self._running_name = name
        self._status.setText(f"running '{name}'")

        self._run_thread = QThread()
        self._run_worker = _RunWorker(name, steps)
        self._run_worker.moveToThread(self._run_thread)
        self._run_thread.started.connect(self._run_worker.run)
        self._run_worker.step_started.connect(self._on_step_started)
        self._run_worker.step_done.connect(self._on_step_done)
        self._run_worker.finished_run.connect(self._on_run_finished)
        self._run_thread.start()

    def _on_step_started(self, index):
        card = self._assemblies.get(self._running_name)
        if card:
            card.step_started(index)

    def _on_step_done(self, index, ok, detail):
        card = self._assemblies.get(self._running_name)
        if card:
            card.step_done(index, ok, detail)

    def _on_run_finished(self, error):
        card = self._assemblies.get(self._running_name)
        if card:
            card.mark_idle()

        self._status.setText(error or f"'{self._running_name}' complete")
        self._running_name = None

        if self._run_thread:
            self._run_thread.quit()
            self._run_thread.wait(2000)
        self._run_thread = None
        self._run_worker = None

        # Pick up the new run count, and any timer a step just set.
        list_workflows, _, _ = _memory_backend()
        if list_workflows:
            try:
                self._workflows = list_workflows()
            except Exception:
                pass
        self._refresh_timers()

    # ── paint ─────────────────────────────────────────────────────────────
    def paintEvent(self, event):
        """
        The workshop frame: flat ground, scanlines, hairline edge and machined
        corner brackets — the same instrument language as the Files panel, so
        the planets read as one system in different colours.
        """
        p = QPainter(self)
        r = self.rect().adjusted(1, 1, -1, -1)

        # One ground layer, and only one. Whichever bay is on screen paints its
        # own translucent ground; filling underneath it as well would composite
        # two alphas and the body would read as an opaque slab under a
        # see-through header.
        ground = QRegion(r)
        for surface in (self._scroll, self._bench):
            if surface.isVisible():
                ground = ground.subtracted(QRegion(surface.geometry()))
        p.setClipRegion(ground)
        p.fillRect(r, QColor(5, 9, 7, S.GROUND_ALPHA))
        p.setClipping(False)

        p.setPen(QPen(QColor(150, 225, 70, 8), 1))
        y = r.top()
        while y < r.bottom():
            p.drawLine(r.left(), y, r.right(), y)
            y += 3

        p.setPen(QPen(QColor(78, 115, 44, 190), 1))
        p.setBrush(Qt.NoBrush)
        p.drawRect(r)

        p.setPen(QPen(QColor(150, 225, 70, 210), 1.6))
        arm = 22
        for cx, cy, dx, dy in (
            (r.left(), r.top(), 1, 1),
            (r.right(), r.top(), -1, 1),
            (r.left(), r.bottom(), 1, -1),
            (r.right(), r.bottom(), -1, -1),
        ):
            p.drawLine(cx, cy, cx + arm * dx, cy)
            p.drawLine(cx, cy, cx, cy + arm * dy)
        p.end()
