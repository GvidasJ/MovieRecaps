"""Day 13 (file names = the video's title): the app names project folders after the video's title -- spaces, an
apostrophe ("He couldn't ..."), emoji -- and the tool's run folders are inside them. The framed preview's ffmpeg concat
list quoted every path in single quotes, so an apostrophe ended the quotes ("Error opening input files"): the preview
failed and the run recorded an error."""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from match_cuts.premiere_preview import concat_line

FFMPEG = shutil.which("ffmpeg")


def test_concat_line_quotes_an_apostrophe_the_way_ffmpeg_reads_it():
    assert concat_line(Path("C:/a/He couldn't/clip000.mp4")) == "file 'C:/a/He couldn'\\''t/clip000.mp4'\n"
    assert concat_line("C:/plain/clip.mp4") == "file 'C:/plain/clip.mp4'\n"


@pytest.mark.skipif(FFMPEG is None, reason="ffmpeg is not on PATH")
def test_ffmpeg_concats_clips_in_a_folder_named_after_a_title(tmp_path):
    d = tmp_path / "2026-10-20_He couldn't get worse \U0001F697\U0001F62D (2)" / "edit" / "extras" / "mc_preview_x"
    d.mkdir(parents=True)
    parts = []
    for i in range(2):
        p = d / f"clip{i:03d}.mp4"
        subprocess.run([FFMPEG, "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc=size=64x48:rate=10:duration=0.5",
                        "-c:v", "libx264", "-pix_fmt", "yuv420p", str(p)], check=True)
        parts.append(p)
    lst = d / "parts.txt"
    lst.write_text("".join(concat_line(q) for q in parts), encoding="utf-8")
    out = d / "video.mp4"
    r = subprocess.run([FFMPEG, "-v", "error", "-y", "-f", "concat", "-safe", "0", "-i", str(lst), "-c", "copy",
                        str(out)], capture_output=True, text=True, encoding="utf-8", errors="replace")
    assert r.returncode == 0, r.stderr
    assert out.is_file() and out.stat().st_size > 0
