# YouTube Shorts Automation

> ระบบ AI สร้างและ post วิดีโอ Facts อัตโนมัติขึ้น YouTube Shorts + TikTok

## What this is
ระบบ pipeline อัตโนมัติที่ใช้ AI generate fact/script, ดึง stock footage, ตัดวิดีโอ, ใส่ text overlay + voiceover แล้ว schedule post ขึ้น YouTube Shorts และ TikTok โดยไม่ต้อง manual

## Who uses it
Film ใช้คนเดียว — เปิดทิ้งไว้แล้วรอรับผล

## Stack & Tools
- Language: Python
- Video processing: FFmpeg, MoviePy
- AI / Script: Claude API (fact generation + script)
- Stock footage: Pexels API / Pixabay API
- TTS (voiceover): ElevenLabs หรือ gTTS (ภาษาอังกฤษ)
- Upload: YouTube Data API v3, TikTok API (fallback: manual notify)
- Scheduling: APScheduler หรือ cron-based
- Dashboard / Log: Notion API (track tasks + video upload history)

## How it works (Workflow)
1. AI generate interesting fact + script (ภาษาอังกฤษ)
2. ดึง stock footage จาก Pexels/Pixabay ที่ตรง topic
3. ตัดวิดีโอให้อยู่ในรูปแบบ Shorts (< 60 วินาที, vertical 9:16)
4. ใส่ text overlay (caption / fact text)
5. สร้าง TTS voiceover แล้ว merge เข้าวิดีโอ
6. Save .mp4 ลงโฟลเดอร์ output/
7. Schedule post ช่วงเวลา engagement ดีที่สุด
8. Upload ขึ้น YouTube Shorts + TikTok อัตโนมัติ
9. Log วิดีโอที่อัปแล้ว (ชื่อ, link, เวลา) ลง Notion database อัตโนมัติ

## Rules & Constraints
- วิดีโอต้องเป็น vertical 9:16, ความยาว < 60 วินาที
- ภาษาอังกฤษเท่านั้น
- เก็บไฟล์ .mp4 ทุกไฟล์ไว้ใน output/ เสมอ แม้ upload แล้ว
- ถ้า TikTok API ไม่ได้ → แจ้ง Film แล้วรอ manual post (ไม่ให้ระบบ crash)
- ห้าม hardcode API keys — ใช้ .env เสมอ
- niche: Facts (สิ่งที่คนขี้สงสัยอยากรู้)

## Done means
- [ ] ระบบ generate fact + script ได้อัตโนมัติ
- [ ] ดึง stock footage และตัดวิดีโอได้
- [ ] ใส่ text overlay + voiceover ได้
- [ ] Save .mp4 ลง output/ ได้
- [ ] Upload YouTube Shorts อัตโนมัติได้
- [ ] Upload TikTok อัตโนมัติได้ (หรือ notify ถ้า API ไม่ได้)
- [ ] Schedule post เวลา engagement ดีที่สุดได้
- [ ] Log วิดีโอที่อัปแล้วลง Notion (ชื่อ, link, เวลา) อัตโนมัติ
- [ ] รันทั้ง pipeline โดยไม่ต้อง touch อะไรเลย

## Known risks
- TikTok API มี restriction สูง อาจต้องใช้ fallback (notify + manual)
- Stock footage อาจไม่ตรง topic เสมอไป — ต้องมี fallback keyword search
- YouTube quota limit ต้องระวัง rate limiting

## MVP vs Later
MVP (ทำก่อน):
- Generate fact/script ด้วย AI
- ดึง stock footage
- ตัดวิดีโอ + ใส่ text + voiceover
- Save .mp4

Later (ทำทีหลัง):
- Upload YouTube + TikTok อัตโนมัติ
- Schedule optimal posting time
- Dashboard ดู stats

---
_Last updated: 2026-05-08_
