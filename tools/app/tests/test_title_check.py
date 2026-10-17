"""TITLE CHECK (mr_app.core.title_check): the word rules, the share of the same words, the warning at 80 %, the OCR
typo tolerance, the OCR text tidied, the channel header dropped, and the headline read from videos (small videos made
here, and real competitor videos when they are on this PC). Run:
python -m pytest tools/app/tests/test_title_check.py -q --basetemp=work/t25/pytest_title"""
from __future__ import annotations

import sys
import threading
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "match_cuts"))

from mr_app.core import title_check as T  # noqa: E402

REPO = Path(__file__).resolve().parents[3]
REAL = REPO / "tests" / "real"


# ---------------------------------------------------------------------------------------------------------------------
# the words
# ---------------------------------------------------------------------------------------------------------------------

def test_words_are_lower_case_without_punctuation_or_emojis():
    assert T.words("When Joe Became The Freakiest Guy In The Pool!") == \
        ["when", "joe", "became", "the", "freakiest", "guy", "in", "the", "pool"]
    assert T.words("The fact that almonds sent him to the hospital is hilarious 🥜😭") == \
        ["the", "fact", "that", "almonds", "sent", "him", "to", "the", "hospital", "is", "hilarious"]
    assert T.words('"This is making insane sense" 🤣') == ["this", "is", "making", "insane", "sense"]
    assert T.words("“Stop fingering me”") == ["stop", "fingering", "me"]
    assert T.words("") == [] and T.words("😭🥀 !!") == []


def test_apostrophes_are_one_kind_and_stay_inside_words():
    assert T.words("Don’t let them know") == T.words("Don't let them know") == ["don't", "let", "them", "know"]
    assert T.words("Kate Winslet‘s Day") == ["kate", "winslet's", "day"]
    assert T.words("'quoted' words") == ["quoted", "words"]                 # quotes around a word are not part of it


def test_hashtags_are_not_words_of_the_title():
    assert T.words("Bro was a slow processor 😩 #shorts #fyp") == ["bro", "was", "a", "slow", "processor"]
    assert T.words("The #1 reason") == ["the", "1", "reason"]                    # a number after '#' stays
    assert T.check("Bro was a slow processor #shorts #funny #fyp", "Bro was a slow processor").share == 1.0


def test_hyphens_split_ampersand_is_and_accents_and_numbers():
    assert T.words("A well-known VR-hospital") == ["a", "well", "known", "vr", "hospital"]
    assert T.words("Tom & Jerry") == ["tom", "and", "jerry"]
    assert T.words("ALLOCINÉ café") == ["allocine", "cafe"]
    assert T.words("ALMOST 30 YEARS AGO, A PROFIT OF £7.63") == ["almost", "30", "years", "ago", "a", "profit", "of",
                                                                  "7.63"]


# ---------------------------------------------------------------------------------------------------------------------
# the share of the same words
# ---------------------------------------------------------------------------------------------------------------------

def test_same_words_ignores_the_order_and_counts_repeats():
    assert T.same_words("the cat sat on the mat", "on the mat the cat sat") == 1.0
    assert T.same_words("the the the", "the") == pytest.approx(1 / 3)       # a word counts as often as both have it
    assert T.same_words("When Joe Became The Freakiest Guy In The Pool",
                        "Joe became the freakiest guy in the pool") == pytest.approx(8 / 9)


def test_same_words_divides_by_the_longer_title():
    assert T.same_words("a b c d", "a b") == 0.5
    assert T.same_words("a b", "a b c d") == 0.5


def test_same_words_is_zero_when_either_is_empty():
    assert T.same_words("", "anything at all") == 0.0
    assert T.same_words("anything at all", "") == 0.0
    assert T.same_words("🥜😭", "!!") == 0.0


def test_the_warning_starts_at_80_percent():
    at = T.check("one two three four five", "five four three two six")           # 4 of 5: exactly 80 %
    assert at.share == pytest.approx(0.8) and at.warn and at.percent == 80
    below = T.check("one two three four", "one two three five")                  # 3 of 4: 75 %
    assert below.share == pytest.approx(0.75) and not below.warn
    just_below = T.check(" ".join(f"w{i}" for i in range(49)),                   # 39 of 49 words: 79.6 %
                         " ".join(f"w{i}" for i in range(39)) + " " + " ".join(f"x{i}" for i in range(10)))
    assert just_below.share == pytest.approx(39 / 49)
    assert not just_below.warn and just_below.percent == 79                      # shown 79, never 80 without a warning
    eight_of_ten = T.check("a b c d e f g h i j", "j i h g f e d c x y")
    assert eight_of_ten.warn and eight_of_ten.percent == 80


def test_check_message_common_words_and_another_order():
    c = T.check("Joe became the freakiest guy in the pool", "When Joe Became The Freakiest Guy In The Pool")
    assert c.warn and c.percent == 88
    assert c.message == "88 % the same words as the competitor's headline -- change your title more"
    assert c.common == ["joe", "became", "the", "freakiest", "guy", "in", "the", "pool"]
    assert not c.other_order
    r = T.check("In the pool Joe became the freakiest guy", "When Joe Became The Freakiest Guy In The Pool")
    assert r.warn and r.other_order and "(in another order)" in r.message
    ok = T.check("The fact that almonds sent him to the hospital is hilarious",
                 "He Accidentally Ate Almonds And Rushed To A VR Hospital")
    assert not ok.warn and ok.percent == 27 and "different enough" in ok.message
    assert sorted(ok.common) == ["almonds", "hospital", "to"]


def test_check_without_a_headline_or_without_my_title():
    assert not T.check("my title", None).warn
    assert T.check("my title", None).message == "no competitor headline to compare with"
    assert T.check("my title", "").share == 0.0
    assert T.check("", "Their headline").message == "type your title to compare it with the competitor's headline"
    hl = T.HeadlineRead(text="Bro was a slow processor", lines=["Bro was a", "slow processor"], score=0.99,
                        frames=3, frames_read=3, box=(51, 134, 873, 325))
    assert T.check("bro was a SLOW processor 😩", hl).share == 1.0


# ---------------------------------------------------------------------------------------------------------------------
# OCR typos
# ---------------------------------------------------------------------------------------------------------------------

def test_words_of_five_letters_or_more_one_letter_apart_are_the_same():
    assert T.one_letter_apart("freakiest", "freakiost")                          # a letter misread
    assert T.one_letter_apart("hospital", "hospitals")                           # a letter added (a plural)
    assert T.one_letter_apart("accountant", "acountant")                         # a letter dropped
    assert not T.one_letter_apart("the", "tho")                                  # short words: too many real pairs
    assert not T.one_letter_apart("plan", "plans")                               # 4 letters
    assert not T.one_letter_apart("almonds", "almanacs")                         # more than one letter
    assert not T.one_letter_apart("10000", "10001")                              # numbers are never typos
    assert not T.one_letter_apart("same", "same")


def test_ocr_typos_count_as_the_same_word_in_the_share():
    mine = "When Joe Became The Freakiest Guy In The Pool"
    ocr = "When Joe Becane The Freakiost Guy In The Pool"                        # one letter misread in two words
    assert T.same_words(mine, ocr) == 1.0
    assert T.same_words(mine, ocr, typos=False) == pytest.approx(7 / 9)
    assert T.same_words("the cat", "tho cat") == 0.5                             # 'the' / 'tho': short, not a typo
    assert T.same_words("When Joe Became", "When Joe Becarne") == pytest.approx(2 / 3)   # 'rn' for 'm': two letters


def test_apostrophes_do_not_make_another_word():
    assert T.same_words("dont let them know", "Don't let them know", typos=False) == 1.0
    assert T.same_words("Kate Winslet's Day", "Kate Winslet Day") == 1.0          # 'winslet' + s: one letter
    assert T.same_words("Kate Winslet's Day", "Kate Winslet Day", typos=False) == pytest.approx(2 / 3)
    assert T.same_words("its over", "it's over") == 1.0


def test_each_word_is_matched_once_and_the_largest_matching_wins():
    assert T.matched_pairs(["sheets", "sheet"], ["sheep", "sheets"]) == [(0, 1), (1, 0)]
    assert len(T.matched_pairs(["a", "a", "b"], ["a", "b", "b"])) == 2           # each word used once
    # 'faint' / 'faint' alone would leave 'paint' unpaired; 'paint'-'faint' and 'faint'-'saint' make two pairs
    assert T.matched_pairs(["paint", "faint"], ["faint", "saint"]) == [(0, 0), (1, 1)]


# ---------------------------------------------------------------------------------------------------------------------
# the OCR text tidied
# ---------------------------------------------------------------------------------------------------------------------

def test_tidy_quotes_spaces_and_emoji_garbage():
    assert T.tidy("“Stop fingering me”") == '"Stop fingering me"'
    assert T.tidy("'Stop fingering me\"") == '"Stop fingering me"'               # opened and closed differently
    assert T.tidy("\"This is making insane sense'") == '"This is making insane sense"'
    assert T.tidy("I'm out 国") == "I'm out"                                     # an emoji read as a Chinese character
    assert T.tidy("is hilarious @") == "is hilarious"                            # ... or as a symbol
    assert T.tidy("@ When you finally") == "When you finally"
    assert T.tidy("Don’t   let them know ,  bro !") == "Don't let them know, bro!"
    assert T.tidy("comedy like this 😭🥀") == "comedy like this"
    assert T.tidy("What a game ?") == "What a game?"
    assert T.tidy("ALMOST 30 YEARS AGO,") == "ALMOST 30 YEARS AGO,"


def test_a_handle_needs_letters_after_the_at_sign():
    assert T.is_handle("@SILLYJOKERS") and T.is_handle("VS Viral Sanity @ViralSanity101")
    assert not T.is_handle("is hilarious @") and not T.is_handle("Bro was a slow processor")
    assert not T.is_handle("When @MrBeast gave away a car")                       # a headline that mentions someone


# ---------------------------------------------------------------------------------------------------------------------
# the lines of a band: the channel header dropped, the headline block kept
# ---------------------------------------------------------------------------------------------------------------------

def L(x0, y0, x1, y1, text, score=1.0):
    return T._Line(x0, y0, x1, y1, text, score)


def test_the_channel_header_goes_and_the_headline_stays():
    lines = [L(609, 138, 984, 214, "SillyJokers"), L(616, 210, 901, 250, "@SILLYJOKERS"),
             L(218, 383, 1222, 488, "When Joe Became The"), L(157, 477, 1282, 583, "Freakiest Guy In The Pool")]
    head, rest = T.pick_headline(lines)
    assert [l.text for l in head] == ["When Joe Became The", "Freakiest Guy In The Pool"]
    assert [l.text for l in rest] == ["SillyJokers", "@SILLYJOKERS"]


def test_text_in_the_round_picture_next_to_the_handle_goes_too():
    lines = [L(444, 242, 700, 305, "@CELEBS"), L(326, 298, 414, 332, "CELEBS"), L(446, 305, 569, 336, "@CELEBS"),
             L(162, 391, 918, 453, "The Time Tom Holland Went"), L(226, 463, 851, 528, "To An American School")]
    head, _ = T.pick_headline(lines)
    assert " ".join(l.text for l in head) == "The Time Tom Holland Went To An American School"


def test_a_tweet_style_name_and_handle_go():
    lines = [L(266, 314, 597, 357, "Laura Casselman"), L(266, 364, 655, 410, "@thelauracasselman"),
             L(99, 507, 674, 572, "She turned a bad question"), L(100, 574, 604, 628, "into a brilliant moment.")]
    head, _ = T.pick_headline(lines)
    assert [l.text for l in head] == ["She turned a bad question", "into a brilliant moment."]


def test_a_logo_block_without_handle_loses_to_the_bigger_headline_block():
    lines = [L(71, 149, 178, 206, "telly"), L(74, 181, 204, 238, "bants"),
             L(182, 289, 889, 355, "ALMOST 30 YEARS AGO,"), L(69, 357, 1023, 413, "YET SOMEHOW STILL ACCURATE")]
    head, rest = T.pick_headline(lines)
    assert [l.text for l in head] == ["ALMOST 30 YEARS AGO,", "YET SOMEHOW STILL ACCURATE"]
    assert [l.text for l in rest] == ["telly", "bants"]


def test_a_handle_under_the_headline_does_not_take_the_headline_with_it():
    lines = [L(80, 200, 640, 250, "The fact that almonds sent him"),
             L(110, 252, 610, 302, "to the hospital is hilarious"), L(260, 306, 460, 334, "@nutfacts")]
    head, rest = T.pick_headline(lines)
    assert [l.text for l in head] == ["The fact that almonds sent him", "to the hospital is hilarious"]
    assert [l.text for l in rest] == ["@nutfacts"]


def test_a_headline_mentioning_someone_is_kept():
    lines = [L(266, 314, 597, 357, "Movie Fan"), L(266, 364, 655, 410, "@moviefan"),
             L(60, 500, 660, 560, "When @MrBeast gave away a car"), L(150, 562, 570, 620, "to a stranger")]
    head, _ = T.pick_headline(lines)
    assert " ".join(l.text for l in head) == "When @MrBeast gave away a car to a stranger"


def test_emoji_lines_are_dropped_and_lines_close_together_are_one_block():
    lines = [L(250, 47, 828, 159, "Nobody let bro"), L(186, 148, 906, 240, "finish his sentence"),
             L(420, 250, 600, 330, "国 @")]
    head, _ = T.pick_headline(lines)
    assert [l.text for l in head] == ["Nobody let bro", "finish his sentence"]


def test_boxes_on_one_row_are_one_line_and_a_lone_quote_mark_is_dropped():
    parts = [L(266, 291, 560, 365, "fingering me’", 0.93), L(159, 292, 292, 362, "‘Stop", 0.92),
             L(546, 306, 566, 323, "99", 0.65)]
    (line,) = T.merge_rows(parts)
    assert line.text == "‘Stop fingering me’" and (line.x0, line.x1) == (159, 560)


# ---------------------------------------------------------------------------------------------------------------------
# reading videos made here
# ---------------------------------------------------------------------------------------------------------------------

W, H = 720, 1280


def _font(size: int, bold: bool = True):
    from PIL import ImageFont
    for name in (("arialbd.ttf", "DejaVuSans-Bold.ttf") if bold else ("arial.ttf", "DejaVuSans.ttf")):
        for d in (Path("C:/Windows/Fonts"), Path("/usr/share/fonts/truetype/dejavu")):
            if (d / name).is_file():
                return ImageFont.truetype(str(d / name), size)
    return ImageFont.load_default(size=size)


def _picture(shot: int, k: int):
    """A moving 'video': coloured blocks that change every shot and drift a little every frame."""
    import numpy as np
    r = np.random.default_rng(1000 + shot)
    img = np.empty((H, W, 3), np.uint8)
    img[:] = r.integers(30, 220, 3)
    for _ in range(9):
        x, y = int(r.integers(0, W - 120)), int(r.integers(0, H - 120))
        w, h = int(r.integers(60, 300)), int(r.integers(60, 300))
        img[y:y + h, x + 2 * k:x + 2 * k + w] = r.integers(0, 255, 3)
    return img


def _write(path: Path, frames) -> Path:
    import cv2
    vw = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"), 30, (W, H))
    for f in frames:
        vw.write(f)
    vw.release()
    return path


def _card_video(path: Path, headline: bool = True) -> Path:
    """A white card (round picture, channel name, @handle, a two-line headline) above a moving window."""
    import numpy as np
    from PIL import Image, ImageDraw
    card = Image.new("RGB", (W, 420), (255, 255, 255))
    d = ImageDraw.Draw(card)
    d.ellipse((30, 40, 110, 120), fill=(200, 60, 60))
    d.text((128, 46), "Movie Fan", font=_font(30), fill=(0, 0, 0))
    d.text((128, 84), "@moviefanclips", font=_font(22, False), fill=(110, 110, 110))
    for i, ln in enumerate(("The fact that almonds sent him", "to the hospital is hilarious") if headline else ()):
        f = _font(40)
        d.text(((W - d.textlength(ln, font=f)) / 2, 190 + 52 * i), ln, font=f, fill=(0, 0, 0))
    top = np.array(card)[:, :, ::-1]

    def frames():
        for k in range(90):
            img = np.full((H, W, 3), 255, np.uint8)
            img[:420] = top
            img[420:1120] = _picture(k // 15, k % 15)[:700]
            yield img
    return _write(path, frames())


def _over_video(path: Path) -> Path:
    """A headline in white letters with a black outline on top of a full-frame moving picture."""
    import numpy as np
    from PIL import Image, ImageDraw
    layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    for i, ln in enumerate(("Nobody saw this ending", "coming at all")):
        f = _font(48)
        d.text(((W - d.textlength(ln, font=f)) / 2, 150 + 62 * i), ln, font=f, fill=(255, 255, 255, 255),
               stroke_width=4, stroke_fill=(0, 0, 0, 255))
    a = np.array(layer)
    txt, alpha = a[:, :, 2::-1].astype(np.float32), a[:, :, 3:4].astype(np.float32) / 255.0

    def frames():
        for k in range(90):
            yield (_picture(k // 15, k % 15) * (1 - alpha) + txt * alpha).astype(np.uint8)
    return _write(path, frames())


def _plain_video(path: Path) -> Path:
    """A full-frame moving picture, no text."""
    return _write(path, (_picture(k // 15, k % 15) for k in range(90)))


@pytest.fixture(scope="module")
def made(tmp_path_factory):
    pytest.importorskip("cv2")
    pytest.importorskip("rapidocr")
    d = tmp_path_factory.mktemp("title_videos")
    return {"card": _card_video(d / "card.mp4"), "over": _over_video(d / "over.mp4"),
            "plain": _plain_video(d / "plain.mp4"), "header": _card_video(d / "header.mp4", headline=False)}


def test_the_headline_above_the_picture_is_read_and_the_channel_header_dropped(made):
    T.warm_up()                                                                  # the OCR loaded (once per process)
    calls = []
    hl = T.read_headline(made["card"], progress=lambda done, total: calls.append((done, total)))
    assert hl is not None, T.last_reason()
    assert T.words(hl.text) == T.words("The fact that almonds sent him to the hospital is hilarious")
    assert hl.lines == ["The fact that almonds sent him", "to the hospital is hilarious"]
    assert hl.where == "above the picture" and hl.sure and hl.score > 0.9
    assert hl.frames >= 3 and hl.frames <= hl.frames_read <= T.OCR_FRAMES
    x, y, w, h = hl.box
    assert 150 <= y <= 200 and 280 <= y + h <= 320 and w > 400                    # around the two lines (px)
    assert any("@moviefan" in o for o in hl.others)                              # the channel's handle, dropped
    assert calls and calls[-1][0] == calls[-1][1]                                # the bar ends full ...
    assert all(a[0] <= b[0] for a, b in zip(calls, calls[1:]))                   # ... and never goes back
    assert all(done < total for done, total in calls[:-1])


def test_a_headline_written_on_the_picture_is_read_but_not_sure(made):
    hl = T.read_headline(made["over"])
    assert hl is not None, T.last_reason()
    assert T.words(hl.text) == ["nobody", "saw", "this", "ending", "coming", "at", "all"]
    assert hl.where == "on the picture" and not hl.sure


def test_no_headline_gives_none_and_says_why(made, tmp_path):
    assert T.read_headline(made["plain"]) is None
    assert "no headline" in T.last_reason() or "no text" in T.last_reason()
    assert T.read_headline(made["header"]) is None                               # the channel's name and handle only
    assert "channel's name" in T.last_reason() and "@moviefan" in T.last_reason()
    assert T.read_headline(tmp_path / "missing.mp4") is None and "no video" in T.last_reason()
    bad = tmp_path / "not_a_video.mp4"
    bad.write_bytes(b"not a video at all")
    assert T.read_headline(bad) is None and "could not be read" in T.last_reason()


def test_two_threads_read_at_the_same_time(made):
    want = {k: T.read_headline(made[k]).text for k in ("card", "over")}
    got: dict[str, str] = {}
    reasons: dict[str, str] = {}

    def run(k: str) -> None:
        hl = T.read_headline(made[k])
        got[k] = hl.text if hl else ""
        reasons[k] = T.last_reason()

    ths = [threading.Thread(target=run, args=(k,)) for k in ("card", "over", "card", "over")]
    for t in ths:
        t.start()
    for t in ths:
        t.join()
    assert got == want and reasons == {"card": "", "over": ""}


# ---------------------------------------------------------------------------------------------------------------------
# real competitor videos (skipped when they are not on this PC)
# ---------------------------------------------------------------------------------------------------------------------

def _real(case: str) -> Path:
    p = REAL / case / "competitor.mp4"
    if not p.is_file():
        pytest.skip(f"{p} is not here")
    pytest.importorskip("rapidocr")
    return p


@pytest.mark.parametrize("case, headline, header", [
    ("deadpool", "When Brad Pitt Did The Funniest Cameo", "@crispytheory"),
    ("video018-fixed", "He Accidentally Ate Almonds And Rushed To A VR Hospital", "@SILLYJOKERS"),
])
def test_real_competitor_headlines(case, headline, header):
    hl = T.read_headline(_real(case))
    assert hl is not None, T.last_reason()
    assert T.words(hl.text) == T.words(headline)
    assert hl.where == "above the picture" and hl.sure and header in hl.others
    assert hl.seconds < 30                                         # a few seconds (generous: the PC may be busy)


def test_real_competitor_without_a_headline():
    assert T.read_headline(_real("video4")) is None                # the picture fills the frame, captions only
    assert "fills the frame" in T.last_reason()


def test_my_almonds_title_against_the_real_almonds_headline():
    hl = T.read_headline(_real("video018-fixed"))
    c = T.check("The fact that almonds sent him to the hospital is hilarious 🥜😭", hl)
    assert not c.warn and c.percent == 27                          # the same topic is fine: only the words count
    assert T.check("He ate almonds by accident and rushed to a VR hospital", hl).warn
