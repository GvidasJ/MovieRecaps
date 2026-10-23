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
    ("kill him", "k*ll him"), ("their arses", "their ars*s"),     # day 16: video31 (no key has them uncensored)
])
def test_your_censored_words_are_written_as_you_write_them(heard, written):
    assert C.censor_text(heard, C.censor_table()) == written


@pytest.mark.parametrize("kept", ["murder", "killer", "crack the code", "dead", "shit"])   # (day 16: "kill" is
#                                                                                   censored now -- video31's k*ll)
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


# ---------------------------------------------------------------------------------------------------------------------
# 6.2 Liquid Death: your project played the RAW straight from where it was downloaded, under its own name
# ---------------------------------------------------------------------------------------------------------------------

def test_learn_finds_the_runs_raw_played_under_another_name(tmp_path):
    from types import SimpleNamespace
    from match_cuts import learn, prproj as PR
    raw = tmp_path / "run" / "extras" / "media" / "raw.mp4"
    raw.parent.mkdir(parents=True)
    raw.write_bytes(b"x" * 4096)
    yours = tmp_path / "Shared" / "Liquid Death “Small Ones” Commercial [zUAvOywcPus].mp4"
    yours.parent.mkdir()
    yours.write_bytes(b"x" * 4096)                                      # the same file, its YouTube name
    other = tmp_path / "Shared" / "enhanced.mp4"
    other.write_bytes(b"y" * 1000)
    item = lambda m, a, b: PR.Item(PR.VIDEO, 1, a, b, media=str(m))     # noqa: E731
    seq = PR.Sequence("Recreated Edit (Premiere)", 25.0, 1080, 1920,
                      [item(yours, 0.0, 5.16), item(yours, 5.16, 6.6), item(other, 6.6, 7.0)])
    cl = {"raw": {"width": 3840, "height": 2160, "fps": "24000/1001", "duration_s": 46.96}}
    pr = PR.Project(str(tmp_path / "x.prproj"), [seq],
                    {str(yours): {"width": 3840, "height": 2160, "fps": 23.976, "duration": 46.96},
                     str(other): {"width": 1080, "height": 1920, "fps": 30.0, "duration": 0.4}})
    assert learn.same_file_name(pr, raw, cl) == yours.name
    # another size (another file under any name) or another length: not the run's RAW
    yours.write_bytes(b"x" * 4000)
    assert learn.same_file_name(pr, raw, cl) is None
    yours.write_bytes(b"x" * 4096)
    pr.media[str(yours)]["duration"] = 30.0
    assert learn.same_file_name(pr, raw, cl) is None
    assert learn.same_file_name(SimpleNamespace(sequences=[], media={}), raw, cl) is None


def test_the_liquid_death_case_runs_with_your_pick_of_the_raw():
    cases = {c.name: c for c in testcases.cases()}
    c = cases["liquid-death-fixed"]
    assert c.style == "new" and "--allow-short-raw" in [str(o) for o in c.options]
    d = REPO / "tests" / "real" / "liquid-death-fixed"
    key = json.loads((d / "answer_edit.json").read_text(encoding="utf-8"))
    clips = [x for x in key["audio"] if x["kind"] == "raw"]
    assert len(clips) == 13 and abs(clips[-1]["end"] - 40.24) < 0.05     # your 13 clips, 40.24 s
