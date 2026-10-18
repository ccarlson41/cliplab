# ClipLab installer: downloads ClipLab, asks a few questions, then runs scripts\setup.ps1.
# Paste this into PowerShell (Start menu > type PowerShell > Enter):
#
#   irm https://raw.githubusercontent.com/ccarlson41/cliplab/main/install.ps1 | iex
#
# No git needed. It installs the latest release (checked against its SHA-256), or the main branch if there's no
# release yet. Run it again any time to update; your settings and shorts are kept (ClipLab also updates itself).
# Testing a branch: set $env:CLIPLAB_BRANCH (and $env:CLIPLAB_REPO for a fork) first; that skips releases.

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
    Write-Host '  3  VALORANT'
    $game = @{ '2' = 'r6'; '3' = 'valorant' }[(Ask 'Pick 1, 2 or 3' '1')]
    if (-not $game) { $game = 'league' }
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

  # --- download: the latest release, checked against its SHA-256 (or a branch when there's no release) ---
  if (Test-Path (Join-Path $dir '.git')) {
    Write-Host 'This copy of ClipLab came from git, so it is not re-downloaded. Update it with: git pull' -ForegroundColor Yellow
  } else {
    $tmp = Join-Path ([IO.Path]::GetTempPath()) ('cliplab-' + [guid]::NewGuid().ToString('N').Substring(0, 8))
    New-Item -ItemType Directory -Force $tmp | Out-Null
    try {
      $zip = Join-Path $tmp 'cliplab.zip'
      $release = $null; $asset = $null; $sums = $null
      if (-not $env:CLIPLAB_BRANCH) {
        try { $release = Invoke-RestMethod "https://api.github.com/repos/$repo/releases/latest" -Headers @{ 'User-Agent' = 'ClipLab-installer' } -UseBasicParsing }
        catch { $release = $null }   # no release yet (or GitHub didn't answer): use the branch
      }
      if ($release) {
        $version = "$($release.tag_name)" -replace '^v', ''
        $asset = @($release.assets) | Where-Object { $_.name -eq "cliplab-$version.zip" } | Select-Object -First 1
        $sums = @($release.assets) | Where-Object { $_.name -eq "cliplab-$version.zip.sha256" } | Select-Object -First 1
      }
      if ($asset -and $sums) {
        Write-Host "Downloading ClipLab $version..." -ForegroundColor Cyan
        $sumFile = Join-Path $tmp 'cliplab.zip.sha256'
        try {
          Invoke-WebRequest $asset.browser_download_url -OutFile $zip -UseBasicParsing
          Invoke-WebRequest $sums.browser_download_url -OutFile $sumFile -UseBasicParsing
        } catch { return Fail "Couldn't download ClipLab: $($_.Exception.Message)" }
        $want = ((Get-Content $sumFile -Raw).Trim() -split '\s+')[0].ToLower()
        $got = (Get-FileHash $zip -Algorithm SHA256).Hash.ToLower()
        if ($got -ne $want) { return Fail "The download didn't match its checksum, so nothing was installed. Try again in a few minutes." }
      } else {
        Write-Host "Downloading ClipLab ($repo, $branch)..." -ForegroundColor Cyan
        try { Invoke-WebRequest "https://github.com/$repo/archive/refs/heads/$branch.zip" -OutFile $zip -UseBasicParsing }
        catch { return Fail "Couldn't download ClipLab: $($_.Exception.Message)" }
      }
      Expand-Archive $zip (Join-Path $tmp 'x') -Force
      $src = Get-ChildItem (Join-Path $tmp 'x') -Directory | Select-Object -First 1
      New-Item -ItemType Directory -Force $dir | Out-Null
      Copy-Item (Join-Path $src.FullName '*') $dir -Recurse -Force   # code only; your clips and settings live in other files
      # the list of code files lets ClipLab's updater remove files a later version drops (see cliplab\updater.py)
      $files = Get-ChildItem $src.FullName -Recurse -File -Force | ForEach-Object { $_.FullName.Substring($src.FullName.Length + 1).Replace('\', '/') }
      [IO.File]::WriteAllLines((Join-Path $dir '.cliplab-files'), [string[]]$files)
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
