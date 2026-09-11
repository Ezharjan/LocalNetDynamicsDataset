#!/usr/bin/env python3
"""
Record a manual event annotation for the LocalNet-Dynamics dataset.

Events describe things the collector cannot observe by itself: a household
member starting a 4K stream, a router reboot, a laptop moved to another room,
a known ISP maintenance window. `build_dataset.py` joins them onto every
sample whose timestamp falls inside the event window, producing the
`event_category` and `event_description` columns.

Events are appended to dataset/raw/events.csv with UTC timestamps.

    python annotate.py "4K streaming on the living-room TV" --category load --minutes 45
    python annotate.py "router rebooted" --category maintenance
    python annotate.py "moved laptop to the garden" --category environment \
        --start "2026-09-12 16:00" --end "2026-09-12 18:30"
    python annotate.py --list
"""

from __future__ import annotations

import argparse
import csv
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

CATEGORIES = ["load", "outage", "maintenance", "environment", "isp", "device", "other"]
COLUMNS = ["start_utc", "end_utc", "category", "description"]
DEFAULT_PATH = Path(__file__).resolve().parent.parent / "dataset" / "raw" / "events.csv"


def parse_time(text: str | None) -> datetime:
    """Interpret a timestamp; naive values are local time."""
    if not text or text.lower() == "now":
        return datetime.now(timezone.utc)
    text = text.strip().replace("Z", "+00:00")
    if " " in text and "T" not in text:
        text = text.replace(" ", "T", 1)
    dt = datetime.fromisoformat(text)
    if dt.tzinfo is None:
        dt = dt.astimezone()          # attach the local zone
    return dt.astimezone(timezone.utc)


def fmt(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def append_event(path: Path, start: datetime, end: datetime, category: str, description: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    new_file = not path.exists() or path.stat().st_size == 0
    with path.open("a", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        if new_file:
            writer.writerow(COLUMNS)
        writer.writerow([fmt(start), fmt(end), category, description])


def list_events(path: Path) -> None:
    if not path.exists():
        print(f"no events recorded yet ({path})")
        return
    with path.open(newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    if not rows:
        print("no events recorded yet")
        return
    print(f"{'start (UTC)':<21}{'end (UTC)':<21}{'category':<13}description")
    for r in rows:
        print(f"{r['start_utc']:<21}{r['end_utc']:<21}{r['category']:<13}{r['description']}")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Append a manual event annotation.")
    p.add_argument("description", nargs="?", help="what happened, in one short sentence")
    p.add_argument("--category", choices=CATEGORIES, default="other")
    p.add_argument("--start", default=None,
                   help="event start, local time unless an offset/Z is given "
                        "(e.g. '2026-09-12 16:00'); default: now")
    p.add_argument("--end", default=None, help="event end (same format)")
    p.add_argument("--minutes", type=float, default=1.0,
                   help="event length when --end is not given")
    p.add_argument("--file", default=str(DEFAULT_PATH), help="events CSV to append to")
    p.add_argument("--list", action="store_true", help="print the recorded events and exit")
    args = p.parse_args(argv)

    path = Path(args.file)
    if args.list:
        list_events(path)
        return 0
    if not args.description:
        p.error("a description is required (or use --list)")
    try:
        start = parse_time(args.start)
        end = parse_time(args.end) if args.end else start + timedelta(minutes=args.minutes)
    except ValueError as exc:
        p.error(f"invalid timestamp: {exc}")
    if end < start:
        p.error("--end is before --start")
    append_event(path, start, end, args.category, args.description.strip())
    print(f"recorded [{args.category}] {fmt(start)} -> {fmt(end)}: {args.description.strip()}")
    print(f"file: {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
