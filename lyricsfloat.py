"""
LyricsFloat (LF) - floating synced lyrics + a tiny glass mini-player.

Reads "now playing" from the Windows media session (Spotify app or Spotify Web in a browser),
fetches synced lyrics from LRCLIB (free, no key) and floats them on screen.

Run:  python lyricsfloat.py            (add --demo to preview without Spotify)
"""
import sys, os, time, json, bisect, argparse, tempfile, shutil
from concurrent.futures import ThreadPoolExecutor

from PySide6.QtCore import QLockFile, QObject, QTimer, Signal
from PySide6.QtGui import QColor, QImage
from PySide6.QtWidgets import QApplication, QMenu, QMessageBox, QSystemTrayIcon

from lf_core import (APP, DATA, IS_WIN, AudioTap, Config, DemoSource, DemoTap, MediaWatcher, _Mgr, accent_from,
                     fetch_lyrics, np)
from lf_sprite import Sprite
from lf_ui import MENU_QSS, Intro, Overlay, Player, SettingsWin, make_icon


class Playback:
    title = artist = album = ""
    dur = base = t0 = 0.0
    playing = has = False

    def pos(self):
        p = self.base + (time.monotonic() - self.t0 if self.playing else 0.0)
        return min(max(p, 0.0), self.dur) if self.dur > 0 else max(p, 0.0)


def set_autostart(on):
    if not IS_WIN:
        return
    try:
        import winreg
        k = winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Run", 0,
                           winreg.KEY_SET_VALUE)
        if on:
            exe = sys.executable
            cmd = f'"{exe}"' if getattr(sys, "frozen", False) else f'"{exe}" "{os.path.abspath(__file__)}"'
            winreg.SetValueEx(k, APP, 0, winreg.REG_SZ, cmd)
        else:
            try:
                winreg.DeleteValue(k, APP)
            except FileNotFoundError:
                pass
        winreg.CloseKey(k)
    except Exception:
        pass


class Controller(QObject):
    lyrics_done = Signal(str, str, object)
    offset_changed = Signal(float)
    track_changed = Signal(str)

    def __init__(self, demo=False, debug=False):
        super().__init__()
        self._ci = -1
        self.dbg = self._debug_log() if debug else None
        self.cfg = Config()
        self.pb = Playback()
        self.key = self.skey = None
        self.kind, self.lines, self.times = "none", [], []
        self.mem = {}
        self.cover_accent = None
        self.accent_qc = QColor(self.cfg["accent"])
        self.offsets_file = os.path.join(DATA, "offsets.json")
        self.songoff = self._load_offsets()
        self.offset = self.cfg["offset"]
        self._save_t = QTimer(self)
        self._save_t.setSingleShot(True)
        self._save_t.timeout.connect(self._save_offsets)
        self.pool = ThreadPoolExecutor(1)
        self.lyrics_done.connect(self._on_lyrics)
        live = not demo and _Mgr is not None
        self.src = MediaWatcher() if live else DemoSource()
        self.tap = AudioTap() if live else DemoTap()
        self.src.state.connect(self._on_state)
        self.src.cover.connect(self._on_cover)
        self.player = Player(self)
        self.overlay = Overlay(self)
        self.sprite = Sprite(self)
        self.settings = self.intro = None
        self.cfg.changed.connect(self._cfg)
        self._tray()
        self._ui_t = QTimer(self)
        self._ui_t.timeout.connect(self._tick_ui)
        self._ui_t.start(500)
        QTimer.singleShot(0, self.launch)

    @staticmethod
    def _debug_log():
        os.makedirs(DATA, exist_ok=True)
        f = open(os.path.join(DATA, "debug.log"), "a", buffering=1, encoding="utf-8")
        return lambda s: f.write(f"{time.strftime('%H:%M:%S')} {s}\n")

    # ---- launch / hub visibility
    def launch(self):
        kind = self.cfg["intro"]
        self.play_intro(kind) if kind != "none" else self._show_hub()

    def _show_hub(self):
        self.player.show()
        self.player.apply_glass()
        self._refresh()

    def play_intro(self, kind=None):
        if self.intro is not None:
            return
        kind = kind or (self.cfg["intro"] if self.cfg["intro"] != "none" else "slide")
        self.player.hide()
        self.intro = Intro(self.player, kind)
        self.intro.done.connect(self._intro_done)
        self.intro.start()

    def _intro_done(self):
        self.intro.deleteLater()
        self.intro = None
        self._show_hub()

    def toggle_hub(self):
        if self.player.isVisible():
            self.player.hide()
        else:
            self._show_hub()

    def _tray(self):
        self.tray = None
        if not QSystemTrayIcon.isSystemTrayAvailable():
            return
        self.tray = QSystemTrayIcon(make_icon(), self)
        self.tray_menu = QMenu()
        self.tray_menu.setStyleSheet(MENU_QSS)
        self.tray_menu.addAction("Show / hide hub").triggered.connect(self.toggle_hub)
        self.tray_menu.addAction("Settings…").triggered.connect(lambda: self.open_settings())
        self.tray_menu.addSeparator()
        self.tray_menu.addAction("Quit").triggered.connect(self.quit)
        self.tray.setContextMenu(self.tray_menu)
        self.tray.setToolTip(APP)
        self.tray.activated.connect(
            lambda r: self.toggle_hub() if r == QSystemTrayIcon.ActivationReason.Trigger else None)
        self.tray.show()

    def open_settings(self, tab=None):
        if self.settings is None:
            self.settings = SettingsWin(self)
        self.settings.show_tab(tab)

    # ---- config reactions
    def _cfg(self, k):
        if k in ("accent", "accent_mode"):
            self._accent()
        elif k in ("vis", "lyrics_on"):
            self._refresh()
        elif k == "autostart":
            set_autostart(self.cfg["autostart"])

    def _accent(self):
        use = self.cfg["accent_mode"] == "cover" and self.cover_accent is not None
        self.accent_qc = QColor(self.cover_accent if use else self.cfg["accent"])
        self.overlay._sig = None
        self.player.update()

    # ---- per-song timing
    def _load_offsets(self):
        try:
            with open(self.offsets_file, encoding="utf-8") as f:
                return {str(k): float(v) for k, v in json.load(f).items()}
        except Exception:
            return {}

    def _save_offsets(self):
        try:
            os.makedirs(DATA, exist_ok=True)
            with open(self.offsets_file, "w", encoding="utf-8") as f:
                json.dump(self.songoff, f)
        except Exception:
            pass

    def set_offset(self, v):
        v = round(max(-30.0, min(30.0, float(v))), 2)
        if v == self.offset:
            return
        self.offset = v
        if self.cfg["remember_offset"] and self.skey:
            if v == 0:
                self.songoff.pop(self.skey, None)
            else:
                self.songoff[self.skey] = v
            self._save_t.start(600)
        else:
            self.cfg.set("offset", v)
        self.offset_changed.emit(v)

    def nudge(self, d):
        self.set_offset(self.offset + d)
        self.player.flash(f"lyric timing {self.offset:+.2f} s")

    # ---- state from the media session
    def _on_state(self, d):
        pb, now = self.pb, time.monotonic()
        if d is None:
            if pb.has:
                pb.has = pb.playing = False
                self.key = self.skey = None
                self.kind, self.lines, self.times = "none", [], []
                self.player.set_cover(None)
            self._refresh()
            return
        key = f"{d['artist']}|{d['title']}"
        if key != self.key:
            pb.title, pb.artist, pb.album = d["title"], d["artist"], d["album"]
            pb.base, pb.t0, pb.has, pb.dur = d["pos"], now, True, d["dur"]
            self.key, self.skey = key, key.lower()
            self.kind, self.lines, self.times = "loading", [], []
            self.overlay.reset()
            self.offset = (self.songoff.get(self.skey, self.cfg["offset"]) if self.cfg["remember_offset"]
                           else self.cfg["offset"])
            self.offset_changed.emit(self.offset)
            self.track_changed.emit(f"{d['artist']} — {d['title']}")
            self.cover_accent = None
            self.player.set_cover(None)
            self._accent()
            self.sprite.poke(quiet=True)
            self._lookup(key, d)
        else:
            err = d["pos"] - pb.pos()
            if self.dbg:
                self.dbg(f"poll pos={d['pos']:.2f} ours={pb.pos():.2f} err={err:+.2f} playing={d['playing']}")
            if d["playing"] != pb.playing or abs(err) > 2.0:   # pause/resume or a real seek: jump
                pb.base, pb.t0 = d["pos"], now
            elif abs(err) > 0.15:                              # drift/jitter: ease toward it, never jump
                pb.base += err * 0.2
            if pb.dur == 0 and d["dur"] > 0 and self.kind == "none":
                pb.dur = d["dur"]
                self._lookup(key, d)
            pb.dur = d["dur"]
        pb.playing = d["playing"]
        self._refresh()

    def _on_cover(self, ck, data):
        if ck != f"{self.pb.artist}|{self.pb.title}":
            return
        img = QImage.fromData(data)
        if img.isNull():
            return
        self.player.set_cover(img)
        self.cover_accent = accent_from(img)
        self._accent()

    def _lookup(self, key, d):
        if key in self.mem:
            self._apply(*self.mem[key])
            return
        self.pool.submit(self._work, key, d["title"], d["artist"], d["album"], d["dur"])

    def _work(self, key, title, artist, album, dur):
        if key != self.key:
            return
        try:
            res = fetch_lyrics(title, artist, album, dur)
        except Exception:
            res = None
        self.lyrics_done.emit(key, res[0] if res else "none", res[1] if res else [])

    def _on_lyrics(self, key, kind, lines):
        if kind != "none":
            if len(self.mem) > 50:
                self.mem.pop(next(iter(self.mem)))
            self.mem[key] = (kind, lines)
        if key == self.key:
            self._apply(kind, lines)

    def _apply(self, kind, lines):
        if kind == "plain":  # no timestamps: spread evenly over the song
            n = len(lines)
            dur = self.pb.dur or n * 4
            start = min(8.0, dur * 0.05)
            step = max(dur - start - 5, n) / n
            lines = [(start + i * step, t) for i, t in enumerate(lines)]
        self.kind, self.lines, self.times = kind, lines, [t for t, _ in lines]
        self._ci = -1
        self.overlay.reset()
        self._refresh()

    def _refresh(self):
        want = self.cfg["lyrics_on"] and self.pb.playing and self.kind in ("synced", "plain")
        if want and not self.overlay.isVisible():
            self.overlay.start()
        elif not want and self.overlay.isVisible():
            self.overlay.stop()
        if self.cfg["vis"] and self.pb.playing and self.player.isVisible():
            self.tap.start()
        else:
            self.tap.stop()
        self.player.sync_vis()
        self.player.update()

    def _tick_ui(self):
        if self.pb.playing and self.player.isVisible() and self.player.drag != "seek":
            self.player.update(self.player._dev(self.player.track.adjusted(-8, -6, 8, 20)))

    # ---- lyric lookup helpers
    def current_index(self):
        if not self.times:
            return -1
        t = self.pb.pos() + self.offset
        i = bisect.bisect_right(self.times, t) - 1
        c = self._ci
        # hysteresis: only step back a line if we are clearly before it (real seek), not on timing jitter
        if 0 <= c < len(self.times) and i < c and t > self.times[c] - 1.0 and self.times[c] - t >= 0:
            i = c
        if i != c and self.dbg:
            self.dbg(f"line {c} -> {i} at t={t:.2f}")
        self._ci = i
        return i

    def line_text(self, i):
        return self.lines[i][1] if 0 <= i < len(self.lines) else ""

    # ---- user actions
    def toggle(self):
        pb = self.pb
        if pb.has:
            pb.base, pb.t0, pb.playing = pb.pos(), time.monotonic(), not pb.playing
            self._refresh()
        self.src.toggle()

    def nxt(self): self.src.next()
    def prev(self): self.src.prev()

    def seek(self, sec):
        self.pb.base, self.pb.t0 = sec, time.monotonic()
        self.src.seek(sec)

    def set_style(self, key):
        self.cfg.set("style", key)

    def cycle_style(self):
        from lf_core import STYLES
        keys = [k for k, _ in STYLES]
        self.set_style(keys[(keys.index(self.cfg["style"]) + 1) % len(keys)])

    def set_vis(self, k):
        if k == "off":
            self.cfg.set("vis", False)
        else:
            self.cfg.set("vis_style", k)
            self.cfg.set("vis", True)

    def set_sprite(self, k):
        if k == "off":
            self.cfg.set("sprite_on", False)
        elif k == "custom" and not (self.cfg["sprite_img"] and os.path.exists(self.cfg["sprite_img"])):
            self.pick_sprite_image()
        else:
            self.cfg.set("sprite", k)
            self.cfg.set("sprite_on", True)

    def pick_sprite_image(self):
        from PySide6.QtWidgets import QFileDialog
        f, _ = QFileDialog.getOpenFileName(self.settings, "Choose a sprite image", "",
                                           "Images (*.png *.gif *.webp *.jpg *.jpeg *.bmp)")
        if not f:
            return
        os.makedirs(DATA, exist_ok=True)
        old = self.cfg["sprite_img"]
        dst = os.path.join(DATA, f"sprite_{int(time.time())}{os.path.splitext(f)[1].lower()}")
        shutil.copy(f, dst)
        self.cfg.set("sprite_img", dst)
        self.cfg.set("sprite", "custom")
        self.cfg.set("sprite_on", True)
        if old and old != dst and os.path.dirname(old) == DATA and os.path.basename(old).startswith("sprite_"):
            try:
                os.remove(old)
            except OSError:
                pass

    def reset_position(self):
        self.cfg.set("hub_pos", "")
        self.player._place()

    def reset_all(self):
        if QMessageBox.question(self.settings, APP, "Reset every setting to its default?") == \
                QMessageBox.StandardButton.Yes:
            self.cfg.reset()
            self.reset_position()

    def quit(self):
        self._save_offsets()
        self.tap.stop()
        self.cfg.q.sync()
        QApplication.quit()


def main():
    ap = argparse.ArgumentParser(description="LyricsFloat")
    ap.add_argument("--demo", action="store_true", help="preview with a fake player")
    ap.add_argument("--debug", action="store_true", help="log playback sync to LOCALAPPDATA/LyricsFloat/debug.log")
    args = ap.parse_args()
    app = QApplication(sys.argv)
    app.setApplicationName(APP)
    app.setQuitOnLastWindowClosed(False)
    app.setWindowIcon(make_icon())
    lock = QLockFile(os.path.join(tempfile.gettempdir(), "lyricsfloat.lock"))
    if not args.demo and not lock.tryLock(100):
        sys.exit(0)  # already running
    ctl = Controller(demo=args.demo, debug=args.debug)  # keep a reference
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
