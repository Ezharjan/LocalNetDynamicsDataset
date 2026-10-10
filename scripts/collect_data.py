#!/usr/bin/env python3
"""
LocalNet-Dynamics telemetry collector.

Samples the network at a fixed cadence from a single client machine and
appends one row per sample to a CSV file in real time. Each sample records:

  * ICMP round-trip time to a reference target (min/avg/median/max/stddev),
    inter-packet jitter and packet loss
  * ICMP round-trip time and loss to the default gateway (first hop)
  * TCP connect time to the target (sub-millisecond resolution on every OS)
  * DNS response time of the configured resolver for a fixed name and for a
    never-before-seen name (forces a full recursive lookup)
  * HTTP time-to-first-response for a small connectivity-check URL
  * Wi-Fi link metrics when the machine is on wireless (signal, PHY rate,
    channel, band, standard)
  * WAN throughput (Ookla Speedtest) and LAN throughput (iperf3) on their own,
    slower cadence

The collector depends only on the Python standard library and the operating
system's `ping` utility. Optional external tools: Ookla `speedtest` CLI or the
`speedtest-cli` Python package (WAN throughput), `iperf3` (LAN throughput).

Typical use, 30-second cadence for 30 days with an hourly WAN speed test:

    python collect_data.py --duration 30d --interval 30s --speedtest --speedtest-every 1h

Run `python collect_data.py --check` to verify the environment and print one
sample without writing any files.
"""

from __future__ import annotations

import argparse
import base64
import contextlib
import csv
import json
import os
import platform
import random
import re
import shutil
import signal
import socket
import statistics
import struct
import subprocess
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, fields
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

COLLECTOR_VERSION = "2.0.1"

IS_WINDOWS = sys.platform.startswith("win")
IS_MACOS = sys.platform == "darwin"
IS_LINUX = sys.platform.startswith("linux")

ROUTE_REFRESH_S = 900.0          # re-detect gateway / link type every 15 min
META_REFRESH_S = 300.0           # rewrite the run metadata JSON every 5 min
SPEEDTEST_PING_FAILED_MS = 600_000.0   # speedtest-cli scores a failed latency attempt as 3600 s
USER_AGENT = f"LocalNet-Dynamics/{COLLECTOR_VERSION}"


# --------------------------------------------------------------------------- #
# Duration parsing
# --------------------------------------------------------------------------- #

_DURATION_RE = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*([smhd]?)\s*$", re.IGNORECASE)
_UNIT_SECONDS = {"": 1, "s": 1, "m": 60, "h": 3600, "d": 86400}


def parse_duration(text: str) -> float:
    """Parse '90', '30s', '10m', '2h', '1.5d' into seconds."""
    m = _DURATION_RE.match(str(text))
    if not m:
        raise argparse.ArgumentTypeError(
            f"invalid duration {text!r} (examples: 30s, 10m, 2h, 7d)"
        )
    value, unit = float(m.group(1)), m.group(2).lower()
    return value * _UNIT_SECONDS[unit]


def human_duration(seconds: float) -> str:
    seconds = int(round(seconds))
    parts = []
    for label, size in (("d", 86400), ("h", 3600), ("m", 60), ("s", 1)):
        if seconds >= size:
            parts.append(f"{seconds // size}{label}")
            seconds %= size
    return " ".join(parts) or "0s"


# --------------------------------------------------------------------------- #
# Sample schema
# --------------------------------------------------------------------------- #

@dataclass
class Sample:
    run_id: str = ""
    host_label: str = ""
    sample_index: int = 0
    timestamp_utc: str = ""
    timestamp_local: str = ""
    epoch_s: int = 0
    elapsed_s: float = 0.0
    link_type: str = ""
    gateway_ip: str = ""
    dns_server: str = ""
    target: str = ""
    rtt_min_ms: Optional[float] = None
    rtt_avg_ms: Optional[float] = None
    rtt_median_ms: Optional[float] = None
    rtt_max_ms: Optional[float] = None
    rtt_stddev_ms: Optional[float] = None
    jitter_ms: Optional[float] = None
    packets_sent: Optional[int] = None
    packets_received: Optional[int] = None
    packet_loss_pct: Optional[float] = None
    gateway_rtt_min_ms: Optional[float] = None
    gateway_rtt_avg_ms: Optional[float] = None
    gateway_rtt_max_ms: Optional[float] = None
    gateway_jitter_ms: Optional[float] = None
    gateway_packet_loss_pct: Optional[float] = None
    tcp_connect_min_ms: Optional[float] = None
    tcp_connect_median_ms: Optional[float] = None
    dns_resolve_ms: Optional[float] = None
    dns_recursive_ms: Optional[float] = None
    http_ms: Optional[float] = None
    http_status: Optional[int] = None
    wifi_ssid: str = ""
    wifi_signal_pct: Optional[int] = None
    wifi_rssi_dbm: Optional[float] = None
    wifi_rx_mbps: Optional[float] = None
    wifi_tx_mbps: Optional[float] = None
    wifi_channel: Optional[int] = None
    wifi_band_ghz: Optional[float] = None
    wifi_phy: str = ""
    download_mbps: Optional[float] = None
    upload_mbps: Optional[float] = None
    speedtest_ping_ms: Optional[float] = None
    download_loaded_latency_ms: Optional[float] = None
    upload_loaded_latency_ms: Optional[float] = None
    speedtest_server_id: Optional[int] = None
    speedtest_server: str = ""
    lan_throughput_mbps: Optional[float] = None
    lan_retransmits: Optional[int] = None
    sample_duration_s: Optional[float] = None
    notes: str = ""


CSV_FIELDS = [f.name for f in fields(Sample)]

SUMMARY_KEYS = [
    "rtt_avg_ms", "rtt_min_ms", "rtt_max_ms", "jitter_ms", "packet_loss_pct",
    "gateway_rtt_avg_ms", "gateway_packet_loss_pct",
    "tcp_connect_median_ms", "dns_resolve_ms", "dns_recursive_ms", "http_ms",
    "wifi_signal_pct", "wifi_rssi_dbm", "wifi_rx_mbps",
    "download_mbps", "upload_mbps", "speedtest_ping_ms",
    "download_loaded_latency_ms", "upload_loaded_latency_ms",
    "lan_throughput_mbps", "sample_duration_s",
]


# --------------------------------------------------------------------------- #
# Subprocess helpers
# --------------------------------------------------------------------------- #

def _console_encoding() -> str:
    """Encoding used by console tools (OEM code page on Windows)."""
    if IS_WINDOWS:
        try:
            import ctypes
            cp = ctypes.windll.kernel32.GetOEMCP()  # type: ignore[attr-defined]
            if cp:
                return f"cp{cp}"
        except Exception:
            pass
    return "utf-8"


def decode_output(data: bytes, encoding: Optional[str] = None) -> str:
    for enc in ((encoding,) if encoding else ()) + ("utf-8", _console_encoding(), "latin-1"):
        try:
            return data.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue
    return data.decode("utf-8", errors="replace")


def run_cmd(args: list[str], timeout: float = 20.0,
            encoding: Optional[str] = None) -> tuple[Optional[int], str]:
    """Run a command, returning (exit code, combined decoded output).

    The exit code is None when the command timed out or could not be started.
    """
    try:
        proc = subprocess.run(args, capture_output=True, timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        out = (exc.stdout or b"") + (exc.stderr or b"")
        return None, decode_output(out, encoding)
    except (FileNotFoundError, OSError):
        return None, ""
    return proc.returncode, decode_output(proc.stdout + proc.stderr, encoding)


def run_powershell(script: str, timeout: float = 30.0) -> str:
    """Run a PowerShell script (Windows only) and return its UTF-8 stdout."""
    if not IS_WINDOWS:
        return ""
    wrapped = "[Console]::OutputEncoding = [System.Text.Encoding]::UTF8\n" + script
    encoded = base64.b64encode(wrapped.encode("utf-16-le")).decode("ascii")
    for exe in ("powershell", "pwsh"):
        if shutil.which(exe) is None:
            continue
        code, out = run_cmd(
            [exe, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
             "-EncodedCommand", encoded],
            timeout=timeout, encoding="utf-8",
        )
        if code == 0 and out.strip():
            return out
    return ""


# --------------------------------------------------------------------------- #
# ICMP ping
# --------------------------------------------------------------------------- #

@dataclass
class PingResult:
    sent: int = 0
    received: int = 0
    rtts: Optional[list[float]] = None
    error: Optional[str] = None

    @property
    def loss_pct(self) -> Optional[float]:
        if self.sent <= 0:
            return None
        lost = max(0, self.sent - self.received)
        return round(100.0 * lost / self.sent, 1)


# RTT token of a reply line: "time=14.2 ms", "time=14ms", "time<1ms",
# "Zeit=14ms", "时间=14ms", "temps=14 ms", "время=14мс". The localized label
# before the operator is irrelevant; the unit is matched in Latin and Cyrillic.
# The operator must directly follow a non-space character, which excludes the
# summary lines ("Minimum = 14ms", "min/avg/max = 13.9/14.5/15.2 ms").
_RTT_RE = re.compile(r"\S([=<])\s*(\d+(?:[.,]\d+)?)\s*(?:ms|мс)\b", re.IGNORECASE)


def parse_ping_output(text: str, sent: int) -> PingResult:
    """Extract per-reply RTTs from `ping` output in a locale-independent way.

    Windows prints integer milliseconds and reports sub-millisecond replies as
    "<1ms"; those are stored as 0.0.
    """
    rtts: list[float] = []
    for line in text.splitlines():
        if "DUP!" in line:              # duplicate reply (Linux) - not a new echo
            continue
        m = _RTT_RE.search(line)
        if not m:
            continue
        if m.group(1) == "<":
            rtts.append(0.0)
        else:
            rtts.append(float(m.group(2).replace(",", ".")))
    return PingResult(sent=sent, received=len(rtts), rtts=rtts)


_PING_FLAVOR: Optional[str] = None


def ping_flavor() -> str:
    """windows | iputils | busybox | bsd (cached)."""
    global _PING_FLAVOR
    if _PING_FLAVOR is None:
        if IS_WINDOWS:
            _PING_FLAVOR = "windows"
        elif IS_LINUX:
            _, out = run_cmd(["ping", "-V"], timeout=5.0)
            _PING_FLAVOR = "busybox" if "busybox" in out.lower() else "iputils"
        else:
            _PING_FLAVOR = "bsd"
    return _PING_FLAVOR


def ping_command(host: str, count: int, timeout_s: float, flavor: str) -> list[str]:
    ipv6 = ":" in host
    if flavor == "windows":
        cmd = ["ping", "-n", str(count), "-w", str(int(timeout_s * 1000))]
        cmd.append("-6" if ipv6 else "-4")
        return cmd + [host]
    wait_s = str(max(1, int(round(timeout_s))))
    if flavor == "iputils":
        return ["ping", "-n", "-c", str(count), "-W", wait_s, host]
    if flavor == "busybox":
        return ["ping", "-c", str(count), "-W", wait_s, host]
    # macOS / BSD: -W is the per-reply wait in milliseconds
    return ["ping", "-n", "-c", str(count), "-W", str(int(timeout_s * 1000)), host]


def ping(host: str, count: int, timeout_s: float) -> PingResult:
    if shutil.which("ping") is None:
        return PingResult(sent=0, received=0, rtts=[], error="ping utility not found")
    budget = count * (1.0 + timeout_s) + 5.0
    code, out = run_cmd(ping_command(host, count, timeout_s, ping_flavor()), timeout=budget)
    result = parse_ping_output(out, sent=count)
    if code is None and not out:
        result.error = "ping could not be started"
        result.sent = 0
    elif result.received == 0:
        result.error = "no replies"
    return result


def rtt_stats(rtts: list[float]) -> dict:
    """min/avg/median/max/stddev/jitter for a burst of RTT samples."""
    if not rtts:
        return {"min": None, "avg": None, "median": None, "max": None,
                "stddev": None, "jitter": None}
    jitter = None
    if len(rtts) > 1:
        diffs = [abs(rtts[i] - rtts[i - 1]) for i in range(1, len(rtts))]
        jitter = round(sum(diffs) / len(diffs), 3)
    return {
        "min": round(min(rtts), 3),
        "avg": round(sum(rtts) / len(rtts), 3),
        "median": round(statistics.median(rtts), 3),
        "max": round(max(rtts), 3),
        "stddev": round(statistics.pstdev(rtts), 3) if len(rtts) > 1 else 0.0,
        "jitter": jitter,
    }


# --------------------------------------------------------------------------- #
# Route, gateway, resolver and link-type detection
# --------------------------------------------------------------------------- #

@dataclass
class NetworkInfo:
    gateway: Optional[str] = None
    interface: Optional[str] = None
    local_ip: Optional[str] = None
    link_type: str = "unknown"        # wifi | ethernet | other | unknown
    dns_servers: Optional[list[str]] = None
    source: str = ""


_WINDOWS_ROUTE_PS = r"""
$ErrorActionPreference = 'SilentlyContinue'
$routes = Get-NetRoute -AddressFamily IPv4 -DestinationPrefix '0.0.0.0/0' |
    Where-Object { $_.NextHop -ne '0.0.0.0' } |
    Where-Object { (Get-NetAdapter -InterfaceIndex $_.ifIndex).Status -eq 'Up' }
$r = $routes | Sort-Object { $_.RouteMetric + $_.InterfaceMetric } | Select-Object -First 1
if ($r) {
  $a = Get-NetAdapter -InterfaceIndex $r.ifIndex
  $ip = (Get-NetIPAddress -InterfaceIndex $r.ifIndex -AddressFamily IPv4 | Select-Object -First 1).IPAddress
  $dns = (Get-DnsClientServerAddress -InterfaceIndex $r.ifIndex -AddressFamily IPv4).ServerAddresses
  $o = [ordered]@{ gateway = [string]$r.NextHop; ifindex = $r.ifIndex; alias = [string]$a.Name;
                   media = [string]$a.PhysicalMediaType; ip = [string]$ip; dns = @($dns) }
  $o | ConvertTo-Json -Compress
}
"""

_ROUTE_PRINT_RE = re.compile(
    r"^\s*0\.0\.0\.0\s+0\.0\.0\.0\s+([\d.]+)\s+([\d.]+)\s+(\d+)\s*$", re.MULTILINE)


def parse_route_print(text: str) -> Optional[tuple[str, str]]:
    """Parse `route print -4 0.0.0.0` output -> (gateway, interface_ip)."""
    best = None
    for gw, iface_ip, metric in _ROUTE_PRINT_RE.findall(text):
        m = int(metric)
        if best is None or m < best[0]:
            best = (m, gw, iface_ip)
    return (best[1], best[2]) if best else None


def _link_type_from_media(media: str) -> str:
    media = (media or "").lower()
    if "802.11" in media or "wireless" in media or "wi-fi" in media:
        return "wifi"
    if "802.3" in media or "ethernet" in media:
        return "ethernet"
    if not media or media == "unspecified":
        return "unknown"
    return "other"


def detect_network_windows() -> NetworkInfo:
    info = NetworkInfo()
    out = run_powershell(_WINDOWS_ROUTE_PS, timeout=40.0)
    if out.strip():
        try:
            data = json.loads(out.strip().splitlines()[-1])
            info.gateway = data.get("gateway") or None
            info.interface = data.get("alias") or None
            info.local_ip = data.get("ip") or None
            info.link_type = _link_type_from_media(data.get("media", ""))
            dns = data.get("dns") or []
            if isinstance(dns, str):
                dns = [dns]
            info.dns_servers = [d for d in dns if d and ":" not in d] or None
            info.source = "powershell"
            if info.gateway:
                return info
        except (ValueError, AttributeError):
            pass
    code, out = run_cmd(["route", "print", "-4", "0.0.0.0"], timeout=20.0)
    parsed = parse_route_print(out)
    if parsed:
        info.gateway, info.local_ip = parsed
        info.source = "route print"
    return info


def parse_ip_route_get(text: str) -> dict:
    """Parse `ip -o route get <ip>` output into via/dev/src."""
    out = {}
    for key in ("via", "dev", "src"):
        m = re.search(rf"\b{key}\s+(\S+)", text)
        if m:
            out[key] = m.group(1)
    return out


def _linux_is_wireless(dev: str) -> bool:
    base = Path("/sys/class/net") / dev
    return (base / "wireless").exists() or (base / "phy80211").exists()


def parse_resolv_conf(text: str) -> list[str]:
    servers = []
    for line in text.splitlines():
        line = line.split("#", 1)[0].strip()
        if line.startswith("nameserver"):
            parts = line.split()
            if len(parts) >= 2:
                servers.append(parts[1])
    return servers


def detect_network_linux(target: str) -> NetworkInfo:
    info = NetworkInfo(source="ip route")
    code, out = run_cmd(["ip", "-o", "route", "get", target], timeout=10.0)
    parsed = parse_ip_route_get(out) if code == 0 else {}
    if not parsed.get("via"):
        code, out = run_cmd(["ip", "-o", "route", "show", "default"], timeout=10.0)
        parsed = parse_ip_route_get(out) if code == 0 else {}
    info.gateway = parsed.get("via")
    info.interface = parsed.get("dev")
    info.local_ip = parsed.get("src")
    if info.interface:
        info.link_type = "wifi" if _linux_is_wireless(info.interface) else "ethernet"
        if info.interface.startswith(("tun", "tap", "wg", "ppp")):
            info.link_type = "other"
    servers: list[str] = []
    try:
        servers = parse_resolv_conf(Path("/etc/resolv.conf").read_text(encoding="utf-8"))
    except OSError:
        pass
    if servers and servers[0].startswith("127.") and shutil.which("resolvectl"):
        code, out = run_cmd(["resolvectl", "dns"], timeout=10.0)
        upstream = []
        for line in out.splitlines():
            if info.interface and f"({info.interface})" in line:
                upstream += re.findall(r"\b(\d{1,3}(?:\.\d{1,3}){3})\b", line.split(":", 1)[-1])
        if not upstream:
            upstream = re.findall(r":\s*(\d{1,3}(?:\.\d{1,3}){3})", out)
        if upstream:
            servers = upstream
    info.dns_servers = [s for s in servers if ":" not in s] or None
    return info


def parse_route_get_default(text: str) -> dict:
    out = {}
    for key in ("gateway", "interface"):
        m = re.search(rf"^\s*{key}:\s*(\S+)", text, re.MULTILINE)
        if m:
            out[key] = m.group(1)
    return out


def parse_hardware_ports(text: str) -> dict:
    """`networksetup -listallhardwareports` -> {device: port name}."""
    mapping = {}
    port = None
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("Hardware Port:"):
            port = line.split(":", 1)[1].strip()
        elif line.startswith("Device:") and port:
            mapping[line.split(":", 1)[1].strip()] = port
    return mapping


def detect_network_macos() -> NetworkInfo:
    info = NetworkInfo(source="route get")
    code, out = run_cmd(["route", "-n", "get", "default"], timeout=10.0)
    parsed = parse_route_get_default(out)
    info.gateway = parsed.get("gateway")
    info.interface = parsed.get("interface")
    if info.interface:
        code, out = run_cmd(["networksetup", "-listallhardwareports"], timeout=10.0)
        port = parse_hardware_ports(out).get(info.interface, "")
        if "wi-fi" in port.lower() or "airport" in port.lower():
            info.link_type = "wifi"
        elif "ethernet" in port.lower() or "lan" in port.lower():
            info.link_type = "ethernet"
        elif port:
            info.link_type = "other"
    code, out = run_cmd(["scutil", "--dns"], timeout=10.0)
    servers = re.findall(r"nameserver\[\d+\]\s*:\s*(\d{1,3}(?:\.\d{1,3}){3})", out)
    info.dns_servers = list(dict.fromkeys(servers)) or None
    return info


def local_ip_for(target: str) -> Optional[str]:
    """Local address the OS would use to reach `target` (no packet is sent)."""
    family = socket.AF_INET6 if ":" in target else socket.AF_INET
    try:
        with socket.socket(family, socket.SOCK_DGRAM) as s:
            s.connect((target, 53))
            return s.getsockname()[0]
    except OSError:
        return None


def detect_network(target: str) -> NetworkInfo:
    try:
        if IS_WINDOWS:
            info = detect_network_windows()
        elif IS_MACOS:
            info = detect_network_macos()
        else:
            info = detect_network_linux(target)
    except Exception as exc:  # detection must never stop the collector
        info = NetworkInfo(source=f"failed: {exc}")
    if not info.local_ip:
        info.local_ip = local_ip_for(target)
    return info


# --------------------------------------------------------------------------- #
# DNS probe (raw UDP query, bypasses the operating-system cache)
# --------------------------------------------------------------------------- #

def build_dns_query(name: str, qid: int, qtype: int = 1) -> bytes:
    header = struct.pack(">HHHHHH", qid, 0x0100, 1, 0, 0, 0)   # RD=1, 1 question
    qname = b""
    for label in name.strip(".").split("."):
        raw = label.encode("idna") if label else b""
        qname += struct.pack("B", len(raw)) + raw
    return header + qname + b"\x00" + struct.pack(">HH", qtype, 1)


def parse_dns_response(data: bytes, qid: int) -> Optional[int]:
    """Return the response code, or None if the datagram does not match."""
    if len(data) < 12:
        return None
    rid, flags = struct.unpack(">HH", data[:4])
    if rid != qid or not flags & 0x8000:
        return None
    return flags & 0x000F


def dns_query(server: str, name: str, timeout_s: float = 3.0) -> tuple[Optional[float], Optional[int], Optional[str]]:
    """Time one UDP DNS query. Returns (milliseconds, rcode, error)."""
    qid = random.randint(0, 0xFFFF)
    packet = build_dns_query(name, qid)
    family = socket.AF_INET6 if ":" in server else socket.AF_INET
    try:
        with socket.socket(family, socket.SOCK_DGRAM) as s:
            s.settimeout(timeout_s)
            start = time.perf_counter()
            s.sendto(packet, (server, 53))
            deadline = start + timeout_s
            while True:
                remaining = deadline - time.perf_counter()
                if remaining <= 0:
                    return None, None, "timeout"
                s.settimeout(remaining)
                data, _ = s.recvfrom(4096)
                elapsed = (time.perf_counter() - start) * 1000.0
                rcode = parse_dns_response(data, qid)
                if rcode is None:
                    continue
                if rcode in (0, 3):          # NOERROR / NXDOMAIN are valid answers
                    return round(elapsed, 3), rcode, None
                return None, rcode, f"rcode {rcode}"
    except socket.timeout:
        return None, None, "timeout"
    except OSError as exc:
        return None, None, f"{exc.__class__.__name__}"


def random_label() -> str:
    return "%012x" % random.getrandbits(48)


# --------------------------------------------------------------------------- #
# TCP connect and HTTP probes
# --------------------------------------------------------------------------- #

def tcp_connect_times(host: str, port: int, count: int = 5, timeout_s: float = 3.0,
                      gap_s: float = 0.2) -> tuple[list[float], Optional[str]]:
    try:
        addr = socket.getaddrinfo(host, port, 0, socket.SOCK_STREAM)[0]
    except (socket.gaierror, IndexError) as exc:
        return [], f"resolve failed ({exc.__class__.__name__})"
    family, _, _, _, sockaddr = addr
    times: list[float] = []
    error = None
    for i in range(count):
        s = socket.socket(family, socket.SOCK_STREAM)
        s.settimeout(timeout_s)
        try:
            start = time.perf_counter()
            s.connect(sockaddr)
            times.append((time.perf_counter() - start) * 1000.0)
        except socket.timeout:
            error = "timeout"
        except OSError as exc:
            error = exc.__class__.__name__
        finally:
            try:
                s.close()
            except OSError:
                pass
        if i < count - 1:
            time.sleep(gap_s)
    return times, (error if not times else None)


def http_probe(url: str, timeout_s: float = 5.0) -> tuple[Optional[float], Optional[int], Optional[str]]:
    """Time-to-first-response for a small URL, bypassing any system proxy."""
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    req = urllib.request.Request(url, headers={
        "User-Agent": USER_AGENT, "Cache-Control": "no-cache", "Pragma": "no-cache",
    })
    start = time.perf_counter()
    try:
        with opener.open(req, timeout=timeout_s) as resp:
            elapsed = (time.perf_counter() - start) * 1000.0
            resp.read(4096)
            return round(elapsed, 3), resp.status, None
    except urllib.error.HTTPError as exc:
        elapsed = (time.perf_counter() - start) * 1000.0
        return round(elapsed, 3), exc.code, None
    except (urllib.error.URLError, socket.timeout, OSError, ValueError) as exc:
        reason = getattr(exc, "reason", exc)
        return None, None, f"{reason}"[:80]


# --------------------------------------------------------------------------- #
# Wi-Fi link metrics
# --------------------------------------------------------------------------- #

@dataclass
class WifiInfo:
    connected: bool = False
    ssid: Optional[str] = None
    signal_pct: Optional[int] = None
    rssi_dbm: Optional[float] = None
    rx_mbps: Optional[float] = None
    tx_mbps: Optional[float] = None
    channel: Optional[int] = None
    band_ghz: Optional[float] = None
    phy: Optional[str] = None


def rssi_from_quality(pct: int) -> float:
    """Windows WLAN mapping: quality 0 -> -100 dBm, 100 -> -50 dBm (linear)."""
    return round(pct / 2.0 - 100.0, 1)


def quality_from_rssi(rssi: float) -> int:
    return int(max(0, min(100, round((rssi + 100.0) * 2.0))))


def channel_from_freq_mhz(freq: int) -> tuple[Optional[int], Optional[float]]:
    if 2412 <= freq <= 2472:
        return (freq - 2407) // 5, 2.4
    if freq == 2484:
        return 14, 2.4
    if 5000 <= freq <= 5900:
        return (freq - 5000) // 5, 5.0
    if 5925 <= freq <= 7125:
        return (freq - 5950) // 5, 6.0
    if 57000 <= freq <= 71000:
        return None, 60.0
    return None, None


def band_from_channel(channel: int) -> Optional[float]:
    if 1 <= channel <= 14:
        return 2.4
    if 32 <= channel <= 177:
        return 5.0
    return None


_NETSH_KV_RE = re.compile(r"^\s*([^:]+?)\s*:\s*(.*?)\s*$")
_PCT_RE = re.compile(r"^(\d{1,3})\s*%$")
_INT_RE = re.compile(r"^\d{1,3}$")
_GHZ_RE = re.compile(r"^(\d+(?:\.\d+)?)\s*GHz$", re.IGNORECASE)
_PHY_RE = re.compile(r"^802\.11[a-z]{1,2}$", re.IGNORECASE)
_MBPS_KEY_RE = re.compile(r"\(\s*Mbps\s*\)", re.IGNORECASE)


def parse_netsh_wlan(text: str) -> WifiInfo:
    """Parse `netsh wlan show interfaces` output without relying on localized labels.

    Only the SSID/BSSID keys are language independent; every other field is
    recognised by the shape of its value (a percentage, "(Mbps)" in the key,
    "802.11xx", "x GHz", a bare channel number).
    """
    blocks: list[list[tuple[str, str]]] = [[]]
    for line in text.splitlines():
        if not line.strip():
            if blocks[-1]:
                blocks.append([])
            continue
        m = _NETSH_KV_RE.match(line)
        if m:
            blocks[-1].append((m.group(1), m.group(2)))
    info = WifiInfo()
    for block in blocks:
        kv = dict(block)
        ssid = kv.get("SSID")
        if not ssid or not any(_PCT_RE.match(v) for _, v in block):
            continue
        info.connected = True
        info.ssid = ssid
        rates: list[tuple[str, float]] = []
        for key, value in block:
            if key in ("SSID", "BSSID"):
                continue
            if info.signal_pct is None and _PCT_RE.match(value):
                info.signal_pct = int(_PCT_RE.match(value).group(1))
            elif _MBPS_KEY_RE.search(key):
                try:
                    rates.append((key.lower(), float(value.replace(",", "."))))
                except ValueError:
                    pass
            elif info.phy is None and _PHY_RE.match(value):
                info.phy = value.lower()
            elif info.band_ghz is None and _GHZ_RE.match(value):
                info.band_ghz = float(_GHZ_RE.match(value).group(1))
            elif info.channel is None and _INT_RE.match(value):
                info.channel = int(value)
        for key, rate in rates:
            if "transmit" in key or "tx" in key.split():
                info.tx_mbps = rate
            elif "receive" in key or "rx" in key.split():
                info.rx_mbps = rate
        if info.rx_mbps is None and rates:
            info.rx_mbps = rates[0][1]
        if info.tx_mbps is None and len(rates) > 1:
            info.tx_mbps = rates[1][1]
        if info.signal_pct is not None:
            info.rssi_dbm = rssi_from_quality(info.signal_pct)
        if info.band_ghz is None and info.channel is not None:
            info.band_ghz = band_from_channel(info.channel)
        break
    return info


def wifi_info_windows() -> WifiInfo:
    code, out = run_cmd(["netsh", "wlan", "show", "interfaces"], timeout=15.0)
    return parse_netsh_wlan(out) if out else WifiInfo()


_IW_PHY = (("EHT", "802.11be"), ("HE", "802.11ax"), ("VHT", "802.11ac"), ("HT", "802.11n"))


def parse_iw_link(text: str) -> WifiInfo:
    info = WifiInfo()
    if "Not connected" in text or "SSID:" not in text:
        return info
    info.connected = True
    m = re.search(r"^\s*SSID:\s*(.+?)\s*$", text, re.MULTILINE)
    info.ssid = m.group(1) if m else None
    m = re.search(r"^\s*freq:\s*(\d+)", text, re.MULTILINE)
    if m:
        info.channel, info.band_ghz = channel_from_freq_mhz(int(m.group(1)))
    m = re.search(r"^\s*signal:\s*(-?\d+(?:\.\d+)?)\s*dBm", text, re.MULTILINE)
    if m:
        info.rssi_dbm = float(m.group(1))
        info.signal_pct = quality_from_rssi(info.rssi_dbm)
    for key, attr in (("rx bitrate", "rx_mbps"), ("tx bitrate", "tx_mbps")):
        m = re.search(rf"^\s*{key}:\s*(\d+(?:\.\d+)?)\s*MBit/s(.*)$", text, re.MULTILINE | re.IGNORECASE)
        if m:
            setattr(info, attr, float(m.group(1)))
            if info.phy is None:
                rest = m.group(2).upper()
                for token, phy in _IW_PHY:
                    if re.search(rf"\b{token}-", rest):
                        info.phy = phy
                        break
                if info.phy is None and re.search(r"\bMCS\b", rest):
                    info.phy = "802.11n"       # HT rates are printed as bare "MCS n"
    return info


def parse_proc_net_wireless(text: str, iface: str) -> WifiInfo:
    info = WifiInfo()
    for line in text.splitlines():
        if line.strip().startswith(iface + ":"):
            parts = line.split()
            try:
                level = float(parts[3].rstrip("."))
            except (IndexError, ValueError):
                break
            info.connected = True
            info.rssi_dbm = level
            info.signal_pct = quality_from_rssi(level)
            break
    return info


def wifi_info_linux(iface: Optional[str]) -> WifiInfo:
    if not iface or not _linux_is_wireless(iface):
        return WifiInfo()
    if shutil.which("iw"):
        code, out = run_cmd(["iw", "dev", iface, "link"], timeout=10.0)
        if code == 0 and out:
            return parse_iw_link(out)
    try:
        return parse_proc_net_wireless(Path("/proc/net/wireless").read_text(), iface)
    except OSError:
        return WifiInfo()


def parse_system_profiler_airport(data: dict) -> WifiInfo:
    info = WifiInfo()
    try:
        interfaces = data["SPAirPortDataType"][0]["spairport_airport_interfaces"]
    except (KeyError, IndexError, TypeError):
        return info
    for entry in interfaces:
        net = entry.get("spairport_current_network_information")
        if not isinstance(net, dict):
            continue
        info.connected = True
        info.ssid = net.get("_name")
        m = re.search(r"(-?\d+)\s*dBm", str(net.get("spairport_signal_noise", "")))
        if m:
            info.rssi_dbm = float(m.group(1))
            info.signal_pct = quality_from_rssi(info.rssi_dbm)
        m = re.search(r"(\d+)\s*\((\d+(?:\.\d+)?)\s*GHz", str(net.get("spairport_network_channel", "")))
        if m:
            info.channel = int(m.group(1))
            info.band_ghz = float(m.group(2))
        phy = str(net.get("spairport_network_phymode", "")).lower()
        info.phy = phy if phy.startswith("802.11") else None
        rate = net.get("spairport_network_rate")
        try:
            info.rx_mbps = float(rate) if rate is not None else None
            info.tx_mbps = info.rx_mbps
        except (TypeError, ValueError):
            pass
        break
    return info


def wifi_info_macos() -> WifiInfo:
    code, out = run_cmd(["system_profiler", "SPAirPortDataType", "-json"], timeout=25.0)
    if code != 0 or not out:
        return WifiInfo()
    try:
        return parse_system_profiler_airport(json.loads(out))
    except ValueError:
        return WifiInfo()


def wifi_info(net: NetworkInfo) -> WifiInfo:
    try:
        if IS_WINDOWS:
            return wifi_info_windows()
        if IS_MACOS:
            return wifi_info_macos()
        return wifi_info_linux(net.interface)
    except Exception:
        return WifiInfo()


# --------------------------------------------------------------------------- #
# Throughput tests (slow cadence)
# --------------------------------------------------------------------------- #

def _ookla_cli_path() -> Optional[str]:
    """Path of the official Ookla CLI, distinguishing it from speedtest-cli."""
    exe = shutil.which("speedtest")
    if not exe:
        return None
    code, out = run_cmd([exe, "--version"], timeout=15.0)
    return exe if "ookla" in out.lower() else None


def parse_ookla_json(data: dict) -> dict:
    result = {}
    down = data.get("download") or {}
    up = data.get("upload") or {}
    if "bandwidth" in down:
        result["download_mbps"] = round(down["bandwidth"] * 8 / 1e6, 2)
    if "bandwidth" in up:
        result["upload_mbps"] = round(up["bandwidth"] * 8 / 1e6, 2)
    ping_info = data.get("ping") or {}
    if ping_info.get("latency") is not None:
        result["speedtest_ping_ms"] = round(float(ping_info["latency"]), 3)
    for key, col in (("download", "download_loaded_latency_ms"),
                     ("upload", "upload_loaded_latency_ms")):
        lat = (data.get(key) or {}).get("latency") or {}
        if lat.get("iqm") is not None:
            result[col] = round(float(lat["iqm"]), 3)
    server = data.get("server") or {}
    if server.get("id") is not None:
        result["speedtest_server_id"] = int(server["id"])
    name = ", ".join(str(server[k]) for k in ("name", "location") if server.get(k))
    if name:
        result["speedtest_server"] = name
    return result


def run_speedtest(server_id: Optional[int] = None, timeout_s: float = 180.0) -> dict:
    exe = _ookla_cli_path()
    if exe:
        cmd = [exe, "--accept-license", "--accept-gdpr", "--format=json", "--progress=no"]
        if server_id:
            cmd += ["--server-id", str(server_id)]
        code, out = run_cmd(cmd, timeout=timeout_s, encoding="utf-8")
        for line in out.splitlines():
            line = line.strip()
            if line.startswith("{"):
                try:
                    data = json.loads(line)
                except ValueError:
                    continue
                if data.get("type") == "result":
                    return parse_ookla_json(data)
        return {"error": "speedtest (ookla) failed: " + (out.strip().splitlines() or ["no output"])[-1][:80]}
    try:
        import speedtest  # type: ignore
    except ImportError:
        return {"error": "no speedtest tool (install the Ookla CLI or `pip install speedtest-cli`)"}
    try:
        st = speedtest.Speedtest(secure=True)
        if server_id:
            st.get_servers([server_id])
        st.get_best_server()
        down = st.download() / 1e6
        up = st.upload(pre_allocate=False) / 1e6
        res = st.results
        out = {"download_mbps": round(down, 2), "upload_mbps": round(up, 2)}
        if res.ping is not None and float(res.ping) < SPEEDTEST_PING_FAILED_MS:
            out["speedtest_ping_ms"] = round(float(res.ping), 3)
        elif res.ping is not None:
            out["error"] = "speedtest-cli: server latency probe failed"
        server = res.server or {}
        if server.get("id"):
            out["speedtest_server_id"] = int(server["id"])
        name = ", ".join(str(server[k]) for k in ("sponsor", "name") if server.get(k))
        if name:
            out["speedtest_server"] = name
        return out
    except Exception as exc:
        return {"error": f"speedtest-cli failed: {exc}"[:120]}


def run_iperf3(server: str, port: int, duration_s: int, reverse: bool) -> dict:
    if not shutil.which("iperf3"):
        return {"error": "iperf3 not found"}
    cmd = ["iperf3", "-c", server, "-p", str(port), "-t", str(duration_s), "-J"]
    if reverse:
        cmd.append("-R")
    code, out = run_cmd(cmd, timeout=duration_s + 30, encoding="utf-8")
    try:
        data = json.loads(out)
    except ValueError:
        return {"error": "iperf3 failed: " + (out.strip().splitlines() or ["no output"])[-1][:80]}
    if data.get("error"):
        return {"error": f"iperf3 failed: {data['error']}"[:120]}
    try:
        end = data["end"]
        received = end.get("sum_received") or end.get("sum") or {}
        result = {"lan_throughput_mbps": round(received["bits_per_second"] / 1e6, 2)}
        sent = end.get("sum_sent") or {}
        if sent.get("retransmits") is not None:
            result["lan_retransmits"] = int(sent["retransmits"])
        return result
    except (KeyError, TypeError):
        return {"error": "iperf3 failed: unexpected JSON"}


# --------------------------------------------------------------------------- #
# Keep the machine awake while collecting
# --------------------------------------------------------------------------- #

class KeepAwake:
    """System sleep inhibitor (display may still turn off)."""

    ES_CONTINUOUS = 0x80000000
    ES_SYSTEM_REQUIRED = 0x00000001

    def __init__(self) -> None:
        self.proc: Optional[subprocess.Popen] = None
        self.active = False

    def __enter__(self) -> "KeepAwake":
        try:
            if IS_WINDOWS:
                import ctypes
                ok = ctypes.windll.kernel32.SetThreadExecutionState(  # type: ignore[attr-defined]
                    self.ES_CONTINUOUS | self.ES_SYSTEM_REQUIRED)
                self.active = bool(ok)
            elif IS_MACOS and shutil.which("caffeinate"):
                self.proc = subprocess.Popen(["caffeinate", "-i", "-w", str(os.getpid())])
                self.active = True
            elif IS_LINUX and shutil.which("systemd-inhibit"):
                self.proc = subprocess.Popen(
                    ["systemd-inhibit", "--what=sleep:idle", "--who=LocalNet-Dynamics",
                     "--why=telemetry collection in progress", "--mode=block",
                     "sleep", "infinity"],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                self.active = True
        except Exception:
            self.active = False
        return self

    def __exit__(self, *exc) -> None:
        try:
            if IS_WINDOWS:
                import ctypes
                ctypes.windll.kernel32.SetThreadExecutionState(self.ES_CONTINUOUS)  # type: ignore[attr-defined]
            if self.proc:
                self.proc.terminate()
        except Exception:
            pass


# --------------------------------------------------------------------------- #
# One sample
# --------------------------------------------------------------------------- #

def now_timestamps() -> tuple[str, str, int]:
    utc = datetime.now(timezone.utc)
    local = utc.astimezone()
    return (utc.strftime("%Y-%m-%dT%H:%M:%SZ"),
            local.isoformat(timespec="seconds"),
            int(utc.timestamp()))


def collect_sample(cfg: argparse.Namespace, net: NetworkInfo, dns_server: Optional[str],
                   pool: ThreadPoolExecutor, run_speed: bool, run_lan: bool) -> Sample:
    s = Sample(run_id=cfg.run_id, host_label=cfg.host_label, target=cfg.target,
               link_type=net.link_type, gateway_ip=net.gateway or "",
               dns_server=dns_server or "")
    s.timestamp_utc, s.timestamp_local, s.epoch_s = now_timestamps()
    t0 = time.perf_counter()
    notes: list[str] = []

    futures = {
        "ping": pool.submit(ping, cfg.target, cfg.ping_count, cfg.ping_timeout),
    }
    if net.gateway:
        futures["gw"] = pool.submit(ping, net.gateway, cfg.gateway_ping_count, cfg.ping_timeout)
    if cfg.tcp_port:
        futures["tcp"] = pool.submit(tcp_connect_times, cfg.target, cfg.tcp_port,
                                     cfg.tcp_count, cfg.ping_timeout)
    if dns_server:
        futures["dns"] = pool.submit(dns_query, dns_server, cfg.dns_host, cfg.dns_timeout)
        futures["dnsr"] = pool.submit(dns_query, dns_server,
                                      f"{random_label()}.{cfg.dns_host}", cfg.dns_timeout)
    if cfg.http_url:
        futures["http"] = pool.submit(http_probe, cfg.http_url, cfg.http_timeout)
    if cfg.wifi and net.link_type in ("wifi", "unknown"):
        futures["wifi"] = pool.submit(wifi_info, net)

    p: PingResult = futures["ping"].result()
    st = rtt_stats(p.rtts or [])
    s.rtt_min_ms, s.rtt_avg_ms, s.rtt_median_ms = st["min"], st["avg"], st["median"]
    s.rtt_max_ms, s.rtt_stddev_ms, s.jitter_ms = st["max"], st["stddev"], st["jitter"]
    s.packets_sent, s.packets_received = p.sent, p.received
    s.packet_loss_pct = p.loss_pct
    if p.error:
        notes.append(f"ping: {p.error}")

    if "gw" in futures:
        g: PingResult = futures["gw"].result()
        gst = rtt_stats(g.rtts or [])
        s.gateway_rtt_min_ms, s.gateway_rtt_avg_ms = gst["min"], gst["avg"]
        s.gateway_rtt_max_ms, s.gateway_jitter_ms = gst["max"], gst["jitter"]
        s.gateway_packet_loss_pct = g.loss_pct
        if g.error:
            notes.append(f"gateway ping: {g.error}")

    if "tcp" in futures:
        times, err = futures["tcp"].result()
        if times:
            s.tcp_connect_min_ms = round(min(times), 3)
            s.tcp_connect_median_ms = round(statistics.median(times), 3)
        if err:
            notes.append(f"tcp: {err}")

    if "dns" in futures:
        ms, rcode, err = futures["dns"].result()
        s.dns_resolve_ms = ms
        if err:
            notes.append(f"dns: {err}")
        ms, rcode, err = futures["dnsr"].result()
        s.dns_recursive_ms = ms
        if err:
            notes.append(f"dns recursive: {err}")

    if "http" in futures:
        ms, status, err = futures["http"].result()
        s.http_ms, s.http_status = ms, status
        if err:
            notes.append(f"http: {err}")

    if "wifi" in futures:
        w: WifiInfo = futures["wifi"].result()
        if w.connected:
            s.wifi_ssid = w.ssid if (cfg.record_ssid and w.ssid) else ""
            s.wifi_signal_pct, s.wifi_rssi_dbm = w.signal_pct, w.rssi_dbm
            s.wifi_rx_mbps, s.wifi_tx_mbps = w.rx_mbps, w.tx_mbps
            s.wifi_channel, s.wifi_band_ghz = w.channel, w.band_ghz
            s.wifi_phy = w.phy or ""

    # Heavy tests run after the latency probes so they cannot bias them.
    if run_speed:
        r = run_speedtest(cfg.speedtest_server)
        for key, value in r.items():
            if key != "error":
                setattr(s, key, value)
        if r.get("error"):
            notes.append(r["error"])
    if run_lan:
        r = run_iperf3(cfg.iperf_server, cfg.iperf_port, cfg.iperf_duration, cfg.iperf_reverse)
        for key, value in r.items():
            if key != "error":
                setattr(s, key, value)
        if r.get("error"):
            notes.append(r["error"])

    s.sample_duration_s = round(time.perf_counter() - t0, 2)
    s.notes = "; ".join(notes)
    return s


# --------------------------------------------------------------------------- #
# Summary statistics and run metadata
# --------------------------------------------------------------------------- #

def column_stats(values: list) -> Optional[dict]:
    vals = [float(v) for v in values if v is not None and v != ""]
    if not vals:
        return None
    vals_sorted = sorted(vals)
    p95 = vals_sorted[min(len(vals_sorted) - 1, int(0.95 * len(vals_sorted)))]
    return {
        "count": len(vals),
        "mean": round(statistics.mean(vals), 3),
        "median": round(statistics.median(vals), 3),
        "min": round(vals_sorted[0], 3),
        "max": round(vals_sorted[-1], 3),
        "p95": round(p95, 3),
        "stddev": round(statistics.pstdev(vals), 3) if len(vals) > 1 else 0.0,
    }


class RunningSummary:
    """Keeps per-column value lists so the JSON summary can be refreshed cheaply."""

    def __init__(self) -> None:
        self.values: dict[str, list] = {k: [] for k in SUMMARY_KEYS}
        self.count = 0
        self.first_utc = ""
        self.last_utc = ""
        self.link_types: dict[str, int] = {}

    def add(self, s: Sample) -> None:
        self.count += 1
        self.first_utc = self.first_utc or s.timestamp_utc
        self.last_utc = s.timestamp_utc
        self.link_types[s.link_type] = self.link_types.get(s.link_type, 0) + 1
        for k in SUMMARY_KEYS:
            v = getattr(s, k)
            if v is not None:
                self.values[k].append(v)

    def summary(self) -> dict:
        out = {k: column_stats(v) for k, v in self.values.items()}
        return {k: v for k, v in out.items() if v is not None}


def timezone_name() -> str:
    local = datetime.now(timezone.utc).astimezone()
    return local.tzname() or ""


def write_meta(path: Path, cfg: argparse.Namespace, net: NetworkInfo, dns_server: Optional[str],
               summary: RunningSummary, status: str, started_utc: str,
               elapsed_s: float, csv_name: str) -> None:
    local = datetime.now(timezone.utc).astimezone()
    payload = {
        "run_id": cfg.run_id,
        "host_label": cfg.host_label,
        "collector_version": COLLECTOR_VERSION,
        "status": status,
        "started_utc": started_utc,
        "updated_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "planned_duration_s": cfg.duration,
        "elapsed_s": round(elapsed_s, 1),
        "interval_s": cfg.interval,
        "sample_count": summary.count,
        "first_sample_utc": summary.first_utc,
        "last_sample_utc": summary.last_utc,
        "csv_file": csv_name,
        "config": {
            "target": cfg.target,
            "ping_count": cfg.ping_count,
            "gateway_ping_count": cfg.gateway_ping_count,
            "ping_timeout_s": cfg.ping_timeout,
            "tcp_port": cfg.tcp_port,
            "tcp_count": cfg.tcp_count,
            "dns_host": cfg.dns_host,
            "dns_server": dns_server,
            "dns_timeout_s": cfg.dns_timeout,
            "http_url": cfg.http_url,
            "http_timeout_s": cfg.http_timeout,
            "wifi": cfg.wifi,
            "record_ssid": cfg.record_ssid,
            "speedtest": cfg.speedtest,
            "speedtest_every_s": cfg.speedtest_every if cfg.speedtest else None,
            "speedtest_server_id": cfg.speedtest_server,
            "iperf_server": cfg.iperf_server,
            "iperf_port": cfg.iperf_port,
            "iperf_every_s": cfg.iperf_every if cfg.iperf_server else None,
            "iperf_duration_s": cfg.iperf_duration,
            "iperf_reverse": cfg.iperf_reverse,
        },
        "environment": {
            "os": platform.platform(),
            "python": platform.python_version(),
            "machine": platform.machine(),
            "timezone": timezone_name(),
            "utc_offset_s": int(local.utcoffset().total_seconds()) if local.utcoffset() else 0,
            "ping_resolution_ms": 1.0 if IS_WINDOWS else 0.001,
        },
        "network": {
            "gateway_ip": net.gateway,
            "interface": net.interface,
            "local_ip": net.local_ip,
            "link_type": net.link_type,
            "link_types_seen": summary.link_types,
            "detection_source": net.source,
        },
        "summary": summary.summary(),
    }
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def print_summary(summary: RunningSummary, elapsed: float) -> None:
    stats = summary.summary()
    print()
    print("=" * 72)
    print(f" SUMMARY: {summary.count} samples over {human_duration(elapsed)}")
    print("=" * 72)
    header = f"{'metric':<28}{'n':>7}{'median':>10}{'mean':>10}{'p95':>10}{'max':>10}"
    print(header)
    print("-" * len(header))
    for key in SUMMARY_KEYS:
        st = stats.get(key)
        if not st:
            continue
        print(f"{key:<28}{st['count']:>7}{st['median']:>10}{st['mean']:>10}"
              f"{st['p95']:>10}{st['max']:>10}")
    print("=" * 72)


def format_progress(s: Sample) -> str:
    def f(v, suffix="", nd=1):
        return "-" if v is None else f"{v:.{nd}f}{suffix}"
    parts = [
        f"[{s.sample_index + 1:>5}] {s.timestamp_utc}",
        f"rtt={f(s.rtt_avg_ms, 'ms')}",
        f"jit={f(s.jitter_ms, 'ms')}",
        f"loss={f(s.packet_loss_pct, '%', 0)}",
        f"gw={f(s.gateway_rtt_avg_ms, 'ms')}",
        f"tcp={f(s.tcp_connect_median_ms, 'ms')}",
        f"dns={f(s.dns_resolve_ms, '', 0)}/{f(s.dns_recursive_ms, 'ms', 0)}",
        f"http={f(s.http_ms, 'ms', 0)}",
    ]
    if s.wifi_signal_pct is not None:
        parts.append(f"wifi={s.wifi_signal_pct}%/{f(s.wifi_rx_mbps, 'Mbps', 0)}")
    if s.download_mbps is not None:
        parts.append(f"down={f(s.download_mbps, '', 1)} up={f(s.upload_mbps, 'Mbps', 1)}")
    if s.lan_throughput_mbps is not None:
        parts.append(f"lan={f(s.lan_throughput_mbps, 'Mbps', 0)}")
    if s.notes:
        parts.append(f"! {s.notes}")
    return "  ".join(parts)


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="LocalNet-Dynamics: continuous network telemetry collector.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    default_out = Path(__file__).resolve().parent.parent / "dataset" / "raw"

    g = p.add_argument_group("schedule")
    g.add_argument("--duration", type=parse_duration, default="24h",
                   help="total run time (e.g. 6h, 7d, 30d)")
    g.add_argument("--interval", type=parse_duration, default="30s",
                   help="time between sample starts (e.g. 30s, 1m, 5m)")

    g = p.add_argument_group("latency probes")
    g.add_argument("--target", default="8.8.8.8",
                   help="reference host for ICMP and TCP probes")
    g.add_argument("--ping-count", type=int, default=10,
                   help="ICMP echo requests per sample (sent 1 s apart)")
    g.add_argument("--gateway-ping-count", type=int, default=5,
                   help="ICMP echo requests to the default gateway per sample")
    g.add_argument("--ping-timeout", type=float, default=2.0,
                   help="seconds to wait for each ICMP reply / TCP connect")
    g.add_argument("--tcp-port", type=int, default=443,
                   help="TCP port on the target for connect-time probes (0 disables)")
    g.add_argument("--tcp-count", type=int, default=5,
                   help="TCP connections per sample")
    g.add_argument("--dns-host", default="example.com",
                   help="name queried for DNS timing; the recursive probe queries a "
                        "random subdomain of it")
    g.add_argument("--dns-server", default=None,
                   help="resolver IP to query (default: the system resolver of the "
                        "default-route interface, then the gateway)")
    g.add_argument("--dns-timeout", type=float, default=3.0, help="DNS query timeout (s)")
    g.add_argument("--http-url", default="http://www.msftconnecttest.com/connecttest.txt",
                   help="small URL for the HTTP time-to-first-response probe "
                        "('' disables)")
    g.add_argument("--http-timeout", type=float, default=5.0, help="HTTP probe timeout (s)")

    g = p.add_argument_group("wireless")
    g.add_argument("--no-wifi", dest="wifi", action="store_false",
                   help="do not query Wi-Fi link metrics")
    g.add_argument("--record-ssid", action="store_true",
                   help="store the Wi-Fi network name in the dataset (off by default "
                        "because it can identify the collection site)")

    g = p.add_argument_group("throughput tests")
    g.add_argument("--speedtest", action="store_true",
                   help="run a WAN speed test periodically (Ookla CLI or speedtest-cli)")
    g.add_argument("--speedtest-every", type=parse_duration, default="30m",
                   help="interval between WAN speed tests")
    g.add_argument("--speedtest-server", type=int, default=None,
                   help="Ookla server id to pin (recommended for consistency)")
    g.add_argument("--iperf-server", default=None,
                   help="address of an `iperf3 -s` host on the LAN")
    g.add_argument("--iperf-port", type=int, default=5201, help="iperf3 server port")
    g.add_argument("--iperf-every", type=parse_duration, default="30m",
                   help="interval between LAN throughput tests")
    g.add_argument("--iperf-duration", type=int, default=10, help="seconds per iperf3 test")
    g.add_argument("--iperf-reverse", action="store_true",
                   help="measure server-to-client (download) direction instead")

    g = p.add_argument_group("output")
    g.add_argument("--output-dir", default=str(default_out),
                   help="directory for the run's CSV and JSON files")
    g.add_argument("--run-id", default=None,
                   help="run identifier and file stem (default: <label_>YYYYMMDD_HHMMSS)")
    g.add_argument("--host-label", default="",
                   help="free-text label for this vantage point, e.g. desktop-eth or "
                        "laptop-wifi (stored in every row)")
    g.add_argument("--no-keep-awake", dest="keep_awake", action="store_false",
                   help="do not inhibit system sleep while collecting")
    g.add_argument("--quiet", action="store_true", help="print only the summary")
    g.add_argument("--check", action="store_true",
                   help="detect the environment, take one sample, print it as JSON "
                        "and exit without writing files")
    return p


def resolve_dns_server(cfg: argparse.Namespace, net: NetworkInfo) -> Optional[str]:
    if cfg.dns_server:
        return cfg.dns_server
    if net.dns_servers:
        return net.dns_servers[0]
    return net.gateway


def sanitize_label(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9_-]+", "-", text.strip()).strip("-")


def install_sigterm_handler() -> None:
    def handler(signum, frame):
        raise KeyboardInterrupt
    try:
        signal.signal(signal.SIGTERM, handler)
    except (ValueError, OSError, AttributeError):
        pass


def main(argv: Optional[list[str]] = None) -> int:
    for stream in (sys.stdout, sys.stderr):      # never crash on an unprintable path
        try:
            stream.reconfigure(errors="replace")
        except (AttributeError, ValueError):
            pass
    cfg = build_parser().parse_args(argv)
    cfg.duration = float(cfg.duration)
    cfg.interval = float(cfg.interval)
    cfg.speedtest_every = float(cfg.speedtest_every)
    cfg.iperf_every = float(cfg.iperf_every)
    cfg.host_label = sanitize_label(cfg.host_label)
    if cfg.interval <= 0:
        print("error: --interval must be positive", file=sys.stderr)
        return 2
    if cfg.ping_count < 1 or cfg.gateway_ping_count < 1:
        print("error: ping counts must be at least 1", file=sys.stderr)
        return 2
    stamp = f"{datetime.now():%Y%m%d_%H%M%S}"
    cfg.run_id = sanitize_label(cfg.run_id) if cfg.run_id else \
        (f"{cfg.host_label}_{stamp}" if cfg.host_label else stamp)

    net = detect_network(cfg.target)
    dns_server = resolve_dns_server(cfg, net)
    pool = ThreadPoolExecutor(max_workers=8)

    if cfg.check:
        print(json.dumps({
            "collector_version": COLLECTOR_VERSION,
            "os": platform.platform(),
            "python": platform.python_version(),
            "ping_utility": shutil.which("ping"),
            "ookla_cli": _ookla_cli_path(),
            "iperf3": shutil.which("iperf3"),
            "network": asdict(net),
            "dns_server": dns_server,
        }, indent=2))
        s = collect_sample(cfg, net, dns_server, pool, run_speed=cfg.speedtest,
                           run_lan=bool(cfg.iperf_server))
        print(json.dumps(asdict(s), indent=2))
        pool.shutdown(wait=False)
        return 0

    out_dir = Path(cfg.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    csv_path = out_dir / f"localnet_{cfg.run_id}.csv"
    json_path = out_dir / f"localnet_{cfg.run_id}.json"
    if csv_path.exists():
        print(f"error: {csv_path} already exists; choose another --run-id", file=sys.stderr)
        return 2

    light_budget = max(cfg.ping_count, cfg.gateway_ping_count) * max(1.0, cfg.ping_timeout) + 2.0
    print(f"LocalNet-Dynamics collector {COLLECTOR_VERSION}")
    print(f"run id      : {cfg.run_id}")
    print(f"target      : {cfg.target} (ICMP x{cfg.ping_count}, TCP :{cfg.tcp_port} x{cfg.tcp_count})")
    print(f"gateway     : {net.gateway or 'not detected'}  link: {net.link_type}"
          f"  interface: {net.interface or '?'}  local ip: {net.local_ip or '?'}")
    print(f"dns server  : {dns_server or 'none (DNS probes disabled)'}  name: {cfg.dns_host}")
    print(f"http probe  : {cfg.http_url or 'disabled'}")
    print(f"schedule    : every {human_duration(cfg.interval)} for {human_duration(cfg.duration)}"
          f" (~{int(cfg.duration // cfg.interval) + 1} samples)")
    if cfg.speedtest:
        print(f"wan speed   : every {human_duration(cfg.speedtest_every)}"
              f" ({'ookla cli' if _ookla_cli_path() else 'speedtest-cli module'})")
    if cfg.iperf_server:
        print(f"lan iperf3  : {cfg.iperf_server}:{cfg.iperf_port} every "
              f"{human_duration(cfg.iperf_every)}, {cfg.iperf_duration}s per test")
    print(f"output      : {csv_path}")
    if cfg.interval < light_budget:
        print(f"warning: the worst-case probe time ({light_budget:.0f}s) exceeds the interval;"
              f" samples will be skipped during outages")
    if not net.gateway:
        print("warning: default gateway not detected; gateway metrics will be empty")
    print("Press Ctrl-C to stop; every completed sample is already on disk.\n")

    install_sigterm_handler()
    started_utc = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    summary = RunningSummary()
    status = "completed"

    csv_file = csv_path.open("w", newline="", encoding="utf-8")
    writer = csv.DictWriter(csv_file, fieldnames=CSV_FIELDS)
    writer.writeheader()
    csv_file.flush()
    write_meta(json_path, cfg, net, dns_server, summary, "running", started_utc, 0.0, csv_path.name)

    start_mono = time.monotonic()
    next_speed = start_mono if cfg.speedtest else None
    next_lan = start_mono if cfg.iperf_server else None
    next_route = start_mono + ROUTE_REFRESH_S
    next_meta = start_mono + META_REFRESH_S
    tick = 0
    index = 0

    try:
        with (KeepAwake() if cfg.keep_awake else contextlib.nullcontext()) as awake:
            if cfg.keep_awake and not (awake and awake.active):
                print("note: could not inhibit system sleep; disable sleep in the OS power settings")
            while True:
                due = start_mono + tick * cfg.interval
                if due - start_mono >= cfg.duration:
                    break
                now = time.monotonic()
                if due > now:
                    time.sleep(due - now)
                elapsed = time.monotonic() - start_mono

                if time.monotonic() >= next_route:
                    fresh = detect_network(cfg.target)
                    if fresh.gateway:
                        net = fresh
                        dns_server = resolve_dns_server(cfg, net)
                    next_route = time.monotonic() + ROUTE_REFRESH_S

                run_speed = next_speed is not None and time.monotonic() >= next_speed
                run_lan = next_lan is not None and time.monotonic() >= next_lan
                s = collect_sample(cfg, net, dns_server, pool, run_speed, run_lan)
                s.sample_index = index
                s.elapsed_s = round(elapsed, 1)
                if run_speed:
                    next_speed += cfg.speedtest_every
                if run_lan:
                    next_lan += cfg.iperf_every
                if s.gateway_packet_loss_pct == 100.0:
                    next_route = min(next_route, time.monotonic() + 60.0)

                writer.writerow(asdict(s))
                csv_file.flush()
                summary.add(s)
                index += 1
                if not cfg.quiet:
                    print(format_progress(s), flush=True)

                if time.monotonic() >= next_meta:
                    write_meta(json_path, cfg, net, dns_server, summary, "running",
                               started_utc, time.monotonic() - start_mono, csv_path.name)
                    next_meta = time.monotonic() + META_REFRESH_S

                tick += 1
                finished = time.monotonic()
                if finished > start_mono + tick * cfg.interval:
                    skipped = int((finished - start_mono) // cfg.interval) - tick + 1
                    tick += skipped
                    if not cfg.quiet:
                        print(f"        sample took {s.sample_duration_s}s; skipping {skipped} tick(s)")
    except KeyboardInterrupt:
        status = "interrupted"
        print("\nStopping; finalizing files...")
    finally:
        csv_file.close()
        pool.shutdown(wait=False)

    elapsed = time.monotonic() - start_mono
    write_meta(json_path, cfg, net, dns_server, summary, status, started_utc, elapsed, csv_path.name)
    if summary.count:
        print_summary(summary, elapsed)
    print(f"\n{summary.count} samples written to {csv_path}\nrun metadata: {json_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
