"""The cut score (edit_score.py): the user's cut points reproduced within 2 frames, compared by the RAW."""
from __future__ import annotations

import json

import pytest

from match_cuts import edit_score as E


def edit(*pieces, fps=30.0, raw_fps=25.0):
    """pieces: (t0, t1, raw second at t0 or None, speed)"""
    return E.Edit([E.Piece(a, b, r, v) for a, b, r, v in pieces], fps, raw_fps)


def test_cuts_are_where_the_raw_jumps():
    e = edit((0.0, 2.0, 10.0, 1.0),                   # a take ...
             (2.0, 3.0, 12.0, 1.0),                   # ... that runs on: no cut
             (3.0, 4.0, 20.0, 1.0),                   # a jump: a cut (13.0 -> 20.0)
             (4.0, 4.066, None, 1.0),                 # two frames of other footage between two pieces that run on
             (4.066, 5.0, 21.066, 1.0),               # (an uncertain frame of the analysis): no cut
             (5.0, 6.0, None, 1.0),                   # other footage: a cut out of the RAW and one back into it
             (6.0, 7.0, 30.0, 0.5))
    cuts = [(round(c.t, 3), c.out if c.out is None else round(c.out, 3), c.into) for c in e.cuts()]
    assert cuts == [(3.0, 13.0, 20.0), (5.0, 22.0, None), (6.0, None, 30.0)]
    assert e.duration == 7.0


def test_a_cut_is_reproduced_when_both_raw_moments_are_within_two_frames():
    key = edit((0.0, 2.0, 10.0, 1.0), (2.0, 4.0, 20.0, 1.0), (4.0, 6.0, 30.0, 1.0))       # cuts 12->20, 22->30
    got = edit((0.0, 2.05, 10.0, 1.0), (2.05, 4.3, 20.05, 1.0),                         # 12.05->20.05: 1.5 frames
               (4.3, 7.0, 31.0, 1.0))                                                    # 22.3->31: 0.3 s / 1 s off
    sc = E.score(key, got)
    assert (sc.cuts, sc.reproduced, sc.near, sc.others) == (2, 1, 0, 1)
    assert sc.length_diff == pytest.approx(1.0)
    assert "1/2 of your cuts within 2 frames" in sc.line() and "1.0 s longer" in sc.line()
    near = edit((0.0, 2.2, 10.0, 1.0), (2.2, 4.0, 20.2, 1.0), (4.0, 6.0, 30.0, 1.0))      # 12.2->20.2: trimmed
    sc = E.score(key, near)
    assert (sc.reproduced, sc.near) == (1, 1)
    assert sc.misses == [{"t": 2.0, "out": 12.0, "into": 20.0, "got_out": 12.2, "got_into": 20.2}]


def test_cuts_match_by_the_raw_in_any_order():
    key = edit((0.0, 1.0, 10.0, 1.0), (1.0, 2.0, 50.0, 1.0))
    got = edit((0.0, 3.0, 5.0, 1.0), (3.0, 4.0, 10.0, 1.0), (4.0, 5.0, 50.0, 1.0))      # later in its own edit
    assert E.score(key, got).reproduced == 1


def test_answer_edit_json_and_xml(tmp_path):
    p = tmp_path / "answer_edit.json"
    p.write_text(json.dumps({"track": "picture", "fps": 30, "raw_fps": 25, "audio": [
        {"start": 0, "end": 2, "kind": "raw", "src_in": 10.0, "speed": 1.0},
        {"start": 2, "end": 3, "kind": "other", "src_in": 2.0, "speed": 1.0},
        {"start": 3, "end": 5, "kind": "raw", "src_in": 40.0, "speed": 1.0}]}), encoding="utf-8")
    e = E.Edit.from_answer(p)
    assert (e.what, e.fps, e.raw_fps) == ("picture", 30.0, 25.0)
    assert [(c.out, c.into) for c in e.cuts()] == [(12.0, None), (None, 40.0)]
    xml = tmp_path / "edit.xml"                        # a finished XML: the sequence inside a project
    xml.write_text("""<xmeml version="4"><project><children><sequence><rate><timebase>30</timebase><ntsc>FALSE</ntsc>
      </rate><duration>150</duration><media><video><track>
        <clipitem><start>0</start><end>60</end><in>250</in><out>300</out><rate><timebase>25</timebase></rate></clipitem>
        <clipitem><start>60</start><end>150</end><in>1000</in><out>1075</out><rate><timebase>25</timebase></rate></clipitem>
      </track></video><audio><track>
        <clipitem><start>0</start><end>150</end><in>0</in><out>150</out></clipitem>
      </track></audio></media></sequence></children></project></xmeml>""", encoding="utf-8")
    pic = E.Edit.from_xml(xml, "picture")
    assert [(round(c.out, 3), round(c.into, 3)) for c in pic.cuts()] == [(12.0, 40.0)]
    assert pic.duration == pytest.approx(5.0)
    assert E.Edit.from_xml(xml, "sound").cuts() == []


def test_the_cuts_both_edits_make_record_how_each_trims_them():
    """The user's RAW moment minus the other edit's, at every cut both make (within 2 frames or 0.5 s): out < 0 the
    user leaves earlier, into > 0 the user comes in later."""
    key = edit((0.0, 2.0, 10.0, 1.0), (2.0, 4.0, 20.0, 1.0), (4.0, 6.0, 30.0, 1.0))       # cuts 12->20, 22->30
    got = edit((0.0, 2.2, 10.0, 1.0), (2.2, 4.0, 19.9, 1.0), (4.0, 6.0, 30.0, 1.0))       # 12.2->19.9, 21.7->30
    d = E.score(key, got).to_dict()
    assert d["trims"] == [{"t": 2.0, "out": -0.2, "into": 0.1}, {"t": 4.0, "out": 0.3, "into": 0.0}]
    assert d["out_median"] == pytest.approx(0.05) and d["into_median"] == pytest.approx(0.05)
