# LyricsFloat (LF)

Floating synced lyrics + a tiny glass mini-player for whatever is playing on Windows (Spotify app or Spotify Web).

**Build:** double-click `build.bat` → `dist\LyricsFloat.exe` (installs Python 3.12 via winget if missing, then all dependencies).
**Run from source:** `pip install -r requirements.txt` then `python lyricsfloat.py` (`--demo` previews without Spotify).

## Hub (bottom-left by default)
- prev / play-pause / next, drag the bar to seek, `♪` lyrics on/off, `Aa` cycle style, `⋯` menu. Right-click also opens the menu.
- Drag the card anywhere. **Resize:** drag the corner grip (top-right, appears on hover) or Ctrl+scroll. Size/width/radius also in Settings → Hub.
- Small audio visualizer (bars / mirror / wave) reacts to system audio (WASAPI loopback); only runs while playing.
- Optional blurred album-art background; accent colour can follow the cover.
- Scroll over `♪` to nudge lyric timing ±0.05 s (Shift: ±0.5 s).
- **Sprite:** a pixel-art buddy (cat, axolotl, ghost, bot, or your own PNG/GIF) sits on the hub. Each has its own personality: the **cat** grooms, puffs up and digs in its claws when yanked, and curls into a loaf to sleep; the **axolotl** is jelly-soft, flares its gills and blows bubbles; the **ghost** turns see-through and leaves a trail when moved, plays peek-a-boo (BOO!) and dozes half-sunk into the hub; the **bot** glitches and throws sparks when shaken, turns its screen into an equalizer while music plays and beeps. All blink, sleep when paused, hop on a new song and react to clicks. Settings → Sprite (or `⋯ → Sprite`).
- Tray icon: show/hide hub, Settings, Quit.

## Settings (⋯ → Settings…)
- **Lyrics:** style (Float, Karaoke, Drift, Stack, Minimal), font, weight, size, vertical position, width, opacity, shadow/glow.
- **Colors:** solid, gradient or animated gradient (3 colors, angle, speed) + presets; accent colour.
- **Hub:** size, width, corner radius, glass blur/tint, cover background, hub font, visualizer.
- **Effects:** launch animation (none / slide in + shine / break through the screen); monitor-change effect when you drag the hub to another screen.
- **Timing:** ±30 s offset in 0.01 s steps, nudge buttons, remembered per song.
- **General:** animation smoothness (Eco = lowest CPU), start with Windows.

Lyrics come from LRCLIB (no key); found lyrics are cached in `%LOCALAPPDATA%\LyricsFloat\cache`.
Next/previous/seek need Spotify Premium (Windows just forwards the command to Spotify).
If the visualizer says nothing happens, `PyAudioWPatch` failed to install for your Python version; use Python 3.12.

If lyrics ever misbehave, run `lyricsfloat.exe --debug` and send `%LOCALAPPDATA%\LyricsFloat\debug.log`.
