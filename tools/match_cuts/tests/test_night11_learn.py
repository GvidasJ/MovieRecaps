"""Night 11, item 4: what was learned from your fixes of video11, video12 and chocolate.

* R-C5: no "!" in captions made from the heard words (you took out 10 of the 11 the tool wrote) -- a lone name or an
  interjection called out keeps it; the competitor's own text (followed) keeps it.
* R-C4: follow mode -- a heard word written otherwise on the competitor's screen ("scrumptious" heard, "scrunchous"
  shown) counts as that word when a caption boundary is settled by the screen.
* S-1 (check-all's scoring): a key made from a 23.976 delivery is compared with the tool's captions placed where
  Premiere's SRT import puts them (one frame early), as yours were (case.json "srt_rate")."""
from __future__ import annotations

import math
import sys
from fractions import Fraction
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from match_cuts import caption_rules as R  # noqa: E402
from match_cuts import caption_score as CS  # noqa: E402
from match_cuts import caption_style as S  # noqa: E402
from match_cuts import testcases  # noqa: E402


def _cap(text: str, mode: str = "voice") -> R.Cap:
    return R.Cap(0, 30, [R.Tok(t, t) for t in text.split()], mode, {})


def test_r_c5_takes_the_exclamation_marks_out_of_heard_captions_but_not_a_called_name_or_an_interjection():
    rep = R.Report(Fraction(60), "voice")
    caps = [_cap("put those away!"), _cap("you!"), _cap("Ron!"), _cap("whoa!"), _cap("of d*ck! no")]
    R._strip_exclaims(caps, rep)                                                     # noqa: SLF001
    assert [c.text for c in caps] == ["put those away", "you", "Ron!", "whoa!", "of d*ck no"]
    assert rep.notes["exclaims"] == 3


def test_r_c5_is_only_for_captions_made_from_the_heard_words():
    from match_cuts.captions import Caption
    caps = [Caption("put those away!", 60, 120)]
    voice, _ = R.enforce(caps, Fraction(60), "voice")
    comp, _ = R.enforce(caps, Fraction(60), "competitor")
    assert "!" not in voice[0].text and comp[0].text.endswith("!")


def test_r_c4_a_heard_word_written_otherwise_on_the_screen_goes_to_the_caption_that_shows_it():
    w = lambda t, a, b: SimpleNamespace(text=t, start=a, end=b)                      # noqa: E731
    words = [w("Chocolate", 0.0, 0.4), w("is", 0.4, 0.6), w("scrumptious", 0.6, 1.2)]
    owner = [0, 0, 0]                                                                # by time: all in the first
    S._by_screen(words, owner, ["Chocolate is", "scrunchous"])                       # noqa: SLF001
    assert owner == [0, 0, 1]
    owner = [0, 0, 0]                                                                # a word nowhere on screen: stays
    S._by_screen(words, owner, ["Chocolate is", "the best"])                         # noqa: SLF001
    assert owner == [0, 0, 0]


@pytest.mark.parametrize("rate, frame, placed", [
    (Fraction(24000, 1001), 66, 65),     # 23.976: every caption one frame early
    (Fraction(24000, 1001), 10, 9),
    (Fraction(30), 37, 36),              # 30: 1233.33 ms written 1.233 -> frame 36
    (Fraction(30), 36, 36),              # 1200 ms: on time
    (Fraction(60), 60, 60),
])
def test_s_1_places_a_caption_where_premieres_srt_import_puts_it(rate, frame, placed):
    t = math.floor(frame / rate * 60 + 0.5) / 60                                     # the plan's 60 fps time
    got = CS.premiere_placed(t, float(rate))
    assert round(got * float(rate)) == placed


def test_learn_marks_a_key_from_a_23_976_sequence_for_the_premiere_placed_scoring(tmp_path, monkeypatch):
    import json
    from match_cuts import learn
    from match_cuts import prproj as PR
    monkeypatch.setattr(testcases, "small_copy", lambda s, d: (Path(d).write_bytes(b"x"), {"bytes": 1})[1])
    comp, raw = tmp_path / "c.mp4", tmp_path / "r.mp4"
    comp.write_bytes(b"c")
    raw.write_bytes(b"r")
    for name, fps, want in (("ntsc", 24000 / 1001, "24000/1001"), ("thirty", 30.0, None)):
        learn.write_case(tmp_path / name, comp, raw, PR.Sequence("s", fps, 1080, 1920), "r.mp4", [], {"style": "new"},
                         no_caps_why="a test")
        meta = json.loads((tmp_path / name / "case.json").read_text(encoding="utf-8"))
        assert meta.get("srt_rate") == want, name


def test_s_1_is_read_from_case_json_and_only_the_23_976_keys_have_it():
    assert testcases._rate("24000/1001") == pytest.approx(23.976, abs=1e-3)          # noqa: SLF001
    assert testcases._rate(None) is None and testcases._rate("fast") is None           # noqa: SLF001
    rates = {c.name: c.srt_rate for c in testcases.cases()}
    for name in ("video7-fixed", "video8-fixed", "video9-fixed", "video10-fixed", "video11-fixed", "video12-fixed"):
        assert rates[name] == pytest.approx(24000 / 1001), name
    assert rates["chocolate-fixed"] is None and rates["mike005-fixed"] is None
