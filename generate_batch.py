"""
Generate the day's Thai explainer short, queue it, and upload it with a
scheduled publish time.

Usage: python generate_batch.py [N]   (default N=1 video)

One Thai video a day at noon, since 2026-08-16. The previous shape was
three EN+TH pairs a day across 08:00/12:00/19:00. Sixty days of analytics
killed it: Thai ran a median 196 views at 59.7% retention against
English's 66 views at 33.4%, and the three slots were within noise of each
other (132 / 98 / 104 median). Volume and slot timing were not the
constraint -- per-video quality was. So the render budget now goes into
one video instead of six.
"""
import sys
import os
import re
import json
import glob
import time
sys.stdout.reconfigure(encoding="utf-8")

from dotenv import load_dotenv
load_dotenv()

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from src.ai_broll import replace_with_ai_clips
from src.generator import generate_explainer_script, _next_bucket
from src.research import get_brief
from src.footage import fetch_multiple_clips
from src.tts import generate_voiceover
from src.thumbnail import create_thumbnail
from src.music import get_track
from src.notion_logger import log_scheduled, mark_uploaded
from src.uploader import upload_youtube, upload_tiktok
from src.topic_history import load_history, save_topic
from src.entity_images import fetch_entity_image
from main import make_video

OUTPUT_DIR = "output"
QUEUE_DIR  = "queue"
BKK        = ZoneInfo("Asia/Bangkok")
POST_HOURS  = [12]
SLOT_STYLES = {12: "explainer"}

os.makedirs(OUTPUT_DIR, exist_ok=True)
os.makedirs(QUEUE_DIR, exist_ok=True)

_SERIES_STATE_FILE = "series_state.json"
_SERIES_STOPWORDS  = {"the", "of", "that", "and", "a", "to", "in", "we", "are", "is"}

# Buckets for the days no live trend is worth explaining. Every entry is
# phrased as an origin question, because "where did this come from" is the
# format -- a bucket like "space facts" would pull the writer back toward
# the generic did-you-know videos this replaced.
EXPLAINER_CATEGORIES = [
    "ที่มาของคำและสำนวนที่คนไทยใช้ทุกวัน",
    "ที่มาของอาหารไทยจานที่ทุกคนคิดว่ารู้จักดี",
    "จุดเริ่มต้นของแบรนด์ดังที่คนใช้ทุกวัน",
    "ที่มาของสิ่งของธรรมดาในบ้านที่ไม่มีใครสงสัย",
    "จุดกำเนิดของเกมและของเล่นที่เคยฮิตทั้งประเทศ",
    "ที่มาของประเพณีและความเชื่อไทยที่ทำตามกันมา",
    "เบื้องหลังสัญลักษณ์และโลโก้ที่เห็นทุกวัน",
    "ที่มาของกฎและมารยาทที่ไม่มีใครรู้ว่าใครตั้ง",
    "จุดเริ่มต้นของเทคโนโลยีที่อยู่ในมือถือทุกเครื่อง",
    "ที่มาของเพลงหรือเสียงที่ทุกคนจำได้แต่ไม่รู้ว่ามาจากไหน",
    "เรื่องจริงเบื้องหลังสถานที่ที่คนไทยผ่านทุกวัน",
    "ที่มาของหน่วยวัดและตัวเลขที่ใช้กันจนชิน",
]


def _series_tag(category: str) -> str:
    """Short uppercase series label (<=12 chars) from a bucket category.
    'deep ocean' -> 'DEEP OCEAN', 'space & universe' -> 'SPACE'."""
    words = [w for w in category.replace("&", " ").split()
             if w.lower() not in _SERIES_STOPWORDS]
    tag = ""
    for w in words:
        nxt = (tag + " " + w).strip()
        if len(nxt) > 12:
            break
        tag = nxt
    if not tag and words:
        tag = words[0][:12]
    return (tag or "FACTS").upper()


def _bump_series(category: str) -> int:
    """Increment and return the episode counter for `category`."""
    try:
        with open(_SERIES_STATE_FILE, encoding="utf-8") as f:
            state = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        state = {}
    n = state.get(category, 0) + 1
    state[category] = n
    try:
        with open(_SERIES_STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
    except OSError:
        pass
    return n


def _used_publish_slots() -> set[str]:
    """ISO strings of publish_at already present in queue (any status).

    Slot allocation diffs against this set so each future slot gets exactly
    one pair. Prevents two bugs the count-based predecessor suffered:
      - duplicate (two pairs racing for the same slot if batches ran twice
        in a day with the marker cleared)
      - silent skip (a count of 11 across non-contiguous slots advanced the
        offset past gaps like 24/8 + 24/12, leaving them empty forever)
    """
    used = set()
    for f in glob.glob(os.path.join(QUEUE_DIR, "job_*.json")):
        try:
            with open(f, encoding="utf-8") as fp:
                job = json.load(fp)
            pub = job.get("publish_at")
            if pub:
                used.add(pub)
        except Exception:
            pass
    return used


def _future_slots(used: set[str], n: int) -> list[str]:
    """Return next n future publish-time ISO strings not already in `used`."""
    now   = datetime.now(BKK)
    slots = []
    day   = 0
    while len(slots) < n and day < 30:
        for h in POST_HOURS:
            t = now.replace(hour=h, minute=0, second=0, microsecond=0) + timedelta(days=day)
            iso = t.isoformat()
            if t > now and iso not in used:
                slots.append(iso)
                if len(slots) == n:
                    break
        day += 1
    return slots


def _build_entity_overlays(data: dict, timestamp: int,
                           th_boundaries: list[dict]) -> list[dict]:
    """Download images for `data['entities']` and window them to the
    sentence that mentions each one.

    Each overlay = {"image_path", "start", "end"}. Entities with no
    downloadable image, or no boundary for their sentence, are skipped --
    a missing photo is not worth failing a render over.
    """
    entities  = data.get("entities") or []
    sentences = data.get("sentences") or []
    if not entities or not sentences or not th_boundaries:
        return []

    img_dir = os.path.join(OUTPUT_DIR, "entities", str(timestamp))
    os.makedirs(img_dir, exist_ok=True)

    overlays = []
    for i, ent in enumerate(entities[:5]):
        name = (ent.get("name") or "").strip()
        idx  = ent.get("sentence_idx")
        if not name or idx is None or not (0 <= idx < len(th_boundaries)):
            continue

        safe_name = re.sub(r"[^\w\-]", "_", name)[:30]
        img_path  = os.path.join(img_dir, f"ent_{i}_{safe_name}.jpg")
        # lang_hint="th": the explainer format leans on Thai subjects that
        # often exist only on Thai Wikipedia.
        if not fetch_entity_image(name, img_path, lang_hint="th"):
            print(f"  [entity] no image for '{name}' — skip")
            continue

        b = th_boundaries[idx]
        overlays.append({"image_path": img_path,
                         "start": round(float(b["start"]) + 0.05, 3),
                         "end":   round(float(b["end"]) + 0.4, 3)})
        print(f"  [entity] '{name}' → {os.path.basename(img_path)} @ sent {idx}")

    return overlays


def _normalize_hashtags(raw, default: list[str]) -> list[str]:
    """Coerce whatever the model returned into a clean tag list."""
    tags = raw or default
    if isinstance(tags, str):
        tags = [t.strip().lstrip("#")
                for t in tags.replace(",", " ").split() if t.strip()]
    tags = [str(t).strip().lstrip("#") for t in tags if str(t).strip()]
    if "shorts" not in [t.lower() for t in tags]:
        tags = ["shorts"] + tags
    return tags


def _title_with_tags(title: str, tags: list, max_len: int = 95) -> str:
    picked = ["#" + t for t in tags[:4] if t.lower() != "shorts"][:3]
    suffix = " " + " ".join(["#Shorts"] + picked)
    return (title[:max_len - len(suffix)] + suffix).strip()


def generate_one(index: int, publish_at: str) -> None:
    """Research, write, render, queue and upload one Thai explainer short."""
    timestamp = int(time.time()) + index * 10
    style     = SLOT_STYLES.get(datetime.fromisoformat(publish_at).hour,
                                "explainer")
    print(f"\n[Video {index+1}] ts={timestamp}  publish→{publish_at[:16]}  "
          f"style={style}")

    history = load_history()

    # Hybrid sourcing: a live trend when one is worth explaining, an
    # evergreen bucket topic when none is. Either way the facts come back
    # search-grounded -- the trend half is mostly post-cutoff material the
    # scriptwriter would otherwise invent.
    category = _next_bucket("explainer", EXPLAINER_CATEGORIES)
    brief    = get_brief(EXPLAINER_CATEGORIES, category, avoid=history,
                         use_trends=os.getenv("USE_TRENDING_TOPIC") != "0")
    if not brief:
        print("  ERROR: research produced no usable brief — skipping")
        return

    data = generate_explainer_script(brief, used_titles=history)

    title_th  = data["title_th"]
    script_th = data["script_th"]
    hook_th   = data.get("hook_th") or ""
    loop_th   = data.get("loop_th") or ""
    cta_th    = data.get("cta_th")  or ""
    thumb_txt = data.get("thumb_text_th") or hook_th

    series_tag = _series_tag(brief.source)
    episode    = _bump_series(brief.source)
    print(f"  Topic : {brief.topic}  ({brief.source})")
    print(f"  Title : {title_th}")
    print(f"  Hook  : {hook_th}   |  Series: {series_tag} #{episode}")

    clips = fetch_multiple_clips(data["keywords"], OUTPUT_DIR)
    if not clips:
        print("  ERROR: No footage — skipping")
        return

    # Generated stills for the two or three shots stock cannot serve --
    # the historical origin moment and the reveal. Failures keep the
    # stock clip, so this can only improve the render.
    clips = replace_with_ai_clips(clips, data.get("sentences", []),
                                  OUTPUT_DIR, timestamp)

    audio_th     = os.path.join(OUTPUT_DIR, f"audio_{timestamp}_th.mp3")
    sentences_th = [s.get("text_th", "") for s in data.get("sentences", [])]
    _, th_boundaries = generate_voiceover(script_th, audio_th, lang="th",
                                          style=style, sentences=sentences_th)

    from main import _sync_th_subs
    th_words = _sync_th_subs(script_th, audio_th,
                             sentences_th=sentences_th or None,
                             style=style, tts_boundaries=th_boundaries)

    music = get_track(data.get("music_mood", "dramatic"))

    thumb_keyword = data.get("thumbnail_keyword")
    ai_prompt     = data.get("thumbnail_prompt")
    thumb    = os.path.join(OUTPUT_DIR, f"thumb_{timestamp}_th.jpg")
    thumb_b  = os.path.join(OUTPUT_DIR, f"thumb_{timestamp}_th_b.jpg")
    # A: deterministic from prompt. B: same prompt, different seed --
    # the A/B candidate swapped in after 24h if CTR is weak
    # (see swap_thumbnails.py).
    seed_a = (hash(ai_prompt or title_th) % 99999) if ai_prompt else None
    seed_b = ((seed_a or 0) + 41337) % 99999 if ai_prompt else None
    create_thumbnail(None, title_th, thumb,
                     thai_ver=False, photo_keyword=thumb_keyword,
                     ai_prompt=ai_prompt, seed=seed_a,
                     series_tag=series_tag, episode=episode,
                     lang="th", badge_text=thumb_txt)
    if ai_prompt:
        create_thumbnail(None, title_th, thumb_b,
                         thai_ver=False, photo_keyword=thumb_keyword,
                         ai_prompt=ai_prompt, seed=seed_b,
                         series_tag=series_tag, episode=episode,
                         lang="th", badge_text=thumb_txt)
    else:
        thumb_b = None

    overlays = _build_entity_overlays(data, timestamp, th_boundaries)

    # title_card / outro_card off: both bracket the video with silent
    # frames, which is exactly where Shorts viewers leave.
    final_th = make_video(clips, audio_th, title_th, th_words, timestamp,
                          "th", music, thumb_path=thumb, content_style=style,
                          entity_overlays=overlays,
                          hook_text=hook_th, loop_text=loop_th,
                          cta_text=cta_th,
                          title_card=False, outro_card=False)

    # Lead the description with the comment-bait question: it is the first
    # line a viewer sees when they expand, and comments are the engagement
    # signal this channel is weakest on.
    desc_th = data.get("description_th", script_th)
    if cta_th:
        desc_th = f"{cta_th}\n\n{desc_th}"

    hashtags = _normalize_hashtags(data.get("hashtags_th"),
                                   ["shorts", "เรื่องน่ารู้", "ความรู้"])
    title_full = _title_with_tags(title_th, hashtags)

    notion_page_id = (None if os.getenv("DRY_RUN") == "1"
                      else log_scheduled(f"[TH] {title_th}",
                                         publish_at=publish_at,
                                         lang="th", topic=brief.topic))

    job = {
        "timestamp":      timestamp,
        "lang":           "th",
        "title":          title_th,
        "title_full":     title_full,
        "description":    desc_th,
        "tags":           hashtags,
        "topic":          brief.topic,
        "brief_source":   brief.source,
        "video_path":     final_th,
        "thumb_path":     thumb,
        "thumb_path_b":   thumb_b,
        "ab_swapped":     False,
        "publish_at":     publish_at,
        "notion_page_id": notion_page_id,
        "created_at":     datetime.now(BKK).isoformat(),
    }
    job_path = os.path.join(QUEUE_DIR, f"job_{timestamp}_th.json")
    with open(job_path, "w", encoding="utf-8") as f:
        json.dump(job, f, ensure_ascii=False, indent=2)
    print(f"  Queued: {job_path}")

    # Upload now; YouTube publishes at publish_at on its own clock.
    # DRY_RUN=1 renders and queues everything but never touches the channel.
    if os.getenv("DRY_RUN") == "1":
        print("  [DRY_RUN] upload skipped")
        result = None
    else:
        result = upload_youtube(final_th, title_full,
                                description=desc_th, tags=hashtags,
                                thumbnail_path=thumb, lang="th",
                                publish_at=publish_at, seed_comment=cta_th)

    # TikTok stays off until the official Content Posting API is approved:
    # tiktok-uploader 1.2.0's xpath selectors broke against TikTok's 2026-05
    # UI. Set TIKTOK_ENABLED=1 once src/tiktok_api.py is live.
    tt_url = None
    if os.getenv("TIKTOK_ENABLED") == "1":
        tt_url = upload_tiktok(final_th, title_th, publish_at=publish_at)

    if result:
        yt_url, yt_id = result
        job["status"]           = "uploaded"
        job["youtube_url"]      = yt_url
        job["youtube_video_id"] = yt_id
        job["tiktok_url"]       = tt_url
        # Wall-clock insert time, not publish_at -- swap_thumbnails.py
        # measures the 24h CTR window from here.
        job["uploaded_at"]      = datetime.now(BKK).isoformat()
        with open(job_path, "w", encoding="utf-8") as f:
            json.dump(job, f, ensure_ascii=False, indent=2)
        if notion_page_id:
            mark_uploaded(notion_page_id, youtube_url=yt_url, tiktok_url=tt_url)
    elif os.getenv("DRY_RUN") != "1":
        print("  [Upload] Failed — job left queued for retry")

    save_topic(title_th)
    save_topic(brief.topic)

    for path in clips + [audio_th]:
        if os.path.exists(path):
            os.remove(path)


if __name__ == "__main__":
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 1
    print(f"[Batch] Generating {n} video(s)...")

    used  = _used_publish_slots()
    slots = _future_slots(used, n)
    print(f"  Existing queue slots: {len(used)}")
    print(f"  Planned slots : {[s[:16] for s in slots]}")

    n_ok = 0
    for i, slot in enumerate(slots):
        try:
            generate_one(i, slot)
            n_ok += 1
        except Exception as e:
            print(f"  [Batch] Video {i+1} failed: {type(e).__name__}: {e}")

    print(f"\n[Batch] Done — {n_ok}/{len(slots)} video(s) queued")

    # Exit non-zero when nothing succeeded so run_batch.ps1 does NOT write the
    # daily-once marker -- a later trigger then retries today instead of
    # skipping. A transient TTS or research outage self-heals on the next fire.
    if slots and n_ok == 0:
        sys.exit(1)
