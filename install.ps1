# ClipLab installer: downloads ClipLab, asks a few questions, then runs scripts\setup.ps1.
# Paste this into PowerShell (Start menu > type PowerShell > Enter):
#
#   irm https://raw.githubusercontent.com/ccarlson41/cliplab/main/install.ps1 | iex
#
# No git needed. Run it again any time to update; your settings and shorts are kept.
# Testing a branch: set $env:CLIPLAB_BRANCH (and $env:CLIPLAB_REPO for a fork) first.

& {
  $ErrorActionPreference = 'Stop'
  $ProgressPreference = 'SilentlyContinue'   # Windows PowerShell's progress bar makes downloads crawl
  [Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12
  $repo = if ($env:CLIPLAB_REPO) { $env:CLIPLAB_REPO } else { 'ccarlson41/cliplab' }
  $branch = if ($env:CLIPLAB_BRANCH) { $env:CLIPLAB_BRANCH } else { 'main' }
  $dir = if ($env:CLIPLAB_HOME) { $env:CLIPLAB_HOME } else { Join-Path $env:USERPROFILE 'ClipLab' }
  $videos = [Environment]::GetFolderPath('MyVideos')

  function Ask($question, $default) {
    $answer = Read-Host "$question [$default]"
    if ($answer.Trim()) { $answer.Trim() } else { $default }
  }
  function Fail($message) {
    Write-Host ''
    Write-Host $message -ForegroundColor Red
    Write-Host 'Nothing else was changed. Fix that and run the install line again.'
  }

  Write-Host ''
  Write-Host 'ClipLab installer' -ForegroundColor Cyan
  Write-Host 'Turns your OBS replays into vertical shorts. This takes a few minutes the first time.'
  Write-Host ''

  if ([Environment]::OSVersion.Version.Major -lt 10) { return Fail 'ClipLab needs Windows 10 or 11.' }
  if (-not (Get-Command winget -ErrorAction SilentlyContinue)) {
    Start-Process 'ms-windows-store://pdp/?productid=9NBLGGH4NNS1'
    return Fail 'ClipLab installs Python and ffmpeg with winget, which comes with "App Installer". The Microsoft Store just opened on it: install or update it, then run the install line again.'
  }

  # --- questions (a new install only; an update keeps your settings) ---
  $config = Join-Path $dir 'config.json'
  $fresh = -not (Test-Path $config)
  $setupArgs = @()
  if ($fresh) {
    Write-Host 'Which game do you play?'
    Write-Host '  1  League of Legends'
    Write-Host '  2  Rainbow Six Siege'
    $game = if ((Ask 'Pick 1 or 2' '1') -eq '2') { 'r6' } else { 'league' }
    Write-Host ''
    Write-Host 'Where should OBS save replays? ClipLab watches this folder. Press Enter for the suggestion.'
    while ($true) {
      $watch = (Ask 'Replays folder' (Join-Path $videos 'Replays')).Trim('"')
      if ($watch.TrimEnd('\') -ne $videos.TrimEnd('\')) { break }
      Write-Host 'Pick a folder of its own: finished shorts go in your Videos folder, and ClipLab would re-process them.' -ForegroundColor Yellow
    }
    Write-Host ''
    $lan = (Ask 'Open ClipLab from your phone on the same Wi-Fi too? y or n' 'n') -match '^y'
    $setupArgs = @('-Game', $game, '-WatchDir', $watch)
    if ($lan) { $setupArgs += '-AllowLan' }
  } else {
    Write-Host "Updating the ClipLab in $dir. Your settings are kept (change them in the app)."
  }
  Write-Host ''

  # --- download ---
  if (Test-Path (Join-Path $dir '.git')) {
    Write-Host 'This copy of ClipLab came from git, so it is not re-downloaded. Update it with: git pull' -ForegroundColor Yellow
  } else {
    Write-Host "Downloading ClipLab ($repo, $branch)..." -ForegroundColor Cyan
    $tmp = Join-Path ([IO.Path]::GetTempPath()) ('cliplab-' + [guid]::NewGuid().ToString('N').Substring(0, 8))
    New-Item -ItemType Directory -Force $tmp | Out-Null
    try {
      $zip = Join-Path $tmp 'cliplab.zip'
      try { Invoke-WebRequest "https://github.com/$repo/archive/refs/heads/$branch.zip" -OutFile $zip -UseBasicParsing }
      catch { return Fail "Couldn't download ClipLab: $($_.Exception.Message)" }
      Expand-Archive $zip (Join-Path $tmp 'x') -Force
      $src = Get-ChildItem (Join-Path $tmp 'x') -Directory | Select-Object -First 1
      New-Item -ItemType Directory -Force $dir | Out-Null
      Copy-Item (Join-Path $src.FullName '*') $dir -Recurse -Force   # code only; your clips and settings live in other files
    } finally {
      Remove-Item $tmp -Recurse -Force -ErrorAction SilentlyContinue
    }
  }

  # --- setup: Python, ffmpeg, config, start at sign-in, start now ---
  Write-Host ''
  & powershell.exe -NoProfile -ExecutionPolicy Bypass -File (Join-Path $dir 'scripts\setup.ps1') @setupArgs
  if ($LASTEXITCODE) { return Fail 'Setup stopped with an error (above).' }

  $port = (Get-Content $config -Raw | ConvertFrom-Json).port
  if (-not $port) { $port = 8765 }
  Write-Host ''
  Write-Host 'All set. ClipLab is opening in your browser and will walk you through setting up OBS.' -ForegroundColor Green
  Write-Host "Open it any time at http://localhost:$port (it starts by itself when you sign in)."
  Start-Process "http://localhost:$port"
}
