"""Post the comment-bait question on videos once they are actually public.

The question used to be posted by src/uploader.py straight after the insert
call. Every one of those failed with a 403, because generate_batch uploads
private with a publishAt and YouTube refuses comments on a video nobody can
see yet. The failure was caught and logged, so nothing broke -- the channel
just quietly ran for months with the one feature meant to fix its weakest
signal doing nothing. Ninety days of that: eight comments on ninety-nine
thousand views.

So the post moved here, to a pass that runs after the video goes live. A
channel-owner comment gives the thread somewhere to start, and comment depth
is the signal that decides whether a Short gets pushed past its first
audience.

Run as: python seed_comments.py [--live]
Idempotent: `seed_comment_at` on the job is the record that it is done.
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import sys
import time
from datetime import datetime, timedelta, timezone

sys.stdout.reconfigure(encoding="utf-8")

from dotenv import load_dotenv
load_dotenv()

from src.uploader import _get_service

QUEUE_DIR = "queue"

# Only seed recent uploads. Dropping a comment on every video in the back
# catalogue in one run reads as spam to YouTube and burns the daily quota
# for no benefit -- an old Short is past the window where the signal counts.
DEFAULT_DAYS = 3
MAX_PER_RUN  = 5


def _pending_jobs(days: int) -> list[tuple[str, dict]]:
    """Uploaded jobs inside the window that carry an unposted question."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    out = []
    for path in glob.glob(os.path.join(QUEUE_DIR, "job_*.json")):
        try:
            with open(path, encoding="utf-8") as f:
                job = json.load(f)
        except Exception:
            continue
        if not job.get("youtube_video_id") or job.get("seed_comment_at"):
            continue
        if not (job.get("cta") or "").strip():
            continue
        stamp = job.get("uploaded_at") or job.get("created_at") or ""
        try:
            t = datetime.fromisoformat(stamp)
            t = t.replace(tzinfo=timezone.utc) if t.tzinfo is None else t
        except ValueError:
            continue
        if t >= cutoff:
            out.append((path, job))
    out.sort(key=lambda p: p[1].get("uploaded_at") or "")
    return out


def _is_public(youtube, video_id: str) -> bool:
    try:
        resp = youtube.videos().list(part="status", id=video_id).execute()
    except Exception as e:
        print(f"  [{video_id}] status lookup failed: {e}")
        return False
    items = resp.get("items", [])
    return bool(items) and items[0]["status"]["privacyStatus"] == "public"


def _post(youtube, video_id: str, text: str) -> bool:
    try:
        youtube.commentThreads().insert(
            part="snippet",
            body={"snippet": {
                "videoId": video_id,
                "topLevelComment": {"snippet": {"textOriginal": text}},
            }},
        ).execute()
        return True
    except Exception as e:
        print(f"  [{video_id}] post failed: {e}")
        return False


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=DEFAULT_DAYS,
                    help="Only seed videos uploaded in the last N days")
    ap.add_argument("--live", action="store_true",
                    help="Actually post. Without it, prints what it would do.")
    args = ap.parse_args()

    jobs = _pending_jobs(args.days)
    print(f"[seed_comments] {len(jobs)} job(s) awaiting a question "
          f"(mode = {'LIVE' if args.live else 'DRY-RUN'})")
    if not jobs:
        return 0

    youtube = _get_service()
    if not youtube:
        print("[seed_comments] no YouTube service — abort")
        return 1

    posted = 0
    for path, job in jobs[:MAX_PER_RUN]:
        vid  = job["youtube_video_id"]
        text = job["cta"].strip()
        if not _is_public(youtube, vid):
            print(f"  [{vid}] not public yet — leaving it queued")
            continue

        print(f"  [{vid}] {text}")
        if not args.live:
            continue

        if not _post(youtube, vid, text):
            continue

        job["seed_comment_at"] = datetime.now(timezone.utc).isoformat()
        with open(path, "w", encoding="utf-8") as f:
            json.dump(job, f, ensure_ascii=False, indent=2)
        posted += 1
        time.sleep(2)   # gentle pacing against YouTube's spam heuristics

    print(f"[seed_comments] done — posted {posted}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
