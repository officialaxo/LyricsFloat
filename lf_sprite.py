"""LyricsFloat sprites: pixel-art buddies that sit on the hub and react to it being moved.

Each character is a 16x18 pixel grid (auto-outlined). Lean / stretch / squash are done on whole pixels so it
stays crisp. Every character has its own idle behaviours, physics feel and reactions:
  cat      grooms, puffs up + digs claws in when yanked, curls into a loaf to sleep
  axolotl  jelly-soft sway, flaring gills, blows bubbles
  ghost    goes see-through and leaves a trail when moved, peek-a-boo, sinks into the hub to sleep
  bot      glitches + sparks when thrown, its screen becomes an equalizer while music plays, beeps
"""
import os, math, time, random

from PySide6.QtCore import QPointF, QRect, QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QMovie, QPainter, QPixmap
from PySide6.QtWidgets import QWidget

from lf_core import fps_ms, np

SPRITES = [("cat", "Cat"), ("axolotl", "Axolotl"), ("ghost", "Ghost"), ("bot", "Bot"), ("custom", "Custom image")]
CANVAS = 96.0
GW, GH = 16, 18


# --------------------------------------------------------------------------- pixel grid helpers
def grid(rows, y0):
    g = [[" "] * GW for _ in range(GH)]
    for i, r in enumerate(rows):
        assert len(r) == GW, (y0 + i, len(r), r)
        g[y0 + i] = list(r.replace(".", " "))
    return g


def put(g, x, y, ch):
    if 0 <= x < GW and 0 <= y < GH:
        g[y][x] = ch


def stamp(g, x, y, rows):
    for dy, row in enumerate(rows):
        for dx, ch in enumerate(row):
            if ch != " ":
                put(g, x + dx, y + dy, ch)


def outline(g, ch="k"):
    out = [r[:] for r in g]
    for y in range(GH):
        for x in range(GW):
            if g[y][x] == " ":
                for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                    nx, ny = x + dx, y + dy
                    if 0 <= nx < GW and 0 <= ny < GH and g[ny][nx] != " ":
                        out[y][x] = ch
                        break
    return out


def shear(g, lean):
    """Whole-pixel lean: the top shifts `lean` cells, the feet stay planted."""
    if not lean:
        return g
    out = []
    for y, row in enumerate(g):
        sh = int(round(lean * (GH - 1 - y) / (GH - 1)))
        if sh > 0:
            row = [" "] * sh + row[:-sh]
        elif sh < 0:
            row = row[-sh:] + [" "] * (-sh)
        out.append(row)
    return out


def stretch(g, k):
    """+k: grow taller by duplicating mid rows; -k: squash. Feet stay on the ground."""
    g = [r[:] for r in g]
    for _ in range(abs(k)):
        if k > 0:
            g.insert(9, g[9][:])
            g.pop(0)
        else:
            g.pop(9)
            g.insert(0, [" "] * GW)
    return g


def glitch(g, seed, amount):
    rnd = random.Random(seed)
    g = [r[:] for r in g]
    for _ in range(amount + 1):
        y = rnd.randint(3, GH - 2)
        sh = rnd.choice((-2, -1, 1, 2))
        row = g[y]
        g[y] = ([" "] * sh + row[:-sh]) if sh > 0 else (row[-sh:] + [" "] * (-sh))
    return g


def draw_grid(p, g, pal, x0, y0, px):
    p.setPen(Qt.PenStyle.NoPen)
    for y, row in enumerate(g):
        x = 0
        while x < GW:
            ch = row[x]
            if ch == " ":
                x += 1
                continue
            x2 = x
            while x2 + 1 < GW and row[x2 + 1] == ch:
                x2 += 1
            col = pal.get(ch)
            if col is not None:
                p.fillRect(QRect(x0 + x * px, y0 + y * px, (x2 - x + 1) * px, px), col)
            x = x2 + 1


def pal(**kw):
    return {k: QColor(v) for k, v in kw.items()}


class St:
    """Pose handed to the builders."""
    t = 0.0
    blink = 0.0
    look = 0.0
    scared = happy = sleep = boo = False
    beh = None            # current idle behaviour name
    beh_p = 0.0           # 0..1 progress through it
    eq = None             # 5 band levels while music plays (bot equalizer)
    sink = 0              # cells the sprite sinks into the hub (ghost)


def look_dx(st):
    return 1 if st.look > 0.45 else -1 if st.look < -0.45 else 0


# --------------------------------------------------------------------------- CAT
CAT = [
    "..o..........o..",   # 2
    "..opo......opo..",
    "..opoooooooopo..",
    "..ooododdodooo..",
    "..oooooooooooo..",
    "..oooooooooooo..",
    "..ooowwwwwwooo..",
    "..oooowwwwoooo..",
    "...oowwwwwwoo...",
    "...doowwwwood...",
    "...oowwwwwwoo...",
    "...doowwwwood...",
    "...oowwwwwwoo...",
    "...ooowwwwooo...",
    "...owww..wwwo...",   # 16
]
CAT_PAL = pal(k="#43261a", o="#f2a65a", d="#c26f2b", w="#ffe6c4", p="#ff8fa8", e="#1d1414", h="#ffffff", W="#ffffff")
TAILS = [[(13, 15), (14, 15), (15, 14), (15, 13), (15, 12), (14, 11)],
         [(13, 15), (14, 15), (15, 15), (15, 14), (15, 13), (15, 12)],
         [(13, 15), (14, 14), (15, 14), (14, 13), (15, 12), (15, 11)]]


def build_cat(st):
    g = grid(CAT, 2)
    f = 1 if st.sleep else [0, 1, 2, 1][int(st.t * 2.5) % 4]
    cells = TAILS[f]
    for i, (x, y) in enumerate(cells):
        put(g, x, y, "d" if i == len(cells) - 1 else "o")
    groom = st.beh == "groom" and not st.scared
    dx = look_dx(st)
    if st.happy:
        for x0 in (3, 10):
            put(g, x0, 7, "e"); put(g, x0 + 1, 6, "e"); put(g, x0 + 2, 7, "e")
    elif st.sleep or st.blink or groom:
        for x0 in (4, 10):
            put(g, x0, 7, "e"); put(g, x0 + 1, 7, "e")
    elif st.scared:
        for x0 in (3, 10):
            stamp(g, x0, 5, ["WWW", "WeW", "WWW"])
    else:
        for x0 in (4 + dx, 10 + dx):
            stamp(g, x0, 6, ["he", "ee"])
    put(g, 7, 8, "p"); put(g, 8, 8, "p")
    if st.happy:
        put(g, 3, 8, "p"); put(g, 12, 8, "p")
        put(g, 6, 9, "e"); put(g, 7, 9, "p"); put(g, 8, 9, "p"); put(g, 9, 9, "e")
    elif st.scared:
        put(g, 7, 9, "e"); put(g, 8, 9, "e"); put(g, 7, 10, "e"); put(g, 8, 10, "e")
    else:
        put(g, 7, 9, "d"); put(g, 8, 9, "d")
    if groom:                                            # lick the paw
        up = int(st.t * 4) % 2
        stamp(g, 9, 9 - up, ["ww", "ww"])
        put(g, 8, 9, "p")
        put(g, 11, 11 - up, "o")
    if st.scared:                                        # fur puffed up, claws dug into the edge
        for y in (5, 7, 9):
            put(g, 1, y, "o"); put(g, 14, y, "o")
        for x, y in ((13, 14), (14, 14), (14, 13), (14, 12)):
            put(g, x, y, "o")
        for sg in (0, 1):
            for x, y, c in ((2, 13, "o"), (2, 14, "o"), (1, 15, "o"), (1, 16, "o"), (0, 17, "w"), (1, 17, "w")):
                put(g, x if sg == 0 else 15 - x, y, c)
    return g


# --------------------------------------------------------------------------- AXOLOTL
AXO = [
    ".....bbbbbb.....",   # 5
    "....bbbbbbbb....",
    "...bbbbbbbbbb...",
    "..bbbbbbbbbbbb..",
    "..bbbbbbbbbbbb..",
    "..bbbbbbbbbbbb..",
    "..bbbBBBBBBbbb..",
    "..bbBBBBBBBBbb..",
    "..bbBBBBBBBBbb..",
    "..bbBBBBBBBBbb..",
    "...bbBBBBBBbb...",
    "..bbb......bbb..",   # 16
]
AXO_PAL = pal(k="#7a3350", b="#ffb3c8", B="#ffdbe6", g="#ff6f9a", G="#ff3d7a", e="#2a1420", p="#ff8fb0",
              m="#c4426d", W="#ffffff")
GILLS = [[(1, 8, "g"), (0, 7, "G"), (1, 10, "g"), (0, 10, "G"), (1, 12, "g"), (0, 13, "G")],
         [(1, 8, "g"), (0, 8, "G"), (1, 10, "g"), (0, 11, "G"), (1, 12, "g"), (0, 12, "G")],
         [(1, 8, "g"), (1, 7, "g"), (0, 6, "G"), (1, 10, "g"), (0, 9, "G"), (1, 12, "g"), (0, 11, "G")]]


def build_axo(st):
    g = grid(AXO, 5)
    fl = 2 if st.scared else int(st.t * 3.2) % 2
    for sg in (0, 1):
        for x, y, c in GILLS[fl]:
            put(g, x if sg == 0 else 15 - x, y, c)
    tf = int(st.t * 2) % 2
    for x, y in ((13, 14), (14, 14), (15, 13 + tf)):
        put(g, x, y, "b")
    put(g, 3, 10, "p"); put(g, 12, 10, "p")
    bub = st.beh == "bubbles"
    if st.happy:
        for x0 in (4, 9):
            put(g, x0, 9, "e"); put(g, x0 + 1, 8, "e"); put(g, x0 + 2, 9, "e")
    elif st.sleep or st.blink:
        for x in (4, 5, 10, 11):
            put(g, x, 9, "e")
    elif st.scared:
        stamp(g, 4, 7, ["WW", "We"]); stamp(g, 10, 7, ["WW", "eW"])
    else:
        for x in (5, 10):
            put(g, x, 8, "e"); put(g, x, 9, "e")
    if st.scared or bub:
        for x, y in ((7, 10), (8, 10), (7, 11), (8, 11)):
            put(g, x, y, "e" if st.scared else "m")
    elif st.happy:
        for x in (6, 7, 8, 9):
            put(g, x, 10, "m")
        put(g, 7, 11, "e"); put(g, 8, 11, "e")
    else:
        put(g, 6, 10, "m"); put(g, 7, 11, "m"); put(g, 8, 11, "m"); put(g, 9, 10, "m")
    if st.scared:                                        # stubby arms hanging on
        for sg in (0, 1):
            for x, y, c in ((1, 14, "b"), (1, 15, "b"), (0, 16, "B"), (0, 17, "B"), (1, 17, "B")):
                put(g, x if sg == 0 else 15 - x, y, c)
    return g


# --------------------------------------------------------------------------- GHOST
GHOST = [".....wwwwww.....",   # 3
         "....wwwwwwww....",
         "...wwwwwwwwww...",
         "...wwwwwwwwww..."] + ["..wwwwwwwwwwss.."] * 9 + ["................"]
GHOST_PAL = pal(k="#5a6b99", w="#ffffff", s="#c9d9ff", e="#1c2540", p="#ffb3cc", h="#ffffff")


def build_ghost(st):
    g = grid(GHOST, 3)
    g[16] = list(["..www..ww..www..", "...ww.wwww.ww..."][int(st.t * 2.8) % 2].replace(".", " "))
    boo = st.boo or st.scared
    if boo:                                              # arms up: BOO!
        for x, y in ((1, 8), (0, 7), (14, 8), (15, 7), (1, 9), (14, 9)):
            put(g, x, y, "w")
    else:
        for x, y in ((1, 11), (1, 12), (14, 11), (14, 12)):
            put(g, x, y, "w")
    put(g, 3, 10, "p"); put(g, 12, 10, "p")
    dx = look_dx(st)
    if st.happy:
        for x0 in (4, 9):
            put(g, x0, 8, "e"); put(g, x0 + 1, 7, "e"); put(g, x0 + 2, 8, "e")
    elif st.sleep or st.blink:
        for x in (5, 6, 9, 10):
            put(g, x, 8, "e")
    elif boo:
        for x0 in (4, 9):
            stamp(g, x0, 6, ["eee", "eee", "eee", "eee"])
    else:
        for x0 in (5 + dx, 9 + dx):
            stamp(g, x0, 7, ["he", "ee", "ee"])
    if boo:
        stamp(g, 6, 11, [" ee ", "eeee", "eeee", " ee "])
    elif st.happy:
        put(g, 6, 11, "e"); put(g, 7, 12, "e"); put(g, 8, 12, "e"); put(g, 9, 11, "e")
    elif st.sleep:
        put(g, 7, 11, "e"); put(g, 8, 11, "e")
    else:
        put(g, 7, 11, "e"); put(g, 8, 11, "e")
    return g


# --------------------------------------------------------------------------- BOT
BOT = [
    ".......rr.......",   # 2
    ".......ss.......",
    "..mmmmmmmmmmmm..",
    ".mmmmmmmmmmmmmm.",
] + [".mmddddddddddmm."] * 6 + [
    ".mmmmmmmmmmmmmm.",   # 12
    "...mmmmmmmmmm...",
    "...mmmmllmmmm...",
    "...mmmmmmmmmm...",
    "...sss....sss...",   # 16
]
BOT_PAL = pal(k="#2c3750", m="#c9d5e8", s="#8fa1bd", d="#10162a", l="#5be7ff", g="#5dff9d", r="#ff5f5f",
              y="#ffd84a", L="#2a6f80", R="#6b3030")


def build_bot(st):
    g = grid(BOT, 2)
    for y in (7, 8, 9):
        put(g, 0, y, "s"); put(g, 15, y, "s")
    tip = "r" if (int(st.t * 2) % 2 or st.scared) else "R"
    if st.happy:
        tip = "g"
    if st.sleep:
        tip = "s"
    put(g, 7, 2, tip); put(g, 8, 2, tip)
    dx = 0
    if st.beh == "scan":                                  # eyes sweep left and right
        dx = [-1, 0, 1, 0][int(st.beh_p * 4) % 4]
    if st.scared:
        stamp(g, 4, 6, ["r r", " r ", "r r"]); stamp(g, 9, 6, ["r r", " r ", "r r"])
    elif st.sleep:
        if int(st.t * 1.2) % 2:
            put(g, 7, 10, "L")
    elif st.happy:
        for x0 in (4, 9):
            put(g, x0, 8, "g"); put(g, x0 + 1, 7, "g"); put(g, x0 + 2, 8, "g")
    elif st.blink:
        for x in (5, 6, 9, 10):
            put(g, x + dx, 7, "l")
    else:
        for x0 in (5 + dx, 9 + dx):
            stamp(g, x0, 7 if not st.eq else 6, ["ll", "ll"])
    if st.eq and not (st.scared or st.sleep):             # screen turns into an equalizer
        for i, x in enumerate((4, 6, 8, 10, 12)):
            h = int(st.eq[i] * 3.4 + 0.3)
            for k in range(min(3, h)):
                put(g, x, 11 - k, "g" if k == 2 else "l")
    elif not (st.scared or st.sleep or st.happy):
        for x in (6, 7, 8, 9):
            put(g, x, 10, "L")
    if st.scared:                                         # clamps
        for sg in (0, 1):
            for x, y, c in ((2, 14, "s"), (1, 15, "s"), (1, 16, "s"), (0, 17, "y"), (1, 17, "y")):
                put(g, x if sg == 0 else 15 - x, y, c)
    return g


ART = {"cat": build_cat, "axolotl": build_axo, "ghost": build_ghost, "bot": build_bot}
PALS = {"cat": CAT_PAL, "axolotl": AXO_PAL, "ghost": GHOST_PAL, "bot": BOT_PAL}
# (spring stiffness, damping, inertia gain): how each one feels when the hub is moved
PHYS = {"cat": (110.0, 9.0, 0.30), "axolotl": (55.0, 3.2, 0.34), "ghost": (26.0, 2.2, 0.45),
        "bot": (190.0, 16.0, 0.18), "custom": (110.0, 9.0, 0.30)}
BEH = {"cat": [("groom", 2.4)], "axolotl": [("bubbles", 2.6)], "ghost": [("peek", 2.8)],
       "bot": [("beep", 1.2), ("scan", 3.0)], "custom": []}

# --------------------------------------------------------------------------- particles (pixel stamps)
PARTS = {
    "heart": ([" r r ", "rrrrr", " rrr ", "  r  "], {"r": "#ff5f82"}, 1.2),
    "sweat": ([" b ", "bbb", "bbb"], {"b": "#8fd0ff"}, 0.8),
    "z": (["zzzz", "  z ", " z  ", "zzzz"], {"z": "#ffffff"}, 2.0),
    "bubble": ([" w ", "w w", " w "], {"w": "#cfeaff"}, 2.2),
    "note": ([" nn", " n ", " n ", "nn "], {"n": "#8ff0ff"}, 1.4),
    "spark": (["y"], {"y": "#ffe45c"}, 0.35),
}


class Sprite(QWidget):
    """Frameless window sitting on the hub's top edge. Body is a spring that lags behind the hub."""

    def __init__(self, ctl):
        super().__init__(None, Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint |
                         Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.WindowDoesNotAcceptFocus)
        self.ctl, self.cfg = ctl, ctl.cfg
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.st = St()
        self.s = 1.0
        self.pix = self.movie = None
        self.parts = []
        self.hist = []
        self.bass = self.mv = 0.0
        self.last = time.monotonic()
        self.next_blink, self.blink_t = self.last + 3.0, 0.0
        self.happy_until = self.scared_until = 0.0
        self.beh, self.beh_t0, self.beh_dur = None, 0.0, 1.0
        self.next_beh = self.last + random.uniform(5, 9)
        self.emit_t = {}
        self.was_scared = False
        self.reset_phys()
        self.timer = QTimer(self)
        self.timer.timeout.connect(self._tick)
        self.cfg.changed.connect(self._cfg)
        self._load()

    # ---- config / placement
    def _cfg(self, k):
        if k in ("sprite", "sprite_img"):
            self._load()
            self.beh = None
            self.parts = []
        if k.startswith("sprite") or k in ("hub_scale", "hub_w", "vis"):
            self.sync()
            self.update()

    def _load(self):
        if self.movie:
            self.movie.stop()
        self.movie = self.pix = None
        path = self.cfg["sprite_img"]
        if self.cfg["sprite"] != "custom" or not path or not os.path.exists(path):
            return
        if path.lower().endswith(".gif"):
            m = QMovie(path)
            if m.isValid():
                m.jumpToFrame(0)
                sz = m.currentImage().size()
                if max(sz.width(), sz.height()) > 256:
                    sz.scale(256, 256, Qt.AspectRatioMode.KeepAspectRatio)
                    m.setScaledSize(sz)
                m.frameChanged.connect(lambda _: self.update())
                self.movie = m
        else:
            pm = QPixmap(path)
            if not pm.isNull():
                self.pix = pm.scaled(256, 256, Qt.AspectRatioMode.KeepAspectRatio,
                                     Qt.TransformationMode.SmoothTransformation) if max(pm.width(), pm.height()) > 256 else pm

    def reset_phys(self):
        self.ox = self.oy = self.vx = self.vy = self.ang = self.angv = 0.0
        self.hp, self.hv = None, QPointF(0, 0)

    def sync(self):
        hub = self.ctl.player
        if not (self.cfg["sprite_on"] and hub.isVisible()):
            self.timer.stop()
            self.hide()
            if self.movie:
                self.movie.stop()
            return
        self.s = self.cfg["sprite_size"] / 100.0 * (0.5 + 0.5 * hub.s)
        cw = int(round(CANVAS * self.s))
        if self.width() != cw:
            self.setFixedSize(cw, cw)
        self.follow()
        if not self.isVisible():
            self.last = time.monotonic()
            self.reset_phys()
            self.show()
            self.raise_()
            self.poke(quiet=True)
            if self.movie:
                self.movie.start()
        if not self.timer.isActive():
            self.timer.start(fps_ms(self.cfg))

    def follow(self):
        hub = self.ctl.player
        cw = self.width()
        x = hub.x() + 18 * hub.s + (hub.width() - 36 * hub.s) * self.cfg["sprite_pos"] / 100.0 - cw / 2
        y = hub.y() - cw + round(2 * hub.s)
        scr = hub.screen()
        if scr is not None:
            g = scr.geometry()
            y = max(g.top(), y)
            x = min(max(g.left(), x), g.right() - cw)
        self.move(int(x), int(y))

    # ---- interaction
    def emit(self, kind, x=0.0, y=0.0, vx=0.0, vy=-20.0, life=None):
        spec = PARTS[kind]
        self.parts.append([kind, CANVAS / 2 + x + self.ox, CANVAS - 56 + y, vx, vy, time.monotonic(),
                           life or spec[2]])
        del self.parts[:-24]

    def poke(self, quiet=False):
        now = time.monotonic()
        kind = self.cfg["sprite"]
        self.vy = -170.0
        self.happy_until = now + 1.0
        if kind == "bot":
            self.beh, self.beh_t0, self.beh_dur = "beep", now, 1.2
            self.emit("note", 8, -8, 6, -22)
        if quiet:
            return
        if kind == "ghost":                                   # BOO!
            self.beh, self.beh_t0, self.beh_dur = "boo", now, 1.0
        elif kind == "axolotl":
            for i in range(3):
                self.emit("bubble", random.uniform(-8, 8), -4, random.uniform(-4, 4), -18, 1.6 + i * 0.3)
        elif kind != "bot":
            for i in range(3):
                self.emit("heart", random.uniform(-14, 14), -8 - i * 3, 0, -26)

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self.poke()

    # ---- physics
    def step(self, dt, hp, now):
        """Advance the spring body. hp: hub top-left. Returns how violent the motion is (>1 = hold on!)."""
        if self.hp is None:
            self.hp = QPointF(hp)
        hv = QPointF((hp.x() - self.hp.x()) / dt, (hp.y() - self.hp.y()) / dt)
        self.hp = QPointF(hp)
        hv = QPointF(self.hv.x() * 0.5 + hv.x() * 0.5, self.hv.y() * 0.5 + hv.y() * 0.5)
        ax, ay = (hv.x() - self.hv.x()) / dt, (hv.y() - self.hv.y()) / dt
        self.hv = hv
        if not self.cfg["sprite_react"]:
            ax = ay = 0.0
            hv = QPointF(0, 0)
        ax = ax if abs(ax) > 800 else 0.0
        ay = ay if abs(ay) > 800 else 0.0
        K, C, G = PHYS.get(self.cfg["sprite"], PHYS["cat"])
        LIM = 24.0
        self.vx += (-ax * G - K * self.ox - C * self.vx) * dt     # inertia: body lags the hub
        self.vy += (-ay * G - K * self.oy - C * self.vy) * dt
        self.ox += self.vx * dt
        self.oy += self.vy * dt
        if abs(self.ox) > LIM:
            self.ox, self.vx = math.copysign(LIM, self.ox), self.vx * 0.3
        if abs(self.oy) > LIM:
            self.oy, self.vy = math.copysign(LIM, self.oy), self.vy * 0.3
        target = max(-0.5, min(0.5, -hv.x() * 0.0007))                 # lean against the motion
        self.angv += (-80.0 * (self.ang - target) - 8.0 * self.angv) * dt
        self.ang += self.angv * dt
        lvl = math.hypot(hv.x(), hv.y()) / 1400.0 + math.hypot(ax, ay) / 16000.0
        if lvl > 1.0 and self.cfg["sprite_react"]:
            self.scared_until = now + 0.55
        self.mv = max(min(lvl, 1.5), self.mv * 0.93)
        return lvl

    def _tick(self):
        now = time.monotonic()
        dt = min(max(now - self.last, 0.004), 0.05)
        self.last = now
        hub, st, cfg = self.ctl.player, self.st, self.cfg
        kind = cfg["sprite"]
        self.step(dt, hub.pos(), now)
        st.t = now
        if now > self.next_blink:
            self.blink_t, self.next_blink = now, now + random.uniform(2.2, 5.5)
        st.blink = 1.0 if now - self.blink_t < 0.13 else 0.0
        st.look = math.sin(now * 0.45) * 0.7
        st.scared = now < self.scared_until
        st.happy = now < self.happy_until and not st.scared
        st.sleep = (not self.ctl.pb.playing) and not st.happy and not st.scared
        # music: bass hop + equalizer
        st.eq, self.bass = None, 0.0
        if cfg["sprite_dance"] and cfg["vis"] and self.ctl.pb.playing and np is not None and hub.vis is not None:
            v = hub.vis
            eq = [float(a.mean()) for a in (v[0:3], v[3:7], v[7:12], v[12:18], v[18:24])]
            self.bass = eq[0]
            if max(eq) > 0.05:
                st.eq = eq
        # idle behaviours
        calm = not (st.scared or st.sleep) and abs(self.ox) + abs(self.oy) < 1.0
        if self.beh and now - self.beh_t0 > self.beh_dur:
            self.beh = None
        if not self.beh and calm and now > self.next_beh:
            opts = BEH.get(kind)
            if opts:
                self.beh, d = random.choice(opts)
                self.beh_t0, self.beh_dur = now, d
                if self.beh == "beep":
                    self.emit("note", 8, -8, 6, -22)
            self.next_beh = now + random.uniform(8, 16)
        st.beh = self.beh
        st.beh_p = (now - self.beh_t0) / self.beh_dur if self.beh else 0.0
        st.boo = False
        st.sink = 0
        if kind == "ghost":
            if self.beh == "peek":                            # hide behind the hub, then pop up
                p = st.beh_p
                st.sink = int(18 * (p / 0.3 if p < 0.3 else 1 if p < 0.55 else max(0.0, 1 - (p - 0.55) / 0.15)))
                st.boo = 0.62 < p < 0.95
            elif self.beh == "boo":
                st.boo = True
            elif st.sleep:
                st.sink = 3                                   # dozes off half inside the hub
        # particles
        self._emit_logic(now, st, kind)
        self.parts = [q for q in self.parts if now - q[5] < q[6]]
        self.hist.append((self.ox, self.oy))
        del self.hist[:-10]
        moving = (st.scared or st.happy or abs(self.ox) + abs(self.oy) > 0.3 or abs(self.vx) + abs(self.vy) > 4
                  or self.bass > 0.04 or self.parts or self.beh or self.mv > 0.05)
        iv = fps_ms(cfg) if moving else 90          # idle breathing/blinking needs very little CPU
        if iv != self.timer.interval():
            self.timer.setInterval(iv)
        self.update()

    def _every(self, key, now, gap):
        if now - self.emit_t.get(key, 0.0) >= gap:
            self.emit_t[key] = now
            return True
        return False

    def _emit_logic(self, now, st, kind):
        if st.scared and not self.was_scared and kind != "bot":
            self.emit("sweat", 14, -14, 0, 28)
        self.was_scared = st.scared
        if kind == "bot" and st.scared and self._every("spark", now, 0.12):
            self.emit("spark", random.uniform(-14, 14), random.uniform(-26, -6), random.uniform(-30, 30), random.uniform(-30, 10))
        if st.sleep:
            if kind == "axolotl":
                if self._every("zb", now, 1.6):
                    self.emit("bubble", 2, -2, 3, -12, 2.0)
            elif kind == "bot":
                pass
            elif self._every("z", now, 1.4):
                self.emit("z", 12, -16, 6, -10)
        if kind == "axolotl" and self.beh == "bubbles" and self._every("bub", now, 0.4):
            self.emit("bubble", random.uniform(-3, 3), -2, random.uniform(-5, 5), -16, 2.0)

    # ---- painting
    def paintEvent(self, _):
        cfg, st = self.cfg, self.st
        p = QPainter(self)
        kind = cfg["sprite"]
        s, now = self.s, (st.t or time.monotonic())
        if kind not in ART:
            self._paint_custom(p, s, now)
        else:
            self._paint_pixel(p, kind, s, now)
        self._paint_parts(p, s, now)

    def _paint_pixel(self, p, kind, s, now):
        st = self.st
        p.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        px = max(2, int(round(4 * s)))
        cw = self.width()
        snap = lambda v: int(round(v / px)) * px
        lean = max(-2, min(2, int(round(self.ang * 4))))
        sy = -self.vy * 0.0011
        k = 1 if sy > 0.07 else -1 if sy < -0.07 else 0
        k += -1 if ((now * (0.7 if st.sleep else 1.1)) % 1.0) > 0.55 else 0       # breathing
        if kind == "cat" and st.sleep:
            k = -2                                                                   # curled into a loaf
        k = max(-3, min(2, k))
        g = stretch(shear(outline(ART[kind](st)), lean), k)
        if kind == "bot" and (st.scared or self.mv > 0.9):
            g = glitch(g, int(now * 14), 3 if st.scared else 1)
        x0 = (cw - GW * px) // 2 + snap(self.ox * s)
        y0 = cw - GH * px + snap(self.oy * s) + int(st.sink) * px
        if self.bass > 0.45:
            y0 -= px
        if st.scared:
            x0 += (1 if int(now * 22) % 2 else -1) * max(1, px // 2)
        pal_ = PALS[kind]
        if kind == "ghost":
            alpha = 1.0 - 0.5 * min(1.0, self.mv * 1.4)
            if st.scared:
                alpha = 0.35 + 0.25 * (int(now * 12) % 2)
            if self.mv > 0.15 and len(self.hist) > 8:                              # see-through trail
                for idx, a in ((-4, 0.22), (-8, 0.12)):
                    hx, hy = self.hist[idx]
                    p.setOpacity(a)
                    draw_grid(p, g, pal_, (cw - GW * px) // 2 + snap(hx * s), cw - GH * px + snap(hy * s) + int(st.sink) * px, px)
            p.setOpacity(alpha)
        draw_grid(p, g, pal_, x0, y0, px)
        p.setOpacity(1.0)

    def _paint_parts(self, p, s, now):
        p.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        pp = max(2, int(round(3 * s)))
        for kind, x, y, vx, vy, born, life in self.parts:
            age = now - born
            if not 0 <= age < life:
                continue
            rows, cols, _ = PARTS[kind]
            sway = math.sin(age * 6) * 3 if kind in ("heart", "bubble", "note") else 0.0
            dx = int(round((x + vx * age + sway) * s / pp)) * pp
            dy = int(round((y + vy * age) * s / pp)) * pp
            p.setOpacity(max(0.0, min(1.0, (life - age) / (life * 0.4))))
            for j, row in enumerate(rows):
                for i, ch in enumerate(row):
                    if ch != " ":
                        p.fillRect(QRect(dx + i * pp, dy + j * pp, pp, pp), QColor(cols[ch]))
        p.setOpacity(1.0)

    def _paint_custom(self, p, s, now):
        st = self.st
        p.setRenderHints(QPainter.RenderHint.Antialiasing | QPainter.RenderHint.SmoothPixmapTransform)
        p.scale(s, s)
        sy = 1 + max(-0.2, min(0.25, -self.vy * 0.0011)) + math.sin(now * 2.4) * 0.018
        shake = math.sin(now * 45) * 0.05 if st.scared else 0.0
        p.translate(CANVAS / 2 + self.ox, CANVAS + self.oy - self.bass * 3.0)
        p.rotate(math.degrees(self.ang + shake))
        p.scale(1 / sy, sy)
        p.translate(-32, -64)
        pm = self.movie.currentPixmap() if self.movie else self.pix
        if pm is None or pm.isNull():
            return
        w, h = pm.width(), pm.height()
        k = min(60.0 / w, 60.0 / h)
        tw, th = w * k, h * k
        p.drawPixmap(QRectF(32 - tw / 2, 64 - th, tw, th), pm, QRectF(pm.rect()))
