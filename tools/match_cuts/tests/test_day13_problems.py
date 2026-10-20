"""Day 13, section 7: the open problems of problems_queue.md.

Family Guy 1999-present ("OK with a note": XML ENDING ... the edit ends on RAW 621.433 s, the competitor on RAW
619.650 s (S42): 1.78 s long): the competitor's last 0.3 s (S42) replay RAW 619.35-619.65 s, which it had played a
moment before -- the repeats step cut that stutter out (your rule), so the edit ends where the take before it ends
(621.43 s). The ending check still compared with the removed piece. Now a tail cut out with the repeats is not the
competitor's ending any more: the frame before it is."""
from __future__ import annotations

from match_cuts import export_xml_edl as ex
from match_cuts.config import Config
from match_cuts.keep_speed import keep_speed

from test_night4 import cutlist


def _plan_and_xml(tmp_path):
    kc, _ = keep_speed(cutlist())
    cfg = Config(out_dir=str(tmp_path), premiere=True)
    xml = tmp_path / "1_edit.xml"
    ex.write_premiere_xml(kc, xml, cfg)
    plan, _, _ = ex.premiere_clips(kc, cfg)
    return plan, ex.parse_premiere_xml(xml)


def _tail_cut(x: dict, k: int) -> dict:
    """The XML as it is when the last ``k`` frames of the plan were cut out: its last clip ends k frames sooner, in
    the sequence and in the RAW."""
    last = max((c for c in x["clips"] if c["end"] >= 0), key=lambda c: c["end"])
    clips = [dict(c, out=c["out"] - k, end=c["end"] - k) if c is last else c for c in x["clips"]]
    return dict(x, clips=clips, duration=x["duration"] - k)


def test_a_tail_cut_out_as_a_repeat_is_not_the_competitors_ending(tmp_path):
    plan, x = _plan_and_xml(tmp_path)
    total = max(c.rec_end for c in plan)
    cut = _tail_cut(x, 18)                                       # 0.3 s at 60 fps, like Family Guy's S42
    probs = ex.ending_problems(plan, cut)
    assert probs and "short" in probs[0]                         # without the removal: "ends 0.30 s short"
    assert ex.ending_problems(plan, cut, removed=[(total - 18, total)]) == []
    # a removed range in the middle changes nothing at the end
    assert ex.ending_problems(plan, cut, removed=[(10, 28)]) == probs
    # the whole edit as it was: no removal needed, none reported
    assert ex.ending_problems(plan, x, removed=[]) == []


def test_the_outro_cut_off_and_a_removed_tail_together(tmp_path):
    plan, x = _plan_and_xml(tmp_path)
    total = max(c.rec_end for c in plan)
    # the competitor's ending cut off 30 frames early (an outro not from the RAW), and a stutter just before it
    cut = _tail_cut(x, 48)
    assert ex.ending_problems(plan, cut, end=total - 30, removed=[(total - 48, total - 30)]) == []
    assert ex.ending_problems(plan, cut, end=total - 30)          # the stutter not known: 0.3 s short
