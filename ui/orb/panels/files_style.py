"""
files_style.py — the visual language for the Files planet.

Deliberately NOT the default dark-dashboard look. The reference is an avionics
multi-function display / mission-control telemetry readout:

    - flat ground, no glass, no blur, no gradients on controls
    - square corners; framing comes from corner brackets and hairlines,
      never from rounded card outlines
    - NO card-inside-a-card: rows are separated by rules, not boxes
    - condensed letterspaced caps for labels, monospace for every value,
      so numbers form real columns
    - exactly one hot accent, spent only on the active element

Banned here on purpose (this is what made the first pass look generic):
rounded pills, per-row card backgrounds, gradient buttons, accent borders on
everything, uniform 8px radius on every surface.
"""
from PyQt5.QtGui import QFont

# ── palette (capped on purpose) ──────────────────────────────────────────────
GROUND   = "#05090B"   # flat near-black, faintly cool
GROUND_2 = "#080F12"   # row band
RULE     = "#15302B"   # hairline
RULE_HI  = "#1F4A42"   # hairline, emphasised
DIM      = "#4E6F68"   # secondary / units
MUTED    = "#6E948B"   # tertiary label
TEXT     = "#D6E6E1"   # primary
BRIGHT   = "#EFFAF7"   # filename / focus
ACCENT   = "#00F5C0"   # the one hot colour — active state only
WARN     = "#FF6B5A"   # abort only

# ── type ─────────────────────────────────────────────────────────────────────
# Two real faces, each with a job: condensed grotesque for labels, mono for data.
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


# ── reusable stylesheet fragments ────────────────────────────────────────────
# Every one of these is square-cornered and borderless by default; emphasis is
# added with a single edge, never a full outline.

TRANSPARENT_LABEL = "background:transparent;border:none;"

TAB_ACTIVE = (
    f"QPushButton{{background:transparent;border:none;"
    f"border-bottom:2px solid {ACCENT};color:{ACCENT};padding:0 4px 5px 4px;}}"
)
TAB_IDLE = (
    f"QPushButton{{background:transparent;border:none;"
    f"border-bottom:2px solid transparent;color:{DIM};padding:0 4px 5px 4px;}}"
    f"QPushButton:hover{{color:{TEXT};border-bottom-color:{RULE_HI};}}"
)

# Terminal-style entry: one hairline underneath, nothing else.
INPUT = (
    f"QLineEdit{{background:transparent;border:none;"
    f"border-bottom:1px solid {RULE_HI};color:{BRIGHT};"
    f"padding:3px 2px;selection-background-color:{ACCENT};selection-color:#04110E;}}"
    f"QLineEdit:focus{{border-bottom:1px solid {ACCENT};}}"
)

# Actions read as bracketed commands, not buttons.
ACTION = (
    f"QPushButton{{background:transparent;border:none;color:{ACCENT};padding:0 6px;}}"
    f"QPushButton:hover{{color:{BRIGHT};}}"
)
ACTION_WARN = (
    f"QPushButton{{background:transparent;border:none;color:{WARN};padding:0 6px;}}"
    f"QPushButton:hover{{color:#FFB0A6;}}"
)

FILTER_ON = (
    f"QPushButton{{background:transparent;border:none;color:{ACCENT};padding:0 3px;}}"
)
FILTER_OFF = (
    f"QPushButton{{background:transparent;border:none;color:{DIM};padding:0 3px;}}"
    f"QPushButton:hover{{color:{TEXT};}}"
)

# A row is a band with a rule under it and a left edge that lights up on hover.
# No radius, no card.
ROW = (
    f"QFrame{{background:transparent;border:none;"
    f"border-bottom:1px solid {RULE};border-left:2px solid transparent;}}"
    f"QFrame:hover{{background:rgba(0, 245, 192, 16);"
    f"border-left:2px solid {ACCENT};}}"
)

ROW_GLYPH = (
    f"QPushButton{{background:transparent;border:none;color:{DIM};padding:0 5px;"
    f"text-align:center;}}"
    f"QPushButton:hover{{color:{ACCENT};}}"
)

# Rows are transparent by design (no cards), so the list needs its own ground.
# Same value as the panel fill so there's no visible slab where the list starts,
# and light enough to sit in the same range as the System panel's cards.
GROUND_ALPHA = 160
LIST_GROUND = f"rgba(4, 8, 10, {GROUND_ALPHA})"

# Transparent here on purpose: the list container is the single surface that
# paints the ground. Painting it on the scroll area as well stacks two alphas
# and the results area ends up a different shade from the header.
SCROLL = (
    "QScrollArea{background:transparent;border:none;}"
    "QScrollArea > QWidget > QWidget{background:transparent;}"
    "QScrollBar:vertical{background:transparent;width:3px;margin:0;}"
    f"QScrollBar::handle:vertical{{background:{RULE_HI};}}"
    "QScrollBar::add-line:vertical,QScrollBar::sub-line:vertical{height:0;}"
    "QScrollBar::add-page:vertical,QScrollBar::sub-page:vertical{background:transparent;}"
)

COMBO = (
    f"QComboBox{{background:transparent;border:none;color:{TEXT};padding:0 2px;}}"
    f"QComboBox::drop-down{{border:none;width:14px;}}"
    f"QComboBox QAbstractItemView{{background:{GROUND};color:{TEXT};"
    f"border:1px solid {RULE_HI};selection-background-color:{RULE_HI};"
    f"outline:none;}}"
)
