# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Purpose

Automated YouTube Shorts pipeline — generate fact/script + footage + voice + subtitle + thumbnail → schedule upload. **3 content slots ต่อวัน (Bangkok time):**

| Slot | Time | Style | Voice (EN/TH) | System prompt |
|---|---|---|---|---|
| Morning | 08:00 | `trending` | EN AnaNeural / TH PremwadeeNeural (edge-tts) | viral facts |
| Noon | 12:00 | `chaos` | EN AnaNeural +15% / TH gTTS | brain rot, reaction words |
| Evening | 19:00 | `narrative` | EN calm / TH gTTS | จิตวิทยา/ธรรมชาติสอนใจ (documentary) |

`SLOT_STYLES = {8: "trending", 12: "chaos", 19: "narrative"}` ใน `generate_batch.py`.

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
- ข้อจำกัด cloud: ไม่มี `music/` (143MB, ไม่ commit เพราะ repo public) → BGM fallback SoundHelix; font EN = DejaVu Bold แทน Impact (ดู `FONT_EN` ใน editor.py)

## Pipeline (per video)

`main.py` → `make_video()` calls in order:
1. `src/generator.py:generate_fact_script` — Claude API, returns `sentences[]` (each w/ keyword) + `script_en`/`script_th`/`title_en`/`title_th`
2. `src/footage.py:fetch_multiple_clips` — Pexels API, 1 clip per sentence (5 clips/video), portrait-first, skip <5s, random top 3 by resolution
3. `src/tts.py:generate_voiceover` — edge-tts (trending TH = PremwadeeNeural, chaos EN = AnaNeural +15%, narrative TH = gTTS)
4. `src/captions.py` — faster-whisper, word-level timestamps
5. `src/editor.py:create_short` — ffmpeg ASS karaoke (2-pass), Kanit font, highlight สีเหลือง
6. `src/thumbnail.py` — Pollinations flux-pro → clip frame → Pexels → video frame (fallback chain)
7. `src/uploader.py:upload_youtube` — YouTube Data API v3 (called by scheduler)

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

# One-off test
python test_trending.py
python test_chaos.py

# Batch generate N pairs (default 3 = 6 videos)
python generate_batch.py [N]

# Long-running uploader (run separately, ทิ้งไว้)
python scheduler.py
```

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

- Vertical 9:16 (1080x1920), <60 sec, H.264 + AAC
- ภาษา EN + TH ต่อ video (pair = 2 ไฟล์ใช้ฉาก/audio คนละชุดอิสระ)
- Font EN = Impact (bundled `Kanit-Bold.ttf` for TH)
- ห้าม hardcode API key

## What NOT to Do

- อย่าเปลี่ยน Whisper text → subtitle text (มันเพี้ยน — ใช้ script text, Whisper timing เท่านั้น)
- อย่าลบ `_subs_from_sentences()` fallback — trending TH กลับมาใช้เมื่อ TTS boundary coverage <70%
- อย่า refactor `SLOT_STYLES` map ก่อนถาม — workflow + scheduler ผูกกับ hour key
- อย่ารวม EN+TH เป็นวิดีโอเดียว — pair = 2 ไฟล์อิสระ ใช้คนละ voiceover/clip/thumbnail
- `project-brief.md` = historical artifact (2026-05-08), ไม่ใช่ current state — ใช้ไฟล์นี้แทน
