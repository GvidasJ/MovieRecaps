"""post_captions.py: two fixes on the finished captions, made after the caption step wrote 2_captions.srt
(pipeline.stage_captions) -- orchestration, in no cached stage (no stage module imports this file), so they cost no
re-transcription.

1. **On the sound of the edit you get** (day 13, 6.1 -- your video16 / mindculpt fixes). The captions are timed on
   a transcript of the caption step's own rendering of the edit's audio (the cut list: each piece at the
   competitor's speed), while 1_edit.xml plays every RAW clip at 100 %; where the competitor changed the speed the
   two can run apart (video16: 0.27 s early from 4.8 s on, mindculpt: up to 0.47 s late in its second half -- the
   delivered audio transcribed again agrees with you there), and in your fixes you dragged exactly those stretches
   onto the words as heard. Each transcript word is paired with the same word of the RAW's own transcript where
   1_edit.xml's A1 plays it (captions.speech_starts: what Premiere plays); a caption whose neighbouring words agree
   on an offset of at least MIN_SHIFT_S belongs to a stretch, and a stretch whose offset reaches STRETCH_SHIFT_S
   somewhere moves -- each caption by its offset, start and end, as you drag a block; an edge on a video cut stays
   on the cut. Captions on the sound or nearly (a smaller offset, words that disagree, too few words) are not
   touched: in your earlier fixes you kept them where they were.
2. **Pairs kept together** (day 13, 9.3; caption rule 9): see pairs_fix.
"""
from __future__ import annotations

import re
import statistics
from fractions import Fraction
from typing import Any, Sequence

PAIR_MAX_S = 1.0          # a transcript word pairs with the same word of the RAW's transcript only this near
WINDOW_S = 2.0            # a caption's offset: the paired words this near its start (each side)
MIN_WORDS = 5             # ... at least this many of them
AGREE_S = 0.1             # ... a word within this of their median offset agrees with it
AGREE_SHARE = 0.6         # ... and at least this share of them agree
MIN_SHIFT_S = 0.15        # only an offset this big moves a caption (0.15 s: 3.6 frames at 23.976 fps) ...
STRETCH_SHIFT_S = 0.25    # ... and only in a stretch whose offset reaches this somewhere (your video16: 0.27 s,
                          # mindculpt up to 0.47 s moved; laptop009's drift to 0.22 s at its end you kept)
MIN_STRETCH = 3           # ... of at least this many captions (one caption off alone, like video021's stammer
                          # "I've-", is no stretch)
MIN_FRAMES_S = 2 / 60     # a caption moved shorter than this keeps its length

_NORM = re.compile(r"[^0-9a-z']+")


def norm_word(text: str) -> str:
    """A word as compared between two transcripts: lower case, letters / digits / apostrophes only."""
    return _NORM.sub("", str(text).lower().replace("’", "'")).strip("'")


def pair_words(own: Sequence[Sequence[Any]], ref: Sequence[Sequence[Any]], max_s: float = PAIR_MAX_S
               ) -> list[tuple[float, float]]:
    """(own start, ref start) of the words both transcripts have, in order: the longest common run of the same words
    (lower case, punctuation dropped), a pair only within ``max_s`` -- a repeated word pairs with its own repeat.
    ``own`` / ``ref``: [(text, start s, end s), ...] on the same timeline."""
    a = [(norm_word(w[0]), float(w[1])) for w in own if norm_word(w[0])]
    b = [(norm_word(w[0]), float(w[1])) for w in ref if norm_word(w[0])]
    n, m = len(a), len(b)
    if not n or not m:
        return []
    dp = [[0] * (m + 1) for _ in range(n + 1)]
    for i in range(1, n + 1):
        row, prev = dp[i], dp[i - 1]
        wa, ta = a[i - 1]
        for j in range(1, m + 1):
            wb, tb = b[j - 1]
            if wa == wb and abs(ta - tb) <= max_s:
                row[j] = prev[j - 1] + 1
            else:
                row[j] = row[j - 1] if row[j - 1] > prev[j] else prev[j]
    out = []
    i, j = n, m
    while i > 0 and j > 0:
        wa, ta = a[i - 1]
        wb, tb = b[j - 1]
        if wa == wb and abs(ta - tb) <= max_s and dp[i][j] == dp[i - 1][j - 1] + 1:
            out.append((ta, tb))
            i, j = i - 1, j - 1
        elif dp[i - 1][j] >= dp[i][j - 1]:
            i -= 1
        else:
            j -= 1
    return out[::-1]


def local_offset(pairs: Sequence[tuple[float, float]], t: float, window: float = WINDOW_S) -> float | None:
    """The offset (own - ref, s) the paired words near second ``t`` agree on -- their median, when at least
    MIN_WORDS of them lie within ``window`` and AGREE_SHARE of those within AGREE_S of it; else None."""
    near = [o - r for o, r in pairs if abs(o - t) <= window]
    if len(near) < MIN_WORDS:
        return None
    med = statistics.median(near)
    if sum(1 for d in near if abs(d - med) <= AGREE_S) < AGREE_SHARE * len(near):
        return None
    return med


def _spoken(c: dict) -> bool:
    t = str(c.get("text") or "").strip()
    return c.get("mode") not in ("placeholder", "action") and not (t.startswith("*") and t.endswith("*"))


def retime(caps: Sequence[dict], pairs: Sequence[tuple[float, float]], fps: Fraction, cuts: Sequence[int] = (),
           stretches: Sequence[tuple[int, int]] = (), n_frames: int | None = None,
           min_shift: float = MIN_SHIFT_S) -> tuple[list[dict], list[dict]]:
    """(the captions moved onto the delivered sound, what moved): ``caps`` [{text, start, end (frames of ``fps``),
    mode, ...}] in order; ``pairs`` pair_words(transcript the captions are timed on, the RAW's words where the
    delivered A1 plays them); spoken captions next to each other whose starts have a local_offset of at least
    ``min_shift`` the same way make a stretch, and a stretch reaching STRETCH_SHIFT_S somewhere moves, each caption by
    its own offset (start and end, rounded to frames). An edge on a video cut (``cuts``, frames) stays there;
    captions inside another video's stretch (``stretches``, frames) and action / placeholder captions do not move.
    Order and back-to-back kept: a caption ends where the next one starts when it did before, never after it; one
    left shorter than 2 frames keeps its length. Returns new dicts (the input is not changed)."""
    f = float(fps)
    out = [dict(c) for c in caps]
    cut = set(int(x) for x in cuts)
    moved = []
    offs: list[float | None] = []
    for c in out:
        a, b = int(c["start"]), int(c["end"])
        if not _spoken(c) or any(s0 <= a < s1 or s0 < b <= s1 for s0, s1 in stretches):
            offs.append(None)
            continue
        off = local_offset(pairs, a / f)
        offs.append(None if off is None or abs(off) < min_shift else off)
    # a stretch: neighbouring spoken captions off the same way by min_shift or more (a placeholder / action between
    # them does not end it); it moves only when somewhere in it the offset reaches STRETCH_SHIFT_S -- a small drift
    # that only touches min_shift at its end stays (laptop009: 0.22 s at its very end, you kept those)
    shift = [0] * len(out)
    i = 0
    while i < len(out):
        if offs[i] is None:
            i += 1
            continue
        j, members = i, []
        while j < len(out) and (offs[j] is not None and (offs[j] > 0) == (offs[i] > 0) or
                                offs[j] is None and not _spoken(out[j]) and j > i):
            if offs[j] is not None:
                members.append(j)
            j += 1
        if len(members) >= MIN_STRETCH and max(abs(offs[k]) for k in members) >= max(min_shift, STRETCH_SHIFT_S):
            for k in members:
                shift[k] = -int(round(offs[k] * f))
        i = max(j, i + 1)
    if not any(shift):
        return out, moved
    was = [(int(c["start"]), int(c["end"])) for c in out]
    joined = [was[i][1] >= was[i + 1][0] for i in range(len(out) - 1)]
    min_len = max(1, int(round(MIN_FRAMES_S * f)))
    for i, c in enumerate(out):
        k = shift[i]
        if not k:
            continue
        a, b = was[i]
        na = a if a in cut else a + k
        nb = b if b in cut else b + k
        lo = int(out[i - 1]["start"]) + min_len if i else 0  # the caption before it keeps 2 frames at least
        na = max(na, lo, 0)
        if i + 1 < len(out) and not shift[i + 1]:            # ... and the one after it, when that one stays
            na = min(na, was[i + 1][0] - min_len)
        cuts_between = [x for x in cut if min(a, na) < x < max(a, na)]
        if cuts_between:                                       # never across a video cut: up to it
            na = max(cuts_between) if k < 0 else min(cuts_between)
        if n_frames is not None:
            nb = min(nb, int(n_frames))
        if nb - na < min_len:
            nb = na + (b - a)
        c["start"], c["end"] = na, nb
        if (na, nb) != (a, b):
            moved.append({"text": c.get("text"), "from": [a, b], "to": [na, nb], "by_frames": na - a,
                          "offset_s": round(-k / f, 3)})
    for i in range(len(out) - 1):                              # order and back to back as before
        p, q = out[i], out[i + 1]
        if int(q["start"]) < int(p["start"]) + min_len:
            q["start"] = int(p["start"]) + min_len
        if joined[i] or int(p["end"]) > int(q["start"]):
            p["end"] = int(q["start"])
        if int(q["end"]) < int(q["start"]) + min_len:
            q["end"] = int(q["start"]) + min_len
    if n_frames is not None and out and int(out[-1]["end"]) > int(n_frames):
        out[-1]["end"] = max(int(out[-1]["start"]) + 1, int(n_frames))
    return out, moved


# ---------------------------------------------------------------------------------------------------------------------
# 2. pairs kept together (caption rule 9)
# ---------------------------------------------------------------------------------------------------------------------

PAIR_DETERMINERS = frozenset("a an the my your".split())   # the words whose noun you always keep with them
PAUSE_S = 0.25            # captions.PAUSE_S: a pause this long between two captions lets them split anywhere
CUT_NEAR_S = 0.25         # a video cut this near the boundary: rule 10 decides there, not this
MAX_ON_SCREEN_S = 1.5     # caption_rules.MAX_ON_SCREEN_S: a caption of 2+ words at most this long on screen
LONE_WEAK = "other way"   # moving the determiner on would leave one weak word ("on" | "the island"): the noun comes
                          # back instead ("on the island" | "together", your video18)


def _toks(text: str) -> list[str]:
    return [t for t in str(text).replace("\n", " ").split() if t]


def _fits_text(toks: Sequence[str]) -> bool:
    """captions._fits on the words' text: at most 20 characters and 4 words (5 when it starts with a weak word)."""
    from .captions import MAX_CHARS, MAX_WORDS, is_weak
    if not toks or len(" ".join(toks)) > MAX_CHARS:
        return False
    return len(toks) <= MAX_WORDS or (len(toks) == MAX_WORDS + 1 and is_weak(toks[0]))


def _heard(words: Sequence[Sequence[Any]], tok: str, lo: float, hi: float, near: float
           ) -> tuple[float, float, str] | None:
    """The heard word ``tok`` starting between lo and hi (s), the one nearest ``near``: (start, end, as heard --
    with its punctuation); None if not heard there."""
    w0 = norm_word(tok)
    cand = [(float(w[1]), float(w[2]), str(w[0])) for w in words
            if norm_word(w[0]) == w0 and lo - 0.05 <= float(w[1]) <= hi + 0.05]
    return min(cand, key=lambda w: abs(w[0] - near)) if cand else None


def _word_time(words: Sequence[Sequence[Any]], tok: str, lo: float, hi: float, near: float) -> float | None:
    """The start (s) of the heard word ``tok`` between lo and hi (s), the one nearest ``near``; None if not heard
    there."""
    h = _heard(words, tok, lo, hi, near)
    return None if h is None else h[0]


def _share_time(c: dict, toks: Sequence[str], k: int, f: float) -> float:
    """Where token k of caption c starts when no heard word says so: the caption's time shared by the words'
    lengths (s)."""
    total = sum(len(t) for t in toks) or 1
    before = sum(len(t) for t in toks[:k])
    return (int(c["start"]) + (int(c["end"]) - int(c["start"])) * before / total) / f


def _name_pair(ta: Sequence[str], tb: Sequence[str], hx: tuple | None, hy: tuple | None) -> bool:
    """The first caption ends on a name word and the next starts with one ("all over Hong" | "Kong"): both
    capitalised words, letters only, not "I" nor an interjection -- and heard as one name: both in the transcript
    (``hx`` / ``hy``: _heard), capitalised there too, nothing between them ("God." | "No" is a sentence end); the
    first caption has another word before it (a lone capitalised word may be a sentence's first)."""
    from .captions import is_interjection
    x, y = ta[-1], tb[0]
    if not (x.isalpha() and y.isalpha() and x[:1].isupper() and y[:1].isupper()) or "I" in (x, y):
        return False
    if len(ta) == 1 or is_interjection(x) or is_interjection(y) or hx is None or hy is None:
        return False
    rx, ry = hx[2].strip(), hy[2].strip()
    return rx[:1].isupper() and ry[:1].isupper() and not re.search(r"[.?!,;:…\-]['\"’”]?$", rx)


def pairs_fix(caps: Sequence[dict], words: Sequence[Sequence[Any]], fps: Fraction, cuts: Sequence[int] = (),
              stretches: Sequence[tuple[int, int]] = (), names: bool = True) -> tuple[list[dict], list[dict]]:
    """Rule 9 made, where it is safe (your fixes: "you're a" | "lighting guy" -> "a lighting guy", "still in the" |
    "water?" -> "the water?", "my" | "daddy saved" -> "my daddy"): two spoken captions back to back (no pause, no
    video cut there) that split a determiner (a, an, the, my, your) from the word it belongs to --
      * the first caption ends on it after other words: the determiner moves to the front of the next caption;
      * the first caption is the determiner alone: the next caption's first word joins it (both into one when that
        was the next caption's only word)
    -- only when every caption it makes keeps the caption rules: at most 20 characters and 4 words (5 starting with a
    weak word), a caption of 2+ words at most 1.5 s on screen, none shorter than 2 frames. The new boundary is where
    the moved word is heard (``words``: the transcript [(text, start s, end s)]; else the captions' time shared by
    the letters). Returns (the captions, what changed); the input is not changed."""
    f = float(fps)
    out = [dict(c) for c in caps]
    done: list[dict] = []
    gap = int(round(PAUSE_S * f))
    near = int(round(CUT_NEAR_S * f))
    min_len = max(1, int(round(MIN_FRAMES_S * f)))
    i = 0
    while i < len(out) - 1:
        a, b = out[i], out[i + 1]
        ta, tb = _toks(a.get("text", "")), _toks(b.get("text", ""))
        a0, a1, b0, b1 = int(a["start"]), int(a["end"]), int(b["start"]), int(b["end"])
        ok = (_spoken(a) and _spoken(b) and ta and tb and b0 - a1 <= gap
              and not any(a1 - near <= x <= b0 + near for x in cuts)
              and not any(s0 < b1 and a0 < s1 for s0, s1 in stretches))
        if not ok:
            i += 1
            continue
        from .captions import DETERMINERS, PREPOSITIONS
        last, first = norm_word(ta[-1]), norm_word(tb[0])
        hx = _heard(words, ta[-1], a0 / f, a1 / f, a1 / f)       # the two words as heard (time, punctuation)
        hy = _heard(words, tb[0], b0 / f, b1 / f, b0 / f)
        name = names and _name_pair(ta, tb, hx, hy)
        det = last in PAIR_DETERMINERS and ta[-1].isalpha() and first not in DETERMINERS and first not in PREPOSITIONS
        if not (name or det) or not first or not re.match(r"^[A-Za-z0-9]", tb[0]):
            i += 1
            continue
        if hx is not None and (re.search(r"[.?!,;:…\-]['\"’”]?$", hx[2].strip()) or
                               (hy is not None and hy[0] - hx[1] > PAUSE_S)):
            i += 1                             # heard with a comma / a pause after it: a break may go there
            continue
        if name and len(ta) < 2:
            i += 1
            continue
        from .captions import is_weak
        alone = len(ta) == 2 and (is_weak(ta[0]) or norm_word(ta[0]) in PREPOSITIONS)
        if len(ta) >= 2 and not (alone and LONE_WEAK == "other way" and len(tb) >= 2):
            na, nb = ta[:-1], [ta[-1]] + tb    # "still in the" | "water?" -> "still in" | "the water?"
            t = hx[0] if hx is not None else _share_time(a, ta, len(ta) - 1, f)
            merged = False
        elif len(ta) >= 2:                     # "on the" | "island together" -> "on the island" | "together"
            na, nb = ta + [tb[0]], tb[1:]
            t = _word_time(words, tb[1], b0 / f, b1 / f, b0 / f)
            t = _share_time(b, tb, 1, f) if t is None else t
            merged = False
        elif len(tb) >= 2:                     # "my" | "daddy saved" -> "my daddy" | "saved"
            na, nb = ta + [tb[0]], tb[1:]
            t = _word_time(words, tb[1], b0 / f, b1 / f, b0 / f)
            t = _share_time(b, tb, 1, f) if t is None else t
            merged = False
        else:                                  # "My" | "sister's" -> "My sister's"
            na, nb, t, merged = ta + tb, [], b1 / f, True
        k = int(round(t * f))
        if not merged:
            k = max(a0 + min_len, min(k, b1 - min_len))
        new_a = (a0, b1 if merged else k)
        new_b = None if merged else (k, b1)
        pieces = [(na, new_a)] + ([] if merged else [(nb, new_b)])
        if not all(_fits_text(tx) and (len(tx) < 2 or (y - x) / f <= MAX_ON_SCREEN_S + 1e-9) and y - x >= min_len
                   for tx, (x, y) in pieces):
            i += 1
            continue
        was = f"{a.get('text')} | {b.get('text')}"
        a.update(text=" ".join(na), start=new_a[0], end=new_a[1])
        if merged:
            out.pop(i + 1)
        else:
            b.update(text=" ".join(nb), start=new_b[0], end=new_b[1])
        now = " | ".join(" ".join(tx) for tx, _ in pieces)
        done.append({"was": was, "now": now, "start": a0, "end": b1})
        i += 1
    return out, done


# ---------------------------------------------------------------------------------------------------------------------
# the run's step (pipeline.stage_captions)
# ---------------------------------------------------------------------------------------------------------------------

def run(ctx: Any) -> dict:
    """Both fixes on a run's written captions (ctx.captions; 2_captions.srt on the planning grid, before --speed /
    the RAW's rate): pairs_fix, then retime -- only for captions timed on the edit's transcript (styled: grouped in
    your style or following the competitor's captions; the copied ones were timed to the speech already), not for a
    voice-over. 2_captions.srt and debug/captions.json are rewritten when something changed; ctx.captions gets
    "post": {pairs: [...], retimed: [...], offset_s: {median, words}}. Never raises for a missing piece (no speech
    map, no transcript: that fix is skipped)."""
    from pathlib import Path

    from .captions import Caption, edit_cuts, speech_starts, write_srt
    from .common import dump_json, log
    from .export_xml_edl import parse_premiere_xml
    from .run_folders import EDIT_XML
    cap = getattr(ctx, "captions", None) or {}
    info: dict = {"pairs": [], "retimed": []}
    if not cap.get("path") or not cap.get("captions"):
        return info
    fps = Fraction(str(cap.get("fps") or 60))
    xml = ctx.cfg.deliver / EDIT_XML
    words = cap.get("words_final") or cap.get("transcript") or []
    cuts = edit_cuts(xml, fps) if xml.exists() else []
    stretches = [(int(o["a"]), int(o["b"])) for o in cap.get("other_video") or []]
    rows = sorted((dict(c) for c in cap["captions"]), key=lambda c: int(c["start"]))
    rows, info["pairs"] = pairs_fix(rows, words, fps, cuts, stretches)
    voice_over = str(cap.get("source") or "").startswith("voice-over")
    sm = getattr(ctx, "speech", None)
    if cap.get("styled") and not voice_over and sm is not None and xml.exists() and words:
        _onsets, ref = speech_starts(parse_premiere_xml(xml)["audio"], sm, fps)
        pairs = pair_words(words, ref)
        if pairs:
            info["offset_s"] = {"median": round(statistics.median(o - r for o, r in pairs), 3), "words": len(pairs)}
        rows, info["retimed"] = retime(rows, pairs, fps, cuts, stretches, int(cap.get("frames") or 0) or None)
    if not info["pairs"] and not info["retimed"]:
        cap["post"] = info
        return info
    keep = ("text", "start", "end", "mode", "start_tc", "end_tc")
    caps = [Caption(str(c["text"]), int(c["start"]), int(c["end"]), str(c.get("mode") or "voice")) for c in rows]
    write_srt(caps, Path(cap["path"]), fps)
    from .captions import frame_ms, ms_tc
    for c in rows:
        c["start_tc"], c["end_tc"] = ms_tc(frame_ms(int(c["start"]), fps)), ms_tc(frame_ms(int(c["end"]), fps))
    cap["captions"] = [{**{k: v for k, v in c.items() if k not in keep}, **{k: c[k] for k in keep if k in c}}
                       for c in rows]
    cap["count"] = len(rows)
    cap["by_mode"] = {m: sum(1 for c in rows if c.get("mode") == m) for m in dict.fromkeys(c.get("mode") for c in rows)}
    cap["placeholders"] = [c for c in cap["captions"] if c.get("mode") == "placeholder"]
    cap["post"] = info
    dump_json(cap, ctx.cfg.debug_dir / "captions.json")
    if info["pairs"]:
        log.info("captions: %d pair(s) kept together (rule 9) -- %s", len(info["pairs"]),
                 "; ".join(f"'{d['was']}' -> '{d['now']}'" for d in info["pairs"][:8]))
    if info["retimed"]:
        off = statistics.median(d["offset_s"] for d in info["retimed"])
        log.info("captions: %d caption(s) moved onto the sound of the edit (there the transcript ran %.2f s %s the "
                 "sound 1_edit.xml plays) -- %s", len(info["retimed"]), abs(off), "after" if off > 0 else "before",
                 "; ".join(f"'{d['text']}' {d['by_frames']:+d} frames" for d in info["retimed"][:8]))
    return info
