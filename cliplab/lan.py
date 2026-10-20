"""Phone access: the address a phone on the same Wi-Fi uses to open ClipLab, and a QR code for it.

Windows lists a lot of network adapters that a phone can't reach: Hyper-V and WSL virtual switches, VPN
tunnels, Bluetooth. ClipLab asks Windows for every adapter that's up (with PowerShell's NetTCPIP cmdlets),
skips those, and prefers a physical Wi-Fi or Ethernet adapter with a default gateway: the one on your
home network. Nothing here changes any network or firewall setting.
"""
import ipaddress
import json
import os
import re
import socket
import subprocess
import threading
import time

from .settings import NO_WINDOW
from .vendor.qrcodegen import QrCode

# adapters a phone on your Wi-Fi can't reach through
VIRTUAL = re.compile(r"hyper-v|vethernet|\bwsl\b|virtual|vmware|virtualbox|vbox|\bvpn\b|tap-|\btun\b|tunnel|wireguard|"
                     r"tailscale|zerotier|hamachi|openvpn|wintun|nordlynx|proton|cisco anyconnect|fortinet|"
                     r"loopback|bluetooth|npcap|docker", re.I)
HOME_NETWORK = re.compile(r"wi-?fi|wireless|wlan|802\.11|ethernet", re.I)

ADAPTERS_PS = r"""
$up = @{}
Get-NetAdapter -ErrorAction SilentlyContinue | Where-Object Status -eq 'Up' | ForEach-Object { $up[[int]$_.ifIndex] = $_ }
$gw = @{}
Get-NetRoute -DestinationPrefix '0.0.0.0/0' -ErrorAction SilentlyContinue | ForEach-Object { $gw[[int]$_.InterfaceIndex] = $true }
$list = @(Get-NetIPAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue | Where-Object { $up.ContainsKey([int]$_.InterfaceIndex) } | ForEach-Object {
  $a = $up[[int]$_.InterfaceIndex]
  [pscustomobject]@{ ip = $_.IPAddress; name = $_.InterfaceAlias; description = $a.InterfaceDescription;
                     physical = [bool]$a.HardwareInterface; gateway = [bool]$gw[[int]$_.InterfaceIndex] }
})
ConvertTo-Json -InputObject $list -Compress
"""
_cache = {"at": 0.0, "adapters": None, "refreshing": False}
_lock = threading.Lock()


def windows_adapters():
    """[{"ip", "name", "description", "physical", "gateway"}] for every IPv4 address on an adapter that's up.

    PowerShell can take several seconds to start, so after the first lookup this answers at once from the last
    one and, when that's over 30 s old, refreshes it in the background for next time."""
    if _cache["adapters"] is not None:
        if time.time() - _cache["at"] > 30 and not _cache["refreshing"]:
            _cache["refreshing"] = True
            threading.Thread(target=_refresh, daemon=True).start()
        return _cache["adapters"]
    with _lock:
        if _cache["adapters"] is None:
            _refresh()
    return _cache["adapters"]


def _refresh():
    try:
        adapters = _ask_windows()
        if not adapters:   # no PowerShell answer: every address this PC's name resolves to, nothing else known
            try:
                ips = {i[4][0] for i in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET)}
            except OSError:
                ips = set()
            adapters = [{"ip": ip, "name": "", "description": "", "physical": False, "gateway": False}
                        for ip in sorted(ips)]
        _cache.update(at=time.time(), adapters=adapters)
    finally:
        _cache["refreshing"] = False


def _ask_windows():
    if os.name != "nt":
        return []
    try:
        out = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", ADAPTERS_PS],
                             capture_output=True, text=True, timeout=20, creationflags=NO_WINDOW).stdout
        data = json.loads(out or "[]")
    except (OSError, subprocess.SubprocessError, ValueError):
        return []
    return [a for a in (data if isinstance(data, list) else [data]) if isinstance(a, dict)]


def default_route_ip():
    """The address this PC would use to reach the internet. A UDP "connect" sends nothing; it only picks a route."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("192.0.2.1", 9))   # TEST-NET-1: never actually contacted
            return s.getsockname()[0]
    except OSError:
        return None


def home_ip(text):
    """Whether an address is one a phone on a home network could use: private IPv4, not loopback or link-local."""
    try:
        ip = ipaddress.IPv4Address(str(text).strip())
    except ValueError:
        return False
    return ip.is_private and not ip.is_loopback and not ip.is_link_local


def pick(adapters, default_ip=None):
    """The addresses worth showing, best first: [{"ip", "name"}]. A physical adapter with a default gateway
    (your Wi-Fi or Ethernet) wins; virtual, VPN and Bluetooth adapters are left out."""
    scored = []
    for a in adapters:
        ip, label = str(a.get("ip", "")), f"{a.get('name') or ''} {a.get('description') or ''}"
        if not home_ip(ip) or VIRTUAL.search(label):
            continue
        score = (4 if a.get("physical") else 0) + (2 if a.get("gateway") else 0) + \
                (1 if HOME_NETWORK.search(label) else 0) + (1 if ip == default_ip else 0)
        scored.append((-score, len(scored), {"ip": ip, "name": (a.get("name") or "").strip()}))
    found = [s[2] for s in sorted(scored)]
    if not found and home_ip(default_ip):   # nothing obviously right: the route Windows itself would use
        found = [{"ip": default_ip, "name": ""}]
    seen, unique = set(), []
    for f in found:
        if f["ip"] not in seen:
            seen.add(f["ip"])
            unique.append(f)
    return unique


def phone_addresses():
    return pick(windows_adapters(), default_route_ip())


def qr_svg(text, border=3):
    """A QR code for text as a small SVG (dark modules on white, which every phone camera reads)."""
    qr = QrCode.encode_text(text, QrCode.Ecc.MEDIUM)
    n = qr.get_size()
    dots = "".join(f"M{x + border},{y + border}h1v1h-1z" for y in range(n) for x in range(n) if qr.get_module(x, y))
    side = n + 2 * border
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {side} {side}" shape-rendering="crispEdges" '
            f'role="img" aria-label="QR code for {text}"><rect width="{side}" height="{side}" fill="#fff"/>'
            f'<path d="{dots}" fill="#000"/></svg>')
