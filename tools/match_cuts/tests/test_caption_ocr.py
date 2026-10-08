"""caption_ocr.read_caption_spans on a short synthetic clip with burned-in captions (white text, black outline, over a
colourful moving test pattern): exact text and exact first / last frame of every caption, a pop-in animation and a
word highlighted in another colour that stay one caption, the same word shown twice in a row (two captions), static
text ignored. Then the whole caption stage (captions.run_captions) in competitor mode without a transcript: the
competitor's timing and splits, my hard rules on the text (caption_rules.py).
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
from portable import font_file, pop_in  # noqa: E402

FONT = next((f for f in [font_file('bold')] + FONTS if Path(f).is_file()), None)   # DejaVu first

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
    from portable import ff_path
    return ff_path(p, quoted=True)          # used inside '...'


@pytest.fixture(scope="module")
def clip(tmp_path_factory) -> Path:
    out = tmp_path_factory.mktemp("caption_ocr") / "captions.mp4"
    font = _ff_path(FONT)
    st = f"fontfile='{font}':fontcolor=white:borderw=4:bordercolor=black"
    centre = "x=(w-tw)/2:y=880-th/2"
    vf = ",".join([
        f"drawtext={st}:text='MY TITLE':fontsize=60:x=(w-tw)/2:y=80",                                # static title
        f"drawtext={st}:text='@chan':fontsize=22:x=600:y=905",                                      # static watermark
        f"drawtext={st}:text='I got a Parker Peter':fontsize=44:{centre}:enable='between(n,10,29)'",
        *pop_in(st, "*automatic audi braking*", 30, 54, centre),                                     # pop-in
        f"drawtext={st}:text='whoa!! x2':fontsize=44:x=250:y=860:enable='between(n,55,84)'",
        f"drawtext=fontfile='{font}':fontcolor=yellow:borderw=4:bordercolor=black:text='whoa!!':fontsize=44"
        f":x=250:y=860:enable='between(n,70,84)'",                                                  # highlight
        f"drawtext={st}:text='no':fontsize=44:{centre}:enable='between(n,85,99)'",
        *pop_in(st, "no", 100, 114, centre),                                                        # again
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


def test_caption_stage_keeps_the_competitors_timing_and_applies_my_rules(clip, tmp_path, monkeypatch):
    from match_cuts import report, transcribe
    from match_cuts.common import Cache
    from match_cuts.config import Config
    monkeypatch.setattr(transcribe, "available", lambda: "not installed in this test")
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
    assert res["mode"] == "competitor" and warnings == ["captions: no transcription: not installed in this test"]
    blocks = C.parse_srt(Path(res["path"]).read_text(encoding="utf-8"))
    # 30 fps frame k = 2k at 60; "whoa!! x2" split after the sentence end (rule 1), "Audi" a name, "Whoa!!" the
    # first word after an action caption; "no" | "no" (interjections the competitor shows alone) stay apart
    assert [(b["text"], round(b["start_ms"] * 60 / 1000), round(b["end_ms"] * 60 / 1000)) for b in blocks] == \
        [("I got a Parker Peter", 20, 60), ("*automatic Audi braking*", 60, 110), ("Whoa!!", 110, 157),
         ("x2", 157, 170), ("no", 170, 200), ("no", 200, 236), ("Spider-Man is", 236, 282)]   # back to back
    assert res["competitor_notes"] == {"from_transcript": [], "unreadable": []}
    assert res["rules"]["changed"][1] == 1 and res["weak_kept"][0]["reason"] == "last caption"
    ctx.captions = res
    md = "\n".join(report._captions(ctx))
    assert "the competitor decides the timing" in md and "**Hard rules**" in md and "24-character cap" not in md
    from match_cuts.caption_rules import summary_line
    assert summary_line(res["rules"]).startswith("1 one sentence: 1 split; 2 one speaker: 0 split")


def test_a_zone_over_the_caption_events_is_not_ignored():
    """The Zendaya-age competitor (run 011): the layout found a 'channel name' zone over most of the frame, the
    caption band included -- ignoring it left nothing to read. A zone holding the caption events is not ignored;
    a small logo next to them still is."""
    from match_cuts.caption_ocr import band_from_layout
    lay = {"captions": [{"type": "captions", "comp_in": 73, "comp_out": 93, "x": 380, "y": 1508, "w": 296, "h": 70}],
           "zones": [{"type": "channel_name", "x": 0, "y": 170, "w": 984, "h": 1452},
                     {"type": "watermark", "x": 900, "y": 1500, "w": 100, "h": 80}]}
    band, spans = band_from_layout(lay, (1080, 1920))
    assert spans == [(73, 93)]
    assert band.ignore == [(900, 1500, 100, 80)]


# video018's competitor: each speaker's captions in a colour of their own (yellow, green; a laugh in pink)
TRUTH_COLOURS = [("Are you okay", 5, 35, "yellow"), ("I gotta go", 35, 60, "0x00FF00"), ("trail mix", 60, 95, "yellow"),
                 ("*laughing*", 95, 120, "0xFF6CE4"), ("bye now", 120, 145, "0x00FF00")]


@pytest.fixture(scope="module")
def colour_clip(tmp_path_factory) -> Path:
    out = tmp_path_factory.mktemp("caption_ocr_colours") / "colours.mp4"
    font = _ff_path(FONT)
    vf = ",".join(
        [f"drawtext=fontfile='{font}':fontcolor=white:borderw=4:bordercolor=black:text='MY TITLE':fontsize=60"
         ":x=(w-tw)/2:y=80"] +
        [f"drawtext=fontfile='{font}':fontcolor={col}:borderw=4:bordercolor=black:text='{t}':fontsize=44"
         f":x=(w-tw)/2:y=880-th/2:enable='between(n,{a},{b - 1})'" for t, a, b, col in TRUTH_COLOURS])
    # the moving test pattern without its colours: its saturated yellow and green bars would be caption colours here
    cmd = ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", f"testsrc2=s={W}x{H}:r=30:d={N / 30}",
           "-vf", "hue=s=0," + vf, "-c:v", "libx264", "-crf", "20", "-pix_fmt", "yuv420p", str(out)]
    subprocess.run(cmd, check=True)
    return out


def test_captions_in_a_colour_per_speaker_are_all_read(colour_clip):
    """video018: the competitor wrote one speaker's captions in yellow, the other's in green and a laugh in pink;
    only the yellow ones were read (one fill colour learned) -- 20 of its 50 captions were missing, and the stretches
    without a read caption fell back to the transcript. Every caption is read now, with its exact frames; the
    colours learned are the three."""
    lay = {"captions": [{"type": "captions", "comp_in": a, "comp_out": b, "x": 200, "y": 862, "w": 320, "h": 36}
                        for _, a, b, _ in TRUTH_COLOURS],
           "zones": [{"type": "title", "x": 150, "y": 70, "w": 420, "h": 90},
                     {"type": "captions", "x": 100, "y": 850, "w": 520, "h": 60,
                      "notes": "5 caption events; white text with dark outline, median glyph height 32 px"}]}
    res = caption_ocr.read_caption_spans(str(colour_clip), lay, (W, H), Fraction(30), N)
    got = [(c["ocr"], c["comp_in"], c["comp_out"]) for c in res["spans"]]
    assert got == [(t, a, b) for t, a, b, _ in TRUTH_COLOURS]
    assert len(res["fills"]) == 3 and res["fill"] == res["fills"][0]
