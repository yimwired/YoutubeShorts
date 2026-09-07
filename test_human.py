"""Render one morning behaviour short without queueing or uploading.

The full path (research -> script -> footage -> voice -> subtitles ->
render) with the queue and YouTube steps left out, so a change to the
prompt, the voice or the footage rules can be checked against a real
video instead of against reasoning about the filter graph.

    $env:PYTHONIOENCODING="utf-8"
    python test_human.py

Output: output/test_human_<ts>.mp4

No --no-trends switch, unlike test_explainer.py: this format has no trend
half. A behaviour is evergreen by definition.
"""

import json
import os
import sys
import time

sys.stdout.reconfigure(encoding="utf-8")

from dotenv import load_dotenv
load_dotenv()

from generate_batch import HUMAN_CATEGORIES, SERIES_NAMES
from main import make_video, _sync_th_subs
from src.footage import fetch_multiple_clips
from src.generator import generate_human_script, _next_bucket
from src.music import get_track
from src.research import research_human
from src.thumbnail import create_thumbnail
from src.topic_history import load_history
from src.tts import generate_voiceover

OUTPUT_DIR = "output"
STYLE      = "human"


def _episode_preview() -> int:
    """Next episode number, without writing the counter back."""
    try:
        with open("series_state.json", encoding="utf-8") as f:
            return json.load(f).get("human", 0) + 1
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return 0


def main() -> int:
    timestamp = int(time.time())

    category = _next_bucket("human_test", HUMAN_CATEGORIES)
    print(f"[test] bucket: {category}")

    brief = research_human(category, avoid=load_history())
    if not brief:
        print("[test] no usable brief — aborting")
        return 1

    print(f"[test] topic: {brief.topic}  ({brief.confidence})")
    data = generate_human_script(brief, used_titles=load_history())

    sentences_th = [s.get("text_th", "") for s in data.get("sentences", [])]
    print(f"[test] title: {data['title_th']}")
    print(f"[test] hook : {data.get('hook_th')}")
    print(f"[test] {len(sentences_th)} sentences, "
          f"{len(data['script_th'])} Thai chars")
    for i, kw in enumerate(data["keywords"], 1):
        print(f"    [{i}] {kw['specific']}")

    clips = fetch_multiple_clips(data["keywords"], OUTPUT_DIR)
    if not clips:
        print("[test] no footage — aborting")
        return 1

    # No replace_with_ai_clips here, deliberately: every shot in this format
    # is footage of a real person.

    audio = os.path.join(OUTPUT_DIR, f"audio_test_{timestamp}.mp3")
    _, boundaries = generate_voiceover(data["script_th"], audio, lang="th",
                                       style=STYLE, sentences=sentences_th)
    words = _sync_th_subs(data["script_th"], audio,
                          sentences_th=sentences_th or None,
                          style=STYLE, tts_boundaries=boundaries)

    thumb = os.path.join(OUTPUT_DIR, f"thumb_test_{timestamp}.jpg")
    create_thumbnail(None, data["title_th"], thumb,
                     photo_keyword=data.get("thumbnail_keyword"),
                     ai_prompt=None,
                     lang="th",
                     badge_text=data.get("thumb_text_th"),
                     series_tag="TEST", episode=1)

    final = make_video(clips, audio, data["title_th"], words, timestamp,
                       "th", get_track(data.get("music_mood", "upbeat")),
                       thumb_path=thumb, content_style=STYLE,
                       hook_text=data.get("hook_th"),
                       loop_text=data.get("loop_th"),
                       cta_text=data.get("cta_th"),
                       # Read the episode counter without advancing it -- a
                       # test render should look like production, not consume
                       # a number from it.
                       series_label=SERIES_NAMES["human"],
                       episode=_episode_preview(),
                       title_card=False, outro_card=False)

    test_path = os.path.join(OUTPUT_DIR, f"test_human_{timestamp}.mp4")
    os.replace(final, test_path)
    for path in clips + [audio]:
        if os.path.exists(path):
            os.remove(path)

    print(f"\n[test] done: {test_path}")
    print(f"[test] thumbnail: {thumb}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
