"""
files.py — The Files planet: a document-intelligence console.

FilesPanel is the painter-only renderer (header only — the body is the widget).
FilesWidget is the real interactive panel, with two views:

  SEARCH   streaming filesystem search, type filters, and per-file AI actions
           (open / reveal / summarize / remember)
  LIBRARY  everything Helio has committed to file-knowledge memory, with
           forget + ask-in-chat

Filesystem access is strictly read-only: nothing here renames, moves or deletes
a real file. "Forget" only removes Helio's memory of a file.
"""
import os
import time
import datetime
import subprocess

from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton,
    QFrame, QScrollArea, QComboBox, QFileDialog, QApplication
)
from PyQt5.QtCore import Qt, QTimer, QThread, QObject, pyqtSignal, QRectF
from PyQt5.QtGui import QColor, QPainter, QPen, QFont, QRegion
from .base import BasePanel, _content_alpha, _draw_header
from . import files_style as S

ACCENT = "#00DCAA"

_KIND_LABELS = {
    ".pdf": "PDF", ".doc": "Word", ".docx": "Word",
    ".txt": "Text", ".ppt": "Slides", ".pptx": "Slides",
    ".xls": "Sheet", ".xlsx": "Sheet",
}

_KIND_ICONS = {
    ".pdf": "📕", ".doc": "📘", ".docx": "📘",
    ".txt": "📄", ".ppt": "📙", ".pptx": "📙",
    ".xls": "📗", ".xlsx": "📗",
}

# Filter chips -> the extensions each one accepts
_FILTERS = [
    ("All", None),
    ("PDF", (".pdf",)),
    ("Word", (".doc", ".docx")),
    ("Sheet", (".xls", ".xlsx")),
    ("Slides", (".ppt", ".pptx")),
    ("Text", (".txt",)),
]

# Readers exist for these; .doc/.ppt are legacy binary formats with no reader.
_UNREADABLE = (".doc", ".ppt")


# ── lazy backends (UI still loads if src/ isn't importable) ───────────────────

def _search_backend():
    try:
        from tools.file_ops.file_search import iter_search_files, SEARCH_DRIVES
        return iter_search_files, SEARCH_DRIVES
    except Exception:
        return None, []


def _knowledge_backend():
    try:
        from memory.semantic_memory import (
            list_indexed_files, index_file_knowledge, forget_file_knowledge
        )
        return list_indexed_files, index_file_knowledge, forget_file_knowledge
    except Exception:
        return None, None, None


def _read_file_text(path):
    """Extract text from a document, or '' if this format has no reader."""
    ext = os.path.splitext(path)[1].lower()
    if ext in _UNREADABLE:
        return ""
    try:
        from tools.file_ops.file_reader import (
            read_pdf, read_text, read_docx, read_pptx, read_excel
        )
    except Exception:
        return ""

    try:
        if ext == ".pdf":
            return read_pdf(path)
        if ext == ".docx":
            return read_docx(path)
        if ext == ".pptx":
            return read_pptx(path)
        if ext in (".xls", ".xlsx"):
            return read_excel(path)
        return read_text(path)
    except Exception:
        return ""


def _clean_llm(text):
    """Strip the reasoning-model <think> blocks and reject error strings."""
    if not text or text.startswith("LLM Error:"):
        return ""
    import re
    return re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()


def _fmt_size(path):
    try:
        size = os.path.getsize(path)
    except OSError:
        return "—"
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024.0
    return "—"


def _fmt_date(mtime):
    try:
        return datetime.datetime.fromtimestamp(mtime).strftime("%d %b %Y")
    except Exception:
        return "—"


def _elide(text, limit):
    return text if len(text) <= limit else text[: limit - 1] + "…"


# ── workers ──────────────────────────────────────────────────────────────────

class _Cancelled(Exception):
    """Raised inside the walk callback to abandon a search promptly."""


class _SearchWorker(QObject):
    """Streams filesystem hits so results appear immediately, not after ~60s."""
    hits = pyqtSignal(list)      # [(score, mtime, path), ...]
    progress = pyqtSignal(str)   # directory currently being scanned
    finished_search = pyqtSignal(int)

    def __init__(self, query, root=None):
        super().__init__()
        self.query = query
        self.root = root
        self._cancelled = False

    def cancel(self):
        self._cancelled = True

    def run(self):
        iter_search_files, _ = _search_backend()
        if not iter_search_files:
            self.finished_search.emit(0)
            return

        state = {"batch": [], "total": 0, "last": time.time(), "dir": ""}

        def flush(force=False):
            """Push whatever we have to the UI.

            This has to be reachable from the directory callback, not just the
            hit loop: a walk can go minutes between matches, and hits found in
            the first millisecond would otherwise sit here unseen until the
            next one turned up.
            """
            now = time.time()
            if not force and (now - state["last"]) < 0.35:
                return
            if state["batch"]:
                self.hits.emit(state["batch"])
                state["batch"] = []
            self.progress.emit(state["dir"])
            state["last"] = now

        def on_dir(directory):
            state["dir"] = directory
            if self._cancelled:
                raise _Cancelled()
            flush()

        try:
            for hit in iter_search_files(self.query, root=self.root,
                                         max_results=200, progress_cb=on_dir):
                if self._cancelled:
                    break
                state["batch"].append(hit)
                state["total"] += 1
                if len(state["batch"]) >= 8:
                    flush(force=True)
        except _Cancelled:
            pass
        except Exception:
            pass

        if state["batch"] and not self._cancelled:
            self.hits.emit(state["batch"])

        self.finished_search.emit(state["total"])


class _DocWorker(QObject):
    """Reads a document and asks the LLM to summarize it (and tag it)."""
    done = pyqtSignal(str, str, list)   # path, summary, topics
    failed = pyqtSignal(str, str)       # path, reason

    def __init__(self, path, want_topics=False):
        super().__init__()
        self.path = path
        self.want_topics = want_topics

    def run(self):
        content = _read_file_text(self.path)

        if not content or not content.strip():
            ext = os.path.splitext(self.path)[1].lower()
            if ext in _UNREADABLE:
                self.failed.emit(self.path, f"No reader for legacy {ext} files")
            else:
                self.failed.emit(self.path, "Couldn't extract any text")
            return

        if content.startswith("Error reading"):
            self.failed.emit(self.path, content[:60])
            return

        content = content[:6000]
        name = os.path.basename(self.path)

        if self.want_topics:
            prompt = (
                f"Summarize this document in 2-3 clear sentences, then list topic tags.\n"
                f"Respond in exactly this format:\n"
                f"SUMMARY: <your summary>\n"
                f"TOPICS: <comma-separated tags>\n\n"
                f"Filename: {name}\n\nContent:\n{content}"
            )
        else:
            prompt = (
                f"Summarize this document in 2-3 clear, plain sentences. "
                f"No preamble, just the summary.\n\n"
                f"Filename: {name}\n\nContent:\n{content}"
            )

        try:
            from core.llm import generate
        except Exception:
            self.failed.emit(self.path, "LLM unavailable")
            return

        raw = _clean_llm(generate(prompt, role="chat"))
        if not raw:
            self.failed.emit(self.path, "Model returned nothing")
            return

        summary, topics = raw, []
        if self.want_topics:
            summary_part, topics_part = raw, ""
            if "TOPICS:" in raw:
                summary_part, topics_part = raw.split("TOPICS:", 1)
            summary = summary_part.replace("SUMMARY:", "").strip()
            topics = [t.strip() for t in topics_part.split(",") if t.strip()][:6]

        self.done.emit(self.path, summary.strip(), topics)


class _LibraryLoader(QObject):
    done = pyqtSignal(list)

    def load(self):
        list_indexed_files, _, _ = _knowledge_backend()
        entries = []
        if list_indexed_files:
            try:
                entries = list_indexed_files() or []
            except Exception:
                entries = []
        entries.sort(key=lambda e: e.get("indexed_at", ""), reverse=True)
        self.done.emit(entries)


# ── rows ─────────────────────────────────────────────────────────────────────

class _FileRow(QFrame):
    """One search result, with read-only + AI actions."""
    open_requested = pyqtSignal(str)
    reveal_requested = pyqtSignal(str)
    summarize_requested = pyqtSignal(str, object)   # path, row
    remember_requested = pyqtSignal(str, object)

    def __init__(self, path, mtime, index=0, parent=None):
        super().__init__(parent)
        self.path = path
        self.setStyleSheet(S.ROW)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(10, 8, 12, 8)
        lay.setSpacing(4)

        ext = os.path.splitext(path)[1].lower()
        top = QHBoxLayout()
        top.setSpacing(12)

        # Index, so the list reads as a numbered readout rather than a card stack
        idx = QLabel(f"{index:02d}")
        idx.setFont(S.mono_font(9))
        idx.setFixedWidth(20)
        idx.setAlignment(Qt.AlignRight | Qt.AlignTop)
        idx.setStyleSheet(f"color:{S.RULE_HI};{S.TRANSPARENT_LABEL}")
        top.addWidget(idx)

        name_col = QVBoxLayout()
        name_col.setSpacing(1)
        name = QLabel(_elide(os.path.basename(path), 48))
        name.setFont(QFont("Segoe UI", 11))
        name.setStyleSheet(f"color:{S.BRIGHT};{S.TRANSPARENT_LABEL}")
        folder = QLabel(_elide(os.path.dirname(path), 62))
        folder.setFont(S.mono_font(8))
        folder.setStyleSheet(f"color:{S.DIM};{S.TRANSPARENT_LABEL}")
        name_col.addWidget(name)
        name_col.addWidget(folder)
        top.addLayout(name_col, 1)

        # Values in mono so size/date line up as columns down the list
        meta = QLabel(f"{_KIND_LABELS.get(ext, 'FILE').upper():>6}  {_fmt_size(path):>9}\n"
                      f"{_fmt_date(mtime).upper():>17}")
        meta.setFont(S.mono_font(8))
        meta.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        meta.setStyleSheet(f"color:{S.MUTED};{S.TRANSPARENT_LABEL}")
        top.addWidget(meta)

        glyphs = QHBoxLayout()
        glyphs.setSpacing(8)
        glyphs.setContentsMargins(16, 0, 0, 0)
        for label, tip, slot in (
            ("OPEN", "Open", lambda: self.open_requested.emit(self.path)),
            ("DIR", "Show in Explorer", lambda: self.reveal_requested.emit(self.path)),
            ("SUM", "Summarize with AI", lambda: self.summarize_requested.emit(self.path, self)),
            ("MEM", "Remember this file", lambda: self.remember_requested.emit(self.path, self)),
        ):
            btn = QPushButton(label)
            btn.setToolTip(tip)
            btn.setFont(S.mono_font(8))
            btn.setFixedHeight(18)
            btn.setFixedWidth(38)
            btn.setCursor(Qt.PointingHandCursor)
            btn.setStyleSheet(S.ROW_GLYPH)
            btn.clicked.connect(slot)
            glyphs.addWidget(btn)
        top.addLayout(glyphs)

        lay.addLayout(top)

        self.status = QLabel("")
        self.status.setWordWrap(True)
        self.status.setFont(S.mono_font(9))
        self.status.setStyleSheet(
            f"color:{S.TEXT};{S.TRANSPARENT_LABEL}"
            f"padding:6px 0 2px 32px;border-left:1px solid {S.RULE_HI};margin-left:8px;")
        self.status.hide()
        lay.addWidget(self.status)

    def show_status(self, text):
        self.status.setText(text)
        self.status.show()


class _KnowledgeRow(QFrame):
    """One file Helio remembers."""
    open_requested = pyqtSignal(str)
    ask_requested = pyqtSignal(str)
    forget_requested = pyqtSignal(str)

    def __init__(self, entry, index=0, parent=None):
        super().__init__(parent)
        self.path = entry.get("path", "")
        self.setStyleSheet(S.ROW)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(10, 9, 12, 10)
        lay.setSpacing(5)

        filename = entry.get("filename", "Unknown")
        ext = os.path.splitext(filename)[1].lower()

        head = QHBoxLayout()
        head.setSpacing(12)

        idx = QLabel(f"{index:02d}")
        idx.setFont(S.mono_font(9))
        idx.setFixedWidth(20)
        idx.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        idx.setStyleSheet(f"color:{S.RULE_HI};{S.TRANSPARENT_LABEL}")
        head.addWidget(idx)

        name = QLabel(_elide(filename, 44))
        name.setFont(QFont("Segoe UI", 11))
        name.setStyleSheet(f"color:{S.BRIGHT};{S.TRANSPARENT_LABEL}")
        head.addWidget(name, 1)

        kind = QLabel(_KIND_LABELS.get(ext, "FILE").upper())
        kind.setFont(S.mono_font(8))
        kind.setStyleSheet(f"color:{S.MUTED};{S.TRANSPARENT_LABEL}")
        head.addWidget(kind)

        when = QLabel(entry.get("indexed_at", "").upper())
        when.setFont(S.mono_font(8))
        when.setStyleSheet(f"color:{S.DIM};{S.TRANSPARENT_LABEL}")
        head.addWidget(when)

        for label, tip, slot in (
            ("OPEN", "Open", lambda: self.open_requested.emit(self.path)),
            ("ASK", "Ask Helio about this", lambda: self.ask_requested.emit(self.path)),
            ("DROP", "Forget (memory only)", lambda: self.forget_requested.emit(self.path)),
        ):
            btn = QPushButton(label)
            btn.setToolTip(tip)
            btn.setFont(S.mono_font(8))
            btn.setFixedHeight(18)
            btn.setFixedWidth(40)
            btn.setCursor(Qt.PointingHandCursor)
            btn.setStyleSheet(S.ROW_GLYPH)
            btn.clicked.connect(slot)
            head.addWidget(btn)

        lay.addLayout(head)

        body = QVBoxLayout()
        body.setContentsMargins(32, 0, 0, 0)
        body.setSpacing(4)

        summary = QLabel(_elide(entry.get("summary", ""), 260))
        summary.setWordWrap(True)
        summary.setFont(QFont("Segoe UI", 9))
        summary.setStyleSheet(f"color:{S.TEXT};{S.TRANSPARENT_LABEL}")
        body.addWidget(summary)

        topics = entry.get("topics", [])
        if topics:
            tags = QLabel("  ".join(t.upper() for t in topics[:6]))
            tags.setFont(S.mono_font(8))
            tags.setStyleSheet(f"color:{S.ACCENT};{S.TRANSPARENT_LABEL}")
            body.addWidget(tags)

        lay.addLayout(body)


# ── painter-only renderer ────────────────────────────────────────────────────

class FilesPanel(BasePanel):
    def __init__(self):
        super().__init__("FILES", QColor(0, 220, 170))

    def draw(self, painter, shape_rect, panel_progress, alpha):
        # Header only — FilesWidget paints the body.
        content_a = _content_alpha(panel_progress, alpha)
        _draw_header(painter, shape_rect, self.title, self.accent, content_a)


# ── the interactive panel ────────────────────────────────────────────────────

class FilesWidget(QWidget):
    ask_in_chat = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WA_TranslucentBackground)

        self._results = []          # [(score, mtime, path)]
        # Summaries are keyed by path, not held on the row widget: the result
        # list is rebuilt on every streamed batch, filter change and sort flip,
        # which destroys row widgets. Keyed state survives those rebuilds.
        self._row_status = {}
        self._filter_ext = None
        self._sort_newest = False
        self._view = "search"
        self._library = []
        self._search_thread = None
        self._search_worker = None
        self._doc_threads = []

        self._build_ui()
        self._rebuild_pending = False
        # Show the idle readout straight away, otherwise the first thing the
        # user sees is an empty rectangle.
        self._rebuild_results()

    # ── construction ─────────────────────────────────────────────────────
    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(22, 22, 22, 16)
        root.setSpacing(0)

        # ── command strip: tabs underlined, scope as a plain readout ──────
        head = QHBoxLayout()
        head.setSpacing(18)
        head.setContentsMargins(0, 0, 0, 8)

        self._tab_search = self._make_tab("SEARCH", True)
        self._tab_library = self._make_tab("LIBRARY", False)
        self._tab_search.clicked.connect(lambda: self._set_view("search"))
        self._tab_library.clicked.connect(lambda: self._set_view("library"))
        head.addWidget(self._tab_search)
        head.addWidget(self._tab_library)
        head.addStretch()

        self._scope_lbl = QLabel("SCOPE")
        self._scope_lbl.setFont(S.label_font(8, tracking=2.0))
        self._scope_lbl.setStyleSheet(f"color:{S.DIM};{S.TRANSPARENT_LABEL}")
        head.addWidget(self._scope_lbl)

        self._scope = QComboBox()
        self._scope.setFont(S.mono_font(9))
        self._scope.setCursor(Qt.PointingHandCursor)
        _, drives = _search_backend()
        self._scope.addItem("THIS PC", None)
        for d in drives:
            self._scope.addItem(d, d)
        self._scope.addItem("BROWSE…", "__browse__")
        self._scope.currentIndexChanged.connect(self._on_scope_changed)
        self._scope.setStyleSheet(S.COMBO)
        head.addWidget(self._scope)
        root.addLayout(head)

        root.addWidget(self._rule(S.RULE_HI))

        # ── query line: a prompt, not a search box ────────────────────────
        self._search_row = QWidget()
        srow = QHBoxLayout(self._search_row)
        srow.setContentsMargins(0, 10, 0, 0)
        srow.setSpacing(10)

        prompt = QLabel("▸")
        prompt.setFont(S.mono_font(11, bold=True))
        prompt.setStyleSheet(f"color:{S.ACCENT};{S.TRANSPARENT_LABEL}")
        srow.addWidget(prompt)

        self._search_box = QLineEdit()
        self._search_box.setPlaceholderText("query document index")
        self._search_box.setFont(S.mono_font(11))
        self._search_box.setStyleSheet(S.INPUT)
        self._search_box.returnPressed.connect(self._start_search)
        srow.addWidget(self._search_box, 1)

        self._go_btn = QPushButton("[ SCAN ]")
        self._go_btn.setFont(S.mono_font(9, bold=True))
        self._go_btn.setCursor(Qt.PointingHandCursor)
        self._go_btn.setStyleSheet(S.ACTION)
        self._go_btn.clicked.connect(self._start_search)
        srow.addWidget(self._go_btn)

        self._cancel_btn = QPushButton("[ ABORT ]")
        self._cancel_btn.setFont(S.mono_font(9, bold=True))
        self._cancel_btn.setCursor(Qt.PointingHandCursor)
        self._cancel_btn.setStyleSheet(S.ACTION_WARN)
        self._cancel_btn.clicked.connect(self._cancel_search)
        self._cancel_btn.hide()
        srow.addWidget(self._cancel_btn)
        root.addWidget(self._search_row)

        # Filter chips + sort
        self._chip_row = QWidget()
        crow = QHBoxLayout(self._chip_row)
        crow.setContentsMargins(0, 10, 0, 8)
        crow.setSpacing(4)

        type_lbl = QLabel("TYPE")
        type_lbl.setFont(S.label_font(8, tracking=2.0))
        type_lbl.setStyleSheet(f"color:{S.DIM};{S.TRANSPARENT_LABEL}margin-right:6px;")
        crow.addWidget(type_lbl)

        self._chips = []
        for i, (label, exts) in enumerate(_FILTERS):
            if i:
                sep = QLabel("·")
                sep.setFont(S.mono_font(8))
                sep.setStyleSheet(f"color:{S.RULE_HI};{S.TRANSPARENT_LABEL}")
                crow.addWidget(sep)
            chip = self._make_chip(label, exts)
            self._chips.append(chip)
            crow.addWidget(chip)
        crow.addStretch()

        self._sort_btn = QPushButton("SORT ▸ RELEVANCE")
        self._sort_btn.setFont(S.mono_font(8))
        self._sort_btn.setCursor(Qt.PointingHandCursor)
        self._sort_btn.setStyleSheet(S.FILTER_OFF)
        self._sort_btn.clicked.connect(self._toggle_sort)
        crow.addWidget(self._sort_btn)
        root.addWidget(self._chip_row)

        # Library filter (hidden while searching)
        self._lib_box = QLineEdit()
        self._lib_box.setPlaceholderText("filter remembered files")
        self._lib_box.setFont(S.mono_font(10))
        self._lib_box.setStyleSheet(S.INPUT)
        self._lib_box.textChanged.connect(self._rebuild_library)
        self._lib_box.hide()
        root.addWidget(self._lib_box)

        # Status readout
        self._status = QLabel("STANDBY · ENTER A QUERY")
        self._status.setFont(S.mono_font(8))
        self._status.setStyleSheet(
            f"color:{S.DIM};{S.TRANSPARENT_LABEL}padding:2px 0 6px 0;")
        root.addWidget(self._status)

        root.addWidget(self._rule(S.RULE))

        # Results list
        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setStyleSheet(S.SCROLL)
        self._list_container = QWidget()
        self._list_container.setStyleSheet(f"background:{S.LIST_GROUND};")
        self._list_layout = QVBoxLayout(self._list_container)
        self._list_layout.setContentsMargins(0, 0, 0, 0)
        self._list_layout.setSpacing(0)
        self._list_layout.addStretch()
        self._scroll.setWidget(self._list_container)
        root.addWidget(self._scroll, 1)

    def _rule(self, color):
        """A hairline. Framing here comes from rules, not from boxes."""
        line = QFrame()
        line.setFixedHeight(1)
        line.setStyleSheet(f"background:{color};border:none;")
        return line

    def _empty_block(self, title, detail):
        """An idle readout, so an empty list reads as a resting instrument
        rather than a blank rectangle."""
        holder = QWidget()
        holder.setAttribute(Qt.WA_TranslucentBackground)
        box = QVBoxLayout(holder)
        box.setContentsMargins(0, 54, 0, 0)
        box.setSpacing(9)

        head = QLabel(title)
        head.setFont(S.label_font(13, bold=True, tracking=4.0))
        head.setAlignment(Qt.AlignCenter)
        head.setStyleSheet(f"color:{S.RULE_HI};{S.TRANSPARENT_LABEL}")
        box.addWidget(head)

        sub = QLabel(detail)
        sub.setFont(S.mono_font(9))
        sub.setAlignment(Qt.AlignCenter)
        sub.setStyleSheet(f"color:{S.DIM};{S.TRANSPARENT_LABEL}")
        box.addWidget(sub)
        return holder

    def _make_tab(self, text, selected):
        btn = QPushButton(text)
        btn.setFont(S.label_font(11, bold=True, tracking=2.4))
        btn.setCursor(Qt.PointingHandCursor)
        btn.setCheckable(True)
        btn.setChecked(selected)
        btn.setStyleSheet(S.TAB_ACTIVE if selected else S.TAB_IDLE)
        return btn

    def _make_chip(self, label, exts):
        chip = QPushButton(label.upper())
        chip.setFont(S.mono_font(8))
        chip.setCheckable(True)
        chip.setChecked(exts is None)
        chip.setCursor(Qt.PointingHandCursor)
        chip.setStyleSheet(S.FILTER_ON if exts is None else S.FILTER_OFF)
        chip.clicked.connect(lambda: self._set_filter(exts, chip))
        return chip

    # ── view switching ───────────────────────────────────────────────────
    def _set_view(self, view):
        self._view = view
        is_search = view == "search"
        self._tab_search.setChecked(is_search)
        self._tab_library.setChecked(not is_search)
        self._tab_search.setStyleSheet(S.TAB_ACTIVE if is_search else S.TAB_IDLE)
        self._tab_library.setStyleSheet(S.TAB_IDLE if is_search else S.TAB_ACTIVE)
        self._search_row.setVisible(is_search)
        self._chip_row.setVisible(is_search)
        self._scope.setVisible(is_search)
        self._scope_lbl.setVisible(is_search)
        self._lib_box.setVisible(not is_search)

        if is_search:
            self._rebuild_results()
        else:
            self.refresh_library()

    # ── search ───────────────────────────────────────────────────────────
    def _dialog_parent(self):
        """
        A dialog must not be parented to this widget: it lives inside a
        QGraphicsProxyWidget, and a native dialog parented into the graphics
        scene renders as a stunted, unusable window. Parent it to the real
        top-level overlay window instead, which also keeps it above the
        always-on-top overlay.
        """
        for top_level in QApplication.instance().topLevelWidgets():
            overlay = getattr(top_level, "_space_overlay", None)
            if overlay is not None:
                return overlay
        return None

    def _on_scope_changed(self, _idx):
        if self._scope.currentData() != "__browse__":
            return

        folder = QFileDialog.getExistingDirectory(
            self._dialog_parent(),
            "Choose a folder to search",
            "",
            QFileDialog.ShowDirsOnly,
        )

        browse_index = self._scope.count() - 1
        if not folder:
            self._scope.setCurrentIndex(0)
            return

        # Add the folder as its own entry rather than overwriting "Browse…",
        # so browsing stays available and previous picks are reusable.
        existing = self._scope.findData(folder)
        if existing == -1:
            self._scope.insertItem(browse_index, _elide(folder, 28), folder)
            existing = browse_index
        self._scope.setCurrentIndex(existing)

    def _current_root(self):
        data = self._scope.currentData()
        if not data or data == "__browse__":
            return None
        return data

    def _start_search(self):
        query = self._search_box.text().strip()
        if not query:
            return

        self._cancel_search()
        self._results = []
        self._rebuild_results()

        self._status.setText("SCANNING…")
        self._go_btn.hide()
        self._cancel_btn.show()

        self._search_thread = QThread()
        self._search_worker = _SearchWorker(query, self._current_root())
        self._search_worker.moveToThread(self._search_thread)
        self._search_thread.started.connect(self._search_worker.run)
        self._search_worker.hits.connect(self._on_hits)
        self._search_worker.progress.connect(self._on_progress)
        self._search_worker.finished_search.connect(self._on_search_done)
        self._search_worker.finished_search.connect(self._search_thread.quit)
        self._search_thread.start()

    def _cancel_search(self):
        if self._search_worker:
            self._search_worker.cancel()
        self._cancel_btn.hide()
        self._go_btn.show()

    def _on_hits(self, batch):
        self._results.extend(batch)
        self._status.setText(f"SCANNING · {len(self._results):03d} HITS")
        if not self._rebuild_pending:
            self._rebuild_pending = True
            QTimer.singleShot(350, self._flush_rebuild)

    def _flush_rebuild(self):
        self._rebuild_pending = False
        if self._view == "search":
            self._rebuild_results()

    def _on_progress(self, directory):
        if directory:
            self._status.setText(
                f"SCANNING · {len(self._results):03d} HITS · {_elide(directory, 40).upper()}")

    def _on_search_done(self, total):
        self._cancel_btn.hide()
        self._go_btn.show()
        self._rebuild_results()
        shown = len(self._visible_results())
        if total == 0:
            self._status.setText("COMPLETE · NO MATCHES")
        else:
            self._status.setText(f"COMPLETE · {shown:03d} SHOWN · {total:03d} FOUND")

    def _set_filter(self, exts, chip):
        self._filter_ext = exts
        for c in self._chips:
            active = c is chip
            c.setChecked(active)
            c.setStyleSheet(S.FILTER_ON if active else S.FILTER_OFF)
        self._rebuild_results()

    def _toggle_sort(self):
        self._sort_newest = not self._sort_newest
        self._sort_btn.setText(
            "SORT ▸ NEWEST" if self._sort_newest else "SORT ▸ RELEVANCE")
        self._rebuild_results()

    def _visible_results(self):
        items = self._results
        if self._filter_ext:
            items = [r for r in items
                     if os.path.splitext(r[2])[1].lower() in self._filter_ext]
        key = (lambda r: r[1]) if self._sort_newest else (lambda r: r[0])
        return sorted(items, key=key, reverse=True)[:60]

    def _clear_list(self):
        while self._list_layout.count() > 1:
            item = self._list_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

    def _rebuild_results(self):
        self._clear_list()
        visible = self._visible_results()
        if not visible:
            self._list_layout.insertWidget(
                self._list_layout.count() - 1,
                self._empty_block(
                    "AWAITING QUERY",
                    "Type a document name and press Enter.\n"
                    "Results stream in as the scan walks your drives."
                )
            )
            return
        for n, (score, mtime, path) in enumerate(visible, start=1):
            row = _FileRow(path, mtime, index=n)
            row.open_requested.connect(self._open_path)
            row.reveal_requested.connect(self._reveal_path)
            row.summarize_requested.connect(self._summarize)
            row.remember_requested.connect(self._remember)
            self._list_layout.insertWidget(self._list_layout.count() - 1, row)
            if path in self._row_status:
                row.show_status(self._row_status[path])

    def _set_row_status(self, path, text):
        """Record a row's status and push it to whichever widget currently
        represents that path (the original may have been rebuilt away)."""
        self._row_status[path] = text
        for i in range(self._list_layout.count() - 1):
            widget = self._list_layout.itemAt(i).widget()
            if getattr(widget, "path", None) == path and hasattr(widget, "show_status"):
                widget.show_status(text)
                break

    # ── file actions (read-only) ─────────────────────────────────────────
    def _open_path(self, path):
        try:
            os.startfile(path)  # noqa: S606 - user-initiated open of their own file
        except Exception:
            return
        self._close_space()

    def _reveal_path(self, path):
        try:
            subprocess.Popen(f'explorer /select,"{path}"', shell=True)
        except Exception:
            return
        self._close_space()

    def _close_space(self):
        """Match system.py: opening an external window collapses the overlay."""
        for top_level in QApplication.instance().topLevelWidgets():
            if hasattr(top_level, "_space_overlay"):
                top_level._space_overlay.close_space()
                break

    # ── AI actions ───────────────────────────────────────────────────────
    def _run_doc_worker(self, path, want_topics, busy_text):
        self._set_row_status(path, busy_text)

        thread = QThread()
        worker = _DocWorker(path, want_topics=want_topics)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)

        if want_topics:
            worker.done.connect(self._on_remembered)
        else:
            worker.done.connect(
                lambda p, s, _t: self._set_row_status(p, f"✦  {s}"))

        worker.failed.connect(lambda p, why: self._set_row_status(p, f"⚠  {why}"))
        worker.done.connect(thread.quit)
        worker.failed.connect(thread.quit)
        thread.start()

        # Keep refs alive; drop finished ones.
        self._doc_threads = [t for t in self._doc_threads if t[0].isRunning()]
        self._doc_threads.append((thread, worker))

    def _summarize(self, path, _row=None):
        self._run_doc_worker(path, False, "✦  Reading and summarizing…")

    def _remember(self, path, _row=None):
        self._run_doc_worker(path, True, "◈  Reading and remembering…")

    def _on_remembered(self, path, summary, topics):
        _, index_file_knowledge, _ = _knowledge_backend()
        if not index_file_knowledge:
            self._set_row_status(path, "⚠  Memory backend unavailable")
            return
        try:
            index_file_knowledge(path, summary, topics)
        except Exception:
            self._set_row_status(path, "⚠  Couldn't save to memory")
            return
        tag_str = ("  " + "  ".join(f"#{t}" for t in topics)) if topics else ""
        self._set_row_status(path, f"◈  Remembered.{tag_str}\n{summary}")

    # ── library ──────────────────────────────────────────────────────────
    def refresh_library(self):
        self._lib_thread = QThread()
        self._lib_loader = _LibraryLoader()
        self._lib_loader.moveToThread(self._lib_thread)
        self._lib_thread.started.connect(self._lib_loader.load)
        self._lib_loader.done.connect(self._on_library_loaded)
        self._lib_loader.done.connect(self._lib_thread.quit)
        self._lib_thread.start()

    def _on_library_loaded(self, entries):
        self._library = entries
        if self._view == "library":
            self._rebuild_library()

    def _rebuild_library(self):
        if self._view != "library":
            return

        query = self._lib_box.text().strip().lower()
        entries = self._library
        if query:
            entries = [
                e for e in entries
                if query in e.get("filename", "").lower()
                or query in e.get("summary", "").lower()
                or any(query in t.lower() for t in e.get("topics", []))
            ]

        self._clear_list()

        if not entries:
            self._status.setText("INDEX EMPTY · 000 RECORDS")
            self._list_layout.insertWidget(
                self._list_layout.count() - 1,
                self._empty_block(
                    "NO RECORDS",
                    "Run a scan, then press MEM on a result.\n"
                    "Helio reads the document and keeps a summary here."
                )
            )
            return

        self._status.setText(f"INDEX · {len(entries):03d} RECORDS")
        for n, entry in enumerate(entries, start=1):
            row = _KnowledgeRow(entry, index=n)
            row.open_requested.connect(self._open_path)
            row.ask_requested.connect(self._ask_about)
            row.forget_requested.connect(self._forget)
            self._list_layout.insertWidget(self._list_layout.count() - 1, row)

    def _ask_about(self, path):
        name = os.path.basename(path)
        self.ask_in_chat.emit(f"What do you remember about the file {name}?")

    def _forget(self, path):
        _, _, forget_file_knowledge = _knowledge_backend()
        if forget_file_knowledge:
            try:
                forget_file_knowledge(path)
            except Exception:
                pass
        QTimer.singleShot(80, self.refresh_library)

    # ── frame ────────────────────────────────────────────────────────────
    def paintEvent(self, event):
        """
        Square instrument frame: flat ground, scanlines, hairline edge and
        corner brackets. Deliberately not a rounded glass card — that shape is
        what made this read as a generic dashboard panel.
        """
        p = QPainter(self)
        r = self.rect().adjusted(1, 1, -1, -1)

        # One ground layer, and only one. The scroll area paints its own
        # translucent ground (rows are borderless, so the list needs it); if we
        # filled underneath it as well the two alphas would composite to ~86%
        # and the results area would read as an opaque slab while the header
        # above it stayed see-through.
        ground = QRegion(r)
        if self._scroll.isVisible():
            ground = ground.subtracted(QRegion(self._scroll.geometry()))
        p.setClipRegion(ground)
        p.fillRect(r, QColor(4, 8, 10, S.GROUND_ALPHA))
        p.setClipping(False)

        # Scanlines — faint horizontal texture instead of a flat wash
        p.setPen(QPen(QColor(0, 245, 192, 8), 1))
        y = r.top()
        while y < r.bottom():
            p.drawLine(r.left(), y, r.right(), y)
            y += 3

        # Hairline edge
        p.setPen(QPen(QColor(31, 74, 66, 190), 1))
        p.setBrush(Qt.NoBrush)
        p.drawRect(r)

        # Corner brackets carry the framing, so no full accent outline is needed
        p.setPen(QPen(QColor(0, 245, 192, 210), 1.6))
        arm = 22
        for cx, cy, dx, dy in (
            (r.left(), r.top(), 1, 1),
            (r.right(), r.top(), -1, 1),
            (r.left(), r.bottom(), 1, -1),
            (r.right(), r.bottom(), -1, -1),
        ):
            p.drawLine(cx, cy, cx + arm * dx, cy)
            p.drawLine(cx, cy, cx, cy + arm * dy)
