---
pretty_name: LocalNet-Dynamics
license: mit
language:
  - en
tags:
  - tabular
  - timeseries
  - networking
  - telemetry
  - latency
  - jitter
  - packet-loss
  - dns
  - wifi
  - throughput
  - qos
  - anomaly-detection
task_categories:
  - time-series-forecasting
  - tabular-classification
  - tabular-regression
annotations_creators:
  - machine-generated
source_datasets:
  - original
size_categories:
  - 10K<n<100K
configs:
  - config_name: default
    data_files:
      - split: train
        path: dataset/localnet_dynamics.parquet
---

# LocalNet-Dynamics

A longitudinal, high-resolution telemetry dataset of a residential local area
network, sampled every 30 seconds around the clock from a wired desktop for
19 days (2026-09-20 to 2026-10-09). Each of the 54,617 rows is one sampling
epoch and combines ICMP latency, loss and jitter to a reference host and to
the gateway, TCP connect time, cached and recursive DNS response time, HTTP
time-to-first-response and hourly WAN throughput tests with time-of-day
features and rule-based reference labels.

- Author and point of contact: Alexander Ezharjan
- Dataset: https://huggingface.co/datasets/ezharjan/LocalNetDynamics
- DOI: https://doi.org/10.57967/hf/10841
- Source code (collector, build pipeline, analysis): https://github.com/Ezharjan/LocalNetDynamicsDataset
- License: MIT (code and data)
- Version: 1.0.0

## Intended uses

- Time-series forecasting of latency, jitter and loss. The capture shows
  strong persistence (lag-1 autocorrelation of the log round-trip time 0.81,
  still 0.42 after three hours) and almost no 24-hour periodicity (0.09 at a
  one-day lag), so it rewards models that track regime changes rather than
  seasonal ones.
- Unsupervised and semi-supervised anomaly detection, evaluated against the
  reference labels and the episode tables in `dataset/analysis.json`.
- QoS classification (for example, whether a sample meets the requirements of
  real-time voice, cloud gaming or video conferencing).
- Root-cause studies: the gateway probe, the TCP probe and the
  cached-versus-recursive DNS timings make it possible to separate first-hop
  degradation from path changes and from resolver degradation. The capture
  contains worked examples of each.
- Studies of the relationship between ICMP loss and application-visible
  latency (HTTP retransmission modes, DNS tails).

## Collection site

| Item | Value |
|---|---|
| Vantage point | one desktop PC, host label `desktop-eth` |
| Link between client and router | wired Ethernet (the collector could not read the media type, see the limitations) |
| Client operating system | Windows 11 (build 26200), Python 3.12.12 |
| Gateway and resolver | the home router, a private address on the `192.168.1.0/24` LAN |
| Time zone | US Eastern, UTC-4 for the whole run |
| Reference host | `8.8.8.8` (ICMP and TCP port 443) |
| Access technology, advertised plan, router model | not recorded |
| Household usage during the run | normal use, not annotated |

## Files

| File | Content |
|---|---|
| `dataset/localnet_dynamics.parquet` | consolidated dataset, one row per sample, typed columns (the file served by the dataset viewer) |
| `dataset/localnet_dynamics.csv` | the same table as CSV |
| `dataset/summary.json` | row counts, coverage, per-metric statistics, per-run label thresholds |
| `dataset/analysis.json` | every number quoted in this card: profiles, regimes, episodes, conditional statistics, autocorrelation, speed-test quality |
| `dataset/raw/localnet_desktop-eth_20260920_123407.csv` | raw measurements of the collection run, exactly as written by the collector |
| `dataset/raw/localnet_desktop-eth_20260920_123407.json` | configuration, environment, detected network and running summary of that run |
| `figures/png/`, `figures/pdf/` | the ten figures below, as 200 dpi PNG and as vector PDF |
| `scripts/` | collector, builder, annotation tool, analysis, plotting and packaging scripts |
| `tests/` | pytest suite |

The consolidated files are produced from the raw files by
`scripts/build_dataset.py`; the raw files are never edited. No manual event
annotations were recorded during this run, so there is no `events.csv`.

## Findings

The numbers in this section are computed by `scripts/analyze.py` and stored in
`dataset/analysis.json`.

### Overview

![Overview timeline](figures/png/overview_timeline.png)

54,617 samples from 2026-09-20 12:34 to 2026-10-09 15:49 local time, 99.1 % of
the samples expected at the 30-second interval. 345 gaps are exactly 60 s,
the tick skipped after an hourly speed test; 78 gaps are longer, up to 122 s,
53 of them after a speed test that overran two ticks and the rest after
samples whose probes waited for their timeouts. The median round-trip time
to 8.8.8.8 is 11.1 ms (95th percentile 22.3 ms), the median first-hop
round-trip time 0.2 ms, the median TCP handshake 14.5 ms.

### Two regimes

A single least-squares change point in the daily share of samples with ICMP
loss falls on 2026-09-30 and divides the capture into two ten-day halves:

| | Sep 20 to Sep 29 | Sep 30 to Oct 9 |
|---|---|---|
| samples | 27,020 | 27,597 |
| samples with at least one lost echo | 23.2 % | 5.3 % |
| mean packet loss | 2.98 % | 0.57 % |
| round-trip time, median / p95 / p99 | 11.9 / 24.3 / 28.4 ms | 10.8 / 14.2 / 25.5 ms |
| jitter, median / p95 | 2.22 / 9.22 ms | 0.78 / 4.56 ms |
| HTTP response, median / p95 | 87.7 / 1,094 ms | 77.6 / 157 ms |
| HTTP responses slower than 1 s | 5.87 % | 1.09 % |
| recursive DNS, median | 62.5 ms | 56.2 ms |
| samples with any label | 38.9 % | 9.2 % |

![Regimes](figures/png/regimes.png)

![Labels](figures/png/labels.png)

### Loss: daytime, weekdays, upstream of the gateway

![Diurnal profile](figures/png/diurnal_profile.png)

![Calendar heat maps](figures/png/calendar_heatmaps.png)

14.1 % of all samples lost at least one of their ten echoes; 6,309 lost
exactly one, 1,092 two, 230 three, 41 four. On weekdays the hourly share of
samples with loss rises from 6 to 8 % between midnight and 05:00 to 25.2 % at
08:00 and stays above 20 % until 16:00; on weekends it stays between 6 % and
14 % all day. Weekdays as a whole have 15.2 % of samples with loss, weekends
10.5 %. Outside the four outages, the gateway lost a packet in exactly one
sample, so the loss sits beyond the first hop.

![Loss anatomy](figures/png/loss_anatomy.png)

Loss is only weakly bursty: a sample that follows a lossy sample is lossy with
probability 0.30 against a base rate of 0.14, and the autocorrelation of the
loss indicator is 0.18 at lag 1. The other probes show what a lost echo means:

| | no loss | 1 of 10 lost | 2 or more lost |
|---|---|---|---|
| samples | 46,907 | 6,309 | 1,383 |
| round-trip time, median | 11.0 ms | 11.8 ms | 12.6 ms |
| jitter, median | 1.00 ms | 2.12 ms | 3.29 ms |
| TCP handshake, median | 14.5 ms | 14.3 ms | 14.4 ms |
| HTTP responses slower than 1 s | 2.54 % | 7.47 % | 16.34 % |
| recursive DNS slower than 200 ms | 1.75 % | 5.22 % | 9.60 % |

The HTTP response time has a smooth body with a median of 81.9 ms and a 95th
percentile of 271 ms, then discrete modes: 2.48 % of responses fall between
1.0 and 1.4 s and 0.55 % between 3.9 and 4.4 s. Their timing matches the
retransmission timers of the operating system's DNS client and TCP stack (one
second, then multiples of it), and they occur three to six times more often in
samples that also lost an echo, so they are lost packets waiting out a timer
rather than slow servers: the mechanism by which an echo lost in a ten-second
burst becomes a one-second stall for an application.

### Latency episodes: path changes and a first-hop event

![Degradation anatomy](figures/png/degradation_anatomy.png)

Runs of the high-latency label, merged across gaps of up to ten samples, give
nine episodes of 30 minutes or more; they contain 88.4 % of all high-latency
labels. Each is classified from the probe that moved: *first hop* if the
median gateway round-trip time exceeds the run's gateway threshold (5.2 ms),
*path change* if the median minimum ICMP time or the median fastest TCP
handshake rises at least 5 ms above its run median (10.0 and 12.3 ms), and
*queueing* otherwise.

| start (local) | end (local) | hours | ICMP min | ICMP mean | TCP fastest | gateway | kind |
|---|---|---|---|---|---|---|---|
| Mon Sep 21 22:34 | Tue Sep 22 01:05 | 2.52 | 20.0 | 26.4 | 25.7 | 0.0 | path change |
| Tue Sep 22 01:27 | 02:41 | 1.23 | 10.0 | 20.4 | 20.1 | 0.0 | path change |
| Tue Sep 22 02:47 | 03:44 | 0.95 | 11.0 | 21.1 | 19.5 | 0.0 | path change |
| Thu Sep 24 23:51 | Fri Sep 25 01:16 | 1.42 | 11.0 | 22.3 | 20.1 | 0.0 | path change |
| Sat Sep 26 14:07 | 14:54 | 0.78 | 14.0 | 23.5 | 18.1 | 11.4 | first hop |
| Sat Sep 26 15:01 | 15:32 | 0.52 | 14.0 | 22.0 | 18.1 | 11.2 | first hop |
| Sat Sep 26 16:36 | 18:17 | 1.68 | 14.0 | 23.7 | 20.7 | 10.6 | first hop |
| Sun Sep 27 00:00 | 06:16 | 6.28 | 17.0 | 25.6 | 23.8 | 0.0 | path change |
| Tue Sep 29 21:21 | Wed Sep 30 07:34 | 10.22 | 20.0 | 24.8 | 24.5 | 0.0 | path change |

Values are medians over the episode, in milliseconds; an end time without a
date is on the start date. The six path changes fall on four nights and all
lie between 21:21 and 07:34. In every one of them the fastest TCP handshake
sits 7 to 13 ms above its 12.3 ms baseline, the gateway is untouched, and the
DNS probe, whose queries the gateway forwards to the upstream resolver, stays
within 4 ms of its 36 ms median, so the extra delay lies on the route towards
8.8.8.8, beyond the first hop and beyond the point where the path to the
resolver branches off. The three first-hop episodes are one event on the
afternoon of Saturday September 26: the gateway answered in 10 to 11 ms
instead of 0.2 ms, every other probe inherited the delay, and that day holds
507 of the 560 gateway-degraded labels.

### Outages

| start (local) | duration | samples | gateway reachable |
|---|---|---|---|
| Mon Sep 21 13:03 | 2.0 min | 4 | no |
| Thu Sep 24 07:10 | 2.5 min | 5 | no |
| Sat Sep 26 13:11 | 2.5 min | 5 | no |
| Tue Oct 6 02:51 | 2.0 min | 4 | no |

Every outage took the gateway down together with the target, with name
resolution failing as well (`getaddrinfo failed` in the `notes` column), so
all four were first-hop events. The ISP path never failed on its own during
the capture. Outages account for 18 samples, 0.03 % of the dataset.

### The TCP/ICMP split

![TCP versus ICMP](figures/png/tcp_vs_icmp.png)

Over the whole capture the fastest TCP handshake exceeds the minimum ICMP time
by 2.27 ms (median). From 22:00 on October 6 the share of samples in which it
exceeds it by more than 5 ms jumps from 7.2 % to 63.7 %, and the fastest
handshake becomes bimodal (first quartile 12.9 ms, median 18.6 ms, third
quartile 22.2 ms) while the ICMP minimum stays at 10 ms. The ICMP and TCP
probes address the same host, so the difference is in how the two kinds of
flow are routed; a monitor that relies on ping alone would not see the change.

### DNS

The "cached" probe asks the gateway resolver for `example.com`, which it has
answered before, yet its median is 36.0 ms (5th percentile 17 ms; only 3.4 %
of answers arrive within 15 ms). The gateway therefore forwards every query
upstream rather than serving it from a local cache, and the column measures
the upstream resolver's responsiveness. The recursive probe (a random name
under `example.com`) has a median of 59.2 ms, 20.2 ms above the cached probe
in the same sample; 2.35 % of recursive lookups take longer than 200 ms and
the 99th percentile is 1,266 ms. 24 samples recorded a DNS timeout, 14 of
them during the four outages (the other four outage samples failed with a
socket error instead).

### Memory

![Autocorrelation](figures/png/autocorrelation.png)

| lag | 30 s | 5 min | 30 min | 3 h | 12 h | 24 h |
|---|---|---|---|---|---|---|
| log round-trip time | 0.81 | 0.76 | 0.68 | 0.42 | 0.18 | 0.09 |
| jitter | 0.44 | 0.42 | 0.36 | 0.25 | 0.23 | 0.17 |
| lost-echo indicator | 0.18 | 0.17 | 0.16 | 0.14 | 0.04 | 0.10 |
| fastest TCP handshake | 0.28 | 0.15 | 0.14 | 0.09 | 0.04 | 0.02 |
| recursive DNS | 0.04 | 0.02 | 0.02 | 0.02 | 0.00 | 0.01 |
| HTTP | 0.06 | 0.05 | 0.03 | 0.04 | 0.02 | 0.01 |

Latency is persistent for hours and has no daily cycle at this site; DNS and
HTTP timings are memoryless at every lag.

### WAN throughput

![Speed tests](figures/png/speedtest.png)

460 hourly speed tests ran through the `speedtest-cli` Python fallback with no
pinned server and used 162 different servers. 79 tests could not measure the
server's latency (the fallback encodes that failure as a value of 600,000 ms
or more; the builder sets it to missing), 7 reported a download of 0 Mbit/s
and 4 an upload of 0 Mbit/s. Download: median 34.7 Mbit/s, 90th percentile
183 Mbit/s, maximum 480.7 Mbit/s; among the ten most used servers the
per-server median ranges from 20.9 to 371.8 Mbit/s. Upload: median
28.6 Mbit/s, 90th percentile 40.3 Mbit/s, maximum 54.2 Mbit/s. The median
download is 61.5 Mbit/s for the 95 tests started between 00:00 and 04:59 and
22.0 Mbit/s for the 156 started between 09:00 and 16:59. The download column is a lower bound on
the access link and depends heavily on the server; the upload column is more
consistent. Loaded latency was not measured because the official Ookla CLI was
not installed.

## Measurement protocol

One sample is taken at every tick of a fixed schedule (`start + k * interval`).
Within a sample the following probes run concurrently:

- ICMP: `ping_count` (10) echo requests to the target and `gateway_ping_count`
  (5) to the default gateway, one second apart, each waiting at most
  `ping_timeout` (2 s) for its reply. Per-reply round-trip times are parsed
  from the output of the operating system's `ping`.
- TCP: `tcp_count` (5) TCP handshakes to `target:443`, 0.2 s apart, timed with
  a high-resolution clock from `connect()` start to completion.
- DNS: two UDP queries (type A, recursion desired) sent directly to the
  configured resolver, bypassing the operating-system cache. The first asks for
  `dns_host` (`example.com`), which the resolver serves from its cache after
  the first sample and therefore measures the resolver's responsiveness. The
  second asks for a random 12-hexadecimal-character subdomain of `dns_host`,
  which can never be cached and therefore measures a full recursive
  resolution. A response with rcode NOERROR or NXDOMAIN is a valid measurement.
- HTTP: one GET request for `http_url`
  (`http://www.msftconnecttest.com/connecttest.txt`) with the system proxy
  bypassed, timed until the response headers have been received.
- Wi-Fi: link metrics read from the operating system
  (`netsh wlan show interfaces`, `iw dev <if> link`, `system_profiler
  SPAirPortDataType`).

After the concurrent probes, and only on their own cadence (every hour in the
published run), the heavy tests run: an Ookla speed test and/or an `iperf3`
test. Because they run after the latency probes of the same sample, they do
not influence that sample's latency columns; a sample that includes a speed
test usually overruns the interval, and the collector then skips to the next
tick.

The default gateway, the interface type and the resolver are detected at start
and re-detected every 15 minutes (or within a minute after the gateway probe
loses every packet). System sleep is inhibited for the duration of the run.

Run configuration of the published capture: interval 30 s, 10 echoes to
`8.8.8.8` and 5 to the gateway with a 2 s timeout, 5 TCP handshakes to port
443, DNS probes against the gateway resolver with a 3 s timeout, HTTP probe
with a 5 s timeout, Wi-Fi metrics enabled, SSID not recorded, speed test every
hour with no pinned server, no iperf3 server, collector version 2.0.0.

## Schema

Empty cells mean "not measured or failed"; the `notes` column records the
reason (for example `ping: no replies`, `dns recursive: timeout`,
`speedtest (ookla) failed: ...`). Times are in milliseconds unless the column
name says otherwise.

### Identity and time

| Column | Type | Description |
|---|---|---|
| `run_id` | string | collection run identifier, `<host_label_>YYYYMMDD_HHMMSS` in local time of the run start |
| `host_label` | string | free-text label of the vantage point (`--host-label`), empty if not given |
| `sample_index` | int | 0-based row number within the run |
| `timestamp_utc` | string | sample start, ISO 8601 UTC, e.g. `2026-09-05T14:00:00Z` |
| `timestamp_local` | string | sample start in the collector's local time with UTC offset, e.g. `2026-09-05T10:00:00-04:00` |
| `epoch_s` | int | sample start as Unix time (seconds) |
| `elapsed_s` | float | seconds since the start of the run |
| `link_type` | string | `wifi`, `ethernet`, `other` or `unknown`: the interface that carries the default route |
| `gateway_ip` | string | default gateway (private address) |
| `dns_server` | string | resolver used for the DNS probes |
| `target` | string | host used for the ICMP and TCP probes |

### Run context (added by the builder)

| Column | Type | Description |
|---|---|---|
| `collector_version` | string | version of `collect_data.py` that wrote the run |
| `collector_os` | string | `Windows`, `Linux` or `macOS` |
| `interval_s` | float | configured sampling interval of the run |
| `rtt_resolution_ms` | float | resolution of the ICMP columns: 1.0 on Windows, 0.001 elsewhere |

### Time features (added by the builder, from `timestamp_local`)

| Column | Type | Description |
|---|---|---|
| `date_local` | string | local calendar date `YYYY-MM-DD` |
| `hour_local` | int | local hour 0-23 |
| `weekday_local` | int | 0 = Monday ... 6 = Sunday |
| `is_weekend` | int | 1 on Saturday and Sunday |
| `gap_before_s` | float | seconds since the previous sample of the same run; larger than `interval_s` after a skipped tick or a sleep/outage gap |

### Target latency (ICMP)

| Column | Type | Description |
|---|---|---|
| `rtt_min_ms` | float | minimum round-trip time of the received replies |
| `rtt_avg_ms` | float | arithmetic mean |
| `rtt_median_ms` | float | median |
| `rtt_max_ms` | float | maximum |
| `rtt_stddev_ms` | float | population standard deviation of the received replies (the `mdev` of Linux `ping`) |
| `jitter_ms` | float | mean absolute difference between consecutive received replies (packet delay variation in the sense of RFC 3393, applied to round-trip times); empty when fewer than two replies were received |
| `packets_sent` | int | echo requests sent (`ping_count`) |
| `packets_received` | int | replies received |
| `packet_loss_pct` | float | `100 * (sent - received) / sent` |

### Gateway latency (ICMP to the default gateway)

| Column | Type | Description |
|---|---|---|
| `gateway_rtt_min_ms` | float | minimum round-trip time |
| `gateway_rtt_avg_ms` | float | mean |
| `gateway_rtt_max_ms` | float | maximum |
| `gateway_jitter_ms` | float | mean absolute consecutive difference |
| `gateway_packet_loss_pct` | float | loss in percent |

### TCP, DNS and HTTP

| Column | Type | Description |
|---|---|---|
| `tcp_connect_min_ms` | float | fastest of the TCP handshakes to `target:443` |
| `tcp_connect_median_ms` | float | median handshake time |
| `dns_resolve_ms` | float | response time of the resolver for `dns_host` (cache-served after the first sample) |
| `dns_recursive_ms` | float | response time for a random subdomain of `dns_host` (full recursive lookup) |
| `http_ms` | float | time to the response headers of `http_url`, including the operating-system name lookup, the TCP handshake and the request |
| `http_status` | int | HTTP status code of that response |

### Wi-Fi link (empty on wired links)

| Column | Type | Description |
|---|---|---|
| `wifi_ssid` | string | network name; only recorded with `--record-ssid` |
| `wifi_signal_pct` | int | link quality 0-100: the value reported by Windows; on Linux and macOS derived from RSSI with the Windows mapping `quality = 2 * (rssi + 100)` clipped to 0-100 |
| `wifi_rssi_dbm` | float | received signal strength: as reported on Linux and macOS; on Windows derived from the quality with `rssi = quality / 2 - 100` |
| `wifi_rx_mbps` | float | negotiated receive PHY rate (Mbit/s) |
| `wifi_tx_mbps` | float | negotiated transmit PHY rate (Mbit/s); equals the receive rate on macOS, which reports one rate |
| `wifi_channel` | int | channel number |
| `wifi_band_ghz` | float | 2.4, 5 or 6 |
| `wifi_phy` | string | standard in use, e.g. `802.11ax` |

### WAN throughput (rows in which a speed test ran)

| Column | Type | Description |
|---|---|---|
| `download_mbps` | float | download throughput (Mbit/s) |
| `upload_mbps` | float | upload throughput (Mbit/s) |
| `speedtest_ping_ms` | float | idle latency to the speed-test server as reported by the tool; missing when the `speedtest-cli` fallback could not measure it |
| `download_loaded_latency_ms` | float | interquartile-mean latency measured during the download (Ookla CLI only) |
| `upload_loaded_latency_ms` | float | interquartile-mean latency measured during the upload (Ookla CLI only) |
| `speedtest_server_id` | int | Ookla server id |
| `speedtest_server` | string | server name and location |

### LAN throughput (rows in which an iperf3 test ran)

| Column | Type | Description |
|---|---|---|
| `lan_throughput_mbps` | float | TCP throughput to the LAN iperf3 server (Mbit/s) |
| `lan_retransmits` | int | TCP retransmissions during the test, when reported |

### Reference labels (added by the builder)

| Column | Type | Rule |
|---|---|---|
| `label_outage` | int | `packets_received == 0` while `packets_sent > 0` |
| `label_packet_loss` | int | `0 < packet_loss_pct < 100` |
| `label_high_latency` | int | `rtt_avg_ms > T_rtt` |
| `label_high_jitter` | int | `jitter_ms > T_jitter` |
| `label_gateway_degraded` | int | `gateway_rtt_avg_ms > T_gateway` or `gateway_packet_loss_pct > 0` |
| `label_anomaly` | int | any of the above |

The thresholds are computed per run from that run's own distribution with a
robust rule, so they adapt to the baseline of the site without being pulled by
the outliers they are meant to find:

```text
T = median + max(k * 1.4826 * MAD, absolute floor, relative floor * median)
```

with `k = 5`, the median absolute deviation `MAD`, and the floors
10 ms / 50 % for `rtt_avg_ms`, 5 ms / 100 % for `jitter_ms` and
5 ms / 100 % for `gateway_rtt_avg_ms`. A threshold is not computed (and the
label stays 0) for runs with fewer than 20 valid values. For the published run
the thresholds are 21.1 ms (`rtt_avg_ms`), 6.0 ms (`jitter_ms`) and 5.2 ms
(`gateway_rtt_avg_ms`); the values used for each run are stored in
`dataset/summary.json`. These labels are derived from the measurements
themselves; they are reference points for evaluating detectors, not
independent ground truth.

### Manual annotations and notes

| Column | Type | Description |
|---|---|---|
| `event_category` | string | categories of the manual events covering the sample time, joined with ` \| `; one of `load`, `outage`, `maintenance`, `environment`, `isp`, `device`, `other` |
| `event_description` | string | the corresponding free-text descriptions |
| `sample_duration_s` | float | wall-clock time the sample's probes took |
| `notes` | string | probe errors and status messages, `; `-separated |

## Validation performed by the builder

- Rows with a malformed `timestamp_utc` are dropped and duplicate
  `(run_id, timestamp_utc)` pairs are reduced to the first occurrence (none in
  the published run).
- Loss percentages outside 0-100 are set to missing (none in the published
  run).
- `speedtest_ping_ms` values of 600,000 ms or more are set to missing. The
  `speedtest-cli` fallback scores each failed latency attempt as 3,600 s and
  averages three attempts over six, so one failed attempt already yields
  600,000 ms and three yield 1,800,000 ms. The raw file keeps the original
  values; 79 of the 460 speed-test rows are affected. Collector version 2.0.1
  no longer records such values.

## Statistics

<!-- BEGIN GENERATED STATISTICS -->
Generated by `scripts/build_dataset.py` on 2026-10-10T05:16:24Z from 1 collection run(s).

| Item | Value |
|---|---|
| Rows | 54,617 |
| Columns | 67 |
| First sample (UTC) | 2026-09-20T16:34:22Z |
| Last sample (UTC) | 2026-10-09T19:49:43Z |
| Span | 19.14 days |
| Sampling coverage | 99.1 % of the samples expected at the configured interval |
| Manual event annotations | 0 |

| Run | Vantage | Link | OS | Interval | Rows | Span | Coverage |
|---|---|---|---|---|---|---|---|
| desktop-eth_20260920_123407 | desktop-eth | unknown (54617) | Windows | 30 s | 54,617 | 459.26 h | 99.1 % |

| Metric | n | Median | Mean | p95 | Max |
|---|---|---|---|---|---|
| rtt_avg_ms | 54,599 | 11.1 | 12.581 | 22.3 | 288.6 |
| rtt_min_ms | 54,599 | 10.0 | 10.432 | 14.0 | 263.0 |
| rtt_max_ms | 54,599 | 13.0 | 17.869 | 32.0 | 373.0 |
| jitter_ms | 54,599 | 1.0 | 2.306 | 7.667 | 59.778 |
| packet_loss_pct | 54,617 | 0.0 | 1.763 | 10.0 | 100.0 |
| gateway_rtt_avg_ms | 54,599 | 0.2 | 0.321 | 0.8 | 89.4 |
| gateway_packet_loss_pct | 54,617 | 0.0 | 0.033 | 0.0 | 100.0 |
| tcp_connect_median_ms | 54,598 | 14.484 | 19.311 | 32.127 | 1524.463 |
| dns_resolve_ms | 54,595 | 36.026 | 43.594 | 74.878 | 3252.214 |
| dns_recursive_ms | 54,590 | 59.242 | 87.275 | 106.31 | 25645.041 |
| http_ms | 54,565 | 81.911 | 154.745 | 271.36 | 13111.02 |
| wifi_signal_pct | 4 | 85.0 | 85.75 | 88.4 | 89.0 |
| wifi_rssi_dbm | 4 | -57.5 | -57.125 | -55.8 | -55.5 |
| wifi_rx_mbps | 4 | 866.7 | 866.7 | 866.7 | 866.7 |
| download_mbps | 460 | 34.725 | 68.463 | 318.275 | 480.72 |
| upload_mbps | 460 | 28.625 | 26.121 | 41.142 | 54.17 |
| speedtest_ping_ms | 381 | 52.058 | 93.665 | 196.381 | 1167.189 |

| Reference label | Rows | Share |
|---|---|---|
| label_outage | 18 | 0.033 % |
| label_packet_loss | 7,692 | 14.084 % |
| label_high_latency | 3,067 | 5.615 % |
| label_high_jitter | 5,074 | 9.29 % |
| label_gateway_degraded | 560 | 1.025 % |
| label_anomaly | 13,036 | 23.868 % |

<!-- END GENERATED STATISTICS -->

## Known limitations and biases

- Single vantage point, single run. Absolute values reflect one site's access
  technology, router, cabling and ISP routing. Relative variation, the
  structure of episodes and the probe-to-probe relationships transfer better
  than absolute baselines.
- `link_type` is `unknown` for every row. The PowerShell route query returned
  nothing on this machine, the collector fell back to `route print`, and that
  fallback does not report the media type. The vantage point is a wired
  desktop (host label `desktop-eth`, median first-hop round-trip time 0.2 ms).
  Four rows carry Wi-Fi link metrics (channel 44 or 157, 5 GHz, 802.11ac,
  866.7 Mbit/s): a wireless adapter on the same machine was connected at those
  moments. Their first-hop round-trip times (0.0 to 1.2 ms) and target
  latencies match the wired baseline, so the default route stayed on the
  wired link.
- ICMP resolution on Windows is 1 ms and sub-millisecond replies are stored as
  0; jitter and standard deviation computed from such values contain
  quantisation noise, and the gateway columns are mostly 0 for this wired
  site. Use `tcp_connect_*` for sub-millisecond latency, or collect on
  Linux/macOS. `rtt_resolution_ms` states the resolution of each run.
- ICMP is handled differently from user traffic by some routers and hosts
  (rate limiting, de-prioritisation). The TCP connect and HTTP columns measure
  the transport and application paths that applications actually use, and the
  October 6 TCP/ICMP split shows that the two can be routed differently.
- The 10-echo burst characterises latency over roughly ten seconds of each
  interval; events shorter than that between samples are not observed.
- The DNS "cached" probe is cached at the upstream resolver, not at the
  gateway: at this site the gateway forwards every query, so the column
  measures the upstream resolver plus the forwarding hop.
- The DNS "recursive" probe generates NXDOMAIN answers for random names; a
  resolver that applies aggressive negative caching or that intercepts
  NXDOMAIN (ISP search redirection) still answers, but its timing reflects that
  behaviour.
- The HTTP probe includes the operating system's name lookup for the URL's
  host, which is normally cached; its discrete modes near 1.1, 1.3 and 4.1 s
  are retransmission timers waiting out lost packets, not propagation or
  queueing delay.
- Speed tests were run with the `speedtest-cli` fallback, which uses the
  Python interpreter's own HTTP transfers, and without a pinned server. The
  download column is a lower bound that depends strongly on the chosen
  server; 79 tests have no latency, 7 report 0 Mbit/s download and 4 report
  0 Mbit/s upload. Loaded latency is absent. Speed tests also load the
  household link for about half a minute, which is visible to other devices,
  and they leave a 60-second gap in the 30-second grid.
- The two halves of the capture differ markedly (see the findings). Models
  trained on one half should be evaluated on the other.
- No manual event annotations were recorded, so the `event_*` columns are
  empty and the causes of the episodes are inferred from the probes only.
- Gaps: system sleep, reboots and collector restarts appear as gaps
  (`gap_before_s`) or as separate runs; ticks skipped after an overrun also
  appear as gaps. The published run has none longer than 122 s.
- Clock: timestamps come from the system clock; `elapsed_s` uses a monotonic
  clock and is unaffected by clock adjustments.

## Privacy

The dataset does not contain public IP addresses, hostnames of household
devices, MAC addresses, Wi-Fi network names or per-device traffic. Gateway and
resolver addresses are private LAN addresses. The run metadata records the
operating system build, the Python version, the time zone and the private LAN
address of the collector. Speed-test rows name the test server, which was not
pinned and therefore only indicates the broad region of the site.

## Citation

If you use the data:

```bibtex
@dataset{ezharjan_localnetdynamics_data_2026,
  author    = {Ezharjan, Alexander},
  title     = {LocalNet-Dynamics: A Longitudinal Local Area Network Telemetry Dataset},
  year      = {2026},
  version   = {1.0.0},
  publisher = {Hugging Face},
  doi       = {10.57967/hf/10841},
  url       = {https://huggingface.co/datasets/ezharjan/LocalNetDynamics}
}
```

If you use the collector, the build pipeline or the analysis code:

```bibtex
@software{ezharjan_localnetdynamics_code_2026,
  author  = {Ezharjan, Alexander},
  title   = {LocalNet-Dynamics: collector, build pipeline and analysis toolkit for longitudinal home-network telemetry},
  year    = {2026},
  version = {1.0.0},
  url     = {https://github.com/Ezharjan/LocalNetDynamicsDataset}
}
```
