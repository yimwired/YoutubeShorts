import os
import json
import requests
from src.rate_tracker import record

try:
    from google import genai
    from google.genai import types as genai_types
    _GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
    _GEMINI_CLIENT = genai.Client(api_key=_GEMINI_API_KEY) if _GEMINI_API_KEY else None
    _GEMINI_AVAILABLE = _GEMINI_CLIENT is not None
except ImportError:
    _GEMINI_CLIENT = None
    _GEMINI_AVAILABLE = False

# Round-robin bucket index across slot styles. Persists between runs
# so we don't bias toward whatever random.seed lands on each invocation.
# Each style gets its own counter; index wraps when it exceeds list len.
_BUCKET_STATE_FILE = "bucket_state.json"


def _next_bucket(style: str, cats: list[str]) -> str:
    try:
        with open(_BUCKET_STATE_FILE, encoding="utf-8") as f:
            state = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        state = {}
    last = state.get(style, -1)
    idx  = (last + 1) % len(cats)
    state[style] = idx
    try:
        with open(_BUCKET_STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(state, f, indent=2)
    except OSError:
        pass
    return cats[idx]

GROQ_API_KEY = os.getenv("GROQ_API_KEY")
GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"

SYSTEM_PROMPT_TRENDING = """You are a Thai YouTube Shorts scriptwriter in the style of @develian_g —
a friend casually telling you a wild-but-true fact. Conversational, playful, slightly cheeky.

ACCURACY IS NON-NEGOTIABLE:
- Every fact must be verifiable on Wikipedia or major scientific sources
- If unsure of a number, date, name, or claim — PICK A DIFFERENT FACT
- The "wow" comes from the TRUTH being weird, not from exaggeration
- If a fact has nuance, include it. Misleading > absent

TONE (Thai script especially):
- Talk like a friend, NOT a narrator. Use spoken Thai: "ปะ", "นะ", "แต่...", "ไอ้...", "ชิว ๆ", "เลย", "หน่ะ"
- Rhetorical questions OK: "คุณคิดว่า...?", "เคยสงสัยไหมว่า...?", "รู้ปะว่า...?"
- Short punchy sentences. Mix question + statement. No formal narrator words like "ดังนั้น", "เนื่องจาก", "อย่างไรก็ตาม"
- A vivid twist or punch line at the end ("...แต่จริง ๆ คือ...", "เลี้ยงได้นะ แต่...")
- Authority cites OK if true (Guinness, NASA, นักวิทยาศาสตร์ที่ X) — adds credibility

STRUCTURE (10-14 sentences total, each ~2-3 seconds when spoken):
1. HOOK (sentence 1) — Open with a question OR a surprising one-liner that hooks instantly
2. SETUP (sentences 2-4) — Quick context. Why is this surprising? What does it look like?
3. DETAILS (sentences 5-10) — Stack 3-5 concrete facts/examples. Each = one short sentence.
   Sprinkle rhetorical questions ("X เหรอ? Y เหรอ?")
4. TWIST/PAYOFF (sentences 11-14) — End with a memorable vivid image or a "yes-but" twist

Rules:
- 10-14 sentences. Each MAX ~12 Thai words / 10 EN words.
- Each sentence = one visual moment a camera can show
- text_th = natural spoken Thai (the real voice). text_en = casual conversational English (not formal).
- If the topic can't be made conversational without losing accuracy, pick a different topic."""

SYSTEM_PROMPT_CHAOS = """You are a Thai scriptwriter for light, fun-fact-style YouTube Shorts.
Tone: casual, slightly humorous, like a smart friend telling you trivia over coffee.
Not absurd, not formal — somewhere comfortably in between.

ACCURACY:
- Every fact must be verifiable on Wikipedia / scientific sources
- If unsure of specifics, pick a different topic — do not guess
- Humor comes from the framing or wording, NOT from exaggerating facts

TONE:
- Light, breezy, mildly playful — NEVER over-the-top
- NO reaction interjections (no "เว๋ย / อ๋าว / โอ้โห / บ้าเลย / ตาย / OMG / BRO / NO WAY")
- NO Gen Z brain-rot phrases. NO repeated characters (อ๊าาา, ว้าาาว)
- OK: gentle conversational Thai (เนอะ, นะ, น่ะ, เลย) — sparingly, not every sentence
- OK: a soft witty observation at the end ("...ก็แปลกดีนะ", "...ลองคิดดูสิ")
- Sentences flow as continuous narration, NOT shouty alternating

STRUCTURE (8-10 sentences):
1. Open with the surprising fact, stated plainly
2. Build context: who/where/when/how
3. Add 2-3 supporting details
4. Close with a light observation or "did-you-know" framing

Rules:
- Each sentence normal length (8-15 words). Not staccato single-word lines.
- Thai = natural spoken Thai. EN = casual conversational English.
- text_en and text_th convey the same point, reimagined for each language."""

SYSTEM_PROMPT_NARRATIVE = """You are a Thai narrated documentary scriptwriter.
Style: calm, deep, thought-provoking — like NatGeo narration meets Thai philosophical wisdom.

ACCURACY RULES (non-negotiable):
- Any factual claim about animals, biology, psychology, history must be verifiable
- If citing behavior or biology, it must be a real documented phenomenon
- Metaphors are fine; FABRICATED facts dressed as truth are not
- If unsure, pick a different angle — never invent

Rules:
- Write as if you are observing something quietly, not teaching
- Every sentence = one visual moment in nature or human experience
- No explicit lessons. Let the viewer feel it themselves.
- Use simple Thai words. Short sentences. Natural pauses.
- Topics: animal behavior, nature metaphors, human psychology, silent truths
- End with one question or image that stays in the mind
- Thai translation: poetic, spoken Thai — not formal, not literal"""

SYSTEM_PROMPT = SYSTEM_PROMPT_TRENDING  # default


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


def _call_gemini(messages: list, max_tokens: int = 1200) -> str:
    """Gemini 2.5 Flash with JSON output. Stronger factual accuracy than llama-3.3."""
    system = next((m["content"] for m in messages if m["role"] == "system"), "")
    user   = next((m["content"] for m in messages if m["role"] == "user"),   "")

    resp = _GEMINI_CLIENT.models.generate_content(
        model="gemini-2.5-flash-lite",
        contents=user,
        config=genai_types.GenerateContentConfig(
            system_instruction=system,
            temperature=0.85,
            max_output_tokens=max_tokens,
            response_mime_type="application/json",
        ),
    )
    record("gemini")
    text = (resp.text or "").strip()
    if not text:
        raise RuntimeError("Gemini returned empty response")
    return text


def _llm_call(messages: list, max_tokens: int = 1200) -> str:
    """Try Gemini first, fall back to Groq on any failure."""
    if _GEMINI_AVAILABLE:
        try:
            return _call_gemini(messages, max_tokens)
        except Exception as e:
            print(f"[generator] Gemini failed, fallback Groq: {e}")
    return _call_groq(messages, max_tokens)


_CATEGORIES = [
    "space & universe", "deep ocean", "human brain", "animal behavior",
    "ancient history", "food science", "psychology tricks", "weird laws",
    "record-breaking nature", "invisible technology", "lost civilizations",
    "extreme survival", "medical mysteries", "optical illusions", "physics",
]

_NARRATIVE_CATEGORIES = [
    "nature metaphors that mirror human life",
    "animal behavior that reflects human psychology",
    "silent truths about success and failure",
    "the psychology of loneliness and connection",
    "what science says about how we love",
    "trees and plants doing things we never notice",
    "animals that grieve, mourn, and remember",
    "the hidden cost of ambition",
    "why silence is more powerful than words",
    "what predators teach us about patience",
    "the biology of letting go",
    "things that survive by adapting, not fighting",
    "the mathematics of kindness and trust",
    "why the strongest things are often the quietest",
    "what ancient wisdom and modern science agree on",
]

_CHAOS_CATEGORIES = [
    "world records that sound impossible",
    "disgusting human body facts",
    "laws that actually exist and are insane",
    "animal behavior that defies logic",
    "things that sound fake but are 100% real",
    "historical events nobody talks about",
    "food facts that will ruin your appetite",
    "science facts that break common sense",
    "sleep and dreams weirdness",
    "money and wealth facts that are absurd",
    "sports records that are unbelievably extreme",
    "ancient civilizations doing crazy things",
    "modern technology facts that seem like magic",
    "phobias and fears that are bizarrely specific",
    "crime records and heists that seem fictional",
]

def generate_fact_script(topic: str = None, used_titles: list = None,
                         style: str = "trending") -> dict:
    """
    Generate bilingual fact scripts.
    Returns {title_en, script_en, title_th, script_th, keywords}
    """
    _prompts = {
        "trending":  SYSTEM_PROMPT_TRENDING,
        "chaos":     SYSTEM_PROMPT_CHAOS,
        "narrative": SYSTEM_PROMPT_NARRATIVE,
    }
    system = _prompts.get(style, SYSTEM_PROMPT_TRENDING)

    cats = (
        _CHAOS_CATEGORIES     if style == "chaos"     else
        _NARRATIVE_CATEGORIES if style == "narrative" else
        _CATEGORIES
    )
    category = topic if topic else _next_bucket(style, cats)
    topic_hint = f" The fact MUST be about this category: '{category}'. Pick a specific surprising angle within it."

    if used_titles:
        avoid_block = "\n".join(f"- {t}" for t in used_titles[-50:])
        topic_hint += f"\n\nDo NOT use any of these already-published topics:\n{avoid_block}"

    sentence_count = (
        "8-10"   if style == "chaos"     else
        "5-6"    if style == "narrative" else
        "10-14"  # trending (Develian-style, short punchy lines)
    )
    chaos_note = ""  # legacy reaction-injection removed (chaos now = light informative)

    # Narrative scripts are abstract by design (animal metaphors, psychology) and
    # rarely mention proper nouns. Broaden the entity definition for narrative so
    # the Wikipedia-image overlay has something to render -- specific species,
    # named natural locations, and named researchers/studies all resolve cleanly.
    if style == "narrative":
        entity_clause = (
            "- entities: array of 2-4 lookup terms that Wikipedia would have a page for,\n"
            "    drawn from what the script actually mentions. Accepted: specific species\n"
            "    (use the most common English name -- 'African elephant', 'axolotl',\n"
            "    'mycelium', 'octopus'); specific named places/ecosystems (Amazon\n"
            "    rainforest, Sahara, Mariana Trench, Pando aspen grove); named\n"
            "    researchers/studies if cited; named natural phenomena (aurora borealis).\n"
            "    Each entry: {'name': str, 'sentence_idx': int (0-based)}.\n"
            "    Max 4 entities. Prefer subjects with iconic visual identity."
        )
    else:
        entity_clause = (
            "- entities: array (can be empty) of REAL-WORLD NAMED entities mentioned in the script.\n"
            "    Include ONLY: real people (historical or living), specific organizations (NASA, FBI),\n"
            "    specific places/landmarks (Mount Tambora, Eiffel Tower), named events, named artworks\n"
            "    (Mona Lisa), specific products/inventions with a proper name. Skip common nouns.\n"
            "    Each entry: {'name': str (use the most well-known English name for Wikipedia lookup),\n"
            "                 'sentence_idx': int (0-based index of the sentence where it appears)}\n"
            "    If a sentence has no named entity, do not invent one. Max 5 entities per script."
        )

    prompt = (
        "Generate one YouTube Shorts script using the HOOK→BUILDUP→REVEAL structure."
        + topic_hint +
        " Pick a genuinely surprising or little-known fact.\n\n"
        "Return ONLY a valid JSON object with these keys:\n\n"
        "- title_en: curiosity-gap English title (max 60 chars)\n"
        "- title_th: Thai title — natural spoken Thai, NOT word-for-word translation (max 50 chars, Thai chars only)\n"
        f"- sentences: array of EXACTLY {sentence_count} objects, one per sentence of the script.{chaos_note} Each object:\n"
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
        "- thumbnail_prompt: vivid AI image prompt — cinematic, dramatic, ultra-realistic\n"
        + entity_clause + "\n\n"
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

    raw = _llm_call([
        {"role": "system", "content": system},
        {"role": "user", "content": prompt},
    ], max_tokens=3000)

    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]

    # strict=False tolerates raw control chars (newlines, tabs) inside JSON
    # string values — Groq sometimes emits them in Thai content.
    try:
        data = json.loads(raw.strip(), strict=False)
    except json.JSONDecodeError:
        cleaned = _re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f]', '', raw.strip())
        data = json.loads(cleaned, strict=False)

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
