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
from src.uploader import upload_youtube, upload_tiktok
from src.topic_history import load_history, save_topic
from src.entity_images import fetch_entity_image
from main import _make_th_subs, make_video, _silence_boundaries

OUTPUT_DIR = "output"
QUEUE_DIR  = "queue"
BKK        = ZoneInfo("Asia/Bangkok")
POST_HOURS  = [8, 12, 19]
SLOT_STYLES = {8: "trending", 12: "chaos", 19: "narrative"}

os.makedirs(OUTPUT_DIR, exist_ok=True)
os.makedirs(QUEUE_DIR, exist_ok=True)


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
                           audio_en: str, audio_th: str,
                           th_boundaries: list[dict]) -> tuple[list[dict], list[dict]]:
    """Download images for `data['entities']` and compute per-sentence
    overlay windows for EN + TH. Returns (en_overlays, th_overlays).

    Each overlay = {"image_path", "start", "end"}. Sentences without a
    downloadable image are silently skipped.
    """
    entities = data.get("entities") or []
    sentences = data.get("sentences") or []
    if not entities or not sentences:
        return [], []

    # TH per-sentence timing comes from edge-tts boundary tracking (ground truth).
    # EN single-call TTS has no boundary stream, so detect inter-sentence pauses
    # in the rendered audio via ffmpeg silencedetect. Falls back to even split if
    # pauses are too few (rare — Ana/Aria speech has clean ~0.3s gaps).
    try:
        import subprocess
        r = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", audio_en],
            capture_output=True, text=True
        )
        en_dur = float(r.stdout.strip() or 30.0)
    except Exception:
        en_dur = 30.0

    n_sent = max(len(sentences), 1)
    en_bounds = _silence_boundaries(audio_en, n_sent) or []
    if len(en_bounds) != n_sent:
        # Fallback: even split across audio duration
        per = en_dur / n_sent
        en_bounds = [{"start": i * per, "end": (i + 1) * per} for i in range(n_sent)]
        print(f"  [entity] EN silence detect → fallback even-split ({n_sent} sentences)")
    else:
        print(f"  [entity] EN silence boundaries OK ({n_sent} sentences)")

    img_dir = os.path.join(OUTPUT_DIR, "entities", str(timestamp))
    os.makedirs(img_dir, exist_ok=True)

    en_out, th_out = [], []
    for i, ent in enumerate(entities[:5]):
        name = (ent.get("name") or "").strip()
        idx  = ent.get("sentence_idx")
        if not name or idx is None or idx < 0 or idx >= len(sentences):
            continue

        img_path = os.path.join(img_dir, f"ent_{i}_{name[:30].replace(' ', '_')}.jpg")
        got = fetch_entity_image(name, img_path, lang_hint="en")
        if not got:
            print(f"  [entity] no image for '{name}' — skip")
            continue
        print(f"  [entity] '{name}' → {os.path.basename(img_path)} @ sent {idx}")

        # EN window: real sentence boundary from silencedetect
        eb = en_bounds[idx]
        en_out.append({"image_path": img_path,
                       "start": round(float(eb["start"]) + 0.05, 3),
                       "end":   round(min(float(eb["end"]) + 0.4, en_dur - 0.1), 3)})

        # TH window: use edge-tts boundary if available
        if idx < len(th_boundaries):
            b = th_boundaries[idx]
            th_out.append({"image_path": img_path,
                           "start": float(b["start"]) + 0.05,
                           "end":   float(b["end"])   + 0.4})
        else:
            th_out.append(en_out[-1].copy())

    return en_out, th_out


def generate_one_pair(index: int, publish_at: str) -> None:
    timestamp = int(time.time()) + index * 10
    slot_hour = datetime.fromisoformat(publish_at).hour
    style     = SLOT_STYLES.get(slot_hour, "trending")
    print(f"\n[Batch {index+1}] ts={timestamp}  publish→{publish_at[:16]}  style={style}")

    # Google Trends RSS biases hard toward news/sports (Iran war, NASCAR,
    # Liverpool etc.) which the generator then has to bend into a "fact".
    # Default to category-bucket round-robin in src/generator.py; opt back
    # in to RSS via USE_TRENDING_TOPIC=1 for time-sensitive runs.
    use_rss = os.getenv("USE_TRENDING_TOPIC") == "1"
    topic   = get_trending_topic() if (style == "trending" and use_rss) else None
    print(f"  Topic: {topic or f'(bucket — {style})'}")

    data      = generate_fact_script(topic=topic, used_titles=load_history(), style=style)
    title_en  = data["title_en"]
    title_th  = data["title_th"]
    script_en = data["script_en"]
    script_th = data["script_th"]
    keywords  = data["keywords"]
    print(f"  EN: {title_en}")
    print(f"  TH: {title_th}")

    # trending: 7 clips (Develian-style fast cuts) — not per-sentence
    kw_for_clips = keywords[:7] if style == "trending" else keywords
    clips = fetch_multiple_clips(kw_for_clips, OUTPUT_DIR)
    if not clips:
        print("  ERROR: No footage — skipping")
        return

    audio_en = os.path.join(OUTPUT_DIR, f"audio_{timestamp}_en.mp3")
    audio_th = os.path.join(OUTPUT_DIR, f"audio_{timestamp}_th.mp3")
    sentences_th = [s.get("text_th", "") for s in data.get("sentences", [])]
    generate_voiceover(script_en, audio_en, lang="en", style=style)
    _, th_boundaries = generate_voiceover(script_th, audio_th, lang="th",
                                          style=style, sentences=sentences_th)

    from main import _sync_th_subs
    en_words = get_word_timestamps(audio_en, lang="en")
    th_words = _sync_th_subs(script_th, audio_th, sentences_th=sentences_th or None,
                             style=style,
                             tts_boundaries=th_boundaries)

    mood  = data.get("music_mood", "dramatic")
    music = get_track(mood)

    thumb_keyword = data.get("thumbnail_keyword")
    ai_prompt     = data.get("thumbnail_prompt")
    thumb_en   = os.path.join(OUTPUT_DIR, f"thumb_{timestamp}_en.jpg")
    thumb_th   = os.path.join(OUTPUT_DIR, f"thumb_{timestamp}_th.jpg")
    thumb_en_b = os.path.join(OUTPUT_DIR, f"thumb_{timestamp}_en_b.jpg")
    thumb_th_b = os.path.join(OUTPUT_DIR, f"thumb_{timestamp}_th_b.jpg")
    # A: deterministic from prompt (existing behavior).
    # B: same prompt, different seed -- A/B candidate for post-24h swap
    # based on CTR (see swap_thumbnails.py, Phase 2).
    seed_a = (hash(ai_prompt or title_en) % 99999) if ai_prompt else None
    seed_b = ((seed_a or 0) + 41337) % 99999 if ai_prompt else None
    create_thumbnail(None, title_en, thumb_en,
                     thai_ver=False, photo_keyword=thumb_keyword,
                     ai_prompt=ai_prompt, seed=seed_a)
    create_thumbnail(None, title_en, thumb_th,
                     thai_ver=True, photo_keyword=thumb_keyword,
                     ai_prompt=ai_prompt, seed=seed_a)
    if ai_prompt:
        create_thumbnail(None, title_en, thumb_en_b,
                         thai_ver=False, photo_keyword=thumb_keyword,
                         ai_prompt=ai_prompt, seed=seed_b)
        create_thumbnail(None, title_en, thumb_th_b,
                         thai_ver=True, photo_keyword=thumb_keyword,
                         ai_prompt=ai_prompt, seed=seed_b)
    else:
        thumb_en_b = thumb_th_b = None

    # Entity image overlays — fetch CC-licensed photos of named people/places
    # mentioned in the script and overlay them while their sentence is spoken.
    en_overlays, th_overlays = _build_entity_overlays(
        data, timestamp, audio_en, audio_th, th_boundaries
    )

    final_en = make_video(clips, audio_en, title_en, en_words, timestamp, "en", music,
                          thumb_path=thumb_en, content_style=style,
                          entity_overlays=en_overlays)
    final_th = make_video(clips, audio_th, title_en, th_words, timestamp, "th", music,
                          thumb_path=thumb_th, content_style=style,
                          entity_overlays=th_overlays)

    desc_en     = data.get("description", script_en)
    desc_th     = data.get("description_th", script_th)
    hashtags_en = data.get("hashtags", ["shorts", "facts", "didyouknow"])
    hashtags_th = data.get("hashtags_th", ["shorts", "เรื่องน่ารู้", "ความรู้"])

    if isinstance(hashtags_en, str):
        hashtags_en = [h.strip().lstrip("#") for h in hashtags_en.replace(",", " ").split() if h.strip()]
    if isinstance(hashtags_th, str):
        hashtags_th = [h.strip().lstrip("#") for h in hashtags_th.replace(",", " ").split() if h.strip()]

    if "shorts" not in [h.lower() for h in hashtags_en]:
        hashtags_en = ["shorts"] + hashtags_en
    if "shorts" not in [h.lower() for h in hashtags_th]:
        hashtags_th = ["shorts"] + hashtags_th

    def _title_with_tags(title: str, tags: list, max_len: int = 95) -> str:
        picked = ["#" + t for t in tags[:4] if t.lower() != "shorts"][:3]
        suffix = " " + " ".join(["#Shorts"] + picked)
        return (title[:max_len - len(suffix)] + suffix).strip()

    for lang, title, title_full, video_path, thumb_path, thumb_path_b, desc, hashtags in [
        ("en", title_en, _title_with_tags(title_en, hashtags_en),
         final_en, thumb_en, thumb_en_b, desc_en, hashtags_en),
        ("th", title_th, _title_with_tags(title_th, hashtags_th),
         final_th, thumb_th, thumb_th_b, desc_th, hashtags_th),
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
            "thumb_path_b":   thumb_path_b,    # A/B candidate; swap if CTR low
            "ab_swapped":     False,
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
        # TikTok upload — disabled until official Content Posting API
        # is approved. The cookie path via tiktok-uploader 1.2.0 is
        # broken: TikTok rolled out a new UI (2026-05) and the lib's
        # xpath selectors (e.g. //*[@id='tux-1']) no longer resolve, so
        # both _set_interactivity and _set_schedule_video time out.
        # Re-enable by setting TIKTOK_ENABLED=1 in the env once the
        # official API path is live (see src/tiktok_api.py).
        tt_url = None
        if os.getenv("TIKTOK_ENABLED") == "1":
            tt_title = title if lang == "en" else f"{title_en} Thai Ver"
            tt_url   = upload_tiktok(video_path, tt_title, publish_at=publish_at)

        if result:
            yt_url, yt_id = result
            job["status"]           = "uploaded"
            job["youtube_url"]      = yt_url
            job["youtube_video_id"] = yt_id
            job["tiktok_url"]       = tt_url
            # uploaded_at is the wall-clock moment the YouTube insert
            # returned -- used by swap_thumbnails.py to decide when the
            # 24h CTR window has elapsed (publish_at can be much later).
            job["uploaded_at"]      = datetime.now(BKK).isoformat()
            with open(job_path, "w", encoding="utf-8") as f:
                json.dump(job, f, ensure_ascii=False, indent=2)
            if notion_page_id:
                mark_uploaded(notion_page_id, youtube_url=yt_url, tiktok_url=tt_url)
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

    used  = _used_publish_slots()
    slots = _future_slots(used, n)
    print(f"  Existing queue slots: {len(used)}")
    print(f"  Planned slots : {[s[:16] for s in slots]}")

    n_ok = 0
    for i, slot in enumerate(slots):
        try:
            generate_one_pair(i, slot)
            n_ok += 1
        except Exception as e:
            print(f"  [Batch] Pair {i+1} failed: {e}")

    print(f"\n[Batch] Done — {n_ok}/{len(slots)} pair(s) queued")

    # Exit non-zero when nothing succeeded so run_batch.ps1 does NOT write the
    # daily-once marker -- a later trigger (12:00 / 18:00) then retries today
    # instead of skipping. A transient edge-tts outage that kills every pair
    # therefore self-heals on the next fire.
    if slots and n_ok == 0:
        sys.exit(1)
