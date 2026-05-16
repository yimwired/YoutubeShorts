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


def generate_voiceover(script: str, output_path: str,
                       lang: str = "en",
                       style: str = "trending") -> tuple[str, list[dict]]:
    """
    style=trending  EN: GuyNeural +0%       TH: PremwadeeNeural +0% (Neural, returns boundaries)
    style=chaos     EN: AnaNeural +15%      TH: gTTS normal
    style=narrative EN: GuyNeural -5%       TH: gTTS atempo=0.88
    """
    if lang == "th":
        if style == "trending":
            boundaries = _premwadee_thai(script, output_path)
            return output_path, boundaries
        elif style == "narrative":
            _gtts_thai(script, output_path, atempo=0.88)
        else:  # chaos
            _gtts_thai(script, output_path)
        return output_path, []

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
