#!/usr/bin/env python3
"""
Analyse the consolidated LocalNet-Dynamics dataset.

Reads dataset/localnet_dynamics.parquet (or .csv), prints a text report and
writes dataset/analysis.json with every number the documentation quotes:
overall statistics, hour-of-day and day-by-day profiles, the two regimes
separated by a change point in the daily loss share, outage and high-latency
episodes with their diagnosis (path change, first hop, queueing), the
conditional effect of ICMP loss on the other probes, the ICMP/TCP path split,
DNS and HTTP facts, autocorrelation, speed-test quality and label statistics.

    python analyze.py
    python analyze.py --input dataset/localnet_dynamics.csv --no-report

The figures are rendered by plot_figures.py from the same functions.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent

METRICS = ["rtt_avg_ms", "rtt_min_ms", "rtt_max_ms", "jitter_ms", "packet_loss_pct",
           "gateway_rtt_avg_ms", "tcp_connect_min_ms", "tcp_connect_median_ms",
           "dns_resolve_ms", "dns_recursive_ms", "http_ms", "download_mbps", "upload_mbps"]
LABELS = ["label_outage", "label_packet_loss", "label_high_latency", "label_high_jitter",
          "label_gateway_degraded", "label_anomaly"]
ACF_LAGS_MIN = [0.5, 1, 2.5, 5, 10, 30, 60, 180, 360, 720, 1440]

# Latency episodes: runs of label_high_latency, merged across gaps of at most
# MERGE_GAP samples and kept when they span at least MIN_EPISODE_MIN minutes.
MERGE_GAP = 10
MIN_EPISODE_MIN = 30
# An episode whose first-hop RTT exceeds the run's gateway threshold is a
# first-hop event; one whose ICMP or TCP latency floor rises by at least
# FLOOR_SHIFT_MS over the run median is a path change; anything else is queueing.
FLOOR_SHIFT_MS = 5.0
TCP_SPLIT_MS = 5.0                 # TCP floor above ICMP floor by more than this
SPEEDTEST_PING_FAILED_MS = 600_000  # speedtest-cli counts a failed latency probe as 3600 s


# --------------------------------------------------------------------------- #
# Loading and helpers
# --------------------------------------------------------------------------- #

def load(path: Path) -> pd.DataFrame:
    df = pd.read_parquet(path) if path.suffix == ".parquet" else pd.read_csv(path, low_memory=False)
    df["ts"] = pd.to_datetime(df["timestamp_utc"], utc=True)
    df["ts_local"] = pd.to_datetime(df["timestamp_local"].astype(str).str[:19],
                                    format="%Y-%m-%dT%H:%M:%S", errors="coerce")
    for col in LABELS:
        if col in df.columns:
            df[col] = df[col].fillna(0).astype(int)
    for col in METRICS + ["gateway_packet_loss_pct", "packets_received", "speedtest_ping_ms"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce").astype(float)
    return df.sort_values("ts").reset_index(drop=True)


def q(series: pd.Series, p: float) -> float | None:
    v = series.dropna()
    return round(float(v.quantile(p)), 3) if len(v) else None


def describe(series: pd.Series) -> dict | None:
    v = series.dropna().astype(float)
    if v.empty:
        return None
    return {"n": int(v.size), "median": round(float(v.median()), 3), "mean": round(float(v.mean()), 3),
            "p95": round(float(v.quantile(0.95)), 3), "p99": round(float(v.quantile(0.99)), 3),
            "max": round(float(v.max()), 3)}


def share(mask: pd.Series) -> float:
    return round(100.0 * float(mask.mean()), 2) if len(mask) else 0.0


def change_point(series: pd.Series) -> tuple:
    """Split index that minimises the within-segment sum of squares (one break)."""
    x = series.dropna().values.astype(float)
    n = len(x)
    if n < 4:
        k = max(1, n // 2)
        return series.dropna().index[k], float(x[:k].mean()), float(x[k:].mean())
    cs, cs2 = np.cumsum(x), np.cumsum(x * x)
    best = None
    for k in range(2, n - 1):
        a = cs2[k - 1] - cs[k - 1] ** 2 / k
        b = (cs2[-1] - cs2[k - 1]) - (cs[-1] - cs[k - 1]) ** 2 / (n - k)
        if best is None or a + b < best[0]:
            best = (a + b, k)
    k = best[1]
    return series.dropna().index[k], float(x[:k].mean()), float(x[k:].mean())


def runs_of(mask: np.ndarray, merge_gap: int) -> list[tuple[int, int]]:
    idx = np.flatnonzero(mask)
    if idx.size == 0:
        return []
    runs, start, prev = [], idx[0], idx[0]
    for i in idx[1:]:
        if i - prev > merge_gap:
            runs.append((start, prev))
            start = i
        prev = i
    runs.append((start, prev))
    return runs


def acf(series: pd.Series, lag: int) -> float | None:
    s = series.astype(float)
    if lag >= len(s) - 2:
        return None
    value = s.autocorr(lag)
    return round(float(value), 3) if np.isfinite(value) else None


# --------------------------------------------------------------------------- #
# Sections
# --------------------------------------------------------------------------- #

def overview(df: pd.DataFrame, summary: dict | None) -> dict:
    span = (df["ts"].max() - df["ts"].min()).total_seconds() / 86400.0
    out = {
        "rows": int(len(df)),
        "runs": int(df["run_id"].nunique()),
        "first_utc": df["ts"].min().strftime("%Y-%m-%dT%H:%M:%SZ"),
        "last_utc": df["ts"].max().strftime("%Y-%m-%dT%H:%M:%SZ"),
        "first_local": str(df["timestamp_local"].iloc[0]),
        "last_local": str(df["timestamp_local"].iloc[-1]),
        "span_days": round(span, 2),
        "full_days": int((df.groupby("date_local").size() >= 2700).sum()),
        "coverage_pct": summary.get("coverage_pct") if summary else None,
        "max_gap_s": float(df["gap_before_s"].max()),
        "gaps_of_2_intervals": int((df["gap_before_s"] == 2 * df["interval_s"].median()).sum()),
        "gaps_over_2_intervals": int((df["gap_before_s"] > 2 * df["interval_s"].median()).sum()),
        "gaps_over_2_intervals_after_speedtest": int((df["download_mbps"].shift(1).notna()
                                                      & (df["gap_before_s"] > 2 * df["interval_s"].median())).sum()),
        "rows_with_notes": int(df["notes"].fillna("").astype(str).str.len().gt(0).sum()),
        "wifi_rows": int(df["wifi_signal_pct"].notna().sum()) if "wifi_signal_pct" in df else 0,
        "link_types": {str(k): int(v) for k, v in df["link_type"].value_counts().items()},
        "metrics": {c: describe(df[c]) for c in METRICS if c in df.columns and describe(df[c])},
    }
    return out


def hour_profile(df: pd.DataFrame) -> list[dict]:
    rows = []
    for hour, part in df.groupby("hour_local"):
        rows.append({
            "hour": int(hour), "n": int(len(part)),
            "rtt_median": q(part["rtt_avg_ms"], 0.5), "rtt_p25": q(part["rtt_avg_ms"], 0.25),
            "rtt_p75": q(part["rtt_avg_ms"], 0.75), "rtt_p95": q(part["rtt_avg_ms"], 0.95),
            "jitter_median": q(part["jitter_ms"], 0.5),
            "loss_share_pct": share(part["packet_loss_pct"] > 0),
            "loss_share_weekday_pct": share(part.loc[part["is_weekend"] == 0, "packet_loss_pct"] > 0),
            "loss_share_weekend_pct": share(part.loc[part["is_weekend"] == 1, "packet_loss_pct"] > 0),
            "http_p95": q(part["http_ms"], 0.95),
            "http_over_1s_pct": share(part["http_ms"].dropna() > 1000),
            "high_latency_pct": share(part["label_high_latency"] == 1),
            "gateway_p95": q(part["gateway_rtt_avg_ms"], 0.95),
        })
    return rows


def weekday_weekend(df: pd.DataFrame) -> dict:
    out = {}
    for name, part in (("weekday", df[df["is_weekend"] == 0]), ("weekend", df[df["is_weekend"] == 1])):
        out[name] = {"n": int(len(part)), "rtt_median": q(part["rtt_avg_ms"], 0.5),
                     "rtt_p95": q(part["rtt_avg_ms"], 0.95), "loss_share_pct": share(part["packet_loss_pct"] > 0),
                     "jitter_median": q(part["jitter_ms"], 0.5), "http_median": q(part["http_ms"], 0.5)}
    return out


def daily(df: pd.DataFrame) -> list[dict]:
    rows = []
    for date, part in df.groupby("date_local"):
        rows.append({
            "date": str(date), "weekday": int(part["weekday_local"].iloc[0]), "n": int(len(part)),
            "rtt_median": q(part["rtt_avg_ms"], 0.5), "rtt_p95": q(part["rtt_avg_ms"], 0.95),
            "rtt_min_median": q(part["rtt_min_ms"], 0.5),
            "tcp_min_median": q(part["tcp_connect_min_ms"], 0.5),
            "loss_share_pct": share(part["packet_loss_pct"] > 0),
            "mean_loss_pct": round(float(part["packet_loss_pct"].mean()), 3),
            "jitter_median": q(part["jitter_ms"], 0.5),
            "http_p95": q(part["http_ms"], 0.95),
            "anomaly_pct": share(part["label_anomaly"] == 1),
            "gateway_degraded": int(part["label_gateway_degraded"].sum()),
        })
    return rows


def period_stats(part: pd.DataFrame) -> dict:
    return {
        "n": int(len(part)),
        "days": int(part["date_local"].nunique()),
        "rtt_median": q(part["rtt_avg_ms"], 0.5), "rtt_p95": q(part["rtt_avg_ms"], 0.95),
        "rtt_p99": q(part["rtt_avg_ms"], 0.99),
        "jitter_median": q(part["jitter_ms"], 0.5), "jitter_p95": q(part["jitter_ms"], 0.95),
        "loss_share_pct": share(part["packet_loss_pct"] > 0),
        "mean_loss_pct": round(float(part["packet_loss_pct"].mean()), 3),
        "tcp_median": q(part["tcp_connect_median_ms"], 0.5),
        "dns_cached_median": q(part["dns_resolve_ms"], 0.5),
        "dns_recursive_median": q(part["dns_recursive_ms"], 0.5),
        "http_median": q(part["http_ms"], 0.5), "http_p95": q(part["http_ms"], 0.95),
        "http_over_1s_pct": share(part["http_ms"].dropna() > 1000),
        "labels_pct": {c: share(part[c] == 1) for c in LABELS},
    }


def regimes(df: pd.DataFrame) -> dict:
    daily_loss = df.groupby("date_local")["packet_loss_pct"].apply(lambda s: float((s > 0).mean()))
    if len(daily_loss) < 2:
        split, before, after = daily_loss.index[0], float(daily_loss.iloc[0]), float(daily_loss.iloc[0])
        first = second = df
    else:
        split, before, after = change_point(daily_loss)
        first = df[df["date_local"] < split]
        second = df[df["date_local"] >= split]
    return {
        "method": "single least-squares change point in the daily share of samples with ICMP loss",
        "split_date": str(split),
        "daily_loss_share_before_pct": round(100 * before, 2),
        "daily_loss_share_after_pct": round(100 * after, 2),
        "before": {"first_date": str(first["date_local"].min()), "last_date": str(first["date_local"].max()),
                   **period_stats(first)},
        "after": {"first_date": str(second["date_local"].min()), "last_date": str(second["date_local"].max()),
                  **period_stats(second)},
    }


def outages(df: pd.DataFrame) -> list[dict]:
    out = []
    for a, b in runs_of(df["label_outage"].values == 1, merge_gap=2):
        part = df.iloc[a:b + 1]
        interval = float(df["interval_s"].median())
        out.append({
            "start_local": str(part["timestamp_local"].iloc[0]),
            "end_local": str(part["timestamp_local"].iloc[-1]),
            "weekday": int(part["weekday_local"].iloc[0]),
            "samples": int(len(part)),
            "minutes": round((float(part["epoch_s"].iloc[-1] - part["epoch_s"].iloc[0]) + interval) / 60.0, 1),
            "gateway_unreachable": bool((part["gateway_packet_loss_pct"] == 100).all()),
            "gateway_loss_sample_before": float(df["gateway_packet_loss_pct"].iloc[a - 1]) if a > 0 else None,
        })
    return out


def latency_episodes(df: pd.DataFrame, thresholds: dict) -> list[dict]:
    """High-latency episodes with a diagnosis from the probe that moved."""
    out = []
    for run_id, part in df.groupby("run_id", sort=False):
        part = part.reset_index(drop=True)
        rtt_floor = float(part["rtt_min_ms"].median())
        tcp_floor = float(part["tcp_connect_min_ms"].median())
        gw_thr = (thresholds.get(run_id, {}).get("gateway_rtt_avg_ms") or {}).get("threshold")
        for a, b in runs_of(part["label_high_latency"].values == 1, MERGE_GAP):
            ep = part.iloc[a:b + 1]
            minutes = (float(ep["epoch_s"].iloc[-1] - ep["epoch_s"].iloc[0])) / 60.0
            if minutes < MIN_EPISODE_MIN:
                continue
            rtt_min_med = float(ep["rtt_min_ms"].median())
            tcp_min_med = float(ep["tcp_connect_min_ms"].median())
            gw_med = float(ep["gateway_rtt_avg_ms"].median())
            if gw_thr is not None and gw_med > gw_thr:
                kind = "first hop"
            elif max(rtt_min_med - rtt_floor, tcp_min_med - tcp_floor) >= FLOOR_SHIFT_MS:
                kind = "path change"
            else:
                kind = "queueing"
            out.append({
                "run_id": run_id,
                "start_local": str(ep["timestamp_local"].iloc[0]),
                "end_local": str(ep["timestamp_local"].iloc[-1]),
                "weekday": int(ep["weekday_local"].iloc[0]),
                "hours": round(minutes / 60.0, 2),
                "samples": int(len(ep)),
                "labelled_samples": int(ep["label_high_latency"].sum()),
                "rtt_min_median": round(rtt_min_med, 2),
                "rtt_avg_median": q(ep["rtt_avg_ms"], 0.5),
                "tcp_min_median": round(tcp_min_med, 2),
                "gateway_median": round(gw_med, 2),
                "jitter_median": q(ep["jitter_ms"], 0.5),
                "dns_cached_median": q(ep["dns_resolve_ms"], 0.5),
                "http_median": q(ep["http_ms"], 0.5),
                "loss_share_pct": share(ep["packet_loss_pct"] > 0),
                "run_rtt_min_median": round(rtt_floor, 2),
                "run_tcp_min_median": round(tcp_floor, 2),
                "kind": kind,
            })
    return out


def loss_analysis(df: pd.DataFrame) -> dict:
    ok = df[df["label_outage"] == 0]
    received = ok["packets_received"].dropna().astype(int)
    classes = {
        "no_loss": ok[ok["packet_loss_pct"] == 0],
        "one_lost": ok[ok["packets_received"] == ok["packets_sent"] - 1],
        "two_plus_lost": ok[(ok["packets_received"] <= ok["packets_sent"] - 2) & (ok["packets_received"] > 0)],
    }
    conditional = {}
    for name, part in classes.items():
        conditional[name] = {
            "n": int(len(part)),
            "rtt_median": q(part["rtt_avg_ms"], 0.5),
            "jitter_median": q(part["jitter_ms"], 0.5),
            "tcp_median": q(part["tcp_connect_median_ms"], 0.5),
            "tcp_p95": q(part["tcp_connect_median_ms"], 0.95),
            "tcp_missing_pct": share(part["tcp_connect_median_ms"].isna()),
            "http_median": q(part["http_ms"], 0.5),
            "http_p95": q(part["http_ms"], 0.95),
            "http_over_1s_pct": share(part["http_ms"].dropna() > 1000),
            "dns_recursive_over_200ms_pct": share(part["dns_recursive_ms"].dropna() > 200),
            "gateway_loss_pct": share(part["gateway_packet_loss_pct"] > 0),
        }
    indicator = (df["packet_loss_pct"] > 0).astype(int)
    p_loss = float(indicator.mean())
    p_cond = float(indicator[1:][indicator[:-1].values == 1].mean())
    return {
        "samples_with_loss_pct": share(df["packet_loss_pct"] > 0),
        "mean_loss_pct": round(float(df["packet_loss_pct"].mean()), 3),
        "packets_received_counts": {int(k): int(v) for k, v in received.value_counts().sort_index().items()},
        "gateway_loss_samples_pct": share(df["gateway_packet_loss_pct"] > 0),
        "gateway_loss_outside_outages_samples": int((ok["gateway_packet_loss_pct"] > 0).sum()),
        "persistence": {"p_loss": round(p_loss, 4), "p_loss_given_previous_loss": round(p_cond, 4),
                        "ratio": round(p_cond / p_loss, 2) if p_loss else None},
        "conditional": conditional,
    }


def tcp_icmp(df: pd.DataFrame) -> dict:
    gap = df["tcp_connect_min_ms"] - df["rtt_min_ms"]
    hourly = (gap > TCP_SPLIT_MS).astype(float).groupby(df["ts_local"].dt.floor("h")).mean()
    split, before, after = change_point(hourly)
    sec = df[df["ts_local"] >= split]
    v = sec["tcp_connect_min_ms"].dropna()
    return {
        "tcp_min_minus_icmp_min_median_ms": round(float(gap.median()), 3),
        "tcp_median_minus_icmp_avg_median_ms": round(float((df["tcp_connect_median_ms"] - df["rtt_avg_ms"]).median()), 3),
        "split_local": split.strftime("%Y-%m-%dT%H:%M"),
        "long_path_share_before_pct": round(100 * before, 2),
        "long_path_share_after_pct": round(100 * after, 2),
        "after_split": {
            "tcp_min_p25": q(v, 0.25), "tcp_min_median": q(v, 0.5), "tcp_min_p75": q(v, 0.75),
            "icmp_min_median": q(sec["rtt_min_ms"], 0.5), "icmp_avg_median": q(sec["rtt_avg_ms"], 0.5),
            "share_tcp_min_over_17ms_pct": share(v > 17),
        },
        "before_split": {
            "tcp_min_median": q(df.loc[df["ts_local"] < split, "tcp_connect_min_ms"], 0.5),
            "icmp_min_median": q(df.loc[df["ts_local"] < split, "rtt_min_ms"], 0.5),
        },
    }


def dns_http(df: pd.DataFrame) -> dict:
    c, r, h = df["dns_resolve_ms"].dropna(), df["dns_recursive_ms"].dropna(), df["http_ms"].dropna()
    return {
        "dns_cached": {"median": q(c, 0.5), "p05": q(c, 0.05), "p95": q(c, 0.95),
                       "share_under_15ms_pct": share(c < 15), "share_under_5ms_pct": share(c < 5)},
        "dns_recursive": {"median": q(r, 0.5), "p95": q(r, 0.95), "p99": q(r, 0.99),
                          "share_over_200ms_pct": share(r > 200)},
        "dns_recursive_minus_cached_median_ms": round(float((df["dns_recursive_ms"] - df["dns_resolve_ms"]).median()), 2),
        "dns_timeouts": int(df["notes"].fillna("").astype(str).str.contains("dns: timeout|dns recursive: timeout").sum()),
        "dns_timeouts_during_outages": int((df["notes"].fillna("").astype(str).str.contains("dns: timeout|dns recursive: timeout")
                                            & (df["label_outage"] == 1)).sum()),
        "http": {"median": q(h, 0.5), "p90": q(h, 0.9), "p95": q(h, 0.95), "p99": q(h, 0.99),
                 "share_1000_1400ms_pct": share((h >= 1000) & (h < 1400)),
                 "share_3900_4400ms_pct": share((h >= 3900) & (h < 4400)),
                 "share_over_1s_pct": share(h > 1000), "share_over_5s_pct": share(h > 5000),
                 "timeouts": int(df["notes"].fillna("").astype(str).str.contains("http: timed out").sum()),
                 "status_counts": {str(int(k)): int(v) for k, v in df["http_status"].dropna().value_counts().items()}},
    }


def autocorrelation(df: pd.DataFrame) -> dict:
    interval = float(df["interval_s"].median())
    lags = [max(1, int(round(m * 60 / interval))) for m in ACF_LAGS_MIN]
    series = {
        "rtt_avg_ms": df["rtt_avg_ms"],
        "log_rtt_avg_ms": np.log(df["rtt_avg_ms"]),
        "jitter_ms": df["jitter_ms"],
        "loss_indicator": (df["packet_loss_pct"] > 0).astype(float),
        "tcp_connect_min_ms": df["tcp_connect_min_ms"],
        "dns_recursive_ms": df["dns_recursive_ms"],
        "http_ms": df["http_ms"],
    }
    return {"lag_minutes": ACF_LAGS_MIN, "lag_samples": lags,
            "values": {name: [acf(s, l) for l in lags] for name, s in series.items()}}


def speedtest(df: pd.DataFrame) -> dict:
    sp = df[df["download_mbps"].notna()].copy()
    if sp.empty:
        return {"tests": 0}
    failed_ping = sp["speedtest_ping_ms"].isna() | (sp["speedtest_ping_ms"] >= SPEEDTEST_PING_FAILED_MS)
    top = (sp.groupby("speedtest_server")
             .agg(n=("download_mbps", "size"), download_median=("download_mbps", "median"),
                  download_max=("download_mbps", "max"), upload_median=("upload_mbps", "median"),
                  ping_median=("speedtest_ping_ms", "median"))
             .sort_values("n", ascending=False).head(10))
    return {
        "tests": int(len(sp)),
        "servers": int(sp["speedtest_server"].nunique()),
        "failed_latency_probe": int(failed_ping.sum()),
        "zero_download": int((sp["download_mbps"] == 0).sum()),
        "zero_upload": int((sp["upload_mbps"] == 0).sum()),
        "download": describe(sp["download_mbps"]),
        "upload": describe(sp["upload_mbps"]),
        "download_quantiles": {str(p): q(sp["download_mbps"], p) for p in (0.1, 0.25, 0.5, 0.75, 0.9)},
        "upload_quantiles": {str(p): q(sp["upload_mbps"], p) for p in (0.1, 0.25, 0.5, 0.75, 0.9)},
        "ping_valid": describe(sp.loc[~failed_ping, "speedtest_ping_ms"]),
        "download_median_night_0_4": q(sp.loc[sp["hour_local"].between(0, 4), "download_mbps"], 0.5),
        "download_median_day_9_16": q(sp.loc[sp["hour_local"].between(9, 16), "download_mbps"], 0.5),
        "sample_duration_median_s": q(sp["sample_duration_s"], 0.5),
        "top_servers": [{"server": str(k), **{c: (int(v) if c == "n" else round(float(v), 1)) for c, v in row.items()}}
                        for k, row in top.iterrows()],
    }


def labels(df: pd.DataFrame) -> dict:
    M = df[LABELS[:-1]]
    return {
        "counts": {c: int(df[c].sum()) for c in LABELS},
        "share_pct": {c: share(df[c] == 1) for c in LABELS},
        "exactly_one_label": int((M.sum(axis=1) == 1).sum()),
        "two_or_more_labels": int((M.sum(axis=1) >= 2).sum()),
        "high_latency_in_episodes_pct": None,
    }


# --------------------------------------------------------------------------- #
# Report
# --------------------------------------------------------------------------- #

def analyse(df: pd.DataFrame, summary: dict | None) -> dict:
    thresholds = {r["run_id"]: r.get("thresholds", {}) for r in (summary or {}).get("runs", [])}
    eps = latency_episodes(df, thresholds)
    result = {
        "generated_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "overview": overview(df, summary),
        "hour_profile": hour_profile(df),
        "weekday_weekend": weekday_weekend(df),
        "daily": daily(df),
        "regimes": regimes(df),
        "outages": outages(df),
        "latency_episodes": eps,
        "loss": loss_analysis(df),
        "tcp_icmp": tcp_icmp(df),
        "dns_http": dns_http(df),
        "autocorrelation": autocorrelation(df),
        "speedtest": speedtest(df),
        "labels": labels(df),
    }
    total = int(df["label_high_latency"].sum())
    inside = sum(e["labelled_samples"] for e in eps)
    result["labels"]["high_latency_in_episodes_pct"] = round(100.0 * inside / total, 1) if total else None
    result["labels"]["episode_kinds"] = {k: sum(1 for e in eps if e["kind"] == k)
                                         for k in ("path change", "first hop", "queueing")}
    return result


def print_report(r: dict) -> None:
    o = r["overview"]
    print(f"rows {o['rows']:,}  runs {o['runs']}  span {o['span_days']} days  "
          f"{o['first_utc']} -> {o['last_utc']}  coverage {o['coverage_pct']} %")
    print()
    print(f"{'metric':<24}{'n':>8}{'median':>10}{'mean':>10}{'p95':>10}{'p99':>10}{'max':>12}")
    for col, d in o["metrics"].items():
        print(f"{col:<24}{d['n']:>8}{d['median']:>10.2f}{d['mean']:>10.2f}{d['p95']:>10.2f}"
              f"{d['p99']:>10.2f}{d['max']:>12.2f}")
    g = r["regimes"]
    print()
    print(f"regimes (split {g['split_date']}):")
    for name in ("before", "after"):
        p = g[name]
        print(f"  {name:<7}{p['first_date']} .. {p['last_date']}  n={p['n']:,}  rtt med {p['rtt_median']}  "
              f"p95 {p['rtt_p95']}  loss {p['loss_share_pct']} %  jitter med {p['jitter_median']}  "
              f"http>1s {p['http_over_1s_pct']} %  anomaly {p['labels_pct']['label_anomaly']} %")
    print()
    print(f"outages: {len(r['outages'])}")
    for e in r["outages"]:
        print(f"  {e['start_local'][:16]}  {e['minutes']} min  {e['samples']} samples  "
              f"gateway unreachable: {e['gateway_unreachable']}")
    print()
    print(f"high-latency episodes >= {MIN_EPISODE_MIN} min: {len(r['latency_episodes'])}")
    for e in r["latency_episodes"]:
        print(f"  {e['start_local'][:16]} -> {e['end_local'][11:16]}  {e['hours']:>5.2f} h  "
              f"rtt min {e['rtt_min_median']:>5.1f}  avg {e['rtt_avg_median']:>5.1f}  tcp min {e['tcp_min_median']:>5.1f}  "
              f"gw {e['gateway_median']:>5.1f}  {e['kind']}")
    t = r["tcp_icmp"]
    print()
    print(f"tcp/icmp: floor offset {t['tcp_min_minus_icmp_min_median_ms']} ms; long-path share "
          f"{t['long_path_share_before_pct']} % -> {t['long_path_share_after_pct']} % from {t['split_local']}")
    L = r["loss"]
    print(f"loss: {L['samples_with_loss_pct']} % of samples; P(loss|loss) = {L['persistence']['p_loss_given_previous_loss']}"
          f" vs {L['persistence']['p_loss']}")
    for name, c in L["conditional"].items():
        print(f"  {name:<14}n={c['n']:>6}  jitter med {c['jitter_median']:.2f}  tcp med {c['tcp_median']:.2f}  "
              f"http>1s {c['http_over_1s_pct']:.2f} %  dns rec>200ms {c['dns_recursive_over_200ms_pct']:.2f} %")
    s = r["speedtest"]
    if s.get("tests"):
        print(f"speed tests: {s['tests']} on {s['servers']} servers, {s['failed_latency_probe']} failed latency probes, "
              f"{s['zero_download']} zero downloads; download median {s['download']['median']} max {s['download']['max']}; "
              f"upload median {s['upload']['median']} p95 {s['upload']['p95']}")
    print()
    print("labels:")
    for col, n in r["labels"]["counts"].items():
        print(f"  {col:<24}{n:>8}  ({r['labels']['share_pct'][col]} %)")
    print()
    print("hour-of-day (local):")
    print(f"{'hour':>4}{'n':>7}{'rtt med':>9}{'rtt p95':>9}{'loss %':>8}{'wkday %':>9}{'wkend %':>9}{'http>1s %':>11}{'hi-lat %':>10}")
    for h in r["hour_profile"]:
        print(f"{h['hour']:>4}{h['n']:>7}{h['rtt_median']:>9.2f}{h['rtt_p95']:>9.2f}{h['loss_share_pct']:>8.2f}"
              f"{h['loss_share_weekday_pct']:>9.2f}{h['loss_share_weekend_pct']:>9.2f}{h['http_over_1s_pct']:>11.2f}"
              f"{h['high_latency_pct']:>10.2f}")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Statistics and findings for the consolidated dataset.")
    p.add_argument("--input", default=None,
                   help="consolidated parquet or CSV (default: dataset/localnet_dynamics.parquet)")
    p.add_argument("--summary", default=None, help="summary.json written by build_dataset.py")
    p.add_argument("--output", default=None, help="analysis JSON (default: dataset/analysis.json)")
    p.add_argument("--no-report", action="store_true", help="write the JSON without printing the report")
    args = p.parse_args(argv)

    path = Path(args.input) if args.input else ROOT / "dataset" / "localnet_dynamics.parquet"
    if not path.exists() and not args.input:
        path = ROOT / "dataset" / "localnet_dynamics.csv"
    if not path.exists():
        raise SystemExit(f"{path} not found; run build_dataset.py first")
    summary_path = Path(args.summary) if args.summary else path.parent / "summary.json"
    summary = json.loads(summary_path.read_text(encoding="utf-8")) if summary_path.exists() else None

    df = load(path)
    result = analyse(df, summary)
    out = Path(args.output) if args.output else path.parent / "analysis.json"
    out.write_text(json.dumps(result, indent=2), encoding="utf-8")
    if not args.no_report:
        print_report(result)
        print()
    print(f"analysis written to {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
