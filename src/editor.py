import subprocess
import re
import tempfile
import os

from src.thai_text import clean_thai

_BASE     = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_font_th_raw = os.path.join(_BASE, "Kanit-Bold.ttf")
FONT_TH   = _font_th_raw.replace("\\", "/").replace(":", "\\:")
FONT_EN   = "C\\:/Windows/Fonts/impact.ttf" if os.name == "nt" else "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
LOGO_PATH = os.path.join(_BASE, "logo.png")
WORD_GAP  = 0.07

# Shorts accepts three minutes, so nothing about the platform requires the
# renderer to cut a voiceover short. HARD_CAP exists only to stop a runaway
# TTS response from producing a ten-minute render; length is controlled in
# the script, where it belongs. SPEECH_TARGET is the number the writer is
# aiming at -- exceeding it is logged, never silently trimmed.
HARD_CAP      = 100.0
SPEECH_TARGET = 52.0

# Shortest segment ffmpeg will trim without complaint. Two cut points closer
# together than this, or a cut point past the end of the audio, used to reach
# the filter graph as a negative `trim=duration` and abort the whole render.
MIN_SEGMENT = 0.35

# Shot length band. Below ~1.2s the eye reads cuts as noise and tunes out;
# past ~2.5s the frame is static long enough for a Shorts viewer to swipe.
# One shot per sentence lands around 4.6s, which is why segments get divided.
MIN_SHOT   = 1.8
MAX_SUBCUT = 3

# Delivery loudness. YouTube plays everything at -14 LUFS: it attenuates an
# upload that is louder and leaves a quieter one where it is, so anything
# below the target plays quieter than the videos around it in the feed.
LOUDNORM = "loudnorm=I=-14:TP=-1.5:LRA=11"

# Bed loudness, absolute rather than a gain multiplier. The library masters
# between -14.2 and -10.7 LUFS depending on where a track came from, so the
# old `volume=0.13` produced a bed anywhere across a 3.5 dB spread and the
# SoundHelix fallback came in loudest of all. Normalising the music to a
# fixed target makes the bed sit the same distance under the voiceover no
# matter which track is playing.
#
# The voiceover arrives at -16 LUFS (see _pcm_to_mp3 in tts_gemini), so -30
# puts the bed 14 dB down: present in the pauses, subordinate under speech.
MUSIC_LUFS = -30

# Where each sub-shot sits in the horizontal slack left by the oversized
# scale, as a fraction of it. Spread wide enough that the cut is visible,
# narrow enough that a centred subject stays in frame in both shots.
_SHOT_FRAMING = {1: (0.50,), 2: (0.34, 0.66), 3: (0.30, 0.50, 0.70)}

# ── Thai ASS karaoke helpers ─────────────────────────────────────────────────

def _ass_header(style: str = "trending") -> str:
    # Fontname, Size, Primary(highlight), Secondary(dim), Outline, Back,
    # OutlineWidth, ShadowDepth
    _styles = {
        "trending":  ("Kanit", 68, "&H0000E0FF", "&H00FFFFFF", "&H00000000", "&H90000000", 3, 1),
        "chaos":     ("Kanit", 78, "&H000060FF", "&H00FFFFFF", "&H00000000", "&H90000000", 3, 1),
        "narrative": ("Kanit", 58, "&H00E8E8E8", "&H00888888", "&H00000000", "&H80000000", 3, 1),
        # Heavier than the rest on purpose. A 3px outline disappears when
        # white subtitles land on bright stock footage (snow, sky, sand),
        # and on a phone that reads as a video with no subtitles at all.
        "explainer": ("Kanit", 76, "&H0000E0FF", "&H00FFFFFF", "&H00000000", "&HA0000000", 6, 3),
    }
    (font, size, pri, sec, outline, back,
     outline_w, shadow) = _styles.get(style, _styles["trending"])
    return f"""\
[Script Info]
ScriptType: v4.00+
PlayResX: 1080
PlayResY: 1920
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,{font},{size},{pri},{sec},{outline},{back},-1,0,0,0,100,100,0,0,1,{outline_w},{shadow},5,20,20,0,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""

_ASS_HEADER = _ass_header("trending")


def _to_ass_time(s: float) -> str:
    h, rem = divmod(s, 3600)
    m, sec = divmod(rem, 60)
    return f"{int(h)}:{int(m):02d}:{sec:05.2f}"


def _make_thai_ass(words: list, ass_path: str, style: str = "trending"):
    """Build ASS karaoke file: one line per sentence with word-level highlight sweep.
    Sentence breaks come from `break_after` flag (set by _subs_from_sentences);
    pause-based and word-count splits act as fallbacks for legacy inputs."""
    is_narrative = style == "narrative"
    pause_threshold = 0.6 if is_narrative else 0.45
    max_words = 999  # whole sentence stays on one line
    # Wrap width per visual line. Explainer runs wider than the rest: at 18
    # a thirteen-word sentence stacked five lines deep and covered the middle
    # of the frame, which is the footage the sentence is describing. Twenty
    # Thai glyphs of Kanit-Bold at 76px still clear the 1080 frame.
    max_chars = 22 if is_narrative else 20 if style == "explainer" else 18

    lines, current = [], []
    for i, w in enumerate(words):
        text = clean_thai(w["word"])
        if not text:
            continue
        current.append(w)
        is_last   = i == len(words) - 1
        flagged   = bool(w.get("break_after"))
        has_pause = (i + 1 < len(words) and
                     words[i + 1]["start"] - w["end"] > pause_threshold)
        if flagged or len(current) >= max_words or has_pause or is_last:
            lines.append(current)
            current = []

    with open(ass_path, "w", encoding="utf-8") as f:
        f.write(_ass_header(style))
        for line_words in lines:
            start = _to_ass_time(line_words[0]["start"])
            end   = _to_ass_time(line_words[-1]["end"] + 0.05)

            karaoke = ""
            line_len = 0
            for w in line_words:
                dur_cs = max(1, int((w["end"] - w["start"]) * 100))
                text   = clean_thai(w["word"])
                if not text:
                    continue
                if max_chars < 999 and line_len + len(text) > max_chars and line_len > 0:
                    karaoke += r"\N"
                    line_len = 0
                karaoke += f"{{\\kf{dur_cs}}}{text}"
                line_len += len(text)

            f.write(f'Dialogue: 0,{start},{end},Default,,0,0,0,,{{\\pos(540,1080)}}{karaoke}\n')


def _burn_ass(src: str, ass_path: str, dst: str):
    """Pass 2: burn ASS subtitle onto video. Copies ass to repo root and runs ffmpeg
    with cwd=_BASE so we can pass simple relative paths — avoids Windows colon-escape
    issues in libavfilter parsing. fontsdir=. lets libass find bundled Kanit-Bold.ttf."""
    import shutil
    target_ass = os.path.join(_BASE, "_burn_temp.ass")
    if os.path.abspath(ass_path) != target_ass:
        shutil.copy(ass_path, target_ass)
    try:
        r = subprocess.run(
            ["ffmpeg", "-y", "-i", os.path.abspath(src),
             "-vf", "ass=_burn_temp.ass:fontsdir=.",
             "-c:v", "libx264", "-preset", "fast", "-crf", "23",
             "-c:a", "copy", os.path.abspath(dst)],
            capture_output=True, text=True,
            cwd=_BASE,
        )
        if r.returncode != 0:
            print(r.stderr[-2000:])
            raise RuntimeError("ASS burn failed")
    finally:
        if os.path.exists(target_ass):
            os.remove(target_ass)

def _escape(text: str) -> str:
    """Make `text` safe to sit inside a single-quoted drawtext value.

    Quote characters are substituted, not escaped. ffmpeg processes no
    escapes at all inside '...' -- a backslashed quote closes the string
    just the same, and everything after it leaks into the filtergraph as
    syntax. That is not theoretical: a hook reading ใครคือ 'เจ้าสัว' ended
    the quoted value early, so the commas in the neighbouring alpha
    expression were read as filter separators and ffmpeg died on
    "No such filter: '2.25)'".

    Curly quotes render better in a title anyway, so there is nothing to
    trade off here.
    """
    return (
        text.replace("\\", "\\\\")
            .replace("'", "’")     # ’
            .replace('"', "”")     # ”
            .replace(":", "\\:")
            .replace("%", "\\%")
            .replace("\n", " ")
    )


def _clip_duration(path: str) -> float:
    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", path],
        capture_output=True, text=True
    )
    return float(probe.stdout.strip())


def _find_cut_points(words: list[dict], n: int, duration: float) -> list[float]:
    """Return n-1 cut times that divide video into n segments at natural pauses."""
    if n <= 1:
        return []

    # Natural breaks: gaps > 0.25s between words
    breaks = []
    for i in range(1, len(words)):
        if words[i]["start"] - words[i - 1]["end"] > 0.25:
            breaks.append(words[i]["start"])

    # Whisper timestamps run against the full voiceover, which can be longer
    # than the window being cut up; a break past the end would land as a
    # negative trim duration downstream.
    breaks = [b for b in breaks if MIN_SEGMENT < b < duration - MIN_SEGMENT]

    if len(breaks) >= n - 1:
        # Pick evenly spaced from natural breaks
        step = len(breaks) / (n - 1)
        return [breaks[int(i * step)] for i in range(n - 1)]
    else:
        # Not enough pauses — divide equally
        step = duration / n
        return [step * i for i in range(1, n)]


def _plan_shots(segments: list[tuple], n_clips: int) -> list[dict]:
    """Expand sentence-length segments into shot-length ones.

    One shot per sentence holds the same frame for four to five seconds. The
    channel's retention curve goes flat exactly there, and the benchmark for
    Shorts is a visual change every 1.5-2.5s, so any segment long enough to
    carry two shots is divided into equal sub-shots taken from different
    windows of that sentence's own clip. The subject stays on topic; only
    the framing changes.

    Returns one dict per shot: clip index, duration, and its position within
    the parent segment (used to pick a distinct window of the source).
    """
    shots = []
    for i, (t_start, t_end) in enumerate(segments):
        seg_dur = max(round(t_end - t_start, 3), MIN_SEGMENT)
        n_sub   = min(MAX_SUBCUT, max(1, int(seg_dur // MIN_SHOT)))
        sub_dur = round(seg_dur / n_sub, 3)
        for j in range(n_sub):
            # Absorb the rounding remainder into the last sub-shot so the
            # shots of a segment still add up to the segment.
            dur = round(seg_dur - sub_dur * (n_sub - 1), 3) if j == n_sub - 1 else sub_dur
            shots.append({"clip_idx": i % n_clips, "dur": max(dur, MIN_SEGMENT),
                          "sub": j, "of": n_sub})

    # Close on the footage the video opened with. The final script sentence is
    # written as a callback to the hook, so returning to the first image makes
    # the last frame flow into the first one -- a viewer who does not register
    # the ending rewatches without deciding to, and YouTube reads that replay
    # as satisfaction. It is the cheapest retention device available here.
    if len(shots) > 1:
        shots[-1] = {**shots[-1], "clip_idx": shots[0]["clip_idx"],
                     "sub": shots[0]["sub"], "of": shots[0]["of"]}
    return shots


def _build_segment_filters(clips: list[str], segments: list[tuple],
                            total_dur: float) -> tuple[list[str], list[str]]:
    """Build per-shot trim/crop/pan filters.

    Returns (filter_parts, shot_labels) for use in concat. A clip feeding more
    than one shot is fanned out with `split` first -- an ffmpeg input pad can
    only be consumed once, so reusing `[n:v]` directly is a filtergraph error
    rather than a repeated clip.
    """
    shots = _plan_shots(segments, len(clips))

    uses = {}
    for sh in shots:
        uses[sh["clip_idx"]] = uses.get(sh["clip_idx"], 0) + 1

    filter_parts = []
    # Fan out every clip that more than one shot draws from.
    for clip_idx, n in sorted(uses.items()):
        if n > 1:
            outs = "".join(f"[c{clip_idx}_{k}]" for k in range(n))
            filter_parts.append(f"[{clip_idx}:v]split={n}{outs}")

    taken = {}
    labels = []
    for i, sh in enumerate(shots):
        clip_idx = sh["clip_idx"]
        dur      = sh["dur"]
        clip_dur = _clip_duration(clips[clip_idx])
        label    = f"seg{i}"

        k = taken.get(clip_idx, 0)
        taken[clip_idx] = k + 1
        src = f"[c{clip_idx}_{k}]" if uses[clip_idx] > 1 else f"[{clip_idx}:v]"

        # Spread the sub-shots of one segment across the source clip so two
        # shots of the same sentence are not the same footage twice.
        usable  = max(clip_dur - dur, 0.0)
        start_t = round(usable * (sh["sub"] + 0.5) / sh["of"], 3) if usable else 0.0

        if clip_dur >= dur:
            head = f"{src}trim=start={start_t}:duration={dur}"
        else:
            loops = int(dur / max(clip_dur, 0.1)) + 2
            head  = (f"{src}loop=loop={loops}:size=9999:start=0,"
                     f"trim=duration={dur}")

        # Two sub-shots of one sentence come from the same clip, so a shared
        # centre crop would make the cut invisible -- scene detection did not
        # even register it. Each sub-shot instead frames a different part of
        # the picture, which reads as a second camera setup rather than a
        # jump cut, and a slow drift on top keeps the frame alive. `crop`
        # evaluates x per frame, so the motion costs nothing.
        base  = _SHOT_FRAMING[sh["of"]][sh["sub"]]
        sign  = "+" if i % 2 == 0 else "-"
        drift = f"(iw-ow)*({base}{sign}0.06*t/{dur})"
        filter_parts.append(
            f"{head},setpts=PTS-STARTPTS,"
            f"scale=1242:2208:force_original_aspect_ratio=increase,"
            f"crop=1080:1920:x='clip({drift},0,iw-ow)':y=(ih-oh)/2,setsar=1[{label}]"
        )
        labels.append(label)
    return filter_parts, labels


def prepend_title_card(video_path: str, thumb_path: str, title: str,
                       lang: str = "en") -> str:
    """Prepend a 0.8s title card (thumbnail image) before the video."""
    if not thumb_path or not os.path.exists(thumb_path):
        return video_path

    out_path = video_path.replace(".mp4", "_tc.mp4")

    # thumbnail already has title baked in — just show it for 0.8s
    fc = (
        "[0:v]scale=1080:1920:force_original_aspect_ratio=increase,"
        "crop=1080:1920[card_v];"
        "aevalsrc=0:c=stereo:s=44100:d=0.8[card_a];"
        "[card_v][card_a][1:v][1:a]concat=n=2:v=1:a=1[outv][outa]"
    )

    fc_file = tempfile.NamedTemporaryFile(mode='w', suffix='.txt',
                                          delete=False, encoding='utf-8')
    fc_file.write(fc)
    fc_file.close()

    cmd = [
        "ffmpeg", "-y",
        "-loop", "1", "-t", "0.8", "-i", thumb_path,
        "-i", video_path,
        "-filter_complex_script", fc_file.name,
        "-map", "[outv]", "-map", "[outa]",
        "-c:v", "libx264", "-preset", "fast", "-crf", "23",
        "-c:a", "aac", "-b:a", "128k",
        out_path,
    ]
    r = subprocess.run(cmd, capture_output=True, text=True)
    os.unlink(fc_file.name)

    if r.returncode != 0:
        print("  [TitleCard] Failed — skipping")
        return video_path

    os.remove(video_path)
    os.rename(out_path, video_path)
    return video_path


def append_outro_card(video_path: str, lang: str = "en",
                      duration: float = 1.5) -> str:
    """Append a short end-card prompting follow / next video.

    Black background + two lines of text + silent audio, then concat onto
    the input video. ~1.5s default. Silent so it doesn't fight the
    voiceover fade-out."""
    if not os.path.exists(video_path):
        return video_path

    out_path = video_path.replace(".mp4", "_outro.mp4")

    if lang == "th":
        top  = "ติดตาม"
        sub  = "เพื่อดูเรื่องน่ารู้"
        font = FONT_TH
    else:
        top  = "FOLLOW"
        sub  = "for more facts"
        font = FONT_EN

    fc = (
        f"[1:v]drawtext=fontfile='{font}':text='{_escape(top)}':"
        f"fontsize=160:fontcolor=white:"
        f"box=1:boxcolor=black@0.0:boxborderw=0:"
        f"x=(w-text_w)/2:y=h*0.36,"
        f"drawtext=fontfile='{font}':text='{_escape(sub)}':"
        f"fontsize=72:fontcolor=#FFE000:"
        f"x=(w-text_w)/2:y=h*0.55[card_v];"
        f"[0:v][0:a][card_v][2:a]concat=n=2:v=1:a=1[outv][outa]"
    )

    fc_file = tempfile.NamedTemporaryFile(mode='w', suffix='.txt',
                                          delete=False, encoding='utf-8')
    fc_file.write(fc)
    fc_file.close()

    cmd = [
        "ffmpeg", "-y",
        "-i", video_path,
        "-f", "lavfi", "-t", str(duration), "-i", "color=c=black:s=1080x1920:r=30",
        "-f", "lavfi", "-t", str(duration), "-i", "aevalsrc=0:c=stereo:s=44100",
        "-filter_complex_script", fc_file.name,
        "-map", "[outv]", "-map", "[outa]",
        "-c:v", "libx264", "-preset", "fast", "-crf", "23",
        "-c:a", "aac", "-b:a", "128k",
        out_path,
    ]
    r = subprocess.run(cmd, capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    os.unlink(fc_file.name)

    if r.returncode != 0:
        print("  [OutroCard] Failed — skipping")
        print((r.stderr or "")[-800:])
        return video_path

    os.remove(video_path)
    os.rename(out_path, video_path)
    return video_path


_HOOK_COLORS = {"trending": "#FFE000", "chaos": "#FF2EA0",
                "narrative": "#00E0FF", "explainer": "#FFE000"}

# How long the hook stays up. The old 2.0s ended right as the viewer was
# still deciding; the swipe-away call happens across the first ~3s, so the
# promise should still be legible at the end of that window.
_HOOK_END = 2.6


# Hooks longer than this cannot be shown on one line at a readable size.
# drawtext does not wrap, so an over-long hook would run off both edges of
# the frame -- the weaker model sometimes ignores the length rule in the
# schema, and a silently clipped hook is worse than a trimmed one.
_HOOK_MAX_CHARS = 30


def _hook_size(text: str) -> int:
    """Pick a fontsize that keeps the hook on one line at 1080 wide."""
    n = len(text)
    return 132 if n <= 12 else 108 if n <= 18 else 88 if n <= 26 else 68


def _fit_hook(text: str) -> str:
    """Trim an over-long hook at a word boundary so it still reads.

    Thai writes without spaces, so cutting on the last space either finds
    nothing to cut on or throws away most of the line. Falling back to a
    tokenizer keeps whole words -- and the hook is the one piece of text on
    screen while the viewer decides whether to stay, so half a word there
    is worse than a shorter hook.
    """
    text = text.strip()
    if len(text) <= _HOOK_MAX_CHARS:
        return text

    cut = text[:_HOOK_MAX_CHARS]
    space = cut.rfind(" ")
    if space >= _HOOK_MAX_CHARS // 2:
        cut = cut[:space]
    else:
        try:
            from pythainlp.tokenize import word_tokenize
            kept = ""
            for word in word_tokenize(text, engine="newmm", keep_whitespace=True):
                if len(kept) + len(word) > _HOOK_MAX_CHARS:
                    break
                kept += word
            if kept.strip():
                cut = kept
        except Exception:
            pass       # a hard cut still beats no hook

    cut = cut.rstrip(" ,.!?")
    print(f"  [hook] trimmed {len(text)} chars to {len(cut)}: {cut}")
    return cut


def _hook_overlay(style: str, lang: str, text: str = None) -> list[str]:
    """Filters that slam the retention hook onto the opening frames.

    Built as a three-stage scale pop rather than one static line:
    drawtext cannot animate fontsize, so three copies at 118% / 94% / 100%
    are switched between over the first 0.3s. The eye reads that as the
    text punching in, which holds attention through the swipe window in a
    way a cross-fade does not. A translucent plate behind it keeps the
    text legible over bright stock footage.

    `text` is the fact-specific hook from the generator; a generic
    per-style line stands in when it is missing.
    """
    FALLBACK = {
        "trending":  {"en": "DID YOU KNOW?",  "th": "รู้หรือเปล่า?"},
        "chaos":     {"en": "WAIT FOR IT...", "th": "ห้ามพลาด"},
        "narrative": {"en": "TRUE STORY",     "th": "เรื่องจริง"},
        "explainer": {"en": "HERE IS WHY",    "th": "มันเริ่มจากอะไร?"},
    }
    color = _HOOK_COLORS.get(style, _HOOK_COLORS["trending"])
    if text and text.strip():
        disp = text.strip().upper() if lang == "en" else text.strip()
        disp = _fit_hook(disp)
    else:
        fb   = FALLBACK.get(style, FALLBACK["trending"])
        disp = fb["en"] if lang == "en" else fb["th"]

    font = FONT_EN if lang == "en" else FONT_TH
    base = _hook_size(disp)
    esc  = _escape(disp)
    end  = _HOOK_END
    fade = round(end - 0.35, 2)

    # Contrast plate — sized off the largest of the three stages so it
    # never clips the text mid-pop.
    parts = [
        f"drawbox=x=0:y=ih*0.13:w=iw:h={int(base * 1.18 * 1.7)}:"
        f"color=black@0.42:t=fill:enable='between(t\\,0\\,{end})'"
    ]

    stages = [
        (0.00, 0.12, 1.18),   # punch in oversized
        (0.12, 0.30, 0.94),   # overshoot back
        (0.30, end,  1.00),   # settle
    ]
    for t0, t1, scale in stages:
        # Only the settled stage fades out; the two pop frames are too
        # brief to fade and would just flicker.
        alpha = (f"if(lt(t,{fade}),1,({end}-t)/0.35)"
                 if scale == 1.00 else "1")
        parts.append(
            f"drawtext=fontfile='{font}':text='{esc}':"
            f"fontsize={int(base * scale)}:fontcolor={color}:"
            f"borderw=7:bordercolor=black:"
            f"shadowcolor=black@0.8:shadowx=4:shadowy=4:"
            f"x=(w-text_w)/2:y=h*0.18-text_h/2:"
            f"alpha='{alpha}':enable='between(t\\,{t0}\\,{t1})'"
        )
    return parts


def _series_badge(lang: str, label: str, episode: int) -> list[str]:
    """A small standing mark next to the logo, for the whole video.

    The channel converts 0.12% of its viewers into subscribers, against a
    0.3-1% norm for Shorts. A viewer who likes one video has nothing telling
    them there are forty more of the same thing -- every Short reads as a
    standalone fact. An episode number is the cheapest way to say "this is a
    series": it sits in the corner, costs no retention, and does not spend
    three seconds of the video asking for a follow.
    """
    if not label:
        return []
    font = FONT_EN if lang == "en" else FONT_TH
    text = f"{label} · EP.{episode}" if episode else label
    return [
        f"drawtext=fontfile='{font}':text='{_escape(text)}':"
        f"fontsize=42:fontcolor=white@0.92:"
        f"borderw=4:bordercolor=black@0.75:"
        f"x=148:y=52"
    ]


def _endcard_overlay(style: str, lang: str, loop_text: str,
                     cta_text: str, audio_dur: float) -> list[str]:
    """Drawtext filters for the final ~3 seconds: a loop line that throws the
    viewer back to the start (boosts replay rate) plus a comment-bait CTA
    question. Both fade in/out together. Returns a list (possibly empty)."""
    parts = []
    if audio_dur < 4:
        return parts
    color = _HOOK_COLORS.get(style, _HOOK_COLORS["trending"])
    font  = FONT_EN if lang == "en" else FONT_TH
    t0 = round(audio_dur - 3.0, 2)
    t1 = round(audio_dur - 0.3, 2)
    fb = round(t0 + 0.3, 2)   # fade-in done
    fc = round(t1 - 0.4, 2)   # fade-out start
    alpha = (f"if(lt(t,{t0}),0,"
             f"if(lt(t,{fb}),(t-{t0})/0.3,"
             f"if(lt(t,{fc}),1,"
             f"if(lt(t,{t1}),({t1}-t)/0.4,0))))")
    if loop_text and loop_text.strip():
        disp = loop_text.strip().upper() if lang == "en" else loop_text.strip()
        size = 88 if len(disp) <= 18 else 68
        parts.append(
            f"drawtext=fontfile='{font}':text='{_escape(disp)}':"
            f"fontsize={size}:fontcolor={color}:borderw=6:bordercolor=black:"
            f"shadowcolor=black@0.7:shadowx=3:shadowy=3:"
            f"x=(w-text_w)/2:y=h*0.30:"
            f"alpha='{alpha}':enable='between(t\\,{t0}\\,{t1})'"
        )
    if cta_text and cta_text.strip():
        disp = cta_text.strip().upper() if lang == "en" else cta_text.strip()
        size = 64 if len(disp) <= 22 else 50
        parts.append(
            f"drawtext=fontfile='{font}':text='{_escape(disp)}':"
            f"fontsize={size}:fontcolor=white:borderw=5:bordercolor=black:"
            f"shadowcolor=black@0.7:shadowx=3:shadowy=3:"
            f"x=(w-text_w)/2:y=h*0.78:"
            f"alpha='{alpha}':enable='between(t\\,{t0}\\,{t1})'"
        )
    return parts


def _reveal_flash(style: str, reveal_start: float) -> list[str]:
    """A brief 0.10s half-white flash at the reveal moment — a pattern
    interrupt that makes the eye snap back right before the payoff. Skipped
    for narrative (would break the calm documentary mood)."""
    if style == "narrative" or reveal_start <= 0.5:
        return []
    s = round(reveal_start, 2)
    e = round(reveal_start + 0.10, 2)
    return [f"drawbox=x=0:y=0:w=iw:h=ih:color=white@0.45:t=fill:"
            f"enable='between(t\\,{s}\\,{e})'"]


def create_short(video_path: str, audio_path: str, title: str, script: str,
                 output_path: str, words: list[dict] = None,
                 clips: list[str] = None, lang: str = "en",
                 music_path: str = None,
                 cut_times: list[float] = None,
                 content_style: str = "trending",
                 entity_overlays: list[dict] = None,
                 hook_text: str = None,
                 loop_text: str = None,
                 cta_text: str = None,
                 series_label: str = None,
                 episode: int = 0) -> str:

    # Render the whole voiceover. The previous 62.0s ceiling truncated it
    # instead, and both failure modes reached the channel: a 64.0s Thai
    # voiceover published with its closing reveal sliced off mid-sentence,
    # and a 68.2s one aborted the render because segment boundaries beyond
    # the ceiling became negative trim durations. An over-long script is a
    # writing problem, so it is reported here and fixed in the prompt.
    speech_dur = _clip_duration(audio_path)
    audio_dur  = min(speech_dur + 0.5, HARD_CAP)
    if speech_dur > SPEECH_TARGET:
        print(f"  [editor] voiceover is {speech_dur:.1f}s against a "
              f"{SPEECH_TARGET:.0f}s target — rendering it in full; "
              f"the script wants tightening")

    # ── Master the audio bus ─────────────────────────────────────
    # Two faults here, and the first one cost reach on every video ever
    # published. amix defaults to normalize=1, which divides each input by
    # the input count, so mixing music in pulled the whole programme down
    # 6 dB. Measured output was -22 LUFS against YouTube's -14 delivery
    # target, and YouTube turns loud uploads down without ever turning
    # quiet ones up -- in a feed, this channel simply played quieter than
    # everything around it.
    #
    # The second: music sat at a flat 0.13, which is quiet enough to be
    # inaudible under speech and in the gaps alike, so it paid for its
    # bandwidth and contributed nothing. Ducked against the voiceover it
    # can sit high enough to carry the pauses and still clear out the
    # instant a word lands.
    final_audio = audio_path.replace(".mp3", "_master.m4a")
    if music_path:
        graph = (
            f"[0:a]asplit=2[vo][key];"
            f"[1:a]atrim=duration={audio_dur},asetpts=PTS-STARTPTS,"
            f"loudnorm=I={MUSIC_LUFS}:TP=-6:LRA=7[bed];"
            f"[bed][key]sidechaincompress=threshold=0.06:ratio=4:"
            f"attack=8:release=250:makeup=1[duck];"
            f"[vo][duck]amix=inputs=2:duration=first:normalize=0[mix];"
            f"[mix]{LOUDNORM}[aout]"
        )
        cmd = ["ffmpeg", "-y",
               "-i", audio_path,
               "-stream_loop", "-1", "-i", music_path,
               "-filter_complex", graph]
    else:
        cmd = ["ffmpeg", "-y",
               "-i", audio_path,
               "-filter_complex", f"[0:a]{LOUDNORM}[aout]"]
    cmd += ["-map", "[aout]", "-c:a", "aac", "-b:a", "160k",
            "-t", str(audio_dur), final_audio]

    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        print(f"  [audio] master failed, shipping the raw voiceover: "
              f"{(r.stderr or '')[-200:]}")
        final_audio = audio_path

    # ── Decide clips ────────────────────────────────────────────
    all_clips = clips if clips else [video_path]
    n_clips = len(all_clips)

    # ── Find cut points ─────────────────────────────────────────
    if cut_times:
        cut_points = sorted({round(t, 3) for t in cut_times
                             if MIN_SEGMENT < t < audio_dur - MIN_SEGMENT})
    else:
        cut_points = _find_cut_points(words or [], n_clips, audio_dur)

    # Collapse cuts that land on top of each other. Sentence boundaries from
    # silencedetect occasionally arrive a few milliseconds apart, and a
    # zero-length segment is a filtergraph error, not a fast cut.
    spaced = []
    for t in cut_points:
        if not spaced or t - spaced[-1] >= MIN_SEGMENT:
            spaced.append(t)
    cut_points = spaced

    boundaries = [0.0] + cut_points + [audio_dur]
    segments = [(boundaries[i], boundaries[i + 1]) for i in range(len(boundaries) - 1)]

    # ── Per-segment video filters ────────────────────────────────
    seg_filters, seg_labels = _build_segment_filters(all_clips, segments, audio_dur)

    # ── Concat all segments ──────────────────────────────────────
    concat_inputs = "".join(f"[{l}]" for l in seg_labels)
    concat_filter = f"{concat_inputs}concat=n={len(seg_labels)}:v=1:a=0[base]"

    # ── Text overlays ────────────────────────────────────────────
    text_parts = []

    # 0..2s retention hook -- big colored line above the subtitle band,
    # fades in/out with a subtle bounce. Style-specific copy makes the
    # first frame promise something so viewers wait past the swipe-away
    # threshold.
    text_parts += _hook_overlay(content_style, lang, hook_text)

    if lang == "th":
        pass  # all TH styles: ASS karaoke in pass 2 (see below)
    else:
        # EN: char-bounded word chunks per drawtext frame.
        # Distinct from TH (ASS karaoke sweep). Keeps Impact + drawtext identity.
        EN_STYLES = {
            "trending":  {"size": 84, "accent": "#FF2A2A"},
            "chaos":     {"size": 92, "accent": "#FFE000"},
            "narrative": {"size": 74, "accent": "#FF8C00"},
        }
        st         = EN_STYLES.get(content_style, EN_STYLES["trending"])
        MAX_CHARS  = 17       # Impact at these sizes fits ~17 chars wide in 1080
        MAX_WORDS  = 3

        valid = []
        for w in (words or []):
            clean = re.sub(r"[^\w\s]", "", w["word"]).strip()
            if clean:
                valid.append({"text": clean, "start": w["start"], "end": w["end"]})

        # Greedy pack: respect both MAX_CHARS and MAX_WORDS
        chunks  = []
        buf     = []
        buf_len = 0
        for w in valid:
            wl = len(w["text"])
            add_len = buf_len + (1 if buf else 0) + wl
            if buf and (add_len > MAX_CHARS or len(buf) >= MAX_WORDS):
                chunks.append(buf)
                buf, buf_len = [w], wl
            else:
                buf.append(w)
                buf_len = add_len
        if buf:
            chunks.append(buf)

        n_chunks = len(chunks)
        for ci, chunk in enumerate(chunks):
            t_start = chunk[0]["start"]
            t_end   = chunks[ci + 1][0]["start"] - WORD_GAP if ci + 1 < n_chunks else chunk[-1]["end"]
            t_end   = max(t_end, t_start + 0.3)

            chunk_text = " ".join(w["text"] for w in chunk)
            text = _escape(chunk_text)
            # Accent every 3rd chunk + final chunk for rhythm without randomness
            is_accent = (ci % 3 == 2) or (ci == n_chunks - 1)
            color = st["accent"] if is_accent else "white"
            text_parts.append(
                f"drawtext=fontfile='{FONT_EN}':text='{text}':"
                f"fontsize={st['size']}:fontcolor={color}:"
                f"bordercolor=black:borderw=6:"
                f"shadowcolor=black@0.7:shadowx=3:shadowy=3:"
                f"x=(w-text_w)/2:y=(h-text_h)/2+160:"
                f"enable='between(t\\,{t_start}\\,{t_end})'"
            )

    if not text_parts and lang != "th":
        caption = _escape(script[:80])
        text_parts.append(
            f"drawtext=fontfile='{FONT_EN}':text='{caption}':"
            f"fontsize=60:fontcolor=white:"
            f"bordercolor=black:borderw=6:"
            f"shadowcolor=black@0.7:shadowx=3:shadowy=3:"
            f"x=(w-text_w)/2:y=h*0.70"
        )

    # Reveal flash: a pattern interrupt on the sentence that pays the video
    # off. That is the third from last -- the last two are the second half of
    # the reveal and the loop line back to the hook. Firing it on the final
    # segment, as it used to, put the flash on the callback instead of on the
    # thing being called back to.
    reveal_idx   = max(len(boundaries) - 4, 1)
    reveal_start = boundaries[reveal_idx] if len(boundaries) >= 3 else 0.0
    text_parts += _reveal_flash(content_style, reveal_start)

    # End card: loop line (drives replay) + comment-bait CTA, last ~3s.
    text_parts += _endcard_overlay(content_style, lang, loop_text, cta_text, audio_dur)

    # Standing series mark beside the logo watermark.
    text_parts += _series_badge(lang, series_label, episode)

    # Color boost + vignette → [vtxt]
    text_parts.append("eq=saturation=1.35:contrast=1.08:brightness=0.02")
    text_parts.append("vignette=0.7")
    text_chain = f"[base]{','.join(text_parts)}[vtxt]"

    # ── Logo watermark overlay ───────────────────────────────────
    has_logo  = os.path.exists(LOGO_PATH)
    n_clips   = len(all_clips)
    audio_idx = n_clips
    logo_idx  = n_clips + 1 if has_logo else None

    base_fc = ";".join(seg_filters + [concat_filter])

    # ── Entity image overlays (real-world people/places/events) ──
    # Each overlay: {"image_path": str, "start": float, "end": float}
    # Skipped silently if file missing or invalid times.
    valid_overlays = []
    for ov in (entity_overlays or []):
        ip, s, e = ov.get("image_path"), ov.get("start"), ov.get("end")
        if ip and os.path.exists(ip) and s is not None and e is not None and e > s:
            valid_overlays.append({"image_path": ip, "start": float(s), "end": float(e)})

    first_overlay_idx = (logo_idx + 1) if has_logo else (n_clips + 1)

    def _build_overlay_chain(in_label: str) -> tuple[str, str]:
        """Return (filter_fragment, final_label) that overlays each entity image
        on top of `in_label` in turn. If no overlays, returns ('', in_label)."""
        if not valid_overlays:
            return "", in_label
        parts = []
        cur = in_label
        for i, ov in enumerate(valid_overlays):
            idx   = first_overlay_idx + i
            tag   = f"ent{i}"
            # 320px wide, semi-transparent white border, top-right corner with
            # 90px margin from top to clear the hook overlay band.
            parts.append(
                f"[{idx}:v]scale=320:-1,pad=iw+12:ih+12:6:6:color=white@0.95,"
                f"format=rgba,colorchannelmixer=aa=1.0[{tag}_img]"
            )
            nxt = f"vov{i}"
            parts.append(
                f"[{cur}][{tag}_img]overlay=W-w-30:90:"
                f"enable='between(t\\,{ov['start']:.3f}\\,{ov['end']:.3f})'[{nxt}]"
            )
            cur = nxt
        return ";".join(parts), cur

    if has_logo:
        text_and_logo = (
            text_chain + ";" +
            f"[{logo_idx}:v]scale=110:-1,format=rgba,colorchannelmixer=aa=0.85[logo];"
            f"[vtxt][logo]overlay=20:20[vlogo]"
        )
        ov_chain, final_label = _build_overlay_chain("vlogo")
        filter_complex = base_fc + ";" + text_and_logo + (";" + ov_chain if ov_chain else "")
        if final_label != "v":
            filter_complex += f";[{final_label}]copy[v]"
    else:
        ov_chain, final_label = _build_overlay_chain("vtxt")
        filter_complex = base_fc + ";" + text_chain + (";" + ov_chain if ov_chain else "")
        if final_label != "v":
            filter_complex += f";[{final_label}]copy[v]"

    # ── FFmpeg command ───────────────────────────────────────────
    cmd = ["ffmpeg", "-y"]
    for clip in all_clips:
        cmd += ["-i", clip]
    cmd += ["-i", final_audio]
    if has_logo:
        cmd += ["-i", LOGO_PATH]
    for ov in valid_overlays:
        cmd += ["-i", ov["image_path"]]
    audio_map = f"{audio_idx}:a"

    # Write filter_complex to temp file
    fc_file = tempfile.NamedTemporaryFile(mode='w', suffix='.txt',
                                          delete=False, encoding='utf-8')
    fc_file.write(filter_complex)
    fc_file.close()

    cmd += [
        "-filter_complex_script", fc_file.name,
        "-map", "[v]",
        "-map", audio_map,
        "-c:v", "libx264", "-preset", "fast", "-crf", "23",
        "-c:a", "aac", "-b:a", "128k",
        "-t", str(audio_dur),
        output_path
    ]

    result = subprocess.run(cmd, capture_output=True, text=True,
                            encoding="utf-8", errors="replace")
    os.unlink(fc_file.name)
    if final_audio != audio_path and os.path.exists(final_audio):
        os.remove(final_audio)
    if result.returncode != 0:
        print(result.stderr[-3000:])
        raise RuntimeError("FFmpeg failed")

    # ── Pass 2: burn Thai ASS karaoke subtitle (all TH styles) ───
    if lang == "th" and words:
        ass_file = tempfile.NamedTemporaryFile(suffix=".ass", delete=False,
                                               mode="w", encoding="utf-8")
        ass_file.close()
        pass1 = output_path.replace(".mp4", "_pass1.mp4")
        os.rename(output_path, pass1)
        try:
            _make_thai_ass(words, ass_file.name, style=content_style)
            _burn_ass(pass1, ass_file.name, output_path)
        finally:
            if os.path.exists(pass1):
                os.remove(pass1)
            if os.path.exists(ass_file.name):
                os.unlink(ass_file.name)

    return output_path
