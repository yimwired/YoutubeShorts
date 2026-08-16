"""Grounded research step -- the "where did this come from" layer.

The scriptwriter alone cannot cover a trend: anything that went viral
after the model's training cutoff would be confabulated, and that is
exactly the material this channel now leads with. So topic selection and
fact gathering run first, through Gemini with Google Search grounding,
and the scriptwriter is only allowed to dramatize what comes back here.

Google's API rejects `tools` combined with `response_mime_type=json`, so
the brief comes back as labeled plain text and is parsed leniently.

Two entry points:
  pick_trend_topic(candidates) -- choose + research a live trend
  research_evergreen(category) -- research an evergreen bucket topic

Both return a Brief, or None when nothing usable was found (the caller
falls back down the chain: trend -> evergreen -> ungrounded generation).
"""

from __future__ import annotations

import os
import re
import time
from dataclasses import dataclass

try:
    from google import genai
    from google.genai import types as genai_types
    _KEY = os.getenv("GEMINI_API_KEY")
    _CLIENT = genai.Client(api_key=_KEY) if _KEY else None
except ImportError:
    _CLIENT = None

from src.rate_tracker import record

# Flash first: the lite tier is noticeably worse at holding a grounded
# answer to its sources, and this is the step where a hallucinated year or
# name would ship straight into a video.
#
# Lite second because flash's free tier is only 20 requests per day per
# project (GenerateRequestsPerDayPerProjectPerModel-FreeTier). The daily
# run needs two, so a couple of manual test runs are enough to exhaust it,
# and without a second model a 429 at 06:00 means no video that day.
_MODELS = ["gemini-2.5-flash", "gemini-2.5-flash-lite"]

# 429 responses carry retryDelay ~20s (per-minute window). Waiting it out
# is worth it for a job that runs once a day.
_RETRIES = 3
_BACKOFF = 22

# What makes a topic wrong for this format, stated once and reused by
# both prompts so the two paths reject the same things.
_REJECT = """ห้ามเลือกหัวข้อประเภทนี้เด็ดขาด:
- การเมือง เลือกตั้ง นักการเมือง นโยบายรัฐ
- ข่าวเศร้า อุบัติเหตุ ภัยพิบัติ อาชญากรรมที่มีเหยื่อจริง คนเสียชีวิต
- ผลการแข่งกีฬา ตารางคะแนน (แต่ "ที่มาของกีฬา/กฎแปลกๆ" ทำได้)
- ดราม่าดารา ข่าวซุบซิบ เรื่องส่วนตัวของคนที่ยังมีชีวิต
- หวย การพนัน เนื้อหาที่ YouTube จำกัดโฆษณา
- อะไรที่จะเก่าภายใน 3 วัน (ราคาหุ้น สภาพอากาศ)"""

_BRIEF_FORMAT = """ตอบเป็นข้อความธรรมดาตามรูปแบบนี้เป๊ะๆ (ห้ามใส่ markdown, ห้ามใส่ JSON):

TOPIC: <ชื่อหัวข้อสั้นๆ ภาษาไทย ไม่เกิน 40 ตัวอักษร>
ANGLE: <มุมที่จะเล่า 1 ประโยค — ต้องเป็นมุม "ที่มา/จุดเริ่มต้น" ไม่ใช่แค่ "มันคืออะไร">
ORIGIN: <จุดเริ่มต้นจริงๆ ใคร ที่ไหน ปีไหน — ต้องมีปีและชื่อที่เจาะจง>
SPREAD: <มันแพร่ออกไปได้ยังไง ผ่านใคร ผ่านอะไร ช่วงไหน>
NUMBERS: <ตัวเลขจริง 2-3 ตัวที่ใช้ในสคริปต์ได้ เช่น ปี จำนวนวิว ระยะเวลา ราคา>
SURPRISE: <รายละเอียดที่คนส่วนใหญ่ไม่รู้ — อันนี้จะเก็บไว้เฉลยท้ายคลิป>
CONFIDENCE: <high|medium|low — ต่ำถ้าแหล่งข้อมูลขัดแย้งกันหรือหาไม่เจอ>

ทุกบรรทัดต้องมาจากผลค้นหาจริง ห้ามเดา ถ้าข้อไหนไม่มีข้อมูลจริงให้เขียนว่า unknown"""


@dataclass
class Brief:
    topic: str
    angle: str
    origin: str
    spread: str
    numbers: str
    surprise: str
    confidence: str
    source: str          # "trend" | "evergreen"
    raw: str

    def as_prompt_block(self) -> str:
        """The block handed to the scriptwriter as its only source of fact."""
        return (
            f"หัวข้อ: {self.topic}\n"
            f"มุมเล่า: {self.angle}\n"
            f"จุดเริ่มต้น: {self.origin}\n"
            f"การแพร่กระจาย: {self.spread}\n"
            f"ตัวเลขที่ใช้ได้: {self.numbers}\n"
            f"จุดเฉลยท้ายคลิป: {self.surprise}"
        )


def _grounded(prompt: str, temperature: float = 0.4) -> str | None:
    """Search-grounded completion, retried across models before giving up.

    Returning None here costs the whole day's video, so this leans hard on
    retrying: each model gets a few attempts with a pause long enough to
    clear a per-minute quota window, then the next model takes over.
    """
    if _CLIENT is None:
        print("  [research] no GEMINI_API_KEY — skipping grounded research")
        return None

    config = genai_types.GenerateContentConfig(
        tools=[genai_types.Tool(google_search=genai_types.GoogleSearch())],
        temperature=temperature,
    )

    for model in _MODELS:
        for attempt in range(1, _RETRIES + 1):
            try:
                resp = _CLIENT.models.generate_content(
                    model=model, contents=prompt, config=config)
                record("gemini")
                text = (resp.text or "").strip()
                if text:
                    return text
                print(f"  [research] {model} returned empty text")
                break            # empty is not a rate problem; change model
            except Exception as e:
                is_quota = "RESOURCE_EXHAUSTED" in str(e) or "429" in str(e)
                label    = f"{type(e).__name__}"
                if is_quota and attempt < _RETRIES:
                    print(f"  [research] {model} quota hit — retry "
                          f"{attempt}/{_RETRIES - 1} in {_BACKOFF}s")
                    time.sleep(_BACKOFF)
                    continue
                print(f"  [research] {model} failed: {label}: {str(e)[:160]}")
                break            # out of retries, or a non-quota error
        if model != _MODELS[-1]:
            print(f"  [research] falling back to {_MODELS[_MODELS.index(model) + 1]}")
    return None


def _parse_brief(text: str, source: str) -> Brief | None:
    """Pull the labeled fields out of the model's plain-text answer.

    Tolerant on purpose: the model sometimes bolds a label or adds a
    stray bullet, and losing a whole day's video to a markdown asterisk
    would be absurd.
    """
    fields = {}
    for line in text.splitlines():
        m = re.match(r"^\s*[*\-#\s]*\**\s*([A-Z]+)\**\s*:\s*(.+?)\s*$", line)
        if m:
            fields[m.group(1).upper()] = m.group(2).strip(" *")

    topic = fields.get("TOPIC", "")
    if not topic or topic.lower() in ("unknown", "none", "n/a"):
        return None

    brief = Brief(
        topic=topic[:60],
        angle=fields.get("ANGLE", ""),
        origin=fields.get("ORIGIN", ""),
        spread=fields.get("SPREAD", ""),
        numbers=fields.get("NUMBERS", ""),
        surprise=fields.get("SURPRISE", ""),
        confidence=fields.get("CONFIDENCE", "medium").lower(),
        source=source,
        raw=text,
    )

    # A brief with no origin is the one thing this whole step exists to
    # produce -- without it the script degenerates into the generic
    # "did you know" format the channel is moving away from.
    if brief.origin.lower().startswith("unknown") or len(brief.origin) < 15:
        print(f"  [research] '{topic}' has no usable origin — rejected")
        return None
    if brief.confidence == "low":
        print(f"  [research] '{topic}' confidence low — rejected")
        return None
    return brief


def pick_trend_topic(candidates: list[dict],
                     avoid: list[str] | None = None) -> Brief | None:
    """Choose the most explainable candidate and research its backstory."""
    if not candidates:
        return None

    listing = "\n".join(
        f"{i+1}. {c['title']}" + (f"  ({c['context']})" if c.get("context") else "")
        for i, c in enumerate(candidates)
    )
    avoid_block = ""
    if avoid:
        avoid_block = ("\n\nหัวข้อที่ทำไปแล้ว ห้ามซ้ำ:\n"
                       + "\n".join(f"- {t}" for t in avoid[-40:]))

    prompt = (
        "คุณคือคนหาหัวข้อให้ช่องคลิปสั้นที่เล่า 'ที่มาของสิ่งที่กำลังฮิต'\n\n"
        "นี่คือสิ่งที่กำลังเป็นกระแสตอนนี้:\n" + listing + avoid_block + "\n\n"
        "งานของคุณ:\n"
        "1. ค้นหาว่าแต่ละอันคืออะไรจริงๆ (อย่าเดาจากชื่อ)\n"
        "2. เลือกมา 1 อันที่ 'เล่าที่มาได้สนุกที่สุด' — มีจุดกำเนิดที่ชัดเจน "
        "มีคนเริ่ม มีปี มีเรื่องเบื้องหลังที่คนส่วนใหญ่ไม่รู้\n"
        "3. ถ้าไม่มีอันไหนผ่านเกณฑ์เลย ให้ตอบ TOPIC: unknown\n\n"
        + _REJECT + "\n\n"
        "เลือกแล้วค้นหาข้อมูลเชิงลึกของหัวข้อนั้น แล้ว" + _BRIEF_FORMAT
    )

    text = _grounded(prompt, temperature=0.5)
    if not text:
        return None
    brief = _parse_brief(text, source="trend")
    if brief:
        print(f"  [research] trend → {brief.topic} ({brief.confidence})")
    return brief


def research_evergreen(category: str,
                       avoid: list[str] | None = None) -> Brief | None:
    """Research an evergreen bucket topic to the same depth as a trend.

    Used on days when no trend qualifies. Grounding matters here too --
    it is what stopped the fact-accuracy complaints the channel used to
    get in comments.
    """
    avoid_block = ""
    if avoid:
        avoid_block = ("\n\nเรื่องที่ทำไปแล้ว ห้ามซ้ำ:\n"
                       + "\n".join(f"- {t}" for t in avoid[-40:]))

    prompt = (
        "คุณคือคนหาหัวข้อให้ช่องคลิปสั้นที่เล่า 'ที่มาของเรื่องที่คนคิดว่ารู้แล้ว'\n\n"
        f"หมวดวันนี้: {category}\n"
        "หาเรื่องจริง 1 เรื่องในหมวดนี้ที่:\n"
        "- คนไทยส่วนใหญ่เคยได้ยิน แต่ไม่รู้ว่ามันเริ่มมาจากอะไร\n"
        "- มีจุดกำเนิดที่ระบุได้ (คน ปี สถานที่)\n"
        "- มีรายละเอียดที่ทำให้คนอึ้งตอนรู้"
        + avoid_block + "\n\n" + _REJECT + "\n\n"
        "ค้นหาข้อมูลจริงให้ครบก่อน แล้ว" + _BRIEF_FORMAT
    )

    text = _grounded(prompt, temperature=0.7)
    if not text:
        return None
    brief = _parse_brief(text, source="evergreen")
    if brief:
        print(f"  [research] evergreen → {brief.topic} ({brief.confidence})")
    return brief


def get_brief(categories: list[str], category: str,
              avoid: list[str] | None = None,
              use_trends: bool = True) -> Brief | None:
    """Hybrid entry point: try a live trend, fall back to evergreen.

    `category` is the evergreen bucket already chosen by the caller's
    round-robin, so bucket rotation stays owned by the generator.
    """
    if use_trends:
        from src.trends import get_trend_candidates
        brief = pick_trend_topic(get_trend_candidates(), avoid=avoid)
        if brief:
            return brief
        print("  [research] no trend qualified — falling back to evergreen")
    return research_evergreen(category, avoid=avoid)
