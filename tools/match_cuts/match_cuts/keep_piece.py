"""keep_piece.py -- day 16, task 3: only the movie's piece in the project folder, and 1_edit.xml plays that piece.

Until day 16 a run on a long RAW ended with 1_edit.xml playing the WHOLE movie (extras/media/raw.<ext>: a hard link or
a copy of it) and A1 a raw_stereo.wav as long as the movie up to the piece -- gigabytes in every project folder and its
zip. At the very end of a Premiere run (after excerpt.finish moved the edit onto the movie) ``finish`` now keeps only
what the edit plays, with room around it:

* the RAW frames every V1 / A1 clip of 1_edit.xml plays are grouped (two clips closer than 2 x KEEP_MARGIN_S are one
  group), each group +- KEEP_MARGIN_S (10 minutes) -- from the keyframe before to the keyframe after -- is cut out of
  the movie by stream copy (excerpt.copy_packets: the very packets, nothing re-encoded), and the groups are put one
  after the other into ONE file, ``extras/media/raw.mov`` (one group: one piece; a montage from far-apart places: one
  piece per group, the pieces in a row);
* A1: raw_stereo.wav is cut the same way (sample for sample: a WAV has no encoder delay); when A1 plays the RAW's own
  sound (plain stereo AAC), the piece carries that sound as 16-bit PCM decoded sample-exact (excerpt.cut_piece);
* every RAW clip of 1_edit.xml and of the planning copies in extras/ (edit_*.xml) is moved onto the piece: <in> /
  <out> / its keyframes minus (the group's first movie frame - where the group starts in the piece), on the clip's own
  scale (a 125 % clip counts 1.25 frames a step: the group's start is chosen so that the shift is a whole number);
* checked before anything is changed: the moved XML has no Premiere problem, the piece's frames are the movie's at the
  start, middle and end of every group, and three V1 clips per group show the movie's frame (gray SSIM, as excerpt's
  checks); then swapped in, and the run's link / copy of the whole movie is removed from extras/media (the movie itself
  stays where it was downloaded).

The whole movie stays, as before, when: it is not a Premiere run, the RAW is shorter than MIN_RAW_S, the pieces would
be more than KEEP_MAX_SHARE of it, the RAW cannot be cut that way (excerpt.movie_facts: VFR, an edit list, a container
Premiere does not play ...), a shift would not be whole, or any check fails (a warning says so; nothing is changed).
``extras/keep_piece.json`` says where each group is (``to_piece`` maps a movie frame onto the piece): excerpt.
movie_cutlist uses it, so Learn and the scores read the run in the frames 1_edit.xml plays.

No cached stage imports this module (it is not in common.STAGE_CODE): it changes no cache key.
"""
from __future__ import annotations

import json
import math
import os
import shutil
import subprocess
import time
import wave
import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass
from fractions import Fraction
from pathlib import Path
from typing import Any

from .common import ffmpeg_bin, log

KEEP_MARGIN_S = 10 * 60.0        # every group of clips +- this
KEEP_MAX_SHARE = 0.60            # the pieces more than this share of the movie: the whole movie stays
MIN_RAW_S = 15 * 60.0            # a shorter RAW is kept whole
INFO_NAME = "keep_piece.json"
PIECE_NAME = "raw.mov"
SOUND_RATE = 48000
RAW_IDS = ("file-raw", "file-raw-sound")
POINTS = 3


@dataclass
class Group:
    """One piece of the movie: frames [k0, k1) (k0 a keyframe), placed in the piece from frame ``offset`` on."""
    k0: int
    k1: int
    pts0: int
    dts_end: int | None
    offset: int = 0

    @property
    def frames(self) -> int:
        return self.k1 - self.k0

    @property
    def shift(self) -> int:
        """Movie frame -> piece frame: minus this."""
        return self.k0 - self.offset


def _off(why: str) -> dict:
    log.info("only the movie's piece: no -- %s (1_edit.xml plays the whole RAW)", why)
    return {"kept": False, "why": why}


# ---------------------------------------------------------------------------------------------------------------------
# what the edit plays
# ---------------------------------------------------------------------------------------------------------------------

def _clips(root: ET.Element) -> list[ET.Element]:
    return [ci for ci in root.iter("clipitem") if ci.find("file") is not None and ci.find("file").get("id") in RAW_IDS]


def clip_span(ci: ET.Element, raw_fps: Fraction) -> tuple[int, int] | None:
    """The movie frames [first, last + 1) a RAW clip item plays (its <in> / <out> on its own scale), None without."""
    from .excerpt import _clip_speed, _rate
    rate = _rate(ci.find("rate")) or raw_fps
    s = Fraction(str(_clip_speed(ci)))
    try:
        a, b = int(ci.findtext("in")), int(ci.findtext("out"))
    except (TypeError, ValueError):
        return None
    if a < 0 or b < 0:
        return None
    k = s * raw_fps / rate
    lo, hi = sorted((a, b))
    return int(math.floor(lo * k)), int(math.ceil(hi * k)) + 1


def spans_of(xml: Path, raw_fps: Fraction) -> list[tuple[int, int]]:
    root = ET.parse(str(xml)).getroot()
    return [sp for sp in (clip_span(ci, raw_fps) for ci in _clips(root)) if sp is not None]


def plan_groups(m: Any, spans: list[tuple[int, int]], step: int = 1, margin_s: float = KEEP_MARGIN_S,
                share: float = KEEP_MAX_SHARE) -> tuple[list[Group] | None, str]:
    """The pieces for the clips' ``spans`` (movie frames), or (None, why not). ``step``: every group's shift
    (k0 - offset) a multiple of it (a sped-up clip's <in> moves by whole steps); ``m``: excerpt.Movie."""
    if not spans:
        return None, "the edit plays no RAW frame"
    f = float(m.rate)
    margin = int(math.ceil(margin_s * f))
    raw = sorted((max(0, a), min(m.frames, b)) for a, b in spans)
    wanted: list[list[int]] = []
    for a, b in raw:
        if wanted and a - wanted[-1][1] <= 2 * margin:
            wanted[-1][1] = max(wanted[-1][1], b)
        else:
            wanted.append([a, b])
    keys = sorted(m.keyframes)
    while True:
        groups: list[Group] = []
        offset = 0
        for a, b in wanted:
            want0, want1 = max(0, a - margin), min(m.frames, b + margin)
            starts = [k for k in keys if k[0] <= want0 and (k[0] - offset) % step == 0]
            if not starts:
                starts = [k for k in keys if k[0] <= a and (k[0] - offset) % step == 0]
            if not starts:
                return None, f"no keyframe to start a piece from at frame {want0}"
            k0, pts0, _d = max(starts)
            ends = [k for k in keys if k[0] >= want1]
            k1, dts_end = (ends[0][0], ends[0][2]) if ends else (m.frames, None)
            groups.append(Group(int(k0), int(k1), int(pts0), dts_end, offset))
            offset += int(k1) - int(k0)
        merged = False                         # two groups that touch after the keyframes: one, and plan again
        for i in range(1, len(groups)):
            if groups[i].k0 <= groups[i - 1].k1:
                wanted[i - 1][1] = max(wanted[i - 1][1], wanted[i][1])
                del wanted[i]
                merged = True
                break
        if not merged:
            break
    total = sum(g.frames for g in groups)
    if total > share * m.frames:
        return None, (f"the pieces would be {100 * total / max(1, m.frames):.0f} % of the RAW (more than "
                      f"{100 * share:.0f} %)")
    for a, b in raw:
        if not any(g.k0 <= a and b <= g.k1 for g in groups):
            return None, f"frames {a}-{b} fall between the pieces"
    return groups, ""


def to_piece(frame: int, groups: list[dict | Group]) -> int | None:
    """A movie frame's frame in the piece (None: not in it)."""
    for g in groups:
        k0, k1, off = (g.k0, g.k1, g.offset) if isinstance(g, Group) else (int(g["k0"]), int(g["k1"]),
                                                                             int(g["offset"]))
        if k0 <= frame < k1:
            return frame - k0 + off
    return None


# ---------------------------------------------------------------------------------------------------------------------
# the piece, its sound, the moved XML
# ---------------------------------------------------------------------------------------------------------------------

def cut(m: Any, groups: list[Group], dst: Path, sound: bool) -> dict:
    """``dst``: the groups one after the other (stream copy; ``sound``: with the movie's sound as PCM, sample-exact).
    Returns {seconds, bytes}; raises on failure."""
    from .excerpt import Plan, copy_packets, cut_piece
    t0 = time.perf_counter()
    parts: list[Path] = []
    try:
        for i, g in enumerate(groups):
            part = dst.with_name(f"{dst.stem}.g{i}.part.mov")
            plan = Plan(g.k0, g.k1, g.pts0, g.dts_end, [])
            if sound:
                cut_piece(m, plan, part)
            else:
                copy_packets(m, plan, part)
            parts.append(part)
        tmp = dst.with_name(dst.stem + ".part.mov")
        if len(parts) == 1:
            os.replace(parts[0], tmp)
        else:
            lst = dst.with_name(dst.stem + ".concat.txt")
            lst.write_text("".join(f"file '{p.name}'\n" for p in parts), encoding="utf-8")
            r = subprocess.run([ffmpeg_bin(), "-hide_banner", "-v", "error", "-nostdin", "-y", "-f", "concat",
                                "-safe", "0", "-i", str(lst), "-map", "0", "-c", "copy", "-map_metadata", "-1",
                                "-fflags", "+bitexact", str(tmp)], capture_output=True, text=True, encoding="utf-8",
                               errors="replace", cwd=str(dst.parent))
            lst.unlink(missing_ok=True)
            if r.returncode != 0 or not tmp.is_file():
                tmp.unlink(missing_ok=True)
                raise RuntimeError(f"the pieces could not be put together: {(r.stderr or '').strip()[-300:]}")
        os.replace(tmp, dst)
    finally:
        for p in parts:
            p.unlink(missing_ok=True)
    return {"seconds": round(time.perf_counter() - t0, 2), "bytes": int(dst.stat().st_size)}


def check_frames(piece: Path, m: Any, groups: list[Group], points: int = POINTS) -> dict:
    """The piece's frame offset + j is the movie's frame k0 + j at the start, middle and end of every group (gray
    SSIM at <= 480 px above excerpt.CHECK_SSIM, and not closer to a neighbouring frame). {ok, rows, why}."""
    import cv2
    from .conform import ssim
    from .excerpt import CHECK_SSIM
    from .media import VideoReader
    pairs = []
    for g in groups:
        for j in sorted({0, g.frames // 2, max(0, g.frames - 2)})[:points]:
            pairs.append((g.offset + j, g.k0 + j))
    rows, bad = [], []
    with VideoReader(str(piece), fps=m.rate) as rp, VideoReader(m.path, fps=m.rate, stream_index=m.vindex) as ro:
        fp = rp.get_many(sorted({p for p, _k in pairs}), fmt="gray")
        fo = ro.get_many(sorted({k + d for _p, k in pairs for d in (-1, 0, 1) if k + d >= 0}), fmt="gray")
    for p, k in pairs:
        a, b = fp.get(p), fo.get(k)
        if a is None or b is None:
            bad.append(f"piece frame {p} / movie frame {k} could not be read")
            continue
        h, w = a.shape[:2]
        f = min(1.0, 480.0 / max(h, w))
        size = (max(8, int(round(w * f))), max(8, int(round(h * f))))
        A = cv2.resize(a, size, interpolation=cv2.INTER_AREA)
        s = ssim(A, cv2.resize(b, size, interpolation=cv2.INTER_AREA))
        nb = [ssim(A, cv2.resize(fo[x], size, interpolation=cv2.INTER_AREA)) for x in (k - 1, k + 1)
              if fo.get(x) is not None]
        rows.append({"piece": p, "movie": k, "ssim": round(s, 4), "neighbours": [round(x, 4) for x in nb]})
        if s < CHECK_SSIM or any(x > s + 0.003 for x in nb):
            bad.append(f"piece frame {p} is not the movie's frame {k} (SSIM {s:.3f})")
    return {"ok": not bad, "rows": rows, "why": "; ".join(bad)}


def cut_wav(src: Path, dst: Path, groups: list[Group], fps: Fraction) -> int:
    """``dst``: ``src``'s samples of every group one after the other (the piece's frame j <-> sample j x 48 kHz /
    fps; silence where ``src`` ends). Returns its length in samples."""
    r = Fraction(SOUND_RATE) / fps
    with wave.open(str(src), "rb") as a:
        nch, width, rate, n = a.getnchannels(), a.getsampwidth(), a.getframerate(), a.getnframes()
        if rate != SOUND_RATE:
            raise RuntimeError(f"{src.name} is {rate} Hz, not {SOUND_RATE}")
        tmp = dst.with_name(dst.stem + ".part" + dst.suffix)
        total = 0
        with wave.open(str(tmp), "wb") as b:
            b.setnchannels(nch)
            b.setsampwidth(width)
            b.setframerate(rate)
            for g in groups:
                p0, p1 = int(round(g.offset * r)), int(round((g.offset + g.frames) * r))
                s0 = int(round(g.k0 * r))
                want = p1 - p0
                got = b""
                if s0 < n:
                    a.setpos(s0)
                    got = a.readframes(min(want, n - s0))
                pad = want - len(got) // (nch * width)
                b.writeframes(got + b"\x00" * (pad * nch * width))
                total += want
    os.replace(tmp, dst)
    return total


def shift_xml(src: Path, dst: Path, groups: list[Group], raw_fps: Fraction, piece: Path, piece_frames: int,
              wav_frames: int | None, exact: bool = True) -> dict:
    """``src``'s RAW clips moved onto the piece (written to ``dst``); raises when a clip is not in a group, or
    (``exact``: 1_edit.xml) when its shift is not whole on its scale -- a planning copy at another rate (edit_60fps.xml)
    is rounded to its nearest frame, as excerpt.shift_xml does. Returns {items, shifts, exact}."""
    from .common import atomic_write_text
    from .excerpt import _clip_speed, _rate
    from .export_xml_edl import _file_url
    root = ET.parse(str(src)).getroot()
    for fe in root.iter("file"):
        fid = fe.get("id")
        if fid not in RAW_IDS or fe.find("pathurl") is None:
            continue
        rate = _rate(fe.find("rate")) or raw_fps
        frames = piece_frames if fid == "file-raw" else (wav_frames if wav_frames is not None else piece_frames)
        if fe.find("duration") is not None:
            fe.find("duration").text = str(int(math.floor(frames * float(rate) / float(raw_fps))))
        if fid == "file-raw":
            fe.find("pathurl").text = _file_url(str(piece.resolve()))
            if fe.find("name") is not None:
                fe.find("name").text = piece.name
    items, shifts, whole = 0, set(), True
    for ci in _clips(root):
        fid = ci.find("file").get("id")
        sp = clip_span(ci, raw_fps)
        if sp is None:
            continue
        g = next((g for g in groups if g.k0 <= sp[0] and sp[1] <= g.k1), None)
        if g is None:
            raise RuntimeError(f"clip {ci.get('id')} (movie frames {sp[0]}-{sp[1]}) is in no piece")
        rate = _rate(ci.find("rate")) or raw_fps
        s = Fraction(str(_clip_speed(ci)))
        d = Fraction(g.shift) * rate / raw_fps / s
        if d.denominator != 1:
            if exact:
                raise RuntimeError(f"clip {ci.get('id')}: the shift {float(d):.3f} is not whole on its scale")
            whole = False
        di = int(round(d))
        shifts.add(di)
        for tag in ("in", "out"):
            el = ci.find(tag)
            if el is not None and el.text is not None and int(el.text) >= 0:
                el.text = str(int(el.text) - di)
        for kf in ci.iter("keyframe"):
            w = kf.find("when")
            if w is not None and w.text is not None:
                w.text = str(int(w.text) - di)
        frames = piece_frames if fid == "file-raw" else (wav_frames if wav_frames is not None else piece_frames)
        media_len = int(math.floor(frames * float(rate) / float(raw_fps)))
        if ci.find("duration") is not None:
            ci.find("duration").text = str(media_len if s == 1 else int(math.floor(media_len / float(s))))
        if fid == "file-raw" and ci.find("name") is not None:
            ci.find("name").text = piece.name
        items += 1
    ET.indent(root, space="  ")
    atomic_write_text(dst, '<?xml version="1.0" encoding="UTF-8"?>\n<!DOCTYPE xmeml>\n' +
                      ET.tostring(root, encoding="unicode") + "\n")
    return {"items": items, "shifts": sorted(shifts), "exact": whole}


def check_clips(before: Path, after: Path, m: Any, piece: Path, groups: list[Group], raw_fps: Fraction) -> dict:
    """Up to three V1 clips per group: the movie's frame at the old <in> and the piece's at the new one look alike,
    and the new frame is the old one minus the group's shift. {ok, rows, why}."""
    import cv2
    from .conform import ssim
    from .excerpt import CHECK_SSIM
    from .media import VideoReader

    def v1(p: Path) -> list[tuple[str, int]]:
        root = ET.parse(str(p)).getroot()
        video = root.find("sequence/media/video")
        out = []
        for tr in (video.findall("track") if video is not None else []):
            for ci in tr.findall("clipitem"):
                f = ci.find("file")
                if f is not None and f.get("id") == "file-raw":
                    sp = clip_span(ci, raw_fps)
                    if sp is not None:
                        out.append((ci.get("id"), sp[0]))
        return out

    a, b = v1(before), v1(after)
    if [x[0] for x in a] != [x[0] for x in b]:
        return {"ok": False, "rows": [], "why": "the moved edit has other clips"}
    jobs = []
    for g in groups:
        idx = [i for i, (_c, k) in enumerate(a) if g.k0 <= k < g.k1]
        for i in sorted({idx[0], idx[len(idx) // 2], idx[-1]}) if idx else []:
            jobs.append((a[i][0], a[i][1], b[i][1], g))
    rows, bad = [], []
    with VideoReader(str(piece), fps=raw_fps) as rp, VideoReader(m.path, fps=raw_fps, stream_index=m.vindex) as rm:
        fp = rp.get_many([j for _c, _k, j, _g in jobs], fmt="gray")
        fm = rm.get_many([k for _c, k, _j, _g in jobs], fmt="gray")
    for cid, k, j, g in jobs:
        x, y = fp.get(j), fm.get(k)
        if x is None or y is None:
            bad.append(f"{cid}: a frame could not be read")
            continue
        h, w = x.shape[:2]
        f = min(1.0, 480.0 / max(h, w))
        size = (max(8, int(round(w * f))), max(8, int(round(h * f))))
        sv = ssim(cv2.resize(x, size, interpolation=cv2.INTER_AREA), cv2.resize(y, size, interpolation=cv2.INTER_AREA))
        rows.append({"clip": cid, "movie_frame": k, "piece_frame": j, "ssim": round(sv, 4)})
        if k - j != g.shift or sv < CHECK_SSIM:
            bad.append(f"{cid}: the piece's frame {j} is not the movie's frame {k} (SSIM {sv:.3f})")
    return {"ok": not bad, "rows": rows, "why": "; ".join(bad)}


# ---------------------------------------------------------------------------------------------------------------------
# the run
# ---------------------------------------------------------------------------------------------------------------------

def _media_of(xml: Path, fid: str) -> Path | None:
    from .export_xml_edl import _url_path
    root = ET.parse(str(xml)).getroot()
    for fe in root.iter("file"):
        if fe.get("id") == fid and fe.find("pathurl") is not None:
            p = _url_path(fe.findtext("pathurl") or "")
            return Path(p) if p else None
    return None


def _a1_plays_raw(xml: Path) -> bool:
    root = ET.parse(str(xml)).getroot()
    audio = root.find("sequence/media/audio")
    for tr in (audio.findall("track") if audio is not None else []):
        for ci in tr.findall("clipitem"):
            f = ci.find("file")
            if f is not None and f.get("id") == "file-raw":
                return True
    return False


def _speed_step(xml: Path) -> int:
    """The step every shift must be a multiple of: the numerators of the clips' speeds (1.25 -> 5)."""
    from .excerpt import _clip_speed, speed_step
    root = ET.parse(str(xml)).getroot()
    step = 1
    for ci in _clips(root):
        s = speed_step(_clip_speed(ci))
        step = step * s // math.gcd(step, s)
    return step


def keep_run(run_dir: str | os.PathLike, sources: list[str | os.PathLike] | None = None,
             margin_s: float = KEEP_MARGIN_S, share: float = KEEP_MAX_SHARE) -> dict:
    """Make a finished run folder play only its piece of the movie (module text). ``sources``: the original RAW's
    paths -- the run's link / copy of the whole movie in extras/media is removed only when one of them is still
    there (and is not that file). Never raises: {kept, why | groups, piece, ...}."""
    t0 = time.perf_counter()
    run = Path(run_dir)
    extras = run / "extras"
    final = run / "1_edit.xml"
    temps: list[Path] = []
    try:
        if not final.is_file():
            return _off("no 1_edit.xml")
        movie = _media_of(final, "file-raw")
        if movie is None or not movie.is_file():
            return _off("the RAW 1_edit.xml plays is not there")
        media = movie.parent
        from .excerpt import movie_facts
        m = movie_facts(movie)
        if m.why:
            return _off(m.why)
        if m.duration < MIN_RAW_S:
            return _off(f"the RAW is {m.duration / 60:.1f} min, under {MIN_RAW_S / 60:.0f}")
        raw_fps = m.rate
        xmls = [final] + sorted(extras.glob("edit_*.xml"))
        spans = []
        for x in xmls:
            spans += spans_of(x, raw_fps)
        step = max(_speed_step(x) for x in xmls)
        groups, why = plan_groups(m, spans, step, margin_s, share)
        if groups is None:
            return _off(why)
        sound = _a1_plays_raw(final)
        piece = media / PIECE_NAME
        if os.path.normcase(str(piece.resolve())) == os.path.normcase(str(movie.resolve())):
            piece = media / ("raw_piece.mov")
        work = media / (piece.stem + ".new.mov")
        temps.append(work)
        made = cut(m, groups, work, sound)
        n = sum(g.frames for g in groups)
        from . import stereo
        got = int(stereo._frames(str(work))[0])
        if got != n:
            raise RuntimeError(f"the piece has {got} frames, not {n}")
        chk = check_frames(work, m, groups)
        if not chk["ok"]:
            raise RuntimeError(chk["why"])
        wav = _media_of(final, "file-raw-sound")
        wav_new, wav_frames = None, None
        if wav is not None and wav.is_file() and wav.suffix.lower() == ".wav":
            wav_new = wav.with_name(wav.stem + ".new.wav")
            temps.append(wav_new)
            samples = cut_wav(wav, wav_new, groups, raw_fps)
            wav_frames = int(math.floor(samples * float(raw_fps) / SOUND_RATE))
        moved = []
        for x in xmls:
            tmp = x.with_name(x.stem + ".piece.part.xml")
            temps.append(tmp)
            info = shift_xml(x, tmp, groups, raw_fps, piece, n, wav_frames, exact=x == final)
            moved.append((x, tmp, info))
        from .export_xml_edl import premiere_item_problems
        probs = premiere_item_problems(moved[0][1])
        if probs:
            raise RuntimeError(f"the moved 1_edit.xml has problems: {'; '.join(probs[:3])}")
        cc = check_clips(final, moved[0][1], m, work, groups, raw_fps)
        if not cc["ok"]:
            raise RuntimeError(cc["why"])
        # everything made and checked: swap it in
        before = {"movie": int(movie.stat().st_size), "wav": int(wav.stat().st_size) if wav_new else 0}
        os.replace(work, piece)
        if wav_new is not None:
            os.replace(wav_new, wav)
        for x, tmp, _i in moved:
            os.replace(tmp, x)
        removed = _drop_movie(movie, piece, sources or [])
        out = {"kept": True, "movie": str(movie), "movie_frames": m.frames, "fps": m.fps,
               "movie_seconds": round(m.duration, 3), "groups": [asdict(g) for g in groups], "piece": piece.name,
               "frames": n, "wav_frames": wav_frames, "sound": "pcm" if sound else "", "margin_s": margin_s,
               "bytes_before": before, "bytes_after": {"piece": int(piece.stat().st_size),
                                                      "wav": int(wav.stat().st_size) if wav_new else 0},
               "movie_link_removed": removed, "cut": made, "check": chk["rows"], "check_clips": cc["rows"],
               "xmls": [str(x) for x, _t, _i in moved], "seconds": round(time.perf_counter() - t0, 2)}
        (extras / INFO_NAME).write_text(json.dumps(out, indent=1, default=str), encoding="utf-8")
        fps = float(raw_fps)
        where = ", ".join(f"{_clock(g.k0 / fps)}-{_clock(g.k1 / fps)}" for g in groups)
        mb = (before["movie"] + before["wav"]) / 1024 ** 2
        ma = (out["bytes_after"]["piece"] + out["bytes_after"]["wav"]) / 1024 ** 2
        line = (f"V1 plays only the movie's piece {where} of {_clock(m.duration)} (extras\\media\\{piece.name}, "
                f"{len(groups)} piece{'s' if len(groups) != 1 else ''}, cut by stream copy): {mb:.0f} MB -> {ma:.0f} MB")
        out["line"] = line
        try:
            rep = extras / "report.md"
            if rep.is_file():
                with open(rep, "a", encoding="utf-8") as fh:
                    fh.write(f"\n\n## Only the movie's piece (day 16)\n\n- {line}.\n- Each piece is the clips' frames "
                             f"+- {margin_s / 60:.0f} min, from the keyframe before; every RAW clip of 1_edit.xml "
                             f"plays it at its frame (checked: {len(chk['rows'])} piece frames and "
                             f"{len(cc['rows'])} clips against the movie).\n")
        except OSError:
            pass
        log.info("only the movie's piece: %s (%.1f s)", line, out["seconds"])
        return out
    except Exception as e:  # noqa: BLE001 - nothing changed: the edit plays the whole movie
        for p in temps:
            try:
                if p.exists():
                    p.unlink()
            except OSError:
                pass
        log.warning("only the movie's piece: no -- %s: %s (nothing changed: 1_edit.xml plays the whole RAW)",
                    type(e).__name__, e)
        return {"kept": False, "why": f"{type(e).__name__}: {e}"}


def _drop_movie(movie: Path, piece: Path, sources: list) -> str:
    """The run's link / copy of the whole movie goes when the movie is still elsewhere (a hard link: another name
    of it; a copy: one of ``sources`` still there). Returns how ('' = kept)."""
    try:
        if os.path.normcase(str(movie.resolve())) == os.path.normcase(str(piece.resolve())):
            return ""
        st = movie.stat()
        elsewhere = [Path(s) for s in sources if s and Path(s).is_file()
                     and os.path.normcase(str(Path(s).resolve())) != os.path.normcase(str(movie.resolve()))]
        if st.st_nlink > 1 or elsewhere:
            movie.unlink()
            return "hard link removed" if st.st_nlink > 1 else "copy removed (the movie is still at its place)"
    except OSError as e:
        log.warning("only the movie's piece: %s could not be removed (%s)", movie, e)
    return ""


def finish(ctx: Any) -> dict:
    """The end of a Premiere run: ``keep_run`` on its run folder. Never raises."""
    cfg = ctx.cfg
    if not getattr(cfg, "premiere", False):
        return {}
    # Day 16 follow-up (the user's call): OFF by default -- the edit plays the whole movie again, the whole movie
    # stays in the project folder. MATCH_CUTS_KEEP_PIECE=1 switches the piece back on.
    if (getattr(cfg, "no_keep_piece", False)
            or os.environ.get("MATCH_CUTS_NO_KEEP_PIECE", "").strip() not in ("", "0")
            or os.environ.get("MATCH_CUTS_KEEP_PIECE", "").strip() in ("", "0")):
        return _off("switched off")
    sources = [x for x in (getattr(cfg, "raw_movie", None), getattr(cfg, "raw_input", None), getattr(cfg, "raw", None))
               if x]
    out = keep_run(cfg.deliver, sources)
    if out.get("kept") and isinstance(getattr(ctx, "edit_media", None), dict):
        ctx.edit_media["line"] = out["line"]
    try:
        ctx.dlog.record("premiere", "keep_piece", **{k: v for k, v in out.items() if k not in ("check", "check_clips")})
    except Exception:  # noqa: BLE001
        pass
    return out


def run_info(run_dir: str | os.PathLike) -> dict | None:
    """extras/keep_piece.json of a run folder whose edit plays only the piece; None otherwise."""
    d = Path(run_dir)
    for p in (d / "extras" / INFO_NAME, d / INFO_NAME):
        if p.is_file():
            try:
                info = json.loads(p.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                return None
            return info if isinstance(info, dict) and info.get("kept") else None
    return None


def _clock(s: float) -> str:
    s = max(0.0, float(s))
    h, rem = divmod(int(round(s)), 3600)
    mm, ss = divmod(rem, 60)
    return f"{h}:{mm:02d}:{ss:02d}" if h else f"{mm}:{ss:02d}"


def main(argv: list[str] | None = None) -> int:
    """``python -m match_cuts.keep_piece <run folder> [original RAW ...]``: a finished run made to play only its
    piece (a test on a copy of a run folder)."""
    import sys
    args = list(sys.argv[1:] if argv is None else argv)
    if not args:
        print(main.__doc__)
        return 2
    out = keep_run(args[0], args[1:])
    print(json.dumps({k: v for k, v in out.items() if k not in ("check", "check_clips")}, indent=1, default=str))
    return 0 if out.get("kept") else 1


if __name__ == "__main__":
    raise SystemExit(main())
