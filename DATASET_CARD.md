---
pretty_name: LocalNet-Dynamics
license: mit
language:
  - en
tags:
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
  - time-series
task_categories:
  - time-series-forecasting
  - tabular-classification
  - tabular-regression
size_categories:
  - n<1K
configs:
  - config_name: default
    data_files:
      - split: train
        path: dataset/localnet_dynamics.csv
---

# LocalNet-Dynamics

A longitudinal, high-resolution telemetry dataset of a residential/edge local
area network, sampled every 30 seconds around the clock from a client machine.
Each row is one sampling epoch and combines latency, loss, jitter, DNS, HTTP,
Wi-Fi link and throughput measurements with time-of-day features, rule-based
reference labels and manual event annotations.

- Point of contact: Aizierjiang Aiersilan
- Repository (code and data): https://github.com/Ezharjan/LocalNet-Dynamics
- License: MIT
- Version: 1.0.0

## Intended uses

- Time-series forecasting of latency, jitter and loss from diurnal and weekly
  patterns.
- Unsupervised and semi-supervised anomaly detection, evaluated against the
  reference labels and the manual annotations.
- QoS classification (for example, whether a sample meets the requirements of
  real-time voice, cloud gaming or video conferencing).
- Root-cause studies: the gateway probe, the Wi-Fi link metrics and the
  cached-versus-recursive DNS timings make it possible to separate first-hop
  (Wi-Fi/LAN) degradation from ISP path and resolver degradation.
- Bufferbloat and latency-under-load analysis from the speed-test rows.

## Collection site

<!-- Fill in the site description before a release. -->

| Item | Value |
|---|---|
| Region | - |
| Access technology and advertised plan | - |
| Router / access point | - |
| Client machine and operating system | - |
| Link between client and router | - |
| Wi-Fi standard and band (if wireless) | - |
| Typical household usage during the run | - |

## Files

| File | Content |
|---|---|
| `dataset/localnet_dynamics.csv` | consolidated dataset, one row per sample, all runs |
| `dataset/localnet_dynamics.parquet` | the same table with typed columns |
| `dataset/summary.json` | row counts, coverage, per-metric statistics, per-run label thresholds |
| `dataset/raw/localnet_<run_id>.csv` | raw measurements of one collection run, exactly as written by the collector |
| `dataset/raw/localnet_<run_id>.json` | configuration, environment, detected network and summary of that run |
| `dataset/raw/events.csv` | manual event annotations (`start_utc`, `end_utc`, `category`, `description`) |

The consolidated files are produced from the raw files by
`scripts/build_dataset.py`; the raw files are never edited.

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

After the concurrent probes, and only on their own cadence (default every
30 minutes), the heavy tests run: an Ookla speed test and/or an `iperf3` test.
Because they run after the latency probes of the same sample, they do not
influence that sample's latency columns; a sample that includes a speed test
usually overruns the interval, and the collector then skips to the next tick.

The default gateway, the interface type and the resolver are detected at start
and re-detected every 15 minutes (or within a minute after the gateway probe
loses every packet). System sleep is inhibited for the duration of the run.

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
| `speedtest_ping_ms` | float | idle latency to the speed-test server as reported by the tool |
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
label stays 0) for runs with fewer than 20 valid values. The values used for
each run are stored in `dataset/summary.json`. These labels are derived from
the measurements themselves; they are reference points for evaluating
detectors, not independent ground truth.

### Manual annotations and notes

| Column | Type | Description |
|---|---|---|
| `event_category` | string | categories of the manual events covering the sample time, joined with ` \| `; one of `load`, `outage`, `maintenance`, `environment`, `isp`, `device`, `other` |
| `event_description` | string | the corresponding free-text descriptions |
| `sample_duration_s` | float | wall-clock time the sample's probes took |
| `notes` | string | probe errors and status messages, `; `-separated |

## Statistics

<!-- BEGIN GENERATED STATISTICS -->
Not built yet. Run `python scripts/build_dataset.py` after collecting data; the
statistics of the consolidated dataset are inserted here automatically.
<!-- END GENERATED STATISTICS -->

## Known limitations and biases

- Single vantage point per run. Absolute values reflect one site's access
  technology, router, Wi-Fi conditions and ISP routing. Relative variation,
  diurnal structure and anomalies transfer better than absolute baselines.
- ICMP resolution on Windows is 1 ms and sub-millisecond replies are stored as
  0; jitter and standard deviation computed from such values contain
  quantisation noise. Use `tcp_connect_*` for sub-millisecond latency, or
  collect on Linux/macOS. `rtt_resolution_ms` states the resolution of each
  run.
- ICMP is handled differently from user traffic by some routers and hosts
  (rate limiting, de-prioritisation). The TCP connect and HTTP columns measure
  the transport and application paths that applications actually use.
- The 10-echo burst characterises latency over roughly ten seconds of each
  interval; events shorter than that between samples are not observed.
- The DNS "recursive" probe generates NXDOMAIN answers for random names; a
  resolver that applies aggressive negative caching or that intercepts
  NXDOMAIN (ISP search redirection) still answers, but its timing reflects that
  behaviour.
- The HTTP probe includes the operating system's name lookup for the URL's
  host, which is normally cached.
- Speed tests depend on the selected Ookla server and on the server's load;
  pinning a server reduces, but does not remove, this variance. Speed tests
  also load the household link for about half a minute, which is visible to
  other devices.
- Wi-Fi quality on Windows is a 0-100 value from which RSSI is derived, not a
  direct RSSI reading; the negotiated PHY rate is not the achievable
  throughput.
- Gaps: system sleep, reboots and collector restarts appear as gaps
  (`gap_before_s`) or as separate runs; ticks skipped after an overrun also
  appear as gaps.
- Clock: timestamps come from the system clock; `elapsed_s` uses a monotonic
  clock and is unaffected by clock adjustments.

## Privacy

The dataset does not contain public IP addresses, hostnames, MAC addresses or
per-device traffic. The Wi-Fi network name is only stored when the collector
is run with `--record-ssid`. Gateway and resolver addresses are private LAN
addresses. Speed-test rows name the test server, which indicates the
metropolitan area of the site.

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
