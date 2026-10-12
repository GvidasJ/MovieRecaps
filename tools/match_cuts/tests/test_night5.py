"""Night 5: no transition ever (every dissolve a hard cut; XML TRANSITION), the pieces of one continuous RAW take
played as one (join_takes: laptop009's competitor, sped up to 125 % with many tiny cuts), and A1 each picture's own
sound with no jump or stutter at a cut (XML SYNC)."""
from __future__ import annotations

import copy
import json
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from match_cuts import export_xml_edl as ex
from match_cuts.config import Config
from match_cuts.keep_speed import keep_speed
from match_cuts.model import Cutlist, Segment

from test_keep_speed import PAN, cutlist

DATA = Path(__file__).parent / "data"


def laptop009() -> Cutlist:
    """The competitor's cut list of laptop009 (output/laptop009, thorough): 48 pieces in 24.8 s."""
    d = json.loads((DATA / "laptop009_cutlist.json").read_text(encoding="utf-8"))
    return Cutlist(1, d["competitor"], d["raw"], d["layout"], [Segment.from_dict(s) for s in d["segments"]])


def _plan(cl: Cutlist) -> tuple[Cutlist, list[dict]]:
    """As pipeline.premiere_plan: no transitions, the takes joined, every clip at 100 %, the takes exact again."""
    cl, _ = ex.no_transitions(cl)
    cl, takes, joined = ex.join_takes(cl)
    kc, _ = keep_speed(cl)
    return ex.snap_takes(kc, takes), joined


def _write(tmp_path: Path, kc: Cutlist, name: str = "1_edit.xml") -> tuple[Path, Config]:
    cfg = Config(out_dir=str(tmp_path), premiere=True)
    p = tmp_path / name
    ex.write_premiere_xml(kc, p, cfg)
    return p, cfg


def _dissolve(cl: Cutlist, frames: int = 6) -> Cutlist:
    """S02 starts ``frames`` before S01 ends, joined by a cross dissolve (the competitor's)."""
    a, b = cl.segments[0], cl.segments[1]
    b.comp_in = int(a.comp_out) - frames
    b.raw_in_seconds = 511.0 - frames * 1.25 / 60.0
    a.transition_out = {"type": "crossfade", "duration_frames": frames}
    b.transition_in = {"type": "crossfade", "duration_frames": frames}
    return cl


# ---------------------------------------------------------------------------------------------------------------------
# no transitions
# ---------------------------------------------------------------------------------------------------------------------

def test_a_cross_dissolve_becomes_a_hard_cut_on_its_middle_frame(tmp_path):
    cl = _dissolve(cutlist())
    hard, done = ex.no_transitions(cl)
    s1, s2 = sorted(hard.segments, key=lambda s: s.comp_in)[:2]
    assert (s1.comp_out, s2.comp_in) == (301, 301)                   # 298..304: the middle frame
    assert s2.raw_in_seconds == pytest.approx(511.0 - 3 * 1.25 / 60.0)   # S02 on its own time line from there
    assert not any(s.transition_in or s.transition_out for s in hard.segments)
    assert done == [{"cut": 301, "segments": [1, 2], "frames": 6, "raw": [pytest.approx(503.52 + 301 * 1.25 / 60, abs=1e-3),
                                                                        pytest.approx(511.0 - 3 * 1.25 / 60, abs=1e-3)]}]
    assert cl.segments[0].transition_out                              # the input (the faithful cut list) keeps it
    kc, _ = _plan(cl)
    xml, cfg = _write(tmp_path, kc)
    assert ET.parse(xml).getroot().find(".//transitionitem") is None
    assert ex.premiere_transition_problems(xml) == []


def test_a_fade_to_black_is_dropped():
    cl = cutlist()
    cl.segments[2].transition_out = {"type": "fade", "duration_frames": 10}
    hard, done = ex.no_transitions(cl)
    assert not any(s.transition_in or s.transition_out for s in hard.segments)
    assert [d["cut"] for d in done] == [None] and done[0]["segments"] == [3]
    assert [(s.comp_in, s.comp_out) for s in hard.segments] == [(s.comp_in, s.comp_out) for s in cl.segments]


def test_the_writer_never_writes_a_transition(tmp_path):
    kc, _ = keep_speed(_dissolve(cutlist()))                          # a cut list that still has the dissolve
    xml, cfg = _write(tmp_path, kc)
    assert ex.premiere_transition_problems(xml) == []
    edl = tmp_path / "e.edl"
    ex.write_edl(kc, edl, cfg)
    v = ex.validate_premiere_exports(kc, xml, edl, cfg)
    assert v["ok"], v["errors"]


def test_the_transition_check_fails_any_dissolve(tmp_path, monkeypatch):
    kc, _ = keep_speed(_dissolve(cutlist()))
    monkeypatch.setattr(ex, "no_transitions", lambda cl: (cl, []))    # the writer as before night 5
    xml, cfg = _write(tmp_path, kc)
    probs = ex.premiere_transition_problems(xml)
    assert any("Cross Dissolve" in p for p in probs)
    assert any("start / end -1" in p for p in probs)
    edl = tmp_path / "e.edl"
    ex.write_edl(kc, edl, cfg)
    v = ex.validate_premiere_exports(kc, xml, edl, cfg)
    assert not v["ok"] and any(e.startswith("XML TRANSITION") for e in v["errors"])
    assert v["transition_problems"] == probs


def test_a_repeat_at_a_dissolve_is_cut_now(tmp_path):
    # laptop008's XML REPEAT (A1 S29 / S30): two pieces of one take joined by a cross dissolve, the second starting 0.1 s
    # back in the RAW -- the repeat could not be cut inside the dissolve and stayed. With the dissolve a hard cut it goes
    from match_cuts import repeats
    cl = cutlist()
    a, b = cl.segments[0], cl.segments[1]
    b.comp_in = int(a.comp_out) - 4
    b.raw_in_seconds = 503.52 + int(b.comp_in) * 1.25 / 60 - 0.1
    a.transition_out = {"type": "crossfade", "duration_frames": 4}
    b.transition_in = {"type": "crossfade", "duration_frames": 4}
    kc, _ = keep_speed(cl)
    cfg = Config(out_dir=str(tmp_path), premiere=True)
    plan = repeats.add_to_plan({}, kc, cfg)
    assert plan["repeats"]["left"] == [] and [r["removed"] for r in plan["repeats"]["rows"]] == ["S02"]
    xml = tmp_path / "1_edit.xml"
    ex.write_premiere_xml(kc, xml, cfg, plan["ripple"])
    assert ex.premiere_repeat_problems(xml) == [] and ex.premiere_transition_problems(xml) == []


def test_a_clip_between_two_dissolves_is_read_at_its_own_place(tmp_path, monkeypatch):
    # laptop008: three clips that started and ended inside cross dissolves (start and end -1) were read by the link
    # check at -00:00:00:01, running to the first dissolve of the sequence -- false XML LINK failures
    monkeypatch.setattr(ex, "no_transitions", lambda cl: (cl, []))    # an XML written as before night 5
    cl = _dissolve(cutlist())
    b, c = cl.segments[1], cl.segments[2]
    c.comp_in = int(b.comp_out) - 4
    c.raw_in_seconds = 600.0 - 4 / 60.0
    b.transition_out = {"type": "crossfade", "duration_frames": 4}
    c.transition_in = {"type": "crossfade", "duration_frames": 4}
    kc, _ = keep_speed(cl)
    xml, _ = _write(tmp_path, kc)
    x = ex.parse_premiere_xml(xml)
    mid = next(c for c in x["clips"] if c["start"] == -1 and c["end"] == -1)
    t0, t1 = sorted(t["start"] for t in x["transitions"])
    assert mid["rec"] == (t0, t1)
    rng = ex._track_ranges(x["clips"], x["transitions"])
    assert all(0 <= a < b for a, b in rng) and rng[1] == (t0, t1)
    probs, _ = ex.premiere_link_problems(xml)
    assert not any("-00:00:00:01" in p for p in probs), probs


# ---------------------------------------------------------------------------------------------------------------------
# one continuous RAW take plays as one (laptop009)
# ---------------------------------------------------------------------------------------------------------------------

def _takes(cl: Cutlist) -> list[list[int]]:
    _, take, _ = ex.join_takes(cl)
    groups: dict[int, list[int]] = {}
    for s in sorted(cl.segments, key=lambda s: s.comp_in):
        groups.setdefault(take.get(int(s.id), int(s.id)), []).append(int(s.id))
    return list(groups.values())


def test_laptop009_pieces_of_one_take_are_one_take():
    # S04-S11 and S14-S17: a still picture matched anywhere near (uncertain, measured speed 0) -- played over, as you
    # did; S44 (5 frames, uncertain) too; every other piece meets the one before within a RAW frame or two
    assert _takes(laptop009()) == [list(range(1, 12)), list(range(12, 20)), list(range(20, 43)), [43, 44], [45],
                                   [46, 47, 48]]


def test_laptop009_takes_play_straight_through_at_100_percent(tmp_path):
    kc, joined = _plan(laptop009())
    assert {d["how"] for d in joined} == {"continues", "played over"}
    segs = sorted(kc.segments, key=lambda s: s.comp_in)
    by_id = {int(s.id): s for s in segs}
    for take in ([1, 11], [12, 19], [20, 42], [46, 48]):             # each piece starts where the one before ends
        for a, b in zip(range(take[0], take[1]), range(take[0] + 1, take[1] + 1)):
            sa, sb = by_id[a], by_id[b]
            end = float(sa.raw_in_seconds) + float(sa.speed) * (sb.comp_in - sa.comp_in) / 60.0
            assert sb.raw_in_seconds == pytest.approx(end, abs=1e-6), (a, b)
            assert sa.speed == 1.0 and sb.speed == 1.0
    assert by_id[12].raw_in_seconds == pytest.approx(708.8967, abs=1e-3)   # real cuts keep their own moment
    assert by_id[20].raw_in_seconds == pytest.approx(736.0058, abs=1e-3)
    # S03's take runs on over the still picture: 707.2 s on to 708.33 s (your fix: 707.200 - 708.350)
    s11 = by_id[11]
    assert float(s11.raw_in_seconds) + (s11.comp_out - s11.comp_in) / 60.0 == pytest.approx(708.33, abs=0.02)
    xml, cfg = _write(tmp_path, kc)
    x = ex.parse_premiere_xml(xml)
    assert len(x["clips"]) == 6 and len(x["audio"]) == 6                 # was 16 clips with 17 ms jumps between
    assert ex.premiere_sync_problems(xml)[0] == []
    assert ex.premiere_transition_problems(xml) == []
    edl = tmp_path / "e.edl"
    ex.write_edl(kc, edl, cfg)
    v = ex.validate_premiere_exports(kc, xml, edl, cfg)
    assert v["ok"], v["errors"]


def test_a_take_joins_jumps_of_at_most_two_and_a_half_raw_frames_and_0_1_s():
    assert ex.take_jump_s("24000/1001") == pytest.approx(0.1)            # 2.5 frames = 104 ms: 0.1 s caps it
    assert ex.take_jump_s("30000/1001") == pytest.approx(2.5 * 1.001 / 30)
    assert ex.take_jump_s(60) == pytest.approx(2.5 / 60)
    assert ex.take_jump_s(None) == pytest.approx(ex.TAKE_JUMP_S)


def test_a_real_cut_and_a_far_sliver_stay():
    def seg(i, a, b, raw, **kw):
        return Segment(id=i, type="raw", comp_in=a, comp_out=b, raw_in_seconds=raw, speed=kw.pop("speed", 1.25),
                       speed_measured=kw.pop("sm", 1.25), transform=dict(PAN), confidence=.9, **kw)
    cl = cutlist()
    cl.segments = [seg(1, 0, 120, 100.0),
                   seg(2, 120, 240, 100.0 + 2.5 + 0.3),                   # 0.3 s after S01's end: a real cut
                   seg(3, 240, 246, 300.0, sm=0.0, uncertain=True),       # a doubted sliver far away: kept
                   seg(4, 246, 700, 102.8 + 2.5 + 6 * 1.25 / 60)]
    assert _takes(cl) == [[1], [2], [3], [4]]


def test_a_real_slow_motion_inside_a_take_keeps_its_speed():
    def seg(i, a, b, raw, v):
        return Segment(id=i, type="raw", comp_in=a, comp_out=b, raw_in_seconds=raw, speed=v, speed_measured=v,
                       transform=dict(PAN), confidence=.9)
    cl = cutlist()
    cl.segments = [seg(1, 0, 120, 100.0, 1.0), seg(2, 120, 144, 102.0, 0.075),
                   seg(3, 144, 700, 102.0 + 24 * 0.075 / 60 + 0.02, 1.0)]
    out, take, joined = ex.join_takes(cl)
    s = sorted(out.segments, key=lambda s: s.comp_in)
    assert [x.speed for x in s] == [1.0, 0.075, 1.0]                      # zendaya-age's slow motion stays slow
    assert take == {2: 1, 3: 1}
    assert s[2].raw_in_seconds == pytest.approx(102.0 + 24 * 0.075 / 60)


# ---------------------------------------------------------------------------------------------------------------------
# XML SYNC: A1 is each picture's own sound, no jump or stutter at a cut
# ---------------------------------------------------------------------------------------------------------------------

def _edit(xml: Path, out: Path, fn) -> Path:
    tree = ET.parse(xml)
    fn(tree.getroot())
    tree.write(out)
    return out


def test_the_sync_check_catches_a_jump_a_desync_and_an_overlap(tmp_path):
    kc, _ = _plan(laptop009())
    xml, _ = _write(tmp_path, kc)
    assert ex.premiere_sync_problems(xml)[0] == []

    def skip(root):                       # the second V1 / A1 clip continues the first one 1 tick (17 ms) on
        for tr in ("video", "audio"):
            c1, c2 = root.findall(f"sequence/media/{tr}/track/clipitem")[:2]
            n = int(c2.findtext("out")) - int(c2.findtext("in"))
            c2.find("in").text = str(int(c1.findtext("out")) + 1)
            c2.find("out").text = str(int(c1.findtext("out")) + 1 + n)
    probs, _ = ex.premiere_sync_problems(_edit(xml, tmp_path / "skip.xml", skip))
    assert any("skips 17 ms" in p for p in probs)

    def desync(root):                     # A1 three frames off its picture
        ci = root.findall("sequence/media/audio/track/clipitem")[2]
        ci.find("in").text = str(int(ci.findtext("in")) + 3)
        ci.find("out").text = str(int(ci.findtext("out")) + 3)
    probs, _ = ex.premiere_sync_problems(_edit(xml, tmp_path / "desync.xml", desync))
    assert any("not the picture's own" in p for p in probs)

    def overlap(root):                    # A1 clip 2 runs 5 frames into clip 3
        ci = root.findall("sequence/media/audio/track/clipitem")[1]
        ci.find("end").text = str(int(ci.findtext("end")) + 5)
        ci.find("out").text = str(int(ci.findtext("out")) + 5)
    probs, _ = ex.premiere_sync_problems(_edit(xml, tmp_path / "overlap.xml", overlap))
    assert any("overlap" in p for p in probs)


def test_the_old_laptop009_edit_fails_the_sync_check(tmp_path):
    # the cut list as before tonight (no takes joined): pieces of one take started each on its own RAW time
    kc, _ = keep_speed(copy.deepcopy(laptop009()))
    xml, _ = _write(tmp_path, kc)
    probs, _ = ex.premiere_sync_problems(xml)
    assert any("ms at the cut" in p for p in probs)
