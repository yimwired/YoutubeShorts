"""Daily job for #5 Phase 2 + #6.

Walks queue/job_*.json. For each job that:
  - has youtube_video_id (i.e. upload succeeded)
  - has uploaded_at older than MIN_AGE_HOURS (analytics lag window)
  - is not yet ab_swapped
  - has thumb_path_b (B variant exists locally)
... fetches YouTube Analytics for the video and decides whether to
swap to thumbnail B. Swap criterion is intentionally conservative:
both views *and* retention have to be below the channel-relative
band -- a thumbnail isn't the only thing that drives Shorts views, so
we only swap when the signal is clearly weak.

Side effects per processed job:
  - thumbnails().set(B) on YouTube if swap decided
  - job["ab_swapped"] = True
  - job["analytics_at"] = now
  - notion_logger.update_analytics(...) writes views / retention /
    watch minutes + active variant to the existing Notion page

Run as: python swap_thumbnails.py
Designed to be idempotent + cheap to re-run every day."""

from __future__ import annotations

import glob
import json
import os
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

from src.analytics import stats_for_video, channel_median_views
from src.notion_logger import update_analytics


BKK              = ZoneInfo("Asia/Bangkok")
QUEUE_DIR        = "queue"
TOKEN_FILE       = "token_youtube.json"

# Wait this long after the YouTube insert call before trusting the
# analytics window. YouTube Analytics has a 24-48h ingestion lag and
# Shorts surfaces gradually, so swapping too early would punish
# perfectly fine thumbnails that just haven't accumulated views yet.
MIN_AGE_HOURS    = 48

# Relative thresholds vs channel median. Swap only if BOTH conditions
# are true so we don't churn thumbnails on a single weak signal.
VIEWS_FRACTION   = 0.40   # views < 40% of channel median => low
RETENTION_FLOOR  = 45.0   # avg view % below this => weak retention


def _yt_data_service():
    if not os.path.exists(TOKEN_FILE):
        return None
    creds = Credentials.from_authorized_user_file(TOKEN_FILE)
    return build("youtube", "v3", credentials=creds, cache_discovery=False)


def _set_thumbnail(yt, video_id: str, thumb_path: str) -> bool:
    try:
        yt.thumbnails().set(
            videoId=video_id,
            media_body=MediaFileUpload(thumb_path, mimetype="image/jpeg"),
        ).execute()
        return True
    except Exception as e:
        print(f"  [Swap] thumbnails().set failed: {e}")
        return False


def _should_swap(stats: dict, median_views: int | None) -> tuple[bool, str]:
    views     = stats["views"]
    retention = stats["retention"]
    reasons = []

    low_views = (median_views is not None
                 and median_views > 0
                 and views < median_views * VIEWS_FRACTION)
    low_ret   = retention < RETENTION_FLOOR

    if low_views:
        reasons.append(f"views {views} < {VIEWS_FRACTION:.0%} of "
                       f"median {median_views}")
    if low_ret:
        reasons.append(f"retention {retention:.1f}% < {RETENTION_FLOOR}%")

    return (low_views and low_ret), "; ".join(reasons) or "ok"


def process_job(job_path: str, yt, median_views: int | None) -> None:
    with open(job_path, encoding="utf-8") as f:
        job = json.load(f)

    vid_id = job.get("youtube_video_id")
    if not vid_id:
        return  # never uploaded

    uploaded_at = job.get("uploaded_at")
    if not uploaded_at:
        return  # legacy job, no timestamp -- skip silently

    try:
        up = datetime.fromisoformat(uploaded_at)
    except Exception:
        return

    if up.tzinfo is None:
        up = up.replace(tzinfo=BKK)
    age_hours = (datetime.now(BKK) - up).total_seconds() / 3600
    if age_hours < MIN_AGE_HOURS:
        return

    stats = stats_for_video(vid_id)
    if not stats:
        print(f"  [Swap] {vid_id}: no analytics rows yet "
              f"(age {age_hours:.0f}h) -- skipping")
        return

    print(f"  [Swap] {vid_id}: views={stats['views']} "
          f"retention={stats['retention']:.1f}% "
          f"watch_min={stats['watch_minutes']}")

    # Update Notion regardless of swap decision -- this is #6.
    page_id = job.get("notion_page_id")
    active_variant = "B" if job.get("ab_swapped") else "A"

    already_swapped = bool(job.get("ab_swapped"))
    thumb_b = job.get("thumb_path_b")

    if (not already_swapped
            and thumb_b
            and os.path.exists(thumb_b)):
        swap, why = _should_swap(stats, median_views)
        if swap:
            print(f"  [Swap] {vid_id}: SWAP to B -- {why}")
            if _set_thumbnail(yt, vid_id, thumb_b):
                job["ab_swapped"]   = True
                job["ab_swap_at"]   = datetime.now(BKK).isoformat()
                job["ab_swap_why"]  = why
                active_variant = "B"
        else:
            print(f"  [Swap] {vid_id}: keep A -- {why}")

    job["analytics_at"]   = datetime.now(BKK).isoformat()
    job["last_views"]     = stats["views"]
    job["last_retention"] = stats["retention"]
    with open(job_path, "w", encoding="utf-8") as f:
        json.dump(job, f, ensure_ascii=False, indent=2)

    if page_id:
        update_analytics(
            page_id,
            views=stats["views"],
            retention=stats["retention"],
            watch_minutes=stats["watch_minutes"],
            ab_variant=active_variant,
        )


def main() -> None:
    yt = _yt_data_service()
    if not yt:
        print("[Swap] No YouTube Data API service (token missing). Aborting.")
        return

    median_views = channel_median_views(30)
    print(f"[Swap] channel median views (30d): {median_views}")

    jobs = sorted(glob.glob(os.path.join(QUEUE_DIR, "job_*.json")))
    print(f"[Swap] scanning {len(jobs)} jobs...")
    for path in jobs:
        try:
            process_job(path, yt, median_views)
        except Exception as e:
            print(f"  [Swap] {path}: error {e}")
    print("[Swap] done.")


if __name__ == "__main__":
    main()
