"""
planets.py — Orbital planet orb renderers for the Helio carousel.

BasePlanet provides two shared cinematic animation systems:
  - _draw_idle()  : calm, organic ambient animation (always running)
  - _draw_hover() : energetic targeting-lock animation (on mouse-over)

Subclass BasePlanet, set self.color / self.title / self.subtitle,
override draw() to add unique visual layers, then call the shared systems.
Register in get_planets() at the bottom.
"""
import math, random
from PyQt5.QtGui  import (QColor, QRadialGradient, QPen, QPainterPath,
                           QFontMetrics, QFont)
from PyQt5.QtCore import Qt, QPointF, QRectF


# ─────────────────────────────────────────────────────────────────────────────
# BASE PLANET  — shared idle + hover animation engine
# ─────────────────────────────────────────────────────────────────────────────
class BasePlanet:
    def __init__(self, title: str, color: QColor,
                 subtitle: str = ""):
        self.title    = title
        self.color    = color
        self.subtitle = subtitle

        # Idle orbit ring angle
        self._idle_ring_angle = random.uniform(0, 360)
        # Idle ambient micro-particles (angle, dist_frac, speed, alpha, a_dir)
        rng = random.Random(hash(title) & 0xFFFF)
        self._idle_parts = [
            {
                "angle":    rng.uniform(0, 360),
                "dfrac":    rng.uniform(0.55, 0.82),
                "speed":    rng.uniform(0.12, 0.32) * rng.choice([-1,1]),
                "alpha":    rng.randint(30, 110),
                "adir":     rng.choice([-1,1]) * rng.uniform(0.8, 2.0),
            }
            for _ in range(5)
        ]
        self._prev_phase = None

    # ── frame tick (call once per frame) ──────────────────────────────────
    def _tick(self):
        self._idle_ring_angle = (self._idle_ring_angle + 0.28) % 360
        for p in self._idle_parts:
            p["angle"] = (p["angle"] + p["speed"]) % 360
            p["alpha"] += p["adir"]
            if p["alpha"] > 120: p["alpha"], p["adir"] = 120, -abs(p["adir"])
            elif p["alpha"] < 20: p["alpha"], p["adir"] =  20,  abs(p["adir"])

    def _maybe_tick(self, phase):
        if phase != self._prev_phase:
            self._tick()
            self._prev_phase = phase

    # ── SHARED IDLE ANIMATION (sonar pings + scanner sweep + node crown) ────
    def _draw_idle(self, painter, cx, cy, w, r_base, alpha, inner_opacity, phase):
        a_mul = alpha / 255.0
        opa   = inner_opacity * a_mul
        cr, cg, cb = self.color.red(), self.color.green(), self.color.blue()

        # I1. SONAR RADAR PINGS — 3 staggered expanding rings
        painter.setBrush(Qt.NoBrush)
        for ri in range(3):
            t      = ((phase * 0.018) + ri/3.0) % 1.0
            ring_r = r_base * (0.85 + t * 1.10)
            ring_a = int(160 * (1.0-t) * opa)
            if ring_a > 0:
                gpen = QPen(QColor(cr,cg,cb, int(ring_a*0.32))); gpen.setWidthF(3.0)
                painter.setPen(gpen)
                painter.drawEllipse(QPointF(cx,cy), ring_r, ring_r)
                cpen = QPen(QColor(min(255,cr+40),min(255,cg+40),min(255,cb+40), ring_a))
                cpen.setWidthF(0.8); painter.setPen(cpen)
                painter.drawEllipse(QPointF(cx,cy), ring_r, ring_r)

        # I2. ROTATING SCANNER SWEEP
        sweep_rot  = (phase * 1.8) % 360.0
        sweep_r    = r_base * 1.35
        sweep_rect = QRectF(cx-sweep_r, cy-sweep_r, sweep_r*2, sweep_r*2)
        for ti in range(4):
            t_start = (sweep_rot - ti*20) % 360.0
            t_alpha = int(opa * (65 - ti*14))
            if t_alpha > 0:
                tpen = QPen(QColor(cr,cg,cb,t_alpha)); tpen.setWidthF(3.5-ti*0.5)
                tpen.setCapStyle(Qt.RoundCap); painter.setPen(tpen)
                painter.setBrush(Qt.NoBrush)
                painter.drawArc(sweep_rect, int(t_start*16), int(20*16))
        lead = QPen(QColor(min(255,cr+80),min(255,cg+80),min(255,cb+80), int(180*opa)))
        lead.setWidthF(1.2); lead.setCapStyle(Qt.RoundCap); painter.setPen(lead)
        painter.drawArc(sweep_rect, int(sweep_rot*16), int(8*16))

        # I3. ELECTRIC NODE CROWN — 6 orbiting nodes
        node_r    = r_base * 1.18
        node_spin = (phase * 0.9) % 360.0
        painter.setPen(Qt.NoPen)
        for ni in range(6):
            na  = math.radians(node_spin + ni*60.0)
            npt = QPointF(cx + node_r*math.cos(na), cy + node_r*math.sin(na))
            painter.setBrush(QColor(cr,cg,cb, int(45*opa)))
            painter.drawEllipse(npt, 4.0, 4.0)
            painter.setBrush(QColor(230,248,255, int(200*opa)))
            painter.drawEllipse(npt, 1.6, 1.6)

        # I4. Core breath bloom
        boost = QRadialGradient(cx, cy, r_base*0.55)
        boost.setColorAt(0.0, QColor(255,255,255, int(55*opa)))
        boost.setColorAt(0.5, QColor(cr,cg,cb,    int(35*opa)))
        boost.setColorAt(1.0, QColor(0,0,0,0))
        painter.setBrush(boost); painter.setPen(Qt.NoPen)
        painter.drawEllipse(QPointF(cx,cy), r_base*0.55, r_base*0.55)

    # ── CINEMATIC HOVER ANIMATION (Refined & Elegant) ─────────────────────────
    def _draw_hover(self, painter, cx, cy, w, h, r_base, alpha,
                    inner_opacity, panel_progress, hover_prog, phase):
        if hover_prog < 0.01 or panel_progress >= 0.1:
            return
        a_mul = alpha / 255.0
        hp    = hover_prog
        cr, cg, cb = self.color.red(), self.color.green(), self.color.blue()

        # W1. SMOOTH SHOCKWAVE PULSE — 3 rings, elegant expansion
        painter.setBrush(Qt.NoBrush)
        for ri in range(3):
            t      = ((phase * 0.020) + ri/3.0) % 1.0
            ring_r = r_base * (0.8 + t * 1.4)
            ring_a = int(180 * (1.0-t)**1.5 * hp * a_mul)
            if ring_a > 0:
                gpen = QPen(QColor(cr,cg,cb, int(ring_a*0.35))); gpen.setWidthF(3.5)
                painter.setPen(gpen)
                painter.drawEllipse(QPointF(cx,cy), ring_r, ring_r)
                cpen = QPen(QColor(255,255,255, ring_a)); cpen.setWidthF(0.9)
                painter.setPen(cpen)
                painter.drawEllipse(QPointF(cx,cy), ring_r, ring_r)

        # W2. ELEGANT COUNTER-ROTATING RINGS
        for rr_, rot_, arc_deg, width_, arc_a_ in [
            (r_base*1.25,  phase*1.8,  55, 1.5, 40),
            (r_base*1.45, -phase*1.0,  35, 1.0, 30),
        ]:
            rect = QRectF(cx-rr_, cy-rr_, rr_*2, rr_*2)
            rot_ = rot_ % 360.0
            for ti in range(3):
                t_start = (rot_ - ti*20) % 360.0
                t_alpha = int(hp * a_mul * (arc_a_ - ti*10))
                if t_alpha > 0:
                    tpen = QPen(QColor(cr,cg,cb,t_alpha))
                    tpen.setWidthF(width_+ti*0.4); tpen.setCapStyle(Qt.RoundCap)
                    painter.setPen(tpen); painter.setBrush(Qt.NoBrush)
                    painter.drawArc(rect, int(t_start*16), int(arc_deg*16))
            lead = QPen(QColor(255,255,255, int(150*hp*a_mul)))
            lead.setWidthF(width_*0.8); lead.setCapStyle(Qt.RoundCap)
            painter.setPen(lead)
            painter.drawArc(rect, int(rot_*16), int(8*16))

        # W3. CONTROLLED ENERGY ARCS — 5 subtle lightning spikes
        limb_r = r_base * 0.90
        for arc_i in range(5):
            base_angle = (phase * 1.5 + arc_i * 72.0) % 360.0
            flicker    = math.sin(phase * 0.15 + arc_i * 2.1)
            tip_r      = r_base * (1.15 + 0.25 * abs(math.sin(phase*0.1 + arc_i)))
            arc_a      = int(150 * hp * a_mul * (0.4 + 0.6*abs(flicker)))
            if arc_a <= 0: continue
            fa = math.radians(base_angle)
            x0 = cx + limb_r * math.cos(fa);  y0 = cy + limb_r * math.sin(fa)
            k1_r = limb_r + (tip_r-limb_r)*0.45
            k1_a = fa + math.radians(flicker * 12)
            x1 = cx + k1_r*math.cos(k1_a); y1 = cy + k1_r*math.sin(k1_a)
            k2_r = limb_r + (tip_r-limb_r)*0.75
            k2_a = fa + math.radians(-flicker * 8)
            x2 = cx + k2_r*math.cos(k2_a); y2 = cy + k2_r*math.sin(k2_a)
            x3 = cx + tip_r*math.cos(fa);  y3 = cy + tip_r*math.sin(fa)
            path = QPainterPath(QPointF(x0,y0))
            path.lineTo(x1,y1); path.lineTo(x2,y2); path.lineTo(x3,y3)
            gpen = QPen(QColor(cr,cg,cb, int(arc_a*0.4)))
            gpen.setWidthF(2.5); gpen.setCapStyle(Qt.RoundCap)
            painter.setPen(gpen); painter.drawPath(path)
            cpen = QPen(QColor(255,255,255, arc_a))
            cpen.setWidthF(0.7); cpen.setCapStyle(Qt.RoundCap)
            painter.setPen(cpen); painter.drawPath(path)

        # W4. GENTLE PARTICLE DRIFT — 8 outward sparks
        painter.setPen(Qt.NoPen)
        for pi in range(8):
            p_angle = (phase * 2.5 + pi * 45.0) % 360.0
            p_t     = ((phase * 0.035) + pi/8.0) % 1.0
            p_r     = r_base * (0.7 + p_t * 1.20)
            p_a     = int(180 * (1.0-p_t)**1.5 * hp * a_mul)
            if p_a <= 0: continue
            pr = math.radians(p_angle)
            px_ = cx + p_r*math.cos(pr); py_ = cy + p_r*math.sin(pr)
            painter.setBrush(QColor(cr,cg,cb, int(p_a*0.4)))
            painter.drawEllipse(QPointF(px_,py_), 2.8, 2.8)
            painter.setBrush(QColor(255,255,255, p_a))
            painter.drawEllipse(QPointF(px_,py_), 1.0, 1.0)

        # W5. SOFT CORE GLOW
        nova = QRadialGradient(cx, cy, r_base*0.7)
        nova.setColorAt(0.0, QColor(255,255,255, int(170*hp*inner_opacity*a_mul)))
        nova.setColorAt(0.3, QColor(min(255,cr+60),min(255,cg+60),min(255,cb+60),
                                    int(110*hp*inner_opacity*a_mul)))
        nova.setColorAt(0.7, QColor(cr,cg,cb, int(55*hp*inner_opacity*a_mul)))
        nova.setColorAt(1.0, QColor(0,0,0,0))
        painter.setPen(Qt.NoPen); painter.setBrush(nova)
        painter.drawEllipse(QPointF(cx,cy), r_base*0.7, r_base*0.7)

        # W6. PILL LABEL + SUBTITLE
        title_a = int(255 * hp * a_mul)
        if title_a > 0:
            font_t = QFont("Segoe UI", 11, QFont.Bold); painter.setFont(font_t)
            fm = QFontMetrics(font_t)
            tw = fm.width(self.title); th = fm.height()
            pw = tw+28; ph_h = th+10
            px_ = cx - pw/2; py_ = cy - h/2 - ph_h - 10
            painter.setBrush(QColor(0,10,20, int(180*hp*a_mul)))
            bpen = QPen(QColor(cr,cg,cb, int(160*hp*a_mul))); bpen.setWidthF(1.2)
            painter.setPen(bpen)
            painter.drawRoundedRect(QRectF(px_,py_,pw,ph_h), ph_h/2, ph_h/2)
            ty = int(py_ + ph_h/2 + th/2 - 3)
            for dx,dy_ in [(-1,0),(1,0),(0,-1),(0,1)]:
                painter.setPen(QColor(cr,cg,cb, int(80*hp*a_mul)))
                painter.drawText(int(cx-tw/2+dx), ty+dy_, self.title)
            painter.setPen(QColor(230,248,255, title_a))
            painter.drawText(int(cx-tw/2), ty, self.title)
            if self.subtitle:
                sfont = QFont("Segoe UI", 7); painter.setFont(sfont)
                sfm   = QFontMetrics(sfont)
                sw    = sfm.width(self.subtitle)
                painter.setPen(QColor(180,220,255, int(180*hp*a_mul)))
                painter.drawText(int(cx-sw/2), int(cy+h/2+16), self.subtitle)


    # ── base draw (override in subclasses) ────────────────────────────────
    def draw(self, painter, i, cx, cy, w, h, rad, alpha, is_hovered,
             inner_opacity, panel_progress, is_active, phase):
        raise NotImplementedError


# ─────────────────────────────────────────────────────────────────────────────
# GENERIC PLANET
# ─────────────────────────────────────────────────────────────────────────────
class GenericPlanet(BasePlanet):
    def draw(self, painter, i, cx, cy, w, h, rad, alpha, is_hovered,
             inner_opacity, panel_progress, is_active, phase, hover_prog=None):
        self._maybe_tick(phase)
        a_mul  = alpha / 255.0
        cr,cg,cb = self.color.red(), self.color.green(), self.color.blue()
        r_base = w * 0.42
        # Use externally supplied hover_prog (correct, pre-depth value) when available
        if hover_prog is None:
            hover_prog = max(0.0, min(1.0, (w/90.0-1.0)/0.2))

        painter.save()
        painter.setRenderHint(painter.Antialiasing)

        # Idle animation (runs always)
        if inner_opacity > 0.02:
            self._draw_idle(painter, cx, cy, w, r_base, alpha, inner_opacity, phase)

        # ── 3D SPHERE RENDERING ──────────────────────────────────────────
        if inner_opacity > 0:
            # Layer 1: Base ambient fill (dark undercoat)
            base_fill = QRadialGradient(cx, cy, r_base)
            base_fill.setColorAt(0.0, QColor(cr//2, cg//4, 0,      int(220*inner_opacity*a_mul)))
            base_fill.setColorAt(1.0, QColor(0, 0, 0,              int(180*inner_opacity*a_mul)))
            painter.setPen(Qt.NoPen); painter.setBrush(base_fill)
            painter.drawEllipse(QPointF(cx,cy), r_base, r_base)

            # Layer 2: Primary light source (top-left, warm key light)
            light_x = cx - r_base * 0.30
            light_y = cy - r_base * 0.32
            key_light = QRadialGradient(light_x, light_y, r_base * 1.10)
            key_light.setColorAt(0.00, QColor(255, min(255,cg+100), min(255,cb+80),
                                              int(255*inner_opacity*a_mul)))
            key_light.setColorAt(0.35, QColor(cr, cg, cb//2,        int(190*inner_opacity*a_mul)))
            key_light.setColorAt(0.65, QColor(cr//2, cg//4, 0,      int(80*inner_opacity*a_mul)))
            key_light.setColorAt(1.00, QColor(0, 0, 0, 0))
            painter.setBrush(key_light)
            painter.drawEllipse(QPointF(cx,cy), r_base, r_base)

            # Layer 3: Shadow (bottom-right, cool shadow)
            shadow_x = cx + r_base * 0.25
            shadow_y = cy + r_base * 0.28
            shadow = QRadialGradient(shadow_x, shadow_y, r_base * 0.85)
            shadow.setColorAt(0.00, QColor(0, 0, 0,        int(160*inner_opacity*a_mul)))
            shadow.setColorAt(0.60, QColor(0, 0, 0,        int(60*inner_opacity*a_mul)))
            shadow.setColorAt(1.00, QColor(0, 0, 0, 0))
            painter.setBrush(shadow)
            painter.drawEllipse(QPointF(cx,cy), r_base, r_base)

            # Layer 4: Specular highlight (small bright dot, top-left)
            spec_x = cx - r_base * 0.28
            spec_y = cy - r_base * 0.30
            spec = QRadialGradient(spec_x, spec_y, r_base * 0.28)
            spec.setColorAt(0.0, QColor(255, 255, 240, int(200*inner_opacity*a_mul)))
            spec.setColorAt(0.5, QColor(255, 230, 180, int(80*inner_opacity*a_mul)))
            spec.setColorAt(1.0, QColor(0, 0, 0, 0))
            painter.setBrush(spec)
            painter.drawEllipse(QPointF(cx,cy), r_base, r_base)

            # Layer 5: Rim light (subtle bright edge on the shadow side)
            rim = QRadialGradient(cx + r_base*0.7, cy + r_base*0.7, r_base*0.6)
            rim.setColorAt(0.0, QColor(cr, cg, min(255,cb+60), int(60*inner_opacity*a_mul)))
            rim.setColorAt(1.0, QColor(0, 0, 0, 0))
            painter.setBrush(rim)
            painter.drawEllipse(QPointF(cx,cy), r_base, r_base)

        # Frosted shell border
        pen_a = int((80+120*hover_prog+70*panel_progress)*a_mul) if not is_active \
                else int((80+70*panel_progress)*a_mul)
        shell = QPen(QColor(cr,cg,cb,pen_a))
        shell.setWidthF(1.5 + hover_prog)
        painter.setBrush(Qt.NoBrush); painter.setPen(shell)
        painter.drawRoundedRect(QRectF(cx-w/2,cy-h/2,w,h), rad, rad)

        # Cast shadow ellipse below the planet (3D grounding)
        if inner_opacity > 0.1:
            shadow_ell = QRadialGradient(cx, cy + r_base*1.05, r_base*0.55)
            shadow_ell.setColorAt(0.0, QColor(0, 0, 0, int(70*inner_opacity*a_mul)))
            shadow_ell.setColorAt(1.0, QColor(0, 0, 0, 0))
            painter.setPen(Qt.NoPen); painter.setBrush(shadow_ell)
            painter.drawEllipse(QPointF(cx, cy+r_base*1.05), r_base*0.55, r_base*0.20)

        # Cinematic hover
        self._draw_hover(painter, cx, cy, w, h, r_base, alpha,
                         inner_opacity, panel_progress, hover_prog, phase)
        painter.restore()


# ─────────────────────────────────────────────────────────────────────────────
# CHAT PLANET  — holographic blue orb with extra rings, particles, flares
# ─────────────────────────────────────────────────────────────────────────────
_CHAT_RINGS = [
    dict(rf=0.50, arc=55, gap=20, spd= 0.55, r=0,  g=220,b=255,w=1.2,a=200),
    dict(rf=0.64, arc=40, gap=35, spd=-0.38, r=0,  g=160,b=255,w=0.9,a=160),
    dict(rf=0.80, arc=28, gap=50, spd= 0.22, r=80, g=180,b=255,w=0.7,a=110),
]

class ChatPlanet(BasePlanet):
    def __init__(self):
        super().__init__("CHAT", QColor(0,180,255), "Conversations · Voice · AI")
        self._ring_angles = [0.0,0.0,0.0]
        rng = random.Random(42)
        self._orb_parts = [
            {"angle":rng.uniform(0,360),"dfrac":rng.uniform(0.38,0.72),
             "speed":rng.uniform(0.18,0.55)*rng.choice([-1,1]),
             "size":rng.uniform(0.9,2.0),"alpha":rng.randint(40,160),
             "adir":rng.choice([-1,1])*rng.uniform(1.5,3.5)}
            for _ in range(14)]
        self._flares = [self._new_flare(rng) for _ in range(4)]

    @staticmethod
    def _new_flare(rng=None):
        r = rng or random
        return {"angle":r.uniform(0,360),"length":r.uniform(3,10),"cur_len":0.0,
                "speed":r.uniform(0.15,0.40),"alpha":0.0,"phase":"grow",
                "hold_t":0,"hold_max":r.randint(12,36)}

    def _tick(self):
        super()._tick()
        for i,ring in enumerate(_CHAT_RINGS):
            self._ring_angles[i] = (self._ring_angles[i]+ring["spd"]) % 360
        for p in self._orb_parts:
            p["angle"] = (p["angle"]+p["speed"]) % 360
            p["alpha"] += p["adir"]
            if p["alpha"]>175: p["alpha"],p["adir"]=175,-abs(p["adir"])
            elif p["alpha"]<30: p["alpha"],p["adir"]=30, abs(p["adir"])
        for f in self._flares:
            if f["phase"]=="grow":
                f["cur_len"]+=f["speed"]; f["alpha"]=min(130,f["alpha"]+8)
                if f["cur_len"]>=f["length"]: f["phase"]="hold"
            elif f["phase"]=="hold":
                f["hold_t"]+=1
                if f["hold_t"]>=f["hold_max"]: f["phase"]="fade"
            else:
                f["alpha"]-=5
                if f["alpha"]<=0: f.update(self._new_flare())

    def draw(self, painter, i, cx, cy, w, h, rad, alpha, is_hovered,
             inner_opacity, panel_progress, is_active, phase, hover_prog=None):
        self._maybe_tick(phase)
        a_mul  = alpha/255.0
        r_base = w*0.42
        pulse  = math.sin(phase*0.045)
        if hover_prog is None:
            hover_prog = max(0.0, min(1.0, (w/90.0-1.0)/0.2))

        painter.save()
        painter.setRenderHint(painter.Antialiasing)

        # Shared Idle Animation
        if inner_opacity > 0.02:
            self._draw_idle(painter, cx, cy, w, r_base, alpha, inner_opacity, phase)

        # Outer fog
        r_fog = r_base*1.55 + pulse*2.0
        fog = QRadialGradient(cx,cy,r_fog)
        fog.setColorAt(0.0, QColor(0,200,255,int(55*inner_opacity*a_mul)))
        fog.setColorAt(0.5, QColor(0,140,255,int(28*inner_opacity*a_mul)))
        fog.setColorAt(1.0, QColor(0,0,0,0))
        painter.setPen(Qt.NoPen); painter.setBrush(fog)
        painter.drawEllipse(QPointF(cx,cy), r_fog, r_fog)

        # Plasma core
        r_core = r_base*(0.88+pulse*0.04)
        core = QRadialGradient(cx,cy,r_core)
        core.setColorAt(0.00, QColor(200,240,255,int(240*inner_opacity*a_mul)))
        core.setColorAt(0.18, QColor(80, 210,255,int(220*inner_opacity*a_mul)))
        core.setColorAt(0.45, QColor(0,  160,255,int(160*inner_opacity*a_mul)))
        core.setColorAt(0.75, QColor(0,   80,200,int( 70*inner_opacity*a_mul)))
        core.setColorAt(1.00, QColor(0,0,0,0))
        painter.setBrush(core); painter.drawEllipse(QPointF(cx,cy),r_core,r_core)

        # Holographic arc-rings
        if inner_opacity > 0.02:
            for ri,ring in enumerate(_CHAT_RINGS):
                rr = w*ring["rf"]/2
                rect = QRectF(cx-rr,cy-rr,rr*2,rr*2)
                arc,gap = ring["arc"],ring["gap"]
                rot = self._ring_angles[ri]
                ba  = int(ring["a"]*inner_opacity*a_mul)
                ga  = int(ba*0.35)
                angle=0.0
                while angle < 360.0:
                    st = (rot+angle)%360.0; dl=min(arc,360.0-angle)
                    gpen=QPen(QColor(ring["r"],ring["g"],ring["b"],ga))
                    gpen.setWidthF(ring["w"]*3); gpen.setCapStyle(Qt.RoundCap)
                    painter.setPen(gpen); painter.setBrush(Qt.NoBrush)
                    painter.drawArc(rect,int(st*16),int(dl*16))
                    cpen=QPen(QColor(ring["r"],ring["g"],ring["b"],ba))
                    cpen.setWidthF(ring["w"]); cpen.setCapStyle(Qt.RoundCap)
                    painter.setPen(cpen); painter.drawArc(rect,int(st*16),int(dl*16))
                    angle += arc+gap

        # Ambient particles
        if inner_opacity > 0.02:
            painter.setPen(Qt.NoPen)
            for p in self._orb_parts:
                rad_p=math.radians(p["angle"])
                dist=(w/2)*p["dfrac"]
                pt=QPointF(cx+dist*math.cos(rad_p), cy+dist*math.sin(rad_p))
                pa=int(p["alpha"]*inner_opacity*a_mul)
                painter.setBrush(QColor(0,200,255,int(pa*0.30)))
                painter.drawEllipse(pt, p["size"]*2.2, p["size"]*2.2)
                painter.setBrush(QColor(180,240,255,pa))
                painter.drawEllipse(pt, p["size"], p["size"])

        # Flare spikes
        if inner_opacity > 0.02:
            limb=r_base*0.52
            for f in self._flares:
                fa=int(f["alpha"]*inner_opacity*a_mul)
                if fa<=0: continue
                fr=math.radians(f["angle"])
                x0=cx+limb*math.cos(fr); y0=cy+limb*math.sin(fr)
                x1=cx+(limb+f["cur_len"])*math.cos(fr)
                y1=cy+(limb+f["cur_len"])*math.sin(fr)
                gpen=QPen(QColor(0,220,255,int(fa*0.30)))
                gpen.setWidthF(2.0); gpen.setCapStyle(Qt.RoundCap)
                painter.setPen(gpen)
                painter.drawLine(QPointF(x0,y0),QPointF(x1,y1))
                cpen=QPen(QColor(200,240,255,fa))
                cpen.setWidthF(0.7); cpen.setCapStyle(Qt.RoundCap)
                painter.setPen(cpen)
                painter.drawLine(QPointF(x0,y0),QPointF(x1,y1))

        # Shell border
        pen_a = int((80+120*hover_prog+70*panel_progress)*a_mul) if not is_active \
                else int((80+70*panel_progress)*a_mul)
        sp=QPen(QColor(0,210,255,pen_a)); sp.setWidthF(1.5+hover_prog)
        painter.setBrush(Qt.NoBrush); painter.setPen(sp)
        painter.drawRoundedRect(QRectF(cx-w/2,cy-h/2,w,h), rad, rad)

        # Cinematic hover (shared engine)
        self._draw_hover(painter, cx, cy, w, h, r_base, alpha,
                         inner_opacity, panel_progress, hover_prog, phase)

        # Chat bubble icon
        if inner_opacity > 0.05:
            ia=int(255*inner_opacity*a_mul); is_=w*0.30
            ix=cx-is_/2; iy=cy-is_*0.48
            painter.setPen(QPen(QColor(255,255,255,ia),1.6,
                                Qt.SolidLine,Qt.RoundCap,Qt.RoundJoin))
            painter.setBrush(Qt.NoBrush)
            path=QPainterPath()
            path.addRoundedRect(ix,iy,is_,is_*0.70,4,4)
            tp=QPainterPath()
            tp.moveTo(ix+is_*0.25,iy+is_*0.70)
            tp.lineTo(ix+is_*0.12,iy+is_*0.92)
            tp.lineTo(ix+is_*0.44,iy+is_*0.70)
            path.addPath(tp); painter.drawPath(path)
            painter.setBrush(QColor(255,255,255,ia)); painter.setPen(Qt.NoPen)
            dy_=iy+is_*0.35
            painter.drawEllipse(QPointF(ix+is_*0.33,dy_),1.4,1.4)
            painter.drawEllipse(QPointF(ix+is_*0.67,dy_),1.4,1.4)

        painter.restore()


# ─────────────────────────────────────────────────────────────────────────────
# MEMORY PLANET  — holographic purple orb with neural graph core
# ─────────────────────────────────────────────────────────────────────────────
_MEMORY_RINGS = [
    dict(rf=0.50, arc=55, gap=20, spd= 0.55, r=180, g=50,  b=255, w=1.2, a=200),
    dict(rf=0.64, arc=40, gap=35, spd=-0.38, r=130, g=50,  b=255, w=0.9, a=160),
    dict(rf=0.80, arc=28, gap=50, spd= 0.22, r=220, g=180, b=255, w=0.7, a=110),
]

class MemoryPlanet(BasePlanet):
    def __init__(self):
        super().__init__("MEMORY", QColor(180,50,255), "Neural graph · personalized knowledge")
        self._ring_angles = [0.0,0.0,0.0]
        rng = random.Random(43)
        self._orb_parts = [
            {"angle":rng.uniform(0,360),"dfrac":rng.uniform(0.38,0.72),
             "speed":rng.uniform(0.18,0.55)*rng.choice([-1,1]),
             "size":rng.uniform(0.9,2.0),"alpha":rng.randint(40,160),
             "adir":rng.choice([-1,1])*rng.uniform(1.5,3.5)}
            for _ in range(14)]
        self._flares = [self._new_flare(rng) for _ in range(4)]

    @staticmethod
    def _new_flare(rng=None):
        r = rng or random
        return {"angle":r.uniform(0,360),"length":r.uniform(3,10),"cur_len":0.0,
                "speed":r.uniform(0.15,0.40),"alpha":0.0,"phase":"grow",
                "hold_t":0,"hold_max":r.randint(12,36)}

    def _tick(self):
        super()._tick()
        for i,ring in enumerate(_MEMORY_RINGS):
            self._ring_angles[i] = (self._ring_angles[i]+ring["spd"]) % 360
        for p in self._orb_parts:
            p["angle"] = (p["angle"]+p["speed"]) % 360
            p["alpha"] += p["adir"]
            if p["alpha"]>175: p["alpha"],p["adir"]=175,-abs(p["adir"])
            elif p["alpha"]<30: p["alpha"],p["adir"]=30, abs(p["adir"])
        for f in self._flares:
            if f["phase"]=="grow":
                f["cur_len"]+=f["speed"]; f["alpha"]=min(130,f["alpha"]+8)
                if f["cur_len"]>=f["length"]: f["phase"]="hold"
            elif f["phase"]=="hold":
                f["hold_t"]+=1
                if f["hold_t"]>=f["hold_max"]: f["phase"]="fade"
            else:
                f["alpha"]-=5
                if f["alpha"]<=0: f.update(self._new_flare())

    def draw(self, painter, i, cx, cy, w, h, rad, alpha, is_hovered,
             inner_opacity, panel_progress, is_active, phase, hover_prog=None):
        self._maybe_tick(phase)
        a_mul  = alpha/255.0
        r_base = w*0.42
        pulse  = math.sin(phase*0.045)
        if hover_prog is None:
            hover_prog = max(0.0, min(1.0, (w/90.0-1.0)/0.2))

        painter.save()
        painter.setRenderHint(painter.Antialiasing)

        # Shared Idle Animation
        if inner_opacity > 0.02:
            self._draw_idle(painter, cx, cy, w, r_base, alpha, inner_opacity, phase)

        # Outer purple fog
        r_fog = r_base*1.55 + pulse*2.0
        fog = QRadialGradient(cx,cy,r_fog)
        fog.setColorAt(0.0, QColor(180,50,255,int(55*inner_opacity*a_mul)))
        fog.setColorAt(0.5, QColor(130,50,255,int(28*inner_opacity*a_mul)))
        fog.setColorAt(1.0, QColor(0,0,0,0))
        painter.setPen(Qt.NoPen); painter.setBrush(fog)
        painter.drawEllipse(QPointF(cx,cy), r_fog, r_fog)

        # Plasma core
        r_core = r_base*(0.88+pulse*0.04)
        core = QRadialGradient(cx,cy,r_core)
        core.setColorAt(0.00, QColor(230,180,255,int(240*inner_opacity*a_mul)))
        core.setColorAt(0.18, QColor(180,80,255,int(220*inner_opacity*a_mul)))
        core.setColorAt(0.45, QColor(130,50,255,int(160*inner_opacity*a_mul)))
        core.setColorAt(0.75, QColor(80,10,200,int(70*inner_opacity*a_mul)))
        core.setColorAt(1.00, QColor(0,0,0,0))
        painter.setBrush(core); painter.drawEllipse(QPointF(cx,cy),r_core,r_core)

        # Holographic arc-rings
        if inner_opacity > 0.02:
            for ri,ring in enumerate(_MEMORY_RINGS):
                rr = w*ring["rf"]/2
                rect = QRectF(cx-rr,cy-rr,rr*2,rr*2)
                arc,gap = ring["arc"],ring["gap"]
                rot = self._ring_angles[ri]
                ba  = int(ring["a"]*inner_opacity*a_mul)
                ga  = int(ba*0.35)
                angle=0.0
                while angle < 360.0:
                    st = (rot+angle)%360.0; dl=min(arc,360.0-angle)
                    gpen=QPen(QColor(ring["r"],ring["g"],ring["b"],ga))
                    gpen.setWidthF(ring["w"]*3); gpen.setCapStyle(Qt.RoundCap)
                    painter.setPen(gpen); painter.setBrush(Qt.NoBrush)
                    painter.drawArc(rect,int(st*16),int(dl*16))
                    cpen=QPen(QColor(ring["r"],ring["g"],ring["b"],ba))
                    cpen.setWidthF(ring["w"]); cpen.setCapStyle(Qt.RoundCap)
                    painter.setPen(cpen); painter.drawArc(rect,int(st*16),int(dl*16))
                    angle += arc+gap

        # Ambient particles
        if inner_opacity > 0.02:
            painter.setPen(Qt.NoPen)
            for p in self._orb_parts:
                rad_p=math.radians(p["angle"])
                dist=(w/2)*p["dfrac"]
                pt=QPointF(cx+dist*math.cos(rad_p), cy+dist*math.sin(rad_p))
                pa=int(p["alpha"]*inner_opacity*a_mul)
                painter.setBrush(QColor(180,50,255,int(pa*0.30)))
                painter.drawEllipse(pt, p["size"]*2.2, p["size"]*2.2)
                painter.setBrush(QColor(230,180,255,pa))
                painter.drawEllipse(pt, p["size"], p["size"])

        # Flare spikes
        if inner_opacity > 0.02:
            limb=r_base*0.52
            for f in self._flares:
                fa=int(f["alpha"]*inner_opacity*a_mul)
                if fa<=0: continue
                fr=math.radians(f["angle"])
                x0=cx+limb*math.cos(fr); y0=cy+limb*math.sin(fr)
                x1=cx+(limb+f["cur_len"])*math.cos(fr)
                y1=cy+(limb+f["cur_len"])*math.sin(fr)
                gpen=QPen(QColor(180,50,255,int(fa*0.30)))
                gpen.setWidthF(2.0); gpen.setCapStyle(Qt.RoundCap)
                painter.setPen(gpen)
                painter.drawLine(QPointF(x0,y0),QPointF(x1,y1))
                cpen=QPen(QColor(230,180,255,fa))
                cpen.setWidthF(0.7); cpen.setCapStyle(Qt.RoundCap)
                painter.setPen(cpen)
                painter.drawLine(QPointF(x0,y0),QPointF(x1,y1))

        # Shell border
        pen_a = int((80+120*hover_prog+70*panel_progress)*a_mul) if not is_active \
                else int((80+70*panel_progress)*a_mul)
        sp=QPen(QColor(180,50,255,pen_a)); sp.setWidthF(1.5+hover_prog)
        painter.setBrush(Qt.NoBrush); painter.setPen(sp)
        painter.drawRoundedRect(QRectF(cx-w/2,cy-h/2,w,h), rad, rad)

        # Cinematic hover (shared engine)
        self._draw_hover(painter, cx, cy, w, h, r_base, alpha,
                         inner_opacity, panel_progress, hover_prog, phase)

        # Neural graph icon
        if inner_opacity > 0.05:
            ia=int(255*inner_opacity*a_mul); is_=w*0.30
            ix=cx; iy=cy
            
            pts = [QPointF(ix, iy)]  # Center node
            
            r_graph = is_ * 0.45
            for n_idx in range(6):
                ang = math.radians(n_idx * 60 + phase * 0.5)
                pts.append(QPointF(ix + r_graph * math.cos(ang), iy + r_graph * math.sin(ang)))
                
            # Draw connecting lines
            painter.setPen(QPen(QColor(255,255,255,int(ia*0.65)), 0.9, Qt.SolidLine, Qt.RoundCap))
            for n_idx in range(1, 7):
                painter.drawLine(pts[0], pts[n_idx])
                next_idx = n_idx + 1 if n_idx < 6 else 1
                painter.drawLine(pts[n_idx], pts[next_idx])
                
            # Draw node circles
            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor(255,255,255,ia))
            painter.drawEllipse(pts[0], 2.8, 2.8)
            painter.setBrush(QColor(230,180,255,ia))
            for pt in pts[1:]:
                painter.drawEllipse(pt, 1.8, 1.8)

        painter.restore()


# ─────────────────────────────────────────────────────────────────────────────
# FILES PLANET  — holographic cyan/blue orb with folder core
# ─────────────────────────────────────────────────────────────────────────────
_FILES_RINGS = [
    dict(rf=0.50, arc=55, gap=20, spd= 0.55, r=0,   g=220, b=170, w=1.2, a=200),
    dict(rf=0.64, arc=40, gap=35, spd=-0.38, r=0,   g=160, b=130, w=0.9, a=160),
    dict(rf=0.80, arc=28, gap=50, spd= 0.22, r=100, g=255, b=220, w=0.7, a=110),
]

class FilesPlanet(BasePlanet):
    def __init__(self):
        super().__init__("FILES", QColor(0,220,170), "Search, preview, manage all files")
        self._ring_angles = [0.0,0.0,0.0]
        rng = random.Random(44)
        self._orb_parts = [
            {"angle":rng.uniform(0,360),"dfrac":rng.uniform(0.38,0.72),
             "speed":rng.uniform(0.18,0.55)*rng.choice([-1,1]),
             "size":rng.uniform(0.9,2.0),"alpha":rng.randint(40,160),
             "adir":rng.choice([-1,1])*rng.uniform(1.5,3.5)}
            for _ in range(14)]
        self._flares = [self._new_flare(rng) for _ in range(4)]

    @staticmethod
    def _new_flare(rng=None):
        r = rng or random
        return {"angle":r.uniform(0,360),"length":r.uniform(3,10),"cur_len":0.0,
                "speed":r.uniform(0.15,0.40),"alpha":0.0,"phase":"grow",
                "hold_t":0,"hold_max":r.randint(12,36)}

    def _tick(self):
        super()._tick()
        for i,ring in enumerate(_FILES_RINGS):
            self._ring_angles[i] = (self._ring_angles[i]+ring["spd"]) % 360
        for p in self._orb_parts:
            p["angle"] = (p["angle"]+p["speed"]) % 360
            p["alpha"] += p["adir"]
            if p["alpha"]>175: p["alpha"],p["adir"]=175,-abs(p["adir"])
            elif p["alpha"]<30: p["alpha"],p["adir"]=30, abs(p["adir"])
        for f in self._flares:
            if f["phase"]=="grow":
                f["cur_len"]+=f["speed"]; f["alpha"]=min(130,f["alpha"]+8)
                if f["cur_len"]>=f["length"]: f["phase"]="hold"
            elif f["phase"]=="hold":
                f["hold_t"]+=1
                if f["hold_t"]>=f["hold_max"]: f["phase"]="fade"
            else:
                f["alpha"]-=5
                if f["alpha"]<=0: f.update(self._new_flare())

    def draw(self, painter, i, cx, cy, w, h, rad, alpha, is_hovered,
             inner_opacity, panel_progress, is_active, phase, hover_prog=None):
        self._maybe_tick(phase)
        a_mul  = alpha/255.0
        r_base = w*0.42
        pulse  = math.sin(phase*0.045)
        if hover_prog is None:
            hover_prog = max(0.0, min(1.0, (w/90.0-1.0)/0.2))

        painter.save()
        painter.setRenderHint(painter.Antialiasing)

        # Shared Idle Animation
        if inner_opacity > 0.02:
            self._draw_idle(painter, cx, cy, w, r_base, alpha, inner_opacity, phase)

        # Outer emerald fog
        r_fog = r_base*1.55 + pulse*2.0
        fog = QRadialGradient(cx,cy,r_fog)
        fog.setColorAt(0.0, QColor(0,220,170,int(55*inner_opacity*a_mul)))
        fog.setColorAt(0.5, QColor(0,160,130,int(28*inner_opacity*a_mul)))
        fog.setColorAt(1.0, QColor(0,0,0,0))
        painter.setPen(Qt.NoPen); painter.setBrush(fog)
        painter.drawEllipse(QPointF(cx,cy), r_fog, r_fog)

        # Plasma core
        r_core = r_base*(0.88+pulse*0.04)
        core = QRadialGradient(cx,cy,r_core)
        core.setColorAt(0.00, QColor(200,255,235,int(240*inner_opacity*a_mul)))
        core.setColorAt(0.18, QColor(0,220,170,int(220*inner_opacity*a_mul)))
        core.setColorAt(0.45, QColor(0,160,130,int(160*inner_opacity*a_mul)))
        core.setColorAt(0.75, QColor(0,90,70,int(70*inner_opacity*a_mul)))
        core.setColorAt(1.00, QColor(0,0,0,0))
        painter.setBrush(core); painter.drawEllipse(QPointF(cx,cy),r_core,r_core)

        # Holographic arc-rings
        if inner_opacity > 0.02:
            for ri,ring in enumerate(_FILES_RINGS):
                rr = w*ring["rf"]/2
                rect = QRectF(cx-rr,cy-rr,rr*2,rr*2)
                arc,gap = ring["arc"],ring["gap"]
                rot = self._ring_angles[ri]
                ba  = int(ring["a"]*inner_opacity*a_mul)
                ga  = int(ba*0.35)
                angle=0.0
                while angle < 360.0:
                    st = (rot+angle)%360.0; dl=min(arc,360.0-angle)
                    gpen=QPen(QColor(ring["r"],ring["g"],ring["b"],ga))
                    gpen.setWidthF(ring["w"]*3); gpen.setCapStyle(Qt.RoundCap)
                    painter.setPen(gpen); painter.setBrush(Qt.NoBrush)
                    painter.drawArc(rect,int(st*16),int(dl*16))
                    cpen=QPen(QColor(ring["r"],ring["g"],ring["b"],ba))
                    cpen.setWidthF(ring["w"]); cpen.setCapStyle(Qt.RoundCap)
                    painter.setPen(cpen); painter.drawArc(rect,int(st*16),int(dl*16))
                    angle += arc+gap

        # Ambient particles
        if inner_opacity > 0.02:
            painter.setPen(Qt.NoPen)
            for p in self._orb_parts:
                rad_p=math.radians(p["angle"])
                dist=(w/2)*p["dfrac"]
                pt=QPointF(cx+dist*math.cos(rad_p), cy+dist*math.sin(rad_p))
                pa=int(p["alpha"]*inner_opacity*a_mul)
                painter.setBrush(QColor(0,220,170,int(pa*0.30)))
                painter.drawEllipse(pt, p["size"]*2.2, p["size"]*2.2)
                painter.setBrush(QColor(200,255,235,pa))
                painter.drawEllipse(pt, p["size"], p["size"])

        # Flare spikes
        if inner_opacity > 0.02:
            limb=r_base*0.52
            for f in self._flares:
                fa=int(f["alpha"]*inner_opacity*a_mul)
                if fa<=0: continue
                fr=math.radians(f["angle"])
                x0=cx+limb*math.cos(fr); y0=cy+limb*math.sin(fr)
                x1=cx+(limb+f["cur_len"])*math.cos(fr)
                y1=cy+(limb+f["cur_len"])*math.sin(fr)
                gpen=QPen(QColor(0,220,170,int(fa*0.30)))
                gpen.setWidthF(2.0); gpen.setCapStyle(Qt.RoundCap)
                painter.setPen(gpen)
                painter.drawLine(QPointF(x0,y0),QPointF(x1,y1))
                cpen=QPen(QColor(200,255,235,fa))
                cpen.setWidthF(0.7); cpen.setCapStyle(Qt.RoundCap)
                painter.setPen(cpen)
                painter.drawLine(QPointF(x0,y0),QPointF(x1,y1))

        # Shell border
        pen_a = int((80+120*hover_prog+70*panel_progress)*a_mul) if not is_active \
                else int((80+70*panel_progress)*a_mul)
        sp=QPen(QColor(0,220,170,pen_a)); sp.setWidthF(1.5+hover_prog)
        painter.setBrush(Qt.NoBrush); painter.setPen(sp)
        painter.drawRoundedRect(QRectF(cx-w/2,cy-h/2,w,h), rad, rad)

        # Cinematic hover (shared engine)
        self._draw_hover(painter, cx, cy, w, h, r_base, alpha,
                         inner_opacity, panel_progress, hover_prog, phase)

        # Folder icon
        if inner_opacity > 0.05:
            ia=int(255*inner_opacity*a_mul); is_=w*0.28
            ix=cx-is_/2; iy=cy-is_/2
            painter.setPen(QPen(QColor(255,255,255,ia), 1.6, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
            painter.setBrush(Qt.NoBrush)
            
            path=QPainterPath()
            path.moveTo(ix, iy + is_*0.15)
            path.lineTo(ix + is_*0.35, iy + is_*0.15)
            path.lineTo(ix + is_*0.48, iy + is_*0.3)
            path.lineTo(ix + is_, iy + is_*0.3)
            path.lineTo(ix + is_, iy + is_*0.85)
            path.lineTo(ix, iy + is_*0.85)
            path.closeSubpath()
            painter.drawPath(path)
            
            # Subtle file lines inside
            painter.setPen(QPen(QColor(255,255,255,int(ia*0.5)), 1.0))
            painter.drawLine(QPointF(ix + is_*0.2, iy + is_*0.48), QPointF(ix + is_*0.8, iy + is_*0.48))
            painter.drawLine(QPointF(ix + is_*0.2, iy + is_*0.62), QPointF(ix + is_*0.65, iy + is_*0.62))

        painter.restore()


# ─────────────────────────────────────────────────────────────────────────────
# SYSTEM PLANET — holographic orange/red orb with heartbeat core
# ─────────────────────────────────────────────────────────────────────────────
_SYSTEM_RINGS = [
    dict(rf=0.50, arc=55, gap=20, spd= 0.55, r=255, g=60,  b=0,   w=1.2, a=200),
    dict(rf=0.64, arc=40, gap=35, spd=-0.38, r=255, g=120, b=0,   w=0.9, a=160),
    dict(rf=0.80, arc=28, gap=50, spd= 0.22, r=255, g=180, b=100, w=0.7, a=110),
]

class SystemPlanet(BasePlanet):
    def __init__(self):
        super().__init__("SYSTEM", QColor(255,60,0), "CPU, RAM, disk, processes, live monitoring")
        self._ring_angles = [0.0,0.0,0.0]
        rng = random.Random(45)
        self._orb_parts = [
            {"angle":rng.uniform(0,360),"dfrac":rng.uniform(0.38,0.72),
             "speed":rng.uniform(0.18,0.55)*rng.choice([-1,1]),
             "size":rng.uniform(0.9,2.0),"alpha":rng.randint(40,160),
             "adir":rng.choice([-1,1])*rng.uniform(1.5,3.5)}
            for _ in range(14)]
        self._flares = [self._new_flare(rng) for _ in range(4)]

    @staticmethod
    def _new_flare(rng=None):
        r = rng or random
        return {"angle":r.uniform(0,360),"length":r.uniform(3,10),"cur_len":0.0,
                "speed":r.uniform(0.15,0.40),"alpha":0.0,"phase":"grow",
                "hold_t":0,"hold_max":r.randint(12,36)}

    def _tick(self):
        super()._tick()
        for i,ring in enumerate(_SYSTEM_RINGS):
            self._ring_angles[i] = (self._ring_angles[i]+ring["spd"]) % 360
        for p in self._orb_parts:
            p["angle"] = (p["angle"]+p["speed"]) % 360
            p["alpha"] += p["adir"]
            if p["alpha"]>175: p["alpha"],p["adir"]=175,-abs(p["adir"])
            elif p["alpha"]<30: p["alpha"],p["adir"]=30, abs(p["adir"])
        for f in self._flares:
            if f["phase"]=="grow":
                f["cur_len"]+=f["speed"]; f["alpha"]=min(130,f["alpha"]+8)
                if f["cur_len"]>=f["length"]: f["phase"]="hold"
            elif f["phase"]=="hold":
                f["hold_t"]+=1
                if f["hold_t"]>=f["hold_max"]: f["phase"]="fade"
            else:
                f["alpha"]-=5
                if f["alpha"]<=0: f.update(self._new_flare())

    def draw(self, painter, i, cx, cy, w, h, rad, alpha, is_hovered,
             inner_opacity, panel_progress, is_active, phase, hover_prog=None):
        self._maybe_tick(phase)
        a_mul  = alpha/255.0
        r_base = w*0.42
        pulse  = math.sin(phase*0.045)
        if hover_prog is None:
            hover_prog = max(0.0, min(1.0, (w/90.0-1.0)/0.2))

        painter.save()
        painter.setRenderHint(painter.Antialiasing)

        # Shared Idle Animation
        if inner_opacity > 0.02:
            self._draw_idle(painter, cx, cy, w, r_base, alpha, inner_opacity, phase)

        # Outer orange fog
        r_fog = r_base*1.55 + pulse*2.0
        fog = QRadialGradient(cx,cy,r_fog)
        fog.setColorAt(0.0, QColor(255,60,0,int(55*inner_opacity*a_mul)))
        fog.setColorAt(0.5, QColor(255,110,0,int(28*inner_opacity*a_mul)))
        fog.setColorAt(1.0, QColor(0,0,0,0))
        painter.setPen(Qt.NoPen); painter.setBrush(fog)
        painter.drawEllipse(QPointF(cx,cy), r_fog, r_fog)

        # Plasma core
        r_core = r_base*(0.88+pulse*0.04)
        core = QRadialGradient(cx,cy,r_core)
        core.setColorAt(0.00, QColor(255,200,150,int(240*inner_opacity*a_mul)))
        core.setColorAt(0.18, QColor(255,100,0,int(220*inner_opacity*a_mul)))
        core.setColorAt(0.45, QColor(220,50,0,int(160*inner_opacity*a_mul)))
        core.setColorAt(0.75, QColor(140,10,0,int(70*inner_opacity*a_mul)))
        core.setColorAt(1.00, QColor(0,0,0,0))
        painter.setBrush(core); painter.drawEllipse(QPointF(cx,cy),r_core,r_core)

        # Holographic arc-rings
        if inner_opacity > 0.02:
            for ri,ring in enumerate(_SYSTEM_RINGS):
                rr = w*ring["rf"]/2
                rect = QRectF(cx-rr,cy-rr,rr*2,rr*2)
                arc,gap = ring["arc"],ring["gap"]
                rot = self._ring_angles[ri]
                ba  = int(ring["a"]*inner_opacity*a_mul)
                ga  = int(ba*0.35)
                angle=0.0
                while angle < 360.0:
                    st = (rot+angle)%360.0; dl=min(arc,360.0-angle)
                    gpen=QPen(QColor(ring["r"],ring["g"],ring["b"],ga))
                    gpen.setWidthF(ring["w"]*3); gpen.setCapStyle(Qt.RoundCap)
                    painter.setPen(gpen); painter.setBrush(Qt.NoBrush)
                    painter.drawArc(rect,int(st*16),int(dl*16))
                    cpen=QPen(QColor(ring["r"],ring["g"],ring["b"],ba))
                    cpen.setWidthF(ring["w"]); cpen.setCapStyle(Qt.RoundCap)
                    painter.setPen(cpen); painter.drawArc(rect,int(st*16),int(dl*16))
                    angle += arc+gap

        # Ambient particles
        if inner_opacity > 0.02:
            painter.setPen(Qt.NoPen)
            for p in self._orb_parts:
                rad_p=math.radians(p["angle"])
                dist=(w/2)*p["dfrac"]
                pt=QPointF(cx+dist*math.cos(rad_p), cy+dist*math.sin(rad_p))
                pa=int(p["alpha"]*inner_opacity*a_mul)
                painter.setBrush(QColor(255,60,0,int(pa*0.30)))
                painter.drawEllipse(pt, p["size"]*2.2, p["size"]*2.2)
                painter.setBrush(QColor(255,200,150,pa))
                painter.drawEllipse(pt, p["size"], p["size"])

        # Flare spikes
        if inner_opacity > 0.02:
            limb=r_base*0.52
            for f in self._flares:
                fa=int(f["alpha"]*inner_opacity*a_mul)
                if fa<=0: continue
                fr=math.radians(f["angle"])
                x0=cx+limb*math.cos(fr); y0=cy+limb*math.sin(fr)
                x1=cx+(limb+f["cur_len"])*math.cos(fr)
                y1=cy+(limb+f["cur_len"])*math.sin(fr)
                gpen=QPen(QColor(255,60,0,int(fa*0.30)))
                gpen.setWidthF(2.0); gpen.setCapStyle(Qt.RoundCap)
                painter.setPen(gpen)
                painter.drawLine(QPointF(x0,y0),QPointF(x1,y1))
                cpen=QPen(QColor(255,200,150,fa))
                cpen.setWidthF(0.7); cpen.setCapStyle(Qt.RoundCap)
                painter.setPen(cpen)
                painter.drawLine(QPointF(x0,y0),QPointF(x1,y1))

        # Shell border
        pen_a = int((80+120*hover_prog+70*panel_progress)*a_mul) if not is_active \
                else int((80+70*panel_progress)*a_mul)
        sp=QPen(QColor(255,60,0,pen_a)); sp.setWidthF(1.5+hover_prog)
        painter.setBrush(Qt.NoBrush); painter.setPen(sp)
        painter.drawRoundedRect(QRectF(cx-w/2,cy-h/2,w,h), rad, rad)

        # Cinematic hover (shared engine)
        self._draw_hover(painter, cx, cy, w, h, r_base, alpha,
                         inner_opacity, panel_progress, hover_prog, phase)

        # Heartbeat pulse icon
        if inner_opacity > 0.05:
            ia=int(255*inner_opacity*a_mul); is_=w*0.30
            ix=cx-is_/2; iy=cy
            painter.setPen(QPen(QColor(255,255,255,ia), 1.6, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
            painter.setBrush(Qt.NoBrush)
            
            path=QPainterPath()
            path.moveTo(ix, iy)
            path.lineTo(ix + is_*0.22, iy)
            path.lineTo(ix + is_*0.32, iy - is_*0.32)
            path.lineTo(ix + is_*0.42, iy + is_*0.38)
            path.lineTo(ix + is_*0.52, iy - is_*0.16)
            path.lineTo(ix + is_*0.62, iy + is_*0.12)
            path.lineTo(ix + is_*0.72, iy)
            path.lineTo(ix + is_, iy)
            painter.drawPath(path)

        painter.restore()


# ─────────────────────────────────────────────────────────────────────────────
# REGISTRY
# ─────────────────────────────────────────────────────────────────────────────
def get_planets():
    return [
        GenericPlanet("Planet 0", QColor(255,140, 40), "App · Tools · Settings"),
        GenericPlanet("Planet 1", QColor(255,140, 40), "App · Tools · Settings"),
        MemoryPlanet(),
        ChatPlanet(),
        FilesPlanet(),
        SystemPlanet(),
        GenericPlanet("Planet 6", QColor(255,140, 40), "App · Tools · Settings"),
        GenericPlanet("Planet 7", QColor(255,140, 40), "App · Tools · Settings"),
    ]

