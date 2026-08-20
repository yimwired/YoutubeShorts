"""Render the same Thai line in several Gemini TTS voices, to pick a pool.

Google documents a one-word character for each voice (Firm, Breezy,
Youthful...) but not whether it reads male or female, and the character
words do not survive the jump to Thai reliably either. So the only way to
choose is to listen.

    $env:PYTHONIOENCODING="utf-8"
    python tools/voice_samples.py                 # the default shortlist
    python tools/voice_samples.py Kore Puck Gacrux

Writes output/voice_samples/<Voice>.mp3, then edit VOICE_POOL in
src/tts_gemini.py to taste. Keep the pool alternating male/female down the
list -- rotation walks it in order, one step per video.

One request per voice, and the TTS models rate-limit per minute, so this
paces itself rather than firing them all at once.
"""

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.stdout.reconfigure(encoding="utf-8")

from dotenv import load_dotenv
load_dotenv()

from src.tts_gemini import _pcm_to_mp3, _synthesize

# Wide enough to hear the range: two of each documented character type
# that plausibly suits narration.
SHORTLIST = [
    "Charon", "Leda", "Orus", "Aoede", "Achird", "Sulafat",
    "Kore", "Puck", "Gacrux", "Vindemiatrix",
]

# Long enough to judge pacing and how the voice handles Thai numbers,
# short enough to stay cheap.
SAMPLE = (
    "รู้ปะว่าไฟแช็กอันแรกของโลก "
    "เกิดขึ้นตั้งแต่ปี หนึ่งแปดสองสาม ??? \n"
    "คนคิดค้นมันคือนักเคมีชาวเยอรมัน ??? \n"
    "แต่ที่หลายคนไม่รู้คือ มันอันตรายกว่าที่คิดเยอะเลย"
)

OUT_DIR = os.path.join("output", "voice_samples")
PAUSE   = 8      # seconds between calls, to stay under the per-minute limit


def main() -> int:
    voices = sys.argv[1:] or SHORTLIST
    os.makedirs(OUT_DIR, exist_ok=True)

    ok = 0
    for i, voice in enumerate(voices):
        dst = os.path.join(OUT_DIR, f"{voice}.mp3")
        print(f"[{i+1}/{len(voices)}] {voice} ...", end=" ", flush=True)
        pcm = _synthesize(SAMPLE, voice)
        if pcm and _pcm_to_mp3(pcm, dst):
            print(f"OK -> {dst}")
            ok += 1
        else:
            print("FAILED")
        if i < len(voices) - 1:
            time.sleep(PAUSE)

    print(f"\n{ok}/{len(voices)} rendered into {OUT_DIR}")
    print("Listen, then set VOICE_POOL in src/tts_gemini.py "
          "(alternate male/female down the list).")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
