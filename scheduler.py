"""
Uploads pre-generated videos from queue/ at scheduled times.
On startup: auto-catches up missed slots and checks if scheduled videos went live.

Run: python scheduler.py
Keep running in background.

Generate videos first: python generate_batch.py [N]
"""
import sys
import os
import json
import glob
import threading
sys.stdout.reconfigure(encoding="utf-8")

_generating = False   # prevent concurrent batch generation

from dotenv import load_dotenv
load_dotenv()

from datetime import datetime
from zoneinfo import ZoneInfo
from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger

from src.uploader import upload_youtube, upload_tiktok, check_video_public
from src.notion_logger import mark_uploaded

QUEUE_DIR = "queue"
BKK       = ZoneInfo("Asia/Bangkok")


# ── Helpers ──────────────────────────────────────────────────────────────────

def _load_jobs(status_filter: str = None) -> list[dict]:
    files = sorted(glob.glob(os.path.join(QUEUE_DIR, "job_*.json")))
    jobs  = []
    for f in files:
        with open(f, encoding="utf-8") as fp:
            job = json.load(fp)
        job["_file"] = f
        if status_filter is None or job.get("status", "pending") == status_filter:
            jobs.append(job)
    return jobs


def _save_job(job: dict) -> None:
    path = job.pop("_file")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(job, f, ensure_ascii=False, indent=2)
    job["_file"] = path


def _is_past(publish_at: str) -> bool:
    try:
        t = datetime.fromisoformat(publish_at)
        return t < datetime.now(BKK)
    except Exception:
        return False


# ── Upload one job ────────────────────────────────────────────────────────────

def _upload_job(job: dict, publish_at: str) -> None:
    lang       = job["lang"]
    title_full = job["title_full"]
    title      = job["title"]
    video_path = job["video_path"]
    thumb_path = job["thumb_path"]
    desc       = job["description"]
    tags       = job["tags"]
    notion_id  = job.get("notion_page_id")

    if not os.path.exists(video_path):
        print(f"  [Queue] Missing video: {video_path} — removing job")
        os.remove(job["_file"])
        return

    result = upload_youtube(
        video_path, title_full,
        description=desc, tags=tags,
        thumbnail_path=thumb_path,
        lang=lang,
        publish_at=publish_at,
    )
    yt_url, yt_id = result if result else (None, None)
    upload_tiktok(video_path, title)

    # Update job: mark as uploaded, store video ID
    job["status"]           = "uploaded"
    job["youtube_video_id"] = yt_id
    job["youtube_url"]      = yt_url
    _save_job(job)

    print(f"  [Queue] Scheduled: [{lang.upper()}] {title[:40]} → live at {publish_at[:16]}")


# ── Catchup on startup ────────────────────────────────────────────────────────

def catchup() -> None:
    """
    Run on every startup:
    1. Jobs status='uploaded' + publish_at in past → check if YouTube went public
       → if yes: update Notion + delete job
    2. Jobs status='pending'  + publish_at in past → missed slot → upload now as public
    """
    print("[Catchup] Checking queue...")

    # --- Check uploaded jobs that should be live by now ---
    for job in _load_jobs("uploaded"):
        if not _is_past(job.get("publish_at", "")):
            continue
        vid_id    = job.get("youtube_video_id")
        notion_id = job.get("notion_page_id")
        yt_url    = job.get("youtube_url")
        title     = job.get("title", "")

        if vid_id and check_video_public(vid_id):
            print(f"  [Catchup] Now live: {title[:50]}")
            if notion_id:
                mark_uploaded(notion_id, youtube_url=yt_url)
            os.remove(job["_file"])
        else:
            print(f"  [Catchup] Not public yet: {title[:50]} (vid={vid_id})")

    # --- Handle missed pending slots ---
    missed = [j for j in _load_jobs("pending") if _is_past(j.get("publish_at", ""))]
    if missed:
        print(f"  [Catchup] {len(missed)} missed job(s) — uploading now as public")
    for job in missed:
        notion_id = job.get("notion_page_id")
        title     = job["title"]
        video_path = job["video_path"]

        if not os.path.exists(video_path):
            print(f"  [Catchup] Missing video: {video_path} — skip")
            os.remove(job["_file"])
            continue

        # Upload immediately as public (no publishAt)
        result = upload_youtube(
            video_path, job["title_full"],
            description=job["description"], tags=job["tags"],
            thumbnail_path=job["thumb_path"],
            lang=job["lang"],
            publish_at=None,
        )
        yt_url, yt_id = result if result else (None, None)
        upload_tiktok(video_path, title)

        if notion_id:
            mark_uploaded(notion_id, youtube_url=yt_url)

        os.remove(job["_file"])
        print(f"  [Catchup] Uploaded: {title[:50]}")

    pending_count  = len(_load_jobs("pending"))
    uploaded_count = len(_load_jobs("uploaded"))
    print(f"[Catchup] Done — pending={pending_count}, waiting_live={uploaded_count}")


# ── Auto-generate refill ──────────────────────────────────────────────────────

TARGET_PAIRS = 3   # keep at least this many pairs in queue

def _refill_queue() -> None:
    global _generating
    if _generating:
        return
    _generating = True
    try:
        from generate_batch import generate_one_pair, _count_queued_pairs, _future_slots
        existing = _count_queued_pairs()
        need     = max(0, TARGET_PAIRS - existing)
        if need == 0:
            print(f"  [Refill] Queue already has {existing} pair(s) — skip")
            return
        print(f"  [Refill] Generating {need} new pair(s)...")
        slots = _future_slots(existing, need)
        for i, slot in enumerate(slots):
            try:
                generate_one_pair(i, slot)
            except Exception as e:
                print(f"  [Refill] Pair {i+1} failed: {e}")
        print(f"  [Refill] Done")
    except Exception as e:
        print(f"  [Refill] Error: {e}")
    finally:
        _generating = False


def _refill_in_background() -> None:
    t = threading.Thread(target=_refill_queue, daemon=True)
    t.start()


# ── Scheduled slot ────────────────────────────────────────────────────────────

def post_slot(hour: int) -> None:
    now        = datetime.now(BKK)
    publish_at = now.replace(hour=hour, minute=0, second=0, microsecond=0).isoformat()

    print(f"\n{'='*55}")
    print(f"[Scheduler] Slot {hour:02d}:00 — {publish_at[:16]}")

    # Pick pending jobs whose publish_at matches this slot (or oldest if none match)
    all_pending = _load_jobs("pending")
    jobs = [j for j in all_pending
            if j.get("publish_at", "")[:13] == publish_at[:13]]
    if not jobs:
        jobs = all_pending[:2]

    if not jobs:
        print("  [Queue] Empty — run: python generate_batch.py")
        return

    for job in jobs:
        try:
            _upload_job(job, publish_at)
        except Exception as e:
            print(f"  [Queue] Upload failed: {e}")

    remaining = len(_load_jobs("pending"))
    pairs     = remaining // 2
    print(f"  [Queue] Pending: {remaining} jobs ({pairs} pair(s))")
    if pairs < TARGET_PAIRS:
        print(f"  [Queue] Low ({pairs}/{TARGET_PAIRS}) — auto-generating refill...")
        _refill_in_background()


# ── Main ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    catchup()   # Always run on startup

    scheduler = BlockingScheduler(timezone="Asia/Bangkok")
    scheduler.add_job(lambda: post_slot(8),  CronTrigger(hour=8,  minute=0))
    scheduler.add_job(lambda: post_slot(12), CronTrigger(hour=12, minute=0))
    scheduler.add_job(lambda: post_slot(19), CronTrigger(hour=19, minute=0))

    # Re-check "uploaded" jobs every hour
    scheduler.add_job(catchup, CronTrigger(minute=0))

    pending_count = len(_load_jobs("pending"))
    print(f"\nScheduler started — posting at 08:00, 12:00, 19:00 Bangkok time")
    print(f"Queue: {pending_count} pending jobs")
    print("Press Ctrl+C to stop\n")

    try:
        scheduler.start()
    except KeyboardInterrupt:
        print("Scheduler stopped")
