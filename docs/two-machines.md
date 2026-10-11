# Two-PC setup

Your **gaming PC** records; a **second PC** (an old laptop works fine) renders the shorts and hosts the app. Clips move between them with [Syncthing](https://syncthing.net/), directly over your home network.

```
Gaming PC                                   Second PC
OBS replay buffer → C:\Clips  ──Syncthing──▶ ClipLab\Inbox → engine → ClipLab\Ready ──Syncthing──▶ Videos\Shorts Ready
```

## 1. Second PC
1. Clone the repo and run setup with the default folders:
   ```
   git clone https://github.com/ccarlson41/cliplab.git %USERPROFILE%\ClipLab
   powershell -ExecutionPolicy Bypass -File %USERPROFILE%\ClipLab\scripts\setup.ps1 -WatchDir %USERPROFILE%\ClipLab\Inbox -OutputDir %USERPROFILE%\ClipLab\Ready -AllowLan
   ```
2. Install Syncthing (`winget install Syncthing.Syncthing`) and start it.
3. In Syncthing, add two folders:
   - **ClipLab Inbox**: path `%USERPROFILE%\ClipLab\Inbox`, folder type **Receive Only**.
   - **ClipLab Ready**: path `%USERPROFILE%\ClipLab\Ready`, folder type **Send Only**.

## 2. Gaming PC
1. Install Syncthing and add the second PC as a device (use its device ID).
2. Share folders with it:
   - `C:\Clips` (where OBS saves replays) as **Send Only**, using the same folder ID as *ClipLab Inbox*.
   - `Videos\Shorts Ready` as **Receive Only**, using the same folder ID as *ClipLab Ready*.
3. Accept the device and folders on the second PC.

## 3. Show the gaming PC in the app (optional)
Add a `peer` section to the second PC's `config.json` so the app shows the connection, copy progress and paths on the gaming PC:

```json
"peer": {
  "name": "Gaming PC",
  "syncthing_home": "C:\\Users\\you\\AppData\\Local\\Syncthing",
  "syncthing_device": "<gaming PC's Syncthing device ID>",
  "syncthing_folder": "<folder ID of ClipLab Inbox>",
  "clips_path": "C:\\Clips",
  "shorts_path": "C:\\Users\\you\\Videos\\Shorts Ready"
}
```

`syncthing_home` is the folder holding the second PC's Syncthing `config.xml` (ClipLab reads its API key from there to check the connection). Restart with `python -m cliplab`.

## 4. Open the app from the gaming PC
1. On the second PC: set your Wi-Fi to **Private**, and allow Python through Windows Defender Firewall on **Private** networks.
2. On the gaming PC: open `http://<second PC's IP>:8765`, then install it as an app from your browser's menu.

## Tips
- Keep the replay bitrate around 15 Mbps; bigger files just take longer to copy and get re-encoded anyway.
- To avoid any in-game hitching, pause Syncthing during games (the auto-clipper can do this for you).
