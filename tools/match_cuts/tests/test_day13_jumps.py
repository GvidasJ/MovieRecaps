"""Day 13 (the lead, learned from your fixes): XML SYNC "the RAW skips 42 ms at the cut ... one take, cut" -- video14 at
00:00:20:12 and video16 (Always Sunny) at 00:00:04:13 and 00:00:07:04 failed their hard check with it. The competitor
cut about two RAW frames out of one take (83 ms in the 60 fps plan: a real cut to join_takes), the RAW's rate (23.976)
rounds that to one frame, and the sync check calls a one-frame skip inside one take a stutter. At all three your fixes
play the take straight through. 1_edit.xml now plays the skipped frame -- with the clip whose RAW shot it is -- and
everything after the cut comes a frame later (A1, markers, captions too); the checked 60 fps edit keeps the jump."""
from __future__ import annotations

import math
from fractions import Fraction
from types import SimpleNamespace

from match_cuts import export_xml_edl as ex, shots
from match_cuts.model import Segment
from test_day13_editnotes import F60, R24, _a1, _clip24, _framed, _shape, config, cutlist, framing


def three(y_in: int = 1051):
    """S01 plays RAW 1000-1049, S02 (the same take, another framing) from ``y_in``, S03 another moment."""
    x = _framed(_clip24(0, 50, 1000, 1), 0.0)
    y = _framed(_clip24(50, 100, y_in, 2), 40.0)
    z = _framed(_clip24(100, 150, 5000, 3), 0.0)
    return [x, y, z]


def at(pm: dict, p: int) -> int:                     # a planning-grid position on the delivered sequence
    return pm.get(p, int(math.floor(Fraction(p) * R24 / F60 + Fraction(1, 2))))


def test_a_skipped_last_frame_of_a_shot_is_played_by_the_clip_before():
    clips = three()
    audio = _a1(clips)
    markers = [{"name": "m", "in": 60, "out": 70}, {"name": "p", "in": 10, "out": -1}]
    changes = shots.seconds([900, 4990, 6000], R24)
    pm: dict[int, int] = {}
    got, a2, m2, n, done = ex.fix_take_jumps(clips, audio, markers, 150, pm, 375, F60, R24, R24, changes)
    assert _shape(got) == [(0, 51, 1000, 1051), (51, 101, 1051, 1101), (101, 151, 5000, 5050)]
    assert [(a["start"], a["end"], a["in"], a["out"]) for a in a2] == [(0, 51, 1000, 1051), (51, 101, 1051, 1101),
                                                                       (101, 151, 5000, 5050)]
    assert [(m["in"], m["out"]) for m in m2] == [(61, 71), (10, -1)]
    assert n == 151
    assert done == [{"clips": "S01|S02", "at": 51, "was": 50, "frames": 1, "ms": 42, "to": "before", "put": 50}]
    assert at(pm, 125) == 51 and at(pm, 100) == 40                  # captions after the cut a frame later, not before
    assert audio[0]["end"] == 50 and markers[0]["in"] == 60          # the inputs are not changed
    assert ex._flash_lines(got, changes, R24, R24) == []
    line = ex.edit_fix_lines([], None, R24, None, (), done)[0]
    assert line == ("1 skipped frame put back at 00:00:02:03 (S01|S02): the RAW jumped 42 ms inside one take at that "
                    "cut -- the take now plays straight through, picture and sound, as in your fixes (the edit is 1 "
                    "frame longer)")


def test_a_skipped_first_frame_of_the_next_shot_is_played_by_the_clip_after():
    clips = three()
    changes = shots.seconds([900, 1050, 4990, 6000], R24)          # the skipped RAW 1050 starts S02's shot
    got, a2, _m, n, done = ex.fix_take_jumps(clips, _a1(clips), [], 150, {}, 375, F60, R24, R24, changes)
    assert _shape(got) == [(0, 50, 1000, 1050), (50, 101, 1050, 1101), (101, 151, 5000, 5050)]
    assert [(a["start"], a["end"], a["in"]) for a in a2] == [(0, 50, 1000), (50, 101, 1050), (101, 151, 5000)]
    assert n == 151 and done[0]["at"] == 50 and done[0]["to"] == "after"
    assert ex._flash_lines(got, changes, R24, R24) == []


def test_jumps_that_stay_as_they_are():
    def fixed(clips, audio=None, changes=shots.seconds([900, 4990, 6000], R24)):
        return ex.fix_take_jumps(clips, _a1(clips) if audio is None else audio, [], 150, {}, 375, F60, R24, R24,
                                 changes)[4]
    assert fixed(three(1052)) == []                                 # 2 frames: the competitor's real cut
    assert fixed(three(1050)) == []                                 # no jump
    x, y, z = three()
    assert fixed([x, y, ex.dataclasses.replace(z, src_in=1045, src_out=1095)]) == []   # another clip shows RAW 1050
    line = [{"start": 0, "end": 150, "in": 1000, "out": 1150, "speed": 1.0, "what": "audio line"}]
    assert fixed([x, y, z], line) == []                             # a sound line runs on under the cut
    assert fixed([ex.dataclasses.replace(x, speed=1.25), y, z]) == []                  # not at 100 %
    assert fixed([x, y, z], changes=shots.seconds([900, 1050, 1051, 6000], R24)) == []  # RAW 1050: a shot of its own
    assert fixed([x, y, z], changes=None) == []                     # the RAW's shots unknown: nothing is moved
    try:
        ex.TAKE_JUMP_FIX = False
        assert fixed([x, y, z]) == []
    finally:
        ex.TAKE_JUMP_FIX = True


def test_after_jumps_moves_the_other_fixes_positions():
    end_cut = {"frames": 2, "at": 98, "label": "S03", "was": 100}
    flashes = [{"clips": "S00|S01", "from": 20, "to": 22, "frames": 2}, {"clips": "S02|S03", "from": 80, "to": 78,
                                                                        "frames": 2}]
    e2, f2 = ex._after_jumps(end_cut, flashes, [{"at": 51, "was": 50, "frames": 1}])
    assert e2 == {"frames": 2, "at": 99, "label": "S03", "was": 101}
    assert [(m["from"], m["to"]) for m in f2] == [(20, 22), (81, 79)]


def plan_like_video14(shot_at: int) -> tuple:
    """Two pieces of one take, the competitor cut 1.6 RAW frames out of it, a RAW shot change at RAW ``shot_at`` --
    1_edit.xml at 23.976 skips one frame at the cut (the night-7 rounding)."""
    t0 = 240 / float(R24)
    segs = [Segment(1, "raw", 0, 120, raw_in_seconds=t0, transform=framing(-96.7)),
            Segment(2, "raw", 120, 300, raw_in_seconds=t0 + 2.0 + 1.6 / float(R24), transform=framing(-246.7))]
    return cutlist(segs, "24000/1001", 300, raw_frames=3000), shots.seconds([100, shot_at, 2000], R24)


def test_the_delivered_edit_plays_the_take_through_and_passes(tmp_path):
    for shot_at in (287, 288):                     # the skipped frame ends S01's shot / starts S02's
        cl, sh = plan_like_video14(shot_at)
        cfg = config(tmp_path, sh)
        base, final = tmp_path / "edit_60fps.xml", tmp_path / "1_edit.xml"
        ex.write_premiere_xml(cl, base, cfg)
        assert not [p for p in ex.premiere_sync_problems(base)[0] if "one take" in p]   # the checked edit: a cut
        try:
            ex.TAKE_JUMP_FIX = False
            ex.write_premiere_xml(cl, final, cfg, seq_rate=R24)
            old = ex.final_xml_problems(base, final, 1.0, sh, R24)
            assert any("XML SYNC V1" in p and "skips 42 ms" in p for p in old), old    # the hard check's note
            n_old = ex.parse_premiere_xml(final)["duration"]
        finally:
            ex.TAKE_JUMP_FIX = True
        res = ex.write_premiere_xml(cl, final, cfg, seq_rate=R24)
        assert [j["frames"] for j in res["take_jumps"]] == [1], (shot_at, res["take_jumps"])
        puts = [(j["put"], j["frames"]) for j in res["take_jumps"]]
        assert ex.final_xml_problems(base, final, 1.0, sh, R24, put_back=puts) == []
        x = ex.parse_premiere_xml(final)
        assert x["duration"] == n_old + 1
        v = sorted(x["clips"], key=lambda c: c["start"])
        assert all(int(q["in"]) == int(p["out"]) for p, q in zip(v, v[1:]) if p["end"] == q["start"]
                   and abs(int(q["in"]) - int(p["out"])) <= 2)                      # the take runs on
        assert res["edit_fixes"] and "skipped frame put back" in res["edit_fixes"][-1]


def test_the_run_writes_and_checks_it(tmp_path):
    """pipeline.apply_final at the RAW's rate: 1_edit.xml plays the take through and passes its hard checks (the rate
    check knows the frame put back); the fix is in the end summary and report.md."""
    from match_cuts import pipeline, report
    from match_cuts.common import DecisionLog
    cl, sh = plan_like_video14(287)
    run = tmp_path / "001"
    (run / "extras").mkdir(parents=True)
    cfg = config(tmp_path, sh, out_dir=str(run / "extras"), deliver_dir=str(run), premiere_seq_rate=str(R24))
    xml = run / "1_edit.xml"
    r60 = ex.write_premiere_xml(cl, xml, cfg)
    (run / "2_captions.srt").write_text("1\n00:00:01,000 --> 00:00:03,000\none\n\n2\n00:00:03,000 --> 00:00:05,000\n"
                                        "two\n", encoding="utf-8")
    ctx = SimpleNamespace(cfg=cfg, premiere_cutlist=cl, silence={}, premiere_xml=dict(r60), raw_fps=R24,
                          captions={"path": str(run / "2_captions.srt")}, paths={"xml": str(xml)},
                          dlog=DecisionLog(run / "extras" / "decisions.jsonl"), exports={"ok": True, "errors": []},
                          people=None, cutlist=cl, broll=None, comp_fps=Fraction(60))
    out = pipeline.apply_final(ctx, 1.0, R24)
    assert out["problems"] == [], out["problems"]
    assert any("skipped frame put back" in s for s in ctx.premiere_xml["edit_fixes"])
    assert any("Fixed in the edit by itself" in s for s in report._premiere_checks(ctx))
