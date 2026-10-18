# ClipLab

**Turns your gaming replays (League of Legends, Rainbow Six Siege, VALORANT or anything else) into ready-to-post vertical shorts, automatically.**

Save a replay in OBS (or let the auto-clipper catch your multikills), and about a minute later you have 1080×1920 shorts trimmed to the action, with **TRIPLE KILL**-style text on screen and a caption ready to paste. A small web app lets you watch, keep, toss, re-cut and download them, and stitch a game's best plays into a highlight reel.

- **Two looks per clip:** *Blur fill* (the whole fight, over a blurred backdrop) and *Center crop* (fills the phone screen). Place a webcam from the app and there's a third: *Webcam*, your reaction with the play, in the layout you pick.
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

It asks which game you play, where OBS should save replays, and whether your phone should be able to open the app. Then it downloads the latest ClipLab release (no git needed; the download is checked against its SHA-256), installs Python and ffmpeg, starts ClipLab, sets it to start when you sign in, and opens the app. A **Getting started** guide there walks you through OBS's settings and checks each one, then has you save a test replay. Reopen the guide any time from Settings (⚙).

ClipLab then keeps itself up to date (see [Updates](#updates)). Running the same line again also updates it, along with Python and ffmpeg; your settings and shorts are kept. To see what it runs first, read [install.ps1](install.ps1).

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

## Subtitles and the intro hook
Settings (⚙) > **Shorts**:
- **Subtitles** write what's said in the clip (your voice, your comms) on screen a few words at a time, the way shorts do it. Pick a look (**Bold**, **Pop**, **Boxed** or **Clean**) and where it goes (lower, middle or upper); the preview shows it. Speech-to-text runs on your PC with ffmpeg's built-in whisper filter, so nothing is uploaded. The first subtitled short downloads the speech model once: 150 MB, or 490 MB for **Accurate**. It needs an ffmpeg with speech-to-text (gyan.dev's full build, ffmpeg 8 or newer). If yours is older, the app says so under **Subtitles**: run ClipLab's setup again (or the install line) and it updates ClipLab's copy of ffmpeg. Each short also says why if it was made without subtitles.
- **Intro hook** adds a quick whoosh and zoom punch in the first moment of each short, so people scrolling past stop on it (or just the zoom).

In the player, **Ready to post** (P) downloads the short and copies its caption, ready to paste into TikTok, YouTube Shorts or Reels.

## Other games
Rendering works the same for any game; the **Game** setting only picks the caption hashtags. League of Legends, Rainbow Six Siege and VALORANT are built in. Only League has an auto-clipper (see Auto-clipping League above); for other games, press your OBS *Save Replay* hotkey after a good play.

## Rainbow Six Siege
Pick **Rainbow Six Siege** under **Game** in the app's settings (⚙), or run setup with `-Game r6`. Captions then get Siege hashtags instead of League ones, and ClipLab stops waiting for tag files (*Wait for tag file* is set to 0).

There's no auto-clipper for Siege: Ubisoft doesn't offer a local game-data API like Riot's, and reading the game's memory would risk a BattlEye ban. Press your OBS *Save Replay* hotkey right after a good round. Each short is the last `max_seconds` of the replay, and you can re-cut it in the app.

## OBS from the app
Settings (⚙) > **OBS** changes OBS for you through its built-in WebSocket server (turn it on once in OBS: Tools > WebSocket Server Settings > Enable WebSocket server). ClipLab reads the password from OBS's own settings, so there's nothing to type.
- **Microphone** and **Screen**: pick from the devices OBS sees. Picking a screen when your scene only has a game capture adds a screen capture behind it.
- **Webcam**: pick a camera, a corner and a size. ClipLab puts it in that corner of OBS's picture and makes a **Webcam** version of each short: your camera with the play, laid out the way you pick (see Webcam layouts below). Move it in OBS later and ClipLab follows.
- **Sound when a replay is saved**: a chime, blip or pop when OBS saves a replay, so you know your hotkey worked.

These work with the OBS on the same PC as ClipLab. In two-PC mode they need ClipLab running on the gaming PC too.

## Webcam layouts
Where your webcam goes in the **Webcam** version. Pick the layout for new shorts in Settings (⚙) > **Shorts** > **Webcam layout**; the little previews show where your webcam, the play label and the subtitles land.
- **Top**: your webcam as a strip across the top, the play below (the default, and how earlier shorts were made).
- **Bottom**: the play on top, your webcam as a strip under it.
- **Split**: half and half, your webcam on the top half.
- **Corner**: the play fills the screen and your webcam sits in a rounded box with a white edge, in the corner you pick (Small, Medium or Large). Boxes stay clear of the bar TikTok, Shorts and Reels draw across the top and of the caption at the bottom.

Play labels, subtitles and your watermark always move out of the webcam's way. Each short keeps its own layout through re-cuts and re-renders. To change one short, open it and pick a new **Webcam layout**: only its Webcam version is made again, with the same cut and your current settings.

## What's new, ideas and the app icon
- **What's new:** after each update, ClipLab greets you with a pop-up of what changed (once per device). Tap **✨** at the top of the app any time to see it again, or browse the **Patch history** of every version.
- **Request a feature:** the ✨ pop-up (or Settings > More) has a short form. It opens a GitHub issue with your idea filled in; press the green button there to send it (needs a free GitHub account).
- **Install app:** Settings > More > **Install app** (or the button at the top on phones) gives ClipLab its own icon. On this PC, Edge and Chrome install it as an app. On an iPhone, use Safari's Share > Add to Home Screen. Phones reach ClipLab over home Wi-Fi without https, so Android adds a home-screen shortcut that opens in Chrome rather than a full app.

## Updates
ClipLab checks GitHub for a new release when it starts and every 6 hours. Settings (⚙) > **More** > **Updates** picks what happens:
- **Install them by themselves** (the default): ClipLab downloads the new version, checks it against its SHA-256, waits until nothing is rendering, then swaps in the new code and restarts. The app reloads and shows what's new.
- **Tell me first:** a banner says a new version is out; **Update now** installs it.
- **Don't check:** nothing happens unless you press **Check now**.

Only ClipLab's code is replaced. Your settings, shorts, clips, logs and downloaded models stay as they are, and the old code is kept in `backups\<version>`. If the new version doesn't start within a minute, ClipLab puts the old one back, keeps running it, and doesn't try that version again by itself (details in `logs\update.log`). Python and ffmpeg aren't updated this way; run the install line for those.

A copy that came from `git clone` is only updated with `git pull --ff-only`, and only when it's on `main` with no changes or commits of its own. Otherwise the app just says an update is out.

## Two PCs
Got a spare laptop? Let it do the rendering so your gaming PC doesn't. See [docs/two-machines.md](docs/two-machines.md).

## Using the app
| | |
|---|---|
| **Browse** | Shorts are grouped by game. Filter by play (DOUBLE KILL, ACE…) or by New / Kept. Hover to preview. |
| **Player** | Switch versions, Ready to post, Download, Copy caption, Keep, Toss, change the Webcam layout. Keys: ← → browse · P ready to post · K keep · T toss · D download · C copy · 1 / 2 / 3 version. |
| **Re-cut** | Nudge start/end, preview the cut on the raw clip, then re-render. |
| **Reels** | "Make a reel of this game" on any game with 2+ shorts, or from everything kept. |
| **What's new (✨)** | What changed in this version, the patch history of every version, and Request a feature. |
| **Settings (⚙)** | **Shorts:** game, versions, webcam layout, watermark, gameplay size, seconds before/after the play, shortest/longest short (up to 3 minutes). **Look:** theme and accent color. **OBS:** mic, screen, webcam, save sound. **More:** quality and housekeeping, the Getting started guide, What's new, Request a feature, Install app, Updates. |

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
| `game` | `league` | `league`, `r6` or `valorant`; sets caption hashtags (a tag file naming its game wins) |
| `theme`, `accent` | `system`, `copper` | The app's look: `system`/`dark`/`light`; `copper`, `blue`, `green`, `purple` or `pink` |
| `subtitles` | `off` | `off`, `on` or `accurate` (bigger speech model) |
| `subtitle_style`, `subtitle_position` | `bold`, `lower` | `bold`, `pop`, `boxed` or `clean`; `lower`, `center` or `upper` |
| `intro` | `off` | `off`, `whoosh` (sound + zoom punch) or `punch` (zoom only) |
| `cam_layout` | `{"layout": "top", "corner": "top-left", "size": "medium"}` | The Webcam version's layout for new shorts: `layout` is `top`, `bottom`, `split` or `corner`; `corner` (`top-left`, `top-right`, `bottom-left`, `bottom-right`) and `size` (`small`, `medium`, `large`) place the corner box |
| `auto_update` | `auto` | `auto` installs new releases by itself, `notify` only says one is out, `off` doesn't check (see [Updates](#updates)) |
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
- **Branches:** `dev` is where reviewed work lands; `main` is what people get, and only moves when Cam releases.
- **Every change people can see gets a What's new entry** in `cliplab/changelog.json` (each highlight is an emoji, a short headline and one plain sentence). Which entry:
  - If dev's `__version__` (in `cliplab/__init__.py`) **hasn't been released yet** (`git fetch --tags`, then `git tag -l v<version>` prints nothing), add your highlights to the newest entry.
  - If it **has** (that tag exists), bump the minor version (`0.10.0` → `0.11.0`), and add a new entry at the top with a fun title, today's date and your highlights.

  The app shows the newest entry once on each device after an update. The tests fail if the newest entry doesn't match `__version__`, or has more than 8 highlights.
- **Releasing (Cam):** on GitHub, **Actions > Release > Run workflow** (or ask Claude to run `gh workflow run release.yml`). It releases what's on dev:
  1. It checks that the tests passed on dev's newest commit, and that its version isn't released yet.
  2. It moves `main` up to dev.
  3. It tags `v<version>` and publishes a GitHub Release with the What's new notes and `cliplab-<version>.zip` (plus its `.sha256`). Installs and updates download that zip.

  If something's off, it stops and says why on the run's page. `python scripts/release.py check` shows the same checks without changing anything. Nothing is ever released automatically.

  GitHub doesn't let a workflow push changes to `.github/workflows`. A release that includes such a change (the first one does, since it adds `release.yml`) stops and asks you to move main yourself once: `git fetch origin`, then `git push origin origin/dev:main`. Then run Release again. GitHub also only shows the Run workflow button once `release.yml` is on `main`, so that first push comes before the first run.
- App icons: after changing `cliplab/web/icon.svg`, run `python scripts/make_icons.py` to redraw the PNGs (needs Edge or Chrome).
- Logs: `logs\engine.log`, `logs\launcher.log`, `logs\update.log`.
- Testing updates: `CLIPLAB_UPDATE_URL` points ClipLab at another "latest release" address (a local copy of GitHub's JSON) instead of GitHub's.

## Disclaimer
ClipLab isn't endorsed by Riot Games and doesn't reflect the views or opinions of Riot Games or anyone officially involved in producing or managing Riot Games properties. Riot Games and all associated properties are trademarks or registered trademarks of Riot Games, Inc. ClipLab isn't affiliated with or endorsed by Ubisoft; Rainbow Six Siege is a trademark of Ubisoft Entertainment.

## License
[MIT](LICENSE)
