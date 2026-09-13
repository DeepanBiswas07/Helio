"""
schedule_panel.py — The SCHEDULE planet: the day as an instrument.

Three bays, one on screen at a time:

  BAY 01 · TODAY    the day drawn on a real time axis — a ruled track from
                    07:00 to 23:00 with commitments as blocks, free stretches
                    as gaps, and a live NOW marker crossing it.
  BAY 02 · HORIZON  what's coming, grouped by day, birthdays included.
  BAY 03 · ADVISE   the assistant part: the free block you're standing in,
                    what you've already walked past, and three things you
                    could do about it. Pick one and it lands on the day.

The backend is memory/schedule_store (recurrence, occurrences, free slots) and
tools/time_ops/schedule_tool (the same suggestions the voice route produces),
so the panel and the voice agree on what your day looks like.
"""
import time
from datetime import datetime, date, timedelta

from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton,
    QFrame, QScrollArea, QSizePolicy
)
from PyQt5.QtCore import Qt, QThread, QObject, QTimer, QRect, QPointF, pyqtSignal
from PyQt5.QtGui import QColor, QPainter, QPen, QRegion, QFontMetrics

from .base import BasePanel, _content_alpha, _draw_header
from . import workshop_style as W

S = W.SCHEDULE

# Each kind gets a mark on the track, so a glance separates a meeting from a
# birthday without reading a word.
KIND_MARK = {
    "meeting":  "▮",
    "birthday": "★",
    "workout":  "▲",
    "task":     "▪",
    "study":    "◆",
    "personal": "●",
    "break":    "▫",
}

DAY_START = 7
DAY_END = 23
TRACK_H = 142


def _backend():
    try:
        from memory import schedule_store as store
        return store
    except Exception:
        return None


def _tools():
    try:
        from tools.time_ops import schedule_tool
        return schedule_tool
    except Exception:
        return None


def _clock(moment):
    return moment.strftime("%I:%M %p").lstrip("0").lower()


def _short_clock(moment):
    return moment.strftime("%H:%M")


# ── the day track ────────────────────────────────────────────────────────────

class _DayTrack(QWidget):
    """
    The day as a ruled axis: hour ticks, commitments as blocks on the line,
    free stretches left open, and a NOW marker that actually moves.

    A list of times would say the same thing; a track shows you the shape of
    the day — where it's packed, where the hole is — which is the question
    you're actually asking when you open this.
    """

    picked = pyqtSignal(str)          # occurrence id

    def __init__(self, parent=None):
        super().__init__(parent)
        self.items = []
        self.free = []
        self.setFixedHeight(TRACK_H)
        self.setMouseTracking(True)
        self._hover = None

        # The NOW marker is the whole point of a live track.
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(20_000)

    def _tick(self):
        if self.isVisible():
            self.update()

    def set_day(self, items, free):
        self.items = items
        self.free = free
        self.update()

    def _x_for(self, moment):
        span = (DAY_END - DAY_START) * 60.0
        minutes = (moment.hour - DAY_START) * 60 + moment.minute
        minutes = max(0.0, min(span, minutes))
        left, width = 34, self.width() - 60
        return left + width * (minutes / span)

    def _hit(self, x, y):
        for o, rect in self._blocks():
            if rect.adjusted(-2, -6, 2, 6).contains(int(x), int(y)):
                return o
        return None

    def _blocks(self):
        """(occurrence, rect) for everything drawn on the line."""
        out = []
        bus_y = 74
        for o in self.items:
            x0 = self._x_for(o["start"])
            x1 = self._x_for(o["end"]) if o["duration_min"] > 0 else x0 + 3
            out.append((o, QRect(int(x0), bus_y - 7, max(4, int(x1 - x0)), 14)))
        return out

    def mouseMoveEvent(self, event):
        hit = self._hit(event.x(), event.y())
        if hit is not self._hover:
            self._hover = hit
            self.setCursor(Qt.PointingHandCursor if hit else Qt.ArrowCursor)
            self.update()

    def mousePressEvent(self, event):
        hit = self._hit(event.x(), event.y())
        if hit:
            self.picked.emit(hit["id"])

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        now = datetime.now()
        left, right = 34, self.width() - 26
        bus_y = 74

        # ── hour ruling ───────────────────────────────────────────────────
        p.setFont(W.mono_font(7))
        for hour in range(DAY_START, DAY_END + 1):
            x = self._x_for(datetime(2000, 1, 1, hour, 0))
            major = hour % 3 == 0
            p.setPen(QPen(S.C_RULE if not major else S.C_ACCENT_D, 1))
            p.drawLine(int(x), bus_y - 14, int(x), bus_y - 9)
            if major:
                p.setPen(QPen(S.C_DIM, 1))
                p.drawText(int(x) - 16, bus_y - 32, 32, 13,
                           Qt.AlignHCenter | Qt.AlignVCenter, f"{hour:02d}")

        # ── the axis ──────────────────────────────────────────────────────
        p.setPen(QPen(S.C_RULE, 1))
        p.drawLine(left, bus_y, right, bus_y)

        # ── free stretches, drawn as open span markers ────────────────────
        p.setFont(W.mono_font(7))
        for start, end in self.free:
            x0, x1 = self._x_for(start), self._x_for(end)
            if x1 - x0 < 14:
                continue
            p.setPen(QPen(S.C_ACCENT_D, 1, Qt.DashLine))
            p.drawLine(int(x0) + 2, bus_y + 34, int(x1) - 2, bus_y + 34)
            minutes = int((end - start).total_seconds() // 60)
            if x1 - x0 > 46:
                p.setPen(QPen(S.C_DIM, 1))
                p.drawText(int(x0), bus_y + 38, int(x1 - x0), 13,
                           Qt.AlignHCenter | Qt.AlignTop, f"{minutes}m free")

        # ── commitments ───────────────────────────────────────────────────
        metrics = QFontMetrics(W.mono_font(8))
        for o, rect in self._blocks():
            status = o["status"]
            if status == "done":
                edge, fill = S.C_ACCENT_D, QColor(S.C_ACCENT_D)
            elif status == "skipped":
                edge, fill = S.C_RULE, None
            elif o["end"] < now:
                edge, fill = S.C_WARN, None
            else:
                edge, fill = S.C_ACCENT, QColor(*S.accent_rgb, 70)

            if o is self._hover:
                edge = S.C_BRIGHT

            p.setPen(QPen(edge, 1.4))
            p.setBrush(fill if fill else Qt.NoBrush)
            if o["duration_min"] > 0:
                p.drawRect(rect)
            else:
                # A birthday has no span — draw it as a marker on the line.
                cx = rect.center().x()
                p.setBrush(edge)
                p.drawEllipse(QPointF(cx, bus_y), 4, 4)

            # Alternate the label above / below so neighbours don't collide.
            label = o["title"]
            index = self.items.index(o)
            above = index % 2 == 0
            text_y = rect.top() - 15 if above else rect.bottom() + 3
            width = max(64, metrics.width(label) + 8)
            p.setFont(W.mono_font(8))
            p.setPen(QPen(S.C_BRIGHT if status == "pending" else S.C_DIM, 1))
            p.drawText(int(rect.center().x() - width / 2), text_y, width, 12,
                       Qt.AlignHCenter | Qt.AlignVCenter,
                       metrics.elidedText(label, Qt.ElideRight, width))

        # ── NOW ───────────────────────────────────────────────────────────
        if DAY_START <= now.hour < DAY_END:
            x = self._x_for(now)
            p.setPen(QPen(S.C_BRIGHT, 1.4))
            p.drawLine(int(x), bus_y - 24, int(x), bus_y + 30)
            p.setBrush(S.C_BRIGHT)
            p.setPen(Qt.NoPen)
            p.drawEllipse(QPointF(x, bus_y - 24), 2.6, 2.6)
            p.setFont(W.label_font(7, tracking=1.6))
            p.setPen(QPen(S.C_BRIGHT, 1))
            p.drawText(int(x) + 5, bus_y - 32, 60, 13,
                       Qt.AlignLeft | Qt.AlignVCenter, "NOW")
        p.end()


# ── one row in a list ────────────────────────────────────────────────────────

class _EntryRow(QFrame):
    """An entry with its time, mark, title and the two things you'd do to it."""

    done_requested = pyqtSignal(str, str)
    skip_requested = pyqtSignal(str, str)
    drop_requested = pyqtSignal(str)

    def __init__(self, occurrence, show_day=False, parent=None):
        super().__init__(parent)
        self.occurrence = occurrence
        self.setStyleSheet(S.ROW)
        self.setFixedHeight(30)

        row = QHBoxLayout(self)
        row.setContentsMargins(12, 0, 8, 0)
        row.setSpacing(10)

        status = occurrence["status"]
        stamp = occurrence["start"]
        when = stamp.strftime("%a %d  %H:%M") if show_day else _short_clock(stamp)
        if occurrence["duration_min"] <= 0:
            when = stamp.strftime("%a %d") if show_day else "all day"

        time_lbl = QLabel(when)
        time_lbl.setFont(W.mono_font(9))
        time_lbl.setFixedWidth(86 if show_day else 58)
        time_lbl.setStyleSheet(f"color:{S.MUTED};{W.TRANSPARENT_LABEL}")
        row.addWidget(time_lbl)

        mark = QLabel(KIND_MARK.get(occurrence["kind"], "▪"))
        mark.setFont(W.mono_font(8))
        mark.setFixedWidth(14)
        mark.setStyleSheet(f"color:{S.ACCENT_D};{W.TRANSPARENT_LABEL}")
        row.addWidget(mark)

        title = QLabel(occurrence["title"])
        title.setFont(W.mono_font(10))
        colour = {"done": S.DIM, "skipped": S.DIM}.get(status, S.TEXT)
        strike = "text-decoration:line-through;" if status != "pending" else ""
        title.setStyleSheet(f"color:{colour};{strike}{W.TRANSPARENT_LABEL}")
        row.addWidget(title, 1)

        if occurrence["recurrence"] != "none":
            repeat = QLabel(occurrence["recurrence"])
            repeat.setFont(W.label_font(7, tracking=1.4))
            repeat.setStyleSheet(f"color:{S.RULE_HI};{W.TRANSPARENT_LABEL}")
            row.addWidget(repeat)

        if status == "pending":
            for text, signal, style in (
                ("DONE", self.done_requested, S.ENGRAVED),
                ("SKIP", self.skip_requested, S.ENGRAVED),
            ):
                button = QPushButton(text)
                button.setFont(W.label_font(8, tracking=1.5))
                button.setCursor(Qt.PointingHandCursor)
                button.setStyleSheet(style)
                button.clicked.connect(
                    lambda _c=False, sig=signal: sig.emit(
                        occurrence["id"], occurrence["day_key"]))
                row.addWidget(button)
        else:
            state = QLabel("DONE" if status == "done" else "SKIPPED")
            state.setFont(W.label_font(8, tracking=1.5))
            state.setStyleSheet(f"color:{S.ACCENT_D};{W.TRANSPARENT_LABEL}")
            row.addWidget(state)

        drop = QPushButton("✕")
        drop.setFont(W.mono_font(9))
        drop.setFixedWidth(20)
        drop.setCursor(Qt.PointingHandCursor)
        drop.setStyleSheet(S.ENGRAVED_WARN)
        drop.clicked.connect(lambda: self.drop_requested.emit(occurrence["id"]))
        row.addWidget(drop)


# ── suggestions on a worker ──────────────────────────────────────────────────

class _AdviseWorker(QObject):
    """suggest_activity calls the model; that can't happen on the GUI thread."""

    done = pyqtSignal(list, str)

    def run(self):
        tools = _tools()
        if tools is None:
            self.done.emit([], "Schedule tools unavailable.")
            return
        try:
            context = tools.build_suggestion_context()
            tools.handle_suggest_activity({"context": ""})
            options = list(tools._PENDING_SUGGESTIONS)
            minutes = context["free_minutes"]
            if minutes:
                headline = f"{minutes} minutes free"
            else:
                headline = "no gap right now"
            self.done.emit(options, headline)
        except Exception as e:
            self.done.emit([], f"Could not build suggestions: {e}")


# ── the painter-only planet panel ────────────────────────────────────────────

class SchedulePanel(BasePanel):
    def __init__(self):
        super().__init__("SCHEDULE", QColor(*S.accent_rgb))

    def draw(self, painter, shape_rect, panel_progress, alpha):
        # Header only — ScheduleWidget paints the body.
        content_a = _content_alpha(panel_progress, alpha)
        _draw_header(painter, shape_rect, self.title, self.accent, content_a)


# ── the interactive panel ────────────────────────────────────────────────────

class ScheduleWidget(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WA_TranslucentBackground)

        self._bay = "today"
        self._suggestions = []
        self._advise_thread = None
        self._advise_worker = None

        self._build_ui()
        self.refresh()

        # The day moves under you, so re-read it periodically even if nothing
        # in the UI changed.
        self._poll = QTimer(self)
        self._poll.timeout.connect(self._maybe_refresh)
        self._poll.start(20_000)
        self._last_mtime = -1.0

    # ── construction ──────────────────────────────────────────────────────
    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(20, 20, 20, 14)
        root.setSpacing(0)

        bays = QHBoxLayout()
        bays.setSpacing(8)
        bays.setContentsMargins(0, 0, 0, 10)
        self._bay_today = self._make_bay("01", "TODAY", True)
        self._bay_horizon = self._make_bay("02", "HORIZON", False)
        self._bay_advise = self._make_bay("03", "ADVISE", False)
        self._bay_today.clicked.connect(lambda: self._set_bay("today"))
        self._bay_horizon.clicked.connect(lambda: self._set_bay("horizon"))
        self._bay_advise.clicked.connect(lambda: self._set_bay("advise"))
        for widget in (self._bay_today, self._bay_horizon, self._bay_advise):
            bays.addWidget(widget)
        bays.addStretch()

        self._status = QLabel("")
        self._status.setFont(W.mono_font(9))
        self._status.setStyleSheet(f"color:{S.MUTED};{W.TRANSPARENT_LABEL}")
        bays.addWidget(self._status)
        root.addLayout(bays)

        self._build_today(root)
        self._build_horizon(root)
        self._build_advise(root)

        # ── add line, always available ────────────────────────────────────
        root.addWidget(self._rule(S.RULE))
        entry = QHBoxLayout()
        entry.setContentsMargins(0, 8, 0, 0)
        entry.setSpacing(10)
        entry.addWidget(self._caption("ADD"))
        self._add_edit = QLineEdit()
        self._add_edit.setFont(W.mono_font(10))
        self._add_edit.setStyleSheet(S.INPUT)
        self._add_edit.setPlaceholderText(
            "dentist tomorrow at 3pm   ·   Arjun's birthday on March 12   ·   gym at 7 every weekday")
        self._add_edit.returnPressed.connect(self._add_from_line)
        entry.addWidget(self._add_edit, 1)
        commit = QPushButton("PUT ON SCHEDULE")
        commit.setFont(W.label_font(9, bold=True, tracking=1.8))
        commit.setCursor(Qt.PointingHandCursor)
        commit.setStyleSheet(S.COMMAND)
        commit.clicked.connect(self._add_from_line)
        entry.addWidget(commit)
        root.addLayout(entry)

    def _build_today(self, root):
        self._today_page = QWidget()
        self._today_page.setStyleSheet(f"background:{S.PANEL_GROUND};")
        page = QVBoxLayout(self._today_page)
        page.setContentsMargins(16, 12, 16, 12)
        page.setSpacing(0)

        self._day_label = QLabel("")
        self._day_label.setFont(W.label_font(12, bold=True, tracking=2.6))
        self._day_label.setStyleSheet(f"color:{S.BRIGHT};{W.TRANSPARENT_LABEL}")
        page.addWidget(self._day_label)

        self._day_summary = QLabel("")
        self._day_summary.setFont(W.mono_font(8))
        self._day_summary.setStyleSheet(f"color:{S.DIM};{W.TRANSPARENT_LABEL}")
        page.addWidget(self._day_summary)
        page.addSpacing(8)

        self._track = _DayTrack()
        page.addWidget(self._track)
        page.addSpacing(6)

        self._today_scroll, self._today_list = self._make_list()
        page.addWidget(self._today_scroll, 1)
        root.addWidget(self._today_page, 1)

    def _build_horizon(self, root):
        self._horizon_page = QWidget()
        self._horizon_page.setStyleSheet(f"background:{S.PANEL_GROUND};")
        page = QVBoxLayout(self._horizon_page)
        page.setContentsMargins(16, 12, 16, 12)
        page.setSpacing(0)

        head = QHBoxLayout()
        head.setSpacing(8)
        title = QLabel("THE NEXT 30 DAYS")
        title.setFont(W.label_font(12, bold=True, tracking=2.6))
        title.setStyleSheet(f"color:{S.BRIGHT};{W.TRANSPARENT_LABEL}")
        head.addWidget(title)
        head.addStretch()
        self._horizon_note = QLabel("")
        self._horizon_note.setFont(W.mono_font(8))
        self._horizon_note.setStyleSheet(f"color:{S.DIM};{W.TRANSPARENT_LABEL}")
        head.addWidget(self._horizon_note)
        page.addLayout(head)
        page.addSpacing(8)

        self._horizon_scroll, self._horizon_list = self._make_list()
        page.addWidget(self._horizon_scroll, 1)
        root.addWidget(self._horizon_page, 1)
        self._horizon_page.hide()

    def _build_advise(self, root):
        self._advise_page = QWidget()
        self._advise_page.setStyleSheet(f"background:{S.PANEL_GROUND};")
        page = QVBoxLayout(self._advise_page)
        page.setContentsMargins(18, 14, 18, 14)
        page.setSpacing(0)

        self._advise_title = QLabel("WHAT NOW")
        self._advise_title.setFont(W.label_font(12, bold=True, tracking=2.6))
        self._advise_title.setStyleSheet(f"color:{S.ACCENT};{W.TRANSPARENT_LABEL}")
        page.addWidget(self._advise_title)

        self._advise_state = QLabel("")
        self._advise_state.setFont(W.mono_font(9))
        self._advise_state.setWordWrap(True)
        self._advise_state.setStyleSheet(f"color:{S.DIM};{W.TRANSPARENT_LABEL}")
        page.addWidget(self._advise_state)
        page.addSpacing(14)

        self._advise_box = QVBoxLayout()
        self._advise_box.setSpacing(4)
        page.addLayout(self._advise_box)
        page.addStretch(1)

        ask = QPushButton("ASK HELIO WHAT TO DO")
        ask.setFont(W.label_font(10, bold=True, tracking=2.0))
        ask.setCursor(Qt.PointingHandCursor)
        ask.setStyleSheet(S.COMMAND)
        ask.clicked.connect(self._advise)
        bottom = QHBoxLayout()
        bottom.addStretch()
        bottom.addWidget(ask)
        page.addLayout(bottom)

        root.addWidget(self._advise_page, 1)
        self._advise_page.hide()

    def _make_list(self):
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setStyleSheet(S.SCROLL)
        scroll.setAttribute(Qt.WA_TranslucentBackground)
        scroll.viewport().setAttribute(Qt.WA_TranslucentBackground)
        container = QWidget()
        container.setAttribute(Qt.WA_TranslucentBackground)
        layout = QVBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addStretch()
        scroll.setWidget(container)
        return scroll, layout

    def _make_bay(self, index, name, active):
        button = QPushButton(f"{index}   {name}")
        button.setFont(W.label_font(10, tracking=2.2))
        button.setCursor(Qt.PointingHandCursor)
        button.setStyleSheet(S.BAY_ACTIVE if active else S.BAY_IDLE)
        return button

    def _caption(self, text):
        label = QLabel(text)
        label.setFont(W.label_font(8, tracking=2.2))
        label.setStyleSheet(f"color:{S.DIM};{W.TRANSPARENT_LABEL}")
        return label

    def _rule(self, colour):
        line = QFrame()
        line.setFixedHeight(1)
        line.setStyleSheet(f"background:{colour};border:none;")
        return line

    # ── bays ──────────────────────────────────────────────────────────────
    def _set_bay(self, bay):
        """Exclusive, like every other planet — stations, not tabs."""
        self._bay = bay
        for name, button in (("today", self._bay_today),
                             ("horizon", self._bay_horizon),
                             ("advise", self._bay_advise)):
            button.setStyleSheet(S.BAY_ACTIVE if name == bay else S.BAY_IDLE)
        self._today_page.setVisible(bay == "today")
        self._horizon_page.setVisible(bay == "horizon")
        self._advise_page.setVisible(bay == "advise")
        if bay == "advise" and not self._suggestions:
            self._advise()
        else:
            self.refresh()
        self.update()

    # ── data ──────────────────────────────────────────────────────────────
    def _maybe_refresh(self):
        store = _backend()
        if store is None:
            return
        mtime = store.schedule_mtime()
        if mtime != self._last_mtime:
            self._last_mtime = mtime
            self.refresh()
        elif self._bay == "today":
            self._track.update()          # keep NOW moving

    def refresh(self):
        store = _backend()
        if store is None:
            self._status.setText("schedule unavailable")
            return
        self._last_mtime = store.schedule_mtime()

        if self._bay == "today":
            self._refresh_today(store)
        elif self._bay == "horizon":
            self._refresh_horizon(store)

    def _refresh_today(self, store):
        now = datetime.now()
        items = store.day_agenda(now.date())
        free = store.free_slots(now.date(), now=now, min_minutes=20)

        self._day_label.setText(now.strftime("%A %d %B"))
        pending = [o for o in items if o["status"] == "pending"]
        done = [o for o in items if o["status"] == "done"]
        free_minutes = sum(int((b - a).total_seconds() // 60) for a, b in free)
        self._day_summary.setText(
            "{} scheduled   ·   {} done   ·   {} left   ·   {}h {}m free ahead".format(
                len(items), len(done), len(pending),
                free_minutes // 60, free_minutes % 60))

        self._track.set_day(items, free)
        self._fill(self._today_list, items, show_day=False,
                   empty="Nothing on today. Add something below, or ask Helio.")
        self._status.setText(
            "{} today".format(len(items)) if items else "day clear")

    def _refresh_horizon(self, store):
        items = store.horizon(30)
        today = date.today()
        upcoming = [o for o in items if o["start"].date() > today]
        birthdays = [o for o in items if o["kind"] == "birthday"]
        self._horizon_note.setText(
            "{} ahead   ·   {} birthday{}".format(
                len(upcoming), len(birthdays), "" if len(birthdays) == 1 else "s"))
        self._fill(self._horizon_list, items, show_day=True,
                   empty="Nothing scheduled in the next 30 days.")
        self._status.setText("{} in 30 days".format(len(items)))

    def _fill(self, layout, items, show_day, empty):
        while layout.count():
            item = layout.takeAt(0)
            # Bind it: setParent(None) detaches the item, so a second
            # item.widget() call would come back None.
            widget = item.widget()
            if widget:
                widget.setParent(None)
                widget.deleteLater()

        if not items:
            note = QLabel(empty)
            note.setFont(W.mono_font(9))
            note.setContentsMargins(12, 18, 0, 0)
            note.setStyleSheet(f"color:{S.DIM};{W.TRANSPARENT_LABEL}")
            layout.addWidget(note)
            layout.addStretch()
            return

        last_day = None
        for occurrence in items:
            day = occurrence["start"].date()
            if show_day and day != last_day:
                last_day = day
                header = QLabel(occurrence["start"].strftime("%A %d %B").upper())
                header.setFont(W.label_font(8, tracking=2.2))
                header.setContentsMargins(12, 10, 0, 3)
                header.setStyleSheet(f"color:{S.ACCENT_D};{W.TRANSPARENT_LABEL}")
                layout.addWidget(header)

            row = _EntryRow(occurrence, show_day=False)
            row.done_requested.connect(self._mark_done)
            row.skip_requested.connect(self._mark_skipped)
            row.drop_requested.connect(self._drop)
            layout.addWidget(row)
        layout.addStretch()

    # ── actions ───────────────────────────────────────────────────────────
    def _add_from_line(self):
        text = self._add_edit.text().strip()
        if not text:
            return
        tools = _tools()
        if tools is None:
            self._status.setText("schedule tools unavailable")
            return

        # Straight through the same tool the voice route uses, so typing and
        # speaking can never disagree about how a phrase is understood.
        result = tools.handle_add_event({"what": text, "when": text})
        self._status.setText(result.split(" — ")[0].lower())
        if result.startswith("Added"):
            self._add_edit.clear()
        self.refresh()

    def _mark_done(self, event_id, day_key):
        store = _backend()
        if store:
            store.mark_done(event_id, date.fromisoformat(day_key))
            self.refresh()

    def _mark_skipped(self, event_id, day_key):
        store = _backend()
        if store:
            store.mark_skipped(event_id, date.fromisoformat(day_key))
            self.refresh()

    def _drop(self, event_id):
        store = _backend()
        if store:
            store.remove_event(event_id)
            self.refresh()

    # ── advise ────────────────────────────────────────────────────────────
    def _advise(self):
        if self._advise_thread and self._advise_thread.isRunning():
            return
        self._clear_advice()
        self._advise_state.setText("Reading your day…")

        self._advise_thread = QThread()
        self._advise_worker = _AdviseWorker()
        self._advise_worker.moveToThread(self._advise_thread)
        self._advise_thread.started.connect(self._advise_worker.run)
        self._advise_worker.done.connect(self._advice_ready)
        self._advise_thread.start()

    def _clear_advice(self):
        while self._advise_box.count():
            item = self._advise_box.takeAt(0)
            widget = item.widget()
            if widget:
                widget.setParent(None)
                widget.deleteLater()

    def _advice_ready(self, options, headline):
        if self._advise_thread:
            self._advise_thread.quit()
            self._advise_thread.wait(2000)
        self._advise_thread = None
        self._advise_worker = None

        self._suggestions = options
        self._clear_advice()

        store = _backend()
        missed = store.missed_today() if store else []
        state = headline
        if missed:
            state += ".  You've walked past " + ", ".join(o["title"] for o in missed)
        self._advise_state.setText(state + ".")

        if not options:
            return

        for index, option in enumerate(options, 1):
            row = QFrame()
            row.setStyleSheet(S.ROW)
            row.setFixedHeight(38)
            line = QHBoxLayout(row)
            line.setContentsMargins(12, 0, 8, 0)
            line.setSpacing(10)

            number = QLabel(f"{index:02d}")
            number.setFont(W.mono_font(9))
            number.setFixedWidth(24)
            number.setStyleSheet(f"color:{S.ACCENT_D};{W.TRANSPARENT_LABEL}")
            line.addWidget(number)

            title = QLabel(option["title"])
            title.setFont(W.mono_font(10))
            title.setStyleSheet(f"color:{S.TEXT};{W.TRANSPARENT_LABEL}")
            line.addWidget(title)

            why = QLabel(option.get("why", ""))
            why.setFont(W.mono_font(8))
            why.setStyleSheet(f"color:{S.DIM};{W.TRANSPARENT_LABEL}")
            line.addWidget(why, 1)

            span = QLabel(f"{option['minutes']} min")
            span.setFont(W.mono_font(8))
            span.setStyleSheet(f"color:{S.MUTED};{W.TRANSPARENT_LABEL}")
            line.addWidget(span)

            take = QPushButton("TAKE IT")
            take.setFont(W.label_font(9, bold=True, tracking=1.6))
            take.setCursor(Qt.PointingHandCursor)
            take.setStyleSheet(S.COMMAND)
            take.clicked.connect(lambda _c=False, i=index: self._take(i))
            line.addWidget(take)

            self._advise_box.addWidget(row)

    def _take(self, index):
        tools = _tools()
        if tools is None:
            return
        tools._PENDING_SUGGESTIONS = self._suggestions
        result = tools.handle_accept_suggestion({"choice": str(index)})
        self._status.setText(result.split(" — ")[0].lower())
        self._suggestions = []
        self._set_bay("today")

    # ── paint ─────────────────────────────────────────────────────────────
    def paintEvent(self, event):
        W.SCHEDULE.frame(self, exclude=(
            self._today_page, self._horizon_page, self._advise_page))
