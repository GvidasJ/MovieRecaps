"""7 Nov (run 21's task 1, done now -- your call: "accurate as fast as possible"): MATCH ON THE NORMAL RAW, PLAY THE
ENHANCED PICTURE.

Run 20 matched the edit ON Topaz's 60 fps piece. Apollo makes new in-between frames, so many competitor frames have no
exact frame there: the last 33 enhanced edits had a median of 19 cuts per 10 s (1-3 frame clips), and 8 of 33 passed.
Now the edit is matched on the normal RAW, exactly as without enhance (flow.edit_job), and does not wait for Topaz;
when the enhanced piece is back (core.enhance.watch / take_back) and the edit is finished, ``swap_in`` makes the
finished 1_edit.xml play it:

* every V1 clip whose RAW frames lie inside the enhanced piece plays enhance\\raw_enhanced.mp4 at the same moment --
  enhanced time = RAW time - the piece's start (its _TOENHANCE.json: start_frame, fps) -- on the clip's own scale (its
  rate and speed: a shift that is not whole keeps that clip on the normal RAW); a RAW clip of an edit that plays only
  the scene (extras\\keep_piece.json) is first put back on the movie's frames;
* its framing stays: its position and scale halved (the enhanced picture has 2x the pixels -- Premiere reads both
  in the clip's own pixels);
* a clip partly outside the piece keeps the normal RAW; A1 (the sound) is not touched -- in sync, as matched;
* checked before anything changes: three swapped clips show the normal RAW's picture at the mapped moment (gray,
  small, closer than half a second before or after) -- else nothing changes and the edit stays on the normal RAW;
* the edit as it was matched stays in extras\\1_edit_normal.xml (swapping again starts from it).

18 Nov: Settings' "Match on the normal RAW" switch is gone (off was run 20: the edit waited for the piece and was
matched on it) -- this is the only way now. Everything here reads files and decodes frames: call it from a worker
thread."""
from __future__ import annotations

import copy
import json
import math
import os
import shutil
import time
import xml.etree.ElementTree as ET
from fractions import Fraction
from pathlib import Path

NORMAL_XML = "1_edit_normal.xml"
CHECK_MIN = 0.55                    # the mapped frame's correlation with the normal RAW's (gray, 160 px) at least
CHECK_MARGIN = 0.02                 # ... and not worse than half a second before / after by more than this
MAX_REDOS = 3                       # 7 Nov: a video's piece goes to Topaz again at most this often (a failure, clips
                                    #   left on the normal RAW) -- your call: no normal RAW in the timeline


def _rate(el) -> Fraction | None:
    if el is None or el.find("timebase") is None:
        return None
    tb = int(el.findtext("timebase") or 0)
    return Fraction(tb * 1000, 1001) if str(el.findtext("ntsc") or "").upper() == "TRUE" else Fraction(tb)


def _speed(ci) -> Fraction:
    try:
        from match_cuts.excerpt import _clip_speed
        return Fraction(str(round(float(_clip_speed(ci)), 6))).limit_denominator(100000)
    except Exception:  # noqa: BLE001
        return Fraction(1)


def _groups(run: Path) -> list[dict]:
    try:
        d = json.loads((run / "extras" / "keep_piece.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return _piece_group(run)
    if not d.get("kept"):
        return _piece_group(run)
    return [g for g in (d.get("groups") or []) if isinstance(g, dict)] if d.get("kept") else []


def _groups_for(run: Path, info: dict | None) -> list[dict]:
    """``_groups`` for the piece Topaz has (``info``, its TOENHANCE.json). A piece made by ``cover_all`` before 17 Nov
    from an edit that plays only the movie's piece counts that piece's frames (no "units" in it): no group then."""
    g = _groups(run)
    if g and info and info.get("units") != "movie" and str(info.get("project") or "").endswith(("_all", "_all2")) \
            and g == _piece_group(run):
        return []
    return g


def _piece_group(run: Path) -> list[dict]:
    """17 Nov (The Biggest Meltdown, The Worst Prank: Topaz ran twice): the run analysed a piece of the movie and its
    edit could not be moved onto the movie (extras/excerpt.json: moved false) -- 1_edit.xml then counts the PIECE's
    frames, which are the movie's frames minus the piece's start (k0). Said as one group, so the piece Topaz enhanced
    before the edit (cut from the movie, in the movie's frames) is still found and used."""
    try:
        d = json.loads((run / "extras" / "excerpt.json").read_text(encoding="utf-8"))
        mv = d.get("moved")
        if not isinstance(mv, dict) or mv.get("moved") is not False:
            return []
        k0, n = int(d["k0"]), int(d["frames"])
    except (OSError, ValueError, KeyError, TypeError):
        return []
    return [{"k0": k0, "k1": k0 + n, "offset": 0}] if n > 0 else []


def to_movie(frame: Fraction | int, groups: list[dict]) -> Fraction | None:
    """A frame of the RAW 1_edit.xml plays -> the movie's frame (the scene's piece: keep_piece's groups)."""
    if not groups:
        return Fraction(frame)
    for g in groups:
        off, n = int(g["offset"]), int(g["k1"]) - int(g["k0"])
        if off <= frame <= off + n:
            return Fraction(frame) + int(g["k0"]) - off
    return None


def _v1(root) -> list:
    seq = root.find(".//sequence")
    out = []
    for tr in (seq.findall("media/video/track") if seq is not None else []):
        for c in tr.findall("clipitem"):
            f = c.find("file")
            if f is not None and f.get("id") == "file-raw":
                out.append(c)
    return out


def _gray(path: Path, fps: Fraction, frames: list[int]) -> dict:
    import cv2
    from match_cuts.media import VideoReader
    with VideoReader(str(path), fps=fps) as r:
        got = r.get_many(sorted({f for f in frames if f >= 0}), fmt="gray")
    out = {}
    for k, a in got.items():
        if a is None:
            continue
        h, w = a.shape[:2]
        out[k] = cv2.resize(a, (160, max(8, int(round(h * 160 / max(1, w))))), interpolation=cv2.INTER_AREA)
    return out


def _zncc(a, b) -> float:
    import numpy as np
    if a is None or b is None or a.shape != b.shape:
        return -1.0
    x, y = a.astype(np.float64).ravel(), b.astype(np.float64).ravel()
    x, y = x - x.mean(), y - y.mean()
    d = math.sqrt(float((x * x).sum()) * float((y * y).sum()))
    return float((x * y).sum()) / d if d > 0 else -1.0


def check(normal_media: Path, raw_fps: Fraction, enhanced: Path, pairs: list[tuple[int, Fraction]]) -> dict:
    """``pairs``: (the normal media's frame, the enhanced piece's time in s) in the middle of up to three swapped
    clips -- the mapped enhanced frame looks like the normal one, and is not beaten by half a second before or after.
    {ok, rows, why}."""
    from .enhance import OUT_FPS
    want = {int(round(float(t) * OUT_FPS)) for _f, t in pairs}
    alt = {e + d for e in want for d in (-OUT_FPS // 2, OUT_FPS // 2)}
    norm = _gray(normal_media, raw_fps, [f for f, _t in pairs])
    enh = _gray(enhanced, Fraction(OUT_FPS), sorted(want | alt))
    rows, bad = [], []
    for f, t in pairs:
        e = int(round(float(t) * OUT_FPS))
        a = norm.get(f)
        s = _zncc(a, enh.get(e))
        others = [_zncc(a, enh.get(x)) for x in (e - OUT_FPS // 2, e + OUT_FPS // 2) if enh.get(x) is not None]
        rows.append({"normal_frame": f, "enhanced_frame": e, "zncc": round(s, 4),
                     "half_second_off": [round(x, 4) for x in others]})
        if s < CHECK_MIN or any(x > s + CHECK_MARGIN for x in others):
            bad.append(f"the enhanced frame {e} does not show the RAW's frame {f} (ZNCC {s:.2f})")
    return {"ok": not bad and bool(rows), "rows": rows, "why": "; ".join(bad) or ("" if rows else "nothing read")}


CUT_Z = 0.5                         # two RAW frames in a row that look this little alike: a shot change between them
END_MARGIN = 0.05                   # a clip's end frame must look this much more like its own RAW frame than the
#                                     next shot's (its first frame: than the shot before's)
END_MIXED = 0.15                    # ... and may look at most this much more like the other shot than its own RAW
#                                     frame does (a picture mixed of both shots)
END_SHIFTS = (0, -1, -2, 1, 2)      # the clip's source moved by this many 60 fps frames, the smallest that fits


def _int(el, tag: str) -> int | None:
    try:
        return int(el.findtext(tag))
    except (TypeError, ValueError):
        return None


def _fit_ends(plan: list, normal_media: Path, raw_fps: Fraction, enhanced: Path, lims: list) -> tuple[dict, list]:
    """19 Nov (the first whole video through the REAL Topaz, The Best Business Idea In Key and Peele (2): 5 of its
    55 clips ended with ONE frame of the next shot -- a flash frame nobody had put there). Premiere shows the normal
    RAW's frame that is on at a moment (the one before, until the next begins); Topaz's 60 fps picture shows the
    NEAREST RAW frame (Apollo makes the in-between pictures; across a shot change it jumps half a RAW frame early).
    A clip that ends on the RAW's shot change -- most do, the tool cuts there -- so showed the next shot in its last
    1/60 s whenever that frame lay in the second half of the RAW frame. The fake Topaz of the tests holds each frame
    like Premiere, so no test saw it.

    For every take the edit plays in one go (clips that follow each other in the source: they move together), when
    the normal RAW has a shot change right before its first frame or right after its last: the enhanced frame at
    that end must look like the take's own RAW frame, not like the other shot's. If not, the take's source is moved
    by a 60 fps frame or two (``END_SHIFTS``; under a RAW frame -- nothing else changes, the cuts stay where they
    are). ``plan``: swap_in's (..., a, b last); ``lims``: per clip the enhanced frames it may use (its part).
    Returns ({plan index: frames moved}, [what could not be fitted])."""
    from .enhance import OUT_FPS
    groups: list[list[int]] = []
    cur: list[int] = []
    for i, pl in enumerate(plan):
        c, na, rate, k = pl[0], pl[1], pl[5], pl[6]
        if rate != OUT_FPS or k != raw_fps / OUT_FPS:      # (only a 60 fps sequence's clips at 100 %)
            if cur:
                groups.append(cur)
            cur = []
            continue
        if cur:
            q = plan[cur[-1]]
            if q[2] == na and _int(q[0], "end") is not None and _int(q[0], "end") == _int(c, "start") \
                    and lims[cur[-1]] == lims[i]:
                cur.append(i)
                continue
            groups.append(cur)
        cur = [i]
    if cur:
        groups.append(cur)
    info = []
    for g in groups:
        first, last = plan[g[0]], plan[g[-1]]
        f0 = int(math.floor(first[8] * first[6]))          # the normal media's first frame the take shows
        f1 = int(math.floor((last[9] - 1) * last[6]))      # ... and its last
        info.append((g, f0, f1, int(first[1]), int(last[2])))
    norm = _gray(normal_media, raw_fps, sorted({x for _g, f0, f1, _a, _b in info for x in (f0 - 1, f0, f1, f1 + 1)}))

    def cut(a: int, b: int) -> bool:
        return norm.get(a) is not None and norm.get(b) is not None and _zncc(norm[a], norm[b]) < CUT_Z
    todo = [(g, f0, f1, na, nb, cut(f0 - 1, f0), cut(f1, f1 + 1)) for g, f0, f1, na, nb in info]
    todo = [t for t in todo if t[5] or t[6]]
    if not todo:
        return {}, []
    want: set = set()
    for _g, _f0, _f1, na, nb, before, after in todo:
        want |= {na + m for m in END_SHIFTS} if before else set()
        want |= {nb - 1 + m for m in END_SHIFTS} if after else set()
    enh = _gray(enhanced, Fraction(OUT_FPS), sorted(x for x in want if x >= 0))

    def more_like(e: int, own: int, other: int) -> bool:
        import cv2
        pic, a, b = enh.get(e), norm.get(own), norm.get(other)
        if pic is None or a is None or b is None:
            return False
        if pic.shape != a.shape:                           # (a stretched RAW: the enhanced picture has square pixels)
            pic = cv2.resize(pic, (a.shape[1], a.shape[0]), interpolation=cv2.INTER_AREA)
        # like its own frame, and no more like the other shot than its own frame is (Topaz sometimes mixes the two
        # shots in the frame at a change: The Best Business Idea (2), clip 14 -- 0.75 its own, 0.50 the next shot)
        return _zncc(pic, a) > _zncc(pic, b) + END_MARGIN and _zncc(pic, b) < _zncc(a, b) + END_MIXED
    shifts: dict[int, int] = {}
    left: list[str] = []
    for g, f0, f1, na, nb, before, after in todo:
        lo, hi = lims[g[0]]

        def fits(m: int) -> bool:
            if na + m < lo or nb + m > hi:
                return False
            return (not before or more_like(na + m, f0, f0 - 1)) and (not after or more_like(nb - 1 + m, f1, f1 + 1))
        m = next((m for m in END_SHIFTS if fits(m)), None)
        if m is None:
            which = "first" if before and not more_like(na, f0, f0 - 1) else "last"
            left.append(f"the clip at {_int(plan[g[0]][0], 'start')}-{_int(plan[g[-1]][0], 'end')}: its {which} "
                        f"frame shows the shot next to it")
        elif m:
            for i in g:
                shifts[i] = m
    return shifts, left


def _same(a: Path, b: Path) -> bool:
    """The same video: one file (a hard link) or the same size."""
    try:
        return os.path.samefile(a, b) or a.stat().st_size == b.stat().st_size
    except OSError:
        return False


def _media(root, fid: str) -> Path | None:
    from .share_zip import _path_of_url, reach
    for f in root.iter("file"):
        if f.get("id") == fid and f.findtext("pathurl"):
            p = _path_of_url((f.findtext("pathurl") or "").strip())
            return reach(p) if p is not None else None
    return None


def edit_made(folder, ed) -> bool:
    """18 Nov ("it must not stop"): the edit Topaz may work on -- a finished one (PASS / OK with a note), and also one
    marked FAIL whose 1_edit.xml is there: you open those in Premiere too, and until now such a video never got its
    Topaz picture (everything here wanted a "done" edit), so the normal RAW stayed in its timeline."""
    from . import projects as PJ
    from .share_zip import run_dir_of
    if not isinstance(ed, dict):
        return False
    if ed.get("state") == PJ.DONE:
        return True
    try:
        return ed.get("state") == PJ.FAILED and bool(ed.get("run_dir")) and             (run_dir_of(Path(folder), ed) / "1_edit.xml").is_file()
    except (OSError, TypeError, ValueError):
        return False


COVER_MARGIN_S = 10.0                  # the piece cut after the edit: this much before its first clip and after its last
#                                        (room to make a clip longer in Premiere with the enhanced picture)


INNER_MARGIN_S = 2.0                   # 18 Nov: at a gap INSIDE the edit (the clips jump over a part of the RAW) this
#                                        much is enhanced on each side of the gap (the outer ends keep COVER_MARGIN_S)
MIN_GAP_S = 6.0                        # a part of the RAW nothing plays is only left out when it is longer than this
#                                        (after the margins): each part enhanced on its own costs Topaz a start
MAX_RANGES = 12
RANGES_MIN_SAVED = 0.20                # ... and only when at least this share of the piece, and RANGES_MIN_SAVED_S
RANGES_MIN_SAVED_S = 20.0              #   seconds of it, need no Topaz at all
RANGES_ENV = "MOVIERECAPS_TOPAZ_RANGES"   # "0": the whole piece is enhanced, as before 18 Nov


NOT_LINED_UP = "the enhanced piece does not line up with the edit"     # swap_in's own look at the frames said no


def play_ranges(every: list[tuple[int, int]], fps: Fraction, start: int, frames: int) -> list[list[int]]:
    """18 Nov (The Best Business Idea In Key and Peele (2): 57 s of clips spread over 3:04 of the RAW -- Topaz enhanced
    a 3:24 piece, about an hour, of which the edit plays under a third): the parts of the piece the edit plays, as
    [first frame, frame after the last] in the PIECE's own frames -- COVER_MARGIN_S before the first clip and after
    the last (as before), INNER_MARGIN_S around each clip at a gap inside the edit, parts closer than MIN_GAP_S
    joined. What the cut added before and after that to reach the RAW's keyframes (enhance._span_plan) is left out
    too. Leaving a gap out must be worth another start of Topaz (RANGES_MIN_SAVED of the piece and
    RANGES_MIN_SAVED_S): else the one part from 10 s before the first clip to 10 s after the last. [] when that is
    the whole piece (or nothing can be said): the whole piece is enhanced, as before. ``every``: the clips' frames
    in the movie; ``start`` / ``frames``: the piece in the movie's frames."""
    if os.environ.get(RANGES_ENV, "").strip() == "0" or frames <= 0 or not every:
        return []
    f = float(fps)
    pad, gap, outer = int(round(INNER_MARGIN_S * f)), int(round(MIN_GAP_S * f)), int(round(COVER_MARGIN_S * f))
    spans = sorted((max(0, int(a) - start), min(frames, int(b) - start)) for a, b in every)
    spans = [(a, b) for a, b in spans if b > a]
    if not spans:
        return []
    lo, hi = max(0, spans[0][0] - outer), min(frames, max(b for _a, b in spans) + outer)
    out: list[list[int]] = []
    for a, b in spans:
        a, b = max(lo, a - pad), min(hi, b + pad)
        if out and a - out[-1][1] <= gap:
            out[-1][1] = max(out[-1][1], b)
        else:
            out.append([a, b])
    while len(out) > MAX_RANGES:                           # too many parts: the two nearest are joined
        i = min(range(len(out) - 1), key=lambda j: out[j + 1][0] - out[j][1])
        out[i][1] = out[i + 1][1]
        del out[i + 1]
    out[0][0], out[-1][1] = lo, hi                         # the outer ends: 10 s more each side, as before
    gaps = sum(out[i + 1][0] - out[i][1] for i in range(len(out) - 1))
    if len(out) > 1 and (gaps < RANGES_MIN_SAVED * frames or gaps < RANGES_MIN_SAVED_S * f):
        out = [[lo, hi]]                                   # the gaps are not worth another start of Topaz
    if out == [[0, frames]]:
        return []
    return out


def segments(info: dict | None) -> list[tuple[int, int, int]]:
    """The parts of a piece enhanced on their own (``info``: the piece's json with "ranges"), as (first frame, frame
    after the last, the part's first frame in the enhanced file) -- the first two in the piece's own frames, the
    third in the enhanced file's 60 fps frames. [] for a piece enhanced whole."""
    from .enhance import range_out_frames, valid_ranges
    rs = valid_ranges(info or {})
    out, at = [], 0
    for a, b in rs:
        out.append((a, b, at))
        at += range_out_frames(b - a, (info or {}).get("fps"))
    return out


def outside(project) -> dict:
    """7 Nov (your call: "everything must be in enhanced"): the V1 clips of the finished edit (as matched: on the
    normal RAW) whose frames are not in the piece sent to Topaz. {"need": [(movie frame a, b) ...], "run", "media",
    "groups", "fps", "start", "frames"} -- {} when there is nothing to look at."""
    from . import projects as PJ
    from .share_zip import run_dir_of
    p = PJ.load(project)
    rec = p.data.get("enhance") if isinstance(p.data.get("enhance"), dict) else {}
    info = rec.get("piece") if isinstance(rec.get("piece"), dict) else {}
    if rec.get("state") not in ("sent", "used", "failed", "skipped", "late"):
        return {}
    info0 = info
    if rec.get("state") in ("failed", "skipped", "late") or "start_frame" not in info or not info.get("fps"):
        info = {}                                        # no piece with Topaz: every clip needs one
    ed = (p.data.get("steps") or {}).get("edit") or {}
    if not edit_made(p.folder, ed):                      # (18 Nov: a FAIL with its 1_edit.xml too)
        return {}
    run = run_dir_of(Path(p.folder), ed)
    normal = run / "extras" / NORMAL_XML
    src = normal if normal.is_file() else run / "1_edit.xml"
    try:
        root = ET.parse(str(src)).getroot()
    except (OSError, ET.ParseError):
        return {}
    raw_def = next((f for f in root.iter("file") if f.get("id") == "file-raw" and f.find("rate") is not None), None)
    raw_fps = Fraction(str(info["fps"])) if info else (_rate(raw_def.find("rate")) if raw_def is not None else None)
    if raw_fps is None:
        return {}
    start, n = (int(info["start_frame"]), int(info.get("frames") or 0)) if info else (None, 0)
    groups = _groups_for(run, info0) if info else _groups(run)
    parts = []                                           # 18 Nov: only parts of the piece were enhanced (play_ranges)
    if info and (rec.get("state") != "used" or _was_ranged(p.folder)):
        parts = [(start + a, start + b) for a, b, _at in segments(info)]
    need, every, rates = [], [], set()
    for c in _v1(root):
        rate = _rate(c.find("rate")) or raw_fps
        rates.add(rate)
        k = _speed(c) * raw_fps / rate
        try:
            a, b = int(c.findtext("in")), int(c.findtext("out"))
        except (TypeError, ValueError):
            continue
        ma, mb = to_movie(a * k, groups), to_movie(b * k, groups)
        if ma is None or mb is None:
            continue
        every.append((int(math.floor(ma)), int(math.ceil(mb))))
        if (start is None or ma < start or mb > start + n
                or (parts and not any(pa <= ma and mb <= pb for pa, pb in parts))):
            need.append((int(math.floor(ma)), int(math.ceil(mb))))
    return {"need": need, "every": every, "run": run, "media": _media(root, "file-raw"), "groups": groups, "fps": raw_fps,
            "start": start, "frames": n, "rec": rec, "rates": rates}


def _was_ranged(folder) -> bool:
    """The enhanced piece in the project holds only the played parts (its enhance.json, written by take_back)."""
    from .enhance import PROJECT_SUB, read_info
    d = read_info(Path(folder) / PROJECT_SUB / "enhance.json") or {}
    return bool((d.get("enhanced") or {}).get("ranged")) if isinstance(d.get("enhanced"), dict) else False


def cover_all(project, say=None) -> dict:
    """7 Nov: when clips of the finished edit lie outside the piece Topaz has, a new piece goes to Topaz instead --
    from the first frame to the last one the edit plays (the old piece with them, 1 s more each side), cut from the
    file the edit plays; the old piece leaves the line when nobody enhances it yet. The swap then puts the enhanced
    picture on every clip. {ok, why, clips} -- never raises."""
    from . import enhance as E
    from . import projects as PJ
    try:
        o = outside(project)
        if not o or not o["need"]:
            return {"ok": True, "why": "every clip is in the enhanced piece" if o else "nothing to look at", "clips": 0}
        media = o["media"]
        if media is None or not Path(media).is_file():
            return {"ok": False, "why": "the RAW the edit plays is not here to cut a new piece from"}
        rec = o["rec"]
        if int(rec.get("redos") or 0) >= MAX_REDOS:
            return {"ok": False, "why": f"Topaz was tried {MAX_REDOS} times for this video"}
        if rec.get("state") == "skipped":               # never sent: only when this computer sends to Topaz
            from . import enhance as E0
            from . import settings as S0
            if not rec.get("after_edit") and not E0.sends(S0.load()):     # (17 Nov: after_edit -- its row sends)
                return {"ok": False, "why": "sending to Topaz is off in Settings"}
        fps, groups = o["fps"], o["groups"]
        lo = min(a for a, _b in o["every"])              # 8 Nov: from the edit's first clip to its last -- not the
        hi = max(b for _a, b in o["every"])              #   old piece too (a wrong guess made it 2:25 for 18 s)
        off = 0                                          # movie frames -> the frames of the file the edit plays
        if groups:
            if len(groups) > 1:
                g = [g for g in groups if int(g["k0"]) <= lo and hi <= int(g["k1"])]
                if not g:
                    return {"ok": False, "why": "the clips are in several pieces of the RAW"}
                g = g[0]
            else:
                g = groups[0]
            off = int(g["k0"]) - int(g["offset"])
            lo, hi = max(lo, int(g["k0"])), min(hi, int(g["k1"]))
        a_s = max(0.0, float(Fraction(lo - off) / fps) - COVER_MARGIN_S)   # 17 Nov (your call): 10 s before the
        b_s = float(Fraction(hi - off) / fps) + COVER_MARGIN_S             #   first clip and after the last (was 1 s)
        p = PJ.load(project)
        old = str(rec.get("name") or E.safe_name(Path(p.folder).name))
        name = E.safe_name(Path(p.folder).name) + "_all"
        if old == name:
            name = E.safe_name(Path(p.folder).name) + "_all2"
        folder = rec.get("folder") or str(E.share_dir())
        if not Path(folder).is_dir():                    # 13 Nov: the address it had then is not reachable now
            folder = str(E.share_dir())
        # 19 Nov (your call): a piece that goes again after a failure is made ANOTHER way each time (E.WAYS: the
        # RAW's own frames + only the played parts, then an encoded piece whole, then the RAW's frames whole, then
        # every frame a keyframe) -- the same piece again could fail three times for the same reason. A piece that
        # goes again because clips were left outside it (nothing failed) is made as before: a copy, whole.
        tries = [dict(t) for t in (rec.get("tries") or []) if isinstance(t, dict)]
        if rec.get("state") == "failed" and tries and not tries[-1].get("why"):
            tries[-1]["why"] = str(rec.get("why") or "it failed")       # (a failure nobody wrote down: it counts)
        first = rec.get("state") == "skipped" and not tries
        kind, want_parts = E.next_way(tries) if rec.get("state") in ("skipped", "failed") else (E.COPY, False)
        info = E.make_piece(media, name, folder, (a_s, b_s), project=p.folder,
                            topaz_on=str((rec.get("piece") or {}).get("topaz_on") or rec.get("topaz_on") or ""),
                            progress=(lambda s, t: say(s, t)) if say else None, way=kind)
        again = False
        if off or groups:                                # the piece's start in the movie's frames (swap_in maps so)
            info["start_frame"] = int(info["start_frame"]) + off
            info["start_s"] = round(float(Fraction(int(info["start_frame"])) / fps), 6)
            info["units"] = "movie"
            again = True
        # 18 Nov: only what the edit plays is enhanced (play_ranges) -- in a 60 fps sequence (the enhanced frames
        # are then found by their own number; at the RAW's rate a part's start would fall between two frames) and
        # only when this try's way says so (19 Nov: the first piece, and a later one when the other ways failed)
        ranges = []
        if (o.get("rates") == {Fraction(E.OUT_FPS)} and Fraction(str(info.get("fps") or 0)) == fps
                and want_parts):
            ranges = play_ranges(o["every"], fps, int(info["start_frame"]), int(info.get("frames") or 0))
        if ranges:
            info["ranges"] = ranges
            kept = sum(b - a for a, b in ranges)
            info["ranges_text"] = (f"{len(ranges)} parts, {E._clock(kept / float(fps))} of the piece's "   # noqa: SLF001
                                   f"{E._clock(float(info.get('duration') or 0.0))}")                      # noqa: SLF001
            again = True
        if again:
            E._write_json(E.names(folder, name).info, info)                       # noqa: SLF001
        lk = E.read_lock(E.names(folder, old).lock)
        if lk is None or E.lock_stale(E.names(folder, old).lock):
            E.clear(folder, old)                         # the old piece: nobody enhances it -- it leaves the line
        made = E.piece_way(info)
        tries.append({"n": len(tries) + 1, "piece": name, "sent": time.time(), "asked": [kind, want_parts],
                      "way": list(made), "text": E.way_text(*made)})
        p.update(enhance={"name": name, "folder": str(folder), "state": "sent", "sent": time.time(),
                          "where": info.get("where"), "piece": info, "swapped": None, "swap": None, "used": None,
                          "text": None, "file": None, "why": None, "after_edit": None, "tries": tries,
                          "way": E.way_text(*made), "swap_failed": None, "swap_check": None,
                          "redos": int(rec.get("redos") or 0) + (0 if rec.get("after_edit") else 1),
                          "redo": None if rec.get("after_edit") else
                          f"{len(o['need'])} clip(s) were not enhanced: a piece with all of them again"})
        return {"ok": True, "why": "", "clips": len(o["need"]), "where": info.get("where"),
                "way": "" if first else E.way_text(*made)}
    except Exception as e:  # noqa: BLE001 - the edit stays as it is (the clips outside keep the normal RAW)
        return {"ok": False, "why": f"{type(e).__name__}: {e}"}


def swap_in(project, do_check: bool = True) -> dict:
    """The finished edit plays the enhanced picture (module text). {ok, why, swapped, kept, text}; changes nothing
    when it is not ok. Never raises."""
    from . import projects as PJ
    from .enhance import OUT_FPS, PROJECT_FILE, PROJECT_SUB, read_info
    from .share_zip import run_dir_of
    try:
        p = PJ.load(project)
        rec = p.data.get("enhance") if isinstance(p.data.get("enhance"), dict) else {}
        if rec.get("state") != "used":
            return {"ok": False, "why": "the enhanced piece is not back yet"}
        ed = (p.data.get("steps") or {}).get("edit") or {}
        if not edit_made(p.folder, ed):                  # (18 Nov: a FAIL with its 1_edit.xml too)
            return {"ok": False, "why": "the edit is not finished yet"}
        folder = Path(p.folder)
        if rec.get("swapped") and (rec.get("swap") or {}).get("run") == run_dir_of(folder, ed).name and \
                (run_dir_of(folder, ed) / "extras" / NORMAL_XML).is_file():
            return {"ok": True, "why": "", "text": str(rec.get("text") or ""), "already": True}
        enh = folder / PROJECT_SUB / PROJECT_FILE
        info = read_info(folder / PROJECT_SUB / "enhance.json") or {}
        if not enh.is_file() or "start_frame" not in info or not info.get("fps"):
            return {"ok": False, "why": "the enhanced piece or its start is not in the project"}
        run = run_dir_of(folder, ed)
        xml = run / "1_edit.xml"
        normal = run / "extras" / NORMAL_XML
        if not xml.is_file():
            return {"ok": False, "why": "the edit has no 1_edit.xml"}
        src = normal if normal.is_file() else xml
        tree = ET.parse(str(src))
        root = tree.getroot()
        clips = _v1(root)
        if not clips:
            return {"ok": False, "why": "the edit has no RAW clip on V1"}
        raw_fps = Fraction(str(info["fps"]))
        start = int(info["start_frame"])
        chk = info.get("enhanced") if isinstance(info.get("enhanced"), dict) else {}
        enh_frames = int(chk.get("frames") or 0)
        width, height = chk.get("width"), chk.get("height")
        if enh_frames <= 0:
            return {"ok": False, "why": "the enhanced piece's length is not known"}
        covered = int(math.floor(Fraction(enh_frames) * raw_fps / OUT_FPS))
        groups = _groups_for(run, info)
        # 18 Nov: an enhanced file that holds only the played parts of the piece (play_ranges; its check says
        # "ranged") -- each part (the movie's frames a..b) starts at its own 60 fps frame ``at`` of the file
        parts = [(start + a, start + b, at) for a, b, at in segments(info)] if chk.get("ranged") else []
        normal_media = _media(root, "file-raw")
        raw_def = next((copy.deepcopy(f) for f in root.iter("file") if f.get("id") == "file-raw"
                        and f.find("pathurl") is not None), None)
        if raw_def is None or normal_media is None:
            return {"ok": False, "why": "1_edit.xml does not say where its RAW is"}
        if _rate(raw_def.find("rate")) not in (None, raw_fps) or _same(normal_media, enh):
            return {"ok": False, "why": "this edit was matched on the enhanced piece itself (run 20): it plays it "
                                        "already -- Redo it on the normal RAW first"}
        raw_url = raw_def.findtext("pathurl") or ""
        if "/" not in raw_url or normal_media.parent.name.lower() != "media":
            return {"ok": False, "why": "1_edit.xml's RAW is not in the edit's media folder"}
        media_enh = normal_media.parent / PROJECT_FILE      # next to the RAW the edit plays: published, zipped with it
        enh_def = ET.Element("file", {"id": "file-enhanced"})
        ET.SubElement(enh_def, "name").text = PROJECT_FILE
        ET.SubElement(enh_def, "pathurl").text = raw_url.rsplit("/", 1)[0] + f"/{PROJECT_FILE}"
        r = ET.SubElement(enh_def, "rate")
        ET.SubElement(r, "timebase").text = str(OUT_FPS)
        ET.SubElement(r, "ntsc").text = "FALSE"
        ET.SubElement(enh_def, "duration").text = str(enh_frames)
        sc = ET.SubElement(ET.SubElement(ET.SubElement(enh_def, "media"), "video"), "samplecharacteristics")
        r2 = ET.SubElement(sc, "rate")
        ET.SubElement(r2, "timebase").text = str(OUT_FPS)
        ET.SubElement(r2, "ntsc").text = "FALSE"
        if width and height:
            ET.SubElement(sc, "width").text = str(width)
            ET.SubElement(sc, "height").text = str(height)
        ET.SubElement(sc, "pixelaspectratio").text = "square"
        plan, kept = [], 0
        for c in clips:
            rate = _rate(c.find("rate")) or raw_fps
            k = _speed(c) * raw_fps / rate                 # one step of <in> / <out> in the RAW's frames
            try:
                a, b = int(c.findtext("in")), int(c.findtext("out"))
            except (TypeError, ValueError):
                kept += 1
                continue
            ma, mb = to_movie(a * k, groups), to_movie(b * k, groups)
            if ma is None or mb is None or ma < start or (not parts and mb > start + covered):   # (<out> is the
                kept += 1                                                                        #   frame after)
                continue
            base, at = start, Fraction(0)                  # the movie's frame that is the enhanced file's frame ``at``
            if parts:
                part = next((x for x in parts if x[0] <= ma and mb <= x[1]), None)
                if part is None or rate != OUT_FPS:        # in a part nobody enhanced: the normal RAW (cover_all
                    kept += 1                              #   then sends a piece with it)
                    continue
                base, at = part[0], Fraction(part[2])
            na, nb = at * rate / OUT_FPS + (ma - base) / k, at * rate / OUT_FPS + (mb - base) / k
            if (na.denominator != 1 or nb.denominator != 1) and rate == OUT_FPS:
                na = Fraction(int(round(na)))              # 7 Nov: a 60 fps sequence's clip -- the nearest of the
                nb = na + (b - a)                          #   enhanced piece's 60 fps frames (within 1/120 s)
            if na.denominator != 1 or nb.denominator != 1:
                kept += 1                                  # not a whole step on its scale: the normal RAW
                continue
            mid = int((a * k + b * k) / 2)                 # (the check looks inside the clip, not at a cut)
            mm = to_movie(mid, groups)
            when = at / OUT_FPS + Fraction((mm if mm is not None else ma) - base) / raw_fps     # s in the enhanced file
            plan.append((c, int(na), int(nb), mid, when, rate, k, base, a, b))
        if not plan:
            return {"ok": False, "why": f"no V1 clip lies inside the enhanced piece ({kept} outside it)"}
        if do_check:
            idx = {0, len(plan) // 2, len(plan) - 1}
            for x in parts:                                # (18 Nov: and the first clip of every part)
                idx |= {next((i for i, pl in enumerate(plan) if pl[7] == x[0]), 0)}
            pairs = [(plan[i][3], Fraction(plan[i][4])) for i in sorted(idx)]
            ck = check(normal_media, raw_fps, enh, pairs)
            if not ck["ok"]:
                p.update(enhance={"swap_failed": ck["why"], "swap_check": ck["rows"]})
                return {"ok": False, "why": f"{NOT_LINED_UP}: {ck['why']}"}
        else:
            ck = {"rows": []}
        ends: dict = {"moved": 0, "left": []}             # 19 Nov: no clip ends on the next shot's picture
        if do_check:
            try:
                lims = []
                for pl in plan:
                    part = next((x for x in parts if x[0] == pl[7]), None)
                    if part is None:
                        lims.append((0, enh_frames))
                    else:
                        later = [x[2] for x in parts if x[2] > part[2]]
                        lims.append((int(part[2]), int(min(later)) if later else enh_frames))
                shifts, left = _fit_ends(plan, normal_media, raw_fps, enh, lims)
                plan = [pl if not shifts.get(i) else (pl[0], pl[1] + shifts[i], pl[2] + shifts[i], *pl[3:])
                        for i, pl in enumerate(plan)]
                ends = {"moved": len(shifts), "by": sorted(set(shifts.values())), "left": left}
            except Exception as e:  # noqa: BLE001 - then the swap as it was before 19 Nov
                ends = {"moved": 0, "left": [], "error": f"{type(e).__name__}: {e}"}
        try:                                               # 18 Nov: how many times bigger the enhanced picture is --
            bigger = float(width) / float(info["width"])    #   2, or 1 for a piece that was 4K already (topaz_scale)
            if not (0.9 < bigger < 4.1):
                bigger = 2.0
        except (KeyError, TypeError, ValueError, ZeroDivisionError):
            bigger = 2.0
        defined = False
        for c, na, nb, _f, _m, rate, k, _base, _a, _b in plan:
            c.find("in").text, c.find("out").text = str(na), str(nb)
            dur = c.find("duration")
            if dur is not None:
                dur.text = str(int(math.floor(Fraction(enh_frames, OUT_FPS) * raw_fps / k)))
            if c.find("name") is not None:
                c.find("name").text = PROJECT_FILE
            f = c.find("file")
            i = list(c).index(f)
            c.remove(f)
            c.insert(i, copy.deepcopy(enh_def) if not defined else ET.Element("file", {"id": "file-enhanced"}))
            defined = True
            for par in c.iter("parameter"):
                if (par.findtext("parameterid") or "") == "scale":
                    for v in par.iter("value"):
                        if v.text and v.text.strip():
                            v.text = f"{float(v.text) / bigger:.6f}"
                elif (par.findtext("parameterid") or "") == "center":
                    _half_center(par, bigger)
        users = [ci for ci in root.iter("clipitem") if ci.find("file") is not None
                 and ci.find("file").get("id") == "file-raw"]
        if users and not any(ci.find("file").find("pathurl") is not None for ci in users):
            first = users[0]                               # the RAW's definition goes to its first clip left
            f = first.find("file")
            i = list(first).index(f)
            first.remove(f)
            first.insert(i, copy.deepcopy(raw_def))
        if not normal.is_file():
            normal.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(xml, normal)
        if not media_enh.is_file() or media_enh.stat().st_size != enh.stat().st_size:
            part = media_enh.with_name(media_enh.stem + ".part" + media_enh.suffix)
            part.unlink(missing_ok=True)
            try:
                os.link(enh, part)                         # a hard link: no space
            except OSError:
                shutil.copyfile(enh, part)
            os.replace(part, media_enh)
        tmp = xml.with_name(xml.stem + ".swap.part.xml")
        ET.indent(root, space="  ")
        tmp.write_text('<?xml version="1.0" encoding="UTF-8"?>\n<!DOCTYPE xmeml>\n' +
                       ET.tostring(root, encoding="unicode") + "\n", encoding="utf-8")
        ET.parse(str(tmp))                                 # (reads back)
        os.replace(tmp, xml)
        try:                                               # 7 Nov: no small cuts (core.join_clips)
            from . import join_clips
            join_clips.join(xml)
        except Exception:  # noqa: BLE001
            pass
        text = (f"enhanced: {width}x{height}, {OUT_FPS} fps -- {len(plan)} of {len(plan) + kept} V1 clips play it "
                f"(matched on the normal RAW" + (f"; only the {len(parts)} played parts were enhanced" if parts
                                                 else "") + ")")
        if ends.get("left"):
            text += f" -- look at {len(ends['left'])} clip end(s): {ends['left'][0]}"
        p.update(enhance={"swapped": _now(), "text": text, "swap": {"run": run.name, "clips": len(plan),
                                                                   "kept": kept, "check": ck.get("rows"),
                                                                   "ends": ends}})
        try:                                               # only the scene stays: the downloaded RAW nothing plays
            from . import archive, run as RN              #   any more leaves the folder (as after an edit)
            if RN.scene_env() and ed.get("state") == PJ.DONE:     # (18 Nov: a FAIL keeps all its files: you may
                archive.drop_unplayed(folder, PJ.load(folder).data)   #   make it again)
        except Exception:  # noqa: BLE001
            pass
        zip_when_done(folder)                              # the zip to send: the cut Topaz version
        return {"ok": True, "why": "", "swapped": len(plan), "kept": kept, "text": text}
    except Exception as e:  # noqa: BLE001 - the edit stays on the normal RAW
        try:
            tmp = locals().get("tmp")
            if tmp is not None and Path(tmp).exists():
                Path(tmp).unlink()
        except OSError:
            pass
        return {"ok": False, "why": f"{type(e).__name__}: {e}"}


def _half_center(par, by: float = 2.0) -> None:
    """7 Nov: Premiere reads a clip's Position (center horiz / vert) in the clip's OWN pixels -- the enhanced picture
    has 2x the pixels, so the same numbers moved it twice as far (a black band over the picture). Halved, like the
    scale; keyframed positions too."""
    for v in par.iter("value"):
        for ax in ("horiz", "vert"):
            e = v.find(ax)
            if e is not None and e.text and e.text.strip():
                e.text = f"{float(e.text) / by:.7f}"


def wants_60(job) -> bool:
    """7 Nov (your call: "it should always be 60"): an edit whose project sends its piece to Topaz is made in a 60 fps
    sequence (--sequence-fps 60) -- the enhanced picture swapped in later is 60 fps, and plays at its own rate."""
    try:
        folder = getattr(job, "project", None)
        if folder is None:
            return False
        d = json.loads((Path(folder) / "project.json").read_text(encoding="utf-8"))
        e = d.get("enhance") if isinstance(d.get("enhance"), dict) else {}
        # 18 Nov (The Smartest Kevin Moment In The Office: its edit ran without --sequence-fps 60): since 17 Nov no
        # piece is sent before the edit -- the record then says "skipped, Topaz comes after the edit" (after_edit),
        # which this did not count, so every edit since was made at the RAW's rate again. It gets Topaz all the same.
        return e.get("state") in ("sent", "used") or (e.get("state") == "skipped" and bool(e.get("after_edit")))
    except Exception:  # noqa: BLE001 - as before: the RAW's own rate
        return False


def zip_when_done(project) -> None:
    """The zip to send, made now that the picture is final (the Topaz one, or the normal RAW when Topaz failed or
    is late) -- only for a finished edit; in its own thread (core.share_zip)."""
    try:
        from . import projects as PJ
        from . import share_zip as Z
        p = PJ.load(project)
        ed = (p.data.get("steps") or {}).get("edit") or {}
        if ed.get("state") == PJ.DONE:
            Z.make_later(Path(p.folder), Z.run_dir_of(Path(p.folder), ed))
    except Exception:  # noqa: BLE001
        pass


def _now() -> str:
    from . import projects as PJ
    return PJ.now()
