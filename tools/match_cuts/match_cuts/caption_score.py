"""caption_score.py: how close the tool's captions come to the user's finished captions (an answer-key SRT) --
check-all's scorecard and the end summary.

The two files are compared **moment by moment**, so a different cut of the edit (a silence removed, a clip that
plays on to finish a word) does not count against the captions: each caption's start is the moment of the source it
captions -- the RAW (``raw``), or on another video's stretch the competitor (``comp``). The tool's moments come from
its own ``1_edit.xml`` (A1: the RAW audio under the clips; OTHER VIDEO stretches: the competitor), the answer key's
from the timeline the user made it on: the user's finished edit, or the competitor's own edit (a Timeline).

* **exact**: a key caption the tool reproduces -- the same text (case, letters, digits, ? and !; spacing and the
  apostrophe's shape ignored) starting within EXACT_FRAMES frames of the 60 fps sequence of where the tool's edit
  plays the key caption's first moment;
* **word errors**: the tool's words against the key's (lower case, punctuation dropped) -- wrong, extra and missing
  words, counted only where both edits play the moment (a stretch one of them cut out is not a caption error);
* **rule breaks**: the tool's captions against the user's style (style_rules: length, punctuation, back to back).

The remaining differences, by type: ``timing`` (the same caption, starting more than EXACT_FRAMES away), ``split``
(the same words, grouped into captions differently), ``casing`` (the same words, other capitals or punctuation),
``words`` (other words), ``edit`` (the tool's edit does not play that moment), ``extra`` (a tool caption where the
user has none).
"""
from __future__ import annotations

import bisect
import json
import re
from dataclasses import dataclass, field
from fractions import Fraction
from pathlib import Path
from typing import Any, Sequence

EXACT_FRAMES = 2                 # a caption starting within this many 60 fps frames of the user's is on time
SEQ_FPS = 60.0
SAME_MOMENT_S = 0.25             # a key moment the tool's edit plays within this (source s): it plays that moment
MAX_CHARS = 20                   # the user's style (srt/): no caption over 20 characters ...
MAX_WORDS = 5                    # ... or 5 words
_PUNCT = re.compile(r"[^\w' ]+")
_QUOTES = str.maketrans({"’": "'", "‘": "'", "“": '"', "”": '"'})


def norm_words(text: str) -> list[str]:
    """Lower-case words, punctuation dropped, typographic apostrophes as "'" (word errors are about words)."""
    t = _PUNCT.sub(" ", str(text).translate(_QUOTES).replace("\n", " ").lower())
    return [w.strip("'") for w in t.split() if w.strip("'")]


def exact_text(text: str) -> str:
    """The text as compared for an exact caption: case, words and punctuation kept; spacing, line breaks and the
    apostrophe's shape normalised."""
    return " ".join(str(text).translate(_QUOTES).replace("\n", " ").split())


# ---------------------------------------------------------------------------------------------------------------------
# timelines: sequence seconds -> the moment of the source they play
# ---------------------------------------------------------------------------------------------------------------------

@dataclass(frozen=True)
class Piece:
    t0: float                    # sequence seconds [t0, t1)
    t1: float
    kind: str                    # "raw" | "comp" (another video's stretch: the competitor's own seconds)
    src: float                   # the source second played at t0
    speed: float = 1.0

    def at(self, t: float) -> float:
        return self.src + (t - self.t0) * self.speed

    @property
    def src_end(self) -> float:
        return self.at(self.t1)


class Timeline:
    """An edit's audio as pieces of its sources (``at``: what plays at a sequence second; ``find``: where a moment
    of a source plays)."""

    def __init__(self, pieces: Sequence[Piece]):
        self.pieces = sorted((p for p in pieces if p.t1 > p.t0), key=lambda p: (p.t0, p.t1))
        self.starts = [p.t0 for p in self.pieces]

    def at(self, t: float) -> tuple[str, float] | None:
        """The (kind, source second) playing at sequence second t: the latest-starting piece holding it (half a
        frame of slack at a piece's edges); None in a gap."""
        i = bisect.bisect_right(self.starts, t + 1e-6) - 1
        for j in range(i, max(-1, i - 8), -1):
            p = self.pieces[j]
            if p.t0 - 1e-6 <= t < p.t1 - 1e-9:
                return p.kind, p.at(max(t, p.t0))
        for j in (i, i + 1):
            if 0 <= j < len(self.pieces):
                p = self.pieces[j]
                if abs(t - p.t0) < 0.5 / SEQ_FPS:
                    return p.kind, p.src
                if abs(t - p.t1) < 0.5 / SEQ_FPS:
                    return p.kind, p.at(p.t1)
        return None

    def find(self, kind: str, src: float, after: float | None = None) -> tuple[float, float] | None:
        """(sequence second, distance in source seconds) where this edit plays (kind, src): inside a piece the
        distance is 0; else the nearest piece edge. A moment played twice: the first occurrence at or after
        ``after`` (the order of the captions)."""
        best = None
        for p in self.pieces:
            if p.kind != kind or not p.speed:
                continue
            lo, hi = sorted((p.src, p.src_end))
            if lo - 1e-6 <= src <= hi + 1e-6:
                t, d = p.t0 + (src - p.src) / p.speed, 0.0
            elif src < lo:
                t, d = (p.t0 if p.speed > 0 else p.t1), lo - src
            else:
                t, d = (p.t1 if p.speed > 0 else p.t0), src - hi
            key = (round(d, 3), 0 if after is None or t >= after - 0.5 else 1, t)
            if best is None or key < best[0]:
                best = (key, t, d)
        return None if best is None else (best[1], best[2])

    @property
    def duration(self) -> float:
        return max((p.t1 for p in self.pieces), default=0.0)

    def to_json(self) -> list[dict]:
        return [{"start": round(p.t0, 6), "end": round(p.t1, 6), "kind": p.kind, "src_in": round(p.src, 6),
                 "speed": p.speed} for p in self.pieces]

    @classmethod
    def from_json(cls, data: dict | list | str | Path) -> "Timeline":
        if isinstance(data, (str, Path)):
            data = json.loads(Path(data).read_text(encoding="utf-8"))
        rows = data.get("audio") if isinstance(data, dict) else data
        return cls([Piece(float(r["start"]), float(r["end"]), str(r.get("kind") or "raw"), float(r["src_in"]),
                          float(r.get("speed") or 1.0)) for r in rows or []])

    @classmethod
    def from_xml(cls, xml: str | Path, other_video: Sequence[dict] = ()) -> "Timeline":
        """An edit's XML (the tool's 1_edit.xml, or the user's finished XML as Premiere exports it -- the sequence
        inside a project): A1 (the RAW), plus another video's stretches ``other_video`` ([{a, b (sequence frames),
        t0 (competitor s)}], the run's captions.json)."""
        import xml.etree.ElementTree as ET
        from .export_xml_edl import _remap_speed, plan_in_out
        root = ET.parse(str(xml)).getroot()
        seqs = [root] if root.tag == "sequence" else list(root.iter("sequence"))
        if not seqs:
            raise ValueError(f"{xml}: no <sequence>")
        seq = max(seqs, key=lambda s: len(list(s.iter("clipitem"))))
        f = float(seq.findtext("rate/timebase") or SEQ_FPS)
        if str(seq.findtext("rate/ntsc") or "").upper() == "TRUE":
            f = f * 1000.0 / 1001.0
        ps = []
        a1 = seq.find("media/audio/track")
        for el in (a1.findall("clipitem") if a1 is not None else []):
            s, e = int(el.findtext("start") or -1), int(el.findtext("end") or -1)
            if not 0 <= s < e:
                continue
            sp = _remap_speed(el)
            i0, _i1 = plan_in_out(int(el.findtext("in") or 0), int(el.findtext("out") or 0), sp < 0)
            ps.append(Piece(s / f, e / f, "raw", i0 / f, float(sp or 1.0)))
        ps += [Piece(int(o["a"]) / f, int(o["b"]) / f, "comp", float(o["t0"])) for o in other_video or []]
        return cls(ps)


def competitor_timeline(cutlist: Any, lag_s: float = 0.0) -> Timeline:
    """The competitor's own edit as its editor made it: each segment's audio range (J/L offsets included) playing
    its RAW audio map (render_preview.audio_segment: its own, or the audio line it follows) in the picture's time --
    ``lag_s`` later in the RAW (the competitor's audio offset, export_ae.audio_sync_params) -- and another video's
    stretches as the competitor's own seconds. Pass the cut list with the B-roll spots replaced (broll.py) so a
    cutaway over RAW speech maps too: a piece placed where the competitor's sound plays (``audio.broll.heard``) moves
    by the competitor's measured A/V offset into the picture's time (a file that plays its sound 54 ms after the
    picture: 54 ms later in the RAW), and so do the sound's cuts between two such pieces (its editor made them that
    much earlier than they are heard)."""
    from .export_xml_edl import _raw_in_seconds, other_video_of, seg_speed
    from .render_preview import audio_segment
    cf, rf = Fraction(cutlist.comp_fps), Fraction(cutlist.raw_fps)
    n = int(cutlist.competitor["frames"])
    av = (getattr(cutlist, "audio", None) or {}).get("av_offset") or {}
    g = float(av["lag_ms"]) / 1000.0 if av.get("status") == "measured" and av.get("lag_ms") is not None else 0.0
    ps, heard = [], []
    for seg in sorted(cutlist.segments, key=lambda s: (int(s.comp_in), int(s.id))):
        au = seg.audio or {}
        k0 = max(0, int(seg.comp_in) + int(au.get("in_offset_frames") or 0))
        k1 = min(n, int(seg.comp_out) + int(au.get("out_offset_frames") or 0))
        if k1 <= k0:
            continue
        if other_video_of(seg) is not None:
            ps.append(Piece(k0 / float(cf), k1 / float(cf), "comp", k0 / float(cf)))
            heard.append(False)
            continue
        a = audio_segment(seg)
        if a is None or a.time_remap_keys:
            continue
        v = float(seg_speed(a, cf))
        by_sound = bool(((seg.audio or {}).get("broll") or {}).get("heard"))
        r0 = _raw_in_seconds(a, rf) + v * (float(Fraction(k0 - int(seg.comp_in)) / cf) + lag_s - (g if by_sound else 0.0))
        ps.append(Piece(k0 / float(cf), k1 / float(cf), "raw", r0, v))
        heard.append(by_sound)
    for i in range(len(ps) - 1):                 # a cut of the sound between two pieces found in the sound
        p, q = ps[i], ps[i + 1]
        if heard[i] and heard[i + 1] and abs(p.t1 - q.t0) < 1e-6 and g:
            t = p.t1 + g
            if p.t0 < t < q.t1:
                ps[i] = Piece(p.t0, t, p.kind, p.src, p.speed)
                ps[i + 1] = Piece(t, q.t1, q.kind, q.src + q.speed * (t - q.t0), q.speed)
    return Timeline(ps)


# ---------------------------------------------------------------------------------------------------------------------
# the score
# ---------------------------------------------------------------------------------------------------------------------

@dataclass
class Cap:
    text: str
    start: float                 # sequence seconds (its own file's timeline)
    end: float
    where: float | None = None   # a key caption: where the tool's edit plays its first moment (tool seconds)
    cut_s: float = 0.0           # ... how much of the source before it the tool's edit left out (s)


@dataclass
class Score:
    """One video's caption score (see the module docstring)."""
    name: str
    key: int = 0
    exact: int = 0
    words: int = 0               # words of the key where both edits play the moment
    subs: int = 0
    ins: int = 0
    dels: int = 0
    rule_breaks: list[str] = field(default_factory=list)
    diffs: list[dict] = field(default_factory=list)

    @property
    def exact_pct(self) -> float:
        return 100.0 * self.exact / self.key if self.key else 0.0

    @property
    def wer(self) -> float:
        return 100.0 * (self.subs + self.ins + self.dels) / self.words if self.words else 0.0

    def by_type(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for d in self.diffs:
            out[d["type"]] = out.get(d["type"], 0) + 1
        return dict(sorted(out.items(), key=lambda kv: (-kv[1], kv[0])))

    def line(self) -> str:
        bt = ", ".join(f"{k} {v}" for k, v in self.by_type().items())
        return (f"{self.name}: {self.exact}/{self.key} of your captions exactly ({self.exact_pct:.0f} %), word errors "
                f"{self.wer:.1f} % ({self.subs} wrong, {self.ins} extra, {self.dels} missing of {self.words}), "
                f"{len(self.rule_breaks)} rule break(s)" + (f"; differences: {bt}" if bt else ""))

    def to_dict(self) -> dict:
        return {"name": self.name, "key": self.key, "exact": self.exact, "exact_pct": round(self.exact_pct, 1),
                "words": self.words, "subs": self.subs, "ins": self.ins, "dels": self.dels,
                "wer": round(self.wer, 2), "rule_breaks": list(self.rule_breaks), "by_type": self.by_type(),
                "diffs": list(self.diffs)}


def word_edits(a: Sequence[str], b: Sequence[str]) -> tuple[int, int, int]:
    """(substitutions, insertions, deletions) turning reference a into hypothesis b (word Levenshtein)."""
    n, m = len(a), len(b)
    d = [[0] * (m + 1) for _ in range(n + 1)]
    op = [[""] * (m + 1) for _ in range(n + 1)]
    for i in range(1, n + 1):
        d[i][0], op[i][0] = i, "D"
    for j in range(1, m + 1):
        d[0][j], op[0][j] = j, "I"
    for i in range(1, n + 1):
        for j in range(1, m + 1):
            same = a[i - 1] == b[j - 1]
            d[i][j], op[i][j] = min((d[i - 1][j - 1] + (not same), "M" if same else "S"),
                                    (d[i - 1][j] + 1, "D"), (d[i][j - 1] + 1, "I"))
    s = ins = dl = 0
    i, j = n, m
    while i > 0 or j > 0:
        o = op[i][j]
        if o in ("S", "M"):
            s += o == "S"
            i, j = i - 1, j - 1
        elif o == "D":
            dl += 1
            i -= 1
        else:
            ins += 1
            j -= 1
    return s, ins, dl


def _number_punct(text: str) -> str:
    return re.sub(r"(?<=\d)[.,:](?=\d)", "", text)


def style_rules(caps: Sequence[Cap], allowed_gaps: Sequence[tuple[float, float]] = ()) -> list[str]:
    """The tool's captions against the user's style: at most MAX_CHARS characters and MAX_WORDS words, no full stop
    or comma (but inside a number), back to back -- no gap, no overlap (another video's stretches ``allowed_gaps``,
    sequence seconds, aside). A ``*sound*`` caption is exempt from the length and punctuation rules."""
    out = []
    srt = sorted(caps, key=lambda c: c.start)
    for i, c in enumerate(srt):
        t = c.text.replace("\n", " ").strip()
        tag = f"{c.start:.3f} s '{t}'"
        action = t.startswith("*") and t.endswith("*")
        if not action and len(t) > MAX_CHARS:
            out.append(f"{tag}: {len(t)} characters (max {MAX_CHARS})")
        if not action and len(t.split()) > MAX_WORDS:
            out.append(f"{tag}: {len(t.split())} words (max {MAX_WORDS})")
        if not action and re.search(r"[.,]", _number_punct(t)):
            out.append(f"{tag}: a full stop or comma")
        if i + 1 < len(srt):
            gap = srt[i + 1].start - c.end
            if gap > 0.5 / SEQ_FPS and not any(a - 1e-3 <= c.end and srt[i + 1].start <= b + 1e-3
                                               for a, b in allowed_gaps):
                out.append(f"{tag}: a gap of {gap:.3f} s before the next caption")
            elif gap < -0.5 / SEQ_FPS:
                out.append(f"{tag}: overlaps the next caption by {-gap:.3f} s")
    return out


def score(name: str, key: Sequence[Cap], key_tl: Timeline, got: Sequence[Cap], got_tl: Timeline,
          allowed_gaps: Sequence[tuple[float, float]] = ()) -> Score:
    """The tool's captions ``got`` (on its edit ``got_tl``) against the answer key ``key`` (on ``key_tl``)."""
    sc = Score(name, key=len(key))
    tol = EXACT_FRAMES / SEQ_FPS + 1e-6
    key = sorted(key, key=lambda c: c.start)
    got = sorted(got, key=lambda c: c.start)
    after = None
    for k in key:                                 # where the tool's edit plays each key caption's first moment
        k.where, k.cut_s = _where(k, key_tl, got_tl, after)
        if k.where is not None:
            after = k.where
    used: set[int] = set()
    starts = [c.start for c in got]
    for k in key:
        if k.where is not None:
            i = bisect.bisect_left(starts, k.where - tol)
            hit = next((j for j in range(i, len(got)) if got[j].start <= k.where + tol and j not in used
                        and exact_text(got[j].text) == exact_text(k.text)), None)
            if hit is not None:
                used.add(hit)
                sc.exact += 1
                continue
        sc.diffs.append(_why(k, got, tol))
    key_moments = [(k.where, k.where + (k.end - k.start)) for k in key if k.where is not None]
    for j, c in enumerate(got):                   # a tool caption over a moment the user left uncaptioned
        if j in used or any(a - tol < c.end and c.start < b + tol for a, b in key_moments):
            continue
        m = got_tl.at(frame_start(c.start) + 1e-4)
        f = key_tl.find(m[0], m[1]) if m is not None else None
        if f is not None and f[1] <= SAME_MOMENT_S:
            sc.diffs.append({"type": "extra", "got": exact_text(c.text), "got_start": round(c.start, 3)})
    # word errors where both edits play the moment, word by word (each placed in its caption by its length), in order
    kw = [w for w, t in _caption_words(key) if _both_play(key_tl, got_tl, t)]
    gw = [w for w, t in _caption_words(got) if _both_play(got_tl, key_tl, t)]
    sc.words = len(kw)
    sc.subs, sc.ins, sc.dels = word_edits(kw, gw)
    sc.rule_breaks = style_rules(got, allowed_gaps)
    return sc


def frame_start(t: float) -> float:
    """An SRT time (whole milliseconds) back on the 60 fps frame it stands for (24.783 -> frame 1487 = 24.78333)."""
    return round(t * SEQ_FPS) / SEQ_FPS


def _where(k: Cap, key_tl: Timeline, got_tl: Timeline, after: float | None) -> tuple[float | None, float]:
    """(where the tool's edit plays key caption k's first moment, how far that is from it in the source), or (None,
    0) when the tool's edit plays none of it. The first moment is its start; when the tool's edit leaves that out
    (e.g. the user's caption starts on a short piece the tool cut), the first moment of the caption the tool plays,
    a frame at a time -- the tool's caption should start where that content starts."""
    t0 = frame_start(k.start)
    step = 1.0 / SEQ_FPS
    cut = next((p.t0 for p in key_tl.pieces if t0 < p.t0 <= t0 + EXACT_FRAMES * step + 1e-6), None)
    if cut is not None:              # a frame or two before a cut of its own edit (a competitor frame at 30 fps
        t0 = cut                     # rounded on the 60 fps grid): the caption starts on the cut
    n = max(1, int(round((frame_start(k.end) - t0) / step)))
    for i in range(n):
        m = key_tl.at(t0 + i * step + 1e-4)
        f = got_tl.find(m[0], m[1], after) if m is not None else None
        if f is not None and f[1] <= SAME_MOMENT_S:
            return f
    return None, 0.0


def _caption_words(caps: Sequence[Cap]) -> list[tuple[str, float]]:
    """Each caption's words with an estimated time: the caption's time shared by the words' lengths (their middles)."""
    out = []
    for c in sorted(caps, key=lambda c: c.start):
        ws = norm_words(c.text)
        if not ws:
            continue
        total = sum(len(w) for w in ws)
        t0 = frame_start(c.start)
        span = max(frame_start(c.end) - t0, 1.0 / SEQ_FPS)
        acc = 0
        for w in ws:
            out.append((w, t0 + span * (acc + len(w) / 2.0) / total))
            acc += len(w)
    return out


def _both_play(tl: Timeline, other: Timeline, t: float) -> bool:
    """Does the other edit play the moment ``tl`` plays at its second t?"""
    m = tl.at(t)
    f = other.find(m[0], m[1]) if m is not None else None
    return f is not None and f[1] <= SAME_MOMENT_S


def _why(k: Cap, got: Sequence[Cap], tol: float) -> dict:
    """Why key caption k has no exact counterpart (the module docstring's types)."""
    d: dict[str, Any] = {"key": exact_text(k.text), "key_start": round(k.start, 3)}
    if k.where is None:
        return dict(d, type="edit")
    a, b = k.where, k.where + (k.end - k.start)
    over = [c for c in got if c.start < b - tol and c.end > a + tol]
    near = min(got, key=lambda c: abs(c.start - a), default=None)
    if near is not None:
        d.update(got=exact_text(near.text), got_start=round(near.start, 3),
                 off_frames=round((near.start - a) * SEQ_FPS, 1))
    if near is not None and exact_text(near.text) == exact_text(k.text):
        return dict(d, type="timing")
    kw = norm_words(k.text)
    if over:
        d["got_over"] = [exact_text(c.text) for c in over]
        ow = [w for c in over for w in norm_words(c.text)]
        one = min(over, key=lambda c: abs(c.start - a))
        if norm_words(one.text) == kw and abs(one.start - a) <= tol:
            return dict(d, type="casing")
        if norm_words(one.text) == kw:
            return dict(d, type="timing")
        if kw and (ow == kw or _contains(ow, kw)):
            return dict(d, type="split")
    return dict(d, type="words")


def _contains(hay: Sequence[str], needle: Sequence[str]) -> bool:
    n = len(needle)
    return any(list(hay[i:i + n]) == list(needle) for i in range(len(hay) - n + 1))


def load_captions(srt: str | Path) -> list[Cap]:
    from .captions import read_srt
    return [Cap(c["text"], c["start_ms"] / 1000.0, c["end_ms"] / 1000.0) for c in read_srt(srt)]


def score_run(name: str, key_srt: str | Path, key_tl: Timeline, run_dir: str | Path) -> Score:
    """A run folder's 2_captions.srt (on its 1_edit.xml; another video's stretches from extras/debug/captions.json)
    against the user's key_srt on key_tl."""
    from .run_folders import CAPTIONS_SRT, EDIT_XML, EXTRAS
    run = Path(run_dir)
    ov: list[dict] = []
    dbg = run / EXTRAS / "debug" / "captions.json"
    if dbg.is_file():
        ov = list(json.loads(dbg.read_text(encoding="utf-8")).get("other_video") or [])
    got_tl = Timeline.from_xml(run / EDIT_XML, ov)
    got = load_captions(run / CAPTIONS_SRT) if (run / CAPTIONS_SRT).is_file() else []
    gaps = [(int(o["a"]) / SEQ_FPS, int(o["b"]) / SEQ_FPS) for o in ov]
    return score(name, load_captions(key_srt), key_tl, got, got_tl, gaps)


def for_run(comp_path: str | Path, comp_hash: str, run_dir: str | Path,
            cases_dir: str | Path | None = None) -> tuple[str, Score] | None:
    """(case name, score) when the run's competitor is a test video with an answer key (the same file content:
    tests/real/<case>/competitor.mp4 and answer.srt), else None. Only a case whose competitor has the run's file
    size is hashed."""
    from . import testcases
    from .common import file_hash
    size = Path(comp_path).stat().st_size
    root = Path(cases_dir) if cases_dir else testcases.CASES_DIR
    for case in testcases.cases(root=root):
        if case.has_key and case.competitor.stat().st_size == size and file_hash(case.competitor) == comp_hash:
            return case.name, score_run(case.name, case.answer_srt, testcases.answer_timeline(case), run_dir)
    return None


def summary_lines(name: str, sc: Score, examples: int = 3) -> list[str]:
    """The end summary's caption score: the score, then each type of difference with a few examples."""
    out = [f"{sc.line()} -- answer key tests/real/{name}/answer.srt"]
    for kind in sc.by_type():
        rows = [d for d in sc.diffs if d["type"] == kind]
        ex = "; ".join((f"yours '{d['key']}' @{d['key_start']:.2f}s" if "key" in d else "") +
                       (f" / mine '{d['got']}'" + (f" ({d['off_frames']:+.0f} frames)" if "off_frames" in d else "")
                        if "got" in d else "") for d in rows[:examples])
        out.append(f"{kind} ({len(rows)}): {ex}")
    return out
