"""
setup_style.py — the visual language for the SETUP planet (Helio's workshop).

The reference is a fabrication bench with a holographic schematic on it, not a
web page. What that means concretely, and what it rules out:

    - An assembly is drawn as a WIRED SCHEMATIC — nodes on a bus, callout
      leaders down to labels. Never a list of rows with a bullet.
    - Chrome is machined: registration crosses, tick rules, engraved
      designators (ASM 01 / REV 02), bracket corners. Never a card outline.
    - Two BAYS you switch between, shown as lit bay selectors with index
      numbers. Never text tabs across the top — that is the website tell.
    - Fills are for energised things only: a live command, an executing node.
      Everything else is line work on a dark bench.

Banned on purpose, because each one is what made the first pass read as a
generic dashboard: rounded pills, per-row card backgrounds, gradient buttons,
uniform radii, accent borders on every surface, centred content columns.
"""
from PyQt5.QtGui import QFont, QColor

# ── palette (lime; capped on purpose) ────────────────────────────────────────
GROUND    = "#06090A"
BENCH     = "#080D07"   # the work area, a shade off the panel ground
RULE      = "#243515"   # hairline
RULE_HI   = "#4E732C"   # hairline, emphasised
GRID      = "#16210E"   # bench grid
DIM       = "#93AC70"
MUTED     = "#B4C994"
TEXT      = "#DDE7CE"
BRIGHT    = "#F3F9EA"
ACCENT    = "#96E146"   # the one hot colour — energised state only
ACCENT_D  = "#7CBB34"   # accent, receded (wires, idle nodes)
WARN      = "#FF6B5A"

# Same values as QColor, for the painters.
C_ACCENT   = QColor(150, 225, 70)
C_ACCENT_D = QColor(124, 187, 52)
C_WIRE     = QColor(78, 115, 44)
C_GRID     = QColor(22, 33, 14)
C_DIM      = QColor(147, 172, 112)
C_MUTED    = QColor(180, 201, 148)
C_TEXT     = QColor(221, 231, 206)
C_BRIGHT   = QColor(243, 249, 234)
C_WARN     = QColor(255, 107, 90)

# ── type ─────────────────────────────────────────────────────────────────────
LABEL_FACE = "Bahnschrift"   # ships with Windows 10/11
MONO_FACE  = "Consolas"


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


# ── stylesheet fragments ─────────────────────────────────────────────────────
TRANSPARENT_LABEL = "background:transparent;border:none;"

# Bay selectors. An index number, a name, and a lit bar down the left edge —
# the shape of a rack label, not a nav link.
BAY_ACTIVE = (
    f"QPushButton{{background:rgba(150, 225, 70, 20);border:none;"
    f"border-left:3px solid {ACCENT};color:{ACCENT};"
    f"padding:5px 14px 5px 10px;text-align:left;}}"
)
BAY_IDLE = (
    f"QPushButton{{background:transparent;border:none;"
    f"border-left:3px solid {RULE};color:{DIM};"
    f"padding:5px 14px 5px 10px;text-align:left;}}"
    f"QPushButton:hover{{color:{TEXT};border-left:3px solid {RULE_HI};"
    f"background:rgba(150, 225, 70, 8);}}"
)

# The one energised control on the bench.
COMMAND = (
    f"QPushButton{{background:rgba(150, 225, 70, 26);"
    f"border:1px solid {ACCENT_D};color:{ACCENT};padding:5px 15px;}}"
    f"QPushButton:hover{{background:rgba(150, 225, 70, 55);"
    f"border:1px solid {ACCENT};color:{BRIGHT};}}"
    f"QPushButton:disabled{{background:transparent;"
    f"border:1px solid {RULE_HI};color:{DIM};}}"
)

# Secondary commands are engraved text, no box.
ENGRAVED = (
    f"QPushButton{{background:transparent;border:none;color:{DIM};padding:0 7px;}}"
    f"QPushButton:hover{{color:{ACCENT};}}"
)
ENGRAVED_WARN = (
    f"QPushButton{{background:transparent;border:none;color:{DIM};padding:0 7px;}}"
    f"QPushButton:hover{{color:{WARN};}}"
)

# Terminal-style entry: one hairline underneath, nothing else.
INPUT = (
    f"QLineEdit{{background:transparent;border:none;"
    f"border-bottom:1px solid {RULE_HI};color:{BRIGHT};"
    f"padding:3px 2px;selection-background-color:{ACCENT};selection-color:#0C1505;}}"
    f"QLineEdit:focus{{border-bottom:1px solid {ACCENT};}}"
)

# Parts bin. Square, hairline, no pill radius.
PART = (
    f"QPushButton{{background:transparent;border:1px solid {RULE};"
    f"color:{DIM};padding:3px 9px;}}"
    f"QPushButton:hover{{border:1px solid {ACCENT_D};color:{TEXT};"
    f"background:rgba(150, 225, 70, 14);}}"
)
PART_HOT = (
    f"QPushButton{{background:transparent;border:1px solid {ACCENT_D};"
    f"color:{ACCENT};padding:4px 12px;}}"
    f"QPushButton:hover{{background:rgba(150, 225, 70, 34);color:{BRIGHT};}}"
)

# Same translucency as the Files panel so the planets read as one system.
GROUND_ALPHA = 160
BENCH_GROUND = f"rgba(5, 9, 7, {GROUND_ALPHA})"

# Transparent by design: whichever bay is on screen paints the single ground
# layer. Painting it here too would stack two alphas and the body would read as
# an opaque slab under a see-through header.
SCROLL = (
    "QScrollArea{background:transparent;border:none;}"
    "QScrollArea > QWidget > QWidget{background:transparent;}"
    "QScrollBar:vertical{background:transparent;width:3px;margin:0;}"
    f"QScrollBar::handle:vertical{{background:{RULE_HI};}}"
    "QScrollBar::add-line:vertical,QScrollBar::sub-line:vertical{height:0;}"
    "QScrollBar::add-page:vertical,QScrollBar::sub-page:vertical{background:transparent;}"
)
