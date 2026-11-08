# LyricsFloat (LF)

A tiny hub that lets you manage whatever your playing on spotify without having to go back and fourth to the app/website.

**Build:** double-click `build.bat` → `dist\LyricsFloat.exe` (installs Python 3.12 via winget if missing, then all dependencies).
**Run from source:** `pip install -r requirements.txt` then `python lyricsfloat.py` (`--demo` previews without Spotify).

## Hub (bottom-left by default)
- prev / play-pause / next, drag the bar to seek, `♪` lyrics on/off, `Aa` cycle style, `⋯` menu. Right-click also opens the menu.
- Drag the card anywhere. **Resize:** drag the corner grip (top-right, appears on hover) or Ctrl+scroll. Size/width/radius also in Settings → Hub.
- Small audio visualizer (bars / mirror / wave) reacts to system audio (WASAPI loopback); only runs while playing.
- Optional blurred album-art background; accent colour can follow the cover.
- Scroll over `♪` to nudge lyric timing ±0.05 s (Shift: ±0.5 s).
- **Sprite:** a couple of premade sprites you can choose from like a cat, ghost, axolotl etc., as well as upload your own (if they have all the needed animations, as the sprites are made to be reactive).

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
