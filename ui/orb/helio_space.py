"""
helio_space.py — Fullscreen Helio Space overlay.

Architecture: QGraphicsView + QGraphicsScene.

SpaceSunItem renders a highly-detailed, cinematic, volumetric plasma star
that matches the concept perfectly. It avoids flat vector shapes, sharp lines,
and cartoon spikes.

Visual Features:
  - Exact desktop orb core scaled up 2.1x and nested in the center
  - Scaled three segmented arc rings rotating inside the sun
  - Scaled hairline flare arcs shooting from the core
  - Scaled subtle plasma surface blobs and ambient particles
  - Extremely delicate, lighter golden outline around the outer photosphere
  - exactly 3 delicate concentric orbital rings (highly cleaned up)
  - Orbiting particles (orbs) way less in count and much smaller/subtle
  - Deep space star field in background
"""
import math
import random

from PyQt5.QtWidgets import QGraphicsView, QGraphicsScene, QGraphicsObject, QApplication
from PyQt5.QtCore import (Qt, QVariantAnimation, QEasingCurve, QPointF, QPoint,
                          QRectF, QTimer, pyqtSignal)
from PyQt5.QtGui import (QPainter, QRadialGradient, QConicalGradient, QLinearGradient,
                         QColor, QPen, QRegion, QPainterPath, QBrush)

from orb.config import WIN_SIZE, FRAME_TIME
from orb.planets import get_planets
from orb.panels.panel_0 import Panel0
from orb.panels.panel_1 import Panel1
from orb.panels.memory import MemoryPanel, MemoryWidget
from orb.panels.chat import ChatPanel, ChatWidget
from orb.panels.files import FilesPanel
from orb.panels.system import SystemPanel, SystemWidget
from orb.panels.panel_6 import Panel6
from orb.panels.panel_7 import Panel7


# ── scene constants ─────────────────────────────────────────
_SUN_R     = 96.0    # Plasma sphere base radius in scene px
_CORONA_R  = 360.0   # Wide holographic glow radius
_PULSE_SPD = 0.026   # Slightly more alive than the compact desktop orb

_RING_TILT = 0.24    # ry = rx * tilt  (perspective foreshortening)
_PLANE_ROT = -7.0    # degrees: overall ring-plane rotation from horizontal

# Fullscreen orbital rings are disabled; the expanded scene is a clean plasma field.
_RINGS = []

_STAR_COUNT = 1200
_SCALE_FACTOR = 3.0


# ── SpaceSunItem ────────────────────────────────────────────

class SpaceSunItem(QGraphicsObject):
    """Self-animating cinematic sun + orbital ring system."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._half = 980.0
        self.setTransformOriginPoint(0, 0)

        self._t     = random.uniform(0, math.tau)
        self._pulse = 0.0
        self._orbit_t = random.uniform(0, 360)

        # Build ring state with per-particle angles
        self._rings = []
        for rx, base_alpha, width, npart, speed in _RINGS:
            ry = rx * _RING_TILT
            particles = [
                {
                    "angle": i * (360.0 / npart) + random.uniform(-10, 10),
                    "size":  random.uniform(0.9, 1.8), # Very small particles (orbs)
                    "alpha": random.randint(110, 210),
                    "a_dir": random.choice([-1, 1]) * random.uniform(0.4, 1.2),
                }
                for i in range(npart)
            ]
            self._rings.append({
                "rx": rx, "ry": ry,
                "alpha": base_alpha, "width": width,
                "speed": speed, "particles": particles,
            })

        # ── Desktop core component states scaled 2.1x ──
        # 1. Three segmented arc rings
        self._inner_ring_angles = [0.0, 0.0, 0.0]

        # 2. Plasma surface blobs
        _pc = 30
        self._plasma = [
            {
                "angle": i * (360.0 / _pc) + random.uniform(-10, 10),
                "speed": random.uniform(0.05, 0.22) * random.choice([-1, 1]),
                "size":  random.uniform(1.8, 5.5) * _SCALE_FACTOR,
                "alpha": random.randint(35, 115),
                "a_dir": random.choice([-1, 1]) * random.uniform(0.3, 0.9),
                "r_off": random.uniform(-18.0, 12.0),
            }
            for i in range(_pc)
        ]

        # 3. Flare arcs are intentionally disabled for the fullscreen scene.
        self._flares = []

        # 4. Ambient particles
        self._particles = []
        for _ in range(90):
            self._particles.append({
                "angle": random.uniform(0, 360),
                "dist": random.uniform(_SUN_R * 0.28, _SUN_R * 1.18),
                "speed": random.uniform(0.12, 0.95) * random.choice([-1, 1]),
                "size": random.uniform(0.8, 2.8),
                "alpha": random.randint(55, 190),
                "alpha_dir": random.choice([-1, 1]) * random.uniform(1.5, 3.5),
            })

        self._filaments = [
            {
                "a1": random.uniform(0, 360),
                "a2": random.uniform(0, 360),
                "bend": random.uniform(-0.55, 0.55),
                "phase": random.uniform(0, math.tau),
                "speed": random.uniform(0.018, 0.052),
                "alpha": random.randint(80, 170),
                "width": random.uniform(0.42, 1.25),
            }
            for _ in range(42)
        ]

        self._timer = QTimer()
        self._timer.timeout.connect(self._tick)
        self._timer.start(FRAME_TIME)

    def _new_flare(self):
        return {
            "angle":    random.uniform(0, 360),
            "length":   random.uniform(24, 92),
            "cur_len":  0.0,
            "speed":    random.uniform(0.7, 2.2),
            "alpha":    0.0,
            "phase":    "grow",
            "hold_t":   0.0,
            "hold_max": random.uniform(10, 34),
        }

    def _tick(self):
        self._t    += _PULSE_SPD
        self._pulse = math.sin(self._t)
        self._orbit_t = (self._orbit_t + 0.42) % 360

        # Rotate orbital ring particles
        for ring in self._rings:
            for p in ring["particles"]:
                p["angle"] = (p["angle"] + ring["speed"]) % 360
                p["alpha"] += p["a_dir"]
                if p["alpha"] > 225:
                    p["alpha"], p["a_dir"] = 225, -abs(p["a_dir"])
                elif p["alpha"] < 100:
                    p["alpha"], p["a_dir"] = 100,  abs(p["a_dir"])

        # Rotate internal segmented rings (desktop core)
        _ring_defs = [
            dict(speed=0.55),
            dict(speed=-0.86),
            dict(speed=0.34),
        ]
        for i in range(3):
            self._inner_ring_angles[i] = (
                self._inner_ring_angles[i] + _ring_defs[i]["speed"] * 1.0 * (1 / 60) * 60
            ) % 360

        # Drift plasma blobs
        for p in self._plasma:
            p["angle"] = (p["angle"] + p["speed"]) % 360
            p["alpha"] += p["a_dir"]
            if p["alpha"] > 75:
                p["alpha"], p["a_dir"] = 75, -abs(p["a_dir"])
            elif p["alpha"] < 15:
                p["alpha"], p["a_dir"] = 15,  abs(p["a_dir"])

        # Evolve flare arcs
        for f in self._flares:
            if f["phase"] == "grow":
                f["cur_len"] += f["speed"]
                f["alpha"]    = min(140, f["alpha"] + 7)
                if f["cur_len"] >= f["length"]:
                    f["phase"] = "hold"
            elif f["phase"] == "hold":
                f["hold_t"] += 1
                if f["hold_t"] >= f["hold_max"]:
                    f["phase"] = "fade"
            else:
                f["alpha"] -= 4
                if f["alpha"] <= 0:
                    f.update(self._new_flare())

        # Drift ambient particles
        for p in self._particles:
            p["angle"] = (p["angle"] + p["speed"]) % 360
            p["alpha"] += p["alpha_dir"]
            if p["alpha"] > 175:
                p["alpha"], p["alpha_dir"] = 175, -abs(p["alpha_dir"])
            elif p["alpha"] < 30:
                p["alpha"], p["alpha_dir"] =  30,  abs(p["alpha_dir"])

        for f in self._filaments:
            f["phase"] += f["speed"]
            f["a1"] = (f["a1"] + f["speed"] * 8.0) % 360
            f["a2"] = (f["a2"] - f["speed"] * 5.5) % 360

        self.update()

    # ── QGraphicsObject interface ────────────────────────────

    def boundingRect(self):
        h = self._half
        return QRectF(-h, -h, h * 2, h * 2)

    def paint(self, painter, option, widget=None):
        painter.setRenderHint(QPainter.Antialiasing)

        painter.setCompositionMode(QPainter.CompositionMode_Screen)

        self._draw_corona(painter)
        self._draw_plasma_sphere(painter)
        self._draw_plasma_filaments(painter)
        self._draw_hologram_shell(painter)
        self._draw_surface_sparks(painter)
        self._draw_photosphere_outline(painter)

        # Reset composition mode back to normal
        painter.setCompositionMode(QPainter.CompositionMode_SourceOver)

    # ── cinematic layers ────────────────────────────────────

    def _draw_corona(self, painter):
        """Wide volumetric halo around the plasma ball."""
        r = _CORONA_R + self._pulse * 18
        grad = QRadialGradient(QPointF(0, 0), r)
        grad.setColorAt(0.00, QColor(255, 216,  82, 238))
        grad.setColorAt(0.11, QColor(255, 164,   0, 192))
        grad.setColorAt(0.28, QColor(218,  82,   0, 118))
        grad.setColorAt(0.52, QColor(128,  38,   0,  52))
        grad.setColorAt(0.78, QColor( 46,  10,   0,  18))
        grad.setColorAt(1.00, QColor(  0,   0,   0,   0))
        painter.setPen(Qt.NoPen)
        painter.setBrush(grad)
        painter.drawEllipse(QPointF(0, 0), r, r)

    def _draw_lens_flare(self, painter):
        """Thin horizontal/diagonal bloom rays like a bright holographic core."""
        center = QPointF(0, 0)
        lengths = (360, 520, 700)
        for i, length in enumerate(lengths):
            alpha = 95 - i * 24
            pen = QPen(QColor(255, 205, 65, alpha))
            pen.setWidthF(1.8 - i * 0.45)
            pen.setCapStyle(Qt.RoundCap)
            painter.setPen(pen)
            for deg in (0, 18, -18):
                rad = math.radians(deg)
                dx = math.cos(rad) * length
                dy = math.sin(rad) * length * 0.22
                painter.drawLine(QPointF(-dx, -dy), QPointF(dx, dy))

        flare_grad = QRadialGradient(center, 44)
        flare_grad.setColorAt(0.00, QColor(255, 255, 230, 255))
        flare_grad.setColorAt(0.28, QColor(255, 222,  70, 220))
        flare_grad.setColorAt(0.68, QColor(255, 145,  15, 80))
        flare_grad.setColorAt(1.00, QColor(255, 145,  15, 0))
        painter.setPen(Qt.NoPen)
        painter.setBrush(flare_grad)
        painter.drawEllipse(center, 44, 44)

    def _draw_plasma_sphere(self, painter):
        """Layered sphere body with hot core, transparent surface, and molten texture."""
        center = QPointF(0, 0)
        r = _SUN_R + self._pulse * 3.5

        outer = QRadialGradient(center, r * 1.16)
        outer.setColorAt(0.00, QColor(255, 236, 150, 238))
        outer.setColorAt(0.18, QColor(255, 180,  24, 228))
        outer.setColorAt(0.42, QColor(255, 116,   0, 184))
        outer.setColorAt(0.70, QColor(185,  48,   0, 104))
        outer.setColorAt(0.92, QColor(255, 138,  10, 78))
        outer.setColorAt(1.00, QColor(255,  80,   0, 0))
        painter.setPen(Qt.NoPen)
        painter.setBrush(outer)
        painter.drawEllipse(center, r * 1.08, r * 1.08)

        shell = QConicalGradient(center, (self._t * 18) % 360)
        shell.setColorAt(0.00, QColor(255, 178, 32, 34))
        shell.setColorAt(0.16, QColor(255, 112,  0, 118))
        shell.setColorAt(0.34, QColor(255, 205, 82, 58))
        shell.setColorAt(0.52, QColor(180,  50,  0, 96))
        shell.setColorAt(0.72, QColor(255, 146, 18, 126))
        shell.setColorAt(1.00, QColor(255, 178, 32, 34))
        painter.setBrush(shell)
        painter.drawEllipse(center, r * 0.98, r * 0.98)

        for i in range(7):
            angle = self._t * (0.7 + i * 0.09) + i * 0.9
            px = math.cos(angle) * r * (0.16 + i * 0.08)
            py = math.sin(angle * 1.4) * r * (0.11 + i * 0.055)
            blob_r = r * (0.22 - i * 0.014)
            grad = QRadialGradient(QPointF(px, py), blob_r)
            grad.setColorAt(0.00, QColor(255, 190,  70, 90))
            grad.setColorAt(0.45, QColor(255, 104,   0, 52))
            grad.setColorAt(1.00, QColor(180,  42,   0, 0))
            painter.setBrush(grad)
            painter.drawEllipse(QPointF(px, py), blob_r, blob_r)

    def _draw_plasma_filaments(self, painter):
        """Curved lightning-like threads inside the plasma ball."""
        painter.setBrush(Qt.NoBrush)
        radius = _SUN_R * 0.96
        for f in self._filaments:
            shimmer = 0.55 + 0.45 * math.sin(f["phase"])
            a1 = math.radians(f["a1"])
            a2 = math.radians(f["a2"])
            p1 = QPointF(math.cos(a1) * radius * 0.86, math.sin(a1) * radius * 0.86)
            p2 = QPointF(math.cos(a2) * radius * 0.78, math.sin(a2) * radius * 0.78)
            mid_angle = (a1 + a2) * 0.5 + f["bend"]
            control = QPointF(
                math.cos(mid_angle) * radius * (0.12 + shimmer * 0.30),
                math.sin(mid_angle) * radius * (0.12 + shimmer * 0.30),
            )

            path = QPainterPath(p1)
            path.quadTo(control, p2)

            glow = QPen(QColor(255, 104, 0, int(f["alpha"] * 0.28 * shimmer)))
            glow.setWidthF(f["width"] * 5.8)
            glow.setCapStyle(Qt.RoundCap)
            painter.setPen(glow)
            painter.drawPath(path)

            core = QPen(QColor(255, 176, 42, int(f["alpha"] * 0.82 * shimmer)))
            core.setWidthF(f["width"])
            core.setCapStyle(Qt.RoundCap)
            painter.setPen(core)
            painter.drawPath(path)

    def _draw_hologram_shell(self, painter):
        """Latitude/longitude lines wrapped around the glowing ball."""
        painter.setBrush(Qt.NoBrush)
        r = _SUN_R * 1.04

        for i, tilt in enumerate((-0.64, -0.40, -0.20, 0.0, 0.20, 0.40, 0.64)):
            ry = r * math.sqrt(max(0.08, 1.0 - tilt * tilt)) * 0.24
            y = r * tilt
            rect = QRectF(-r, y - ry, r * 2, ry * 2)
            pen = QPen(QColor(255, 150, 18, 52 if i != 3 else 96))
            pen.setWidthF(0.72 if i != 3 else 1.08)
            painter.setPen(pen)
            painter.drawEllipse(rect)

        for deg in range(0, 180, 18):
            painter.save()
            painter.rotate(deg + self._t * 4.0)
            rect = QRectF(-r, -r * 0.24, r * 2, r * 0.48)
            pen = QPen(QColor(230, 88, 0, 40))
            pen.setWidthF(0.48)
            painter.setPen(pen)
            painter.drawEllipse(rect)
            painter.restore()

    def _draw_surface_sparks(self, painter):
        painter.setPen(Qt.NoPen)
        center = QPointF(0, 0)
        for p in self._particles:
            rad = math.radians(p["angle"])
            px = math.cos(rad) * p["dist"]
            py = math.sin(rad) * p["dist"]
            size = p["size"] * (0.75 + 0.45 * math.sin(self._t + p["angle"]))
            alpha = max(0, min(255, int(p["alpha"])))
            pt = QPointF(px, py)

            painter.setBrush(QColor(255, 96, 0, int(alpha * 0.24)))
            painter.drawEllipse(pt, size * 3.6, size * 3.6)
            painter.setBrush(QColor(255, 165, 38, int(alpha * 0.82)))
            painter.drawEllipse(pt, size, size)

        hot = QRadialGradient(center, _SUN_R * 0.48)
        hot.setColorAt(0.00, QColor(255, 226, 140, 228))
        hot.setColorAt(0.22, QColor(255, 166,  28, 192))
        hot.setColorAt(0.68, QColor(255,  86,   0, 68))
        hot.setColorAt(1.00, QColor(255,  86,   0, 0))
        painter.setBrush(hot)
        painter.drawEllipse(center, _SUN_R * 0.50, _SUN_R * 0.50)

    def _draw_desktop_aura(self, painter, c):
        """Blends the three aura layers from desktop HoloOverlay, scaled up 2.1x."""
        # 1. Outer soft deep corona fog
        r_out = (42.0 + self._pulse * 5) * _SCALE_FACTOR
        grad  = QRadialGradient(c, r_out)
        grad.setColorAt(0.00, QColor(180, 80,  0,   100))
        grad.setColorAt(0.40, QColor(120, 40,  0,   50))
        grad.setColorAt(0.75, QColor(60,  15,  0,   18))
        grad.setColorAt(1.00, QColor(0,   0,   0,   0))
        painter.setPen(Qt.NoPen)
        painter.setBrush(grad)
        painter.drawEllipse(c, r_out, r_out)

        # 2. Inner gold energy halo bloom
        r_in  = (26.0 + self._pulse * 7) * _SCALE_FACTOR
        grad2 = QRadialGradient(c, r_in)
        grad2.setColorAt(0.00, QColor(255, 200, 40,  170))
        grad2.setColorAt(0.45, QColor(255, 160, 0,   95))
        grad2.setColorAt(0.80, QColor(220, 100, 0,   35))
        grad2.setColorAt(1.00, QColor(0,   0,   0,   0))
        painter.setBrush(grad2)
        painter.drawEllipse(c, r_in, r_in)

        # 3. Core photosphere
        r_core = (16.0 + self._pulse * 1.5) * _SCALE_FACTOR
        grad3 = QRadialGradient(c, r_core)
        grad3.setColorAt(0.00, QColor(255, 255, 220, 255))
        grad3.setColorAt(0.35, QColor(255, 210, 60,  220))
        grad3.setColorAt(0.70, QColor(255, 130, 0,   130))
        grad3.setColorAt(1.00, QColor(0,   0,   0,   0))
        painter.setBrush(grad3)
        painter.drawEllipse(c, r_core, r_core)

    def _draw_desktop_rings(self, painter, c):
        """Paints the three segmented arc-rings from desktop scaled 2.1x."""
        _ring_defs = [
            dict(radius=30, width=1.0, r=255, g=200, b=40, base_alpha=150, arc_len=60, gap=20),
            dict(radius=34, width=0.8, r=255, g=130, b=0,  base_alpha=100, arc_len=80, gap=30),
            dict(radius=39, width=0.6, r=210, g=60,  b=0,  base_alpha=55,  arc_len=120, gap=40),
        ]

        for i, rdef in enumerate(_ring_defs):
            base_alpha = rdef["base_alpha"]
            glow_alpha = min(80, int(base_alpha * 0.35))
            rot        = self._inner_ring_angles[i]
            r          = rdef["radius"] * _SCALE_FACTOR
            arc_len    = rdef["arc_len"]
            gap        = rdef["gap"]
            cycle      = arc_len + gap
            rect       = QRectF(c.x() - r, c.y() - r, r * 2, r * 2)

            angle = 0.0
            while angle < 360.0:
                start_deg = (rot + angle) % 360.0
                draw_len  = min(arc_len, 360.0 - angle)

                # Glow pass
                gpen = QPen(QColor(rdef["r"], rdef["g"], rdef["b"], glow_alpha))
                gpen.setWidthF(rdef["width"] * _SCALE_FACTOR * 3.0)
                gpen.setCapStyle(Qt.RoundCap)
                painter.setPen(gpen)
                painter.setBrush(Qt.NoBrush)
                painter.drawArc(rect, int(start_deg * 16), int(draw_len * 16))

                # Crisp pass
                cpen = QPen(QColor(rdef["r"], rdef["g"], rdef["b"], base_alpha))
                cpen.setWidthF(rdef["width"] * _SCALE_FACTOR)
                cpen.setCapStyle(Qt.RoundCap)
                painter.setPen(cpen)
                painter.drawArc(rect, int(start_deg * 16), int(draw_len * 16))

                angle += cycle

    def _draw_desktop_particles(self, painter, c):
        """Paints floating particles scaled 2.1x."""
        painter.setPen(Qt.NoPen)
        for p in self._particles:
            rad = math.radians(p["angle"])
            px  = c.x() + p["dist"] * math.cos(rad)
            py  = c.y() + p["dist"] * math.sin(rad)
            pt  = QPointF(px, py)
            sz  = p["size"]
            a   = int(p["alpha"])

            # Outer amber glow
            painter.setBrush(QColor(255, 140, 0, int(a * 0.35)))
            painter.drawEllipse(pt, sz * 2.2, sz * 2.2)

            # Inner golden core
            painter.setBrush(QColor(255, 230, 100, a))
            painter.drawEllipse(pt, sz, sz)

    def _draw_desktop_plasma_surface(self, painter, c):
        """Subtle plasma blobs scaled 2.1x."""
        _LIMB_R = 18.0 * _SCALE_FACTOR
        painter.setPen(Qt.NoPen)
        for p in self._plasma:
            rad   = math.radians(p["angle"])
            r_pos = _LIMB_R + p["r_off"]
            px    = c.x() + r_pos * math.cos(rad)
            py    = c.y() + r_pos * math.sin(rad)
            pt    = QPointF(px, py)
            sz    = p["size"]
            a     = int(p["alpha"])
            painter.setBrush(QColor(255, 160, 20, max(0, int(a * 0.5))))
            painter.drawEllipse(pt, sz * 1.4, sz * 1.4)
            painter.setBrush(QColor(255, 220, 80, a))
            painter.drawEllipse(pt, sz * 0.55, sz * 0.55)

    def _draw_desktop_flare_arcs(self, painter, c):
        """Hairline flare spikes around the plasma limb."""
        _LIMB_R = _SUN_R * 0.92
        for f in self._flares:
            a = int(f["alpha"])
            if a <= 0:
                continue
            a = min(140, a)
            rad = math.radians(f["angle"])
            x0  = c.x() + _LIMB_R * math.cos(rad)
            y0  = c.y() + _LIMB_R * math.sin(rad)
            x1  = c.x() + (_LIMB_R + f["cur_len"]) * math.cos(rad)
            y1  = c.y() + (_LIMB_R + f["cur_len"]) * math.sin(rad)
            
            gpen = QPen(QColor(255, 150, 20, int(a * 0.38)))
            gpen.setWidthF(4.8)
            gpen.setCapStyle(Qt.RoundCap)
            painter.setPen(gpen)
            painter.drawLine(QPointF(x0, y0), QPointF(x1, y1))
            
            cpen = QPen(QColor(255, 242, 145, a))
            cpen.setWidthF(1.15)
            cpen.setCapStyle(Qt.RoundCap)
            painter.setPen(cpen)
            painter.drawLine(QPointF(x0, y0), QPointF(x1, y1))

    def _draw_photosphere_outline(self, painter):
        """Draws an extremely delicate, lighter golden circular outline around the outer boundary."""
        r_sun = _SUN_R + self._pulse * 3.0
        
        glow_pen = QPen(QColor(255, 116, 0, 76))
        glow_pen.setWidthF(4.6)
        painter.setPen(glow_pen)
        painter.setBrush(Qt.NoBrush)
        painter.drawEllipse(QPointF(0, 0), r_sun, r_sun)

        outline_pen = QPen(QColor(255, 172, 36, 162))
        outline_pen.setWidthF(0.95)
        painter.setPen(outline_pen)
        painter.drawEllipse(QPointF(0, 0), r_sun, r_sun)

    def _draw_orbital_rings(self, painter):
        """Concentric elliptical rings in a shared tilted orbital plane."""
        painter.save()
        painter.rotate(_PLANE_ROT)   # tilt the whole ring plane

        for ring in self._rings:
            rx, ry = ring["rx"], ring["ry"]
            a  = ring["alpha"]
            rect = QRectF(-rx, -ry, rx * 2, ry * 2)

            # Glow pass
            gpen = QPen(QColor(255, 104, 0, int(a * 0.36)))
            gpen.setWidthF(ring["width"] * 5.8)
            painter.setPen(gpen)
            painter.setBrush(Qt.NoBrush)
            painter.drawEllipse(rect)

            # Crisp ring line
            cpen = QPen(QColor(255, 138, 18, a))
            cpen.setWidthF(ring["width"])
            painter.setPen(cpen)
            painter.drawEllipse(rect)

        painter.restore()


# ── HelioSpaceOverlay ───────────────────────────────────────

class HelioSpaceOverlay(QGraphicsView):
    collapsed = pyqtSignal()

    def __init__(self, main_window):
        super().__init__()
        self.main_window = main_window

        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setStyleSheet("background: transparent; border: none;")
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setRenderHint(QPainter.Antialiasing)
        self.setMouseTracking(True)  # Enable tracking to detect real-time hover

        self.scene = QGraphicsScene(self)
        self.setScene(self.scene)

        self.sun_item = SpaceSunItem()
        self.scene.addItem(self.sun_item)

        # Pre-generate star field (normalized -1..1 coords)
        self._stars = [
            (
                random.uniform(-0.98, 0.98),
                random.uniform(-0.98, 0.98),
                random.uniform(0.18, 1.05),
                random.randint(18, 104),
            )
            for _ in range(_STAR_COUNT)
        ]

        # Load background image dynamically
        import os
        from PyQt5.QtGui import QImage
        ui_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        bg_path = os.path.join(ui_dir, "img", "BG.png")
        self._bg_image = QImage(bg_path)

        self.progress      = 0.0
        self.is_closing    = False
        self.start_center  = QPointF(0, 0)
        self.target_center = QPointF(0, 0)

        # Background Rings Configuration
        self._bg_rings = [
            {"cx": 0.50, "cy": 0.50, "rx": 117.0, "ry": 50.3, "rot": 0.0, "width": 2, "speed": 1.5, "count": 4},
            {"cx": 0.50, "cy": 0.50, "rx": 156.0, "ry": 67.1, "rot": 0.0, "width": 2, "speed": 1.2, "count": 5},
            {"cx": 0.50, "cy": 0.50, "rx": 221.0, "ry": 99.5, "rot": 0.0, "width": 2, "speed": 0.9, "count": 6},
            {"cx": 0.50, "cy": 0.50, "rx": 286.0, "ry": 134.4, "rot": 0.0, "width": 2, "speed": 0.6, "count": 8},
            {"cx": 0.50, "cy": 0.50, "rx": 357.0, "ry": 160.7, "rot": 0.0, "width": 2, "speed": 0.4, "count": 9},
        ]
        self.ring_phase = 0.0

        # Dynamic hover scaling state (eases like desktop version)
        self._hover_scale = 1.0
        self._hover_scale_target = 1.0
        
        self.planet_hover_scales = [1.0] * 8
        self._hovered_planet_index = -1
        
        self._mouse_offset = QPointF(0, 0)
        self._target_mouse_offset = QPointF(0, 0)

        # Click effects state lists
        self._ripples = []
        self._burst_particles = []

        self.anim = QVariantAnimation()
        self.anim.setDuration(900)
        self.anim.setEasingCurve(QEasingCurve.InOutQuint)
        self.anim.valueChanged.connect(self._on_anim_step)
        self.anim.finished.connect(self._on_anim_finished)

        # Panel Transition state
        self.panel_mode = False
        self.panel_progress = 0.0
        
        # Carousel state for multiple planets
        self.active_planet_index = 3 # Center planet is initially active
        self.carousel_rotations = 0  # Tracks full loops for the epic wrap-around animation
        self.planet_angles = [90.0 + (3 - i) * 45.0 for i in range(8)] # Current animated angles
        self.planet_rects = []

        # Planet & panel renderers — edit orb/planets.py and orb/panels.py to customise each
        self._planet_renderers = get_planets()
        self._panel_renderers  = [
            Panel0("PLANET 0", QColor(255, 140, 40)),
            Panel1("PLANET 1", QColor(255, 140, 40)),
            MemoryPanel(),
            ChatPanel(),
            FilesPanel(),
            SystemPanel(),
            Panel6("PLANET 6", QColor(255, 140, 40)),
            Panel7("PLANET 7", QColor(255, 140, 40)),
        ]
        
        self.panel_anim = QVariantAnimation()
        self.panel_anim.setDuration(700)
        self.panel_anim.setEasingCurve(QEasingCurve.InOutQuint)
        self.panel_anim.valueChanged.connect(self._on_panel_anim_step)
        
        # Navigation state
        self.setFocusPolicy(Qt.StrongFocus)
        self._last_wheel_time = 0
        self._is_shifting = False

        # Memory UI Integration (Planet 2)
        self.memory_widget = MemoryWidget()
        self.memory_proxy = self.scene.addWidget(self.memory_widget)
        self.memory_proxy.setZValue(100)
        self.memory_proxy.hide()

        # Chat UI Integration (Planet 3)
        self.chat_widget = ChatWidget()
        # Connect ChatWidget's message_sent signal to the AgentBridge in MainWindow
        self.chat_widget.message_sent.connect(self._on_chat_message_sent)
        
        self.chat_proxy = self.scene.addWidget(self.chat_widget)
        self.chat_proxy.setZValue(100) # Draw on top of panels
        self.chat_proxy.hide()

        # System UI Integration (Planet 5)
        self.system_widget = SystemWidget()
        self.system_proxy = self.scene.addWidget(self.system_widget)
        self.system_proxy.setZValue(100)
        self.system_proxy.hide()

        # Timer for animating click effects at 60 FPS
        self._effect_timer = QTimer(self)
        self._effect_timer.timeout.connect(self._tick_click_effects)
        self._effect_timer.start(FRAME_TIME)

    def _tick_click_effects(self):
        # Smoothly ease the hover visual expansion scale factor
        self._hover_scale += (self._hover_scale_target - self._hover_scale) * 0.1
        
        # Smoothly ease parallax mouse offset
        self._mouse_offset.setX(self._mouse_offset.x() + (self._target_mouse_offset.x() - self._mouse_offset.x()) * 0.05)
        self._mouse_offset.setY(self._mouse_offset.y() + (self._target_mouse_offset.y() - self._mouse_offset.y()) * 0.05)
        
        # Base spacing exactly 45 degrees so 8 planets cover the entire 360-degree ring
        spacing = 45.0
        for i in range(8):
            target_angle = 90.0 + (self.active_planet_index - i) * spacing + self.carousel_rotations * 360.0
            self.planet_angles[i] += (target_angle - self.planet_angles[i]) * 0.1
            
            # Hover scale for planets
            target_scale = 1.2 if (i == getattr(self, "_hovered_planet_index", -1) and self.progress > 0.95 and self.panel_progress < 0.1) else 1.0
            self.planet_hover_scales[i] += (target_scale - self.planet_hover_scales[i]) * 0.15
            
        self._apply_progress(self.progress)

        # Update ripples
        active_ripples = []
        for r in self._ripples:
            r["radius"] += r["expansion_speed"]
            r["alpha"] -= r["fade_speed"]
            if r["alpha"] > 0 and r["radius"] < r["max_radius"]:
                active_ripples.append(r)
        self._ripples = active_ripples

        # Update burst particles
        active_burst = []
        for p in self._burst_particles:
            rad = math.radians(p["angle"])
            p["pos"] = QPointF(
                p["pos"].x() + p["speed"] * math.cos(rad),
                p["pos"].y() + p["speed"] * math.sin(rad)
            )
            p["speed"] *= p["drag"]
            p["alpha"] -= p["fade_speed"]
            if p["alpha"] > 0:
                active_burst.append(p)
        self._burst_particles = active_burst

        self.ring_phase += 1.0

        if self._ripples or self._burst_particles or self.progress > 0:
            self.viewport().update()

    # ── background (star field + deep space) ────────────────

    def drawBackground(self, painter, rect):
        if self.progress <= 0:
            return

        painter.save()
        scene_rect = self.scene.sceneRect()

        # 1. Base dark background (fades the desktop to theater mode)
        painter.fillRect(scene_rect, QColor(10, 5, 2, int(180 * self.progress)))

        # 2. Draw the user's custom background photo with robust Cover aspect scaling
        if hasattr(self, "_bg_image") and not self._bg_image.isNull():
            # Robust, premium opacity so your custom background is beautifully clear and visible
            painter.setOpacity(self.progress * 0.82)
            img_w = self._bg_image.width()
            img_h = self._bg_image.height()
            view_w = scene_rect.width()
            view_h = scene_rect.height()

            # Aspect Ratio Cover scaling calculation to prevent stretching
            scale_x = view_w / img_w
            scale_y = view_h / img_h
            scale = max(scale_x, scale_y)

            w = img_w * scale
            h = img_h * scale
            x = scene_rect.left() + (view_w - w) / 2.0
            y = scene_rect.top()  + (view_h - h) / 2.0

            # Far background parallax
            px = self._mouse_offset.x() * 15.0 * self.progress
            py = self._mouse_offset.y() * 15.0 * self.progress
            painter.drawImage(QRectF(x + px, y + py, w, h), self._bg_image)

        # Reset painter opacity to full for the radial vignette bloom
        painter.setOpacity(1.0)

        # 3. Cinematic warm solar radial gradient overlay (vignettes the edges, blooms the center)
        grad = QRadialGradient(scene_rect.center(), scene_rect.width() * 0.68)
        grad.setColorAt(0.00, QColor( 255, 120,   0, int(75 * self.progress)))
        grad.setColorAt(0.24, QColor(  44,  16,   2, int(45 * self.progress)))
        grad.setColorAt(0.68, QColor(   8,   2,   0, int(120 * self.progress)))
        grad.setColorAt(1.00, QColor(   0,   0,   0, int(215 * self.progress)))
        painter.fillRect(scene_rect, grad)

        # 3.5 Orbital Rings with Orbiting Particles
        painter.save()
        painter.setRenderHint(QPainter.Antialiasing)
        
        # Dynamic scale factor based on screen width to ensure they are majestic and large
        min_ring_scale = 0.5
        current_ring_scale = min_ring_scale + (2.4 - min_ring_scale) * self.progress
        base_ring_scale = (scene_rect.width() / 1920.0) * current_ring_scale
        
        # Shrink rings slightly horizontally (12%) to clear panel, but more vertically (32%) to prevent top/bottom edge clipping
        ring_scale_x = base_ring_scale * (1.0 - 0.12 * self.panel_progress)
        ring_scale_y = base_ring_scale * (1.0 - 0.32 * self.panel_progress)
        
        alpha = int(255 * self.progress)
        if alpha > 0:
            for i, ring in enumerate(self._bg_rings):
                # Subtle angular drift over time (independent drifting orbit without rotating shape)
                drift_x = math.cos(self.ring_phase * 0.003 * ring["speed"] + i) * 8.0 * self.progress
                drift_y = math.sin(self.ring_phase * 0.004 * ring["speed"] + i) * 5.0 * self.progress
                
                # Midground parallax
                px = self._mouse_offset.x() * -12.0 * self.progress
                py = self._mouse_offset.y() * -12.0 * self.progress
                
                # Shift rings up when panel opens (0.50 -> 0.275)
                ring_cy_offset = -0.225 * self.panel_progress
                
                cx = scene_rect.left() + scene_rect.width() * ring["cx"] + drift_x + px
                cy = scene_rect.top() + scene_rect.height() * (ring["cy"] + ring_cy_offset) + drift_y + py
                
                rx = ring["rx"] * ring_scale_x
                ry = ring["ry"] * ring_scale_y
                rot = ring["rot"]
                
                painter.save()
                painter.translate(cx, cy)
                painter.rotate(rot)
                
                # ── Draw the Ring with 3D Depth Perspective ──
                # Qt drawArc: angle 0=right, 90=TOP (back), 270=BOTTOM (front)
                # So depth = -sin(angle): front (270°) gets depth=1, back (90°) gets depth=0
                painter.setBrush(Qt.NoBrush)
                seg_count = 72
                for seg in range(seg_count):
                    seg_angle = (seg / seg_count) * 360.0
                    # Qt's drawArc uses counter-clockwise angles where 90=TOP and 270=BOTTOM.
                    # We must INVERT the sine value so that 270 (bottom/front) gets depth=1.0 and 90 (top/back) gets depth=0.0.
                    depth = (-math.sin(math.radians(seg_angle)) + 1.0) / 2.0  # 0.0=back(top) 1.0=front(bottom)
                    seg_alpha = int(alpha * (0.18 + 0.60 * depth))
                    seg_w     = ring["width"] * self.progress * (0.5 + 1.5 * depth)
                    if seg_alpha <= 0:
                        continue
                    g_col = int(100 + 40 * depth)
                    pen = QPen(QColor(255, g_col, 10, seg_alpha))
                    pen.setWidthF(seg_w)
                    pen.setCapStyle(Qt.RoundCap)
                    painter.setPen(pen)
                    painter.drawArc(QRectF(-rx, -ry, rx*2, ry*2),
                                    int(seg_angle * 16),
                                    int((360.0 / seg_count) * 16))

                # ── Draw Orbiting Particles ──
                t = math.radians(self.ring_phase * ring["speed"])
                num_particles = ring.get("count", 5)
                for j in range(num_particles):
                    offset = j * (360.0 / num_particles) + (i * 20)
                    pt = t + math.radians(offset)
                    px = rx * math.cos(pt)
                    py = ry * math.sin(pt)

                    # Depth: Qt sin convention — BOTTOM of ellipse (front) is at pt≈-π/2
                    # sin(pt) = -1 at bottom → depth_p should = 1.0 at bottom
                    depth_p    = (-math.sin(pt) + 1.0) / 2.0
                    blink_phase = self.ring_phase * 0.12 + j * 1.5 + i
                    blink       = (math.sin(blink_phase) + 1.0) / 2.0
                    blink_alpha = int(alpha * (0.15 + 0.65 * depth_p) * (0.15 + 0.85 * blink))
                    particle_sz = self.progress * (1.2 + 1.6 * depth_p)

                    painter.setPen(Qt.NoPen)
                    painter.setBrush(QColor(255, 160, 60, int(blink_alpha * 0.7)))
                    painter.drawEllipse(QPointF(px, py), particle_sz * 2.0, particle_sz * 2.0)
                    painter.setBrush(QColor(255, 240, 200, blink_alpha))
                    painter.drawEllipse(QPointF(px, py), particle_sz, particle_sz)

                    
                painter.restore()
        
        painter.restore()

        # 4. Star field
        painter.setPen(Qt.NoPen)
        w = scene_rect.width()
        h = scene_rect.height()
        for sx, sy, sz, sa in self._stars:
            # Deep background parallax mapped to star size (depth)
            px = self._mouse_offset.x() * (45.0 * sz) * self.progress
            py = self._mouse_offset.y() * (45.0 * sz) * self.progress
            x = scene_rect.left() + (sx + 1.0) * 0.5 * w + px
            y = scene_rect.top()  + (sy + 1.0) * 0.5 * h + py
            a = int(sa * self.progress)
            if a <= 0:
                continue
            painter.setBrush(QColor(255, 116, 12, int(a * 0.20)))
            painter.drawEllipse(QPointF(x, y), sz * 2.0, sz * 2.0)
            painter.setBrush(QColor(255, 152, 28, a))
            painter.drawEllipse(QPointF(x, y), sz, sz)

        painter.restore()

    # ── foreground (visual overlays like click ripples/bursts) ──

    def drawForeground(self, painter, rect):
        painter.setRenderHint(QPainter.Antialiasing)
        
        # Holographic Screen Blending Mode for click effects
        painter.setCompositionMode(QPainter.CompositionMode_Screen)

        # Draw ripples
        for r in self._ripples:
            alpha = int(r["alpha"])
            if alpha <= 0:
                continue
            alpha = max(0, min(255, alpha))

            # Outer amber glow ripple ring
            glow_pen = QPen(QColor(255, 130, 0, int(alpha * 0.35)))
            glow_pen.setWidthF(3.5)
            painter.setPen(glow_pen)
            painter.setBrush(Qt.NoBrush)
            painter.drawEllipse(r["pos"], r["radius"], r["radius"])

            # Inner solar gold ripple ring
            core_pen = QPen(QColor(255, 230, 100, alpha))
            core_pen.setWidthF(1.2)
            painter.setPen(core_pen)
            painter.drawEllipse(r["pos"], r["radius"], r["radius"])

        # Draw burst particles
        painter.setPen(Qt.NoPen)
        for p in self._burst_particles:
            alpha = int(p["alpha"])
            if alpha <= 0:
                continue
            alpha = max(0, min(255, alpha))
            sz = p["size"]

            # Solar flare burst halo
            painter.setBrush(QColor(255, 140, 0, int(alpha * 0.42)))
            painter.drawEllipse(p["pos"], sz * 2.2, sz * 2.2)

            # Hot white-gold core
            painter.setBrush(QColor(255, 245, 180, alpha))
            painter.drawEllipse(p["pos"], sz, sz)

        painter.setCompositionMode(QPainter.CompositionMode_SourceOver)

        # ── Draw Planet Morphing into UI Panel ──
        if self.progress > 0.5:
            # We only show it when space is mostly open
            alpha = int(min(1.0, (self.progress - 0.5) * 2.0) * 255)
            
            scene_rect = self.scene.sceneRect()
            outer_ring = self._bg_rings[4]
            
            # Current ring center (incorporating panel_progress shift)
            base_cy = outer_ring["cy"]
            panel_cy_target = base_cy - 0.225  # matching ring_cy_offset
            current_cy = base_cy + (panel_cy_target - base_cy) * self.panel_progress
            
            cx = scene_rect.left() + scene_rect.width() * outer_ring["cx"]
            cy = scene_rect.top() + scene_rect.height() * current_cy
            
            # Current ring scale
            min_ring_scale = 0.5
            current_ring_scale = min_ring_scale + (2.4 - min_ring_scale) * self.progress
            base_ring_scale = (scene_rect.width() / 1920.0) * current_ring_scale
            ring_scale_x = base_ring_scale * (1.0 - 0.12 * self.panel_progress)
            ring_scale_y = base_ring_scale * (1.0 - 0.32 * self.panel_progress)
            
            rx = outer_ring["rx"] * ring_scale_x
            ry = outer_ring["ry"] * ring_scale_y
            
            # Planet rendering
            # Pre-populate rect list to maintain index mapping for clicks
            self.planet_rects = [None] * 8
            
            # Helper to draw a single planet/panel
            def draw_entity(i):
                theta = math.radians(self.planet_angles[i])
                planet_x = cx + rx * math.cos(theta)
                planet_y = cy + ry * math.sin(theta)

                hover_scale = getattr(self, "planet_hover_scales", [1.0] * 8)[i]
                # Compute hover_prog from raw hover_scale BEFORE depth is applied
                # so that far (depth-scaled) planets still show hover animations
                raw_hover_prog = max(0.0, min(1.0, (hover_scale - 1.0) / 0.2))

                planet_w = 90.0 * hover_scale
                planet_h = 90.0 * hover_scale
                planet_radius = 45.0 * hover_scale

                current_x = planet_x
                current_y = planet_y
                current_w = planet_w
                current_h = planet_h
                current_rad = planet_radius

                is_active = (i == self.active_planet_index)

                # If this is the active planet, interpolate it into the panel
                if is_active:
                    panel_w = 860.0
                    panel_h = 650.0
                    panel_x = scene_rect.center().x()
                    panel_y = scene_rect.center().y() + 175.0
                    panel_radius = 20.0

                    current_x   = planet_x + (panel_x - planet_x) * self.panel_progress
                    current_y   = planet_y + (panel_y - planet_y) * self.panel_progress
                    current_w   = planet_w + (panel_w - planet_w) * self.panel_progress
                    current_h   = planet_h + (panel_h - planet_h) * self.panel_progress
                    current_rad = planet_radius + (panel_radius - planet_radius) * self.panel_progress

                shape_rect = QRectF(
                    current_x - current_w/2, current_y - current_h/2,
                    current_w, current_h
                )

                if self.progress > 0.95:
                    self.planet_rects[i] = shape_rect

                inner_opacity = (1.0 - self.panel_progress) if is_active else 1.0
                is_hovered = (i == getattr(self, "_hovered_planet_index", -1))

                # ── 3D Depth: planets in front of the ring are larger + brighter ──
                # In screen coords with Qt ellipse: sin(theta) = +1 = bottom (front of ring, closest to viewer)
                # sin(theta) = -1 = top (back of ring)
                if not is_active:
                    depth_factor = (math.sin(theta) + 1.0) / 2.0  # 0.0=back(top) 1.0=front(bottom)
                    depth_scale  = 0.82 + 0.18 * depth_factor       # subtle: back=82%, front=100%
                    depth_alpha  = int(alpha * (0.55 + 0.45 * depth_factor))  # back=55%, front=100%
                    current_w   *= depth_scale
                    current_h   *= depth_scale
                    current_rad *= depth_scale
                    shape_rect = QRectF(
                        current_x - current_w/2, current_y - current_h/2,
                        current_w, current_h
                    )
                    if self.progress > 0.95:
                        self.planet_rects[i] = shape_rect
                else:
                    depth_alpha = alpha

                # ── Planet orb ── delegate to planets.py renderer
                self._planet_renderers[i].draw(
                    painter, i,
                    current_x, current_y, current_w, current_h, current_rad,
                    depth_alpha, is_hovered, inner_opacity, self.panel_progress,
                    is_active, self.ring_phase, raw_hover_prog
                )

                # ── Panel content ── static renderer always draws (header stays visible)
                if is_active and self.panel_progress > 0.05:
                    self._panel_renderers[i].draw(
                        painter, shape_rect, self.panel_progress, alpha
                    )

                if is_active and self.panel_progress > 0.05:
                    # Memory panel overlay (index 2)
                    if i == 2:
                        if self.panel_progress >= 0.99:
                            if not self.memory_proxy.isVisible():
                                self.memory_proxy.show()
                                self.memory_widget._refresh()
                            mem_rect = shape_rect.adjusted(0, 50, 0, 0)
                            self.memory_proxy.setGeometry(mem_rect)
                            self.memory_proxy.setOpacity(alpha / 255.0)
                        else:
                            self.memory_proxy.hide()

                    # If this is the Chat panel (index 3), update the real QWidget overlay
                    if i == 3:
                        if self.panel_progress >= 0.99:
                            if not self.chat_proxy.isVisible():
                                self.chat_proxy.show()
                            # Use adjusted rect to match the message area background
                            msg_rect = shape_rect.adjusted(0, 50, 0, 0)
                            self.chat_proxy.setGeometry(msg_rect)
                            self.chat_proxy.setOpacity(alpha / 255.0)
                        else:
                            self.chat_proxy.hide()
                    # If this is the System panel (index 5)
                    if i == 5:
                        if self.panel_progress >= 0.99:
                            if not self.system_proxy.isVisible():
                                self.system_proxy.show()
                            sys_rect = shape_rect.adjusted(0, 50, 0, 0)
                            self.system_proxy.setGeometry(sys_rect)
                            self.system_proxy.setOpacity(alpha / 255.0)
                        else:
                            self.system_proxy.hide()
                elif is_active and i == 2:
                    self.memory_proxy.hide()
                elif is_active and i == 3:
                    self.chat_proxy.hide()
                elif is_active and i == 5:
                    self.system_proxy.hide()

            # 1. Draw all inactive planets (they stay in the background)
            for i in range(8):
                if i != self.active_planet_index:
                    draw_entity(i)

            # 2. Draw the active panel LAST so it perfectly overlaps anything behind it
            draw_entity(self.active_planet_index)


    # ── public API ──────────────────────────────────────────

    def open_space(self):
        if self.isVisible() and not self.is_closing:
            return

        self.is_closing = False

        screen_rect = QApplication.desktop().screenGeometry()
        self.setGeometry(screen_rect)
        self.scene.setSceneRect(
            -screen_rect.width()  / 2,
            -screen_rect.height() / 2,
             screen_rect.width(),
             screen_rect.height(),
         )
        self.centerOn(0, 0)

        orb_c = QPointF(self.main_window.geometry().center())
        self.start_center = self.mapToScene(self.mapFromGlobal(orb_c.toPoint()))
        
        # Align sun perfectly with the geometric center of the rings (Ring 1)
        bg_cx = screen_rect.width() * (0.50 - 0.5)
        bg_cy = screen_rect.height() * (0.50 - 0.5)
        self.target_center = QPointF(bg_cx, bg_cy)

        self._apply_progress(0.0)
        self.show()
        self.main_window.hide()

        self.anim.setStartValue(0.0)
        self.anim.setEndValue(1.0)
        self.anim.start()

    def close_space(self):
        if not self.isVisible() or self.is_closing:
            return
        self.is_closing = True
        
        # Close panel first if open, then close UI
        if self.panel_mode:
            self.toggle_panel()
            QTimer.singleShot(700, self._start_close_space_anim)
        else:
            self._start_close_space_anim()

    def _start_close_space_anim(self):
        self.panel_mode = False
        self.panel_progress = 0.0
        self.anim.setStartValue(self.progress)
        self.anim.setEndValue(0.0)
        self.anim.start()

    def toggle_panel(self):
        # Clear hover states explicitly to prevent phantom visual states locking until mouse movement
        self._hovered_planet_index = -1
        self.setCursor(Qt.ArrowCursor)
        
        self.panel_mode = not self.panel_mode
        self.panel_anim.setStartValue(self.panel_progress)
        self.panel_anim.setEndValue(1.0 if self.panel_mode else 0.0)
        self.panel_anim.start()
        
        # Hide chat proxy immediately when closing starts
        if not self.panel_mode:
            self.chat_proxy.hide()
            self.memory_proxy.hide()

    def _on_panel_anim_step(self, val):
        self.panel_progress = val
        self._apply_progress(self.progress)

    # ── internal ────────────────────────────────────────────

    def _apply_progress(self, val):
        self.progress = val

        cx = self.start_center.x() + (self.target_center.x() - self.start_center.x()) * val
        cy = self.start_center.y() + (self.target_center.y() - self.start_center.y()) * val
        
        # When panel opens, shift sun up and scale it down
        # Move up by 22.5% of the screen height (approx 243px on 1080p)
        screen_h = QApplication.desktop().screenGeometry().height()
        panel_shift_y = -(screen_h * 0.225) * self.panel_progress
        cy += panel_shift_y
        
        # Foreground parallax for the sun (moves inversely to mouse)
        px = self._mouse_offset.x() * -40.0 * val
        py = self._mouse_offset.y() * -40.0 * val
        
        self.sun_item.setPos(cx + px, cy + py)

        min_scale = (WIN_SIZE * 0.48) / (_CORONA_R + 20)
        scale = min_scale + (1.0 - min_scale) * val
        
        # Shrink sun slightly when panel opens
        scale *= (1.0 - 0.3 * self.panel_progress)
        
        # Apply hover visual scale factor dynamically
        self.sun_item.setScale(scale * self._hover_scale)
        self.sun_item.setOpacity(min(1.0, val * 1.8))

        self.scene.invalidate(self.scene.sceneRect(), QGraphicsScene.BackgroundLayer)

    def _on_anim_step(self, val):
        self._apply_progress(val)

    def _on_anim_finished(self):
        if self.is_closing:
            self.hide()
            self.main_window.show()
            self.collapsed.emit()

    def mouseMoveEvent(self, event):
        pos_scene = self.mapToScene(event.pos())
        sun_pos = self.sun_item.pos()
        dist = math.hypot(pos_scene.x() - sun_pos.x(), pos_scene.y() - sun_pos.y())
        
        # Update target parallax offset
        if self.progress > 0:
            scene_rect = self.scene.sceneRect()
            dx = (pos_scene.x() - scene_rect.center().x()) / (scene_rect.width() * 0.5)
            dy = (pos_scene.y() - scene_rect.center().y()) / (scene_rect.height() * 0.5)
            self._target_mouse_offset = QPointF(dx, dy)

        # Determine clickable boundary
        min_scale = (WIN_SIZE * 0.48) / (_CORONA_R + 20)
        scale = min_scale + (1.0 - min_scale) * self.progress
        clickable_radius = _SUN_R * scale

        hovering_planet = False
        self._hovered_planet_index = -1
        for i, rect in enumerate(getattr(self, "planet_rects", [])):
            if rect is not None and rect.contains(pos_scene):
                hovering_planet = True
                self._hovered_planet_index = i
                break

        if dist <= clickable_radius * 1.4 and self.progress > 0.95 and not self.is_closing:
            self.setCursor(Qt.PointingHandCursor)
            self._hover_scale_target = 1.08  # Eases smoothly to 8% scale, matching desktop
        elif hovering_planet and self.progress > 0.95 and not self.is_closing:
            self.setCursor(Qt.PointingHandCursor)
            self._hover_scale_target = 1.0
        else:
            self.setCursor(Qt.ArrowCursor)
            self._hover_scale_target = 1.0

        super().mouseMoveEvent(event)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            click_pos_scene = self.mapToScene(event.pos())
            
            # 1. ALWAYS check first if the user clicked the central SUN for direct exit
            sun_pos = self.sun_item.pos()
            dx = click_pos_scene.x() - sun_pos.x()
            dy = click_pos_scene.y() - sun_pos.y()
            dist = math.hypot(dx, dy)

            min_scale = (WIN_SIZE * 0.48) / (_CORONA_R + 20)
            scale = min_scale + (1.0 - min_scale) * self.progress
            clickable_radius = _SUN_R * scale
            
            if dist <= clickable_radius * 1.4:
                # Reset cursor back to normal immediately
                self.setCursor(Qt.ArrowCursor)
                self._hover_scale_target = 1.0
                
                # Close the space (direct exit!)
                self.close_space()

                # Spawn shockwave ripple at click location
                self._ripples.append({
                    "pos": click_pos_scene,
                    "radius": 1.0,
                    "max_radius": 120.0,
                    "alpha": 230.0,
                    "fade_speed": 4.8,
                    "expansion_speed": 4.2
                })

                # Spawn energetic particle burst
                for _ in range(20):
                    angle = random.uniform(0, 360)
                    speed = random.uniform(2.5, 6.0)
                    self._burst_particles.append({
                        "pos": QPointF(click_pos_scene),
                        "angle": angle,
                        "speed": speed,
                        "size": random.uniform(1.2, 3.2),
                        "alpha": 255.0,
                        "fade_speed": random.uniform(4.5, 8.5),
                        "drag": 0.94
                    })
                return

            # 2. Check if planet/panel was clicked
            if self.progress > 0.95:
                clicked_planet_index = -1
                for i, rect in enumerate(getattr(self, "planet_rects", [])):
                    if rect is not None and rect.contains(click_pos_scene):
                        clicked_planet_index = i
                        break
                        
                if clicked_planet_index != -1:
                    if self.panel_mode and clicked_planet_index == self.active_planet_index:
                        # User clicked inside the active open panel (e.g. Chat UI)
                        # Let the event pass through to child QWidgets (so text boxes can be focused)
                        super().mousePressEvent(event)
                        return
                    else:
                        self.trigger_planet(clicked_planet_index, auto_open=True)
                        return
                elif self.panel_mode:
                    # User clicked completely outside the active panel -> close it
                    self.toggle_panel()
                    return

                # 2. Spawn shockwave ripple at click location
                self._ripples.append({
                    "pos": click_pos_scene,
                    "radius": 1.0,
                    "max_radius": 120.0,
                    "alpha": 230.0,
                    "fade_speed": 4.8,
                    "expansion_speed": 4.2
                })

                # 3. Spawn energetic particle burst
                for _ in range(20):
                    angle = random.uniform(0, 360)
                    speed = random.uniform(2.5, 6.0)
                    self._burst_particles.append({
                        "pos": QPointF(click_pos_scene),
                        "angle": angle,
                        "speed": speed,
                        "size": random.uniform(1.2, 3.2),
                        "alpha": 255.0,
                        "fade_speed": random.uniform(4.5, 8.5),
                        "drag": 0.94
                    })
                self.viewport().update()

        super().mousePressEvent(event)

    def trigger_planet(self, index, rotations=None, auto_open=True):
        if self._is_shifting:
            return
            
        # Force clear hover state when action is triggered
        self._hovered_planet_index = -1
        self.setCursor(Qt.ArrowCursor)
            
        if index == self.active_planet_index:
            self.toggle_panel()
        else:
            self._is_shifting = True
            
            if rotations is None:
                rotations = self.carousel_rotations
                # Find shortest path around the 8-planet circular array
                diff = index - self.active_planet_index
                if diff > 4:
                    rotations -= 1
                elif diff < -4:
                    rotations += 1
                
            def apply_shift():
                self.active_planet_index = index
                self.carousel_rotations = rotations

            if self.panel_mode:
                self.toggle_panel() # 1. Close current panel
                # 2. Wait for close to finish, then start sliding to center
                QTimer.singleShot(700, apply_shift)
                # 3. Wait for sliding to finish, then open new panel (always open if panel mode was active)
                QTimer.singleShot(1150, self.toggle_panel)
                QTimer.singleShot(1150, lambda: setattr(self, '_is_shifting', False))
            else:
                apply_shift()
                if auto_open:
                    # Auto-open the new panel after it finishes sliding to the center
                    QTimer.singleShot(450, self.toggle_panel)
                QTimer.singleShot(450, lambda: setattr(self, '_is_shifting', False))

    def shift_planet(self, direction):
        # direction: 1 for right, -1 for left
        if self.progress < 0.95 or self.is_closing:
            return
            
        next_idx = self.active_planet_index + direction
        new_rotations = self.carousel_rotations
        if next_idx >= 8:
            new_rotations += 1
            next_idx = 0
        elif next_idx < 0:
            new_rotations -= 1
            next_idx = 7
            
        self.trigger_planet(next_idx, new_rotations, auto_open=False)

    def wheelEvent(self, event):
        if self.progress < 0.95 or self.is_closing:
            super().wheelEvent(event)
            return

        import time
        current_time = time.time()
        if current_time - self._last_wheel_time < 0.8:
            event.accept()
            return
            
        dx = event.angleDelta().x()
        dy = event.angleDelta().y()

        if abs(dx) > abs(dy):
            # Horizontal swipe
            if dx > 0:
                self.shift_planet(-1) # Swipe right -> left planet
            else:
                self.shift_planet(1)  # Swipe left -> right planet
            self._last_wheel_time = current_time
        elif abs(dy) > 10:
            # Vertical scroll
            if dy > 0:
                # Zoom in -> open panel
                if not self.panel_mode:
                    self.toggle_panel()
                    self._last_wheel_time = current_time
            else:
                # Zoom out -> close panel
                if self.panel_mode:
                    self.toggle_panel()
                    self._last_wheel_time = current_time
        event.accept()

    def keyPressEvent(self, event):
        if self.progress > 0.95 and not self.is_closing:
            if event.key() == Qt.Key_Left:
                self.shift_planet(-1)
            elif event.key() == Qt.Key_Right:
                self.shift_planet(1)
            elif event.key() == Qt.Key_Up:
                if not self.panel_mode:
                    self.toggle_panel()
            elif event.key() == Qt.Key_Down:
                if self.panel_mode:
                    self.toggle_panel()
            elif event.key() == Qt.Key_Escape:
                if self.panel_mode:
                    self.toggle_panel()
                else:
                    self.close_space()
        super().keyPressEvent(event)

    def _on_chat_message_sent(self, text):
        """Forward typed message to the central AgentBridge."""
        print(f"[Chat UI] Sending text to Helio: {text}")
        parent_win = self.main_window
        if hasattr(parent_win, 'agent_bridge'):
            parent_win.agent_bridge.on_transcription(text)
            parent_win.set_ai_state("thinking")
