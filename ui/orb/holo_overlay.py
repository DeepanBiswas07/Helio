"""
holo_overlay.py — Transparent QPainter overlay drawn on top of the orb.

Renders:
  • Volumetric cyan aura (radial gradient fog)
  • Three layered rotating holographic arc-rings
  • Floating ambient particles
  • State-driven glow intensity
  • Easing-based hover scale expansion
  • Smooth vertical floating bobbing (idle hover)
  • Elastic bounce and ripple shockwave click animations

This widget is the same size as the main window and is composited
without a video background using WA_TranslucentBackground.
"""
import math
import random
from PyQt5.QtWidgets import QWidget
from PyQt5.QtCore import Qt, QTimer, QPointF, QRectF
from PyQt5.QtGui import QPainter, QColor, QPen, QRadialGradient, QBrush

from orb.config import (WIN_SIZE, RINGS, PARTICLE_COUNT,
                    PARTICLE_R_MIN, PARTICLE_R_MAX,
                    AURA_RADIUS_INNER, AURA_RADIUS_OUTER,
                    FPS, FRAME_TIME, ORB_FLOAT_AMP, ORB_FLOAT_SPEED)
from orb.state import state as orb_state


class HoloOverlay(QWidget):
    """
    Sits inside the MainWindow; paints holographic rings, aura, particles, 
    and click animation ripples/bursts. Transparent to click events so
    the MainWindow can receive and forward drag/click inputs.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setFixedSize(WIN_SIZE, WIN_SIZE)

        # ── Ring animation state ──────────────
        self._ring_angles = [0.0] * len(RINGS)   # current rotation per ring

        # ── Aura / glow breathing ─────────────
        self._pulse_t = 0.0
        self._pulse   = 0.0          # -1 → +1

        # ── Ambient particles ─────────────────
        self._particles = self._init_particles()

        # ── Float & Scale animation state ─────
        self._float_t      = 0.0
        self._float_dy     = 0.0
        self._scale        = 1.0
        self._scale_target = 1.0

        # ── Click animation state ─────────────
        self._ripples            = []
        self._burst_particles    = []
        self._click_scale_offset = 0.0
        self._click_scale_vel    = 0.0

        # ── Audio Wave visualizer state ────────
        self._target_volume  = 0.0
        self._current_volume = 0.0
        self._wave_phase     = 0.0

        # ── Plasma surface blobs ──────────────
        _pc = 7
        self._plasma = [
            {
                "angle": i * (360.0 / _pc) + random.uniform(-10, 10),
                "speed": random.uniform(0.06, 0.16) * random.choice([-1, 1]),
                "size":  random.uniform(2.5, 6.0),
                "alpha": random.randint(20, 70),
                "a_dir": random.choice([-1, 1]) * random.uniform(0.3, 0.9),
                "r_off": random.uniform(-2.0, 4.0),
            }
            for i in range(_pc)
        ]

        # ── Flare arcs ────────────────────────
        self._flares = [self._new_flare() for _ in range(3)]

        # ── Timer ─────────────────────────────
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(FRAME_TIME)

    # ── Particle initialisation ───────────────

    def _init_particles(self):
        pts = []
        for _ in range(PARTICLE_COUNT):
            angle  = random.uniform(0, 360)
            dist   = random.uniform(PARTICLE_R_MIN, PARTICLE_R_MAX)
            pts.append({
                "angle":      angle,
                "dist":       dist,
                "speed":      random.uniform(0.25, 0.80) * random.choice([-1, 1]),
                "size":       random.uniform(1.2, 2.8),
                "alpha":      random.randint(40, 160),
                "alpha_dir":  random.choice([-1, 1]) * random.uniform(1.5, 3.5),
            })
        return pts

    # ── External Interaction API ──────────────

    def set_scale_target(self, target: float):
        """Called by parent window to dynamically adjust scale (e.g. on hover)"""
        self._scale_target = target

    def trigger_click(self, local_pos):
        """Triggers click ripple, particle burst, and elastic spring bounce (scaled down 5x)"""
        # 1. Trigger elastic spring compression (implode briefly, then bounce back)
        self._click_scale_offset = -0.06
        self._click_scale_vel    = -0.03
        
        # 2. Spawn a visual shockwave ripple (scaled down 5x)
        ripple = {
            "pos": QPointF(local_pos),
            "radius": 1.0,
            "max_radius": 38.0,
            "alpha": 230.0,
            "fade_speed": 6.8,
            "expansion_speed": 1.2
        }
        self._ripples.append(ripple)
        
        # 3. Spawn a burst of energetic, short-lived particles radiating outward (scaled down 5x)
        for _ in range(12):
            angle = random.uniform(0, 360)
            speed = random.uniform(0.7, 1.6)
            self._burst_particles.append({
                "pos": QPointF(local_pos),
                "angle": angle,
                "speed": speed,
                "size": random.uniform(0.5, 1.2),
                "alpha": 255.0,
                "fade_speed": random.uniform(6.5, 10.5),
                "drag": 0.94
            })

    # ── Tick ─────────────────────────────────

    @staticmethod
    def _new_flare():
        return {
            "angle":    random.uniform(0, 360),
            "length":   random.uniform(6, 18),
            "cur_len":  0.0,
            "speed":    random.uniform(0.2, 0.6),
            "alpha":    0.0,
            "phase":    "grow",
            "hold_t":   0.0,
            "hold_max": random.uniform(15, 45),
        }

    def _tick(self):
        sp = orb_state.ring_speed   # state multiplier

        # Breathing pulse
        self._pulse_t += orb_state.pulse_speed
        self._pulse    = math.sin(self._pulse_t)

        # Rotate each ring
        for i, ring in enumerate(RINGS):
            self._ring_angles[i] = (
                self._ring_angles[i] + ring["speed"] * sp * (1 / FPS) * 60
            ) % 360

        # Drift plasma blobs
        for p in self._plasma:
            p["angle"] = (p["angle"] + p["speed"] * sp) % 360
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
            p["angle"] = (p["angle"] + p["speed"] * sp) % 360
            p["alpha"] += p["alpha_dir"]
            if p["alpha"] > 175:
                p["alpha"], p["alpha_dir"] = 175, -abs(p["alpha_dir"])
            elif p["alpha"] < 30:
                p["alpha"], p["alpha_dir"] =  30,  abs(p["alpha_dir"])

        # Float vertical movement (sine wave)
        self._float_t += ORB_FLOAT_SPEED
        self._float_dy = ORB_FLOAT_AMP * math.sin(self._float_t)

        # Eased scale transition
        self._scale += (self._scale_target - self._scale) * 0.08

        # Elastic click spring physics
        k = 0.12  # spring constant
        d = 0.82  # damping ratio
        accel = -k * self._click_scale_offset
        self._click_scale_vel = (self._click_scale_vel + accel) * d
        self._click_scale_offset += self._click_scale_vel

        # Smoothly ease the visual volume towards the captured microphone volume
        if orb_state.name == "listening":
            self._current_volume += (self._target_volume - self._current_volume) * 0.22
            self._wave_phase += 0.16
        else:
            # Fade out gracefully when returning to idle
            self._current_volume += (0.0 - self._current_volume) * 0.25
            self._wave_phase += 0.03  # slow idle wave ripple

        # Update click ripples
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

        self.update()

    # ── Paint ────────────────────────────────

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        # ── 1. Draw click visual animations in static window coordinate space ──
        # This keeps the ripple and burst anchored to the physical screen tap location!
        self._draw_ripples(painter)
        self._draw_burst_particles(painter)

        # ── 2. Save canvas state and apply float & scale transformations ──
        painter.save()

        cx = WIN_SIZE / 2.0
        cy = WIN_SIZE / 2.0
        c  = QPointF(cx, cy)

        # Overall scale combines baseline scale and spring shockwave offset
        total_scale = self._scale + self._click_scale_offset

        # Translate to center, scale and float, then translate back
        painter.translate(cx, cy + self._float_dy)
        painter.scale(total_scale, total_scale)
        painter.translate(-cx, -cy)

        glow = orb_state.glow      # e.g. 1.0 → 1.8

        # Draw primary holographic components
        self._draw_aura(painter, c, glow)
        self._draw_audio_wave(painter, c, glow)
        self._draw_rings(painter, c, glow)
        self._draw_particles(painter, c)
        self._draw_plasma_surface(painter, c)
        self._draw_flare_arcs(painter, c)

        painter.restore()
        painter.end()

    # ── Aura ─────────────────────────────────

    def _draw_aura(self, painter, c, glow):
        """Volumetric solar corona fog that breathes with the pulse."""
        # 1. Outer soft environmental fog — deep corona atmospheric glow
        r_out = (AURA_RADIUS_OUTER + self._pulse * 5) * (0.85 + glow * 0.15)
        grad  = QRadialGradient(c, r_out)
        grad.setColorAt(0.00, QColor(180, 80,  0,   min(255, int(100 * glow))))
        grad.setColorAt(0.40, QColor(120, 40,  0,   min(255, int(50  * glow))))
        grad.setColorAt(0.75, QColor(60,  15,  0,   min(255, int(18  * glow))))
        grad.setColorAt(1.00, QColor(0,   0,   0,   0))
        painter.setPen(Qt.NoPen)
        painter.setBrush(grad)
        painter.drawEllipse(c, r_out, r_out)

        # 2. Inner energy halo bloom — chromosphere, intense solar gold
        r_in  = (AURA_RADIUS_INNER + self._pulse * 7) * (0.90 + glow * 0.10)
        grad2 = QRadialGradient(c, r_in)
        grad2.setColorAt(0.00, QColor(255, 200, 40,  min(255, int(170 * glow))))
        grad2.setColorAt(0.45, QColor(255, 160, 0,   min(255, int(95  * glow))))
        grad2.setColorAt(0.80, QColor(220, 100, 0,   min(255, int(35  * glow))))
        grad2.setColorAt(1.00, QColor(0,   0,   0,   0))
        painter.setBrush(grad2)
        painter.drawEllipse(c, r_in, r_in)

        # 3. Photosphere core — blinding white-gold solar surface (calm ±1.5 px)
        r_core = (16.0 + self._pulse * 1.5) * (0.97 + glow * 0.03)
        grad3 = QRadialGradient(c, r_core)
        grad3.setColorAt(0.00, QColor(255, 255, 220, min(255, int(255 * glow))))
        grad3.setColorAt(0.35, QColor(255, 210, 60,  min(255, int(220 * glow))))
        grad3.setColorAt(0.70, QColor(255, 130, 0,   min(255, int(130 * glow))))
        grad3.setColorAt(1.00, QColor(0,   0,   0,   0))
        painter.setBrush(grad3)
        painter.drawEllipse(c, r_core, r_core)

    # ── Rings ────────────────────────────────

    def _draw_rings(self, painter, c, glow):
        """
        Each ring is drawn as multiple arcs separated by gaps.
        arc_len and gap are in degrees.
        The whole ring is rotated by its current _ring_angles value.
        """
        alpha_mul = orb_state.ring_alpha

        for i, ring in enumerate(RINGS):
            base_alpha = min(255, int(ring["base_alpha"] * alpha_mul * glow))
            glow_alpha = min(80,  int(base_alpha * 0.35))
            rot        = self._ring_angles[i]
            r          = ring["radius"]
            arc_len    = ring["arc_len"]
            gap        = ring["gap"]
            cycle      = arc_len + gap
            rect       = QRectF(c.x() - r, c.y() - r, r * 2, r * 2)

            # How many arcs fit in 360°?
            angle = 0.0
            while angle < 360.0:
                start_deg = (rot + angle) % 360.0
                draw_len  = min(arc_len, 360.0 - angle)

                # ── Glow pass (wider, transparent) ──
                gpen = QPen(QColor(ring["r"], ring["g"], ring["b"], glow_alpha))
                gpen.setWidthF(ring["width"] * 3.0)
                gpen.setCapStyle(Qt.RoundCap)
                painter.setPen(gpen)
                painter.setBrush(Qt.NoBrush)
                painter.drawArc(rect,
                                int(start_deg * 16),
                                int(draw_len  * 16))

                # ── Core crisp pass ──
                cpen = QPen(QColor(ring["r"], ring["g"], ring["b"], base_alpha))
                cpen.setWidthF(ring["width"])
                cpen.setCapStyle(Qt.RoundCap)
                painter.setPen(cpen)
                painter.drawArc(rect,
                                int(start_deg * 16),
                                int(draw_len  * 16))

                angle += cycle

    # ── Ambient Particles ────────────────────

    def _draw_particles(self, painter, c):
        painter.setPen(Qt.NoPen)
        for p in self._particles:
            rad = math.radians(p["angle"])
            px  = c.x() + p["dist"] * math.cos(rad)
            py  = c.y() + p["dist"] * math.sin(rad)
            pt  = QPointF(px, py)
            sz  = p["size"]
            a   = int(p["alpha"])

            # Outer amber glow — solar wind particle trail
            painter.setBrush(QColor(255, 140, 0, int(a * 0.35)))
            painter.drawEllipse(pt, sz * 2.2, sz * 2.2)

            # Inner golden core — solar spark
            painter.setBrush(QColor(255, 230, 100, a))
            painter.drawEllipse(pt, sz, sz)

    # ── Plasma surface ────────────────────────

    def _draw_plasma_surface(self, painter, c):
        """Subtle plasma blobs hugging the solar limb — very VERY subtle on desktop size."""
        _LIMB_R = 18.0   # matches desktop photosphere radius roughly
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

    # ── Flare arcs ────────────────────────────

    def _draw_flare_arcs(self, painter, c):
        """Hairline flare spikes shooting from the limb surface."""
        _LIMB_R = 18.0
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
            gpen = QPen(QColor(255, 180, 40, int(a * 0.28)))
            gpen.setWidthF(2.2)
            gpen.setCapStyle(Qt.RoundCap)
            painter.setPen(gpen)
            painter.drawLine(QPointF(x0, y0), QPointF(x1, y1))
            cpen = QPen(QColor(255, 240, 160, a))
            cpen.setWidthF(0.7)
            cpen.setCapStyle(Qt.RoundCap)
            painter.setPen(cpen)
            painter.drawLine(QPointF(x0, y0), QPointF(x1, y1))

    # ── Click FX Drawing ─────────────────────

    def _draw_ripples(self, painter):
        for r in self._ripples:
            alpha = int(r["alpha"])
            if alpha <= 0:
                continue
            alpha = max(0, min(255, alpha))
            
            # Outer amber glow ripple ring
            glow_pen = QPen(QColor(255, 150, 0, int(alpha * 0.35)))
            glow_pen.setWidthF(1.5)
            painter.setPen(glow_pen)
            painter.setBrush(Qt.NoBrush)
            painter.drawEllipse(r["pos"], r["radius"], r["radius"])

            # Inner solar gold ripple ring
            core_pen = QPen(QColor(255, 230, 100, alpha))
            core_pen.setWidthF(0.4)
            painter.setPen(core_pen)
            painter.drawEllipse(r["pos"], r["radius"], r["radius"])

    def _draw_burst_particles(self, painter):
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

    # ── Real-time Audio Wave ──────────────────

    def set_audio_volume(self, volume: float):
        """Dynamic hook to update the target voice amplitude level (0.0 to 1.0)"""
        self._target_volume = volume

    def _draw_audio_wave(self, painter, c, glow):
        """
        Renders a beautiful, reactive, symmetric sci-fi oscilloscope wave
        that grows from the sides of the orb and ripples dynamically.
        Starts deep from the core of the orb and tapers exponentially outward!
        """
        # Skip drawing if the wave is completely silent/collapsed
        if self._current_volume < 0.005:
            return

        painter.setPen(Qt.NoPen)

        cx, cy = c.x(), c.y()
        orb_radius = 32.0  # Starts exactly outside the holographic ring circle!
        num_bars = 48      # Perfectly balanced liquid ribbon density
        spacing = 1.62     # Tailored spacing to fill the rest of the window screen width
        bar_width = 0.8    # Razor-sharp bar width

        for i in range(num_bars):
            # Distance from orb center outwards
            dist = orb_radius + i * spacing
            x_left  = cx - dist
            x_right = cx + dist

            # Normalized position coordinate [0.0, 1.0] across the wave width
            t = i / float(num_bars - 1)

            # Exponential decay envelope for voice peaks (tallest right next to the orb, decays outward)
            voice_envelope = math.exp(-t * 2.8)

            # Gentler, wider envelope for the ambient noise floor so it stays visible across the whole line!
            noise_envelope = 1.0 - t * 0.55

            # ── 1. Pristine Sine Component (Muted Profile) ─────────────────
            # Continuous, smooth rolling sine wave with per-bar progressive phase shift
            pristine_ripple = math.sin(self._wave_phase * 2.0 - t * 14.0) * 0.35 + 0.65
            pristine_height = 4.8 * pristine_ripple * noise_envelope

            # ── 2. Jumbled Voice Component (Speaking Profile) ──────────────
            jumble_ripple = math.sin(self._wave_phase - t * 7.2) * 0.28 + 0.72
            # Multi-frequency organic noise for each bar individually to create a beautiful, jumbled audio spectrum!
            noise_i = (
                math.sin(self._wave_phase * 1.8 - i * 0.42) * 0.35 +
                math.sin(self._wave_phase * 3.2 + i * 0.73) * 0.20 +
                math.cos(self._wave_phase * 0.9 - i * 0.15) * 0.15 +
                0.30
            )
            noise_i = max(0.02, noise_i * 0.16)
            
            voice_component = (self._current_volume * 46.0) * voice_envelope
            noise_component = (noise_i * 8.5) * noise_envelope
            jumble_height = (voice_component + noise_component) * jumble_ripple

            # ── 3. Dynamic Blend Transition ───────────────────────────────
            # Blend instantly as volume goes up
            mix = min(1.0, self._current_volume * 16.0)
            live_height = (1.0 - mix) * pristine_height + mix * jumble_height + 1.2
            
            # Enforce a sleek baseline height to look alive
            live_height = max(1.0, live_height)

            # Dynamic opacity blending with the overall UI state glow (clamped to 255 to prevent Qt overflow gap)
            alpha = min(255, max(0, int(220 * glow * noise_envelope)))
            if alpha <= 0:
                continue

            # Draw symmetric vertical bars on both sides
            # Left side bars (uses QRectF for sub-pixel precision to prevent any truncation)
            painter.fillRect(
                QRectF(x_left - bar_width / 2.0, cy - live_height, bar_width, live_height * 2.0),
                QColor(255, 165, 20, alpha)
            )
            
            # Right side bars
            painter.fillRect(
                QRectF(x_right - bar_width / 2.0, cy - live_height, bar_width, live_height * 2.0),
                QColor(255, 165, 20, alpha)
            )
