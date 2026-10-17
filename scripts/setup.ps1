<#
.SYNOPSIS
  Sets up ClipLab on this PC: Python, ffmpeg, a config file, and start-at-sign-in.

.EXAMPLE
  # One machine: OBS saves replays to C:\Clips, shorts go to Videos\ClipLab Shorts
  powershell -ExecutionPolicy Bypass -File scripts\setup.ps1 -WatchDir C:\Clips

.EXAMPLE
  # Also let your phone / other PCs on your home network open the app
  powershell -ExecutionPolicy Bypass -File scripts\setup.ps1 -WatchDir C:\Clips -AllowLan

.EXAMPLE
  # Rainbow Six Siege player: Siege captions, and no waiting for League auto-clipper tag files
  powershell -ExecutionPolicy Bypass -File scripts\setup.ps1 -WatchDir C:\Clips -Game r6
#>
param(
  [string]$WatchDir = (Join-Path ([Environment]::GetFolderPath('MyVideos')) 'Replays'),
  [string]$OutputDir = (Join-Path ([Environment]::GetFolderPath('MyVideos')) 'ClipLab Shorts'),
  [ValidateSet('league', 'r6', 'valorant')][string]$Game = 'league',
  [switch]$AllowLan,
  [switch]$InstallObs,
  [switch]$NoStartup,
  [switch]$Force
)
$ErrorActionPreference = 'Stop'
$Game = $Game.ToLower()   # ValidateSet lets "R6" through; ClipLab's keys are lowercase
$Repo = Split-Path $PSScriptRoot -Parent
$Home2 = if ($env:CLIPLAB_HOME) { $env:CLIPLAB_HOME } else { Join-Path $env:USERPROFILE 'ClipLab' }
$Utf8 = New-Object Text.UTF8Encoding $false
function Step($n, $text) { Write-Host "[$n/5] $text" -ForegroundColor Cyan }
function Have($cmd) { [bool](Get-Command $cmd -ErrorAction SilentlyContinue) }

Step 1 'Python 3.12+'
$py = Join-Path $env:LOCALAPPDATA 'Programs\Python\Launcher\py.exe'
if (-not (Test-Path $py)) { $py = (Get-Command py -ErrorAction SilentlyContinue).Source }
$ok = $false
if ($py) { & $py -3 -c "import sys; sys.exit(0 if sys.version_info >= (3, 12) else 1)" 2>$null; $ok = ($LASTEXITCODE -eq 0) }
if (-not $ok) {
  winget install --id Python.Python.3.12 -e --silent --scope user --accept-package-agreements --accept-source-agreements | Out-Null
  $py = Join-Path $env:LOCALAPPDATA 'Programs\Python\Launcher\py.exe'
}
$pyw = Join-Path (Split-Path $py) 'pyw.exe'

Step 2 'ffmpeg'
# ClipLab uses the ffmpeg in its own bin folder first, so keep that copy current. Subtitles need a build
# with speech-to-text (the whisper filter, in gyan.dev "full" builds from ffmpeg 8).
$bin = Join-Path $Home2 'bin'
$binFfmpeg = Join-Path $bin 'ffmpeg.exe'
$packages = Join-Path $env:LOCALAPPDATA 'Microsoft\WinGet\Packages'
function Has-Whisper($exe) {
  if (-not $exe -or -not (Test-Path $exe)) { return $false }
  $ErrorActionPreference = 'Continue'   # Windows PowerShell turns any stderr line into a stopping error otherwise
  try { $filters = & $exe -hide_banner -filters 2>$null | Out-String } catch { return $false }
  return [bool]($filters -match 'whisper\s+A->A')
}
function Newest-FFmpeg {   # winget keeps old version folders after an upgrade: take the newest build
  Get-ChildItem $packages -Recurse -Filter ffmpeg.exe -ErrorAction SilentlyContinue |
    Where-Object { $_.FullName -match 'Gyan\.FFmpeg' } | Sort-Object LastWriteTime -Descending | Select-Object -First 1
}
$onPath = (Get-Command ffmpeg -ErrorAction SilentlyContinue).Source
if (-not (Test-Path $binFfmpeg) -and (Has-Whisper $onPath)) {
  Write-Host "  Using $onPath"
} else {
  $found = Newest-FFmpeg
  if (-not (Has-Whisper $found.FullName)) {   # none yet, or too old for subtitles: get the current build
    $verb = if ($found) { 'upgrade' } else { 'install' }
    winget $verb --id Gyan.FFmpeg -e --silent --scope user --accept-package-agreements --accept-source-agreements | Out-Null
    $found = Newest-FFmpeg
  }
  if (-not $found -and -not (Test-Path $binFfmpeg)) { throw 'ffmpeg did not install. Install it (winget install Gyan.FFmpeg) and run setup again.' }
  if ($found -and (-not (Test-Path $binFfmpeg) -or -not (Has-Whisper $binFfmpeg) -or
                   $found.LastWriteTime -gt (Get-Item $binFfmpeg).LastWriteTime)) {
    New-Item -ItemType Directory -Force $bin | Out-Null
    Copy-Item (Join-Path $found.DirectoryName 'ffmpeg.exe'), (Join-Path $found.DirectoryName 'ffprobe.exe') $bin -Force
    Write-Host "  ClipLab's ffmpeg is now $($found.Directory.Parent.Name)."
  }
  if (-not (Has-Whisper $binFfmpeg)) {
    Write-Host '  This ffmpeg has no speech-to-text, so shorts are made without subtitles. Run setup again later to retry.' -ForegroundColor Yellow
  }
}
if ($InstallObs -and -not (Test-Path "$env:ProgramFiles\obs-studio")) {
  winget install --id OBSProject.OBSStudio -e --silent --accept-package-agreements --accept-source-agreements | Out-Null
}

Step 3 'Config'
New-Item -ItemType Directory -Force $Home2 | Out-Null
$config = Join-Path $Home2 'config.json'
if ((Test-Path $config) -and -not $Force) {
  Write-Host "  Keeping your existing $config (use -Force to replace it)."
  if ($PSBoundParameters.ContainsKey('WatchDir') -or $PSBoundParameters.ContainsKey('OutputDir')) {
    Write-Host '  -WatchDir and -OutputDir only apply to a new config: edit config.json, or rerun with -Force.' -ForegroundColor Yellow
  }
  if ($PSBoundParameters.ContainsKey('Game')) {
    $cfg = Get-Content $config -Raw | ConvertFrom-Json
    $cfg | Add-Member -NotePropertyName game -NotePropertyValue $Game -Force
    if ($Game -ne 'league') { $cfg | Add-Member -NotePropertyName tag_wait -NotePropertyValue 0 -Force }
    [IO.File]::WriteAllText($config, ($cfg | ConvertTo-Json -Depth 5), $Utf8)
    Write-Host "  Game set to $Game."
  }
} else {
  New-Item -ItemType Directory -Force $WatchDir, $OutputDir | Out-Null
  $cfg = [ordered]@{ watch_dir = (Resolve-Path $WatchDir).Path; output_dir = (Resolve-Path $OutputDir).Path; allow_lan = [bool]$AllowLan; game = $Game }
  if ($Game -ne 'league') { $cfg.tag_wait = 0 }   # only the League auto-clipper writes tag files
  [IO.File]::WriteAllText($config, ($cfg | ConvertTo-Json), $Utf8)
}
Push-Location $Repo
& $py -3 -m cliplab config | Select-Object -First 3
Pop-Location

Step 4 'Start at sign-in'
if (-not $NoStartup) {
  $lnk = (New-Object -ComObject WScript.Shell).CreateShortcut((Join-Path ([Environment]::GetFolderPath('Startup')) 'ClipLab.lnk'))
  $lnk.TargetPath = $pyw; $lnk.Arguments = '-3 -m cliplab'; $lnk.WorkingDirectory = $Repo; $lnk.Save()
}

Step 5 'Starting ClipLab'
Start-Process $pyw -ArgumentList '-3 -m cliplab' -WorkingDirectory $Repo
Start-Sleep 4
# report what the config says (an update keeps the folders picked at install), with ClipLab's own defaults
$saved = Get-Content $config -Raw | ConvertFrom-Json
$port = $saved.port; if (-not $port) { $port = 8765 }
$watch = $saved.watch_dir; if (-not $watch) { $watch = Join-Path $Home2 'Inbox' }
$shorts = $saved.output_dir; if (-not $shorts) { $shorts = Join-Path $Home2 'Ready' }
Write-Host ''
Write-Host "ClipLab is running: http://localhost:$port" -ForegroundColor Green
Write-Host "  Raw clips:  $watch   (point OBS's replay buffer here)"
Write-Host "  Shorts:     $shorts"
if ($saved.allow_lan) { Write-Host '  Other devices: allow Python through Windows Firewall on Private networks, then use this PC''s IP.' }
