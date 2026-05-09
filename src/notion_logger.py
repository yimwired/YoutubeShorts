import os
import requests
from datetime import datetime, timezone

TOKEN = lambda: os.getenv("NOTION_TOKEN")
DB    = lambda: os.getenv("NOTION_VIDEO_LOG_DB_ID")

_HEADERS = lambda: {
    "Authorization": f"Bearer {TOKEN()}",
    "Content-Type":  "application/json",
    "Notion-Version": "2022-06-28",
}


def log_video(title: str, youtube_url: str = None, tiktok_url: str = None,
              lang: str = "en", topic: str = None, upload_time: str = None) -> None:
    """Log an already-uploaded video."""
    db_id = DB()
    if not db_id or db_id == "your_database_id":
        return
    try:
        props = {
            "Title":       {"title": [{"text": {"content": title[:100]}}]},
            "Language":    {"select": {"name": "TH" if lang == "th" else "EN"}},
            "Upload Time": {"date": {"start": upload_time or datetime.now(timezone.utc).isoformat()}},
            "Status":      {"select": {"name": "Uploaded"}},
        }
        if youtube_url:
            props["YouTube"] = {"url": youtube_url}
        if tiktok_url:
            props["TikTok"]  = {"url": tiktok_url}
        if topic:
            props["Topic"]   = {"rich_text": [{"text": {"content": topic[:100]}}]}

        r = requests.post(
            "https://api.notion.com/v1/pages",
            headers=_HEADERS(),
            json={"parent": {"database_id": db_id}, "properties": props},
        )
        r.raise_for_status()
        print(f"  [Notion] Logged: {title[:50]}")
    except Exception as e:
        print(f"  [Notion] Failed: {e}")


def log_scheduled(title: str, publish_at: str, lang: str = "en",
                  topic: str = None) -> str | None:
    """
    Create a Notion entry with Status='Scheduled'.
    Returns the Notion page_id to update later after upload.
    """
    db_id = DB()
    if not db_id or db_id == "your_database_id":
        return None
    try:
        props = {
            "Title":       {"title": [{"text": {"content": title[:100]}}]},
            "Language":    {"select": {"name": "TH" if lang == "th" else "EN"}},
            "Upload Time": {"date": {"start": publish_at}},
            "Status":      {"select": {"name": "Scheduled"}},
        }
        if topic:
            props["Topic"] = {"rich_text": [{"text": {"content": topic[:100]}}]}

        r = requests.post(
            "https://api.notion.com/v1/pages",
            headers=_HEADERS(),
            json={"parent": {"database_id": db_id}, "properties": props},
        )
        r.raise_for_status()
        page_id = r.json()["id"].replace("-", "")
        print(f"  [Notion] Scheduled: {title[:50]} → {publish_at[:16]}")
        return page_id
    except Exception as e:
        print(f"  [Notion] Failed: {e}")
        return None


def mark_uploaded(page_id: str, youtube_url=None,
                  tiktok_url: str = None) -> None:
    """Update an existing Notion entry to Status='Uploaded' after publishing."""
    if not page_id or not TOKEN():
        return
    # Unpack tuple if caller passed (url, vid_id) directly
    if isinstance(youtube_url, tuple):
        youtube_url = youtube_url[0]
    if isinstance(tiktok_url, tuple):
        tiktok_url = tiktok_url[0]
    try:
        props = {"Status": {"select": {"name": "Uploaded"}}}
        if youtube_url:
            props["YouTube"] = {"url": youtube_url}
        if tiktok_url:
            props["TikTok"]  = {"url": tiktok_url}

        r = requests.patch(
            f"https://api.notion.com/v1/pages/{page_id}",
            headers=_HEADERS(),
            json={"properties": props},
        )
        r.raise_for_status()
        print(f"  [Notion] Marked uploaded: {page_id[:8]}...")
    except Exception as e:
        print(f"  [Notion] Update failed: {e}")
