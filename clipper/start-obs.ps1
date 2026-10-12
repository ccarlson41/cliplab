# Starts OBS minimized to the tray with the replay buffer running.
# OBS leaves a run_* marker in .sentinel while it's open and treats a leftover one as a crash, which
# shows a "Crash Detected" prompt and blocks startup. Clearing stale markers first means a power cut or
# force-close can't leave OBS stuck on that prompt at sign-in.
param([string]$ObsExe)
if (-not $ObsExe) { $ObsExe = 'C:\Program Files\obs-studio\bin\64bit\obs64.exe' }
if (Get-Process obs64 -ErrorAction SilentlyContinue) { exit }
Remove-Item (Join-Path $env:APPDATA 'obs-studio\.sentinel\run_*') -Force -ErrorAction SilentlyContinue
Start-Process $ObsExe -ArgumentList '--startreplaybuffer', '--minimize-to-tray' -WorkingDirectory (Split-Path $ObsExe)
