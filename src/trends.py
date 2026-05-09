import random

def get_trending_topic() -> str | None:
    """Fetch a trending topic from Thailand + Global Google Trends. Returns None on failure."""
    try:
        from pytrends.request import TrendReq
        pytrends = TrendReq(hl='en-US', tz=420, timeout=(10, 25))

        topics = []
        for region in ['thailand', 'united_states']:
            try:
                df = pytrends.trending_searches(pn=region)
                topics += df[0].tolist()[:15]
            except Exception:
                pass

        topics = [t for t in topics if isinstance(t, str) and len(t) > 2]
        if topics:
            pick = random.choice(topics[:25])
            return pick
    except Exception:
        pass
    return None
