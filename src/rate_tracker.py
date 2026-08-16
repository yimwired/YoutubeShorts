import json
import os
from datetime import date

TRACK_FILE = "rate_usage.json"

# Free tier limits
LIMITS = {
    "pexels":  {"hourly": 200, "monthly": 20000},
    "pixabay": {"hourly": 100, "monthly": 5000},
    "groq":    {"daily":  14400},  # ~10 req/min free tier
}

# Estimated USD per call, for the services that are actually billed.
# Gemini TTS is $10 per 1M output tokens at 25 tokens per second of audio,
# so a ~50s voiceover is 1250 tokens ~= $0.0125. One call per video.
COST_PER_CALL = {
    "gemini_tts": 0.0125,
}


def _load() -> dict:
    if os.path.exists(TRACK_FILE):
        with open(TRACK_FILE) as f:
            return json.load(f)
    return {}


def _save(data: dict):
    with open(TRACK_FILE, "w") as f:
        json.dump(data, f, indent=2)


def record(service: str) -> None:
    """Record one API call for a service."""
    data = _load()
    today = str(date.today())
    month = today[:7]

    if service not in data:
        data[service] = {}
    svc = data[service]

    svc.setdefault("daily", {})
    svc.setdefault("monthly", {})
    svc["daily"][today]  = svc["daily"].get(today, 0) + 1
    svc["monthly"][month] = svc["monthly"].get(month, 0) + 1

    _save(data)
    _warn(service, svc, today, month)


def _warn(service: str, svc: dict, today: str, month: str):
    limits = LIMITS.get(service, {})
    daily  = svc["daily"].get(today, 0)
    monthly = svc["monthly"].get(month, 0)

    if "daily" in limits and daily >= limits["daily"] * 0.8:
        print(f"  [WARNING] [{service}] {daily}/{limits['daily']} daily requests used")
    if "monthly" in limits and monthly >= limits["monthly"] * 0.8:
        print(f"  [WARNING] [{service}] {monthly}/{limits['monthly']} monthly requests used")


def summary() -> str:
    data = _load()
    today = str(date.today())
    month = today[:7]
    lines = ["--- API Usage ---"]
    month_cost = 0.0
    for svc, info in sorted(data.items()):
        d = info.get("daily", {}).get(today, 0)
        m = info.get("monthly", {}).get(month, 0)
        lim = LIMITS.get(svc, {})
        d_lim = lim.get("daily", lim.get("hourly", "?"))
        m_lim = lim.get("monthly", "?")
        line = f"  {svc:12} today={d}/{d_lim}  month={m}/{m_lim}"
        rate = COST_PER_CALL.get(svc)
        if rate:
            cost = m * rate
            month_cost += cost
            line += f"  ~${cost:.2f} this month"
        lines.append(line)
    lines.append(f"  {'':12} estimated spend {month}: ~${month_cost:.2f}")
    return "\n".join(lines)


if __name__ == "__main__":
    import sys
    sys.stdout.reconfigure(encoding="utf-8")
    print(summary())
