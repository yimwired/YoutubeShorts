# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Purpose

Automated YouTube Shorts pipeline — research + script + footage + voice + subtitle + thumbnail → scheduled upload.

**ตั้งแต่ 2026-09-08: วันละ 2 คลิป ภาษาไทยอย่างเดียว**
`POST_HOURS = [8, 12]`, `SLOT_STYLES = {8: "human", 12: "explainer"}`
ใน `generate_batch.py` — รันรอบเดียว 06:00 ได้ทั้งคู่.

ก่อนหน้านั้น 08-16 → 09-07 คือ explainer คลิปเดียว 12:00.
เหตุผลที่เปลี่ยนจาก 3 slot × EN+TH pair (analytics 60 วัน ก่อนเปลี่ยน):

| | median views | median retention |
|---|---|---|
| TH | 196 | 59.7% |
| EN | 66 | 33.4% |

slot 08/12/19 median = 132 / 98 / 104 → เวลาลงไม่ใช่ตัวแปร. AVD 23 วิ, 82 subs
และ 5 comments จาก 70k views ใน 60 วัน → คุณภาพต่อคลิปคือคอขวด ไม่ใช่จำนวน.
งบ render ทั้งหมดเลยไปลงที่คลิปเดียวแทนหกคลิป.

### Format 12:00 = `explainer`

เล่า "ที่มาของสิ่งที่กำลังฮิต / สิ่งที่คนคิดว่ารู้แล้ว"
**6 ประโยค ยาว 27-34 วิ** โครง HOOK → PROOF → ORIGIN → TURN → REVEAL → LOOP.
แบรนด์บนจอ = `ที่มาของ · EP.n` ข้างโลโก้ (`series_state.json["explainer"]`).

### Format 08:00 = `human` (ตั้งแต่ 2026-09-08)

เล่า "ทำไมเราถึงทำสิ่งที่ทำอยู่ทุกวัน" — พฤติกรรมที่คนดูทำเอง + กลไกจริงจากงานวิจัย
**6 ประโยค ยาว 27-34 วิ** โครง MIRROR → PROOF → MECHANISM → WHY → REVEAL → LOOP
แบรนด์บนจอ = `ทำไมเราถึง · EP.n` (`series_state.json["human"]`).

ต่างจาก explainer 3 อย่าง เท่านั้น — ที่เหลือใช้ของเดียวกันหมด
(schema, word guard, subtitle style, TTS, editor):

| | explainer | human |
|---|---|---|
| research | `get_brief()` เทรนด์ก่อน แล้ว evergreen | `research_human()` ไม่มีเทรนด์ |
| ประโยคแรก | ข้อเท็จจริงที่ขัดกับที่คนคิดว่ารู้ | ภาพพฤติกรรมที่คนดูทำเอง |
| ภาพ | stock + AI still 3 ช็อต | **stock คนจริงล้วน ไม่มี AI เลย** |

**ห้ามเปิด AI b-roll ให้ `human`** — จุดขายของ format นี้คือฟุตเทจคนจริง
ใบหน้าที่ generate ขึ้นมาคือสิ่งเดียวที่มันแสดงไม่ได้.
`generate_human_script()` เคลียร์ `ai_prompt` ทุกประโยคด้วยโค้ด และ
`generate_one()` ข้าม `replace_with_ai_clips` เมื่อ style เป็น human.

keyword ของ human **ต้องมีคนอยู่ในเฟรมทุกอัน** (`visual_rules` ใน
`SYSTEM_PROMPT_HUMAN`) — "sleep cycle" หรือ "phone screen closeup" ใช้ไม่ได้
เพราะได้ภาพที่ไม่มีคน ซึ่งทำให้ format นี้เหมือน explainer ทันที

**กฎ footage อีกสองข้อ มี code guard คุม** (`find_abstract_keywords`):

1. **ถ่ายการกระทำ ไม่ใช่ความรู้สึก** — กล้องถ่าย "คิด" ไม่ได้ คลังฟุตเทจตอบคำ
   พวกนี้ด้วยภาพเดียวกันหมด คือคนนั่งนิ่งมองออกนอกเฟรม render แรกได้ภาพนั้น
   สามช็อตติดกันตรงกลางคลิป ซึ่งคือช่วงที่ retention หล่นอยู่แล้ว
   `_ABSTRACT_FOOTAGE` เก็บรายชื่อคำอารมณ์ และ `_LOOKING_STATE` จับรูปประโยค
   `looking <adj>` ทั้งหมด (เว้น `looking at/for/through/down/...` ที่เป็นการกระทำ)
   — ไล่ตั้งชื่ออารมณ์ทีละคำแพ้เสมอ เพราะอารมณ์มีมากกว่าที่ list จะเก็บไหว
2. **ทั้งคลิปต้องเป็นคนคนเดียวในที่เดียว** เปลี่ยนแค่การกระทำ หกภาพจากหกที่
   กับหกคนทำให้คลิปเป็นสไลด์โชว์ ไม่ใช่เรื่องของใครคนหนึ่ง

guard ใช้ retry budget เดียวกับ word count ผ่าน `extra_check` ของ
`_write_thai_script()` และตอนเลือก "อันที่ใกล้สุด" candidate ที่ผ่านกฎ format
ชนะ candidate ที่พลาดเสมอ ไม่ว่า word count จะใกล้เป้ากว่าแค่ไหน

**เสียงพากย์แยกน้ำเสียงต่อ format** (`_STYLES` ใน `tts_gemini.py`) —
voice ยังเป็น Charon ทั้งคู่ (คือ identity ของช่อง) แต่คำสั่งการอ่านต่างกัน:
explainer = เพื่อนเล่าเรื่องให้ฟัง · human = คนที่เพิ่งสังเกตเห็นแล้วหันมาบอก
คนข้างๆ พูดใกล้ตัว ประโยคแรกเหมือนจับได้ว่าอีกฝ่ายก็ทำ
`_SEP_RULE` (คำสั่งเรื่อง `???`) ต่อท้ายทั้งสองแบบ **ห้ามแก้** —
subtitle timing ทั้งเส้นพึ่งช่องเงียบพวกนั้น

**ที่ไม่ทำ และเหตุผล** — Film เสนอให้เอาคลิปคนจริงจากเน็ตมาพากย์ (แนวช่อง
HMONG SPEED) reupload ของคนอื่นคือ Content ID claim + copyright strike
สามครั้งใน 90 วัน = ช่องถูกลบ และ policy reused content ปิดประตู YPP ถาวร
ช่องยังไม่ monetize (155 subs, `isChannelMonetizationEnabled: false`)
ถ้าจะลอง format reupload จริงๆ ต้องเปิดช่องใหม่ ไม่ใช่บนช่องนี้

ประวัติการย่อ 3 รอบ:

| | ประโยค | ยาว | ผลที่วัดได้ |
|---|---|---|---|
| ถึง 08-15 | 12 | 45-60 วิ | retention 38-48% |
| 08-16 → 09-06 | 9 | 36-49 วิ | retention median 44 → 49.8%, views median 173 → 272 |
| ตั้งแต่ 09-07 | 6 | 27-34 วิ | — |

รอบ 12→9 ตัดช่วง STAKE ทิ้ง (ประโยคที่ "บอกว่าเดี๋ยวจะเฉลย" ไม่ให้ข้อมูลใหม่)
ประโยคที่ 2 ต้องให้ fact จริงทันที. รอบ 9→6 ตัด ORIGIN/TURN/REVEAL ตัวที่สอง
ของแต่ละช่วง ซึ่งเป็นการขยายความ ไม่ใช่การเดินเรื่อง — `audienceWatchRatio`
ของ 6 คลิปที่ใช้โครง 9 ประโยคยังหล่นจาก 1.06 เหลือ 0.66 ระหว่าง ratio 15-35%
ซึ่งตกอยู่ที่ประโยคพวกนั้นพอดี.

**ความยาวคุมที่จำนวนประโยค ไม่ใช่จำนวนคำ** — โมเดลนับคำไทยไม่ได้ (ไทยไม่มี
ช่องว่างคั่นคำ) สั่ง 58-72 คำสามรอบได้ 83 / 78 / 84 ทุกรอบ. จำนวนประโยค
บังคับได้จริงเพราะ `EXPLAINER_RESPONSE_SCHEMA` ตั้ง `minItems`/`maxItems`
และ decoder เป็นคนคุม. ประโยคหนึ่งของโมเดลยาวราว 11 คำ หกประโยค = ~66 คำ
= ~31 วิ.

`EXPLAINER_WORDS_MIN/MAX` ใน `generator.py` ยังนับคำไทยของทุก attempt
(pythainlp newmm) — ไม่ใช่เพื่อบังคับ แต่เพื่อจับกรณีที่โมเดลเขียนประโยคยาว
ผิดปกติ สคริปต์ที่นอกช่วงถูกส่งกลับไปเขียนใหม่พร้อมจำนวนคำจริง สูงสุด 3
attempt แล้วเลือกอันที่ใกล้ 65 คำที่สุด. โครง 9 ประโยคคุมด้วย prompt
อย่างเดียวและได้ 29-49 วิ จากสเปก 38-45 — สองคลิปที่ยาวสุดคือสองคลิปที่เข้า
Shorts feed น้อยที่สุด (SHORTS=14 เทียบกับ 515).

Styles เดิม (`trending` / `chaos` / `narrative`) ยังอยู่ใน `src/generator.py`
เพราะ `test_trending.py` / `test_chaos.py` / `test_narrative.py` ยังเรียกใช้ —
แต่ daily run ไม่แตะแล้ว.

## Two-Process Architecture

```
generate_batch.py  → queue/job_<ts>_<lang>.json + output/short_<ts>_<lang>.mp4
                     (1 pair = EN + TH version, แต่ละ slot)
                     upload YouTube ทันที + ตั้ง publish_at ฝั่ง YouTube server
```

`scheduler.py` = legacy, ไม่ต้องรันแล้ว — generate_batch upload เองจบในรอบเดียว.

**Full-cloud ตั้งแต่ 2026-06-10** — GitHub Actions เป็น primary runner, ไม่ต้องเปิดคอม:
- `daily.yml` cron `0 23 * * *` UTC = 06:00 Bangkok — generate + upload, แล้ว commit state กลับ repo (`queue/`, `output/thumb_*_b.jpg`, `topic_history.json`, `rate_usage.json`, `bucket_state.json`)
- `swap.yml` cron `0 19 * * *` UTC = 02:00 Bangkok — thumbnail A/B swap + analytics→Notion + prune thumb เก่า (`prune_thumbs.py`)
- Local Task Scheduler (`FactSnapBatch`, `FactSnapSwap`) ถูก disable แล้ว — ถ้าจะรัน batch local ต้อง `git pull` ก่อน (state อยู่ใน repo) และ re-enable task ไม่ได้ถ้า GH cron ยังเปิด (จะรันซ้ำซ้อน)
- ข้อจำกัด cloud: `music/` ไม่ขึ้น repo (144MB + repo public) → ใช้ `music/cloud/<mood>.mp3`
  ที่ commit ไว้ (เตรียมด้วย `tools/prepare_cloud_music.py` แล้ว `git add -f`)
  ถ้าไม่มีก็ fallback SoundHelix; font EN = DejaVu Bold แทน Impact (ดู `FONT_EN` ใน editor.py)

## Pipeline (per video)

`generate_batch.py:generate_one()` เรียงตามนี้:
1. `src/trends.py:get_trend_candidates` — Google Trends RSS (TH+US) + YouTube most-popular chart TH, กรอง noise regex ออก
2. `src/research.py:get_brief` — **Gemini + Google Search grounding** เลือกหัวข้อที่เล่า "ที่มา" ได้ แล้วขุด origin/spread/numbers/surprise. ไม่มีเทรนด์ผ่านเกณฑ์ → `research_evergreen()` จาก `EXPLAINER_CATEGORIES`. คืน `Brief` หรือ `None`
   · slot 08:00 ใช้ `research_human()` + `HUMAN_CATEGORIES` แทน — `Brief` ตัวเดียวกัน แต่ ORIGIN เก็บกลไก ไม่ใช่ปีก่อตั้ง
3. `src/generator.py:generate_explainer_script` / `generate_human_script` — gemini-2.5-flash เขียนสคริปต์ไทย **6 ประโยค** (schema บังคับ, word count นับด้วยโค้ด retry ได้ 3 ครั้ง) **ห้ามใส่ fact ที่ไม่มีใน brief**. ทั้งคู่เรียก `_write_thai_script()` ตัวเดียวกัน ต่างที่ system prompt
4. `src/footage.py:fetch_multiple_clips` — Pexels 1 clip ต่อประโยค
5. `src/tts.py:generate_voiceover` — Gemini TTS → edge-tts Premwadee → gTTS (ดูหัวข้อ Voice)
6. `main.py:_sync_th_subs` — silencedetect → TTS boundaries → Whisper → script split
7. `src/editor.py:create_short` — ffmpeg ASS karaoke 2-pass, Kanit, hook pop 0-2.6 วิ, reveal flash, end card, series badge
8. `src/thumbnail.py` — Pollinations flux-pro → clip frame → Pexels → video frame
9. `src/uploader.py:upload_youtube` — Data API v3 + `publish_at`
10. `seed_comments.py` (รันจาก `reply.yml` 13:00 BKK) — โพสต์คำถามชวนคอมเมนต์
    **หลัง**คลิป public แล้ว

**ห้ามรวม tools กับ `response_mime_type=application/json` ใน call เดียว** — Gemini
ตอบ 400 `Tool use with a response mime type: 'application/json' is unsupported`.
นี่คือเหตุผลที่ research (grounded, plain text) กับ script (JSON, ไม่มี tool) แยกกัน.

## Voice

`src/tts_gemini.py` เป็น path หลักของไทย — `gemini-3.1-flash-tts-preview` voice `Leda`,
**1 call ต่อคลิป** (ไม่ใช่ต่อประโยค) เพราะ preview model quota แคบและ 503 บ่อย.

per-sentence timing ได้จาก silencedetect หลัง render โดยแทรก `" ??? \n"` คั่นบรรทัด.
ตัวเลขที่วัดจริงจากสคริปต์ 10 บรรทัด:

| separator | ช่องว่างระหว่างบรรทัด | ความยาวรวม | gap ที่ detect ได้ |
|---|---|---|---|
| ไม่มี | 0.3-0.5 วิ ปนกับจังหวะกลางประโยค | 57 วิ | 27 (ต้องการ 9) |
| `...` | 2.2-2.7 วิ (dead air) | 59 วิ | 9 |
| `???` | 0.63-0.85 วิ | 44 วิ | 9 |

fallback: Gemini fail → edge-tts Premwadee per-sentence → gTTS. ปิด Gemini TTS
ด้วย `GEMINI_TTS_DISABLED=1`. เสียงพากย์ ~4.4 วิ/ประโยค รวมจังหวะหยุด.

## Thai Subtitle Logic (sensitive)

Trending TH uses **TTS boundary timing** if coverage ≥70%, else falls back to `_subs_from_sentences()`. Word-level timing via Whisper word_timestamps → find n-1 largest pauses as sentence boundaries (not linear division).

`_make_th_subs` (main.py) groups by:
- Space-based phrase split (Thai uses spaces as clause break)
- Merge chunks <4 chars with next
- Split >16 chars via pythainlp `word_tokenize` engine=newmm, group 3 words
- Group 3 words per ASS entry → subtitle stays on screen ~0.75s (not ~0.3s/word)

อย่า revert เป็น Whisper transcribe text — text เพี้ยน. Always: text จาก script, timing จาก Whisper/TTS.

## Commands

```powershell
$env:PYTHONIOENCODING="utf-8"

# Render 1 คลิปเต็ม ไม่ queue ไม่ upload — ใช้ตรวจ prompt/voice/subtitle
python test_explainer.py
python test_explainer.py --no-trends   # ข้ามเทรนด์ ใช้ evergreen bucket
python test_human.py                   # format เช้า (ไม่มี --no-trends, ไม่มีเทรนด์อยู่แล้ว)

# Generate + upload จริง (default = 1 คลิปต่อ slot ใน POST_HOURS = 2)
python generate_batch.py [N]

# Generate ครบทุกขั้นแต่ไม่แตะ YouTube/Notion
$env:DRY_RUN="1"; python generate_batch.py 1
```

`scheduler.py` = legacy ไม่ต้องรัน (ยังอ้าง `generate_one_pair` ที่ลบไปแล้ว).

## Env / Secrets

`.env` (local) + GitHub Actions secrets:
- `ANTHROPIC_API_KEY` (or `GROQ_API_KEY`) — Claude/Groq for generator
- `PEXELS_API_KEY`
- `YOUTUBE_TOKEN` + `YOUTUBE_CLIENT_SECRETS` — written to `token_youtube.json` + `client_secrets.json` by workflow
- `NOTION_TOKEN` + `NOTION_DATABASE_ID` — `src/notion_logger.py` logs scheduled + uploaded
- `DISCORD_WEBHOOK` — notify on completion

## State Files (gitignored, don't delete)

- `queue/job_*.json` — pending uploads, scheduler reads here
- `topic_history.json` — dedupe topic
- `rate_usage.json` — API rate tracking
- `token_youtube.json` — OAuth refresh token
- `scheduler.log`, `sched_combined.log`, `scheduler_err.log` — runtime logs
- `output/short_<ts>_<lang>.mp4` — final video, kept even after upload

## Constraints

- Vertical 9:16 (1080x1920), 27-34 วิ, H.264 + AAC
- **ส่งที่ -14 LUFS** (`LOUDNORM` ใน editor.py) — YouTube เล่นทุกคลิปที่ -14
  ดังกว่านั้นมันหรี่ให้ เบากว่านั้นมันไม่ดันขึ้น. เดิม `amix` ไม่ได้ตั้ง `normalize=0`
  เลยหารเสียงด้วยจำนวน input → ทุกคลิปออกที่ -22 LUFS = เบากว่าคลิปอื่นในฟีด 8 dB
- เพลงประกอบ normalize เป็น `MUSIC_LUFS = -30` แล้ว duck ด้วย `sidechaincompress`
  ห้ามกลับไปใช้ `volume=` คูณตรงๆ — เพลงในคลังมาสเตอร์มาที่ -14.2 ถึง -10.7 LUFS
  คูณค่าเดียวกันจึงได้ระดับเตียงไม่เท่ากันทุกคลิป
- **ห้ามใส่ cap ที่ตัดเสียงพากย์กลับมา** — `editor.py` เดิม cap 62 วิ ทำให้
  คลิปที่ TTS ยาว 64 วิ ถูกตัดประโยคปิดทิ้ง และ 68 วิ render fail
  (cut point เกิน cap → `trim=duration` ติดลบ). `HARD_CAP = 100` เป็นกันบ้าเท่านั้น
  คุมความยาวที่สคริปต์ ไม่ใช่ที่ renderer. `SPEECH_TARGET = 36` แค่ log เตือน
- ไทยอย่างเดียว 2 ไฟล์ต่อวัน (08:00 human, 12:00 explainer)
- Font ไทย = bundled `Kanit-Bold.ttf` (thumbnail ใช้ตัวนี้ด้วย — Impact ไม่มี glyph ไทย)
- ห้าม hardcode API key

## What NOT to Do

- อย่าเปลี่ยน Whisper text → subtitle text (มันเพี้ยน — ใช้ script text, Whisper timing เท่านั้น)
- อย่าลบ `_subs_from_sentences()` fallback — ใช้เมื่อ silencedetect กับ TTS boundary ไม่พอ
- **อย่าเปิด `title_card` / `outro_card` กลับมาสำหรับ explainer** — title card = ภาพนิ่งเงียบ 0.8 วิ ก่อน hook (ช่วงที่คนตัดสินใจปัด), outro card = จอดำ 1.5 วิ ที่ตัด loop กลับต้นคลิปทิ้ง
- อย่าให้ generator แต่ง fact เอง — ทุกอย่างต้องมาจาก `Brief` ที่ ground แล้ว เทรนด์ส่วนใหญ่เกิดหลัง training cutoff
- **อย่าโพสต์คอมเมนต์ตอน upload** — คลิปยัง private (รอ `publish_at`) YouTube ตอบ 403
  `insufficient permissions`. เดิม `uploader.py` ทำแบบนี้และ fail เงียบทุกครั้ง
  3 เดือน = 8 คอมเมนต์จาก 99k views. ตอนนี้เป็นหน้าที่ `seed_comments.py`
- อย่าลด cut rate กลับเป็น 1 shot ต่อประโยค — `_plan_shots()` ซอยให้ภาพเปลี่ยนทุก ~2.3 วิ
  และ shot สุดท้ายวนกลับ clip แรกเพื่อให้คลิปต่อกันเป็นวง
- อย่าใส่ entity overlay ของบริษัท/แบรนด์/สถานที่ — Wikipedia คืนโลโก้หรือภาพหน้าจอ
  ("Roblox" ได้ภาพคำสั่งศาลตุรกี) `_is_usable()` กรองด้วยสัดส่วนภาพ ใช้เฉพาะ "คน"
- `project-brief.md` = historical artifact (2026-05-08), ไม่ใช่ current state — ใช้ไฟล์นี้แทน
