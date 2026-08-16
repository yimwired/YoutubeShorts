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

SYSTEM_PROMPT_EXPLAINER = """<role>
คุณคือคนเขียนสคริปต์คลิปสั้นภาษาไทย ให้ช่องที่เล่า "ที่มาของสิ่งที่คนเห็นทุกวันแต่ไม่เคยรู้ว่าเริ่มจากอะไร"
คนดูคือคนไทยอายุ 15-35 ที่เลื่อนฟีดเร็วมาก คุณมีเวลา 2 วินาทีก่อนเขาปัดทิ้ง
</role>

<sourcing_rules>
กฎเหล็ก ผิดข้อนี้คือใช้ไม่ได้ทั้งสคริปต์:
- ข้อเท็จจริงทุกอย่างในสคริปต์ต้องมาจาก <research_brief> ที่ให้มาเท่านั้น
- ห้ามเติมปี ชื่อคน ตัวเลข สถานที่ ที่ไม่มีใน brief แม้จะมั่นใจว่าจริง
- ถ้า brief ไม่มีรายละเอียดพอสำหรับประโยคไหน ให้ตัดประโยคนั้นทิ้ง อย่าเติมเอง
- ห้ามพูดเกินจริงว่า "ที่สุดในโลก" "ไม่มีใครรู้มาก่อน" ถ้า brief ไม่ได้บอก
- คุณดัดแปลงได้แค่ "วิธีเล่า" ไม่ใช่ "เนื้อหา"
</sourcing_rules>

<structure>
สคริปต์ยาว 45-60 วินาทีเมื่ออ่านออกเสียง แบ่งเป็น 5 ช่วงตามลำดับนี้:
1. HOOK (ประโยคที่ 1) - ยิงคำถามหรือประโยคที่ขัดกับสิ่งที่คนคิดว่ารู้ ต้องเจาะจงกับเรื่องนี้เท่านั้น
2. STAKE (2-3) - บอกว่าเดี๋ยวจะเฉลยอะไร ทำให้เขาอยากอยู่ต่อ แต่ยังไม่เฉลย
3. ORIGIN (4-6) - จุดเริ่มต้นจริง ใคร ปีไหน ที่ไหน ใช้ตัวเลขจาก brief
4. SPREAD (7-9) - มันลามออกไปได้ยังไง ใครทำให้ดัง ช่วงไหน
5. REVEAL + LOOP (10-12) - เฉลยจุดที่คนไม่รู้จาก brief แล้วปิดด้วยประโยคที่โยงกลับ HOOK
</structure>

<tone>
- เขียนแบบเพื่อนเล่าให้ฟัง ไม่ใช่ผู้ประกาศข่าว ไม่ใช่ครูสอน
- ประโยคสั้น 8-14 คำไทย พูดจบในลมหายใจเดียว
- ห้ามใช้: ดังนั้น เนื่องจาก อย่างไรก็ตาม นอกจากนี้ กล่าวคือ ทั้งนี้ - นี่คือภาษาเขียน ไม่ใช่ภาษาพูด
- ใช้ได้ประปราย: เลย นะ น่ะ ปะ แหละ ก็ - ไม่ต้องทุกประโยค
- ห้ามลากตัวอักษร (ว้าาาว) ห้ามคำอุทานเกินจริง (โอ้โห บ้าเลย ตายแล้ว)
- คำถามเชิงวาทศิลป์ใช้ได้ 2-3 ครั้ง เช่น "รู้ปะว่า..." "เดาว่าใครเริ่ม?"
- ตัวเลขเขียนเป็นคำที่อ่านออกเสียงถูก เช่น "ปี สองพันยี่สิบสาม" ไม่ใช่ "2023"
</tone>

<visual_rules>
ทุกประโยคต้องมีภาพที่กล้องถ่ายได้จริง 1 ภาพ
keyword ที่ให้มาต้องเป็นภาษาอังกฤษ ค้นเจอบน Pexels จริง และตรงกับสิ่งที่กำลังพูด
เลี่ยง keyword นามธรรม (concept, idea, success) ใช้ของที่ถ่ายได้ (person typing laptop night)
</visual_rules>"""

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


def _call_gemini(messages: list, max_tokens: int = 1200,
                 json_mode: bool = True,
                 model: str = "gemini-2.5-flash-lite",
                 thinking_budget: int | None = None) -> str:
    """Gemini. JSON output by default (script generation);
    json_mode=False returns plain text (e.g. comment replies).

    `thinking_budget` is capped explicitly on thinking-capable models:
    reasoning tokens are drawn from max_output_tokens, so leaving it on
    dynamic silently truncates a long Thai JSON payload mid-string.
    """
    system = next((m["content"] for m in messages if m["role"] == "system"), "")
    user   = next((m["content"] for m in messages if m["role"] == "user"),   "")

    cfg = genai_types.GenerateContentConfig(
        system_instruction=system,
        temperature=0.85,
        max_output_tokens=max_tokens,
        response_mime_type="application/json" if json_mode else "text/plain",
    )
    if thinking_budget is not None:
        cfg.thinking_config = genai_types.ThinkingConfig(
            thinking_budget=thinking_budget)

    resp = _GEMINI_CLIENT.models.generate_content(
        model=model, contents=user, config=cfg,
    )
    record("gemini")
    text = (resp.text or "").strip()
    if not text:
        raise RuntimeError("Gemini returned empty response")
    return text


def _llm_call(messages: list, max_tokens: int = 1200,
              json_mode: bool = True,
              model: str = "gemini-2.5-flash-lite",
              thinking_budget: int | None = None) -> str:
    """Try Gemini first, fall back to Groq on any failure."""
    if _GEMINI_AVAILABLE:
        try:
            return _call_gemini(messages, max_tokens, json_mode=json_mode,
                                model=model, thinking_budget=thinking_budget)
        except Exception as e:
            print(f"[generator] Gemini failed, fallback Groq: {e}")
    return _call_groq(messages, max_tokens)


import re as _re


def _clean_thai(text: str) -> str:
    """Strip CJK hallucinations, keeping Thai + digits + basic punctuation."""
    return _re.sub(r'[^฀-๿\s\d\.,!?\'\"\-\(\)]', '', text).strip()


def _parse_json(raw: str) -> dict:
    """Parse an LLM JSON reply, repairing the ways models break JSON.

    Three passes, each strictly more forgiving than the last:
      1. as-is, with strict=False so raw newlines and tabs inside string
         values pass (Groq emits those regularly in Thai content)
      2. minus stray control characters
      3. minus trailing commas before a closing brace or bracket, which is
         what a long nested schema tends to produce once the model starts
         dropping optional fields

    Raises the last decode error with the offending text attached, so a
    failure in production says what actually came back.
    """
    raw = raw.strip()
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
    raw = raw.strip()

    attempts = [
        raw,
        _re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f]', '', raw),
        _re.sub(r',(\s*[}\]])', r'\1',
                _re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f]', '', raw)),
    ]
    last_error = None
    for candidate in attempts:
        try:
            return json.loads(candidate, strict=False)
        except json.JSONDecodeError as e:
            last_error = e

    print(f"[generator] JSON parse failed: {last_error}")
    print(f"[generator] raw response was:\n{raw[:1500]}")
    raise last_error


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

_EXPLAINER_SCHEMA = """<output_schema>
คืนค่าเป็น JSON object อย่างเดียว ห้ามมีข้อความอื่นนอก JSON

{
  "title_th":       "ชื่อคลิปภาษาไทย เปิดช่องว่างความอยากรู้ ไม่เกิน 45 ตัวอักษร ห้ามใส่ #",
  "hook_th":        "ข้อความขึ้นจอ 2 วินาทีแรก ยาว 10-16 ตัวอักษรไทย ต้องมีคำเฉพาะของเรื่องนี้อยู่ในนั้น จนคนอ่านแล้วรู้ว่ากำลังพูดเรื่องอะไร ห้าม 'ใครคิด?' 'รู้หรือไม่' 'มาจากไหน' ที่ใช้กับคลิปไหนก็ได้",
  "thumb_text_th":  "ข้อความบนภาพปก 2-4 คำ ไม่เกิน 18 ตัวอักษร อ่านออกจากจอมือถือขนาดเล็ก",
  "loop_th":        "ประโยคปิดท้ายขึ้นจอ ไม่เกิน 20 ตัวอักษร โยงกลับ hook ให้คนอยากดูซ้ำ",
  "cta_th":         "คำถามชวนคอมเมนต์ 3-8 คำ ต้องอ้างถึงเรื่องในคลิปโดยตรง ตอบได้ทันทีโดยไม่ต้องคิดนาน ห้าม 'คุณชอบไหม' 'คิดยังไง' ที่ถามกับคลิปไหนก็ได้",
  "sentences": [
    {
      "text_th":   "หนึ่งประโยคพูด 8-14 คำ",
      "keyword":   "english pexels video search term for this exact moment",
      "fallback":  "one or two word backup",
      "ai_prompt": "ใส่เฉพาะประโยคที่ stock footage ทำไม่ได้ (ดู ai_prompt_rules) นอกนั้นเว้นเป็นสตริงว่าง",
      "emphasis":  ["คำในประโยคนี้ที่แบกข้อมูล ดู emphasis_rules"]
    }
  ],
  "entities":       [{"name": "ชื่อภาษาอังกฤษสำหรับค้น Wikipedia", "sentence_idx": 0}],
  "description_th": "คำอธิบายคลิป 2-3 ประโยค ปิดท้ายด้วย 'ติดตามเพื่อรับความรู้ใหม่ทุกวัน! #Shorts'",
  "hashtags_th":    ["shorts", "อีก 7 แท็กไทยที่คนค้นจริง"],
  "music_mood":     "หนึ่งคำจาก: mysterious|dramatic|upbeat|melancholic|epic|peaceful|tense|inspiring",
  "thumbnail_keyword": "english pexels photo search term",
  "thumbnail_prompt":  "english AI image prompt — cinematic, ultra realistic, high contrast, one hero subject filling the frame, no text"
}
</output_schema>

<emphasis_rules>
emphasis = คำในประโยคที่จะถูกไฮไลต์ด้วยกล่องสีบนซับ คนเลื่อนผ่านแบบปิดเสียง
ต้องกวาดตาเห็นคำพวกนี้แล้วเข้าใจว่าคลิปพูดเรื่องอะไร

ใส่ 0-2 คำต่อประโยค ไม่ต้องมีทุกประโยค เลือกเฉพาะ:
- ปี ตัวเลข จำนวน (เช่น "สองพันยี่สิบสาม", "เก้าล้านคน")
- ชื่อคน ชื่อองค์กร ชื่อสถานที่
- คำที่เป็นจุดหักมุมของประโยคนั้น

ต้องเป็นข้อความที่ปรากฏใน text_th ของประโยคนั้นแบบตรงตัวเป๊ะๆ
ห้ามไฮไลต์คำเชื่อมหรือคำทั่วไป (ที่ ของ แล้ว มัน เป็น ก็ นะ)
ประโยคไหนไม่มีคำที่คู่ควร ใส่ array ว่าง
</emphasis_rules>

<ai_prompt_rules>
ใส่ ai_prompt ให้ 4-5 ประโยค ที่เหลือเว้นเป็น ""
เลือกประโยคที่ stock footage หาไม่ได้จริงๆ เรียงตามความสำคัญ:
- ประโยค ORIGIN ทุกประโยคที่เป็นเหตุการณ์เฉพาะเจาะจงในอดีต (คนนี้ ปีนี้ ที่นี่)
  stock ไม่มีทางมีภาพยุคนั้น มันจะให้ภาพปัจจุบันมาแทนแล้วผิดยุคทันที
- ประโยค REVEAL ที่เป็นจุดเฉลย
- ประโยคที่พูดถึงคน องค์กร หรือของเฉพาะเจาะจงที่หาคลิปจริงไม่ได้
ai_prompt เขียนเป็นภาษาอังกฤษ บรรยายภาพเดียวที่เห็นได้จริง:
ใคร ทำอะไร ที่ไหน ยุคไหน แสงแบบไหน
ตัวอย่างดี: "1886 Atlanta pharmacy, bearded pharmacist pouring dark syrup
into a glass, warm gaslight, wooden counter, close up"
ตัวอย่างแย่: "the origin of a famous drink" (นามธรรม ไม่มีภาพ)
ห้ามใส่ตัวหนังสือหรือโลโก้ในภาพ ห้ามใส่ชื่อคนที่ยังมีชีวิต
</ai_prompt_rules>

<field_rules>
- sentences: ต้องมี 11-12 ประโยค เรียงตามโครง HOOK/STAKE/ORIGIN/SPREAD/REVEAL
    (วัดจากของจริง: เสียงพากย์ใช้เวลาราว 4.4 วินาทีต่อประโยครวมจังหวะหยุด
     12 ประโยคคือราว 53 วินาที ซึ่งคือเป้าหมาย)
- entities: 2-4 รายการ เฉพาะคน องค์กร สถานที่ ผลิตภัณฑ์ ที่มีชื่อเฉพาะและมีหน้า Wikipedia
    ถ้า brief ไม่มีชื่อเฉพาะเลย ให้คืน array ว่าง ห้ามคิดชื่อขึ้นมาเอง
- ทุกฟิลด์ภาษาไทยต้องเป็นอักษรไทยล้วน ห้ามมีอักษรจีน ญี่ปุ่น เกาหลี
- hook_th กับ thumb_text_th ต้องไม่ซ้ำข้อความกัน
</field_rules>"""


def generate_explainer_script(brief, used_titles: list = None) -> dict:
    """Turn a researched Brief into a Thai-only explainer script.

    `brief` is a src.research.Brief. Everything factual in the output
    traces back to it -- the model's job here is delivery, not recall.
    """
    avoid_block = ""
    if used_titles:
        avoid_block = ("\n<already_published>\n"
                       + "\n".join(f"- {t}" for t in used_titles[-40:])
                       + "\n</already_published>\n")

    prompt = (
        "<research_brief>\n" + brief.as_prompt_block() + "\n</research_brief>\n"
        + avoid_block +
        "\nเขียนสคริปต์คลิปสั้น 45-60 วินาที จาก <research_brief> ข้างบน\n"
        "ลำดับการทำงาน:\n"
        "1. อ่าน brief ให้ครบก่อน แล้วเลือกว่าจะเก็บ 'จุดเฉลยท้ายคลิป' ไว้เฉลยประโยคไหน\n"
        "2. เขียน hook ที่ตั้งคำถามซึ่งจะถูกเฉลยด้วยจุดนั้นพอดี\n"
        "3. เขียนประโยคที่เหลือให้เดินจากจุดเริ่มต้นไปหาจุดเฉลย\n"
        "4. ตรวจทุกประโยคอีกรอบว่ามีข้อเท็จจริงที่ไม่ได้อยู่ใน brief หลุดเข้ามาไหม ถ้ามีให้ตัดออก\n\n"
        + _EXPLAINER_SCHEMA
    )

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT_EXPLAINER},
        {"role": "user",   "content": prompt},
    ]

    # flash first: this call has to hold a 6-field brief, a 12-sentence
    # structure and a no-invention rule at the same time, and lite drifts on
    # the sourcing rule first. But flash's free tier is 20 requests a day
    # for the whole project, so lite has to be able to take over -- a weaker
    # script beats no video.
    #
    # _call_gemini directly rather than _llm_call: the latter swallows a
    # Gemini failure and answers from Groq, which would make the lite
    # attempt unreachable. Groq stays as the final fallback below.
    raw = None
    if _GEMINI_AVAILABLE:
        for model in ("gemini-2.5-flash", "gemini-2.5-flash-lite"):
            try:
                raw = _call_gemini(messages, max_tokens=12000, model=model,
                                   thinking_budget=2048)
                break
            except Exception as e:
                print(f"[generator] {model} failed: {type(e).__name__}: "
                      f"{str(e)[:140]}")
    if raw is None:
        print("[generator] falling back to Groq for the script")
        raw = _call_groq(messages, max_tokens=6000)

    data = _parse_json(raw)

    sentences = data.get("sentences", [])
    data["script_th"] = " ".join(s.get("text_th", "") for s in sentences)
    data["script_en"] = ""      # TH-only format; kept for downstream compat
    data["keywords"]  = [{"specific": s.get("keyword", ""),
                          "fallback": s.get("fallback", "")}
                         for s in sentences]

    for key in ("title_th", "hook_th", "loop_th", "cta_th", "thumb_text_th",
                "script_th"):
        if data.get(key):
            data[key] = _clean_thai(data[key])
    for s in sentences:
        s["text_th"] = _clean_thai(s.get("text_th", ""))

    # Flattened for the subtitle builder, which matches tokens against the
    # whole set rather than per sentence -- a Thai token that collides
    # across two sentences is a highlight in both, which is harmless.
    data["emphasis"] = {
        _clean_thai(phrase)
        for s in sentences
        for phrase in (s.get("emphasis") or [])
        if phrase and len(_clean_thai(phrase)) >= 2
    }

    data["category"]     = brief.topic
    data["brief_source"] = brief.source
    data["research_raw"] = brief.raw
    return data


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
        " Pick a genuinely surprising or little-known fact.\n"
        " RETENTION LOOP: hook_en must tease something the viewer does NOT yet know. "
        "Withhold ONE concrete detail it teases and reveal that detail ONLY in the final sentence, "
        "so a rewatch pays off and the ending loops back to the opening question.\n\n"
        "Return ONLY a valid JSON object with these keys:\n\n"
        "- title_en: curiosity-gap English title (max 60 chars)\n"
        "- title_th: Thai title — natural spoken Thai, NOT word-for-word translation (max 50 chars, Thai chars only)\n"
        "- hook_en: 2-5 word ON-SCREEN hook for the first 2 seconds — the SPECIFIC curiosity gap of THIS fact, present tense + active verb. NEVER generic like 'DID YOU KNOW'. Max 18 chars. e.g. 'HONEY NEVER ROTS'\n"
        "- hook_th: same idea, punchy spoken Thai, max 16 Thai chars (Thai chars only)\n"
        "- loop_en: 2-6 word closing line shown at the very end that makes the viewer want to REWATCH from the start (callback to the hook's open question). e.g. 'NOW WATCH THE START'\n"
        "- loop_th: same idea in spoken Thai (Thai chars only)\n"
        "- cta_en: a 3-6 word comment-bait QUESTION for the end card + seed comment. e.g. 'Would you eat it?'\n"
        "- cta_th: same question in spoken Thai (Thai chars only)\n"
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
        "- thumbnail_prompt: vivid AI image prompt — cinematic, dramatic, ultra-realistic. "
        "When the fact involves a person or animal, make the SUBJECT FILL THE FRAME with a strong "
        "emotional facial expression (shock, awe, fear) facing the camera — expressive faces lift CTR. "
        "Otherwise: one bold high-contrast hero subject, no clutter.\n"
        + entity_clause + "\n\n"
        'Example: {"title_en":"Why Honey Never Expires","title_th":"ทำไมน้ำผึ้งไม่มีวันหมดอายุ",'
        '"hook_en":"HONEY NEVER ROTS","hook_th":"น้ำผึ้งไม่มีวันเสีย",'
        '"loop_en":"NOW WATCH THE START","loop_th":"ย้อนดูตอนต้นสิ",'
        '"cta_en":"Would you eat it?","cta_th":"กล้ากินไหม?",'
        '"sentences":['
        '{"text_en":"Honey found in ancient Egyptian tombs is still edible after 3000 years.","text_th":"น้ำผึ้งที่พบในสุสานอียิปต์โบราณยังกินได้แม้ผ่านมา 3000 ปี","keyword":"ancient egypt tomb artifact closeup","fallback":"ancient egypt"},'
        '{"text_en":"Scientists tasted it. It was perfect.","text_th":"นักวิทยาศาสตร์ชิมดู มันยังสมบูรณ์แบบ","keyword":"scientist lab tasting sample microscope","fallback":"scientist lab"}'
        '],'
        '"music_mood":"mysterious","thumbnail_keyword":"golden honey jar macro"}'
    )

    raw = _llm_call([
        {"role": "system", "content": system},
        {"role": "user", "content": prompt},
    ], max_tokens=3000)

    data = _parse_json(raw)

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
    for k in ("hook_th", "loop_th", "cta_th"):
        if data.get(k):
            data[k] = _clean_thai(data[k])
    for s in data.get("sentences", []):
        s["text_th"] = _clean_thai(s.get("text_th", ""))
    data["category"] = category  # for series numbering downstream
    return data
