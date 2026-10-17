# ClipLab

**Turns your League of Legends and Rainbow Six Siege replays into ready-to-post vertical shorts, automatically.**

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
- Python 3.12+ and ffmpeg (the installer gets both with winget)

## Install
Open **PowerShell** (Start menu, type *PowerShell*, press Enter), paste this line and press Enter:

```powershell
irm https://raw.githubusercontent.com/ccarlson41/cliplab/main/install.ps1 | iex
```

It asks which game you play, where OBS should save replays, and whether your phone should be able to open the app. Then it downloads ClipLab (no git needed), installs Python and ffmpeg, starts ClipLab, sets it to start when you sign in, and opens the app. A **Getting started** guide there walks you through OBS's settings and checks each one, then has you save a test replay. Reopen the guide any time from Settings (⚙).

Run the same line again later to update ClipLab; your settings and shorts are kept. To see what it runs first, read [install.ps1](install.ps1).

## Manual install (one PC)
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
4. Play, press your replay hotkey after a big play, then open **http://localhost:8765**. (Or follow the Getting started guide there.)

Add `-AllowLan` to setup to open the app from your phone on the same Wi-Fi.

## Auto-clipping League
The [`clipper/`](clipper/README.md) folder watches the game through Riot's official local [Live Client Data API](https://developer.riotgames.com/docs/lol#game-client-api), saves a replay through OBS on multikills, aces and objective steals, and writes a [tag file](docs/tag-format.md) so ClipLab can trim and label the short. Any other tool can write tag files too; the format is documented.

## Rainbow Six Siege
Pick **Rainbow Six Siege** under **Game** in the app's settings (⚙), or run setup with `-Game r6`. Captions then get Siege hashtags instead of League ones, and ClipLab stops waiting for tag files (*Wait for tag file* is set to 0).

There's no auto-clipper for Siege: Ubisoft doesn't offer a local game-data API like Riot's, and reading the game's memory would risk a BattlEye ban. Press your OBS *Save Replay* hotkey right after a good round. Each short is the last `max_seconds` of the replay, and you can re-cut it in the app.

## OBS from the app
Settings (⚙) > **OBS** changes OBS for you through its built-in WebSocket server (turn it on once in OBS: Tools > WebSocket Server Settings > Enable WebSocket server). ClipLab reads the password from OBS's own settings, so there's nothing to type.
- **Microphone** and **Screen**: pick from the devices OBS sees. Picking a screen when your scene only has a game capture adds a screen capture behind it.
- **Webcam**: pick a camera, a corner and a size. ClipLab puts it in that corner of OBS's picture and makes a **Webcam on top** version of each short: your camera across the top, the play below. Move it in OBS later and ClipLab follows.
- **Sound when a replay is saved**: a chime, blip or pop when OBS saves a replay, so you know your hotkey worked.

These work with the OBS on the same PC as ClipLab. In two-PC mode they need ClipLab running on the gaming PC too.

## What's new, ideas and the app icon
- **What's new:** after each update, ClipLab greets you with a pop-up of what changed (once per device). Tap **✨** at the top of the app any time to see it again, or browse the **Patch history** of every version.
- **Request a feature:** the ✨ pop-up (or Settings > More) has a short form. It opens a GitHub issue with your idea filled in; press the green button there to send it (needs a free GitHub account).
- **Install app:** Settings > More > **Install app** (or the button at the top on phones) gives ClipLab its own icon. On this PC, Edge and Chrome install it as an app. On an iPhone, use Safari's Share > Add to Home Screen. Phones reach ClipLab over home Wi-Fi without https, so Android adds a home-screen shortcut that opens in Chrome rather than a full app.

## Two PCs
Got a spare laptop? Let it do the rendering so your gaming PC doesn't. See [docs/two-machines.md](docs/two-machines.md).

## Using the app
| | |
|---|---|
| **Browse** | Shorts are grouped by game. Filter by play (DOUBLE KILL, ACE…) or by New / Kept. Hover to preview. |
| **Player** | Switch Blur fill / Center crop, Download, Copy caption, Keep, Toss. Keys: ← → browse · K keep · T toss · D download · C copy · 1 / 2 version. |
| **Re-cut** | Nudge start/end, preview the cut on the raw clip, then re-render. |
| **Reels** | "Make a reel of this game" on any game with 2+ shorts, or from everything kept. |
| **What's new (✨)** | What changed in this version, the patch history of every version, and Request a feature. |
| **Settings (⚙)** | **Shorts:** game, versions, watermark, gameplay size, seconds before/after the play, shortest/longest short (up to 3 minutes). **Look:** theme and accent color. **OBS:** mic, screen, webcam, save sound. **More:** quality and housekeeping, the Getting started guide, What's new, Request a feature, Install app. |

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
| `setup_done` | `false` | Set when you finish the app's Getting started guide; set it back to `false` to see the guide on its own again |
| `feedback_url` | *(GitHub)* | Where Request a feature sends people. Empty opens a new GitHub issue on this repo. Set an `http(s)` address (say, a Google Form) to use that instead; `{what}`, `{why}`, `{name}`, `{title}` and `{version}` in it are filled in from the app's form, and without them the app copies the idea for pasting |
| `game` | `league` | `league` or `r6`; sets caption hashtags (a tag file naming its game wins) |
| `theme`, `accent` | `system`, `copper` | The app's look: `system`/`dark`/`light`; `copper`, `blue`, `green`, `purple` or `pink` |
| `save_sound` | `off` | `off`, `chime`, `blip` or `pop`, played when OBS on this PC saves a replay |
| `webcam` | *(none)* | Where the app placed your webcam in OBS (`rect` as fractions of the canvas, `corner`, `size`); used by the `cam` version |
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
- **Every change people can see gets a What's new entry:** bump `__version__` in `cliplab/__init__.py` and add the release to the top of `cliplab/changelog.json` (a fun title and 2–5 highlights, each an emoji, a short headline and one plain sentence). The app shows it once on each device after the update; the tests fail if the newest entry doesn't match `__version__`.
- App icons: after changing `cliplab/web/icon.svg`, run `python scripts/make_icons.py` to redraw the PNGs (needs Edge or Chrome).
- Logs: `logs\engine.log`, `logs\launcher.log`.

## Disclaimer
ClipLab isn't endorsed by Riot Games and doesn't reflect the views or opinions of Riot Games or anyone officially involved in producing or managing Riot Games properties. Riot Games and all associated properties are trademarks or registered trademarks of Riot Games, Inc. ClipLab isn't affiliated with or endorsed by Ubisoft; Rainbow Six Siege is a trademark of Ubisoft Entertainment.

## License
[MIT](LICENSE)
