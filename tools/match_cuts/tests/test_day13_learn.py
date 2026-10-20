"""Day 13, section 5: learned from video16-19 (the learn queue) and video20 (mindculpt's fix, Desktop\\cop.prproj).
What your fixes changed that became a rule: three words you write censored, as you wrote them (video14 "bit*h" --
day 12 missed it --, video19 "murde*ed", video20 "cra*kheads"). The five new test cases are in the library with
their channel template and watermark, scored as new-style cases."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from match_cuts import captions as C, testcases

REPO = Path(__file__).resolve().parents[3]
NEW = ("video16-fixed", "video17-fixed", "video18-fixed", "video19-fixed", "video20-fixed")


@pytest.mark.parametrize("heard, written", [
    ("you bitch", "you bit*h"), ("Bitches be like", "Bit*hes be like"), ("he got murdered", "he got murde*ed"),
    ("5 crackheads", "5 cra*kheads"), ("a crackhead", "a cra*khead"), ("what the fuck", "what the fu*k"),
])
def test_your_censored_words_are_written_as_you_write_them(heard, written):
    assert C.censor_text(heard, C.censor_table()) == written


@pytest.mark.parametrize("kept", ["murder", "kill him", "crack the code", "dead", "shit"])
def test_words_your_fixes_never_censored_stay(kept):
    assert C.censor_text(kept, C.censor_table()) == kept


def test_your_fixes_hold_exactly_these_forms():
    keys = {n: (REPO / "tests" / "real" / n / "answer.srt").read_text(encoding="utf-8-sig") for n in NEW}
    assert "murde*ed" in keys["video19-fixed"] and "cra*kheads" in keys["video20-fixed"]
    assert "bit*h" in (REPO / "tests" / "real" / "video14-fixed" / "answer.srt").read_text(encoding="utf-8-sig")


def test_the_new_cases_are_in_the_library_scored_with_their_template_and_watermark():
    cases = {c.name: c for c in testcases.cases()}
    for n in NEW:
        c = cases[n]
        assert c.style == "new", n
        d = REPO / "tests" / "real" / n
        for f in ("competitor.mp4", "raw.mp4", "answer.srt", "answer_edit.json", "frame.png", "watermark.png"):
            assert (d / f).is_file(), (n, f)
        opts = [str(o) for o in c.options]
        assert "--frame" in opts and "--watermark" in opts, (n, opts)
        cj = json.loads((d / "case.json").read_text(encoding="utf-8"))
        assert "learned on day 13" in cj["notes"]
        assert cj.get("srt_rate") in (None, "24000/1001")
