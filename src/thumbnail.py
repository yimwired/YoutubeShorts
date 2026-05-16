import os
import requests
import subprocess
from PIL import Image, ImageDraw, ImageFilter, ImageFont

_BASE       = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FONT_IMPACT = "C:/Windows/Fonts/impact.ttf" if os.name == "nt" else os.path.join(_BASE, "Kanit-Bold.ttf")
FONT_BOLD   = "C:/Windows/Fonts/arialbd.ttf" if os.name == "nt" else "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
PEXELS_KEY  = os.getenv("PEXELS_API_KEY")

W, H = 1080, 1920


def _extract_from_clip(clip_path: str, output_path: str, pct: float = 0.25) -> str | None:
    """Extract a frame from a video clip at pct% of its duration."""
    try:
        probe = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", clip_path],
            capture_output=True, text=True)
        dur = float(probe.stdout.strip())
        t = max(0.5, dur * pct)
        subprocess.run([
            "ffmpeg", "-y", "-ss", str(t), "-i", clip_path, "-vframes", "1",
            "-vf", f"scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H}",
            output_path], check=True, capture_output=True)
        return output_path
    except Exception:
        return None


def _fetch_pollinations(prompt: str, output_path: str) -> str | None:
    """Generate image via Pollinations.ai (free, no key required)."""
    import urllib.parse
    p = (f"{prompt}, cinematic photography, dramatic lighting, "
         f"photorealistic, sharp focus, 9:16 vertical format")
    url = (f"https://image.pollinations.ai/prompt/{urllib.parse.quote(p)}"
           f"?width=1080&height=1920&model=flux-pro&nologo=true"
           f"&seed={hash(prompt) % 99999}")
    try:
        r = requests.get(url, timeout=90)
        if r.status_code == 200 and len(r.content) > 1000:
            with open(output_path, "wb") as f:
                f.write(r.content)
            return output_path
    except Exception:
        pass
    return None


def _fetch_pexels_photo(keyword: str, output_path: str) -> str | None:
    """Download a portrait Pexels photo matching keyword."""
    headers = {"Authorization": PEXELS_KEY}
    params  = {"query": keyword, "per_page": 5,
                "orientation": "portrait", "size": "large"}
    try:
        resp = requests.get("https://api.pexels.com/v1/search",
                            headers=headers, params=params, timeout=15)
        photos = resp.json().get("photos", [])
        if not photos:
            return None
        url = photos[0]["src"]["large2x"]
        r = requests.get(url, timeout=30)
        with open(output_path, "wb") as f:
            f.write(r.content)
        return output_path
    except Exception:
        return None


def _extract_frame(video_path: str, output_path: str, percent: float = 0.2) -> str:
    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", video_path],
        capture_output=True, text=True)
    t = float(probe.stdout.strip()) * percent
    subprocess.run([
        "ffmpeg", "-y", "-ss", str(t), "-i", video_path, "-vframes", "1",
        "-vf", f"scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H}",
        output_path], check=True, capture_output=True)
    return output_path


def _draw_text_outlined(draw, pos, text, font, fill,
                        stroke_color="black", stroke_w=8, anchor="mm"):
    draw.text(pos, text, font=font, fill=stroke_color, anchor=anchor,
              stroke_width=stroke_w, stroke_fill=stroke_color)
    draw.text(pos, text, font=font, fill=fill, anchor=anchor)


def _wrap_text(text, font, max_width):
    words = text.split()
    lines, line = [], ""
    dummy = ImageDraw.Draw(Image.new("RGB", (1, 1)))
    for word in words:
        test = (line + " " + word).strip()
        if dummy.textlength(test, font=font) <= max_width:
            line = test
        else:
            if line: lines.append(line)
            line = word
    if line: lines.append(line)
    return lines


def _build_base(img: Image.Image, title: str) -> Image.Image:
    """Apply overlay + title text. Returns RGBA image."""
    img = img.filter(ImageFilter.GaussianBlur(radius=3))

    overlay = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    ov = ImageDraw.Draw(overlay)
    for y in range(H):
        if y < H * 0.35:
            alpha = int(160 * (1 - y / (H * 0.35)))
        elif y > H * 0.55:
            alpha = int(200 * ((y - H * 0.55) / (H * 0.45)))
        else:
            alpha = 40
        ov.line([(0, y), (W, y)], fill=(0, 0, 0, alpha))

    img = Image.alpha_composite(img.convert("RGBA"), overlay)
    draw = ImageDraw.Draw(img)

    # Hook badge
    hook_font = ImageFont.truetype(FONT_BOLD, 58)
    hook = "DID YOU KNOW?"
    hw = int(draw.textlength(hook, font=hook_font))
    pad = 28
    bx0, by0 = W // 2 - hw // 2 - pad, 70
    draw.rounded_rectangle([(bx0, by0), (W // 2 + hw // 2 + pad, by0 + 80)],
                            radius=40, fill=(255, 210, 0))
    draw.text((W // 2, by0 + 40), hook, font=hook_font, fill="#111111", anchor="mm")

    # Title
    font_size = 160 if len(title) <= 12 else 130 if len(title) <= 20 else 105
    title_font = ImageFont.truetype(FONT_IMPACT, font_size)
    line_h = int(font_size * 1.15)
    lines = _wrap_text(title.upper(), title_font, W - 60)
    start_y = H // 2 - len(lines) * line_h // 2 + 60
    COLORS = ["#FFFFFF", "#FFE000", "#FF6B35", "#FFFFFF"]
    for i, line in enumerate(lines):
        color = COLORS[i % len(COLORS)]
        _draw_text_outlined(draw, (W // 2, start_y + i * line_h), line,
                            title_font, fill=color, stroke_w=10, anchor="mm")

    # CTA
    cta_font = ImageFont.truetype(FONT_BOLD, 52)
    _draw_text_outlined(draw, (W // 2, H - 140), "Watch to the end!",
                        cta_font, fill="white", stroke_w=4, anchor="mm")

    return img


def create_thumbnail(video_path: str, title: str, output_path: str,
                     thai_ver: bool = False,
                     photo_keyword: str = None,
                     ai_prompt: str = None,
                     clips: list = None) -> str:

    # ── Background priority: Pollinations AI → clip frame → Pexels → video frame → dark ──
    bg_path = output_path.replace(".jpg", "_bg.jpg")
    got_bg = False

    # 1. Pollinations AI — prompt is topic-specific, most relevant for thumbnail
    if ai_prompt:
        print(f"    [Thumbnail] Generating AI image (flux-pro)...")
        got_bg = bool(_fetch_pollinations(ai_prompt, bg_path))
        if got_bg:
            print(f"    [Thumbnail] AI image OK")

    # 2. Clip frame — actual footage, fallback if Pollinations fails
    if not got_bg and clips:
        for clip in clips:
            if clip and os.path.exists(clip):
                got_bg = bool(_extract_from_clip(clip, bg_path))
                if got_bg:
                    print(f"    [Thumbnail] Using footage frame")
                    break

    # 3. Pexels photo
    if not got_bg and photo_keyword:
        got_bg = bool(_fetch_pexels_photo(photo_keyword, bg_path))

    # 4. Video frame (post-render fallback)
    if not got_bg and video_path and os.path.exists(video_path):
        _extract_frame(video_path, bg_path)
        got_bg = True

    # 5. Dark background
    if not got_bg:
        Image.new("RGB", (W, H), (15, 15, 15)).save(bg_path)

    img = Image.open(bg_path).convert("RGB").resize((W, H))
    os.remove(bg_path)

    base = _build_base(img, title)   # RGBA

    # ── THAI Ver badge (center, prominent) ──────────────────────────
    if thai_ver:
        draw = ImageDraw.Draw(base)
        badge_font = ImageFont.truetype(FONT_IMPACT, 72)
        badge_text = "THAI Ver"
        bw = int(draw.textlength(badge_text, font=badge_font))
        pad_x, pad_y = 30, 18
        cx = W // 2
        cy = int(H * 0.62)          # just below center title area
        rx0 = cx - bw // 2 - pad_x
        ry0 = cy - 44 - pad_y
        rx1 = cx + bw // 2 + pad_x
        ry1 = cy + 44 + pad_y
        draw.rounded_rectangle([(rx0, ry0), (rx1, ry1)],
                                radius=20, fill=(220, 40, 40))
        draw.text((cx, cy), badge_text, font=badge_font,
                  fill="white", anchor="mm",
                  stroke_width=3, stroke_fill="#800000")

    base.convert("RGB").save(output_path, "JPEG", quality=95)
    return output_path
