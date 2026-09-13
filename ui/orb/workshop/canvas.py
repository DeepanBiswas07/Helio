"""
canvas.py — the full-screen workshop surface.

The Forge planet panel is a bench in a small window. This is the room: it
takes the whole screen, slabs float on it, and the command line at the bottom
runs the full agent — so anything Helio can do, it can do here, and what it
makes lands on the surface next to what you were already looking at.

Helio reaches this through workshop_tool, which calls in from the agent's
worker thread. Every one of those calls crosses into the GUI thread as a
signal; touching a widget directly from there is how you get a native crash
with no traceback.
"""
import json
import os
import pathlib

from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QLineEdit, QFrame,
)
from PyQt5.QtCore import Qt, QEvent, QObject, QRect, QTimer, pyqtSignal
from PyQt5.QtGui import QPainter, QPen, QColor, QPixmap, QRadialGradient

from ..panels import workshop_style as W
from .slab import Slab, HEADER_H

S = W.FORGE

STATE_PATH = (pathlib.Path(__file__).resolve().parents[3]
              / "data" / "forge" / "workshop.json")

# Where the next slab lands when Helio does not say. Stepped so a run of
# builds fans out instead of stacking into one pile.
CASCADE = (46, 38)

_CANVAS = None


def get_canvas():
    """The live canvas, or None when the workshop is closed."""
    return _CANVAS


def canvas_is_open():
    return _CANVAS is not None and _CANVAS.isVisible()


class _Bridge(QObject):
    """Worker-thread calls from workshop_tool, marshalled onto the GUI thread."""

    place = pyqtSignal(str, str, str, str)   # kind, title, payload, slab_id
    clear = pyqtSignal()
    note = pyqtSignal(str)
    beat = pyqtSignal(bool)                  # start / stop the preview refresh
    split = pyqtSignal(str)                  # a build starting: brief

    def request(self, action, **kwargs):
        if action == "place":
            self.place.emit(
                str(kwargs.get("kind", "text")), str(kwargs.get("title", "")),
                str(kwargs.get("payload", "")), str(kwargs.get("slab_id", "")))
        elif action == "clear":
            self.clear.emit()
        elif action == "note":
            self.note.emit(str(kwargs.get("text", "")))


class WorkshopCanvas(QWidget):
    """Full-screen making surface."""

    closed = pyqtSignal()
    handed_off = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        # With a parent this is a panel inside Helio's own full-screen overlay,
        # not a second window on top of it — opening the workshop should not
        # close Helio and hand you a different full screen to look at.
        self._embedded = parent is not None
        if not self._embedded:
            self.setWindowFlags(Qt.Window | Qt.FramelessWindowHint)
            self.setWindowTitle("Helio Workshop")
        # It paints every one of its own pixels (see paintEvent), so tell Qt:
        # otherwise each workshop repaint — every step of a slab drag — also
        # re-rendered the whole solar system hidden beneath it. Set here, but
        # the style sheet inherited from Helio's overlay clears it when the
        # canvas is polished, so showEvent and changeEvent set it again.
        self.setAttribute(Qt.WA_OpaquePaintEvent, True)
        self.setAutoFillBackground(False)
        self._backdrop = None
        self._backdrop_size = None
        self.setFocusPolicy(Qt.StrongFocus)

        self.slabs = []
        self._by_id = {}
        self._next_drop = [70, 90]
        self._agent = None
        self._build_slab = None
        self._buffer = ""
        self._dirty = False

        self.bridge = _Bridge()
        self.bridge.place.connect(self._on_place)
        self.bridge.clear.connect(self.clear)
        self.bridge.note.connect(self.say)
        self.bridge.beat.connect(self._set_beat)
        self.bridge.split.connect(self._open_build_split)

        self._beat = QTimer(self)
        self._beat.setInterval(420)
        self._beat.timeout.connect(self._paint_partial)

        self._build_ui()
        self._hook_backend()
        self.restore()

    # ── construction ──────────────────────────────────────────────────────
    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # ── top rail ─────────────────────────────────────────────────────
        rail = QFrame()
        rail.setFixedHeight(44)
        rail.setStyleSheet(
            f"background:{S.GROUND};border:none;border-bottom:1px solid {S.RULE_HI};")
        row = QHBoxLayout(rail)
        row.setContentsMargins(24, 0, 14, 0)
        row.setSpacing(14)

        mark = QLabel("HELIO  ·  WORKSHOP")
        mark.setFont(W.label_font(11, bold=True, tracking=3.0))
        mark.setStyleSheet(f"color:{S.ACCENT};{W.TRANSPARENT_LABEL}")
        row.addWidget(mark)

        self._count = QLabel("")
        self._count.setFont(W.mono_font(9))
        self._count.setStyleSheet(f"color:{S.DIM};{W.TRANSPARENT_LABEL}")
        row.addWidget(self._count)
        row.addStretch()

        self._status = QLabel("")
        self._status.setFont(W.mono_font(9))
        self._status.setMinimumWidth(0)
        # Squeezed between a stretch and four buttons it collapsed to one
        # character; give it room and let it elide itself instead.
        self._status.setFixedWidth(320)
        self._status.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self._status.setStyleSheet(f"color:{S.MUTED};{W.TRANSPARENT_LABEL}")
        row.addWidget(self._status)

        for label, slot in (("BUILDS", self.show_rack), ("TIDY", self.tidy),
                            ("CLEAR", self.clear), ("CLOSE", self.close)):
            button = QPushButton(label)
            button.setFont(W.label_font(8, bold=True, tracking=1.8))
            button.setCursor(Qt.PointingHandCursor)
            button.setStyleSheet(
                S.CHIP if label in ("BUILDS", "TIDY") else S.ENGRAVED_WARN)
            button.clicked.connect(slot)
            row.addWidget(button)
        root.addWidget(rail)

        # ── the surface itself ───────────────────────────────────────────
        self.surface = QWidget()
        self.surface.setStyleSheet("background:transparent;")
        root.addWidget(self.surface, 1)

        # ── command line ─────────────────────────────────────────────────
        bar = QFrame()
        bar.setFixedHeight(52)
        bar.setStyleSheet(
            f"background:{S.GROUND};border:none;border-top:1px solid {S.RULE_HI};")
        entry = QHBoxLayout(bar)
        entry.setContentsMargins(24, 0, 24, 0)
        entry.setSpacing(12)

        caption = QLabel("HELIO")
        caption.setFont(W.label_font(9, bold=True, tracking=2.4))
        caption.setStyleSheet(f"color:{S.ACCENT};{W.TRANSPARENT_LABEL}")
        entry.addWidget(caption)

        self._line = QLineEdit()
        self._line.setFont(W.mono_font(11))
        self._line.setStyleSheet(S.INPUT)
        self._line.setPlaceholderText(
            "build me a site for the bakery   ·   find me a cat photo   ·   "
            "make the buttons bigger   ·   what's my cpu at")
        self._line.returnPressed.connect(self._send)
        entry.addWidget(self._line, 1)

        self._go = QPushButton("RUN")
        self._go.setFont(W.label_font(9, bold=True, tracking=2.0))
        self._go.setCursor(Qt.PointingHandCursor)
        self._go.setStyleSheet(S.COMMAND)
        self._go.clicked.connect(self._send)
        entry.addWidget(self._go)
        root.addWidget(bar)

    def _hook_backend(self):
        """Watch Forge builds. The workshop hook itself follows visibility."""

        try:
            from tools.system import forge_tool
            self._forge = forge_tool
            forge_tool.add_stream_listener(self._on_forge_stream)
            forge_tool.add_artifact_listener(self._on_forge_done)
        except Exception as e:
            print(f"[Workshop] forge hook failed: {e}")

    # ── placing things ────────────────────────────────────────────────────
    def place(self, kind, title, payload="", slab_id="", at=None, size=None):
        """Put one thing on the surface. Returns the slab."""
        if slab_id and slab_id in self._by_id:
            slab = self._by_id[slab_id]
            slab.set_title(title or slab.title)
            slab.set_payload(payload)
            slab.lift()
            return slab

        slab = Slab(kind, title or kind.upper(), payload, self.surface)
        slab.dismissed.connect(self._forget)
        slab.changed.connect(self._touch)
        slab.handed_off.connect(self.handed_off.emit)
        slab.picked.connect(self._place_from_rack)
        if size:
            slab.resize(*size)
        slab.move(*(at or self._drop_point(slab)))
        slab.show()
        slab.lift()

        self.slabs.append(slab)
        if slab_id:
            self._by_id[slab_id] = slab
        self._touch()
        return slab

    def _drop_point(self, slab):
        x, y = self._next_drop
        room = self.surface.size()
        if x + slab.width() > room.width() - 30 or y + slab.height() > room.height() - 30:
            self._next_drop = [70, 90]
            x, y = self._next_drop
        self._next_drop = [x + CASCADE[0], y + CASCADE[1]]
        return x, y

    def _on_place(self, kind, title, payload, slab_id):
        self.place(kind, title, payload, slab_id)
        self.say(f"placed {title or kind}")

    def _forget(self, slab):
        if slab in self.slabs:
            self.slabs.remove(slab)
        for key, value in list(self._by_id.items()):
            if value is slab:
                del self._by_id[key]
        if slab is self._build_slab:
            self._build_slab = None
        self._touch()

    def clear(self):
        for slab in list(self.slabs):
            slab.dismiss()
        self.slabs = []
        self._by_id = {}
        self._build_slab = None
        self._next_drop = [70, 90]
        self._touch()

    def show_rack(self):
        """Put the rack of everything built up, to open one from."""
        existing = self._by_id.get("__rack__")
        if existing is not None:
            existing.body.reload()
            existing.lift()
            return existing
        room = self.surface.size()
        slab = self.place("rack", "Everything built", "", slab_id="__rack__",
                          at=(24, 24), size=(600, max(320, room.height() - 60)))
        return slab

    def _place_from_rack(self, item):
        """A row was chosen: put that build on the surface beside the rack."""
        path = item.get("file_path", "")
        if not path or not os.path.exists(path):
            self.say("that build's file has gone")
            return
        kind = {"chart": "chart", "image": "image", "code": "code",
                "document": "document"}.get(item.get("type"), "web")

        # Beside the rack, not on top of it — the cascade was dropping the
        # thing you just picked over the list you picked it from.
        rack = self._by_id.get("__rack__")
        at = None
        if rack is not None and rack.isVisible():
            room = self.surface.size()
            left = rack.x() + rack.width() + 16
            width = max(420, room.width() - left - 24)
            at = (left, rack.y())
            slab = self.place(kind, item.get("title", "build"), path,
                              at=at, size=(width, rack.height()))
        else:
            slab = self.place(kind, item.get("title", "build"), path)
        slab.lift()
        self.say(f"put up {item.get('title', 'it')}")

    def tidy(self):
        """Lay every slab out on a grid — the 'put my desk back' button."""
        if not self.slabs:
            return
        count = len(self.slabs)
        columns = 1 if count == 1 else (2 if count <= 4 else 3)
        rows = (count + columns - 1) // columns
        room = self.surface.size()
        pad = 16
        width = (room.width() - pad * (columns + 1)) // columns
        height = (room.height() - pad * (rows + 1)) // rows
        for index, slab in enumerate(self.slabs):
            column, row = index % columns, index // columns
            slab.resize(max(280, width), max(200, height))
            slab.move(pad + column * (width + pad), pad + row * (height + pad))
        self._touch()

    def say(self, text):
        from PyQt5.QtGui import QFontMetrics
        text = " ".join(str(text).split())
        metrics = QFontMetrics(self._status.font())
        self._status.setText(
            metrics.elidedText(text, Qt.ElideRight, self._status.width() - 6))

    def _touch(self):
        self._count.setText(
            f"{len(self.slabs)} on the surface" if self.slabs else "surface clear")
        self._dirty = True
        self._publish()

    def _publish(self):
        """
        Tell the tools what is on the surface.

        Helio could place things and clear them but never read the surface
        back, so "use the picture on screen" meant nothing — it had no idea a
        picture was up, let alone which one. A snapshot of plain dicts is safe
        for the worker thread to read; the widgets never leave this one.
        """
        try:
            from tools.system import workshop_tool
            workshop_tool.SURFACE = [
                {"kind": slab.kind, "title": slab.title,
                 "path": str(slab.payload or "")}
                for slab in self.slabs
                if slab.payload and slab.kind != "rack"
            ]
        except Exception as e:
            print(f"[Workshop] could not publish the surface: {e}")

    # ── a build, watched on the surface ───────────────────────────────────
    def _on_forge_stream(self, event, payload):
        """
        Forge callbacks arrive on the agent's worker thread.

        Qt queues a signal emitted across threads, so bouncing through the
        bridge is what makes this safe; calling place() directly from here
        would be a widget touched off the GUI thread.
        """
        if event == "start":
            brief = (payload or {}).get("brief", "")
            self._buffer = ""
            # Two panels, immediately: the source arriving, and where it ends
            # up. The output one stays as the finished thing.
            self.bridge.split.emit(brief)
            self.bridge.beat.emit(True)
        elif event == "chunk":
            self._buffer += payload or ""
        elif event == "done":
            self._buffer = payload or ""
            self.bridge.beat.emit(False)
        elif event == "error":
            self.bridge.beat.emit(False)

    def _set_beat(self, running):
        """Start or stop the preview refresh — always on the GUI thread."""
        if running:
            self._beat.start()
        else:
            self._beat.stop()
            self._finish_build()

    def _open_build_split(self, brief):
        """
        Stand up the two panels a build needs, side by side.

        Left is the source as it is written; right is what it renders to, and
        that one stays afterwards as the finished output.
        """
        headline = (brief or "building")[:46]
        room = self.surface.size()
        pad = 18
        gap = 14
        height = max(320, room.height() - pad * 2)
        left_w = max(300, int((room.width() - pad * 2 - gap) * 0.40))
        right_w = max(340, room.width() - pad * 2 - gap - left_w)

        code = self.place("code", f"source · {headline}", "",
                          slab_id="__build_code__",
                          at=(pad, pad), size=(left_w, height))
        code.set_text("")

        out = self.place("web", headline, "", slab_id="__build_out__",
                         at=(pad + left_w + gap, pad), size=(right_w, height))
        out.load("")
        out.lift()
        self.say(f"forging · {headline}")

    def _paint_partial(self):
        if not self._buffer:
            return
        code = self._by_id.get("__build_code__")
        if code is not None:
            # A tail, not the whole file — relaying out megabytes on every
            # beat is what makes a stream stutter.
            code.set_text(self._buffer[-4000:])
        out = self._by_id.get("__build_out__")
        if out is not None:
            out.load(self._buffer)
        self.say(f"forging · {len(self._buffer):,} bytes")

    def _finish_build(self):
        self._beat.stop()
        if not self._buffer:
            return
        code = self._by_id.get("__build_code__")
        if code is not None:
            code.set_text(self._buffer[-40000:])
        out = self._by_id.get("__build_out__")
        if out is not None:
            out.load(self._buffer)

    def _on_forge_done(self, artifact):
        """The finished file lands in the output panel, which then stays."""
        if not artifact:
            return
        path = artifact.get("file_path", "")
        title = artifact.get("title", "build")
        kind = {"chart": "chart", "document": "document"}.get(
            artifact.get("type"), "web")
        target = "__build_out__" if "__build_out__" in self._by_id else ""
        self.bridge.request("place", kind=kind, title=title, payload=path,
                            slab_id=target)
        # Both ids are freed so the next build gets fresh panels and these two
        # stay where they are — the output panel IS the finished output.
        QTimer.singleShot(0, self._release_build_slab)

    def _release_build_slab(self):
        self._by_id.pop("__build_code__", None)
        self._by_id.pop("__build_out__", None)

    # ── the command line ──────────────────────────────────────────────────
    def _send(self):
        query = self._line.text().strip()
        if not query or (self._agent and self._agent.isRunning()):
            return
        self._line.clear()
        self.say("thinking…")
        self._go.setEnabled(False)

        from voice.agent_bridge import AgentThread
        self._agent = AgentThread(query)
        self._agent.response_ready.connect(self._answered)
        self._agent.start()

    def _answered(self, response):
        self._go.setEnabled(True)
        self.say(response.strip().replace("\n", "  ") if response else "done")

    # ── persistence ───────────────────────────────────────────────────────
    def remember(self):
        try:
            STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
            specs = [s.spec() for s in self.slabs
                     if s.payload and s is not self._by_id.get("__build__")]
            STATE_PATH.write_text(
                json.dumps({"slabs": specs}, indent=2), encoding="utf-8")
        except Exception as e:
            print(f"[Workshop] could not save layout: {e}")

    def restore(self):
        if not STATE_PATH.exists():
            return
        try:
            specs = json.loads(STATE_PATH.read_text(encoding="utf-8")).get("slabs", [])
        except Exception:
            return
        for spec in specs[:12]:
            payload = spec.get("payload", "")
            # A build whose file has since been scrapped would open an empty
            # slab; skip it rather than litter the surface.
            if not payload or (not payload.startswith("http")
                               and not os.path.exists(payload)):
                continue
            self.place(spec.get("kind", "text"), spec.get("title", ""), payload,
                       at=(spec.get("x", 70), spec.get("y", 90)),
                       size=(spec.get("w", 560), spec.get("h", 400)))

    # ── chrome ────────────────────────────────────────────────────────────
    def _bake_backdrop(self, size):
        """The room — black, a tinted wash and the bench grid — as a pixmap."""
        dpr = self.devicePixelRatioF()
        pm = QPixmap(max(1, int(size.width() * dpr)), max(1, int(size.height() * dpr)))
        pm.setDevicePixelRatio(dpr)
        p = QPainter(pm)
        r = QRect(0, 0, size.width(), size.height())
        p.fillRect(r, QColor(*W.Workshop.BLACK))

        # A faint tinted wash so the room is not flat black.
        wash = QRadialGradient(r.center(), max(r.width(), r.height()) * 0.75)
        wash.setColorAt(0.0, QColor(*S.ground_rgb, 210))
        wash.setColorAt(1.0, QColor(*W.Workshop.BLACK, 255))
        p.fillRect(r, wash)

        # Bench grid — the surface you are putting things down on.
        grid = QColor(*S.accent_rgb, 15)
        p.setPen(QPen(grid, 1))
        for x in range(0, r.width(), 48):
            p.drawLine(x, 44, x, r.height() - 52)
        for y in range(44, r.height() - 52, 48):
            p.drawLine(0, y, r.width(), y)
        p.end()
        return pm

    def paintEvent(self, event):
        # Drawn live, the full-screen gradient and grid were redone on every
        # repaint, i.e. on every step of a slab drag. Baked once at the full
        # workshop size and blitted, only the uncovered part is repainted.
        # Mid-animation, when the workshop is smaller, the same pixmap is
        # scaled rather than rebaked at every intermediate size.
        parent = self.parentWidget()
        full = parent.size() if (self._embedded and parent is not None) else self.size()
        if self._backdrop is None or self._backdrop_size != full:
            self._backdrop = self._bake_backdrop(full)
            self._backdrop_size = full

        p = QPainter(self)
        p.setClipRegion(event.region())
        if self.size() == full:
            p.drawPixmap(0, 0, self._backdrop)
        else:
            p.drawPixmap(self.rect(), self._backdrop)
        p.end()

    def wheelEvent(self, event):
        """
        Swallow the wheel.

        Embedded, this sits on the QGraphicsView that spins the planet ring —
        without this, scrolling a slab also rotates the solar system behind it.
        """
        event.accept()

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Escape:
            self.close()
        elif event.key() == Qt.Key_L and event.modifiers() & Qt.ControlModifier:
            self._line.setFocus()
            self._line.selectAll()
        else:
            super().keyPressEvent(event)

    # ── visibility owns the hook ──────────────────────────────────────────
    def showEvent(self, event):
        super().showEvent(event)
        # Polishing (which happens just before the first show) runs the style
        # sheet inherited from Helio's overlay, and that wipes the opaque flag
        # set in __init__. Measured: without this, a slab drag re-rendered the
        # hidden solar system 62 times a second; with it, never.
        self.setAttribute(Qt.WA_OpaquePaintEvent, True)
        try:
            from tools.system import workshop_tool
            workshop_tool.ON_WORKSHOP_REQUEST = self.bridge.request
            self._publish()
            held = workshop_tool.drain_pending()
            if held:
                self.say(f"put up {held} thing" + ("" if held == 1 else "s")
                         + " you asked for earlier")
        except Exception as e:
            print(f"[Workshop] tool hook failed: {e}")

    def changeEvent(self, event):
        super().changeEvent(event)
        # A later style change re-polishes and would clear it again.
        if event.type() == QEvent.StyleChange:
            self.setAttribute(Qt.WA_OpaquePaintEvent, True)

    def hideEvent(self, event):
        try:
            from tools.system import workshop_tool
            if workshop_tool.ON_WORKSHOP_REQUEST is self.bridge.request:
                workshop_tool.ON_WORKSHOP_REQUEST = None
            # Nothing is on screen while the surface is put away.
            workshop_tool.SURFACE = []
        except Exception:
            pass
        super().hideEvent(event)

    def closeEvent(self, event):
        global _CANVAS
        self.remember()
        self._beat.stop()

        if self._embedded:
            # A panel inside Helio's overlay. Closing it means going back to
            # the solar system, not tearing the surface down — the slabs and
            # anything mid-build are still there when you come back.
            self.hide()
            self.closed.emit()
            event.accept()
            return

        try:
            from tools.system import forge_tool
            forge_tool.remove_stream_listener(self._on_forge_stream)
            forge_tool.remove_artifact_listener(self._on_forge_done)
        except Exception:
            pass
        _CANVAS = None
        self.closed.emit()
        super().closeEvent(event)


def open_workshop(parent=None):
    """
    Open the workshop, or bring the open one forward. GUI thread only.

    With a parent it becomes a panel filling Helio's own overlay, so going
    into the workshop is a mode of the same window rather than closing Helio
    and opening a second full screen beside it.
    """
    global _CANVAS
    if _CANVAS is not None:
        try:
            if _CANVAS.parent() is not parent:
                # The surface was built for the other mode; start clean.
                _CANVAS.setParent(None)
                _CANVAS.deleteLater()
                _CANVAS = None
        except RuntimeError:
            _CANVAS = None

    if _CANVAS is None:
        _CANVAS = WorkshopCanvas(parent)

    if parent is not None:
        _CANVAS.setGeometry(parent.rect())
        _CANVAS.show()
    else:
        _CANVAS.showFullScreen()
        _CANVAS.activateWindow()

    _CANVAS.raise_()
    _CANVAS._line.setFocus()
    return _CANVAS
