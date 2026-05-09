import os
import json
import requests
from src.rate_tracker import record

GROQ_API_KEY = os.getenv("GROQ_API_KEY")
GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"

SYSTEM_PROMPT = """You are a viral YouTube Shorts scriptwriter. Your scripts follow a proven 3-part structure:

1. HOOK (first 3 seconds) — Start with a shocking statement, surprising question, or disbelief trigger.
   Examples: "Most people have no idea that...", "This will sound insane but...", "Scientists just discovered..."
   The hook MUST make the viewer stop scrolling immediately.

2. BUILDUP — 2-3 sentences that deepen curiosity. Tease the answer without revealing it yet.
   Build tension. Use phrases like "But here's the crazy part...", "And it gets weirder..."

3. REVEAL — End with the surprising payoff. The last sentence should feel like a mic drop.

Rules:
- Total: 5-7 sentences, under 45 seconds when spoken
- Simple exciting language, no jargon
- Every word must earn its place"""


def _call_groq(messages: list, max_tokens: int = 600) -> str:
    headers = {"Authorization": f"Bearer {GROQ_API_KEY}", "Content-Type": "application/json"}
    payload = {
        "model": "llama-3.3-70b-versatile",
        "messages": messages,
        "temperature": 0.9,
        "max_tokens": max_tokens,
    }
    resp = requests.post(GROQ_URL, headers=headers, json=payload, timeout=30)
    record("groq")
    resp.raise_for_status()
    return resp.json()["choices"][0]["message"]["content"].strip()


_CATEGORIES = [
    "space & universe", "deep ocean", "human brain", "animal behavior",
    "ancient history", "food science", "psychology tricks", "weird laws",
    "record-breaking nature", "invisible technology", "lost civilizations",
    "extreme survival", "medical mysteries", "optical illusions", "physics",
]

def generate_fact_script(topic: str = None, used_titles: list = None) -> dict:
    """
    Generate bilingual fact scripts.
    Returns {title_en, script_en, title_th, script_th, keywords}
    """
    import random
    category = topic if topic else random.choice(_CATEGORIES)
    topic_hint = f" The fact MUST be about this category: '{category}'. Pick a specific surprising angle within it."

    if used_titles:
        avoid_block = "\n".join(f"- {t}" for t in used_titles[-50:])
        topic_hint += f"\n\nDo NOT use any of these already-published topics:\n{avoid_block}"

    prompt = (
        "Generate one viral YouTube Shorts script using the HOOK→BUILDUP→REVEAL structure."
        + topic_hint +
        " Pick a genuinely surprising or little-known fact.\n"
        "Return ONLY a JSON object with keys:\n"
        "- title_en: curiosity-gap English title (max 60 chars, start with 'Why', 'The reason', 'This', etc.)\n"
        "- script_en: English voiceover (5-7 sentences, HOOK→BUILDUP→REVEAL)\n"
        "- title_th: Thai title — translate the English title naturally. MUST be a complete, standalone Thai sentence that makes sense on its own (max 50 chars). Use engaging Thai phrasing, not word-for-word translation. Example: 'Why Honey Never Expires' → 'ทำไมน้ำผึ้งถึงไม่มีวันเสีย'. Use ONLY Thai characters.\n"
        "- script_th: full Thai translation of script_en (natural spoken Thai, not formal) — use ONLY Thai characters (Unicode ก-๙), NO Chinese/Japanese/Korean characters whatsoever\n"
        "- keywords: list of 3 objects each with:\n"
        "  'specific': cinematic stock footage search term — MUST be visually stunning AND commonly available on Pexels (e.g. 'person looking at stars at night', 'time lapse city lights', 'ocean waves crashing slow motion'). Avoid technical/scientific terms.\n"
        "  'fallback': simple 1-2 word backup (e.g. 'ocean', 'city', 'nature')\n"
        "- description: YouTube description in English (2-3 sentences about the fact + 'Follow for more mind-blowing facts every day! #Shorts')\n"
        "- description_th: same description translated to natural Thai (end with 'ติดตามเพื่อรับความรู้ใหม่ทุกวัน! #Shorts')\n"
        "- hashtags: list of 10 English hashtags WITHOUT # — must include 'shorts','facts','didyouknow' + topic-specific trending tags (e.g. for ocean: ['shorts','facts','didyouknow','ocean','deepocean','marinelife','nature','science','mindblown','viral'])\n"
        "- hashtags_th: list of 8 Thai hashtags WITHOUT # — must include 'shorts' + Thai trending tags relevant to the topic (e.g. for ocean: ['shorts','ทะเล','ปริศนา','ความรู้','เรื่องน่ารู้','วิทยาศาสตร์','ธรรมชาติ','เรื่องแปลก'])\n"
        "- music_mood: ONE word describing the emotional mood for background music. Choose from: mysterious, dramatic, upbeat, melancholic, epic, peaceful, tense, inspiring\n"
        "- thumbnail_keyword: ONE search term for Pexels photo search (e.g. 'human brain anatomy').\n"
        "- thumbnail_prompt: vivid AI image generation prompt — cinematic, dramatic, ultra-realistic. Include lighting, mood, and subject. E.g. 'dramatic cinematic photo of a glowing ancient underwater city, blue ethereal light rays, ultra-realistic 4K'\n"
        'Example: {"title_en":"Why Honey Never Expires","title_th":"ทำไมน้ำผึ้งไม่มีวันหมดอายุ",'
        '"script_en":"This will blow your mind...","script_th":"นี่จะทำให้คุณตกใจ...",'
        '"keywords":[{"specific":"honey jar ancient egypt","fallback":"honey"},{"specific":"beekeeper honeycomb","fallback":"bees"},{"specific":"ancient jar preserved","fallback":"ancient"}],'
        '"thumbnail_keyword":"golden honey jar macro"}'
    )

    import re as _re

    def _clean_thai(text: str) -> str:
        # Keep only Thai chars + spaces + basic punctuation
        return _re.sub(r'[^฀-๿\s\d\.,!?\'\"\-\(\)]', '', text).strip()

    raw = _call_groq([
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": prompt},
    ], max_tokens=1200)

    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]

    data = json.loads(raw.strip())
    # Strip CJK hallucinations from Thai fields
    data["title_th"]  = _clean_thai(data.get("title_th", ""))
    data["script_th"] = _clean_thai(data.get("script_th", ""))
    return data
