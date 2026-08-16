"""AI-generated B-roll for the moments stock footage cannot cover.

Pexels is fine for "person typing at night" and useless for "a pharmacist
in 1886 Atlanta pouring syrup into a glass" -- the search falls back to
something vaguely on-theme, and every video ends up looking like every
other video. For the two or three shots that actually carry the story
(the origin moment, the reveal), a generated image beats a generic clip.

The still is rendered to a slow Ken Burns push so it reads as footage
rather than a slideshow, then handed to the editor as an ordinary
video-only clip -- no special case anywhere downstream.

Free: Pollinations, same service the thumbnails already use.
"""

from __future__ import annotations

import os
import subprocess
import urllib.parse

import requests

W, H = 1080, 1920
FPS  = 30

# Rendered longer than any single segment so the editor never has to loop
# it, which on a still would read as a stutter.
CLIP_SECONDS = 8.0

_STYLE_SUFFIX = ("cinematic still, dramatic lighting, photorealistic, "
                 "sharp focus, rich colour, no text, no watermark, "
                 "9:16 vertical composition")


def _fetch_image(prompt: str, dst: str, seed: int) -> bool:
    url = ("https://image.pollinations.ai/prompt/"
           + urllib.parse.quote(f"{prompt}, {_STYLE_SUFFIX}")
           + f"?width={W}&height={H}&model=flux-pro&nologo=true&seed={seed}")
    try:
        r = requests.get(url, timeout=120)
    except Exception as e:
        print(f"    [ai-broll] fetch failed: {type(e).__name__}")
        return False
    if r.status_code != 200 or len(r.content) < 1000:
        print(f"    [ai-broll] fetch failed: HTTP {r.status_code}")
        return False
    with open(dst, "wb") as f:
        f.write(r.content)
    return True


def _ken_burns(image_path: str, out_path: str,
               duration: float = CLIP_SECONDS) -> bool:
    """Slow centre push over the still, rendered at 1080x1920.

    zoompan samples from the source at output resolution, so the input is
    upscaled 2x first -- driving it straight from a 1080-wide still makes
    the push visibly step between pixels.
    """
    frames = int(duration * FPS)
    vf = (
        f"scale={W * 2}:{H * 2},"
        f"zoompan=z='min(1+0.0009*on,1.18)':d={frames}:"
        f"x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':"
        f"s={W}x{H}:fps={FPS},"
        f"format=yuv420p"
    )
    r = subprocess.run(
        ["ffmpeg", "-y", "-loop", "1", "-i", image_path, "-t", str(duration),
         "-vf", vf, "-an", "-c:v", "libx264", "-preset", "fast", "-crf", "20",
         out_path],
        capture_output=True, text=True)
    if r.returncode != 0:
        print(f"    [ai-broll] ken burns failed: {(r.stderr or '')[-250:]}")
        return False
    return True


def make_clip(prompt: str, out_dir: str, tag: str, seed: int) -> str | None:
    """Generate one Ken Burns B-roll clip. None on any failure."""
    img  = os.path.join(out_dir, f"broll_{tag}.jpg")
    clip = os.path.join(out_dir, f"broll_{tag}.mp4")
    if not _fetch_image(prompt, img, seed):
        return None
    ok = _ken_burns(img, clip)
    if os.path.exists(img):
        os.remove(img)
    return clip if ok else None


def replace_with_ai_clips(clips: list[str], sentences: list[dict],
                          out_dir: str, timestamp: int) -> list[str]:
    """Swap stock clips for generated ones where a sentence asked for it.

    A sentence opts in by carrying a non-empty `ai_prompt`. Anything that
    fails to generate silently keeps its stock clip -- a plain video beats
    a missing one.
    """
    out = list(clips)
    for i, sentence in enumerate(sentences):
        if i >= len(out):
            break
        prompt = (sentence.get("ai_prompt") or "").strip()
        if not prompt:
            continue
        print(f"    [ai-broll] sentence {i}: {prompt[:60]}")
        clip = make_clip(prompt, out_dir, f"{timestamp}_{i}",
                         seed=(timestamp + i * 7919) % 99999)
        if clip:
            out[i] = clip
            print(f"    [ai-broll] sentence {i} → {os.path.basename(clip)}")
    return out
