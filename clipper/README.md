# League auto-clipper

Saves an OBS replay by itself when you make a play in League of Legends, and writes a [tag file](../docs/tag-format.md) next to it so ClipLab can trim the short to the action and put **TRIPLE KILL** on screen.

- **What counts:** your multikills (double kill and up, configurable), aces, first blood, and Baron / dragon / Herald steals. Only your own plays.
- **How it knows:** Riot's official [Live Client Data API](https://developer.riotgames.com/docs/lol#game-client-api), a read-only local API that League itself serves at `https://127.0.0.1:2999` during a game. It's checked once a second.
- **How it saves:** through OBS's built-in **obs-websocket**. It never fakes keypresses, which matters with Vanguard running.
- **Plays close together become one clip:** a double kill that turns into a triple, then an ace, is one save. After a multikill below a penta it waits up to 10 s in case it grows.
- **Survives restarts:** League closing, OBS closing (it reopens OBS if you want), and reboots.

Windows 10/11, PowerShell 5.1 (built in). No Python needed on this side.

## 1. Set up OBS
You need OBS Studio 28 or newer (obs-websocket is built in): `winget install OBSProject.OBSStudio`.

**Quick way:** close OBS, then run

```
powershell -ExecutionPolicy Bypass -File setup-obs.ps1 -ClipsDir C:\Clips
```

It adds a **ClipLab** profile and scene collection and switches OBS to them; your other profiles are left alone. Options: `-Encoder x264` (no NVIDIA card), `-BitrateKbps 15000`, `-ReplaySeconds 60`, `-Width 1920 -Height 1080 -Fps 60`, `-SaveHotkey F12`.

**By hand**, if you'd rather:
1. **Settings → Output**, Output Mode **Advanced**, **Recording** tab: Type *Standard*, Recording Path `C:\Clips`, Format *MPEG-4 (.mp4)*, Encoder *NVIDIA NVENC H.264* (or *x264*), Rate Control *CBR*, Bitrate *15000 Kbps*.
2. **Settings → Output → Replay Buffer:** enable it, 60 seconds.
3. **Settings → Video:** 1920×1080, 60 fps.
4. **Settings → Hotkeys:** give *Save Replay* a key for manual saves.
5. **Sources:** add a **Game Capture** for *League of Legends (TM) Client*. For audio, add **Application Audio Capture** sources named `Game Audio` (League) and `Discord Audio` (Discord), and an **Audio Input Capture** named `Mic`. Turn off the global Desktop Audio / Mic devices if you only want these.
6. **Tools → WebSocket Server Settings:** enable the server, keep authentication on, port 4455.

Keep the bitrate around 15 Mbps. ClipLab re-encodes everything to 1080×1920, so more doesn't show.

## 2. Install the clipper
```
cd clipper
powershell -ExecutionPolicy Bypass -File install.ps1
```

This creates `config.json` from `config.example.json` if needed, adds two hidden sign-in shortcuts (OBS through `start-obs.ps1`, and the clipper), and starts both. `install.ps1 -Uninstall` removes the shortcuts; `-NoObs` leaves starting OBS to you.

`start-obs.ps1` starts OBS in the tray with the replay buffer running. It also clears OBS's leftover crash marker first, so a power cut or force-close can't leave OBS waiting on its "Crash Detected" prompt at sign-in.

## 3. Check it
```
powershell -ExecutionPolicy Bypass -File check.ps1
powershell -ExecutionPolicy Bypass -File autoclip.ps1 -TestSave
```

`check.ps1` confirms the clipper is running and OBS's replay buffer is on. `-TestSave` saves one replay with a `TEST CLIP` tag so you can follow it through ClipLab. `-SelfTest` runs sample League events through the rules and prints the tag it would write, without OBS.

What it saw and saved goes to `autoclip.log` next to the script.

## Settings (`config.json`)
Saved changes apply within a few seconds.

| Key | Default | |
|---|---|---|
| `clipsDir` | `C:\Clips` | Where OBS saves replays. Must match OBS's recording path. |
| `clipOn.multikillMinimum` | `2` | 2 = double kill and up; 0 = never clip multikills |
| `clipOn.ace`, `firstBlood`, `baronSteal`, `dragonSteal`, `heraldSteal` | `true` | |
| `audio.<name>` | game, discord, mic | `source` (OBS input name), `on`, `volume` 0–1. Pushed to OBS when the clipper connects and whenever you save. |
| `timing.secondsAfterEvent` | `4` | Wait after a play before saving, to catch the aftermath |
| `timing.secondsToWaitForBiggerMultikill` | `10` | Wait after a double/triple/quadra in case it grows |
| `timing.longestChainSeconds` | `40` | Longest a chain of plays can keep postponing the save |
| `obs.port` | `4455` | obs-websocket port |
| `obs.launchIfClosed` | `true` | Start OBS if it's closed when a game starts |
| `obs.exe` | default install path | Path to `obs64.exe` |
| `obs.passwordFile` | *(empty)* | Text file with the websocket password. Empty = read it from OBS's own settings (`%APPDATA%\obs-studio\plugin_config\obs-websocket\config.json`). Never put the password in `config.json`. |
| `sync.pauseDuringGames` | `false` | Two-PC setups: pause Syncthing during games, resume after |
| `sync.syncthingConfig` | *(empty)* | Syncthing's `config.xml`, for its API key. Empty = `%LOCALAPPDATA%\Syncthing\config.xml` |
| `cleanup.rawClipDays` | `0` | Send raw clips older than this to the Recycle Bin (0 = never). Move a clip out of `clipsDir` to keep it. |
| `cleanup.shortsDir` | *(empty)* | If set, clips with no short there yet get twice as long before cleanup |

## Notes
- The tag's `secondsBeforeEnd` is measured from when the clipper first saw the event to when it sent `SaveReplayBuffer`, which is the moment OBS cuts the buffer.
- Tag files are written to a temporary name and renamed into place, so ClipLab never reads half a file.
- Only one copy runs at a time; starting a second one exits quietly.
