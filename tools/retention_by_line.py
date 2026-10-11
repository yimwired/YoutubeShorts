#!/usr/bin/env python3
"""
Retention by line -- which sentence do viewers leave on?

Usage: python tools/retention_by_line.py
       python tools/retention_by_line.py --days 14

Each TH job records where its six sentences play in the video (`sentences` and
`video_sec` in queue/job_*_th.json, written by generate_batch.py). This lays each
clip's audience retention curve from the YouTube Analytics API over those times and
reports how much of the audience every sentence lost: per clip, then as a median per
slot across clips.

The curve is `audienceWatchRatio` by `elapsedVideoTimeRatio`, organic views only --
the share of views still watching at each 1% of the runtime. Shorts loop, so values
above 1.0 are normal, and a loss is measured in those same units (x100 = points).

Skipped: clips public for less than 48h (Analytics has not ingested them yet) and jobs
from before the timeline was recorded (no `sentences` field).
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import statistics
import sys
from datetime import date, datetime, timedelta

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_ROOT)

from googleapiclient.errors import HttpError  # noqa: E402

from src.analytics import _service  # noqa: E402

# Beat names in the order src/generator.py asks for them. The morning "human"
# format trades the history beats for a body-mechanism arc.
SLOT_NAMES = {
    "human":   ("MIRROR", "PROOF", "MECHANISM", "WHY", "REVEAL", "LOOP"),
    "default": ("HOOK", "PROOF", "ORIGIN", "TURN", "REVEAL", "LOOP"),
}
INGEST_DELAY = timedelta(hours=48)


def script_format(job: dict) -> str:
    return "human" if job.get("style") == "human" else "default"


def slot_names(job: dict) -> tuple[str, ...]:
    names = SLOT_NAMES[script_format(job)]
    count = len(job["sentences"])
    return names if count == len(names) else tuple(f"S{i + 1}" for i in range(count))


def load_jobs(days: int | None) -> list[dict]:
    """Published TH jobs that carry a sentence timeline and are old enough to read."""
    now = datetime.now().astimezone()
    jobs = []
    for path in sorted(glob.glob(os.path.join(REPO_ROOT, "queue", "job_*_th.json"))):
        with open(path, encoding="utf-8") as f:
            job = json.load(f)
        if not all(job.get(key) for key in
                   ("youtube_video_id", "sentences", "video_sec", "publish_at")):
            continue
        published = datetime.fromisoformat(job["publish_at"])
        if now - published < INGEST_DELAY:
            continue
        if days and now - published > timedelta(days=days):
            continue
        job["published"] = published
        jobs.append(job)
    return jobs


def retention_curve(ya, job: dict) -> list[tuple[float, float]]:
    """(elapsed ratio, audience watch ratio) points, organic views only.

    The range opens a day before the Bangkok publish date: Analytics days run on
    Pacific Time, where a morning publish here is still the previous day, and a
    range that misses that first day comes back with no retention rows at all.
    """
    first_day = (job["published"] - timedelta(days=1)).date()
    response = ya.reports().query(
        ids="channel==MINE",
        startDate=first_day.isoformat(),
        endDate=date.today().isoformat(),
        metrics="audienceWatchRatio",
        dimensions="elapsedVideoTimeRatio",
        filters=f"video=={job['youtube_video_id']};audienceType==ORGANIC",
    ).execute()
    return [(float(ratio), float(watch)) for ratio, watch in response.get("rows", [])]


def watch_ratio_at(curve: list[tuple[float, float]], ratio: float) -> float:
    """Linear interpolation, held flat beyond the first and last points."""
    if ratio <= curve[0][0]:
        return curve[0][1]
    for (x0, y0), (x1, y1) in zip(curve, curve[1:]):
        if ratio <= x1:
            return y0 + (y1 - y0) * (ratio - x0) / (x1 - x0)
    return curve[-1][1]


def sentence_losses(job: dict, curve: list[tuple[float, float]]) -> list[dict]:
    """Audience share as each sentence starts and ends, and how much it lost.

    `lost_per_sec` is the fair comparison between slots -- a long sentence loses
    more simply by lasting longer.
    """
    video_sec = job["video_sec"]
    rows = []
    for slot, sentence in zip(slot_names(job), job["sentences"]):
        at_start = watch_ratio_at(curve, sentence["start"] / video_sec)
        at_end = watch_ratio_at(curve, sentence["end"] / video_sec)
        seconds = max(sentence["end"] - sentence["start"], 0.1)
        rows.append({
            "slot":         slot,
            "start":        sentence["start"],
            "end":          sentence["end"],
            "at_start":     at_start,
            "at_end":       at_end,
            "lost":         at_start - at_end,
            "lost_per_sec": (at_start - at_end) / seconds,
        })
    return rows


def steepest(rows: list[dict]) -> dict:
    return max(rows, key=lambda row: row["lost_per_sec"])


def print_clip(job: dict, rows: list[dict]) -> None:
    worst = steepest(rows)
    print(f"\n{job['published']:%Y-%m-%d}  {job['youtube_video_id']}  "
          f"{job['video_sec']:.1f}s  {job.get('title', '')[:48]}")
    for row in rows:
        marker = "  <- steepest" if row is worst else ""
        print(f"  {row['slot']:<9} {row['start']:5.1f}-{row['end']:5.1f}s  "
              f"{row['at_start']:.2f} -> {row['at_end']:.2f}  "
              f"lost {row['lost'] * 100:5.1f} pts  {row['lost_per_sec'] * 100:5.1f}/s"
              f"{marker}")


def print_summary(results: list[tuple[dict, list[dict]]]) -> None:
    by_slot: dict[tuple[str, str], list[dict]] = {}
    steepest_count: dict[tuple[str, str], int] = {}
    for job, rows in results:
        fmt = script_format(job)
        for row in rows:
            by_slot.setdefault((fmt, row["slot"]), []).append(row)
        key = (fmt, steepest(rows)["slot"])
        steepest_count[key] = steepest_count.get(key, 0) + 1

    print("\nMedian per slot (points of the audience lost while the sentence played)")
    current_fmt = None
    for (fmt, slot), rows in by_slot.items():
        if fmt != current_fmt:
            current_fmt = fmt
            print(f"\n  {fmt} format")
        lost = statistics.median(row["lost"] for row in rows) * 100
        rate = statistics.median(row["lost_per_sec"] for row in rows) * 100
        print(f"    {slot:<9} n={len(rows):<3} lost {lost:5.1f} pts  {rate:5.1f}/s  "
              f"steepest in {steepest_count.get((fmt, slot), 0)} clip(s)")


def main() -> int:
    parser = argparse.ArgumentParser(description="Which sentence do viewers leave on?")
    parser.add_argument("--days", type=int,
                        help="only clips published within this many days")
    args = parser.parse_args()

    os.chdir(REPO_ROOT)  # src.analytics reads token_youtube.json from the cwd
    ya = _service()
    if ya is None:
        print("token_youtube.json not found in the repo root")
        return 1

    jobs = load_jobs(args.days)
    if not jobs:
        print("No clip has a sentence timeline and 48h of data yet.")
        return 0

    results = []
    for job in jobs:
        try:
            curve = retention_curve(ya, job)
        except HttpError as err:
            print(f"  {job['youtube_video_id']}: query failed (HTTP {err.resp.status})")
            continue
        if not curve:
            print(f"  {job['youtube_video_id']}: no retention rows yet")
            continue
        rows = sentence_losses(job, curve)
        print_clip(job, rows)
        results.append((job, rows))

    if results:
        print_summary(results)
    return 0


if __name__ == "__main__":
    sys.exit(main())
