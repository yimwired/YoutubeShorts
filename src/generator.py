import os
import json
import requests
from src.rate_tracker import record

GROQ_API_KEY = os.getenv("GROQ_API_KEY")
GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"

SYSTEM_PROMPT = """You are an elite YouTube Shorts scriptwriter and visual storyteller.
Your scripts follow a 3-part cinematic structure:

1. HOOK (sentence 1) — One striking statement that forces the viewer to stop scrolling.
   Must create instant curiosity or disbelief. No filler words.

2. BUILDUP (sentences 2-4) — Each sentence deepens the story. Every sentence must:
   - Add new information or tension
   - Paint a vivid visual scene the camera can capture
   - Make the viewer need to hear the next sentence

3. REVEAL (sentences 5-6) — The payoff. End with a truth that reframes everything.
   The final sentence should feel like a quiet mic drop, not a shout.

Rules:
- Exactly 5-6 sentences total. No more.
- Each sentence = one clear visual moment (something a camera can show)
- Simple, precise language. No jargon. No filler.
- Thai translation must sound natural when spoken aloud — not translated, reimagined."""


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
        "Generate one YouTube Shorts script using the HOOK→BUILDUP→REVEAL structure."
        + topic_hint +
        " Pick a genuinely surprising or little-known fact.\n\n"
        "Return ONLY a valid JSON object with these keys:\n\n"
        "- title_en: curiosity-gap English title (max 60 chars)\n"
        "- title_th: Thai title — natural spoken Thai, NOT word-for-word translation (max 50 chars, Thai chars only)\n"
        "- sentences: array of EXACTLY 5-6 objects, one per sentence of the script. Each object:\n"
        "  'text_en': one English sentence\n"
        "  'text_th': Thai translation of that sentence (natural spoken Thai, Thai chars only)\n"
        "  'keyword': Pexels video search term for THIS specific sentence's visual moment — cinematic, commonly available (e.g. 'close up bee on honeycomb slow motion'). Must match what is being said.\n"
        "  'fallback': simple 1-2 word backup keyword\n"
        "- description: YouTube description in English (2-3 sentences + 'Follow for more! #Shorts')\n"
        "- description_th: same in natural Thai (end with 'ติดตามเพื่อรับความรู้ใหม่ทุกวัน! #Shorts')\n"
        "- hashtags: 10 English hashtags WITHOUT # (include 'shorts','facts','didyouknow')\n"
        "- hashtags_th: 8 Thai hashtags WITHOUT # (include 'shorts')\n"
        "- music_mood: ONE word — mysterious/dramatic/upbeat/melancholic/epic/peaceful/tense/inspiring\n"
        "- thumbnail_keyword: ONE Pexels photo search term\n"
        "- thumbnail_prompt: vivid AI image prompt — cinematic, dramatic, ultra-realistic\n\n"
        'Example: {"title_en":"Why Honey Never Expires","title_th":"ทำไมน้ำผึ้งไม่มีวันหมดอายุ",'
        '"sentences":['
        '{"text_en":"Honey found in ancient Egyptian tombs is still edible after 3000 years.","text_th":"น้ำผึ้งที่พบในสุสานอียิปต์โบราณยังกินได้แม้ผ่านมา 3000 ปี","keyword":"ancient egypt tomb artifact closeup","fallback":"ancient egypt"},'
        '{"text_en":"Scientists tasted it. It was perfect.","text_th":"นักวิทยาศาสตร์ชิมดู มันยังสมบูรณ์แบบ","keyword":"scientist lab tasting sample microscope","fallback":"scientist lab"}'
        '],'
        '"music_mood":"mysterious","thumbnail_keyword":"golden honey jar macro"}'
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

    # Build script_en / script_th / keywords from sentences (backward compat)
    sentences = data.get("sentences", [])
    if sentences:
        data["script_en"] = " ".join(s.get("text_en", "") for s in sentences)
        data["script_th"] = " ".join(s.get("text_th", "") for s in sentences)
        data["keywords"]  = [
            {"specific": s.get("keyword", ""), "fallback": s.get("fallback", "")}
            for s in sentences
        ]

    # Strip CJK hallucinations from Thai fields
    data["title_th"]  = _clean_thai(data.get("title_th", ""))
    data["script_th"] = _clean_thai(data.get("script_th", ""))
    for s in data.get("sentences", []):
        s["text_th"] = _clean_thai(s.get("text_th", ""))
    return data
