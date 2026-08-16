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


# How far down the relevance ranking we are willing to look before
# preferring a fresher clip. Wide enough that the newest of the genuinely
# relevant results wins; narrow enough that relevance still decides.
_RELEVANCE_WINDOW = 4


def _search_pexels(keyword: str, size: str | None) -> list[dict]:
    headers = {"Authorization": PEXELS_KEY}
    params  = {"query": keyword, "per_page": 20, "orientation": "portrait"}
    if size:
        params["size"] = size
    try:
        resp = requests.get("https://api.pexels.com/videos/search",
                            headers=headers, params=params, timeout=15)
    except Exception:
        return []
    record("pexels")
    if resp.status_code != 200:
        return []
    return resp.json().get("videos", [])


def _pexels(keyword: str, output_path: str,
            used_ids: set | None = None) -> str | None:
    """Download a relevant, reasonably recent portrait clip for `keyword`.

    Two signals, in this order:

    Relevance. Pexels returns results ordered by match to the query, and
    that ordering is the only relevance information available. An earlier
    version re-sorted by resolution, so a 4K clip of something unrelated
    beat the 720p clip that was on topic every time -- the reason videos
    kept drifting off theme. Only the top few results are considered.

    Recency. Pexels has no sort-by-date, but ids increase with upload
    time, so the highest id inside the relevance window is the newest of
    the clips that actually match. Combined with size=medium (Full HD
    floor) this keeps the soft, grainy, 2015-era uploads out.

    Falls back to an unfiltered search when the HD floor leaves nothing --
    niche queries sometimes have only a handful of portrait clips at all.

    `used_ids` is mutated with whatever gets taken, so one clip cannot
    appear twice in a single video.
    """
    for size in ("medium", None):
        candidates = []
        for video in _search_pexels(keyword, size):     # relevance order
            vid_id = video.get("id")
            if used_ids is not None and vid_id in used_ids:
                continue
            if video.get("duration", 0) < 5:            # too short to cut into
                continue
            if not _best_portrait_file(video):
                continue
            candidates.append(video)
            if len(candidates) >= _RELEVANCE_WINDOW:
                break

        # Newest among the most relevant, then next-newest if it won't download.
        for video in sorted(candidates, key=lambda v: v.get("id", 0),
                            reverse=True):
            try:
                path = _download(_best_portrait_file(video), output_path)
            except Exception:
                continue
            if used_ids is not None:
                used_ids.add(video.get("id"))
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
