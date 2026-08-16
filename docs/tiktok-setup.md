# TikTok upload — setup checklist

โค้ดฝั่งเราเสร็จหมดแล้ว (`src/tiktok_api.py`). ที่ค้างคือ config ใน
TikTok developer portal. เอกสารนี้คือลำดับที่ต้องทำ

## เลือก mode ก่อน — อันนี้คือจุดที่ติดมาตลอด

| | `inbox` (default) | `direct` |
|---|---|---|
| scope | `video.upload` | `video.publish` |
| ต้อง audit ไหม | **ไม่ต้อง** | ต้อง 2-4 สัปดาห์ หลายรอบ feedback |
| คลิปไปไหน | กล่อง inbox ใน TikTok app → กดโพสต์เอง | ขึ้นโปรไฟล์เลย |
| ก่อน audit ผ่าน | ใช้ได้เต็มที่ | บังคับเป็น `SELF_ONLY` = มีแต่เราเห็น ไร้ประโยชน์ |
| แรงคนต่อวัน | กด 1 ครั้ง | 0 |

**เริ่มที่ `inbox`** — ได้ใช้วันนี้ แลกกับกดยืนยันวันละครั้ง. ยื่น audit
คู่ขนานไปได้ ผ่านเมื่อไหร่ค่อยสลับเป็น `direct`

## ขั้นตอน

### 1. developers.tiktok.com — สร้าง app
- Login ด้วยบัญชี TikTok ของช่อง
- Manage apps → Create an app
- กรอกชื่อ app, คำอธิบาย, category

### 2. เพิ่ม product + scope
- เพิ่ม product **Content Posting API**
- เปิด scope: `user.info.basic` และ `video.upload`
- **อย่าเพิ่ง request `video.publish`** — ขอไปแล้วมันจะบังคับเข้า audit
  flow ทั้งที่ยังไม่พร้อม

### 3. ตั้ง redirect URI
ใส่ให้ตรงเป๊ะกับ `REDIRECT_URI` ใน `src/tiktok_api.py`:

```
http://localhost:8080/callback
```

### 4. copy key ลง .env
```
TIKTOK_CLIENT_KEY=...
TIKTOK_CLIENT_SECRET=...
```

### 5. login ครั้งแรก (เครื่องตัวเอง เท่านั้น)
```powershell
$env:PYTHONIOENCODING="utf-8"
python -m src.tiktok_api login
```
เบราว์เซอร์เปิด → กด Authorize → token เซฟลง `token_tiktok.json`

### 6. ทดสอบส่งจริง
```powershell
python -m src.tiktok_api upload "output\test_explainer_XXXX.mp4" "ทดสอบ"
```
ได้ `SEND_TO_USER_INBOX` = สำเร็จ → เปิด TikTok app → notifications → กดโพสต์

### 7. เปิดใน pipeline
ใส่ `TIKTOK_ENABLED=1` ใน `.env`

## ข้อควรรู้

- **login ต้องทำบนเครื่องตัวเอง** — OAuth ต้องเปิดเบราว์เซอร์ + localhost
  callback ทำบน GitHub Actions ไม่ได้. ทำครั้งเดียวแล้ว refresh token
  ต่ออายุเองอัตโนมัติ
- ถ้าจะให้ cloud run ต้องเอา `token_tiktok.json` ใส่เป็น GitHub secret
  แล้วเขียนลงไฟล์ตอน workflow รัน แบบเดียวกับ `YOUTUBE_TOKEN`
- unaudited client จำกัด 5 users/24 ชม. เรามีบัญชีเดียว ไม่ชน
- คลิป 1080x1920 H.264+AAC <60 วิ ผ่าน spec TikTok อยู่แล้ว

## ยื่น audit (ทำทีหลัง ถ้าอยากได้ direct)

ต้องเตรียม:
- คลิปวิดีโอสาธิต flow ทั้งหมดของ app
- หน้าจอที่โชว์ **username + avatar ของ creator ก่อนโพสต์ทุกครั้ง** —
  TikTok เช็คข้อนี้จริงจัง ตกเพราะข้อนี้บ่อยสุด
- URL privacy policy + terms of service ที่เข้าถึงได้จริง

ผ่านแล้ว: `TIKTOK_SCOPES="user.info.basic,video.publish"` +
`TIKTOK_MODE=direct` แล้ว login ใหม่รอบเดียว
