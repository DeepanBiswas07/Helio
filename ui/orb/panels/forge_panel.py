"""
forge_panel.py — THE FORGE: the small bench on the planet ring.

This is the *readout*, not the workshop. It says what is on the bench, lets
you read and edit its source, lists everything built, and takes a command —
all in a panel that has to fit inside a planet frame.

Anything that needs room or a live renderer belongs in the workshop
(ui/orb/workshop), which takes the whole screen. Deliberately there is no
QWebEngineView here: one cost nearly a second to construct at startup and
another second to re-render whenever the planet opened, which on a 2MB page
with inlined photos is exactly the stall this panel used to have.

  BAY 01 BENCH    what is on it, and a build streaming in as it is written
  BAY 02 SOURCE   its source, editable, save writes it back
  BAY 03 RACK     everything built so far
"""
import os

from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QLabel, QPushButton,
    QFrame, QScrollArea, QPlainTextEdit, QLineEdit, QSizePolicy,
)
from PyQt5.QtCore import Qt, QTimer, QObject, QThread, pyqtSignal
from PyQt5.QtGui import QColor, QPixmap

from .base import BasePanel, _content_alpha, _draw_header
from . import workshop_style as W

S = W.FORGE
ACCENT = QColor(*S.accent_rgb)

# The panel lives inside a planet frame. Every child is kept small enough that
# the whole thing can shrink into it — an oversized minimum here does not
# clip, it pushes the widget out past the frame and under the orbiting planet.
THUMB = (168, 124)
MIN_PANEL = (380, 260)


def _tools():
    try:
        from tools.system import forge_tool
        return forge_tool
    except Exception:
        return None


def _kind_mark(kind):
    return {"chart": "CHT", "website": "WEB", "app": "APP",
            "document": "DOC", "script": "SRC", "code": "SRC",
            "image": "IMG", "diff": "DIF"}.get(kind, "OBJ")


def _shorten(text, limit=46):
    """Cut a spoken brief to a headline without slicing a word in half."""
    text = " ".join((text or "").split())
    if len(text) <= limit:
        return text
    cut = text[:limit]
    space = cut.rfind(" ")
    return (cut[:space] if space > limit * 0.6 else cut).rstrip(" ,;:-") + "…"


# ═══════════════════════════════════════════════════════════════════════════
#  THREAD BRIDGE
#
#  The forge tools run on the agent's worker thread and call back from there.
#  Touching a widget from that thread is how you get a native crash with no
#  traceback, so every callback becomes a signal and lands on the GUI thread.
# ═══════════════════════════════════════════════════════════════════════════
class _ForgeBridge(QObject):
    started = pyqtSignal(str)
    chunk = pyqtSignal(str)
    finished = pyqtSignal(str)
    failed = pyqtSignal(str)
    artifact = pyqtSignal(dict)

    def on_stream(self, event, payload):
        if event == "start":
            self.started.emit((payload or {}).get("brief", ""))
        elif event == "chunk":
            self.chunk.emit(payload or "")
        elif event == "done":
            self.finished.emit(payload or "")
        elif event == "error":
            self.failed.emit(str(payload or "unknown error"))

    def on_artifact(self, artifact):
        self.artifact.emit(artifact or {})


class _BuildWorker(QObject):
    """A build calls the model; that cannot happen on the GUI thread."""

    done = pyqtSignal(str)

    def __init__(self, brief):
        super().__init__()
        self.brief = brief

    def run(self):
        tools = _tools()
        if tools is None:
            self.done.emit("Forge tools unavailable.")
            return
        try:
            self.done.emit(tools.handle_forge_create_website({"topic": self.brief}))
        except Exception as e:
            self.done.emit(f"Build failed: {e}")


# ═══════════════════════════════════════════════════════════════════════════
#  THE PAINTER-ONLY PLANET PANEL
# ═══════════════════════════════════════════════════════════════════════════
class ForgePanel(BasePanel):
    def __init__(self):
        super().__init__("THE FORGE", ACCENT)

    def draw(self, painter, shape_rect, panel_progress, alpha):
        content_a = _content_alpha(panel_progress, alpha)
        _draw_header(painter, shape_rect, self.title, self.accent, content_a)


# ═══════════════════════════════════════════════════════════════════════════
#  ONE ROW ON THE RACK
# ═══════════════════════════════════════════════════════════════════════════
class _RackRow(QFrame):
    load = pyqtSignal(dict)
    drop = pyqtSignal(dict)

    def __init__(self, item, parent=None):
        super().__init__(parent)
        self.item = item
        self.setStyleSheet(S.ROW)
        self.setFixedHeight(30)

        row = QHBoxLayout(self)
        row.setContentsMargins(8, 0, 6, 0)
        row.setSpacing(8)

        mark = QLabel(_kind_mark(item.get("type")))
        mark.setFont(W.mono_font(8, bold=True))
        mark.setFixedWidth(28)
        mark.setStyleSheet(f"color:{S.ACCENT_D};{W.TRANSPARENT_LABEL}")
        row.addWidget(mark)

        title = QLabel(_shorten(item.get("title", "Untitled"), 40))
        title.setFont(W.mono_font(9))
        title.setMinimumWidth(0)
        title.setStyleSheet(f"color:{S.TEXT};{W.TRANSPARENT_LABEL}")
        row.addWidget(title, 1)

        when = QLabel((item.get("created_at", "") or "")[5:16].replace("-", "/"))
        when.setFont(W.mono_font(8))
        when.setStyleSheet(f"color:{S.DIM};{W.TRANSPARENT_LABEL}")
        row.addWidget(when)

        put = QPushButton("BENCH")
        put.setFont(W.label_font(8, tracking=1.5))
        put.setCursor(Qt.PointingHandCursor)
        put.setStyleSheet(S.CHIP)
        put.clicked.connect(lambda: self.load.emit(self.item))
        row.addWidget(put)

        scrap = QPushButton("SCRAP")
        scrap.setFont(W.label_font(8, tracking=1.5))
        scrap.setCursor(Qt.PointingHandCursor)
        scrap.setStyleSheet(S.ENGRAVED_WARN)
        scrap.clicked.connect(lambda: self.drop.emit(self.item))
        row.addWidget(scrap)


# ═══════════════════════════════════════════════════════════════════════════
#  THE BENCH
# ═══════════════════════════════════════════════════════════════════════════
class ForgeWidget(QWidget):
    # Raised the moment a build begins, so the overlay can swing the Forge
    # into view rather than the build happening off screen.
    build_began = pyqtSignal()
    # The bench is a panel; the workshop is the whole screen.
    workshop_wanted = pyqtSignal()
    # Raised when a build is handed to the browser or another app: Helio is
    # always-on-top, so without this it stays in front of the thing it opened.
    handed_off = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.active_artifact = {}
        self._bay = "bench"
        self._building = False
        self._buffer = ""
        self._thread = None
        self._worker = None
        self._pixmap = QPixmap()
        self._loaded_path = None

        self._bridge = _ForgeBridge()
        self._bridge.started.connect(self._build_started)
        self._bridge.chunk.connect(self._build_chunk)
        self._bridge.finished.connect(self._build_finished)
        self._bridge.failed.connect(self._build_failed)
        self._bridge.artifact.connect(self.set_artifact)

        self.setMinimumSize(*MIN_PANEL)
        self._build_ui()
        self._hook_backend()
        self.refresh()

    # ── construction ──────────────────────────────────────────────────────
    def _build_ui(self):
        self.setStyleSheet(f"QLabel{{{W.TRANSPARENT_LABEL}}}")
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 12)
        root.setSpacing(0)

        # ── bay selectors ────────────────────────────────────────────────
        bays = QHBoxLayout()
        bays.setSpacing(6)
        bays.setContentsMargins(0, 0, 0, 9)
        self._bay_bench = self._make_bay("01", "BENCH", True)
        self._bay_source = self._make_bay("02", "SOURCE", False)
        self._bay_rack = self._make_bay("03", "RACK", False)
        self._bay_bench.clicked.connect(lambda: self._set_bay("bench"))
        self._bay_source.clicked.connect(lambda: self._set_bay("source"))
        self._bay_rack.clicked.connect(lambda: self._set_bay("rack"))
        for widget in (self._bay_bench, self._bay_source, self._bay_rack):
            bays.addWidget(widget)
        bays.addStretch()

        self._status = QLabel("")
        self._status.setFont(W.mono_font(8))
        self._status.setMinimumWidth(0)
        self._status.setStyleSheet(f"color:{S.MUTED};{W.TRANSPARENT_LABEL}")
        bays.addWidget(self._status)
        root.addLayout(bays)

        self._build_bench(root)
        self._build_source(root)
        self._build_rack(root)

        # ── the command line, always available ───────────────────────────
        root.addWidget(self._rule(S.RULE))
        entry = QHBoxLayout()
        entry.setContentsMargins(0, 8, 0, 0)
        entry.setSpacing(8)
        entry.addWidget(self._caption("FORGE"))
        self._brief = QLineEdit()
        self._brief.setFont(W.mono_font(10))
        self._brief.setMinimumWidth(0)
        self._brief.setStyleSheet(S.INPUT)
        self._brief.setPlaceholderText("a site for the bakery   ·   a tip calculator")
        self._brief.returnPressed.connect(self._forge_it)
        entry.addWidget(self._brief, 1)
        self._go = QPushButton("FORGE IT")
        self._go.setFont(W.label_font(9, bold=True, tracking=1.7))
        self._go.setCursor(Qt.PointingHandCursor)
        self._go.setStyleSheet(S.COMMAND)
        self._go.clicked.connect(self._forge_it)
        entry.addWidget(self._go)
        root.addLayout(entry)

    def _build_bench(self, root):
        self._bench_page = QWidget()
        self._bench_page.setStyleSheet(f"background:{S.PANEL_GROUND};")
        self._bench_page.setMinimumWidth(0)
        page = QVBoxLayout(self._bench_page)
        page.setContentsMargins(14, 12, 14, 12)
        page.setSpacing(9)

        head = QHBoxLayout()
        head.setSpacing(9)
        self._mark = QLabel("EMPTY")
        self._mark.setFont(W.label_font(8, bold=True, tracking=1.9))
        self._mark.setStyleSheet(f"color:{S.ACCENT_D};{W.TRANSPARENT_LABEL}")
        head.addWidget(self._mark)
        self._title = QLabel("Nothing on the bench")
        self._title.setFont(W.label_font(11, bold=True, tracking=2.0))
        self._title.setMinimumWidth(0)
        self._title.setStyleSheet(f"color:{S.BRIGHT};{W.TRANSPARENT_LABEL}")
        head.addWidget(self._title, 1)
        page.addLayout(head)

        # ── what it is: a thumbnail beside a spec plate ──────────────────
        self._detail = QWidget()
        self._detail.setMinimumWidth(0)
        # Exactly one surface paints the ground. A bare background: in a
        # parent's stylesheet cascades, so a nested plain QWidget repaints the
        # same translucent fill and the alphas stack into a dark slab.
        self._detail.setStyleSheet("background:transparent;")
        detail = QHBoxLayout(self._detail)
        detail.setContentsMargins(0, 0, 0, 0)
        detail.setSpacing(14)

        self._thumb = QLabel()
        self._thumb.setFixedSize(*THUMB)
        self._thumb.setAlignment(Qt.AlignCenter)
        self._thumb.setStyleSheet(
            f"background:{S.GROUND};border:1px solid {S.RULE};color:{S.DIM};")
        detail.addWidget(self._thumb, 0, Qt.AlignTop)

        specs = QWidget()
        specs.setMinimumWidth(0)
        specs.setStyleSheet("background:transparent;")
        self._specs = QGridLayout(specs)
        self._specs.setContentsMargins(0, 0, 0, 0)
        self._specs.setHorizontalSpacing(12)
        self._specs.setVerticalSpacing(5)
        self._specs.setColumnStretch(1, 1)
        self._spec_values = {}
        for row, label in enumerate(("KIND", "SIZE", "MADE", "FILE")):
            caption = self._caption(label)
            self._specs.addWidget(caption, row, 0, Qt.AlignTop)
            value = QLabel("")
            value.setFont(W.mono_font(9))
            value.setMinimumWidth(0)
            value.setWordWrap(True)
            value.setStyleSheet(f"color:{S.TEXT};{W.TRANSPARENT_LABEL}")
            self._specs.addWidget(value, row, 1)
            self._spec_values[label] = value
        self._specs.setRowStretch(4, 1)
        detail.addWidget(specs, 1)
        self._detail.setFixedHeight(THUMB[1])
        page.addWidget(self._detail)

        # ── a build, streaming in as it is written ───────────────────────
        self._stream = QPlainTextEdit()
        self._stream.setReadOnly(True)
        self._stream.setFont(W.mono_font(8))
        self._stream.setLineWrapMode(QPlainTextEdit.NoWrap)
        self._stream.setMinimumSize(0, 0)
        self._stream.setStyleSheet(
            f"QPlainTextEdit{{background:{S.GROUND};color:{S.ACCENT_D};"
            f"border:1px solid {S.RULE};padding:8px;}}" + S.SCROLL)
        self._stream.hide()
        page.addWidget(self._stream, 1)

        # ── the tail of the rack, so a glance answers "what have I made" ──
        self._recent_head = self._caption("RECENT")
        page.addWidget(self._recent_head)
        self._recent = QWidget()
        self._recent.setStyleSheet("background:transparent;")
        self._recent.setMinimumWidth(0)
        self._recent_list = QVBoxLayout(self._recent)
        self._recent_list.setContentsMargins(0, 0, 0, 0)
        self._recent_list.setSpacing(0)
        page.addWidget(self._recent)

        page.addStretch()
        page.addWidget(self._rule(S.RULE))

        # ── what you can do with it ──────────────────────────────────────
        actions = QHBoxLayout()
        actions.setSpacing(8)
        actions.setContentsMargins(0, 4, 0, 0)
        self._workshop_btn = QPushButton("OPEN WORKSHOP")
        self._workshop_btn.setFont(W.label_font(9, bold=True, tracking=1.7))
        self._workshop_btn.setCursor(Qt.PointingHandCursor)
        self._workshop_btn.setStyleSheet(S.COMMAND)
        self._workshop_btn.clicked.connect(self.workshop_wanted.emit)
        actions.addWidget(self._workshop_btn)

        self._open_btn = QPushButton("OPEN FILE")
        self._open_btn.setFont(W.label_font(9, tracking=1.7))
        self._open_btn.setCursor(Qt.PointingHandCursor)
        self._open_btn.setStyleSheet(S.CHIP)
        self._open_btn.clicked.connect(self._open_external)
        actions.addWidget(self._open_btn)
        actions.addStretch()

        hint = QLabel("live preview lives in the workshop")
        hint.setFont(W.mono_font(8))
        hint.setMinimumWidth(0)
        hint.setStyleSheet(f"color:{S.DIM};{W.TRANSPARENT_LABEL}")
        actions.addWidget(hint)
        page.addLayout(actions)

        root.addWidget(self._bench_page, 1)

    def _build_source(self, root):
        self._source_page = QWidget()
        self._source_page.setStyleSheet(f"background:{S.PANEL_GROUND};")
        self._source_page.setMinimumWidth(0)
        page = QVBoxLayout(self._source_page)
        page.setContentsMargins(14, 12, 14, 12)
        page.setSpacing(7)

        head = QHBoxLayout()
        head.addWidget(self._caption("SOURCE"))
        head.addStretch()
        self._path = QLabel("")
        self._path.setFont(W.mono_font(8))
        self._path.setMinimumWidth(0)
        self._path.setStyleSheet(f"color:{S.DIM};{W.TRANSPARENT_LABEL}")
        head.addWidget(self._path)
        page.addLayout(head)

        self._editor = QPlainTextEdit()
        self._editor.setFont(W.mono_font(9))
        self._editor.setMinimumSize(0, 0)
        self._editor.setStyleSheet(
            f"QPlainTextEdit{{background:{S.GROUND};color:{S.TEXT};"
            f"border:1px solid {S.RULE};padding:9px;"
            f"selection-background-color:{S.ACCENT};selection-color:{S.GROUND};}}"
            + S.SCROLL)
        page.addWidget(self._editor, 1)

        foot = QHBoxLayout()
        foot.setSpacing(8)
        self._save = QPushButton("SAVE")
        self._save.setFont(W.label_font(9, bold=True, tracking=1.7))
        self._save.setCursor(Qt.PointingHandCursor)
        self._save.setStyleSheet(S.COMMAND)
        self._save.clicked.connect(self._save_source)
        foot.addWidget(self._save)
        foot.addStretch()
        self._saved_note = QLabel("")
        self._saved_note.setFont(W.mono_font(8))
        self._saved_note.setMinimumWidth(0)
        self._saved_note.setStyleSheet(f"color:{S.DIM};{W.TRANSPARENT_LABEL}")
        foot.addWidget(self._saved_note)
        page.addLayout(foot)
        root.addWidget(self._source_page, 1)
        self._source_page.hide()

    def _build_rack(self, root):
        self._rack_page = QWidget()
        self._rack_page.setStyleSheet(f"background:{S.PANEL_GROUND};")
        self._rack_page.setMinimumWidth(0)
        page = QVBoxLayout(self._rack_page)
        page.setContentsMargins(14, 12, 14, 12)
        page.setSpacing(5)

        head = QHBoxLayout()
        title = QLabel("EVERYTHING BUILT")
        title.setFont(W.label_font(11, bold=True, tracking=2.2))
        title.setStyleSheet(f"color:{S.BRIGHT};{W.TRANSPARENT_LABEL}")
        head.addWidget(title)
        head.addStretch()
        self._rack_note = QLabel("")
        self._rack_note.setFont(W.mono_font(8))
        self._rack_note.setStyleSheet(f"color:{S.DIM};{W.TRANSPARENT_LABEL}")
        head.addWidget(self._rack_note)
        page.addLayout(head)
        page.addSpacing(3)

        self._rack_scroll, self._rack_list = self._make_list()
        page.addWidget(self._rack_scroll, 1)
        root.addWidget(self._rack_page, 1)
        self._rack_page.hide()

    # ── small shared pieces ───────────────────────────────────────────────
    def _make_bay(self, index, name, active):
        button = QPushButton(f"{index}  {name}")
        button.setFont(W.label_font(9, tracking=1.9))
        button.setCursor(Qt.PointingHandCursor)
        button.setStyleSheet(S.BAY_ACTIVE if active else S.BAY_IDLE)
        return button

    def _rule(self, colour):
        line = QFrame()
        line.setFixedHeight(1)
        line.setStyleSheet(f"background:{colour};border:none;")
        return line

    def _caption(self, text):
        label = QLabel(text)
        label.setFont(W.label_font(8, tracking=1.9))
        label.setStyleSheet(f"color:{S.DIM};{W.TRANSPARENT_LABEL}")
        return label

    def _make_list(self):
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setMinimumSize(0, 0)
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

    def _set_bay(self, bay):
        """Exclusive, like every other planet — stations, not tabs."""
        self._bay = bay
        for name, button in (("bench", self._bay_bench),
                             ("source", self._bay_source),
                             ("rack", self._bay_rack)):
            button.setStyleSheet(S.BAY_ACTIVE if name == bay else S.BAY_IDLE)
        self._bench_page.setVisible(bay == "bench")
        self._source_page.setVisible(bay == "source")
        self._rack_page.setVisible(bay == "rack")
        if bay == "rack":
            self._render_rack()
        elif bay == "source":
            self._load_source()
        self.update()

    # ── backend wiring ────────────────────────────────────────────────────
    def _hook_backend(self):
        tools = _tools()
        if tools:
            # Register, never assign: the workshop canvas watches the same
            # builds, and whoever assigned last used to unhook the other.
            tools.add_stream_listener(self._bridge.on_stream)
            tools.add_artifact_listener(self._bridge.on_artifact)

    # ── running a build ───────────────────────────────────────────────────
    def _forge_it(self):
        brief = self._brief.text().strip()
        if not brief or self._building:
            return
        self._brief.clear()
        self.start_build(brief)

    def start_build(self, brief):
        if self._building:
            return
        self._thread = QThread()
        self._worker = _BuildWorker(brief)
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._worker.done.connect(self._build_returned)
        self._thread.start()

    def _build_returned(self, message):
        if self._thread:
            self._thread.quit()
        self._status.setText(_shorten(message, 44))

    # ── the watched build ─────────────────────────────────────────────────
    def _build_started(self, brief):
        self.build_began.emit()
        self._building = True
        self._buffer = ""
        self._go.setEnabled(False)
        self._set_bay("bench")

        self._mark.setText("FORGING")
        self._title.setText(_shorten(brief) if brief else "Building")
        self._detail.hide()
        self._recent.hide()
        self._recent_head.hide()
        self._stream.clear()
        self._stream.show()
        self._status.setText("0 bytes")

    def _build_chunk(self, fragment):
        self._buffer += fragment
        # A tail, not the whole file — re-laying out megabytes on every chunk
        # is what makes a stream stutter.
        self._stream.setPlainText(self._buffer[-2400:])
        self._stream.verticalScrollBar().setValue(
            self._stream.verticalScrollBar().maximum())
        self._status.setText(f"{len(self._buffer):,} bytes")

    def _build_finished(self, html):
        self._building = False
        self._go.setEnabled(True)
        self._status.setText(f"built · {len(html):,} bytes")

    def _build_failed(self, reason):
        self._building = False
        self._go.setEnabled(True)
        self._stream.hide()
        self._detail.show()
        self._recent.show()
        self._status.setText(f"failed · {_shorten(reason, 34)}")

    # ── what is on the bench ──────────────────────────────────────────────
    def set_artifact(self, artifact):
        self.active_artifact = artifact or {}
        self._loaded_path = None
        self.refresh()

    def refresh(self):
        tools = _tools()
        if not self.active_artifact and tools:
            self.active_artifact = tools.get_active_artifact()
        if self._building:
            return

        self._stream.hide()
        self._detail.show()
        self._recent.show()
        self._render_recent()

        art = self.active_artifact
        if not art or not art.get("title"):
            self._mark.setText("EMPTY")
            self._title.setText("Nothing on the bench")
            self._thumb.setPixmap(QPixmap())
            self._thumb.setText("nothing\nyet")
            for value in self._spec_values.values():
                value.setText("")
            self._open_btn.setEnabled(False)
            self._path.setText("")
            self._editor.setPlainText("")
            return

        kind = art.get("type", "artifact")
        path = art.get("file_path", "")
        exists = bool(path and os.path.exists(path))

        self._mark.setText(_kind_mark(kind))
        self._title.setText(_shorten(art.get("title", "Untitled"), 42))
        self._open_btn.setEnabled(exists)

        size = os.path.getsize(path) if exists else 0
        files = art.get("files") or []
        self._spec_values["KIND"].setText(
            f"{kind}  ·  {len(files)} files" if len(files) > 1 else kind)
        self._spec_values["SIZE"].setText(f"{size:,} bytes" if size else "—")
        self._spec_values["MADE"].setText(art.get("created_at", "")[5:16] or "—")
        if len(files) > 1:
            self._spec_values["FILE"].setText(
                os.path.basename(art.get("root", "")) + "/  "
                + ", ".join(files[:4]) + ("…" if len(files) > 4 else ""))
        else:
            self._spec_values["FILE"].setText(
                os.path.basename(path) if path else "—")

        # A picture or a chart gets a real thumbnail; everything else says so.
        # Rendering a page would mean a browser engine in this panel, which is
        # what made opening it slow — the workshop shows those live instead.
        if exists and kind in ("chart", "image"):
            pixmap = QPixmap(path)
            if not pixmap.isNull():
                self._thumb.setPixmap(pixmap.scaled(
                    THUMB[0] - 2, THUMB[1] - 2,
                    Qt.KeepAspectRatio, Qt.SmoothTransformation))
            else:
                self._thumb.setPixmap(QPixmap())
                self._thumb.setText("unreadable")
        else:
            self._thumb.setPixmap(QPixmap())
            self._thumb.setText(
                "open the\nworkshop\nto see it live" if exists else "no file")

        self._path.setText(os.path.basename(path) if path else "")
        if self._bay == "source":
            self._load_source()

    def _load_source(self):
        """Read the file only when the source bay is actually looked at."""
        art = self.active_artifact
        path = art.get("file_path", "")
        if self._loaded_path == path:
            return

        if path and os.path.exists(path) and art.get("type") not in ("chart", "image"):
            try:
                with open(path, "r", encoding="utf-8", errors="replace") as f:
                    text = f.read()
            except Exception as e:
                text = f"could not read {path}: {e}"
            # An inlined photo is megabytes of base64 nobody can read or edit.
            tools = _tools()
            if tools and "data:image" in text[:400_000]:
                try:
                    text = tools._dehydrate(text)
                except Exception:
                    pass
            self._editor.setPlainText(text)
            self._editor.setReadOnly(False)
            self._save.setEnabled(True)
        else:
            self._editor.setPlainText(art.get("code", "") or "(no editable source)")
            self._editor.setReadOnly(True)
            self._save.setEnabled(False)
        self._loaded_path = path

    # ── the rack ──────────────────────────────────────────────────────────
    def _render_recent(self, limit=5):
        """The last few builds, under whatever is on the bench."""
        while self._recent_list.count():
            entry = self._recent_list.takeAt(0)
            widget = entry.widget()
            if widget is not None:
                widget.setParent(None)
                widget.deleteLater()

        tools = _tools()
        history = tools.list_forge_history() if tools else []
        current = self.active_artifact.get("file_path")
        rows = [h for h in history if h.get("file_path") != current][:limit]

        self._recent_head.setVisible(bool(rows))
        for item in rows:
            row = _RackRow(item)
            row.load.connect(self._load_from_rack)
            row.drop.connect(self._scrap)
            self._recent_list.addWidget(row)

    def _render_rack(self):
        while self._rack_list.count():
            entry = self._rack_list.takeAt(0)
            widget = entry.widget()
            if widget is not None:
                widget.setParent(None)
                widget.deleteLater()

        tools = _tools()
        history = tools.list_forge_history() if tools else []
        if not history:
            empty = QLabel("Nothing built yet.")
            empty.setFont(W.mono_font(9))
            empty.setStyleSheet(f"color:{S.DIM};{W.TRANSPARENT_LABEL}")
            self._rack_list.addWidget(empty)
            self._rack_list.addStretch()
            self._rack_note.setText("")
            return

        self._rack_note.setText(
            f"{len(history)} build" + ("" if len(history) == 1 else "s"))
        for item in history[:40]:
            row = _RackRow(item)
            row.load.connect(self._load_from_rack)
            row.drop.connect(self._scrap)
            self._rack_list.addWidget(row)
        self._rack_list.addStretch()

    def _load_from_rack(self, item):
        self.set_artifact(item)
        self._set_bay("bench")

    def _scrap(self, item):
        tools = _tools()
        if tools:
            tools.delete_artifact(item.get("file_path", ""))
        if self.active_artifact.get("file_path") == item.get("file_path"):
            self.active_artifact = {}
            self.refresh()
        self._render_rack()

    # ── bench controls ────────────────────────────────────────────────────
    def _open_external(self):
        path = self.active_artifact.get("file_path")
        if path and os.path.exists(path):
            try:
                os.startfile(path)
                self.handed_off.emit()
            except Exception as e:
                self._status.setText(f"could not open: {e}")

    def _save_source(self):
        path = self.active_artifact.get("file_path")
        if not path or not os.path.exists(path):
            return
        text = self._editor.toPlainText()
        tools = _tools()
        # Always re-embed. The editor shows the dehydrated page — pictures as
        # bare filenames, marker and all stripped — so gating on the marker
        # meant a save wrote that straight back and the page lost its photos.
        # On anything without a local <img> this is a no-op.
        if tools:
            try:
                text, restored = tools._embed_local_images(text)
                if restored:
                    print(f"[Forge] put {restored} picture(s) back on save")
            except Exception:
                pass
        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write(text)
        except Exception as e:
            self._saved_note.setText(f"could not save: {e}")
            return

        self._loaded_path = None
        self._saved_note.setText("saved")
        QTimer.singleShot(2200, lambda: self._saved_note.setText(""))

    # ── chrome ────────────────────────────────────────────────────────────
    def paintEvent(self, event):
        S.frame(self, exclude=(
            self._bench_page, self._source_page, self._rack_page))
