"""YouTube Analytics API helpers.

Requires the `yt-analytics.readonly` scope on token_youtube.json --
ensure src/uploader.py SCOPES includes it and re-run OAuth if you
upgraded an old token.

Note: YouTube Analytics has a ~24-48h ingestion delay. Querying
within the first 24h after a video goes public usually returns no
rows. Callers should wait at least 48h after the public timestamp
before treating absent rows as "low performance".
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta, date

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build


TOKEN_FILE = "token_youtube.json"


def _service():
    if not os.path.exists(TOKEN_FILE):
        return None
    creds = Credentials.from_authorized_user_file(TOKEN_FILE)
    return build("youtubeAnalytics", "v2", credentials=creds,
                 cache_discovery=False)


def stats_for_video(video_id: str,
                    start: date | None = None,
                    end: date | None = None) -> dict | None:
    """Return analytics for a single video as a dict, or None if no
    rows (typical for videos <24-48h old). Keys:
      - views (int)
      - retention (float, %, 0..100 -- "averageViewPercentage")
      - watch_minutes (int)
    """
    ya = _service()
    if not ya:
        return None

    today = date.today()
    start = start or (today - timedelta(days=30))
    end   = end   or today

    try:
        r = ya.reports().query(
            ids="channel==MINE",
            startDate=start.isoformat(),
            endDate=end.isoformat(),
            metrics="views,averageViewPercentage,estimatedMinutesWatched",
            dimensions="video",
            filters=f"video=={video_id}",
            maxResults=1,
        ).execute()
    except Exception as e:
        print(f"  [Analytics] query failed for {video_id}: {e}")
        return None

    rows = r.get("rows", [])
    if not rows:
        return None
    _, views, retention, minutes = rows[0]
    return {
        "views":         int(views or 0),
        "retention":     float(retention or 0.0),
        "watch_minutes": int(minutes or 0),
    }


def channel_median_views(days: int = 30) -> int | None:
    """Median views across all videos in the last `days`. Used as a
    baseline so the swap decision is relative to the channel, not an
    absolute number that drifts as the channel grows."""
    ya = _service()
    if not ya:
        return None

    today = date.today()
    start = today - timedelta(days=days)

    try:
        r = ya.reports().query(
            ids="channel==MINE",
            startDate=start.isoformat(),
            endDate=today.isoformat(),
            metrics="views",
            dimensions="video",
            maxResults=200,
            sort="-views",
        ).execute()
    except Exception as e:
        print(f"  [Analytics] median query failed: {e}")
        return None

    views = sorted(int(row[1] or 0) for row in r.get("rows", []))
    if not views:
        return None
    mid = len(views) // 2
    return views[mid] if len(views) % 2 else (views[mid - 1] + views[mid]) // 2
