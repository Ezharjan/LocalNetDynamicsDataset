#!/usr/bin/env python3
"""
Consolidate raw collector runs into the published LocalNet-Dynamics dataset.

Reads every `localnet_*.csv` (and its `.json` run metadata) from the raw
directory, validates and merges them (malformed timestamps, duplicates, loss
percentages outside 0-100 and failed speed-test latency probes are handled),
adds time-of-day features, gap information, rule-based reference labels and
manual event annotations, and writes:

    dataset/localnet_dynamics.csv       consolidated dataset
    dataset/localnet_dynamics.parquet   same content (needs pyarrow)
    dataset/summary.json                statistics, per-run thresholds, label counts

It also refreshes the auto-generated statistics section of DATASET_CARD.md so
the documentation always matches the data.

    python build_dataset.py
    python build_dataset.py --raw-dir dataset/raw --out-dir dataset --mad-k 5
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from collect_data import COLLECTOR_VERSION, CSV_FIELDS  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent

STRING_COLUMNS = ["run_id", "host_label", "timestamp_utc", "timestamp_local", "link_type",
                  "gateway_ip", "dns_server", "target", "wifi_ssid", "wifi_phy",
                  "speedtest_server", "notes"]
INT_COLUMNS = ["sample_index", "epoch_s", "packets_sent", "packets_received", "http_status",
               "wifi_signal_pct", "wifi_channel", "speedtest_server_id", "lan_retransmits"]

RUN_COLUMNS = ["collector_version", "collector_os", "interval_s", "rtt_resolution_ms"]
TIME_COLUMNS = ["date_local", "hour_local", "weekday_local", "is_weekend", "gap_before_s"]
LABEL_COLUMNS = ["label_outage", "label_packet_loss", "label_high_latency",
                 "label_high_jitter", "label_gateway_degraded", "label_anomaly"]
EVENT_COLUMNS = ["event_category", "event_description"]

# (column, absolute floor in ms, floor as a fraction of the median)
THRESHOLD_RULES = {
    "rtt_avg_ms": (10.0, 0.5),
    "jitter_ms": (5.0, 1.0),
    "gateway_rtt_avg_ms": (5.0, 1.0),
}

STATS_COLUMNS = [
    "rtt_avg_ms", "rtt_min_ms", "rtt_max_ms", "jitter_ms", "packet_loss_pct",
    "gateway_rtt_avg_ms", "gateway_packet_loss_pct", "tcp_connect_median_ms",
    "dns_resolve_ms", "dns_recursive_ms", "http_ms", "wifi_signal_pct", "wifi_rssi_dbm",
    "wifi_rx_mbps", "download_mbps", "upload_mbps", "speedtest_ping_ms",
    "download_loaded_latency_ms", "upload_loaded_latency_ms", "lan_throughput_mbps",
]

# speedtest-cli scores a failed latency attempt as 3600 s and averages three attempts
# over six, so one failed attempt already yields a value of at least 600,000 ms.
SPEEDTEST_PING_FAILED_MS = 600_000

CARD_BEGIN = "<!-- BEGIN GENERATED STATISTICS -->"
CARD_END = "<!-- END GENERATED STATISTICS -->"


# --------------------------------------------------------------------------- #
# Loading
# --------------------------------------------------------------------------- #

def os_family(platform_string: str) -> str:
    p = (platform_string or "").lower()
    if p.startswith("windows"):
        return "Windows"
    if p.startswith("macos") or p.startswith("darwin"):
        return "macOS"
    if p.startswith("linux"):
        return "Linux"
    return platform_string.split("-")[0] if platform_string else ""


def load_run(csv_path: Path) -> tuple[pd.DataFrame, dict]:
    dtypes = {c: "string" for c in STRING_COLUMNS}
    df = pd.read_csv(csv_path, dtype=dtypes, keep_default_na=True, na_values=[""])
    for c in STRING_COLUMNS:
        if c in df.columns:
            df[c] = df[c].fillna("").astype(str)
    meta: dict = {}
    json_path = csv_path.with_suffix(".json")
    if json_path.exists():
        try:
            meta = json.loads(json_path.read_text(encoding="utf-8"))
        except ValueError:
            meta = {}
    if "run_id" not in df.columns or df["run_id"].eq("").all():
        df["run_id"] = meta.get("run_id") or csv_path.stem.replace("localnet_", "")
    df["collector_version"] = meta.get("collector_version", "")
    df["collector_os"] = os_family((meta.get("environment") or {}).get("os", ""))
    interval = meta.get("interval_s")
    if interval is None and "epoch_s" in df.columns and len(df) > 1:
        interval = float(np.nanmedian(np.diff(df["epoch_s"].astype(float).values)))
    df["interval_s"] = interval
    df["rtt_resolution_ms"] = (meta.get("environment") or {}).get("ping_resolution_ms")
    return df, meta


def load_raw(raw_dir: Path) -> tuple[pd.DataFrame, dict[str, dict]]:
    files = sorted(raw_dir.glob("localnet_*.csv"))
    if not files:
        raise SystemExit(f"no localnet_*.csv files found in {raw_dir}")
    frames, metas = [], {}
    for path in files:
        df, meta = load_run(path)
        if df.empty:
            print(f"skipping empty file {path.name}")
            continue
        frames.append(df)
        metas[str(df["run_id"].iloc[0])] = meta
        print(f"loaded {path.name}: {len(df)} rows")
    df = pd.concat(frames, ignore_index=True, sort=False)
    for c in CSV_FIELDS:
        if c not in df.columns:
            df[c] = "" if c in STRING_COLUMNS else np.nan
    return df, metas


# --------------------------------------------------------------------------- #
# Validation and derived columns
# --------------------------------------------------------------------------- #

def validate(df: pd.DataFrame) -> pd.DataFrame:
    before = len(df)
    df = df[df["timestamp_utc"].str.match(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")]
    dropped = before - len(df)
    if dropped:
        print(f"dropped {dropped} rows with malformed timestamps")
    df = df.drop_duplicates(subset=["run_id", "timestamp_utc"], keep="first")
    df = df.sort_values(["run_id", "epoch_s"], kind="stable").reset_index(drop=True)
    for col in ("packet_loss_pct", "gateway_packet_loss_pct"):
        bad = (df[col] < 0) | (df[col] > 100)
        if bad.any():
            print(f"warning: {int(bad.sum())} rows have {col} outside 0-100; set to missing")
            df.loc[bad, col] = np.nan
    failed = df["speedtest_ping_ms"] >= SPEEDTEST_PING_FAILED_MS
    if failed.any():
        print(f"{int(failed.sum())} speed-test rows have a failed server latency probe "
              f"(speedtest_ping_ms >= {SPEEDTEST_PING_FAILED_MS:,}); set to missing")
        df.loc[failed, "speedtest_ping_ms"] = np.nan
    return df


def add_time_features(df: pd.DataFrame) -> pd.DataFrame:
    local = pd.to_datetime(df["timestamp_local"].str[:19], format="%Y-%m-%dT%H:%M:%S", errors="coerce")
    df["date_local"] = local.dt.strftime("%Y-%m-%d")
    df["hour_local"] = local.dt.hour
    df["weekday_local"] = local.dt.dayofweek
    df["is_weekend"] = (local.dt.dayofweek >= 5).astype(int)
    df["gap_before_s"] = df.groupby("run_id")["epoch_s"].diff()
    return df


def robust_threshold(values: pd.Series, floor_abs: float, floor_rel: float, k: float) -> dict:
    v = values.dropna().astype(float)
    if len(v) < 20:
        return {"n": int(len(v)), "median": None, "mad": None, "threshold": None}
    median = float(v.median())
    mad = float((v - median).abs().median())
    threshold = median + max(k * 1.4826 * mad, floor_abs, floor_rel * median)
    return {"n": int(len(v)), "median": round(median, 3), "mad": round(mad, 3),
            "threshold": round(threshold, 3)}


def add_labels(df: pd.DataFrame, k: float) -> tuple[pd.DataFrame, dict]:
    thresholds: dict[str, dict] = {}
    for col in LABEL_COLUMNS:
        df[col] = 0
    sent = df["packets_sent"].fillna(0)
    received = df["packets_received"].fillna(0)
    outage = (sent > 0) & (received == 0)
    df["label_outage"] = outage.astype(int)
    df["label_packet_loss"] = (df["packet_loss_pct"].gt(0) & ~outage).astype(int)

    for run_id, idx in df.groupby("run_id").groups.items():
        part = df.loc[idx]
        rules = {col: robust_threshold(part[col], fa, fr, k) for col, (fa, fr) in THRESHOLD_RULES.items()}
        thresholds[run_id] = rules
        t = rules["rtt_avg_ms"]["threshold"]
        if t is not None:
            df.loc[idx, "label_high_latency"] = part["rtt_avg_ms"].gt(t).astype(int)
        t = rules["jitter_ms"]["threshold"]
        if t is not None:
            df.loc[idx, "label_high_jitter"] = part["jitter_ms"].gt(t).astype(int)
        t = rules["gateway_rtt_avg_ms"]["threshold"]
        gw = part["gateway_packet_loss_pct"].gt(0)
        if t is not None:
            gw |= part["gateway_rtt_avg_ms"].gt(t)
        df.loc[idx, "label_gateway_degraded"] = gw.astype(int)

    df["label_anomaly"] = df[LABEL_COLUMNS[:-1]].max(axis=1).astype(int)
    return df, thresholds


def load_events(path: Path) -> pd.DataFrame:
    columns = ["start_utc", "end_utc", "category", "description"]
    if not path.exists():
        return pd.DataFrame(columns=columns)
    ev = pd.read_csv(path, dtype=str, keep_default_na=False)
    missing = [c for c in columns if c not in ev.columns]
    if missing:
        raise SystemExit(f"{path}: missing columns {missing}")
    ev["start"] = pd.to_datetime(ev["start_utc"], utc=True, errors="coerce")
    ev["end"] = pd.to_datetime(ev["end_utc"], utc=True, errors="coerce")
    bad = ev["start"].isna() | ev["end"].isna()
    if bad.any():
        print(f"warning: ignoring {int(bad.sum())} events with unparsable timestamps")
        ev = ev[~bad]
    return ev.reset_index(drop=True)


def add_events(df: pd.DataFrame, events: pd.DataFrame) -> pd.DataFrame:
    df["event_category"] = ""
    df["event_description"] = ""
    if events.empty:
        return df
    ts = pd.to_datetime(df["timestamp_utc"], utc=True)
    cats = [[] for _ in range(len(df))]
    descs = [[] for _ in range(len(df))]
    for _, ev in events.iterrows():
        hit = np.flatnonzero((ts >= ev["start"]) & (ts <= ev["end"]))
        for i in hit:
            cats[i].append(str(ev["category"]))
            descs[i].append(str(ev["description"]))
    df["event_category"] = [" | ".join(c) for c in cats]
    df["event_description"] = [" | ".join(d) for d in descs]
    return df


def order_columns(df: pd.DataFrame) -> pd.DataFrame:
    base = [c for c in CSV_FIELDS if c != "notes"]
    head = base[:11]
    body = base[11:]
    ordered = head + RUN_COLUMNS + TIME_COLUMNS + body + LABEL_COLUMNS + EVENT_COLUMNS + ["notes"]
    extra = [c for c in df.columns if c not in ordered]
    return df[ordered + extra]


def cast_types(df: pd.DataFrame) -> pd.DataFrame:
    for col in INT_COLUMNS + ["hour_local", "weekday_local", "is_weekend"] + LABEL_COLUMNS:
        if col in df.columns:
            df[col] = df[col].astype("Int64")
    return df


# --------------------------------------------------------------------------- #
# Summary and dataset card
# --------------------------------------------------------------------------- #

def column_summary(series: pd.Series) -> dict | None:
    v = series.dropna().astype(float)
    if v.empty:
        return None
    return {"count": int(v.size), "mean": round(float(v.mean()), 3),
            "median": round(float(v.median()), 3), "p95": round(float(v.quantile(0.95)), 3),
            "min": round(float(v.min()), 3), "max": round(float(v.max()), 3)}


def build_summary(df: pd.DataFrame, metas: dict, thresholds: dict, events: pd.DataFrame,
                  k: float) -> dict:
    ts = pd.to_datetime(df["timestamp_utc"], utc=True)
    runs = []
    expected_total = 0.0
    for run_id, part in df.groupby("run_id", sort=True):
        meta = metas.get(run_id, {})
        interval = part["interval_s"].iloc[0]
        span = float(part["epoch_s"].max() - part["epoch_s"].min())
        expected = span / float(interval) + 1 if interval and interval > 0 else len(part)
        expected_total += expected
        runs.append({
            "run_id": run_id,
            "host_label": part["host_label"].iloc[0],
            "rows": int(len(part)),
            "first_utc": part["timestamp_utc"].iloc[0],
            "last_utc": part["timestamp_utc"].iloc[-1],
            "span_hours": round(span / 3600.0, 2),
            "interval_s": float(interval) if interval else None,
            "coverage_pct": round(100.0 * len(part) / expected, 2) if expected else None,
            "link_types": {str(k_): int(v) for k_, v in part["link_type"].value_counts().items()},
            "collector_version": meta.get("collector_version", ""),
            "os": (meta.get("environment") or {}).get("os", ""),
            "status": meta.get("status", ""),
            "thresholds": thresholds.get(run_id, {}),
        })
    labels = {col: {"rows": int(df[col].sum()), "share_pct": round(100.0 * float(df[col].mean()), 3)}
              for col in LABEL_COLUMNS}
    return {
        "generated_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "builder_version": COLLECTOR_VERSION,
        "rows": int(len(df)),
        "columns": int(df.shape[1]),
        "runs": runs,
        "first_utc": ts.min().strftime("%Y-%m-%dT%H:%M:%SZ"),
        "last_utc": ts.max().strftime("%Y-%m-%dT%H:%M:%SZ"),
        "span_days": round((ts.max() - ts.min()).total_seconds() / 86400.0, 2),
        "coverage_pct": round(100.0 * len(df) / expected_total, 2) if expected_total else None,
        "label_rule": {
            "mad_k": k,
            "description": "threshold = median + max(k * 1.4826 * MAD, absolute floor, "
                           "relative floor * median), computed per run",
            "floors": {c: {"absolute_ms": fa, "relative": fr} for c, (fa, fr) in THRESHOLD_RULES.items()},
        },
        "labels": labels,
        "events": int(len(events)),
        "metrics": {c: column_summary(df[c]) for c in STATS_COLUMNS if column_summary(df[c])},
    }


def size_category(n: int) -> str:
    bounds = [(1_000, "n<1K"), (10_000, "1K<n<10K"), (100_000, "10K<n<100K"),
              (1_000_000, "100K<n<1M"), (10_000_000, "1M<n<10M")]
    for limit, label in bounds:
        if n < limit:
            return label
    return "10M<n<100M"


def render_card_block(summary: dict) -> str:
    lines = [CARD_BEGIN,
             f"Generated by `scripts/build_dataset.py` on {summary['generated_utc']} "
             f"from {len(summary['runs'])} collection run(s).", "",
             "| Item | Value |", "|---|---|",
             f"| Rows | {summary['rows']:,} |",
             f"| Columns | {summary['columns']} |",
             f"| First sample (UTC) | {summary['first_utc']} |",
             f"| Last sample (UTC) | {summary['last_utc']} |",
             f"| Span | {summary['span_days']} days |",
             f"| Sampling coverage | {summary['coverage_pct']} % of the samples expected at the configured interval |",
             f"| Manual event annotations | {summary['events']} |", ""]
    lines += ["| Run | Vantage | Link | OS | Interval | Rows | Span | Coverage |",
              "|---|---|---|---|---|---|---|---|"]
    for r in summary["runs"]:
        links = ", ".join(f"{k_} ({v})" for k_, v in r["link_types"].items())
        interval = f"{r['interval_s']:.0f} s" if r["interval_s"] else "-"
        lines.append(f"| {r['run_id']} | {r['host_label'] or '-'} | {links} | {os_family(r['os']) or '-'} | "
                     f"{interval} | {r['rows']:,} | {r['span_hours']} h | {r['coverage_pct']} % |")
    lines += ["", "| Metric | n | Median | Mean | p95 | Max |", "|---|---|---|---|---|---|"]
    for col, st in summary["metrics"].items():
        lines.append(f"| {col} | {st['count']:,} | {st['median']} | {st['mean']} | {st['p95']} | {st['max']} |")
    lines += ["", "| Reference label | Rows | Share |", "|---|---|---|"]
    for col, st in summary["labels"].items():
        lines.append(f"| {col} | {st['rows']:,} | {st['share_pct']} % |")
    lines += ["", CARD_END]
    return "\n".join(lines)


def update_card(card_path: Path, summary: dict) -> None:
    text = card_path.read_text(encoding="utf-8")
    if CARD_BEGIN not in text or CARD_END not in text:
        print(f"warning: {card_path.name} has no generated-statistics markers; not updated")
        return
    block = render_card_block(summary)
    pattern = re.compile(re.escape(CARD_BEGIN) + r".*?" + re.escape(CARD_END), re.DOTALL)
    text = pattern.sub(lambda _: block, text, count=1)
    text = re.sub(r"(size_categories:\s*\n\s*-\s*)(\S+)", lambda m: m.group(1) + size_category(summary["rows"]),
                  text, count=1)
    card_path.write_text(text, encoding="utf-8")
    print(f"updated statistics in {card_path}")


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #

def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Build the consolidated LocalNet-Dynamics dataset.")
    p.add_argument("--raw-dir", default=str(ROOT / "dataset" / "raw"))
    p.add_argument("--out-dir", default=str(ROOT / "dataset"))
    p.add_argument("--events", default=None, help="events CSV (default: <raw-dir>/events.csv)")
    p.add_argument("--card", default=str(ROOT / "DATASET_CARD.md"))
    p.add_argument("--no-card", action="store_true", help="do not touch the dataset card")
    p.add_argument("--no-parquet", action="store_true")
    p.add_argument("--mad-k", type=float, default=5.0,
                   help="MAD multiplier for the high-latency / high-jitter / gateway rules")
    args = p.parse_args(argv)

    raw_dir, out_dir = Path(args.raw_dir), Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    events_path = Path(args.events) if args.events else raw_dir / "events.csv"

    df, metas = load_raw(raw_dir)
    df = validate(df)
    df = add_time_features(df)
    df, thresholds = add_labels(df, args.mad_k)
    events = load_events(events_path)
    df = add_events(df, events)
    df = cast_types(order_columns(df))

    csv_out = out_dir / "localnet_dynamics.csv"
    df.to_csv(csv_out, index=False)
    print(f"wrote {csv_out} ({len(df):,} rows, {df.shape[1]} columns)")
    if not args.no_parquet:
        try:
            parquet_out = out_dir / "localnet_dynamics.parquet"
            df.to_parquet(parquet_out, index=False)
            print(f"wrote {parquet_out}")
        except (ImportError, ValueError) as exc:
            print(f"parquet not written ({exc}); install pyarrow to enable it")

    summary = build_summary(df, metas, thresholds, events, args.mad_k)
    (out_dir / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(f"wrote {out_dir / 'summary.json'}")
    if not args.no_card and Path(args.card).exists():
        update_card(Path(args.card), summary)

    print(f"\nrows: {summary['rows']:,}  span: {summary['span_days']} days  "
          f"coverage: {summary['coverage_pct']} %")
    for col, st in summary["labels"].items():
        print(f"  {col:<24}{st['rows']:>8}  ({st['share_pct']} %)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
