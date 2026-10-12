# Starts OBS and the auto-clipper at sign-in (Startup-folder shortcuts, both hidden), creates config.json
# from the example if there isn't one, and starts both now. Run it again any time; -Uninstall removes the shortcuts.
param([switch]$Uninstall, [switch]$NoObs)

$ErrorActionPreference = 'Stop'
$startup = [Environment]::GetFolderPath('Startup')
$links = @{ obs = Join-Path $startup 'ClipLab OBS.lnk'; clipper = Join-Path $startup 'ClipLab Auto-clipper.lnk' }

if ($Uninstall) {
  $links.Values | Where-Object { Test-Path $_ } | Remove-Item
  Write-Host 'Removed the sign-in shortcuts. Stop a running clipper from Task Manager (powershell.exe running autoclip.ps1).'
  return
}

$config = Join-Path $PSScriptRoot 'config.json'
if (-not (Test-Path $config)) {
  Copy-Item (Join-Path $PSScriptRoot 'config.example.json') $config
  Write-Host "Created $config from the example. Check clipsDir matches OBS's recording path."
}

# conhost --headless runs PowerShell with no window at all (Windows 11 / recent Windows 10)
function New-HiddenLink($path, $script, $extra) {
  $l = (New-Object -ComObject WScript.Shell).CreateShortcut($path)
  $l.TargetPath = "$env:WINDIR\System32\conhost.exe"
  $l.Arguments = "--headless powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$script`" $extra".Trim()
  $l.WorkingDirectory = $PSScriptRoot
  $l.WindowStyle = 7
  $l.Save()
  $l
}

$obsExe = [string]((Get-Content $config -Raw | ConvertFrom-Json).obs.exe)
$started = @()
if (-not $NoObs) {
  $extra = ''; if ($obsExe) { $extra = "-ObsExe `"$obsExe`"" }
  $l = New-HiddenLink $links.obs (Join-Path $PSScriptRoot 'start-obs.ps1') $extra
  Start-Process $l.TargetPath -ArgumentList $l.Arguments -WorkingDirectory $PSScriptRoot; $started += 'OBS'
}
$l = New-HiddenLink $links.clipper (Join-Path $PSScriptRoot 'autoclip.ps1') ''
Start-Process $l.TargetPath -ArgumentList $l.Arguments -WorkingDirectory $PSScriptRoot; $started += 'auto-clipper'

Write-Host "Started $($started -join ' and ') and set them to start at sign-in. Log: $(Join-Path $PSScriptRoot 'autoclip.log')"
Write-Host 'Check everything with: powershell -ExecutionPolicy Bypass -File check.ps1'
