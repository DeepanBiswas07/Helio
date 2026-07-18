import random
import math
import datetime
from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, 
    QScrollArea, QLineEdit, QPushButton, QFrame, QSizePolicy
)
from PyQt5.QtCore import Qt, pyqtSignal, QSize, QTimer, QPointF, QRectF
from PyQt5.QtGui import QColor, QFont, QPainter, QPainterPath, QPen, QBrush, QRadialGradient
from .base import BasePanel, _content_alpha, _draw_header

# ── RENDERER ──────────────────────────────────────────────────────────────────

class ChatPanel(BasePanel):
    def __init__(self):
        super().__init__("CHAT", QColor(0, 180, 255))
        # Placeholder conversation lines shown in the panel preview
        self._preview_messages = [
            ("Helio", "Hello! How can I assist you today?", False),
            ("You",   "What's the weather like?",           True),
            ("Helio", "It's 28 °C and sunny in your area.", False),
            ("You",   "Great, thanks!",                     True),
        ]

    def draw(self, painter, shape_rect, panel_progress, alpha):
        content_a = _content_alpha(panel_progress, alpha)
        _draw_header(painter, shape_rect, self.title, self.accent, content_a)
        if content_a <= 0:
            return

        painter.save()
        painter.setOpacity(content_a / 255.0)

        # Message area background (glass-like to see the stars behind)
        msg_rect = shape_rect.adjusted(16, 50, -16, 0)
        painter.setBrush(QColor(0, 0, 0, 60))
        painter.setPen(QPen(QColor(0, 180, 255, 30), 1))
        painter.drawRoundedRect(msg_rect, 12, 12)

        painter.restore()


# ── INTERACTIVE WIDGET COMPONENTS ─────────────────────────────────────────────

class HoloOrbWidget(QWidget):
    """
    Highly premium holographic orb widget inspired by HoloOverlay and ChatPlanet.
    Features:
      - Volumetric aura (radial gradient breathing glow) in cyan/blue
      - Core plasma sphere
      - 3 Rotating concentric arc-rings (counter-rotating)
      - Floating energy particles
      - Dynamic state indicators:
        - "idle": Slow breathing and drift
        - "typing": High-frequency vibration, active ring spinning
        - "thinking": Quick flare pulses, speed-up of all rotations
        - "speaking": Core sizes scale dynamically matching simulated audio waves
    """
    def __init__(self, size=40, parent=None):
        super().__init__(parent)
        self.size = size
        self.setFixedSize(size, size)
        
        self.state = "idle"
        self.phase = 0.0
        self.volume = 0.0
        
        self.ring_angles = [0.0, 0.0, 0.0]
        self.rings_def = [
            {"rf": 0.50, "arc": 60, "gap": 20, "spd": 1.2, "r": 0, "g": 220, "b": 255, "w": 1.2, "a": 200},
            {"rf": 0.68, "arc": 45, "gap": 35, "spd": -0.8, "r": 0, "g": 160, "b": 255, "w": 0.9, "a": 160},
            {"rf": 0.85, "arc": 30, "gap": 50, "spd": 0.5, "r": 80, "g": 180, "b": 255, "w": 0.7, "a": 110},
        ]
        
        # Floating particles
        self.particles = []
        for i in range(6):
            self.particles.append({
                "angle": random.uniform(0, 360),
                "dist_pct": random.uniform(0.35, 0.8),
                "speed": random.uniform(0.3, 0.8) * random.choice([-1, 1]),
                "size": random.uniform(0.8, 1.8),
                "alpha": random.randint(50, 180)
            })
            
        self.timer = QTimer(self)
        self.timer.timeout.connect(self._tick)
        self.timer.start(16) # ~60FPS
        
    def set_state(self, state):
        if self.state != state:
            self.state = state
            
    def _tick(self):
        self.phase += 0.05
        
        # Adjust dynamics based on state
        spd_scale = 1.0
        if self.state == "typing":
            spd_scale = 2.2
        elif self.state == "thinking":
            spd_scale = 4.5
        elif self.state == "speaking":
            spd_scale = 1.8
            # Simulate speech audio wave oscillations
            self.volume = 0.4 + 0.6 * abs(math.sin(self.phase * 2.5))
        else:
            self.volume = 0.0
            
        # Rotate rings
        for i, ring in enumerate(self.rings_def):
            self.ring_angles[i] = (self.ring_angles[i] + ring["spd"] * spd_scale) % 360.0
            
        # Animate particles
        for p in self.particles:
            p["angle"] = (p["angle"] + p["speed"] * spd_scale) % 360.0
            
        self.update()
        
    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        
        cx, cy = self.width() / 2, self.height() / 2
        r_base = self.width() * 0.42
        
        # States affect pulsing
        pulse_spd = 0.05
        if self.state == "typing": pulse_spd = 0.12
        elif self.state == "thinking": pulse_spd = 0.25
        elif self.state == "speaking": pulse_spd = 0.08
        
        pulse = math.sin(self.phase * pulse_spd)
        
        # 1. Volumetric Corona Aura
        r_aura = r_base * 1.45 + (pulse * 2.0 if self.state != "speaking" else self.volume * 4.0)
        aura = QRadialGradient(cx, cy, r_aura)
        aura.setColorAt(0.0, QColor(0, 200, 255, 60))
        aura.setColorAt(0.5, QColor(0, 140, 255, 30))
        aura.setColorAt(1.0, QColor(0, 0, 0, 0))
        painter.setPen(Qt.NoPen)
        painter.setBrush(aura)
        painter.drawEllipse(QPointF(cx, cy), r_aura, r_aura)
        
        # 2. Plasma Core
        core_scale = 0.78 + (pulse * 0.04 if self.state != "speaking" else self.volume * 0.12)
        r_core = r_base * core_scale
        core = QRadialGradient(cx, cy, r_core)
        core.setColorAt(0.00, QColor(220, 248, 255, 240))
        core.setColorAt(0.25, QColor(80, 210, 255, 210))
        core.setColorAt(0.60, QColor(0, 150, 255, 140))
        core.setColorAt(0.85, QColor(0, 70, 180, 70))
        core.setColorAt(1.00, QColor(0, 0, 0, 0))
        painter.setBrush(core)
        painter.drawEllipse(QPointF(cx, cy), r_core, r_core)
        
        # 3. Holographic counter-rotating arc-rings
        for ri, ring in enumerate(self.rings_def):
            rr = self.width() * ring["rf"] / 2.0
            rect = QRectF(cx - rr, cy - rr, rr * 2.0, rr * 2.0)
            arc, gap = ring["arc"], ring["gap"]
            rot = self.ring_angles[ri]
            ba = ring["a"]
            
            # Boost alpha during thinking
            if self.state == "thinking":
                ba = min(255, int(ba * 1.5))
                
            ga = int(ba * 0.3)
            
            angle = 0.0
            while angle < 360.0:
                start_deg = (rot + angle) % 360.0
                draw_len = min(arc, 360.0 - angle)
                
                # Glow ring
                gpen = QPen(QColor(ring["r"], ring["g"], ring["b"], ga))
                gpen.setWidthF(ring["w"] * 2.8)
                gpen.setCapStyle(Qt.RoundCap)
                painter.setPen(gpen)
                painter.setBrush(Qt.NoBrush)
                painter.drawArc(rect, int(start_deg * 16), int(draw_len * 16))
                
                # Core ring
                cpen = QPen(QColor(ring["r"], ring["g"], ring["b"], ba))
                cpen.setWidthF(ring["w"])
                cpen.setCapStyle(Qt.RoundCap)
                painter.setPen(cpen)
                painter.drawArc(rect, int(start_deg * 16), int(draw_len * 16))
                
                angle += (arc + gap)
                
        # 4. Particles
        painter.setPen(Qt.NoPen)
        for p in self.particles:
            rad = math.radians(p["angle"])
            dist = r_base * p["dist_pct"]
            px = cx + dist * math.cos(rad)
            py = cy + dist * math.sin(rad)
            
            # Particle core
            painter.setBrush(QColor(0, 220, 255, p["alpha"]))
            painter.drawEllipse(QPointF(px, py), p["size"], p["size"])


class ChatMessageWidget(QWidget):
    def __init__(self, sender, text, time_str="14:30", is_user=False, parent=None):
        super().__init__(parent)
        self.is_user = is_user
        
        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 5, 10, 5)
        layout.setSpacing(12)
        
        # Left side avatar for Helio
        if not is_user:
            avatar_layout = QVBoxLayout()
            avatar_layout.setAlignment(Qt.AlignTop)
            self.avatar = HoloOrbWidget(size=36)
            avatar_layout.addWidget(self.avatar)
            layout.addLayout(avatar_layout)
            
        # Message content bubble
        self.bubble = QFrame()
        self.bubble.setMaximumWidth(450)
        
        bubble_layout = QVBoxLayout(self.bubble)
        bubble_layout.setContentsMargins(15, 12, 15, 12)
        bubble_layout.setSpacing(6)
        
        msg_label = QLabel(text)
        msg_label.setWordWrap(True)
        msg_label.setStyleSheet("color: #E2EEF8; font-family: 'Segoe UI'; font-size: 13px; background: transparent; border: none;")
        msg_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        bubble_layout.addWidget(msg_label)
        
        # Time and Status line
        time_label = QLabel(time_str + (" ✓✓" if is_user else ""))
        time_label.setAlignment(Qt.AlignRight)
        time_label.setStyleSheet("color: #507A9A; font-family: 'Segoe UI'; font-size: 10px; background: transparent; border: none;")
        bubble_layout.addWidget(time_label)
        
        # Styles (glassmorphism/glowing border matching dark theme)
        if is_user:
            self.bubble.setStyleSheet("""
                QFrame {
                    background-color: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 #093356, stop:1 #021a30);
                    border: 1px solid #0a4f85;
                    border-radius: 14px;
                    border-top-right-radius: 2px;
                }
            """)
        else:
            self.bubble.setStyleSheet("""
                QFrame {
                    background-color: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 #06192b, stop:1 #010c17);
                    border: 1px solid #0b2e4c;
                    border-radius: 14px;
                    border-top-left-radius: 2px;
                }
            """)
            
        layout.addWidget(self.bubble)
        
        # Spacing/stretch
        if is_user:
            # Shift bubble to the right
            layout.insertStretch(0, 1)
        else:
            # Shift bubble to the left
            layout.addStretch(1)


class ChatWidget(QWidget):
    message_sent = pyqtSignal(str)
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WA_TranslucentBackground)
        
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(20, 60, 20, 20)
        main_layout.setSpacing(15)
        
        # Scroll area for messages
        self.scroll_area = QScrollArea()
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setStyleSheet("""
            QScrollArea {
                background: transparent;
                border: none;
            }
            QScrollBar:vertical {
                border: none;
                background: rgba(0,0,0,0);
                width: 6px;
                border-radius: 3px;
            }
            QScrollBar::handle:vertical {
                background: rgba(0, 150, 255, 80);
                border-radius: 3px;
            }
        """)
        
        self.scroll_content = QWidget()
        self.scroll_content.setAttribute(Qt.WA_TranslucentBackground)
        self.scroll_layout = QVBoxLayout(self.scroll_content)
        self.scroll_layout.setContentsMargins(10, 10, 10, 10)
        self.scroll_layout.addStretch()
        
        self.scroll_area.setWidget(self.scroll_content)
        main_layout.addWidget(self.scroll_area, 1)
        
        # Input Area
        input_container = QFrame()
        input_container.setStyleSheet("""
            QFrame {
                background-color: #020A12;
                border: 1px solid #003355;
                border-radius: 25px;
            }
        """)
        input_layout = QHBoxLayout(input_container)
        input_layout.setContentsMargins(10, 5, 10, 5)
        input_layout.setSpacing(10)
        
        # Orb Status Indicator (cyan/blue holographic orb next to typing box)
        self.indicator_orb = HoloOrbWidget(size=38)
        input_layout.addWidget(self.indicator_orb)
        
        btn_plus = QPushButton("+")
        btn_plus.setFixedSize(40, 40)
        btn_plus.setStyleSheet("""
            QPushButton {
                background: transparent;
                color: #00AFFF;
                border: 1px solid #003355;
                border-radius: 20px;
                font-size: 20px;
            }
            QPushButton:hover { background: rgba(0, 150, 255, 30); }
        """)
        input_layout.addWidget(btn_plus)
        
        self.line_edit = QLineEdit()
        self.line_edit.setPlaceholderText("Message Helio...")
        self.line_edit.setStyleSheet("""
            QLineEdit {
                background: transparent;
                color: white;
                border: none;
                font-family: 'Segoe UI';
                font-size: 14px;
            }
        """)
        self.line_edit.returnPressed.connect(self._send_clicked)
        self.line_edit.textChanged.connect(self._on_text_changed)
        input_layout.addWidget(self.line_edit, 1)
        
        btn_send = QPushButton("➢")
        btn_send.setFixedSize(40, 40)
        btn_send.setStyleSheet("""
            QPushButton {
                background: transparent;
                color: #00AFFF;
                border: 1px solid #003355;
                border-radius: 20px;
                font-size: 18px;
            }
            QPushButton:hover { background: rgba(0, 150, 255, 30); }
        """)
        btn_send.clicked.connect(self._send_clicked)
        input_layout.addWidget(btn_send)
        
        main_layout.addWidget(input_container)
        
        # Initialize with one greeting from Helio
        self.add_message("Helio", "Hello! I'm Helio, your AI companion. How can I assist you today?", is_user=False)
        
        # Typing tracker timer
        self.typing_timer = QTimer(self)
        self.typing_timer.setSingleShot(True)
        self.typing_timer.timeout.connect(self._on_typing_timeout)
        
    def add_message(self, sender, text, is_user):
        # Determine time of message
        now = datetime.datetime.now()
        time_str = now.strftime("%H:%M")
        
        msg = ChatMessageWidget(sender, text, time_str=time_str, is_user=is_user)
        # Insert before the stretch
        self.scroll_layout.insertWidget(self.scroll_layout.count() - 1, msg)
        
        # Scroll to bottom
        QTimer.singleShot(100, lambda: self.scroll_area.verticalScrollBar().setValue(
            self.scroll_area.verticalScrollBar().maximum()
        ))
        
    def _send_clicked(self):
        text = self.line_edit.text().strip()
        if text:
            self.add_message("You", text, is_user=True)
            self.line_edit.clear()
            self.indicator_orb.set_state("thinking") # Trigger AI processing state
            self.message_sent.emit(text)
            
    def _on_text_changed(self, text):
        if text.strip() and self.indicator_orb.state == "idle":
            self.indicator_orb.set_state("typing")
        self.typing_timer.start(1500)
        
    def _on_typing_timeout(self):
        if self.indicator_orb.state == "typing":
            self.indicator_orb.set_state("idle")
