from PyQt5.QtGui import QColor, QFont, QPen, QLinearGradient
from PyQt5.QtCore import Qt, QRectF
from .base import BasePanel, _content_alpha, _draw_header

class FilesPanel(BasePanel):
    def __init__(self):
        super().__init__("FILES", QColor(0, 220, 170)) # Sleek emerald/teal accent

    def draw(self, painter, shape_rect, panel_progress, alpha):
        content_a = _content_alpha(panel_progress, alpha)
        _draw_header(painter, shape_rect, self.title, self.accent, content_a)
        if content_a <= 0:
            return

        painter.save()
        painter.setOpacity(content_a / 255.0)

        # Draw a premium files information container
        msg_rect = shape_rect.adjusted(16, 50, -16, -16)
        painter.setBrush(QColor(2, 18, 14, 90)) # Deep emerald green/teal glass tint
        painter.setPen(QPen(QColor(0, 220, 170, 40), 1.2))
        painter.drawRoundedRect(msg_rect, 10, 10)

        # Text fonts
        font_lbl = QFont("Segoe UI", 9, QFont.Bold)
        font_val = QFont("Segoe UI", 9)

        # 1. Indexing Status
        painter.setFont(font_lbl)
        painter.setPen(QColor(0, 220, 170, 220))
        painter.drawText(int(msg_rect.left() + 16), int(msg_rect.top() + 24), "File Indexing Status:")

        painter.setFont(font_val)
        painter.setPen(QColor(200, 255, 235, 200))
        painter.drawText(int(msg_rect.left() + 16), int(msg_rect.top() + 42), "  • Scope: C:/Users/deepan/Documents")
        painter.drawText(int(msg_rect.left() + 16), int(msg_rect.top() + 60), "  • Indexed Files: 12,482 documents")
        painter.drawText(int(msg_rect.left() + 16), int(msg_rect.top() + 78), "  • Last Sync: Just Now (Real-time)")

        # 2. Neat progress bar for Indexing completeness
        bar_y = msg_rect.top() + 104
        painter.setFont(font_lbl)
        painter.setPen(QColor(0, 220, 170, 200))
        painter.drawText(int(msg_rect.left() + 16), int(bar_y), "Search Index Integrity:")

        bar_rect = QRectF(msg_rect.left() + 16, bar_y + 10, msg_rect.width() - 32, 8)
        painter.setBrush(QColor(5, 30, 24, 200))
        painter.setPen(Qt.NoPen)
        painter.drawRoundedRect(bar_rect, 4, 4)

        grad = QLinearGradient(bar_rect.topLeft(), bar_rect.topRight())
        grad.setColorAt(0.0, QColor(0, 160, 130))
        grad.setColorAt(1.0, QColor(0, 220, 170))
        painter.setBrush(grad)
        fill_w = bar_rect.width() * 1.0  # 100% indexed
        painter.drawRoundedRect(QRectF(bar_rect.left(), bar_rect.top(), fill_w, 8), 4, 4)

        # 3. Recent documents
        rec_y = bar_y + 40
        painter.setFont(font_lbl)
        painter.setPen(QColor(0, 220, 170, 220))
        painter.drawText(int(msg_rect.left() + 16), int(rec_y), "Recent Local Documents:")

        painter.setFont(QFont("Segoe UI", 8))
        painter.setPen(QColor(180, 215, 205, 180))
        painter.drawText(int(msg_rect.left() + 20), int(rec_y + 18), "📄 report_final.pdf (PDF Document)")
        painter.drawText(int(msg_rect.left() + 20), int(rec_y + 34), "📝 project_notes.txt (Plain Text)")
        painter.drawText(int(msg_rect.left() + 20), int(rec_y + 50), "📊 financial_summary.xlsx (Excel Spreadsheet)")

        painter.restore()
