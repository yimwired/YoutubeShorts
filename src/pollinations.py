"""The one door to Pollinations for every generated image in the pipeline.

The anonymous tier serves one new generation per IP roughly every 40 to 45
seconds and answers anything sooner with HTTP 402 and an empty JSON body. The
B-roll step asks for two to four images back to back and the thumbnails for two
more, so every video got its first image and nothing after it: over the twelve
days to 2026-10-14, 19 of 31 B-roll requests and 23 of 24 thumbnail requests came
back 402. Each failure quietly kept its stock fallback, which is how a Turkish
novelist's book cover ended up on screen while the voice talked about Field
Marshal Phibun (EP.49), and why both A/B thumbnail candidates were usually the
same Pexels photo.

Measured from this machine on 2026-10-14, polling every six seconds: a success
came back every 46 s and never sooner, failed attempts did not push the next
success back, and a prompt + seed already generated is served instantly from
cache without counting against the limit.

Every caller goes through `fetch()`, so the spacing holds across modules, not
just within one loop.
"""

from __future__ import annotations

import time

import requests

# Gap kept between the end of one successful generation and the next request.
# Generation itself takes six to eight seconds, so this lands each request just
# past the measured 46 s cadence instead of spending a retry on it.
_MIN_INTERVAL_SECONDS = 40.0
_RETRY_WAIT_SECONDS = 8.0
# Past this, give up on the image: every caller already has a stock or photo
# fallback, and a run that blocks forever on one still is worse than that.
_MAX_WAIT_SECONDS = 120.0

_last_success: float | None = None


def fetch(url: str, timeout: float = 120) -> bytes | None:
    """GET one Pollinations image URL, paced and retried around the rate limit.

    Returns the image bytes, or None on any failure other than the rate limit
    or once the wait budget is spent. Failures are logged here, with the status,
    so callers only need to handle None.
    """
    global _last_success

    if _last_success is not None:
        remaining = _MIN_INTERVAL_SECONDS - (time.monotonic() - _last_success)
        if remaining > 0:
            time.sleep(remaining)

    deadline = time.monotonic() + _MAX_WAIT_SECONDS
    while True:
        try:
            response = requests.get(url, timeout=timeout)
        except requests.RequestException as exc:
            print(f"    [pollinations] request failed: {type(exc).__name__}")
            return None

        if response.status_code == 200 and len(response.content) > 1000:
            _last_success = time.monotonic()
            return response.content

        rate_limited = response.status_code == 402
        if not rate_limited or time.monotonic() + _RETRY_WAIT_SECONDS > deadline:
            reason = "still rate-limited after retries" if rate_limited else "unexpected response"
            print(f"    [pollinations] HTTP {response.status_code}, "
                  f"{len(response.content)} bytes: {reason}")
            return None
        time.sleep(_RETRY_WAIT_SECONDS)
