"""Premiere competitor captions: the competitor's on-screen TIMING and splits (caption_ocr.read_caption_spans), the
transcript's WORDS (captions.competitor_text), OCR only for the word split, names and non-speech captions.

* a synthetic clip in the style of the real competitor (cream text with a soft drop shadow over a moving test
  pattern): exact first / last frames; a pop-in, a highlighted word and a caption that grows word by word stay one
  caption; the same word twice is two captions; *laughs* is read from the picture;
* the text rules on hand-made spans and words;
* the acceptance test on input/competitor.mp4 (the clip whose captions came out as "buiIam" / "es Avenge s"): every
  caption is real words and none is shorter than 0.1 s.
"""
from __future__ import annotations

import re
import shutil
import subprocess
from fractions import Fraction
from pathlib import Path

import pytest

from match_cuts import caption_ocr, captions as C

FONTS = ["/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", "C:/Windows/Fonts/arialbd.ttf",
         "/Library/Fonts/Arial Bold.ttf", "/System/Library/Fonts/Supplemental/Arial Bold.ttf"]
FONT = next((f for f in FONTS if Path(f).is_file()), None)
REAL = Path(__file__).resolve().parents[3] / "input" / "competitor.mp4"
# the caption zone layout.py measured on input/competitor.mp4 (output/report.md of that run)
REAL_LAYOUT = {"zones": [{"type": "title", "x": 26, "y": 76, "w": 554, "h": 222},
                         {"type": "other", "x": 246, "y": 96, "w": 218, "h": 54},
                         {"type": "captions", "x": 32, "y": 696, "w": 434, "h": 64,
                          "notes": "47 caption events; white text with dark outline, median glyph height 26 px"}]}

W, H, N = 720, 1280, 150
TRUTH = [(0, 10), (10, 35), (35, 60), (60, 90), (90, 105), (105, 120), (125, 150)]
LAYOUT = {"zones": [{"type": "title", "x": 150, "y": 70, "w": 420, "h": 90},
                    {"type": "captions", "x": 60, "y": 860, "w": 600, "h": 50,
                     "notes": "7 caption events; median glyph height 32 px"}]}

need_ocr = pytest.mark.skipif(caption_ocr.available() is not None, reason="RapidOCR not installed")


def _ff(p: str) -> str:
    return p.replace("\\", "/").replace(":", "\\\\:")


@pytest.fixture(scope="module")
def clip(tmp_path_factory) -> Path:
    if FONT is None or shutil.which("ffmpeg") is None:
        pytest.skip("ffmpeg / a TrueType font not available")
    out = tmp_path_factory.mktemp("caption_spans") / "spans.mp4"
    font = _ff(FONT)
    st = f"fontfile='{font}':fontcolor=0xF0E18C:shadowcolor=black@0.85:shadowx=3:shadowy=3"
    hi = f"fontfile='{font}':fontcolor=0x40FF40:shadowcolor=black@0.85:shadowx=3:shadowy=3"
    ctr = "x=(w-tw)/2:y=880-th/2"
    pop = "if(lt(n,{a}+2),44*(0.5+0.25*(n-{a})),44)"
    vf = ",".join([
        f"drawtext={st}:text='MY TITLE':fontsize=60:x=(w-tw)/2:y=80",
        f"drawtext={st}:text='Deadpool':fontsize=44:{ctr}:enable='between(n,0,9)'",
        f"drawtext={st}:text='builds a team':fontsize='{pop.format(a=10)}':{ctr}:enable='between(n,10,34)'",
        f"drawtext={st}:text='the X-Force':fontsize=44:x=200:y=860:enable='between(n,35,59)'",
        f"drawtext={hi}:text='the':fontsize=44:x=200:y=860:enable='between(n,47,59)'",          # highlighted word
        f"drawtext={st}:text='you see':fontsize=44:{ctr}:enable='between(n,60,69)'",            # grows word by word
        f"drawtext={st}:text='you see him':fontsize=44:{ctr}:enable='between(n,70,89)'",
        f"drawtext={st}:text='no':fontsize=44:{ctr}:enable='between(n,90,104)'",
        f"drawtext={st}:text='no':fontsize='{pop.format(a=105)}':{ctr}:enable='between(n,105,119)'",
        f"drawtext={st}:text='*laughs*':fontsize=44:{ctr}:enable='between(n,125,149)'",
    ])
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", f"testsrc2=s={W}x{H}:r=30:d={N / 30}",
                    "-vf", vf, "-c:v", "libx264", "-crf", "20", "-pix_fmt", "yuv420p", str(out)], check=True)
    return out


@need_ocr
def test_spans_follow_the_on_screen_captions_exactly(clip):
    res = caption_ocr.read_caption_spans(str(clip), LAYOUT, (W, H), Fraction(30), N)
    got = [(d["comp_in"], d["comp_out"]) for d in res["spans"]]
    assert got == TRUTH                        # pop-in / highlight / word-by-word growth: one caption; "no" twice: two
    ocr = [d["ocr"] for d in res["spans"]]
    assert ocr[0] == "Deadpool" and ocr[-1] == "*laughs*" and "X-Force" in ocr[2]
    assert all(b - a >= 0.15 * 30 for a, b in got)


@need_ocr
def test_caption_stage_writes_the_spoken_words_with_the_competitor_timing(clip, tmp_path, monkeypatch):
    import types

    import numpy as np
    import soundfile as sf

    from match_cuts import transcribe
    from match_cuts.common import Cache
    from match_cuts.config import Config
    heard = [("Deadpool", .05), ("builds", .4), ("a", .7), ("team", .85), ("the", 1.25), ("x", 1.4), ("force", 1.6),
             ("you", 2.1), ("see", 2.4), ("him", 2.7), ("no", 3.1), ("no", 3.6)]
    words = [C.Word(w, t, t + 0.12, 0.99, w) for w, t in heard]
    monkeypatch.setattr(transcribe, "available", lambda: None)
    monkeypatch.setattr(transcribe, "transcribe_words", lambda *a, **k: list(words))
    vo = tmp_path / "vo.wav"
    sf.write(str(vo), (0.2 * np.sin(np.arange(16000 * 5) * 0.1)).astype(np.float32), 16000)
    cfg = Config(out_dir=str(tmp_path / "out"), work_dir=str(tmp_path / "work"), premiere=True, captions="competitor")
    cfg.voiceover = str(vo)
    info = types.SimpleNamespace(path=str(clip), file_hash="spans", width=W, height=H, display_width=W, display_height=H)
    ctx = types.SimpleNamespace(cfg=cfg, comp_info=info, comp_fps=Fraction(30), n_comp=N,
                                cutlist=types.SimpleNamespace(layout=LAYOUT), cache=Cache(cfg.work), raw_audio=None,
                                audio_sr=16000, warn=lambda m: None)
    res = C.run_captions(ctx)
    blocks = C.parse_srt(Path(res["path"]).read_text(encoding="utf-8"))
    assert [(b["text"], round(b["start_ms"] * 60 / 1000), round(b["end_ms"] * 60 / 1000)) for b in blocks] == [
        ("Deadpool", 0, 20), ("builds a team", 20, 70), ("the X-Force", 70, 120), ("you see him", 120, 180),
        ("no", 180, 210), ("no", 210, 240), ("*laughs*", 250, 300)]
    assert res["competitor_notes"]["names"] == [{"start_tc": "00:00:01,167", "end_tc": "00:00:02,000",
                                                 "heard": "x force", "written": "X-Force"}]
    assert [r["text"] for r in res["competitor_notes"]["non_speech"]] == ["*laughs*"]
    from match_cuts import report
    ctx.captions = res
    md = "\n".join(report._captions(ctx))
    assert "competitor's on-screen timing" in md and "Captions shorter than 0.1 s**: none" in md


# ---------------------------------------------------------------------------------------------
# the text rules
# ---------------------------------------------------------------------------------------------

def W_(text: str, t: float) -> C.Word:
    return C.Word(C.clean_text(text), t, t + 0.1, 0.99, text)


def _caps(spans, words):
    return C.competitor_text(spans, words, Fraction(30), lambda k: 2 * k, Fraction(60))


def test_words_go_to_the_caption_on_screen_and_ocr_moves_a_boundary_word():
    spans = [{"comp_in": 0, "comp_out": 9, "ocr": "that's what", "score": 0.99},
             {"comp_in": 9, "comp_out": 16, "ocr": "we're going", "score": 0.98}]
    words = [W_("that's", 0.05), W_("what", 0.26), W_("we're", 0.35), W_("going", 0.45)]   # "what" ends after 0.3 s
    caps, loose, _ = _caps(spans, words)
    assert [c.text for c in caps] == ["that's what", "we're going"] and not loose


def test_a_sentence_end_goes_back_to_its_sentence_and_spoken_words_stay():
    spans = [{"comp_in": 0, "comp_out": 11, "ocr": "to get out of", "score": 0.99},
             {"comp_in": 11, "comp_out": 20, "ocr": "This is going", "score": 0.99}]
    words = [W_("to", 0.0), W_("get", 0.1), W_("out", 0.2), W_("of", 0.28), W_("this.", 0.37), W_("This", 0.45),
             W_("is", 0.5), W_("going", 0.55)]
    caps, _, notes = _caps(spans, words)
    assert [c.text for c in caps] == ["to get out of this", "This is going"]


def test_names_from_ocr_spacing_slips_and_caption_start_capitals_ignored():
    fix = C.fix_names
    assert fix(["he's", "going", "to", "play", "vanisher"], "he's going to play Vanisher")[0][-1] == "Vanisher"
    assert fix(["the", "x", "force"], "the X-Force")[0] == ["the", "X-Force"]
    assert fix(["And", "I", "was"], "AndI was")[0] == ["And", "I", "was"]              # OCR spacing slip
    assert fix(["back", "up"], "Back up")[0] == ["back", "up"]                         # the caption's first capital
    assert fix(["Brad", "Pitt's", "going"], "Brad Pitt's going")[1] == []


def test_non_speech_and_unreadable_captions():
    spans = [{"comp_in": 0, "comp_out": 15, "ocr": "*Laughter*", "score": 0.99},
             {"comp_in": 15, "comp_out": 30, "ocr": "e r", "score": 0.4},
             {"comp_in": 30, "comp_out": 45, "ocr": "Wow", "score": 0.95}]
    words = [W_("ha", 0.2)]
    caps, _, notes = _caps(spans, words)
    assert [c.text for c in caps] == ["*Laughter*", "*...*", "Wow"]
    assert [n["start_tc"] for n in notes["unreadable"]] == ["00:00:00,500"]           # listed in the report
    assert notes["from_ocr"][0]["text"] == "Wow"


def test_words_outside_every_caption_are_left_for_the_voice_fill():
    spans = [{"comp_in": 0, "comp_out": 15, "ocr": "hello there", "score": 0.99}]
    caps, loose, _ = _caps(spans, [W_("hello", 0.1), W_("there", 0.3), W_("later", 2.0)])
    assert caps[0].text == "hello there" and [w.text for w in loose] == ["later"]


# ---------------------------------------------------------------------------------------------
# acceptance: input/competitor.mp4
# ---------------------------------------------------------------------------------------------

REAL_WORD = re.compile(r"^[A-Za-z0-9][A-Za-z0-9'\u2019\-]*[?!]*$")


@need_ocr
@pytest.mark.skipif(not REAL.is_file(), reason="input/competitor.mp4 not in this checkout")
def test_real_competitor_captions_are_real_words_with_the_competitor_timing(tmp_path):
    from match_cuts import transcribe
    from match_cuts.common import Cache
    from match_cuts.media import extract_audio
    if transcribe.available() is not None:
        pytest.skip("faster-whisper not installed")
    res = caption_ocr.read_caption_spans(str(REAL), REAL_LAYOUT, (608, 1080), Fraction(30), 700)
    y = extract_audio(str(REAL), sr=16000, mono=True)
    words = transcribe.transcribe_words(y, 16000, "small.en", "en", Cache(tmp_path))
    caps, loose, notes = C.competitor_text(res["spans"], words, Fraction(30), lambda k: 2 * k, Fraction(60))
    assert len(caps) >= 50
    spoken = {C._key(w.text) for w in words}
    read = {C._key(t) for d in res["spans"] for t in str(d.get("ocr") or "").split()}
    for c in caps:
        assert (c.end - c.start) / 60.0 >= 0.1, c
        if C.NON_SPEECH_RE.match(c.text):
            continue
        for tok in c.text.split():
            assert REAL_WORD.match(tok), (c.text, tok)
            assert C._key(tok) in spoken or C._key(tok) in read, (c.text, tok)
    texts = [c.text for c in caps]
    for want in ("Deadpool", "builds a team", "the X-Force", "yes Avengers", "*Laughter*"):
        assert want in texts, want
