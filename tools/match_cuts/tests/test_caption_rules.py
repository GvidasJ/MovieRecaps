"""caption_rules.py: the "Hard rules" of caption-generator-prompt.md, the final check on every caption file.

* bad_captions.srt (a real run's competitor captions as read, in the repo root): with and without a transcript, the
  file written breaks none of rules 1-4, keeps the competitor's timing and gaps, fixes what is mechanical (weak
  endings, length, stray marks, screen noise) and lists the rest instead of guessing;
* the examples that went wrong: "How are yoU? Thanks", "We wre", a run of ALL-CAPS words, a deliberate misspelling;
* voice mode: the words of the ten reference SRTs come out passing all the checks;
* grouping: a caption is never a single weak word, the pairs kept together are never split, a competitor showing one
  word at a time is regrouped on its own timing, and capitals only for "I", names, acronyms and after a pause (the
  examples from a real run: "a" | "joke", "I" | "know", "Bronx" | "School", "joke" | "And" | "Marvel").
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
    """Every caption starts where a competitor caption starts (or inside one, where it was split), and every gap of
    the competitor's longer than a second (a silence) is still a gap."""
    assert all(any(o.start <= c.start < o.end for o in orig) for c in out)
    for a, b in zip(orig, orig[1:]):
        if b.start - a.end > 60:
            assert not any(c.start < b.start and c.end > a.end for c in out)
    assert not [c.text for c in out if len(c.text.split()) == 1 and C.is_weak(c.text)]     # no lone weak word


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
    assert all(rep["left"][r] == 0 for r in (1, 2, 3, 4, 6, 7, 8, 9))
    assert not any(re.search(r"(?<!\d)[.,]|[.,](?!\d)", t) for t in got)          # no full stops / commas
    # regrouped: weak words with the next words, names and pronoun + verb kept together
    for a, b in [("You play", "the films main"), ("Mayday Parker", "the daughter"), ("the daughter", "of Peter and MJ"),
                 ("bring a new era", "of X-Men mutants"), ("Sadie Sink", "you are"), ("you are", "in the film")]:
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
    assert {"Have t1o", "I lless we", "Dont know", "Wiu knowe?", "Wwnt you e"} <= flagged    # a capital after a pause
    assert "Dont know" in got and any("Spider-Mans" in t for t in got)          # clearly read: never changed


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
    # "t1o" is no one's spelling: "to"; "1" read where "I" was said, then the lone "I" joins "don't know"
    assert "have to see" in got and "I don't know" in got and "You know?" in got
    assert "you are" in got and any("Spider-Mans" in t for t in got)           # real words: never changed
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
    assert [(c.text, c.start, c.end) for c in out] == [("How are you?", 0, 54), ("thanks", 54, 72)]   # no pause
    assert rep["changed"][1] == 1 and rep["changed"][3] == 1
    # no "?" on screen: the transcript's sentence end is the speaker boundary (rule 2)
    out, rep = R.enforce([cap("How are you Thanks", 0.0, 1.2)], FPS, "competitor", words)
    assert texts(out) == ["How are you", "thanks"] and rep["changed"][2] == 1
    # no transcript: the text's own "?" still splits it, the time shared by characters
    out, _ = R.enforce([cap("How are yoU? Thanks", 0.0, 1.2)], FPS, "competitor", None)
    assert texts(out) == ["How are you?", "thanks"] and out[0].start == 0 and out[-1].end == 72


def test_a_garbled_reading_takes_the_word_heard_but_a_clearly_read_misspelling_stays():
    words = heard("We're going to the shop.", 2.0) + heard("my new computer", 4.0) + heard("I don't know", 6.0)
    caps = [cap("We wre", 2.0, 2.3, score=0.78), cap("going to the shop", 2.3, 3.4),
            cap("my new compluter", 4.0, 4.9), cap("I dont know", 6.0, 6.9)]
    out, rep = R.enforce(caps, FPS, "competitor", words)
    assert texts(out) == ["We're", "going to the shop", "My new compluter", "I dont know"]   # 0.56 s pause: "My"
    assert [r["detail"].split(":")[0] for r in rep["rows"] if r["kind"] == "changed"] == ["'We wre' -> 'We're'"]
    flagged = {r["text"]: r["detail"] for r in rep["rows"] if r["kind"] == "flagged"}
    assert "compluter" in flagged["My new compluter"] and "heard: 'my new computer'" in flagged["My new compluter"]
    assert "'dont'" in flagged["I dont know"]                    # read clearly: listed, not changed


def test_all_caps_words_are_written_in_sentence_case_and_acronyms_stay():
    words = (heard("So as a joke, Marvel suggested I should go", 0.0) + heard("to the next school.", 3.0)
             + heard("It had to be the MCU and AI, MJ.", 5.0))
    caps = [cap("SO AS A", 0.0, 0.8), cap("JOKE", 0.8, 1.2), cap("MARVEL SUGGESTED", 1.2, 1.8),
            cap("I SHOULD GO", 1.8, 2.7),
            cap("TO THE NEXT SCHOOL.", 3.0, 4.2), cap("IT HAD TO BE", 5.0, 6.2), cap("THE mcu AND Ai, MJ.", 6.2, 7.6)]
    out, rep = R.enforce(caps, FPS, "competitor", words)
    got = texts(out)
    assert got == ["So as a joke", "Marvel suggested", "I should go", "to the next school", "It had to be",
                   "the MCU and AI MJ"]                    # "a" | "joke" regrouped; stops and commas off
    assert not [t for t in got if re.search(r"\b(?!MCU|AI|MJ)[A-Z]{2,}\b", t)]
    assert rep["changed"][4] == 7 and rep["left"][4] == 0
    # without a transcript: the word list's casing (PETER next to the name PARKER), a capital after a pause
    out, _ = R.enforce([cap("WAS SHOULD THE BE NEXT HAD", 0, 2), cap("PETER PARKER", 2.5, 3)], FPS, "competitor")
    assert texts(out) == ["Was should the be", "next had", "Peter Parker"]      # 26 characters: split


def test_a_one_letter_word_on_screen_stays_but_digits_and_marks_where_nothing_is_heard_do_not():
    caps = [cap("I", 0.0, 0.5), cap("1", 0.5, 1.5), cap("know", 1.5, 2.0), cap("V", 2.0, 3.0), cap("_", 3.0, 4.0)]
    for words in ([], None):                         # an edit with no speech heard / no transcript at all
        out, rep = R.enforce(caps, FPS, "competitor", words)
        assert [(c.text, c.start, c.end) for c in out] == [("I know", 0, 120)] and rep["notes"]["noise_dropped"] == 3
    out, _ = R.enforce([cap("1", 0.5, 1.5)], FPS, "competitor", heard("one", 0.9))   # "1" where "one" was said
    assert texts(out) == ["One"]                                  # the first word: after silence


def test_the_acronym_allowlist_is_a_file_that_can_be_extended(tmp_path):
    assert {"AI", "MJ", "MCU"} <= set(R.read_allowlist())
    assert R.ALLOWLIST_FILE.is_file() and R.ALLOWLIST_FILE.name == "caption_allowlist.txt"
    p = tmp_path / "allow.txt"
    p.write_text("# mine\nAI\nMJ, MCU\nGOJO\niPhone\nquazoosl\nBronx High School of Science\n", encoding="utf-8")
    lex = R.lexicon(p)
    out, rep = R.enforce([cap("GOJO has an IPHONE", 0, 1), cap("quazoosl FBI WAS", 1, 2)], FPS, "competitor",
                         None, lex)
    assert texts(out) == ["GOJO has an iPhone", "quazoosl FBI was"]                # FBI: the word list's
    assert rep["flagged"][5] == 0
    out, rep = R.enforce([cap("GOJO quazoosl", 0, 1)], FPS, "competitor", None)       # not on the default list
    assert texts(out) == ["Gojo quazoosl"] and rep["flagged"][5] == 1
    assert "Bronx High School of Science" in R.read_allowlist(p)                       # a phrase per line


def test_weak_words_move_to_the_next_words_but_never_across_a_sentence_end():
    caps = [cap("I bought a", 0.0, 0.6), cap("beanie hat", 0.6, 1.2),               # touching
            cap("we went to", 2.0, 2.6), cap("the shop", 3.0, 3.6),                 # a short pause: still moves
            cap("you do it", 4.0, 4.6), cap("That is a pro", 4.6, 5.2)]             # the screen starts a sentence
    out, rep = R.enforce(caps, FPS, "competitor", None)
    assert texts(out) == ["I bought", "a beanie hat", "We went", "to the shop", "you do it", "that is a pro"]
    assert [(c.start, c.end) for c in out] == [(0, 32), (32, 72), (120, 149), (149, 216), (240, 276), (276, 312)]
    assert {w["caption"]: w["reason"] for w in rep["kept_weak"]} == {
        "you do it": "the next caption starts a new sentence"}
    assert rep["flagged"][7] == 1 and rep["left"][7] == 0


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
    assert texts(out) == ["How are you?", "thanks", "Dont was", "it you can"]
    assert line == ("1 one sentence: 1 split; 2 one speaker: 0 split; 3 casing inside a word: 1 recased; "
                    "4 capitals: 3 recased; 5 real words: 1 flagged; 6 length: 0 split; 7 weak words: 0 moved or "
                    "joined, 1 kept (listed); 8 no gaps: the competitor's timing kept, gaps included; 9 kept together: "
                    "2 of the competitor's captions regrouped into 2"), line
    _, rep = R.enforce([C.Caption("hello there", 0, 30, "voice", heard("hello there", 0.0, 0.2)),
                        C.Caption("friend", 40, 60, "voice", heard("friend", 0.66, 0.2))], FPS, "voice", None)
    assert R.summary_line(rep).endswith("8 no gaps: 1 closed; 9 kept together: 0 flagged")


# ---------------------------------------------------------------------------------------------
# Voice mode
# ---------------------------------------------------------------------------------------------

def test_voice_mode_follows_the_hard_rules():
    words = (heard("How are you? Thanks.", 0.0) + heard("I bought a", 1.6) + heard("beanie hat.", 2.9)
             + heard("It WAS the MCU", 5.0))
    caps = C.voice_captions(words, FPS, n_frames=C.to_frame(words[-1].end, FPS))
    out, rep = R.enforce(caps, FPS, "voice", words)
    assert texts(out) == ["How are you?", "thanks", "I bought", "a beanie hat", "*...*", "It was the MCU"]
    assert all(a.end == b.start for a, b in zip(out, out[1:]))                        # rule 8: back to back
    assert not any(R.check(out, FPS, "voice").values())


@pytest.mark.skipif(not SRT_DIR.is_dir(), reason="srt/ reference files not found")
@pytest.mark.parametrize("name", NAMES)
def test_the_words_of_each_reference_srt_through_voice_mode_pass_all_the_checks(name):
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
    assert not [c.text for c in out if len(c.words) == 1 and C.is_weak(c.text)]       # never a lone weak word


# ---------------------------------------------------------------------------------------------
# Grouping: the examples from a real competitor run (one word per caption)
# ---------------------------------------------------------------------------------------------

TRUTH_SRT = Path(__file__).resolve().parent / "fixtures" / "competitor_captions_truth.srt"
# what the transcript of that edit hears, word for word with the competitor's captions (Whisper's capitals and
# punctuation: a capital at every sentence start)
HEARD = ('So as a joke, I suggested to Marvel that I should go to a high school undercover, right? And it was '
         'completely a joke. And Marvel took it completely seriously. So the next thing I know, I had a backpack with '
         'a pencil case on my way to Bronx School of Science, the school for genius kids. I went to school with a fake '
         'name and a fake accent. Even the teachers didn\'t know that I was not a real student. So they would bring me '
         'up to the front of the class and be like, "What dya think, new kid?" I\'m like, I have no idea what you\'re '
         'talking about. So I was sat at the back of a classroom next to quite a pretty girl. And then she\'s like, '
         '"So, dude, what\'s your deal, man?" I was like, well, do you want to know my secret? I\'m actually '
         'Spider-Man.')


def competitor_run():
    """The competitor's 112 captions (the answer key of input/competitor.mp4, ALL CAPS, mostly one word at a time)
    and the transcript, each word timed inside the caption that shows it."""
    key = C.read_srt(TRUTH_SRT)
    said = HEARD.split()
    caps, words = [], []
    for k in key:
        a, b = round(k["start_ms"] * 60 / 1000), round(k["end_ms"] * 60 / 1000)
        caps.append(C.Caption(k["text"], a, b, "competitor", info={"score": 0.99, "agreement": 1.0, "reads": 3}))
        if C.is_action_text(k["text"]):
            continue
        toks = k["text"].split()
        for i in range(len(toks)):
            raw = said[len(words)].strip('"')
            words.append(C.Word(C.clean_text(raw), (a + (b - a) * i / len(toks)) / 60,
                                (a + (b - a) * (i + 0.9) / len(toks)) / 60, 0.95, raw))
    assert len(words) == len(said) == 151
    return caps, words


@pytest.mark.skipif(not TRUTH_SRT.is_file(), reason="the competitor answer key is not in this checkout")
def test_one_word_competitor_captions_are_regrouped_on_the_competitors_timing():
    caps, words = competitor_run()
    out, rep = R.enforce(caps, FPS, "competitor", words)
    got = texts(out)
    # the examples that came out one word per caption
    for want in ("I know", "a backpack", "the school", "of Science", "So as a joke", "completely a joke"):
        assert want in got, want
    assert "to Bronx School" in got and got[got.index("to Bronx School") + 1] == "of Science"   # a name kept whole
    assert "and Marvel took" in got                      # "joke" | "And" | "Marvel": no capital for "And"
    assert not [t for t in got if t.split()[0] in ("And", "The", "You")]
    # never a single weak word; 2-4 words but where nothing could join
    assert not [t for t in got if len(t.split()) == 1 and C.is_weak(t)]
    assert sorted(t for t in got if len(t.split()) == 1) == ["*laughs*", "Spider-Man", "about", "seriously"]
    assert all(len(t) <= 20 and len(t.split()) <= 4 for t in got if not C.is_action_text(t))
    # the competitor's timing: each caption starts on the frame its first word appeared on their screen
    starts = {c.start for c in caps}
    assert all(c.start in starts for c in out) and out[-1].end == caps[-1].end
    assert all(a.end == b.start for a, b in zip(out, out[1:]))          # their captions touch: so do these
    # the competitor's own captions of 2+ words that pass my rules stay as they were
    for kept in ("I have no idea", "a real student", "my secret", "I'm actually", "so I was sat", "the school"):
        assert kept in got, kept
    assert "“so dude what's”" in got                     # quoted words shown one at a time: one pair of quotes
    assert not any(v for r, v in rep["left"].items() if r != 5)
    assert rep["notes"]["regrouped"] == 97 and len(out) == 56


def test_a_caption_is_never_a_single_weak_word():
    # "a" | "joke", "I" | "know", "the" | "school" from the competitor, each shown alone
    caps = [cap("SO AS", 0.0, 0.4), cap("A", 0.4, 0.5), cap("JOKE", 0.5, 0.8), cap("I", 3.0, 3.1),
            cap("KNOW", 3.1, 3.3), cap("THE", 5.0, 5.3), cap("SCHOOL", 5.3, 5.8)]
    out, _ = R.enforce(caps, FPS, "competitor", None)
    assert texts(out) == ["So as a joke", "I know", "The school"]          # each after a pause: a capital
    assert [(c.start, c.end) for c in out] == [(0, 48), (180, 198), (300, 348)]
    # voice mode: a lone weak word joins the words after it, or -- before a silence -- the caption before it
    words = heard("I", 0.0) + heard("know what", 0.6) + heard("we went to", 3.0) + heard("the", 3.95)
    caps = C.voice_captions(words, FPS, n_frames=C.to_frame(words[-1].end, FPS) + 120)
    out, _ = R.enforce(caps, FPS, "voice", words)
    assert [t for t in texts(out) if t != C.PLACEHOLDER] == ["I know what", "We went to the"]   # after silence


def test_pairs_kept_together_are_never_split():
    def bonds(text):
        ws = [C.Word(C.clean_text(t), 0.2 * i, 0.2 * i + 0.2, 1.0, t) for i, t in enumerate(text.split())]
        return [f"{ws[i].text} {ws[i + 1].text}" for i, b in enumerate(C.compute_bonds(ws)) if b]
    assert bonds("it was a joke") == ["it was", "a joke"]                    # pronoun + verb, determiner + noun
    assert bonds("we went to the school") == ["we went", "to the", "the school"]
    assert bonds("on my way to Bronx High School of Science") == [
        "on my", "my way", "to Bronx", "Bronx High", "High School", "School of", "of Science"]
    assert bonds("this is your deal") == ["this is", "your deal"]
    assert bonds("a joke, Marvel") == ["a joke"]                             # a comma ends the phrase
    assert bonds("I know so") == ["I know"]
    # in competitor mode: "Bronx" | "School" | "of" | "Science" shown one at a time
    caps = [cap("TO", 0.0, 0.2), cap("BRONX", 0.2, 0.5), cap("SCHOOL", 0.5, 0.8), cap("OF", 0.8, 0.9),
            cap("SCIENCE", 0.9, 1.4)]
    words = heard("to Bronx School of Science.", 0.0, 0.25)
    out, _ = R.enforce(caps, FPS, "competitor", words)
    assert texts(out) == ["To Bronx School", "of Science"]          # 23 characters: cut where a name is not split


def test_the_allowlist_phrases_are_kept_together(tmp_path, monkeypatch):
    p = tmp_path / "allow.txt"
    p.write_text("AI\nnew kid on the block\n", encoding="utf-8")
    monkeypatch.setattr(R, "ALLOWLIST_FILE", p)
    ws = [C.Word(t, 0.2 * i, 0.2 * i + 0.2, 1.0, t) for i, t in enumerate("he is the new kid on the block".split())]
    assert C.compute_bonds(ws) == [True, False, True, True, True, True, True]


def test_capitals_only_for_I_names_acronyms_and_after_a_pause():
    # "joke" | "And" | "Marvel": the transcript starts a sentence at "And", but there is no pause before it
    caps = [cap("A JOKE", 0.0, 0.6), cap("AND", 0.6, 0.75), cap("MARVEL", 0.75, 1.2), cap("TOOK IT", 1.2, 1.6),
            cap("THE MCU", 2.6, 3.0)]
    words = heard("a joke. And Marvel took it.", 0.0, 0.3) + heard("The MCU", 2.6, 0.2)
    out, _ = R.enforce(caps, FPS, "competitor", words)
    # "TOOK IT" stays the competitor's own caption (its weak "it" ends the sentence)
    assert texts(out) == ["A joke", "and Marvel", "took it", "The MCU"]   # the first word / after a 1 s pause
    # voice mode: Whisper's sentence capitals are not kept, names are
    words = heard("So we went. And the guy said Peter Parker is in the MCU. The end", 0.0, 0.25)
    out, _ = R.enforce(C.voice_captions(words, FPS, n_frames=C.to_frame(words[-1].end, FPS)), FPS, "voice", words)
    assert " ".join(texts(out)) == "So we went and the guy said Peter Parker is in the MCU the end"
