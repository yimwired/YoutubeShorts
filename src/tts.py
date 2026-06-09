import asyncio
import time
import edge_tts
from edge_tts.exceptions import NoAudioReceived

_RETRY_ATTEMPTS = 5
_RETRY_DELAY    = 3


async def _edge_stream(script: str, voice: str, rate: str,
                       audio_path: str) -> list[dict]:
    comm = edge_tts.Communicate(script, voice, rate=rate)
    audio_buf  = bytearray()
    boundaries = []
    async for chunk in comm.stream():
        if chunk["type"] == "audio":
            audio_buf.extend(chunk["data"])
        elif chunk["type"] == "SentenceBoundary":
            boundaries.append({
                "start": chunk["offset"] / 10_000_000,
                "end":   (chunk["offset"] + chunk["duration"]) / 10_000_000,
            })
    with open(audio_path, "wb") as f:
        f.write(bytes(audio_buf))
    return boundaries


def _run_with_retry(script: str, voice: str, rate: str,
                    audio_path: str) -> list[dict]:
    for attempt in range(1, _RETRY_ATTEMPTS + 1):
        try:
            return asyncio.run(_edge_stream(script, voice, rate, audio_path))
        except NoAudioReceived:
            if attempt < _RETRY_ATTEMPTS:
                print(f"  [TTS] NoAudioReceived — retry {attempt}/{_RETRY_ATTEMPTS - 1}")
                time.sleep(_RETRY_DELAY * attempt)
            else:
                raise


def _rate_to_atempo(rate: str) -> float:
    """Convert an edge-tts rate string to an ffmpeg atempo factor.
    '+8%' -> 1.08, '-5%' -> 0.95, '+0%' -> 1.0."""
    try:
        return round(1.0 + int(rate.strip().replace("%", "")) / 100.0, 3)
    except Exception:
        return 1.0


def _gtts(script: str, audio_path: str, lang: str = "th", atempo: float = 1.0):
    """gTTS fallback for any language. Used when edge-tts is unreachable
    (e.g. Microsoft 'No audio was received' outage)."""
    import subprocess, tempfile, os
    from gtts import gTTS
    if abs(atempo - 1.0) < 1e-3:
        gTTS(text=script, lang=lang, slow=False).save(audio_path)
    else:
        tmp = tempfile.NamedTemporaryFile(suffix=".mp3", delete=False)
        tmp.close()
        gTTS(text=script, lang=lang, slow=False).save(tmp.name)
        subprocess.run(
            ["ffmpeg", "-y", "-i", tmp.name,
             "-filter:a", f"atempo={atempo}",
             audio_path],
            check=True, capture_output=True,
        )
        os.unlink(tmp.name)


def _gtts_thai(script: str, audio_path: str, atempo: float = 1.0):
    _gtts(script, audio_path, lang="th", atempo=atempo)


async def _edge_thai_stream(script: str, audio_path: str,
                            rate: str = "+0%") -> list[dict]:
    comm = edge_tts.Communicate(script, "th-TH-PremwadeeNeural", rate=rate)
    audio_buf  = bytearray()
    boundaries = []
    async for chunk in comm.stream():
        if chunk["type"] == "audio":
            audio_buf.extend(chunk["data"])
        elif chunk["type"] == "SentenceBoundary":
            boundaries.append({
                "start": chunk["offset"] / 10_000_000,
                "end":   (chunk["offset"] + chunk["duration"]) / 10_000_000,
            })
    with open(audio_path, "wb") as f:
        f.write(bytes(audio_buf))
    return boundaries


def _premwadee_thai(script: str, audio_path: str,
                    rate: str = "+0%") -> list[dict]:
    for attempt in range(1, _RETRY_ATTEMPTS + 1):
        try:
            return asyncio.run(_edge_thai_stream(script, audio_path, rate))
        except NoAudioReceived:
            if attempt < _RETRY_ATTEMPTS:
                print(f"  [TTS] PremwadeeNeural retry {attempt}/{_RETRY_ATTEMPTS - 1}")
                time.sleep(_RETRY_DELAY * attempt)
            else:
                print("  [TTS] PremwadeeNeural failed — fallback gTTS")
                _gtts_thai(script, audio_path)
                return []
    return []


async def _edge_thai_one(sentence: str, audio_path: str, rate: str):
    """TTS a single sentence to its own mp3 file. No boundaries needed."""
    comm = edge_tts.Communicate(sentence, "th-TH-PremwadeeNeural", rate=rate)
    buf = bytearray()
    async for chunk in comm.stream():
        if chunk["type"] == "audio":
            buf.extend(chunk["data"])
    with open(audio_path, "wb") as f:
        f.write(bytes(buf))


def _premwadee_per_sentence(sentences: list[str], audio_path: str,
                            rate: str = "+0%") -> list[dict]:
    """Synthesize each sentence into its own audio, trim head/tail silence,
    concat with ffmpeg, and accumulate sentence boundaries from each
    trimmed chunk's measured duration. Boundaries match the final audio
    exactly. Returns one {start, end} per sentence."""
    import subprocess, tempfile, os as _os
    boundaries = []
    chunks = []
    cum = 0.0
    tmpdir = tempfile.mkdtemp(prefix="ttsseg_")
    try:
        for i, s in enumerate(sentences):
            s = s.strip()
            if not s:
                continue
            raw_path     = _os.path.join(tmpdir, f"{i:03d}_raw.mp3")
            trimmed_path = _os.path.join(tmpdir, f"{i:03d}.mp3")
            for attempt in range(1, _RETRY_ATTEMPTS + 1):
                try:
                    asyncio.run(_edge_thai_one(s, raw_path, rate))
                    break
                except NoAudioReceived:
                    if attempt < _RETRY_ATTEMPTS:
                        time.sleep(_RETRY_DELAY * attempt)
                    else:
                        print(f"  [TTS] Premwadee sentence {i} failed — fallback gTTS")
                        _gtts(s, raw_path, lang="th",
                              atempo=_rate_to_atempo(rate))

            # Trim leading + trailing silence (threshold -38dB, leave 50ms head pad)
            trim_r = subprocess.run(
                ["ffmpeg", "-y", "-i", raw_path,
                 "-af",
                 "silenceremove=start_periods=1:start_silence=0.05:start_threshold=-38dB,"
                 "areverse,"
                 "silenceremove=start_periods=1:start_silence=0.05:start_threshold=-38dB,"
                 "areverse",
                 "-acodec", "libmp3lame", "-q:a", "4",
                 trimmed_path],
                capture_output=True, text=True
            )
            chunk_path = trimmed_path if trim_r.returncode == 0 else raw_path

            dur_r = subprocess.run(
                ["ffprobe", "-v", "error", "-show_entries", "format=duration",
                 "-of", "default=noprint_wrappers=1:nokey=1", chunk_path],
                capture_output=True, text=True
            )
            try:
                dur = float(dur_r.stdout.strip())
            except Exception:
                dur = 0.0
            boundaries.append({"start": round(cum, 3),
                               "end":   round(cum + dur, 3)})
            cum += dur
            chunks.append(chunk_path)

        # Concat via ffmpeg concat demuxer. Re-encode (not -c copy) because
        # trimmed chunks may have varying lame headers from silenceremove.
        list_path = _os.path.join(tmpdir, "list.txt")
        with open(list_path, "w", encoding="utf-8") as f:
            for c in chunks:
                f.write(f"file '{c.replace(chr(92), '/')}'\n")
        subprocess.run(
            ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", list_path,
             "-acodec", "libmp3lame", "-q:a", "4", audio_path],
            capture_output=True, text=True, check=True,
        )
    finally:
        import shutil
        shutil.rmtree(tmpdir, ignore_errors=True)
    return boundaries


def generate_voiceover(script: str, output_path: str,
                       lang: str = "en",
                       style: str = "trending",
                       sentences: list[str] = None) -> tuple[str, list[dict]]:
    """
    style=trending  EN: GuyNeural +5%   TH: PremwadeeNeural +8%   (friendly/energetic)
    style=chaos     EN: AnaNeural +5%   TH: PremwadeeNeural +5%   (light, breezy)
    style=narrative EN: GuyNeural -5%   TH: PremwadeeNeural -8%

    For TH: pass `sentences` (list of script sentences) to get ground-truth
    per-sentence boundaries via per-sentence TTS + concat. Without it,
    one TTS call is made and boundaries come from edge-tts SentenceBoundary
    events (which Premwadee TH does not emit reliably).
    """
    if lang == "th":
        rate = {"chaos": "+5%", "narrative": "-8%"}.get(style, "+8%")
        if sentences and len(sentences) > 1:
            boundaries = _premwadee_per_sentence(sentences, output_path, rate=rate)
        else:
            boundaries = _premwadee_thai(script, output_path, rate=rate)
        return output_path, boundaries

    # English — edge-tts with gTTS(en) fallback if Microsoft's endpoint is
    # unreachable (transient 'No audio was received' outage).
    voice, rate = {
        "chaos":     ("en-US-AnaNeural", "+5%"),
        "narrative": ("en-US-GuyNeural", "-5%"),
    }.get(style, ("en-US-GuyNeural", "+5%"))
    try:
        _run_with_retry(script=script, voice=voice, rate=rate,
                        audio_path=output_path)
    except NoAudioReceived:
        print("  [TTS] edge-tts EN failed — fallback gTTS(en)")
        _gtts(script, output_path, lang="en", atempo=_rate_to_atempo(rate))
    return output_path, []
