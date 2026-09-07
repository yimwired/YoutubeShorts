"""
Generate the day's Thai explainer short, queue it, and upload it with a
scheduled publish time.

Usage: python generate_batch.py [N]   (default: one per slot in POST_HOURS)

Two Thai videos a day since 2026-09-08: a behaviour clip at 08:00 and the
explainer at 12:00. Before that it was the explainer alone, and before
that, until 2026-08-16, three EN+TH pairs a day across 08:00/12:00/19:00.
Sixty days of analytics killed the pairs: Thai ran a median 196 views at
59.7% retention against English's 66 views at 33.4%, and the three slots
were within noise of each other (132 / 98 / 104 median). Volume was not
what the channel was short of -- per-video quality was.

The second slot is back now for a different reason: the explainer's own
numbers moved once it was rewritten (retention median 44 to 49.8%), and
the morning format tests a separate question -- whether footage of real
people holds a Thai feed better than stock b-roll of objects does.
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
from src.generator import (generate_explainer_script, generate_human_script,
                           count_thai_words, _next_bucket)
from src.research import get_brief, research_human
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
# Two formats a day, deliberately unlike each other. The noon explainer is
# the one with five days of measured retention behind it; the 08:00 slot is
# the same six-sentence shape pointed at the viewer's own behaviour, on
# stock footage of real people rather than generated stills.
POST_HOURS  = [8, 12]
SLOT_STYLES = {8: "human", 12: "explainer"}

os.makedirs(OUTPUT_DIR, exist_ok=True)
os.makedirs(QUEUE_DIR, exist_ok=True)

_SERIES_STATE_FILE = "series_state.json"
_SERIES_STOPWORDS  = {"the", "of", "that", "and", "a", "to", "in", "we", "are", "is"}

# One series name and one running episode number per format. The tag used to
# be derived per bucket, which produced "TREND" and "EVERGREEN" -- printed on
# the thumbnail, where it told a viewer nothing, and split the count across
# two arbitrary halves. The counters live under the style key in
# series_state.json, so each format numbers its own episodes.
SERIES_NAMES = {
    "explainer": "ที่มาของ",
    "human":     "ทำไมเราถึง",
}
SERIES_NAME = SERIES_NAMES["explainer"]   # legacy import in test_explainer.py
SERIES_KEY  = "explainer"

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


# Morning buckets. Every entry has to satisfy three things at once: the
# viewer does it themselves, a real study explains why, and stock footage of
# an ordinary person doing it exists. That last one is what rules out most
# of psychology -- there is no footage of a cognitive bias.
HUMAN_CATEGORIES = [
    "นิสัยตอนใช้มือถือที่ทุกคนทำโดยไม่รู้ตัว",
    "พฤติกรรมตอนกินข้าวที่คนไทยทำเหมือนกันหมด",
    "สิ่งที่ร่างกายทำตอนนอนและตอนตื่น",
    "ปฏิกิริยาเวลาเจอคนแปลกหน้าหรืออยู่ในที่สาธารณะ",
    "เรื่องความจำกับสมาธิที่คนเข้าใจผิดมาตลอด",
    "อาการทางร่างกายที่เกิดเองโดยห้ามไม่ได้",
    "พฤติกรรมเวลาโกรธ เขิน หรือกลัว",
    "นิสัยตอนทำงานหรือเรียนที่คนคิดว่าตัวเองคนเดียวที่เป็น",
    "วิธีที่คนตัดสินใจซื้อของโดยไม่รู้ตัว",
    "พฤติกรรมกับคนในครอบครัวและเพื่อนสนิท",
    "สิ่งที่คนทำเวลาอยู่คนเดียวแล้วไม่เคยบอกใคร",
    "นิสัยการเดินทางและการรอคอย",
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


def _description_with_tags(description: str, tags: list, n: int = 5) -> str:
    """Put the hashtags at the foot of the description instead of the title.

    YouTube renders the first three hashtags of a description as links above
    the title, so they are just as findable there -- and the Shorts player
    truncates the title after roughly forty characters, which the old
    "title #Shorts #a #b #c" shape spent on tags the viewer did not need to
    read. The title now gets all forty of them.
    """
    picked = ["#" + t for t in tags if t.lower() != "shorts"][:n]
    # The model already signs off its Thai description with "#Shorts"; leaving
    # it there would print the tag twice in a row.
    body = description.replace("#Shorts", "").replace("#shorts", "").rstrip()
    return body + chr(10) + chr(10) + " ".join(["#Shorts"] + picked)


def generate_one(index: int, publish_at: str) -> None:
    """Research, write, render, queue and upload one Thai short.

    Which format depends on the slot: SLOT_STYLES maps the publish hour to
    "human" or "explainer", and that choice picks the research call, the
    scriptwriter and the series badge.
    """
    timestamp = int(time.time()) + index * 10
    style     = SLOT_STYLES.get(datetime.fromisoformat(publish_at).hour,
                                "explainer")
    print(f"\n[Video {index+1}] ts={timestamp}  publish→{publish_at[:16]}  "
          f"style={style}")

    history = load_history()

    if style == "human":
        # No trend half: a behaviour is evergreen by definition, and a live
        # trend is what the noon explainer is for.
        category = _next_bucket("human", HUMAN_CATEGORIES)
        brief    = research_human(category, avoid=history)
    else:
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

    data = (generate_human_script(brief, used_titles=history)
            if style == "human"
            else generate_explainer_script(brief, used_titles=history))

    title_th  = data["title_th"]
    script_th = data["script_th"]
    hook_th   = data.get("hook_th") or ""
    loop_th   = data.get("loop_th") or ""
    cta_th    = data.get("cta_th")  or ""
    thumb_txt = data.get("thumb_text_th") or hook_th

    series_tag = SERIES_NAMES[style]
    episode    = _bump_series(style)
    print(f"  Topic : {brief.topic}  ({brief.source})")
    print(f"  Title : {title_th}")
    print(f"  Hook  : {hook_th}   |  Series: {series_tag} #{episode}")

    clips = fetch_multiple_clips(data["keywords"], OUTPUT_DIR)
    if not clips:
        print("  ERROR: No footage — skipping")
        return

    # Generated stills for the two or three shots stock cannot serve --
    # the historical origin moment and the reveal. Failures keep the
    # stock clip, so this can only improve the render. The morning format
    # skips it outright: its premise is footage of real people, and a
    # generated face is the one thing it cannot show.
    if style != "human":
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

    # Length is the lever with the clearest effect on reach, and until now
    # nothing recorded what a published clip was actually made of -- the word
    # count behind a 49s video could only be recovered by re-watching it.
    # Both numbers go into the job so the next comparison is a read.
    word_count = count_thai_words(script_th)
    speech_sec = round(th_words[-1]["end"], 1) if th_words else 0.0
    print(f"  Length: {word_count} words -> {speech_sec}s speech")

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
                          series_label=series_tag, episode=episode,
                          title_card=False, outro_card=False)

    # Lead the description with the comment-bait question: it is the first
    # line a viewer sees when they expand, and comments are the engagement
    # signal this channel is weakest on.
    desc_th = data.get("description_th", script_th)
    if cta_th:
        desc_th = f"{cta_th}\n\n{desc_th}"

    hashtags   = _normalize_hashtags(data.get("hashtags_th"),
                                     ["shorts", "เรื่องน่ารู้", "ความรู้"])
    desc_th    = _description_with_tags(desc_th, hashtags)
    title_full = title_th.strip()[:100]

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
        # What the clip was actually made of. Kept so a later look at which
        # lengths travelled is a query over the queue, not a re-watch.
        "script":         script_th,
        "word_count":     word_count,
        "speech_sec":     speech_sec,
        # The research the script was allowed to draw on, verbatim. The
        # no-invention rule is a prompt rule, and prompt rules do not hold on
        # gemini-2.5-flash-lite, which is what the daily job falls back to
        # most days. When a viewer disputes a fact, this is the only way to
        # tell whether the writer made it up or the research did.
        "brief_raw":      brief.raw,
        # Posted by seed_comments.py once the video is public -- see the note
        # in src/uploader.py about why it cannot go up at insert time.
        "cta":            cta_th,
        "video_path":     final_th,
        "thumb_path":     thumb,
        "thumb_path_b":   thumb_b,
        "ab_swapped":     False,
        "publish_at":     publish_at,
        "notion_page_id": notion_page_id,
        "created_at":     datetime.now(BKK).isoformat(),
    }
    # A dry run must not leave a job behind under the name the slot allocator
    # reads. DRY_RUN skips the upload but still produced a queue file, and the
    # workflow commits queue/ unconditionally, so a validation run parked an
    # unuploaded job on the next publish slot -- _used_publish_slots counted
    # it, the scheduled run stepped past to the day after, and that slot went
    # out empty. The payload is still written, under a name nothing allocates
    # against, so a dry run stays inspectable.
    prefix   = "dryrun" if os.getenv("DRY_RUN") == "1" else "job"
    job_path = os.path.join(QUEUE_DIR, f"{prefix}_{timestamp}_th.json")
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
                                publish_at=publish_at)

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
    n = int(sys.argv[1]) if len(sys.argv) > 1 else len(POST_HOURS)
    print(f"[Batch] Generating {n} video(s)...")

    used  = _used_publish_slots()
    slots = _future_slots(used, n)

    # The backup run exists so a failed morning run does not cost the whole
    # day -- three consecutive mornings failed before it was added. It must
    # not quietly produce tomorrow's video on the days the morning run worked,
    # so it only proceeds while today's slot is still empty.
    if os.getenv("ONLY_TODAY") == "1":
        today = datetime.now(BKK).date()
        slots = [x for x in slots
                 if datetime.fromisoformat(x).date() == today]
        if not slots:
            print("[Batch] today's slot is already filled — nothing to do")
            sys.exit(0)

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

    # Exit non-zero when a planned slot went unfilled, so run_batch.ps1 does
    # NOT write the daily-once marker and the workflow's retry fires -- a
    # later attempt then covers today instead of skipping it. A transient TTS
    # or research outage self-heals on the next run. Since the day has two
    # slots, "some succeeded" has to count as failure too, or a morning where
    # only one format rendered would look like a clean day and never retry.
    # The retry cannot double-post: _used_publish_slots already holds the
    # slots that were filled, so a second pass only sees the empty ones.
    if n_ok < len(slots):
        sys.exit(1)
