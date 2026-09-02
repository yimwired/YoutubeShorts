"""Fail loudly when the channel has gone quiet.

The daily job failed three mornings in a row and nobody noticed, because a
failed run is a red mark on a page nobody opens. This runs at the end of the
swap workflow, which is the one that has not missed a day, and exits non-zero
when the queue has no video published in the last `--hours`. A second red
workflow is not much of an alarm, but it is a different one, it arrives by
email, and it says what is wrong instead of ending in an ffmpeg backtrace.

Run as: python check_pipeline.py [--hours 48]
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import sys
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

sys.stdout.reconfigure(encoding="utf-8")

QUEUE_DIR = "queue"
BKK       = ZoneInfo("Asia/Bangkok")


def _recent_publishes(hours: int) -> list[tuple[datetime, str]]:
    cutoff = datetime.now(BKK) - timedelta(hours=hours)
    out = []
    for path in glob.glob(os.path.join(QUEUE_DIR, "job_*.json")):
        try:
            with open(path, encoding="utf-8") as f:
                job = json.load(f)
        except Exception:
            continue
        if job.get("status") != "uploaded":
            continue
        try:
            when = datetime.fromisoformat(job["publish_at"])
        except (KeyError, ValueError):
            continue
        if when >= cutoff:
            out.append((when, job.get("title", "")))
    out.sort()
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--hours", type=int, default=48,
                    help="Window that must contain at least one publish")
    args = ap.parse_args()

    found = _recent_publishes(args.hours)
    if found:
        print(f"[check] {len(found)} video(s) published in the last {args.hours}h:")
        for when, title in found:
            print(f"    {when:%Y-%m-%d %H:%M}  {title}")
        return 0

    print(f"[check] NO video published in the last {args.hours}h.")
    print("[check] The daily job is not producing. Check the Daily Video "
          "Generation workflow, most recent failed run.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
