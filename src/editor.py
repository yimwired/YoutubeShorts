import subprocess
import re
import tempfile
import os

_BASE     = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FONT_TH   = os.path.join(_BASE, "Kanit-Bold.ttf")
FONT_EN   = "C\\:/Windows/Fonts/impact.ttf" if os.name == "nt" else "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
LOGO_PATH = os.path.join(_BASE, "logo.png")
WORD_GAP  = 0.07

# ── Thai ASS karaoke helpers ─────────────────────────────────────────────────

_ASS_HEADER = """\
[Script Info]
ScriptType: v4.00+
PlayResX: 1080
PlayResY: 1920
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,Kanit,68,&H0000E0FF,&H00FFFFFF,&H00000000,&H90000000,-1,0,0,0,100,100,0,0,1,4,1,5,20,20,0,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""


def _to_ass_time(s: float) -> str:
    h, rem = divmod(s, 3600)
    m, sec = divmod(rem, 60)
    return f"{int(h)}:{int(m):02d}:{sec:05.2f}"


def _make_thai_ass(words: list, ass_path: str):
    """Build ASS karaoke file from word timestamp list."""
    lines, current = [], []
    for i, w in enumerate(words):
        text = re.sub(r"[^฀-๿\s]", "", w["word"]).strip()
        if not text:
            continue
        current.append(w)
        is_last  = i == len(words) - 1
        has_pause = (i + 1 < len(words) and
                     words[i + 1]["start"] - w["end"] > 0.3)
        if len(current) >= 4 or has_pause or is_last:
            lines.append(current)
            current = []

    with open(ass_path, "w", encoding="utf-8") as f:
        f.write(_ASS_HEADER)
        for line_words in lines:
            start = _to_ass_time(line_words[0]["start"])
            end   = _to_ass_time(line_words[-1]["end"] + 0.05)
            karaoke = ""
            for w in line_words:
                dur_cs = max(1, int((w["end"] - w["start"]) * 100))
                text   = re.sub(r"[^฀-๿\s]", "", w["word"]).strip()
                karaoke += f"{{\\kf{dur_cs}}}{text}"
            f.write(f'Dialogue: 0,{start},{end},Default,,0,0,0,,{{\\pos(540,1120)}}{karaoke}\n')


def _burn_ass(src: str, ass_path: str, dst: str):
    """Pass 2: burn ASS subtitle onto video. Run from ASS dir to avoid Windows path issues."""
    ass_dir  = os.path.dirname(os.path.abspath(ass_path))
    ass_name = os.path.basename(ass_path)
    r = subprocess.run(
        ["ffmpeg", "-y", "-i", os.path.abspath(src),
         "-vf", f"ass={ass_name}",
         "-c:v", "libx264", "-preset", "fast", "-crf", "23",
         "-c:a", "copy", os.path.abspath(dst)],
        capture_output=True, text=True,
        cwd=ass_dir,
    )
    if r.returncode != 0:
        print(r.stderr[-2000:])
        raise RuntimeError("ASS burn failed")

# Color scheme: white normal, red for emphasis (every ~5th word or last word of sentence)
def _word_color(word: str, idx: int, total: int) -> str:
    if idx == total - 1:          # last word = red
        return "red"
    if len(word) >= 6 and idx % 5 == 0:   # long words at intervals = red
        return "red"
    return "white"


def _escape(text: str) -> str:
    return (
        text.replace("\\", "\\\\")
            .replace("'", "\\'")
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

    if len(breaks) >= n - 1:
        # Pick evenly spaced from natural breaks
        step = len(breaks) / (n - 1)
        return [breaks[int(i * step)] for i in range(n - 1)]
    else:
        # Not enough pauses — divide equally
        step = duration / n
        return [step * i for i in range(1, n)]


def _build_segment_filters(clips: list[str], segments: list[tuple],
                            total_dur: float) -> tuple[list[str], list[str]]:
    """
    Build per-segment trim+crop filters.
    Returns (filter_parts, seg_labels) for use in concat.
    """
    filter_parts = []
    labels = []
    for i, (t_start, t_end) in enumerate(segments):
        seg_dur = round(t_end - t_start, 3)
        clip_idx = i % len(clips)
        clip_dur = _clip_duration(clips[clip_idx])
        label = f"seg{i}"
        if clip_dur >= seg_dur:
            start_t = max(0, (clip_dur - seg_dur) / 2)
            filter_parts.append(
                f"[{clip_idx}:v]trim=start={round(start_t,3)}:duration={seg_dur},"
                f"setpts=PTS-STARTPTS,"
                f"scale=1188:2112:force_original_aspect_ratio=increase,"
                f"crop=1080:1920[{label}]"
            )
        else:
            loops = int(seg_dur / clip_dur) + 2
            filter_parts.append(
                f"[{clip_idx}:v]loop=loop={loops}:size=9999:start=0,"
                f"trim=duration={seg_dur},setpts=PTS-STARTPTS,"
                f"scale=1188:2112:force_original_aspect_ratio=increase,"
                f"crop=1080:1920[{label}]"
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


def create_short(video_path: str, audio_path: str, title: str, script: str,
                 output_path: str, words: list[dict] = None,
                 clips: list[str] = None, lang: str = "en",
                 music_path: str = None,
                 cut_times: list[float] = None) -> str:

    audio_dur = min(_clip_duration(audio_path) + 0.5, 58.0)

    # ── Pre-mix audio: voiceover + background music ──────────────
    final_audio = audio_path
    if music_path:
        mixed = audio_path.replace(".mp3", "_mixed.m4a")
        mix_cmd = [
            "ffmpeg", "-y",
            "-i", audio_path,
            "-stream_loop", "-1", "-i", music_path,
            "-filter_complex",
            (f"[0:a]volume=1.0[vo];"
             f"[1:a]atrim=duration={audio_dur},asetpts=PTS-STARTPTS,volume=0.22[bg];"
             f"[vo][bg]amix=inputs=2:duration=first[aout]"),
            "-map", "[aout]",
            "-c:a", "aac", "-b:a", "128k",
            "-t", str(audio_dur), mixed
        ]
        r = subprocess.run(mix_cmd, capture_output=True, text=True)
        if r.returncode == 0:
            final_audio = mixed
        else:
            print("  [Music] Mix failed, using voiceover only")

    # ── Decide clips ────────────────────────────────────────────
    all_clips = clips if clips else [video_path]
    n_clips = len(all_clips)

    # ── Find cut points ─────────────────────────────────────────
    if cut_times:
        cut_points = [t for t in sorted(cut_times) if 0 < t < audio_dur]
    else:
        cut_points = _find_cut_points(words or [], n_clips, audio_dur)
    boundaries = [0.0] + cut_points + [audio_dur]
    segments = [(boundaries[i], boundaries[i + 1]) for i in range(len(boundaries) - 1)]

    # ── Per-segment video filters ────────────────────────────────
    seg_filters, seg_labels = _build_segment_filters(all_clips, segments, audio_dur)

    # ── Concat all segments ──────────────────────────────────────
    concat_inputs = "".join(f"[{l}]" for l in seg_labels)
    concat_filter = f"{concat_inputs}concat=n={len(segments)}:v=1:a=0[base]"

    # ── Text overlays ────────────────────────────────────────────
    text_parts = []

    if lang == "th":
        pass  # Thai subtitle handled via ASS in pass 2 (see below)
    else:
        # EN: word-by-word karaoke style
        for i, w in enumerate(words):
            t_start = w["start"]
            t_end = w["end"] if i + 1 >= len(words) else min(
                w["end"], words[i + 1]["start"] - WORD_GAP)
            t_end = max(t_end, t_start + 0.08)

            clean = re.sub(r"[^\w\s]", "", w["word"]).strip()
            if not clean:
                continue
            text  = _escape(clean)
            color = _word_color(clean, i, len(words))
            text_parts.append(
                f"drawtext=fontfile='{FONT_EN}':text='{text}':"
                f"fontsize=95:fontcolor={color}:"
                f"box=1:boxcolor=black@0.85:boxborderw=12:"
                f"x=(w-text_w)/2:y=(h-text_h)/2+160:"
                f"enable='between(t\\,{t_start}\\,{t_end})'"
            )

    if not text_parts and lang != "th":
        caption = _escape(script[:80])
        text_parts.append(
            f"drawtext=fontfile='{FONT_EN}':text='{caption}':"
            f"fontsize=60:fontcolor=white:box=1:boxcolor=black@0.85:boxborderw=12:"
            f"x=(w-text_w)/2:y=h*0.70"
        )

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

    if has_logo:
        filter_complex = (
            base_fc + ";" + text_chain + ";" +
            f"[{logo_idx}:v]scale=110:-1,format=rgba,colorchannelmixer=aa=0.85[logo];"
            f"[vtxt][logo]overlay=20:20[v]"
        )
    else:
        # No logo — rename [vtxt] → [v]
        filter_complex = base_fc + ";" + text_chain.replace("[vtxt]", "[v]")

    # ── FFmpeg command ───────────────────────────────────────────
    cmd = ["ffmpeg", "-y"]
    for clip in all_clips:
        cmd += ["-i", clip]
    cmd += ["-i", final_audio]
    if has_logo:
        cmd += ["-i", LOGO_PATH]
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

    result = subprocess.run(cmd, capture_output=True, text=True)
    os.unlink(fc_file.name)
    if final_audio != audio_path and os.path.exists(final_audio):
        os.remove(final_audio)
    if result.returncode != 0:
        print(result.stderr[-3000:])
        raise RuntimeError("FFmpeg failed")

    # ── Pass 2: burn Thai ASS karaoke subtitle ───────────────────
    if lang == "th" and words:
        ass_file = tempfile.NamedTemporaryFile(suffix=".ass", delete=False,
                                               mode="w", encoding="utf-8")
        ass_file.close()
        pass1 = output_path.replace(".mp4", "_pass1.mp4")
        os.rename(output_path, pass1)
        try:
            _make_thai_ass(words, ass_file.name)
            _burn_ass(pass1, ass_file.name, output_path)
        finally:
            if os.path.exists(pass1):
                os.remove(pass1)
            if os.path.exists(ass_file.name):
                os.unlink(ass_file.name)

    return output_path
