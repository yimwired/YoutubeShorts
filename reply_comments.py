"""Scan recent uploaded videos, generate Claude-style replies to the top
unanswered comment per video, and (optionally) post them.

Dry-run by default. To actually post:
    set COMMENT_REPLY_ENABLED=1
    python reply_comments.py --live

Defaults are conservative: 1 reply per video, only videos uploaded in the
last `--days` window (default 7), only top comment that meets
`--min-likes` (default 1), skipped if we already replied.
"""

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

from src.comment_replier import (
    get_service, list_top_comments, generate_reply, post_reply,
    _our_channel_id,
)

QUEUE_DIR = "queue"
STATE_FILE = "comment_reply_state.json"  # track which comment ids we've replied to


def _load_state() -> dict:
    try:
        with open(STATE_FILE, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {"replied_comment_ids": []}


def _save_state(state: dict) -> None:
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2, ensure_ascii=False)


def _recent_uploaded_jobs(days: int) -> list[dict]:
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    jobs = []
    for f in glob.glob(os.path.join(QUEUE_DIR, "job_*.json")):
        try:
            with open(f, encoding="utf-8") as fp:
                j = json.load(fp)
        except Exception:
            continue
        if j.get("status") != "uploaded":
            continue
        vid = j.get("youtube_video_id")
        if not vid:
            continue
        up_at = j.get("uploaded_at") or j.get("created_at")
        try:
            t = datetime.fromisoformat(up_at)
            if t.tzinfo is None:
                t = t.replace(tzinfo=timezone.utc)
            if t < cutoff:
                continue
        except Exception:
            continue
        jobs.append(j)
    # Newest first
    jobs.sort(key=lambda j: j.get("uploaded_at") or "", reverse=True)
    return jobs


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=7,
                    help="Only scan videos uploaded in the last N days")
    ap.add_argument("--per-video", type=int, default=1,
                    help="Max replies to post per video")
    ap.add_argument("--min-likes", type=int, default=1,
                    help="Skip comments with fewer likes than this")
    ap.add_argument("--max-fetch", type=int, default=10,
                    help="commentThreads.list maxResults per video")
    ap.add_argument("--live", action="store_true",
                    help="Actually post replies. Also requires COMMENT_REPLY_ENABLED=1.")
    args = ap.parse_args()

    live = args.live and os.getenv("COMMENT_REPLY_ENABLED") == "1"
    if args.live and not live:
        print("[reply_comments] --live ignored: COMMENT_REPLY_ENABLED is not 1")

    print(f"[reply_comments] mode = {'LIVE' if live else 'DRY-RUN'}")

    youtube = get_service()
    if not youtube:
        print("[reply_comments] no YouTube service -- abort")
        return

    jobs = _recent_uploaded_jobs(args.days)
    print(f"[reply_comments] {len(jobs)} uploaded videos within last {args.days}d")
    if not jobs:
        return

    state         = _load_state()
    already       = set(state.get("replied_comment_ids") or [])
    our_cid       = _our_channel_id(youtube)
    posted_count  = 0
    skipped_count = 0

    for j in jobs:
        vid_id     = j["youtube_video_id"]
        title      = j.get("title", "")
        desc       = j.get("description", "")
        lang_hint  = j.get("lang", "en")

        try:
            comments = list_top_comments(youtube, vid_id, args.max_fetch)
        except Exception as e:
            print(f"\n[{vid_id}] list_top_comments error: {e}")
            continue

        if not comments:
            if os.getenv("VERBOSE") == "1":
                print(f"[{vid_id}] no comments")
            continue

        if os.getenv("VERBOSE") == "1":
            print(f"\n[{vid_id}] {title[:60]} -- {len(comments)} comments")
            for c in comments[:3]:
                print(f"    raw: ♥{c['like_count']} we_replied={c['we_replied']} "
                      f"id={c['comment_id'][:20]} -- {c['text'][:60]}")

        candidates = [c for c in comments
                      if not c["we_replied"]
                      and c["comment_id"] not in already
                      and c["like_count"] >= args.min_likes
                      and not (our_cid and c["author_channel_id"] == our_cid)]
        if not candidates:
            continue

        print(f"\n[{vid_id}] {title[:70]}")
        for c in candidates[:args.per_video]:
            preview = c["text"].replace("\n", " ")[:100]
            print(f"  - {c['author']} (♥{c['like_count']}): {preview}")
            try:
                reply = generate_reply(c["text"], title, desc, lang=lang_hint)
            except Exception as e:
                print(f"    [gen] failed: {e}")
                continue
            print(f"    -> {reply}")

            if not live:
                skipped_count += 1
                continue

            new_id = post_reply(youtube, c["comment_id"], reply)
            if new_id:
                already.add(c["comment_id"])
                posted_count += 1
                print(f"    [posted] id={new_id}")
                time.sleep(2)  # gentle pacing -- YouTube quota / spam heuristics

    state["replied_comment_ids"] = sorted(already)
    if live:
        _save_state(state)

    print(f"\n[reply_comments] done. posted={posted_count} dry-run={skipped_count}")


if __name__ == "__main__":
    main()
