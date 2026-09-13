"""
workshop_style.py — the shared visual language for every Helio planet panel.

One workshop, four bays. MEMORY, CHAT, FILES, SYSTEM and SETUP each get their
own accent, but the *construction* is identical, so the planets read as five
instruments on one bench instead of five different apps.

The reference is a fabrication bench with a holographic readout on it. What
that means concretely, and what it rules out:

    - Flat ground. No glass, no blur, no gradients on controls.
    - Square corners. Framing comes from hairlines, tick rules, corner
      brackets and registration crosses — never from a rounded card outline.
    - No card-inside-a-card: rows are separated by rules, not boxes.
    - Condensed letterspaced caps for labels, monospace for every value, so
      numbers line up in real columns.
    - Exactly one hot accent per panel, spent only on the live element.

Banned on purpose, because each one is what makes a panel read as a generic
web dashboard: rounded pills, per-row card backgrounds, gradient buttons,
uniform 8px radii, accent borders on every surface, drop shadows.

Usage:

    from . import workshop_style as W
    S = W.Workshop(180, 50, 255)      # the planet's accent
    label.setStyleSheet(f"color:{S.DIM};{W.TRANSPARENT_LABEL}")
    ...
    def paintEvent(self, event):
        S.frame(self, exclude=(self._scroll,))
"""
from PyQt5.QtGui import QColor, QFont, QPainter, QPen, QRegion
from PyQt5.QtCore import Qt

# ── type ─────────────────────────────────────────────────────────────────────
LABEL_FACE = "Bahnschrift"   # ships with Windows 10/11
MONO_FACE  = "Consolas"

TRANSPARENT_LABEL = "background:transparent;border:none;"

# Every panel sits at the same translucency, so the planets look like one set.
GROUND_ALPHA = 160


def label_font(size=10, bold=False, tracking=1.7):
    """Letterspaced condensed caps. QSS has no letter-spacing, so it's set here."""
    f = QFont(LABEL_FACE, size)
    f.setBold(bold)
    f.setCapitalization(QFont.AllUppercase)
    f.setLetterSpacing(QFont.AbsoluteSpacing, tracking)
    return f


def mono_font(size=10, bold=False):
    f = QFont(MONO_FACE, size)
    f.setBold(bold)
    return f


def _mix(a, b, t):
    return tuple(int(round(a[i] + (b[i] - a[i]) * t)) for i in range(3))


def _hex(rgb):
    return "#{:02X}{:02X}{:02X}".format(*rgb)


class Workshop:
    """
    A panel's palette and chrome, derived from one accent colour.

    Deriving the whole ramp instead of hand-picking per panel is what keeps
    five planets consistent — the tones sit at the same contrast in each,
    so no panel ends up with unreadable 8px grey-on-grey the way the first
    pass did.
    """

    BLACK = (4, 6, 8)
    WHITE = (255, 255, 255)

    def __init__(self, r, g, b):
        accent = (r, g, b)
        self.accent_rgb = accent

        # Ground is the accent drained almost to black — a faint tint, so a
        # red panel and a violet panel don't share a literally identical fill.
        self.ground_rgb = _mix(self.BLACK, accent, 0.06)

        self.ACCENT   = _hex(accent)
        self.ACCENT_D = _hex(_mix(self.ground_rgb, accent, 0.72))
        self.GROUND   = _hex(self.ground_rgb)
        self.GRID     = _hex(_mix(self.ground_rgb, accent, 0.14))
        self.RULE     = _hex(_mix(self.ground_rgb, accent, 0.24))
        self.RULE_HI  = _hex(_mix(self.ground_rgb, accent, 0.46))
        self.DIM      = _hex(_mix(_mix(self.ground_rgb, accent, 0.70), self.WHITE, 0.22))
        self.MUTED    = _hex(_mix(_mix(self.ground_rgb, accent, 0.82), self.WHITE, 0.38))
        self.TEXT     = _hex(_mix(accent, self.WHITE, 0.72))
        self.BRIGHT   = _hex(_mix(accent, self.WHITE, 0.88))
        self.WARN     = "#FF6B5A"

        self.C_ACCENT   = QColor(*accent)
        self.C_ACCENT_D = QColor(*_mix(self.ground_rgb, accent, 0.72))
        self.C_RULE     = QColor(*_mix(self.ground_rgb, accent, 0.24))
        self.C_GRID     = QColor(*_mix(self.ground_rgb, accent, 0.14))
        self.C_DIM      = QColor(*_mix(_mix(self.ground_rgb, accent, 0.70), self.WHITE, 0.22))
        self.C_TEXT     = QColor(*_mix(accent, self.WHITE, 0.72))
        self.C_BRIGHT   = QColor(*_mix(accent, self.WHITE, 0.88))
        self.C_WARN     = QColor(255, 107, 90)

        self.PANEL_GROUND = "rgba({}, {}, {}, {})".format(
            self.ground_rgb[0], self.ground_rgb[1], self.ground_rgb[2], GROUND_ALPHA)

        self._build_fragments()

    # ── stylesheet fragments ──────────────────────────────────────────────
    def _build_fragments(self):
        a = self.accent_rgb
        tint = "rgba({}, {}, {}, {{}})".format(*a)

        # Bay / tab selectors: an indexed rack label with a lit left edge.
        # Deliberately not an underlined text tab — that is the website tell.
        self.BAY_ACTIVE = (
            f"QPushButton{{background:{tint.format(20)};border:none;"
            f"border-left:3px solid {self.ACCENT};color:{self.ACCENT};"
            f"padding:5px 14px 5px 10px;text-align:left;}}"
        )
        self.BAY_IDLE = (
            f"QPushButton{{background:transparent;border:none;"
            f"border-left:3px solid {self.RULE};color:{self.DIM};"
            f"padding:5px 14px 5px 10px;text-align:left;}}"
            f"QPushButton:hover{{color:{self.TEXT};"
            f"border-left:3px solid {self.RULE_HI};background:{tint.format(8)};}}"
        )

        # The one energised control on a panel.
        self.COMMAND = (
            f"QPushButton{{background:{tint.format(26)};"
            f"border:1px solid {self.ACCENT_D};color:{self.ACCENT};padding:5px 15px;}}"
            f"QPushButton:hover{{background:{tint.format(55)};"
            f"border:1px solid {self.ACCENT};color:{self.BRIGHT};}}"
            f"QPushButton:disabled{{background:transparent;"
            f"border:1px solid {self.RULE_HI};color:{self.DIM};}}"
        )

        # Secondary commands are engraved text, no box.
        self.ENGRAVED = (
            f"QPushButton{{background:transparent;border:none;"
            f"color:{self.DIM};padding:0 7px;}}"
            f"QPushButton:hover{{color:{self.ACCENT};}}"
        )
        self.ENGRAVED_WARN = (
            f"QPushButton{{background:transparent;border:none;"
            f"color:{self.DIM};padding:0 7px;}}"
            f"QPushButton:hover{{color:{self.WARN};}}"
        )

        # Terminal-style entry: one hairline underneath, nothing else.
        self.INPUT = (
            f"QLineEdit{{background:transparent;border:none;"
            f"border-bottom:1px solid {self.RULE_HI};color:{self.BRIGHT};"
            f"padding:4px 2px;selection-background-color:{self.ACCENT};"
            f"selection-color:{self.GROUND};}}"
            f"QLineEdit:focus{{border-bottom:1px solid {self.ACCENT};}}"
        )

        # A row is a band with a rule under it and a left edge that lights up.
        # No radius, no card.
        self.ROW = (
            f"QFrame{{background:transparent;border:none;"
            f"border-bottom:1px solid {self.RULE};"
            f"border-left:2px solid transparent;}}"
            f"QFrame:hover{{background:{tint.format(16)};"
            f"border-left:2px solid {self.ACCENT};}}"
        )

        # Square, hairline chips. Never pills.
        self.CHIP = (
            f"QPushButton{{background:transparent;border:1px solid {self.RULE};"
            f"color:{self.DIM};padding:3px 9px;}}"
            f"QPushButton:hover{{border:1px solid {self.ACCENT_D};"
            f"color:{self.TEXT};background:{tint.format(14)};}}"
        )

        # Transparent by design: exactly one surface paints the ground, or the
        # alphas stack and the body reads as an opaque slab under a
        # see-through header.
        self.SCROLL = (
            "QScrollArea{background:transparent;border:none;}"
            "QScrollArea > QWidget > QWidget{background:transparent;}"
            "QScrollBar:vertical{background:transparent;width:3px;margin:0;}"
            f"QScrollBar::handle:vertical{{background:{self.RULE_HI};}}"
            "QScrollBar::add-line:vertical,QScrollBar::sub-line:vertical{height:0;}"
            "QScrollBar::add-page:vertical,QScrollBar::sub-page:vertical"
            "{background:transparent;}"
            # Horizontal too — a no-wrap code view gets one, and unstyled it
            # renders as a bright native bar across the bottom of the panel.
            "QScrollBar:horizontal{background:transparent;height:3px;margin:0;}"
            f"QScrollBar::handle:horizontal{{background:{self.RULE_HI};}}"
            "QScrollBar::add-line:horizontal,QScrollBar::sub-line:horizontal"
            "{width:0;}"
            "QScrollBar::add-page:horizontal,QScrollBar::sub-page:horizontal"
            "{background:transparent;}"
        )

        self.PROGRESS = (
            f"QProgressBar{{background:{tint.format(18)};border:none;"
            f"border-left:1px solid {self.RULE_HI};height:5px;text-align:right;"
            f"color:transparent;}}"
            f"QProgressBar::chunk{{background:{self.ACCENT};}}"
        )

    # ── chrome ────────────────────────────────────────────────────────────
    def registration(self, p, rect, arm=7, alpha=150):
        """Machined crosses at the corners — the bench's registration marks."""
        colour = QColor(self.C_ACCENT_D)
        colour.setAlpha(alpha)
        p.setPen(QPen(colour, 1))
        for x, y in ((rect.left(), rect.top()), (rect.right(), rect.top()),
                     (rect.left(), rect.bottom()), (rect.right(), rect.bottom())):
            p.drawLine(x - arm, y, x + arm, y)
            p.drawLine(x, y - arm, x, y + arm)

    def tick_rule(self, p, x0, x1, y, every=9, height=3):
        """A hairline with fabrication ticks, instead of a plain divider."""
        p.setPen(QPen(self.C_RULE, 1))
        p.drawLine(int(x0), int(y), int(x1), int(y))
        x = x0
        while x < x1:
            p.drawLine(int(x), int(y), int(x), int(y - height))
            x += every

    def frame(self, widget, exclude=()):
        """
        Paint a panel's whole instrument frame: translucent ground, scanlines,
        hairline edge and corner brackets.

        `exclude` lists child surfaces that paint their own ground (a scroll
        area's container, a bench). They are subtracted from the fill so two
        alphas never stack — the bug that made the Files body read as an
        opaque slab under a see-through header.
        """
        p = QPainter(widget)
        r = widget.rect().adjusted(1, 1, -1, -1)

        ground = QRegion(r)
        for surface in exclude:
            if surface is not None and surface.isVisible():
                ground = ground.subtracted(QRegion(surface.geometry()))
        p.setClipRegion(ground)
        p.fillRect(r, QColor(self.ground_rgb[0], self.ground_rgb[1],
                             self.ground_rgb[2], GROUND_ALPHA))
        p.setClipping(False)

        # Scanlines — faint horizontal texture instead of a flat wash.
        scan = QColor(*self.accent_rgb)
        scan.setAlpha(8)
        p.setPen(QPen(scan, 1))
        y = r.top()
        while y < r.bottom():
            p.drawLine(r.left(), y, r.right(), y)
            y += 3

        edge = QColor(self.C_ACCENT_D)
        edge.setAlpha(190)
        p.setPen(QPen(edge, 1))
        p.setBrush(Qt.NoBrush)
        p.drawRect(r)

        # Corner brackets carry the framing, so no full accent outline needed.
        bracket = QColor(*self.accent_rgb)
        bracket.setAlpha(210)
        p.setPen(QPen(bracket, 1.6))
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


# The five planets, so every panel imports its accent from one place.
MEMORY = Workshop(180, 50, 255)
CHAT   = Workshop(0, 180, 255)
FILES  = Workshop(0, 220, 170)
SYSTEM = Workshop(255, 90, 20)
SETUP  = Workshop(150, 225, 70)
# Rose sits in the one clear gap on the wheel — ~50° off MEMORY's violet and
# far from SYSTEM's red-orange, so a glance at the ring never confuses them.
SCHEDULE = Workshop(255, 70, 150)

# Molten amber — the one colour a forge should be. It reads clearly against
# SYSTEM's red-orange two slots away (that one is far redder), and against
# SCHEDULE's rose beside it on the ring.
FORGE = Workshop(255, 185, 0)
