"""Night 15, task 2: WRONG PAIR and right pairs, on the real cases.

* **Dr. Dolittle 2 (YTS) + the TikTok / the Instagram reel DdVZePAga2I** -- the stop was RIGHT: that download is the
  1998 film under the 2001 name (its subtitles are the 1998 film's, 85 min, its frames are the 1998 YIFY file's frame
  for frame); both reels are Dr. Dolittle 2 (Raven-Symone, the report card and the phone). They stay WRONG PAIRs.
* **Family Guy, the wrong episodes** (the darkhumor reel with S11E01 and with S08E11 -- both runs ended NOT IN THIS RAW
  on the laptop after the long search): the quick check let them go on ("too little to be sure": the look-alike rooms
  of another episode are almost found here and there). Now caught (``pair_check.chain``): no frame found, fewer than 3
  of the almost found ones agreeing in order and timing, nothing of the sound.
* **The Jumanji promo** (Kevin Hart dressed up as The Rock: not in the film): still a WRONG PAIR.
* **Right pairs** -- American Dad's tilted / zoomed reel, Blue Mountain State, The Suite Life, every check-all case --
  go on (measured on 37 true and 90 crossed pairs: no true pair stopped).

The fixtures (tests/data/pairs_night15) are the real checks' results (the frames' evidence kept); the end-to-end tests
on the real files run where D:\\Shared is (this PC) and are skipped elsewhere."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from match_cuts import pair_check as PC

DATA = Path(__file__).resolve().parent / "data" / "pairs_night15"


def _load(name: str) -> PC.Result:
    d = json.loads((DATA / f"{name}.json").read_text(encoding="utf-8"))
    r = PC.Result.from_dict(d)
    if r.picture is not None:
        r.picture.details["chain"] = PC.chain(r.picture.details.get("frames") or [])
    return r


@pytest.mark.parametrize("name", ["X__darkhumor__fg08", "X__darkhumor__fg11", "X__video6-fixed__fg11",
                                  "X__jumanji_promo", "X__dol_tiktok", "X__dol_reel"])
def test_real_wrong_pairs_are_stopped(name):
    r = _load(name)
    assert PC.decide(r.sound, r.picture), r.summary()


@pytest.mark.parametrize("name", ["T__amdadreel", "T__video020-fixed", "T__video5", "T__video4", "T__mike005-fixed",
                                  "T__bms", "T__suite"])
def test_real_right_pairs_go_on(name):
    r = _load(name)
    assert not PC.decide(r.sound, r.picture), r.summary()


def test_the_family_guy_look_alikes_do_not_agree_and_a_true_reel_does():
    fg = _load("X__darkhumor__fg08").picture
    assert fg.state == PC.UNCLEAR and fg.found == 0 and fg.weak >= 8       # almost found a lot ...
    assert fg.details["chain"] <= 2                                      # ... but here and there
    ad = _load("T__amdadreel").picture
    assert ad.details["chain"] >= 5                                      # the tilted reel's frames follow its scene


def test_a_found_look_alike_shot_with_no_sound_still_goes_on():
    """The safe side: video19's competitor (Family Guy S11E10) has 3 frames really found in S08E11 (a shot that comes
    back in many episodes) -- with frames found, the check is not sure, so the run goes on (the long search says NOT
    IN THIS RAW)."""
    r = _load("X__video19-fixed__fg08")
    assert r.picture.found >= 2 and not PC.decide(r.sound, r.picture)


def test_chain_rules():
    f = lambda at, raw, m=False, found=False: {"at": at, "raw_at": raw, "mirrored": m, "weak": True, "found": found}
    assert PC.chain([]) == 0
    assert PC.chain([f(1, 100), f(3, 102), f(5, 104), f(7, 106)]) == 4          # one scene in order
    assert PC.chain([f(1, 100), f(3, 100), f(5, 112)]) == 3                     # the same keyframe, then the next
    assert PC.chain([f(1, 500), f(3, 100), f(5, 900), f(7, 300)]) == 1          # here and there: none agree
    assert PC.chain([f(1, 100), f(3, 101, True), f(5, 102)]) == 2               # mirrored one way, then the other
    assert PC.chain([{"at": 1, "raw_at": 5, "weak": False, "found": False}]) == 0   # not even almost found


def test_the_sound_must_be_empty_for_the_new_stop():
    r = _load("X__darkhumor__fg08")
    s = PC.Side(PC.UNCLEAR, 10, 1, weak=1)                               # one piece of the sound found: not sure
    assert not PC.decide(s, r.picture)
    s = PC.Side(PC.UNCLEAR, 10, 0, weak=2)                               # two pieces almost found: not sure
    assert not PC.decide(s, r.picture)
    assert PC.decide(PC.Side(PC.NOT_FOUND, 10, 0), r.picture)


L = Path("D:/Shared/laptop_cases")
FG = L / "2026-10-21_The Most Accurate Prediction of All Time 🔮😭"
D2 = L / "2026-10-20_When Your Dad Takes Your Phone Away 📱😭"
AD = L / "2026-10-21_The Most Dramatic Dad of All Time 💀😭"
REAL = [
    (FG / "Video by darkhumor [DYZ2Vtgxi3n].mp4",
     FG / "Family Guy - S11E01 - Into Fat Air [1080p Web x265][AAC 5.1][Sub][Ch].mp4", True),
    (D2 / "tiktok_cultureratedpv_7357048330179841323.mp4",
     D2 / "Dr..Dolittle.2.2001.1080p.BluRay.x264.AAC5.1-[YTS.GG - YTS.BZ].mp4", True),
    (AD / "Video by cinemaclips991 [DczA1q3MTge].mp4",
     AD / "American Dad! - S03E02 - The American Dad After School Special - [HDTV-1080p]h265 AAC.mp4", False),
]


@pytest.mark.slow
@pytest.mark.parametrize("comp, raw, wrong", REAL, ids=["fg_wrong_episode", "dolittle_1998_as_2", "american_dad"])
def test_the_real_files(comp, raw, wrong):
    if not (comp.is_file() and raw.is_file()):
        pytest.skip("the real files are on the PC's D:\\Shared only")
    res = PC.check(comp, raw, None)
    assert res.wrong == wrong, res.summary()
