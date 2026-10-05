"""caption_score.py: the caption score against an answer key (task 4) -- timelines, exact captions, word errors, rule
breaks, the differences by type, and the end summary's lines."""
from __future__ import annotations

import gzip
import json
from pathlib import Path

import pytest

from match_cuts import caption_score as S
from match_cuts.model import Cutlist, Segment


def caps(rows):
    return [S.Cap(t, a, b) for t, a, b in rows]


def tl(*pieces):
    return S.Timeline([S.Piece(*p) for p in pieces])


# ---- timelines ------------------------------------------------------------------------------------------------------

def test_timeline_at_and_find():
    t = tl((0.0, 1.0, "raw", 10.0), (1.0, 2.0, "raw", 20.0), (3.0, 4.0, "comp", 5.0))
    assert t.at(0.5) == ("raw", 10.5)
    assert t.at(1.25) == ("raw", 20.25)
    assert t.at(2.5) is None                                    # a gap: nothing plays
    assert t.at(3.5) == ("comp", 5.5)
    assert t.find("raw", 20.5) == pytest.approx((1.5, 0.0))
    at, d = t.find("raw", 15.0)                                 # cut out of this edit: the nearest piece edge
    assert d == pytest.approx(4.0) and at == pytest.approx(1.0)   # RAW 11 s (the first piece's end) is nearest
    assert t.find("comp", 5.25) == pytest.approx((3.25, 0.0))
    assert t.duration == 4.0


def test_timeline_find_repeated_moment_follows_the_order():
    t = tl((0.0, 1.0, "raw", 10.0), (1.0, 2.0, "raw", 10.0))   # the same RAW second played twice
    assert t.find("raw", 10.5)[0] == pytest.approx(0.5)
    assert t.find("raw", 10.5, after=1.2)[0] == pytest.approx(1.5)   # the next caption comes after 1.2 s


def test_timeline_json_roundtrip(tmp_path):
    t = tl((0.0, 1.0, "raw", 10.0, 1.0), (1.0, 2.5, "comp", 3.0))
    p = tmp_path / "e.json"
    p.write_text(json.dumps({"audio": t.to_json()}), encoding="utf-8")
    back = S.Timeline.from_json(p)
    assert [(q.t0, q.t1, q.kind, q.src) for q in back.pieces] == [(0.0, 1.0, "raw", 10.0), (1.0, 2.5, "comp", 3.0)]


XML_TOOL = """<?xml version="1.0"?><xmeml version="4"><sequence><rate><timebase>60</timebase><ntsc>FALSE</ntsc></rate>
<media><video><track/></video><audio><track>
<clipitem id="a1"><name>S01</name><start>0</start><end>60</end><in>600</in><out>660</out></clipitem>
<clipitem id="a2"><name>S02</name><start>60</start><end>120</end><in>1200</in><out>1260</out></clipitem>
</track></audio></media></sequence></xmeml>"""

XML_PREMIERE = """<?xml version="1.0"?><xmeml version="4"><project><name>x</name><children>
<clip id="m1"><name>raw.mp4</name></clip>
<sequence id="sequence-1"><rate><timebase>60</timebase><ntsc>FALSE</ntsc></rate><media><video><track>
<clipitem id="v1"><start>0</start><end>90</end><in>300</in><out>390</out></clipitem></track></video>
<audio><track><clipitem id="c1"><start>0</start><end>90</end><in>300</in><out>390</out></clipitem></track></audio>
</media></sequence></children></project></xmeml>"""


def test_timeline_from_the_tools_xml_and_a_premiere_export(tmp_path):
    a = tmp_path / "tool.xml"
    a.write_text(XML_TOOL, encoding="utf-8")
    t = S.Timeline.from_xml(a, [{"a": 120, "b": 180, "t0": 7.0}])
    assert t.at(0.5) == ("raw", pytest.approx(10.5))
    assert t.at(1.5) == ("raw", pytest.approx(20.5))
    assert t.at(2.5) == ("comp", pytest.approx(7.5))           # another video's stretch: the competitor's seconds
    b = tmp_path / "premiere.xml"
    b.write_text(XML_PREMIERE, encoding="utf-8")                # the sequence inside a project
    assert S.Timeline.from_xml(b).at(1.0) == ("raw", pytest.approx(6.0))


def test_competitor_timeline_follows_the_audio_maps():
    segs = [Segment(1, "raw", 0, 30, raw_in_seconds=10.0),
            Segment(2, "raw", 30, 60, raw_in_seconds=50.0),
            Segment(3, "dip", 60, 90)]
    segs[0].audio["out_offset_frames"] = 3                      # an L cut: the first segment's sound plays on
    segs[1].audio["in_offset_frames"] = 3                       # 3 frames into the second's picture
    cl = Cutlist(1, {"fps": "30", "frames": 90}, {"fps": "30"}, {}, segs)
    t = S.competitor_timeline(cl)
    assert t.at(0.5) == ("raw", pytest.approx(10.5))
    assert t.at(1.05) == ("raw", pytest.approx(10.0 + 1.05))     # the first segment's audio until its offset ends
    assert t.at(1.5) == ("raw", pytest.approx(50.5))
    assert t.at(2.5) is None                                     # a dip plays no RAW


def test_competitor_timeline_puts_pieces_found_in_the_sound_in_the_pictures_time():
    # the competitor's file plays its sound 54 ms after its picture: a B-roll replacement placed where its sound
    # plays (broll.py) sits 54 ms early in the RAW against the shots around it, and a cut of the sound between two
    # such pieces is heard 54 ms after its editor made it
    segs = [Segment(1, "raw", 0, 30, raw_in_seconds=10.0),
            Segment(2, "raw", 30, 60, raw_in_seconds=20.0),
            Segment(3, "raw", 60, 90, raw_in_seconds=40.0)]
    for s in segs[1:]:
        s.audio["broll"] = {"replaced": "NOT-IN-RAW", "line": "audio found at RAW", "how": "audio", "heard": True}
    cl = Cutlist(1, {"fps": "30", "frames": 90}, {"fps": "30"}, {}, segs)
    cl.audio = {"av_offset": {"status": "measured", "lag_ms": -54.0}}
    t = S.competitor_timeline(cl)
    assert t.at(0.5) == ("raw", pytest.approx(10.5))                     # a RAW shot: in its picture's time
    assert t.at(1.0 + 1e-4) == ("raw", pytest.approx(20.054, abs=1e-3))  # the picture's cut stays
    assert t.at(1.94) == ("raw", pytest.approx(20.0 + 0.94 + 0.054))     # found in the sound: + 54 ms
    assert t.at(1.95) == ("raw", pytest.approx(40.0 - 0.05 + 0.054))     # the sound's cut, 54 ms earlier
    del cl.audio["av_offset"]
    assert S.competitor_timeline(cl).at(1.95) == ("raw", pytest.approx(20.95))   # no offset measured: as found


# ---- the score ------------------------------------------------------------------------------------------------------

def test_exact_within_two_frames_and_text():
    key_tl = tl((0.0, 10.0, "raw", 100.0))
    got_tl = tl((0.0, 10.0, "raw", 100.0))
    key = caps([("So as", 0.0, 0.5), ("a joke", 0.5, 1.0), ("I", 1.0, 1.5), ("suggested", 1.5, 2.0)])
    got = caps([("So as", 0.0, 0.5), ("a joke", 0.533, 1.0),       # 2 frames late: still exact
                ("I", 1.06, 1.5),                                  # 3.6 frames late: timing
                ("Suggested", 1.5, 2.0)])                          # the same words, other capitals: casing
    sc = S.score("t", key, key_tl, got, got_tl)
    assert sc.exact == 2 and sc.key == 4
    assert sc.by_type() == {"casing": 1, "timing": 1}
    assert sc.wer == 0.0                                           # word errors ignore case
    assert "2/4 of your captions exactly (50 %)" in sc.line()


def test_score_in_raw_time_across_a_different_cut():
    # the user's edit cut 0.5 s of silence the tool kept: the same captions still match, at the tool's own times
    key_tl = tl((0.0, 1.0, "raw", 100.0), (1.0, 3.0, "raw", 101.5))
    got_tl = tl((0.0, 3.5, "raw", 100.0))
    key = caps([("hello there", 0.2, 1.0), ("my friend", 1.2, 2.0)])
    got = caps([("hello there", 0.2, 1.7), ("my friend", 1.7, 2.5)])
    sc = S.score("t", key, key_tl, got, got_tl)
    assert sc.exact == 2


def test_split_words_edit_and_extra():
    key_tl = tl((0.0, 6.0, "raw", 0.0))
    got_tl = tl((0.0, 3.0, "raw", 0.0), (3.0, 9.0, "raw", 10.0))   # the tool's edit leaves out RAW 3..10 s ...
    key = caps([("to Marvel that", 0.0, 1.0), ("completely", 1.0, 2.0), ("seriously", 2.0, 3.0),
                ("gone", 3.5, 4.0)])                               # ... where the user captioned "gone"
    got = caps([("to", 0.0, 0.4), ("Marvel that", 0.4, 1.0),       # the same words, split differently
                ("totally", 1.0, 2.0), ("seriously", 2.0, 3.0),
                ("applause", 3.0, 4.0)])                           # RAW 10 s: the user's edit does not play it
    sc = S.score("t", key, key_tl, got, got_tl)
    types = {d["key"]: d["type"] for d in sc.diffs if "key" in d}
    assert types == {"to Marvel that": "split", "completely": "words", "gone": "edit"}
    assert sc.exact == 1
    assert not [d for d in sc.diffs if d["type"] == "extra"]       # not counted: the user's edit has nothing there
    assert sc.subs == 1 and sc.ins == 0 and sc.dels == 0           # "completely" -> "totally"; "gone" not compared


def test_extra_caption_where_the_user_has_none():
    t = tl((0.0, 5.0, "raw", 0.0))
    sc = S.score("t", caps([("hi", 0.0, 1.0)]), t, caps([("hi", 0.0, 1.0), ("*laughs*", 3.0, 4.0)]), t)
    assert [d["type"] for d in sc.diffs] == ["extra"]


def test_style_rules():
    rows = S.style_rules(caps([("a very long caption text", 0.0, 1.0),     # 24 characters, 4 words
                               ("one two three four five six", 1.0, 2.0),  # 6 words
                               ("yes, sure.", 2.0, 2.5),                   # punctuation
                               ("3.5 million", 2.5, 3.0),                  # a number's point is fine
                               ("*laughs, loudly*", 3.0, 3.5),             # a sound: exempt
                               ("gap after", 3.5, 3.7), ("next", 4.0, 4.5),
                               ("overlap", 4.4, 5.0)]))
    text = "\n".join(rows)
    assert "24 characters" in text and "6 words" in text and "'yes, sure.': a full stop or comma" in text
    assert "3.5 million" not in text and "*laughs" not in text
    assert "'gap after': a gap of 0.300 s" in text and "'next': overlaps the next caption" in text
    allowed = S.style_rules(caps([("gap after", 3.5, 3.7), ("next", 4.0, 4.5)]), allowed_gaps=[(3.7, 4.0)])
    assert allowed == []                                            # another video's stretch: the gap is allowed


def test_norm_and_exact_text():
    assert S.norm_words("I’m Spider-Man, ok?") == ["i'm", "spider", "man", "ok"]
    assert S.exact_text("I’m\nhere ") == "I'm here"
    assert S.word_edits(["a", "b", "c"], ["a", "x", "c", "d"]) == (1, 1, 0)


def test_score_run_and_summary(tmp_path):
    run = tmp_path / "001"
    (run / "extras" / "debug").mkdir(parents=True)
    (run / "1_edit.xml").write_text(XML_TOOL, encoding="utf-8")
    (run / "2_captions.srt").write_text("1\n00:00:00,000 --> 00:00:00,500\nhello\n\n"
                                        "2\n00:00:00,500 --> 00:00:01,000\nthere\n", encoding="utf-8")
    (run / "extras" / "debug" / "captions.json").write_text(json.dumps({"other_video": []}), encoding="utf-8")
    key = tmp_path / "answer.srt"
    key.write_text("1\n00:00:00,000 --> 00:00:01,000\nhello there\n", encoding="utf-8")
    sc = S.score_run("x", key, tl((0.0, 2.0, "raw", 10.0)), run)
    assert sc.exact == 0 and sc.by_type() == {"split": 1}
    lines = S.summary_lines("x", sc)
    assert lines[0].startswith("x: 0/1 of your captions exactly") and lines[1].startswith("split (1): yours 'hello there'")


def test_a_key_caption_a_frame_before_a_cut_starts_on_the_cut():
    # the user's caption starts one 60 fps frame before the cut its words start after (a 30 fps competitor frame
    # rounded on the 60 fps grid): it is matched where the tool's edit plays the moment just after the cut
    key_tl = tl((0.0, 1.0333, "raw", 50.0), (1.0333, 3.0, "raw", 68.0))
    got_tl = tl((0.0, 1.2, "raw", 50.0), (1.2, 3.2, "raw", 68.0))         # the tool played on 10 frames longer
    key = caps([("the X-Force", 61 / 60, 2.0)])
    got = caps([("the X-Force", 1.2, 2.2)])
    assert S.score("t", key, key_tl, got, got_tl).exact == 1
