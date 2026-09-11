# LocalNet-Dynamics

A longitudinal telemetry dataset of a real residential/edge network, and the
toolkit that produces it.

A single client machine samples its network every 30 seconds around the clock
for weeks: ICMP latency and loss to a reference host and to the local gateway,
TCP connect time, DNS response time (cached and fully recursive), HTTP
time-to-first-response, Wi-Fi link quality, and periodic WAN/LAN throughput
tests. The consolidated dataset adds time-of-day features, gap information,
rule-based reference labels for outages and degradations, and manual event
annotations, so it can be used directly for time-series forecasting, anomaly
detection, QoS classification and diurnal-pattern studies.

Author: Aizierjiang Aiersilan. License: MIT (code and data).
Dataset documentation, schema and statistics: [DATASET_CARD.md](DATASET_CARD.md).

## What is measured

Every sample is one row. All latency values are in milliseconds.

| Group | Columns | How it is measured |
|---|---|---|
| Target latency | `rtt_min_ms`, `rtt_avg_ms`, `rtt_median_ms`, `rtt_max_ms`, `rtt_stddev_ms`, `jitter_ms`, `packets_sent`, `packets_received`, `packet_loss_pct` | 10 ICMP echo requests to `--target` (default `8.8.8.8`), sent one second apart; parsed from the operating system's `ping` |
| Gateway latency | `gateway_rtt_min_ms`, `gateway_rtt_avg_ms`, `gateway_rtt_max_ms`, `gateway_jitter_ms`, `gateway_packet_loss_pct` | 5 ICMP echo requests to the default gateway, concurrently with the target probe; separates LAN/Wi-Fi problems from ISP problems |
| TCP connect | `tcp_connect_min_ms`, `tcp_connect_median_ms` | 5 TCP handshakes to `target:443`; sub-millisecond resolution on every platform and not subject to ICMP de-prioritisation |
| DNS | `dns_resolve_ms`, `dns_recursive_ms`, `dns_server` | Raw UDP queries to the system resolver: a fixed name (served from the resolver cache after the first sample) and a random, never-seen subdomain (forces a full recursive lookup). The operating-system cache is bypassed |
| HTTP | `http_ms`, `http_status` | Time to the response headers of a small connectivity-check URL, system proxy bypassed |
| Wi-Fi link | `wifi_signal_pct`, `wifi_rssi_dbm`, `wifi_rx_mbps`, `wifi_tx_mbps`, `wifi_channel`, `wifi_band_ghz`, `wifi_phy`, `wifi_ssid` (opt-in) | `netsh wlan` (Windows), `iw` (Linux), `system_profiler` (macOS); empty on wired links |
| WAN throughput | `download_mbps`, `upload_mbps`, `speedtest_ping_ms`, `download_loaded_latency_ms`, `upload_loaded_latency_ms`, `speedtest_server_id`, `speedtest_server` | Ookla Speedtest on its own cadence (default every 30 min), run after the latency probes of the sample so it cannot bias them. Loaded latency (latency during the transfer, a bufferbloat indicator) is available with the official Ookla CLI |
| LAN throughput | `lan_throughput_mbps`, `lan_retransmits` | `iperf3` against a server on the LAN, own cadence |
| Context | `run_id`, `host_label`, `sample_index`, `timestamp_utc`, `timestamp_local`, `epoch_s`, `elapsed_s`, `link_type`, `gateway_ip`, `target`, `sample_duration_s`, `notes` | Identity, time (UTC and local with offset), wired/wireless link type, probe errors |

## Repository layout

```text
LocalNet-Dynamics/
├── README.md                 this file
├── DATASET_CARD.md           dataset card: schema, label rules, protocol, limitations, statistics
├── CITATION.cff              citation metadata
├── LICENSE                   MIT
├── requirements.txt          optional Python dependencies (build, analysis, tests, speedtest fallback)
├── scripts/
│   ├── collect_data.py       collector (standard library only)
│   ├── build_dataset.py      merges raw runs, adds features, labels and events, refreshes the card
│   ├── annotate.py           records manual event annotations
│   └── analyze.py            text report and figures
├── tests/                    pytest suite (parsers for every supported platform, build pipeline)
└── dataset/
    ├── raw/                  one CSV + JSON per collection run, events.csv
    ├── localnet_dynamics.csv     consolidated dataset (built)
    ├── localnet_dynamics.parquet consolidated dataset, same content (built)
    └── summary.json              statistics and per-run label thresholds (built)
```

## Requirements

- Python 3.8 or newer. The collector uses only the standard library and the
  operating system's `ping`.
- Optional, for WAN throughput: the [Ookla Speedtest CLI](https://www.speedtest.net/apps/cli)
  (`speedtest` on the PATH, preferred; provides loaded latency) or
  `pip install speedtest-cli` (fallback).
- Optional, for LAN throughput: `iperf3` on the collector and `iperf3 -s` on
  another LAN host.
- Optional, for building and analysing: `pip install -r requirements.txt`
  (pandas, pyarrow, matplotlib, pytest).

## Quick start

Check the environment first. This detects the gateway, link type and resolver,
takes one sample and prints it as JSON without writing any files:

```bash
python scripts/collect_data.py --check
```

Start a collection run. The example below samples every 30 seconds for 30 days,
runs a WAN speed test every hour, and labels the vantage point:

```bash
python scripts/collect_data.py --duration 30d --interval 30s --speedtest --speedtest-every 1h --host-label desktop-eth
```

Every completed sample is written to `dataset/raw/localnet_<run_id>.csv`
immediately; a run can be stopped at any time with Ctrl-C and the files remain
valid. The run metadata (`dataset/raw/localnet_<run_id>.json`) is refreshed every
five minutes and finalised when the run ends.

When enough data has been collected, build the consolidated dataset and the
figures:

```bash
python scripts/build_dataset.py
python scripts/analyze.py
```

## Collection protocol

The value of the dataset comes from uninterrupted, regularly spaced samples
over many diurnal cycles. The published runs follow the protocol below, which
is also the recommended setup for new vantage points.

- Interval 30 s (2,880 samples per day, 86,400 per 30 days). The latency
  probes take about 10 s per sample; anything below 15 s is not recommended.
- Duration of at least 14 days; 30 days or more captures weekday/weekend
  effects with enough repetitions.
- The collector inhibits system sleep while running (Windows
  `SetThreadExecutionState`, macOS `caffeinate`, Linux `systemd-inhibit`). It
  cannot prevent sleep on lid close: on a laptop set the lid-close action to
  "do nothing" and keep the machine on mains power.
- Keep the machine in one place for the whole run, or record moves with
  `annotate.py` (`--category environment`). The default gateway and link type
  are re-detected every 15 minutes, so a change of network is visible in the
  `gateway_ip` and `link_type` columns.
- Record notable household events (streaming, large downloads, video calls,
  router reboots, ISP maintenance) as they happen with `annotate.py`. They
  become the `event_category` and `event_description` columns.
- Pin the speed-test server (`--speedtest-server <id>`, see
  `speedtest --servers` or `speedtest-cli --list`) so that throughput numbers
  are comparable across the run.
- For a paired wired/wireless view, run a second collector on another machine
  on the same network with a different `--host-label`; the builder keeps runs
  apart by `run_id` and computes label thresholds per run.
- Leave the network in normal use. The dataset is meant to reflect real
  household traffic, not a quiet lab.

## Command-line reference

`python scripts/collect_data.py [options]`

| Option | Default | Description |
|---|---|---|
| `--duration` | `24h` | total run time (`6h`, `7d`, `30d`) |
| `--interval` | `30s` | time between sample starts |
| `--target` | `8.8.8.8` | reference host for the ICMP and TCP probes |
| `--ping-count` | `10` | ICMP echo requests per sample, sent 1 s apart |
| `--gateway-ping-count` | `5` | ICMP echo requests to the default gateway per sample |
| `--ping-timeout` | `2.0` | seconds to wait for each ICMP reply / TCP connect |
| `--tcp-port` | `443` | TCP port on the target for connect-time probes (`0` disables) |
| `--tcp-count` | `5` | TCP connections per sample |
| `--dns-host` | `example.com` | name for the DNS probes; the recursive probe queries a random subdomain of it |
| `--dns-server` | auto | resolver IP to query (default: the resolver of the default-route interface, then the gateway) |
| `--dns-timeout` | `3.0` | DNS query timeout in seconds |
| `--http-url` | `http://www.msftconnecttest.com/connecttest.txt` | URL for the HTTP probe (`''` disables) |
| `--http-timeout` | `5.0` | HTTP probe timeout in seconds |
| `--no-wifi` | | do not query Wi-Fi link metrics |
| `--record-ssid` | off | store the Wi-Fi network name (identifies the site; off by default) |
| `--speedtest` | off | run WAN speed tests |
| `--speedtest-every` | `30m` | interval between WAN speed tests |
| `--speedtest-server` | none | Ookla server id to pin |
| `--iperf-server` | none | address of an `iperf3 -s` host on the LAN |
| `--iperf-port` | `5201` | iperf3 port |
| `--iperf-every` | `30m` | interval between LAN throughput tests |
| `--iperf-duration` | `10` | seconds per iperf3 test |
| `--iperf-reverse` | off | measure server-to-client instead of client-to-server |
| `--output-dir` | `dataset/raw` | directory for the run files |
| `--run-id` | `<label_>YYYYMMDD_HHMMSS` | run identifier and file stem |
| `--host-label` | empty | label of the vantage point, stored in every row |
| `--no-keep-awake` | | do not inhibit system sleep |
| `--quiet` | | print only the final summary |
| `--check` | | detect the environment, take one sample, print JSON, exit |

Scheduling: samples start at fixed ticks (`start + k * interval`). If a sample
overruns the interval (a speed test, or an outage in which every probe waits
for its timeout) the collector skips to the next tick rather than drifting,
and the missing ticks show up as a larger `gap_before_s` in the built dataset.
Every ICMP echo waits at most `--ping-timeout`, so the worst case for the
default settings is about 20 s per sample.

## Output files

`dataset/raw/localnet_<run_id>.csv` has one row per sample with the columns
listed in the dataset card (empty cell = not measured / failed; the `notes`
column says why). `dataset/raw/localnet_<run_id>.json` holds the run
configuration, the environment (OS, Python version, time zone, ping
resolution), the detected network (gateway, interface, link type, resolver) and
running summary statistics.

`scripts/build_dataset.py` merges all runs into `dataset/localnet_dynamics.csv`
and `.parquet`, adds:

- run context: `collector_version`, `collector_os`, `interval_s`, `rtt_resolution_ms`;
- time features from the local timestamp: `date_local`, `hour_local`,
  `weekday_local` (0 = Monday), `is_weekend`, and `gap_before_s`;
- reference labels computed with documented rules: `label_outage`,
  `label_packet_loss`, `label_high_latency`, `label_high_jitter`,
  `label_gateway_degraded`, `label_anomaly`;
- manual annotations: `event_category`, `event_description`;

writes `dataset/summary.json` (row counts, coverage, per-metric statistics,
per-run label thresholds) and refreshes the statistics section of
`DATASET_CARD.md`. The label rules are specified in the dataset card.

## Annotating events

```bash
python scripts/annotate.py "4K streaming on the living-room TV" --category load --minutes 45
python scripts/annotate.py "router rebooted" --category maintenance
python scripts/annotate.py "laptop moved to the garden" --category environment --start "2026-09-12 16:00" --end "2026-09-12 18:30"
python scripts/annotate.py --list
```

Categories: `load`, `outage`, `maintenance`, `environment`, `isp`, `device`,
`other`. Times without an offset are interpreted as local time; the file
`dataset/raw/events.csv` stores UTC.

## Analysis

```bash
python scripts/analyze.py                # text report + figures/*.png
python scripts/analyze.py --no-figures   # text report only
```

The report prints overall and hour-of-day statistics and the label counts; the
figures cover the latency timeline, the diurnal profile, a day-by-hour heat
map, metric distributions, speed-test results and Wi-Fi signal against
latency.

Loading the dataset in Python:

```python
import pandas as pd
df = pd.read_csv("dataset/localnet_dynamics.csv", parse_dates=["timestamp_utc"])
evening = df[df.hour_local.between(19, 22)]
print(evening.rtt_avg_ms.describe())
print(df.groupby("hour_local").packet_loss_pct.apply(lambda s: (s > 0).mean()))
```

## Tests

```bash
pip install -r requirements.txt
python -m pytest tests
```

The suite covers the `ping` parsers with sample output from Windows
(English, Chinese, German, French, Russian, IPv6), Linux (iputils and BusyBox)
and macOS, the route/resolver/interface parsers for the three platforms, the
Wi-Fi parsers, DNS packet construction, speed-test and iperf3 output, and the
complete build pipeline (labels, gaps, event join, card update, figures) on a
synthetic run generated inside the test.

## Platform notes

- Windows: `ping` reports whole milliseconds and prints `<1ms` for
  sub-millisecond replies, which are stored as 0. The TCP connect columns
  provide sub-millisecond latency on every platform. The gateway, interface
  type and resolver are read with PowerShell (`Get-NetRoute`,
  `Get-NetAdapter`, `Get-DnsClientServerAddress`), which works in every
  display language; `route print` is the fallback.
- Linux: `iw` is used for Wi-Fi metrics when present, `/proc/net/wireless`
  otherwise; `resolvectl` is consulted when `/etc/resolv.conf` points to the
  local stub resolver.
- macOS: `system_profiler SPAirPortDataType` provides the Wi-Fi metrics and
  takes a few seconds per call; it runs in parallel with the latency probes.
- IPv6 targets work for the ICMP and TCP probes; gateway detection is IPv4.

## Citation

```bibtex
@dataset{localnet_dynamics_2026,
  author    = {Aiersilan, Aizierjiang},
  title     = {LocalNet-Dynamics: A Longitudinal Local Area Network Telemetry Dataset},
  year      = {2026},
  version   = {1.0.0},
  url       = {https://github.com/Ezharjan/LocalNet-Dynamics}
}
```

See [CITATION.cff](CITATION.cff) for the machine-readable form.

## License

MIT, see [LICENSE](LICENSE).
