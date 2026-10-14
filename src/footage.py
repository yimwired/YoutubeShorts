import requests
import os
import re
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


# How far down Pexels' relevance ranking a clip may come from. Wide enough that
# a clip whose title matches more of the query can beat a vaguer one ranked
# above it; narrow enough that Pexels' own order still decides among equals.
_RELEVANCE_WINDOW = 8

# Words that say nothing about whether a clip fits: nearly every stock clip has
# a person in it, and "close up shot" describes framing, not the subject.
_UNINFORMATIVE = {
    "the", "and", "with", "for", "from", "into", "onto", "over", "under",
    "their", "this", "that", "while", "very", "close", "shot", "view",
    "video", "footage", "scene", "person", "people", "man", "men", "woman",
    "women",
}

# Words that set the era or place rather than name the subject. They break ties
# between clips that already show the subject and never qualify a clip alone:
# "surprised thai person greeting" otherwise promoted a Bangkok parade from
# sixth place over five clips of people reacting, on the word "thai".
_SETTING_WORDS = {
    "thai", "thailand", "old", "vintage", "retro", "modern", "ancient",
    "historic", "historical", "traditional", "classic", "world", "global",
    "globally", "different", "various",
}

_SUFFIXES = ("ing", "ed", "s")

# One search per query and size per run: the fallback tiers in
# fetch_multiple_clips ask again for queries already searched.
_search_cache: dict[tuple[str, str | None], list[dict]] = {}


def _search_pexels(keyword: str, size: str | None) -> list[dict]:
    if (keyword, size) in _search_cache:
        return _search_cache[(keyword, size)]
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
    videos = resp.json().get("videos", [])
    _search_cache[(keyword, size)] = videos
    return videos


def _stem(word: str) -> str:
    """Rough English stem, so "boring" meets "bored" and "notes" meets "note".

    One ending comes off ("glass" keeps its "ss"), then a trailing "e", which
    is what lets "making", "make" and "makes" land on the same stem.
    """
    for suffix in _SUFFIXES:
        if word.endswith(suffix) and not word.endswith("ss") and len(word) - len(suffix) >= 3:
            word = word[: -len(suffix)]
            break
    if word.endswith("e") and len(word) >= 4:
        word = word[:-1]
    return word


def _query_words(keyword: str) -> set[str]:
    return {word for word in re.findall(r"[a-z0-9]+", keyword.lower())
            if len(word) >= 3 and word not in _UNINFORMATIVE}


def _title_words(video: dict) -> list[str]:
    """The clip's title as Pexels spells it in the page URL,
    e.g. https://www.pexels.com/video/an-animal-skull-7189100/."""
    slug = (video.get("url") or "").rstrip("/").rsplit("/", 1)[-1]
    return [word for word in slug.lower().split("-") if word and not word.isdigit()]


def _match_score(keyword: str, video: dict) -> tuple[int, int]:
    """(subject words matched, setting words matched) between query and clip title.

    Words are compared after _stem. Stems of four letters or more also match
    inside each other, so "prison" meets "prisoner" and "nails" meets
    "fingernails"; shorter ones must match whole, or "old" would match
    "golden". Setting words only count once a subject word has matched.
    """
    title = [_stem(t) for t in _title_words(video)]

    def found(word: str) -> bool:
        word = _stem(word)
        return any(word == t or (len(word) >= 4 and len(t) >= 4 and (word in t or t in word))
                   for t in title)

    words = _query_words(keyword)
    subject = sum(1 for word in words - _SETTING_WORDS if found(word))
    setting = sum(1 for word in words & _SETTING_WORDS if found(word)) if subject else 0
    return subject, setting


def _pexels(keyword: str, output_path: str, used_ids: set | None = None,
            require_match: bool = False) -> str | None:
    """Download the portrait clip that best fits `keyword`.

    Pexels orders results by relevance, and that order is the base: an
    earlier version re-sorted by resolution and kept drifting off theme. On
    top of it, a clip whose title names more of the query's subject moves up,
    and with `require_match` a clip that names none of it is not taken at
    all, so the caller can try its fallback query before settling.

    A recency rule used to sit here as well -- the newest id among the top
    four, to keep soft 2015-era uploads out -- and it chose the wrong clip
    whenever a newer upload ranked lower. "animal bone sculpture" ranked an
    animal skull first and took "playful dog chewing bone", fourth and
    newest; "old thai prime minister portrait" took a vintage book display,
    which aired in EP.49 as a Turkish novelist's book cover. size=medium (a
    Full HD floor) already keeps the soft uploads out, so the rule went.

    Falls back to an unfiltered search when the HD floor leaves nothing
    usable (no clip at all, or with `require_match` none that matches) --
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

        # sorted() is stable, reverse included, so clips that match equally
        # keep Pexels' order.
        ranked = sorted(candidates, key=lambda v: _match_score(keyword, v), reverse=True)
        if require_match:
            ranked = [v for v in ranked if _match_score(keyword, v)[0] > 0]
        for video in ranked:
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

        result = (_pexels(specific, out, used_ids, require_match=True)
                  or _pexels(fallback, out, used_ids, require_match=True))
        matched = result is not None
        # A clip whose title shares nothing with either query is the last
        # resort, taken only so the sentence still has footage: a sentence left
        # without one shifts every later clip onto the wrong sentence.
        if not result:
            result = (_pexels(specific, out, used_ids)
                      or _pixabay(specific, out, used_ids)
                      or _pexels(fallback, out, used_ids)
                      or _pixabay(fallback, out, used_ids))

        if result:
            paths.append(result)
            note = "" if matched else "  (no title match)"
            print(f"    [{i+1}/{len(keywords)}] OK: {specific[:35]}{note}")
        else:
            print(f"    [{i+1}/{len(keywords)}] SKIP: {specific[:35]}")
    return paths
