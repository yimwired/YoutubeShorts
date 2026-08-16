"""Trend candidate sourcing for the daily explainer video.

Two independent feeds, merged and de-duplicated:

  1. Google Trends daily RSS (TH + US) -- what people are *searching*.
     Each item carries news-item titles, which give the research step
     enough context to tell "Kasetsart University" the football club
     from the university.
  2. YouTube most-popular chart (TH) -- what people are *watching*.
     Catches viral formats and memes that never become a search query.

The list is deliberately raw: filtering ("is this explainable? is it
politics?") happens in src/research.py, where a grounded model can
actually check what a candidate is about.
"""

from __future__ import annotations

import os
import re
import xml.etree.ElementTree as ET

import requests

_TRENDS_FEEDS = [
    "https://trends.google.com/trending/rss?geo=TH",
    "https://trends.google.com/trending/rss?geo=US",
]
_NS = {"ht": "https://trends.google.com/trending/rss"}

# Titles hitting these are almost never a good 60s explainer: they are
# breaking news, tragedy, or a scoreline that is stale within a day.
_NOISE = re.compile(
    r"vs\.?\s|highlights|live score|ผลบอล|ถ่ายทอดสด|หวย|สลากกินแบ่ง|"
    r"earthquake|shooting|dies|death|obituary|เสียชีวิต|ศพ|ฆ่า|"
    r"election|ผลเลือกตั้ง|ประยุทธ|นายกรัฐมนตรี",
    re.IGNORECASE,
)


def _google_trends() -> list[dict]:
    """Daily trending searches with their news context."""
    out = []
    for url in _TRENDS_FEEDS:
        try:
            r = requests.get(url, timeout=15,
                             headers={"User-Agent": "Mozilla/5.0"})
            r.raise_for_status()
            root = ET.fromstring(r.content)
        except Exception as e:
            print(f"  [trends] {url.split('=')[-1]} feed failed: {e}")
            continue

        for item in root.findall(".//item"):
            title = (item.findtext("title") or "").strip()
            if not title:
                continue
            news = [n.text.strip() for n in
                    item.findall("ht:news_item/ht:news_item_title", _NS)
                    if n.text]
            out.append({
                "title":   title,
                "context": " | ".join(news[:2]),
                "source":  "google-trends",
            })
    return out


def _youtube_popular(region: str = "TH", limit: int = 25) -> list[dict]:
    """Titles from the region's most-popular chart.

    Uses the API key path (no OAuth) so this still works on a runner that
    only has the upload token mounted. Returns [] when no key is set.
    """
    key = os.getenv("YOUTUBE_API_KEY") or os.getenv("GOOGLE_API_KEY")
    if not key:
        return []
    try:
        r = requests.get(
            "https://www.googleapis.com/youtube/v3/videos",
            params={"part": "snippet", "chart": "mostPopular",
                    "regionCode": region, "maxResults": limit, "key": key},
            timeout=15,
        )
        r.raise_for_status()
        items = r.json().get("items", [])
    except Exception as e:
        print(f"  [trends] YouTube chart failed: {e}")
        return []

    return [{"title":   it["snippet"]["title"][:120],
             "context": it["snippet"]["channelTitle"],
             "source":  "youtube-chart"}
            for it in items]


def get_trend_candidates(limit: int = 30) -> list[dict]:
    """Merged, de-duplicated, noise-filtered trend candidates.

    Each item: {"title", "context", "source"}. Empty list is a normal
    outcome (both feeds down / nothing survives the filter) -- callers
    fall back to the evergreen bucket.
    """
    seen, out = set(), []
    for item in _google_trends() + _youtube_popular():
        title = item["title"]
        key = title.lower()
        if key in seen or _NOISE.search(title):
            continue
        seen.add(key)
        out.append(item)
        if len(out) >= limit:
            break
    print(f"  [trends] {len(out)} candidate(s) after filtering")
    return out


def get_trending_topic() -> str | None:
    """Back-compat shim for callers that just want one topic string."""
    cands = get_trend_candidates(limit=10)
    return cands[0]["title"] if cands else None
