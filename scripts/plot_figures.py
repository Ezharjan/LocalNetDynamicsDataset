#!/usr/bin/env python3
"""
Render the LocalNet-Dynamics figures as PNG and PDF.

Reads the consolidated dataset and dataset/analysis.json (written by
analyze.py) and writes every figure twice, figures/png/<name>.png and
figures/pdf/<name>.pdf, cropped to the drawn area with no margin:

    overview_timeline     the whole capture: target RTT, TCP connect, first hop, loss
    diurnal_profile       hour-of-day latency, loss (weekday/weekend) and tail shares
    calendar_heatmaps     day-by-hour loss share, high-latency share, HTTP > 1 s share
    degradation_anatomy   a path change next to a first-hop event, probe by probe
    tcp_vs_icmp           the TCP/ICMP floor split that ping cannot see
    loss_anatomy          what a lost echo means for the other probes
    regimes               distributions before and after the change point
    autocorrelation       persistence of every metric up to a one-day lag
    speedtest             WAN throughput by time and by server
    labels                reference-label shares in the two regimes

    python plot_figures.py
    python plot_figures.py --input dataset/localnet_dynamics.parquet --out-dir figures
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from analyze import ROOT, analyse, load  # noqa: E402

BLUE, ORANGE, AQUA, YELLOW = "#2a78d6", "#eb6834", "#1baf7a", "#eda100"
MAGENTA, GREEN, VIOLET, RED = "#e87ba4", "#008300", "#6250d6", "#e34948"
INK, INK2, MUTED, GRID, AXIS = "#0b0b0b", "#52514e", "#898781", "#e1e0d9", "#c3c2b7"
BLUES = ["#f3f7fd", "#cde2fb", "#9ec5f4", "#6da7ec", "#3987e5", "#256abf", "#184f95", "#0d366b"]
NAVY = "#0d366b"
LOSS_CLASSES = [("no_loss", "no loss"), ("one_lost", "1 of 10 lost"), ("two_plus_lost", "2+ of 10 lost")]
WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


def style(plt) -> None:
    plt.rcParams.update({
        "figure.facecolor": "white", "axes.facecolor": "white", "savefig.facecolor": "white",
        "font.family": "sans-serif",
        "font.sans-serif": ["Inter", "Segoe UI", "Helvetica Neue", "Arial", "DejaVu Sans"],
        "font.size": 15, "axes.titlesize": 18, "axes.titleweight": "semibold", "axes.titlepad": 14,
        "axes.labelsize": 15, "axes.labelcolor": INK2, "xtick.labelsize": 13, "ytick.labelsize": 13,
        "xtick.color": INK2, "ytick.color": INK2, "legend.fontsize": 13, "legend.frameon": False,
        "axes.edgecolor": AXIS, "axes.linewidth": 1.0, "axes.grid": True, "grid.color": GRID,
        "grid.linewidth": 0.8, "axes.axisbelow": True, "axes.spines.top": False, "axes.spines.right": False,
        "lines.linewidth": 2.0, "xtick.major.size": 0, "ytick.major.size": 0,
        "xtick.major.pad": 8, "ytick.major.pad": 8, "legend.handlelength": 1.6,
    })


def save(fig, out: Path, name: str) -> None:
    (out / "png").mkdir(parents=True, exist_ok=True)
    (out / "pdf").mkdir(parents=True, exist_ok=True)
    fig.savefig(out / "png" / f"{name}.png", dpi=200, bbox_inches="tight", pad_inches=0)
    fig.savefig(out / "pdf" / f"{name}.pdf", bbox_inches="tight", pad_inches=0,
                metadata={"CreationDate": None, "ModDate": None})


def month_day(ts) -> str:
    return f"{ts:%b %-d}" if sys.platform != "win32" else f"{ts:%b %d}".replace(" 0", " ")


def period_label(g: dict, key: str) -> str:
    first, last = pd.Timestamp(g[key]["first_date"]), pd.Timestamp(g[key]["last_date"])
    return f"{month_day(first)} to {month_day(last)}"


def date_axis(ax, mdates) -> None:
    ax.xaxis.set_major_locator(mdates.DayLocator(interval=2))
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%b %d"))
    ax.xaxis.set_minor_locator(mdates.DayLocator())
    for label in ax.get_xticklabels():
        label.set_rotation(0)


def shade_episodes(ax, a: dict, labels: bool = False) -> list:
    handles = []
    kinds = {"path change": (BLUE, 0.14, "path change (latency floor moved)"),
             "first hop": (AQUA, 0.22, "first-hop degradation"),
             "queueing": (YELLOW, 0.22, "queueing")}
    seen = set()
    for e in a["latency_episodes"]:
        color, alpha, text = kinds[e["kind"]]
        s, t = pd.Timestamp(e["start_local"][:19]), pd.Timestamp(e["end_local"][:19])
        ax.axvspan(s, t, color=color, alpha=alpha, linewidth=0, zorder=0)
        if e["kind"] not in seen:
            seen.add(e["kind"])
            handles.append((color, alpha, text))
    for o in a["outages"]:
        ax.axvline(pd.Timestamp(o["start_local"][:19]), color=RED, linewidth=2.2, alpha=0.9, zorder=1)
    split = pd.Timestamp(a["regimes"]["split_date"])
    ax.axvline(split, color=INK2, linewidth=1.6, linestyle=(0, (5, 4)), zorder=1)
    return handles


# --------------------------------------------------------------------------- #
# Figures
# --------------------------------------------------------------------------- #

def fig_overview(df, a, out, plt, mdates):
    from matplotlib.lines import Line2D
    from matplotlib.patches import Patch
    x = df["ts_local"]
    fig, axes = plt.subplots(4, 1, figsize=(18, 13), sharex=True,
                             gridspec_kw={"height_ratios": [3, 2.2, 1.6, 1.6], "hspace": 0.32})
    rtt_cap = 40
    ax = axes[0]
    handles = shade_episodes(ax, a)
    ax.plot(x, df["rtt_avg_ms"].clip(upper=rtt_cap), color=BLUE, linewidth=0.5, alpha=0.45)
    roll = df["rtt_avg_ms"].rolling(60, center=True, min_periods=20).median()
    ax.plot(x, roll, color=NAVY, linewidth=1.8)
    ax.set_ylim(0, rtt_cap)
    ax.set_ylabel("ms")
    ax.set_title(f"Round-trip time to {df['target'].iloc[0]} (ICMP, 10-echo mean per sample, clipped at {rtt_cap} ms)",
                 loc="left")
    series = [Line2D([], [], color=BLUE, linewidth=1.2, alpha=0.7, label="per sample"),
              Line2D([], [], color=NAVY, linewidth=2.2, label="30-minute rolling median")]
    bands = [Patch(facecolor=c, alpha=min(1.0, al * 2.2), label=t) for c, al, t in handles]
    bands += [Line2D([], [], color=RED, linewidth=2.2, label="outage (gateway unreachable)"),
              Line2D([], [], color=INK2, linewidth=1.6, linestyle=(0, (5, 4)), label="change point in daily loss share")]
    fig.legend(handles=series + bands, loc="lower center", bbox_to_anchor=(0.5, 0.925), ncol=6,
               frameon=False, columnspacing=1.6, handletextpad=0.6)

    ax = axes[1]
    shade_episodes(ax, a)
    ax.scatter(x, df["tcp_connect_min_ms"].clip(upper=rtt_cap), s=2.5, color=ORANGE, alpha=0.35, linewidth=0, rasterized=True)
    ax.set_ylim(0, rtt_cap)
    ax.set_ylabel("ms")
    ax.set_title("Fastest of 5 TCP handshakes to port 443 of the same host (sub-millisecond resolution)", loc="left")

    ax = axes[2]
    shade_episodes(ax, a)
    ax.plot(x, df["gateway_rtt_avg_ms"].clip(upper=20), color=AQUA, linewidth=0.7)
    ax.set_ylim(0, 20)
    ax.set_ylabel("ms")
    ax.set_title("Round-trip time to the default gateway (first hop, clipped at 20 ms)", loc="left")

    ax = axes[3]
    shade_episodes(ax, a)
    hourly = (df["packet_loss_pct"] > 0).groupby(df["ts_local"].dt.floor("h")).mean() * 100
    ax.bar(hourly.index, hourly.values, width=pd.Timedelta(hours=1), color=RED, linewidth=0, alpha=0.9)
    ax.set_ylim(0, 100)
    ax.set_ylabel("% of samples")
    ax.set_title("Samples with at least one lost echo, per hour", loc="left")
    ax.set_xlabel("local time (America/New_York)")
    date_axis(ax, mdates)
    ax.set_xlim(x.min() - pd.Timedelta(hours=3), x.max() + pd.Timedelta(hours=3))
    save(fig, out, "overview_timeline")
    plt.close(fig)


def fig_diurnal(df, a, out, plt):
    prof = pd.DataFrame(a["hour_profile"]).set_index("hour").reindex(range(24))
    hours = np.arange(24)
    fig, axes = plt.subplots(1, 3, figsize=(20, 6.4), gridspec_kw={"wspace": 0.28})
    ax = axes[0]
    ax.fill_between(hours, prof["rtt_p25"], prof["rtt_p75"], color=BLUE, alpha=0.16, linewidth=0, label="interquartile range")
    ax.plot(hours, prof["rtt_median"], color=BLUE, marker="o", markersize=6, label="median")
    ax.plot(hours, prof["rtt_p95"], color=NAVY, marker="o", markersize=6, label="95th percentile")
    ax.set_ylim(0, max(32, float(prof["rtt_p95"].max()) * 1.15))
    ax.set_ylabel("target RTT (ms)")
    ax.set_title("Latency by local hour", loc="left")
    ax.legend(loc="upper center", ncol=1)
    ax = axes[1]
    ax.plot(hours, prof["loss_share_weekday_pct"], color=BLUE, marker="o", markersize=6, label="weekdays")
    ax.plot(hours, prof["loss_share_weekend_pct"], color=ORANGE, marker="o", markersize=6, label="weekends")
    ax.set_ylim(0, max(30, float(prof[["loss_share_weekday_pct", "loss_share_weekend_pct"]].max().max()) * 1.2))
    ax.set_ylabel("% of samples with a lost echo")
    ax.set_title("Loss by local hour", loc="left")
    ax.legend(loc="upper left")
    ax = axes[2]
    ax.plot(hours, prof["http_over_1s_pct"], color=VIOLET, marker="o", markersize=6, label="HTTP response > 1 s")
    ax.plot(hours, prof["high_latency_pct"], color=BLUE, marker="o", markersize=6, label="high-latency label")
    ax.set_ylim(0, max(25, float(prof[["http_over_1s_pct", "high_latency_pct"]].max().max()) * 1.25))
    ax.set_ylabel("% of samples")
    ax.set_title("Two tails with opposite clocks", loc="left")
    ax.legend(loc="upper center")
    for ax in axes:
        ax.set_xticks(range(0, 24, 3))
        ax.set_xticklabels([f"{h:02d}" for h in range(0, 24, 3)])
        ax.set_xlim(-0.5, 23.5)
        ax.set_xlabel("local hour")
    save(fig, out, "diurnal_profile")
    plt.close(fig)


def fig_heatmaps(df, a, out, plt):
    from matplotlib.colors import LinearSegmentedColormap
    cmap = LinearSegmentedColormap.from_list("blues", BLUES).with_extremes(bad="#eeede9")
    panels = [
        ("packet_loss_pct", lambda s: 100 * (s > 0).mean(), "Samples with a lost echo (%)"),
        ("label_high_latency", lambda s: 100 * s.mean(), "Samples over the high-latency threshold (%)"),
        ("http_ms", lambda s: 100 * (s.dropna() > 1000).mean(), "HTTP responses slower than 1 s (%)"),
    ]
    dates = sorted(df["date_local"].unique())
    weekday = df.groupby("date_local")["weekday_local"].first()
    fig, axes = plt.subplots(1, 3, figsize=(22, 0.42 * len(dates) + 2.4), gridspec_kw={"wspace": 0.12})
    for ax, (col, fn, title) in zip(axes, panels):
        pivot = df.pivot_table(index="date_local", columns="hour_local", values=col, aggfunc=fn)
        pivot = pivot.reindex(index=dates, columns=range(24))
        vmax = max(1.0, float(np.nanpercentile(pivot.values, 98)))
        im = ax.imshow(np.ma.masked_invalid(pivot.values.astype(float)), aspect="auto", cmap=cmap,
                       vmin=0, vmax=vmax, interpolation="nearest")
        ax.set_xticks(range(0, 24, 3))
        ax.set_xticklabels([f"{h:02d}" for h in range(0, 24, 3)])
        ax.set_xlabel("local hour")
        ax.set_title(title, loc="left")
        ax.grid(False)
        for spine in ax.spines.values():
            spine.set_visible(False)
        cbar = fig.colorbar(im, ax=ax, orientation="horizontal", fraction=0.045, pad=0.14, aspect=28)
        cbar.outline.set_visible(False)
        cbar.ax.tick_params(labelsize=12)
    axes[0].set_yticks(range(len(dates)))
    axes[0].set_yticklabels([f"{WEEKDAYS[int(weekday[d])]}  {pd.Timestamp(d):%b %d}" for d in dates])
    for d, label in zip(dates, axes[0].get_yticklabels()):
        if int(weekday[d]) >= 5:
            label.set_color(ORANGE)
            label.set_fontweight("semibold")
    for ax in axes[1:]:
        ax.set_yticks([])
    save(fig, out, "calendar_heatmaps")
    plt.close(fig)


def fig_anatomy(df, a, out, plt, mdates):
    def window(kind):
        """The longest episode of this kind, extended over same-kind episodes within two hours of it."""
        eps = [e for e in a["latency_episodes"] if e["kind"] == kind]
        if not eps:
            return None
        chain = [max(eps, key=lambda e: e["hours"])]
        grown = True
        while grown:
            grown = False
            lo = min(pd.Timestamp(e["start_local"][:19]) for e in chain) - pd.Timedelta(hours=2)
            hi = max(pd.Timestamp(e["end_local"][:19]) for e in chain) + pd.Timedelta(hours=2)
            for e in eps:
                if e not in chain and lo <= pd.Timestamp(e["start_local"][:19]) <= hi:
                    chain.append(e)
                    grown = True
        return sorted(chain, key=lambda e: e["start_local"])

    chosen = [(window("path change"), "Path change"), (window("first hop"), "First-hop event")]
    chosen = [(c, t) for c, t in chosen if c]
    if not chosen:
        return
    fig, axes = plt.subplots(2, len(chosen), figsize=(10 * len(chosen), 9.5), sharex="col",
                             gridspec_kw={"height_ratios": [2.4, 1], "hspace": 0.22, "wspace": 0.16}, squeeze=False)
    cap, top = 45, 60
    for col, (chain, title) in enumerate(chosen):
        start = pd.Timestamp(chain[0]["start_local"][:19])
        end = pd.Timestamp(chain[-1]["end_local"][:19])
        part = df[(df["ts_local"] >= start - pd.Timedelta(hours=2)) & (df["ts_local"] <= end + pd.Timedelta(hours=2))]
        x = part["ts_local"]
        tint = BLUE if chain[0]["kind"] == "path change" else AQUA
        ax = axes[0, col]
        for e in chain:
            ax.axvspan(pd.Timestamp(e["start_local"][:19]), pd.Timestamp(e["end_local"][:19]), color=tint, alpha=0.12, linewidth=0)
        ax.plot(x, part["rtt_avg_ms"].clip(upper=cap), color=BLUE, linewidth=1.0, alpha=0.5, label="ICMP mean of 10")
        ax.plot(x, part["rtt_min_ms"].clip(upper=cap), color=NAVY, linewidth=1.4, label="ICMP minimum of 10")
        ax.plot(x, part["tcp_connect_min_ms"].clip(upper=cap), color=ORANGE, linewidth=1.2, alpha=0.9, label="TCP fastest of 5")
        ax.set_ylim(0, top)
        ax.set_ylabel(f"ms to target (clipped at {cap})")
        hours = (end - start).total_seconds() / 3600.0
        ax.set_title(f"{title}: {WEEKDAYS[chain[0]['weekday']]} {month_day(start)}, {hours:.1f} h", loc="left")
        ax.legend(loc="upper left", ncol=3)
        ax = axes[1, col]
        for e in chain:
            ax.axvspan(pd.Timestamp(e["start_local"][:19]), pd.Timestamp(e["end_local"][:19]), color=tint, alpha=0.12, linewidth=0)
        ax.plot(x, part["gateway_rtt_avg_ms"].clip(upper=30), color=AQUA, linewidth=1.2)
        ax.set_ylim(0, 30)
        ax.set_ylabel("ms to gateway")
        ax.set_xlabel("local time")
        ax.xaxis.set_major_locator(mdates.HourLocator(interval=2))
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M"))
    save(fig, out, "degradation_anatomy")
    plt.close(fig)


def fig_tcp_icmp(df, a, out, plt, mdates):
    split = pd.Timestamp(a["tcp_icmp"]["split_local"])
    fig, axes = plt.subplots(1, 2, figsize=(20, 6.4), gridspec_kw={"width_ratios": [2.3, 1], "wspace": 0.2})
    ax = axes[0]
    x = df["ts_local"]
    cap = 35
    from matplotlib.lines import Line2D
    ax.scatter(x, df["tcp_connect_min_ms"].clip(upper=cap), s=2.5, color=ORANGE, alpha=0.35, linewidth=0, rasterized=True)
    roll = df["rtt_min_ms"].rolling(120, center=True, min_periods=30).median()
    ax.plot(x, roll, color=NAVY, linewidth=2.0)
    handles = [Line2D([], [], marker="o", color=ORANGE, linestyle="none", markersize=8, label="TCP fastest of 5 handshakes, per sample"),
               Line2D([], [], color=NAVY, linewidth=2.2, label="ICMP minimum of 10, 1-hour rolling median")]
    ax.axvline(split, color=INK2, linewidth=1.6, linestyle=(0, (5, 4)))
    ax.text(split, cap * 0.97, f"  {month_day(split)} {split:%H:%M}", color=INK2, va="top", ha="left", fontsize=13)
    ax.set_ylim(0, cap)
    ax.set_ylabel("ms")
    ax.set_xlabel("local time")
    ax.set_title("Latency floor seen by TCP and by ICMP", loc="left")
    ax.legend(handles=handles, loc="lower left")
    date_axis(ax, mdates)
    ax = axes[1]
    before = df.loc[df["ts_local"] < split, "tcp_connect_min_ms"].dropna()
    after = df.loc[df["ts_local"] >= split, "tcp_connect_min_ms"].dropna()
    bins = np.arange(8, 32.5, 0.5)
    ax.hist(before.clip(8, 32), bins=bins, density=True, color=MUTED, alpha=0.75, linewidth=0,
            label=f"before ({len(before):,} samples)")
    ax.hist(after.clip(8, 32), bins=bins, density=True, color=ORANGE, alpha=0.8, linewidth=0,
            label=f"after ({len(after):,} samples)")
    ax.set_xlabel("TCP fastest handshake (ms)")
    ax.set_ylabel("density")
    ax.set_title("Before and after the split", loc="left")
    ax.legend(loc="upper right")
    save(fig, out, "tcp_vs_icmp")
    plt.close(fig)


def fig_loss(df, a, out, plt):
    L = a["loss"]
    fig = plt.figure(figsize=(20, 11))
    gs = fig.add_gridspec(2, 3, height_ratios=[1, 1.1], hspace=0.45, wspace=0.3)
    ax = fig.add_subplot(gs[0, 0])
    counts = {int(k): v for k, v in L["packets_received_counts"].items()}
    keys = sorted(counts, reverse=True)
    ax.bar(range(len(keys)), [counts[k] for k in keys], color=BLUE, linewidth=0, width=0.72)
    ax.set_xticks(range(len(keys)))
    ax.set_xticklabels([str(k) for k in keys])
    ax.set_yscale("log")
    ax.set_ylim(1, max(counts.values()) * 4)
    ax.set_xlabel("echo replies received out of 10")
    ax.set_ylabel("samples (log scale)")
    ax.set_title("Loss comes one echo at a time", loc="left")
    for i, k in enumerate(keys):
        ax.text(i, counts[k] * 1.25, f"{counts[k]:,}", ha="center", va="bottom", fontsize=12, color=INK2)

    xs = np.arange(len(LOSS_CLASSES))
    w = 0.36
    c = L["conditional"]

    def grouped(ax, left, right, unit, title):
        for offset, (key, color, name) in zip((-w / 2, w / 2), (left, right)):
            values = [c[k][key] or 0.0 for k, _ in LOSS_CLASSES]
            ax.bar(xs + offset, values, w, color=color, linewidth=0, label=name)
            for i, v in enumerate(values):
                ax.text(i + offset, v + 0.02 * top, f"{v:.1f}", ha="center", fontsize=12, color=INK2)
        ax.set_xticks(xs)
        ax.set_xticklabels([t for _, t in LOSS_CLASSES])
        ax.set_ylabel(unit)
        ax.set_ylim(0, top)
        ax.set_title(title, loc="left")
        ax.legend(loc="upper left")

    top = max(1.0, max((c[k]["http_over_1s_pct"] or 0.0) for k, _ in LOSS_CLASSES) * 1.35)
    grouped(fig.add_subplot(gs[0, 1]), ("http_over_1s_pct", VIOLET, "HTTP response > 1 s"),
            ("dns_recursive_over_200ms_pct", GREEN, "recursive DNS > 200 ms"), "% of samples",
            "Application tails grow with ICMP loss")
    top = max(1.0, max((c[k]["tcp_median"] or 0.0) for k, _ in LOSS_CLASSES) * 1.4)
    grouped(fig.add_subplot(gs[0, 2]), ("jitter_median", MAGENTA, "jitter, median"),
            ("tcp_median", ORANGE, "TCP handshake, median"), "ms", "Jitter rises, the TCP handshake does not")

    ax = fig.add_subplot(gs[1, :])
    h = df["http_ms"].dropna()
    lo, hi = 20, 8000
    bins = np.logspace(np.log10(lo), np.log10(hi), 150)
    ax.hist(h.clip(lo, hi), bins=bins, color=VIOLET, linewidth=0)
    ax.set_xscale("log")
    ax.set_yscale("log")
    ax.set_xlim(lo, hi)
    ticks = [20, 50, 100, 200, 500, 1000, 2000, 5000]
    ax.set_xticks(ticks)
    ax.set_xticklabels([f"{t:,}" for t in ticks])
    ax.set_xlabel("HTTP time to response headers (ms, log scale)")
    ax.set_ylabel("samples (log scale)")
    H = a["dns_http"]["http"]
    ax.set_title(f"HTTP response time: a smooth body around {H['median']:.0f} ms, then discrete modes near "
                 "1.1 s, 1.3 s and 4.1 s where lost packets wait for retransmission timers", loc="left")
    ax.axvspan(1000, 1400, color=VIOLET, alpha=0.1, linewidth=0)
    ax.axvspan(3900, 4400, color=VIOLET, alpha=0.1, linewidth=0)
    save(fig, out, "loss_anatomy")
    plt.close(fig)


def fig_regimes(df, a, out, plt):
    g = a["regimes"]
    split = g["split_date"]
    before, after = df[df["date_local"] < split], df[df["date_local"] >= split]
    panels = [("rtt_avg_ms", "ICMP round-trip time, mean of 10 (ms)", (8, 60), [10, 15, 20, 30, 40, 60]),
              ("jitter_ms", "ICMP jitter (ms)", (0.1, 30), [0.1, 0.3, 1, 3, 10, 30]),
              ("tcp_connect_median_ms", "TCP handshake, median of 5 (ms)", (10, 80), [10, 15, 20, 30, 40, 60, 80]),
              ("dns_resolve_ms", "DNS, resolver-cached name (ms)", (5, 300), [5, 10, 20, 50, 100, 200, 300]),
              ("dns_recursive_ms", "DNS, recursive lookup (ms)", (20, 2000), [20, 50, 100, 200, 500, 1000, 2000]),
              ("http_ms", "HTTP time to response headers (ms)", (30, 5000), [30, 100, 300, 1000, 3000])]
    fig, axes = plt.subplots(2, 3, figsize=(20, 10), gridspec_kw={"hspace": 0.5, "wspace": 0.18})
    for i, (ax, (col, title, lim, ticks)) in enumerate(zip(axes.ravel(), panels)):
        for part, color, key in ((before, BLUE, "before"), (after, ORANGE, "after")):
            v = np.sort(part[col].dropna().values)
            if v.size == 0:
                continue
            v = np.clip(v, lim[0], lim[1])
            ax.step(v, np.arange(1, v.size + 1) / v.size * 100, where="post", color=color, linewidth=2.2,
                    label=f"{key}: {period_label(g, key)}")
        ax.set_xscale("log")
        ax.set_xlim(*lim)
        ax.set_xticks(ticks)
        ax.set_xticklabels([f"{t:g}" for t in ticks])
        ax.xaxis.set_minor_locator(plt.NullLocator())
        ax.set_ylim(0, 100)
        ax.set_yticks([0, 25, 50, 75, 100])
        if i % 3 == 0:
            ax.set_ylabel("% of samples at or below")
        ax.set_title(title, loc="left")
    axes[0, 0].legend(loc="lower right")
    save(fig, out, "regimes")
    plt.close(fig)


def fig_acf(df, a, out, plt):
    A = a["autocorrelation"]
    lags = A["lag_minutes"]
    series = [("log_rtt_avg_ms", "ICMP RTT (log)", BLUE), ("jitter_ms", "jitter", MAGENTA),
              ("loss_indicator", "lost echo (0/1)", RED), ("tcp_connect_min_ms", "TCP fastest handshake", ORANGE),
              ("dns_recursive_ms", "recursive DNS", GREEN), ("http_ms", "HTTP", VIOLET)]
    fig, ax = plt.subplots(figsize=(16, 7))
    for key, name, color in series:
        pts = [(l, v) for l, v in zip(lags, A["values"][key]) if v is not None]
        if pts:
            ax.plot([l for l, _ in pts], [v for _, v in pts], color=color, marker="o", markersize=6, label=name)
    ax.set_xscale("log")
    ticks = [0.5, 1, 2.5, 5, 10, 30, 60, 180, 360, 720, 1440]
    ax.set_xticks(ticks)
    ax.set_xticklabels(["30 s", "1 m", "2.5 m", "5 m", "10 m", "30 m", "1 h", "3 h", "6 h", "12 h", "24 h"])
    ax.set_xlim(0.4, 1440 * 1.3)
    ax.set_ylim(-0.05, 1.0)
    ax.axhline(0, color=AXIS, linewidth=1.0)
    ax.set_xlabel("lag")
    ax.set_ylabel("autocorrelation")
    ax.set_title("How long the network remembers: autocorrelation of each probe against lag", loc="left")
    ax.legend(loc="upper right", ncol=2)
    save(fig, out, "autocorrelation")
    plt.close(fig)


def fig_speedtest(df, a, out, plt, mdates):
    sp = df[df["download_mbps"].notna()].copy()
    if sp.empty:
        return
    S = a["speedtest"]
    failed = sp["speedtest_ping_ms"].isna() | (sp["speedtest_ping_ms"] >= 600_000)
    fig = plt.figure(figsize=(20, 9.5))
    gs = fig.add_gridspec(2, 2, width_ratios=[1.45, 1], hspace=0.42, wspace=0.12)
    for row, (col, color, title) in enumerate((("download_mbps", BLUE, "Download"), ("upload_mbps", ORANGE, "Upload"))):
        ax = fig.add_subplot(gs[row, 0])
        ok, bad = sp[~failed], sp[failed]
        ax.scatter(ok["ts_local"], ok[col], s=30, color=color, alpha=0.85, linewidth=0, label="server latency probe succeeded")
        ax.scatter(bad["ts_local"], bad[col], s=42, facecolor="white", edgecolor=color, linewidth=1.4,
                   label="server latency probe failed")
        ax.set_ylabel("Mbit/s")
        ax.set_title(f"{title}: {len(sp)} hourly tests on {S['servers']} different servers" if row == 0 else title, loc="left")
        ax.set_ylim(0, float(sp[col].max()) * 1.12)
        if row == 0:
            ax.legend(loc="upper left", ncol=2)
        else:
            ax.set_xlabel("local time")
        date_axis(ax, mdates)
    ax = fig.add_subplot(gs[:, 1])
    top = S["top_servers"]
    names = [t["server"] for t in top]
    for i, t in enumerate(top):
        part = sp[sp["speedtest_server"] == t["server"]]
        y = np.full(len(part), i) + np.random.default_rng(i).uniform(-0.18, 0.18, len(part))
        ax.scatter(part["download_mbps"], y, s=34, color=BLUE, alpha=0.6, linewidth=0)
        ax.plot([t["download_median"], t["download_median"]], [i - 0.32, i + 0.32], color=NAVY, linewidth=3)
    ax.set_yticks(range(len(top)))
    ax.set_yticklabels([f"{n}  ({t['n']})" for n, t in zip(names, top)])
    ax.yaxis.tick_right()
    ax.invert_yaxis()
    ax.set_xlabel("download (Mbit/s); bar = median of that server")
    ax.set_title("Download depends on which server was picked", loc="left")
    save(fig, out, "speedtest")
    plt.close(fig)


def fig_labels(df, a, out, plt):
    g = a["regimes"]
    names = [("label_outage", "outage"), ("label_packet_loss", "packet loss"), ("label_high_latency", "high latency"),
             ("label_high_jitter", "high jitter"), ("label_gateway_degraded", "gateway degraded"), ("label_anomaly", "any (anomaly)")]
    ys = np.arange(len(names))
    h = 0.38
    fig, ax = plt.subplots(figsize=(14, 6.6))
    b = [g["before"]["labels_pct"][k] for k, _ in names]
    c = [g["after"]["labels_pct"][k] for k, _ in names]
    ax.barh(ys - h / 2, b, h, color=BLUE, linewidth=0, label=period_label(g, "before"))
    ax.barh(ys + h / 2, c, h, color=ORANGE, linewidth=0, label=period_label(g, "after"))
    for y, v in zip(ys - h / 2, b):
        ax.text(v + 0.6, y, f"{v:.1f} %", va="center", fontsize=13, color=INK2)
    for y, v in zip(ys + h / 2, c):
        ax.text(v + 0.6, y, f"{v:.1f} %", va="center", fontsize=13, color=INK2)
    ax.set_yticks(ys)
    ax.set_yticklabels([n for _, n in names])
    ax.invert_yaxis()
    ax.set_xlim(0, max(b + c) * 1.18)
    ax.set_xlabel("% of samples carrying the label")
    ax.set_title("Reference labels before and after the change point", loc="left")
    ax.legend(loc="upper right")
    save(fig, out, "labels")
    plt.close(fig)


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #

def render_all(df: pd.DataFrame, a: dict, out: Path) -> list[str]:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.dates as mdates
    import matplotlib.pyplot as plt
    style(plt)
    out.mkdir(parents=True, exist_ok=True)
    fig_overview(df, a, out, plt, mdates)
    fig_diurnal(df, a, out, plt)
    fig_heatmaps(df, a, out, plt)
    fig_anatomy(df, a, out, plt, mdates)
    fig_tcp_icmp(df, a, out, plt, mdates)
    fig_loss(df, a, out, plt)
    fig_regimes(df, a, out, plt)
    fig_acf(df, a, out, plt)
    fig_speedtest(df, a, out, plt, mdates)
    fig_labels(df, a, out, plt)
    return sorted(p.name for p in (out / "png").glob("*.png"))


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Render the dataset figures as PNG and PDF.")
    p.add_argument("--input", default=None,
                   help="consolidated parquet or CSV (default: dataset/localnet_dynamics.parquet)")
    p.add_argument("--analysis", default=None,
                   help="analysis JSON from analyze.py (default: next to the input; recomputed if missing)")
    p.add_argument("--out-dir", default=str(ROOT / "figures"))
    args = p.parse_args(argv)

    path = Path(args.input) if args.input else ROOT / "dataset" / "localnet_dynamics.parquet"
    if not path.exists() and not args.input:
        path = ROOT / "dataset" / "localnet_dynamics.csv"
    if not path.exists():
        raise SystemExit(f"{path} not found; run build_dataset.py first")
    df = load(path)
    analysis_path = Path(args.analysis) if args.analysis else path.parent / "analysis.json"
    if analysis_path.exists():
        a = json.loads(analysis_path.read_text(encoding="utf-8"))
    else:
        summary_path = path.parent / "summary.json"
        summary = json.loads(summary_path.read_text(encoding="utf-8")) if summary_path.exists() else None
        a = analyse(df, summary)
    names = render_all(df, a, Path(args.out_dir))
    print(f"{len(names)} figures written to {args.out_dir} (png/ and pdf/): " + ", ".join(names))
    return 0


if __name__ == "__main__":
    sys.exit(main())
