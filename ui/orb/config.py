"""
config.py — Central constants for the Helio UI.
Edit values here to tune the entire visual system.
"""
import os


# ── Window ───────────────────────────────────────
WIN_SIZE        = 220          # Pixel size of the floating widget (square) — expanded to allow gorgeous horizontal waves
WIN_OPACITY_IDLE   = 0.92      # Window opacity when idle
WIN_OPACITY_HOVER  = 1.00      # Window opacity on hover

# ── Orb ──────────────────────────────────────────

ORB_SCALE_IDLE  = 1.00
ORB_SCALE_HOVER = 1.08
ORB_SCALE_THINK = 1.04
ORB_FLOAT_AMP   = 1.0          # Pixels of idle floating movement (scaled down)
ORB_FLOAT_SPEED = 0.020        # Radians per frame

# ── Holographic Overlay ───────────────────────────
# Three ring layers scaled 5x down. Stroke widths adjusted for visibility.
RINGS = [
    # Inner segmented arc ring — solar gold, brightest
    dict(radius=30, width=1.0, r=255, g=200, b=40,
         base_alpha=150, speed=0.6,  arc_len=60, gap=20),
    # Mid ring — deep amber solar flare
    dict(radius=34, width=0.8, r=255, g=130, b=0,
         base_alpha=100, speed=-1.0, arc_len=80, gap=30),
    # Outer halo arc — burnt orange solar corona edge
    dict(radius=39, width=0.6, r=210, g=60,  b=0,
         base_alpha=55,  speed=0.5,  arc_len=120, gap=40),
]

# Particle count and orbit radius range
PARTICLE_COUNT   = 18          # Reduced slightly for visual clarity on small scale
PARTICLE_R_MIN   = 29
PARTICLE_R_MAX   = 40

# Aura (volumetric solar corona fog)
AURA_RADIUS_INNER = 26
AURA_RADIUS_OUTER = 42

# ── Animation ─────────────────────────────────────
FPS        = 60
FRAME_TIME = 1000 // FPS      # ms per frame

# ── State Profiles ────────────────────────────────
# Each state tunes: ring_speed_mul, glow_intensity, ring_alpha_mul
STATE_PROFILES = {
    "idle":      dict(ring_speed=1.0,  glow=1.0,  ring_alpha=1.0,  pulse_speed=0.025),
    "hover":     dict(ring_speed=1.4,  glow=1.35, ring_alpha=1.5,  pulse_speed=0.030),
    "listening": dict(ring_speed=1.8,  glow=1.55, ring_alpha=1.7,  pulse_speed=0.040),
    "thinking":  dict(ring_speed=2.8,  glow=1.80, ring_alpha=2.0,  pulse_speed=0.065),
    "speaking":  dict(ring_speed=2.0,  glow=1.60, ring_alpha=1.8,  pulse_speed=0.050),
}
