"""caption_ocr.py on a short synthetic clip with burned-in captions (ffmpeg drawtext over a colourful moving test
pattern): exact text and exact first / last frame of every caption, a pop-in animation and a word-by-word colour
highlight that stay one caption, the same word shown twice in a row (two captions), static text ignored. Then the
whole caption stage (captions.run_captions) in competitor mode, with the transcription replaced by a fixed word list.
"""
from __future__ import annotations

import shutil
import subprocess
import types
from fractions import Fraction
from pathlib import Path

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
    res = caption_ocr.read_competitor_captions(str(clip), LAYOUT, (W, H), Fraction(30), N)
    got = [(c["text"], c["comp_in"], c["comp_out"]) for c in res["captions"]]
    assert got == TRUTH
    for c in res["captions"]:
        assert c["reads"] >= 10 and c["agreement"] >= 0.9           # many frames read, majority agrees
    assert res["events"] == 6 and not res["notes"]["static"] and not res["notes"]["unreadable"]


def test_text_mask_ignores_the_static_zones(clip):
    band, spans = caption_ocr.band_from_layout(LAYOUT, (W, H))
    assert spans == [(a, b) for a, b, _, _ in EVENTS] and band.y < 850 and band.y + band.h > 910
    reads = caption_ocr.read_frames(str(clip), [5, 20], band, Fraction(30))
    assert reads[0].area == 0                                        # only the static watermark there: nothing
    assert reads[1].text == "I got a Parker Peter" and "@" not in reads[1].text


def test_caption_stage_copies_the_competitor_and_fills_uncaptioned_speech(clip, tmp_path, monkeypatch):
    from match_cuts import report, transcribe
    from match_cuts.common import Cache
    from match_cuts.config import Config
    heard = [("I", .35), ("got", .5), ("a", .65), ("Peter", .8), ("Parker", .95),          # differs from the OCR
             ("whoa", 2.0), ("x2", 2.3), ("no", 2.95), ("no", 3.45), ("Spider-Man", 4.0), ("is", 4.3),
             ("back", 6.0), ("to", 6.2), ("the", 6.4), ("test", 6.6)]                     # no caption there
    words = [C.Word(w, t, t + 0.15, 0.99, w) for w, t in heard]
    monkeypatch.setattr(transcribe, "available", lambda: None)
    monkeypatch.setattr(transcribe, "transcribe_words", lambda *a, **k: list(words))
    cfg = Config()
    # without --premiere the competitor's caption text is copied as read (--premiere: tests/test_caption_spans.py)
    cfg.out_dir, cfg.work_dir, cfg.premiere = str(tmp_path / "out"), str(tmp_path / "work"), False
    import numpy as np
    import soundfile as sf
    vo = tmp_path / "voiceover.wav"                 # any audio: the transcription is replaced above
    sf.write(str(vo), (0.2 * np.sin(np.arange(16000 * 8) * 2 * np.pi * 220 / 16000)).astype(np.float32), 16000)
    cfg.voiceover = str(vo)
    cfg.captions = "competitor"
    info = types.SimpleNamespace(path=str(clip), file_hash="synthetic", width=W, height=H, display_width=W,
                                 display_height=H)
    warnings: list[str] = []
    ctx = types.SimpleNamespace(cfg=cfg, comp_info=info, comp_fps=Fraction(30), n_comp=240,
                                cutlist=types.SimpleNamespace(layout=LAYOUT), cache=Cache(cfg.work), raw_audio=None,
                                audio_sr=16000, warn=warnings.append)
    res = C.run_captions(ctx)
    assert res["mode"] == "competitor" and not warnings
    srt = Path(res["path"]).read_text(encoding="utf-8")
    blocks = C.parse_srt(srt)
    comp = [b for b in blocks if b["text"] != "back to the test"]
    assert [(b["text"], round(b["start_ms"] * 60 / 1000), round(b["end_ms"] * 60 / 1000)) for b in comp] == \
        [(t, 2 * a, 2 * b) for t, a, b in TRUTH]                                       # 30 fps frame k = 2k at 60
    assert blocks[-1]["text"] == "back to the test" and blocks[-1]["start_ms"] >= 141 * 1000 // 30
    dis = res["disagreements"]
    assert [d["ocr"] for d in dis] == ["I got a Parker Peter"] and "Peter Parker" in dis[0]["heard"]
    ctx.captions = res
    md = "\n".join(report._captions(ctx))
    assert "Mode: competitor" in md and "OCR / transcript disagreements" in md and "I got a Parker Peter" in md
    assert "*automatic audi braking*" in md                       # 24 characters: at the cap, listed
