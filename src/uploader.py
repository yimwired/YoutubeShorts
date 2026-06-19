import os
import sys
import json
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials

SCOPES           = ["https://www.googleapis.com/auth/youtube.upload",
                    "https://www.googleapis.com/auth/youtube",
                    "https://www.googleapis.com/auth/youtube.force-ssl",
                    "https://www.googleapis.com/auth/yt-analytics.readonly"]
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


def _seed_top_comment(youtube, video_id: str, text: str) -> None:
    """Post a top-level comment as the channel to prime the conversation
    (comment-bait question). Best-effort: scheduled/private videos reject
    comments until they go public, so failures are logged and swallowed.

    NOTE: the YouTube Data API cannot PIN a comment — pinning stays manual.
    Channel-owner comments still surface prominently without it.
    """
    if not text or not text.strip():
        return
    try:
        youtube.commentThreads().insert(
            part="snippet",
            body={"snippet": {
                "videoId": video_id,
                "topLevelComment": {"snippet": {"textOriginal": text.strip()}},
            }},
        ).execute()
        print(f"  [YouTube] Seed comment posted")
    except Exception as e:
        print(f"  [YouTube] Seed comment skipped: {e}")


def upload_youtube(video_path: str, title: str, description: str = "",
                   tags: list[str] = None, thumbnail_path: str = None,
                   lang: str = "en", publish_at: str = None,
                   seed_comment: str = None) -> str | None:
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

    if seed_comment:
        _seed_top_comment(youtube, vid_id, seed_comment)

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
    """Upload video to TikTok via cookie-based path.

    Runs the actual Playwright upload in a subprocess (`src._tiktok_cookie`)
    so the sync Playwright event loop doesn't leak into the caller and
    break subsequent edge-tts / asyncio.run() calls.

    Requires TIKTOK_SESSIONID env. Returns profile URL on success, None on
    failure. publish_at ISO string triggers TikTok native scheduling when
    inside the 15min..10day window.
    """
    if not os.getenv("TIKTOK_SESSIONID"):
        print(f"  [TikTok] No TIKTOK_SESSIONID set — skipping: {video_path}")
        return None

    import subprocess
    cmd = [sys.executable, "-m", "src._tiktok_cookie", video_path, title]
    if publish_at:
        cmd.append(publish_at)

    print(f"  [TikTok] Uploading (subprocess): {title[:50]}...")
    try:
        r = subprocess.run(
            cmd, capture_output=True, text=True,
            encoding="utf-8", errors="replace",
            timeout=15 * 60,
        )
    except subprocess.TimeoutExpired:
        print("  [TikTok] subprocess timeout (>15min) — skipping")
        return None
    except Exception as e:
        print(f"  [TikTok] subprocess error: {e}")
        return None

    # Surface child stderr (progress / errors) so the batch log shows it.
    if r.stderr:
        for line in r.stderr.rstrip().splitlines()[-12:]:
            print(f"  [TikTok] {line}")

    if r.returncode == 0:
        url = (r.stdout or "").strip().splitlines()[-1] if r.stdout else ""
        print(f"  [TikTok] Done")
        return url or "https://www.tiktok.com/@me"
    print(f"  [TikTok] Failed (exit {r.returncode})")
    return None
