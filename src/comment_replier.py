"""YouTube comment auto-reply helper.

Lists top comments on recently uploaded videos, generates a context-aware
reply via Gemini (with Groq fallback), and posts it. Dry-run by default --
the caller must opt in to live posting.

Scope note: posting comments via the YouTube Data API requires the
`youtube.force-ssl` OAuth scope, which is broader than the existing
`youtube` scope used by uploader.py. If posting returns a 403, the user
needs to re-authenticate with the expanded scope (see SCOPES below).
"""

import json
import os
import re
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials

# Must match uploader.py SCOPES exactly -- both share token_youtube.json, and
# a scope mismatch forces a re-auth prompt on whichever module runs second.
# force-ssl is required by comments.insert and allThreadsRelatedToChannelId.
SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube",
    "https://www.googleapis.com/auth/youtube.force-ssl",
    "https://www.googleapis.com/auth/yt-analytics.readonly",
]
TOKEN_FILE   = "token_youtube.json"
SECRETS_FILE = "client_secrets.json"


def _get_service():
    creds = None
    if os.path.exists(TOKEN_FILE):
        creds = Credentials.from_authorized_user_file(TOKEN_FILE, SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
            except Exception as e:
                print(f"  [comments] token refresh failed: {e}")
                creds = None
        if not creds:
            if not os.path.exists(SECRETS_FILE):
                print(f"  [comments] {SECRETS_FILE} not found -- cannot auth")
                return None
            flow  = InstalledAppFlow.from_client_secrets_file(SECRETS_FILE, SCOPES)
            creds = flow.run_local_server(port=0)
        with open(TOKEN_FILE, "w") as f:
            f.write(creds.to_json())
    return build("youtube", "v3", credentials=creds)


_OUR_CHANNEL_ID: str | None = None


def _our_channel_id(youtube) -> str | None:
    global _OUR_CHANNEL_ID
    if _OUR_CHANNEL_ID:
        return _OUR_CHANNEL_ID
    try:
        resp = youtube.channels().list(part="id", mine=True).execute()
        items = resp.get("items") or []
        if items:
            _OUR_CHANNEL_ID = items[0]["id"]
    except Exception as e:
        print(f"  [comments] channels.mine failed: {e}")
    return _OUR_CHANNEL_ID


def list_top_comments(youtube, video_id: str,
                      max_results: int = 10) -> list[dict]:
    """Return top-level comments sorted by likeCount desc.

    Each entry: {comment_id, author, author_channel_id, text, like_count,
                 we_replied (bool), total_reply_count}
    """
    try:
        resp = youtube.commentThreads().list(
            part="snippet,replies",
            videoId=video_id,
            maxResults=max_results,
            order="relevance",
            textFormat="plainText",
        ).execute()
    except HttpError as e:
        # commentsDisabled (403) is common -- treat as empty
        if e.resp.status in (403, 404):
            return []
        raise

    our_cid = _our_channel_id(youtube)
    out = []
    for item in resp.get("items", []):
        top = item["snippet"]["topLevelComment"]
        snip = top["snippet"]

        # Check whether any reply is from our channel already
        we_replied = False
        for r in (item.get("replies") or {}).get("comments", []) or []:
            rcid = (r["snippet"].get("authorChannelId") or {}).get("value")
            if our_cid and rcid == our_cid:
                we_replied = True
                break

        out.append({
            "comment_id":        top["id"],
            "author":            snip.get("authorDisplayName", ""),
            "author_channel_id": (snip.get("authorChannelId") or {}).get("value"),
            "text":              snip.get("textDisplay", ""),
            "like_count":        int(snip.get("likeCount", 0)),
            "we_replied":        we_replied,
            "total_reply_count": int(item["snippet"].get("totalReplyCount", 0)),
            "published_at":      snip.get("publishedAt", ""),
        })

    out.sort(key=lambda c: c["like_count"], reverse=True)
    return out


def _detect_lang(text: str) -> str:
    """Pick reply language by detecting Thai chars in the comment."""
    return "th" if re.search(r"[฀-๿]", text or "") else "en"


def generate_reply(comment_text: str, video_title: str,
                   video_description: str = "", lang: str | None = None) -> str:
    """Compose a short context-aware reply. Imports generator lazily so the
    reply path doesn't pull Gemini SDK if the script is only used for dry-run
    listing."""
    from src.generator import _llm_call

    reply_lang = lang or _detect_lang(comment_text)
    if reply_lang == "th":
        system = (
            "You are the friendly host of a Thai YouTube Shorts channel that posts "
            "surprising-but-true facts. Reply to a viewer's comment in natural "
            "spoken Thai -- warm, brief, with a tiny extra nugget of insight when "
            "you can. Avoid corporate phrasing. Never invent facts. No emojis "
            "unless the comment used one. 1-2 sentences."
        )
    else:
        system = (
            "You are the friendly host of a YouTube Shorts channel that posts "
            "surprising-but-true facts. Reply to a viewer's comment in casual "
            "English -- warm, brief, with a tiny extra nugget of insight when "
            "you can. No corporate phrasing. Never invent facts. No emojis "
            "unless the comment used one. 1-2 sentences."
        )

    user = (
        f"Video title: {video_title}\n"
        f"Video description (truncated): {(video_description or '')[:400]}\n\n"
        f"Viewer comment:\n{comment_text}\n\n"
        "Write the reply text only -- no quotes, no prefixes, no signoff."
    )

    raw = _llm_call(
        [{"role": "system", "content": system},
         {"role": "user",   "content": user}],
        max_tokens=200,
        json_mode=False,
    )
    return _clean_reply(raw)


def _clean_reply(raw: str) -> str:
    """Normalize LLM output to plain reply text. Defends against the model
    wrapping the reply in a JSON object or markdown code fences."""
    text = raw.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-z]*\s*|\s*```$", "", text).strip()
    if text.startswith("{"):
        try:
            obj = json.loads(text)
            if isinstance(obj, dict):
                text = next((v for v in obj.values()
                             if isinstance(v, str) and v.strip()), text)
        except json.JSONDecodeError:
            pass
    return text.strip().strip('"').strip("'")


def post_reply(youtube, parent_comment_id: str, text: str) -> str | None:
    """Insert a reply to a top-level comment. Returns the new comment id."""
    body = {
        "snippet": {
            "parentId":     parent_comment_id,
            "textOriginal": text,
        }
    }
    try:
        resp = youtube.comments().insert(part="snippet", body=body).execute()
        return resp.get("id")
    except HttpError as e:
        print(f"  [comments] insert failed ({e.resp.status}): {e}")
        return None


def get_service():
    """Public accessor so the CLI can reuse the cached service."""
    return _get_service()
