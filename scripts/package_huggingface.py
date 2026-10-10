#!/usr/bin/env python3
"""
Assemble the huggingface/ folder: a self-contained copy of everything that is
published on the Hugging Face Hub, ready for `hf upload`.

    huggingface/
    ├── README.md          the dataset card (DATASET_CARD.md, unchanged)
    ├── LICENSE
    ├── CITATION.cff       citation metadata for the dataset
    ├── requirements.txt
    ├── dataset/           consolidated files, analysis, raw run files
    ├── figures/png, pdf   the figures the card embeds
    ├── scripts/           collector, builder, analysis, plotting, packaging
    └── tests/

Run it after build_dataset.py, analyze.py and plot_figures.py. The folder is
rebuilt from scratch on every run and is ignored by git.

    python package_huggingface.py
    python package_huggingface.py --out-dir /some/other/place
"""

from __future__ import annotations

import argparse
import re
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

DATASET_CITATION = """cff-version: 1.2.0
message: "If you use this dataset, please cite it as below. The toolkit that produced it has its own citation in the source repository."
type: dataset
title: "LocalNet-Dynamics: A Longitudinal Local Area Network Telemetry Dataset"
authors:
  - family-names: "Ezharjan"
    given-names: "Alexander"
version: {version}
date-released: "{date}"
license: MIT
doi: 10.57967/hf/10841
url: "https://huggingface.co/datasets/ezharjan/LocalNetDynamics"
repository-code: "https://github.com/Ezharjan/LocalNetDynamicsDataset"
abstract: >-
  A longitudinal telemetry dataset of a residential local area network,
  sampled every 30 seconds around the clock from a wired desktop for 19 days.
  Each sample records ICMP latency, jitter and loss to a reference host and to
  the gateway, TCP connect time, cached and recursive DNS response time, HTTP
  time-to-first-response and hourly WAN throughput tests, with time-of-day
  features and rule-based reference labels for forecasting, anomaly detection
  and quality-of-service research.
keywords:
  - network telemetry
  - latency
  - jitter
  - packet loss
  - DNS
  - throughput
  - quality of service
  - anomaly detection
  - time series
"""


def field(text: str, name: str) -> str:
    m = re.search(rf"^{name}:\s*\"?([^\"\n]+)\"?\s*$", text, re.MULTILINE)
    if not m:
        raise SystemExit(f"CITATION.cff has no '{name}' field")
    return m.group(1).strip()


def copy_tree(src: Path, dst: Path, patterns: tuple[str, ...]) -> list[Path]:
    copied = []
    for pattern in patterns:
        for path in sorted(src.glob(pattern)):
            if path.is_file():
                target = dst / path.relative_to(src)
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(path, target)
                copied.append(target)
    return copied


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Assemble the folder that is uploaded to Hugging Face.")
    p.add_argument("--out-dir", default=str(ROOT / "huggingface"))
    args = p.parse_args(argv)
    out = Path(args.out_dir)

    required = [ROOT / "DATASET_CARD.md", ROOT / "LICENSE", ROOT / "CITATION.cff",
                ROOT / "dataset" / "localnet_dynamics.parquet", ROOT / "dataset" / "analysis.json",
                ROOT / "figures" / "png"]
    missing = [str(path) for path in required if not path.exists()]
    if missing:
        raise SystemExit("missing, run build_dataset.py, analyze.py and plot_figures.py first:\n  " + "\n  ".join(missing))

    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)

    shutil.copy2(ROOT / "DATASET_CARD.md", out / "README.md")
    shutil.copy2(ROOT / "LICENSE", out / "LICENSE")
    shutil.copy2(ROOT / "requirements.txt", out / "requirements.txt")
    code_citation = (ROOT / "CITATION.cff").read_text(encoding="utf-8")
    (out / "CITATION.cff").write_text(
        DATASET_CITATION.format(version=field(code_citation, "version"), date=field(code_citation, "date-released")),
        encoding="utf-8")

    files = [out / "README.md", out / "LICENSE", out / "requirements.txt", out / "CITATION.cff"]
    files += copy_tree(ROOT / "dataset", out / "dataset",
                       ("localnet_dynamics.csv", "localnet_dynamics.parquet", "summary.json", "analysis.json",
                        "raw/localnet_*.csv", "raw/localnet_*.json", "raw/events.csv"))
    files += copy_tree(ROOT / "figures", out / "figures", ("png/*.png", "pdf/*.pdf"))
    files += copy_tree(ROOT / "scripts", out / "scripts", ("*.py",))
    files += copy_tree(ROOT / "tests", out / "tests", ("*.py",))

    total = sum(f.stat().st_size for f in files)
    for f in files:
        print(f"  {f.relative_to(out).as_posix():<60}{f.stat().st_size / 1e6:>9.2f} MB")
    print(f"{len(files)} files, {total / 1e6:.1f} MB written to {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
