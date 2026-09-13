"""
slab.py — one movable, resizable pane on the workshop canvas.

A slab is the nearest thing to a hologram a screen allows: pick it up by its
header, drop it anywhere, drag its corner to resize, shove it aside and come
back to it. Each holds exactly one thing — a live page, an image, source, a
chart, a note — so several can sit side by side and be compared, or one can be
left building while you start the next.
"""
import os
import subprocess

from PyQt5.QtWidgets import (
    QFrame, QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QPlainTextEdit, QScrollArea, QSizePolicy,
)
from PyQt5.QtCore import Qt, QUrl, QPoint, pyqtSignal, QTimer
from PyQt5.QtGui import QPixmap, QPainter, QPen, QColor

from ..panels import workshop_style as W

S = W.FORGE

HEADER_H = 30
GRIP = 18
MIN_W, MIN_H = 280, 200

# Both the slab kinds and the artifact "type" values a record carries, or the
# rack lists every row as OBJ.
KIND_MARK = {
    "web": "WEB", "page": "WEB", "website": "WEB", "app": "APP",
    "image": "IMG", "code": "SRC", "script": "SRC",
    "chart": "CHT", "text": "TXT", "document": "DOC", "diff": "DIF",
    "rack": "RCK",
}


def _web_view():
    """Imported lazily: QtWebEngine is heavy and not every slab needs it."""
    from PyQt5.QtWebEngineWidgets import QWebEngineView, QWebEngineSettings
    view = QWebEngineView()
    view.settings().setAttribute(
        QWebEngineSettings.LocalContentCanAccessFileUrls, True)
    view.settings().setAttribute(
        QWebEngineSettings.LocalContentCanAccessRemoteUrls, True)
    view.page().setBackgroundColor(QColor(*S.ground_rgb))
    return view


class _Grip(QWidget):
    """The resize corner. Its own widget so the slab body keeps its events."""

    def __init__(self, slab):
        super().__init__(slab)
        self.slab = slab
        self.setFixedSize(GRIP, GRIP)
        self.setCursor(Qt.SizeFDiagCursor)
        self._from = None

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._from = (event.globalPos(), self.slab.size())
            self.slab.lift()
            event.accept()

    def mouseMoveEvent(self, event):
        if not self._from:
            return
        origin, size = self._from
        delta = event.globalPos() - origin
        self.slab.resize(max(MIN_W, size.width() + delta.x()),
                         max(MIN_H, size.height() + delta.y()))
        event.accept()

    def mouseReleaseEvent(self, event):
        self._from = None
        self.slab.remember()

    def paintEvent(self, event):
        p = QPainter(self)
        pen = QPen(QColor(*S.accent_rgb, 150), 1)
        p.setPen(pen)
        for offset in (4, 9, 14):
            p.drawLine(GRIP - offset, GRIP - 2, GRIP - 2, GRIP - offset)
        p.end()


class _Header(QFrame):
    """Grab handle. Dragging anywhere on it moves the whole slab."""

    def __init__(self, slab, title):
        super().__init__(slab)
        self.slab = slab
        self._from = None
        self.setFixedHeight(HEADER_H)
        self.setCursor(Qt.SizeAllCursor)
        self.setStyleSheet(
            f"background:{S.GROUND};border:none;border-bottom:1px solid {S.RULE};")

        row = QHBoxLayout(self)
        row.setContentsMargins(10, 0, 6, 0)
        row.setSpacing(9)

        self.mark = QLabel(KIND_MARK.get(slab.kind, "OBJ"))
        self.mark.setFont(W.mono_font(8, bold=True))
        self.mark.setStyleSheet(f"color:{S.ACCENT};{W.TRANSPARENT_LABEL}")
        row.addWidget(self.mark)

        self.title = QLabel(title)
        self.title.setFont(W.mono_font(9))
        self.title.setStyleSheet(f"color:{S.TEXT};{W.TRANSPARENT_LABEL}")
        row.addWidget(self.title, 1)

        for label, slot in (("OPEN", slab.open_external), ("X", slab.dismiss)):
            button = QPushButton(label)
            button.setFont(W.label_font(8, tracking=1.6))
            button.setCursor(Qt.PointingHandCursor)
            button.setStyleSheet(
                S.ENGRAVED_WARN if label == "X" else S.ENGRAVED)
            button.clicked.connect(slot)
            row.addWidget(button)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._from = event.globalPos() - self.slab.pos()
            self.slab.lift()
            event.accept()

    def mouseMoveEvent(self, event):
        if self._from and event.buttons() & Qt.LeftButton:
            target = event.globalPos() - self._from
            parent = self.slab.parentWidget()
            if parent:
                # Keep the header reachable — a slab dragged off the top edge
                # can never be picked up again.
                target.setX(max(-self.slab.width() + 80,
                                min(target.x(), parent.width() - 80)))
                target.setY(max(0, min(target.y(), parent.height() - HEADER_H)))
            self.slab.move(target)
            event.accept()

    def mouseReleaseEvent(self, event):
        self._from = None
        self.slab.remember()


class _RackBody(QScrollArea):
    """Everything Helio has built, as a list you can pick from."""

    chosen = pyqtSignal(dict)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWidgetResizable(True)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setStyleSheet(S.SCROLL)
        self.setMinimumSize(0, 0)

        holder = QWidget()
        holder.setStyleSheet(f"background:{S.GROUND};")
        self._rows = QVBoxLayout(holder)
        self._rows.setContentsMargins(0, 0, 0, 0)
        self._rows.setSpacing(0)
        self.setWidget(holder)
        self.reload()

    def reload(self):
        while self._rows.count():
            entry = self._rows.takeAt(0)
            widget = entry.widget()
            if widget is not None:
                widget.setParent(None)
                widget.deleteLater()

        try:
            from tools.system.forge_tool import list_forge_history
            history = list_forge_history()
        except Exception:
            history = []

        if not history:
            empty = QLabel("  Nothing built yet.")
            empty.setFont(W.mono_font(9))
            empty.setStyleSheet(f"color:{S.DIM};{W.TRANSPARENT_LABEL}")
            self._rows.addWidget(empty)
            self._rows.addStretch()
            return

        for item in history[:60]:
            self._rows.addWidget(self._make_row(item))
        self._rows.addStretch()

    def _make_row(self, item):
        row = QFrame()
        row.setStyleSheet(S.ROW)
        row.setFixedHeight(30)
        line = QHBoxLayout(row)
        line.setContentsMargins(9, 0, 7, 0)
        line.setSpacing(9)

        mark = QLabel(KIND_MARK.get(item.get("type"), "OBJ"))
        mark.setFont(W.mono_font(8, bold=True))
        mark.setFixedWidth(28)
        mark.setStyleSheet(f"color:{S.ACCENT_D};{W.TRANSPARENT_LABEL}")
        line.addWidget(mark)

        # Long enough to tell builds apart, short enough that PUT UP is not
        # pushed off the end of the row.
        raw = str(item.get("title", "Untitled"))
        title = QLabel(raw if len(raw) <= 38 else raw[:37].rstrip() + "…")
        title.setFont(W.mono_font(9))
        title.setMinimumWidth(0)
        title.setStyleSheet(f"color:{S.TEXT};{W.TRANSPARENT_LABEL}")
        line.addWidget(title, 1)

        when = QLabel(str(item.get("created_at", ""))[5:16].replace("-", "/"))
        when.setFont(W.mono_font(8))
        when.setStyleSheet(f"color:{S.DIM};{W.TRANSPARENT_LABEL}")
        line.addWidget(when)

        put = QPushButton("PUT UP")
        put.setFont(W.label_font(8, tracking=1.5))
        put.setCursor(Qt.PointingHandCursor)
        put.setStyleSheet(S.CHIP)
        put.clicked.connect(lambda: self.chosen.emit(item))
        line.addWidget(put)
        return row


class Slab(QFrame):
    """One thing on the workshop surface."""

    dismissed = pyqtSignal(object)
    lifted = pyqtSignal(object)
    changed = pyqtSignal()
    handed_off = pyqtSignal()
    picked = pyqtSignal(dict)

    def __init__(self, kind, title, payload="", parent=None):
        super().__init__(parent)
        self.kind = kind
        self.title = title
        self.payload = payload
        self.body = None

        self.setMinimumSize(MIN_W, MIN_H)
        self.setStyleSheet(f"background:{S.GROUND};border:none;")

        root = QVBoxLayout(self)
        root.setContentsMargins(1, 1, 1, 1)
        root.setSpacing(0)

        self.header = _Header(self, title)
        root.addWidget(self.header)

        self.body = self._make_body()
        root.addWidget(self.body, 1)

        self.grip = _Grip(self)
        self.resize(560, 400)

    # ── content ───────────────────────────────────────────────────────────
    def _make_body(self):
        if self.kind == "rack":
            rack = _RackBody()
            rack.chosen.connect(self.picked.emit)
            return rack

        if self.kind in ("web", "page"):
            view = _web_view()
            self.load(self.payload, view)
            return view

        if self.kind in ("image", "chart"):
            label = QLabel()
            label.setAlignment(Qt.AlignCenter)
            label.setStyleSheet(f"background:{S.GROUND};{W.TRANSPARENT_LABEL}")
            label.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Ignored)
            self._pixmap = QPixmap(self.payload) if self.payload else QPixmap()
            label.setText("" if not self._pixmap.isNull() else "could not read that image")
            return label

        editor = QPlainTextEdit()
        editor.setFont(W.mono_font(9))
        editor.setLineWrapMode(
            QPlainTextEdit.NoWrap if self.kind == "code" else QPlainTextEdit.WidgetWidth)
        editor.setStyleSheet(
            f"QPlainTextEdit{{background:{S.GROUND};color:{S.TEXT};border:none;"
            f"padding:10px;selection-background-color:{S.ACCENT};"
            f"selection-color:{S.GROUND};}}" + S.SCROLL)
        text = self.payload
        if self.kind in ("code", "document", "diff") and os.path.exists(str(text)):
            try:
                with open(text, "r", encoding="utf-8", errors="replace") as f:
                    text = f.read()
            except Exception as e:
                text = f"could not read {self.payload}: {e}"
        editor.setPlainText(str(text))
        editor.setReadOnly(self.kind == "diff")
        return editor

    def load(self, target, view=None):
        """Point a web slab at a file path or a URL."""
        view = view or self.body
        if view is None:
            return
        target = str(target or "")
        if target.startswith(("http://", "https://")):
            view.load(QUrl(target))
        elif os.path.exists(target):
            # Absolute, always. QUrl.fromLocalFile on a relative path yields a
            # URL Chromium cannot resolve, and the slab shows a file-not-found
            # page rather than the thing that is plainly sitting on disk.
            view.load(QUrl.fromLocalFile(os.path.abspath(target)))
        else:
            view.setHtml(target, QUrl("file:///"))

    def set_payload(self, payload):
        """Repoint an existing slab — used when Helio revises what it built."""
        self.payload = payload
        if self.kind in ("web", "page"):
            self.load(payload)
        elif self.kind in ("image", "chart"):
            self._pixmap = QPixmap(payload)
            self._rescale()
        elif hasattr(self.body, "setPlainText"):
            text = payload
            if os.path.exists(str(payload)):
                try:
                    with open(payload, "r", encoding="utf-8", errors="replace") as f:
                        text = f.read()
                except Exception:
                    pass
            self.body.setPlainText(str(text))
        self.changed.emit()

    def set_text(self, text):
        """
        Write straight into a text body.

        set_payload() stats the filesystem and emits changed() — fine once,
        wasteful sixty times a second while source is streaming in.
        """
        if hasattr(self.body, "setPlainText"):
            self.body.setPlainText(str(text))
            bar = self.body.verticalScrollBar()
            bar.setValue(bar.maximum())

    def set_title(self, title):
        self.title = title
        self.header.title.setText(title)

    # ── behaviour ─────────────────────────────────────────────────────────
    def lift(self):
        self.raise_()
        self.lifted.emit(self)

    def dismiss(self):
        self.dismissed.emit(self)
        self.setParent(None)
        self.deleteLater()

    def remember(self):
        self.changed.emit()

    def open_external(self):
        target = str(self.payload or "")
        opened = False
        if target.startswith(("http://", "https://")):
            try:
                import webbrowser
                webbrowser.open(target)
                opened = True
            except Exception:
                pass
        elif os.path.exists(target):
            try:
                os.startfile(target)
                opened = True
            except Exception:
                pass
        if opened:
            # Helio is always-on-top; get it out of the way of the thing it
            # just opened rather than sitting in front of it.
            self.handed_off.emit()

    def spec(self):
        """What it takes to rebuild this slab next time the workshop opens."""
        return {
            "kind": self.kind,
            "title": self.title,
            "payload": self.payload,
            "x": self.x(), "y": self.y(),
            "w": self.width(), "h": self.height(),
        }

    def mousePressEvent(self, event):
        self.lift()
        super().mousePressEvent(event)

    def _rescale(self):
        if self.kind not in ("image", "chart") or not hasattr(self, "_pixmap"):
            return
        if self._pixmap.isNull():
            return
        area = self.body.size()
        if area.width() < 8 or area.height() < 8:
            return
        self.body.setPixmap(self._pixmap.scaled(
            area, Qt.KeepAspectRatio, Qt.SmoothTransformation))

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.grip.move(self.width() - GRIP - 1, self.height() - GRIP - 1)
        self.grip.raise_()
        self._rescale()

    # ── chrome ────────────────────────────────────────────────────────────
    def paintEvent(self, event):
        p = QPainter(self)
        r = self.rect().adjusted(0, 0, -1, -1)
        p.fillRect(r, QColor(*S.ground_rgb, 244))

        edge = QColor(*S.accent_rgb, 110)
        p.setPen(QPen(edge, 1))
        p.setBrush(Qt.NoBrush)
        p.drawRect(r)

        bracket = QColor(*S.accent_rgb, 220)
        p.setPen(QPen(bracket, 1.6))
        arm = 16
        for cx, cy, dx, dy in (
            (r.left(), r.top(), 1, 1), (r.right(), r.top(), -1, 1),
            (r.left(), r.bottom(), 1, -1), (r.right(), r.bottom(), -1, -1),
        ):
            p.drawLine(cx, cy, cx + arm * dx, cy)
            p.drawLine(cx, cy, cx, cy + arm * dy)
        p.end()
