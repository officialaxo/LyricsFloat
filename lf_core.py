"""LyricsFloat core: settings, lyrics lookup, Windows media session, audio tap."""
import sys, os, re, json, time, math, hashlib, threading, asyncio, importlib
import urllib.request, urllib.parse
from datetime import datetime, timezone

from PySide6.QtCore import QBuffer, QIODevice, QObject, QSettings, QTimer, Qt, Signal
from PySide6.QtGui import QColor, QImage, QLinearGradient, QPainter

try:
    import numpy as np
except Exception:  # visualizer is optional
    np = None

APP = "LyricsFloat"
IS_WIN = sys.platform == "win32"
DATA = os.path.join(os.environ.get("LOCALAPPDATA") or os.path.expanduser("~/.local/share"), APP)
CACHE = os.path.join(DATA, "cache")

STYLES = [("float", "Float"), ("karaoke", "Karaoke"), ("drift", "Drift"), ("stack", "Stack"), ("minimal", "Minimal")]
WEIGHTS = [("light", "Light"), ("regular", "Regular"), ("medium", "Medium"), ("demibold", "Semibold"),
           ("bold", "Bold"), ("black", "Black")]
VIS_STYLES = [("bars", "Bars"), ("mirror", "Mirror"), ("wave", "Wave")]

DEFAULTS = dict(
    # lyrics
    lyrics_on=True, style="float", font="Segoe UI Variable Display", weight="demibold", font_size=34,
    y_off=0, max_w=72, preview=True, shadow="soft", text_opacity=100,
    # colors
    color_mode="solid", color1="#ffffff", color2="#8fd3ff", color3="#ffb8ec", grad_angle=20, grad_speed=6,
    accent_mode="cover", accent="#8fd3ff",
    # hub
    hub_scale=100, hub_w=348, hub_font="Segoe UI Variable Display", glass=True, tint=60, radius=24,
    cover_bg=True, cover_blur=6, cover_dim=50, hub_pos="",
    # visualizer
    vis=True, vis_style="bars", vis_gain=100,
    # effects
    intro="slide", hop_fx=True,
    # timing / system
    offset=0.0, remember_offset=True, perf="normal", autostart=False,
    # sprite
    sprite_on=True, sprite="cat", sprite_img="", sprite_size=100, sprite_pos=18,
    sprite_react=True, sprite_dance=True,
)


class Config(QObject):
    """Typed settings with change notifications (key name is emitted)."""
    changed = Signal(str)

    def __init__(self):
        super().__init__()
        self.q = QSettings(APP, APP)
        self.v = {}
        for k, d in DEFAULTS.items():
            try:
                self.v[k] = self.q.value(k, d, type(d))
            except Exception:
                self.v[k] = d

    def __getitem__(self, k):
        return self.v[k]

    def set(self, k, val):
        if self.v.get(k) == val:
            return
        self.v[k] = val
        self.q.setValue(k, val)
        self.changed.emit(k)

    def reset(self):
        for k, d in DEFAULTS.items():
            self.set(k, d)


def fps_ms(cfg):
    return {"eco": 50, "normal": 33, "smooth": 16}.get(cfg["perf"], 33)


# --------------------------------------------------------------------------- small helpers
def ease(x):
    x = min(max(x, 0.0), 1.0)
    return 1 - (1 - x) ** 3


def eo_back(x, c=1.4):
    x = min(max(x, 0.0), 1.0) - 1
    return 1 + (c + 1) * x ** 3 + c * x ** 2


def eo_expo(x):
    x = min(max(x, 0.0), 1.0)
    return 1.0 if x >= 1 else 1 - 2 ** (-10 * x)


def fmt(sec):
    sec = max(0, int(sec))
    return f"{sec // 60}:{sec % 60:02d}"


def _secs(x):
    try:
        return x.total_seconds()
    except AttributeError:
        return float(getattr(x, "duration", 0)) / 1e7


def accent_from(img):
    """Pick a vivid accent colour from cover art."""
    s = img.scaled(24, 24, Qt.AspectRatioMode.IgnoreAspectRatio, Qt.TransformationMode.SmoothTransformation)
    best, bs = None, 0.0
    for y in range(24):
        for x in range(24):
            h, sat, v, _ = s.pixelColor(x, y).getHsvF()
            score = sat * v * (0.3 + sat) * (0.2 if v < 0.25 else 1.0)
            if score > bs:
                best, bs = (h, sat, v), score
    if best is None or bs < 0.05:
        return QColor("#cfe3ff")
    h, sat, v = best
    return QColor.fromHsvF(max(h, 0.0), max(sat, 0.5), max(v, 0.88))


def make_demo_cover(sz=300):
    img = QImage(sz, sz, QImage.Format.Format_ARGB32)
    p = QPainter(img)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    g = QLinearGradient(0, 0, sz, sz)
    g.setColorAt(0, QColor("#6a2cf5"))
    g.setColorAt(0.55, QColor("#f0359a"))
    g.setColorAt(1, QColor("#ff9a3c"))
    p.fillRect(img.rect(), g)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor(255, 255, 255, 60))
    p.drawEllipse(-40, 120, 220, 220)
    p.setBrush(QColor(20, 220, 255, 150))
    p.drawEllipse(170, 30, 110, 110)
    p.end()
    buf = QBuffer()
    buf.open(QIODevice.OpenModeFlag.WriteOnly)
    img.save(buf, "PNG")
    return bytes(buf.data())


# --------------------------------------------------------------------------- lyrics
LRC_TS = re.compile(r"\[(\d+):(\d+(?:\.\d+)?)\]")
DEMO = ["Floating words on glass tonight", "Every line arrives on time", "Drifting through the quiet light",
        "Soft little letters learn to fly", "Hold the chorus, let it ring", "Pulse and shimmer, hum and sway",
        "Nothing here is heavy now", "Just the music and the view"]
DEMO_LINES = [(2.0 + 4 * i, DEMO[i % len(DEMO)]) for i in range(28)]


def parse_lrc(text):
    out = []
    for raw in text.splitlines():
        stamps = LRC_TS.findall(raw)
        if not stamps:
            continue
        body = re.sub(r"<\d+:\d+(?:\.\d+)?>", "", LRC_TS.sub("", raw)).strip()
        for m, s in stamps:
            out.append((int(m) * 60 + float(s), body))
    out.sort(key=lambda x: x[0])
    return out


def clean_title(t):
    t = re.sub(r"\s*[\(\[].*?[\)\]]", "", t)
    t = re.sub(r"\s+-\s+(remaster(ed)?|live|mono|stereo|single|radio|version|\d{4}).*$", "", t, flags=re.I)
    return t.strip() or t


def _get(url):
    req = urllib.request.Request(url, headers={"User-Agent": f"{APP}/2.0"})
    with urllib.request.urlopen(req, timeout=8) as r:
        return json.loads(r.read().decode("utf-8"))


def _pick(d):
    if not isinstance(d, dict):
        return None
    if d.get("syncedLyrics"):
        lines = parse_lrc(d["syncedLyrics"])
        if lines:
            return "synced", lines
    if d.get("plainLyrics"):
        lines = [l.strip() for l in d["plainLyrics"].splitlines() if l.strip()]
        if lines:
            return "plain", lines
    return None


def _best(items, dur):
    items = [i for i in items if isinstance(i, dict) and not i.get("instrumental")]
    if dur > 0:
        items = [i for i in items if abs((i.get("duration") or 0) - dur) <= 4]
    items.sort(key=lambda i: (not i.get("syncedLyrics"), abs((i.get("duration") or 0) - dur)))
    return _pick(items[0]) if items else None


def fetch_lyrics(title, artist, album, dur):
    """Returns ('synced', [(t, text)]) | ('plain', [text]) | None."""
    if (artist, title) == ("LyricsFloat", "Demo Track"):
        return "synced", DEMO_LINES
    ck = hashlib.sha1(f"{artist}|{title}|{int(dur)}".lower().encode()).hexdigest()
    cf = os.path.join(CACHE, ck + ".json")
    try:
        with open(cf, encoding="utf-8") as f:
            d = json.load(f)
        if d["kind"] == "synced":
            return "synced", [(float(t), s) for t, s in d["lines"]]
        return "plain", list(d["lines"])
    except Exception:
        pass
    q = urllib.parse.urlencode
    base = "https://lrclib.net/api/"
    first_artist = re.split(r",|&| feat\.? | x ", artist, flags=re.I)[0].strip()
    params = {"track_name": title, "artist_name": artist, "album_name": album}
    if dur > 0:
        params["duration"] = int(dur)
    params = {k: v for k, v in params.items() if v}
    tries = [
        lambda: _pick(_get(base + "get?" + q(params))),
        lambda: _best(_get(base + "search?" + q({"track_name": title, "artist_name": artist})), dur),
        lambda: _best(_get(base + "search?" + q({"track_name": clean_title(title),
                                                  "artist_name": first_artist})), dur),
    ]
    found = None
    for t in tries:
        try:
            res = t()
        except Exception:
            continue
        if res and res[0] == "synced":
            found = res
            break
        if res and not found:
            found = res
    if found:
        try:
            os.makedirs(CACHE, exist_ok=True)
            with open(cf, "w", encoding="utf-8") as f:
                json.dump({"kind": found[0], "lines": found[1]}, f)
        except Exception:
            pass
    return found


# --------------------------------------------------------------------------- Windows media session
_Mgr = _PS = _Streams = None
for _mod in ("winsdk", "winrt"):
    try:
        _m = importlib.import_module(_mod + ".windows.media.control")
        _Mgr = _m.GlobalSystemMediaTransportControlsSessionManager
        _PS = _m.GlobalSystemMediaTransportControlsSessionPlaybackStatus
        try:
            _Streams = importlib.import_module(_mod + ".windows.storage.streams")
        except Exception:
            pass
        break
    except Exception:
        pass


class MediaWatcher(QObject):
    """Polls the Windows media session once a second on a background thread."""
    state = Signal(object)
    cover = Signal(str, object)

    def __init__(self):
        super().__init__()
        self._loop = None
        self._session = None
        self._ck = self._cover_done = None
        self._tries = 0
        threading.Thread(target=self._run, daemon=True).start()

    def _run(self):
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        self._loop.run_until_complete(self._main())

    async def _main(self):
        self._mgr = await _Mgr.request_async()
        while True:
            try:
                await self._poll()
            except Exception:
                self.state.emit(None)
            await asyncio.sleep(1.0)

    async def _thumb(self, props):
        t = props.thumbnail
        if t is None or _Streams is None:
            return None
        st = await t.open_read_async()
        size = int(st.size)
        if size <= 0 or size > 8_000_000:
            return None
        buf = _Streams.Buffer(size)
        await st.read_async(buf, size, _Streams.InputStreamOptions.READ_AHEAD)
        rd = _Streams.DataReader.from_buffer(buf)
        arr = bytearray(rd.unconsumed_buffer_length)
        rd.read_bytes(arr)
        return bytes(arr)

    async def _poll(self):
        sessions = list(self._mgr.get_sessions())

        def playing(s):
            return s.get_playback_info().playback_status == _PS.PLAYING

        def spot(s):
            return "spotify" in (s.source_app_user_model_id or "").lower()

        pick = None
        for pred in (lambda s: spot(s) and playing(s), playing, spot):
            pick = next((s for s in sessions if pred(s)), None)
            if pick:
                break
        pick = pick or self._mgr.get_current_session()
        self._session = pick
        if pick is None:
            self.state.emit(None)
            return
        props = await pick.try_get_media_properties_async()
        tl = pick.get_timeline_properties()
        is_playing = playing(pick)
        pos = _secs(tl.position)
        dur = _secs(tl.end_time) - _secs(tl.start_time)
        if is_playing:
            lu = tl.last_updated_time
            if lu.tzinfo is None:
                lu = lu.replace(tzinfo=timezone.utc)
            pos += max(0.0, (datetime.now(timezone.utc) - lu).total_seconds())
        if dur > 0:
            pos = min(pos, dur)
        title, artist = props.title or "", props.artist or ""
        self.state.emit(dict(title=title, artist=artist, album=props.album_title or "",
                             pos=max(pos, 0.0), dur=max(dur, 0.0), playing=is_playing))
        ck = f"{artist}|{title}"
        if ck != self._ck:
            self._ck, self._tries = ck, 0
        if ck != self._cover_done and self._tries < 4:
            self._tries += 1
            try:
                data = await self._thumb(props)
            except Exception:
                data = None
            if data:
                self._cover_done = ck
                self.cover.emit(ck, data)

    def _submit(self, fn):
        if self._loop and self._session is not None:
            asyncio.run_coroutine_threadsafe(self._cmd(fn), self._loop)

    async def _cmd(self, fn):
        try:
            await fn(self._session)
        except Exception:
            pass
        await asyncio.sleep(0.3)
        try:
            await self._poll()
        except Exception:
            pass

    def toggle(self): self._submit(lambda s: s.try_toggle_play_pause_async())
    def next(self): self._submit(lambda s: s.try_skip_next_async())
    def prev(self): self._submit(lambda s: s.try_skip_previous_async())
    def seek(self, sec): self._submit(lambda s: s.try_change_playback_position_async(int(sec * 1e7)))


class DemoSource(QObject):
    """Fake player for previewing the UI without Spotify (--demo, or non-Windows)."""
    state = Signal(object)
    cover = Signal(str, object)

    def __init__(self):
        super().__init__()
        self.pos, self.playing, self.last = 20.0, True, time.monotonic()
        self.t = QTimer(self)
        self.t.timeout.connect(self.emit)
        self.t.start(1000)
        QTimer.singleShot(0, self.emit)
        QTimer.singleShot(60, lambda: self.cover.emit("LyricsFloat|Demo Track", make_demo_cover()))

    def emit(self):
        now = time.monotonic()
        if self.playing:
            self.pos = (self.pos + now - self.last) % 120
        self.last = now
        self.state.emit(dict(title="Demo Track", artist="LyricsFloat", album="Demo",
                             pos=self.pos, dur=120.0, playing=self.playing))

    def toggle(self): self.emit(); self.playing = not self.playing; self.emit()
    def seek(self, s): self.pos, self.last = s, time.monotonic(); self.emit()
    def next(self): self.seek(0.0)
    def prev(self): self.seek(0.0)


# --------------------------------------------------------------------------- audio tap (WASAPI loopback)
class AudioTap:
    """System-audio loopback -> N log-spaced band levels (0..1). Only runs while started."""
    N = 24

    def __init__(self):
        self.levels = np.zeros(self.N) if np is not None else None
        self.stamp = 0.0
        self.status, self.err = "idle", ""   # idle | starting | on | unavailable
        self._pa = self._stream = None
        self._gen = 0
        self._lock = threading.Lock()
        self._ref = 1e-3

    def setup_bands(self, n, sr):
        self._n = n
        self._win = np.hanning(n).astype(np.float32)
        edges = np.geomspace(45, min(14000, sr / 2 - 1), self.N + 1)
        idx = np.clip((edges * n / sr).astype(int), 1, n // 2).tolist()
        for i in range(1, len(idx)):
            idx[i] = max(idx[i], idx[i - 1] + 1)
        self._idx = np.array(idx)
        self._cnt = np.diff(self._idx).astype(np.float32)
        self._tilt = np.linspace(1.0, 4.0, self.N).astype(np.float32)

    def process(self, x):
        """x: mono float32 samples scaled to +-1. Updates self.levels."""
        n = self._n
        x = x[:n] if x.size >= n else np.pad(x, (0, n - x.size))
        mag = np.abs(np.fft.rfft(x * self._win)) / n
        b = np.add.reduceat(mag, self._idx)[:-1] / self._cnt * self._tilt
        pk = float(b.max())
        if pk < 1e-5:
            lv = np.zeros(self.N)
        else:
            self._ref = max(self._ref * 0.997, pk)
            lv = np.clip(b / self._ref, 0.0, 1.0) ** 0.75
        self.levels, self.stamp = lv, time.monotonic()

    def start(self):
        if np is None or self.status in ("starting", "on", "unavailable"):
            return
        self.status = "starting"
        self._gen += 1
        threading.Thread(target=self._open, args=(self._gen,), daemon=True).start()

    def stop(self):
        if self.status in ("idle", "unavailable"):
            return
        self._gen += 1
        self.status = "idle"
        self.levels = np.zeros(self.N)
        threading.Thread(target=self._close, args=(self._gen,), daemon=True).start()

    def read(self):
        if self.levels is None:
            return None
        if time.monotonic() - self.stamp > 0.3:  # loopback sends nothing while silent
            return np.zeros(self.N)
        return self.levels

    def _teardown(self):
        try:
            if self._stream:
                self._stream.stop_stream()
                self._stream.close()
            if self._pa:
                self._pa.terminate()
        except Exception:
            pass
        self._stream = self._pa = None

    def _close(self, gen):
        with self._lock:
            if gen == self._gen:
                self._teardown()

    def _open(self, gen):
        with self._lock:
            if gen != self._gen:
                return
            self._teardown()
            try:
                import pyaudiowpatch as pa
                p = pa.PyAudio()
                self._pa = p
                api = p.get_host_api_info_by_type(pa.paWASAPI)
                dev = p.get_device_info_by_index(api["defaultOutputDevice"])
                if not dev.get("isLoopbackDevice"):
                    for lb in p.get_loopback_device_info_generator():
                        if dev["name"] in lb["name"]:
                            dev = lb
                            break
                    else:
                        raise RuntimeError("no loopback device")
                ch, sr, n = int(dev["maxInputChannels"]), int(dev["defaultSampleRate"]), 2048
                self.setup_bands(n, sr)

                def cb(data, frames, tinfo, flags):
                    try:
                        x = np.frombuffer(data, dtype=np.int16).astype(np.float32) / 32768.0
                        if ch > 1:
                            x = x[: x.size // ch * ch].reshape(-1, ch).mean(axis=1)
                        self.process(x)
                    except Exception:
                        pass
                    return (None, pa.paContinue)

                self._stream = p.open(format=pa.paInt16, channels=ch, rate=sr, frames_per_buffer=n,
                                      input=True, input_device_index=dev["index"], stream_callback=cb)
                self.status = "on" if gen == self._gen else "idle"
            except Exception as e:
                self.err = str(e)
                self._teardown()
                self.status = "unavailable"


class DemoTap:
    """Synthetic spectrum for --demo."""
    N = AudioTap.N
    status, err = "on", ""

    def start(self): pass
    def stop(self): pass

    def read(self):
        t = time.monotonic()
        i = np.arange(self.N)
        beat = max(0.0, math.sin(t * 2 * math.pi * 2.0)) ** 3
        v = (0.6 * np.exp(-i / 9.0) * (0.35 + 0.65 * beat)
             + 0.28 * (0.5 + 0.5 * np.sin(t * 3 + i * 0.7)) * np.exp(-i / 22.0)
             + 0.07 * (0.5 + 0.5 * np.sin(t * 9 + i)))
        return np.clip(v, 0, 1)
