"""caption_ocr.read_caption_spans on a short synthetic clip with burned-in captions (white text, black outline, over a
colourful moving test pattern): exact text and exact first / last frame of every caption, a pop-in animation and a
word highlighted in another colour that stay one caption, the same word shown twice in a row (two captions), static
text ignored. Then the whole caption stage (captions.run_captions) in competitor mode: an exact copy, no transcript.
"""
from __future__ import annotations

import shutil
import subprocess
import types
from fractions import Fraction
from pathlib import Path

import numpy as np
import pytest

from match_cuts import caption_ocr, captions as C

FONTS = ["/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", "C:/Windows/Fonts/arialbd.ttf",
         "/Library/Fonts/Arial Bold.ttf", "/System/Library/Fonts/Supplemental/Arial Bold.ttf"]
FONT = next((f for f in FONTS if Path(f).is_file()), None)

pytestmark = [
    pytest.mark.skipif(caption_ocr.available() is not None, reason="RapidOCR not installed"),
    pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not found"),
    pytest.mark.skipif(FONT is None, reason="no TrueType font for drawtext"),
]

W, H, N = 720, 1280, 150
# (text, first frame, last frame + 1) of every caption actually drawn
TRUTH = [("I got a Parker Peter", 10, 30), ("*automatic audi braking*", 30, 55), ("whoa!! x2", 55, 85),
         ("no", 85, 100), ("no", 100, 115), ("Spider-Man is", 118, 141)]
# what the layout detector would report: events a frame or two off the true boundaries, boxes around the text
EVENTS = [(11, 29, 145, 575), (31, 54, 65, 655), (56, 84, 250, 470), (86, 99, 340, 380), (101, 114, 340, 380),
          (119, 140, 205, 515)]
LAYOUT = {
    "captions": [{"type": "captions", "comp_in": a, "comp_out": b, "x": x0, "y": 862, "w": x1 - x0, "h": 36}
                 for a, b, x0, x1 in EVENTS] + [{"type": "text", "comp_in": 0, "comp_out": N, "x": 150, "y": 70, "w": 420,
                                         "h": 90}],
    "zones": [{"type": "title", "x": 150, "y": 70, "w": 420, "h": 90},
              {"type": "watermark", "x": 590, "y": 900, "w": 110, "h": 34},
              {"type": "captions", "x": 100, "y": 850, "w": 520, "h": 60,
               "notes": "6 caption events; white text with dark outline, median glyph height 32 px"}],
}


def _ff_path(p: str) -> str:
    return p.replace("\\", "/").replace(":", "\\\\:")


@pytest.fixture(scope="module")
def clip(tmp_path_factory) -> Path:
    out = tmp_path_factory.mktemp("caption_ocr") / "captions.mp4"
    font = _ff_path(FONT)
    st = f"fontfile='{font}':fontcolor=white:borderw=4:bordercolor=black"
    pop = "if(lt(n,{a}+2),44*(0.5+0.25*(n-{a})),44)"
    centre = "x=(w-tw)/2:y=880-th/2"
    vf = ",".join([
        f"drawtext={st}:text='MY TITLE':fontsize=60:x=(w-tw)/2:y=80",                                # static title
        f"drawtext={st}:text='@chan':fontsize=22:x=600:y=905",                                      # static watermark
        f"drawtext={st}:text='I got a Parker Peter':fontsize=44:{centre}:enable='between(n,10,29)'",
        f"drawtext={st}:text='*automatic audi braking*':fontsize='{pop.format(a=30)}':{centre}"
        f":enable='between(n,30,54)'",                                                              # pop-in
        f"drawtext={st}:text='whoa!! x2':fontsize=44:x=250:y=860:enable='between(n,55,84)'",
        f"drawtext=fontfile='{font}':fontcolor=yellow:borderw=4:bordercolor=black:text='whoa!!':fontsize=44"
        f":x=250:y=860:enable='between(n,70,84)'",                                                  # highlight
        f"drawtext={st}:text='no':fontsize=44:{centre}:enable='between(n,85,99)'",
        f"drawtext={st}:text='no':fontsize='{pop.format(a=100)}':{centre}:enable='between(n,100,114)'",  # again
        f"drawtext={st}:text='Spider-Man is':fontsize=44:{centre}:enable='between(n,118,140)'",
    ])
    cmd = ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", f"testsrc2=s={W}x{H}:r=30:d={N / 30}",
           "-vf", vf, "-c:v", "libx264", "-crf", "20", "-pix_fmt", "yuv420p", str(out)]
    subprocess.run(cmd, check=True)
    return out


def test_ocr_reads_every_caption_exactly_with_exact_frames(clip):
    res = caption_ocr.read_caption_spans(str(clip), LAYOUT, (W, H), Fraction(30), N)
    got = [(c["ocr"], c["comp_in"], c["comp_out"]) for c in res["spans"]]
    assert got == TRUTH                            # the static title and the "@chan" watermark are never a caption
    for c in res["spans"]:
        assert c["reads"] >= 1 and c["agreement"] >= 0.9 and c["score"] >= 0.9
    assert res["frames_read"] == N and res["conventions"]["all_caps"] is False


def test_caption_stage_copies_the_competitor_exactly(clip, tmp_path, monkeypatch):
    from match_cuts import report, transcribe
    from match_cuts.common import Cache
    from match_cuts.config import Config

    def no_transcript(*a, **k):
        raise AssertionError("every caption was read: no transcript needed")
    monkeypatch.setattr(transcribe, "available", lambda: None)
    monkeypatch.setattr(transcribe, "transcribe_words", no_transcript)
    cfg = Config()
    cfg.out_dir, cfg.work_dir, cfg.premiere = str(tmp_path / "out"), str(tmp_path / "work"), False
    cfg.captions = "competitor"
    info = types.SimpleNamespace(path=str(clip), file_hash="synthetic", width=W, height=H, display_width=W,
                                 display_height=H)
    warnings: list[str] = []
    ctx = types.SimpleNamespace(cfg=cfg, comp_info=info, comp_fps=Fraction(30), n_comp=N,
                                cutlist=types.SimpleNamespace(layout=LAYOUT), cache=Cache(cfg.work),
                                raw_audio=np.zeros(16000 * 5, np.float32), audio_sr=16000, warn=warnings.append)
    res = C.run_captions(ctx)
    assert res["mode"] == "competitor" and not warnings
    blocks = C.parse_srt(Path(res["path"]).read_text(encoding="utf-8"))
    assert [(b["text"], round(b["start_ms"] * 60 / 1000), round(b["end_ms"] * 60 / 1000)) for b in blocks] == \
        [(t, 2 * a, 2 * b) for t, a, b in TRUTH]                  # 30 fps frame k = 2k at 60; nothing added
    assert res["competitor_notes"] == {"from_transcript": [], "unreadable": []}
    ctx.captions = res
    md = "\n".join(report._captions(ctx))
    assert "copied exactly" in md and "no style rules applied" in md and "24-character cap" not in md
