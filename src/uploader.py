import os
import json
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials

SCOPES           = ["https://www.googleapis.com/auth/youtube.upload",
                    "https://www.googleapis.com/auth/youtube"]
TOKEN_FILE       = "token_youtube.json"
SECRETS_FILE     = "client_secrets.json"


def _get_service():
    creds = None
    if os.path.exists(TOKEN_FILE):
        creds = Credentials.from_authorized_user_file(TOKEN_FILE, SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            if not os.path.exists(SECRETS_FILE):
                print(f"  [YouTube] {SECRETS_FILE} not found — skipping upload")
                return None
            flow = InstalledAppFlow.from_client_secrets_file(SECRETS_FILE, SCOPES)
            creds = flow.run_local_server(port=0)
        with open(TOKEN_FILE, "w") as f:
            f.write(creds.to_json())
    return build("youtube", "v3", credentials=creds)


def upload_youtube(video_path: str, title: str, description: str = "",
                   tags: list[str] = None, thumbnail_path: str = None,
                   lang: str = "en", publish_at: str = None) -> str | None:
    youtube = _get_service()
    if not youtube:
        print(f"  [YouTube] Upload skipped — not authenticated")
        return None

    # Append #Shorts to description so YouTube classifies as Short
    full_desc = description.rstrip()
    if "#shorts" not in full_desc.lower():
        full_desc += "\n\n#Shorts"

    status = {
        "selfDeclaredMadeForKids": False,
        "madeForKids": False,
    }
    if publish_at:
        status["privacyStatus"] = "private"
        status["publishAt"] = publish_at
    else:
        status["privacyStatus"] = "public"

    body = {
        "snippet": {
            "title": title[:100],
            "description": full_desc,
            "tags": list({*(tags or []), "shorts", "facts", "didyouknow"}),
            "categoryId": "27",  # Education
            "defaultLanguage": lang,
        },
        "status": status,
    }

    media = MediaFileUpload(video_path, chunksize=-1, resumable=True,
                            mimetype="video/mp4")
    print(f"  [YouTube] Uploading: {title[:50]}...")
    req  = youtube.videos().insert(part="snippet,status", body=body,
                                   media_body=media)
    resp = req.execute()
    vid_id = resp["id"]
    url    = f"https://youtu.be/{vid_id}"
    print(f"  [YouTube] Done: {url}")

    if thumbnail_path and os.path.exists(thumbnail_path):
        try:
            youtube.thumbnails().set(
                videoId=vid_id,
                media_body=MediaFileUpload(thumbnail_path, mimetype="image/jpeg")
            ).execute()
            print(f"  [YouTube] Thumbnail set")
        except Exception as e:
            print(f"  [YouTube] Thumbnail failed: {e}")

    return url, vid_id


def check_video_public(video_id: str) -> bool:
    """Return True if the YouTube video is now public."""
    youtube = _get_service()
    if not youtube:
        return False
    try:
        resp = youtube.videos().list(part="status", id=video_id).execute()
        items = resp.get("items", [])
        if not items:
            return False
        return items[0]["status"]["privacyStatus"] == "public"
    except Exception as e:
        print(f"  [YouTube] check_video_public failed: {e}")
        return False


def upload_tiktok(video_path: str, title: str,
                  publish_at: str = None) -> str | None:
    """Upload video to TikTok via tiktok-uploader (cookie-based).
    Requires TIKTOK_SESSIONID in env. Returns profile URL on success.
    If publish_at is provided (ISO string), TikTok native scheduling is used.
    TikTok constraint: schedule must be between 15min and 10 days from now."""
    sessionid = os.getenv("TIKTOK_SESSIONID")
    if not sessionid:
        print(f"  [TikTok] No TIKTOK_SESSIONID set — skipping: {video_path}")
        return None

    try:
        from tiktok_uploader.upload import upload_video
    except ImportError:
        print(f"  [TikTok] tiktok-uploader not installed — pip install tiktok-uploader")
        return None

    schedule = None
    if publish_at:
        from datetime import datetime, timezone, timedelta
        try:
            t = datetime.fromisoformat(publish_at)
            if t.tzinfo is None:
                t = t.replace(tzinfo=timezone.utc)
            t_utc = t.astimezone(timezone.utc)
            now_utc = datetime.now(timezone.utc)
            # Only schedule if within TikTok's 15min..10day window
            if timedelta(minutes=15) <= (t_utc - now_utc) <= timedelta(days=10):
                schedule = t_utc
        except Exception:
            pass

    # Build cookies_list manually — lib's sessionid path drops the domain
    cookies_list = [{
        "name":   "sessionid",
        "value":  sessionid,
        "domain": ".tiktok.com",
        "path":   "/",
    }]

    print(f"  [TikTok] Uploading: {title[:50]}...")
    try:
        failed = upload_video(
            filename=video_path,
            description=title,
            cookies_list=cookies_list,
            schedule=schedule,
            headless=False,
        )
        if failed:
            print(f"  [TikTok] Failed: {failed}")
            return None
        print(f"  [TikTok] Done (scheduled={schedule is not None})")
        return "https://www.tiktok.com/@me"
    except Exception as e:
        print(f"  [TikTok] Error: {e}")
        return None
