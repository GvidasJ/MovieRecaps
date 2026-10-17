"""The channel headline (mr_app.core.headline): line breaks, emoji clusters, shrinking, drawing -- and the match with
Photoshop's own rendering of the example headline in each channel PSD. Run: python -m pytest tools/app/tests"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mr_app.core import headline as H   # noqa: E402

REPO = Path(__file__).resolve().parents[3]
CHANNELS = ("LouLaughs", "MikeMemesYou", "Flick707")
PSDS = {**{ch: REPO / "assets" / "channels" / ch / "template.psd" for ch in CHANNELS},
        "Example": REPO / "assets" / "reference" / "Example.psd"}       # a 4th headline, not used to fit the style

# your real headlines, as you broke them
REAL = [
    ("The fact that almonds sent him", "to the hospital is hilarious 🥜😭"),
    ("The fact that they're not even", "real cops is hilarious 👮‍♂️😭"),
    ("When dad tries to be", "the tour guide 💀😭"),
    ("The fact that prison changed", "him this much is hilarious 🔒😭"),
    ("The fact that he didn't even", "think twice is hilarious 🏃😭"),
    ("The fact that she called that", "a bad word is hilarious 🙊😭"),
]


def _dark(im: Image.Image) -> np.ndarray:
    a = np.asarray(im.convert("RGBA")).astype(np.float32) / 255.0
    rgb = a[..., :3] * a[..., 3:4] + (1.0 - a[..., 3:4])
    return 1.0 - (0.299 * rgb[..., 0] + 0.587 * rgb[..., 1] + 0.114 * rgb[..., 2])


# ---------------------------------------------------------------------------------------------------------------------
# line breaks
# ---------------------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("line1,line2", REAL)
def test_your_real_headlines_break_where_you_broke_them(line1, line2):
    lay = H.layout(f"{line1} {line2}")
    assert lay.lines == [line1, line2]
    assert not lay.shrunk and lay.scale == 1.0 and lay.why == "" and lay.warnings == [] and not lay.own_break
    assert all(w <= H.MAX_WIDTH for w in lay.widths)
    assert lay.baselines == [849.0, 996.0]                    # the PSD's: 849.2 and 849.2 + 146.4, on whole pixels
    assert lay.summary() == "2 lines, full size (122 pt)"


def test_your_own_line_break_wins():
    lay = H.layout("When dad\ntries to be the tour guide 💀😭")
    assert lay.lines == ["When dad", "tries to be the tour guide 💀😭"] and lay.own_break and not lay.shrunk
    assert H.layout("When dad  tries\r\nto be").lines == ["When dad tries", "to be"]
    assert H.layout("When dad\rtries").lines == ["When dad", "tries"]
    lay = H.layout("one\ntwo\nthree")
    assert lay.lines == ["one", "two three"]
    assert lay.warnings == ["the headline has 2 lines: line 3 was added to line 2"]


def test_your_line_break_leaving_a_line_too_wide_shrinks_and_says_why():
    lay = H.layout("The fact that almonds sent him to the\nhospital 🥜😭")
    assert lay.own_break and lay.shrunk and lay.scale == pytest.approx(H.MAX_WIDTH / lay.widths[0])
    assert lay.why == f"shrunk to {100 * lay.scale:.0f} %: your line break leaves a line wider than 920 px"


def test_no_break_fits_the_headline_shrinks_and_says_so():
    title = "The fact that the dog understood the assignment better than him 😭"
    lay = H.layout(title)
    toks = H.tokens(title)
    best = min(max(H.line_width(" ".join(toks[:i])), H.line_width(" ".join(toks[i:]))) for i in range(1, len(toks)))
    assert lay.shrunk and max(lay.widths) == pytest.approx(best)            # the break with the shortest longer line
    assert lay.scale == pytest.approx(H.MAX_WIDTH / best) and 0.85 < lay.scale < 1.0
    assert lay.why == f"shrunk to {100 * lay.scale:.0f} %: no break keeps both lines inside 920 px"
    assert lay.summary() == f"2 lines, {lay.why}"
    assert max(lay.widths_1080) == pytest.approx(920.0, abs=0.1)
    # the smaller block sits centred where the PSD's 2 lines sit (top of the capitals of line 1 .. baseline of line 2)
    middle = (lay.baselines[0] - H.CAP_HEIGHT * H.SIZE * lay.scale + lay.baselines[1]) / 2.0
    assert middle == pytest.approx(H._block_middle())
    assert lay.baselines[1] - lay.baselines[0] == pytest.approx(H.LINE_PITCH * lay.scale)
    # drawn smaller: its widest line is 1840 px of ink (920 on the sequence)
    img, _ = H.render(title, lay=lay)
    a = np.asarray(img)[..., 3]
    widths = []
    for b in lay.baselines:
        rows = a[int(b) - 60:int(b) + 10]
        xs = np.nonzero((rows > 127).any(axis=0))[0]
        widths.append(xs.max() + 1 - xs.min())
    assert max(widths) == pytest.approx(H.MAX_WIDTH, abs=4)


def test_one_word_is_one_line_in_the_middle_of_the_block():
    lay = H.layout("Hilarious")
    assert lay.lines == ["Hilarious"] and not lay.shrunk and lay.summary() == "1 line, full size (122 pt)"
    assert lay.baselines[0] - H.CAP_HEIGHT * H.SIZE / 2.0 == pytest.approx(H._block_middle())
    assert H.layout("  😭😭  ").lines == ["😭😭"]
    assert H.layout("Hilarious 😭").lines == ["Hilarious 😭"]                    # the emoji sticks to its word
    assert H.layout("").lines == [] and H.layout("  \n ").lines == [] and H.layout("").summary() == "no headline"


def test_one_very_long_word_is_shrunk_on_its_line():
    lay = H.layout("Supercalifragilisticexpialidocious-extraordinaire")
    assert len(lay.lines) == 1 and lay.shrunk
    assert lay.why == f"shrunk to {100 * lay.scale:.0f} %: the title is wider than 920 px on one line"


def test_small_words_do_not_end_line_one_and_punctuation_is_a_good_place():
    assert H._break_cost("I saw the", 1000.0, 900.0) == 1000.0 + H.WEAK_END
    assert H._break_cost("He ran to 😭", 500.0, 1000.0) == 1000.0 + H.WEAK_END    # the emoji hides no small word
    assert H._break_cost("Wait,", 500.0, 1000.0) == 1000.0 - H.PUNCT_END
    assert H._break_cost('He said "no!"', 500.0, 1000.0) == 1000.0 - H.PUNCT_END
    assert H._break_cost("is hilarious 😭", 500.0, 1000.0) == 1000.0
    # "...called that a / bad word" would be balanced too, but it ends on 'a'
    assert H.layout("The fact that she called that a bad word is hilarious 🙊😭").lines[0].endswith("called that")


def test_a_break_is_never_between_a_word_and_its_emojis():
    assert H.tokens("is hilarious 🥜😭") == ["is", "hilarious 🥜😭"]
    assert H.tokens("😭 When dad") == ["😭 When", "dad"]
    assert H.tokens("Wow !! nice — really") == ["Wow !!", "nice —", "really"]
    assert H.tokens("hilarious😭 ok") == ["hilarious😭", "ok"]
    for line1, line2 in REAL:
        last = H.tokens(f"{line1} {line2}")[-1]
        assert last == " ".join(line2.split()[-2:])                         # 'hilarious 🥜😭', 'guide 💀😭'


# ---------------------------------------------------------------------------------------------------------------------
# emojis
# ---------------------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("text,expected", [
    ("👮‍♂️😭", ["👮‍♂️", "😭"]),                                 # a ZWJ sequence
    ("👍🏽👍", ["👍🏽", "👍"]),                                                  # a skin tone
    ("🇺🇸🇬🇧", ["🇺🇸", "🇬🇧"]),                                                # flags: pairs of flag letters
    ("1️⃣#️⃣", ["1️⃣", "#️⃣"]),                                                  # keycaps
    ("🏴\U000e0067\U000e0062\U000e0073\U000e0063\U000e0074\U000e007f!",
     ["🏴\U000e0067\U000e0062\U000e0073\U000e0063\U000e0074\U000e007f", "!"]),  # a flag of tags (Scotland)
    ("👨‍👩‍👧‍👦", ["👨‍👩‍👧‍👦"]),
    ("ét", ["é", "t"]),                                              # an accent
    ("a‍b", ["a‍", "b"]),                                               # a ZWJ joins emojis only
    ("Hi 😭!", ["H", "i", " ", "😭", "!"]),
])
def test_emoji_clusters(text, expected):
    assert H.clusters(text) == expected


def test_the_clusters_are_unicodes():
    regex = pytest.importorskip("regex")
    for t in ["👮‍♂️😭", "👍🏽 ok", "🇺🇸🇬🇧🇫", "1️⃣#️⃣*", "❤️‍🔥 🧑🏽‍🚀", "🏳️‍🌈", "x‍😭",
              "The fact that they're not even real cops is hilarious 👮‍♂️😭", "Café ño"]:
        assert H.clusters(t) == regex.findall(r"\X", t), t


@pytest.mark.parametrize("emoji", ["👮‍♂️", "😭", "🥜", "💀", "🔒", "🙊", "🏃", "🏃‍♂️", "👍🏽", "🇺🇸",
                                   "1️⃣", "#️⃣", "🏴\U000e0067\U000e0062\U000e0073\U000e0063\U000e0074\U000e007f",
                                   "👨‍👩‍👧‍👦", "❤️‍🔥", "❤", "©️"])
def test_each_emoji_is_one_picture_of_the_emoji_font(emoji):
    assert H.is_emoji(emoji)
    names, missing = H._emoji_font().glyphs(emoji)
    assert len(names) == 1 and missing == []


def test_sequences_are_their_own_designs_and_letters_stay_letters():
    ef = H._emoji_font()
    assert ef.glyphs("👮‍♂️")[0] != ef.glyphs("👮")[0]                  # the policeman, not the police officer
    assert ef.glyphs("🏃‍♂️")[0] != ef.glyphs("🏃")[0]
    assert ef.glyphs("👍🏽")[0] != ef.glyphs("👍")[0]
    for ch in ("©", "™", "1", "#", "a", "'", "’"):
        assert not H.is_emoji(ch)


def test_what_cannot_be_drawn_is_left_out_with_a_warning():
    lay = H.layout("Shaking 🫨 face 你")                    # 🫨 is newer than the emoji font, 你 is in neither font
    assert "🫨 is not in the emoji font (Noto Color Emoji 2.034): left out" in lay.warnings
    assert "'你' is not in the headline font: left out" in lay.warnings
    img, _ = H.render("Shaking 🫨 face 你", size=(540, 960))
    assert img.getbbox() is not None
    lay = H.layout("He ran 🏃‍➡️ away")                 # a sequence the font does not know: its parts
    assert lay.warnings == ["🏃‍➡️ is not one emoji in the emoji font: shown as 2 emojis"]
    assert H.layout("a‍b test").warnings == []         # invisible characters go without a word


# ---------------------------------------------------------------------------------------------------------------------
# drawing
# ---------------------------------------------------------------------------------------------------------------------

def test_the_psd_example_lines_are_as_wide_as_photoshop_drew_them():
    """Ink widths of the example lines measured on the PSDs' type layers (px, more than 50 % covered)."""
    assert H.line_width("The fact that almonds sent him") == pytest.approx(1806, abs=2)
    assert H.line_width("to the hospital is hilarious 🥜😭") == pytest.approx(1831, abs=3)
    assert H.line_width("The fact that they're not even") == pytest.approx(1718, abs=2)
    assert H.line_width("When dad tries to be") == pytest.approx(1205, abs=2)


def test_render_draws_black_letters_and_colour_emojis_on_a_transparent_canvas():
    img, lay = H.render("The fact that almonds sent him\nto the hospital is hilarious 🥜😭")
    assert img.size == (2160, 3840) and img.mode == "RGBA"
    a = np.asarray(img)
    ys, xs = np.nonzero(a[..., 3])
    assert ys.min() >= 740 and ys.max() < 1035 and xs.min() >= 150 and xs.max() < 2010     # the headline area
    line1 = a[700:880]
    assert (line1[..., :3][line1[..., 3] > 0] == 0).all()                   # the letters are black
    tail = a[880:1035, 1690:2010].astype(int)
    colour = (tail[..., 3] > 200) & (tail[..., :3].max(axis=2) - tail[..., :3].min(axis=2) > 80)
    assert colour.sum() > 5000                                             # the peanut and the crying face


def test_a_template_with_its_headline_elsewhere_gets_it_there():
    t = "When dad tries to be the tour guide 💀😭"
    std, lay = H.render(t)
    moved, lay2 = H.render(t, anchor=(H.CENTRE_X - 100.0, H.BASELINE_1 + 50.0))
    assert lay2.centre_x == H.CENTRE_X - 100.0 and lay2.baselines == [899.0, 1046.0] and lay2.lines == lay.lines
    a, b = std.getbbox(), moved.getbbox()
    assert (b[0] - a[0], b[1] - a[1], b[2] - a[2], b[3] - a[3]) == (-100, 50, -100, 50)
    assert H.layout("Hilarious", anchor=(1000.0, 900.0)).baselines[0] == pytest.approx(H._block_middle(900.0)
                                                                                     + H.CAP_HEIGHT * H.SIZE / 2)


def test_the_preview_size_is_the_same_headline_smaller():
    t = "The fact that prison changed him this much is hilarious 🔒😭"
    big, _ = H.render(t)
    small, _ = H.render(t, size=(540, 960))
    assert small.size == (540, 960)
    for b, s in zip(big.getbbox(), small.getbbox()):
        assert s == pytest.approx(b / 4.0, abs=2)


def test_on_the_1080_sequence_letters_are_46_px_and_the_baselines_at_425_and_498():
    """Your finished videos: letters 46 px tall, baselines at y 425 and 498 (1080x1920)."""
    img, _ = H.render("The fact that he didn't even\nthink twice is hilarious 🏃‍♂️😭")
    d = _dark(img.resize((1080, 1920), Image.Resampling.BOX))
    for (y0, y1), base in (((360, 440), 425), ((440, 520), 498)):
        band = d[y0:y1] > 0.5
        x0 = x1 = int(np.nonzero(band.any(axis=0))[0][0])                  # the line's first letter: T, t
        while band[:, x1 + 1].any():
            x1 += 1
        stem = max(range(x0, x1 + 1), key=lambda c: float(d[y0:y1, c].sum()))
        rows = np.nonzero(d[y0:y1, stem] > 0.5)[0]
        bottom = y0 + rows[-1] + 1                                          # under the letter = the baseline
        assert abs(bottom - base) <= 1
        any_ink = np.nonzero((d[y0:y1, x0:x1 + 1] > 0.02).any(axis=1))[0]
        if base == 425:
            assert 45 <= any_ink[-1] + 1 - any_ink[0] <= 47                  # the T: 46 px, give or take a pixel


# ---------------------------------------------------------------------------------------------------------------------
# Photoshop's own rendering (the PSDs' example headlines): at least 97 % of the ink shared
# ---------------------------------------------------------------------------------------------------------------------

@pytest.fixture(scope="module")
def photoshop():
    """{psd: (the example headline's text, Photoshop's pixels of it on a transparent 2160x3840 canvas)}."""
    psd_tools = pytest.importorskip("psd_tools")
    out = {}
    for name, p in PSDS.items():
        if not p.is_file():
            continue
        psd = psd_tools.PSDImage.open(p)
        layer = next(lay for lay in psd.descendants() if lay.kind == "type" and lay.is_visible()
                     and "NeueMontreal" in str(lay.resource_dict.get("FontSet")))
        canvas = Image.new("RGBA", psd.size, (0, 0, 0, 0))
        canvas.paste(layer.topil(), layer.bbox[:2])
        out[name] = (str(layer.text).replace("\r", "\n").strip(), canvas)
    return out


def _iou(a: np.ndarray, b: np.ndarray) -> float:
    return float((a & b).sum()) / max(1, int((a | b).sum()))


@pytest.mark.parametrize("channel", list(PSDS))
def test_each_psds_example_headline_matches_photoshops_own(photoshop, channel):
    if channel not in photoshop:
        pytest.skip(f"{PSDS[channel]} is not there")
    text, theirs = photoshop[channel]
    mine, lay = H.render(text)
    assert lay.lines == text.split("\n") and not lay.shrunk
    A = np.asarray(mine)[..., 3] > 127
    B = np.asarray(theirs)[..., 3] > 127
    # line 2: its letters | its emojis, cut halfway between the last letter's ink and the first emoji's
    ln = H._measure(lay.lines[1])
    tf, ef = H._text_font(), H._emoji_font()
    x0 = H.CENTRE_X - (ln.advance - H.CENTRE_TRIM) / 2.0
    right = max(g.x + tf.bounds(g.name)[2] * H.SIZE / tf.upem for g in ln.glyphs
                if g.kind == "text" and tf.bounds(g.name))
    left = min(g.x + (ef.metrics(g.name)[0] + ef.ink(g.name)[0]) * H.SIZE / ef.ppem for g in ln.glyphs
               if g.kind == "emoji")
    cut = int(x0 + (right + left) / 2.0)
    rows1, rows2 = slice(700, 880), slice(880, 1060)
    letters = np.zeros_like(A)
    letters[rows1] = True
    letters[rows2, :cut] = True
    emojis = np.zeros_like(A)
    emojis[rows2, cut:] = True
    assert _iou(A[rows1], B[rows1]) >= 0.965                             # line 1 (measured: 97.2-97.9 %)
    assert _iou(A[rows2, :cut], B[rows2, :cut]) >= 0.965                  # line 2's letters (97.7-98.2 %)
    assert _iou(A & letters, B & letters) >= 0.97                        # all the letters (97.6-97.9 %)
    assert _iou(A & emojis, B & emojis) >= 0.98                          # the emojis (98.7-99.0 %)
    assert _iou(A, B) >= 0.975                                           # the whole headline (97.8-98.1 %)
