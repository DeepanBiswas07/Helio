import math
from PyQt5.QtGui import QColor, QPen, QBrush, QFont, QFontMetrics, QRadialGradient
from PyQt5.QtCore import Qt, QRectF, QPointF

class BasePanel:
    """Minimal contract every panel must fulfil."""
    def __init__(self, title: str, accent: QColor):
        self.title = title
        self.accent = accent

    def draw(self, painter, shape_rect: QRectF, panel_progress: float, alpha: int):
        raise NotImplementedError

def _content_alpha(panel_progress: float, alpha: int) -> int:
    """Content only fades in during the final 30% of the open animation."""
    t = max(0.0, (panel_progress - 0.7) / 0.3)
    return int(255 * t * (alpha / 255.0))

def _draw_header(painter, shape_rect: QRectF, title: str, accent: QColor, content_a: int):
    """Shared top-bar with title and a coloured accent underline."""
    if content_a <= 0:
        return

    painter.save()
    painter.setOpacity(content_a / 255.0)

    # Title text
    font = QFont("Segoe UI", 14, QFont.Bold)
    painter.setFont(font)
    painter.setPen(QColor(255, 255, 255, content_a))
    fm = QFontMetrics(font)
    tx = shape_rect.left() + 28
    ty = shape_rect.top() + 44
    painter.drawText(int(tx), int(ty), title)

    # Accent underline
    ul_y = ty + 8
    pen = QPen(accent)
    pen.setWidthF(2.0)
    painter.setPen(pen)
    painter.drawLine(
        QPointF(tx, ul_y),
        QPointF(tx + fm.width(title), ul_y)
    )

    painter.restore()


class ReservedPanel(BasePanel):
    """
    Shared 'reserved for a future capability' panel — a designed empty state
    rather than a bare 'not yet configured' textblock. Used by the 4 orbital
    slots that don't have a feature behind them yet.
    """
    def __init__(self, slot_number: int, total_slots: int = 4,
                 accent: QColor = QColor(178, 148, 108)):
        super().__init__(f"RESERVED · {slot_number:02d}", accent)
        self.slot_number = slot_number
        self.total_slots = total_slots
        self._t = 0.0

    def draw(self, painter, shape_rect, panel_progress, alpha):
        content_a = _content_alpha(panel_progress, alpha)
        _draw_header(painter, shape_rect, self.title, self.accent, content_a)
        if content_a <= 0:
            return

        self._t += 0.03

        painter.save()
        painter.setOpacity(content_a / 255.0)

        body = shape_rect.adjusted(16, 60, -16, -16)
        painter.setBrush(QColor(14, 11, 6, 70))
        painter.setPen(QPen(QColor(self.accent.red(), self.accent.green(), self.accent.blue(), 40), 1.2))
        painter.drawRoundedRect(body, 12, 12)

        cx = body.center().x()
        cy = body.center().y() - 10

        # Slow-breathing dashed ring — "idle, waiting" rather than "broken"
        pulse = 0.5 + 0.5 * math.sin(self._t)
        ring_r = 34.0 + pulse * 3.0
        painter.setBrush(Qt.NoBrush)
        dash_pen = QPen(QColor(self.accent.red(), self.accent.green(), self.accent.blue(), int(110 + 60 * pulse)))
        dash_pen.setWidthF(1.4)
        dash_pen.setCapStyle(Qt.RoundCap)
        painter.setPen(dash_pen)
        seg, gap = 14, 10
        ang = 0.0
        while ang < 360.0:
            painter.drawArc(
                QRectF(cx - ring_r, cy - ring_r, ring_r * 2, ring_r * 2),
                int(ang * 16), int(seg * 16)
            )
            ang += seg + gap

        # Soft core glow + centered glyph (three dots — "pending")
        glow = QRadialGradient(QPointF(cx, cy), 26)
        glow.setColorAt(0.0, QColor(self.accent.red(), self.accent.green(), self.accent.blue(), 70))
        glow.setColorAt(1.0, QColor(0, 0, 0, 0))
        painter.setBrush(glow)
        painter.setPen(Qt.NoPen)
        painter.drawEllipse(QPointF(cx, cy), 26, 26)

        painter.setBrush(QColor(240, 228, 210, 210))
        for dx in (-9, 0, 9):
            painter.drawEllipse(QPointF(cx + dx, cy), 2.6, 2.6)

        # Label + description
        painter.setPen(QColor(232, 214, 186, 220))
        font_lbl = QFont("Segoe UI", 10, QFont.Bold)
        painter.setFont(font_lbl)
        fm = QFontMetrics(font_lbl)
        label = "AWAITING ASSIGNMENT"
        painter.drawText(int(cx - fm.width(label) / 2), int(cy + 48), label)

        painter.setPen(QColor(200, 184, 160, 190))
        font_sub = QFont("Segoe UI", 9)
        painter.setFont(font_sub)
        sfm = QFontMetrics(font_sub)
        sub = f"Slot {self.slot_number} of {self.total_slots} · reserved for a future capability"
        painter.drawText(int(cx - sfm.width(sub) / 2), int(cy + 68), sub)

        painter.restore()
