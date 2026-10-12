# ClipLab: sets up OBS Studio for the League auto-clipper. Optional: you can do the same by hand (README.md).
#   Profile "ClipLab":          Advanced output, H.264 CBR (NVENC by default, x264 with -Encoder x264), mp4,
#                               replay buffer saving to -ClipsDir as "Replay <date time>.mp4",
#                               -SaveHotkey saves a replay by hand
#   Scene collection "ClipLab": League game capture + League / Discord / Mic audio as separate sources
#                               (their names match "source" in config.example.json)
#   obs-websocket:              enabled on port 4455; OBS generates the password on its next start
# Run with OBS closed. Re-running rewrites the ClipLab profile and scene collection (your other
# profiles and scene collections are left alone; OBS's own user.ini is backed up to user.ini.bak).
param(
  [string]$ClipsDir = 'C:\Clips',
  [ValidateSet('nvenc', 'x264')][string]$Encoder = 'nvenc',
  [int]$BitrateKbps = 15000,
  [int]$ReplaySeconds = 60,
  [int]$Width = 1920, [int]$Height = 1080, [int]$Fps = 60,
  [string]$SaveHotkey = 'F12'
)

$ErrorActionPreference = 'Stop'
if (Get-Process obs64 -ErrorAction SilentlyContinue) { throw 'Close OBS first, then run this again.' }

$Obs     = Join-Path $env:APPDATA 'obs-studio'
$ProfDir = Join-Path $Obs 'basic\profiles\ClipLab'
$Scenes  = Join-Path $Obs 'basic\scenes'
$WsDir   = Join-Path $Obs 'plugin_config\obs-websocket'
New-Item -ItemType Directory -Force $ProfDir, $Scenes, $WsDir, $ClipsDir | Out-Null
$recEncoder = @{ nvenc = 'obs_nvenc_h264_tex'; x264 = 'obs_x264' }[$Encoder]
$hotkey = ''
if ($SaveHotkey) { $hotkey = "ReplayBuffer={`"ReplayBuffer.Save`":[{`"key`":`"OBS_KEY_$($SaveHotkey.ToUpper())`"}]}" }
$utf8 = New-Object System.Text.UTF8Encoding($false)
function Save($path, $text) { [IO.File]::WriteAllText($path, $text, $utf8) }

# Pick the ClipLab profile + scene collection, skip the first-run wizard, keep OBS in the tray
# (backs up whatever OBS wrote on its own first)
$userIni = Join-Path $Obs 'user.ini'
if (Test-Path $userIni) { Copy-Item $userIni "$userIni.bak" -Force }
Save $userIni @"
[General]
FirstRun=true

[Basic]
Profile=ClipLab
ProfileDir=ClipLab
SceneCollection=ClipLab
SceneCollectionFile=ClipLab

[BasicWindow]
SysTrayEnabled=true
SysTrayWhenStarted=true
SysTrayMinimizeToTray=true
"@

Save (Join-Path $ProfDir 'basic.ini') @"
[General]
Name=ClipLab

[Output]
Mode=Advanced
FilenameFormatting=%CCYY-%MM-%DD %hh-%mm-%ss

[AdvOut]
RecType=Standard
RecFilePath=$($ClipsDir.Replace('\', '/'))
RecFormat2=mp4
RecEncoder=$recEncoder
RecTracks=1
RecAudioEncoder=ffmpeg_aac
Track1Bitrate=192
RecRB=true
RecRBTime=$ReplaySeconds
RecRBSize=1024
RecRBPrefix=Replay

[Video]
BaseCX=$Width
BaseCY=$Height
OutputCX=$Width
OutputCY=$Height
FPSType=0
FPSCommon=$Fps

[Audio]
SampleRate=48000
ChannelSetup=Stereo

[Hotkeys]
$hotkey
"@

$encSettings = @{ rate_control = 'CBR'; bitrate = $BitrateKbps; keyint_sec = 2; profile = 'high' }
if ($Encoder -eq 'nvenc') { $encSettings += @{ preset = 'p5'; preset2 = 'p5'; tune = 'hq'; multipass = 'qres' } }
else { $encSettings += @{ preset = 'veryfast' } }
Save (Join-Path $ProfDir 'recordEncoder.json') ($encSettings | ConvertTo-Json)

# Scene collection: one scene holding the League capture and the three audio sources
$league  = 'League of Legends (TM) Client:RiotWindowClass:League of Legends.exe'
$discord = 'Discord:Chrome_WidgetWin_1:Discord.exe'
$srcDefs = @(
  @{ name = 'League';        id = 'game_capture';                  settings = @{ capture_mode = 'window'; window = $league; priority = 2; capture_cursor = $true; anti_cheat_hook = $true }; mixers = 0;   muted = $false },
  @{ name = 'Game Audio';    id = 'wasapi_process_output_capture'; settings = @{ window = $league;  priority = 2 }; mixers = 255; muted = $false },
  @{ name = 'Discord Audio'; id = 'wasapi_process_output_capture'; settings = @{ window = $discord; priority = 2 }; mixers = 255; muted = $false },
  @{ name = 'Mic';           id = 'wasapi_input_capture';          settings = @{ device_id = 'default' };            mixers = 255; muted = $true }
)
$sources = @(); $items = @(); $n = 0
foreach ($d in $srcDefs) {
  $uuid = [guid]::NewGuid().ToString(); $n++
  $sources += [ordered]@{ name = $d.name; uuid = $uuid; id = $d.id; versioned_id = $d.id; settings = $d.settings; mixers = $d.mixers
                          muted = $d.muted; volume = 1.0; enabled = $true; flags = 0; sync = 0; balance = 0.5; monitoring_type = 0; private_settings = @{} }
  $item = [ordered]@{ name = $d.name; source_uuid = $uuid; visible = $true; locked = $false; rot = 0.0; align = 5; id = $n
                      pos = @{ x = 0.0; y = 0.0 }; scale = @{ x = 1.0; y = 1.0 }; crop_left = 0; crop_top = 0; crop_right = 0; crop_bottom = 0
                      bounds_type = 0; bounds_align = 0; bounds = @{ x = 0.0; y = 0.0 }; private_settings = @{} }
  if ($d.id -eq 'game_capture') { $item.bounds_type = 2; $item.bounds = @{ x = [double]$Width; y = [double]$Height } }   # scale to fit the canvas
  $items += $item
}
$scene = [ordered]@{ name = 'ClipLab'; uuid = [guid]::NewGuid().ToString(); id = 'scene'; versioned_id = 'scene'
                     settings = [ordered]@{ id_counter = $n; custom_size = $false; items = $items }; mixers = 0; enabled = $true; private_settings = @{} }
$collection = [ordered]@{
  name = 'ClipLab'; current_scene = 'ClipLab'; current_program_scene = 'ClipLab'; scene_order = @(@{ name = 'ClipLab' })
  sources = @($scene) + $sources; groups = @(); transitions = @(); current_transition = 'Fade'; transition_duration = 300
  quick_transitions = @(); saved_projectors = @(); preview_locked = $false; scaling_enabled = $false; modules = @{}
}
Save (Join-Path $Scenes 'ClipLab.json') ($collection | ConvertTo-Json -Depth 10)

# obs-websocket: on, with auth. first_load makes OBS generate a random password you can change in
# Tools > WebSocket Server Settings. The clipper reads it from this file, so it never needs typing anywhere.
$wsFile = Join-Path $WsDir 'config.json'
if (Test-Path $wsFile) {
  $ws = Get-Content $wsFile -Raw | ConvertFrom-Json
  $ws.server_enabled = $true; $ws.auth_required = $true; $ws.alerts_enabled = $false
  Save $wsFile ($ws | ConvertTo-Json)
} else {
  Save $wsFile (@{ first_load = $true; server_enabled = $true; auth_required = $true; alerts_enabled = $false; server_port = 4455 } | ConvertTo-Json)
}

Write-Host "OBS configured for ClipLab: $Encoder $BitrateKbps kbps, ${Width}x$Height@$Fps, $ReplaySeconds s replay buffer into $ClipsDir."
Write-Host 'Run install.ps1 next to start OBS and the clipper at sign-in.'
