"""Gemini TTS voiceover — the natural-sounding Thai path.

Why one call for the whole script instead of one per sentence (which is
what the edge-tts path does): the TTS preview models are on a tight free
quota and return 503 under load. Sixteen calls a day is sixteen chances
to fail and blow the quota; one call is one. Per-sentence timing, which
the Thai karaoke subtitles need, is recovered afterwards from the
rendered audio with the same silencedetect pass the English track has
always used.

Fails soft on purpose: any failure returns None and the caller drops
back to edge-tts Premwadee, so a bad day at Google costs naturalness,
never the upload.
"""

from __future__ import annotations

import os
import subprocess
import tempfile
import time
import wave

from src.rate_tracker import record

_MODEL = os.getenv("GEMINI_TTS_MODEL", "gemini-3.1-flash-tts-preview")
_VOICE = os.getenv("GEMINI_TTS_VOICE", "Leda")

_SAMPLE_RATE = 24000
_RETRIES     = 4
# Multiplied by attempt number, so waits run 15s / 30s / 45s. Deliberately
# long: the errors seen in practice are 429 (per-minute quota) and 503
# (capacity), and both need a minute-scale wait, not a second-scale one.
# At one call per video per day this costs nothing when it is not needed.
_BACKOFF     = 15

# Separator that buys a clean, uniform pause between lines.
#
# Measured on a 10-line Thai script: with no separator the model scatters
# 0.3-0.5s rhetorical pauses mid-sentence and silencedetect can no longer
# tell a line break from a dramatic beat (27 candidate gaps for 9 real
# breaks). "..." over-corrects into 2.2-2.7s of dead air. "???" lands at
# 0.63-0.85s per break, nine breaks for nine boundaries, and pulls total
# runtime from 57s down to 44s.
_SEP = os.getenv("GEMINI_TTS_SEP", " ??? \n")

# Pauses shorter than this are the model breathing inside a sentence;
# the separator reliably produces longer ones.
_BREAK_SILENCE = 0.45

_STYLE = (
    "อ่านข้อความต่อไปนี้เป็นภาษาไทย ด้วยน้ำเสียงแบบเพื่อนเล่าเรื่องให้ฟัง "
    "สนุก กระตือรือร้นกำลังดี ไม่ใช่ผู้ประกาศข่าว พูดจังหวะกระชับ ไม่ลากเสียง "
    "เน้นเสียงตรงคำที่น่าตกใจ "
    "สำคัญมาก: หยุดเงียบสนิทหนึ่งวินาทีทุกครั้งที่เจอเครื่องหมาย ??? "
    "และห้ามอ่านออกเสียงเครื่องหมาย ??? เด็ดขาด "
    "ห้ามอ่านคำสั่งนี้ อ่านเฉพาะข้อความหลังบรรทัดนี้:\n\n"
)


def _client():
    try:
        from google import genai
    except ImportError:
        return None
    key = os.getenv("GEMINI_API_KEY")
    return genai.Client(api_key=key) if key else None


def _synthesize(text: str) -> bytes | None:
    """One TTS request with backoff. Returns raw 24kHz mono PCM."""
    client = _client()
    if client is None:
        print("  [gemini-tts] no GEMINI_API_KEY / google-genai — skipping")
        return None

    from google.genai import types

    config = types.GenerateContentConfig(
        response_modalities=["AUDIO"],
        speech_config=types.SpeechConfig(
            voice_config=types.VoiceConfig(
                prebuilt_voice_config=types.PrebuiltVoiceConfig(
                    voice_name=_VOICE))),
    )

    for attempt in range(1, _RETRIES + 1):
        try:
            resp = client.models.generate_content(
                model=_MODEL, contents=_STYLE + text, config=config)
            # Tracked separately from the text models: this is the only
            # call in the pipeline that is actually billed, so it is the
            # one worth watching in rate_usage.json.
            record("gemini_tts")
            return resp.candidates[0].content.parts[0].inline_data.data
        except Exception as e:
            name = type(e).__name__
            if attempt < _RETRIES:
                wait = _BACKOFF * attempt
                print(f"  [gemini-tts] {name} — retry {attempt}/{_RETRIES - 1} "
                      f"in {wait}s")
                time.sleep(wait)
            else:
                print(f"  [gemini-tts] giving up after {_RETRIES} attempts: "
                      f"{name}: {str(e)[:160]}")
    return None


def _pcm_to_mp3(pcm: bytes, mp3_path: str) -> bool:
    tmp_wav = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
    tmp_wav.close()
    try:
        with wave.open(tmp_wav.name, "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(_SAMPLE_RATE)
            wf.writeframes(pcm)
        r = subprocess.run(
            ["ffmpeg", "-y", "-i", tmp_wav.name,
             # Trim the model's warm-up silence so the video opens on speech,
             # then normalize so the voice sits above the background music
             # at a consistent level regardless of how loud this take was.
             "-af", "silenceremove=start_periods=1:start_silence=0.05:"
                    "start_threshold=-40dB,loudnorm=I=-16:TP=-1.5:LRA=11",
             "-acodec", "libmp3lame", "-q:a", "3", mp3_path],
            capture_output=True, text=True)
        if r.returncode != 0:
            print(f"  [gemini-tts] ffmpeg encode failed: {r.stderr[-300:]}")
            return False
        return True
    finally:
        try:
            os.unlink(tmp_wav.name)
        except OSError:
            pass


def generate_thai(sentences: list[str], output_path: str
                  ) -> tuple[bool, list[dict]]:
    """Render `sentences` to `output_path` as one take.

    Returns (ok, boundaries). `boundaries` holds one {start, end} per
    sentence and is empty when the pauses could not be resolved — the
    caller should then treat the take as unusable for karaoke timing and
    fall back, rather than shipping drifting subtitles.
    """
    lines = [s.strip() for s in sentences if s and s.strip()]
    if not lines:
        return False, []

    pcm = _synthesize(_SEP.join(lines))
    if not pcm:
        return False, []
    if not _pcm_to_mp3(pcm, output_path):
        return False, []

    from main import _silence_boundaries
    boundaries = _silence_boundaries(output_path, len(lines),
                                     min_silence=_BREAK_SILENCE)
    if len(boundaries) != len(lines):
        print(f"  [gemini-tts] boundary detect got {len(boundaries)}/"
              f"{len(lines)} — audio unusable for karaoke timing")
        return False, []

    dur = boundaries[-1]["end"]
    print(f"  [gemini-tts] OK — {dur:.1f}s, {len(lines)} sentence boundaries "
          f"({_MODEL}, voice={_VOICE})")
    return True, boundaries
