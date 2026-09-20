#!/usr/bin/env python3
"""
Channel Art — avatar and banner for a YouTube channel, rendered with ffmpeg + libass

Usage: python tools/make_channel_art.py --preset songsai --out channel-art/
       python tools/make_channel_art.py --preset laochonchop --out ../art/

Writes: <out>/avatar.png (800x800) and <out>/banner.png (2048x1152)

Text goes through libass, not Pillow: Thai needs mark-to-base shaping and Pillow is not
built against libraqm here, so tone marks land on the baseline instead of above the vowel.

Two sizing facts drive the whole design:

  The avatar is shown at 32px in the subscription feed. Nothing with more than about two
  Thai syllables survives that, so the avatars here are a single mark or word at enormous
  weight, not the channel name. The name is already written next to it in every surface
  that shows the avatar.

  The banner is cropped differently on every device. Only the centre 1235x338 of the
  2048x1152 is guaranteed visible -- TV shows everything, desktop a 2560x423 strip,
  phone 1546x423. Everything that has to be read lives inside the safe box; outside it
  is background that must survive being cut.

Requirements: ffmpeg with libass
"""

import argparse
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

AVATAR = 800
BANNER_W, BANNER_H = 2048, 1152
SAFE_W, SAFE_H = 1235, 338

FONT_NAME = "Kanit"
FONT_FILE = Path(__file__).resolve().parent.parent / "Kanit-Bold.ttf"

PRESETS = {
    # The shorts channel. A question mark carries at 32px where Thai type cannot, and it
    # says what the channel is without needing to be read as a word.
    "songsai": {
        "title": "ไม่เคยสงสัย",
        "tagline": "เรื่องที่อยู่ตรงหน้าทุกวัน แต่ไม่เคยถามว่าทำไม",
        "avatar_text": "?",
        "avatar_size": 560,
        "ink": "&H21C9FF",      # amber, BBGGRR
        "ground": "#12161C",
        # A single off-centre glow rather than a flat fill: flat dark reads as an empty
        # image at banner size, and a gradient is the cheapest thing that does not.
        "wash": "0.55",
    },
    # The folktale channel. Thai numerals and a single word read better here than a symbol
    # -- the audience is arriving from a search for a story title, not from a feed.
    "laochonchop": {
        "title": "เล่าจนจบ",
        "tagline": "ตำนานไทยที่คนรู้จักชื่อ แต่ไม่เคยได้ยินจนจบ",
        "avatar_text": "เล่า",
        "avatar_size": 300,
        "ink": "&H4DD2FF",      # warm gold, closer to leaf on the murals
        "ground": "#14100A",
        "wash": "0.45",
    },
}


def write_ass(lines: list[tuple[str, str]], styles: str, width: int, height: int,
              work: Path, name: str) -> Path:
    """lines: (style_name, text). Spacing stays 0 in every style -- see the note below."""
    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {width}
PlayResY: {height}
WrapStyle: 2
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
{styles}

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    body = "\n".join(f"Dialogue: 0,0:00:00.00,0:00:10.00,{style},,0,0,0,,{text}"
                     for style, text in lines)
    path = work / name
    path.write_text(header + body + "\n", encoding="utf-8")
    return path


# geq evaluates to whole 8-bit values, so a slow gradient steps rather than ramps and the
# steps read as concentric rings on a flat dark field. A few levels of noise before the
# text is drawn dithers the boundaries away; more than this and it looks like grain.
DITHER = "noise=alls=5:allf=t+u"


def render(filters: str, ass_path: Path, fonts_dir: Path, out: Path,
           width: int, height: int) -> None:
    # Relative names only: an absolute Windows path puts a drive-letter colon inside the
    # filter graph, where a colon separates options.
    graph = f"{filters},{DITHER},ass={ass_path.name}:fontsdir={fonts_dir.name}"
    command = ["ffmpeg", "-y", "-f", "lavfi", "-i", f"color=black:s={width}x{height}",
               "-vf", graph, "-frames:v", "1", str(out.resolve())]
    result = subprocess.run(command, capture_output=True, text=True, errors="replace",
                            cwd=ass_path.parent)
    if result.returncode != 0:
        detail = next((line for line in result.stderr.splitlines()
                       if "Error" in line or "Invalid" in line), result.stderr[-400:])
        sys.exit(f"ERROR: ffmpeg failed\n       {detail.strip()}")


def ink_centre(image: Path) -> tuple[float, float] | None:
    """Centre of the drawn glyphs, as a fraction of the frame.

    libass centres on the line box, which includes the font's ascent and descent whether
    or not the glyph uses them. A "?" has no descender, so line-box centring leaves it
    sitting high and the offset differs per glyph and per font -- measuring beats guessing,
    and an avatar is cropped to a circle where being off-centre is obvious.
    """
    try:
        from PIL import Image
    except ImportError:
        return None
    with Image.open(image) as frame:
        box = frame.convert("L").point(lambda v: 255 if v > 90 else 0).getbbox()
    if not box:
        return None
    left, top, right, bottom = box
    return ((left + right) / 2 / frame.width, (top + bottom) / 2 / frame.height)


def build_avatar(spec: dict, work: Path, fonts_dir: Path, out: Path) -> None:
    # Spacing 0: libass advances combining marks by the letter spacing too, which lifts
    # Thai tone marks off their base and drops them entirely. "ที่" becomes "ที".
    def styles_for(pos: tuple[float, float] | None) -> tuple[str, str]:
        text = spec["avatar_text"]
        if pos:
            # Shift by however far the measured centre sat from the middle.
            x = AVATAR / 2 + (AVATAR / 2 - pos[0] * AVATAR)
            y = AVATAR / 2 + (AVATAR / 2 - pos[1] * AVATAR)
            text = f"{{\\pos({x:.0f},{y:.0f})}}{text}"
        return (f"Style: Mark,{FONT_NAME},{spec['avatar_size']},{spec['ink']},{spec['ink']},"
                f"&H000000,&H000000,-1,0,0,0,100,100,0,0,1,0,0,5,0,0,0,1"), text

    # Pass one on a flat field, only to find where the glyph actually landed.
    styles, text = styles_for(None)
    probe_ass = write_ass([("Mark", text)], styles, AVATAR, AVATAR, work, "probe.ass")
    probe_png = work / "probe.png"
    render("format=rgb24", probe_ass, fonts_dir, probe_png, AVATAR, AVATAR)
    measured = ink_centre(probe_png)
    if measured:
        print(f"  glyph centre measured at {measured[0]:.3f}, {measured[1]:.3f} "
              f"— correcting to 0.500, 0.500")

    styles, text = styles_for(measured)
    ass_path = write_ass([("Mark", text)], styles, AVATAR, AVATAR, work, "avatar.ass")
    ground = spec["ground"].lstrip("#")
    # Radial falloff from upper left so the mark sits on light rather than on a flat field.
    filters = (f"format=rgb24,geq="
               f"r='0x{ground[0:2]}+{spec['wash']}*46*max(0,1-hypot(X-W*0.32,Y-H*0.3)/(W*0.78))':"
               f"g='0x{ground[2:4]}+{spec['wash']}*40*max(0,1-hypot(X-W*0.32,Y-H*0.3)/(W*0.78))':"
               f"b='0x{ground[4:6]}+{spec['wash']}*34*max(0,1-hypot(X-W*0.32,Y-H*0.3)/(W*0.78))'")
    render(filters, ass_path, fonts_dir, out, AVATAR, AVATAR)


def build_banner(spec: dict, work: Path, fonts_dir: Path, out: Path) -> None:
    # Alignment 5 centres on the frame; MarginV nudges the pair apart inside the safe box.
    title_v = int((BANNER_H - SAFE_H) / 2) + 96
    styles = "\n".join([
        f"Style: Title,{FONT_NAME},150,{spec['ink']},{spec['ink']},&H000000,&H000000,"
        f"-1,0,0,0,100,100,0,0,1,0,0,8,0,0,{title_v},1",
        f"Style: Tag,{FONT_NAME},52,&HD0D0D0,&HD0D0D0,&H000000,&H000000,"
        f"0,0,0,0,100,100,0,0,1,0,0,8,0,0,{title_v + 210},1",
    ])
    ass_path = write_ass([("Title", spec["title"]), ("Tag", spec["tagline"])],
                         styles, BANNER_W, BANNER_H, work, "banner.ass")
    ground = spec["ground"].lstrip("#")
    filters = (f"format=rgb24,geq="
               f"r='0x{ground[0:2]}+{spec['wash']}*52*max(0,1-hypot(X-W*0.5,Y-H*0.42)/(W*0.62))':"
               f"g='0x{ground[2:4]}+{spec['wash']}*44*max(0,1-hypot(X-W*0.5,Y-H*0.42)/(W*0.62))':"
               f"b='0x{ground[4:6]}+{spec['wash']}*36*max(0,1-hypot(X-W*0.5,Y-H*0.42)/(W*0.62))'")
    render(filters, ass_path, fonts_dir, out, BANNER_W, BANNER_H)


def main() -> None:
    parser = argparse.ArgumentParser(description="Render a channel avatar and banner.")
    parser.add_argument("--preset", required=True, choices=sorted(PRESETS))
    parser.add_argument("--out", required=True)
    args = parser.parse_args()

    if not FONT_FILE.exists():
        sys.exit(f"ERROR: {FONT_FILE} not found — libass cannot draw Thai without it")

    spec = PRESETS[args.preset]
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    work = Path(tempfile.mkdtemp(prefix="art_"))
    try:
        fonts_dir = work / "fonts"
        fonts_dir.mkdir()
        shutil.copy2(FONT_FILE, fonts_dir / FONT_FILE.name)

        print(f"Rendering '{args.preset}' — {spec['title']}")
        build_avatar(spec, work, fonts_dir, out_dir / "avatar.png")
        build_banner(spec, work, fonts_dir, out_dir / "banner.png")
    finally:
        shutil.rmtree(work, ignore_errors=True)

    for name, limit_mb in (("avatar.png", 4), ("banner.png", 6)):
        size_mb = (out_dir / name).stat().st_size / 1048576
        flag = "" if size_mb < limit_mb else f"  ** over YouTube's {limit_mb} MB limit **"
        print(f"  {name:<12} {size_mb:.2f} MB{flag}")
    print(f"\nUpload both in YouTube Studio > Customisation > Branding.")


if __name__ == "__main__":
    main()
