# ClipLab

**Turns your League of Legends replays into ready-to-post vertical shorts, automatically.**

Save a replay in OBS (or let the auto-clipper catch your multikills), and about a minute later you have 1080×1920 shorts trimmed to the action, with **TRIPLE KILL**-style text on screen and a caption ready to paste. A small web app lets you watch, keep, toss, re-cut and download them, and stitch a game's best plays into a highlight reel.

- **Two looks per clip:** *Blur fill* (the whole fight, over a blurred backdrop) and *Center crop* (fills the phone screen).
- **Smart trims:** with a tag file from the auto-clipper, each short runs from just before the first kill to just after the last.
- **Re-cut any short** from its raw clip in the app, nudging start and end by half a second.
- **Highlight reels** of a whole game, or of everything you've kept.
- **Your watermark,** loudness levelled for social apps, captions with hashtags.
- **One PC or two:** run it all on your gaming PC, or hand the rendering to an old laptop over your home network.

## How it works

```
OBS replay buffer ──▶ watch folder ──▶ ClipLab engine (ffmpeg) ──▶ output folder ──▶ ClipLab app
 (+ optional League auto-clipper writes a tag file next to each clip)            http://localhost:8765
```

Everything runs locally. Nothing is uploaded anywhere.

## Requirements
- Windows 10 or 11
- [OBS Studio](https://obsproject.com/) with the **Replay Buffer**
- Python 3.12+ and ffmpeg (the setup script installs both with winget)

## Quick start (one PC)
1. **Get the code:**
   ```
   git clone https://github.com/ccarlson41/cliplab.git %USERPROFILE%\ClipLab
   ```
2. **Run setup**, telling it where OBS saves replays:
   ```
   powershell -ExecutionPolicy Bypass -File %USERPROFILE%\ClipLab\scripts\setup.ps1 -WatchDir C:\Clips
   ```
   It installs Python and ffmpeg if needed, writes `config.json`, starts ClipLab, and sets it to start when you sign in.
3. **Set up OBS:** Settings → Output → Replay Buffer: on, 45–60 seconds. Set the recording path to the same folder (`C:\Clips`) and give *Save Replay* a hotkey.
4. Play, press your replay hotkey after a big play, then open **http://localhost:8765**.

Add `-AllowLan` to setup to open the app from your phone on the same Wi-Fi.

## Auto-clipping League
The `clipper/` folder (coming soon) watches the game through Riot's official local [Live Client Data API](https://developer.riotgames.com/docs/lol#game-client-api), saves a replay through OBS on multikills, aces and objective steals, and writes a [tag file](docs/tag-format.md) so ClipLab can trim and label the short. Any other tool can write tag files too; the format is documented.

## Two PCs
Got a spare laptop? Let it do the rendering so your gaming PC doesn't. See [docs/two-machines.md](docs/two-machines.md).

## Using the app
| | |
|---|---|
| **Browse** | Shorts are grouped by game. Filter by play (DOUBLE KILL, ACE…) or by New / Kept. Hover to preview. |
| **Player** | Switch Blur fill / Center crop, Download, Copy caption, Keep, Toss. Keys: ← → browse · K keep · T toss · D download · C copy · 1 / 2 version. |
| **Re-cut** | Nudge start/end, preview the cut on the raw clip, then re-render. |
| **Reels** | "Make a reel of this game" on any game with 2+ shorts, or from everything kept. |
| **Settings (⚙)** | Versions, watermark, gameplay size, seconds before/after the play, min/max length, quality. |

Tossed shorts move to `Tossed\` and go to the Recycle Bin after 14 days (adjustable).

## Configuration
Settings live in `%USERPROFILE%\ClipLab\config.json` (or `%CLIPLAB_HOME%`). Render settings are editable in the app; the rest you edit by hand, then restart with `python -m cliplab`.

| Key | Default | |
|---|---|---|
| `watch_dir` | `ClipLab\Inbox` | Where raw clips arrive |
| `output_dir` | `ClipLab\Ready` | Where shorts go (must not be inside `watch_dir`) |
| `ffmpeg` | *(auto)* | Folder with ffmpeg/ffprobe; otherwise `ClipLab\bin`, then PATH |
| `port` | `8765` | App port |
| `allow_lan` | `false` | Let other devices on your home network open the app |
| `peer` | *(empty)* | Two-PC mode; see [docs/two-machines.md](docs/two-machines.md) |
| `styles`, `lead_in`, `tail`, `min_seconds`, `max_seconds`, `blur_zoom`, `watermark`, `crf`, `maxrate_mbps`, `tag_wait`, `recycle_tossed_after_days` | | Render settings (also in the app) |

`python -m cliplab config` prints where everything is and the settings in effect.

## Commands
```
python -m cliplab          engine + app, restarted if either crashes (what setup starts)
python -m cliplab stop     stop it
python -m cliplab engine   just the engine, in the foreground (handy while developing)
python -m cliplab app      just the app, in the foreground
python -m cliplab config   show paths and settings
```

## Developing
- Code: `cliplab/settings.py` (paths, defaults), `engine.py` (rendering, jobs), `server.py` (API), `web/index.html` (the whole UI), `launcher.py`.
- Tests: `python -m unittest -v` from the repo root (uses a throwaway home folder; the render test needs ffmpeg).
- The app hands work to the engine by dropping JSON jobs in `requests\`; results land in `requests\done\`.
- Logs: `logs\engine.log`, `logs\launcher.log`.

## Disclaimer
ClipLab isn't endorsed by Riot Games and doesn't reflect the views or opinions of Riot Games or anyone officially involved in producing or managing Riot Games properties. Riot Games and all associated properties are trademarks or registered trademarks of Riot Games, Inc.

## License
[MIT](LICENSE)
