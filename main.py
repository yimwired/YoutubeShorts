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
from src.editor import create_short, _clip_duration, prepend_title_card, append_outro_card
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


def _silence_boundaries(audio_path: str, n: int,
                        threshold_db: int = 28,
                        min_silence: float = 0.15) -> list[dict]:
    """Detect n sentence intervals via ffmpeg silencedetect.
    Picks the n-1 longest silences as sentence breaks. Returns list of
    {start, end} dicts. Empty list on failure or insufficient pauses."""
    import subprocess, re as _re
    if n <= 0:
        return []
    try:
        r = subprocess.run(
            ["ffmpeg", "-i", audio_path,
             "-af", f"silencedetect=n=-{threshold_db}dB:d={min_silence}",
             "-f", "null", "-"],
            capture_output=True, text=True
        )
    except Exception:
        return []

    starts, ends = [], []
    for line in r.stderr.splitlines():
        m = _re.search(r"silence_start:\s*([\d.]+)", line)
        if m:
            starts.append(float(m.group(1)))
            continue
        m = _re.search(r"silence_end:\s*([\d.]+)", line)
        if m:
            ends.append(float(m.group(1)))

    # Pair silences; drop a leading "0.0 → x" (TTS warmup, not a sentence break)
    pairs = list(zip(starts, ends))
    pairs = [(s, e) for s, e in pairs if s > 0.05]
    if n == 1:
        # Whole audio is one sentence — use audio duration
        try:
            dur = _clip_duration(audio_path)
            # Skip TTS leading silence if present
            head = ends[0] if (starts and starts[0] == 0 and ends) else 0.0
            return [{"start": head, "end": dur}]
        except Exception:
            return []

    if len(pairs) < n - 1:
        return []

    # Take n-1 longest silences ordered by time → these are sentence breaks
    pairs_sorted = sorted(pairs, key=lambda p: p[1] - p[0], reverse=True)[:n - 1]
    pairs_sorted.sort(key=lambda p: p[0])

    try:
        dur = _clip_duration(audio_path)
    except Exception:
        return []
    head = ends[0] if (starts and starts[0] == 0 and ends) else 0.0
    result = []
    sent_start = head
    for s, e in pairs_sorted:
        result.append({"start": sent_start, "end": s})
        sent_start = e
    result.append({"start": sent_start, "end": dur})
    return result


def _whisper_word_events(audio_path: str) -> list[dict]:
    """Whisper word-level events (start/end). Empty list on failure."""
    try:
        from src.captions import _get_model
        model = _get_model("base")
        segs, _ = model.transcribe(str(audio_path), language="th",
                                   word_timestamps=True)
        events = []
        for seg in segs:
            for w in seg.words:
                if w.end > w.start:
                    events.append({"start": float(w.start), "end": float(w.end)})
        events.sort(key=lambda e: e["start"])
        return events
    except Exception:
        return []


def _split_events_by_sentence(events: list[dict], n: int) -> list[list[dict]]:
    """Cut event stream into n groups at the n-1 largest gaps between events.
    If too few gaps, pad with evenly-spaced indices."""
    if not events or n <= 1:
        return [events] if events else []
    gaps = []  # (gap, idx_to_cut_at)
    for i in range(1, len(events)):
        g = events[i]["start"] - events[i - 1]["end"]
        if g > 0:
            gaps.append((g, i))
    gaps.sort(reverse=True)
    cuts = sorted(idx for _, idx in gaps[:n - 1])
    # Pad with even splits if Whisper didn't expose enough gaps
    if len(cuts) < n - 1:
        need = (n - 1) - len(cuts)
        existing = set(cuts)
        step = max(len(events) // n, 1)
        for k in range(1, n):
            idx = k * step
            if idx not in existing and 0 < idx < len(events):
                cuts.append(idx)
                existing.add(idx)
                if len(cuts) >= n - 1:
                    break
        cuts = sorted(cuts)[:n - 1]
    # Final clamp in case events shorter than n
    cuts = [c for c in cuts if 0 < c < len(events)]
    while len(cuts) < n - 1:
        cuts.append(len(events))
    bounds = [0] + cuts + [len(events)]
    return [events[bounds[i]:bounds[i + 1]] for i in range(n)]


def _distribute_tokens(tokens: list[str], events: list[dict]) -> list[dict]:
    """Anchor script tokens to Whisper events within one sentence.
    Allocate by character weight — long tokens span more events than short ones.
    Whisper Thai emits subword-level events, so a single PyThaiNLP token
    usually maps to multiple events."""
    if not tokens or not events:
        return []
    n_e = len(events)
    total_chars = sum(len(t) for t in tokens) or 1
    out = []
    cum = 0
    for tok in tokens:
        e_lo = int(cum * n_e / total_chars)
        cum += len(tok)
        e_hi = max(int(cum * n_e / total_chars) - 1, e_lo)
        e_lo = min(e_lo, n_e - 1)
        e_hi = min(e_hi, n_e - 1)
        out.append({
            "word":  tok,
            "start": round(events[e_lo]["start"], 3),
            "end":   round(events[e_hi]["end"] - 0.04, 3),
        })
    return out


def _subs_from_sentences(sentences_th: list, audio_path: str) -> list[dict]:
    """Script text + word-event-anchored timing.
    1) Whisper word_timestamps → event stream
    2) Cut at n-1 largest gaps → one group per script sentence
    3) Distribute that sentence's tokens across its event group (proportional, not uniform)
    Fallback: uniform per-sentence split if Whisper fails."""
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

    events = _whisper_word_events(audio_path)
    if events:
        groups = _split_events_by_sentence(events, len(clean))
        result = []
        for sentence, ev_group in zip(clean, groups):
            tokens = tokenize(sentence)
            sub = _distribute_tokens(tokens, ev_group)
            if sub:
                sub[-1]["break_after"] = True
            result.extend(sub)
        if result:
            return result

    # Fallback: uniform per-sentence split (legacy behavior)
    dur  = _clip_duration(audio_path)
    step = dur / len(clean)
    result = []
    for i, sentence in enumerate(clean):
        tokens = tokenize(sentence)
        if not tokens:
            continue
        t_start = i * step
        word_dur = step / len(tokens)
        for j, tok in enumerate(tokens):
            entry = {
                "word":  tok,
                "start": round(t_start + j * word_dur, 3),
                "end":   round(t_start + (j + 1) * word_dur - 0.04, 3),
            }
            if j == len(tokens) - 1:
                entry["break_after"] = True
            result.append(entry)
    return result


def _subs_from_tts_boundaries(sentences_th: list,
                              boundaries: list[dict]) -> list[dict]:
    """Script tokens timed by TTS SentenceBoundary events.
    Within each sentence, tokens are weighted by character count so long words
    span more time than short ones."""
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
        words = tokenize(clean[i])
        if not words:
            continue
        b        = boundaries[i]
        sent_dur = max(b["end"] - b["start"], 0.1)
        char_tot = sum(len(w) for w in words) or 1
        offset   = 0.0
        for j, word in enumerate(words):
            d = sent_dur * len(word) / char_tot
            entry = {
                "word":  word,
                "start": round(b["start"] + offset, 3),
                "end":   round(b["start"] + offset + d - 0.04, 3),
            }
            if j == len(words) - 1:
                entry["break_after"] = True
            result.append(entry)
            offset += d
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
    """Sub-voice sync priority:
    1. ffmpeg silencedetect → actual TTS pauses (most reliable, works for any TH voice)
    2. TTS SentenceBoundary events (if engine emits per-sentence, e.g. PremwadeeNeural EN)
    3. Whisper word-event anchoring (fallback)
    4. Plain script split (last resort)"""
    if sentences_th:
        n = len(sentences_th)
        # 1. Try silence detection — TTS produces clean pauses between sentences
        sil_bounds = _silence_boundaries(audio_path, n)
        if sil_bounds and len(sil_bounds) == n:
            raw = _subs_from_tts_boundaries(sentences_th, sil_bounds)
            if raw:
                return raw

        # 2. TTS boundaries (need ≥70% coverage to trust)
        if tts_boundaries and len(tts_boundaries) >= n * 0.7:
            raw = _subs_from_tts_boundaries(sentences_th, tts_boundaries)
            if raw:
                return raw

        # 3. Whisper word events
        raw = _subs_from_sentences(sentences_th, audio_path)
        if raw:
            return raw

    return _make_th_subs(script_th, _clip_duration(audio_path))


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
               content_style: str = "trending",
               entity_overlays: list[dict] = None) -> str:
    print(f"  [{lang.upper()}] Editing...")
    final_path = os.path.join(OUTPUT_DIR, f"short_{timestamp}_{lang}.mp4")
    create_short(clips[0], audio_path, title, "", final_path,
                 words=words, clips=clips, lang=lang, music_path=music,
                 cut_times=cut_times, content_style=content_style,
                 entity_overlays=entity_overlays)
    if thumb_path and os.path.exists(thumb_path):
        print(f"  [{lang.upper()}] Adding title card...")
        prepend_title_card(final_path, thumb_path, title, lang)
    print(f"  [{lang.upper()}] Adding outro card...")
    append_outro_card(final_path, lang)
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
