"""Day 13 (the lead, from the final check-all): the speaker rule (section 8) re-frames one RAW shot of a clip that the
XML splits at the RAW's shot change; caption rule 10 then saw a new video cut there and split "in my face" (video14,
11.2 s: captions 16 -> 15 of 42). Your video14 fix re-frames at that very shot change and keeps "in my face" whole. The
caption after-step (post_captions, in no cached stage) now joins a caption split only there; every other cut stays one."""
from __future__ import annotations

from fractions import Fraction

from match_cuts import post_captions as PC

FPS = Fraction(60)


def clip(cid, label, start, end, a, b, motion=(0.0, 0.0, 100.0), name="raw.mp4"):
    return {"id": cid, "label": label, "name": name, "start": start, "end": end, "in": a, "out": b, "speed": 1.0,
            "motion": motion, "flip": False}


def xml_with_notes(tmp_path, notes: dict) -> object:
    p = tmp_path / "1_edit.xml"
    items = "".join(f"<clipitem id='{cid}'><comments><mastercomment2>{n}</mastercomment2></comments></clipitem>"
                    for cid, n in notes.items())
    p.write_text(f"<xmeml><sequence><media><video><track>{items}</track></video></media></sequence></xmeml>",
                 encoding="utf-8")
    return p


RULE = "framing; centred on the person speaking (RAW x 1210; Light-ASD 2.1, 90 % of the speech)"
DAY12 = "moved onto the person speaking (RAW x 300): the template window, narrower than the competitor's"


def test_rule_cuts_are_only_the_speaker_rules_re_frames_inside_one_clip(tmp_path, monkeypatch):
    from match_cuts import export_xml_edl as ex
    x = {"timebase": 60, "ntsc": "FALSE", "clips": [
        clip("clipitem-1", "S06", 0, 377, 1800, 2177),
        clip("clipitem-2", "S07+S08", 377, 672, 2200, 2495, motion=(10.0, 0.0, 100.0)),   # re-framed by the rule
        clip("clipitem-3", "S07+S08", 672, 808, 2495, 2631),                               # its next RAW shot
        clip("clipitem-4", "S09", 808, 900, 2631, 2723, motion=(20.0, 0.0, 100.0))]}       # the competitor's reframe
    monkeypatch.setattr(ex, "parse_premiere_xml", lambda path: x)
    xml = xml_with_notes(tmp_path, {"clipitem-2": RULE, "clipitem-3": RULE, "clipitem-4": RULE})
    assert PC.rule_cuts(xml, FPS) == {672}                     # the split re-framed by the rule; 808 stays a cut
    assert PC.rule_cuts(xml_with_notes(tmp_path, {"clipitem-2": DAY12}), FPS) == set()   # day 12's rule: not this
    assert PC.rule_cuts(tmp_path / "missing.xml", FPS) == set()


def test_a_caption_split_on_such_a_cut_is_whole_again():
    caps = [{"text": "we're done", "start": 500, "end": 640, "mode": "voice"},
            {"text": "in my", "start": 650, "end": 672, "mode": "voice"},
            {"text": "face", "start": 672, "end": 687, "mode": "voice"},
            {"text": "go", "start": 700, "end": 740, "mode": "voice"}]
    out, done = PC.rejoin(caps, {672}, FPS)
    assert [(c["text"], c["start"], c["end"]) for c in out] == [("we're done", 500, 640), ("in my face", 650, 687),
                                                                 ("go", 700, 740)]
    assert done == [{"was": "in my | face", "now": "in my face", "start": 650, "end": 687}]
    assert caps[1]["end"] == 672                                # the input is not changed
    # not on such a cut, or too long joined (21 characters / over 1.5 s): left as they are
    assert PC.rejoin(caps, {700}, FPS)[1] == []
    long = [{"text": "the quick brown", "start": 0, "end": 60, "mode": "voice"},
            {"text": "foxes", "start": 60, "end": 90, "mode": "voice"}]
    assert PC.rejoin(long, {60}, FPS)[1] == []
    slow = [{"text": "in my", "start": 0, "end": 60, "mode": "voice"},
            {"text": "face", "start": 60, "end": 100, "mode": "voice"}]
    assert PC.rejoin(slow, {60}, FPS)[1] == []                  # 1.67 s on screen
