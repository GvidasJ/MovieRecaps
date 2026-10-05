"""captions.py: the caption-generator-prompt.md rules, tested against the ten reference SRTs in srt/.

The reference files are both the style to reproduce and regression fixtures: their words, regrouped by the tool
(each original caption boundary given a 0.3 s pause, each *action* caption a silence), must come out close to the
originals -- except where the rules deliberately differ: the 29 captions ending on a weak word are fixed, and
interjections and repeated words get their own captions.
"""
from __future__ import annotations

import re
from fractions import Fraction
from pathlib import Path

import numpy as np
import pytest

from match_cuts import captions as C

SRT_DIR = Path(__file__).resolve().parents[3] / "srt"
NAMES = ["beanie-hats", "central-park-dog", "giolitti", "instagram-casting", "keanu-marvel", "lyrics-quiz",
         "own-stunts", "spiderman-driving-test", "spiderman-interview", "thor-hammer"]
LEFTOVER_END = {"beanie-hats", "central-park-dog", "keanu-marvel"}   # last caption runs ~100 s to the timeline end
FPS = Fraction(60)

pytestmark = pytest.mark.skipif(not SRT_DIR.is_dir(), reason="srt/ reference files not found")


def load(name: str) -> list[dict]:
    return C.read_srt(SRT_DIR / f"{name}.srt")


def words_of(caps: list[dict], word_s: float = 0.2, pause: float = 0.3, silence: float = 1.2) -> list[C.Word]:
    """The reference captions as a word list: 0.2 s per word, a 0.3 s pause after each caption, an *action*
    caption as a 1.2 s silence."""
    ws, t = [], 0.0
    for c in caps:
        if C.is_action_text(c["text"]):
            t += silence
            continue
        for tok in c["text"].split():
            ws.append(C.Word(C.clean_text(tok), t, t + word_s, 1.0, tok))
            t += word_s
        t += pause
    return ws


def spans_of(caps: list[dict]) -> list[tuple[int, int, str]]:
    out, k = [], 0
    for c in caps:
        if C.is_action_text(c["text"]):
            continue
        n = len(c["text"].split())
        out.append((k, k + n, c["text"]))
        k += n
    return out


# ---------------------------------------------------------------------------------------------
# The reference files themselves
# ---------------------------------------------------------------------------------------------

def test_reference_srts_match_the_measured_style():
    items, n_caps = [], 0
    for name in NAMES:
        raw = (SRT_DIR / f"{name}.srt").read_bytes()
        assert b"\r" not in raw and raw.endswith(b"\n") and not raw.endswith(b"\n\n")
        caps = load(name)
        n_caps += len(caps)
        for i, c in enumerate(caps):
            assert [c["index"] for c in caps] == list(range(1, len(caps) + 1))
            for ms in (c["start_ms"], c["end_ms"]):
                assert abs(ms * 60 / 1000 - round(ms * 60 / 1000)) < 0.04          # on the 1/60 s grid
            if i + 1 < len(caps):
                assert c["end_ms"] == caps[i + 1]["start_ms"]                       # back to back
            dur = (c["end_ms"] - c["start_ms"]) / 1000
            if i + 1 == len(caps) and name in LEFTOVER_END:
                assert dur > 90                                                     # the export leftover
                continue
            items.append((c["text"], dur))
    assert n_caps == 388
    st = C.style_stats(items, last_duration=True)
    assert st["captions"] == 385 and st["actions"] == 37
    assert st["chars_median"] == 11 and st["chars_p90"] == 17 and st["chars_max"] == 24
    assert abs(st["duration_median_s"] - 0.57) < 0.02 and abs(st["cps_median"] - 18) < 1
    assert st["words_pct"]["2"] > st["words_pct"]["3"] > st["words_pct"]["1"] > st["words_pct"]["4"]
    assert st["words"]["5+"] == 3
    assert st["stops_commas"] == 0
    assert st["weak_endings"] == 29                      # the ones to fix


@pytest.mark.parametrize("name", NAMES)
def test_srt_writer_reproduces_each_reference_file_byte_for_byte(name):
    raw = (SRT_DIR / f"{name}.srt").read_bytes().decode("utf-8")
    caps = [C.Caption(c["text"], round(c["start_ms"] * 60 / 1000), round(c["end_ms"] * 60 / 1000))
            for c in C.parse_srt(raw)]
    assert C.srt_text(caps, FPS) == raw
    assert C.parse_srt(raw.replace("\n", "\r\n"))[0]["text"] == caps[0].text      # CRLF tolerant


# ---------------------------------------------------------------------------------------------
# Regrouping the reference words
# ---------------------------------------------------------------------------------------------

KEPT_WEAK = {  # original weak endings the rules keep, with the reason
    ("instagram-casting", "I didn't get it"): "kept together",           # keep-together: "didn't get it"
    ("spiderman-driving-test", "for that"): "silence",                  # *sudden stop* follows
    ("spiderman-driving-test", "Carry on"): "silence",                  # *cruising along* follows
    ("spiderman-interview", "he is"): "interjection",                   # "oh" follows, alone
}


def regroup(name: str):
    caps = load(name)
    ws = words_of(caps)
    notes: list[dict] = []
    groups = C.group_words(ws, notes)
    return caps, ws, groups, notes


def test_regrouping_the_reference_words_comes_out_close_to_the_originals():
    total = same = 0
    for name in NAMES:
        caps, ws, groups, _ = regroup(name)
        got = {(g[0], g[-1] + 1) for g in groups}
        spans = spans_of(caps)
        total += len(spans)
        same += sum(1 for a, b, _ in spans if (a, b) in got)
    assert total == 351
    assert same / total >= 0.8, f"only {same}/{total} reference captions reproduced exactly"


@pytest.mark.parametrize("name", NAMES)
def test_regrouped_captions_follow_every_rule(name):
    caps, ws, groups, notes = regroup(name)
    assert [i for g in groups for i in g] == list(range(len(ws)))           # every word once, in order
    bonds = C.compute_bonds(ws)
    alone = C.standalone_interjections(ws)
    kept = {n["word"] for n in notes}
    for gi, g in enumerate(groups):
        text = " ".join(ws[i].text for i in g)
        assert len(g) <= 5
        if len(g) == 5:                                   # only after leading weak words ("for a 15 year old")
            assert C.is_weak(ws[g[0]].text) and C.is_weak(ws[g[1]].text)
        assert len(text) <= C.MAX_CHARS or len(g) == 1 or all(bonds[i] for i in g[:-1]), text
        assert "." not in re.sub(r"(?<=\d)[.,](?=\d)", "", text) and "," not in re.sub(r"(?<=\d),(?=\d)", "", text)
        if len(g) > 1 and C.is_weak(ws[g[-1]].text):
            assert g[-1] in kept, text                     # every weak ending left is listed with its reason
        for a, b in zip(g, g[1:]):
            assert not alone[a] and not alone[b], f"interjection not alone in {text!r}"
            assert C.norm(ws[a].text) != C.norm(ws[b].text), f"repetition in one caption: {text!r}"
        if gi + 1 < len(groups) and bonds[g[-1]]:          # split only where the caps leave no choice
            lo, hi = g[-1], g[-1] + 1
            while lo > 0 and bonds[lo - 1]:
                lo -= 1
            while hi < len(ws) - 1 and bonds[hi]:
                hi += 1
            while lo > g[0] and C.is_weak(ws[lo - 1].text):
                lo -= 1                                    # "And my" + "favorite thing": weak words go with it
            assert not C._fits(ws, range(lo, hi + 1)), f"bond split after {text!r}"


def test_the_29_weak_endings_are_fixed_except_where_a_rule_keeps_them():
    seen = {}
    for name in NAMES:
        caps, ws, groups, notes = regroup(name)
        ends = {g[-1] + 1 for g in groups}
        for a, b, text in spans_of(caps):
            if b - a > 1 and C.is_weak(text.split()[-1]):
                seen[(name, text)] = b in ends            # True = still ends there
    assert len(seen) == 29
    still = {k for k, v in seen.items() if v}
    assert still == set(KEPT_WEAK), f"unexpected weak endings kept: {still ^ set(KEPT_WEAK)}"


# ---------------------------------------------------------------------------------------------
# Rules, one by one
# ---------------------------------------------------------------------------------------------

def W(text: str, gaps: dict[int, float] | None = None, word_s: float = 0.2) -> list[C.Word]:
    """Words of a sentence (raw tokens keep their punctuation), with extra pauses before given word indices."""
    out, t = [], 0.0
    for i, tok in enumerate(text.split()):
        t += (gaps or {}).get(i, 0.0)
        out.append(C.Word(C.clean_text(tok), t, t + word_s, 1.0, tok))
        t += word_s
    return out


def texts(words: list[C.Word]) -> list[str]:
    return [" ".join(words[i].text for i in g) for g in C.group_words(words)]


@pytest.mark.parametrize("raw,clean", [
    ("£4.50.", "£4.50"), ("15,000,", "15,000"), ("Mr.", "Mr"), ("C.I.D.", "CID"), ("6 a.m.", "6 am"),
    ("  hello,  world.  ", "hello world"), ("Wait...", "Wait"), ("really?", "really?"), ("*shocked*", "*shocked*"),
    ("don't!", "don't!"), ("Spider-Man", "Spider-Man"),
])
def test_cleanup_strips_stops_and_commas_but_not_inside_numbers(raw, clean):
    assert C.clean_text(raw) == clean


def test_a_new_caption_after_four_words_or_twenty_characters():
    assert texts(W("we went to see the big game")) == ["we went to see", "the big game"]
    assert all(len(t) <= 20 for t in texts(W("unbelievably extraordinary circumstances happened")))


def test_a_pause_over_a_quarter_second_starts_a_new_caption():
    assert texts(W("I found out on Instagram", {3: 0.3})) == ["I found out", "on Instagram"]
    assert texts(W("we went there then we left", {3: 0.3})) == ["we went there", "then we left"]
    assert texts(W("we went there then we left", {3: 0.2})) == ["we went there then", "we left"]


def test_weak_word_moves_to_the_next_caption():
    assert texts(W("I bought a beanie hat", {3: 0.4})) == ["I bought", "a beanie hat"]
    # the prompt's example: "there is a" | "legendary sound mixer" -> "there is" | "a legendary ..." (one word moved)
    got = texts(W("there is a legendary sound mixer", {3: 0.4}))
    assert got[0] == "there is" and got[1].startswith("a legendary")
    assert texts(W("and")) == ["and"]                     # moving it would empty the caption


def test_keep_together_names_numbers_and_negations():
    for sentence, unit in [("my friend is Peter Parker from Queens", "Peter Parker"),
                           ("we walked across Central Park today", "Central Park"),
                           ("between 6 am and 9 am", "6 am"), ("for a 10 year old kid", "10 year old"),
                           ("it cost us 50 quid mate", "50 quid"), ("you just don't move okay", "don't move"),
                           ("and then I didn't get it because", "didn't get it")]:
        got = texts(W(sentence))
        assert any(unit in t for t in got), (sentence, got)


def test_interjections_stand_alone_and_repetition_stays_separate():
    assert texts(W("Oh, my god that is huge"))[0] == "Oh"
    assert texts(W("I have no idea"))[0] == "I have no idea"            # "no" as a word, not an interjection
    assert texts(W("whoop whoop whoop")) == ["whoop", "whoop", "whoop"]
    assert texts(W("Yeah, it's actually my friend")) == ["Yeah", "it's actually", "my friend"]


def test_voice_captions_are_back_to_back_with_placeholders_for_silences():
    ws = W("hello there my friend how are you", {0: 1.5, 4: 1.2})       # 1.5 s before, 1.2 s in the middle
    caps = C.voice_captions(ws, FPS, n_frames=60 * 6)
    assert caps[0].text == C.PLACEHOLDER and caps[0].start == 0 and caps[0].end == C.to_frame(ws[0].start, FPS)
    assert [c.text for c in caps if c.mode == "placeholder"] == [C.PLACEHOLDER] * 3     # leading, middle, trailing
    for a, b in zip(caps, caps[1:]):
        assert a.end == b.start                                          # always a caption on screen
    mid = next(i for i, c in enumerate(caps) if i and c.mode == "placeholder")
    assert caps[mid].start == C.to_frame(ws[3].end, FPS) and caps[mid].end == C.to_frame(ws[4].start, FPS)
    assert caps[-1].end == 60 * 6                                        # trailing silence to the timeline end
    ws2 = W("hello there my friend")
    caps2 = C.voice_captions(ws2, FPS, n_frames=60 * 60)
    assert caps2[-1].mode == "placeholder"
    caps3 = C.voice_captions(ws2, FPS, n_frames=C.to_frame(ws2[-1].end, FPS))
    assert caps3[-1].end == C.to_frame(ws2[-1].end, FPS)                 # last caption: its own last word's end
    srt = C.srt_text(caps, FPS)
    assert srt.startswith("1\n00:00:00,000 --> ") and srt.endswith("\n") and not srt.endswith("\n\n")


def test_competitor_captions_are_copied_untouched_and_uncaptioned_speech_is_not_filled():
    # none of the voice rules: past the 24-character cap, more than five words, a weak last word, the capitals as
    # written; the words heard after the last caption ("back to the test") get no caption of their own
    texts = ["And I got a Parker Peter and the", "*brakes*", "Spider-Man IS"]
    spans = [{"comp_in": 30 * i, "comp_out": 30 * (i + 1), "ocr": t, "score": 0.99} for i, t in enumerate(texts)]
    ws = W("I got a Peter Parker", word_s=0.2) + \
        [C.Word(w, 5.0 + 0.2 * i, 5.2 + 0.2 * i, 1.0, w) for i, w in enumerate(["back", "to", "the", "test"])]
    caps, notes = C.competitor_copy(spans, ws, Fraction(30), lambda k: 2 * k, FPS)
    assert [(c.text, c.start, c.end, c.mode) for c in caps] == [(t, 60 * i, 60 * (i + 1), "competitor")
                                                                 for i, t in enumerate(texts)]
    assert notes == {"from_transcript": [], "unreadable": []}


def test_transcript_flags_low_confidence_doubled_and_missing_words():
    ws = [C.Word("the", 0.0, 0.2, 0.99, "the"), C.Word("the", 0.2, 0.4, 0.98, "the"),
          C.Word("quazoosl", 0.4, 0.8, 0.3, "quazoosl"), C.Word("end", 2.0, 2.2, 0.99, "end")]
    sr = 16000
    y = np.zeros(int(3 * sr), np.float32)
    t = np.arange(len(y)) / sr
    voice = 0.3 * np.sin(2 * np.pi * 220 * t).astype(np.float32)
    for a, b in [(0.0, 0.8), (1.1, 1.7), (2.0, 2.2)]:                    # 1.1-1.7 s: speech with no word
        y[int(a * sr):int(b * sr)] = voice[int(a * sr):int(b * sr)]
    kinds = [f["kind"] for f in C.transcript_flags(ws, y, sr)]
    assert kinds == ["doubled word", "possible mis-transcription", "possible missing word"]


def test_only_the_words_taken_from_elsewhere_are_timed_again(monkeypatch):
    import dataclasses
    from match_cuts import align as A
    mine = [C.Word("a", 0.0, 0.2, 0.9, "a"), C.Word("c", 0.4, 0.6, 0.9, "c")]
    rough = C.Word("b", 0.25, 0.3, 0.9, "b")                 # from the second model, spread over what it replaced
    late = C.Word("d", 0.6, 0.7, 0.9, "d")
    monkeypatch.setattr(A, "available", lambda: None)
    monkeypatch.setattr(A, "align", lambda y, ws, **k: ([dataclasses.replace(w, start=w.start + 0.05,
                                                                              end=w.end + 0.2) for w in ws], {}))
    monkeypatch.setattr(A, "refine_onsets", lambda y, ws, **k: ws)
    import dataclasses as dc
    rechecked = dc.replace(mine[1], prob=0.99)                 # the recheck confirmed it: still the main model's word
    out = C._time_new_words(np.zeros(16000, np.float32), [mine[0], rough, rechecked, late],
                            {C._said_as(w) for w in mine})
    assert out[0] is mine[0] and out[2] is rechecked                         # aligned once already: kept
    assert (out[1].start, out[1].end) == pytest.approx((0.30, 0.40))        # timed, up to the next kept word
    assert (out[3].start, out[3].end) == pytest.approx((0.65, 0.90))


def test_words_that_cannot_be_timed_again_keep_their_rough_times(monkeypatch):
    """Task 10: when the forced alignment of the replaced words fails, they keep their rough times and the run says
    so. It called a ``warn`` that only run_captions has: the NameError lost the whole captions.srt."""
    from match_cuts import align as A
    mine = [C.Word("a", 0.0, 0.2, 0.9, "a")]
    words = [mine[0], C.Word("b", 0.25, 0.3, 0.9, "b")]
    monkeypatch.setattr(A, "available", lambda: None)

    def broken(y, ws, **k):
        raise RuntimeError("CUDA out of memory")
    monkeypatch.setattr(A, "align", broken)
    said: list[str] = []
    out = C._time_new_words(np.zeros(16000, np.float32), words, {C._said_as(w) for w in mine}, said.append)
    assert out == words
    assert said == ["the replaced words could not be timed (RuntimeError: CUDA out of memory)"]
    assert C._time_new_words(np.zeros(16000, np.float32), words, {C._said_as(w) for w in mine}) == words  # logged


def test_no_caption_runs_into_the_next():
    caps = [C.Caption("to death", 0, 60, "competitor"), C.Caption("*...*", 60, 160, "placeholder"),
            C.Caption("us", 157, 160, "transcript"), C.Caption("for", 160, 161, "transcript")]
    out = C.no_overlaps(caps)
    assert [(c.start, c.end, c.text) for c in out] == [(0, 60, "to death"), (60, 157, "*...*"), (157, 160, "us"),
                                                       (160, 161, "for")]
    assert [c.text for c in C.no_overlaps([C.Caption("a b", 10, 20), C.Caption("c", 10, 30)])] == ["c"]
