import os, sys, time, shutil
sys.stdout.reconfigure(encoding="utf-8")
from dotenv import load_dotenv; load_dotenv()
from src.generator import generate_fact_script
from src.footage import fetch_multiple_clips
from src.tts import generate_voiceover
from src.captions import get_word_timestamps
from src.thumbnail import create_thumbnail
from src.music import get_track
from main import make_video

STYLE      = sys.argv[1] if len(sys.argv) > 1 else "trending"
OUTPUT_DIR = "output"
DOWNLOADS  = os.path.expanduser("~/Downloads")
os.makedirs(OUTPUT_DIR, exist_ok=True)

ts   = int(time.time())
data = generate_fact_script(style=STYLE)
print(f"EN: {data['title_en']}")

keywords = data.get("keywords", [])[:5]
clips    = fetch_multiple_clips(keywords, OUTPUT_DIR)
print(f"  {len(clips)} clips")

audio_en = os.path.join(OUTPUT_DIR, f"audio_{ts}_en.mp3")
generate_voiceover(data["script_en"], audio_en, lang="en", style=STYLE)

en_words = get_word_timestamps(audio_en, lang="en")
print(f"  {len(en_words)} EN words")

thumb_en = os.path.join(OUTPUT_DIR, f"thumb_{ts}_en.jpg")
create_thumbnail("", data["title_en"], thumb_en, thai_ver=False,
    photo_keyword=data.get("thumbnail_keyword"),
    ai_prompt=data.get("thumbnail_prompt"), clips=clips)

music = get_track(data.get("music_mood", "dramatic"))

final = make_video(clips, audio_en, data["title_en"], en_words, ts, "en",
                   music, thumb_path=thumb_en, cut_times=None, content_style=STYLE)
dest = os.path.join(DOWNLOADS, f"en_{STYLE}_v1.mp4")
shutil.copy(final, dest)
print(f"Saved: {dest}")
for c in clips:
    if os.path.exists(c): os.remove(c)
if os.path.exists(audio_en): os.remove(audio_en)
