"""Competitor mode: the competitor's captions read exactly (caption_ocr.read_caption_spans), then written by my rules
(captions.run_captions + caption_rules.py).

* a synthetic clip in a soft-shadow style (cream text with a soft drop shadow over a moving test pattern): exact first
  / last frames and text; a pop-in whose first frame is misread and a highlighted word stay one caption; a caption
  that grows word by word is a new caption at each new word; the same word twice is two captions;
* the rules on hand-made data: the video's writing conventions, joining runs, a lone bar, the transcript fallback;
* the acceptance test on input/competitor.mp4 against tests/fixtures/competitor_captions_truth.srt, the answer key
  written by eye from contact sheets of every frame: every caption READ with its text identical and its first and last
  frame within one frame; the file WRITTEN keeps the competitor's words, timing and gaps, in sentence case (the video is
  in ALL CAPS) with the weak last words moved, and breaks none of the hard rules 1-4.
"""
from __future__ import annotations

import re
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
REAL = Path(__file__).resolve().parents[3] / "input" / "competitor.mp4"
TRUTH_SRT = Path(__file__).resolve().parent / "fixtures" / "competitor_captions_truth.srt"
# the zones layout.py measures on input/competitor.mp4 (1080x1920, 60 fps, 1965 frames)
REAL_LAYOUT = {"zones": [{"type": "logo", "x": 310, "y": 222, "w": 116, "h": 118},
                         {"type": "channel_name", "x": 450, "y": 250, "w": 298, "h": 84},
                         {"type": "title", "x": 168, "y": 400, "w": 742, "h": 118},
                         {"type": "captions", "x": 306, "y": 1090, "w": 468, "h": 50,
                          "notes": "110 caption events; white text with dark outline, median glyph height 42 px"}]}

W, H, N = 720, 1280, 150
TRUTH = [("Deadpool", 0, 10), ("builds a team", 10, 35), ("the X-Force", 35, 60), ("you see", 60, 70),
         ("you see him", 70, 90), ("no", 90, 105), ("no", 105, 120), ("*laughs*", 125, 150)]
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
        f"drawtext={st}:text='you see':fontsize=44:{ctr}:enable='between(n,60,69)'",            # grows word by word:
        f"drawtext={st}:text='you see him':fontsize=44:{ctr}:enable='between(n,70,89)'",        # two captions
        f"drawtext={st}:text='no':fontsize=44:{ctr}:enable='between(n,90,104)'",
        f"drawtext={st}:text='no':fontsize='{pop.format(a=105)}':{ctr}:enable='between(n,105,119)'",
        f"drawtext={st}:text='*laughs*':fontsize=44:{ctr}:enable='between(n,125,149)'",
    ])
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", f"testsrc2=s={W}x{H}:r=30:d={N / 30}",
                    "-vf", vf, "-c:v", "libx264", "-crf", "20", "-pix_fmt", "yuv420p", str(out)], check=True)
    return out


@need_ocr
def test_spans_and_text_are_the_on_screen_captions_exactly(clip):
    res = caption_ocr.read_caption_spans(str(clip), LAYOUT, (W, H), Fraction(30), N)
    assert [(d["ocr"], d["comp_in"], d["comp_out"]) for d in res["spans"]] == TRUTH


# ---------------------------------------------------------------------------------------------
# the rules
# ---------------------------------------------------------------------------------------------

CAPS_VIDEO = ["SO AS A", "JOKE", "\u201cWHAT DYA\u201d", "DIDN'T", "SPIDER-MAN", "*LAUGHS*", "I HAVE NO IDEA"]


def test_the_videos_conventions_fix_single_frame_misreadings():
    conv = caption_ocr.screen_conventions(CAPS_VIDEO + ["\u201cTHINK\"", "sO"])
    assert conv == {"all_caps": True, "curly_quotes": True, "apostrophe": "'"}
    fix = lambda t: caption_ocr.apply_conventions(t, conv)                       # noqa: E731
    assert fix("sO") == "SO" and fix("\u0131") == "I"                            # lower-case misreads
    assert fix("\u201cTHINK\"") == "\u201cTHINK\u201d"                        # a closing quote read straight
    assert fix("\"WHAT\u2019S\"") == "\u201cWHAT'S\u201d"                    # both quotes, the apostrophe style
    assert fix("\u201cMAN?\u201d") == "\u201cMAN?\u201d" and fix("SPIDER-MAN") == "SPIDER-MAN"
    mixed = caption_ocr.screen_conventions(["I got a Parker Peter", "whoa!! x2", "Spider-Man is", "no"])
    assert mixed["all_caps"] is False and caption_ocr.apply_conventions("sO", mixed) == "sO"


def test_a_lone_bar_is_a_capital_i_and_a_one_is_not():
    bar = np.zeros((60, 40), bool)
    bar[10:50, 15:25] = True
    one = bar.copy()
    one[10:18, 7:15] = True                                                      # the flag of a 1
    assert caption_ocr.lone_bar(bar, 40.0) and not caption_ocr.lone_bar(one, 40.0)


def test_runs_join_into_captions():
    fps = Fraction(60)
    widths = np.array([60, 80] + [100] * 20 + [70, 90] + [100] * 10)
    runs = [[0, 1], [1, 2], [2, 12], [12, 22], [22, 23], [23, 24], [24, 34]]
    # pop-in frames misread ("SCH0OL") or unreadable, a run broken by noise, then the same word popping in again
    texts = ["SCH0OL", "", "SCHOOL", "SCHOOL", "", "SCHOOL", "SCHOOL"]
    alike = lambda i, j: 0.9                                                     # noqa: E731
    assert caption_ocr.join_runs(runs, texts, widths, fps, alike) == [[0, 1, 2, 3], [4, 5, 6]]
    # a short run of other words is its own caption, however alike
    assert caption_ocr.join_runs([[0, 10], [10, 14], [14, 30]], ["I", "I SHOULD", "GO"], np.full(30, 100), fps,
                                 alike) == [[0], [1], [2]]
    # an unreadable run that looks like neither neighbour stays a caption of its own
    assert caption_ocr.join_runs([[0, 10], [10, 14], [14, 30]], ["A", "", "B"], np.full(30, 100), fps,
                                 lambda i, j: 0.2) == [[0], [1], [2]]


def test_only_unreadable_captions_take_the_words_heard_and_they_are_listed():
    spans = [{"comp_in": 0, "comp_out": 30, "ocr": "\u201cWHAT DO YOU SAY\u201d", "score": 0.97},
             {"comp_in": 30, "comp_out": 60, "ocr": "", "score": 0.1},
             {"comp_in": 60, "comp_out": 90, "ocr": "I'M", "score": 0.95},
             {"comp_in": 90, "comp_out": 120, "ocr": "", "score": 0.0}]
    words = [C.Word("what", 0.1, 0.3, 0.9, "what"), C.Word("think", 0.6, 0.8, 0.9, "think,"),
             C.Word("im", 1.1, 1.3, 0.9, "I'm")]
    caps, notes = C.competitor_copy(spans, words, Fraction(60), lambda k: k, Fraction(60))
    assert [(c.text, c.start, c.end) for c in caps] == [("\u201cWHAT DO YOU SAY\u201d", 0, 30), ("THINK", 30, 60),
                                                         ("I'M", 60, 90)]
    assert notes == {"from_transcript": [{"start_tc": "00:00:00,500", "end_tc": "00:00:01,000", "text": "THINK"}],
                     "unreadable": [{"start_tc": "00:00:01,500", "end_tc": "00:00:02,000"}]}


# ---------------------------------------------------------------------------------------------
# acceptance: input/competitor.mp4 against the answer key
# ---------------------------------------------------------------------------------------------

@need_ocr
@pytest.mark.skipif(not REAL.is_file(), reason="input/competitor.mp4 not in this checkout")
def test_real_competitor_captions_match_the_answer_key(tmp_path, monkeypatch):
    from match_cuts import transcribe
    from match_cuts.common import Cache
    from match_cuts.config import Config

    def no_transcript(*a, **k):
        raise AssertionError("every caption is readable: no transcript needed")
    monkeypatch.setattr(transcribe, "transcribe_words", no_transcript)
    cfg = Config(out_dir=str(tmp_path / "out"), work_dir=str(tmp_path / "work"), premiere=True, captions="auto")
    info = types.SimpleNamespace(path=str(REAL), file_hash="competitor", width=1080, height=1920,
                                 display_width=1080, display_height=1920)
    warnings: list[str] = []
    ctx = types.SimpleNamespace(cfg=cfg, comp_info=info, comp_fps=Fraction(60), n_comp=1965,
                                cutlist=types.SimpleNamespace(layout=REAL_LAYOUT), cache=Cache(cfg.work),
                                raw_audio=None, audio_sr=16000, warn=warnings.append)
    res = C.run_captions(ctx)
    assert res["mode"] == "competitor" and not warnings

    def frames(blocks):
        return [(b["text"], round(b["start_ms"] * 60 / 1000), round(b["end_ms"] * 60 / 1000)) for b in blocks]
    key = frames(C.read_srt(TRUTH_SRT))
    assert len(key) == 112
    # read: every caption's text as written and its first / last frame (within one frame)
    spans = C._read_spans(ctx, REAL_LAYOUT, Fraction(60))["spans"]
    read = [(d["ocr"], d["comp_in"], d["comp_out"]) for d in spans]
    exact = [g for g, k in zip(read, key) if g[0] == k[0] and abs(g[1] - k[1]) <= 1 and abs(g[2] - k[2]) <= 1]
    assert len(read) == len(key) and len(exact) == len(key), [(g, k) for g, k in zip(read, key) if g not in exact]
    assert res["competitor_notes"] == {"from_transcript": [], "unreadable": []}
    # written: the same words in the same order, the competitor's timing and gaps, sentence case, rules 1-4 kept
    got = frames(C.parse_srt(Path(res["path"]).read_text(encoding="utf-8")))
    assert len(got) == len(key)

    def words(rows):
        return [w for t, _, _ in rows for w in re.sub(r"[^a-z0-9' -]", "", t.lower().replace("’", "'")).split()]
    assert words(got) == words(key)
    covered = {f for _, a, b in key for f in range(a, b)}
    assert {f for _, a, b in got for f in range(a, b)} == covered                    # the gaps kept
    starts = {a for _, a, _ in key}
    moved = [(g, k) for g, k in zip(got, key) if g[1] != k[1]]
    assert len(moved) == res["rules"]["changed"][7] == 11                          # only where a weak word moved
    assert all(g[0].split()[0].lower() in C.WEAK for g, _ in moved) and not {a for _, a, _ in got} & (
        {k[1] for _, k in moved} - starts - {g[1] for g, _ in moved})
    assert not [t for t, _, _ in got if re.search(r"\b[A-Z]{2,}\b", t)]               # no ALL CAPS left
    assert got[0][0] == "So as" and got[1][0] == "a joke" and ("I had", 524, 545) in got
    from match_cuts.caption_rules import check
    caps = [C.Caption(t, a, b, "competitor") for t, a, b in got]
    assert not any(check(caps, Fraction(60), "competitor", rules=(1, 2, 3, 4)).values())
