"""caption_style.py: the user's caption style learned from their SRTs -- break chances, the grouping, captions
timed on the first word or the cut, following a competitor that captions the user's way."""
from __future__ import annotations

from fractions import Fraction

import pytest

from match_cuts import caption_style as S
from match_cuts.captions import Word

FPS = Fraction(60)


def srt(path, rows):
    blocks = []
    for i, (text, a, b) in enumerate(rows, start=1):
        def tc(ms):
            return f"00:00:{ms // 1000:02d},{ms % 1000:03d}"
        blocks.append(f"{i}\n{tc(a)} --> {tc(b)}\n{text}\n")
    path.write_text("\n".join(blocks), encoding="utf-8")
    return path


def test_learn_counts_breaks_and_lengths(tmp_path):
    p = srt(tmp_path / "a.srt", [("So as", 0, 300), ("a joke", 300, 800), ("I", 800, 1200),
                                 ("suggested", 1200, 1800), ("to Marvel that", 1800, 2400), ("*laughs*", 2400, 3000)])
    m = S.learn([p])
    assert m["pairs"]["w:a|w:joke"] == [1, 0]                    # "a joke" kept together
    assert m["pairs"]["w:suggested|w:to"] == [1, 1]              # "suggested" | "to Marvel that"
    assert m["pairs"]["w:i|w:suggested"] == [1, 1]
    assert m["lengths"] == {"1": 0.4, "2": 0.4, "3": 0.2, "4": 0.0, "5": 0.0}     # the sound caption not counted
    assert m["captions"] == 5


def test_p_break_backs_off_to_kinds():
    m = {"pairs": {"w:a|w:joke": [9, 0], "k:DET|k:WORD": [50, 1], "k:DET|*": [60, 2], "*|k:WORD": [300, 100],
                   "*|*": [1000, 400]}, "lengths": {"1": 0.25, "2": 0.4, "3": 0.25, "4": 0.08, "5": 0.02}}
    assert S.p_break("a", "joke", m) < 0.05
    assert S.p_break("the", "dog", m) < 0.1                      # never seen: the determiner + word counts
    assert 0.3 < S.p_break("seen", "nothing", m) < 0.5           # nothing known: the overall share
    assert S.kind("Avengers") == "LONG" and S.kind("the") == "DET" and S.kind("25") == "NUM"


def test_group_follows_breaks_pauses_limits_and_sentences():
    m = {"pairs": {"*|*": [100, 30]}, "lengths": {"1": 0.24, "2": 0.41, "3": 0.27, "4": 0.07, "5": 0.01}}
    words = "one two three four five six".split()
    t = [0.0, 0.3, 0.6, 0.9, 1.2, 1.5]
    g = S.group(words, t, [x + 0.25 for x in t], m=m)
    assert all(1 <= j - i <= 3 for i, j in g)                    # short captions, as the user writes them
    m2 = dict(m, pairs={"*|*": [100, 30], "w:two|w:three": [20, 20], "w:four|w:five": [20, 20],
                        "w:one|w:two": [20, 0], "w:three|w:four": [20, 0], "w:five|w:six": [20, 0]})
    assert S.group(words, t, [x + 0.25 for x in t], m=m2) == [(0, 2), (2, 4), (4, 6)]   # the learned breaks
    g = S.group(words, t, [x + 0.25 for x in t], forced={3}, m=m)
    assert 3 in [i for i, _ in g]                                # a cut before "four"
    t2 = [0.0, 0.3, 0.6, 1.6, 1.9, 2.2]                          # a 0.75 s pause before "four"
    g = S.group(words, t2, [x + 0.25 for x in t2], m=m)
    assert 3 in [i for i, _ in g]
    g = S.group(["Hello.", "how", "are", "you"], [0, .3, .6, .9], [.25, .55, .85, 1.15], m=m)
    assert (0, 1) in g                                           # never across a sentence end
    long = ["incomprehensibilities", "everywhere"]
    assert S.group(long, [0, 0.5], [0.45, 0.9], m=m) == [(0, 1), (1, 2)]    # over 20 characters together


def W(t, a, b):
    return Word(t, a, b, 1.0, t)


def test_style_captions_start_on_the_word_or_the_cut_and_are_back_to_back():
    words = [W("so", 0.50, 0.70), W("as", 0.72, 0.85), W("a", 0.90, 0.95), W("joke", 0.97, 1.30),
             W("I", 1.60, 1.70), W("suggested", 1.75, 2.30)]
    caps = S.style_captions(words, FPS, 300, cuts=[int(1.5 * 60)], placeholders=False)
    starts = [c.start for c in caps]
    assert caps[0].start == 30                                   # the frame its first word begins in (0.50 s)
    assert int(1.5 * 60) in starts                               # "I" comes 0.1 s after the cut: on the cut
    assert all(a.end == b.start for a, b in zip(caps, caps[1:]))
    assert " ".join(c.text for c in caps) == "so as a joke I suggested"


def test_style_captions_placeholder_for_a_silence():
    words = [W("hi", 0.2, 0.5), W("there", 3.0, 3.4)]
    caps = S.style_captions(words, FPS, 300)
    assert [c.text for c in caps] == ["hi", "*...*", "there", "*...*"]


def test_competitor_style():
    caps = [{"ocr": t} for t in ["SO AS A", "JOKE", "I", "SUGGESTED", "TO", "MARVEL"]]
    st = S.competitor_style(caps)
    assert not st["follow"] and "capitals" in st["why"]
    mixed = [{"ocr": t} for t in ["Deadpool", "builds a team", "the X-Force", "The studio", "is like",
                                  "yes Avengers", "that's what"]]
    assert S.competitor_style(mixed)["follow"]
    singles = [{"ocr": t} for t in ["so", "as", "a", "joke", "I", "suggested two"]]
    assert not S.competitor_style(singles)["follow"]
    assert not S.competitor_style([{"ocr": "hi"}])["follow"]


def test_follow_competitor_keeps_its_breaks_and_timing_with_the_words_heard():
    from match_cuts.caption_score import Piece, Timeline
    # the competitor's edit: 0-25 s plays RAW 100-125 s; mine the same, in two pieces (a cut in one take)
    comp_tl = Timeline([Piece(0.0, 25.0, "raw", 100.0)])
    tool_tl = Timeline([Piece(0.0, 2.0, "raw", 100.0), Piece(2.0, 27.0, "raw", 100.0 + 2.0 - 0.0)])
    spans = [{"comp_in": 0, "comp_out": 9, "ocr": "Deadpool"},
             {"comp_in": 9, "comp_out": 31, "ocr": "builds a team"},
             {"comp_in": 67, "comp_out": 74, "ocr": "is like"},
             {"comp_in": 74, "comp_out": 113, "ocr": "yes Avengers"},
             {"comp_in": 122, "comp_out": 129, "ocr": "we're going"},
             {"comp_in": 129, "comp_out": 130, "ocr": "his is going"},          # a misread first frame ...
             {"comp_in": 130, "comp_out": 140, "ocr": "This is going"},         # ... of this caption
             {"comp_in": 290, "comp_out": 300, "ocr": "yeah"},
             {"comp_in": 673, "comp_out": 700, "ocr": "*Laughter*"}]
    words = [W("Deadpool", 0.05, 0.28), W("builds", 0.32, 0.55), W("a", 0.56, 0.6), W("team,", 0.62, 0.95),
             W("was", 2.25, 2.35), W("like,", 2.36, 2.45), W("yes,", 2.5, 2.7), W("like,", 2.75, 2.9),
             W("Avengers!", 3.0, 3.6), W("we're", 4.07, 4.15), W("gonna", 4.16, 4.29), W("this", 4.35, 4.5),
             W("is", 4.52, 4.6), W("gonna", 4.6, 4.66), W("yeah,", 9.7, 9.85), W("yeah", 9.86, 9.98)]
    caps, notes = S.follow_competitor(spans, words, comp_tl, tool_tl, 30, FPS)
    assert [(c.text, c.start) for c in caps] == [
        ("Deadpool", 0), ("builds a team", 18), ("was like", 134), ("yes Avengers!", 148),   # filler "like" left out
        ("we're gonna", 244), ("This is gonna", 258), ("yeah", 580), ("*Laughter*", 1346)]   # the screen's capital
    assert all(a.end == b.start for a, b in zip(caps, caps[1:]))               # back to back
    assert [n["heard"] for n in notes["changed"]] == ["was like", "we're gonna", "This is gonna"]


def _brad(first_frame: int):
    """The competitor plays the end of one take (RAW 100-101) and cuts to the next (RAW 105) at 1.0 s; its caption
    "Brad Pitt's" shows from ``first_frame`` (30 fps). My edit plays the first take on to 1.3 s before its cut."""
    from match_cuts.caption_score import Piece, Timeline
    comp_tl = Timeline([Piece(0.0, 1.0, "raw", 100.0), Piece(1.0, 3.0, "raw", 105.0)])
    tool_tl = Timeline([Piece(0.0, 1.3, "raw", 100.0), Piece(1.3, 3.3, "raw", 105.0)])
    spans = [{"comp_in": 0, "comp_out": first_frame, "ocr": "Back up"},
             {"comp_in": first_frame, "comp_out": 60, "ocr": "Brad Pitt's"}]
    words = [W("back", 0.1, 0.3), W("up.", 0.35, 0.6), W("Brad", 1.35, 1.55), W("Pitt's", 1.6, 1.9)]
    caps, _ = S.follow_competitor(spans, words, comp_tl, tool_tl, 30, FPS)
    return {c.text: c.start for c in caps}


def test_follow_a_caption_just_before_the_competitors_cut_starts_on_my_cut():
    # one frame before its cut: the caption goes with the take after it -- in my edit, on my cut (1.3 s), not where
    # my edit plays the frame before the competitor's cut (0.97 s, 20 frames early)
    assert _brad(29) == {"Back up": 0, "Brad Pitt's": 78}


def test_follow_a_caption_before_a_cut_keeps_its_lead_on_the_take_of_its_first_word():
    # 4 frames (0.133 s) before the cut, its first word after it: 0.133 s before my edit plays that take
    assert _brad(26) == {"Back up": 0, "Brad Pitt's": 78 - 8}


def test_follow_my_edit_without_the_captions_first_moment_keeps_the_competitors_lead():
    from match_cuts.caption_score import Piece, Timeline
    # the competitor's caption shows 0.4 s before its first word, in a pause my edit cut out (RAW 101.0-101.65)
    comp_tl = Timeline([Piece(0.0, 3.0, "raw", 100.0)])
    tool_tl = Timeline([Piece(0.0, 1.0, "raw", 100.0), Piece(1.0, 3.0, "raw", 101.65)])
    spans = [{"comp_in": 0, "comp_out": 39, "ocr": "Back up"}, {"comp_in": 39, "comp_out": 90, "ocr": "Brad Pitt's"}]
    words = [W("back", 0.1, 0.3), W("up.", 0.35, 0.5), W("Brad", 1.05, 1.25), W("Pitt's", 1.3, 1.6)]
    caps, _ = S.follow_competitor(spans, words, comp_tl, tool_tl, 30, FPS)
    assert {c.text: c.start for c in caps} == {"Back up": 0, "Brad Pitt's": 39}     # 1.05 - 0.4 s: frame 39
    words[1] = W("up.", 0.35, 0.8)                     # ... never before the word before it ends
    caps, _ = S.follow_competitor(spans, words, comp_tl, tool_tl, 30, FPS)
    assert {c.text: c.start for c in caps}["Brad Pitt's"] == 48


def test_hints_move_the_breaks():
    m = {"pairs": {"*|*": [100, 30]}, "lengths": {"1": 0.24, "2": 0.41, "3": 0.27, "4": 0.07, "5": 0.01}}
    words = "name surname and age".split()
    t = [0.0, 0.3, 0.6, 0.9]
    plain = S.group(words, t, [x + 0.25 for x in t], m=m)
    hinted = S.group(words, t, [x + 0.25 for x in t], m=m, hints={1: 3.0, 2: 3.0, 3: -3.0})
    assert hinted == [(0, 1), (1, 2), (2, 4)] != plain                 # "name" | "surname" | "and age"


def test_a_boundary_between_two_followed_captions_goes_where_their_text_says():
    # the competitor switched captions while "this." was said: by time it went with the second caption
    words = [W("get", 0.0, 0.2), W("out", 0.2, 0.4), W("of", 0.4, 0.5), W("this.", 0.5, 0.8), W("This", 0.9, 1.0),
             W("is", 1.0, 1.1), W("gonna", 1.1, 1.3), W("be", 1.3, 1.4)]
    owner = [0, 0, 0, 1, 1, 1, 1, 1]
    S._by_screen(words, owner, ["get out of this", "This is going to be"])     # "gonna" = "going to"
    assert owner == [0, 0, 0, 0, 1, 1, 1, 1]
    owner = [0, 0, 1, 1]
    S._by_screen(words[4:], owner, ["This is", "gonna be"])
    assert owner == [0, 0, 1, 1]
    owner = [0, 1, 1, 1]                                                        # a tie: the split by time
    S._by_screen(words[4:], owner, ["it was", "gonna be"])
    assert owner == [0, 1, 1, 1]


def test_regroup_leans_towards_the_competitors_caption_changes(monkeypatch):
    from match_cuts.caption_score import Piece, Timeline
    comp_tl = Timeline([Piece(0.0, 10.0, "raw", 100.0)])
    tool_tl = Timeline([Piece(0.0, 0.5, "raw", 100.0), Piece(0.5, 10.0, "raw", 100.5)])    # a cut, same take
    spans = [{"comp_in": 0, "comp_out": 15, "ocr": "I SHOULD"}, {"comp_in": 15, "comp_out": 30, "ocr": "GO"}]
    words = [W("I", 0.05, 0.2), W("should", 0.2, 0.45), W("go", 0.55, 0.8), W("now", 2.0, 2.2)]
    breaks = S.competitor_breaks(spans, words, comp_tl, tool_tl, 30)
    assert breaks == {id(words[2])}                      # "now": on no caption of the competitor's, no hint
    seen: dict = {}

    def fake_group(texts, starts, ends, forced=(), m=None, hints=None):
        seen.update(hints or {})
        return [(0, len(texts))]
    monkeypatch.setattr(S, "group", fake_group)
    S.style_captions(words, FPS, 600, comp_breaks=breaks)
    assert seen == {2: S.COMP_BREAK_BONUS}


def test_follow_a_screen_capital_no_longer_first_takes_the_heard_case():
    from match_cuts.caption_score import Piece, Timeline
    tl = Timeline([Piece(0.0, 10.0, "raw", 100.0)])
    spans = [{"comp_in": 0, "comp_out": 30, "ocr": "They're like"}]
    words = [W("And", 0.05, 0.15), W("they're", 0.2, 0.4), W("like,", 0.45, 0.6)]
    caps, _ = S.follow_competitor(spans, words, tl, tl, 30, FPS)
    assert [c.text for c in caps] == ["And they're like"]
