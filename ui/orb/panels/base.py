from PyQt5.QtGui import QColor, QPen, QFont, QFontMetrics
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
