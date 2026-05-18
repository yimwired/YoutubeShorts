import asyncio
import time
import edge_tts
from edge_tts.exceptions import NoAudioReceived

_RETRY_ATTEMPTS = 3
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
                time.sleep(_RETRY_DELAY)
            else:
                raise


def _gtts_thai(script: str, audio_path: str, atempo: float = 1.0):
    import subprocess, tempfile, os
    from gtts import gTTS
    if atempo == 1.0:
        gTTS(text=script, lang="th", slow=False).save(audio_path)
    else:
        tmp = tempfile.NamedTemporaryFile(suffix=".mp3", delete=False)
        tmp.close()
        gTTS(text=script, lang="th", slow=False).save(tmp.name)
        subprocess.run(
            ["ffmpeg", "-y", "-i", tmp.name,
             "-filter:a", f"atempo={atempo}",
             audio_path],
            check=True, capture_output=True,
        )
        os.unlink(tmp.name)


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
                time.sleep(_RETRY_DELAY)
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
    """Synthesize each sentence into its own audio, concat with ffmpeg, and
    accumulate sentence boundaries from each chunk's measured duration.
    Returns one {start, end} per sentence — ground truth, no estimation."""
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
            chunk_path = _os.path.join(tmpdir, f"{i:03d}.mp3")
            for attempt in range(1, _RETRY_ATTEMPTS + 1):
                try:
                    asyncio.run(_edge_thai_one(s, chunk_path, rate))
                    break
                except NoAudioReceived:
                    if attempt < _RETRY_ATTEMPTS:
                        time.sleep(_RETRY_DELAY)
                    else:
                        raise
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

        # Concat via ffmpeg concat demuxer
        list_path = _os.path.join(tmpdir, "list.txt")
        with open(list_path, "w", encoding="utf-8") as f:
            for c in chunks:
                f.write(f"file '{c.replace(chr(92), '/')}'\n")
        subprocess.run(
            ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", list_path,
             "-c", "copy", audio_path],
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
    style=trending  EN: GuyNeural +0%   TH: PremwadeeNeural +0%
    style=chaos     EN: AnaNeural +15%  TH: PremwadeeNeural +15%
    style=narrative EN: GuyNeural -5%   TH: PremwadeeNeural -8%

    For TH: pass `sentences` (list of script sentences) to get ground-truth
    per-sentence boundaries via per-sentence TTS + concat. Without it,
    one TTS call is made and boundaries come from edge-tts SentenceBoundary
    events (which Premwadee TH does not emit reliably).
    """
    if lang == "th":
        rate = {"chaos": "+15%", "narrative": "-8%"}.get(style, "+0%")
        if sentences and len(sentences) > 1:
            boundaries = _premwadee_per_sentence(sentences, output_path, rate=rate)
        else:
            boundaries = _premwadee_thai(script, output_path, rate=rate)
        return output_path, boundaries

    # English
    if style == "chaos":
        _run_with_retry(script=script, voice="en-US-AnaNeural",
                        rate="+15%", audio_path=output_path)
    elif style == "narrative":
        _run_with_retry(script=script, voice="en-US-GuyNeural",
                        rate="-5%", audio_path=output_path)
    else:
        _run_with_retry(script=script, voice="en-US-GuyNeural",
                        rate="+0%", audio_path=output_path)
    return output_path, []
