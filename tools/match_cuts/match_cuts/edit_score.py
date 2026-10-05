"""edit_score.py: the cut score -- how many of the user's cut points another edit reproduces, and how much longer or
shorter that edit is (Task 8).

An edit is a list of pieces of its time line, each playing the RAW from a RAW second at a speed, or other footage
(another video, B-roll, a placeholder). A **cut** is where the RAW the edit plays jumps: the RAW second the picture
leaves (``out``) and the one it cuts to (``into``), either of them None next to other footage. The user's cut is
**reproduced** when the other edit has a cut with both RAW moments within CUT_FRAMES frames of the user's video
(compared by the RAW, so the two edits may run at other times and in another order); **near** when both are within
NEAR_S (the same cut, trimmed otherwise).

Edits come from a run's cut list (the competitor's edit, or the user's finished video matched the same way: every
frame of it against the RAW), from an XML (the tool's 1_edit.xml, or the user's finished XML) or from an answer key's
answer_edit.json ({"audio" | "pieces": [{start, end, kind, src_in, speed}], "track": "picture" | "sound"}).
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from fractions import Fraction
from pathlib import Path
from typing import Any, Sequence

CUT_FRAMES = 2              # a cut reproduced: both RAW moments within this many frames of the user's video
NEAR_S = 0.5                # ... near: both within this (the same cut, another trim)
JUMP_RAW_FRAMES = 1.5       # the RAW jumps (a cut) by more than this many RAW frames
GLITCH_FRAMES = 2           # other footage this short between two pieces that continue each other: no cut


@dataclass
class Piece:
    """[t0, t1) seconds of the edit playing the RAW from second ``raw`` at ``speed`` (``raw`` None: other footage).
    ``view``: the part of the RAW the picture shows, (centre x, centre y, width) as fractions of the RAW's width and
    height (None: unknown / a sound piece)."""
    t0: float
    t1: float
    raw: float | None
    speed: float = 1.0
    view: tuple[float, float, float] | None = None

    def raw_at(self, t: float) -> float | None:
        return None if self.raw is None else self.raw + (t - self.t0) * self.speed

    @property
    def raw_end(self) -> float | None:
        return self.raw_at(self.t1)


@dataclass
class Cut:
    t: float                    # the edit's second the cut is at
    out: float | None           # the RAW second the picture leaves (None: other footage before)
    into: float | None          # the RAW second it cuts to (None: other footage after)


@dataclass
class Edit:
    pieces: list[Piece]
    fps: float                  # the edit's frame rate (its frames are the unit of CUT_FRAMES)
    raw_fps: float              # the RAW's
    what: str = ""

    @property
    def duration(self) -> float:
        return max((p.t1 for p in self.pieces), default=0.0)

    def raw_pieces(self) -> list[Piece]:
        return [p for p in self.pieces if p.raw is not None]

    def cuts(self) -> list[Cut]:
        """Where the RAW it plays jumps (module docstring); a piece of other footage of at most GLITCH_FRAMES frames
        between two pieces that continue each other is no cut (an uncertain frame of the analysis)."""
        ps = sorted((p for p in self.pieces if p.t1 > p.t0), key=lambda p: (p.t0, p.t1))
        tol = JUMP_RAW_FRAMES / self.raw_fps
        out: list[Cut] = []
        i = 0
        while i < len(ps) - 1:
            p, q = ps[i], ps[i + 1]
            if p.raw is not None and q.raw is None:
                j = i + 1                            # the other footage after p, and the RAW piece after it
                while j < len(ps) and ps[j].raw is None:
                    j += 1
                gap = ps[j].t0 - p.t1 if j < len(ps) else math.inf
                if j < len(ps) and gap <= GLITCH_FRAMES / self.fps + 1e-6 and \
                        abs(p.raw_at(ps[j].t0) - ps[j].raw) <= tol + abs(p.speed) * gap:
                    i = j                            # a glitch: the RAW runs on through it
                    continue
                out.append(Cut(p.t1, p.raw_end, None))
                if j < len(ps):
                    out.append(Cut(ps[j].t0, None, ps[j].raw))
                i = j
                continue
            if p.raw is None and q.raw is not None:
                out.append(Cut(q.t0, None, q.raw))
            elif p.raw is not None and q.raw is not None:
                gap = max(0.0, q.t0 - p.t1)
                if gap > 1e-6:                       # nothing between them: other footage
                    out.append(Cut(p.t1, p.raw_end, None))
                    out.append(Cut(q.t0, None, q.raw))
                elif abs(p.raw_end - q.raw) > tol:
                    out.append(Cut(q.t0, p.raw_end, q.raw))
            i += 1
        return out

    # ---- sources ----

    @classmethod
    def from_cutlist(cls, cl: dict | str | Path) -> "Edit":
        """The edit a run matched (cutlist.json): every segment of the RAW at its speed (a time remap at its mean
        speed), the rest as other footage; with the part of the RAW its picture shows."""
        if not isinstance(cl, dict):
            cl = json.loads(Path(cl).read_text(encoding="utf-8"))
        from .model import parse_fps
        fps = float(parse_fps(str(cl["competitor"]["fps"])))
        raw_fps = float(parse_fps(str(cl["raw"]["fps"])))
        rw, rh = float(cl["raw"].get("width") or 0), float(cl["raw"].get("height") or 0)
        box = (cl.get("layout") or {}).get("box")
        pieces = []
        for s in sorted(cl.get("segments") or [], key=lambda s: (int(s["comp_in"]), int(s["id"]))):
            a, b = int(s["comp_in"]), int(s["comp_out"])
            if b <= a:
                continue
            t0, t1 = a / fps, b / fps
            keys = sorted(s.get("time_remap_keys") or [], key=lambda k: float(k["comp_frame"]))
            if s.get("type") == "raw" and len(keys) >= 2:
                k0, k1 = keys[0], keys[-1]
                dt = (float(k1["comp_frame"]) - float(k0["comp_frame"])) / fps
                v = (float(k1["raw_seconds"]) - float(k0["raw_seconds"])) / dt if dt > 0 else 1.0
                r = float(k0["raw_seconds"]) + v * (t0 - float(k0["comp_frame"]) / fps)
            elif s.get("type") == "raw" and s.get("raw_in_seconds") is not None:
                v, r = float(s.get("speed") if s.get("speed") is not None else 1.0), float(s["raw_in_seconds"])
            else:
                pieces.append(Piece(t0, t1, None))
                continue
            pieces.append(Piece(t0, t1, r, v, _view(s.get("transform"), s.get("box") or box, rw, rh)))
        return cls(pieces, fps, raw_fps, "cut list")

    @classmethod
    def from_xml(cls, xml: str | Path, track: str = "picture") -> "Edit":
        """An FCP7 / Premiere XML's clips of the RAW (the tool's 1_edit.xml, or a finished XML as Premiere exports it:
        the sequence inside a project): V1 (``track`` 'picture') or A1 ('sound'), each clip's in / out in its own
        rate; the gaps between them as other footage."""
        import xml.etree.ElementTree as ET
        from .export_xml_edl import _remap_speed, plan_in_out

        def rate(el, default: float) -> float:
            tb = el.findtext("rate/timebase") if el is not None else None
            if not tb:
                return default
            return float(tb) * (1000.0 / 1001.0 if str(el.findtext("rate/ntsc") or "").upper() == "TRUE" else 1.0)
        root = ET.parse(str(xml)).getroot()
        seqs = [root] if root.tag == "sequence" else list(root.iter("sequence"))
        if not seqs:
            raise ValueError(f"{xml}: no <sequence>")
        seq = max(seqs, key=lambda q: len(list(q.iter("clipitem"))))
        fps = rate(seq, 30.0)
        tr = seq.find("media/video/track" if track == "picture" else "media/audio/track")
        pieces, raw_fps = [], None
        for el in sorted((tr.findall("clipitem") if tr is not None else []), key=lambda e: int(e.findtext("start") or -1)):
            a, b = int(el.findtext("start") or -1), int(el.findtext("end") or -1)
            if not 0 <= a < b:
                continue
            cf = rate(el, fps)
            raw_fps = raw_fps or rate(el.find("file"), cf)          # the media's own rate, when the file says
            sp = _remap_speed(el)
            i0, i1 = plan_in_out(int(el.findtext("in") or 0), int(el.findtext("out") or 0), sp < 0)
            t0, t1 = a / fps, b / fps
            v = (i1 - i0) / cf / (t1 - t0) if sp >= 0 else float(sp)
            pieces.append(Piece(t0, t1, i0 / cf, v))
        out, t = [], 0.0
        for p in pieces:
            if p.t0 > t + 1e-6:
                out.append(Piece(t, p.t0, None))
            out.append(p)
            t = max(t, p.t1)
        dur = float(seq.findtext("duration") or 0) / fps
        if dur > t + 1e-6:
            out.append(Piece(t, dur, None))
        return cls(out, fps, raw_fps or fps, f"XML ({track})")

    @classmethod
    def from_answer(cls, path: str | Path, raw_fps: float | None = None) -> "Edit":
        """An answer key's timeline: answer_edit.json (its "track": picture / sound) or answer_edit.xml (the user's
        finished XML: its picture clips of the RAW, else its sound)."""
        path = Path(path)
        if path.suffix.lower() == ".xml":
            e = cls.from_xml(path, "picture")
            if not e.raw_pieces():
                e = cls.from_xml(path, "sound")
            return e
        d = json.loads(path.read_text(encoding="utf-8"))
        rows = d.get("pieces") or d.get("audio") or []
        pieces = [Piece(float(r["start"]), float(r["end"]),
                        float(r["src_in"]) if str(r.get("kind") or "raw") == "raw" else None,
                        float(r.get("speed") or 1.0)) for r in rows]
        fps = float(d.get("fps") or 30.0)
        return cls(sorted(pieces, key=lambda p: p.t0), fps, float(d.get("raw_fps") or raw_fps or fps),
                   str(d.get("track") or "sound"))


def _view(tr: dict | None, box: Any, rw: float, rh: float) -> tuple[float, float, float] | None:
    """The part of the RAW (rw x rh) a picture box shows under transform ``tr`` (p' = s R p + t, corner convention):
    (centre x, centre y, width) as fractions of the RAW."""
    if not tr or not rw or not rh:
        return None
    try:
        s, th = float(tr["scale"]), math.radians(float(tr.get("rotation_deg") or 0.0))
        tx, ty = float(tr["tx"]), float(tr["ty"])
        if isinstance(box, dict):
            bx, by, bw, bh = (float(box[k]) for k in ("x", "y", "w", "h"))
        elif isinstance(box, (list, tuple)) and len(box) == 4:
            bx, by, bw, bh = (float(v) for v in box)
        else:
            return None
    except (KeyError, TypeError, ValueError):
        return None
    if s <= 0:
        return None
    c, si = math.cos(th), math.sin(th)

    def back(x: float, y: float) -> tuple[float, float]:
        u, w = (x - tx) / s, (y - ty) / s
        return c * u + si * w, -si * u + c * w
    x0, y0 = back(bx, by)
    x1, y1 = back(bx + bw, by + bh)
    lo_x, hi_x = max(0.0, min(x0, x1)), min(rw, max(x0, x1))
    lo_y, hi_y = max(0.0, min(y0, y1)), min(rh, max(y0, y1))
    if hi_x <= lo_x or hi_y <= lo_y:
        return None
    return (round((lo_x + hi_x) / 2 / rw, 4), round((lo_y + hi_y) / 2 / rh, 4), round((hi_x - lo_x) / rw, 4))


# ---------------------------------------------------------------------------------------------------------------------
# the score
# ---------------------------------------------------------------------------------------------------------------------

@dataclass
class CutScore:
    cuts: int = 0                                   # the user's cuts
    reproduced: int = 0                             # ... the other edit has within CUT_FRAMES
    near: int = 0                                   # ... within NEAR_S (another trim)
    others: int = 0                                 # the other edit's cuts the user does not make
    length_key: float = 0.0                         # seconds
    length_got: float = 0.0
    tol_s: float = 0.0
    misses: list[dict] = field(default_factory=list)
    trims: list[dict] = field(default_factory=list)     # every cut both make: {t, out, into} = the user's RAW moment
    #                                                     minus the other edit's (out < 0: the user leaves earlier;
    #                                                     into > 0: the user comes in later)

    @property
    def length_diff(self) -> float:
        return self.length_got - self.length_key

    def to_dict(self) -> dict:
        return {"cuts": self.cuts, "reproduced": self.reproduced, "near": self.near, "others": self.others,
                "reproduced_pct": round(100.0 * self.reproduced / self.cuts, 1) if self.cuts else 0.0,
                "length_key": round(self.length_key, 3), "length_got": round(self.length_got, 3),
                "length_diff": round(self.length_diff, 3),
                "length_diff_pct": round(100.0 * self.length_diff / self.length_key, 1) if self.length_key else 0.0,
                "tol_s": round(self.tol_s, 4), "misses": self.misses, "trims": self.trims,
                "out_median": _median([t["out"] for t in self.trims if t["out"] is not None]),
                "into_median": _median([t["into"] for t in self.trims if t["into"] is not None])}

    def line(self) -> str:
        d = self.to_dict()
        return (f"{self.reproduced}/{self.cuts} of your cuts within {CUT_FRAMES} frames ({d['reproduced_pct']:.0f} %)"
                f"{f', {self.near} more trimmed otherwise' if self.near else ''}; the edit is "
                f"{abs(self.length_diff):.1f} s {'longer' if self.length_diff >= 0 else 'shorter'} "
                f"({d['length_diff_pct']:+.0f} %) than yours")


def _median(v: Sequence[float]) -> float | None:
    if not v:
        return None
    v = sorted(v)
    n = len(v)
    return round(v[n // 2] if n % 2 else 0.5 * (v[n // 2 - 1] + v[n // 2]), 4)


def _sides(c: Cut) -> tuple[bool, bool]:
    return c.out is not None, c.into is not None


def _gap(a: Cut, b: Cut) -> float:
    """How far apart two cuts are: the larger of their RAW moments' distances (inf: other sides)."""
    if _sides(a) != _sides(b):
        return math.inf
    d = 0.0
    if a.out is not None:
        d = max(d, abs(a.out - b.out))
    if a.into is not None:
        d = max(d, abs(a.into - b.into))
    return d


def match(key: Sequence[Cut], got: Sequence[Cut], tol: float) -> list[tuple[int, int, float]]:
    """One-to-one pairs (key index, got index, distance) within ``tol``, the closest pairs first."""
    pairs = sorted((_gap(k, g), i, j) for i, k in enumerate(key) for j, g in enumerate(got) if _gap(k, g) <= tol)
    used_k, used_g, out = set(), set(), []
    for d, i, j in pairs:
        if i in used_k or j in used_g:
            continue
        used_k.add(i)
        used_g.add(j)
        out.append((i, j, d))
    return out


def score(key: Edit, got: Edit, frames: int = CUT_FRAMES) -> CutScore:
    """The other edit ``got`` against the user's ``key`` (module docstring)."""
    tol = frames / key.fps + 1e-6
    kc, gc = key.cuts(), got.cuts()
    exact = match(kc, gc, tol)
    hit_k = {i for i, _j, _d in exact}
    hit_g = {j for _i, j, _d in exact}
    rest_k = [i for i in range(len(kc)) if i not in hit_k]
    rest_g = [j for j in range(len(gc)) if j not in hit_g]
    near = match([kc[i] for i in rest_k], [gc[j] for j in rest_g], NEAR_S)
    near_k = {rest_k[i] for i, _j, _d in near}
    near_g = {rest_g[j] for _i, j, _d in near}
    misses = []
    for i in rest_k:
        c = kc[i]
        m = next((rest_g[j] for a, j, _d in near if rest_k[a] == i), None)
        g = gc[m] if m is not None else None
        misses.append({"t": round(c.t, 3), "out": None if c.out is None else round(c.out, 3),
                       "into": None if c.into is None else round(c.into, 3),
                       "got_out": None if g is None or g.out is None else round(g.out, 3),
                       "got_into": None if g is None or g.into is None else round(g.into, 3)})
    trims = []
    for i, j in sorted([(i, j) for i, j, _d in exact] + [(rest_k[a], rest_g[b]) for a, b, _d in near]):
        k, g = kc[i], gc[j]
        trims.append({"t": round(k.t, 3), "out": None if k.out is None else round(k.out - g.out, 4),
                      "into": None if k.into is None else round(k.into - g.into, 4)})
    return CutScore(cuts=len(kc), reproduced=len(exact), near=len(near_k),
                    others=len([j for j in range(len(gc)) if j not in hit_g and j not in near_g]),
                    length_key=key.duration, length_got=got.duration, tol_s=tol, misses=misses, trims=trims)


def for_run(comp_path: str | Path, comp_hash: str, run_dir: str | Path,
            cases_dir: str | Path | None = None) -> tuple[str, CutScore] | None:
    """(case name, cut score) when the run's competitor is a test video whose answer key is the user's own edit (the
    same file content), else None."""
    from . import testcases
    from .common import file_hash
    size = Path(comp_path).stat().st_size
    root = Path(cases_dir) if cases_dir else testcases.CASES_DIR
    for case in testcases.cases(root=root):
        if case.has_cut_key and case.competitor.stat().st_size == size and file_hash(case.competitor) == comp_hash:
            return case.name, score_run(case.answer_edit, run_dir)
    return None


def score_run(answer_edit: str | Path, run_dir: str | Path) -> CutScore:
    """A run folder's 1_edit.xml against an answer key's timeline (its track: picture or sound)."""
    from .run_folders import EDIT_XML
    key = Edit.from_answer(answer_edit)
    got = Edit.from_xml(Path(run_dir) / EDIT_XML, "sound" if key.what == "sound" else "picture")
    if key.raw_fps == key.fps and got.raw_fps:
        key.raw_fps = got.raw_fps
    return score(key, got)
