"""End-to-end tests for build_dataset.py, annotate.py, analyze.py and plot_figures.py.

A small raw run is synthesised in a temporary directory with the collector's
own schema so that the build pipeline, label rules, event join and figure
rendering can be exercised without network access. Nothing here is written
into the repository's dataset directory.
"""

import csv
import json
import math
import random
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

pd = pytest.importorskip("pandas")

import annotate  # noqa: E402
import build_dataset as bd  # noqa: E402
import collect_data as cd  # noqa: E402

START = datetime(2026, 9, 6, 22, 0, 0, tzinfo=timezone.utc)   # 18:00 local (UTC-4)
INTERVAL = 60
ROWS = 3 * 60 * 6                                             # 18 h of 1-minute samples
LOCAL_OFFSET = timedelta(hours=-4)


def write_synthetic_run(raw_dir: Path, run_id: str = "testhost_20260906_180000") -> Path:
    rng = random.Random(7)
    rows = []
    for i in range(ROWS):
        t = START + timedelta(seconds=i * INTERVAL)
        if 400 <= i < 405:            # simulated gap of five samples (machine asleep)
            continue
        s = cd.Sample(run_id=run_id, host_label="testhost", sample_index=i,
                      timestamp_utc=t.strftime("%Y-%m-%dT%H:%M:%SZ"),
                      timestamp_local=(t + LOCAL_OFFSET).strftime("%Y-%m-%dT%H:%M:%S-04:00"),
                      epoch_s=int(t.timestamp()), elapsed_s=float(i * INTERVAL),
                      link_type="wifi", gateway_ip="192.168.1.1", dns_server="192.168.1.1",
                      target="8.8.8.8")
        base = 14.0 + 4.0 * math.sin((i / 60.0) / 24.0 * 2 * math.pi)
        rtts = [max(1.0, rng.gauss(base, 1.2)) for _ in range(10)]
        if i in (700, 701, 702):     # simulated outage
            rtts = []
        elif i % 97 == 0:            # occasional single lost packet
            rtts = rtts[:9]
        elif i == 900:               # latency spike
            rtts = [r + 120 for r in rtts]
        st = cd.rtt_stats(rtts)
        s.rtt_min_ms, s.rtt_avg_ms, s.rtt_median_ms = st["min"], st["avg"], st["median"]
        s.rtt_max_ms, s.rtt_stddev_ms, s.jitter_ms = st["max"], st["stddev"], st["jitter"]
        s.packets_sent, s.packets_received = 10, len(rtts)
        s.packet_loss_pct = round(100.0 * (10 - len(rtts)) / 10, 1)
        g = [max(0.3, rng.gauss(2.0, 0.5)) for _ in range(5)]
        if i == 1000:
            g = [r + 60 for r in g]
        gst = cd.rtt_stats(g)
        s.gateway_rtt_min_ms, s.gateway_rtt_avg_ms = gst["min"], gst["avg"]
        s.gateway_rtt_max_ms, s.gateway_jitter_ms = gst["max"], gst["jitter"]
        s.gateway_packet_loss_pct = 0.0
        s.tcp_connect_min_ms = round(base - 0.5, 3)
        s.tcp_connect_median_ms = round(base, 3)
        s.dns_resolve_ms = round(rng.gauss(2.5, 0.4), 3)
        s.dns_recursive_ms = round(rng.gauss(40, 8), 3)
        s.http_ms, s.http_status = round(rng.gauss(60, 10), 3), 200
        s.wifi_signal_pct = 80 + (i % 5)
        s.wifi_rssi_dbm = cd.rssi_from_quality(s.wifi_signal_pct)
        s.wifi_rx_mbps, s.wifi_tx_mbps = 866.7, 866.7
        s.wifi_channel, s.wifi_band_ghz, s.wifi_phy = 36, 5.0, "802.11ax"
        if i % 30 == 0:
            s.download_mbps, s.upload_mbps = round(rng.gauss(450, 30), 2), round(rng.gauss(22, 2), 2)
            s.speedtest_ping_ms = 1800000.0 if i == 60 else 9.5     # one failed latency probe
            s.speedtest_server_id, s.speedtest_server = 1234, "Example ISP, Springfield"
        s.sample_duration_s = 10.4
        s.notes = "ping: no replies" if not rtts else ""
        rows.append(cd.asdict(s))
    raw_dir.mkdir(parents=True, exist_ok=True)
    csv_path = raw_dir / f"localnet_{run_id}.csv"
    with csv_path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=cd.CSV_FIELDS)
        w.writeheader()
        w.writerows(rows)
    meta = {"run_id": run_id, "collector_version": cd.COLLECTOR_VERSION, "status": "completed",
            "interval_s": float(INTERVAL), "sample_count": len(rows),
            "environment": {"os": "Windows-11-10.0.22631-SP0", "ping_resolution_ms": 1.0},
            "network": {"link_type": "wifi"}}
    csv_path.with_suffix(".json").write_text(json.dumps(meta), encoding="utf-8")
    return csv_path


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    root = tmp_path_factory.mktemp("ds")
    raw = root / "raw"
    write_synthetic_run(raw)
    # a manual annotation covering the simulated outage
    annotate.main(["router rebooted", "--category", "maintenance",
                   "--start", "2026-09-07T09:39:30Z", "--end", "2026-09-07T09:43:00Z",
                   "--file", str(raw / "events.csv")])
    card = root / "DATASET_CARD.md"
    card.write_text("---\nsize_categories:\n  - n<1K\n---\n# card\n\n"
                    f"{bd.CARD_BEGIN}\nold\n{bd.CARD_END}\n", encoding="utf-8")
    bd.main(["--raw-dir", str(raw), "--out-dir", str(root), "--card", str(card)])
    df = pd.read_csv(root / "localnet_dynamics.csv")
    summary = json.loads((root / "summary.json").read_text(encoding="utf-8"))
    return root, df, summary, card


def test_rows_and_columns(built):
    root, df, summary, _ = built
    assert len(df) == ROWS - 5
    assert list(df.columns[:11]) == cd.CSV_FIELDS[:11]
    assert df.columns[-1] == "notes"
    for col in bd.RUN_COLUMNS + bd.TIME_COLUMNS + bd.LABEL_COLUMNS + bd.EVENT_COLUMNS:
        assert col in df.columns
    assert (root / "localnet_dynamics.parquet").exists()
    assert summary["rows"] == len(df) and summary["runs"][0]["interval_s"] == 60.0
    assert summary["runs"][0]["os"].startswith("Windows")
    assert df["collector_os"].iloc[0] == "Windows" and df["rtt_resolution_ms"].iloc[0] == 1.0


def test_time_features_and_gaps(built):
    _, df, summary, _ = built
    first = df.iloc[0]
    assert first["hour_local"] == 18 and first["date_local"] == "2026-09-06"
    assert first["weekday_local"] == 6 and first["is_weekend"] == 1       # Sunday
    gaps = df["gap_before_s"].dropna()
    assert gaps.max() == 6 * INTERVAL and (gaps == INTERVAL).sum() == len(gaps) - 1
    assert 99.0 < summary["coverage_pct"] < 100.0


def test_reference_labels(built):
    _, df, summary, _ = built
    outages = df[df["label_outage"] == 1]
    assert list(outages["sample_index"]) == [700, 701, 702]
    assert df.loc[df["sample_index"] == 900, "label_high_latency"].item() == 1
    assert df.loc[df["sample_index"] == 1000, "label_gateway_degraded"].item() == 1
    assert df.loc[df["sample_index"] == 97, "label_packet_loss"].item() == 1
    assert df.loc[df["sample_index"] == 97, "label_outage"].item() == 0
    assert df["label_high_latency"].sum() <= 5                 # rule is not over-sensitive
    assert (df["label_anomaly"] >= df["label_outage"]).all()
    thr = summary["runs"][0]["thresholds"]["rtt_avg_ms"]
    assert thr["threshold"] >= thr["median"] + 10.0
    assert summary["labels"]["label_outage"]["rows"] == 3


def test_failed_speedtest_latency_is_missing(built):
    _, df, summary, _ = built
    tests = df[df["download_mbps"].notna()]
    assert len(tests) == 36
    assert tests["speedtest_ping_ms"].isna().sum() == 1
    assert tests.loc[tests["sample_index"] == 60, "speedtest_ping_ms"].isna().all()
    assert summary["metrics"]["speedtest_ping_ms"]["max"] == 9.5


def test_event_join(built):
    _, df, summary, _ = built
    tagged = df[df["event_category"].fillna("") != ""]
    assert summary["events"] == 1
    assert set(tagged["sample_index"]) == {700, 701, 702, 703}
    assert (tagged["event_description"] == "router rebooted").all()


def test_card_updated(built):
    _, _, summary, card = built
    text = card.read_text(encoding="utf-8")
    assert "old" not in text
    assert f"| Rows | {summary['rows']:,} |" in text
    assert "size_categories:\n  - 1K<n<10K" in text
    assert text.count(bd.CARD_BEGIN) == 1 and text.count(bd.CARD_END) == 1


def test_size_category():
    assert bd.size_category(500) == "n<1K"
    assert bd.size_category(86_400) == "10K<n<100K"
    assert bd.size_category(2_000_000) == "1M<n<10M"


def test_analysis_json(built):
    import analyze
    root, df, summary, _ = built
    analyze.main(["--input", str(root / "localnet_dynamics.csv"), "--no-report"])
    a = json.loads((root / "analysis.json").read_text(encoding="utf-8"))
    assert a["overview"]["rows"] == len(df)
    assert len(a["hour_profile"]) == 18 and len(a["daily"]) == 2
    assert len(a["outages"]) == 1 and a["outages"][0]["samples"] == 3
    assert a["regimes"]["before"]["n"] + a["regimes"]["after"]["n"] == len(df)
    assert a["speedtest"]["tests"] == 36 and a["speedtest"]["failed_latency_probe"] == 1
    assert a["labels"]["counts"]["label_outage"] == 3
    assert set(a["autocorrelation"]["values"]) >= {"log_rtt_avg_ms", "loss_indicator", "http_ms"}


def test_figures(built, tmp_path):
    pytest.importorskip("matplotlib")
    import plot_figures
    root, _, _, _ = built
    out = tmp_path / "figures"
    plot_figures.main(["--input", str(root / "localnet_dynamics.csv"), "--out-dir", str(out)])
    for name in ("overview_timeline", "diurnal_profile", "calendar_heatmaps", "tcp_vs_icmp",
                 "loss_anatomy", "regimes", "autocorrelation", "speedtest", "labels"):
        assert (out / "png" / f"{name}.png").stat().st_size > 10_000, name
        assert (out / "pdf" / f"{name}.pdf").stat().st_size > 1_000, name


def test_annotate_parses_local_and_utc_times():
    assert annotate.parse_time("2026-09-12T16:00:00Z").isoformat() == "2026-09-12T16:00:00+00:00"
    assert annotate.parse_time("2026-09-12 16:00+02:00").isoformat() == "2026-09-12T14:00:00+00:00"
    local = annotate.parse_time("2026-09-12 16:00")
    assert local.tzinfo == timezone.utc
