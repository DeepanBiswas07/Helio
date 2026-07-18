from PyQt5.QtGui import QColor, QFont
from PyQt5.QtCore import Qt
from .base import BasePanel, _content_alpha, _draw_header

class Panel1(BasePanel):
    def __init__(self, title="PLANET 1", accent=QColor(255, 140, 40)):
        super().__init__(title, accent)

    def draw(self, painter, shape_rect, panel_progress, alpha):
        content_a = _content_alpha(panel_progress, alpha)
        _draw_header(painter, shape_rect, self.title, self.accent, content_a)
        if content_a <= 0:
            return

        painter.save()
        painter.setOpacity((content_a / 255.0) * 0.5)
        font = QFont("Segoe UI", 10)
        painter.setFont(font)
        painter.setPen(QColor(180, 180, 180, content_a))
        painter.drawText(
            shape_rect.adjusted(28, 70, -28, -28).toRect(),
            Qt.AlignTop | Qt.AlignLeft | Qt.TextWordWrap,
            "This panel is not yet configured.\nEdit ui/orb/panels/panel_1.py to add content here."
        )
        painter.restore()
