"""Render one explainer short without queueing or uploading.

The full path (research -> script -> footage -> voice -> subtitles ->
render) with the queue and YouTube steps left out, so a change to the
prompt, the voice or the subtitle style can be checked against a real
video instead of against reasoning about the filter graph.

    $env:PYTHONIOENCODING="utf-8"
    python test_explainer.py                # live trend or evergreen
    python test_explainer.py --no-trends    # force the evergreen path

Output: output/test_explainer_<ts>.mp4
"""

import os
import sys
import time

sys.stdout.reconfigure(encoding="utf-8")

from dotenv import load_dotenv
load_dotenv()

from generate_batch import EXPLAINER_CATEGORIES, _build_entity_overlays
from src.ai_broll import replace_with_ai_clips
from main import make_video, _sync_th_subs
from src.footage import fetch_multiple_clips
from src.generator import generate_explainer_script, _next_bucket
from src.music import get_track
from src.research import get_brief
from src.thumbnail import create_thumbnail
from src.topic_history import load_history
from src.tts import generate_voiceover

OUTPUT_DIR = "output"
STYLE      = "explainer"


def main() -> int:
    use_trends = "--no-trends" not in sys.argv
    timestamp  = int(time.time())

    category = _next_bucket("explainer_test", EXPLAINER_CATEGORIES)
    print(f"[test] bucket: {category}  (trends={'on' if use_trends else 'off'})")

    brief = get_brief(EXPLAINER_CATEGORIES, category,
                      avoid=load_history(), use_trends=use_trends)
    if not brief:
        print("[test] no usable brief — aborting")
        return 1

    print(f"[test] topic: {brief.topic}  ({brief.source}, {brief.confidence})")
    data = generate_explainer_script(brief, used_titles=load_history())

    sentences_th = [s.get("text_th", "") for s in data.get("sentences", [])]
    print(f"[test] title: {data['title_th']}")
    print(f"[test] hook : {data.get('hook_th')}")
    print(f"[test] {len(sentences_th)} sentences, "
          f"{len(data['script_th'])} Thai chars")

    clips = fetch_multiple_clips(data["keywords"], OUTPUT_DIR)
    if not clips:
        print("[test] no footage — aborting")
        return 1

    clips = replace_with_ai_clips(clips, data.get("sentences", []),
                                  OUTPUT_DIR, timestamp)

    audio = os.path.join(OUTPUT_DIR, f"audio_test_{timestamp}.mp3")
    _, boundaries = generate_voiceover(data["script_th"], audio, lang="th",
                                       style=STYLE, sentences=sentences_th)
    words = _sync_th_subs(data["script_th"], audio,
                          sentences_th=sentences_th or None,
                          style=STYLE, tts_boundaries=boundaries)

    thumb = os.path.join(OUTPUT_DIR, f"thumb_test_{timestamp}.jpg")
    create_thumbnail(None, data["title_th"], thumb,
                     photo_keyword=data.get("thumbnail_keyword"),
                     ai_prompt=data.get("thumbnail_prompt"),
                     lang="th",
                     badge_text=data.get("thumb_text_th"),
                     series_tag="TEST", episode=1)

    overlays = _build_entity_overlays(data, timestamp, boundaries)

    final = make_video(clips, audio, data["title_th"], words, timestamp,
                       "th", get_track(data.get("music_mood", "dramatic")),
                       thumb_path=thumb, content_style=STYLE,
                       entity_overlays=overlays,
                       hook_text=data.get("hook_th"),
                       loop_text=data.get("loop_th"),
                       cta_text=data.get("cta_th"),
                       title_card=False, outro_card=False)

    test_path = os.path.join(OUTPUT_DIR, f"test_explainer_{timestamp}.mp4")
    os.replace(final, test_path)
    for path in clips + [audio]:
        if os.path.exists(path):
            os.remove(path)

    print(f"\n[test] done: {test_path}")
    print(f"[test] thumbnail: {thumb}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
