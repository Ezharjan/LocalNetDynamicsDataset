#!/usr/bin/env python3
"""
Summarise the consolidated LocalNet-Dynamics dataset and render figures.

Reads dataset/localnet_dynamics.csv (or .parquet), prints a text report
(overall statistics, hour-of-day profile, label counts) and writes PNG figures
to the figures/ directory:

    rtt_timeline.png        target RTT, gateway RTT and packet loss over time
    diurnal_profile.png     median / p95 RTT and loss share by local hour
    diurnal_heatmap.png     median RTT per day and hour
    distributions.png       distributions of the latency metrics
    throughput.png          WAN speed tests over time (when present)
    wifi_signal_vs_rtt.png  Wi-Fi RSSI against target RTT (when present)

    python analyze.py
    python analyze.py --input dataset/localnet_dynamics.parquet --out-dir figures
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent

BLUE, ORANGE, AQUA, RED, GRAY = "#2a78d6", "#eb6834", "#1baf7a", "#e34948", "#8a8985"
BLUES = ["#cde2fb", "#9ec5f4", "#6da7ec", "#3987e5", "#256abf", "#184f95", "#0d366b"]


def load(path: Path) -> pd.DataFrame:
    if path.suffix == ".parquet":
        df = pd.read_parquet(path)
    else:
        df = pd.read_csv(path, low_memory=False)
    df["ts"] = pd.to_datetime(df["timestamp_utc"], utc=True)
    local = pd.to_datetime(df["timestamp_local"].astype(str).str[:19], format="%Y-%m-%dT%H:%M:%S", errors="coerce")
    df["ts_local"] = local
    return df.sort_values("ts").reset_index(drop=True)


def describe(series: pd.Series) -> dict:
    v = series.dropna().astype(float)
    if v.empty:
        return {}
    return {"n": int(v.size), "median": v.median(), "mean": v.mean(),
            "p95": v.quantile(0.95), "p99": v.quantile(0.99), "max": v.max()}


def text_report(df: pd.DataFrame) -> None:
    span = df["ts"].max() - df["ts"].min()
    print(f"rows: {len(df):,}   runs: {df['run_id'].nunique()}   "
          f"span: {span.total_seconds() / 86400:.1f} days   "
          f"{df['ts'].min():%Y-%m-%d %H:%M} -> {df['ts'].max():%Y-%m-%d %H:%M} UTC")
    print("link types: " + ", ".join(f"{k} ({int(v)})" for k, v in df["link_type"].value_counts().items()))
    print()
    print(f"{'metric':<28}{'n':>8}{'median':>10}{'mean':>10}{'p95':>10}{'p99':>10}{'max':>10}")
    for col in ["rtt_avg_ms", "rtt_min_ms", "jitter_ms", "packet_loss_pct", "gateway_rtt_avg_ms",
                "tcp_connect_median_ms", "dns_resolve_ms", "dns_recursive_ms", "http_ms",
                "wifi_rssi_dbm", "download_mbps", "upload_mbps", "download_loaded_latency_ms"]:
        if col not in df.columns:
            continue
        d = describe(df[col])
        if d:
            print(f"{col:<28}{d['n']:>8}{d['median']:>10.2f}{d['mean']:>10.2f}"
                  f"{d['p95']:>10.2f}{d['p99']:>10.2f}{d['max']:>10.2f}")
    labels = [c for c in df.columns if c.startswith("label_")]
    if labels:
        print()
        print("reference labels:")
        for col in labels:
            n = int(df[col].fillna(0).sum())
            print(f"  {col:<26}{n:>8}  ({100.0 * n / len(df):.2f} %)")
    print()
    print("hour-of-day profile (local time):")
    print(f"{'hour':>4}{'n':>8}{'rtt med':>10}{'rtt p95':>10}{'jitter':>10}{'loss>0 %':>10}{'gw med':>10}")
    by_hour = df.groupby("hour_local")
    for hour, part in by_hour:
        loss_share = 100.0 * (part["packet_loss_pct"] > 0).mean()
        print(f"{int(hour):>4}{len(part):>8}{part['rtt_avg_ms'].median():>10.2f}"
              f"{part['rtt_avg_ms'].quantile(0.95):>10.2f}{part['jitter_ms'].median():>10.2f}"
              f"{loss_share:>10.2f}{part['gateway_rtt_avg_ms'].median():>10.2f}")


def style(plt) -> None:
    plt.rcParams.update({
        "figure.facecolor": "white", "axes.facecolor": "white",
        "axes.edgecolor": "#c9c8c3", "axes.grid": True, "grid.color": "#e8e7e3",
        "grid.linewidth": 0.6, "axes.axisbelow": True,
        "axes.spines.top": False, "axes.spines.right": False,
        "axes.titlesize": 11, "axes.titleweight": "semibold", "axes.labelsize": 9,
        "xtick.labelsize": 8, "ytick.labelsize": 8, "legend.fontsize": 8,
        "legend.frameon": False, "font.family": "DejaVu Sans", "lines.linewidth": 1.2,
    })


def fig_timeline(df: pd.DataFrame, out: Path, plt) -> None:
    fig, axes = plt.subplots(3, 1, figsize=(12, 7.5), sharex=True,
                             gridspec_kw={"height_ratios": [3, 2, 1.2]})
    x = df["ts"]
    axes[0].plot(x, df["rtt_avg_ms"], color=BLUE, linewidth=0.8, label="target RTT (avg)")
    axes[0].plot(x, df["rtt_min_ms"], color=ORANGE, linewidth=0.6, alpha=0.8, label="target RTT (min)")
    axes[0].set_ylabel("ms")
    axes[0].set_title(f"Round-trip time to {df['target'].iloc[0]}")
    axes[0].legend(loc="upper left", ncol=2)
    axes[1].plot(x, df["gateway_rtt_avg_ms"], color=AQUA, linewidth=0.8)
    axes[1].set_ylabel("ms")
    axes[1].set_title("Round-trip time to the default gateway")
    axes[2].bar(x, df["packet_loss_pct"].fillna(0), width=pd.Timedelta(seconds=float(df["interval_s"].median() or 60)),
                color=RED, linewidth=0)
    axes[2].set_ylabel("%")
    axes[2].set_ylim(0, 100)
    axes[2].set_title("Packet loss per sample")
    fig.autofmt_xdate()
    fig.tight_layout()
    fig.savefig(out / "rtt_timeline.png", dpi=150)
    plt.close(fig)


def fig_diurnal_profile(df: pd.DataFrame, out: Path, plt) -> None:
    g = df.groupby("hour_local")
    hours = np.arange(24, dtype=float)
    med = g["rtt_avg_ms"].median().reindex(range(24)).values
    p25 = g["rtt_avg_ms"].quantile(0.25).reindex(range(24)).values
    p75 = g["rtt_avg_ms"].quantile(0.75).reindex(range(24)).values
    p95 = g["rtt_avg_ms"].quantile(0.95).reindex(range(24)).values
    loss_share = g["packet_loss_pct"].apply(lambda s: 100.0 * (s > 0).mean()).reindex(range(24)).values
    fig, axes = plt.subplots(2, 1, figsize=(10, 6.5), sharex=True, gridspec_kw={"height_ratios": [3, 1.6]})
    axes[0].fill_between(hours, p25, p75, color=BLUE, alpha=0.18, linewidth=0, label="interquartile range")
    axes[0].plot(hours, med, color=BLUE, marker="o", markersize=3.5, label="median")
    axes[0].plot(hours, p95, color=ORANGE, marker="o", markersize=3.5, label="95th percentile")
    axes[0].set_ylabel("target RTT (ms)")
    axes[0].set_title("Latency by local hour of day")
    axes[0].legend(loc="upper left", ncol=3)
    axes[1].bar(hours, loss_share, color=RED, width=0.7, linewidth=0)
    axes[1].set_ylabel("samples with loss (%)")
    axes[1].set_xlabel("local hour")
    axes[1].set_xticks(range(0, 24))
    fig.tight_layout()
    fig.savefig(out / "diurnal_profile.png", dpi=150)
    plt.close(fig)


def fig_heatmap(df: pd.DataFrame, out: Path, plt) -> None:
    from matplotlib.colors import LinearSegmentedColormap
    pivot = df.pivot_table(index="date_local", columns="hour_local", values="rtt_avg_ms", aggfunc="median")
    pivot = pivot.reindex(columns=range(24))
    cmap = LinearSegmentedColormap.from_list("blues", BLUES)
    cmap.set_bad("#f0efec")
    height = max(3.0, 0.28 * len(pivot) + 1.6)
    fig, ax = plt.subplots(figsize=(11, height))
    vmax = np.nanpercentile(pivot.values, 98) if np.isfinite(pivot.values).any() else None
    im = ax.imshow(np.ma.masked_invalid(pivot.values), aspect="auto", cmap=cmap,
                   vmin=np.nanmin(pivot.values), vmax=vmax, interpolation="nearest")
    ax.set_xticks(range(24))
    ax.set_yticks(range(len(pivot)))
    ax.set_yticklabels(pivot.index, fontsize=7)
    ax.set_xlabel("local hour")
    ax.set_title("Median target RTT per day and hour (ms)")
    ax.grid(False)
    cbar = fig.colorbar(im, ax=ax, fraction=0.025, pad=0.02)
    cbar.set_label("ms")
    fig.tight_layout()
    fig.savefig(out / "diurnal_heatmap.png", dpi=150)
    plt.close(fig)


def fig_distributions(df: pd.DataFrame, out: Path, plt) -> None:
    cols = [("rtt_avg_ms", "target RTT avg (ms)"), ("jitter_ms", "jitter (ms)"),
            ("gateway_rtt_avg_ms", "gateway RTT avg (ms)"), ("tcp_connect_median_ms", "TCP connect (ms)"),
            ("dns_resolve_ms", "DNS, cached name (ms)"), ("dns_recursive_ms", "DNS, recursive (ms)")]
    cols = [(c, t) for c, t in cols if c in df.columns and df[c].notna().any()]
    if not cols:
        return
    n = len(cols)
    fig, axes = plt.subplots(2, (n + 1) // 2, figsize=(12, 6))
    axes = np.array(axes).reshape(-1)
    for ax, (col, title) in zip(axes, cols):
        v = df[col].dropna().astype(float)
        upper = v.quantile(0.99)
        ax.hist(v.clip(upper=upper), bins=60, color=BLUE, linewidth=0)
        ax.axvline(v.median(), color=ORANGE, linewidth=1.2)
        ax.text(v.median(), ax.get_ylim()[1] * 0.95, f" median {v.median():.1f}", color="#52514e",
                fontsize=8, va="top")
        ax.set_title(title)
        ax.set_ylabel("samples")
    for ax in axes[len(cols):]:
        ax.axis("off")
    fig.suptitle("Distributions (clipped at the 99th percentile)", fontsize=11, fontweight="semibold")
    fig.tight_layout()
    fig.savefig(out / "distributions.png", dpi=150)
    plt.close(fig)


def fig_throughput(df: pd.DataFrame, out: Path, plt) -> None:
    part = df[df["download_mbps"].notna()]
    if part.empty:
        return
    fig, axes = plt.subplots(2, 1, figsize=(11, 6), sharex=True)
    axes[0].plot(part["ts"], part["download_mbps"], color=BLUE, marker="o", markersize=3, linewidth=0.8)
    axes[0].set_ylabel("Mbit/s")
    axes[0].set_title("WAN download throughput (speed test)")
    axes[1].plot(part["ts"], part["upload_mbps"], color=ORANGE, marker="o", markersize=3, linewidth=0.8)
    axes[1].set_ylabel("Mbit/s")
    axes[1].set_title("WAN upload throughput (speed test)")
    fig.autofmt_xdate()
    fig.tight_layout()
    fig.savefig(out / "throughput.png", dpi=150)
    plt.close(fig)


def fig_wifi(df: pd.DataFrame, out: Path, plt) -> None:
    if "wifi_rssi_dbm" not in df.columns:
        return
    part = df[df["wifi_rssi_dbm"].notna() & df["rtt_avg_ms"].notna()]
    if len(part) < 20:
        return
    fig, axes = plt.subplots(1, 2, figsize=(12, 4.5))
    axes[0].scatter(part["wifi_rssi_dbm"], part["rtt_avg_ms"], s=6, color=BLUE, alpha=0.25, linewidth=0)
    axes[0].set_xlabel("Wi-Fi RSSI (dBm)")
    axes[0].set_ylabel("target RTT avg (ms)")
    axes[0].set_ylim(0, part["rtt_avg_ms"].quantile(0.99) * 1.1)
    axes[0].set_title("Signal strength against latency")
    axes[1].scatter(part["wifi_rssi_dbm"], part["gateway_rtt_avg_ms"], s=6, color=AQUA, alpha=0.25, linewidth=0)
    axes[1].set_xlabel("Wi-Fi RSSI (dBm)")
    axes[1].set_ylabel("gateway RTT avg (ms)")
    axes[1].set_ylim(0, max(1.0, part["gateway_rtt_avg_ms"].quantile(0.99) * 1.1))
    axes[1].set_title("Signal strength against first-hop latency")
    fig.tight_layout()
    fig.savefig(out / "wifi_signal_vs_rtt.png", dpi=150)
    plt.close(fig)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Report and figures for the consolidated dataset.")
    p.add_argument("--input", default=None,
                   help="consolidated CSV or parquet (default: dataset/localnet_dynamics.csv)")
    p.add_argument("--out-dir", default=str(ROOT / "figures"))
    p.add_argument("--no-figures", action="store_true", help="print the text report only")
    args = p.parse_args(argv)

    path = Path(args.input) if args.input else ROOT / "dataset" / "localnet_dynamics.csv"
    if not path.exists():
        raise SystemExit(f"{path} not found; run build_dataset.py first")
    df = load(path)
    text_report(df)
    if args.no_figures:
        return 0

    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    style(plt)
    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    fig_timeline(df, out, plt)
    fig_diurnal_profile(df, out, plt)
    fig_heatmap(df, out, plt)
    fig_distributions(df, out, plt)
    fig_throughput(df, out, plt)
    fig_wifi(df, out, plt)
    print(f"\nfigures written to {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
