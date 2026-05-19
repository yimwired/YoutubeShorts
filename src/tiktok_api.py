"""TikTok Content Posting API client.

Flow:
  1. login() — open browser → user authorizes → local server captures the code →
     exchange for access_token + refresh_token → save to token_tiktok.json.
  2. get_access_token() — load token, refresh if near expiry, return access token.
  3. upload(video_path, title) — INIT → PUT video bytes → poll status → return result.

CLI:
  python -m src.tiktok_api login
  python -m src.tiktok_api upload <video_path> "<title>"
  python -m src.tiktok_api refresh
"""
import os
import sys
import json
import time
import base64
import hashlib
import secrets
import threading
import urllib.parse
import webbrowser
import http.server
import socketserver
import requests


def _pkce_pair() -> tuple[str, str]:
    """Return (code_verifier, code_challenge) for PKCE S256."""
    verifier = secrets.token_urlsafe(64)[:96]
    digest   = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return verifier, challenge

TOKEN_FILE   = "token_tiktok.json"
REDIRECT_URI = "http://localhost:8080/callback"
SCOPES       = "user.info.basic,video.upload,video.publish"
PORT         = 8080

OAUTH_AUTHORIZE = "https://www.tiktok.com/v2/auth/authorize/"
OAUTH_TOKEN     = "https://open.tiktokapis.com/v2/oauth/token/"
POST_INIT       = "https://open.tiktokapis.com/v2/post/publish/video/init/"
POST_STATUS     = "https://open.tiktokapis.com/v2/post/publish/status/fetch/"


def _client_creds() -> tuple[str, str] | None:
    key    = os.getenv("TIKTOK_CLIENT_KEY")
    secret = os.getenv("TIKTOK_CLIENT_SECRET")
    if not key or not secret:
        return None
    return key, secret


def _load() -> dict | None:
    if os.path.exists(TOKEN_FILE):
        with open(TOKEN_FILE, encoding="utf-8") as f:
            return json.load(f)
    return None


def _save(tok: dict) -> None:
    with open(TOKEN_FILE, "w", encoding="utf-8") as f:
        json.dump(tok, f, indent=2, ensure_ascii=False)


# ── OAuth flow ──────────────────────────────────────────────────────────────

def _capture_code() -> str:
    """Run a one-shot local HTTP server on PORT, return the OAuth code."""
    holder = {}

    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            qs = urllib.parse.urlparse(self.path).query
            params = urllib.parse.parse_qs(qs)
            if "code" in params:
                holder["code"] = params["code"][0]
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.end_headers()
                self.wfile.write(b"<h2>Authorized. You can close this tab.</h2>")
            elif "error" in params:
                holder["error"] = params.get("error_description", ["unknown"])[0]
                self.send_response(400)
                self.end_headers()
                self.wfile.write(b"<h2>Authorization failed.</h2>")
            else:
                self.send_response(400)
                self.end_headers()

        def log_message(self, *a):
            pass  # silence

    srv = socketserver.TCPServer(("127.0.0.1", PORT), H)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()

    deadline = time.time() + 300  # 5 min
    while "code" not in holder and "error" not in holder and time.time() < deadline:
        time.sleep(0.4)
    srv.shutdown()

    if "error" in holder:
        raise RuntimeError(f"OAuth error: {holder['error']}")
    if "code" not in holder:
        raise RuntimeError("OAuth timed out")
    return holder["code"]


def login() -> dict:
    creds = _client_creds()
    if not creds:
        raise RuntimeError("TIKTOK_CLIENT_KEY / TIKTOK_CLIENT_SECRET not set")
    key, secret = creds

    verifier, challenge = _pkce_pair()
    auth_url = (
        OAUTH_AUTHORIZE
        + "?" + urllib.parse.urlencode({
            "client_key":            key,
            "scope":                 SCOPES,
            "response_type":         "code",
            "redirect_uri":          REDIRECT_URI,
            "state":                 "fs",
            "code_challenge":        challenge,
            "code_challenge_method": "S256",
        })
    )
    print(f"[TikTok API] Opening browser for authorization...")
    print(f"  If it doesn't open, paste this URL:\n  {auth_url}")
    try:
        webbrowser.open(auth_url)
    except Exception:
        pass

    code = _capture_code()
    print(f"[TikTok API] Got code, exchanging for token...")

    r = requests.post(
        OAUTH_TOKEN,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        data={
            "client_key":    key,
            "client_secret": secret,
            "code":          code,
            "grant_type":    "authorization_code",
            "redirect_uri":  REDIRECT_URI,
            "code_verifier": verifier,
        },
        timeout=30,
    )
    r.raise_for_status()
    tok = r.json()
    if "access_token" not in tok:
        raise RuntimeError(f"Token exchange failed: {tok}")
    tok["acquired_at"] = int(time.time())
    _save(tok)
    print(f"[TikTok API] Saved token to {TOKEN_FILE}")
    return tok


def refresh() -> dict:
    creds = _client_creds()
    if not creds:
        raise RuntimeError("Client creds missing")
    key, secret = creds
    tok = _load()
    if not tok or "refresh_token" not in tok:
        raise RuntimeError("No token to refresh — run login first")

    r = requests.post(
        OAUTH_TOKEN,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        data={
            "client_key":    key,
            "client_secret": secret,
            "refresh_token": tok["refresh_token"],
            "grant_type":    "refresh_token",
        },
        timeout=30,
    )
    r.raise_for_status()
    new_tok = r.json()
    if "access_token" not in new_tok:
        raise RuntimeError(f"Refresh failed: {new_tok}")
    new_tok["acquired_at"] = int(time.time())
    # If TikTok did not rotate the refresh_token, keep the old one
    if "refresh_token" not in new_tok:
        new_tok["refresh_token"] = tok["refresh_token"]
    _save(new_tok)
    print(f"[TikTok API] Token refreshed")
    return new_tok


def get_access_token() -> str | None:
    tok = _load()
    if not tok:
        return None
    expires_at = tok.get("acquired_at", 0) + tok.get("expires_in", 0)
    # Refresh if expiring within 1h
    if expires_at - 3600 < int(time.time()):
        try:
            tok = refresh()
        except Exception as e:
            print(f"  [TikTok API] Refresh failed: {e}")
            return None
    return tok.get("access_token")


# ── Upload ──────────────────────────────────────────────────────────────────

def upload(video_path: str, title: str,
           privacy: str = "SELF_ONLY") -> str | None:
    """Upload video via Content Posting API.

    privacy: SELF_ONLY (sandbox), MUTUAL_FOLLOW_FRIENDS, PUBLIC_TO_EVERYONE (post review).
    Returns a profile URL on success, None on failure.
    """
    access = get_access_token()
    if not access:
        print("  [TikTok API] Not logged in — run: python -m src.tiktok_api login")
        return None
    if not os.path.exists(video_path):
        print(f"  [TikTok API] Missing file: {video_path}")
        return None

    size         = os.path.getsize(video_path)
    chunk_size   = size  # single chunk up to 64MB
    total_chunks = 1
    if size > 64 * 1024 * 1024:
        # Multi-chunk path (not exercised here; keep simple)
        chunk_size   = 64 * 1024 * 1024
        total_chunks = (size + chunk_size - 1) // chunk_size

    init_body = {
        "post_info": {
            "title":                    title[:2200],
            "privacy_level":            privacy,
            "disable_duet":             False,
            "disable_comment":          False,
            "disable_stitch":           False,
            "video_cover_timestamp_ms": 1000,
        },
        "source_info": {
            "source":            "FILE_UPLOAD",
            "video_size":        size,
            "chunk_size":        chunk_size,
            "total_chunk_count": total_chunks,
        },
    }
    print(f"  [TikTok API] INIT publish ({size/1024/1024:.1f} MB)...")
    r = requests.post(
        POST_INIT,
        headers={
            "Authorization": f"Bearer {access}",
            "Content-Type":  "application/json; charset=UTF-8",
        },
        json=init_body, timeout=30,
    )
    if r.status_code != 200:
        print(f"  [TikTok API] INIT failed: {r.status_code} {r.text[:300]}")
        return None
    data = r.json().get("data", {})
    publish_id = data.get("publish_id")
    upload_url = data.get("upload_url")
    if not publish_id or not upload_url:
        print(f"  [TikTok API] INIT bad response: {r.text[:300]}")
        return None

    print(f"  [TikTok API] PUT bytes to upload_url...")
    with open(video_path, "rb") as f:
        body = f.read()
    put = requests.put(
        upload_url,
        headers={
            "Content-Type":   "video/mp4",
            "Content-Length": str(size),
            "Content-Range":  f"bytes 0-{size-1}/{size}",
        },
        data=body, timeout=300,
    )
    if put.status_code not in (200, 201):
        print(f"  [TikTok API] PUT failed: {put.status_code} {put.text[:300]}")
        return None

    print(f"  [TikTok API] Polling status (publish_id={publish_id})...")
    for _ in range(40):
        time.sleep(3)
        s = requests.post(
            POST_STATUS,
            headers={
                "Authorization": f"Bearer {access}",
                "Content-Type":  "application/json; charset=UTF-8",
            },
            json={"publish_id": publish_id}, timeout=30,
        )
        sd     = s.json().get("data", {})
        status = sd.get("status", "")
        if status == "PUBLISH_COMPLETE":
            print(f"  [TikTok API] Done")
            return "https://www.tiktok.com/@me"
        if status in ("FAILED", "PROCESSING_DOWNLOAD_FAILED"):
            print(f"  [TikTok API] Failed: {sd}")
            return None
    print(f"  [TikTok API] Timed out waiting for status")
    return None


# ── CLI ─────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    from dotenv import load_dotenv
    load_dotenv()
    sys.stdout.reconfigure(encoding="utf-8")

    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd == "login":
        login()
    elif cmd == "refresh":
        refresh()
    elif cmd == "upload":
        path  = sys.argv[2]
        title = sys.argv[3]
        privacy = sys.argv[4] if len(sys.argv) > 4 else "SELF_ONLY"
        result = upload(path, title, privacy=privacy)
        print(f"RESULT: {result}")
    else:
        print(__doc__)
