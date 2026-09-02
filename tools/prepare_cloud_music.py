"""Shrink chosen music tracks into the small set the cloud runner ships with.

Everything under music/ is gitignored -- it is 144MB, and the repo is public,
so the licensing of whatever is in there is not something to publish on the
owner's behalf. The consequence was that GitHub Actions had no music at all
and every cloud render fell through to SoundHelix's generated rock loops.

music/cloud/ is the exception: one committed file per mood, named for the
mood, small enough that a public repo does not mind carrying it. This trims
and re-encodes source tracks into that shape. Levels are not touched --
src/editor.py normalises the bed to a fixed loudness at render time, so a
track's own mastering no longer matters.

    python tools/prepare_cloud_music.py                 # every mood folder
    python tools/prepare_cloud_music.py tense mysterious
    python tools/prepare_cloud_music.py --from path/to/track.mp3 --mood tense

Only add tracks that are yours to publish: CC0, a library that permits
redistribution, or something you generated. A subscription library's files
are licensed to you for use in videos, not for republishing in a public repo.
"""

from __future__ import annotations

import argparse
import glob
import os
import subprocess
import sys

sys.stdout.reconfigure(encoding="utf-8")

MUSIC_DIR = "music"
CLOUD_DIR = os.path.join(MUSIC_DIR, "cloud")

MOODS = ["mysterious", "dramatic", "upbeat", "melancholic",
         "epic", "peaceful", "tense", "inspiring"]

# Long enough that a 45s video never hears a loop point, short enough that
# eight of these are a few megabytes. Mono: it is a bed 18 dB under a mono
# voiceover, and nobody has ever noticed a bed's stereo image on a phone.
SECONDS  = 75
BITRATE  = "96k"
CHANNELS = "1"


def encode(src: str, dst: str) -> bool:
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    r = subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-i", src,
         "-t", str(SECONDS), "-ac", CHANNELS, "-b:a", BITRATE,
         "-map_metadata", "-1", dst],
        capture_output=True, text=True)
    if r.returncode != 0:
        print(f"  failed: {(r.stderr or '').strip()[-200:]}")
        return False
    return True


def source_for(mood: str) -> str | None:
    """First audio file in music/<mood>/, if that folder has one."""
    folder = os.path.join(MUSIC_DIR, mood)
    if not os.path.isdir(folder):
        return None
    found = sorted(f for f in glob.glob(os.path.join(folder, "*"))
                   if f.lower().endswith((".mp3", ".wav", ".m4a", ".flac")))
    return found[0] if found else None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("moods", nargs="*", default=None,
                    help="Moods to prepare (default: all with a source track)")
    ap.add_argument("--from", dest="src",
                    help="Use this file instead of music/<mood>/")
    ap.add_argument("--mood", help="Mood name to write, with --from")
    args = ap.parse_args()

    if args.src:
        if not args.mood:
            print("--from needs --mood"); return 2
        if not os.path.exists(args.src):
            print(f"no such file: {args.src}"); return 1
        dst = os.path.join(CLOUD_DIR, f"{args.mood}.mp3")
        print(f"{args.mood:<12} {args.src}")
        return 0 if encode(args.src, dst) else 1

    wanted = args.moods or MOODS
    done = 0
    for mood in wanted:
        src = source_for(mood)
        if not src:
            print(f"{mood:<12} (no source track in {MUSIC_DIR}/{mood}/ — skipped)")
            continue
        dst = os.path.join(CLOUD_DIR, f"{mood}.mp3")
        if encode(src, dst):
            size = os.path.getsize(dst) / 1024
            print(f"{mood:<12} {os.path.basename(src)}  ->  {dst}  ({size:.0f} KB)")
            done += 1

    if done:
        total = sum(os.path.getsize(f) for f in glob.glob(os.path.join(CLOUD_DIR, "*.mp3")))
        print(f"\n{done} track(s) ready — {total/1024/1024:.1f} MB in {CLOUD_DIR}")
        print("music/cloud/ is gitignored on purpose. Once you are sure these")
        print("are yours to publish in a public repo:  git add -f music/cloud/")
    return 0


if __name__ == "__main__":
    sys.exit(main())
