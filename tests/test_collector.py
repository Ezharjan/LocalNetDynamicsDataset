"""Unit tests for the parsing and measurement helpers in collect_data.py.

The fixtures below reproduce the output formats of the system utilities on
the platforms and locales the collector supports. They exercise the parsers
without needing network access.
"""

import json
import struct

import pytest

import collect_data as cd


# --------------------------------------------------------------------------- #
# ping output
# --------------------------------------------------------------------------- #

WINDOWS_EN = """
Pinging 8.8.8.8 with 32 bytes of data:
Reply from 8.8.8.8: bytes=32 time=14ms TTL=117
Reply from 8.8.8.8: bytes=32 time<1ms TTL=117
Request timed out.
Reply from 8.8.8.8: bytes=32 time=20ms TTL=117

Ping statistics for 8.8.8.8:
    Packets: Sent = 4, Received = 3, Lost = 1 (25% loss),
Approximate round trip times in milli-seconds:
    Minimum = 0ms, Maximum = 20ms, Average = 11ms
"""

WINDOWS_ZH = """
正在 Ping 8.8.8.8 具有 32 字节的数据:
来自 8.8.8.8 的回复: 字节=32 时间=14ms TTL=117
来自 8.8.8.8 的回复: 字节=32 时间=15ms TTL=117
请求超时。
来自 8.8.8.8 的回复: 字节=32 时间<1ms TTL=117

8.8.8.8 的 Ping 统计信息:
    数据包: 已发送 = 4，已接收 = 3，丢失 = 1 (25% 丢失)，
往返行程的估计时间(以毫秒为单位):
    最短 = 0ms，最长 = 15ms，平均 = 9ms
"""

WINDOWS_DE = """
Ping wird ausgeführt für 8.8.8.8 mit 32 Bytes Daten:
Antwort von 8.8.8.8: Bytes=32 Zeit=14ms TTL=117
Antwort von 8.8.8.8: Bytes=32 Zeit=15ms TTL=117

Ping-Statistik für 8.8.8.8:
    Pakete: Gesendet = 2, Empfangen = 2, Verloren = 0
    (0% Verlust),
Ca. Zeitangaben in Millisek.:
    Minimum = 14ms, Maximum = 15ms, Mittelwert = 14ms
"""

WINDOWS_FR = """
Envoi d'une requête 'Ping'  8.8.8.8 avec 32 octets de données :
Réponse de 8.8.8.8 : octets=32 temps=14 ms TTL=117
Réponse de 8.8.8.8 : octets=32 temps=16 ms TTL=117

Statistiques Ping pour 8.8.8.8:
    Paquets : envoyés = 2, reçus = 2, perdus = 0 (perte 0%),
Durée approximative des boucles en millisecondes :
    Minimum = 14ms, Maximum = 16ms, Moyenne = 15ms
"""

WINDOWS_RU = """
Обмен пакетами с 8.8.8.8 по с 32 байтами данных:
Ответ от 8.8.8.8: число байт=32 время=14мс TTL=117
Ответ от 8.8.8.8: число байт=32 время=17мс TTL=117

Статистика Ping для 8.8.8.8:
    Пакетов: отправлено = 2, получено = 2, потеряно = 0
    (0% потерь)
Приблизительное время приема-передачи в мс:
    Минимальное = 14мсек, Максимальное = 17 мсек, Среднее = 15 мсек
"""

WINDOWS_IPV6 = """
Pinging 2001:4860:4860::8888 with 32 bytes of data:
Reply from 2001:4860:4860::8888: time=15ms
Reply from 2001:4860:4860::8888: time=14ms

Ping statistics for 2001:4860:4860::8888:
    Packets: Sent = 2, Received = 2, Lost = 0 (0% loss),
Approximate round trip times in milli-seconds:
    Minimum = 14ms, Maximum = 15ms, Average = 14ms
"""

WINDOWS_UNREACHABLE = """
Pinging 10.9.9.9 with 32 bytes of data:
Reply from 192.168.1.23: Destination host unreachable.
Reply from 192.168.1.23: Destination host unreachable.
PING: transmit failed. General failure.

Ping statistics for 10.9.9.9:
    Packets: Sent = 3, Received = 2, Lost = 1 (33% loss),
"""

LINUX = """
PING 8.8.8.8 (8.8.8.8) 56(84) bytes of data.
64 bytes from 8.8.8.8: icmp_seq=1 ttl=117 time=14.2 ms
64 bytes from 8.8.8.8: icmp_seq=2 ttl=117 time=13.9 ms
64 bytes from 8.8.8.8: icmp_seq=2 ttl=117 time=14.1 ms (DUP!)
From 192.168.1.1 icmp_seq=3 Destination Host Unreachable
64 bytes from 8.8.8.8: icmp_seq=4 ttl=117 time=15.0 ms

--- 8.8.8.8 ping statistics ---
4 packets transmitted, 3 received, +1 duplicates, 25% packet loss, time 3004ms
rtt min/avg/max/mdev = 13.900/14.367/15.000/0.464 ms
"""

MACOS = """
PING 8.8.8.8 (8.8.8.8): 56 data bytes
64 bytes from 8.8.8.8: icmp_seq=0 ttl=117 time=14.234 ms
64 bytes from 8.8.8.8: icmp_seq=1 ttl=117 time=13.877 ms
Request timeout for icmp_seq 2
64 bytes from 8.8.8.8: icmp_seq=3 ttl=117 time=15.001 ms

--- 8.8.8.8 ping statistics ---
4 packets transmitted, 3 packets received, 25.0% packet loss
round-trip min/avg/max/stddev = 13.877/14.371/15.001/0.478 ms
"""

BUSYBOX = """
PING 8.8.8.8 (8.8.8.8): 56 data bytes
64 bytes from 8.8.8.8: seq=0 ttl=117 time=14.210 ms
64 bytes from 8.8.8.8: seq=1 ttl=117 time=13.905 ms

--- 8.8.8.8 ping statistics ---
2 packets transmitted, 2 packets received, 0% packet loss
round-trip min/avg/max = 13.905/14.057/14.210 ms
"""


@pytest.mark.parametrize("text, sent, expected", [
    (WINDOWS_EN, 4, [14.0, 0.0, 20.0]),
    (WINDOWS_ZH, 4, [14.0, 15.0, 0.0]),
    (WINDOWS_DE, 2, [14.0, 15.0]),
    (WINDOWS_FR, 2, [14.0, 16.0]),
    (WINDOWS_RU, 2, [14.0, 17.0]),
    (WINDOWS_IPV6, 2, [15.0, 14.0]),
    (WINDOWS_UNREACHABLE, 3, []),
    (LINUX, 4, [14.2, 13.9, 15.0]),
    (MACOS, 4, [14.234, 13.877, 15.001]),
    (BUSYBOX, 2, [14.21, 13.905]),
])
def test_parse_ping_output(text, sent, expected):
    result = cd.parse_ping_output(text, sent=sent)
    assert result.rtts == expected
    assert result.received == len(expected)
    assert result.sent == sent
    assert result.loss_pct == round(100.0 * (sent - len(expected)) / sent, 1)


def test_loss_is_clamped_and_none_when_nothing_sent():
    assert cd.PingResult(sent=0, received=0).loss_pct is None
    assert cd.PingResult(sent=2, received=5).loss_pct == 0.0


def test_rtt_stats():
    st = cd.rtt_stats([10.0, 12.0, 11.0, 15.0])
    assert st["min"] == 10.0 and st["max"] == 15.0
    assert st["avg"] == 12.0 and st["median"] == 11.5
    assert st["jitter"] == round((2 + 1 + 4) / 3, 3)
    assert st["stddev"] == pytest.approx(1.871, abs=1e-3)
    assert cd.rtt_stats([]) == {k: None for k in ("min", "avg", "median", "max", "stddev", "jitter")}
    single = cd.rtt_stats([7.0])
    assert single["jitter"] is None and single["stddev"] == 0.0


def test_ping_command_flags():
    assert cd.ping_command("8.8.8.8", 10, 2.0, "windows") == \
        ["ping", "-n", "10", "-w", "2000", "-4", "8.8.8.8"]
    assert cd.ping_command("2001:db8::1", 3, 2.0, "windows")[-2] == "-6"
    assert cd.ping_command("8.8.8.8", 10, 2.0, "iputils") == \
        ["ping", "-n", "-c", "10", "-W", "2", "8.8.8.8"]
    assert cd.ping_command("8.8.8.8", 4, 0.4, "iputils")[5] == "1"
    assert cd.ping_command("8.8.8.8", 10, 2.0, "busybox") == \
        ["ping", "-c", "10", "-W", "2", "8.8.8.8"]
    assert cd.ping_command("8.8.8.8", 10, 2.5, "bsd") == \
        ["ping", "-n", "-c", "10", "-W", "2500", "8.8.8.8"]


# --------------------------------------------------------------------------- #
# routes, resolvers, interfaces
# --------------------------------------------------------------------------- #

ROUTE_PRINT = """
===========================================================================
Interface List
 12...00 11 22 33 44 55 ......Intel(R) Wi-Fi 6 AX201 160MHz
===========================================================================

IPv4 Route Table
===========================================================================
Active Routes:
Network Destination        Netmask          Gateway       Interface  Metric
          0.0.0.0          0.0.0.0      192.168.1.1     192.168.1.23     35
          0.0.0.0          0.0.0.0      10.10.0.1        10.10.0.5     50
===========================================================================
Persistent Routes:
  Network Address          Netmask  Gateway Address  Metric
          0.0.0.0          0.0.0.0      192.168.1.1  Default
===========================================================================
"""


def test_parse_route_print_picks_lowest_metric():
    assert cd.parse_route_print(ROUTE_PRINT) == ("192.168.1.1", "192.168.1.23")
    assert cd.parse_route_print("no routes here") is None


def test_parse_ip_route_get():
    text = "8.8.8.8 via 192.168.1.1 dev wlp3s0 src 192.168.1.50 uid 1000 \\    cache \n"
    assert cd.parse_ip_route_get(text) == {"via": "192.168.1.1", "dev": "wlp3s0", "src": "192.168.1.50"}
    assert cd.parse_ip_route_get("default via 10.0.0.1 dev eth0 proto dhcp metric 100") == \
        {"via": "10.0.0.1", "dev": "eth0"}


def test_parse_resolv_conf():
    text = "# generated\nsearch lan\nnameserver 127.0.0.53  # stub\nnameserver 192.168.1.1\noptions edns0\n"
    assert cd.parse_resolv_conf(text) == ["127.0.0.53", "192.168.1.1"]


def test_parse_route_get_default_macos():
    text = """   route to: default
destination: default
       mask: default
    gateway: 192.168.1.1
  interface: en0
      flags: <UP,GATEWAY,DONE,STATIC,PRCLONING,GLOBAL>
"""
    assert cd.parse_route_get_default(text) == {"gateway": "192.168.1.1", "interface": "en0"}


def test_parse_hardware_ports():
    text = """Hardware Port: Wi-Fi
Device: en0
Ethernet Address: 00:11:22:33:44:55

Hardware Port: Thunderbolt Ethernet
Device: en5
Ethernet Address: 66:77:88:99:aa:bb
"""
    assert cd.parse_hardware_ports(text) == {"en0": "Wi-Fi", "en5": "Thunderbolt Ethernet"}


def test_link_type_from_media():
    assert cd._link_type_from_media("Native 802.11") == "wifi"
    assert cd._link_type_from_media("802.3") == "ethernet"
    assert cd._link_type_from_media("Unspecified") == "unknown"
    assert cd._link_type_from_media("") == "unknown"
    assert cd._link_type_from_media("BlueTooth") == "other"


# --------------------------------------------------------------------------- #
# DNS packets
# --------------------------------------------------------------------------- #

def test_dns_query_roundtrip():
    packet = cd.build_dns_query("example.com", qid=0x1234)
    qid, flags, qd, an, ns, ar = struct.unpack(">HHHHHH", packet[:12])
    assert (qid, flags, qd, an, ns, ar) == (0x1234, 0x0100, 1, 0, 0, 0)
    assert packet[12:] == b"\x07example\x03com\x00" + struct.pack(">HH", 1, 1)

    response = struct.pack(">HHHHHH", 0x1234, 0x8183, 1, 0, 0, 0) + packet[12:]
    assert cd.parse_dns_response(response, 0x1234) == 3           # NXDOMAIN
    other = struct.pack(">HHHHHH", 0x9999, 0x8180, 1, 0, 0, 0)
    assert cd.parse_dns_response(other, 0x1234) is None           # wrong id
    assert cd.parse_dns_response(packet, 0x1234) is None          # a query, not a response
    assert cd.parse_dns_response(b"\x00", 0x1234) is None


def test_random_label_is_hex():
    label = cd.random_label()
    assert len(label) == 12 and int(label, 16) >= 0


# --------------------------------------------------------------------------- #
# Wi-Fi parsers
# --------------------------------------------------------------------------- #

NETSH_EN = """
There is 1 interface on the system:

    Name                   : Wi-Fi
    Description            : Intel(R) Wi-Fi 6 AX201 160MHz
    GUID                   : 4d1a2b3c-0000-1111-2222-333344445555
    Physical address       : 00:11:22:33:44:55
    Interface type         : Primary
    State                  : connected
    SSID                   : Home:Net
    BSSID                  : aa:bb:cc:dd:ee:ff
    Network type           : Infrastructure
    Radio type             : 802.11ax
    Authentication         : WPA2-Personal
    Cipher                 : CCMP
    Connection mode        : Auto Connect
    Band                   : 5 GHz
    Channel                : 36
    Receive rate (Mbps)    : 1200.9
    Transmit rate (Mbps)   : 1200.9
    Signal                 : 84%
    Profile                : Home:Net

    Hosted network status  : Not available
"""

NETSH_ZH = """
系统上有 1 个接口:

    名称                   : WLAN
    描述                   : Intel(R) Wi-Fi 6 AX201 160MHz
    GUID                   : 4d1a2b3c-0000-1111-2222-333344445555
    物理地址               : 00:11:22:33:44:55
    接口类型               : 主要
    状态                   : 已连接
    SSID                   : 家里的WiFi
    BSSID                  : aa:bb:cc:dd:ee:ff
    网络类型               : 结构
    无线电类型             : 802.11ac
    身份验证               : WPA2 - 个人
    密码                   : CCMP
    连接模式               : 自动连接
    信道                   : 149
    接收速率(Mbps)         : 866.7
    传输速率(Mbps)         : 866.7
    信号                   : 91%
    配置文件               : 家里的WiFi

    承载网络状态           : 不可用
"""

NETSH_DISCONNECTED = """
There is 1 interface on the system:

    Name                   : Wi-Fi
    Description            : Intel(R) Wi-Fi 6 AX201 160MHz
    GUID                   : 4d1a2b3c-0000-1111-2222-333344445555
    Physical address       : 00:11:22:33:44:55
    Interface type         : Primary
    State                  : disconnected
    Radio status           : Hardware On
                             Software On

    Hosted network status  : Not available
"""


def test_parse_netsh_english():
    w = cd.parse_netsh_wlan(NETSH_EN)
    assert w.connected and w.ssid == "Home:Net"
    assert w.signal_pct == 84 and w.rssi_dbm == -58.0
    assert w.rx_mbps == 1200.9 and w.tx_mbps == 1200.9
    assert w.channel == 36 and w.band_ghz == 5.0 and w.phy == "802.11ax"


def test_parse_netsh_chinese_without_band_line():
    w = cd.parse_netsh_wlan(NETSH_ZH)
    assert w.connected and w.ssid == "家里的WiFi"
    assert w.signal_pct == 91 and w.rssi_dbm == -54.5
    assert w.rx_mbps == 866.7 and w.tx_mbps == 866.7
    assert w.channel == 149 and w.band_ghz == 5.0 and w.phy == "802.11ac"


def test_parse_netsh_disconnected():
    w = cd.parse_netsh_wlan(NETSH_DISCONNECTED)
    assert not w.connected and w.signal_pct is None


IW_LINK = """Connected to aa:bb:cc:dd:ee:ff (on wlp3s0)
\tSSID: HomeNet
\tfreq: 5180
\tRX: 123456 bytes (789 packets)
\tTX: 23456 bytes (234 packets)
\tsignal: -55 dBm
\trx bitrate: 866.7 MBit/s VHT-MCS 9 80MHz short GI VHT-NSS 2
\ttx bitrate: 780.0 MBit/s VHT-MCS 8 80MHz short GI VHT-NSS 2

\tbss flags:\tshort-slot-time
\tdtim period:\t1
\tbeacon int:\t100
"""


def test_parse_iw_link():
    w = cd.parse_iw_link(IW_LINK)
    assert w.connected and w.ssid == "HomeNet"
    assert w.channel == 36 and w.band_ghz == 5.0
    assert w.rssi_dbm == -55.0 and w.signal_pct == 90
    assert w.rx_mbps == 866.7 and w.tx_mbps == 780.0 and w.phy == "802.11ac"
    assert not cd.parse_iw_link("Not connected.\n").connected
    ht = cd.parse_iw_link("Connected to x (on w)\n\tSSID: n\n\tfreq: 2437\n\ttx bitrate: 72.2 MBit/s MCS 7 short GI\n")
    assert ht.phy == "802.11n" and ht.channel == 6 and ht.band_ghz == 2.4


def test_parse_proc_net_wireless():
    text = ("Inter-| sta-|   Quality        |   Discarded packets               | Missed | WE\n"
            " face | tus | link level noise |  nwid  crypt   frag  retry   misc | beacon | 22\n"
            " wlan0: 0000   60.  -50.  -256        0      0      0      0      0        0\n")
    w = cd.parse_proc_net_wireless(text, "wlan0")
    assert w.connected and w.rssi_dbm == -50.0 and w.signal_pct == 100
    assert not cd.parse_proc_net_wireless(text, "wlan1").connected


def test_parse_system_profiler():
    data = {"SPAirPortDataType": [{"spairport_airport_interfaces": [{
        "_name": "en0",
        "spairport_current_network_information": {
            "_name": "HomeNet",
            "spairport_network_channel": "44 (5GHz, 80MHz)",
            "spairport_network_phymode": "802.11ax",
            "spairport_network_rate": 1200,
            "spairport_signal_noise": "-52 dBm / -92 dBm",
        }}]}]}
    w = cd.parse_system_profiler_airport(data)
    assert w.connected and w.ssid == "HomeNet" and w.channel == 44 and w.band_ghz == 5.0
    assert w.phy == "802.11ax" and w.rx_mbps == 1200 and w.rssi_dbm == -52.0
    assert not cd.parse_system_profiler_airport({}).connected


def test_channel_and_rssi_helpers():
    assert cd.channel_from_freq_mhz(2412) == (1, 2.4)
    assert cd.channel_from_freq_mhz(2484) == (14, 2.4)
    assert cd.channel_from_freq_mhz(5745) == (149, 5.0)
    assert cd.channel_from_freq_mhz(5955) == (1, 6.0)
    assert cd.channel_from_freq_mhz(1000) == (None, None)
    assert cd.rssi_from_quality(100) == -50.0 and cd.rssi_from_quality(0) == -100.0
    assert cd.quality_from_rssi(-75) == 50 and cd.quality_from_rssi(-120) == 0
    assert cd.band_from_channel(11) == 2.4 and cd.band_from_channel(100) == 5.0


# --------------------------------------------------------------------------- #
# throughput tool output
# --------------------------------------------------------------------------- #

def test_parse_ookla_json():
    data = {"type": "result",
            "ping": {"jitter": 0.6, "latency": 9.5},
            "download": {"bandwidth": 58231421, "latency": {"iqm": 21.3}},
            "upload": {"bandwidth": 2500000, "latency": {"iqm": 45.0}},
            "server": {"id": 1234, "name": "Example ISP", "location": "Springfield"}}
    r = cd.parse_ookla_json(data)
    assert r["download_mbps"] == pytest.approx(465.85, abs=0.01)
    assert r["upload_mbps"] == 20.0
    assert r["speedtest_ping_ms"] == 9.5
    assert r["download_loaded_latency_ms"] == 21.3 and r["upload_loaded_latency_ms"] == 45.0
    assert r["speedtest_server_id"] == 1234 and r["speedtest_server"] == "Example ISP, Springfield"


class _FakeResults:
    ping = 1800000.0
    server = {"id": 42, "sponsor": "Example ISP", "name": "Springfield"}


class _FakeSpeedtest:
    def __init__(self, secure=True):
        self.results = _FakeResults()

    def get_servers(self, ids):
        pass

    def get_best_server(self):
        pass

    def download(self):
        return 120e6

    def upload(self, pre_allocate=False):
        return 20e6


def test_speedtest_cli_failed_latency_probe(monkeypatch):
    import sys
    import types
    module = types.ModuleType("speedtest")
    module.Speedtest = _FakeSpeedtest
    monkeypatch.setitem(sys.modules, "speedtest", module)
    monkeypatch.setattr(cd, "_ookla_cli_path", lambda: None)
    out = cd.run_speedtest()
    assert out["download_mbps"] == 120.0 and out["upload_mbps"] == 20.0
    assert "speedtest_ping_ms" not in out
    assert out["error"] == "speedtest-cli: server latency probe failed"
    assert out["speedtest_server"] == "Example ISP, Springfield"
    _FakeResults.ping = 23.4
    out = cd.run_speedtest()
    assert out["speedtest_ping_ms"] == 23.4 and "error" not in out


def test_iperf_json_parsing(monkeypatch):
    payload = {"end": {"sum_sent": {"bits_per_second": 9.4e8, "retransmits": 12},
                       "sum_received": {"bits_per_second": 9.38e8}}}
    monkeypatch.setattr(cd.shutil, "which", lambda name: "/usr/bin/iperf3")
    monkeypatch.setattr(cd, "run_cmd", lambda *a, **k: (0, json.dumps(payload)))
    assert cd.run_iperf3("10.0.0.2", 5201, 10, False) == {"lan_throughput_mbps": 938.0, "lan_retransmits": 12}
    monkeypatch.setattr(cd, "run_cmd", lambda *a, **k: (1, json.dumps({"error": "unable to connect"})))
    assert "unable to connect" in cd.run_iperf3("10.0.0.2", 5201, 10, False)["error"]


# --------------------------------------------------------------------------- #
# configuration helpers
# --------------------------------------------------------------------------- #

def test_parse_duration():
    assert cd.parse_duration("90") == 90
    assert cd.parse_duration("30s") == 30
    assert cd.parse_duration("1.5h") == 5400
    assert cd.parse_duration("7d") == 7 * 86400
    with pytest.raises(Exception):
        cd.parse_duration("1w")


def test_human_duration():
    assert cd.human_duration(0) == "0s"
    assert cd.human_duration(90) == "1m 30s"
    assert cd.human_duration(7 * 86400 + 3600) == "7d 1h"


def test_sanitize_label():
    assert cd.sanitize_label(" laptop wifi/5G ") == "laptop-wifi-5G"
    assert cd.sanitize_label("") == ""


def test_csv_fields_match_dataclass():
    assert cd.CSV_FIELDS[0] == "run_id" and cd.CSV_FIELDS[-1] == "notes"
    assert len(cd.CSV_FIELDS) == len(set(cd.CSV_FIELDS))
    for key in cd.SUMMARY_KEYS:
        assert key in cd.CSV_FIELDS
