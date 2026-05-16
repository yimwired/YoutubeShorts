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


def _gtts_thai(script: str, audio_path: str):
    from gtts import gTTS
    gTTS(text=script, lang="th", slow=False).save(audio_path)


def generate_voiceover(script: str, output_path: str,
                       lang: str = "en") -> tuple[str, list[dict]]:
    """
    EN : edge-tts en-US-GuyNeural
    TH : gTTS (Google TTS Thai) — ฟังดูธรรมชาติกว่าสำหรับภาษาไทย
    """
    if lang == "th":
        _gtts_thai(script, output_path)
        return output_path, []
    else:
        _run_with_retry(script=script, voice="en-US-GuyNeural",
                        rate="+0%", audio_path=output_path)
    return output_path, []
