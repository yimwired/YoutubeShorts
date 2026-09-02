# SESSION_LOG — FactSnap Shorts

อ่านไฟล์นี้ก่อนเริ่ม session ใหม่ อัปเดตตอนจบ session ใต้ `## YYYY-MM-DD — Session N`.
สถาปัตยกรรมและกฎถาวรอยู่ใน `CLAUDE.md` ไม่ใช่ที่นี่ — ที่นี่เก็บว่า *ทำอะไรไปแล้ว* กับ *ค้างอะไร*.

---

## 2026-09-02 — Session 1 (overnight)

เริ่มจาก 3 อาการ: ช่องเงียบไป 3 วัน, บางคลิปตัดจบกลางคัน, ยอดวิวชนเพดาน.

### สภาพที่เจอ

**Pipeline ตายมา 3 วัน** — `daily.yml` fail 08-31, 09-01, 09-02 ติดกัน
สองสาเหตุคนละเรื่อง:
- 08-31 / 09-01: `Value -3.295 for parameter 'duration' out of range` —
  `editor.py` cap ที่ 62 วิ แต่ cut point จาก TTS อยู่เลย 62 → `trim` ติดลบ
- 09-02: `apt-get install ffmpeg` 404 บน libssh-gcrypt-4 (index ของ runner เก่า)

**คลิปตัดจบกลางคัน** = cap เดียวกัน. KFC (08-29) TTS ยาว 64.0 วิ ถูก render
แค่ 62 → ประโยคปิดหายไป 2 วิ. 09-01 TTS 68.2 วิ เลยพังทั้ง render.

**ตัวเลข 90 วัน** (`src/analytics.py`, ดึงผ่าน YouTube Analytics API):

| | ค่า |
|---|---|
| views | 99,695 |
| subs | +121 / -24 → 0.12% ของ views (เกณฑ์ 0.3-1%) |
| likes | 1,076 (1.08%) |
| **comments** | **8** |
| shares | 8 |
| traffic | 97% Shorts feed |
| views ต่อคลิป | 200-1,300 ทุกคลิป ไม่มีตัวไหนหลุดเพดาน |

**retention curve** (ทุกคลิปหน้าตาเหมือนกัน):
```
  5%  1.10   ████████████████████████████████████████████
 10%  1.00   ████████████████████████████████████████
 15%  0.70   ███████████████████████████
 25%  0.47   ██████████████████
 50%  0.28   ███████████
100%  0.18   ███████
```
หลุด 40-50% ระหว่าง ratio 10-25% = **วินาทีที่ 6-15** แล้วแบนราบ.
ตรงนั้นคือช่วง STAKE ของโครงเดิม ซึ่งเป็นประโยคที่ "บอกว่าเดี๋ยวจะเฉลย" ล้วนๆ

**seed comment ไม่เคยโพสต์สำเร็จเลยสักครั้ง** — `uploader.py` ยิงคอมเมนต์
ทันทีหลัง insert ตอนคลิปยัง private (รอ `publish_at`) YouTube ตอบ 403 ทุกครั้ง
error ถูก catch แล้ว log เฉยๆ เลยไม่มีใครเห็น. ฟีเจอร์ที่ตั้งใจแก้จุดอ่อน
ที่สุดของช่อง ตายมาตั้งแต่วันแรก = ที่มาของ 8 คอมเมนต์

### ที่แก้ไป

| commit | เรื่อง |
|---|---|
| `6e467910` | render เสียงพากย์เต็ม + ซอย shot + loop กลับต้นคลิป + seed_comments.py |
| `6d891310` | `apt-get update` ก่อน install + retry generate 1 ครั้ง |
| `5ec6b6ed` | series badge `ที่มาของ · EP.n` + backup cron 11:00 + ซับกว้างขึ้น |
| `f10977a5` | hook frame ต้องเป็นภาพ generate เสมอ |

รายละเอียดที่ไม่ได้อยู่ใน commit message:

- **ความยาว** 12 ประโยค 60 วิ → **9 ประโยค 38-45 วิ**. ตัดช่วง STAKE ทิ้งทั้งช่วง
  ประโยคที่ 2 ต้องให้ fact จริง ห้ามเกริ่น (`retention_law` ใน system prompt)
- **cut rate** 1 shot/ประโยค (4.6 วิ) → 18 shot (2.3 วิ). `_plan_shots()` ซอย
  segment ที่ยาวเกิน `MIN_SHOT` เป็น sub-shot ที่กรอบภาพคนละตำแหน่ง
  (`_SHOT_FRAMING`) + ไหลช้า 6% ระหว่าง shot
- **loop** shot สุดท้ายวนกลับ clip แรก → เฟรมจบต่อเฟรมแรก คนดูซ้ำโดยไม่ทันคิด
- **comment bait** `cta_th` ต้องเป็น 1 ใน 4 แบบ (เลือกข้าง / ทายตัวเลข /
  ของตัวเอง / แท็กเพื่อน) ห้าม "คิดยังไง" "ชอบไหม"
- **entity overlay** `_is_usable()` กรองด้วยสัดส่วนภาพ + ความสว่าง
  ("Roblox" เคยได้ภาพคำสั่งศาลตุรกี, "Knowledge Revolution" ได้ infographic มั่ว)
  prompt จำกัดเหลือเฉพาะ "คน" ที่มีหน้า Wikipedia
- **Latin ในซับ** `src/thai_text.py` รวมที่เดียว — commit `a268413` แก้เฉพาะ title
  ซับที่ burn ลงคลิปยังลบ Latin อยู่ ("KFC" หายจากซับ)
- **hashtag** ย้ายจาก title ไป description (Shorts player ตัด title ที่ ~40 ตัว)
- **tags** เลิกยัด `facts` / `didyouknow` ลงคลิปไทย

### ผลตรวจ

- smoke test 6/6 ผ่าน (`scratchpad/render_smoke.py`) รวมเคสที่ทำ pipeline พังจริง
- test render เต็ม: 9 ประโยค → TTS 42.5 วิ → คลิป 42.9 วิ ไม่มีตัดทิ้ง
- GH Actions dispatch 09-02: GoPro, TTS 35.5 วิ, คลิป 36 วิ, upload สำเร็จ
  (attempt แรกโดน Gemini 429 → retry ที่เพิ่งใส่ช่วยไว้พอดี)
  https://youtu.be/dMkf8T2M1EM publish 12:00 วันนี้

### ค้างไว้ / รอดูผล

1. **รอ retention ของคลิปสั้น** — ต้องรอ 48 ชม. ให้ Analytics ingest
   ดูว่า 36-42 วิ + cut ทุก 2.3 วิ ดัน retention ข้าม 50% ได้ไหม
   (ถ้าไม่ถึง ลองลดเหลือ 30-33 วิ ซึ่งเกณฑ์คือ 65%)
2. **รอคอมเมนต์แรก** — `reply.yml` 13:00 BKK จะยิง `seed_comments.py --live`
   ถ้ายังไม่ขึ้น เช็ค `seed_comment_at` ใน job json
3. **thumbnail ยังไม่แตะ** — ผลต่อ Shorts feed น้อย (feed ไม่โชว์ปก)
   แต่มีผลกับ search/related ซึ่งคือ 2.4% ของ traffic
4. **`swap_thumbnails.py` ยังใช้ `RETENTION_FLOOR = 45.0`** — คลิปสั้นลงแล้ว
   retention ควรสูงขึ้น ถ้าเกิน 45 หมดทุกคลิป A/B swap จะไม่เคยทำงาน
   ค่อยปรับหลังมีข้อมูล 1 สัปดาห์
5. **`scheduler.py` ยัง dead code** — อ้าง `generate_one_pair` ที่ลบไปแล้ว
6. **ไฟล์ค้างใน repo root** — `retry_pair3.*`, `tmp_develian/`, PNG ของ Gemini,
   `token_youtube.json.bak*` ยังไม่ได้เก็บกวาด
