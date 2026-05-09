import os
import random
import requests
import subprocess

MUSIC_DIR = "music"
CACHE_DIR = os.path.join(MUSIC_DIR, ".cache")

BASE_URL = "https://www.soundhelix.com/examples/mp3/SoundHelix-Song-{}.mp3"

MOOD_SONGS = {
    "mysterious":  [9, 11, 14],
    "dramatic":    [1, 3, 5],
    "epic":        [1, 5, 6],
    "upbeat":      [2, 7, 10],
    "inspiring":   [6, 10, 13],
    "peaceful":    [4, 8, 15],
    "tense":       [12, 3, 11],
    "melancholic": [14, 8, 15],
}
DEFAULT_SONGS = [1, 3, 5, 9]


def _is_audio(path: str) -> bool:
    try:
        r = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "stream=codec_type",
             "-of", "default=noprint_wrappers=1:nokey=1", path],
            capture_output=True, text=True, timeout=5
        )
        return "audio" in r.stdout
    except Exception:
        return False


def _download(url: str, path: str) -> str | None:
    try:
        r = requests.get(url, timeout=30, headers={"User-Agent": "Mozilla/5.0"})
        if r.status_code != 200 or len(r.content) < 10000:
            return None
        with open(path, "wb") as f:
            f.write(r.content)
        return path if _is_audio(path) else None
    except Exception:
        return None


def get_track(mood: str = None) -> str | None:
    """Return path to a royalty-free background music track."""
    os.makedirs(CACHE_DIR, exist_ok=True)

    # Local files take priority (Film's own music)
    local = [f for f in os.listdir(MUSIC_DIR)
             if f.lower().endswith((".mp3", ".wav", ".m4a"))
             and not f.startswith(".")]
    if local:
        return os.path.join(MUSIC_DIR, random.choice(local))

    nums = list(MOOD_SONGS.get((mood or "").lower(), DEFAULT_SONGS))
    random.shuffle(nums)

    for n in nums:
        cached = os.path.join(CACHE_DIR, f"song_{n}.mp3")
        if os.path.exists(cached) and _is_audio(cached):
            return cached
        url = BASE_URL.format(n)
        print(f"    [Music] Downloading SoundHelix-Song-{n}...")
        path = _download(url, cached)
        if path:
            return path

    return None
