# Project Brief — YouTube Shorts Automation
_สร้างเมื่อ: 2026-05-08_

**Goal:** ระบบ post YouTube Shorts + TikTok อัตโนมัติ ไม่ต้อง manual
**User:** Film ใช้คนเดียว
**Why:** ไม่มีเวลาทำ manual อยากให้รันเองได้ เปิดคอมทิ้งไว้แล้วรอ
**Workflow:** AI generate fact → ดึง stock footage → ตัด + ใส่ text + TTS → save → schedule → upload YouTube + TikTok
**Stack:** ยังไม่ lock — propose Python + FFmpeg + Claude API + Pexels API
**Domain rules:** Facts niche, ภาษาอังกฤษ, vertical 9:16, < 60 วินาที
**Done means:** รันทั้ง pipeline ได้โดยไม่ต้อง touch อะไรเลย วิดีโอขึ้นเองอัตโนมัติ
**Quality bar:** Production-ready ดูดี เหมาะใช้ระยะยาว
**Edge cases:** วิดีโอต้องสั้น, ภาษาอังกฤษเท่านั้น
**MVP:** สร้างวิดีโอก่อน (generate → ตัด → ใส่ text → save)
**Nice-to-have:** Schedule post เวลา engagement ดีที่สุด, dashboard stats, auto-vary topics
**Deadline:** ไม่มี ค่อยๆ ทำได้
**Known problems:** Raw video หายาก → แก้ด้วย Pexels/Pixabay API แทน
**Failure plan:** ถ้า TikTok API ไม่ได้ → ระบบ prepare + แจ้ง Film ให้กด post เอง โอเค
**Output format:** Save .mp4 ในโฟลเดอร์ output/ + upload ขึ้นแพลตฟอร์มโดยตรง
