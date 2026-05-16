import os, sys, time, shutil
sys.stdout.reconfigure(encoding="utf-8")
from dotenv import load_dotenv; load_dotenv()
from src.generator import generate_fact_script
from src.footage import fetch_multiple_clips
from src.tts import generate_voiceover
from src.thumbnail import create_thumbnail
from src.music import get_track
from main import _sync_th_subs, make_video

OUTPUT_DIR = "output"
DOWNLOADS  = os.path.expanduser("~/Downloads")
os.makedirs(OUTPUT_DIR, exist_ok=True)

ts = int(time.time())
data = generate_fact_script(style="chaos")
print(f"TH: {data['title_th']}")

keywords = data.get("keywords", [])
clips = fetch_multiple_clips(keywords, OUTPUT_DIR)
print(f"  {len(clips)} clips")

audio_th = os.path.join(OUTPUT_DIR, f"audio_{ts}_th.mp3")
_, th_boundaries = generate_voiceover(data["script_th"], audio_th, lang="th", style="chaos")

sentences_th = [s.get("text_th", "") for s in data.get("sentences", [])]
th_words = _sync_th_subs(data["script_th"], audio_th,
                         sentences_th=sentences_th or None,
                         style="chaos")
print(f"  {len(th_words)} word chunks")

thumb_th = os.path.join(OUTPUT_DIR, f"thumb_{ts}_th.jpg")
create_thumbnail("", data["title_en"], thumb_th, thai_ver=True,
    photo_keyword=data.get("thumbnail_keyword"),
    ai_prompt=data.get("thumbnail_prompt"), clips=clips)

music = get_track(data.get("music_mood", "dramatic"))

final = make_video(clips, audio_th, data["title_th"], th_words, ts, "th",
                   music, thumb_path=thumb_th, cut_times=None, content_style="chaos")
dest = os.path.join(DOWNLOADS, "chaos_เที่ยง_v1.mp4")
shutil.copy(final, dest)
print(f"Saved: {dest}")
for c in clips: os.remove(c)
os.remove(audio_th)
