"""Standalone TikTok cookie-based uploader.

Runs as a *subprocess* so the Playwright sync API does not leak a
running event loop into the parent process (which would break
subsequent edge-tts / asyncio calls with
``RuntimeError: asyncio.run() cannot be called from a running event loop``).

CLI:
    python -m src._tiktok_cookie <video> <title> [<publish_at_iso>]

Exit codes:
    0  upload submitted (printed URL on stdout)
    1  upload failed / lib raised / scheduling rejected
    2  bad args / missing env

Required env: TIKTOK_SESSIONID
"""

from __future__ import annotations

import os
import sys
from datetime import datetime, timezone, timedelta


def _patch_lib_dismiss_modals() -> None:
    """Monkey-patch tiktok-uploader to auto-dismiss the "New editing
    features added" tour modal (and similar overlays) that TikTok rolled
    out 2026-05. The lib doesn't know about it; the modal blocks the
    interactivity toggle and schedule switch with a 30s click timeout."""
    try:
        from tiktok_uploader import upload as _tu
        _orig = _tu.complete_upload_form

        def patched(page, *args, **kwargs):
            # add_locator_handler fires whenever Playwright is waiting and
            # the locator appears. Register multiple known dismiss buttons
            # so we cover wording variants.
            for sel in (
                'button:has-text("Got it!")',
                'button:has-text("Got it")',
                'button:has-text("I understand")',
                'div[role="dialog"] button:has-text("OK")',
            ):
                try:
                    loc = page.locator(sel)
                    page.add_locator_handler(loc, lambda l=loc: l.first.click())
                except Exception as e:
                    print(f"[TikTok] handler {sel} failed: {e}",
                          file=sys.stderr)
            return _orig(page, *args, **kwargs)

        _tu.complete_upload_form = patched
        print("[TikTok] patched complete_upload_form with modal handler",
              file=sys.stderr)
    except Exception as e:
        print(f"[TikTok] could not patch lib: {e}", file=sys.stderr)


def _run(video: str, title: str, publish_at: str | None) -> int:
    sessionid = os.getenv("TIKTOK_SESSIONID")
    if not sessionid:
        print("[TikTok] No TIKTOK_SESSIONID in env", file=sys.stderr)
        return 2

    try:
        from tiktok_uploader.upload import upload_video
    except ImportError:
        print("[TikTok] tiktok-uploader not installed", file=sys.stderr)
        return 2

    _patch_lib_dismiss_modals()

    schedule = None
    if publish_at:
        try:
            t = datetime.fromisoformat(publish_at)
            if t.tzinfo is None:
                t = t.replace(tzinfo=timezone.utc)
            t_utc = t.astimezone(timezone.utc)
            now_utc = datetime.now(timezone.utc)
            if timedelta(minutes=15) <= (t_utc - now_utc) <= timedelta(days=10):
                # tiktok-uploader's UTC-aware branch is broken (it calls
                # pytz.UTC.localize on an aware datetime). Strip to naive
                # local — lib treats naive as system-local then converts.
                schedule = t_utc.astimezone().replace(tzinfo=None)
        except Exception as e:
            print(f"[TikTok] Bad publish_at, posting immediately: {e}",
                  file=sys.stderr)

    cookies_list = [{
        "name":   "sessionid",
        "value":  sessionid,
        "domain": ".tiktok.com",
        "path":   "/",
    }]

    print(f"[TikTok] Uploading: {title[:60]}  schedule={schedule}",
          file=sys.stderr)
    try:
        failed = upload_video(
            filename=video,
            description=title,
            cookies_list=cookies_list,
            schedule=schedule,
            headless=False,
        )
    except Exception as e:
        print(f"[TikTok] Exception: {e}", file=sys.stderr)
        return 1

    if failed:
        print(f"[TikTok] Failed: {failed}", file=sys.stderr)
        return 1

    # lib doesn't return real URL — return profile as a success marker.
    print("https://www.tiktok.com/@me")
    return 0


def main() -> None:
    args = sys.argv[1:]
    if len(args) < 2:
        print("usage: python -m src._tiktok_cookie <video> <title> "
              "[<publish_at_iso>]", file=sys.stderr)
        sys.exit(2)
    video      = args[0]
    title      = args[1]
    publish_at = args[2] if len(args) >= 3 and args[2] else None
    sys.exit(_run(video, title, publish_at))


if __name__ == "__main__":
    main()
