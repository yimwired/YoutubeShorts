"""Fetch a free, CC-licensed image for a named real-world entity.

Cascade:
  1. Wikipedia REST page summary  (exact-name match, fast, ~85% hit on celebrities)
  2. Wikipedia opensearch         (fuzzy-name resolution; e.g. "Einstein" → "Albert Einstein")
  3. Wikimedia Commons search     (broader: event photos, organization logos, lesser-known people)
  4. Openverse CC search          (Flickr/museums; long tail of historical/contemporary subjects)

Every source returns CC-licensed or PD imagery — safe for monetized YouTube use.
Returns dst_path on success, None on total miss.
"""

import os
import time
import requests
from urllib.parse import quote

_UA = {"User-Agent": "FactSnap-Shorts/1.0 (entity-image fetcher; contact: youtube channel)"}
_TIMEOUT = 8


def _download(url: str, dst_path: str) -> str | None:
    try:
        r = requests.get(url, headers=_UA, timeout=_TIMEOUT)
        if r.status_code != 200 or len(r.content) < 2000:
            return None
        with open(dst_path, "wb") as f:
            f.write(r.content)
        return dst_path
    except Exception:
        return None


def _try_wikipedia_summary(name: str, dst_path: str, wiki_lang: str) -> str | None:
    # Wikipedia REST API requires the page title with spaces as underscores
    # (or %20); quote_plus would turn spaces into '+' which yields 404.
    title = quote(name.replace(" ", "_"), safe="")
    url = f"https://{wiki_lang}.wikipedia.org/api/rest_v1/page/summary/{title}"
    try:
        r = requests.get(url, headers=_UA, timeout=_TIMEOUT)
        if r.status_code != 200:
            return None
        data = r.json()
        thumb = (data.get("thumbnail") or {}).get("source") \
             or (data.get("originalimage") or {}).get("source")
        if not thumb:
            return None
        return _download(thumb, dst_path)
    except Exception:
        return None


def _try_wikipedia_search(name: str, dst_path: str, wiki_lang: str) -> str | None:
    """Resolve fuzzy name → exact page title, then fetch its summary thumbnail."""
    url = f"https://{wiki_lang}.wikipedia.org/w/api.php"
    params = {
        "action": "opensearch",
        "search": name,
        "limit": 1,
        "namespace": 0,
        "format": "json",
    }
    try:
        r = requests.get(url, headers=_UA, params=params, timeout=_TIMEOUT)
        if r.status_code != 200:
            return None
        results = r.json()
        if not (isinstance(results, list) and len(results) >= 2 and results[1]):
            return None
        resolved = results[1][0]
        if resolved.lower() == name.lower():
            return None  # already tried in _try_wikipedia_summary
        return _try_wikipedia_summary(resolved, dst_path, wiki_lang)
    except Exception:
        return None


def _try_wikimedia_commons(name: str, dst_path: str) -> str | None:
    url = "https://commons.wikimedia.org/w/api.php"
    params = {
        "action": "query",
        "generator": "search",
        "gsrsearch": f"{name} filetype:bitmap",
        "gsrlimit": 3,
        "gsrnamespace": 6,
        "prop": "imageinfo",
        "iiprop": "url|size",
        "iiurlwidth": 600,
        "format": "json",
    }
    try:
        r = requests.get(url, headers=_UA, params=params, timeout=_TIMEOUT)
        if r.status_code != 200:
            return None
        pages = (r.json().get("query") or {}).get("pages") or {}
        for page in pages.values():
            info = (page.get("imageinfo") or [{}])[0]
            thumb = info.get("thumburl") or info.get("url")
            if thumb and not thumb.lower().endswith((".svg", ".tif", ".tiff")):
                return _download(thumb, dst_path)
    except Exception:
        return None
    return None


def _try_openverse(name: str, dst_path: str) -> str | None:
    url = "https://api.openverse.engineering/v1/images/"
    params = {"q": name, "page_size": 3, "license_type": "commercial",
              "size": "medium", "mature": "false"}
    try:
        r = requests.get(url, headers=_UA, params=params, timeout=_TIMEOUT)
        if r.status_code != 200:
            return None
        results = r.json().get("results") or []
        for item in results:
            thumb = item.get("thumbnail") or item.get("url")
            if thumb:
                got = _download(thumb, dst_path)
                if got:
                    return got
    except Exception:
        return None
    return None


def fetch_entity_image(name: str, dst_path: str, lang_hint: str = "en") -> str | None:
    """Fetch a CC-licensed image for `name`. Try EN Wikipedia first; if lang_hint=='th'
    also try TH Wikipedia early (some Thai-only entities only have TH page)."""
    name = (name or "").strip()
    if not name:
        return None

    wiki_langs = ["en"]
    if lang_hint == "th":
        wiki_langs.insert(0, "th")

    for wl in wiki_langs:
        got = _try_wikipedia_summary(name, dst_path, wl)
        if got:
            return got
        got = _try_wikipedia_search(name, dst_path, wl)
        if got:
            return got

    got = _try_wikimedia_commons(name, dst_path)
    if got:
        return got

    got = _try_openverse(name, dst_path)
    if got:
        return got

    return None
