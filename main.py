import os
import sys
import time
from dotenv import load_dotenv

load_dotenv()
sys.stdout.reconfigure(encoding='utf-8')

from src.generator import generate_fact_script
from src.footage import fetch_multiple_clips
from src.tts import generate_voiceover
from src.captions import get_word_timestamps
from src.thumbnail import create_thumbnail
from src.editor import create_short, _clip_duration, prepend_title_card
from src.uploader import upload_youtube, upload_tiktok
from src.rate_tracker import summary as usage_summary
from src.notion_logger import log_video
from src.trends import get_trending_topic
from src.music import get_track

OUTPUT_DIR = "output"



def _make_th_subs(script_th: str, total_dur: float,
                  boundaries: list[dict] = None) -> list[dict]:
    """
    Split Thai script by natural space-based phrase boundaries.
    Thai uses spaces between phrases/clauses — respect those breaks.
    If a phrase is too long (>14 chars), split further by sentence tokenize.
    """
    import re
    clean = re.sub(r"[^฀-๿\s]", "", script_th).strip()
    if not clean:
        return []

    # Split on spaces = natural Thai phrase/clause breaks
    raw_chunks = [c.strip() for c in clean.split() if c.strip()]

    # Merge very short chunks (<4 chars) with next one
    merged = []
    buf = ""
    for c in raw_chunks:
        buf = buf + c if buf else c
        if len(buf) >= 4:
            merged.append(buf)
            buf = ""
    if buf:
        if merged:
            merged[-1] += buf
        else:
            merged.append(buf)

    # Split chunks that are too long (>16 chars) using pythainlp
    final = []
    for chunk in merged:
        if len(chunk) <= 16:
            final.append(chunk)
        else:
            try:
                from pythainlp.tokenize import word_tokenize
                words = word_tokenize(chunk, engine="newmm", keep_whitespace=False)
                words = [w for w in words if w.strip()]
                # Group into 3-word units
                for i in range(0, len(words), 3):
                    g = "".join(words[i:i+3])
                    if g:
                        final.append(g)
            except Exception:
                final.append(chunk)

    if not final:
        return []

    # If we have sentence boundaries from TTS engine, use them as anchors
    if boundaries and len(boundaries) >= 2:
        n_bounds = len(boundaries)
        n_chunks = len(final)
        result   = []
        for ci, chunk in enumerate(final):
            # Map chunk → sentence boundary proportionally
            bi = min(int(ci * n_bounds / n_chunks), n_bounds - 1)
            b  = boundaries[bi]
            # Distribute within this boundary by char weight
            same_b = [c for j, c in enumerate(final)
                      if min(int(j * n_bounds / n_chunks), n_bounds - 1) == bi]
            total_c  = sum(len(x) for x in same_b) or 1
            b_dur    = max(b["end"] - b["start"], 0.1)
            offset   = sum(len(x) for x in same_b[:same_b.index(chunk)]) / total_c * b_dur
            duration = len(chunk) / total_c * b_dur
            result.append({
                "word":  chunk,
                "start": round(b["start"] + offset, 3),
                "end":   round(b["start"] + offset + duration - 0.07, 3),
            })
        return result

    # Fallback: weight by char length across total duration
    total_chars = sum(len(c) for c in final) or 1
    result, t = [], 0.0
    for chunk in final:
        duration = total_dur * len(chunk) / total_chars
        result.append({"word": chunk,
                       "start": round(t, 3),
                       "end":   round(t + duration - 0.08, 3)})
        t += duration
    return result


def _get_sentence_timings(audio_path: str, n: int) -> list[tuple]:
    """
    Find n timing windows by detecting the n-1 largest pauses in the audio.
    Uses Whisper word_timestamps for sub-segment pause resolution.
    Works well for Neural TTS (PremwadeeNeural) which has clear inter-sentence pauses.
    """
    try:
        from src.captions import _get_model
        model = _get_model("base")
        segs, _ = model.transcribe(str(audio_path), language="th",
                                   word_timestamps=True)
        segments = list(segs)
        if not segments:
            raise Exception("empty")

        total = segments[-1].end

        # Collect word-level timing events
        events: list[tuple[float, float]] = []
        for seg in segments:
            for w in seg.words:
                if w.end > w.start:
                    events.append((w.start, w.end))
        events.sort()

        if len(events) < 2:
            raise Exception("too few events")

        # Measure every gap between consecutive word events
        gaps: list[tuple[float, float]] = []  # (gap_size, midpoint)
        for i in range(1, len(events)):
            gap = round(events[i][0] - events[i - 1][1], 4)
            if gap >= 0.1:
                mid = round((events[i - 1][1] + events[i][0]) / 2, 3)
                gaps.append((gap, mid))

        # Pick the n-1 largest gaps as sentence boundaries
        gaps.sort(reverse=True)
        split_points = sorted(mid for _, mid in gaps[:n - 1])

        boundaries = [0.0] + split_points + [total]
        return [(boundaries[i], boundaries[i + 1]) for i in range(n)]

    except Exception:
        dur = _clip_duration(audio_path)
        step = dur / n
        return [(i * step, (i + 1) * step) for i in range(n)]


def _subs_from_sentences(sentences_th: list, audio_path: str) -> list[dict]:
    """
    Subtitle from exact script text + Whisper-based sentence timing.
    Text = correct (no Whisper transcription error)
    Timing = from Whisper segment detection (includes natural pauses)
    """
    import re
    try:
        from pythainlp.tokenize import word_tokenize
        tokenize = lambda t: [w.strip() for w in
                               word_tokenize(t, engine="newmm", keep_whitespace=False) if w.strip()]
    except Exception:
        tokenize = lambda t: t.split()

    clean = [re.sub(r"[^฀-๿\s]", "", s).strip() for s in sentences_th]
    clean = [s for s in clean if s]
    if not clean:
        return []

    timings = _get_sentence_timings(audio_path, len(clean))
    result = []

    for sentence, (t_start, t_end) in zip(clean, timings):
        words = tokenize(sentence)
        if not words:
            continue
        sent_dur = max(t_end - t_start, 0.1)
        word_dur = sent_dur / len(words)
        for i, word in enumerate(words):
            result.append({
                "word":  word,
                "start": round(t_start + i * word_dur, 3),
                "end":   round(t_start + (i + 1) * word_dur - 0.04, 3),
            })

    return result


def _subs_from_tts_boundaries(sentences_th: list,
                              boundaries: list[dict]) -> list[dict]:
    """Exact script text timed by TTS SentenceBoundary events."""
    import re
    try:
        from pythainlp.tokenize import word_tokenize
        tokenize = lambda t: [w.strip() for w in
                               word_tokenize(t, engine="newmm", keep_whitespace=False)
                               if w.strip()]
    except Exception:
        tokenize = lambda t: t.split()

    clean = [re.sub(r"[^฀-๿\s]", "", s).strip() for s in sentences_th]
    clean = [s for s in clean if s]
    if not clean or not boundaries:
        return []

    result = []
    n = min(len(clean), len(boundaries))
    for i in range(n):
        words    = tokenize(clean[i])
        if not words:
            continue
        b        = boundaries[i]
        sent_dur = max(b["end"] - b["start"], 0.1)
        word_dur = sent_dur / len(words)
        for j, word in enumerate(words):
            result.append({
                "word":  word,
                "start": round(b["start"] + j * word_dur, 3),
                "end":   round(b["start"] + (j + 1) * word_dur - 0.04, 3),
            })
    return result


def _group_words(words: list[dict], chunk_size: int = 3) -> list[dict]:
    """Merge consecutive word entries into chunks so each subtitle stays longer."""
    result = []
    for i in range(0, len(words), chunk_size):
        chunk = words[i:i + chunk_size]
        result.append({
            "word":  "".join(w["word"] for w in chunk),
            "start": chunk[0]["start"],
            "end":   chunk[-1]["end"],
        })
    return result


def _sync_th_subs(script_th: str, audio_path: str,
                  sentences_th: list = None,
                  style: str = "trending",
                  tts_boundaries: list = None) -> list[dict]:
    if style == "narrative" and sentences_th:
        return _subs_from_sentences(sentences_th, audio_path)

    # trending: prefer TTS boundaries (if coverage ≥70%), else Whisper+exact text
    if style == "trending" and sentences_th:
        if tts_boundaries and len(tts_boundaries) >= len(sentences_th) * 0.7:
            raw = _subs_from_tts_boundaries(sentences_th, tts_boundaries)
        else:
            raw = _subs_from_sentences(sentences_th, audio_path)
        if raw:
            return _group_words(raw, chunk_size=3)



    # chaos: original approach — Whisper base (no word_timestamps)
    # → PyThaiNLP tokenize per segment → 2-word chunks with proportional timing
    import re
    try:
        from src.captions import _get_model
        model = _get_model("base")
        segs, _ = model.transcribe(str(audio_path), language="th")
        segments = list(segs)
    except Exception:
        return _make_th_subs(script_th, _clip_duration(audio_path))

    if not segments:
        return _make_th_subs(script_th, _clip_duration(audio_path))

    result = []
    for seg in segments:
        text = re.sub(r"[^฀-๿\s]", "", seg.text).strip()
        if not text:
            continue
        try:
            from pythainlp.tokenize import word_tokenize
            words = word_tokenize(text, engine="newmm", keep_whitespace=False)
            words = [w for w in words if w.strip()]
        except Exception:
            words = [text]
        chunks = ["".join(words[i:i+2]) for i in range(0, len(words), 2)]
        chunks = [c for c in chunks if c]
        if not chunks:
            continue
        seg_dur  = max(seg.end - seg.start, 0.1)
        word_dur = seg_dur / len(chunks)
        for i, chunk in enumerate(chunks):
            result.append({
                "word":  chunk,
                "start": round(seg.start + i * word_dur, 3),
                "end":   round(seg.start + (i+1) * word_dur - 0.06, 3),
            })
    return result if result else _make_th_subs(script_th, _clip_duration(audio_path))


def _get_segment_cut_times(audio_path: str, lang: str) -> list[float]:
    """Whisper segment start times → clip cut points."""
    try:
        from src.captions import _get_model
        model = _get_model("base")
        segs, _ = model.transcribe(audio_path, language=lang)
        return [seg.start for seg in list(segs)[1:]]
    except Exception:
        return []


def make_video(clips: list[str], audio_path: str, title: str,
               words: list[dict], timestamp: int, lang: str,
               music: str = None, thumb_path: str = None,
               cut_times: list[float] = None,
               content_style: str = "trending") -> str:
    print(f"  [{lang.upper()}] Editing...")
    final_path = os.path.join(OUTPUT_DIR, f"short_{timestamp}_{lang}.mp4")
    create_short(clips[0], audio_path, title, "", final_path,
                 words=words, clips=clips, lang=lang, music_path=music,
                 cut_times=cut_times, content_style=content_style)
    if thumb_path and os.path.exists(thumb_path):
        print(f"  [{lang.upper()}] Adding title card...")
        prepend_title_card(final_path, thumb_path, title, lang)
    print(f"  [{lang.upper()}] Saved: {final_path}")
    return final_path


def run_pipeline():
    timestamp = int(time.time())
    print("=" * 55)
    print(f"[Pipeline] Starting -- {timestamp}")

    # Step 0: Trending topic
    print("[0] Fetching trending topic...")
    topic = get_trending_topic()
    print(f"  Topic: {topic or '(none)'}")

    # Step 1: Generate bilingual scripts
    print("[1] Generating scripts (EN + TH)...")
    data = generate_fact_script(topic=topic)
    title_en  = data["title_en"]
    title_th  = data["title_th"]
    script_en = data["script_en"]
    script_th = data["script_th"]
    keywords  = data["keywords"]
    print(f"  EN: {title_en}")
    print(f"  TH: {title_th}")

    # Step 2: Fetch footage — 1 clip per sentence if available
    print("[2] Fetching stock footage...")
    sentences = data.get("sentences", [])
    kw_list = (
        [{"specific": s.get("keyword", ""), "fallback": s.get("fallback", "")} for s in sentences]
        if sentences else keywords
    )
    clips = fetch_multiple_clips(kw_list, OUTPUT_DIR)
    if not clips:
        print("  ERROR: No footage. Aborting.")
        return
    print(f"  Got {len(clips)} clip(s) for {len(kw_list)} sentence(s)")

    # Step 3: Generate voiceovers
    print("[3] Generating voiceovers...")
    audio_en = os.path.join(OUTPUT_DIR, f"audio_{timestamp}_en.mp3")
    audio_th = os.path.join(OUTPUT_DIR, f"audio_{timestamp}_th.mp3")
    generate_voiceover(script_en, audio_en, lang="en")
    _, th_boundaries = generate_voiceover(script_th, audio_th, lang="th")
    print(f"  TH sentence boundaries: {len(th_boundaries)}")

    # Step 4: Word timestamps + segment cut times
    print("[4] Getting word timestamps...")
    en_words = get_word_timestamps(audio_en, lang="en")
    print(f"  EN words: {len(en_words)}")
    sentences_th = [s.get("text_th", "") for s in data.get("sentences", [])]
    th_words = _sync_th_subs(script_th, audio_th, sentences_th=sentences_th or None, style="narrative")
    print(f"  TH subs: {len(th_words)} chunks")
    en_cut_times = _get_segment_cut_times(audio_en, "en")
    th_cut_times = _get_segment_cut_times(audio_th, "th")
    print(f"  EN cuts: {len(en_cut_times)} | TH cuts: {len(th_cut_times)}")

    # Step 5: Thumbnails first (needed for title card)
    print("[5] Creating thumbnails...")
    thumb_keyword = data.get("thumbnail_keyword")
    ai_prompt     = data.get("thumbnail_prompt")
    print(f"  AI prompt: {ai_prompt or '(none)'}")
    thumb_en = os.path.join(OUTPUT_DIR, f"thumb_{timestamp}_en.jpg")
    thumb_th = os.path.join(OUTPUT_DIR, f"thumb_{timestamp}_th.jpg")
    # Use a placeholder video path just to satisfy create_thumbnail signature
    _placeholder = os.path.join(OUTPUT_DIR, f"short_{timestamp}_en.mp4")
    create_thumbnail(_placeholder, title_en, thumb_en,
                     thai_ver=False, photo_keyword=thumb_keyword,
                     ai_prompt=ai_prompt, clips=clips)
    create_thumbnail(_placeholder, title_en, thumb_th,
                     thai_ver=True, photo_keyword=thumb_keyword,
                     ai_prompt=ai_prompt, clips=clips)
    print(f"  EN: {thumb_en}")
    print(f"  TH: {thumb_th}")

    # Step 6: Build EN video
    print("[6] Creating EN video...")
    mood  = data.get("music_mood", "dramatic")
    music = get_track(mood)
    print(f"  Music mood: {mood} → {music or '(none — set PIXABAY_API_KEY or add files to music/)'}")
    final_en = make_video(clips, audio_en, title_en, en_words, timestamp, "en", music,
                          thumb_path=thumb_en, cut_times=en_cut_times, content_style="narrative")

    # Step 6.5: Build TH video
    print("[6.5] Creating TH video (Thai voice + EN subs)...")
    final_th = make_video(clips, audio_th, title_th, th_words, timestamp, "th", music,
                          thumb_path=thumb_th, cut_times=th_cut_times, content_style="narrative")

    # Step 7: Upload
    print("[7] Uploading...")
    desc_en     = data.get("description", script_en)
    desc_th     = data.get("description_th", script_th)
    hashtags_en = data.get("hashtags", ["shorts", "facts", "didyouknow"])
    hashtags_th = data.get("hashtags_th", ["shorts", "เรื่องน่ารู้", "ความรู้"])
    if isinstance(hashtags_en, str):
        hashtags_en = [h.strip().lstrip("#") for h in hashtags_en.replace(",", " ").split() if h.strip()]
    if isinstance(hashtags_th, str):
        hashtags_th = [h.strip().lstrip("#") for h in hashtags_th.replace(",", " ").split() if h.strip()]

    # Ensure #shorts always present
    if "shorts" not in [h.lower() for h in hashtags_en]:
        hashtags_en = ["shorts"] + hashtags_en
    if "shorts" not in [h.lower() for h in hashtags_th]:
        hashtags_th = ["shorts"] + hashtags_th

    # Append top hashtags to title (max 3, keep title under 100 chars)
    def _title_with_tags(title: str, tags: list[str], max_len: int = 95) -> str:
        picked = ["#" + t for t in tags[:4] if t.lower() != "shorts"][:3]
        suffix = " " + " ".join(["#Shorts"] + picked)
        return (title[:max_len - len(suffix)] + suffix).strip()

    title_en_full = _title_with_tags(title_en, hashtags_en)
    title_th_full = _title_with_tags(title_th, hashtags_th)

    thumb_en = os.path.join(OUTPUT_DIR, f"thumb_{timestamp}_en.jpg")
    thumb_th = os.path.join(OUTPUT_DIR, f"thumb_{timestamp}_th.jpg")

    yt_en, _ = upload_youtube(final_en, title_en_full, description=desc_en,
                              tags=hashtags_en, thumbnail_path=thumb_en, lang="en") or (None, None)
    yt_th, _ = upload_youtube(final_th, title_th_full,
                              description=desc_th, tags=hashtags_th,
                              thumbnail_path=thumb_th, lang="th") or (None, None)
    tt_en = upload_tiktok(final_en, title_en)
    tt_th = upload_tiktok(final_th, f"{title_en} Thai Ver")

    log_video(f"[EN] {title_en}", youtube_url=yt_en, tiktok_url=tt_en, lang="en", topic=topic or "")
    log_video(f"[TH] {title_th}", youtube_url=yt_th, tiktok_url=tt_th, lang="th", topic=topic or "")

    # Cleanup
    for clip in clips:
        if os.path.exists(clip): os.remove(clip)
    for f in [audio_en, audio_th]:
        if os.path.exists(f): os.remove(f)

    print(f"\n[Pipeline] Done")
    print(f"  EN: {final_en}")
    print(f"  TH: {final_th}")
    print(usage_summary())
    print("=" * 55)


if __name__ == "__main__":
    run_pipeline()
