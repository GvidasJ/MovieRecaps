"""Cut moves out of speech of 0.23 s or more are flagged to check by hand (you undid all 7 such moves on
video017/018 and kept the smaller ones)."""
from match_cuts.pipeline import big_move, speech_lines


def test_a_move_out_of_0_23_s_or_more_is_flagged_and_smaller_or_inward_moves_are_not():
    rows = [{"clip": "S03", "edge": "end", "frames": 14, "from_s": 10.0, "to_s": 10.23},      # plays on 0.233 s
            {"clip": "S04", "edge": "start", "frames": -13, "from_s": 20.0, "to_s": 19.78},   # 0.217 s: kept
            {"clip": "S05", "edge": "start", "frames": -20, "from_s": 30.0, "to_s": 29.67},   # starts 0.33 s earlier
            {"clip": "S06", "edge": "end", "frames": -30, "from_s": 40.0, "to_s": 39.5}]      # ends earlier: inward
    assert [big_move(r) for r in rows] == [True, False, True, False]
    lines = speech_lines({"speech": {"on": True, "rows": rows, "levels": {"models": ["large-v3"]}}})
    assert "2 moved out 0.23 s or more -- CHECK BY HAND" in lines[0]
    assert lines[1].endswith("CHECK BY HAND: 0.23 s out") and "CHECK" not in lines[2]
