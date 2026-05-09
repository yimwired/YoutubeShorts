"""
Pre-generate videos and save to queue/ for scheduled upload.
Usage: python generate_batch.py [N]   (default N=3 pairs = 6 videos)
"""
import sys
import os
import json
import glob
import time
sys.stdout.reconfigure(encoding="utf-8")

from dotenv import load_dotenv
load_dotenv()

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from src.generator import generate_fact_script
from src.footage import fetch_multiple_clips
from src.tts import generate_voiceover
from src.captions import get_word_timestamps
from src.thumbnail import create_thumbnail
from src.editor import create_short, _clip_duration
from src.trends import get_trending_topic
from src.music import get_track
from src.notion_logger import log_scheduled, mark_uploaded
from src.uploader import upload_youtube
from src.topic_history import load_history, save_topic
from main import _make_th_subs, make_video

OUTPUT_DIR = "output"
QUEUE_DIR  = "queue"
BKK        = ZoneInfo("Asia/Bangkok")
POST_HOURS = [8, 12, 19]

os.makedirs(QUEUE_DIR, exist_ok=True)


def _count_queued_pairs() -> int:
    """Count pairs whose publishAt is still in the future (not yet live)."""
    now   = datetime.now(BKK)
    files = glob.glob(os.path.join(QUEUE_DIR, "job_*.json"))
    timestamps = set()
    for f in files:
        try:
            with open(f, encoding="utf-8") as fp:
                job = json.load(fp)
            pub = job.get("publish_at", "")
            if pub and datetime.fromisoformat(pub) > now:
                timestamps.add(os.path.basename(f).split("_")[1])
        except Exception:
            pass
    return len(timestamps)


def _future_slots(start_offset: int, n: int) -> list[str]:
    """Return n future publish-time ISO strings starting after start_offset slots."""
    now   = datetime.now(BKK)
    slots = []
    day   = 0
    while len(slots) < start_offset + n:
        for h in POST_HOURS:
            t = now.replace(hour=h, minute=0, second=0, microsecond=0) + timedelta(days=day)
            if t > now:
                slots.append(t.isoformat())
                if len(slots) == start_offset + n:
                    break
        day += 1
    return slots[start_offset:]


def generate_one_pair(index: int, publish_at: str) -> None:
    timestamp = int(time.time()) + index * 10
    print(f"\n[Batch {index+1}] ts={timestamp}  publish→{publish_at[:16]}")

    topic = get_trending_topic()
    print(f"  Topic: {topic or '(none)'}")

    data      = generate_fact_script(topic=topic, used_titles=load_history())
    title_en  = data["title_en"]
    title_th  = data["title_th"]
    script_en = data["script_en"]
    script_th = data["script_th"]
    keywords  = data["keywords"]
    print(f"  EN: {title_en}")
    print(f"  TH: {title_th}")

    clips = fetch_multiple_clips(keywords, OUTPUT_DIR)
    if not clips:
        print("  ERROR: No footage — skipping")
        return

    audio_en = os.path.join(OUTPUT_DIR, f"audio_{timestamp}_en.mp3")
    audio_th = os.path.join(OUTPUT_DIR, f"audio_{timestamp}_th.mp3")
    generate_voiceover(script_en, audio_en, lang="en")
    _, th_boundaries = generate_voiceover(script_th, audio_th, lang="th")

    en_words = get_word_timestamps(audio_en, lang="en")
    th_words = _make_th_subs(script_th, _clip_duration(audio_th))

    mood  = data.get("music_mood", "dramatic")
    music = get_track(mood)

    thumb_keyword = data.get("thumbnail_keyword")
    ai_prompt     = data.get("thumbnail_prompt")
    thumb_en = os.path.join(OUTPUT_DIR, f"thumb_{timestamp}_en.jpg")
    thumb_th = os.path.join(OUTPUT_DIR, f"thumb_{timestamp}_th.jpg")
    create_thumbnail(None, title_en, thumb_en,
                     thai_ver=False, photo_keyword=thumb_keyword, ai_prompt=ai_prompt)
    create_thumbnail(None, title_en, thumb_th,
                     thai_ver=True, photo_keyword=thumb_keyword, ai_prompt=ai_prompt)

    final_en = make_video(clips, audio_en, title_en, en_words, timestamp, "en", music,
                          thumb_path=thumb_en)
    final_th = make_video(clips, audio_th, title_en, th_words, timestamp, "th", music,
                          thumb_path=thumb_th)

    desc_en     = data.get("description", script_en)
    desc_th     = data.get("description_th", script_th)
    hashtags_en = data.get("hashtags", ["shorts", "facts", "didyouknow"])
    hashtags_th = data.get("hashtags_th", ["shorts", "เรื่องน่ารู้", "ความรู้"])

    if "shorts" not in [h.lower() for h in hashtags_en]:
        hashtags_en = ["shorts"] + hashtags_en
    if "shorts" not in [h.lower() for h in hashtags_th]:
        hashtags_th = ["shorts"] + hashtags_th

    def _title_with_tags(title: str, tags: list, max_len: int = 95) -> str:
        picked = ["#" + t for t in tags[:4] if t.lower() != "shorts"][:3]
        suffix = " " + " ".join(["#Shorts"] + picked)
        return (title[:max_len - len(suffix)] + suffix).strip()

    for lang, title, title_full, video_path, thumb_path, desc, hashtags in [
        ("en", title_en, _title_with_tags(title_en, hashtags_en),
         final_en, thumb_en, desc_en, hashtags_en),
        ("th", title_th, _title_with_tags(title_th, hashtags_th),
         final_th, thumb_th, desc_th, hashtags_th),
    ]:
        # Log to Notion immediately as "Scheduled"
        notion_page_id = log_scheduled(
            f"[{lang.upper()}] {title}",
            publish_at=publish_at,
            lang=lang,
            topic=topic or "",
        )

        job = {
            "timestamp":      timestamp,
            "lang":           lang,
            "title":          title,
            "title_full":     title_full,
            "description":    desc,
            "tags":           hashtags,
            "topic":          topic or "",
            "video_path":     video_path,
            "thumb_path":     thumb_path,
            "publish_at":     publish_at,
            "notion_page_id": notion_page_id,
            "created_at":     datetime.now(BKK).isoformat(),
        }
        job_path = os.path.join(QUEUE_DIR, f"job_{timestamp}_{lang}.json")
        with open(job_path, "w", encoding="utf-8") as f:
            json.dump(job, f, ensure_ascii=False, indent=2)
        print(f"  Queued: {job_path}")

        # Upload immediately — YouTube will publish at publish_at automatically
        result = upload_youtube(
            video_path, title_full,
            description=desc, tags=hashtags,
            thumbnail_path=thumb_path,
            lang=lang, publish_at=publish_at,
        )
        if result:
            yt_url, yt_id = result
            job["status"]           = "uploaded"
            job["youtube_url"]      = yt_url
            job["youtube_video_id"] = yt_id
            with open(job_path, "w", encoding="utf-8") as f:
                json.dump(job, f, ensure_ascii=False, indent=2)
            if notion_page_id:
                mark_uploaded(notion_page_id, youtube_url=yt_url)
        else:
            print(f"  [Upload] Failed — will retry via scheduler")

    save_topic(title_en)

    # Cleanup audio + clips
    for clip in clips:
        if os.path.exists(clip): os.remove(clip)
    for f in [audio_en, audio_th]:
        if os.path.exists(f): os.remove(f)


if __name__ == "__main__":
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 3
    print(f"[Batch] Generating {n} pair(s)...")

    existing = _count_queued_pairs()
    slots    = _future_slots(existing, n)
    print(f"  Existing queue: {existing} pair(s)")
    print(f"  Planned slots : {[s[:16] for s in slots]}")

    for i, slot in enumerate(slots):
        try:
            generate_one_pair(i, slot)
        except Exception as e:
            print(f"  [Batch] Pair {i+1} failed: {e}")

    print(f"\n[Batch] Done — {n} pair(s) queued")
