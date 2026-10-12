# ClipLab auto-clipper
# Watches League's Live Client Data API during a game. When you get a multikill, an ace, first blood or
# an objective steal, it saves OBS's replay buffer through obs-websocket and writes a tag file
# "<clip>.mp4.json" next to the clip (docs/tag-format.md) so ClipLab can trim it and add text.
#
#   autoclip.ps1             run forever (install.ps1 starts it hidden at sign-in)
#   autoclip.ps1 -TestSave   save one replay right now with a "TEST CLIP" tag, then exit
#   autoclip.ps1 -SelfTest   run sample League events through the rules and print the tag, no OBS
# Settings live in config.json next to this script; copy config.example.json to start.
param([switch]$TestSave, [switch]$SelfTest, [string]$ConfigPath = (Join-Path $PSScriptRoot 'config.json'))

$ErrorActionPreference = 'Stop'
$Base        = $PSScriptRoot
$LogPath     = Join-Path $Base 'autoclip.log'
$ObsWsConfig = Join-Path $env:APPDATA 'obs-studio\plugin_config\obs-websocket\config.json'
$LolApi      = 'https://127.0.0.1:2999/liveclientdata'
Add-Type -AssemblyName Microsoft.VisualBasic   # FileSystem.DeleteFile can send to the Recycle Bin

function Log([string]$msg) {
  try {
    if ((Test-Path $LogPath) -and (Get-Item $LogPath).Length -gt 5MB) { Move-Item $LogPath "$LogPath.old" -Force }
    Add-Content -Path $LogPath -Value ('{0:yyyy-MM-dd HH:mm:ss}  {1}' -f (Get-Date), $msg) -Encoding UTF8
  } catch { }
  if ($TestSave -or $SelfTest) { Write-Host $msg }
}

if (-not ($TestSave -or $SelfTest)) {
  $mutex = New-Object System.Threading.Mutex($false, 'Local\ClipLabAutoclip')
  if (-not $mutex.WaitOne(0)) { exit }   # already running
}

# ---------- .NET helpers: TLS exception for League's local API, and an obs-websocket v5 client ----------
Add-Type -ReferencedAssemblies System.Web.Extensions -TypeDefinition @'
using System;
using System.Collections.Concurrent;
using System.Collections.Generic;
using System.Net;
using System.Net.Security;
using System.Net.WebSockets;
using System.Security.Cryptography;
using System.Security.Cryptography.X509Certificates;
using System.Text;
using System.Threading;
using System.Threading.Tasks;
using System.Web.Script.Serialization;

public static class LolTls {
  static bool installed;
  public static void Install() {
    if (installed) return;
    installed = true;
    ServicePointManager.SecurityProtocol |= SecurityProtocolType.Tls12;
    ServicePointManager.ServerCertificateValidationCallback = Validate;
  }
  // League's API uses a self-signed cert. Accept it for 127.0.0.1:2999 only; everything else is validated normally.
  static bool Validate(object sender, X509Certificate cert, X509Chain chain, SslPolicyErrors errors) {
    if (errors == SslPolicyErrors.None) return true;
    var req = sender as HttpWebRequest;
    return req != null && req.RequestUri.Host == "127.0.0.1" && req.RequestUri.Port == 2999;
  }
}

public class ObsClient : IDisposable {
  ClientWebSocket ws;
  CancellationTokenSource cts = new CancellationTokenSource();
  readonly BlockingCollection<Dictionary<string, object>> inbox = new BlockingCollection<Dictionary<string, object>>();
  readonly List<Dictionary<string, object>> events = new List<Dictionary<string, object>>();
  readonly object evLock = new object(), sendLock = new object();
  volatile bool dead;
  static readonly JavaScriptSerializer J = new JavaScriptSerializer { MaxJsonLength = int.MaxValue };

  public bool IsOpen { get { return ws != null && ws.State == WebSocketState.Open && !dead; } }

  public void Connect(string url, string password, int eventSubscriptions, int timeoutMs) {
    ws = new ClientWebSocket();
    ws.Options.AddSubProtocol("obswebsocket.json");
    if (!ws.ConnectAsync(new Uri(url), cts.Token).Wait(timeoutMs)) throw new TimeoutException("OBS websocket connect timed out");
    Task.Factory.StartNew(ReceiveLoop, TaskCreationOptions.LongRunning);
    var hello = (Dictionary<string, object>)Next(timeoutMs, 0, null)["d"];
    var identify = new Dictionary<string, object> { { "rpcVersion", 1 }, { "eventSubscriptions", eventSubscriptions } };
    if (hello.ContainsKey("authentication")) {
      var a = (Dictionary<string, object>)hello["authentication"];
      string secret = Sha64((password ?? "") + (string)a["salt"]);
      identify["authentication"] = Sha64(secret + (string)a["challenge"]);
    }
    Send(1, identify);
    Next(timeoutMs, 2, null);   // Identified; a wrong password closes the socket instead
  }

  // Sends a request and returns its responseData as JSON. Throws if OBS reports a failure.
  public string Request(string type, string dataJson, int timeoutMs) {
    string id = Guid.NewGuid().ToString("N");
    var d = new Dictionary<string, object> { { "requestType", type }, { "requestId", id } };
    if (!string.IsNullOrEmpty(dataJson)) d["requestData"] = J.DeserializeObject(dataJson);
    Send(6, d);
    var resp = (Dictionary<string, object>)Next(timeoutMs, 7, id)["d"];
    var status = (Dictionary<string, object>)resp["requestStatus"];
    if (!(bool)status["result"])
      throw new Exception(type + " failed: " + (status.ContainsKey("comment") ? status["comment"] : status["code"]));
    return resp.ContainsKey("responseData") ? J.Serialize(resp["responseData"]) : "{}";
  }

  public void ClearEvents() { lock (evLock) events.Clear(); }

  // Waits for an OBS event of the given type; returns its eventData as JSON, or null on timeout.
  public string WaitEvent(string type, int timeoutMs) {
    var deadline = DateTime.UtcNow.AddMilliseconds(timeoutMs);
    while (DateTime.UtcNow < deadline && !dead) {
      lock (evLock) {
        for (int i = 0; i < events.Count; i++) {
          if ((string)events[i]["eventType"] != type) continue;
          var e = events[i]; events.RemoveAt(i);
          return e.ContainsKey("eventData") ? J.Serialize(e["eventData"]) : "{}";
        }
      }
      Thread.Sleep(50);
    }
    return null;
  }

  void ReceiveLoop() {
    var buf = new byte[65536];
    var ms = new System.IO.MemoryStream();
    try {
      while (ws.State == WebSocketState.Open) {
        var r = ws.ReceiveAsync(new ArraySegment<byte>(buf), cts.Token).Result;
        if (r.MessageType == WebSocketMessageType.Close) break;
        ms.Write(buf, 0, r.Count);
        if (!r.EndOfMessage) continue;
        var msg = J.Deserialize<Dictionary<string, object>>(Encoding.UTF8.GetString(ms.ToArray()));
        ms.SetLength(0);
        if (Convert.ToInt32(msg["op"]) == 5) {
          lock (evLock) { events.Add((Dictionary<string, object>)msg["d"]); if (events.Count > 200) events.RemoveAt(0); }
        } else inbox.Add(msg);
      }
    } catch { }
    dead = true;
  }

  Dictionary<string, object> Next(int timeoutMs, int op, string requestId) {
    var deadline = DateTime.UtcNow.AddMilliseconds(timeoutMs);
    while (true) {
      int left = (int)(deadline - DateTime.UtcNow).TotalMilliseconds;
      Dictionary<string, object> m;
      if (left <= 0 || !inbox.TryTake(out m, left)) {
        throw new TimeoutException(dead ? "OBS closed the connection (wrong websocket password?)" : "OBS did not answer");
      }
      if (Convert.ToInt32(m["op"]) != op) continue;
      if (requestId != null && (string)((Dictionary<string, object>)m["d"])["requestId"] != requestId) continue;
      return m;
    }
  }

  void Send(int op, object d) {
    var bytes = Encoding.UTF8.GetBytes(J.Serialize(new Dictionary<string, object> { { "op", op }, { "d", d } }));
    lock (sendLock) {
      if (!ws.SendAsync(new ArraySegment<byte>(bytes), WebSocketMessageType.Text, true, cts.Token).Wait(5000))
        throw new TimeoutException("Sending to OBS timed out");
    }
  }

  static string Sha64(string s) {
    using (var sha = SHA256.Create()) return Convert.ToBase64String(sha.ComputeHash(Encoding.UTF8.GetBytes(s)));
  }

  public void Dispose() {
    dead = true;
    try { cts.Cancel(); } catch { }
    try { ws.Dispose(); } catch { }
  }
}
'@
[LolTls]::Install()

# ---------- settings ----------
$script:Cfg = $null
$script:CfgStamp = [datetime]::MinValue
function Load-Config {
  if (-not (Test-Path $ConfigPath)) { Log "No settings file at $ConfigPath. Copy config.example.json to config.json first."; exit 1 }
  $stamp = (Get-Item $ConfigPath).LastWriteTimeUtc
  if ($stamp -eq $script:CfgStamp) { return $false }
  $script:CfgStamp = $stamp
  try { $c = Get-Content $ConfigPath -Raw | ConvertFrom-Json }
  catch {
    if (-not $script:Cfg) { Log "Settings file can't be read: $($_.Exception.Message)"; throw }
    Log "Settings file has a mistake, keeping the previous settings: $($_.Exception.Message)"
    return $false
  }
  $script:Cfg = $c
  $script:ClipsDir = [Environment]::ExpandEnvironmentVariables([string]$c.clipsDir)
  # Tag files are written here first and then renamed into clipsDir, so nothing ever sees half a file.
  # The rename is only atomic on the same drive, so fall back to a hidden folder inside clipsDir.
  $script:TmpDir = Join-Path $Base 'tmp'
  if ([IO.Path]::GetPathRoot($script:TmpDir) -ne [IO.Path]::GetPathRoot($script:ClipsDir)) { $script:TmpDir = Join-Path $script:ClipsDir '.cliplab-tmp' }
  New-Item -ItemType Directory -Force $script:ClipsDir, $script:TmpDir | Out-Null
  $script:StConfig = [Environment]::ExpandEnvironmentVariables([string]$c.sync.syncthingConfig)
  if (-not $script:StConfig) { $script:StConfig = Join-Path $env:LOCALAPPDATA 'Syncthing\config.xml' }
  $script:ShortsDir = [Environment]::ExpandEnvironmentVariables([string]$c.cleanup.shortsDir)
  if ($script:CfgLoadedOnce) { Log 'Settings reloaded.' }
  $script:CfgLoadedOnce = $true
  return $true
}

# ---------- OBS ----------
$script:Obs = $null
$script:ObsDown = $false
$script:BufferSeconds = 60
$script:LastSaved = $null

function Obs-Call([string]$type, $data) {
  $json = $null
  if ($data) { $json = $data | ConvertTo-Json -Compress -Depth 5 }
  $script:Obs.Request($type, $json, 5000) | ConvertFrom-Json
}

function Apply-Audio {
  $parts = @()
  if (-not $script:Cfg.audio) { return }
  foreach ($p in $script:Cfg.audio.PSObject.Properties) {
    $a = $p.Value
    if ($p.Name -like '_*' -or -not $a.source) { continue }
    $vol = [double]$a.volume
    if ($vol -lt 0) { $vol = 0 } elseif ($vol -gt 1) { $vol = 1 }
    try {
      Obs-Call 'SetInputMute' @{ inputName = [string]$a.source; inputMuted = (-not [bool]$a.on) } | Out-Null
      Obs-Call 'SetInputVolume' @{ inputName = [string]$a.source; inputVolumeMul = $vol } | Out-Null
      $state = 'off'; if ([bool]$a.on) { $state = "on {0:0}%" -f ($vol * 100) }
      $parts += "$($p.Name) $state"
    } catch { Log "Couldn't set '$($a.source)' in OBS: $($_.Exception.Message)" }
  }
  if ($parts) { Log ('Audio: ' + ($parts -join ', ')) }
}

function Get-ObsPassword {
  $file = [Environment]::ExpandEnvironmentVariables([string]$script:Cfg.obs.passwordFile)
  if ($file) { return (Get-Content $file -Raw).Trim() }
  (Get-Content $ObsWsConfig -Raw | ConvertFrom-Json).server_password
}

function Ensure-ReplayBuffer {
  if (-not (Obs-Call 'GetReplayBufferStatus' $null).outputActive) {
    Log 'Replay buffer was off; starting it.'
    Obs-Call 'StartReplayBuffer' $null | Out-Null
    Start-Sleep -Seconds 1
  }
}

function Connect-Obs([bool]$launch) {
  if ($script:Obs -and $script:Obs.IsOpen) { return $true }
  if ($script:Obs) { $script:Obs.Dispose(); $script:Obs = $null }
  if (-not (Get-Process obs64 -ErrorAction SilentlyContinue)) {
    if (-not ($launch -and $script:Cfg.obs.launchIfClosed)) { return $false }
    Log 'OBS is not running; starting it.'
    & (Join-Path $Base 'start-obs.ps1') -ObsExe ([string]$script:Cfg.obs.exe)
    Start-Sleep -Seconds 8
  }
  $c = $null
  try {
    $pw = Get-ObsPassword
    $c = New-Object ObsClient
    $c.Connect("ws://127.0.0.1:$($script:Cfg.obs.port)", $pw, 64, 4000)   # 64 = Outputs events (ReplayBufferSaved)
    $script:Obs = $c
    Log 'Connected to OBS.'
    $script:ObsDown = $false
    try {
      $rb = [int](Obs-Call 'GetProfileParameter' @{ parameterCategory = 'AdvOut'; parameterName = 'RecRBTime' }).parameterValue
      if ($rb -gt 0) { $script:BufferSeconds = $rb }
    } catch { }
    Apply-Audio
    Ensure-ReplayBuffer
    return $true
  } catch {
    if ($c) { $c.Dispose() }
    $script:Obs = $null
    if (-not $script:ObsDown) { Log "Can't reach OBS: $($_.Exception.GetBaseException().Message)"; $script:ObsDown = $true }
    return $false
  }
}

# ---------- Syncthing: hold uploads while a game is running so nothing competes with League for the network ----------
$script:SyncPaused = $null
function Set-SyncPaused([bool]$pause) {
  $verb = 'resume'; if ($pause) { $verb = 'pause' }
  try {
    [xml]$x = Get-Content $script:StConfig
    Invoke-RestMethod "http://$($x.configuration.gui.address)/rest/system/$verb" -Method Post -TimeoutSec 5 `
      -Headers @{ 'X-API-Key' = $x.configuration.gui.apikey } | Out-Null    # no device = all devices
    if ($script:SyncPaused -ne $pause) {
      if ($pause) { Log 'Syncing paused until the game ends.' } else { Log 'Syncing on.' }
    }
    $script:SyncPaused = $pause
  } catch { Log "Couldn't $verb Syncthing: $($_.Exception.GetBaseException().Message)" }
}

# ---------- raw clip cleanup: old raws go to the Recycle Bin; shorts and anything moved out of clipsDir are never touched ----------
$script:LastCleanup = [datetime]::MinValue
function Remove-OldClips {
  $days = [double]$script:Cfg.cleanup.rawClipDays
  if ($days -le 0) { return }
  $cutoff = (Get-Date).AddDays(-$days)
  $rendered = $null
  if ($script:ShortsDir) {
    $rendered = @{}
    Get-ChildItem $script:ShortsDir -Recurse -File -Include '*_crop.mp4', '*_blur.mp4' -ErrorAction SilentlyContinue |
      ForEach-Object { $rendered[($_.BaseName -replace '_(crop|blur)$', '')] = $true }
  }
  foreach ($f in Get-ChildItem $script:ClipsDir -Recurse -File -Include '*.mp4', '*.mkv', '*.mov' -ErrorAction SilentlyContinue) {
    if ($f.LastWriteTime -gt $cutoff) { continue }
    # no short yet (second PC off, or the short was tossed): give it double the time
    if ($rendered -and -not $rendered.ContainsKey($f.BaseName) -and $f.LastWriteTime -gt $cutoff.AddDays(-$days)) { continue }
    try {
      [Microsoft.VisualBasic.FileIO.FileSystem]::DeleteFile($f.FullName, 'OnlyErrorDialogs', 'SendToRecycleBin')
      $tag = "$($f.FullName).json"
      if (Test-Path -LiteralPath $tag) { [Microsoft.VisualBasic.FileIO.FileSystem]::DeleteFile($tag, 'OnlyErrorDialogs', 'SendToRecycleBin') }
      Log "Raw clip older than $days days moved to the Recycle Bin: $($f.Name)"
    } catch { Log "Couldn't clean up $($f.Name): $($_.Exception.GetBaseException().Message)" }
  }
}

# ---------- League ----------
$Lead   = @{ 'DOUBLE KILL' = 'Double kill'; 'TRIPLE KILL' = 'Triple kill'; 'QUADRA KILL' = 'Quadra kill'; 'PENTAKILL' = 'Pentakill'
             'ACE' = 'Ace'; 'BARON STEAL' = 'Baron steal'; 'DRAGON STEAL' = 'Dragon steal'; 'HERALD STEAL' = 'Herald steal'
             'FIRST BLOOD' = 'First blood'; 'TEST CLIP' = 'Test clip' }
$Follow = @{ 'DOUBLE KILL' = 'a double kill'; 'TRIPLE KILL' = 'a triple kill'; 'QUADRA KILL' = 'a quadra kill'; 'PENTAKILL' = 'a pentakill'
             'ACE' = 'an ace'; 'BARON STEAL' = 'a Baron steal'; 'DRAGON STEAL' = 'a dragon steal'; 'HERALD STEAL' = 'a Herald steal'
             'FIRST BLOOD' = 'first blood'; 'TEST CLIP' = 'a test clip' }

# Read as UTF-8 explicitly (Invoke-RestMethod in PowerShell 5.1 mangles accented names). Callers assign
# the result to a variable before piping, because a JSON array comes back as one object here.
function Get-Lol([string]$path) {
  $req = [Net.HttpWebRequest]::Create("$LolApi/$path")
  $req.Timeout = 2000; $req.ReadWriteTimeout = 2000; $req.Proxy = $null
  $resp = $req.GetResponse()
  try { (New-Object IO.StreamReader($resp.GetResponseStream(), [Text.Encoding]::UTF8)).ReadToEnd() | ConvertFrom-Json }
  finally { $resp.Close() }
}

function Load-Me {
  try {
    $ap = Get-Lol 'activeplayer'
    $names = @(@($ap.riotId, $ap.riotIdGameName, $ap.summonerName) | Where-Object { $_ })
    if ($ap.riotId) { $names += ($ap.riotId -split '#')[0] }
    if ($names.Count -eq 0) { return $false }
    $champ = ''
    try {
      $players = Get-Lol 'playerlist'
      $p = $players | Where-Object { ($_.riotId -and $names -contains $_.riotId) -or
                                                ($_.riotIdGameName -and $names -contains $_.riotIdGameName) -or
                                                ($_.summonerName -and $names -contains $_.summonerName) } | Select-Object -First 1
      if ($p) { $champ = [string]$p.championName }
    } catch { }
    $display = $names[0]
    $script:Me = [pscustomobject]@{ Names = @($names | Select-Object -Unique); Display = $display; Champion = $champ }
    return $true
  } catch { return $false }
}

function Test-Mine([string]$name) {
  if (-not $name -or -not $script:Me) { return $false }
  return [bool]($script:Me.Names -contains $name)
}

function Get-MultikillLabel([int]$streak) {
  switch ($streak) { 2 { 'DOUBLE KILL' } 3 { 'TRIPLE KILL' } 4 { 'QUADRA KILL' } default { 'PENTAKILL' } }
}

# Returns @{Type; Label; Streak} if this event should be clipped, otherwise $null
function Get-ClipReason($ev) {
  $c = $script:Cfg.clipOn
  switch ([string]$ev.EventName) {
    'Multikill'  { $s = [int]$ev.KillStreak
                   if ([int]$c.multikillMinimum -gt 0 -and $s -ge [int]$c.multikillMinimum -and (Test-Mine $ev.KillerName)) {
                     return @{ Type = 'Multikill'; Label = (Get-MultikillLabel $s); Streak = $s } } }
    'Ace'        { if ($c.ace -and (Test-Mine $ev.Acer)) { return @{ Type = 'Ace'; Label = 'ACE'; Streak = 0 } } }
    'FirstBlood' { if ($c.firstBlood -and (Test-Mine $ev.Recipient)) { return @{ Type = 'FirstBlood'; Label = 'FIRST BLOOD'; Streak = 0 } } }
    'BaronKill'  { if ($c.baronSteal -and "$($ev.Stolen)" -eq 'True' -and (Test-Mine $ev.KillerName)) { return @{ Type = 'BaronKill'; Label = 'BARON STEAL'; Streak = 0 } } }
    'DragonKill' { if ($c.dragonSteal -and "$($ev.Stolen)" -eq 'True' -and (Test-Mine $ev.KillerName)) { return @{ Type = 'DragonKill'; Label = 'DRAGON STEAL'; Streak = 0 } } }
    'HeraldKill' { if ($c.heraldSteal -and "$($ev.Stolen)" -eq 'True' -and (Test-Mine $ev.KillerName)) { return @{ Type = 'HeraldKill'; Label = 'HERALD STEAL'; Streak = 0 } } }
  }
  return $null
}

# ---------- grouping: plays close together become one clip ----------
$script:Pending = $null

function Add-ToClip($reason, $ev, [datetime]$seenAt) {
  $t = $script:Cfg.timing
  if (-not $script:Pending) {
    $script:Pending = [pscustomobject]@{ Events = (New-Object System.Collections.ArrayList); Start = $seenAt; MkDeadline = $seenAt; OtherDeadline = $seenAt; Deadline = $seenAt }
  }
  $p = $script:Pending
  [void]$p.Events.Add([pscustomobject]@{ Type = $reason.Type; Label = $reason.Label; Streak = $reason.Streak; GameTime = [double]$ev.EventTime; SeenAt = $seenAt })
  if ($reason.Type -eq 'Multikill') {
    # only the latest multikill decides how long to wait: a penta can't grow, a double might
    $wait = [double]$t.secondsAfterEvent
    if ($reason.Streak -lt 5) { $wait = [math]::Max($wait, [double]$t.secondsToWaitForBiggerMultikill) }
    $p.MkDeadline = $seenAt.AddSeconds($wait)
  } else {
    $d = $seenAt.AddSeconds([double]$t.secondsAfterEvent)
    if ($d -gt $p.OtherDeadline) { $p.OtherDeadline = $d }
  }
  $deadline = $p.MkDeadline; if ($p.OtherDeadline -gt $deadline) { $deadline = $p.OtherDeadline }
  $cap = $p.Start.AddSeconds([double]$t.longestChainSeconds)
  if ($deadline -gt $cap) { $deadline = $cap }
  $p.Deadline = $deadline
  Log ('Clip-worthy: {0} at {1:0}s game time' -f $reason.Label, [double]$ev.EventTime)
}

function Get-Title($events, [string]$champ) {
  $top = $events | Where-Object { $_.Type -eq 'Multikill' } | Sort-Object Streak -Descending | Select-Object -First 1
  $labels = New-Object System.Collections.Generic.List[string]
  $mkDone = $false
  foreach ($e in $events) {
    if ($e.Type -eq 'Multikill') {
      if ($mkDone) { continue }
      $mkDone = $true; $l = $top.Label
    } else { $l = $e.Label }
    if (-not $labels.Contains($l)) { $labels.Add($l) }
  }
  $title = $Lead[$labels[0]]
  if ($labels.Count -gt 1) { $title += ' into ' + ((@($labels)[1..($labels.Count - 1)] | ForEach-Object { $Follow[$_] }) -join ' and ') }
  if ($champ) { $title += " on $champ" }
  $title
}

function Get-Labels($clip) { ($clip.Events | ForEach-Object { $_.Label }) -join ' + ' }

function New-Tag($clip, [datetime]$cut) {
  $evs = @(foreach ($e in $clip.Events) {
    [ordered]@{ type = $e.Type; label = $e.Label; gameTime = [math]::Round($e.GameTime, 1)
                secondsBeforeEnd = [math]::Round(($cut - $e.SeenAt).TotalSeconds, 1) }
  })
  [ordered]@{
    version       = 1
    game          = 'League of Legends'
    savedAt       = $cut.ToString('yyyy-MM-ddTHH:mm:sszzz')
    bufferSeconds = $script:BufferSeconds
    player        = $script:Me.Display
    champion      = $script:Me.Champion
    events        = $evs
    title         = (Get-Title $clip.Events $script:Me.Champion)
  }
}

function Save-Clip($clip) {
  if (-not (Connect-Obs $true)) { Log "Missed a clip ($(Get-Labels $clip)): OBS isn't reachable."; return }
  try {
    $script:Obs.ClearEvents()
    $cut = Get-Date     # OBS cuts the buffer when the save request lands, so this is the clip's end
    Obs-Call 'SaveReplayBuffer' $null | Out-Null
    $saved = $script:Obs.WaitEvent('ReplayBufferSaved', 20000)
    if ($saved) { $path = ($saved | ConvertFrom-Json).savedReplayPath }
    else {
      $path = (Obs-Call 'GetLastReplayBufferReplay' $null).savedReplayPath
      if (-not $path -or $path -eq $script:LastSaved) { throw 'OBS never reported the saved file.' }
    }
    $path = [IO.Path]::GetFullPath($path)
    $script:LastSaved = $path

    $tmp = Join-Path $script:TmpDir ([guid]::NewGuid().ToString('N') + '.json')
    [IO.File]::WriteAllText($tmp, ((New-Tag $clip $cut) | ConvertTo-Json -Depth 6), (New-Object System.Text.UTF8Encoding($false)))
    $final = "$path.json"
    if (Test-Path -LiteralPath $final) { Remove-Item -LiteralPath $final -Force }
    [IO.File]::Move($tmp, $final)
    Log "Saved $(Split-Path $path -Leaf) + tag  [$(Get-Labels $clip)]"
  } catch {
    Log "Save failed ($(Get-Labels $clip)): $($_.Exception.GetBaseException().Message)"
    if ($script:Obs -and -not $script:Obs.IsOpen) { $script:Obs.Dispose(); $script:Obs = $null }
  }
}

# ---------- one-off modes ----------
if ($SelfTest) {
  [void](Load-Config)
  $script:Me = [pscustomobject]@{ Names = @('Player#NA1', 'Player'); Display = 'Player#NA1'; Champion = 'Ahri' }
  $t0 = Get-Date
  $sample = @(
    [pscustomobject]@{ EventID = 5;  EventName = 'ChampionKill'; EventTime = 1415.0; KillerName = 'Player' },
    [pscustomobject]@{ EventID = 6;  EventName = 'Multikill';    EventTime = 1417.2; KillerName = 'Player'; KillStreak = 2 },
    [pscustomobject]@{ EventID = 7;  EventName = 'Multikill';    EventTime = 1421.3; KillerName = 'Player'; KillStreak = 3 },
    [pscustomobject]@{ EventID = 8;  EventName = 'Ace';          EventTime = 1424.0; Acer = 'Player'; AcingTeam = 'ORDER' },
    [pscustomobject]@{ EventID = 9;  EventName = 'DragonKill';   EventTime = 1424.5; KillerName = 'Someone'; Stolen = 'True' },
    [pscustomobject]@{ EventID = 10; EventName = 'BaronKill';    EventTime = 1425.0; KillerName = 'Player'; Stolen = 'False' },
    [pscustomobject]@{ EventID = 11; EventName = 'Multikill';    EventTime = 1426.0; KillerName = 'Enemy'; KillStreak = 4 }
  )
  foreach ($e in $sample) {
    $r = Get-ClipReason $e
    Write-Host ('{0,-13} {1,-8} -> {2}' -f $e.EventName, $(if ($e.KillerName) { $e.KillerName } else { $e.Acer }), $(if ($r) { $r.Label } else { 'skip' }))
    if ($r) { Add-ToClip $r $e $t0.AddSeconds($e.EventTime - 1415.0) }
  }
  $cut = $script:Pending.Deadline
  Write-Host ('Save would happen {0:0.0}s after the first play.' -f ($cut - $script:Pending.Start).TotalSeconds)
  (New-Tag $script:Pending $cut) | ConvertTo-Json -Depth 6
  exit 0
}

[void](Load-Config)

if ($TestSave) {
  $script:Me = [pscustomobject]@{ Names = @(); Display = 'Test#TEST'; Champion = '' }
  if (-not (Connect-Obs $false)) { Log 'OBS is not running or not reachable.'; exit 1 }
  $now = Get-Date
  $clip = [pscustomobject]@{ Events = @([pscustomobject]@{ Type = 'Test'; Label = 'TEST CLIP'; Streak = 0; GameTime = 0.0; SeenAt = $now.AddSeconds(-5) }); Start = $now }
  Save-Clip $clip
  exit 0
}

# ---------- main loop ----------
function Reset-Game {
  $script:InGame = $false
  $script:Seen = New-Object 'System.Collections.Generic.HashSet[int]'
  $script:LastCount = 0
  $script:Fails = 0
  $script:Me = $null
}
Reset-Game
$lastObsCheck = [datetime]::MinValue
Log "Auto-clipper started (PID $PID)."
if ($script:Cfg.sync.pauseDuringGames) { Set-SyncPaused $false }    # in case the PC went down mid-game with syncing paused

while ($true) {
  try {
    if (Load-Config) {
      if ($script:Obs -and $script:Obs.IsOpen) { Apply-Audio }
      if ($script:InGame -and [bool]$script:Cfg.sync.pauseDuringGames -ne [bool]$script:SyncPaused) { Set-SyncPaused ([bool]$script:Cfg.sync.pauseDuringGames) }
    }

    if ($script:Pending -and (Get-Date) -ge $script:Pending.Deadline) {
      $clip = $script:Pending; $script:Pending = $null
      Save-Clip $clip
    }

    $data = $null
    try { $data = Get-Lol 'eventdata' } catch { }
    $now = Get-Date

    if (-not $data -or $null -eq $data.Events) {
      if ($script:InGame) {
        $script:Fails++
        if ($script:Fails -ge 5) {
          Log 'Game over (League data feed closed).'
          if ($script:Pending) { $clip = $script:Pending; $script:Pending = $null; Save-Clip $clip }
          Reset-Game
          if ($script:SyncPaused) { Set-SyncPaused $false }
        }
        Start-Sleep -Seconds 1
      } else {
        # retry until it sticks: at sign-in Syncthing may not be listening yet
        if ($script:Cfg.sync.pauseDuringGames -and $script:SyncPaused -ne $false) { Set-SyncPaused $false }
        if (((Get-Date) - $script:LastCleanup).TotalHours -ge 1) { $script:LastCleanup = Get-Date; Remove-OldClips }
        Start-Sleep -Seconds ([int]$script:Cfg.timing.idlePollSeconds)
      }
      continue
    }
    $script:Fails = 0
    $events = @($data.Events)

    if ($script:InGame -and $events.Count -lt $script:LastCount) { Log 'New game detected.'; Reset-Game }
    if (-not $script:InGame) {
      $script:InGame = $true
      foreach ($e in $events) { [void]$script:Seen.Add([int]$e.EventID) }
      Log "Game detected; skipping $($events.Count) event(s) from before now."
      if ($script:Cfg.sync.pauseDuringGames) { Set-SyncPaused $true }
      [void](Connect-Obs $true)
      $lastObsCheck = $now
    }
    $script:LastCount = $events.Count

    if (-not $script:Me -or -not $script:Me.Champion) {
      $had = [bool]$script:Me
      if ((Load-Me) -and (-not $had -or $script:Me.Champion)) { Log "Playing as $($script:Me.Display) on $(if ($script:Me.Champion) { $script:Me.Champion } else { 'unknown champion' })." }
    }

    if ($script:Me) {   # until we know who I am, leave new events unseen so they get checked once we do
      foreach ($e in $events) {
        if (-not $script:Seen.Add([int]$e.EventID)) { continue }
        $who = @($e.KillerName, $e.Acer, $e.Recipient) | Where-Object { $_ } | Select-Object -First 1
        Log ('Event {0}: {1}{2} at {3:0}s' -f $e.EventID, $e.EventName, $(if ($who) { " by $who" } else { '' }), [double]$e.EventTime)
        $reason = Get-ClipReason $e
        if ($reason) { Add-ToClip $reason $e $now }
      }
    }

    if (($now - $lastObsCheck).TotalSeconds -ge 30) {
      $lastObsCheck = $now
      if (Connect-Obs $true) { try { Ensure-ReplayBuffer } catch { } }
    }
    Start-Sleep -Milliseconds ([int](1000 * [double]$script:Cfg.timing.pollSeconds))
  } catch {
    Log "Unexpected error: $($_.Exception.Message)"
    Start-Sleep -Seconds 5
  }
}
