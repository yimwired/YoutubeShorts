"""Delete A/B candidate thumbnails whose swap window has passed.

thumb_*_b.jpg files are committed to the repo so swap_thumbnails.py
(running on GitHub Actions days after generation) can still set them.
Without pruning the repo would grow ~2 MB/day forever. A thumb is no
longer needed once:
  - the job already swapped to B (file was uploaded to YouTube), or
  - the video is older than MAX_AGE_DAYS -- past that point analytics
    have spoken and we keep variant A permanently.

Run as: python prune_thumbs.py  (idempotent, prints what it removes)
"""

from __future__ import annotations

import glob
import json
import os
from datetime import datetime
from zoneinfo import ZoneInfo

BKK          = ZoneInfo("Asia/Bangkok")
MAX_AGE_DAYS = 7


def main() -> None:
    removed = 0
    for path in sorted(glob.glob(os.path.join("queue", "job_*.json"))):
        with open(path, encoding="utf-8") as f:
            job = json.load(f)

        thumb_b = (job.get("thumb_path_b") or "").replace("\\", "/")
        if not thumb_b or not os.path.exists(thumb_b):
            continue

        age_days = None
        uploaded_at = job.get("uploaded_at")
        if uploaded_at:
            try:
                up = datetime.fromisoformat(uploaded_at)
                if up.tzinfo is None:
                    up = up.replace(tzinfo=BKK)
                age_days = (datetime.now(BKK) - up).days
            except ValueError:
                pass

        if job.get("ab_swapped") or (age_days is not None and age_days > MAX_AGE_DAYS):
            os.remove(thumb_b)
            removed += 1
            print(f"[Prune] removed {thumb_b} "
                  f"(swapped={bool(job.get('ab_swapped'))}, age={age_days}d)")

    print(f"[Prune] done -- {removed} thumbnail(s) removed.")


if __name__ == "__main__":
    main()
