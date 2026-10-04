"""The Premiere export's hard checks and repeat removal (export_xml_edl.premiere_item_problems /
premiere_repeat_problems, repeats.py):

* a reversed clip is written as Premiere reads it (in < out, the reverse flag), so its audio is not skipped -- the
  bug_edit.xml of a real run had S10's V1 and A1 items with in > out;
* every item: whole frames, start < end, in < out, matching lengths, inside its media, no overlap, never two audio
  clips at the same moment; every V1 clip has its audio on A1 unless it was removed on purpose (listed);
* no RAW footage or audio plays twice: a stutter at a cut is always trimmed, the same moment over 0.5 s twice loses
  the copy out of chronological order (else the later one) unless --allow-repeats; the run fails on what is left.
"""
from __future__ import annotations

import re
import sys
from fractions import Fraction
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_export_xml_edl as T  # noqa: E402

from match_cuts import export_xml_edl as ex, repeats  # noqa: E402
from match_cuts.config import Config  # noqa: E402
from match_cuts.model import Cutlist, Segment  # noqa: E402

BUG = Path(__file__).resolve().parents[3] / "bug_edit.xml"
FPS = Fraction(60)


def cutlist(segs, frames=None) -> Cutlist:
    """A competitor at 30 fps (sequence frames = 2 x competitor frames) over a 30 fps RAW of 100 s."""
    comp = {"file": "media/competitor_ref.mp4", "width": 608, "height": 1080, "fps": "30/1",
            "frames": frames or max(int(s.comp_out) for s in segs)}
    raw = {"file": "media/raw.mp4", "file_abs": "/abs/media/raw.mp4", "width": 1920, "height": 1080,
           "fps": "30/1", "frames": 3000, "has_audio": True, "audio_sample_rate": 48000, "audio_channels": 2}
    layout = {"mode": "match", "layout_kind": "boxed", "box": dict(T.PBOX), "background": "solid",
              "background_detail": {"type": "solid", "color": "#000000"}, "canvas_bg": "#000000"}
    return Cutlist(1, comp, raw, layout, segs)


def seg(id_, a, b, raw_s, **kw) -> Segment:
    """A RAW segment: competitor frames [a, b) playing the RAW from raw_s seconds (raw_s x 30 must be whole)."""
    return T._grid_seg(id_, a, b, int(round(raw_s * 30)), **kw)


def export(tmp_path, cl, name="1_edit.xml", **cfg_kw):
    """The pipeline's order: the repeats join the (here empty) silence plan, the XML is written with that ripple and
    validated against it."""
    cfg = Config(out_dir=str(tmp_path), premiere=True, **cfg_kw)
    plan = repeats.add_to_plan({}, cl, cfg)
    xml = tmp_path / name
    ex.write_premiere_xml(cl, xml, cfg, plan.get("ripple"))
    v = ex.validate_premiere_exports(cl, xml, None, cfg, plan.get("ripple"))
    return plan, xml, v, ex.parse_premiere_xml(xml)


# ---------------------------------------------------------------------------------------------
# The bug: S10's audio skipped by Premiere
# ---------------------------------------------------------------------------------------------

@pytest.mark.skipif(not BUG.is_file(), reason="bug_edit.xml not in this checkout")
def test_the_hard_checks_find_what_was_wrong_in_bug_edit_xml():
    assert ex.premiere_item_problems(BUG) == [
        "V1 S10 raw.mp4 at 00:00:08:57: in 1312 is not before out 1300",
        "A1 S10 raw.mp4 audio at 00:00:08:57: in 1312 is not before out 1300"]
    # and the stutters at its cuts: S04 shows S03's RAW again, S05 S04's (S13 starts on S12's last RAW frame, 722 of
    # the 30 fps RAW: that frame held a moment longer, not shown again)
    assert ex.premiere_repeat_problems(BUG) == [
        "V1 S03 at 00:00:05:11 and S04 at 00:00:05:27 both play RAW 14.27-14.43 s: a stutter at a cut",
        "V1 S04 at 00:00:05:29 and S05 at 00:00:05:57 both play RAW 14.30-14.47 s: a stutter at a cut"]


def test_a_reversed_clip_is_written_with_in_before_out_and_its_audio_is_kept(tmp_path):
    rev = Segment(id=2, type="raw", comp_in=30, comp_out=36, raw_in_seconds=60.0, speed=-1.0, time_mode="remap",
                  confidence=.97, transform=dict(T.PAN0),
                  time_remap_keys=[{"comp_frame": 30, "raw_seconds": 60.0}, {"comp_frame": 36, "raw_seconds": 59.8}])
    cl = cutlist([seg(1, 0, 30, 10.0), rev, seg(3, 36, 60, 80.0)])
    plan, xml, v, x = export(tmp_path, cl)
    assert v["ok"], v["errors"]
    assert v["item_problems"] == [] and v["repeat_problems"] == [] and v["audio_exceptions"] == []
    import xml.etree.ElementTree as ET
    root = ET.parse(xml).getroot()
    for kind in ("video", "audio"):
        item = [e for e in root.iter("clipitem") if ex.item_label(e) == "S02"
                and (e.find("sourcetrack/mediatype").text == kind)][0]
        a, b = int(item.findtext("in")), int(item.findtext("out"))
        # RAW 60.0 s played backwards for 12 sequence frames: frames 3589..3600 (at 60 fps), shown 3600 first
        assert (a, b) == (3589, 3601) and a < b
        assert [p.findtext("value") for p in item.iter("parameter") if p.findtext("parameterid") == "reverse"] == ["TRUE"]
    s2 = [c for c in x["clips"] if c["label"] == "S02"][0]
    assert (s2["in"], s2["out"], s2["speed"]) == (3600, 3588, -1.0)      # parsed back to the plan's frames
    assert ex.plan_in_out(*ex.xml_in_out(3600, 3588), True) == (3600, 3588)
    assert ex.xml_in_out(100, 112) == (100, 112)                          # forward: unchanged


def test_the_fcp7_export_writes_reversed_clips_the_same_way(tmp_path):
    cl = T.make_cutlist()
    xml, edl = tmp_path / "e.xml", tmp_path / "e.edl"
    ex.write_fcp7_xml(cl, xml)
    ex.write_edl(cl, edl)
    import xml.etree.ElementTree as ET
    items = [e for e in ET.parse(xml).getroot().iter("clipitem") if (e.findtext("name") or "").startswith("S10")]
    assert items and all(int(e.findtext("in")) < int(e.findtext("out")) for e in items)
    assert ex.validate_exports(cl, xml, edl)["ok"]


# ---------------------------------------------------------------------------------------------
# The hard item check
# ---------------------------------------------------------------------------------------------

def _broken(xml: Path, tmp_path: Path, n: int, field: str, value: str, kind: str = "clipitem") -> Path:
    """xml with the n-th clip item's <field> set to value."""
    text = xml.read_text(encoding="utf-8")
    items = list(re.finditer(rf'<{kind} id="clipitem-{n}">.*?</{kind}>', text, re.S))
    body = items[0].group(0)
    new = re.sub(rf"<{field}>[^<]*</{field}>", f"<{field}>{value}</{field}>", body, count=1)
    out = tmp_path / f"broken_{n}_{field}.xml"
    out.write_text(text.replace(body, new), encoding="utf-8")
    return out


def test_every_item_must_be_one_premiere_can_import(tmp_path):
    cl = cutlist([seg(1, 0, 30, 10.0), seg(2, 30, 60, 30.0), seg(3, 60, 90, 50.0)])
    plan, xml, v, x = export(tmp_path, cl)
    assert v["ok"] and ex.premiere_item_problems(xml) == []
    cases = {("2", "in"): "1800.5", ("2", "end"): "60", ("3", "out"): "2950", ("a2", "in"): "1810",
             ("3", "in"): "-6", ("2", "start"): "50"}
    got = {k: ex.premiere_item_problems(_broken(xml, tmp_path, k[0], k[1], val)) for k, val in cases.items()}
    assert got[("2", "in")] == ["V1 S02 raw.mp4: in '1800.5' not a whole frame"]
    assert got[("2", "end")] == ["V1 S02 raw.mp4 at 00:00:01:00: start 60 is not before end 60"]
    assert got[("3", "out")] == ["V1 S03 raw.mp4 at 00:00:02:00: in 3000 is not before out 2950"]   # the S10 bug
    assert got[("a2", "in")] == ["A1 S02 raw.mp4 at 00:00:01:00: out - in = 50 source frames, but it lasts 60 "
                                 "sequence frames at 100 % (want 60)"]
    assert got[("3", "in")] == [
        "V1 S03 raw.mp4 at 00:00:02:00: out - in = 3066 source frames, but it lasts 60 sequence frames at 100 % "
        "(want 60)", "V1 S03 raw.mp4 at 00:00:02:00: in / out -6-3060 outside its media (0-6000)"]
    assert got[("2", "start")] == [
        "V1 S02 raw.mp4 at 00:00:00:50: out - in = 60 source frames, but it lasts 70 sequence frames at 100 % (want 70)",
        "V1 S02 raw.mp4 at 00:00:00:50: overlaps V1 S01 raw.mp4, which ends at 00:00:01:00"]
    # doubled audio: an A1 item moved over its neighbour
    bad = _broken(_broken(xml, tmp_path, "a2", "start", "20"), tmp_path, "a2", "end", "50")
    assert ex.premiere_item_problems(bad)[-1] == (
        "A1 S02 raw.mp4 at 00:00:00:20: overlaps A1 S01 raw.mp4, which ends at 00:00:01:00 (doubled audio: "
        "two audio clips at the same moment)")
    # the validation fails on any of them: the run fails, like the gap check
    v2 = ex.validate_premiere_exports(cl, _broken(xml, tmp_path, "2", "in", "1800.5"), None,
                                      Config(out_dir=str(tmp_path), premiere=True), plan.get("ripple"))
    assert not v2["ok"] and v2["item_problems"] and any(e.startswith("XML ITEM V1 S02") for e in v2["errors"])


def test_every_v1_clip_has_its_audio_on_a1_unless_removed_on_purpose(tmp_path):
    cl = T.premiere_cutlist()                       # S09 is a freeze: a frozen picture plays no audio
    cl.segments[7].audio = {"mute": True}           # S08: a cutaway over music, picture only
    plan, xml, v, x = export(tmp_path, cl)
    assert v["ok"], v["errors"]
    assert v["audio_exceptions"] == [
        "S08 00:00:07:20-00:00:08:20: muted on purpose: the competitor showed a cutaway over music / voice-over here "
        "(picture only)",
        "S09 00:00:08:20-00:00:09:00: a freeze: a frozen picture plays no audio"]
    # an A1 item gone for no reason: the run fails
    text = xml.read_text(encoding="utf-8")
    body = re.search(r'<clipitem id="clipitem-a1">.*?</clipitem>', text, re.S).group(0)
    gone = tmp_path / "no_a1.xml"
    gone.write_text(text.replace(body, ""), encoding="utf-8")
    v2 = ex.validate_premiere_exports(cl, gone, None, Config(out_dir=str(tmp_path), premiere=True), plan.get("ripple"))
    assert "XML A1: V1 clip S01 has no audio on A1 at 00:00:00:00-00:00:01:20" in v2["errors"]


def test_the_end_summary_lists_the_v1_clips_without_audio():
    from match_cuts import cli
    text = cli.format_summary({"exit_code": 0, "checklist": {"broll": [], "spots": [], "captions": [],
                                                             "audio": ["S09 00:00:08:20-00:00:09:00: a freeze"],
                                                             "repeats": ["none found"]}}, "out")
    assert "  V1 clips without their audio on A1 (on purpose): 1\n    S09 00:00:08:20-00:00:09:00: a freeze" in text
    assert "Repeats: none found" in text


# ---------------------------------------------------------------------------------------------
# Repeats
# ---------------------------------------------------------------------------------------------

def test_a_stutter_at_a_cut_is_trimmed_so_nothing_plays_twice(tmp_path):
    # S02 starts 0.1 s back in the RAW: RAW 10.9-11.0 s shown at the end of S01 and again at the start of S02
    cl = cutlist([seg(1, 0, 30, 10.0), seg(2, 30, 60, 10.9), seg(3, 60, 90, 40.0)])
    plan, xml, v, x = export(tmp_path, cl)
    rows = plan["repeats"]["rows"]
    assert [(r["kind"], r["track"], r["removed"], r["kept"], r["a"], r["b"], r["copy"], r["why"]) for r in rows] == [
        ("stutter", "V1", "S02", "S01", 60, 66, [54, 60], "the start of the next clip")]
    assert v["ok"] and v["repeat_problems"] == [], v["errors"]
    assert x["duration"] == 174                                    # 180 - the 6 repeated frames
    # the two sides now play one continuous take: one clip, its audio one item with no fade
    assert [(c["label"], c["start"], c["end"], c["in"], c["out"]) for c in x["clips"]] == [
        ("S01+S02", 0, 114, 600, 714), ("S03", 114, 174, 2400, 2460)]   # RAW 10 s = frame 600
    assert [(a["start"], a["end"], a["levels"]) for a in x["audio"]] == [(0, 114, []), (114, 174, [])]
    from match_cuts.pipeline import repeat_lines
    assert repeat_lines(plan) == [
        "1 removed, 0.10 s in all",
        "00:00:01:00-00:00:01:06 V1 S02 repeated 00:00:00:54-00:00:01:00 S01 (RAW 10.90-11.00 s, 0.10 s): a stutter at "
        "a cut, removed the start of the next clip; cut at 00:00:01:00 in the new edit"]
    # without the trim the hard check fails the run -- even with --allow-repeats
    ex.write_premiere_xml(cl, tmp_path / "uncut.xml", Config(out_dir=str(tmp_path), premiere=True))
    assert ex.premiere_repeat_problems(tmp_path / "uncut.xml", allow_repeats=True) == [
        "V1 S01 at 00:00:00:54 and S02 at 00:00:01:00 both play RAW 10.90-11.00 s: a stutter at a cut"]


def test_a_clip_that_only_repeats_the_end_of_the_one_before_goes(tmp_path):
    # S02 shows RAW 20.0-20.2 s; S03 starts at 19.9 s and plays through it: S02 is the repeat (the end before the cut)
    cl = cutlist([seg(1, 0, 30, 5.0), seg(2, 30, 36, 20.0), seg(3, 36, 66, 19.9)])
    plan, xml, v, x = export(tmp_path, cl)
    assert [(r["removed"], r["a"], r["b"], r["why"]) for r in plan["repeats"]["rows"]] == [
        ("S02", 60, 72, "the end of the clip before")]
    assert v["ok"], v["errors"]
    assert [c["label"] for c in x["clips"]] == ["S01", "S03"]


def test_the_same_moment_twice_loses_the_copy_out_of_order(tmp_path):
    # a hook: RAW 30-32 s first, then the story from 10 s on, which reaches 30-32 s again in S03
    cl = cutlist([seg(1, 0, 60, 30.0), seg(2, 60, 360, 10.0), seg(3, 360, 900, 20.0)])
    plan, xml, v, x = export(tmp_path, cl)
    rows = plan["repeats"]["rows"]
    assert [(r["kind"], r["removed"], r["kept"], r["a"], r["b"], r["copy"], r["why"]) for r in rows] == [
        ("repeat", "S01", "S02+S03", 0, 120, [1320, 1440], "out of chronological order")]   # S02, S03: one take
    assert v["ok"] and x["duration"] == 1680 and x["clips"][0]["label"].startswith("S02")
    # both in chronological order: the later copy goes
    cl = cutlist([seg(1, 0, 60, 10.0), seg(2, 60, 120, 30.0), seg(3, 120, 180, 31.0)])
    plan, xml, v, x = export(tmp_path, cl, "later.xml")
    assert [(r["removed"], r["kept"], r["a"], r["b"], r["why"]) for r in plan["repeats"]["rows"]] == [
        ("S03", "S02", 240, 300, "the later copy")]
    assert v["ok"], v["errors"]


def test_allow_repeats_keeps_the_same_moment_twice(tmp_path):
    cl = cutlist([seg(1, 0, 60, 30.0), seg(2, 60, 360, 10.0), seg(3, 360, 900, 20.0)])
    plan, xml, v, x = export(tmp_path, cl, allow_repeats=True)
    assert plan["repeats"]["rows"] == [] and plan["repeats"]["allow"] and v["ok"], v["errors"]
    from match_cuts.pipeline import repeat_lines
    assert repeat_lines(plan) == ["none found; repeats over 0.5 s kept (--allow-repeats)"]
    # without the flag the same XML fails the hard check
    assert ex.premiere_repeat_problems(xml) == [
        "V1 S01 at 00:00:00:00 and S02+S03 at 00:00:22:00 both play RAW 30.00-32.00 s: the same moment over 0.5 s twice "
        "(--allow-repeats keeps it)"]
    v2 = ex.validate_premiere_exports(cl, xml, None, Config(out_dir=str(tmp_path), premiere=True), plan.get("ripple"))
    assert not v2["ok"] and v2["repeat_problems"]


def test_a_repeated_syllable_on_a1_is_trimmed_too(tmp_path):
    # the uncertain spot S02 plays an audio line (RAW 20.0 s on) that runs 0.2 s into what S03's audio plays
    unc = Segment(id=2, type="uncertain", comp_in=30, comp_out=60, label="UNCERTAIN",
                  audio={"line": {"id": "L1", "raw_in_seconds": 20.0, "speed": 1.0, "source": "S03 before"}})
    cl = cutlist([seg(1, 0, 30, 5.0), unc, seg(3, 60, 90, 20.8)])
    plan, xml, v, x = export(tmp_path, cl)
    assert [(r["track"], r["removed"], r["kept"], r["a"], r["b"]) for r in plan["repeats"]["rows"]] == [
        ("A1", "S03", "S02", 120, 132)]
    assert v["ok"] and v["repeat_problems"] == [], v["errors"]


def test_captions_move_with_a_removed_repeat(tmp_path, monkeypatch):
    import types

    from match_cuts import caption_ocr, captions as C
    from match_cuts.common import Cache
    cl = cutlist([seg(1, 0, 30, 10.0), seg(2, 30, 60, 10.9)])
    cfg = Config(out_dir=str(tmp_path / "out"), work_dir=str(tmp_path / "work"), premiere=True)
    plan = repeats.add_to_plan({"off": "--keep-silence"}, cl, cfg)
    assert plan["off"] and plan["cuts"] == [(60, 66)]
    spans = [{"comp_in": 0, "comp_out": 30, "ocr": "SO AS A JOKE", "score": 0.97, "agreement": 1.0},
             {"comp_in": 30, "comp_out": 60, "ocr": "I SUGGESTED IT", "score": 0.97, "agreement": 1.0}]
    monkeypatch.setattr(caption_ocr, "available", lambda: None)
    monkeypatch.setattr(caption_ocr, "caption_band", lambda layout, wh: object())
    monkeypatch.setattr(C, "_read_spans", lambda ctx, layout, fps: {"spans": spans, "frames_read": 60})
    info = types.SimpleNamespace(path="", file_hash="x", width=1080, height=1920, display_width=1080,
                                 display_height=1920)
    ctx = types.SimpleNamespace(cfg=cfg, comp_info=info, comp_fps=Fraction(30), n_comp=60, paths={}, broll=None,
                                cutlist=types.SimpleNamespace(layout={}, segments=[]), cache=Cache(cfg.work),
                                raw_audio=None, audio_sr=16000, warn=lambda m: None, silence=plan)
    res = C.run_captions(ctx)
    got = [(b["text"], round(b["start_ms"] * 60 / 1000), round(b["end_ms"] * 60 / 1000)) for b in C.read_srt(res["path"])]
    assert got == [("So as a joke", 0, 60), ("I suggested it", 60, 114)]   # the second one 6 frames shorter


def test_a_freeze_placeholder_is_not_a_repeat(tmp_path):
    # the fixture's S09 freeze is placed at 100 % (to redo by hand): it may run into RAW the next clip shows
    cl = T.premiere_cutlist()
    cl.segments[9] = T._grid_seg(10, 270, 300, 2700)                     # S10 starts on the freeze's RAW (90 s)
    plan, xml, v, x = export(tmp_path, cl)
    assert plan["repeats"]["rows"] == [] and v["ok"] and v["repeat_problems"] == [], v["errors"]


def test_a_removal_never_leaves_a_sliver_of_a_clip():
    """Task 5 (seen on the thorough zendaya edit): a stutter removed at a cut left one sequence frame of the next clip,
    whose source range rounded to nothing (XML 'in 9534 is not before out 9534'). A piece shorter than MIN_PIECE
    next to a removal goes with it; a clip that short of its own stays."""
    from types import SimpleNamespace as C
    clips = [C(rec_start=0, rec_end=100), C(rec_start=100, rec_end=140), C(rec_start=140, rec_end=142),
             C(rec_start=142, rec_end=200)]
    assert repeats.absorb_slivers([(110, 139)], clips) == [(110, 140)]
    assert repeats.absorb_slivers([(101, 139)], clips) == [(100, 140)]     # 1 frame of it left on both sides
    assert repeats.absorb_slivers([(150, 160)], clips) == [(150, 160)]     # the 2-frame clip 140-142 is its own
    assert repeats.absorb_slivers([(141, 150)], clips) == [(140, 150)]     # ... but 1 frame of it left goes
