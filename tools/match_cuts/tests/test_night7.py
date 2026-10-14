"""Night 7: captions always written (doubtful lines listed, spelled-out letters in capitals, no caption under 2
frames, no single-letter allowlist entry), the caption censor list, a missing 2_captions.srt said clearly (never "all
deliverables present"), the batch's OK with a note, and 1_edit.xml at the RAW's own frame rate."""
from __future__ import annotations

import json
import math
from fractions import Fraction
from pathlib import Path
from types import SimpleNamespace

import pytest

from match_cuts import batch, caption_rules, captions, verify
from match_cuts import export_xml_edl as ex
from match_cuts.captions import Caption
from match_cuts.config import Config

FPS = Fraction(60)


# ---------------------------------------------------------------------------------------------------------------------
# 1. captions: spelled-out letters, no single-letter allowlist entry, always written, doubtful lines, short ones out
# ---------------------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("word", ["A-N", "O-N", "R-E", "T-H-E", "U-S-A"])
def test_a_word_spelled_out_is_a_real_word_in_capitals(word):
    lex = caption_rules.lexicon()
    assert caption_rules.is_spelled(word) and caption_rules.is_real(word, lex)
    assert caption_rules.case_ok(word, lex) and not caption_rules.case_ok(word.lower(), lex)
    assert not caption_rules.is_spelled("X-Men") and not caption_rules.is_spelled("P-P-Peter")


def test_laptop_video2s_spelled_letters_break_no_rule_and_are_written_in_capitals():
    caps = [Caption("it's spelled", 0, 30, "competitor"), Caption("a-n", 30, 60, "competitor"),
            Caption("O-N", 60, 90, "competitor"), Caption("R-E", 90, 120, "competitor"),
            Caption("N", 120, 150, "competitor")]
    out, rep = caption_rules.enforce(caps, FPS, "competitor")
    said = " ".join(c.text for c in out).split()
    assert {"A-N", "O-N", "R-E"} <= set(said)                  # (the lone "N", nothing heard: screen noise, as before)
    left = caption_rules.check(out, FPS, "competitor", rules=(1, 2, 3, 4, 5))
    assert not left[4] and not left[5], left
    lone = [Caption("the letter", 0, 30), Caption("N", 30, 60)]
    assert not caption_rules.check(lone, FPS, "voice", rules=(4, 5))[5]     # a lone capital letter: not flagged


def test_a_single_letter_is_never_an_allowlist_entry_but_I_is(tmp_path):
    p = tmp_path / "caption_allowlist.txt"
    p.write_text("# notes\nA\na, I\nMCU\nAI\nx\n", encoding="utf-8")
    assert caption_rules.read_allowlist(p) == ["I", "MCU", "AI"]
    assert "A" not in caption_rules.read_allowlist()           # the shipped list: no lone "A" either


def test_the_srt_is_always_written_and_its_doubtful_lines_listed(tmp_path):
    caps = [Caption("How are you? Fine", 0, 30), Caption("the WAS", 30, 60), Caption("the MCU", 60, 90)]
    p = captions.write_srt(caps, tmp_path / "2_captions.srt", FPS)          # night 6 refused this file
    assert p.is_file() and "How are you? Fine" in p.read_text(encoding="utf-8")
    d = captions.doubtful_lines(caps, FPS)
    assert {x["rule"] for x in d} >= {1, 4}
    assert all(x["line"].startswith("00:00:0") for x in d)
    assert not any("MCU" in x["line"] for x in d)                          # the allowlist's acronym is fine


def test_a_caption_shorter_than_two_frames_is_left_out_and_the_one_before_runs_on():
    caps = [Caption("which is", 0, 30), Caption("wh****", 30, 31), Caption("fine", 31, 60), Caption("x", 70, 71)]
    keep, gone = captions.drop_short(caps, 2)
    assert [c.text for c in keep] == ["which is", "fine"] and [c.text for c in gone] == ["wh****", "x"]
    assert keep[0].end == 31 and keep[1].start == 31                         # still back to back


# ---------------------------------------------------------------------------------------------------------------------
# 2. the censor list (your answer 2a)
# ---------------------------------------------------------------------------------------------------------------------

def test_the_shipped_censor_list_writes_your_forms_and_never_gay_or_fag():
    t = captions.censor_table()
    assert t["porn"] == "p*rn" and t["sexual"] == "se*ual" and t["pervert"] == "perv*rt" and t["dick"] == "d*ck"
    assert "gay" not in t and "fag" not in t and "fags" not in t and "pissed" not in t
    assert all(f.count("*") == 1 and len(f) == len(w) for w, f in t.items())      # one letter, the same length
    s = "Porn is SEXUAL, you pervert. Dick's car, gay people, fags, dickens"
    assert captions.censor_text(s, t) == "P*rn is SE*UAL, you perv*rt. D*ck's car, gay people, fags, dickens"


def test_a_word_without_a_form_gets_its_first_vowel_starred(tmp_path):
    p = tmp_path / "caption_censor.txt"
    p.write_text("# mine\nboobs\nslut = sl*t\nhorny = h*rny\nbad = b*dd\n", encoding="utf-8")
    t = captions.censor_table(p)
    assert t == {"boobs": "b*obs", "slut": "sl*t", "horny": "h*rny", "bad": "b*d"}   # a wrong form: the default
    caps, rows = captions.censor_captions([Caption("nice boobs", 0, 30), Caption("*thinks*", 30, 60)], t, FPS)
    assert caps[0].text == "nice b*obs" and caps[1].text == "*thinks*" and rows[0]["was"] == "nice boobs"


# ---------------------------------------------------------------------------------------------------------------------
# 3. a missing 2_captions.srt: said clearly, never "all deliverables present"
# ---------------------------------------------------------------------------------------------------------------------

def _deliver_ctx(tmp_path, with_srt: bool):
    out, deliver = tmp_path / "extras", tmp_path
    (out / "debug").mkdir(parents=True)
    for name in ("cutlist.json", "cutlist.csv", "recreated_edit.edl"):
        (out / name).write_text("x", encoding="utf-8")
    (deliver / "1_edit.xml").write_text("x", encoding="utf-8")
    if with_srt:
        (deliver / "2_captions.srt").write_text("1\n00:00:00,000 --> 00:00:01,000\nhi\n", encoding="utf-8")
    cfg = SimpleNamespace(out=out, deliver=deliver, premiere=True, skip_preview=True, skip_compare=True,
                          debug_dir=out / "debug")
    cap = {} if with_srt else {"warnings": ["2_captions.srt not written: no competitor captions and no transcribed "
                                            "speech"]}
    return SimpleNamespace(cfg=cfg, paths={}, cutlist=None, env={}, exports={"ok": True}, errors=[], captions=cap)


def test_a_missing_captions_file_fails_the_deliverables_and_says_why(tmp_path):
    d = verify.check_deliverables(_deliver_ctx(tmp_path, with_srt=False))
    assert d["status"] == "fail"
    assert "2_captions.srt MISSING" in d["summary"]
    assert any(f.startswith("2_captions.srt MISSING") and "no transcribed speech" in f for f in d["failures"])
    ok = verify.check_deliverables(_deliver_ctx(tmp_path / "b", with_srt=True))
    assert ok["status"] == "pass" and "MISSING" not in ok["summary"]
    assert any(i["key"] == "captions" and i["exists"] for i in ok["items"])


# ---------------------------------------------------------------------------------------------------------------------
# 4. the batch: OK with a note when every file is there and only 1_edit.xml's check found something
# ---------------------------------------------------------------------------------------------------------------------

def _row(deliv_failures, summary="8/8 deliverables present, exports NOT validated, 0 stage errors"):
    return {"deliverables": {"status": "fail" if deliv_failures else "pass", "summary": summary,
                             "failures": deliv_failures},
            "coverage": {"status": "pass", "summary": ""}}


def test_only_exports_not_validated_is_ok_with_a_note(monkeypatch, tmp_path):
    from match_cuts import check_all
    flash = ("XML/EDL re-parse validation did not pass: ['XML FLASH S01+S02 at 00:00:21:26: 2 frame(s) of a "
             "different RAW shot (0.03 s, under 0.25 s) -- a flash frame']")
    monkeypatch.setattr(check_all, "read_run", lambda case, run_dir, scored=True: _row([flash]))
    c = batch.checks_of(tmp_path)
    assert c["pass"] and c["notes"] and "XML FLASH" in c["notes"][0] and not c["failures"]
    assert batch.verdict({"pass": True, "notes": c["notes"]}) == "OK (with a note)"
    missing = _row(["2_captions.srt MISSING -- no captions for this edit (x)", flash],
                   "8/9 deliverables present (2_captions.srt MISSING), exports NOT validated, 0 stage errors")
    monkeypatch.setattr(check_all, "read_run", lambda case, run_dir, scored=True: missing)
    c2 = batch.checks_of(tmp_path)
    assert not c2["pass"] and batch.verdict({"pass": False}) == "FAIL"


def test_the_batch_summary_says_no_captions_and_lists_doubtful_lines(tmp_path):
    run = tmp_path / "video2"
    (run / "extras" / "debug").mkdir(parents=True)
    (run / "extras" / "debug" / "captions.json").write_text(json.dumps(
        {"warnings": ["2_captions.srt not written: no competitor captions and no transcribed speech"]}),
        encoding="utf-8")
    cap = batch.captions_of(run)
    assert not cap["written"] and "no transcribed speech" in cap["why"]
    rows = [{"name": "video2", "run_dir": str(run), "exit": 1, "speed": 100, "seconds": 60, "pass": False,
             "failures": ["deliverables: fail -- 8/9 deliverables present (2_captions.srt MISSING)"], "notes": [],
             "captions": False, "captions_why": cap["why"], "doubtful": [], "media_outside": [], "hand": [],
             "log": "x"},
            {"name": "video1", "run_dir": str(tmp_path / "video1"), "exit": 1, "speed": 100, "seconds": 60,
             "pass": True, "failures": [], "notes": ["every file is there, but a check of 1_edit.xml found ..."],
             "captions": True, "captions_why": "", "doubtful": ["00:00:05,000 'A-N': rule 4 (capitals)"],
             "media_outside": [], "hand": [], "log": "y"}]
    md = batch.summary_md(rows, tmp_path, [], "now", 120.0)
    assert "NO CAPTIONS" in md and "OK (with a note)" in md and "'A-N': rule 4" in md
    assert "1 passed (1 with a note), 1 failed" in md


# ---------------------------------------------------------------------------------------------------------------------
# 5. 1_edit.xml at the RAW's own frame rate
# ---------------------------------------------------------------------------------------------------------------------

def test_the_sequence_rate_is_the_raws_or_the_nearest_normal_one():
    s = ex.sequence_rate_for_raw
    for r in ("24000/1001", "24", "25", "30000/1001", "30", "50", "60000/1001", "60"):
        assert s(r) == Fraction(r)
    assert s(15) == Fraction(24000, 1001) and s(48) == 50 and s(23.98) == Fraction(24000, 1001)
    assert s(29.97) == Fraction(30000, 1001) and s(120) == 60 and s(Fraction(2997, 125)) == Fraction(24000, 1001)


def _pair(tmp_path, raw_fps="24000/1001", shots=None, k=1.0):
    from test_export_xml_edl import premiere_cutlist
    cl = premiere_cutlist()
    cl.raw["fps"] = raw_fps
    cfg = Config(out_dir=str(tmp_path), premiere=True)
    cfg.premiere_shots = shots
    base, final = tmp_path / "edit_60fps.xml", tmp_path / "1_edit.xml"
    ex.write_premiere_xml(cl, base, cfg)
    res = ex.write_premiere_xml(cl, final, cfg, speed=k, seq_rate=ex.sequence_rate_for_raw(raw_fps))
    return cl, cfg, base, final, res


@pytest.mark.parametrize("raw_fps", ["24000/1001", "30000/1001", "30", "25"])
def test_the_edit_on_the_raws_rate_plays_the_same_raw_frames_at_the_same_moments(tmp_path, raw_fps):
    cl, cfg, base, final, res = _pair(tmp_path, raw_fps)
    R = Fraction(raw_fps)
    a, b = ex.parse_premiere_xml(base), ex.parse_premiere_xml(final)
    assert ex._seq_rate(b) == R and ex._seq_rate(a) == 60 and res["fps"] == ex.fps_str(R)
    assert all((c["timebase"], str(c["ntsc"]).upper()) == ex.xml_rate(R) for c in b["clips"])
    assert abs(b["duration"] / float(R) - a["duration"] / 60.0) <= 1.0 / float(R)     # the same length
    assert ex.final_xml_problems(base, final, 1.0, None, cl.raw_fps) == []
    # every new take starts on the RAW frame the 60 fps edit starts it on (one sequence frame = one RAW frame)
    prev = None
    for q in b["clips"]:
        if q["speed"] == 1.0 and not (prev is not None and prev["end"] == q["start"] and prev["out"] == q["in"]):
            t = q["start"] / float(R)
            p = min(a["clips"], key=lambda c: abs(c["start"] / 60.0 - t))
            assert q["in"] == math.floor(Fraction(p["in"]) * R / 60 + Fraction(1, 10)), (q, p)
        prev = q


def test_a_cut_at_a_raw_shot_change_lands_exactly_on_it_at_the_raws_rate(tmp_path):
    change = 175 * 1001 / 24000                          # RAW frame 175: inside S01 (158-190): a scene split
    cl, cfg, base, final, res = _pair(tmp_path, shots=[change])
    b = ex.parse_premiere_xml(final)
    p, q = b["clips"][0], b["clips"][1]
    assert p["end"] == q["start"] and p["out"] == q["in"] == 175        # the split exactly on RAW frame 175
    assert ex.final_xml_problems(base, final, 1.0, [change], cl.raw_fps) == []


def test_speed_125_and_the_raws_rate_together(tmp_path):
    cl, cfg, base, final, res = _pair(tmp_path, k=1.25)
    a, b = ex.parse_premiere_xml(base), ex.parse_premiere_xml(final)
    assert ex._seq_rate(b) == Fraction(24000, 1001)
    assert sorted({round(c["speed"], 4) for c in b["clips"]}) == sorted({round(c["speed"] * 1.25, 4) for c in a["clips"]})
    assert abs(b["duration"] / float(ex._seq_rate(b)) - a["duration"] / 60.0 / 1.25) <= 1.0 / 23.976
    assert ex.final_xml_problems(base, final, 1.25, None, cl.raw_fps) == []


def test_the_captions_follow_the_cuts_onto_the_new_frames():
    from match_cuts.pipeline import final_srt
    rows = [{"start_ms": 0, "end_ms": 1000, "text": "Failure"}, {"start_ms": 1000, "end_ms": 1033, "text": "x"},
            {"start_ms": 1033, "end_ms": 2000, "text": "is not"}]
    R = Fraction(24000, 1001)
    text = final_srt(rows, 1.0, Fraction(60), R, {120: 47})             # the cut at 2 s went onto frame 47
    blocks = [b.split("\n") for b in text.strip().split("\n\n")]
    assert [b[2] for b in blocks] == ["Failure", "is not"]               # "x" (1 frame at 23.976) went into the one before
    t = [b[1].split(" --> ") for b in blocks]
    assert t[0][0] == "00:00:00,000" and t[0][1] == t[1][0]              # back to back
    assert t[0][1] == captions.ms_tc(captions.frame_ms(25, R))           # 62 ticks of 60 fps: frame 25 at 23.976
    assert t[1][1] == captions.ms_tc(captions.frame_ms(47, R))           # ... and the end where its cut went


# ---------------------------------------------------------------------------------------------------------------------
# 6. learned from your video6 fix: a pause at a RAW shot change is cut even with a faint sound in it
# ---------------------------------------------------------------------------------------------------------------------

def _faint_pause(a: float, b: float, share: float, shot: bool, n: int = 600):
    import numpy as np
    from match_cuts import silence
    sr, fps = 48000, Fraction(60)
    y = np.zeros(sr * n // 60, np.float32)
    amp = 10 ** ((-60.0 + share * 40.0) / 20.0) * math.sqrt(2.0)          # its loudness that share of the way up
    i0, i1 = int(a * sr), int(b * sr)
    y[i0:i1] = amp * np.sin(2 * np.pi * 200.0 * np.arange(i1 - i0) / sr)
    cuts, lv = silence.pause_ranges(y, sr, fps, n, silence.Settings(), (), quiet=[(a, b)],
                                    lv={"speech_db": -20.0, "noise_db": -60.0, "threshold_db": -45.0, "how": "t"},
                                    shot_in=lambda s0, s1: shot)
    return [(c.a, c.b) for c in cuts], lv


def test_a_pause_holding_a_raw_shot_change_is_cut_with_a_faint_sound_in_it():
    cut, lv = _faint_pause(2.0, 2.46, 0.20, shot=True)          # video6: 0.46 s at RAW 334.38 s, share 0.20
    assert cut and lv["pauses"]["at_shot_change"] == 1
    assert _faint_pause(2.0, 2.46, 0.20, shot=False)[0] == []   # no shot change in it: not a clear silence, kept
    assert _faint_pause(2.0, 3.4, 0.23, shot=True)[0] == []     # laptop013's 1.4 s music beat: kept
    assert _faint_pause(2.0, 2.46, 0.30, shot=True)[0] == []    # a louder sound in it: kept
    assert _faint_pause(2.0, 2.46, 0.05, shot=False)[0]         # a clear one: cut, as before


def test_shot_inside_reads_the_raw_time_each_clip_plays():
    from match_cuts import silence
    clips = [SimpleNamespace(rec_start=0, rec_end=120, src_in=6000, speed=1.0),     # RAW 100-102 s
             SimpleNamespace(rec_start=120, rec_end=240, src_in=12000, speed=1.0)]  # RAW 200-202 s
    f = silence.shot_inside(clips, [101.0, 201.5], Fraction(60))
    assert f(0.9, 1.1) and not f(1.2, 1.9) and f(3.4, 3.6) and not f(2.0, 3.3)


def test_a_caption_of_several_words_on_screen_over_one_and_a_half_seconds_is_split():
    from match_cuts.captions import Word
    w = [Word("You", 8.44, 8.546, 0.9, "You"), Word("mean", 8.626, 8.927, 0.9, "mean"),
         Word("succeed?", 8.927, 9.987, 0.9, "succeed?")]
    caps = [Caption("you mean succeed?", 498, 642, "voice", list(w)), Caption("no", 642, 680, "voice")]
    out, rep = caption_rules.enforce(caps, FPS, "voice", w, keep_groups=True)
    assert [c.text.lower() for c in out[:2]] == ["you mean", "succeed?"]    # your video6 split, not "you | mean ..."
    assert out[0].end == out[1].start == round(8.927 * 60) and out[1].end == 642
    short = [Caption("you mean it", 0, 80, "voice")]                          # 1.33 s: stays
    assert [c.text.lower() for c in caption_rules.enforce(short, FPS, "voice", keep_groups=True)[0]] == ["you mean it"]


def test_only_a_words_soft_tail_goes_with_the_pause():
    import numpy as np
    from match_cuts import silence
    sr, fps = 48000, Fraction(60)
    y = np.zeros(sr * 10, np.float32)

    def tone(a, b, db):
        i0, i1 = int(a * sr), int(b * sr)
        y[i0:i1] = 10 ** (db / 20.0) * math.sqrt(2.0) * np.sin(2 * np.pi * 220.0 * np.arange(i1 - i0) / sr)
    tone(1.0, 1.75, -20.0)                     # the loud part ...
    tone(1.75, 2.0, -40.0)                     # ... and its soft tail, then the pause 2.0-2.6 s
    lv = {"speech_db": -20.0, "noise_db": -60.0, "threshold_db": -45.0, "how": "t"}

    def cut(words):
        cs, _ = silence.pause_ranges(y, sr, fps, 600, silence.Settings(), (), words=words, quiet=[(2.0, 2.6)], lv=lv)
        return [(c.a, c.b) for c in cs]
    w = SimpleNamespace(text="fail", start=1.2, end=1.8)
    trimmed = cut([w])[0][0]
    assert trimmed < 117                        # a word's tail goes: from 0.05 s after its loud part (about 1.8 s)
    assert cut(None)[0][0] == trimmed           # no transcript: as before
    assert cut([])[0][0] == 123                 # no word there (video6's high five): its soft end stays (2.05 s)
    far = SimpleNamespace(text="option", start=0.2, end=0.6)
    assert cut([far])[0][0] == 123


@pytest.mark.parametrize("shift, ok", [(1, True), (3, False)])
def test_xml_rate_allows_a_cut_one_frame_off_but_not_three(tmp_path, shift, ok):
    import xml.etree.ElementTree as ET
    cl, cfg, base, final, res = _pair(tmp_path)
    assert ex.rate_problems(base, final) == []
    root = ET.parse(final).getroot()
    v1 = root.find("sequence/media/video/track")
    items = v1.findall("clipitem")
    # the first real cut between two takes (not a seamless split): move it `shift` frames earlier, the clip after
    # keeping its first frame (its content now starts earlier on the timeline)
    for p, q in zip(items, items[1:]):
        if int(q.findtext("in")) != int(p.findtext("out")) and int(p.findtext("end")) == int(q.findtext("start")):
            for el, tag in ((p, "end"), (p, "out"), (q, "start")):
                el.find(tag).text = str(int(el.findtext(tag)) - shift)
            break
    bad = tmp_path / "moved.xml"
    ET.ElementTree(root).write(bad, encoding="utf-8")
    probs = [p_ for p_ in ex.rate_problems(base, bad) if "V1" in p_]
    assert (probs == []) == ok, probs


def test_check_all_scores_the_60_fps_copies_when_the_edit_was_delivered_at_the_raws_rate(tmp_path):
    from match_cuts.run_folders import planned_files
    run = tmp_path / "001"
    (run / "extras").mkdir(parents=True)
    assert planned_files(run) == (run / "1_edit.xml", run / "2_captions.srt")            # delivered at 60: itself
    for n in ("edit_60fps.xml", "captions_60fps.srt"):
        (run / "extras" / n).write_text("x", encoding="utf-8")
    assert planned_files(run) == (run / "extras" / "edit_60fps.xml", run / "extras" / "captions_60fps.srt")
    fast = tmp_path / "002"                                       # --speed 125 at the RAW's rate: the fast edit on 60
    (fast / "extras").mkdir(parents=True)
    for n in ("edit_100pct.xml", "captions_100pct.srt", "edit_125pct_60fps.xml", "captions_125pct_60fps.srt"):
        (fast / "extras" / n).write_text("x", encoding="utf-8")
    assert planned_files(fast) == (fast / "extras" / "edit_125pct_60fps.xml", fast / "extras" / "captions_125pct_60fps.srt")


def test_a_framing_piece_left_one_frame_long_at_the_raws_rate_goes_into_its_take():
    from match_cuts.geometry import Sim
    from match_cuts.model import Segment
    seg = Segment(id=1, type="raw", comp_in=0, comp_out=300, raw_in_seconds=10.0, speed=1.0, confidence=.9)
    ev = SimpleNamespace(seg=seg, rec_in=0, rec_out=300, seg_name="S01", dissolve_in=0)
    a_sim, b_sim = Sim(0.5, 0.0, 100.0, 0.0), Sim(0.6, 0.0, -50.0, 0.0)

    def clip(r0, r1, s0, sim):
        return ex.PremiereClip(seg, ev, r0, r1, r0, r1, s0, s0 + (r1 - r0), 1.0, True, 0.0, 1.0, True, [(s0, sim)],
                               None, [ev])
    # one take at 60 fps: 2 s, then a 3-tick piece at another framing (the competitor's reframe), then 2 s more
    clips = [clip(0, 120, 600, a_sim), clip(120, 123, 720, b_sim), clip(123, 243, 723, a_sim)]
    R = Fraction(24000, 1001)
    out, _, _, n, _ = ex.rerate_edit(clips, [], [], 243, Fraction(60), R, R)
    assert [c.rec_end - c.rec_start for c in out] != [48, 1, 48]           # no framing shown for a single frame
    assert all(c.rec_end - c.rec_start > 1 for c in out) and sum(c.rec_end - c.rec_start for c in out) == n
    assert all(p.rec_end == q.rec_start and p.src_out == q.src_in for p, q in zip(out, out[1:]))   # seamless
    # ... but not across a RAW shot change at that frame: there the piece starts a new shot and stays with the next
    shot_s = float(Fraction(int(math.floor(Fraction(720, 60) * R + Fraction(1, 10)))) / R)
    out2, _, _, _, _ = ex.rerate_edit(clips, [], [], 243, Fraction(60), R, R, [shot_s])
    assert all(c.rec_end - c.rec_start > 1 for c in out2)


def test_a_cut_a_tick_after_a_raw_shot_change_lands_on_it_at_the_raws_rate():
    """video021 (29.97 RAW): the 60 fps edit cut one tick (17 ms) after the RAW's shot change at frame 1609, so the
    clip before showed 0.4 of the new shot's first frame -- at 29.97 fps a whole frame at its framing (XML FLASH).
    A frame at a shot change goes to the clip that shows the larger part of it."""
    from match_cuts.geometry import Sim
    from match_cuts.model import Segment
    seg = Segment(id=1, type="raw", comp_in=0, comp_out=300, raw_in_seconds=10.0, speed=1.0, confidence=.9)
    ev = SimpleNamespace(seg=seg, rec_in=0, rec_out=300, seg_name="S01", dissolve_in=0)
    R = Fraction(30000, 1001)

    def clip(r0, r1, s0, sim):
        return ex.PremiereClip(seg, ev, r0, r1, r0, r1, s0, s0 + (r1 - r0), 1.0, True, 0.0, 1.0, True, [(s0, sim)],
                               None, [ev])
    clips = [clip(0, 66, 3156, Sim(0.5, 0.0, 100.0, 0.0)), clip(66, 126, 3222, Sim(0.6, 0.0, -50.0, 0.0))]
    shot = float(Fraction(1609) / R)                         # RAW frame 1609 (53.687 s); the cut at 53.700 s
    out, _, _, _, _ = ex.rerate_edit(clips, [], [], 126, Fraction(60), R, R, [shot])
    assert out[0].src_out == out[1].src_in == 1609 and out[0].rec_end == out[1].rec_start
    # a new take starting a tick before a shot change (the plan shows less than half of the frame before): on it
    other = [clip(0, 66, 3156, Sim(0.5, 0.0, 100.0, 0.0)), clip(66, 126, 4001, Sim(0.6, 0.0, -50.0, 0.0))]
    assert Fraction(4001) * R / 60 == Fraction(4001 * 1000, 2002)          # 1998.50: half a frame of 1998 shown
    out2, _, _, _, _ = ex.rerate_edit(other, [], [], 126, Fraction(60), R, R, [float(Fraction(1999) / R)])
    assert out2[1].src_in == 1999                                          # not 1998, the shot before's last frame
    out3, _, _, _, _ = ex.rerate_edit(other, [], [], 126, Fraction(60), R, R, [])
    assert out3[1].src_in == 1998                                          # no shot change there: as the plan shows


def test_at_125_percent_a_clip_never_starts_on_the_frame_before_its_first():
    """laptop004 (--speed 125, 23.976 RAW): S03 starts on the RAW's shot change (frame 813), but Premiere starts a
    retimed clip only every 1.25 RAW frames: <in> 650 is RAW 812.5 -- frame 812, the shot before's last, at S03's
    framing (XML FLASH). The in-point is rounded up on that grid."""
    from match_cuts.geometry import Sim
    from match_cuts.model import Segment
    seg = Segment(id=1, type="raw", comp_in=0, comp_out=300, raw_in_seconds=10.0, speed=1.25, confidence=.9)
    ev = SimpleNamespace(seg=seg, rec_in=0, rec_out=300, seg_name="S03", dissolve_in=0)
    R = Fraction(24000, 1001)
    src = int(math.ceil(Fraction(813) / R * 60))                  # the plan's in-point: just inside RAW frame 813
    c = ex.PremiereClip(seg, ev, 0, 120, 0, 120, src, src + 150, 1.25, True, 0.0, 1.0, True,
                        [(src, Sim(0.5, 0.0, 0.0, 0.0))], None, [ev])
    out, _, _, _, _ = ex.rerate_edit([c], [], [], 120, Fraction(60), R, R, [float(Fraction(813) / R)])
    x_in = ex.remap_encode(out[0].src_in, out[0].src_out, 1.25, out[0].rec_end - out[0].rec_start)[0]
    assert math.floor(Fraction(x_in) * Fraction(5, 4)) == 813      # what Premiere shows first: the new shot's frame


def test_a_competitor_cut_on_a_raw_shot_change_at_125_percent_lands_on_it():
    """laptop004 (--speed 125, 23.976 RAW): the plan's S03 ends at RAW 998.50 and S04 starts at 998.60 -- both hold part
    of the shot change's first frame (998). S03 must end before it and S04 start on it (no repeat, no flash)."""
    from match_cuts.geometry import Sim
    from match_cuts.model import Segment
    seg = Segment(id=1, type="raw", comp_in=0, comp_out=300, raw_in_seconds=10.0, speed=1.25, confidence=.9)
    ev = SimpleNamespace(seg=seg, rec_in=0, rec_out=300, seg_name="S03", dissolve_in=0)
    R = Fraction(24000, 1001)

    def clip(r0, r1, s0, n, sim):
        return ex.PremiereClip(seg, ev, r0, r1, r0, r1, s0, s0 + n, 1.25, True, 0.0, 1.0, True, [(s0, sim)], None,
                               [ev])
    shots = [float(Fraction(813) / R), float(Fraction(998) / R)]
    for n3 in (463, 464):            # the plan's S03 ends a tick before S04 (two takes: laptop004) or on it (one take)
        clips = [clip(186, 557, 2035, n3, Sim(0.5, 0.0, 0.0, 0.0)), clip(557, 596, 2499, 49, Sim(0.6, 0.0, 9.0, 0.0))]
        s3, s4 = ex.rerate_edit(clips, [], [], 596, Fraction(60), R, R, shots)[0]
        x3 = ex.remap_encode(s3.src_in, s3.src_out, 1.25, s3.rec_end - s3.rec_start)[0]
        x4 = ex.remap_encode(s4.src_in, s4.src_out, 1.25, s4.rec_end - s4.rec_start)[0]
        last3 = math.floor(Fraction(x3 + s3.rec_end - s3.rec_start - 1) * Fraction(5, 4))   # what Premiere shows
        first4 = math.floor(Fraction(x4) * Fraction(5, 4))
        assert last3 < 998 == first4 and s3.rec_end == s4.rec_start, (n3, last3, first4)
