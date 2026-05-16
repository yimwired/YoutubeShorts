import requests
import os
import random
from src.rate_tracker import record

PEXELS_KEY  = os.getenv("PEXELS_API_KEY")
PIXABAY_KEY = os.getenv("PIXABAY_API_KEY")


def _download(url: str, path: str) -> str:
    r = requests.get(url, stream=True, timeout=60)
    r.raise_for_status()
    with open(path, "wb") as f:
        for chunk in r.iter_content(chunk_size=8192):
            f.write(chunk)
    return path


def _pexels(keyword: str, output_path: str) -> str | None:
    headers = {"Authorization": PEXELS_KEY}
    params  = {"query": keyword, "per_page": 10, "orientation": "portrait"}
    resp = requests.get("https://api.pexels.com/videos/search",
                        headers=headers, params=params, timeout=15)
    record("pexels")
    if resp.status_code != 200:
        return None

    candidates = []
    for video in resp.json().get("videos", []):
        dur = video.get("duration", 0)
        if dur < 5:   # skip clips shorter than 5s
            continue
        best = None
        best_score = -1
        for f in video.get("video_files", []):
            h = f.get("height", 0)
            w = f.get("width", 0)
            if h <= w:   # must be portrait
                continue
            # Prefer HD + long duration
            q_score = 2 if f.get("quality") == "hd" else 1
            score = q_score * h + dur
            if score > best_score:
                best_score = score
                best = f["link"]
        if best:
            candidates.append((best_score, best))

    if not candidates:
        return None
    candidates.sort(reverse=True)
    # random variety from top 3 (portrait-first, skip <5s already filtered)
    pick = random.choice(candidates[:min(3, len(candidates))])
    return _download(pick[1], output_path)


def _pixabay(keyword: str, output_path: str) -> str | None:
    if not PIXABAY_KEY or PIXABAY_KEY == "your_key_here":
        return None
    params = {
        "key": PIXABAY_KEY, "q": keyword, "video_type": "film",
        "per_page": 10, "safesearch": "true",
    }
    resp = requests.get("https://pixabay.com/api/videos/",
                        params=params, timeout=15)
    record("pixabay")
    if resp.status_code != 200:
        return None

    hits = resp.json().get("hits", [])
    if not hits:
        return None

    hit = random.choice(hits)
    videos = hit.get("videos", {})
    url = (videos.get("large") or videos.get("medium") or {}).get("url")
    if not url:
        return None
    return _download(url, output_path)


def fetch_stock_video(keywords: list[str], output_path: str) -> str | None:
    """Try Pexels first (all keywords), then Pixabay as fallback."""
    for keyword in keywords:
        result = _pexels(keyword, output_path) or _pixabay(keyword, output_path)
        if result:
            return result
    return None


def fetch_multiple_clips(keywords: list, output_dir: str) -> list[str]:
    """
    keywords: list of str OR list of {"specific":..., "fallback":...}
    Tries specific first, then fallback. Returns list of downloaded paths.
    """
    paths = []
    for i, kw in enumerate(keywords):
        out = os.path.join(output_dir, f"clip_{i}.mp4")

        if isinstance(kw, dict):
            specific = kw.get("specific", "")
            fallback = kw.get("fallback", specific)
        else:
            specific, fallback = kw, kw

        result = (_pexels(specific, out) or _pixabay(specific, out)
                  or _pexels(fallback, out) or _pixabay(fallback, out))

        if result:
            paths.append(result)
            print(f"    [{i+1}/{len(keywords)}] OK: {specific[:35]}")
        else:
            print(f"    [{i+1}/{len(keywords)}] SKIP: {specific[:35]}")
    return paths
