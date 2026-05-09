import random
import requests
import xml.etree.ElementTree as ET


def get_trending_topic() -> str | None:
    """Fetch a trending topic from Google Trends RSS (US + TH). Returns None on failure."""
    feeds = [
        "https://trends.google.com/trending/rss?geo=US",
        "https://trends.google.com/trending/rss?geo=TH",
    ]
    topics = []
    for url in feeds:
        try:
            r = requests.get(url, timeout=10, headers={"User-Agent": "Mozilla/5.0"})
            r.raise_for_status()
            root = ET.fromstring(r.content)
            for item in root.findall(".//item/title"):
                if item.text and len(item.text) > 2:
                    topics.append(item.text.strip())
        except Exception:
            pass

    if topics:
        return random.choice(topics[:30])
    return None
