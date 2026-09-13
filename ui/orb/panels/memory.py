import sys, os, math, random, datetime
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', '..', '..', 'src'))

from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit,
    QPushButton, QFrame, QScrollArea, QSizePolicy, QGraphicsOpacityEffect
)
from PyQt5.QtCore import (
    Qt, QTimer, QRectF, QPointF, pyqtSignal, QThread, QObject, QPropertyAnimation, QEasingCurve
)
from PyQt5.QtGui import (
    QColor, QFont, QPainter, QPen, QBrush, QRadialGradient,
    QLinearGradient, QPainterPath, QFontMetrics
)
from . import workshop_style as W
from .base import BasePanel, _content_alpha, _draw_header

# ── memory backend (lazy import so ui still loads if src is missing) ──────────
def _mem():
    try:
        from memory.semantic_memory import (
            get_memories, save_memory, CATEGORIES,
            get_recent_conversations, list_workflows
        )
        return get_memories, save_memory, CATEGORIES, get_recent_conversations, list_workflows
    except Exception:
        return None, None, {}, None, None


# ═══════════════════════════════════════════════════════════════════
#  STATIC RENDERER  (used while panel is animating open)
# ═══════════════════════════════════════════════════════════════════
class MemoryPanel(BasePanel):
    def __init__(self):
        super().__init__("MEMORY", QColor(180, 50, 255))

    def draw(self, painter, shape_rect, panel_progress, alpha):
        content_a = _content_alpha(panel_progress, alpha)
        # Always draw the title header so it never disappears
        _draw_header(painter, shape_rect, self.title, self.accent, content_a)
        if content_a <= 0:
            return
        # Only draw the glass body placeholder while widget is still animating in
        if panel_progress < 0.99:
            painter.save()
            painter.setOpacity(content_a / 255.0)
            bg = shape_rect.adjusted(16, 60, -16, -16)
            painter.setBrush(QColor(10, 5, 20, 80))
            painter.setPen(QPen(QColor(180, 50, 255, 35), 1.2))
            painter.drawRoundedRect(bg, 10, 10)
            painter.restore()


# ═══════════════════════════════════════════════════════════════════
#  ASYNC LOADER
# ═══════════════════════════════════════════════════════════════════
class _MemLoader(QObject):
    done = pyqtSignal(dict)

    def load(self):
        get_memories, _, CATEGORIES, get_recent_conversations, list_workflows = _mem()
        result = {"categories": {}, "recent": [], "workflows": {}, "total": 0}
        if get_memories:
            for cat in CATEGORIES:
                items = get_memories(cat) or []
                result["categories"][cat] = items
                result["total"] += len(items)
        if get_recent_conversations:
            result["recent"] = get_recent_conversations(8) or []
        if list_workflows:
            result["workflows"] = list_workflows() or {}
        self.done.emit(result)


# ═══════════════════════════════════════════════════════════════════
#  AI MEMORY EXTRACTOR  (runs in background thread)
# ═══════════════════════════════════════════════════════════════════
class _AIMemoryExtractor(QObject):
    """
    Sends user text to the LLM. The LLM extracts clean, third-person
    memory facts from any text (phrase, sentence, or paragraph).
    Emits a list of fact strings on completion.
    """
    done    = pyqtSignal(list)   # list[str] of extracted facts
    failed  = pyqtSignal(str)    # error message

    def __init__(self, raw_text: str, user_name: str):
        super().__init__()
        self._raw  = raw_text
        self._name = user_name

    def run(self):
        try:
            from core.llm import generate
        except ImportError:
            # LLM not available — fall back to simple normalization
            self.done.emit([self._fallback()])
            return

        prompt = (
            f"You are a memory extraction assistant for an AI called Helio.\n"
            f"The user's name is {self._name}.\n"
            f"Extract every discrete personal fact from the text below and rewrite each as a "
            f"single, concise third-person statement about {self._name}.\n"
            f"Rules:\n"
            f"- Write each fact on its own line starting with a dash (-).\n"
            f"- Use '{self._name}' as the subject (never 'I', 'me', or 'the user').\n"
            f"- Conjugate verbs correctly (e.g. 'I like' → '{self._name} likes').\n"
            f"- If the text has only one fact, output only one line.\n"
            f"- Output ONLY the dash-prefixed facts, no extra commentary.\n"
            f"\nText: {self._raw}"
        )

        try:
            response = generate(prompt, role="memory")

            # generate() swallows exceptions and returns error strings — catch them
            if not response or response.startswith("LLM Error:"):
                self.done.emit([self._fallback()])
                return

            # qwen3 models wrap output in <think>...</think> — strip it
            import re as _re
            response = _re.sub(r"<think>.*?</think>", "", response, flags=_re.DOTALL).strip()

            facts = []
            for line in response.splitlines():
                line = line.strip().lstrip("-•–").strip()
                # Skip any line that looks like an error, URL, or meta comment
                if not line or len(line) < 5:
                    continue
                if line.lower().startswith(("llm error", "error:", "http", "note:", "text:")):
                    continue
                facts.append(line)

            if facts:
                self.done.emit(facts)
            else:
                self.done.emit([self._fallback()])
        except Exception:
            self.done.emit([self._fallback()])

    def _fallback(self):
        """Simple regex-based normalization when LLM is unavailable."""
        import re
        name = self._name
        text = self._raw.strip()

        # Strip leading filler words so "so i like..." -> "i like..."
        filler_pat = r"^(?:so|well|btw|by the way|hey|um|uh|okay|ok|you know|basically|actually|also|and)\s+"
        text = re.sub(filler_pat, "", text, flags=re.IGNORECASE).strip()
        if text:
            text = text[0].upper() + text[1:]

        replacements = [
            (r"^[Ii] am\b",      f"{name} is"),
            (r"^[Ii]'m\b",       f"{name} is"),
            (r"^[Ii] was\b",     f"{name} was"),
            (r"^[Ii] have\b",    f"{name} has"),
            (r"^[Ii] had\b",     f"{name} had"),
            (r"^[Ii] like\b",    f"{name} likes"),
            (r"^[Ii] love\b",    f"{name} loves"),
            (r"^[Ii] hate\b",    f"{name} hates"),
            (r"^[Ii] prefer\b",  f"{name} prefers"),
            (r"^[Ii] want\b",    f"{name} wants"),
            (r"^[Ii] need\b",    f"{name} needs"),
            (r"^[Ii] use\b",     f"{name} uses"),
            (r"^[Ii] play\b",    f"{name} plays"),
            (r"^[Ii] work\b",    f"{name} works"),
            (r"^[Ii] study\b",   f"{name} studies"),
            (r"^[Ii] go\b",      f"{name} goes"),
            (r"^[Ii] do\b",      f"{name} does"),
            (r"^[Ii] don't\b",   f"{name} doesn't"),
            (r"^[Ii] can\b",     f"{name} can"),
            (r"^[Ii] will\b",    f"{name} will"),
            (r"^[Ii]\b",         f"{name}"),
        ]

        result = text
        for pattern, repl in replacements:
            new = re.sub(pattern, repl, result, count=1)
            if new != result:
                result = new
                break

        result = re.sub(r"\bmy\b",   f"{name}'s", result, flags=re.IGNORECASE)
        result = re.sub(r"\bme\b",   name,         result, flags=re.IGNORECASE)
        result = re.sub(r"\bmine\b", f"{name}'s",  result, flags=re.IGNORECASE)
        result = result.strip().rstrip(".")
        return result


# ═══════════════════════════════════════════════════════════════════
#  NEURAL ORB  (animated purple orb, centre of widget)
# ═══════════════════════════════════════════════════════════════════
class _NeuralOrb(QWidget):
    def __init__(self, size=110, parent=None):
        super().__init__(parent)
        self.setFixedSize(size, size)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self._phase = 0.0
        self._ring_angles = [0.0, 0.0, 0.0]
        self._rings = [
            {"rf": 0.50, "arc": 60, "gap": 20, "spd": 0.9,  "r": 180, "g": 50,  "b": 255, "w": 1.2, "a": 200},
            {"rf": 0.68, "arc": 45, "gap": 35, "spd": -0.6, "r": 130, "g": 30,  "b": 220, "w": 0.9, "a": 160},
            {"rf": 0.85, "arc": 30, "gap": 50, "spd": 0.35, "r": 200, "g": 120, "b": 255, "w": 0.7, "a": 110},
        ]
        rng = random.Random(77)
        self._nodes = [
            {"angle": rng.uniform(0, 360), "dist": rng.uniform(0.28, 0.42),
             "speed": rng.uniform(0.2, 0.5) * rng.choice([-1, 1])}
            for _ in range(7)
        ]
        t = QTimer(self); t.timeout.connect(self._tick); t.start(16)

    def _tick(self):
        # Nothing to animate while the memory panel is closed.
        if not self.isVisible():
            return
        self._phase += 0.04
        for i, r in enumerate(self._rings):
            self._ring_angles[i] = (self._ring_angles[i] + r["spd"]) % 360
        for n in self._nodes:
            n["angle"] = (n["angle"] + n["speed"]) % 360
        self.update()

    def paintEvent(self, event):
        p = QPainter(self); p.setRenderHint(QPainter.Antialiasing)
        cx, cy = self.width()/2, self.height()/2
        rb = self.width() * 0.42
        pulse = math.sin(self._phase * 0.05)

        # aura
        ra = rb * 1.5 + pulse * 2
        grad = QRadialGradient(cx, cy, ra)
        grad.setColorAt(0.0, QColor(180, 50, 255, 55))
        grad.setColorAt(0.6, QColor(100, 20, 200, 22))
        grad.setColorAt(1.0, QColor(0,0,0,0))
        p.setPen(Qt.NoPen); p.setBrush(grad)
        p.drawEllipse(QPointF(cx,cy), ra, ra)

        # core
        rc = rb * (0.78 + pulse*0.04)
        core = QRadialGradient(cx, cy, rc)
        core.setColorAt(0.00, QColor(240, 220, 255, 240))
        core.setColorAt(0.25, QColor(180, 80, 255, 210))
        core.setColorAt(0.65, QColor(100, 30, 200, 140))
        core.setColorAt(1.00, QColor(0,0,0,0))
        p.setBrush(core); p.drawEllipse(QPointF(cx,cy), rc, rc)

        # rings
        for ri, ring in enumerate(self._rings):
            rr = self.width() * ring["rf"] / 2
            rect = QRectF(cx-rr, cy-rr, rr*2, rr*2)
            rot, arc, gap = self._ring_angles[ri], ring["arc"], ring["gap"]
            angle = 0.0
            while angle < 360.0:
                sd = (rot+angle)%360; dl = min(arc, 360.0-angle)
                gp = QPen(QColor(ring["r"],ring["g"],ring["b"], int(ring["a"]*0.3)))
                gp.setWidthF(ring["w"]*2.8); gp.setCapStyle(Qt.RoundCap)
                p.setPen(gp); p.setBrush(Qt.NoBrush)
                p.drawArc(rect, int(sd*16), int(dl*16))
                cp = QPen(QColor(ring["r"],ring["g"],ring["b"],ring["a"]))
                cp.setWidthF(ring["w"]); cp.setCapStyle(Qt.RoundCap)
                p.setPen(cp); p.drawArc(rect, int(sd*16), int(dl*16))
                angle += arc+gap

        # neural graph nodes
        center = QPointF(cx, cy)
        node_pts = []
        for n in self._nodes:
            rad = math.radians(n["angle"])
            dist = rb * n["dist"]
            pt = QPointF(cx + dist*math.cos(rad), cy + dist*math.sin(rad))
            node_pts.append(pt)
            p.setPen(QPen(QColor(220,180,255,80), 0.8))
            p.drawLine(center, pt)

        # center node
        p.setPen(Qt.NoPen); p.setBrush(QColor(255,255,255,230))
        p.drawEllipse(center, 3.5, 3.5)
        # peripheral nodes
        p.setBrush(QColor(200,150,255,200))
        for pt in node_pts:
            p.drawEllipse(pt, 2.0, 2.0)


# ═══════════════════════════════════════════════════════════════════
#  STAT CARD  (clickable filter + counter)
# ═══════════════════════════════════════════════════════════════════
class _StatCard(QFrame):
    filter_clicked = pyqtSignal(str)  # emits category key

    def __init__(self, label, category_key, value="0", color="#b432ff", parent=None):
        super().__init__(parent)
        self._key = category_key
        self._color = color
        self._selected = False
        self.setCursor(Qt.PointingHandCursor)
        # A cell on an instrument, framed by one lit edge — not a tile.
        self._base_style = f"""
            QFrame {{
                background: transparent;
                border: none;
                border-bottom: 1px solid {W.MEMORY.RULE};
                border-left: 2px solid transparent;
            }}
            QFrame:hover {{ border-left: 2px solid {color}; }}
        """
        self._sel_style = f"""
            QFrame {{
                background: rgba(180, 50, 255, 22);
                border: none;
                border-bottom: 1px solid {color};
                border-left: 2px solid {color};
            }}
        """
        self.setStyleSheet(self._base_style)
        lay = QVBoxLayout(self); lay.setContentsMargins(10,8,10,8); lay.setSpacing(2)
        self.val_lbl = QLabel(str(value))
        self.val_lbl.setFont(W.mono_font(15, bold=True))
        self.val_lbl.setStyleSheet(f"color:{color};background:transparent;border:none;")
        self.val_lbl.setAlignment(Qt.AlignCenter)
        cat_lbl = QLabel(label.title())
        cat_lbl.setFont(W.label_font(8, tracking=2.0))
        cat_lbl.setStyleSheet(f"color:{W.MEMORY.DIM};background:transparent;border:none;")
        cat_lbl.setAlignment(Qt.AlignCenter)
        lay.addWidget(self.val_lbl); lay.addWidget(cat_lbl)

    def set_value(self, v):
        self.val_lbl.setText(str(v))

    def set_selected(self, selected: bool):
        self._selected = selected
        self.setStyleSheet(self._sel_style if selected else self._base_style)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.filter_clicked.emit(self._key)
        super().mousePressEvent(event)


# ═══════════════════════════════════════════════════════════════════
#  MEMORY ITEM ROW
# ═══════════════════════════════════════════════════════════════════
class _MemItem(QFrame):
    delete_requested = pyqtSignal(str, str)   # fact, category

    def __init__(self, text, category, dot_color="#b432ff", parent=None):
        super().__init__(parent)
        self._text = text; self._cat = category
        self.setStyleSheet(W.MEMORY.ROW)
        lay = QHBoxLayout(self); lay.setContentsMargins(10,6,8,6); lay.setSpacing(8)

        dot = QLabel("●")
        dot.setStyleSheet(f"color:{dot_color};font-size:9px;background:transparent;border:none;")
        lay.addWidget(dot)

        txt = QLabel(text)
        txt.setWordWrap(True)
        txt.setStyleSheet("color:#e8dcff;font:12px 'Consolas';background:transparent;border:none;")
        lay.addWidget(txt, 1)

        cat_tag = QLabel(category)
        cat_tag.setStyleSheet(f"color:{dot_color};font:9px 'Consolas';background:transparent;border:none;")
        lay.addWidget(cat_tag)

        del_btn = QPushButton("✕")
        del_btn.setFixedSize(18,18)
        del_btn.setStyleSheet("QPushButton{background:transparent;color:#8a6bb0;border:none;font-size:12px;}"
                              "QPushButton:hover{color:#ff6688;}")
        del_btn.clicked.connect(lambda: self.delete_requested.emit(self._text, self._cat))
        lay.addWidget(del_btn)


# ═══════════════════════════════════════════════════════════════════
#  RECENT ACTIVITY ITEM
# ═══════════════════════════════════════════════════════════════════
class _RecentItem(QFrame):
    def __init__(self, title, snippet, time_str, icon="💬", parent=None):
        super().__init__(parent)
        self.setStyleSheet(W.MEMORY.ROW)
        lay = QVBoxLayout(self); lay.setContentsMargins(10,7,10,7); lay.setSpacing(2)

        top = QHBoxLayout(); top.setSpacing(6)
        icon_lbl = QLabel(icon)
        icon_lbl.setStyleSheet("background:transparent;border:none;font-size:13px;")
        top.addWidget(icon_lbl)
        title_lbl = QLabel(title)
        title_lbl.setStyleSheet("color:#eddcff;font:bold 11px 'Consolas';background:transparent;border:none;")
        top.addWidget(title_lbl, 1)
        time_lbl = QLabel(time_str)
        time_lbl.setStyleSheet("color:#9678b8;font:9px 'Consolas';background:transparent;border:none;")
        top.addWidget(time_lbl)
        lay.addLayout(top)

        snip_lbl = QLabel(snippet)
        snip_lbl.setWordWrap(True)
        snip_lbl.setStyleSheet("color:#b294d0;font:10px 'Consolas';background:transparent;border:none;")
        lay.addWidget(snip_lbl)


# ═══════════════════════════════════════════════════════════════════
#  MAIN INTERACTIVE WIDGET
# ═══════════════════════════════════════════════════════════════════
class MemoryWidget(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self._data = {"categories": {}, "recent": [], "workflows": {}, "total": 0}
        self._cat_colors = {
            "facts":       "#00d4ff",
            "preferences": "#b432ff",
            "workflows":   "#ff9a30",
            "execution":   "#30ff80",
        }
        self._build_ui()
        self._refresh()

    # ── BUILD ─────────────────────────────────────────────────────
    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(18, 18, 18, 14)
        root.setSpacing(10)

        # ── header bar ────────────────────────────────────────────
        hdr = QHBoxLayout(); hdr.setSpacing(10)
        hdr.addStretch()

        self._search_box = QLineEdit()
        self._search_box.setPlaceholderText("🔍  Search memories…")
        self._search_box.setFixedHeight(30)
        self._search_box.setFont(W.mono_font(10))
        self._search_box.setStyleSheet(W.MEMORY.INPUT)
        self._search_box.textChanged.connect(self._on_search)
        hdr.addWidget(self._search_box)

        ref_btn = QPushButton("↻")
        ref_btn.setFixedSize(30,30)
        ref_btn.setFont(W.mono_font(11))
        ref_btn.setStyleSheet(W.MEMORY.CHIP)
        ref_btn.clicked.connect(self._refresh)
        hdr.addWidget(ref_btn)
        root.addLayout(hdr)

        # ── stat cards (clickable filters) ────────────────────────────
        stats_row = QHBoxLayout(); stats_row.setSpacing(8)
        self._total_card = _StatCard("Total", "all", "0", "#cc80ff")
        self._total_card.filter_clicked.connect(self._set_filter)
        self._total_card.setMinimumWidth(70)
        stats_row.addWidget(self._total_card)
        self._cat_cards = {}
        for cat, col in self._cat_colors.items():
            card = _StatCard(cat, cat, "0", col)
            card.filter_clicked.connect(self._set_filter)
            self._cat_cards[cat] = card
            stats_row.addWidget(card)
        self._total_card.set_selected(True)  # default: "all" selected
        root.addLayout(stats_row)

        # ── centre orb + list (3-col: recent | orb | list) ──────────
        mid = QHBoxLayout(); mid.setSpacing(10)

        # LEFT: recent activity column
        recent_col = QVBoxLayout(); recent_col.setSpacing(5)
        rec_hdr = QLabel("RECENT ACTIVITY")
        rec_hdr.setStyleSheet("color:#c9a3f0;font:bold 10px 'Consolas';background:transparent;letter-spacing:1px;")
        recent_col.addWidget(rec_hdr)

        self._recent_scroll = QScrollArea()
        self._recent_scroll.setWidgetResizable(True)
        self._recent_scroll.setFixedWidth(215)
        self._recent_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._recent_scroll.setStyleSheet(W.MEMORY.SCROLL)
        self._recent_container = QWidget()
        self._recent_container.setAttribute(Qt.WA_TranslucentBackground)
        self._recent_layout = QVBoxLayout(self._recent_container)
        self._recent_layout.setContentsMargins(0,0,4,0)
        self._recent_layout.setSpacing(5)
        self._recent_layout.addStretch()
        self._recent_scroll.setWidget(self._recent_container)
        recent_col.addWidget(self._recent_scroll, 1)
        mid.addLayout(recent_col)

        # CENTRE: orb
        orb_col = QVBoxLayout(); orb_col.setAlignment(Qt.AlignHCenter | Qt.AlignVCenter)
        self._orb = _NeuralOrb(size=110)
        orb_col.addWidget(self._orb)
        self._orb_label = QLabel("Neural Core")
        self._orb_label.setAlignment(Qt.AlignCenter)
        self._orb_label.setStyleSheet("color:#a878d0;font:10px 'Consolas';background:transparent;")
        orb_col.addWidget(self._orb_label)
        mid.addLayout(orb_col)

        # memory list area
        list_col = QVBoxLayout(); list_col.setSpacing(6)

        cat_hdr_row = QHBoxLayout()
        cat_lbl = QLabel("Memory Entries")
        cat_lbl.setStyleSheet("color:#c9a3f0;font:bold 11px 'Consolas';background:transparent;")
        cat_hdr_row.addWidget(cat_lbl)
        cat_hdr_row.addStretch()
        list_col.addLayout(cat_hdr_row)

        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._scroll.setStyleSheet(W.MEMORY.SCROLL)
        self._list_container = QWidget()
        self._list_container.setAttribute(Qt.WA_TranslucentBackground)
        self._list_layout = QVBoxLayout(self._list_container)
        self._list_layout.setContentsMargins(0,0,0,0)
        self._list_layout.setSpacing(4)
        self._list_layout.addStretch()
        self._scroll.setWidget(self._list_container)
        list_col.addWidget(self._scroll)
        mid.addLayout(list_col, 1)
        root.addLayout(mid, 1)

        # ── add memory bar ────────────────────────────────────────
        add_row = QHBoxLayout(); add_row.setSpacing(8)
        self._add_edit = QLineEdit()
        self._add_edit.setPlaceholderText("Type a new memory and press ＋  (e.g. I prefer dark mode)")
        self._add_edit.setFixedHeight(34)
        self._add_edit.setFont(W.mono_font(10))
        self._add_edit.setStyleSheet(W.MEMORY.INPUT)
        self._add_edit.returnPressed.connect(self._add_memory)
        add_row.addWidget(self._add_edit, 1)

        self._add_btn = QPushButton("＋")
        self._add_btn.setFixedSize(34,34)
        self._add_btn.setFont(W.mono_font(12))
        self._add_btn.setStyleSheet(W.MEMORY.COMMAND)
        self._add_btn.clicked.connect(self._add_memory)
        add_row.addWidget(self._add_btn)
        root.addLayout(add_row)

        # status label shown during AI processing
        self._status_lbl = QLabel("")
        self._status_lbl.setAlignment(Qt.AlignCenter)
        self._status_lbl.setStyleSheet("color:#c9a3f0;font:italic 10px 'Consolas';background:transparent;")
        self._status_lbl.hide()
        root.addWidget(self._status_lbl)

        self._active_filter = "all"
        self._search_query = ""

    # ── DATA LOADING ──────────────────────────────────────────────
    def _refresh(self):
        self._thread = QThread()
        self._loader = _MemLoader()
        self._loader.moveToThread(self._thread)
        self._thread.started.connect(self._loader.load)
        self._loader.done.connect(self._on_data_loaded)
        self._loader.done.connect(self._thread.quit)
        self._thread.start()

    def _on_data_loaded(self, data):
        self._data = data
        self._total_card.set_value(data["total"])
        for cat, card in self._cat_cards.items():
            card.set_value(len(data["categories"].get(cat, [])))
        self._orb_label.setText(f"{data['total']} memories synced")
        self._rebuild_recent(data.get("recent", []))
        self._rebuild_list()

    def _rebuild_recent(self, convs):
        # clear old items
        while self._recent_layout.count() > 1:
            item = self._recent_layout.takeAt(0)
            if item.widget(): item.widget().deleteLater()

        icons = ["💬", "📝", "📂", "🔧", "💡", "⚡"]
        cat_icons = {"facts": "📌", "preferences": "⚙️", "workflows": "🔄", "execution": "⚡"}

        # Recent conversations first
        for i, conv in enumerate(reversed(convs)):
            ts = conv.get("timestamp", "")
            try:
                dt = datetime.datetime.strptime(ts, "%Y-%m-%d %H:%M")
                diff = datetime.datetime.now() - dt
                mins = int(diff.total_seconds() / 60)
                if mins < 60:   time_str = f"{mins}m ago"
                elif mins < 1440: time_str = f"{mins//60}h ago"
                else:             time_str = f"{mins//1440}d ago"
            except Exception:
                time_str = ts[:10] if ts else "—"

            row = _RecentItem(
                conv.get("title", "Conversation")[:28],
                conv.get("summary", "")[:60] + ("…" if len(conv.get("summary","")) > 60 else ""),
                time_str, "💬"
            )
            self._recent_layout.insertWidget(self._recent_layout.count()-1, row)

        # If no conversations, show per-category fact count as activity rows
        if not convs:
            for cat, entries in self._data["categories"].items():
                if entries:
                    row = _RecentItem(
                        f"{len(entries)} {cat.title()} Stored",
                        entries[-1][:55] + "…" if len(entries[-1]) > 55 else entries[-1],
                        "synced", cat_icons.get(cat, "📌")
                    )
                    self._recent_layout.insertWidget(self._recent_layout.count()-1, row)

    def _rebuild_list(self):
        # clear existing items (keep trailing stretch)
        while self._list_layout.count() > 1:
            item = self._list_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        q = self._search_query.lower()
        cats = list(self._cat_colors.keys()) if self._active_filter == "all" else [self._active_filter]

        for cat in cats:
            entries = self._data["categories"].get(cat, [])
            col = self._cat_colors.get(cat, "#b432ff")
            for fact in reversed(entries):
                if q and q not in fact.lower():
                    continue
                row = _MemItem(fact, cat, col)
                row.delete_requested.connect(self._delete_memory)
                self._list_layout.insertWidget(self._list_layout.count()-1, row)

    # ── ACTIONS ───────────────────────────────────────────────────
    def _get_user_name(self):
        """Try to read user's name from facts memory, fall back to 'User'."""
        try:
            from memory.semantic_memory import _load_category
            facts = _load_category("facts")
            for fact in facts:
                fl = fact.lower()
                if "name is" in fl or "name's" in fl:
                    # extract the name after "name is "
                    for phrase in ["name is ", "name's ", "named "]:
                        if phrase in fl:
                            name = fact[fl.index(phrase)+len(phrase):].split()[0].strip('.,!')
                            if name:
                                return name.title()
        except Exception:
            pass
        return "User"

    def _normalize_text(self, text):
        """Replace first-person pronouns with the real user name."""
        name = self._get_user_name()
        import re
        # Replace patterns like "I like" -> "<Name> likes", "I am" -> "<Name> is"
        replacements = [
            (r"^[Ii] am\b", f"{name} is"),
            (r"^[Ii]'m\b",  f"{name} is"),
            (r"^[Ii] was\b", f"{name} was"),
            (r"^[Ii] have\b",f"{name} has"),
            (r"^[Ii] can\b", f"{name} can"),
            (r"^[Ii] will\b",f"{name} will"),
            (r"^[Ii] won't\b",f"{name} won't"),
            (r"^[Ii] don't\b",f"{name} doesn't"),
            (r"^[Ii] do\b",  f"{name} does"),
            (r"^[Ii] like\b", f"{name} likes"),
            (r"^[Ii] love\b", f"{name} loves"),
            (r"^[Ii] hate\b", f"{name} hates"),
            (r"^[Ii] prefer\b",f"{name} prefers"),
            (r"^[Ii] want\b", f"{name} wants"),
            (r"^[Ii] need\b", f"{name} needs"),
            (r"^[Ii] use\b",  f"{name} uses"),
            (r"^[Ii] work\b", f"{name} works"),
            (r"^[Ii] study\b",f"{name} studies"),
            (r"^[Ii]\b",      f"{name}"),   # catch-all: bare "I" at start
        ]
        # Also replace " my " -> " <Name>'s "
        result = text
        for pattern, replacement in replacements:
            new = re.sub(pattern, replacement, result, count=1)
            if new != result:
                result = new
                break
        result = re.sub(r"\bmy\b", f"{name}'s", result, flags=re.IGNORECASE)
        result = re.sub(r"\bme\b", name, result, flags=re.IGNORECASE)
        return result

    def _add_memory(self):
        text = self._add_edit.text().strip()
        if not text:
            return
        self._add_edit.clear()
        self._add_btn.setEnabled(False)
        self._status_lbl.setText("⏳  AI is extracting memories…")
        self._status_lbl.show()

        name = self._get_user_name()
        # Run AI extractor in background thread
        self._ai_thread = QThread()
        self._ai_worker = _AIMemoryExtractor(text, name)
        self._ai_worker.moveToThread(self._ai_thread)
        self._ai_thread.started.connect(self._ai_worker.run)
        self._ai_worker.done.connect(self._on_ai_extracted)
        self._ai_worker.done.connect(self._ai_thread.quit)
        self._ai_thread.start()

    def _on_ai_extracted(self, facts: list):
        """Receives extracted facts from AI and saves each one."""
        _, save_memory, _, _, _ = _mem()
        saved = 0
        if save_memory:
            for fact in facts:
                fact = fact.strip()
                if fact:
                    save_memory(fact)
                    saved += 1
        self._add_btn.setEnabled(True)
        if saved == 1:
            self._status_lbl.setText(f"✓  Saved 1 memory")
        elif saved > 1:
            self._status_lbl.setText(f"✓  Saved {saved} memories from your text")
        else:
            self._status_lbl.setText("⚠  Nothing extracted")
        QTimer.singleShot(2500, self._status_lbl.hide)
        QTimer.singleShot(400, self._refresh)

    def _delete_memory(self, fact, category):
        try:
            from memory.semantic_memory import _load_category, _save_category
            entries = _load_category(category)
            if fact in entries:
                entries.remove(fact)
                _save_category(category, entries)
        except Exception:
            pass
        QTimer.singleShot(100, self._refresh)

    def _set_filter(self, cat):
        self._active_filter = cat
        # Update stat card selection state
        self._total_card.set_selected(cat == "all")
        for k, card in self._cat_cards.items():
            card.set_selected(k == cat)
        self._rebuild_list()

    def _on_search(self, text):
        self._search_query = text
        self._rebuild_list()

    def paintEvent(self, event):
        """The workshop frame: flat ground, scanlines, hairline edge, brackets."""
        W.MEMORY.frame(self)
