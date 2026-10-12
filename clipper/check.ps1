# Health check: is the clipper running, can it reach OBS (replay buffer on?), and Syncthing if it's used?
$config = Get-Content (Join-Path $PSScriptRoot 'config.json') -Raw | ConvertFrom-Json

$running = Get-CimInstance Win32_Process -Filter "Name='powershell.exe'" | Where-Object { $_.CommandLine -like '*autoclip.ps1*' -and $_.CommandLine -notlike '*check.ps1*' }
'Auto-clipper running: ' + [bool]$running

# Reuse the clipper's own obs-websocket client
$t = Get-Content (Join-Path $PSScriptRoot 'autoclip.ps1') -Raw
$start = $t.IndexOf('Add-Type -ReferencedAssemblies'); $end = $t.IndexOf('[LolTls]::Install()')
Invoke-Expression $t.Substring($start, $end - $start)
try {
  $pwFile = [Environment]::ExpandEnvironmentVariables([string]$config.obs.passwordFile)
  if ($pwFile) { $pw = (Get-Content $pwFile -Raw).Trim() }
  else { $pw = (Get-Content "$env:APPDATA\obs-studio\plugin_config\obs-websocket\config.json" -Raw | ConvertFrom-Json).server_password }
  $c = New-Object ObsClient
  $c.Connect("ws://127.0.0.1:$($config.obs.port)", $pw, 0, 4000)
  'OBS replay buffer on: ' + ($c.Request('GetReplayBufferStatus', $null, 3000) | ConvertFrom-Json).outputActive
  'OBS profile: ' + ($c.Request('GetProfileList', $null, 3000) | ConvertFrom-Json).currentProfileName
  $c.Dispose()
} catch { 'OBS: not reachable (' + $_.Exception.GetBaseException().Message + ')' }

if ($config.sync.pauseDuringGames) {
  $st = [Environment]::ExpandEnvironmentVariables([string]$config.sync.syncthingConfig)
  if (-not $st) { $st = Join-Path $env:LOCALAPPDATA 'Syncthing\config.xml' }
  try {
    [xml]$x = Get-Content $st
    $null = Invoke-RestMethod "http://$($x.configuration.gui.address)/rest/system/status" -Headers @{ 'X-API-Key' = $x.configuration.gui.apikey } -TimeoutSec 5
    'Syncthing: reachable'
  } catch { 'Syncthing: not reachable (' + $_.Exception.Message + ')' }
}
