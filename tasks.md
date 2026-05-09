# Tasks — YouTube Shorts Automation

## 🔴 MVP (ต้องทำก่อน)
- [ ] Setup project structure + .env + requirements.txt
- [ ] เชื่อม Claude API สำหรับ generate fact/script (ภาษาอังกฤษ)
- [ ] เชื่อม Pexels API ดึง stock footage ตาม keyword
- [ ] ตัดวิดีโอให้เป็น vertical 9:16, < 60 วินาที ด้วย FFmpeg
- [ ] ใส่ text overlay (fact text / caption) บนวิดีโอ
- [ ] สร้าง TTS voiceover แล้ว merge เข้าวิดีโอ
- [ ] Save output .mp4 ลงโฟลเดอร์ output/
- [ ] ทดสอบ pipeline ตั้งแต่ต้นจนจบ ได้วิดีโอ 1 คลิป

## 🟡 Important (ทำหลัง MVP เสร็จ)
- [ ] เชื่อม YouTube Data API v3 — upload Shorts อัตโนมัติ
- [ ] เชื่อม TikTok API — upload อัตโนมัติ (หรือ fallback notify)
- [ ] Loop pipeline ให้สร้างวิดีโอได้หลายคลิปต่อวัน
- [ ] เชื่อม Notion API — log วิดีโอที่อัปแล้ว (ชื่อ, YouTube link, TikTok link, เวลาที่อัป)
- [ ] Notion dashboard แสดง task progress ของระบบ (ทำถึงขั้นไหนแล้ว)

## 🟢 Nice to have (ถ้ามีเวลา)
- [ ] Schedule post ช่วงเวลา engagement ดีที่สุด (วิเคราะห์ best time)
- [ ] Dashboard ดู stats (views, upload history)
- [ ] Auto-vary fact topics ให้ไม่ซ้ำ (track ที่ generate ไปแล้ว)
- [ ] Background music (royalty-free) ใส่เบาๆ

## ❌ Out of scope (ไม่ทำในโปรเจคนี้)
- Multi-language support (ทำแค่ภาษาอังกฤษ)
- หลายช่อง / หลาย niche พร้อมกัน (ทำทีหลังถ้าต้องการ)

---
_อัปเดตไฟล์นี้ทุกครั้งที่ task เสร็จ หรือมี task ใหม่เพิ่ม_
