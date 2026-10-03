"""caption_rules.py: the "Hard rules" of caption-generator-prompt.md, the final check on every caption file.

* bad_captions.srt (a real run's competitor captions as read, in the repo root): with and without a transcript, the
  file written breaks none of rules 1-4, keeps the competitor's timing and gaps, fixes what is mechanical (weak
  endings, length, stray marks, screen noise) and lists the rest instead of guessing;
* the examples that went wrong: "How are yoU? Thanks", "We wre", a run of ALL-CAPS words, a deliberate misspelling;
* voice mode: the words of the ten reference SRTs come out passing all eight checks.
"""
from __future__ import annotations

import re
from fractions import Fraction
from pathlib import Path

import pytest

from match_cuts import caption_rules as R, captions as C

FPS = Fraction(60)
BAD = Path(__file__).resolve().parents[3] / "bad_captions.srt"
SRT_DIR = Path(__file__).resolve().parents[3] / "srt"
NAMES = ["beanie-hats", "central-park-dog", "giolitti", "instagram-casting", "keanu-marvel", "lyrics-quiz",
         "own-stunts", "spiderman-driving-test", "spiderman-interview", "thor-hammer"]


def tc(fr: int) -> str:
    return C.ms_tc(C.frame_ms(fr, FPS))


def bad_caps(score: float | None = None) -> list[C.Caption]:
    info = {} if score is None else {"score": score, "agreement": 1.0, "reads": 3}
    return [C.Caption(c["text"], round(c["start_ms"] * 60 / 1000), round(c["end_ms"] * 60 / 1000), "competitor",
                      info=dict(info)) for c in C.read_srt(BAD)]


def heard(text: str, t0: float, step: float = 0.3, prob: float = 0.95) -> list[C.Word]:
    """Words of a sentence as the transcript would give them (punctuation on the raw word), one every ``step`` s."""
    out = []
    for i, tok in enumerate(text.split()):
        out.append(C.Word(C.clean_text(tok), t0 + i * step, t0 + i * step + 0.8 * step, prob, tok))
    return out


def texts(caps) -> list[str]:
    return [c.text for c in caps]


def assert_rules_1_to_4(caps, mode="competitor"):
    left = R.check(caps, FPS, mode, rules=(1, 2, 3, 4))
    assert not any(left.values()), left


def assert_timing_kept(out, orig):
    """Every caption inside the competitor's own captions (never over one of its gaps) and every gap still a gap."""
    covered = {f for c in orig for f in range(c.start, c.end)}
    assert all(set(range(c.start, c.end)) <= covered for c in out)
    for a, b in zip(orig, orig[1:]):
        if b.start > a.end:                              # a gap of the competitor's
            assert not any(c.start < b.start and c.end > a.end for c in out)


# ---------------------------------------------------------------------------------------------
# bad_captions.srt
# ---------------------------------------------------------------------------------------------

needs_bad = pytest.mark.skipif(not BAD.is_file(), reason="bad_captions.srt not in this checkout")


@needs_bad
def test_bad_captions_without_a_transcript_come_out_right():
    orig = bad_caps()
    out, rep = R.enforce(orig, FPS, "competitor", None)
    got = texts(out)
    assert_rules_1_to_4(out)
    assert_timing_kept(out, orig)
    assert all(rep["left"][r] == 0 for r in (1, 2, 3, 4, 6, 7, 8))
    assert not any(re.search(r"(?<!\d)[.,]|[.,](?!\d)", t) for t in got)          # no full stops / commas
    # weak last words moved to the next caption where the captions touch; the boundary moves with the word
    for a, b in [("You play", "the films main"), ("Parker", "the daughter"), ("the daughter", "of Peter and MJ"),
                 ("bring a new era", "of X-Men mutants")]:
        i = got.index(a)
        assert got[i + 1] == b and out[i].end == out[i + 1].start
    assert "You play the" not in got and "Parker the" not in got and "daughter of" not in got
    # length (rule 6): 21 characters split at a word, in the caption's own time
    i = got.index("That is how")
    assert got[i + 1] == "you do it" and out[i].start == 3836 and out[i + 1].end == 3910
    assert "into the MCU" in got and "of Peter and MJ" in got                     # acronyms stay
    # screen noise ("1", "V", "_", "二", flickers of a garbled reading) left out, and listed
    assert not [t for t in got if not re.search(r"[A-Za-z]{2}|^I$", t)]
    noise = [r for r in rep["rows"] if r["rule"] == 5 and "screen noise" in r["detail"]]
    assert len(noise) == rep["notes"]["noise_dropped"] >= 50
    assert {"V", "VI", "V1", "1i", "1"} <= {r["text"] for r in noise}
    # garbled words are not guessed without a transcript: kept as read and listed
    flagged = {r["text"] for r in rep["rows"] if r["kind"] == "flagged" and r["rule"] == 5}
    assert {"have t1o", "I lless We", "dont know", "wiu knowe?", "wwnt you e"} <= flagged
    assert "dont know" in got and "Spider-Mans" in got                          # clearly read: never changed


@needs_bad
def test_bad_captions_with_a_transcript_take_its_words_only_for_garbled_readings():
    orig = bad_caps(score=0.85)                         # the OCR was unsure of these readings
    words = (heard("Sadie Sink, you are in the film Spider-Man: Brand New Day. Correct.", 0.3, 0.45)
             + heard("I'd have to see", 13.15, 0.15) + heard("I", 31.0) + heard("don't know.", 31.6, 0.25)
             + heard("You know?", 49.6, 0.2))
    out, rep = R.enforce(orig, FPS, "competitor", words)
    got = texts(out)
    assert_rules_1_to_4(out)
    assert_timing_kept(out, orig)
    # "t1o" is no one's spelling: "to" (then the weak "to" moves on to "see")
    assert got[got.index("have"):got.index("have") + 2] == ["have", "to see"] and "have t1o" not in got
    assert got[got.index("don't know") - 1] == "I"          # "1" read where "I" was said; "dont": unsure, not a word
    assert "You know?" in got and "wiu knowe?" not in got
    assert "you are in the film" in got and "Spider-Mans" in got               # real words: never changed
    rows = [r for r in rep["rows"] if r["rule"] == 5 and r["kind"] == "changed" and "screen noise" not in r["detail"]]
    assert [r["detail"].split(":")[0] for r in rows] == [
        "'t1o' -> 'to'", "the screen reading '1' is not words", "'dont' -> 'don't'", "'wiu knowe?' -> 'You know?'"]
    assert rep["notes"]["from_transcript"] == 4
    # a flicker, or a reading with no word in it where nothing was heard: left out
    assert "1" not in got and "I'd" not in got and "ad see" in got


# ---------------------------------------------------------------------------------------------
# The examples that went wrong
# ---------------------------------------------------------------------------------------------

def cap(text, a_s, b_s, score=0.99):
    return C.Caption(text, int(round(a_s * 60)), int(round(b_s * 60)), "competitor",
                     info={"score": score, "agreement": 1.0, "reads": 4})


def test_a_question_and_its_reply_never_share_a_caption():
    words = heard("How are you? Thanks.", 0.0)                       # "Thanks." at 0.9 s
    out, rep = R.enforce([cap("How are yoU? Thanks", 0.0, 1.2)], FPS, "competitor", words)
    assert [(c.text, c.start, c.end) for c in out] == [("How are you?", 0, 54), ("Thanks", 54, 72)]
    assert rep["changed"][1] == 1 and rep["changed"][3] == 1
    # no "?" on screen: the transcript's sentence end is the speaker boundary (rule 2)
    out, rep = R.enforce([cap("How are you Thanks", 0.0, 1.2)], FPS, "competitor", words)
    assert texts(out) == ["How are you", "Thanks"] and rep["changed"][2] == 1
    # no transcript: the text's own "?" still splits it, the time shared by characters
    out, _ = R.enforce([cap("How are yoU? Thanks", 0.0, 1.2)], FPS, "competitor", None)
    assert texts(out) == ["How are you?", "Thanks"] and out[0].start == 0 and out[-1].end == 72


def test_a_garbled_reading_takes_the_word_heard_but_a_clearly_read_misspelling_stays():
    words = heard("We're going to the shop.", 2.0) + heard("my new computer", 4.0) + heard("I don't know", 6.0)
    caps = [cap("We wre", 2.0, 2.3, score=0.78), cap("going to the shop", 2.3, 3.4),
            cap("my new compluter", 4.0, 4.9), cap("I dont know", 6.0, 6.9)]
    out, rep = R.enforce(caps, FPS, "competitor", words)
    assert texts(out) == ["We're", "going to the shop", "my new compluter", "I dont know"]
    assert [r["detail"].split(":")[0] for r in rep["rows"] if r["kind"] == "changed"] == ["'We wre' -> 'We're'"]
    flagged = {r["text"]: r["detail"] for r in rep["rows"] if r["kind"] == "flagged"}
    assert "compluter" in flagged["my new compluter"] and "heard: 'my new computer'" in flagged["my new compluter"]
    assert "'dont'" in flagged["I dont know"]                    # read clearly: listed, not changed


def test_all_caps_words_are_written_in_sentence_case_and_acronyms_stay():
    words = (heard("So as a joke, Marvel suggested I should go", 0.0) + heard("to the next school.", 3.0)
             + heard("It had to be the MCU and AI, MJ.", 5.0))
    caps = [cap("SO AS A", 0.0, 0.8), cap("JOKE", 0.8, 1.2), cap("MARVEL SUGGESTED", 1.2, 1.8),
            cap("I SHOULD GO", 1.8, 2.7),
            cap("TO THE NEXT SCHOOL.", 3.0, 4.2), cap("IT HAD TO BE", 5.0, 6.2), cap("THE mcu AND Ai, MJ.", 6.2, 7.6)]
    out, rep = R.enforce(caps, FPS, "competitor", words)
    got = texts(out)
    assert got[:4] == ["So as", "a joke", "Marvel suggested", "I should go"]      # the weak "a" moved
    assert got[4:] == ["to the next school", "It had to be", "the MCU and AI MJ"]   # stops and commas off
    assert not [t for t in got if re.search(r"\b(?!MCU|AI|MJ)[A-Z]{2,}\b", t)]
    assert rep["changed"][4] == 7 and rep["left"][4] == 0
    # without a transcript: the word list's casing, a capital at a sentence start
    out, _ = R.enforce([cap("WAS SHOULD THE BE NEXT HAD", 0, 2), cap("PETER PARKER", 2.5, 3)], FPS, "competitor")
    assert texts(out) == ["Was should", "the be next had", "Peter Parker"]       # 26 characters: split, no weak end


def test_a_one_letter_word_on_screen_stays_but_digits_and_marks_where_nothing_is_heard_do_not():
    caps = [cap("I", 0.0, 0.5), cap("1", 0.5, 1.5), cap("A", 1.5, 2.0), cap("V", 2.0, 3.0), cap("_", 3.0, 4.0)]
    for words in ([], None):                         # an edit with no speech heard / no transcript at all
        out, rep = R.enforce(caps, FPS, "competitor", words)
        assert texts(out) == ["I", "A"] and rep["notes"]["noise_dropped"] == 3
    out, _ = R.enforce(caps, FPS, "competitor", heard("I", 0.1) + heard("one", 0.9))   # "1" where "one" was said
    assert texts(out)[:2] == ["I", "one"]


def test_the_acronym_allowlist_is_a_file_that_can_be_extended(tmp_path):
    assert {"AI", "MJ", "MCU"} <= set(R.read_allowlist())
    assert R.ALLOWLIST_FILE.is_file() and R.ALLOWLIST_FILE.name == "caption_allowlist.txt"
    p = tmp_path / "allow.txt"
    p.write_text("# mine\nAI MJ MCU\nGOJO iPhone\nquazoosl\n", encoding="utf-8")
    lex = R.lexicon(p)
    out, rep = R.enforce([cap("GOJO has an IPHONE", 0, 1), cap("quazoosl FBI WAS", 1, 2)], FPS, "competitor",
                         None, lex)
    assert texts(out) == ["GOJO has an iPhone", "quazoosl FBI was"]                # FBI: the word list's
    assert rep["flagged"][5] == 0
    out, rep = R.enforce([cap("GOJO quazoosl", 0, 1)], FPS, "competitor", None)       # not on the default list
    assert texts(out) == ["Gojo quazoosl"] and rep["flagged"][5] == 1


def test_weak_words_move_only_between_touching_captions_and_never_across_a_sentence_end():
    caps = [cap("I bought a", 0.0, 0.6), cap("beanie hat", 0.6, 1.2),               # touching: moves
            cap("we went to", 2.0, 2.6), cap("the shop", 3.0, 3.6),                 # a gap: kept, listed
            cap("you do it", 4.0, 4.6), cap("That is a pro", 4.6, 5.2)]             # a new sentence follows
    out, rep = R.enforce(caps, FPS, "competitor", None)
    assert texts(out) == ["I bought", "a beanie hat", "we went to", "the shop", "you do it", "That is a pro"]
    assert out[0].end == out[1].start and out[1].end == 72 and out[2].end == 156 and out[3].start == 180
    kept = {w["caption"]: w["reason"] for w in rep["kept_weak"]}
    assert kept == {"we went to": "a gap follows (the competitor's timing is kept)",
                    "you do it": "the next caption starts a new sentence"}
    assert rep["changed"][7] == 1 and rep["flagged"][7] == 2 and rep["left"][7] == 0


def test_srt_writer_refuses_a_file_that_breaks_rules_1_to_4(tmp_path):
    for text in ("How are you? Thanks", "How are yoU", "it WAS him"):
        with pytest.raises(ValueError, match="rule"):
            C.write_srt([C.Caption(text, 0, 30)], tmp_path / "x.srt", FPS)
    p = C.write_srt([C.Caption("How are you?", 0, 30), C.Caption("the MCU", 30, 60)], tmp_path / "ok.srt", FPS)
    assert p.read_text(encoding="utf-8").count("-->") == 2


def test_the_summary_says_how_many_captions_each_rule_changed_or_flagged():
    words = heard("How are you? Thanks.", 0.0)
    out, rep = R.enforce([cap("How are yoU? Thanks", 0, 1.2), cap("dont WAS it", 2, 3), cap("you can", 3, 4)],
                         FPS, "competitor", words)
    line = R.summary_line(rep)
    assert line.startswith("1 one sentence: 1 split; 2 one speaker: 0 split; 3 casing inside a word: 1 recased; "
                           "4 all caps: 1 recased; 5 real words: 1 flagged; 6 length: 0 split; 7 weak last word: "
                           "1 moved, 1 kept (listed); 8 no gaps: the competitor's timing kept"), line
    _, rep = R.enforce([C.Caption("hello there", 0, 30, "voice", heard("hello there", 0.0, 0.2)),
                        C.Caption("friend", 40, 60, "voice", heard("friend", 0.66, 0.2))], FPS, "voice", None)
    assert R.summary_line(rep).endswith("8 no gaps: 1 closed")


# ---------------------------------------------------------------------------------------------
# Voice mode
# ---------------------------------------------------------------------------------------------

def test_voice_mode_follows_the_hard_rules():
    words = (heard("How are you? Thanks.", 0.0) + heard("I bought a", 1.6) + heard("beanie hat.", 2.9)
             + heard("It WAS the MCU", 5.0))
    caps = C.voice_captions(words, FPS, n_frames=C.to_frame(words[-1].end, FPS))
    out, rep = R.enforce(caps, FPS, "voice", words)
    assert texts(out) == ["How are you?", "Thanks", "I bought", "a beanie hat", "*...*", "It was the MCU"]
    assert all(a.end == b.start for a, b in zip(out, out[1:]))                        # rule 8: back to back
    assert not any(R.check(out, FPS, "voice").values())


@pytest.mark.skipif(not SRT_DIR.is_dir(), reason="srt/ reference files not found")
@pytest.mark.parametrize("name", NAMES)
def test_the_words_of_each_reference_srt_through_voice_mode_pass_all_eight_checks(name):
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from test_captions import load, words_of
    ws = words_of(load(name))
    caps = C.voice_captions(ws, FPS, C.to_frame(ws[-1].end, FPS) + 120)
    out, rep = R.enforce(caps, FPS, "voice", ws)
    left = R.check(out, FPS, "voice")
    assert not any(left.values()), {r: v for r, v in left.items() if v}
    assert [w for c in out for w in c.words] == list(ws)                              # every word once, in order
    assert all(len(c.text) <= 20 and len(c.text.split()) <= 5 for c in out if c.mode != "placeholder")
