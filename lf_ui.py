"""LyricsFloat UI: lyric overlay, glass hub, launch animation, settings window."""
import sys, time, math, random, ctypes

from PySide6.QtCore import Qt, QPoint, QPointF, QRect, QRectF, QTimer, Signal
from PySide6.QtGui import (QBrush, QColor, QFont, QFontMetrics, QGradient, QGuiApplication, QIcon, QLinearGradient,
                           QPainter, QPainterPath, QPen, QPixmap, QPolygonF, QRadialGradient, QRegion)
from PySide6.QtWidgets import (QCheckBox, QColorDialog, QComboBox, QDoubleSpinBox, QFontComboBox, QGridLayout,
                               QHBoxLayout, QLabel, QMenu, QPushButton, QScrollArea, QSlider, QTabWidget,
                               QVBoxLayout, QWidget)

from lf_sprite import SPRITES
from lf_core import (APP, IS_WIN, STYLES, VIS_STYLES, WEIGHTS, ease, eo_back, eo_expo, fmt, fps_ms, np)

FALLBACK = ["Segoe UI", "Helvetica Neue", "Arial"]
FLAGS = 0x84 | 0x1000  # AlignCenter | TextWordWrap
W_ = QFont.Weight
WEIGHT_MAP = {"light": W_.Light, "regular": W_.Normal, "medium": W_.Medium, "demibold": W_.DemiBold,
              "bold": W_.Bold, "black": W_.Black}
WIN_FLAGS = (Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint |
             Qt.WindowType.WindowDoesNotAcceptFocus)

MENU_QSS = ("QMenu{background:#1d2029;color:#eee;border:1px solid #3a3f4d;padding:4px}"
            "QMenu::item{padding:5px 22px}QMenu::item:selected{background:#3a4258}"
            "QMenu::separator{height:1px;background:#3a3f4d;margin:4px 6px}")


def mk_font(family, px, weight=W_.Normal):
    f = QFont()
    f.setFamilies([family] + FALLBACK)
    f.setPixelSize(px)
    f.setWeight(weight)
    return f


def qc(hexstr, alpha=255):
    c = QColor(hexstr)
    c.setAlpha(max(0, min(255, int(alpha))))
    return c


def text_brush(cfg, r, now, a):
    """Solid / gradient / animated-gradient brush for a text rect."""
    mode = cfg["color_mode"]
    a255 = 255 * a
    c1 = qc(cfg["color1"], a255)
    if mode == "solid":
        return QBrush(c1)
    ang = math.radians(cfg["grad_angle"])
    dx, dy = math.cos(ang), math.sin(ang)
    c = r.center()
    if mode == "gradient":
        L = (abs(r.width() * dx) + abs(r.height() * dy)) / 2 or 1.0
        g = QLinearGradient(c.x() - dx * L, c.y() - dy * L, c.x() + dx * L, c.y() + dy * L)
    else:
        G = max(r.width() * 1.1, 420.0)
        shift = (now * cfg["grad_speed"] * 28.0) % (2 * G)   # reflect pattern period = 2G -> seamless loop
        sx, sy = c.x() - dx * G / 2 + dx * shift, c.y() - dy * G / 2 + dy * shift
        g = QLinearGradient(sx, sy, sx + dx * G, sy + dy * G)
        g.setSpread(QGradient.Spread.ReflectSpread)
    g.setColorAt(0, c1)
    g.setColorAt(0.5, qc(cfg["color2"], a255))
    g.setColorAt(1, qc(cfg["color3"], a255))
    return QBrush(g)


def draw_shine(p, R, rad, u):
    """Diagonal specular sweep across rounded rect R. u: 0..1."""
    if not 0.0 < u < 1.0:
        return
    p.save()
    path = QPainterPath()
    path.addRoundedRect(R, rad, rad)
    p.setClipPath(path)
    cx = R.left() - R.width() * 0.3 + ease(u) * R.width() * 1.6
    p.translate(cx, R.center().y())
    p.rotate(18)
    bw = R.width() * 0.22
    g = QLinearGradient(-bw / 2, 0, bw / 2, 0)
    g.setColorAt(0, QColor(255, 255, 255, 0))
    g.setColorAt(0.5, QColor(255, 255, 255, int(150 * math.sin(u * math.pi) ** 0.5)))
    g.setColorAt(1, QColor(255, 255, 255, 0))
    p.fillRect(QRectF(-bw / 2, -R.height(), bw, R.height() * 2), g)
    p.restore()


def blur_cover(img, w, h, strength):
    """Cheap blur: crop-to-fill, shrink hard, then smooth 2x upscales."""
    fill = img.scaled(w, h, Qt.AspectRatioMode.KeepAspectRatioByExpanding, Qt.TransformationMode.SmoothTransformation)
    fill = fill.copy((fill.width() - w) // 2, (fill.height() - h) // 2, w, h)
    f = 4 + strength * 3
    cur = fill.scaled(max(2, w // f), max(2, h // f), Qt.AspectRatioMode.IgnoreAspectRatio,
                      Qt.TransformationMode.SmoothTransformation)
    while cur.width() < w or cur.height() < h:
        cur = cur.scaled(min(w, cur.width() * 2), min(h, cur.height() * 2), Qt.AspectRatioMode.IgnoreAspectRatio,
                         Qt.TransformationMode.SmoothTransformation)
    return QPixmap.fromImage(cur)


def set_blur(widget, on):
    """Windows backdrop blur behind the widget (undocumented but long-stable API)."""
    if not IS_WIN:
        return False
    try:
        from ctypes import wintypes

        class ACCENT(ctypes.Structure):
            _fields_ = [("state", ctypes.c_int), ("flags", ctypes.c_int),
                        ("color", ctypes.c_uint), ("anim", ctypes.c_int)]

        class WCA(ctypes.Structure):
            _fields_ = [("attr", ctypes.c_int), ("data", ctypes.c_void_p), ("size", ctypes.c_size_t)]

        fn = ctypes.windll.user32.SetWindowCompositionAttribute
        fn.argtypes = [wintypes.HWND, ctypes.POINTER(WCA)]
        fn.restype = wintypes.BOOL
        acc = ACCENT(3 if on else 0, 0, 0, 0)  # 3 = blur-behind (cheap, no drag lag)
        d = WCA(19, ctypes.cast(ctypes.pointer(acc), ctypes.c_void_p), ctypes.sizeof(acc))
        return bool(fn(int(widget.winId()), ctypes.byref(d))) and on
    except Exception:
        return False


def icon_pixmap(sz=64):
    pm = QPixmap(sz, sz)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    path = QPainterPath()
    path.addRoundedRect(QRectF(2, 2, sz - 4, sz - 4), sz * 0.26, sz * 0.26)
    g = QLinearGradient(0, 0, sz, sz)
    g.setColorAt(0, QColor("#3d4678"))
    g.setColorAt(1, QColor("#14161f"))
    p.fillPath(path, g)
    p.setPen(QPen(QColor(255, 255, 255, 150), max(1.0, sz / 40)))
    p.drawPath(path)
    f = QFont()
    f.setFamilies(FALLBACK)
    f.setPixelSize(int(sz * 0.42))
    f.setWeight(W_.Bold)
    p.setFont(f)
    tg = QLinearGradient(0, sz * 0.25, 0, sz * 0.75)
    tg.setColorAt(0, QColor("#ffffff"))
    tg.setColorAt(1, QColor("#8fd3ff"))
    p.setPen(QPen(QBrush(tg), 1))
    p.drawText(QRectF(0, 0, sz, sz), 0x84, "LF")
    p.end()
    return pm


def make_icon():
    return QIcon(icon_pixmap(64))


# --------------------------------------------------------------------------- lyric overlay
class Item:
    __slots__ = ("text", "cx", "cy", "alpha", "size", "fill")

    def __init__(self, text, cx, cy, alpha, size, fill=-1.0):
        self.text, self.cx, self.cy, self.alpha, self.size, self.fill = text, cx, cy, alpha, size, fill


class Overlay(QWidget):
    """Click-through, transparent, always-on-top layer. Only repaints regions that change."""

    def __init__(self, ctl):
        super().__init__(None, Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint |
                         Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.WindowTransparentForInput |
                         Qt.WindowType.WindowDoesNotAcceptFocus)
        self.ctl, self.cfg = ctl, ctl.cfg
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._ival = 0
        self._fonts, self._sizes = {}, {}
        self._geo = None
        self._xf, self._xf_moved = None, True
        self._items, self._rects = [], []
        self.cfg.changed.connect(self._cfg)
        self.reset()

    def _cfg(self, k):
        self._fonts.clear()
        self._sizes.clear()
        self._sig = None
        if k == "style":
            self.reset()

    def reset(self):
        self._idx, self._idx_t = -2, 0.0
        self._cur = self._prev = ""
        self._floaters = []
        self._sig = None

    def start(self):
        self._check_screen()
        self.show()
        self.ctl.player.raise_()
        self._ival = fps_ms(self.cfg)
        self._timer.start(self._ival)

    def stop(self):
        self._timer.stop()
        self.hide()
        self._items, self._rects, self._floaters = [], [], []

    # ---- monitor following (hub dragged to another screen)
    def follow(self):
        if self.isVisible() and self.cfg["hop_fx"]:
            self._xf, self._xf_moved = time.monotonic(), False
        else:
            self._check_screen()

    def _fade(self, now):
        if self._xf is None:
            return 1.0
        t = now - self._xf
        if t < 0.2:
            return 1 - ease(t / 0.2)
        if not self._xf_moved:
            self._xf_moved = True
            self._check_screen()
        u = (t - 0.2) / 0.35
        if u >= 1:
            self._xf = None
            return 1.0
        return ease(u)

    def _check_screen(self):
        scr = self.ctl.player.screen() or QGuiApplication.primaryScreen()
        g = scr.geometry()
        if g != self._geo:
            self._geo = g
            self.setGeometry(g.x(), g.y(), g.width(), g.height() - 1)
            self._sig = None
            self._items, self._rects = [], []

    # ---- text metrics (cached)
    def _font(self, size):
        f = self._fonts.get(size)
        if f is None:
            f = mk_font(self.cfg["font"], size, WEIGHT_MAP.get(self.cfg["weight"], W_.DemiBold))
            self._fonts[size] = f
        return f

    def _measure(self, text, size):
        k = (text, size)
        s = self._sizes.get(k)
        if s is None:
            if len(self._sizes) > 400:
                self._sizes.clear()
            r = QFontMetrics(self._font(size)).boundingRect(
                QRect(0, 0, int(self.width() * self.cfg["max_w"] / 100), 4000), FLAGS, text)
            s = (r.width() + 8, r.height() + 4)
            self._sizes[k] = s
        return s

    def _rect(self, it):
        w, h = self._measure(it.text, it.size)
        return QRectF(it.cx - w / 2, it.cy - h / 2, w, h)

    # ---- frame loop
    def _tick(self):
        cfg, ctl, now = self.cfg, self.ctl, time.monotonic()
        f = self._fade(now)
        idx = ctl.current_index()
        if idx != self._idx:
            self._prev, self._cur = self._cur, ctl.line_text(idx)
            self._idx, self._idx_t = idx, now
            if self._xf is None:
                self._check_screen()
            if cfg["style"] == "drift" and self._cur:
                self._spawn(now)
        items = getattr(self, "_s_" + cfg["style"])(now, idx)
        for it in items:
            it.alpha *= f
        anim = cfg["color_mode"] == "animated"
        sig = tuple((i.text, round(i.cx), round(i.cy), round(i.alpha * 50), i.size, round(i.fill * 100))
                    for i in items) + ((int(now * 30),) if anim else ())
        moving = (cfg["style"] in ("float", "drift", "karaoke") or now - self._idx_t < 0.8 or anim
                  or self._xf is not None)
        iv = fps_ms(cfg) if moving else 200
        if iv != self._ival:
            self._ival = iv
            self._timer.setInterval(iv)
        if sig == self._sig:
            return
        self._sig = sig
        new = [self._rect(i).adjusted(-34, -24, 34, 44).toAlignedRect() for i in items if i.alpha > 0.01]
        reg = QRegion()
        for r in self._rects + new:
            reg = reg.united(r)
        self._items, self._rects = items, new
        if not reg.isEmpty():
            self.update(reg)

    # ---- styles: each returns the Items to draw this frame
    def _yo(self):
        return self.cfg["y_off"] / 100.0

    def _s_float(self, now, idx):
        W, H, S = self.width(), self.height(), self.cfg["font_size"]
        a = ease((now - self._idx_t) / 0.6)
        cx = W / 2 + math.sin(now * 0.6) * 22
        cy = H * (0.72 + self._yo()) + math.sin(now * 0.8 + 1.3) * 12
        items = []
        if self._cur:
            items.append(Item(self._cur, cx, cy + (1 - a) * 26, a, S))
        if self._prev and a < 1:
            items.append(Item(self._prev, cx, cy - a * 30, 1 - a, S))
        return items

    def _s_karaoke(self, now, idx):
        ctl, W, H, S = self.ctl, self.width(), self.height(), self.cfg["font_size"]
        a = ease((now - self._idx_t) / 0.25)
        items = []
        if self._cur:
            t0 = ctl.times[idx]
            t1 = ctl.times[idx + 1] if idx + 1 < len(ctl.times) else t0 + 4
            fill = min(max((ctl.pb.pos() + ctl.offset - t0) / max(0.4, (t1 - t0) * 0.88), 0.0), 1.0)
            y = H * (0.8 + self._yo())
            items.append(Item(self._cur, W / 2, y, a, S, fill))
            nxt = ctl.line_text(idx + 1)
            if nxt and self.cfg["preview"]:
                sz = int(S * 0.7)
                y2 = y + self._measure(self._cur, S)[1] / 2 + self._measure(nxt, sz)[1] / 2 + 10
                items.append(Item(nxt, W / 2, y2, 0.5 * a, sz))
        return items

    def _s_stack(self, now, idx):
        ctl, W, H, S = self.ctl, self.width(), self.height(), self.cfg["font_size"]
        p = ease((now - self._idx_t) / 0.45)
        look = {-3: (0.0, 0.8), -2: (0.0, 0.8), -1: (0.3, 0.8), 0: (1.0, 1.0), 1: (0.3, 0.8),
                2: (0.15, 0.8), 3: (0.0, 0.8)}
        lh = S * 2.0
        items = []
        for r in (range(-2, 3) if self.cfg["preview"] else (0,)):
            text = ctl.line_text(idx + r)
            if not text:
                continue
            a0, s0 = look[r + 1]
            a1, s1 = look[r]
            alpha, scale = a0 + (a1 - a0) * p, s0 + (s1 - s0) * p
            if alpha > 0.01:
                items.append(Item(text, W / 2, H * (0.5 + self._yo()) + (r + (1 - p)) * lh, alpha,
                                  max(12, int(S * scale))))
        return items

    def _s_minimal(self, now, idx):
        W, H, S = self.width(), self.height(), self.cfg["font_size"]
        a = ease((now - self._idx_t) / 0.25)
        y = H * (0.88 + self._yo())
        items = []
        if self._cur:
            items.append(Item(self._cur, W / 2, y, a, S))
        if self._prev and a < 1:
            items.append(Item(self._prev, W / 2, y, 1 - a, S))
        return items

    def _spawn(self, now):
        ctl, W, H = self.ctl, self.width(), self.height()
        size = int(self.cfg["font_size"] * random.uniform(0.85, 1.2))
        w, _ = self._measure(self._cur, size)
        x = random.uniform(w / 2 + 30, max(w / 2 + 31, W - w / 2 - 30))
        y = random.uniform(H * 0.18, H * 0.82)
        t0 = ctl.times[self._idx]
        t1 = ctl.times[self._idx + 1] if self._idx + 1 < len(ctl.times) else t0 + 4
        life = min(max(t1 - t0 + 2.5, 4.0), 9.0)
        self._floaters.append([self._cur, x, y, random.uniform(-10, 10), -random.uniform(6, 16), now, life, size])
        del self._floaters[:-5]

    def _s_drift(self, now, idx):
        items, alive = [], []
        for f in self._floaters:
            text, x, y, vx, vy, born, life, size = f
            age = now - born
            if age >= life:
                continue
            alive.append(f)
            a = min(age / 0.7, 1.0) * min((life - age) / 1.2, 1.0)
            items.append(Item(text, x + vx * age, y + vy * age, a, size))
        self._floaters = alive
        return items

    # ---- painting
    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHints(QPainter.RenderHint.Antialiasing | QPainter.RenderHint.TextAntialiasing)
        now = time.monotonic()
        for it in self._items:
            if it.alpha > 0.01:
                self._draw(p, it, now)

    def _draw(self, p, it, now):
        cfg = self.cfg
        r = self._rect(it)
        a = it.alpha * cfg["text_opacity"] / 100.0
        p.setFont(self._font(it.size))
        sh = cfg["shadow"]
        if sh == "glow":
            gc = QColor(self.ctl.accent_qc)
            for rad, al in ((3.0, 26), (6.5, 14)):
                gc.setAlpha(int(al * a))
                p.setPen(gc)
                for k in range(10):
                    ang = k * math.pi / 5
                    p.drawText(r.translated(math.cos(ang) * rad, math.sin(ang) * rad), FLAGS, it.text)
            p.setPen(QColor(0, 0, 0, int(90 * a)))
            p.drawText(r.translated(0, 3), FLAGS, it.text)
        elif sh == "soft":
            p.setPen(QColor(0, 0, 0, int(70 * a)))
            p.drawText(r.translated(0, 4), FLAGS, it.text)
            p.setPen(QColor(0, 0, 0, int(150 * a)))
            p.drawText(r.translated(0, 2), FLAGS, it.text)
        brush = text_brush(cfg, r, now, a)
        if it.fill < 0:
            p.setPen(QPen(brush, 1))
            p.drawText(r, FLAGS, it.text)
        else:
            p.setPen(QColor(255, 255, 255, int(105 * a)))
            p.drawText(r, FLAGS, it.text)
            p.save()
            p.setClipRect(QRectF(r.left(), r.top(), r.width() * it.fill, r.height()),
                          Qt.ClipOperation.IntersectClip)
            p.setPen(QPen(brush, 1))
            p.drawText(r, FLAGS, it.text)
            p.restore()


# --------------------------------------------------------------------------- glass hub
class Player(QWidget):
    BH = 132  # logical height; everything is drawn in logical units and scaled

    def __init__(self, ctl):
        super().__init__(None, WIN_FLAGS)
        self.ctl, self.cfg = ctl, ctl.cfg
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setMouseTracking(True)
        self.hover = self.press = self.drag = None
        self.seek_frac, self.win_off, self.g0 = 0.0, QPoint(), None
        self.blur_ok = self._snap = self._inside = False
        self.toast = ("", 0.0)
        self.cover, self._cov_id, self._bg, self._bg_key = None, 0, None, None
        self.vis = np.zeros(24) if np is not None else None
        self.fx = None
        self._scr = None
        self._geo_key = None
        self.vis_r = QRectF()
        self._vt = QTimer(self)
        self._vt.timeout.connect(self._vis_tick)
        self._ft = QTimer(self)
        self._ft.timeout.connect(self._fx_tick)
        self._mk_fonts()
        self._apply_size(anchor=False)
        self._place()
        self.cfg.changed.connect(self._cfg)

    # ---- config / geometry
    def _cfg(self, k):
        if k in ("hub_scale", "hub_w", "radius"):
            self._apply_size()
        elif k == "glass":
            self.apply_glass()
        elif k == "hub_font":
            self._mk_fonts()
        if k in ("vis", "vis_style", "perf"):
            self.sync_vis()
        if k in ("cover_bg", "cover_blur"):
            self._bg_key = None
        self.update()

    def _mk_fonts(self):
        fam = self.cfg["hub_font"]
        self.f_title, self.f_sub = mk_font(fam, 15, W_.DemiBold), mk_font(fam, 12)
        self.f_small, self.f_btn = mk_font(fam, 11), mk_font(fam, 13, W_.DemiBold)

    def _apply_size(self, anchor=True):
        s, lw, rad = self.cfg["hub_scale"] / 100.0, float(self.cfg["hub_w"]), float(self.cfg["radius"])
        if (s, lw, rad) == self._geo_key:
            return
        self._geo_key = (s, lw, rad)
        self.s, self.lw, self.rad = s, lw, rad
        W, H = round(lw * s), round(self.BH * s)
        bottom = self.y() + self.height()
        self.setFixedSize(W, H)
        if anchor:
            self.move(self.x(), bottom - H)
            self._clamp()
        self._layout()
        path = QPainterPath()
        path.addRoundedRect(QRectF(0, 0, W, H), rad * s, rad * s)
        self.setMask(QRegion(path.toFillPolygon().toPolygon()))
        self._bg_key = None
        self.update()
        sp = getattr(self.ctl, "sprite", None)
        if sp:
            sp.sync()

    def _clamp(self):
        scr = self.screen()
        if scr is None:
            return
        a = scr.availableGeometry()
        x = min(max(self.x(), a.left()), max(a.left(), a.right() - self.width() + 1))
        y = min(max(self.y(), a.top()), max(a.top(), a.bottom() - self.height() + 1))
        self.move(x, y)

    def _layout(self):
        lw = self.lw
        self.hit = {
            "grip": QRectF(lw - 28, 0, 28, 24),
            "seek": QRectF(10, 96, lw - 20, 20),
            "prev": QRectF(14, 58, 34, 34), "play": QRectF(50, 55, 40, 40), "next": QRectF(92, 58, 34, 34),
            "lyrics": QRectF(lw - 116, 58, 34, 34), "style": QRectF(lw - 82, 58, 34, 34),
            "more": QRectF(lw - 48, 58, 34, 34)}
        self.track = QRectF(18, 104, lw - 36, 4)
        x0, x1 = 138.0, lw - 126.0
        self.vis_r = QRectF(x0, 63, x1 - x0, 24) if x1 - x0 > 40 else QRectF()

    def _place(self):
        pos = str(self.cfg["hub_pos"])
        try:
            x, y = (int(v) for v in pos.split(","))
            pt = QPoint(x, y)
            if QGuiApplication.screenAt(pt) is None:
                raise ValueError
        except Exception:
            av = QGuiApplication.primaryScreen().availableGeometry()
            pt = QPoint(av.left() + 20, av.bottom() - self.height() - 20)
        self.move(pt)
        self._scr = QGuiApplication.screenAt(self.frameGeometry().center())

    def apply_glass(self):
        self.blur_ok = set_blur(self, self.cfg["glass"])
        self.update()

    def snapshot(self):
        self._snap = True
        pm = self.grab()
        self._snap = False
        return pm

    # ---- cover art
    def set_cover(self, img):
        self.cover, self._bg_key = img, None
        self._cov_id += 1
        self.update()

    def _cover_bg(self):
        if not (self.cfg["cover_bg"] and self.cover):
            return None
        key = (self._cov_id, self.width(), self.height(), self.cfg["cover_blur"])
        if key != self._bg_key:
            self._bg = blur_cover(self.cover, self.width(), self.height(), self.cfg["cover_blur"])
            self._bg_key = key
        return self._bg

    # ---- visualizer / fx timers
    def showEvent(self, e):
        super().showEvent(e)
        self.sync_vis()
        sp = getattr(self.ctl, "sprite", None)
        if sp:
            sp.sync()

    def hideEvent(self, e):
        super().hideEvent(e)
        self._vt.stop()
        sp = getattr(self.ctl, "sprite", None)
        if sp:
            sp.sync()

    def sync_vis(self):
        on = (np is not None and self.cfg["vis"] and self.isVisible() and self.ctl.pb.playing
              and not self.vis_r.isNull())
        if on:
            iv = fps_ms(self.cfg)
            if not self._vt.isActive() or self._vt.interval() != iv:
                self._vt.start(iv)
        elif self._vt.isActive():
            self._vt.stop()
            if self.vis is not None and self.vis.any():
                self.vis = np.zeros_like(self.vis)
                self.update()

    def _dev(self, r):
        s = self.s
        return QRectF(r.x() * s, r.y() * s, r.width() * s, r.height() * s).adjusted(-3, -3, 3, 3).toAlignedRect()

    def _vis_tick(self):
        t = self.ctl.tap.read()
        if t is None:
            return
        tgt = np.clip(t * (self.cfg["vis_gain"] / 100.0), 0.0, 1.0)
        new = np.where(tgt > self.vis, self.vis + (tgt - self.vis) * 0.6, self.vis * 0.86)
        if np.allclose(new, self.vis, atol=0.004):
            return
        self.vis = new
        self.update(self._dev(self.vis_r))

    def start_fx(self):
        self.fx = time.monotonic()
        self._ft.start(16)

    def _fx_tick(self):
        if time.monotonic() - self.fx > 0.8:
            self._ft.stop()
            self.fx = None
        self.update()

    def flash(self, text):
        self.toast = (text, time.monotonic() + 1.6)
        self.update()
        QTimer.singleShot(1700, self.update)

    def moveEvent(self, e):
        super().moveEvent(e)
        sp = getattr(self.ctl, "sprite", None)
        if sp and sp.isVisible():
            sp.follow()
        scr = QGuiApplication.screenAt(self.frameGeometry().center())
        if scr is not None and scr != self._scr:
            first = self._scr is None
            self._scr = scr
            if not first and self.isVisible():
                if self.cfg["hop_fx"]:
                    self.start_fx()
                self.ctl.overlay.follow()

    # ---- painting
    def paintEvent(self, _):
        cfg, ctl, pb = self.cfg, self.ctl, self.ctl.pb
        p = QPainter(self)
        p.setRenderHints(QPainter.RenderHint.Antialiasing | QPainter.RenderHint.TextAntialiasing |
                         QPainter.RenderHint.SmoothPixmapTransform)
        s, W, H = self.s, self.lw, float(self.BH)
        now = time.monotonic()
        fxu = (now - self.fx) if self.fx else 9.0
        if fxu < 0.32:                                   # tiny "settle" pop on monitor change
            k = 1 - 0.04 * (1 - ease(fxu / 0.32))
            c = QPointF(self.width() / 2, self.height() / 2)
            p.translate(c)
            p.scale(k, k)
            p.translate(-c)
        p.scale(s, s)
        r = QRectF(0, 0, W, H).adjusted(1, 1, -1, -1)
        path = QPainterPath()
        path.addRoundedRect(r, self.rad, self.rad)
        blur = self.blur_ok and not self._snap
        bg = self._cover_bg()
        if bg:
            p.save()
            p.setClipPath(path)
            p.drawPixmap(QRectF(0, 0, W, H), bg, QRectF(bg.rect()))
            p.restore()
            p.fillPath(path, QColor(8, 10, 16, min(255, int(cfg["cover_dim"] * 2.4))))
        else:
            base = 95 if blur else 205
            p.fillPath(path, QColor(18, 20, 28, min(255, int(base * cfg["tint"] / 60.0))))
        g = QLinearGradient(r.topLeft(), r.bottomRight())
        g.setColorAt(0, QColor(255, 255, 255, 46))
        g.setColorAt(0.5, QColor(255, 255, 255, 10))
        g.setColorAt(1, QColor(255, 255, 255, 26))
        p.fillPath(path, g)
        p.save()
        p.setClipPath(path)
        sheen = QLinearGradient(0, 0, 0, H * 0.5)
        sheen.setColorAt(0, QColor(255, 255, 255, 38))
        sheen.setColorAt(1, QColor(255, 255, 255, 0))
        p.fillRect(QRectF(0, 0, W, H * 0.5), sheen)
        p.restore()
        rim = QLinearGradient(r.topLeft(), r.bottomRight())
        rim.setColorAt(0, QColor(255, 255, 255, 175))
        rim.setColorAt(0.35, QColor(255, 255, 255, 40))
        rim.setColorAt(0.7, QColor(255, 255, 255, 25))
        rim.setColorAt(1, QColor(255, 255, 255, 115))
        p.setPen(QPen(QBrush(rim), 1.2))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawPath(path)

        has = pb.has
        white = lambda a: QColor(255, 255, 255, int(a))
        a0 = ctl.accent_qc   # lightened so it stays readable on top of cover art
        acc = QColor((a0.red() + 255) // 2 + 0, (a0.green() + 255) // 2, (a0.blue() + 255) // 2)
        p.setFont(self.f_title)
        p.setPen(white(245))
        title = pb.title if has else "Nothing playing"
        tw = int(W - 56)
        p.drawText(QRectF(18, 12, tw, 22), int(Qt.AlignmentFlag.AlignVCenter),
                   QFontMetrics(self.f_title).elidedText(title, Qt.TextElideMode.ElideRight, tw))
        status = {"synced": "synced lyrics", "plain": "lyrics (unsynced)",
                  "loading": "searching lyrics…", "none": "no lyrics found"}[ctl.kind]
        if not cfg["lyrics_on"]:
            status = "lyrics off"
        sub = f"{pb.artist}  ·  {status}" if has else "Play something on Spotify"
        if now < self.toast[1]:
            sub = self.toast[0]
        p.setFont(self.f_sub)
        p.setPen(white(150) if now >= self.toast[1] else acc.lighter(130))
        sw = int(W - 36)
        p.drawText(QRectF(18, 34, sw, 18), int(Qt.AlignmentFlag.AlignVCenter),
                   QFontMetrics(self.f_sub).elidedText(sub, Qt.TextElideMode.ElideRight, sw))

        for name in ("prev", "play", "next", "lyrics", "style", "more"):
            b = self.hit[name]
            transport = name in ("prev", "play", "next")
            alpha = 1.0 if (has or not transport) else 0.3
            hov = self.hover if not self._snap else None
            bgc = 70 if self.press == name else 38 if hov == name else (22 if name == "play" else 0)
            if bgc:
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(white(bgc * alpha))
                p.drawEllipse(b)
            col = white(235 * alpha)
            if name == "lyrics" and not cfg["lyrics_on"]:
                col = white(90)
            self._icon(p, name, b, col)

        self._draw_vis(p, acc)

        dur = pb.dur if has else 0.0
        shown = self.seek_frac * dur if self.drag == "seek" else pb.pos()
        frac = min(max(shown / dur, 0.0), 1.0) if dur > 0 else 0.0
        t = self.track
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(white(55))
        p.drawRoundedRect(t, 2, 2)
        p.setBrush(acc)
        p.drawRoundedRect(QRectF(t.left(), t.top(), t.width() * frac, t.height()), 2, 2)
        if has and (self.hover == "seek" or self.drag == "seek"):
            p.setBrush(white(250))
            p.drawEllipse(QPointF(t.left() + t.width() * frac, t.center().y()), 5.5, 5.5)
        p.setFont(self.f_small)
        p.setPen(white(140))
        p.drawText(QRectF(18, 111, 70, 16), int(Qt.AlignmentFlag.AlignLeft), fmt(shown) if has else "0:00")
        p.drawText(QRectF(W - 88, 111, 70, 16), int(Qt.AlignmentFlag.AlignRight), fmt(dur) if has else "0:00")

        if (self._inside or self.drag == "grip") and not self._snap:     # resize grip (top-right)
            p.setPen(QPen(white(170 if self.hover == "grip" or self.drag == "grip" else 80), 1.3))
            for i in (1, 2, 3):
                p.drawLine(QPointF(W - 12 - i * 3.6, 8), QPointF(W - 12, 8 + i * 3.6))
        if 0.05 < fxu < 0.65:                                              # monitor-change shine
            draw_shine(p, QRectF(0, 0, W, H), self.rad, (fxu - 0.05) / 0.6)

    def _draw_vis(self, p, acc):
        r = self.vis_r
        if r.isNull() or not self.cfg["vis"] or self.vis is None:
            return
        v, st = self.vis, self.cfg["vis_style"]
        if st == "wave":
            xs = np.linspace(0, len(v) - 1, 40)
            ys = np.interp(xs, np.arange(len(v)), v)
            pts = [QPointF(r.left() + r.width() * i / (len(xs) - 1), r.bottom() - 1 - float(y) * (r.height() - 2))
                   for i, y in enumerate(ys)]
            path = QPainterPath(pts[0])
            for i in range(1, len(pts)):
                path.quadTo(pts[i - 1], QPointF((pts[i - 1].x() + pts[i].x()) / 2, (pts[i - 1].y() + pts[i].y()) / 2))
            path.lineTo(pts[-1])
            area = QPainterPath(path)
            area.lineTo(r.right(), r.bottom())
            area.lineTo(r.left(), r.bottom())
            area.closeSubpath()
            fg = QLinearGradient(0, r.top(), 0, r.bottom())
            c0, c1 = QColor(acc), QColor(acc)
            c0.setAlpha(90)
            c1.setAlpha(0)
            fg.setColorAt(0, c0)
            fg.setColorAt(1, c1)
            p.fillPath(area, fg)
            c = QColor(acc)
            c.setAlpha(225)
            p.setPen(QPen(c, 1.6, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawPath(path)
            return
        bw, gap = 2.4, 1.7
        nb = max(6, int((r.width() + gap) // (bw + gap)))
        vals = np.interp(np.linspace(0, len(v) - 1, nb), np.arange(len(v)), v)
        x0 = r.left() + (r.width() - (nb * (bw + gap) - gap)) / 2
        p.setPen(Qt.PenStyle.NoPen)
        c = QColor(acc)
        for i, val in enumerate(vals):
            val = float(val)
            c.setAlpha(int(90 + val * 150))
            p.setBrush(c)
            x = x0 + i * (bw + gap)
            if st == "mirror":
                h = max(1.6, val * r.height() / 2)
                p.drawRoundedRect(QRectF(x, r.center().y() - h, bw, 2 * h), bw / 2, bw / 2)
            else:
                h = max(2.0, val * r.height())
                p.drawRoundedRect(QRectF(x, r.bottom() - h, bw, h), bw / 2, bw / 2)

    def _icon(self, p, name, b, col):
        c = b.center()
        x, y = c.x(), c.y()
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(col)
        poly = lambda *pts: p.drawPolygon(QPolygonF([QPointF(a, b_) for a, b_ in pts]))
        if name == "play":
            if self.ctl.pb.playing:
                p.drawRoundedRect(QRectF(x - 6, y - 8, 4.5, 16), 1.5, 1.5)
                p.drawRoundedRect(QRectF(x + 1.5, y - 8, 4.5, 16), 1.5, 1.5)
            else:
                poly((x - 5, y - 9), (x - 5, y + 9), (x + 9, y))
        elif name == "prev":
            p.drawRoundedRect(QRectF(x - 9, y - 7, 3, 14), 1, 1)
            poly((x + 8, y - 7), (x + 8, y + 7), (x - 5, y))
        elif name == "next":
            p.drawRoundedRect(QRectF(x + 6, y - 7, 3, 14), 1, 1)
            poly((x - 8, y - 7), (x - 8, y + 7), (x + 5, y))
        elif name == "more":
            for dx in (-7, 0, 7):
                p.drawEllipse(QPointF(x + dx, y), 1.9, 1.9)
        else:
            p.setFont(self.f_btn)
            p.setPen(col)
            p.drawText(b, int(Qt.AlignmentFlag.AlignCenter), "♪" if name == "lyrics" else "Aa")

    # ---- mouse
    def _lp(self, e):
        pt = e.position()
        return QPointF(pt.x() / self.s, pt.y() / self.s)

    def _at(self, pt):
        for name in ("grip", "seek", "prev", "play", "next", "lyrics", "style", "more"):
            if self.hit[name].contains(pt):
                return name
        return None

    def _frac(self, x):
        return min(max((x - self.track.left()) / self.track.width(), 0.0), 1.0)

    def enterEvent(self, e):
        self._inside = True
        self.update()

    def leaveEvent(self, _):
        self._inside, self.hover = False, None
        self.unsetCursor()
        self.update()

    def mousePressEvent(self, e):
        pt = self._lp(e)
        if e.button() == Qt.MouseButton.RightButton:
            self.menu(e.position().toPoint())
            return
        if e.button() != Qt.MouseButton.LeftButton:
            return
        name = self._at(pt)
        if name == "grip":
            self.drag, self.g0 = "grip", (e.globalPosition().toPoint(), self.width(), self.height())
        elif name == "seek":
            if self.ctl.pb.has and self.ctl.pb.dur > 0:
                self.drag, self.seek_frac = "seek", self._frac(pt.x())
        elif name:
            self.press = name
        else:
            self.drag = "win"
            self.win_off = e.globalPosition().toPoint() - self.frameGeometry().topLeft()
        self.update()

    def mouseMoveEvent(self, e):
        if self.drag == "seek":
            self.seek_frac = self._frac(self._lp(e).x())
        elif self.drag == "win":
            self.move(e.globalPosition().toPoint() - self.win_off)
            return
        elif self.drag == "grip":
            d = e.globalPosition().toPoint() - self.g0[0]
            s = min(max((self.g0[2] - d.y()) / self.BH, 0.7), 2.5)
            lw = min(max((self.g0[1] + d.x()) / s, 300.0), 720.0)
            self.cfg.set("hub_scale", int(round(s * 100)))
            self.cfg.set("hub_w", int(round(lw)))
            return
        elif not e.buttons():
            h = self._at(self._lp(e))
            if h != self.hover:
                self.hover = h
                if h == "grip":
                    self.setCursor(Qt.CursorShape.SizeBDiagCursor)
                else:
                    self.unsetCursor()
            else:
                return
        self.update()

    def mouseReleaseEvent(self, e):
        ctl, name = self.ctl, self.press
        if self.drag == "seek":
            ctl.seek(self.seek_frac * ctl.pb.dur)
        elif self.drag in ("win", "grip"):
            self.cfg.set("hub_pos", f"{self.x()},{self.y()}")
        elif name and self._at(self._lp(e)) == name:
            has = ctl.pb.has
            if name == "play" and has: ctl.toggle()
            elif name == "next" and has: ctl.nxt()
            elif name == "prev" and has: ctl.prev()
            elif name == "lyrics": self.cfg.set("lyrics_on", not self.cfg["lyrics_on"])
            elif name == "style": ctl.cycle_style()
            elif name == "more": self.menu(QPoint(int(self.hit["more"].left() * self.s), 0))
        self.drag = self.press = None
        self.update()

    def wheelEvent(self, e):
        dy = e.angleDelta().y()
        if not dy:
            return
        step = 1 if dy > 0 else -1
        if e.modifiers() & Qt.KeyboardModifier.ControlModifier:
            self.cfg.set("hub_scale", min(250, max(70, self.cfg["hub_scale"] + 5 * step)))
        elif self._at(self._lp(e)) == "lyrics":
            self.ctl.nudge(step * (0.5 if e.modifiers() & Qt.KeyboardModifier.ShiftModifier else 0.05))

    # ---- menu
    def menu(self, pt):
        ctl, cfg = self.ctl, self.cfg
        m = QMenu(self)
        m.setStyleSheet(MENU_QSS)
        a = m.addAction("Show lyrics")
        a.setCheckable(True)
        a.setChecked(cfg["lyrics_on"])
        a.triggered.connect(lambda _=False: cfg.set("lyrics_on", not cfg["lyrics_on"]))
        sm = m.addMenu("Lyric style")
        for key, label in STYLES:
            act = sm.addAction(label)
            act.setCheckable(True)
            act.setChecked(key == cfg["style"])
            act.triggered.connect(lambda _=False, k=key: ctl.set_style(k))
        vm = m.addMenu("Visualizer")
        for key, label in [("off", "Off")] + VIS_STYLES:
            act = vm.addAction(label)
            act.setCheckable(True)
            act.setChecked((not cfg["vis"]) if key == "off" else (cfg["vis"] and cfg["vis_style"] == key))
            act.triggered.connect(lambda _=False, k=key: ctl.set_vis(k))
        spm = m.addMenu("Sprite")
        for key, label in [("off", "Off")] + SPRITES:
            act = spm.addAction(label + ("…" if key == "custom" else ""))
            act.setCheckable(True)
            act.setChecked((not cfg["sprite_on"]) if key == "off" else (cfg["sprite_on"] and cfg["sprite"] == key))
            act.triggered.connect(lambda _=False, k=key: ctl.set_sprite(k))
        c = m.addAction("Blurred album art")
        c.setCheckable(True)
        c.setChecked(cfg["cover_bg"])
        c.triggered.connect(lambda _=False: cfg.set("cover_bg", not cfg["cover_bg"]))
        m.addSeparator()
        m.addAction(f"Lyric timing ({ctl.offset:+.2f}s)…").triggered.connect(lambda _=False: ctl.open_settings("Timing"))
        m.addAction("Settings…").triggered.connect(lambda _=False: ctl.open_settings())
        m.addSeparator()
        m.addAction("Hide hub (tray icon brings it back)").triggered.connect(lambda _=False: ctl.toggle_hub())
        m.addAction("Quit LyricsFloat").triggered.connect(lambda _=False: ctl.quit())
        gp = self.mapToGlobal(pt)
        gp.setY(self.mapToGlobal(QPoint(0, 0)).y() - m.sizeHint().height() - 6)
        m.exec(gp)


# --------------------------------------------------------------------------- launch animation
class Intro(QWidget):
    """Full-screen click-through layer that animates a snapshot of the hub into place."""
    done = Signal()

    def __init__(self, hub, kind):
        super().__init__(None, Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint |
                         Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.WindowTransparentForInput |
                         Qt.WindowType.WindowDoesNotAcceptFocus)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.kind, self.rad = kind, hub.rad * hub.s
        scr = QGuiApplication.screenAt(hub.frameGeometry().center()) or QGuiApplication.primaryScreen()
        g = scr.geometry()
        self.setGeometry(g)
        self.pm = hub.snapshot()
        gh = hub.geometry()
        self.R = QRectF(gh.x() - g.x(), gh.y() - g.y(), gh.width(), gh.height())
        self.dur = 1.3 if kind == "slide" else 1.8
        self.L = max(self.R.width(), self.R.height()) * 1.15
        self._build_fx()
        pad = self.L * 1.5 if kind == "break" else 70
        reg = self.R.adjusted(-pad, -pad, pad, pad)
        if kind == "slide":
            reg = QRectF(0, self.R.y() - 40, self.R.right() + 90, self.R.height() + 80)
        self.region = reg.intersected(QRectF(self.rect())).toAlignedRect()
        self.t0 = 0.0
        self.timer = QTimer(self)
        self.timer.timeout.connect(self._tick)

    def _build_fx(self):
        rnd = random.Random(7)
        self.cracks, self.shards = [], []
        for i in range(11):
            a = i / 11 * 2 * math.pi + rnd.uniform(-0.2, 0.2)
            Lr, n = self.L * rnd.uniform(0.55, 1.0), 8
            pts = [QPointF(0, 0)]
            for j in range(1, n + 1):
                d, jit = Lr * j / n, rnd.uniform(-1, 1) * Lr * 0.045
                pts.append(QPointF(math.cos(a) * d - math.sin(a) * jit, math.sin(a) * d + math.cos(a) * jit))
            self.cracks.append((pts, 0.0))
            k = rnd.randint(3, 5)
            base, ba, bl = pts[k], a + rnd.choice((-1, 1)) * rnd.uniform(0.45, 0.8), Lr * 0.35
            br = [base] + [QPointF(base.x() + math.cos(ba) * bl * j / 3 + rnd.uniform(-3, 3),
                                   base.y() + math.sin(ba) * bl * j / 3 + rnd.uniform(-3, 3)) for j in range(1, 4)]
            self.cracks.append((br, k / n))
        for _ in range(16):
            self.shards.append((rnd.uniform(0, 2 * math.pi), rnd.uniform(0.4, 1.4) * self.L, rnd.uniform(9, 24),
                                rnd.uniform(-5, 5), rnd.uniform(0, 6.28)))

    def start(self):
        self.t0 = time.monotonic()
        self.show()
        self.timer.start(16)

    def _tick(self):
        if time.monotonic() - self.t0 >= self.dur:
            self.timer.stop()
            self.hide()
            self.done.emit()
            return
        self.update(self.region)

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHints(QPainter.RenderHint.Antialiasing | QPainter.RenderHint.SmoothPixmapTransform)
        getattr(self, "_p_" + self.kind)(p, time.monotonic() - self.t0)

    def _src(self):
        return QRectF(self.pm.rect())

    def _p_slide(self, p, t):
        R = self.R
        x0 = -R.width() - 30
        k = eo_back(t / 0.75)
        tr = QRectF(x0 + (R.x() - x0) * k, R.y(), R.width(), R.height())
        p.setOpacity(min(t / 0.18, 1.0))
        p.drawPixmap(tr, self.pm, self._src())
        p.setOpacity(1.0)
        draw_shine(p, tr, self.rad, (t - 0.5) / 0.55)

    def _poly(self, p, pts, g):
        n = len(pts) - 1
        k = min(max(g, 0.0), 1.0) * n
        whole = int(k)
        path = QPainterPath(pts[0])
        for i in range(1, min(whole, n) + 1):
            path.lineTo(pts[i])
        if whole < n:
            a, b, f = pts[whole], pts[whole + 1], k - whole
            path.lineTo(QPointF(a.x() + (b.x() - a.x()) * f, a.y() + (b.y() - a.y()) * f))
        p.drawPath(path)

    def _p_break(self, p, t):
        R, C, L = self.R, self.R.center(), self.L
        emerge = min(max((t - 0.2) / 0.65, 0.0), 1.0)
        # soft halo behind the hub
        if t > 0.2:
            ha = 0.3 * ease((t - 0.2) / 0.3) * (1 - ease((t - 1.2) / 0.5))
            g = QRadialGradient(C, L * 0.9)
            g.setColorAt(0, QColor(170, 220, 255, int(255 * ha)))
            g.setColorAt(1, QColor(170, 220, 255, 0))
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(g)
            p.drawEllipse(C, L * 0.9, L * 0.9)
        # cracks in the "glass"
        grow, fade = ease(t / 0.28), 1 - ease((t - 0.6) / 0.45)
        if fade > 0:
            p.save()
            p.translate(C)
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.setPen(QPen(QColor(225, 242, 255, int(230 * fade)), 1.6, Qt.PenStyle.SolidLine,
                          Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
            for pts, start in self.cracks:
                g2 = (grow - start) / (1 - start) if start else grow
                if g2 > 0:
                    self._poly(p, pts, g2)
            p.restore()
        # impact flash
        fl = max(0.0, 1 - abs(t - 0.25) / 0.2)
        if fl > 0:
            g = QRadialGradient(C, L * 1.3)
            g.setColorAt(0, QColor(255, 255, 255, int(235 * fl)))
            g.setColorAt(0.4, QColor(200, 230, 255, int(110 * fl)))
            g.setColorAt(1, QColor(200, 230, 255, 0))
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(g)
            p.drawEllipse(C, L * 1.3, L * 1.3)
        # hub bursts through toward the viewer, overshoots, settles
        if t >= 0.2:
            sc = 0.12 + (1.14 - 0.12) * eo_expo(emerge)
            if t > 0.85:
                sc = 1.14 - 0.14 * ease((t - 0.85) / 0.35)
            p.save()
            p.translate(C)
            p.scale(sc, sc)
            p.setOpacity(min((t - 0.2) / 0.18, 1.0))
            p.drawPixmap(QRectF(-R.width() / 2, -R.height() / 2, R.width(), R.height()), self.pm, self._src())
            p.restore()
            p.setOpacity(1.0)
        # glass shards
        tau = t - 0.22
        if 0 < tau < 0.95:
            al = 1 - tau / 0.95
            p.setBrush(QColor(200, 230, 255, int(150 * al)))
            p.setPen(QPen(QColor(255, 255, 255, int(220 * al)), 1))
            for ang, dist, size, spin, ph in self.shards:
                r = dist * ease(tau / 0.9)
                c = QPointF(C.x() + math.cos(ang) * r, C.y() + math.sin(ang) * r)
                rot = ph + spin * tau
                p.drawPolygon(QPolygonF([QPointF(c.x() + math.cos(rot + o) * size * m, c.y() + math.sin(rot + o) * size * m)
                                         for o, m in ((0, 1.0), (2.3, 0.7), (4.2, 0.9))]))
        draw_shine(p, R, self.rad, (t - 1.15) / 0.55)


# --------------------------------------------------------------------------- settings window
QSS = """
QWidget{background:#14161d;color:#e8eaf0;font-size:13px}
QTabWidget::pane{border:1px solid #2a2e3a;border-radius:8px;top:-1px}
QTabBar::tab{background:#1b1e27;padding:7px 13px;border:1px solid #2a2e3a;border-bottom:none;
 border-top-left-radius:8px;border-top-right-radius:8px;margin-right:2px;color:#9aa1b2}
QTabBar::tab:selected{background:#232733;color:#fff}
QPushButton{background:#2a3042;border:1px solid #3a4258;border-radius:7px;padding:6px 12px}
QPushButton:hover{background:#343c54}
QComboBox,QDoubleSpinBox,QFontComboBox{background:#1d2029;border:1px solid #3a3f4d;border-radius:6px;padding:4px 8px}
QComboBox QAbstractItemView{background:#1d2029;selection-background-color:#3a4258}
QSlider::groove:horizontal{height:4px;background:#2d3242;border-radius:2px}
QSlider::sub-page:horizontal{background:#8fd3ff;border-radius:2px}
QSlider::handle:horizontal{background:#fff;width:14px;margin:-6px 0;border-radius:7px}
QCheckBox::indicator{width:18px;height:18px;border-radius:5px;border:1px solid #4a5268;background:#1d2029}
QCheckBox::indicator:checked{background:#8fd3ff;border-color:#8fd3ff}
QScrollArea{border:none}
""" + MENU_QSS

PRESETS = {
    "White": dict(color_mode="solid", color1="#ffffff"),
    "Ice": dict(color_mode="gradient", color1="#ffffff", color2="#8fd3ff", color3="#b9a6ff", grad_angle=20),
    "Sunset": dict(color_mode="gradient", color1="#ffd36e", color2="#ff7a59", color3="#d6409f", grad_angle=0),
    "Neon": dict(color_mode="animated", color1="#00f5d4", color2="#9b5de5", color3="#f15bb5", grad_angle=0, grad_speed=8),
    "Aurora": dict(color_mode="animated", color1="#7cffcb", color2="#74c0fc", color3="#c4a1ff", grad_angle=20, grad_speed=5),
}

SCHEMA = [
    ("Lyrics", [
        ("toggle", "lyrics_on", "Show lyrics"),
        ("choice", "style", "Style", STYLES),
        ("font", "font", "Font"),
        ("choice", "weight", "Weight", WEIGHTS),
        ("slider", "font_size", "Text size", (20, 90, "{} px")),
        ("slider", "y_off", "Vertical position", (-30, 30, "{:+d}%")),
        ("slider", "max_w", "Max line width", (40, 95, "{}%")),
        ("toggle", "preview", "Show next / nearby lines (Karaoke, Stack)"),
        ("choice", "shadow", "Text effect", [("soft", "Soft shadow"), ("glow", "Glow"), ("none", "None")]),
        ("slider", "text_opacity", "Opacity", (30, 100, "{}%")),
    ]),
    ("Colors", [
        ("preview",),
        ("presets",),
        ("choice", "color_mode", "Text color",
         [("solid", "Solid"), ("gradient", "Gradient"), ("animated", "Animated gradient")]),
        ("color", "color1", "Color 1 (solid / start)"),
        ("color", "color2", "Color 2"),
        ("color", "color3", "Color 3"),
        ("slider", "grad_angle", "Gradient angle", (0, 360, "{}°")),
        ("slider", "grad_speed", "Animation speed", (1, 20, "{}")),
        ("choice", "accent_mode", "Accent (bar, visualizer, glow)",
         [("cover", "From album art"), ("custom", "Custom")]),
        ("color", "accent", "Custom accent"),
    ]),
    ("Hub", [
        ("slider", "hub_scale", "Size", (70, 250, "{}%")),
        ("slider", "hub_w", "Width", (300, 720, "{}")),
        ("slider", "radius", "Corner radius", (8, 40, "{} px")),
        ("toggle", "glass", "Glass blur behind hub"),
        ("slider", "tint", "Glass tint", (0, 100, "{}%")),
        ("toggle", "cover_bg", "Blurred album-art background"),
        ("slider", "cover_blur", "Art blur", (1, 10, "{}")),
        ("slider", "cover_dim", "Art darkness", (0, 90, "{}%")),
        ("font", "hub_font", "Hub font"),
        ("toggle", "vis", "Audio visualizer"),
        ("choice", "vis_style", "Visualizer style", VIS_STYLES),
        ("slider", "vis_gain", "Visualizer sensitivity", (40, 300, "{}%")),
        ("button", None, "Reset hub position", "reset_position"),
    ]),
    ("Sprite", [
        ("toggle", "sprite_on", "Show sprite on the hub"),
        ("choice", "sprite", "Character", SPRITES),
        ("button", None, "Choose custom image / GIF…", "pick_sprite_image"),
        ("slider", "sprite_size", "Size", (50, 200, "{}%")),
        ("slider", "sprite_pos", "Position along the top edge", (0, 100, "{}%")),
        ("toggle", "sprite_react", "React when the hub is moved or thrown"),
        ("toggle", "sprite_dance", "Bob to the music (needs visualizer on)"),
    ]),
    ("Effects", [
        ("choice", "intro", "Launch animation",
         [("none", "None"), ("slide", "Slide in + shine"), ("break", "Break through the screen")]),
        ("button", None, "Preview launch animation", "play_intro"),
        ("toggle", "hop_fx", "Monitor-change effect"),
    ]),
    ("Timing",),
    ("General", [
        ("choice", "perf", "Animation smoothness",
         [("eco", "Eco (lowest CPU)"), ("normal", "Normal"), ("smooth", "Smooth (60 fps)")]),
        ("toggle", "autostart", "Start with Windows"),
        ("button", None, "Reset all settings", "reset_all"),
        ("button", None, "Quit LyricsFloat", "quit"),
    ]),
]


class ColorPreview(QWidget):
    def __init__(self, cfg):
        super().__init__()
        self.cfg = cfg
        self.setFixedHeight(76)
        self.t = QTimer(self)
        self.t.timeout.connect(self.update)
        cfg.changed.connect(lambda k: self._sync())

    def _sync(self):
        if self.isVisible() and self.cfg["color_mode"] == "animated":
            if not self.t.isActive():
                self.t.start(33)
        else:
            self.t.stop()
        self.update()

    def showEvent(self, e):
        super().showEvent(e)
        self._sync()

    def hideEvent(self, e):
        super().hideEvent(e)
        self.t.stop()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHints(QPainter.RenderHint.Antialiasing | QPainter.RenderHint.TextAntialiasing)
        path = QPainterPath()
        path.addRoundedRect(QRectF(self.rect()).adjusted(1, 1, -1, -1), 12, 12)
        g = QLinearGradient(0, 0, self.width(), self.height())
        g.setColorAt(0, QColor("#2b3350"))
        g.setColorAt(1, QColor("#10131c"))
        p.fillPath(path, g)
        f = mk_font(self.cfg["font"], 30, WEIGHT_MAP.get(self.cfg["weight"], W_.DemiBold))
        p.setFont(f)
        r = QRectF(self.rect())
        tw = QFontMetrics(f).horizontalAdvance("Floating lyrics")
        tr = QRectF(r.center().x() - tw / 2, r.top(), tw, r.height())
        p.setPen(QColor(0, 0, 0, 120))
        p.drawText(tr.translated(0, 2), 0x84, "Floating lyrics")
        p.setPen(QPen(text_brush(self.cfg, tr, time.monotonic(), 1.0), 1))
        p.drawText(tr, 0x84, "Floating lyrics")


class SettingsWin(QWidget):
    def __init__(self, ctl):
        super().__init__(None)
        self.ctl, self.cfg = ctl, ctl.cfg
        self.setWindowTitle("LyricsFloat settings")
        self.setWindowIcon(make_icon())
        self.setStyleSheet(QSS)
        self.resize(560, 640)
        self.sync = {}
        lay = QVBoxLayout(self)
        self.tabs = QTabWidget()
        lay.addWidget(self.tabs)
        for entry in SCHEMA:
            page = self._timing_page() if entry[0] == "Timing" else self._page(entry[1])
            sa = QScrollArea()
            sa.setWidgetResizable(True)
            sa.setWidget(page)
            self.tabs.addTab(sa, entry[0])
        self.cfg.changed.connect(lambda k: self.sync[k](self.cfg[k]) if k in self.sync else None)

    def show_tab(self, name=None):
        if name:
            for i in range(self.tabs.count()):
                if self.tabs.tabText(i) == name:
                    self.tabs.setCurrentIndex(i)
        self.show()
        self.raise_()
        self.activateWindow()

    def closeEvent(self, e):
        e.ignore()
        self.hide()

    # ---- generic rows
    def _page(self, rows):
        w = QWidget()
        g = QGridLayout(w)
        g.setContentsMargins(16, 14, 16, 14)
        g.setVerticalSpacing(10)
        g.setColumnStretch(0, 1)
        r = 0
        for row in rows:
            kind = row[0]
            if kind == "preview":
                g.addWidget(ColorPreview(self.cfg), r, 0, 1, 2)
            elif kind == "presets":
                h = QHBoxLayout()
                for name, d in PRESETS.items():
                    b = QPushButton(name)
                    b.clicked.connect(lambda _=False, d=d: [self.cfg.set(k, v) for k, v in d.items()])
                    h.addWidget(b)
                g.addLayout(h, r, 0, 1, 2)
            elif kind == "button":
                b = QPushButton(row[2])
                b.clicked.connect(lambda _=False, m=row[3]: getattr(self.ctl, m)())
                g.addWidget(b, r, 0, 1, 2)
            elif kind == "toggle" and row[1] == "autostart" and not IS_WIN:
                continue
            else:
                lab = QLabel(row[2])
                g.addWidget(lab, r, 0)
                g.addWidget(self._control(row), r, 1)
            r += 1
        g.setRowStretch(r, 1)
        return w

    def _control(self, row):
        kind, key = row[0], row[1]
        cfg = self.cfg
        if kind == "toggle":
            c = QCheckBox()
            c.setChecked(bool(cfg[key]))
            c.toggled.connect(lambda v: cfg.set(key, bool(v)))
            self.sync[key] = lambda v: (c.blockSignals(True), c.setChecked(bool(v)), c.blockSignals(False))
            return c
        if kind == "choice":
            c = QComboBox()
            for val, label in row[3]:
                c.addItem(label, val)
            c.setCurrentIndex(max(0, c.findData(cfg[key])))
            c.currentIndexChanged.connect(lambda i: cfg.set(key, c.itemData(i)))
            self.sync[key] = lambda v: (c.blockSignals(True), c.setCurrentIndex(max(0, c.findData(v))),
                                        c.blockSignals(False))
            return c
        if kind == "slider":
            lo, hi, f = row[3]
            box = QWidget()
            h = QHBoxLayout(box)
            h.setContentsMargins(0, 0, 0, 0)
            s = QSlider(Qt.Orientation.Horizontal)
            s.setRange(lo, hi)
            s.setMinimumWidth(170)
            lab = QLabel()
            lab.setMinimumWidth(56)
            lab.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            s.setValue(int(cfg[key]))
            lab.setText(f.format(int(cfg[key])))

            def changed(v):
                lab.setText(f.format(int(v)))
                cfg.set(key, int(v))
            s.valueChanged.connect(changed)
            h.addWidget(s)
            h.addWidget(lab)

            def sync(v):
                s.blockSignals(True)
                s.setValue(int(v))
                s.blockSignals(False)
                lab.setText(f.format(int(v)))
            self.sync[key] = sync
            return box
        if kind == "color":
            b = QPushButton()
            b.setFixedWidth(110)

            def paint(v):
                col = QColor(v)
                fg = "#000" if col.lightness() > 140 else "#fff"
                b.setStyleSheet(f"background:{col.name()};color:{fg};border:1px solid #ffffff55")
                b.setText(col.name().upper())

            def pick():
                c = QColorDialog.getColor(QColor(cfg[key]), self, row[2])
                if c.isValid():
                    cfg.set(key, c.name())
            b.clicked.connect(pick)
            paint(cfg[key])
            self.sync[key] = paint
            return b
        if kind == "font":
            c = QFontComboBox()
            c.setCurrentFont(QFont(cfg[key]))
            c.currentFontChanged.connect(lambda f: cfg.set(key, f.family()))
            self.sync[key] = lambda v: (c.blockSignals(True), c.setCurrentFont(QFont(v)), c.blockSignals(False))
            return c
        raise ValueError(kind)

    # ---- timing page
    def _timing_page(self):
        ctl = self.ctl
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(16, 14, 16, 14)
        v.setSpacing(12)
        self.t_song = QLabel("No track playing")
        self.t_song.setStyleSheet("color:#9aa1b2")
        v.addWidget(self.t_song)
        h = QHBoxLayout()
        h.addWidget(QLabel("Offset"))
        self.t_spin = QDoubleSpinBox()
        self.t_spin.setRange(-30, 30)
        self.t_spin.setDecimals(2)
        self.t_spin.setSingleStep(0.05)
        self.t_spin.setSuffix(" s")
        h.addWidget(self.t_spin)
        h.addStretch(1)
        v.addLayout(h)
        self.t_slider = QSlider(Qt.Orientation.Horizontal)
        self.t_slider.setRange(-1000, 1000)
        v.addWidget(self.t_slider)
        hint = QLabel("Positive = lyrics appear earlier, negative = later (±10 s on the slider, ±30 s in the box).\n"
                      "Tip: scroll over the ♪ button on the hub to nudge ±0.05 s (hold Shift for ±0.5 s).")
        hint.setStyleSheet("color:#9aa1b2")
        hint.setWordWrap(True)
        v.addWidget(hint)
        nb = QHBoxLayout()
        for d in (-0.5, -0.1, -0.05, 0.05, 0.1, 0.5):
            b = QPushButton(f"{d:+g}")
            b.clicked.connect(lambda _=False, d=d: ctl.nudge(d))
            nb.addWidget(b)
        v.addLayout(nb)
        rb = QPushButton("Reset to 0")
        rb.clicked.connect(lambda _=False: ctl.set_offset(0.0))
        v.addWidget(rb)
        g = QGridLayout()
        g.addWidget(QLabel("Remember timing per song"), 0, 0)
        g.addWidget(self._control(("toggle", "remember_offset", "")), 0, 1)
        g.setColumnStretch(0, 1)
        v.addLayout(g)
        v.addStretch(1)
        self.t_spin.valueChanged.connect(lambda x: ctl.set_offset(x))
        self.t_slider.valueChanged.connect(lambda x: ctl.set_offset(x / 100.0))
        ctl.offset_changed.connect(self._set_off)
        ctl.track_changed.connect(self.t_song.setText)
        self._set_off(ctl.offset)
        return w

    def _set_off(self, val):
        for wdg, x in ((self.t_spin, val), (self.t_slider, int(round(max(-10, min(10, val)) * 100)))):
            wdg.blockSignals(True)
            wdg.setValue(x)
            wdg.blockSignals(False)
