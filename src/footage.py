import requests
import os
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


def _best_portrait_file(video: dict) -> str | None:
    """Highest-resolution portrait rendition of one Pexels video."""
    best, best_h = None, -1
    for f in video.get("video_files", []):
        h, w = f.get("height", 0), f.get("width", 0)
        if h <= w:            # must be portrait
            continue
        if h > best_h:
            best_h, best = h, f["link"]
    return best


def _pexels(keyword: str, output_path: str,
            used_ids: set | None = None) -> str | None:
    """Download the most relevant unused portrait clip for `keyword`.

    Pexels already returns results ordered by relevance to the query, and
    that ordering is the only signal we have about whether a clip matches
    what the sentence is saying. An earlier version re-sorted the results
    by resolution, which meant a 4K clip of something unrelated always beat
    the 720p clip that was actually on topic -- the reason videos kept
    drifting off theme. Resolution now only picks between renditions of the
    same video, never between videos.

    `used_ids` is mutated with whatever gets taken, so the caller can keep
    one clip from appearing twice in a single video.
    """
    headers = {"Authorization": PEXELS_KEY}
    params  = {"query": keyword, "per_page": 15, "orientation": "portrait"}
    try:
        resp = requests.get("https://api.pexels.com/videos/search",
                            headers=headers, params=params, timeout=15)
    except Exception:
        return None
    record("pexels")
    if resp.status_code != 200:
        return None

    for video in resp.json().get("videos", []):        # relevance order
        vid_id = video.get("id")
        if used_ids is not None and vid_id in used_ids:
            continue
        if video.get("duration", 0) < 5:               # too short to cut into
            continue
        link = _best_portrait_file(video)
        if not link:
            continue
        try:
            path = _download(link, output_path)
        except Exception:
            continue
        if used_ids is not None:
            used_ids.add(vid_id)
        return path
    return None


def _pixabay(keyword: str, output_path: str,
             used_ids: set | None = None) -> str | None:
    """Pixabay fallback. Same relevance-order-first rule as Pexels."""
    if not PIXABAY_KEY or PIXABAY_KEY == "your_key_here":
        return None
    params = {
        "key": PIXABAY_KEY, "q": keyword, "video_type": "film",
        "per_page": 10, "safesearch": "true",
    }
    try:
        resp = requests.get("https://pixabay.com/api/videos/",
                            params=params, timeout=15)
    except Exception:
        return None
    record("pixabay")
    if resp.status_code != 200:
        return None

    for hit in resp.json().get("hits", []):
        key = f"pb{hit.get('id')}"
        if used_ids is not None and key in used_ids:
            continue
        videos = hit.get("videos", {})
        url = (videos.get("large") or videos.get("medium") or {}).get("url")
        if not url:
            continue
        try:
            path = _download(url, output_path)
        except Exception:
            continue
        if used_ids is not None:
            used_ids.add(key)
        return path
    return None


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

    Clips are de-duplicated across the whole call: a stock library will
    happily return the same aerial-city shot for four different queries,
    and seeing it four times in one minute is what makes a video read as
    automated.
    """
    paths = []
    used_ids: set = set()
    for i, kw in enumerate(keywords):
        out = os.path.join(output_dir, f"clip_{i}.mp4")

        if isinstance(kw, dict):
            specific = kw.get("specific", "")
            fallback = kw.get("fallback", specific)
        else:
            specific, fallback = kw, kw

        result = (_pexels(specific, out, used_ids)
                  or _pixabay(specific, out, used_ids)
                  or _pexels(fallback, out, used_ids)
                  or _pixabay(fallback, out, used_ids))

        if result:
            paths.append(result)
            print(f"    [{i+1}/{len(keywords)}] OK: {specific[:35]}")
        else:
            print(f"    [{i+1}/{len(keywords)}] SKIP: {specific[:35]}")
    return paths
