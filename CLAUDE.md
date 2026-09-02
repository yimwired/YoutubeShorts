# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Purpose

Automated YouTube Shorts pipeline — research + script + footage + voice + subtitle + thumbnail → scheduled upload.

**ตั้งแต่ 2026-08-16: วันละ 1 คลิป ภาษาไทยอย่างเดียว publish 12:00 Bangkok.**
`POST_HOURS = [12]`, `SLOT_STYLES = {12: "explainer"}` ใน `generate_batch.py`.

เหตุผลที่เปลี่ยนจาก 3 slot × EN+TH pair (analytics 60 วัน ก่อนเปลี่ยน):

| | median views | median retention |
|---|---|---|
| TH | 196 | 59.7% |
| EN | 66 | 33.4% |

slot 08/12/19 median = 132 / 98 / 104 → เวลาลงไม่ใช่ตัวแปร. AVD 23 วิ, 82 subs
และ 5 comments จาก 70k views ใน 60 วัน → คุณภาพต่อคลิปคือคอขวด ไม่ใช่จำนวน.
งบ render ทั้งหมดเลยไปลงที่คลิปเดียวแทนหกคลิป.

**Format = explainer** — เล่า "ที่มาของสิ่งที่กำลังฮิต / สิ่งที่คนคิดว่ารู้แล้ว"
**9 ประโยค ยาว 38-45 วิ** โครง HOOK → PROOF → ORIGIN → TURN → REVEAL → LOOP.
แบรนด์บนจอ = `ที่มาของ · EP.n` ข้างโลโก้ (`series_state.json["explainer"]`).

เดิมคือ 12 ประโยค 45-60 วิ โครง HOOK → STAKE → ORIGIN → SPREAD → REVEAL.
retention curve ของ 90 วัน (ดู `src/analytics.py`) หลุด 40-50% ระหว่าง
ratio 10-25% ของคลิป = วินาทีที่ 6-15 ซึ่งคือช่วง STAKE พอดี —
STAKE คือประโยคที่ "บอกว่าเดี๋ยวจะเฉลย" ทั้งช่วง ไม่ให้ข้อมูลใหม่เลย
คนเลื่อนฟีดไม่รอ เลยตัดทิ้ง ประโยคที่ 2 ต้องให้ fact จริงทันที.

| | คลิป 58-63 วิ (ส.ค.) | เกณฑ์ที่ YouTube ดันต่อ |
|---|---|---|
| retention | 38-48% | 50% (คลิป 30-60 วิ) |
| ภาพเปลี่ยนทุก | 4.6 วิ | 1.5-2.5 วิ |

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
3. `src/generator.py:generate_explainer_script` — gemini-2.5-flash เขียนสคริปต์ไทย **9 ประโยค 78-95 คำ** **ห้ามใส่ fact ที่ไม่มีใน brief**
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

# Generate + upload จริง (default 1 คลิป)
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

- Vertical 9:16 (1080x1920), 38-45 วิ, H.264 + AAC
- **ส่งที่ -14 LUFS** (`LOUDNORM` ใน editor.py) — YouTube เล่นทุกคลิปที่ -14
  ดังกว่านั้นมันหรี่ให้ เบากว่านั้นมันไม่ดันขึ้น. เดิม `amix` ไม่ได้ตั้ง `normalize=0`
  เลยหารเสียงด้วยจำนวน input → ทุกคลิปออกที่ -22 LUFS = เบากว่าคลิปอื่นในฟีด 8 dB
- เพลงประกอบ normalize เป็น `MUSIC_LUFS = -30` แล้ว duck ด้วย `sidechaincompress`
  ห้ามกลับไปใช้ `volume=` คูณตรงๆ — เพลงในคลังมาสเตอร์มาที่ -14.2 ถึง -10.7 LUFS
  คูณค่าเดียวกันจึงได้ระดับเตียงไม่เท่ากันทุกคลิป
- **ห้ามใส่ cap ที่ตัดเสียงพากย์กลับมา** — `editor.py` เดิม cap 62 วิ ทำให้
  คลิปที่ TTS ยาว 64 วิ ถูกตัดประโยคปิดทิ้ง และ 68 วิ render fail
  (cut point เกิน cap → `trim=duration` ติดลบ). `HARD_CAP = 100` เป็นกันบ้าเท่านั้น
  คุมความยาวที่สคริปต์ ไม่ใช่ที่ renderer. `SPEECH_TARGET = 52` แค่ log เตือน
- ไทยอย่างเดียว 1 ไฟล์ต่อวัน
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
