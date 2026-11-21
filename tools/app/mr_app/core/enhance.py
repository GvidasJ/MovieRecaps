"""The scene's piece enhanced with Topaz Video AI, by itself (run 20; the order as it is since 17 Nov).

The order of one video (your call, 17 Nov): edit -> Topaz once -> the enhanced picture into the edit -> zip.

After the edit (``enhance_swap.cover_all`` -> ``make_piece``): the RAW from the edit's first clip to its last, 10 s
more on each side, is cut by stream copy from the keyframe before (match_cuts.excerpt: the movie's own packets, its
frames checked against the RAW at three places; a RAW that cannot be cut that way gets an encoded piece), its sound
made AAC (Topaz's ffmpeg cannot put PCM in an mp4), and put in the Enhance folder (``share_dir``: D:\\Shared\\Enhance
on the PC) as ``<project>_TOENHANCE.mp4`` with ``<project>_TOENHANCE.json`` (where the piece starts in the RAW, its
rate, length, size, which project; 18 Nov: "ranges" -- the parts of it the edit plays). The json is written last: a
piece without it is not finished yet.

The computer that enhances (``Worker``, every SCAN_S and at the start): a ``_TOENHANCE.mp4`` with its json that
nobody has taken is taken (``<project>_TOENHANCE.lock``, made with O_EXCL: two computers never take the same one; the
owner rewrites it every few seconds with its progress, and a lock nobody rewrote for LOCK_STALE_S is free again),
then Topaz's own ffmpeg runs on it (``topaz_command``: the user's settings, exactly; ``enhance_parts``: only the
played parts) into ``<project>_ENHANCING.part.mp4``, which is checked (``check_enhanced``) and renamed
``<project>_ENHANCED.mp4`` -- nobody ever sees a half-written file. A failure writes
``<project>_ENHANCE_FAILED.txt`` (the reason in plain words and Topaz's last lines).

The computer that made the edit (``watch`` / ``sweep`` / ``take_back``): ``_ENHANCED.mp4`` checked (the length, 60
fps, the size, it decodes from the first frame), copied into the project (``enhance\\raw_enhanced.mp4``), the share's
files deleted, and ``enhance_swap.swap_in`` makes the finished 1_edit.xml play it. A failure: the piece is sent
again (at most enhance_swap.MAX_REDOS times, also later by the round). Nothing back within TIMEOUT_S: the zip is
made with the normal RAW and a late piece is still used by itself when it comes.

MOVIERECAPS_FAKE_TOPAZ=1 (the tests): a plain 2x scale + 60 fps with the normal ffmpeg instead of Topaz."""
from __future__ import annotations

import json
import logging
import math
import os
import shutil
import subprocess
import threading
import time
from datetime import datetime
from dataclasses import dataclass, field
from fractions import Fraction
from pathlib import Path
from typing import Any, Callable

from . import machine as M

log = logging.getLogger(__name__)

MAX_PIECE_S = 80.0                  # ``plan``'s limit when the caller gives none (make_piece gives the span's own)
TIMEOUT_S = 3600.0                  # nothing back from the laptop within this: the normal RAW
TIMEOUT_WHY = "the laptop did not answer in 1 hour"
STILL_WHY = "the laptop was still enhancing it"
MAX_WAIT_S = 4 * 3600.0             # ... but a laptop that is still enhancing it (its lock rewritten) is waited for up
                                    #   to this long (this laptop: 1080p -> 4K runs at ~1/50 of real time)
SCAN_S = 30.0                       # the laptop looks for new pieces this often
WORKER_ALIVE_S = 5 * 60.0           # 30 Oct: a Topaz computer that wrote its heartbeat this recently is alive ...
ALIVE_WAIT_S = 12 * 3600.0          # ... and a piece waiting in its line (behind others / an edit) is waited for this
WORKER_GLOB = ".topaz_worker_*.json"
LOCK_STALE_S = 15 * 60.0            # a lock not rewritten for this long: its computer stopped -- free again
LOCK_BEAT_S = 5.0                   # the owner rewrites its lock this often (its progress)
OUT_FPS = 60
OUT_BITRATE = "60M"                 # 17 Nov (your call): 60 Mb/s, not 180 -- 4K 60 fps H.265 looks the same, a third the size
OUT_BUFFER = "120M"
FAKE_ENV = "MOVIERECAPS_FAKE_TOPAZ"
ENHANCE_ENV = "MOVIERECAPS_ENHANCE"  # "0": both switches off unless Settings say (the app's tests)
TOPAZ_ENV = "MOVIERECAPS_TOPAZ"     # Topaz Video AI's folder, when it is not where it installs itself
PC_DIR = Path(r"D:\Shared\Enhance")
LAPTOP_DIR = Path(rf"\\{M.PC_ADDRESS}\{M.SHARE}\Enhance")
TO = "_TOENHANCE"
PIECE_EXT = ".mp4"
LOCK_EXT = ".lock"
DONE_SUFFIX = "_ENHANCED.mp4"
PART_SUFFIX = "_ENHANCING.part.mp4"
FAILED_SUFFIX = "_ENHANCE_FAILED.txt"
STOP_SUFFIX = "_TOENHANCE.stop"     # 21 Nov: "no Topaz for this video" -- the computer enhancing it stops (no_topaz)
PROJECT_SUB = "enhance"             # the project's own copy: <project>\enhance\raw_enhanced.mp4
PROJECT_FILE = "raw_enhanced.mp4"
NO_WINDOW = 0x08000000 if os.name == "nt" else 0

# the user's Topaz settings (run 20): Proteus (prob-3) Manual -- revert compression 80, improve detail 80, sharpen 60,
# reduce noise 80, dehalo 10, anti-alias/deblur -60, add noise 0, recover original detail 10 -- at 2x the piece's own
# size; Apollo (apo-8) to 60 fps, no slow motion, duplicate frames not replaced; no stabilization, motion deblur or
# grain; H.265 Main10 at a constant 60 Mb/s (180 until 17 Nov) in an mp4, the sound copied
PROTEUS = {"model": "prob-3", "scale": 2, "preblur": -0.6, "noise": 0.8, "details": 0.8, "halo": 0.1, "blur": 0.6,
           "compression": 0.8, "prenoise": 0, "grain": 0, "gsize": 0, "blend": 0.1, "estimate": 0}
APOLLO = {"model": "apo-8", "slowmo": 1, "rdt": -0.000001, "fps": OUT_FPS}
GPU = {"device": 0, "vram": 1, "instances": 1}


class EnhanceError(RuntimeError):
    """Why a piece was not made / not enhanced / not used, in plain words."""


def is_late(text: str) -> bool:
    """A row's "not enhanced" note that means a late piece may still come (its "Use the enhanced piece")."""
    return TIMEOUT_WHY in (text or "") or STILL_WHY in (text or "")


def _clock(s: float) -> str:
    s = max(0, int(round(float(s))))
    h, m, sec = s // 3600, (s % 3600) // 60, s % 60
    return f"{h}:{m:02d}:{sec:02d}" if h else f"{m}:{sec:02d}"


def _minutes(s: float) -> str:
    m = max(1, int(math.ceil(float(s) / 60.0)))
    return f"about {m} min"


# ---------------------------------------------------------------------------------------------------------------------
# the settings
# ---------------------------------------------------------------------------------------------------------------------

def _kind(settings: Any) -> str:
    try:
        return M.which(settings)
    except Exception:  # noqa: BLE001
        return M.PC


def share_dir(settings: Any = None) -> Path:
    """The Enhance folder: Settings' "Enhance folder", else D:\\Shared\\Enhance on the PC and the same folder over
    the network on the laptop."""
    v = str(getattr(settings, "enhance_dir", "") or "").strip()
    if v:
        return Path(v)
    return LAPTOP_DIR if _kind(settings) == M.LAPTOP else PC_DIR


def _default_off() -> bool:
    return os.environ.get(ENHANCE_ENV, "").strip() == "0"     # (the app's tests: off unless a test says)


def sends(settings: Any = None) -> bool:
    """"Enhance the scene on the laptop first" (the PC's switch: on by default there, off on the laptop)."""
    v = getattr(settings, "enhance_send", None)
    return bool(v) if isinstance(v, bool) else (_kind(settings) == M.PC and not _default_off())


def enhances(settings: Any = None) -> bool:
    """"Enhance pieces for the PC with Topaz" (the laptop's switch: on by default there, off on the PC)."""
    v = getattr(settings, "enhance_topaz", None)
    return bool(v) if isinstance(v, bool) else (_kind(settings) == M.LAPTOP and not _default_off())


def fake() -> bool:
    return os.environ.get(FAKE_ENV, "").strip() not in ("", "0")


# ---------------------------------------------------------------------------------------------------------------------
# the names in the Enhance folder
# ---------------------------------------------------------------------------------------------------------------------

def safe_name(name: str) -> str:
    """A project's name as a file name in the Enhance folder (no characters Windows refuses)."""
    bad = '<>:"/\\|?*'
    s = "".join("_" if c in bad or ord(c) < 32 else c for c in str(name or "")).strip(" .")
    return s or "project"


@dataclass
class Names:
    folder: Path
    name: str

    def _p(self, suffix: str) -> Path:
        return Path(self.folder) / f"{self.name}{suffix}"

    @property
    def piece(self) -> Path:
        return self._p(TO + PIECE_EXT)

    @property
    def info(self) -> Path:
        return self._p(TO + ".json")

    @property
    def lock(self) -> Path:
        return self._p(TO + LOCK_EXT)

    @property
    def part(self) -> Path:
        return self._p(PART_SUFFIX)

    @property
    def enhanced(self) -> Path:
        return self._p(DONE_SUFFIX)

    @property
    def failed(self) -> Path:
        return self._p(FAILED_SUFFIX)

    @property
    def stop(self) -> Path:
        return self._p(STOP_SUFFIX)

    def all(self) -> list[Path]:
        return [self.piece, self.info, self.lock, self.part, self.enhanced, self.failed, self.stop]


def names(folder: str | os.PathLike, name: str) -> Names:
    return Names(Path(folder), safe_name(name))


# ---------------------------------------------------------------------------------------------------------------------
# PC: the piece
# ---------------------------------------------------------------------------------------------------------------------

def plan(m: Any, places: list[float], win_s: float, max_s: float = MAX_PIECE_S):
    """The piece's frames (an excerpt.Plan) around the found ``places`` (RAW seconds, each ``win_s`` long): the scene
    in the middle, at most ``max_s`` in all, from a keyframe to a keyframe (or the RAW's end). Raises EnhanceError
    (plain words) when there is none."""
    from match_cuts.excerpt import Plan
    f = float(m.rate)
    dur = float(m.duration)
    s0 = max(0.0, min(places))
    s1 = min(dur, max(places) + win_s)
    if s1 - s0 > max_s:
        raise EnhanceError(f"the scene is {_clock(s1 - s0)} long (places far apart or a montage) -- more than "
                           f"{_clock(max_s)}")
    limit = int(math.floor(max_s * f + 1e-6))
    keys = sorted(m.keyframes)
    if not keys:
        raise EnhanceError("the RAW has no keyframe to start a piece from")
    if dur <= max_s and keys[0][0] == 0:
        return Plan(0, int(m.frames), int(keys[0][1]), None, sorted(float(x) for x in places))
    margin = (max_s - (s1 - s0)) / 2.0
    lo, hi = s0 - margin, s1 + margin
    if lo < 0:
        lo, hi = 0.0, hi - lo
    if hi > dur:
        lo, hi = max(0.0, lo - (hi - dur)), dur
    want0, need1 = int(math.floor(lo * f)), int(math.ceil(s1 * f))
    starts = sorted((k for k in keys if k[0] <= int(math.floor(s0 * f))), key=lambda k: (abs(k[0] - want0), -k[0]))
    for k0, pts0, _d in starts:
        if m.frames - k0 <= limit:                       # (the RAW ends within the 2 minutes: up to its end)
            return Plan(int(k0), int(m.frames), int(pts0), None, sorted(float(x) for x in places))
        ends = [k for k in keys if need1 <= k[0] <= k0 + limit]
        if ends:
            k1, _p1, dts_end = max(ends)
            return Plan(int(k0), int(k1), int(pts0), int(dts_end), sorted(float(x) for x in places))
    raise EnhanceError(f"the RAW's keyframes are too far apart for a piece of at most {_clock(max_s)} around the "
                       "scene")


KEYFRAME_ROOM_S = 30.0              # a keyframe this far before the span / after it still starts / ends the piece


def _span_plan(m: Any, s0: float, s1: float, room_s: float = KEYFRAME_ROOM_S):
    """18 Nov (the projects: 18 of the 26 pieces cut after the edit were ENCODED first -- "the RAW's keyframes are
    too far apart for a piece of at most 0:39": the cut had 2 s of room, a film's keyframes are up to 10 s apart --
    so Topaz got an 8-bit re-encode instead of the RAW's own frames): the piece from the last keyframe at or before
    ``s0`` to the first keyframe at or after ``s1`` (or the RAW's end), each at most ``room_s`` away. What that adds
    before and after the span is not enhanced (enhance_swap.play_ranges leaves it out). None when the RAW has no
    such keyframes (the caller's old rule, then an encoded piece)."""
    from match_cuts.excerpt import Plan
    f = float(m.rate)
    keys = sorted(m.keyframes or [])
    if not keys or f <= 0:
        return None
    first, last = int(math.floor(s0 * f + 1e-6)), int(math.ceil(s1 * f - 1e-6))
    before = [k for k in keys if k[0] <= first]
    if not before:
        return None
    k0, pts0, _d = before[-1]
    if (first - k0) / f > room_s:
        return None
    after = [k for k in keys if k[0] >= last and k[0] > k0]
    if after:
        k1, _p1, dts_end = after[0]
        if (k1 - last) / f > room_s:
            return None
        return Plan(int(k0), int(k1), int(pts0), int(dts_end), [float(s0)])
    if (int(m.frames) - last) / f > room_s or int(m.frames) <= k0:
        return None
    return Plan(int(k0), int(m.frames), int(pts0), None, [float(s0)])          # (the RAW ends there)


# 19 Nov (your call: "make the second try use another way of making the piece ... and the third another again" --
# until now a piece Topaz failed on, or whose result failed its check, went again made the very same way, so it could
# fail three times for one reason: 18 Nov, The Most Jealous Group Leader, "its first frame is at 0.033 s" x3):
# the ways a video's piece is made, in the order they are tried -- (how the piece is made, only the played parts).
COPY, ENCODED, INTRA = "copy", "encoded", "intra"
WAYS = ((COPY, True),               # 1st: the RAW's own frames (stream copy), Topaz on the played parts only
        (ENCODED, False),           # 2nd: an encoded piece (x264, near lossless), the whole piece
        (COPY, False),              # 3rd: the RAW's own frames, the whole piece
        (INTRA, False),             # 4th: encoded with every frame a keyframe, the whole piece
        (ENCODED, True), (INTRA, True))     # (only reached when ways above come out the same for this RAW)
_SAME_WAY = ("gb free", "not in the enhance folder any more", "the enhance folder could not be read",
             "no such file or directory")                 # (21 Nov: the network, not the piece)    # failures that say nothing about the piece itself
#                                                                  (the drive is full, the piece was removed)


def way_text(kind: str, parts: bool) -> str:
    how = {COPY: "the RAW's own frames", ENCODED: "an encoded piece",
           INTRA: "an encoded piece, every frame a keyframe"}.get(kind, str(kind))
    return f"{how}, {'only the played parts' if parts else 'the whole piece'}"


def piece_way(info: dict | None) -> tuple[str, bool]:
    """How a piece was really made (its json): a copy that could not be cut is "encoded", parts not worth it "whole"."""
    info = info or {}
    kind = INTRA if info.get("intra") else ENCODED if info.get("encoded") else COPY
    return kind, bool(valid_ranges(info))


def ways_tried(tries: list | None) -> set:
    """The ways that already failed for this video (asked for, and what came out) -- not a try that failed for a
    reason that says nothing about the piece (``_SAME_WAY``): that way is tried again."""
    out: set = set()
    for t in tries or []:
        if not isinstance(t, dict) or not t.get("why") or t.get("same_way"):
            continue
        for key in ("asked", "way", "also"):
            w = t.get(key)
            if isinstance(w, (list, tuple)) and len(w) == 2:
                out.add((str(w[0]), bool(w[1])))
        a, w = t.get("asked"), t.get("way")
        if isinstance(a, (list, tuple)) and isinstance(w, (list, tuple)) and a and w and a[0] == COPY != w[0]:
            out |= {(COPY, True), (COPY, False)}         # this RAW cannot be cut by copy: asking again changes nothing
    return out


def next_way(tries: list | None) -> tuple[str, bool]:
    """The way the next piece is made: the first of ``WAYS`` that has not failed for this video; when all have, the
    first again."""
    done = ways_tried(tries)
    return next((w for w in WAYS if w not in done), WAYS[0])


def note_failure(project: str | os.PathLike, why: str, details: str = "") -> dict:
    """19 Nov: a try that failed is written down in the project, whole: project.json's enhance "tries" (which try,
    how its piece was made, the full reason, when) and logs/topaz_failed.log (the reason and everything Topaz
    said). Until now the reason was overwritten when the piece went again. Returns the try. Never raises."""
    try:
        from . import projects as PJ
        p = PJ.load(project)
        rec = p.data.get("enhance") if isinstance(p.data.get("enhance"), dict) else {}
        tries = [dict(t) for t in (rec.get("tries") or []) if isinstance(t, dict)]
        if not tries or tries[-1].get("why"):            # a piece sent by the app before 19 Nov: its try, from its json
            tries.append({"n": len(tries) + 1, "piece": rec.get("name"), "sent": rec.get("sent"),
                          "way": list(piece_way(rec.get("piece") if isinstance(rec.get("piece"), dict) else {}))})
        t = tries[-1]
        t["why"], t["failed"] = str(why), time.time()
        if any(x in str(why).lower() for x in _SAME_WAY):
            t["same_way"] = True
        if "the parts could not be used" in f"{why} {details}".lower() and t.get("way"):
            t["also"] = [t["way"][0], False]             # (the laptop went on with the whole piece: that failed too)
        p.update(enhance={"tries": tries})
        d = Path(project) / "logs"
        d.mkdir(parents=True, exist_ok=True)
        w = t.get("way") or t.get("asked") or [COPY, False]
        with open(d / FAILURE_LOG, "a", encoding="utf-8") as f:
            f.write(f"=== {time.strftime('%Y-%m-%d %H:%M:%S')}  try {t.get('n')} of {t.get('piece')} "
                    f"({way_text(str(w[0]), bool(w[1]))})\n{why}\n" + (f"{details.strip()}\n" if details.strip()
                                                                        else "") + "\n")
        return t
    except Exception:  # noqa: BLE001
        return {}


def _run(cmd: list[str], timeout: float = 1800.0) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout,
                          creationflags=NO_WINDOW)


def _aac(mov: Path, dst: Path, channels: int) -> None:
    """The piece (.mov, PCM sound) into an mp4: the same video packets, the sound AAC (Topaz's ffmpeg cannot write
    PCM into an mp4)."""
    from match_cuts.common import ffmpeg_bin
    rate = "320k" if channels <= 2 else "640k"
    tmp = dst.with_name(dst.stem + ".part" + dst.suffix)
    r = _run([ffmpeg_bin(), "-hide_banner", "-v", "error", "-nostdin", "-y", "-i", str(mov), "-map", "0:v:0",
              "-map", "0:a:0?", "-c:v", "copy", "-c:a", "aac", "-b:a", rate, "-map_metadata", "-1",
              "-movflags", "+faststart", "-f", "mp4", str(tmp)])
    if r.returncode != 0 or not tmp.is_file():
        tmp.unlink(missing_ok=True)
        raise EnhanceError(f"the piece could not be written as an mp4: {(r.stderr or '').strip()[-300:]}")
    os.replace(tmp, dst)


def make_piece(raw: str | os.PathLike, name: str, out_dir: str | os.PathLike, span: tuple[float, float],
               project: str | os.PathLike | None = None, work: str | os.PathLike | None = None,
               progress: Callable[[float, str], None] | None = None, topaz_on: str = "", way: str = "") -> dict:
    """The piece to enhance, put in ``out_dir`` (the Enhance folder) as <name>_TOENHANCE.mp4 + .json: the RAW from
    ``span[0]`` to ``span[1]`` seconds -- what the finished edit plays, +- 10 s (enhance_swap.cover_all) -- cut by
    stream copy from the keyframe before to the keyframe after. Returns its info (the json's content); raises
    EnhanceError with the reason in plain words -- then the edit keeps the normal RAW.

    18 Nov: until 17 Nov a piece was also guessed BEFORE the edit (the quick sound check found the scene; a short RAW
    went whole) -- that path is gone (your call: edit, then Topaz, then the zip), and with it find_scene,
    piece_length and the whole-RAW rule.

    19 Nov: ``way`` -- "" / "copy": as above; "encoded" / "intra": the piece is encoded on purpose (another try
    after Topaz failed on the copied one: ``WAYS``)."""
    from match_cuts import excerpt as X
    say = progress or (lambda share, text: None)
    raw = Path(raw)
    nm = names(out_dir, name)
    work = Path(work) if work else Path(project or raw.parent) / PROJECT_SUB
    t0 = time.perf_counter()
    say(0.4, "reading the RAW's keyframes")
    m = X.movie_facts(str(raw))
    places = [max(0.0, float(span[0]))]
    dur_all = float(m.duration or 0.0) or float(_probe_raw(raw).get("duration") or 0.0)   # 9 Nov: 0 for a RAW
    end = min(dur_all, float(span[1])) if dur_all > 0 else float(span[1])                 #   that cannot be cut
    win = max(1.0, end - places[0])                                                       #   (Car Salesman: 3 s)
    max_s = win + 2.0
    if way in (ENCODED, INTRA):                          # 19 Nov: asked for (a try after a failure)
        return _encoded_piece(raw, places, max_s, nm, name, project,
                              "another way of making the piece, after Topaz failed", t0, say, topaz_on, win,
                              intra=way == INTRA)
    if m.why:                                            # 29 Oct: then an encoded piece (near lossless) instead
        return _encoded_piece(raw, places, max_s, nm, name, project, m.why, t0, say, topaz_on, win)
    try:
        p = _span_plan(m, places[0], places[0] + win) or plan(m, places, win, max_s)
    except EnhanceError as e:                            # 30 Oct: keyframes too far apart for a lossless cut -> an
        if "keyframe" not in str(e):                     # encoded piece of the same window
            raise
        return _encoded_piece(raw, places, max_s, nm, name, project, str(e), t0, say, topaz_on, win)
    n_plan = p.k1 - p.k0
    work.mkdir(parents=True, exist_ok=True)
    mov = work / "piece.mov"
    say(0.55, "cutting the piece (stream copy)")
    try:
        X.cut_piece(m, p, mov)
        n = X._frames_in(mov)
        if n <= 0 or n > n_plan:
            raise EnhanceError(f"the piece has {n} frames, the plan {n_plan}")
        say(0.7, "making its sound AAC")
        Path(out_dir).mkdir(parents=True, exist_ok=True)
        for f in nm.all():                               # (an old try of the same project)
            f.unlink(missing_ok=True)
        _aac(mov, nm.piece, m.a_channels)
        say(0.8, "checking its frames against the RAW")
        n2 = X._frames_in(nm.piece)
        if n2 != n:
            raise EnhanceError(f"the mp4 has {n2} frames, the piece {n}")
        chk = X.same_frames(str(nm.piece), m, p.k0, n)
        if not chk["ok"]:
            raise EnhanceError(f"the piece does not show the RAW's frames: {chk['why']}")
    except Exception as e:  # noqa: BLE001 - 30 Oct: the lossless cut is not right (HEVC: 824 frames for 820) ->
        nm.piece.unlink(missing_ok=True)                # an encoded piece of the same window instead
        mov.unlink(missing_ok=True)
        return _encoded_piece(raw, places, max_s, nm, name, project, f"the lossless cut failed: {e}", t0, say,
                              topaz_on, win)
    finally:
        mov.unlink(missing_ok=True)
    fps = m.rate
    a, b = p.k0 / float(fps), (p.k0 + n) / float(fps)
    info = {"project": safe_name(name), "project_folder": str(project or ""), "computer": M.host(),
            "raw": os.path.abspath(str(raw)), "raw_name": raw.name, "start_frame": int(p.k0), "start_s": round(a, 6),
            "frames": int(n), "fps": str(fps), "duration": round(n / float(fps), 6), "width": int(m.width),
            "height": int(m.height), "scene": [round(min(places), 3), round(max(places) + win, 3)],
            "places": [round(x, 3) for x in places], "raw_duration": round(float(m.duration), 3),
            "bytes": int(nm.piece.stat().st_size), "check": chk.get("rows"), "made": time.time(),
            "seconds": round(time.perf_counter() - t0, 1),
            "where": f"{_clock(a)}-{_clock(b)} of {_clock(m.duration)}"}
    info["topaz_on"] = topaz_on
    _write_json(nm.info, info)
    say(1.0, f"the piece {info['where']} is in the Enhance folder")
    return info


def _encoded_piece(raw: Path, places: list[float], max_s: float, nm: "Names", name: str, project: Any,
                   why_not_copy: str, t0: float, say: Callable[[float, str], None], topaz_on: str,
                   win: float, intra: bool = False) -> dict:
    """29 Oct: the piece when the RAW cannot be cut losslessly (uneven frame timing, ...): the same time window
    encoded near-lossless (x264 CRF 10, the RAW's frame rate, AAC). Raises EnhanceError (plain words).
    19 Nov: ``intra`` -- every frame is a keyframe and none waits for a later one (no B-frames): the plainest
    file a reader can get (the last way tried after Topaz failed on the others)."""
    from match_cuts.common import ffmpeg_bin
    st = _probe_raw(raw)
    fps = st["fps"]
    dur = st["duration"]
    if not fps or not dur:
        raise EnhanceError(f"the RAW cannot be cut losslessly ({why_not_copy}) and its frame rate or length can't "
                           "be read")
    s0, s1 = max(0.0, min(places)), min(dur, max(places) + win)
    if s1 - s0 > max_s:
        raise EnhanceError(f"the scene is {_clock(s1 - s0)} long (places far apart or a montage) -- more than "
                           f"{_clock(max_s)}")
    margin = (max_s - (s1 - s0)) / 2.0
    lo, hi = s0 - margin, s1 + margin
    if lo < 0:
        lo, hi = 0.0, hi - lo
    if hi > dur:
        lo, hi = max(0.0, lo - (hi - dur)), dur
    # 18 Nov (The Most Jealous Group Leader, made again: "Topaz's result is not right: its first frame is at 0.033 s,
    # not at 0", three times, and the video stayed on the normal RAW): the window started between two frames, so
    # the encoded piece's first frame came up to one frame after 0 -- about every second encoded piece. It starts
    # ON a frame now (read from a fifth of a millisecond before it), and its start says that frame.
    first = int(math.ceil(lo * float(fps) - 1e-6))
    lo = first / float(fps)
    say(0.55, "encoding the piece (the RAW can't be cut losslessly)")
    Path(nm.piece).parent.mkdir(parents=True, exist_ok=True)
    for f in nm.all():
        f.unlink(missing_ok=True)
    tmp = nm.piece.with_name(nm.piece.stem + ".part" + nm.piece.suffix)
    r = _run([ffmpeg_bin(), "-hide_banner", "-v", "error", "-nostdin", "-y", "-ss", f"{max(0.0, lo - 0.0002):.6f}",
              "-i", str(raw),
              "-t", f"{hi - lo:.6f}", "-map", "0:v:0", "-map", "0:a:0?", "-vf", f"fps={fps}",
              "-c:v", "libx264", "-preset", "medium", "-crf", "10", "-pix_fmt", "yuv420p",
              *(["-g", "1", "-bf", "0"] if intra else []),
              "-c:a", "aac", "-b:a", "320k", "-map_metadata", "-1", "-movflags", "+faststart", "-f", "mp4", str(tmp)])
    if r.returncode != 0 or not tmp.is_file():
        tmp.unlink(missing_ok=True)
        raise EnhanceError(f"the piece could not be encoded: {(r.stderr or '').strip()[-300:]}")
    os.replace(tmp, nm.piece)
    from match_cuts import excerpt as X
    n = X._frames_in(nm.piece)                                                # noqa: SLF001
    if n <= 0:
        nm.piece.unlink(missing_ok=True)
        raise EnhanceError("the encoded piece has no frames")
    f = float(fps)
    info = {"project": safe_name(name), "project_folder": str(project or ""), "computer": M.host(),
            "raw": os.path.abspath(str(raw)), "raw_name": raw.name, "start_frame": int(first),
            "start_s": round(lo, 6), "frames": int(n), "fps": str(fps), "duration": round(n / f, 6),
            "width": st["width"], "height": st["height"], "scene": [round(s0, 3), round(s1, 3)],
            "places": [round(x, 3) for x in places], "raw_duration": round(dur, 3),
            "bytes": int(nm.piece.stat().st_size), "check": None, "made": time.time(),
            "seconds": round(time.perf_counter() - t0, 1), "encoded": True, "why_encoded": why_not_copy,
            "topaz_on": topaz_on, **({"intra": True} if intra else {}),
            "where": f"{_clock(lo)}-{_clock(lo + n / f)} of {_clock(dur)}"}
    _write_json(nm.info, info)
    say(1.0, f"the piece {info['where']} is in the Enhance folder (encoded)")
    return info


def _probe_raw(raw: Path) -> dict:
    """{fps (Fraction or None), duration, width, height} of the RAW's first video stream."""
    from match_cuts.common import ffprobe_bin
    r = _run([ffprobe_bin(), "-v", "error", "-select_streams", "v:0", "-show_entries",
              "stream=width,height,avg_frame_rate,r_frame_rate:format=duration", "-of", "json", str(raw)], timeout=120)
    try:
        d = json.loads(r.stdout or "{}")
        v = (d.get("streams") or [{}])[0]
        fr = None
        for k in ("avg_frame_rate", "r_frame_rate"):
            try:
                x = Fraction(str(v.get(k) or "0/1"))
            except (ValueError, ZeroDivisionError):
                continue
            if 0 < x <= 120:
                fr = x.limit_denominator(1001)
                for std in (Fraction(24000, 1001), Fraction(30000, 1001), Fraction(60000, 1001), Fraction(24),
                            Fraction(25), Fraction(30), Fraction(50), Fraction(60)):
                    if abs(float(x) - float(std)) < 0.002:       # 4 Nov: 21027/877 -> 24000/1001
                        fr = std
                break
        return {"fps": fr, "duration": float((d.get("format") or {}).get("duration") or 0.0),
                "width": int(v.get("width") or 0), "height": int(v.get("height") or 0)}
    except (ValueError, TypeError):
        return {"fps": None, "duration": 0.0, "width": 0, "height": 0}


def _write_json(path: Path, d: dict) -> None:
    tmp = path.with_name(path.name + f".{os.getpid()}.tmp")
    tmp.write_text(json.dumps(d, indent=1), encoding="utf-8")
    os.replace(tmp, path)


def read_info(path: str | os.PathLike) -> dict | None:
    try:
        d = json.loads(Path(path).read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else None
    except (OSError, ValueError):
        return None


# ---------------------------------------------------------------------------------------------------------------------
# the check of an enhanced piece (the laptop before the rename, the PC before it uses it)
# ---------------------------------------------------------------------------------------------------------------------

def valid_ranges(info: dict) -> list[tuple[int, int]]:
    """18 Nov: the parts of the piece to enhance (its json's "ranges": [first frame, frame after the last] in the
    piece's own frames, made by enhance_swap.play_ranges) -- only when they are sound: in order, not touching,
    inside the piece, and not simply all of it. Else [] (the whole piece, as before)."""
    try:
        rs = [(int(a), int(b)) for a, b in (info.get("ranges") or [])]
        n = int(info.get("frames") or 0)
    except (TypeError, ValueError):
        return []
    if not rs or n <= 0 or rs[0][0] < 0 or rs[-1][1] > n or rs == [(0, n)]:
        return []
    at = -1
    for a, b in rs:
        if a <= at or b <= a:
            return []
        at = b
    return rs


def range_out_frames(frames: int, fps: Any) -> int:
    """How many 60 fps frames a part of ``frames`` frames becomes -- the same number on the computer that enhances
    it (it writes exactly that many) and on the one that maps the edit's clips onto it."""
    try:
        f = Fraction(str(fps))
    except (ValueError, ZeroDivisionError):
        return 0
    return int(round(Fraction(int(frames)) * OUT_FPS / f)) if f > 0 else 0


def ranges_out_frames(info: dict) -> int:
    return sum(range_out_frames(b - a, info.get("fps")) for a, b in valid_ranges(info))


def _probe(path: Path) -> dict:
    from match_cuts.common import ffprobe_bin
    r = _run([ffprobe_bin(), "-v", "error", "-select_streams", "v:0", "-count_packets", "-show_entries",
              "stream=codec_name,width,height,r_frame_rate,avg_frame_rate,nb_read_packets,nb_frames,start_time,"
              "has_b_frames",
              "-of", "json", str(path)], timeout=600)
    if r.returncode != 0:
        raise EnhanceError(f"it cannot be read: {(r.stderr or '').strip()[-200:]}")
    st = (json.loads(r.stdout or "{}").get("streams") or [None])[0]
    if not st:
        raise EnhanceError("it has no picture")
    return st


def check_enhanced(path: str | os.PathLike, info: dict) -> dict:
    """An enhanced piece is usable: the piece's length within one of its frames, 60 fps, twice its size, and it
    decodes from the first frame. {ok, why, width, height, fps, frames, duration}."""
    from match_cuts.common import ffmpeg_bin
    path = Path(path)
    out: dict = {"ok": False, "why": ""}
    try:
        if not path.is_file() or path.stat().st_size == 0:
            raise EnhanceError("the file is empty or not there")
        st = _probe(path)
        w, h = int(st.get("width") or 0), int(st.get("height") or 0)
        fr = Fraction(str(st.get("r_frame_rate") or "0/1"))
        n = int(st.get("nb_read_packets") or st.get("nb_frames") or 0)
        out.update(width=w, height=h, fps=str(fr), frames=n)
        k = topaz_scale(info.get("width"), info.get("height"))
        want_w, want_h = k * int(info.get("width") or 0), k * int(info.get("height") or 0)
        if (w, h) != (want_w, want_h):
            raise EnhanceError(f"it is {w}x{h}, not {'twice ' if k == 2 else ''}the piece's size ({want_w}x{want_h})")
        if fr != OUT_FPS:
            raise EnhanceError(f"it is {float(fr):.3f} fps, not {OUT_FPS}")
        dur = n / float(OUT_FPS)
        out["duration"] = round(dur, 4)
        piece_fps = float(Fraction(str(info.get("fps") or "0/1")) or 0) or OUT_FPS
        want = float(info.get("duration") or 0.0)
        short_ok = 1.0 / piece_fps + 0.15 + 1e-3      # 29 Oct: Apollo makes no new frames after the piece's last one
        parts = ranges_out_frames(info)               # 18 Nov: only the played parts, each an exact number of frames
        if parts and n == parts:
            out["ranged"] = True
        elif dur - want > 1.0 / piece_fps + 1e-3 or want - dur > short_ok:   # (a few 60 fps frames short is fine)
            raise EnhanceError(f"it is {dur:.3f} s long, the piece {want:.3f} s (more than one frame apart)" +
                               (f" -- and not its {len(valid_ranges(info))} played parts either ({parts} frames, it "
                                f"has {n})" if parts else ""))
        start = float(st.get("start_time") or 0.0)
        if abs(start) > 1.0 / OUT_FPS:
            raise EnhanceError(f"its first frame is at {start:.3f} s, not at 0")
        r = _run([ffmpeg_bin(), "-hide_banner", "-v", "error", "-nostdin", "-i", str(path), "-map", "0:v:0",
                  "-frames:v", "3", "-f", "null", "-"], timeout=300)
        if r.returncode != 0 or (r.stderr or "").strip():
            raise EnhanceError(f"its first frames do not decode: {(r.stderr or '').strip()[-200:] or r.returncode}")
        out["ok"] = True
    except EnhanceError as e:
        out["why"] = str(e)
    except Exception as e:  # noqa: BLE001
        out["why"] = f"it could not be checked: {e}"
    return out


# ---------------------------------------------------------------------------------------------------------------------
# PC: what came back
# ---------------------------------------------------------------------------------------------------------------------

@dataclass
class Back:
    state: str                      # "waiting" | "enhancing" | "enhanced" | "failed" | "gone"
    text: str = ""                  # plain words for the row
    share: float = 0.0              # the laptop's progress, when it said so
    path: Path | None = None


def status(folder: str | os.PathLike, name: str, now: float | None = None) -> Back:
    """What the Enhance folder says about a project's piece."""
    nm = names(folder, name)
    try:
        if nm.enhanced.is_file():
            return Back("enhanced", "the enhanced piece is back", 1.0, nm.enhanced)
        if nm.failed.is_file():
            try:
                why = nm.failed.read_text(encoding="utf-8", errors="replace").strip().splitlines()
            except OSError:
                why = []
            return Back("failed", (why[0] if why else "Topaz could not enhance it"), 0.0, nm.failed)
        lk = read_lock(nm.lock)
        if lk is not None and not lock_stale(nm.lock, now):
            pct = float(lk.get("share") or 0.0)
            left = lk.get("left_s")
            who = "the laptop" if lk.get("machine") == M.LAPTOP else (lk.get("computer") or "the laptop")
            txt = f"enhancing on {who} · {int(round(100 * pct))} %" + (f" · {_minutes(left)} left"
                                                                       if left else "")
            return Back("enhancing", txt, pct, nm.lock)
        if nm.piece.is_file():
            return Back("waiting", "waiting for the laptop", 0.0, nm.piece)
        return Back("gone", "the piece is not in the Enhance folder any more")
    except OSError as e:
        return Back("waiting", f"the Enhance folder cannot be read now ({e.strerror or e})")


def take_back(folder: str | os.PathLike, name: str, project: str | os.PathLike, info: dict | None = None) -> dict:
    """The enhanced piece checked and copied into the project (<project>\\enhance\\raw_enhanced.mp4: one copy, it
    replaces an older one), then the share's files deleted. {ok, why, file, check}."""
    nm = names(folder, name)
    there = read_info(nm.info) or {}
    info = info or there
    if there.get("worker_note"):                         # 19 Nov: e.g. "the parts could not be used (...)"
        info = dict(info, worker_note=str(there["worker_note"]))
    if there.get("parts_failed"):                        # 21 Nov: the parts' failure, whole (enhance.json + project)
        info = dict(info, parts_failed=str(there["parts_failed"]))
        try:
            from . import projects as PJ
            PJ.load(project).update(enhance={"parts_failed": str(there["parts_failed"])})
        except Exception:  # noqa: BLE001
            pass
    chk = check_enhanced(nm.enhanced, info)
    if not chk["ok"]:
        return {"ok": False, "why": chk["why"], "check": chk}
    dst_dir = Path(project) / PROJECT_SUB
    dst_dir.mkdir(parents=True, exist_ok=True)
    dst = dst_dir / PROJECT_FILE
    tmp = dst.with_name(dst.stem + ".part" + dst.suffix)
    try:
        shutil.copyfile(nm.enhanced, tmp)
        if tmp.stat().st_size != nm.enhanced.stat().st_size:
            raise OSError("the copy is not the same size")
        os.replace(tmp, dst)
    except OSError as e:
        tmp.unlink(missing_ok=True)
        return {"ok": False, "why": f"it could not be copied into the project: {e}", "check": chk}
    _write_json(dst_dir / "enhance.json", dict(info, enhanced=chk, copied=time.time()))
    clear(folder, name)
    return {"ok": True, "why": "", "file": str(dst), "check": chk, "note": str(info.get("worker_note") or "")}


def tidy_project(project: str | os.PathLike) -> list[str]:
    """29 Oct, your call: a finished project whose edit plays the enhanced piece keeps ONE video of it and no RAW:
    - the enhanced piece once: enhance\\raw_enhanced.mp4 goes when the edit's own copy (edit*\\extras\\media\\raw.mp4)
      is the same file (a hard link or the same size)
    - the RAW in the project folder goes when no edit XML plays it (a RAW kept elsewhere -- the torrent folder,
      input\\downloads -- is never touched)
    Returns what was removed (also written into project.json's "enhance": "tidied")."""
    from . import projects as PJ
    try:
        p = PJ.load(project)
    except Exception:  # noqa: BLE001
        return []
    rec = p.data.get("enhance") if isinstance(p.data.get("enhance"), dict) else {}
    if rec.get("state") != "used":
        return []
    folder = Path(p.folder)
    enh = folder / PROJECT_SUB / PROJECT_FILE
    media = [m for m in sorted(folder.glob("edit*/extras/media/raw.mp4")) if m.is_file()]
    if not media and not enh.is_file():
        return []
    xml_text = ""
    for x in folder.glob("edit*/*.xml"):
        try:
            xml_text += x.read_text(encoding="utf-8", errors="replace")
        except OSError:
            pass
    removed: list[str] = []
    if enh.is_file() and media:
        linked, same = False, False
        try:
            linked = os.path.samefile(enh, media[0])         # 4 Nov: a hard link costs no space -- kept, so Try again
            same = not linked and enh.stat().st_size == media[0].stat().st_size   # still finds the piece
        except OSError:
            pass
        if same and PROJECT_FILE not in xml_text:
            try:
                enh.unlink()
                removed.append(str(enh.relative_to(folder)))
            except OSError:
                pass
    raw = p.file("raw")
    try:
        inside = raw is not None and raw.is_file() and raw.resolve().parent == folder.resolve()
    except OSError:
        inside = False
    src = Path(str((p.data.get("raw") or {}).get("source_file") or ""))
    try:                                                   # 4 Nov: only when the RAW is still kept elsewhere (the
        elsewhere = (bool(src.name) and src.is_file() and src.suffix.lower() == raw.suffix.lower()  # torrent /
                     and src.stat().st_size == raw.stat().st_size and not os.path.samefile(src, raw))  # Downloads)
    except (OSError, TypeError):                           # Try again reads it there (project.json "kept_at")
        elsewhere = False
    if inside and media and elsewhere:
        from urllib.parse import quote
        plays = raw.name in xml_text or quote(raw.name) in xml_text
        if not plays:
            try:
                raw.unlink()
                removed.append(raw.name)
                p.update(raw=dict(p.data.get("raw") or {}, kept_at=str(src)))
            except OSError:
                pass
    if removed:
        try:
            p.update(enhance=dict(rec, tidied=removed, tidied_at=time.time()))
        except Exception:  # noqa: BLE001
            pass
    return removed


def clear(folder: str | os.PathLike, name: str) -> None:
    """The project's files in the Enhance folder go (only its own: the piece, its json, the lock, the result)."""
    for f in names(folder, name).all():
        try:
            f.unlink(missing_ok=True)
        except OSError:
            pass


_WATCH = threading.Lock()


def watch(project: str | os.PathLike, now: float | None = None, timeout_s: float = TIMEOUT_S) -> dict:
    """``_watch`` one at a time in this app (9 Nov: the rows and the round over all projects both look)."""
    with _WATCH:
        return _watch(project, now, timeout_s)


RESEND_DAYS = 3.0                      # 13 Nov: a piece never sent because of an error is sent again up to 3 days on
RESEND_GAP_S = 30 * 60                 #   one try per project every 30 minutes (both computers sweep)
_ON_PURPOSE = ("the quick check", "sending to topaz is off", "topaz is off")


def _resend_wanted(p, rec: dict) -> bool:
    """``_resend``'s quick questions (nothing is read but project.json's own record): is there a piece to send again
    now? 18 Nov: also a piece whose Topaz FAILED and could not be sent again at that moment (the Enhance folder or the
    RAW was away: the video stayed on the normal RAW for good) -- ``enhance_swap.cover_all`` stops after MAX_REDOS."""
    from .enhance_swap import MAX_REDOS, edit_made
    if rec.get("state") not in ("skipped", "failed"):
        return False
    why = str(rec.get("why") or "").lower()
    if not why or any(w in why for w in _ON_PURPOSE):
        return False
    if rec.get("state") == "failed" and int(rec.get("redos") or 0) >= MAX_REDOS:
        return False
    ed = (p.data.get("steps") or {}).get("edit") or {}
    if not edit_made(p.folder, ed):                      # (18 Nov: a FAIL with its 1_edit.xml too)
        return False
    try:
        fin = datetime.fromisoformat(str(ed.get("finished"))).timestamp()
    except (TypeError, ValueError):
        return False
    if time.time() - fin > RESEND_DAYS * 86400:
        return False
    if time.time() - float(rec.get("resend_try") or 0) < RESEND_GAP_S:
        return False
    return Path(share_dir()).is_dir()


def _resend(p, rec: dict) -> bool:
    """13 Nov (The Weirdest Name In British Comedy: "[WinError 1909] The referenced account is currently locked out"
    -- the piece was never sent and nothing tried again): a finished edit whose piece was skipped because of an
    error gets its piece now (``enhance_swap.cover_all``). Not when it was skipped on purpose. True when it was sent."""
    if not _resend_wanted(p, rec):
        return False
    rec2 = dict(rec, resend_try=time.time())
    p.update(enhance=rec2)
    from . import enhance_swap as ES
    r = ES.cover_all(p.folder)
    return bool(r.get("ok")) and int(r.get("clips") or 0) > 0


def sweep(roots, timeout_s: float = TIMEOUT_S, busy=None) -> list[str]:
    """9 Nov (Valentine's Day Plan: Topaz's second piece came back at 00:47 and nobody took it -- its row had finished):
    every project under ``roots`` whose piece is with Topaz is looked at (``watch``): an enhanced piece is taken and
    swapped in, whichever row (if any) sent it. Returns what changed. ``busy`` (18 Nov): a context manager entered only
    while a piece is really taken back or sent again (the window counts that as work; the look itself is not)."""
    import contextlib
    from . import projects as PJ
    work = busy or contextlib.nullcontext
    out: list[str] = []
    seen: set = set()
    for root in roots:
        try:
            items = PJ.list_projects(root)
        except Exception:  # noqa: BLE001
            continue
        for p in items:
            try:
                key = os.path.normcase(os.path.abspath(str(p.folder)))
                if key in seen:
                    continue
                seen.add(key)
                rec = p.data.get("enhance") if isinstance(p.data.get("enhance"), dict) else {}
                if _resend_wanted(p, rec):
                    with work():
                        sent = _resend(p, rec)
                    if sent:
                        out.append(f"{p.folder.name}: sent to Topaz again")
                        continue
                if rec.get("state") not in ("sent", "late") or not rec.get("folder") or not rec.get("name"):
                    continue
                if status(rec["folder"], rec["name"]).state != "enhanced":
                    continue                             # only a piece that is back (the rows do the waiting)
                with work():
                    r = watch(p.folder, timeout_s=timeout_s)
                if r.get("state") in ("used", "failed"):
                    out.append(f"{p.folder.name}: {r.get('state')} -- {r.get('text') or r.get('why') or ''}")
            except Exception:  # noqa: BLE001
                continue
    try:                                                 # 18 Nov: and the Enhance folder's left-overs, once a day
        global _CLEANED_AT
        if time.time() - _CLEANED_AT > CLEAN_EVERY_S:
            _CLEANED_AT = time.time()
            folder = share_dir()
            if Path(folder).is_dir():                    # (quick: a look at file dates -- not counted as work)
                out += clean_folder(folder, roots)
    except Exception:  # noqa: BLE001
        pass
    return out


CLEAN_AFTER_DAYS = 7.0                 # a piece nobody waits for is tidied away when it is this old
CLEAN_EVERY_S = 24 * 3600.0
TO_DELETE = "_to_delete"
_CLEANED_AT = 0.0


def clean_folder(folder: str | os.PathLike, roots, now: float | None = None,
                 days: float = CLEAN_AFTER_DAYS) -> list[str]:
    """18 Nov (D:\\Shared\\Enhance held 7 GB of enhanced pieces from 30 Oct - 11 Nov that no video was waiting for):
    the Enhance folder tidies itself. A piece -- its _TOENHANCE.mp4 / .json / .lock, _ENHANCING.part.mp4,
    _ENHANCED.mp4, _ENHANCE_FAILED.txt -- is a left-over when all its files are older than ``days``, nobody is
    enhancing it (no live lock) and no project under ``roots`` (nor the project its json names) still has it "with
    Topaz" under that name. Of a left-over, what the app can make again (the piece cut from the RAW, its json, a
    half-written result, the failure note) is deleted; a finished _ENHANCED.mp4 -- hours of Topaz -- is never
    deleted here: it is moved into the folder's _to_delete, for you to look at. Returns what was done."""
    from . import projects as PJ
    folder = Path(folder)
    now = time.time() if now is None else now
    out: list[str] = []
    waited: set[str] = set()                             # the names some project still waits for
    for root in roots or []:
        try:
            for p in PJ.list_projects(root):
                rec = p.data.get("enhance") if isinstance(p.data.get("enhance"), dict) else {}
                if rec.get("state") in ("sent", "late") and rec.get("name"):
                    waited.add(safe_name(str(rec["name"])))
        except Exception:  # noqa: BLE001 - a folder that cannot be read: its pieces are not known -- nothing is tidied
            return out
    suffixes = (TO + PIECE_EXT, TO + ".json", TO + LOCK_EXT, PART_SUFFIX, DONE_SUFFIX, FAILED_SUFFIX, STOP_SUFFIX)
    names: set[str] = set()
    try:
        for f in folder.iterdir():
            if f.is_file():
                for suf in suffixes:
                    if f.name.endswith(suf) and len(f.name) > len(suf):
                        names.add(f.name[: -len(suf)])
    except OSError:
        return out
    for name in sorted(names):
        nm = Names(folder, name)
        try:
            files = [f for f in nm.all() if f.is_file()]
            if not files or name in waited:
                continue
            if nm.lock.exists() and not lock_stale(nm.lock, now):
                continue
            if now - max(f.stat().st_mtime for f in files) < days * 86400:
                continue
            info = read_info(nm.info) or {}
            pf = _reachable(str(info.get("project_folder") or ""))
            if pf is not None:                           # the project its json names (it may be outside ``roots``)
                rec = (PJ.load(pf).data.get("enhance") or {})
                if isinstance(rec, dict) and rec.get("state") in ("sent", "late") and \
                        safe_name(str(rec.get("name") or "")) == name:
                    continue
            moved = ""
            if nm.enhanced.is_file():
                dst = folder / TO_DELETE
                dst.mkdir(exist_ok=True)
                target = dst / nm.enhanced.name
                if target.exists():
                    target = dst / f"{nm.enhanced.stem}.{int(now)}{nm.enhanced.suffix}"
                os.replace(nm.enhanced, target)
                moved = f", its enhanced picture moved to {TO_DELETE}"
            gone = 0
            for f in (nm.piece, nm.info, nm.lock, nm.part, nm.failed):
                try:
                    if f.is_file():
                        f.unlink()
                        gone += 1
                except OSError:
                    pass
            out.append(f"Enhance folder: {name} -- no video waits for it any more: {gone} file(s) removed{moved}")
        except Exception:  # noqa: BLE001 - that piece stays as it is
            continue
    return out


def _watch(project: str | os.PathLike, now: float | None = None, timeout_s: float = TIMEOUT_S) -> dict:
    """A project's piece, as its row needs it (project.json's "enhance" record, kept up to date here):
    {"state": "progress", share, text} while the laptop has it; "used" once the enhanced piece was checked and copied
    into the project; "failed" (why) -- the laptop's failure file, a piece that is gone or not right; "late" -- nothing
    came back within ``timeout_s`` (the piece stays: a late one makes "late_ready", only used when you say so); {}
    when there is nothing to watch."""
    from . import projects as PJ
    now = time.time() if now is None else now
    try:
        p = PJ.load(project)
    except Exception:  # noqa: BLE001
        return {}
    rec = p.data.get("enhance") if isinstance(p.data.get("enhance"), dict) else {}
    state = rec.get("state")
    if state not in ("sent", "late") or not rec.get("folder") or not rec.get("name"):
        return {}
    folder, name = rec["folder"], rec["name"]
    b = status(folder, name, now)
    if state == "late":                                  # 7 Nov (your call: everything enhanced): a late piece is
        if b.state != "enhanced":                        #   used by itself when it comes
            return {}
        r = use_late(p.folder)
        if not r.get("ok"):
            return {"state": "failed", "why": r.get("why")}
        from . import enhance_swap as ES
        steps = PJ.read_json(p.folder / PJ.FILE).get("steps") or {}
        if ES.edit_made(p.folder, steps.get("edit")):
            sw = ES.swap_in(p.folder)
            if sw.get("ok") and sw.get("kept"):
                ES.cover_all(p.folder)
            return {"state": "used", "text": sw.get("text") or r.get("text"), "swapped": bool(sw.get("ok"))}
        return {"state": "used", "text": r.get("text")}
    if b.state == "enhanced":
        r = take_back(folder, name, p.folder, (rec.get("piece") or None))
        if r["ok"]:
            c = r.get("check") or {}
            text = f"enhanced with Topaz: {c.get('width')}x{c.get('height')}, {OUT_FPS} fps"
            p.update(enhance={"state": "used", "used": now, "file": f"{PROJECT_SUB}\\{PROJECT_FILE}", "text": text,
                              **({"note": r["note"]} if r.get("note") else {})})
            from . import enhance_swap as ES            # 7 Nov: the edit is finished already (matched on the normal
            steps = PJ.read_json(p.folder / PJ.FILE).get("steps") or {}         # RAW): it plays the picture now
            if ES.edit_made(p.folder, steps.get("edit")):                       # (18 Nov: a FAIL with its XML too)
                sw = ES.swap_in(p.folder)
                if sw.get("ok") and sw.get("kept"):     # 7 Nov: clips left on the normal RAW -> Topaz again
                    if ES.cover_all(p.folder).get("clips"):
                        return {"state": "progress", "share": 0.0,
                                "text": f"{sw.get('kept')} clip(s) not enhanced yet: a new piece went to Topaz"}
                if sw.get("ok"):
                    return {"state": "used", "text": sw.get("text") or text, "swapped": True}
                if str(sw.get("why") or "").startswith(ES.NOT_LINED_UP):
                    # 19 Nov: the enhanced picture is not the edit's picture (the swap's own look at its frames) --
                    # until now the video then simply kept the normal RAW. It is a failed try like the others: the
                    # reason is kept and the piece goes again, made another way.
                    why = f"the enhanced piece is not right ({sw.get('why')}) -- the normal RAW is used"
                    rows = (PJ.load(p.folder).data.get("enhance") or {}).get("swap_check")
                    note_failure(p.folder, why, json.dumps(rows) if rows else "")
                    p.update(enhance={"state": "failed", "why": why})
                    return _again(p.folder, why)
                ES.zip_when_done(p.folder)              # (not swapped: the zip with the normal RAW's picture)
                return {"state": "used", "text": f"{text} -- not swapped: {sw.get('why')}", "swapped": False}
            return {"state": "used", "text": text}
        why = f"the enhanced piece is not right ({r['why']}) -- the normal RAW is used"
        note_failure(p.folder, why, json.dumps(r.get("check") or {}))     # (19 Nov: every failure, whole)
        clear(folder, name)
        p.update(enhance={"state": "failed", "why": why})
        return _again(p.folder, why)
    if b.state == "gone":                               # 9 Nov: the other computer took it a moment ago?
        try:
            q = PJ.load(project).data.get("enhance") or {}
            got = read_info(Path(p.folder) / PROJECT_SUB / "enhance.json") or {}
            if q.get("state") != state or float(got.get("copied") or 0) >= float(rec.get("sent") or 0):
                return {}
        except Exception:  # noqa: BLE001
            pass
    if b.state == "gone" and not _folder_answers(folder):
        # 18 Nov ("it must not stop"): the Enhance folder itself does not answer (the PC asleep, the network away
        # for a moment) -- that read as "the piece is not in the Enhance folder any more": the video was marked
        # failed and stayed on the normal RAW. The piece is still there: the row waits and looks again.
        return {"state": "progress", "share": 0.0,
                "text": "the Enhance folder cannot be reached now -- waiting for it to come back"}
    if b.state in ("failed", "gone"):
        why = b.text if b.state == "gone" else f"Topaz could not enhance it: {b.text}"
        note_failure(p.folder, why, _failure_text(names(folder, name).failed) if b.state == "failed" else "")
        clear(folder, name)
        p.update(enhance={"state": "failed", "why": why})
        return _again(p.folder, why)
    waited = now - float(rec.get("sent") or now)
    if waited > timeout_s and not (b.state == "enhancing" and waited < MAX_WAIT_S) and \
            not (b.state != "enhancing" and waited < ALIVE_WAIT_S and worker_alive(folder, now)):
        why = TIMEOUT_WHY if b.state != "enhancing" else f"{STILL_WHY} after {_clock(waited)}"
        p.update(enhance={"state": "late", "why": why, "late": now})
        _zip_now(p.folder)
        return {"state": "late", "why": why}
    return {"state": "progress", "share": b.share, "text": b.text + (" · over 1 hour, still working"
                                                                       if waited > timeout_s else "")}


def _folder_answers(folder: str | os.PathLike) -> bool:
    try:
        return Path(folder).is_dir()
    except OSError:
        return False


FAILURE_LOG = "topaz_failed.log"        # in the project's logs folder


def _failure_text(failed: Path) -> str:
    """18 Nov (17 Nov, Bedtime: "code 69" -- the reason was never seen): Topaz's failure file (the reason and
    Topaz's last lines) was deleted from the Enhance folder the moment the row had read its first line. Its whole
    text is kept in the project now: logs/topaz_failed.log (every failure of this video, the newest last; 19 Nov:
    written by ``note_failure``, with the try and how its piece was made)."""
    try:
        return Path(failed).read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        return ""


def _again(project, why: str) -> dict:
    """7 Nov (your call: no normal RAW in the timeline): Topaz failed -- the piece goes again (enhance_swap.cover_all,
    at most MAX_REDOS times); only then the normal RAW stays and the zip is made. 19 Nov: each time made another
    way (``WAYS``)."""
    try:
        from . import enhance_swap as ES
        r = ES.cover_all(project)
        if r.get("ok") and r.get("clips"):
            return {"state": "progress", "share": 0.0,
                    "text": f"Topaz failed ({why}) -- sent again" + (f", made another way: {r['way']}"
                                                                     if r.get("way") else "")}
    except Exception:  # noqa: BLE001
        pass
    _zip_now(project)
    return {"state": "failed", "why": why}


def _zip_now(project) -> None:
    """7 Nov: the picture is final (no Topaz one): the zip to send is made now when the edit is finished."""
    try:
        from .enhance_swap import zip_when_done
        zip_when_done(project)
    except Exception:  # noqa: BLE001
        pass


def use_late(project: str | os.PathLike) -> dict:
    """"Use the enhanced piece": a piece that came back after the 1-hour wait, checked and copied into the project.
    {ok, why, text}."""
    from . import projects as PJ
    p = PJ.load(project)
    rec = p.data.get("enhance") if isinstance(p.data.get("enhance"), dict) else {}
    if not rec.get("folder") or not rec.get("name"):
        return {"ok": False, "why": "the project has no enhanced piece"}
    if status(rec["folder"], rec["name"]).state != "enhanced":
        return {"ok": False, "why": "the enhanced piece is not in the Enhance folder any more"}
    r = take_back(rec["folder"], rec["name"], p.folder, rec.get("piece") or None)
    if not r["ok"]:
        p.update(enhance={"state": "failed", "why": f"the enhanced piece is not right ({r['why']})"})
        return {"ok": False, "why": r["why"]}
    c = r.get("check") or {}
    text = f"enhanced on the laptop: {c.get('width')}x{c.get('height')}, {OUT_FPS} fps (came late)"
    p.update(enhance={"state": "used", "used": time.time(), "file": f"{PROJECT_SUB}\\{PROJECT_FILE}", "text": text})
    return {"ok": True, "why": "", "text": text}


# ---------------------------------------------------------------------------------------------------------------------
# laptop: Topaz
# ---------------------------------------------------------------------------------------------------------------------

@dataclass
class Topaz:
    ffmpeg: Path | None = None
    models: Path | None = None
    folder: Path | None = None
    why: str = ""                   # "" = found
    fake: bool = False

    @property
    def found(self) -> bool:
        return not self.why

    def line(self) -> str:
        if self.fake:
            return "a fake Topaz (the tests: ffmpeg's plain 2x scale + 60 fps)"
        return f"Topaz Video AI found: {self.folder}" if self.found else self.why


def find_topaz() -> Topaz:
    """Topaz Video AI's own ffmpeg (with its filters) and its model folder."""
    if fake():
        from match_cuts.common import ffmpeg_bin
        return Topaz(Path(ffmpeg_bin()), None, None, "", True)
    pf = [os.environ.get("ProgramFiles", r"C:\Program Files"), os.environ.get("ProgramW6432", ""),
          r"C:\Program Files"] + [f"{d}:\\Program Files" for d in "DEFG"]     # 29 Oct: the PC has it on D:
    cands = [Path(os.environ[TOPAZ_ENV])] if os.environ.get(TOPAZ_ENV) else []
    for base in pf:
        if base:
            root = Path(base) / "Topaz Labs LLC"
            cands.append(root / "Topaz Video AI")
            if root.is_dir():
                cands += sorted(p for p in root.iterdir() if p.is_dir() and p.name.startswith("Topaz Video"))
    folder = next((c for c in cands if (c / "ffmpeg.exe").is_file()), None)
    if folder is None:
        return Topaz(why="Topaz Video AI is not installed on this computer (no ffmpeg.exe in "
                         r"C:\Program Files\Topaz Labs LLC\Topaz Video AI)")
    md = os.environ.get("TVAI_MODEL_DIR", "").strip()
    pd = Path(os.environ.get("ProgramData", r"C:\ProgramData")) / "Topaz Labs LLC"
    mcands = ([Path(md)] if md else []) + [pd / folder.name / "models", pd / "Topaz Video AI" / "models"]
    models = next((c for c in mcands if (c / f"{PROTEUS['model']}.json").is_file()), None)
    if models is None:
        return Topaz(folder / "ffmpeg.exe", None, folder,
                     f"Topaz Video AI's model folder was not found (looked in {mcands[-1]})")
    return Topaz(folder / "ffmpeg.exe", models, folder)


def _opts(d: dict) -> str:
    return ":".join(f"{k}={v}" for k, v in d.items())


MAX_OUT_PIXELS = 3840 * 2880        # 18 Nov: the enhanced picture is at most this big (4K, a 4:3 one too)


def topaz_scale(width: int | None, height: int | None) -> int:
    """18 Nov (The Funniest Shaq Impression: a 4K RAW -- twice its size is 8K at 60 fps, the laptop's graphics card
    failed once and then stood at 0 % until the app was closed): 2 when twice the piece's size is at most 4K, else 1
    -- a piece that is 4K already keeps its size and only gets its 60 fps (Apollo); Proteus has nothing to add to it
    that a 1080x1920 Short shows."""
    try:
        w, h = int(width or 0), int(height or 0)
    except (TypeError, ValueError):
        return 2
    if w <= 0 or h <= 0:
        return 2
    return 2 if (2 * w) * (2 * h) <= MAX_OUT_PIXELS else 1


SEEK_EARLY_S = 0.0002               # a part is read from this much before its first frame's time (never the frame
#                                     after it because of a rounded number), and to this much before its end


def _part_head(part: tuple) -> list[str]:
    """The reading of one part: from SEEK_EARLY_S before its first frame's time, for its length."""
    t0, length = float(part[0]), float(part[1])
    return ["-ss", f"{max(0.0, t0 - SEEK_EARLY_S):.6f}", "-t", f"{length:.6f}"]


def topaz_command(tz: Topaz, src: str | os.PathLike, dst: str | os.PathLike, vram: float = 1.0,
                  scale: int = 2, part: tuple[float, float, int] | None = None) -> list[str]:
    """Topaz's ffmpeg with the user's settings: Proteus at 2x the piece's size, then Apollo to 60 fps; H.265 Main10
    at a constant OUT_BITRATE; the sound copied. ``scale`` 1 (``topaz_scale``: a piece that is 4K already): Apollo
    only, the size as it is. The fake Topaz: a plain scale and 60 fps (the tests).

    18 Nov -- ``part`` (the first frame's time in s, the length in s, the 60 fps frames to write): only that part of
    the piece, read frame-exactly (the seek decodes from the keyframe before), the SAME filters and encoder, no
    sound (the edit's sound is the RAW's own), and exactly that many frames: Apollo writes no new frame after a
    part's last one, so the last picture is held until the count is full (those frames lie in the margin after the
    part: nothing plays them)."""
    head = [str(tz.ffmpeg), "-hide_banner", "-nostdin", "-y"]
    if part is not None:
        head += _part_head(part)
    head += ["-i", str(src), "-map", "0:v:0"] + ([] if part is not None else ["-map", "0:a:0?"])
    tail = ([] if part is not None else ["-c:a", "copy"]) + ["-map_metadata", "-1", "-movflags", "+faststart",
                                                              "-f", "mp4", str(dst)]
    hold = ",tpad=stop_mode=clone:stop_duration=2" if part is not None else ""
    count = ["-frames:v", str(int(part[2]))] if part is not None else []
    scale = 1 if int(scale or 2) == 1 else 2
    if tz.fake:
        return head + ["-vf", f"scale=iw*{scale}:ih*{scale}:flags=lanczos,fps={OUT_FPS}{hold}", "-c:v", "libx264",
                       "-preset", "ultrafast", "-crf", "18", "-pix_fmt", "yuv420p"] + count + tail
    gpu = dict(GPU, vram=f"{vram:g}")
    chain = (f"tvai_up={_opts({**PROTEUS, **gpu})},tvai_fi={_opts({**APOLLO, **gpu})}" if scale == 2 else
             f"tvai_fi={_opts({**APOLLO, **gpu})}")
    return head + ["-vf", chain + hold, "-c:v", "hevc_nvenc", "-profile:v", "main10", "-pix_fmt", "p010le", "-rc",
                   "cbr", "-b:v", OUT_BITRATE, "-maxrate", OUT_BITRATE, "-bufsize", OUT_BUFFER, "-preset", "p5",
                   "-tag:v", "hvc1"] + count + tail


def topaz_env(tz: Topaz) -> dict:
    env = dict(os.environ)
    if tz.models is not None:
        env["TVAI_MODEL_DIR"] = str(tz.models)
        env["TVAI_MODEL_DATA_DIR"] = str(tz.models)
    return env


def _plain_topaz_error(text: str, code: int | None) -> str:
    t = (text or "").lower()
    if "not logged in" in t or "please log in" in t or "login required" in t or "unauthorized" in t:
        return "Topaz says you are not logged in: open Topaz Video AI once and log in"
    if "out of memory" in t or "cuda_error_out_of_memory" in t:
        return "the graphics card ran out of memory"
    if "no nvenc capable devices" in t or "cannot load nvcuda" in t or "openencodesessionex failed" in t:
        return "the graphics card's H.265 encoder could not start (NVENC)"
    if "no space left" in t or "disk full" in t:
        return "the Enhance folder's drive is full"
    if "model" in t and ("download" in t or "not found" in t):
        return "Topaz could not find or download its model (Proteus / Apollo)"
    last = [x.strip() for x in (text or "").splitlines() if x.strip() and not x.lstrip().startswith("frame=")]
    return f"Topaz stopped (code {code})" + (f": {_telling_line(last)[:200]}" if last else "")


_SAYS_NOTHING = ("conversion failed", "error while filtering", "exiting normally", "terminating thread",
                 "task finished with error code", "nothing was written", "error sending frames to consumers",
                 "failed to inject frame into filter network", "error while processing the decoded data")
_SAYS_WHY = ("error", "fail", "cannot", "can't", "could not", "unable", "invalid", "not found", "denied", "no such",
             "unsupported", "refused", "timed out", "corrupt", "missing")


def _telling_line(lines: list[str]) -> str:
    """18 Nov (17 Nov, The Worst Bedtime Routine: "Topaz stopped (code 69): Conversion failed!" -- ffmpeg's last
    line, which says nothing; the reason stood a few lines above it and was never seen): the last line of Topaz's
    output that names a reason, else its last line."""
    for x in reversed(lines):
        low = x.lower()
        if any(w in low for w in _SAYS_NOTHING):
            continue
        if any(w in low for w in _SAYS_WHY):
            return x
    return lines[-1] if lines else ""


# ---------------------------------------------------------------------------------------------------------------------
# 20 Nov: Topaz goes on from where it stopped
# ---------------------------------------------------------------------------------------------------------------------
# Your job 4 ("Make ... a Topaz piece go on from where it stopped, also after the app or the PC was closed in the
# middle"). Until now a stopped Topaz (the app closed, the PC off, Topaz hanging) started again from frame 0: its
# half-written file could not even be read (an mp4 is only readable once it is finished: "moov atom not found" --
# 17 Nov, The Worst Prank In The Office: 150 MB of Topaz's work thrown away) and it was deleted anyway.
#
# Now Topaz writes its file in small self-contained blocks (a fragmented mp4, one block per keyframe: the picture
# and the encoder are exactly as before), so a stopped file is readable up to its last whole block. The next try
# keeps the frames it can (``_salvage``) and Topaz goes on from there in a new file of its own; at the end the
# files are joined without encoding again (as the played parts are since 18 Nov) and written as a normal mp4.
# The place where Topaz goes on is one where a 60 fps frame falls exactly on a frame of the piece (every 1001
# frames of 60 fps for a 23.976 or 29.97 fps piece, every 12 for 25 fps, any for 60 fps): from there Topaz's
# frames are on the very same times as in a run without a stop, so the joined file has the same frames at the same
# places and the swap maps the edit onto it as before. What is kept is written next to the file:
# <file>.resume.json (how it was made: when anything about the piece or Topaz's settings differs, nothing is kept).

RESUME_EXT = ".resume.json"
FRAG_FLAGS = "+frag_keyframe+empty_moov+default_base_moof"
SALVAGE_MARGIN = 250                # frames of the cut-off end not kept (the last block may be half written)
RESUME_ENV = "MOVIERECAPS_TOPAZ_RESUME"     # "0": no resume (a stopped Topaz starts again from frame 0, as before)


def resume_on() -> bool:
    return os.environ.get(RESUME_ENV, "1").strip() != "0"


KEEP_EVERY = 600                    # an IDR keyframe at least every this many frames (at a place on the grid)


def keep_every(fps: Any) -> int:
    """Topaz's file gets an IDR keyframe every this many 60 fps frames -- the places where a stopped file can be cut
    (frames before such a keyframe never need the frames after it, also with B-frames: the real Topaz's H.265 has
    them, 20 Nov) and where a 60 fps frame falls exactly on a piece frame: 1001 at 23.976 / 29.97 fps, 600 at
    24 / 25 / 30 / 60 fps. (Its encoder puts a keyframe every 250 frames by itself; this adds one in about four.)"""
    step = grid_step(fps)[1]
    return step * max(1, -(-KEEP_EVERY // step))


def grid_step(fps: Any) -> tuple[int, int]:
    """(piece frames, 60 fps frames) between two places where a 60 fps frame falls exactly on a piece frame:
    (400, 1001) at 23.976 fps, (500, 1001) at 29.97, (5, 12) at 25, (1, 1) at 60."""
    r = Fraction(OUT_FPS) / Fraction(str(fps))
    return r.denominator, r.numerator


def _unlink_soon(path: Path, wait_s: float = 10.0) -> None:
    """20 Nov: a file a Topaz that was just stopped still holds (Windows: "being used by another process" for a
    moment after the process ended -- the retry after a hang then failed with a PermissionError, not a reason in
    plain words) goes once it is let go; still held after ``wait_s``: a Topaz failure that says so."""
    end = time.monotonic() + wait_s
    while True:
        try:
            path.unlink(missing_ok=True)
            return
        except PermissionError as e:
            if time.monotonic() >= end:
                raise EnhanceError(f"Topaz's file {path.name} is still in use after Topaz stopped") from e
            time.sleep(0.5)


def _fragmented(cmd: list[str], every: int = 0) -> list[str]:
    """Topaz's command writing its file in blocks, and only the picture. (Measured: with the sound in a file written
    in blocks every picture is 21 ms late -- the sound's start-up delay, which a normal mp4 notes and a file in
    blocks does not; the picture alone in blocks, the sound put in at the end: every frame on the same time, the
    same pictures and the same sound as Topaz's own file, checked frame by frame.)"""
    out: list[str] = []
    i = 0
    while i < len(cmd):
        a = cmd[i]
        if a == "-map" and i + 1 < len(cmd) and cmd[i + 1].startswith("0:a"):
            i += 2
            continue
        if a == "-c:a" and i + 1 < len(cmd):
            i += 2
            continue
        if a == "+faststart" and out and out[-1] == "-movflags":
            a = FRAG_FLAGS
            if every > 0:                       # (before -movflags: the IDR keyframes where it can be cut)
                out[-1:-1] = ["-force_key_frames", f"expr:eq(mod(n,{int(every)}),0)"] + \
                    (["-forced-idr", "1"] if "hevc_nvenc" in cmd else [])
        out.append(a)
        i += 1
    return out


def _seg_file(dst: Path, k: int) -> Path:
    return dst.with_name(f"{dst.stem}.seg{k + 1}{dst.suffix}")


def _resume_file(dst: Path) -> Path:
    return dst.with_name(dst.name + RESUME_EXT)


def _resume_sig(tz: Topaz, src: Path, scale: int, part: tuple | None, out_frames: int, fps: Any) -> dict:
    """What a kept file was made from: the piece (its size and time), Topaz's settings and the part asked for --
    anything else and nothing is kept."""
    st = Path(src).stat()
    return {"src": Path(src).name, "size": st.st_size, "mtime": int(st.st_mtime), "scale": int(scale),
            "part": [round(float(x), 6) for x in part] if part else None, "frames": int(out_frames),
            "fps": str(Fraction(str(fps))), "fake": bool(tz.fake), "bitrate": OUT_BITRATE,
            "filters": [PROTEUS, APOLLO] if scale == 2 else [APOLLO]}


def drop_resume(dst: str | os.PathLike) -> None:
    """The kept pieces of a stopped Topaz run of ``dst`` go (after it was finished, or failed for a reason)."""
    dst = Path(dst)
    rf = _resume_file(dst)
    try:
        st = json.loads(rf.read_text(encoding="utf-8"))
        names = [s.get("file") for s in st.get("segs") or [] if isinstance(s, dict)]
    except (OSError, ValueError):
        names = []
    for n in names + [f"{dst.stem}.seg{k}{dst.suffix}" for k in range(1, 4)]:
        if n:
            try:
                (dst.parent / Path(str(n)).name).unlink(missing_ok=True)
            except OSError:
                pass
    try:
        rf.unlink(missing_ok=True)
    except OSError:
        pass


def _write_resume(dst: Path, sig: dict, segs: list[dict], complete: bool = False) -> None:
    try:
        _write_json(_resume_file(dst), {"sig": sig, "segs": segs, "complete": bool(complete)})
    except OSError:
        pass


def _keyframes(path: Path) -> dict[int, float]:
    """{decode position: time} of the file's keyframe packets."""
    from match_cuts.common import ffprobe_bin
    r = _run([ffprobe_bin(), "-v", "error", "-select_streams", "v:0", "-show_entries", "packet=pts_time,flags",
              "-of", "csv=p=0", str(path)], timeout=600)
    out: dict[int, float] = {}
    for i, ln in enumerate((r.stdout or "").splitlines()):
        t, _, fl = ln.partition(",")
        if "K" in fl:
            try:
                out[i] = float(t)
            except ValueError:
                pass
    return out


def _salvage(seg: Path, every: int) -> int:
    """How many frames of a cut-off segment are kept, the file cut to exactly them; 0 = none (the file goes). 20 Nov:
    only up to an IDR keyframe Topaz was asked for (``keep_every``) -- the real Topaz's H.265 has B-frames (frames
    stored out of order), so a cut anywhere else could take a frame that needs one after it -- and only when that
    keyframe is exactly where it should be (its place in the file and its time)."""
    from match_cuts.common import ffmpeg_bin
    try:
        n = int(_probe(seg).get("nb_read_packets") or 0)
        keys = _keyframes(seg) if n > SALVAGE_MARGIN + every else {}
    except (EnhanceError, ValueError, TypeError):
        n, keys = 0, {}
    k = 0
    t0 = keys.get(0, 0.0)                             # (with B-frames a file in blocks starts its times 2 frames on)
    for c in range(((n - SALVAGE_MARGIN) // every) * every if every > 0 else 0, 0, -every):
        if c in keys and abs(keys[c] - t0 - c / float(OUT_FPS)) < 0.5 / OUT_FPS:
            k = c
            break
    if k <= 0:
        seg.unlink(missing_ok=True)
        return 0
    cut = seg.with_name(seg.stem + ".cut" + seg.suffix)
    r = _run([ffmpeg_bin(), "-hide_banner", "-v", "error", "-nostdin", "-y", "-i", str(seg), "-map", "0:v:0", "-c",
              "copy", "-frames:v", str(k), "-map_metadata", "-1", "-movflags", "+faststart", str(cut)], timeout=1800)
    ok = r.returncode == 0 and cut.is_file() and _frames_of(cut) == k
    if ok:                                            # its end decodes (the last block whole)
        t = _run([ffmpeg_bin(), "-hide_banner", "-v", "error", "-nostdin", "-sseof", "-2", "-i", str(cut), "-map",
                  "0:v:0", "-f", "null", "-"], timeout=600)
        ok = t.returncode == 0 and not (t.stderr or "").strip()
    if not ok:
        cut.unlink(missing_ok=True)
        seg.unlink(missing_ok=True)
        return 0
    os.replace(cut, seg)
    return k


def _join(segs: list[Path], dst: Path, audio_from: Path | None, fake_tz: bool) -> None:
    """The segments one after the other, not encoded again, as a normal mp4 (+ the piece's sound when the whole
    piece was enhanced, as Topaz's own file has it)."""
    from match_cuts.common import ffmpeg_bin
    tag = [] if fake_tz or fake() else ["-tag:v", "hvc1"]
    base = [ffmpeg_bin(), "-hide_banner", "-v", "error", "-nostdin", "-y"]
    tmp = dst.with_name(dst.stem + ".join" + dst.suffix)
    if len(segs) == 1:                                # no stop: Topaz's own picture, written as a normal mp4
        cmd = base + ["-i", str(segs[0])]
    else:
        lst = dst.with_name(dst.stem + ".segs.txt")
        lst.write_text("".join(f"file '{s.name}'\n" for s in segs), encoding="utf-8")
        cmd = base + ["-f", "concat", "-safe", "0", "-i", str(lst)]
    if audio_from is not None:                        # the piece's sound, copied (as Topaz's own command does)
        cmd += ["-i", str(audio_from), "-map", "0:v:0", "-map", "1:a:0?"]
    else:
        cmd += ["-map", "0:v:0"]
    cmd += ["-c", "copy", *tag, "-map_metadata", "-1", "-movflags", "+faststart", "-f", "mp4", str(tmp)]
    r = _run(cmd, timeout=3600)
    if len(segs) > 1:
        dst.with_name(dst.stem + ".segs.txt").unlink(missing_ok=True)
    if r.returncode != 0 or not tmp.is_file():
        tmp.unlink(missing_ok=True)
        raise EnhanceError(f"Topaz's file could not be written: {(r.stderr or '').strip()[-200:]}")
    os.replace(tmp, dst)


STALL_S = 8 * 60.0                  # 18 Nov: Topaz writes no new frame for this long: it hangs -- stopped, tried again
STALL_TEXT = "Topaz stopped answering"


def run_topaz(src: str | os.PathLike, dst: str | os.PathLike, out_frames: int, tz: Topaz | None = None,
              progress: Callable[[float, float | None], None] | None = None,
              cancel: threading.Event | None = None, scale: int = 2,
              part: tuple[float, float, int] | None = None, fps: Any = None) -> None:
    """Topaz on ``src`` into ``dst`` (``_topaz_tries``: its retries). 20 Nov: with ``fps`` (the piece's frame rate)
    a run that was stopped -- the app closed, the PC off, Topaz hanging -- goes on from where it stopped (above);
    ``part``: (first frame's time, length, frames) of one part of the piece."""
    tz = tz or find_topaz()
    if not tz.found:
        raise EnhanceError(tz.why)
    if fps is None or not resume_on():
        return _topaz_tries(src, dst, out_frames, tz, progress, cancel, scale, part)
    src, dst = Path(src), Path(dst)
    scale = 1 if int(scale or 2) == 1 else 2
    fps = Fraction(str(fps))
    sig = _resume_sig(tz, src, scale, part, out_frames, fps)
    try:
        st = json.loads(_resume_file(dst).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        st = {}
    if st.get("sig") != sig:                            # (another piece, other settings, or nothing kept)
        drop_resume(dst)
        st = {}
    elif st.get("complete") and dst.is_file() and _frames_of(dst) == int(st.get("frames") or -1):
        return                                          # (a part finished before the stop)
    _unlink_soon(dst)
    every = keep_every(fps)
    segs: list[dict] = []
    for s in st.get("segs") or []:
        f = dst.parent / Path(str(s.get("file") or "")).name
        if not s.get("file") or not f.is_file():
            break
        if s.get("done") and _frames_of(f) == int(s.get("frames") or -1):
            segs.append({"file": f.name, "frames": int(s["frames"]), "done": True})
            continue
        k = _salvage(f, every)                          # the file Topaz was writing when it stopped
        if k:
            segs.append({"file": f.name, "frames": k, "done": True})
        break
    kept = sum(s["frames"] for s in segs)
    first = int(round(float(part[0]) * fps)) if part else 0      # the part's first frame in the piece
    if kept >= out_frames:
        kept = out_frames
    if kept < out_frames:
        seg = _seg_file(dst, len(segs))
        _unlink_soon(seg)
        drop_resume(seg)
        if kept:                                        # Topaz goes on from the piece frame on the grid
            c = Fraction(kept) * fps / OUT_FPS          # (whole: kept is a number of whole grid steps)
            times = _frame_times(src)                   # 21 Nov: from that frame's OWN time in the file (a time in
            at = _frame_time(times, first + int(c), fps)    # whole ms -- an MKV's -- is up to 0.5 ms early: the seek
            if part:                                    # to its exact time skipped it, the part after the stop
                end = float(part[0]) + float(part[1])   # started one frame late and was made again from 0)
                sub = (at, end - at, out_frames - kept)
            elif times:                                 # (the whole piece: read from exactly that frame's time, as
                sub = (at + SEEK_EARLY_S, out_frames / float(OUT_FPS) + 5.0 - at, out_frames - kept)   # without a stop
            else:                                       # the whole piece was read from 0 with no such margin: from
                at = math.floor(float(c / fps) * 1e6) / 1e6     # the frame's own time (never a moment later)
                sub = (at + SEEK_EARLY_S, out_frames / float(OUT_FPS) + 5.0 - at, out_frames - kept)
            log.info("Topaz on %s goes on from where it stopped: %d of %d frames kept", src.name, kept, out_frames)
        else:
            sub = part
        _write_resume(dst, sig, segs + [{"file": seg.name, "frames": None, "done": False}])
        left = out_frames - kept

        def prog(share: float, secs: float | None) -> None:
            if progress is not None:
                progress((kept + share * left) / float(out_frames), secs)
        try:
            _topaz_tries(src, seg, left, tz, prog, cancel, scale, sub, frag=every)
        except EnhanceError as e:
            if not (cancel is not None and cancel.is_set()) and not share_drop(e, src):
                drop_resume(dst)                         # (failed for a reason: nothing of it is kept; 21 Nov: a
            raise                                        #  network drop keeps it, as a stop does)
        n = _frames_of(seg)
        if kept:
            c = int(Fraction(kept) * fps / OUT_FPS)
            why = _part_starts_right(src, fps, first + c, seg)
            if why:                                     # never a picture off by a frame: from the start instead
                log.warning("Topaz on %s: the part after the stop is not right (%s) -- made again from the start",
                            src.name, why)
                drop_resume(dst)
                return run_topaz(src, dst, out_frames, tz, progress, cancel, scale, part, fps)
        segs.append({"file": seg.name, "frames": n, "done": True})
        _write_resume(dst, sig, segs)
    files = [dst.parent / s["file"] for s in segs]
    _join(files, dst, src if part is None else None, tz.fake)
    for f in files:
        f.unlink(missing_ok=True)
    try:
        _write_json(_resume_file(dst), {"sig": sig, "segs": [], "complete": True, "frames": _frames_of(dst)})
    except OSError:
        pass


def _topaz_tries(src: str | os.PathLike, dst: str | os.PathLike, out_frames: int, tz: Topaz,
                 progress: Callable[[float, float | None], None] | None = None,
                 cancel: threading.Event | None = None, scale: int = 2,
                 part: tuple[float, float, int] | None = None, frag: int = 0) -> None:
    """Topaz on ``src`` into ``dst``; ``progress(share, seconds left)`` from its "frame=" lines. Raises EnhanceError
    (plain words) when it fails or was stopped. 29 Oct: the graphics card out of memory (the laptop's 8 GB) -> tried
    again with less of its memory (60 %, then 35 %). 18 Nov: also when it hangs (no new frame for STALL_S)."""
    steps = [1.0, 0.6, 0.35]
    once_more = True                    # 18 Nov (17 Nov, Bedtime on the laptop: "code 69: Conversion failed!", the
    k = 0                               #   second try worked): a failure with no known reason is tried once more
    while True:                         #   as it was, before the piece is given back as failed
        try:
            return _run_topaz_once(src, dst, out_frames, tz, progress, cancel, steps[k], scale, part,
                                   *([frag] if frag else []))
        except EnhanceError as e:
            if cancel is not None and cancel.is_set():
                raise
            if "ran out of memory" in str(e) or STALL_TEXT in str(e):
                if k == len(steps) - 1:
                    raise
                k += 1                  # (less of the graphics card's memory)
            elif once_more and str(e).startswith("Topaz stopped (code"):
                once_more = False
            else:
                raise
            _unlink_soon(Path(dst))


def _run_topaz_once(src, dst, out_frames: int, tz: Topaz, progress, cancel, vram: float, scale: int = 2,
                    part: tuple[float, float, int] | None = None, frag: int = 0) -> None:
    import queue
    cmd = topaz_command(tz, src, dst, vram, scale, part) if part is not None else topaz_command(tz, src, dst, vram,
                                                                                                scale)
    if frag:                                 # 20 Nov: in blocks while Topaz writes it (it can go on after a stop) --
        cmd = _fragmented(cmd, int(frag))               #   the picture only: the sound is put in when the file is finished
    t0 = time.monotonic()
    pr = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                          env=topaz_env(tz), creationflags=NO_WINDOW)
    tail: list[str] = []
    buf = b""
    chunks: "queue.Queue[bytes | None]" = queue.Queue()

    def pump() -> None:                  # (a hanging Topaz writes nothing: the read must not hold the loop)
        try:
            while True:
                ch = pr.stderr.read(256)
                if not ch:
                    break
                chunks.put(ch)
        except Exception:  # noqa: BLE001
            pass
        finally:
            chunks.put(None)
    threading.Thread(target=pump, name="topaz-output", daemon=True).start()
    moved_at, last_frame = time.monotonic(), -1
    try:
        while True:
            if cancel is not None and cancel.is_set():
                pr.kill()
                raise EnhanceError("stopped")
            if time.monotonic() - moved_at > STALL_S:
                pr.kill()
                raise EnhanceError(f"{STALL_TEXT} (no new frame for {STALL_S / 60:.0f} minutes)")
            try:
                ch = chunks.get(timeout=1.0)
            except queue.Empty:
                continue
            if not ch:
                break
            buf += ch
            parts = buf.replace(b"\r", b"\n").split(b"\n")
            buf = parts.pop()
            for raw in parts:
                line = raw.decode("utf-8", "replace").strip()
                if not line:
                    continue
                tail = (tail + [line])[-40:]
                if line.startswith("frame="):
                    try:
                        n = int(line.split("=", 1)[1].strip().split()[0])
                    except (ValueError, IndexError):
                        continue
                    if n > last_frame:              # 20 Nov: also without a progress callback -- a Topaz that
                        last_frame, moved_at = n, time.monotonic()  # worked was taken for hanging after 8 min
                    if out_frames <= 0 or progress is None:
                        continue
                    share = max(0.0, min(0.999, n / float(out_frames)))
                    el = time.monotonic() - t0
                    left = el * (1 - share) / share if share > 0.02 else None
                    progress(share, left)
        code = pr.wait()
    finally:
        if pr.poll() is None:
            pr.kill()
            pr.wait()
    if code != 0 or not Path(dst).is_file():
        e = EnhanceError(_plain_topaz_error("\n".join(tail), code))
        e.details = "\n".join(x for x in tail if not x.startswith("frame="))[-3000:]   # (the failure file keeps them)
        raise e


# ---------------------------------------------------------------------------------------------------------------------
# laptop: taking a piece (the lock)
# ---------------------------------------------------------------------------------------------------------------------

def read_lock(path: Path) -> dict | None:
    try:
        d = json.loads(Path(path).read_text(encoding="utf-8") or "{}")
        return d if isinstance(d, dict) else {}
    except FileNotFoundError:
        return None
    except (OSError, ValueError):
        return {}                                       # (being written: someone has it)


def _pid_alive(pid: int) -> bool:
    """A process with this id runs on this computer (True when it cannot be told)."""
    if pid <= 0:
        return False
    try:
        if os.name == "nt":
            import ctypes
            k = ctypes.windll.kernel32
            h = k.OpenProcess(0x1000, False, int(pid))            # PROCESS_QUERY_LIMITED_INFORMATION
            if not h:
                return k.GetLastError() == 5                      # access denied: it is there; else: no such process
            try:
                code = ctypes.c_ulong(0)
                return bool(k.GetExitCodeProcess(h, ctypes.byref(code))) and code.value == 259     # STILL_ACTIVE
            finally:
                k.CloseHandle(h)
        os.kill(int(pid), 0)
        return True
    except ProcessLookupError:
        return False
    except Exception:  # noqa: BLE001 - cannot tell: as if it runs (the 15 minutes decide)
        return True


OWN_LOCK_S = 20.0                   # our own dead lock is free after this (a lock just being written is left alone)


def _own_dead_lock(path: Path, age: float) -> bool:
    """18 Nov (The Funniest Shaq Impression: the app was closed in the middle of Topaz and opened again -- its piece
    was "being enhanced, 0 %" for 15 minutes with nobody working on it): the lock is this computer's own, from an app
    that does not run any more (its process is gone). Free at once, not after LOCK_STALE_S."""
    if age < OWN_LOCK_S:
        return False
    d = read_lock(path) or {}
    try:
        host, pid = str(d.get("token") or "").split(":")[:2]
        pid = int(pid)
    except (ValueError, TypeError):
        return False
    if host != M.host() or str(d.get("computer") or "") != M.host() or pid == os.getpid():
        return False
    try:
        if str(d.get("machine") or "") != M.which():              # (both computers are called "Gvidas")
            return False
    except Exception:  # noqa: BLE001
        return False
    return not _pid_alive(pid)


def lock_stale(path: Path, now: float | None = None) -> bool:
    try:
        age = (now if now is not None else time.time()) - Path(path).stat().st_mtime
    except OSError:
        return False
    if age > LOCK_STALE_S:
        return True
    try:
        return _own_dead_lock(Path(path), age)
    except Exception:  # noqa: BLE001
        return False


def take(nm: Names, me: str | None = None) -> str | None:
    """Take the piece (its lock made with O_EXCL: only one computer can). A token when taken, None when someone else
    has it. A stale lock (its computer stopped) is taken over."""
    token = f"{me or M.host()}:{os.getpid()}:{time.time_ns()}"
    for _ in range(2):
        try:
            fd = os.open(str(nm.lock), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            if lock_stale(nm.lock):
                try:
                    nm.lock.unlink()
                except OSError:
                    return None
                continue
            return None
        except OSError:
            return None
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(json.dumps({"token": token, "computer": me or M.host(), "machine": M.which(),
                                "started": time.time(), "share": 0.0}))
        d = read_lock(nm.lock) or {}
        return token if d.get("token") == token else None
    return None


def beat(nm: Names, token: str, **fields: Any) -> bool:
    """The owner's lock rewritten with its progress (False: it is not ours any more)."""
    d = read_lock(nm.lock)
    if not d or d.get("token") != token:
        return False
    d.update(fields, beat=time.time())
    try:
        _write_json(nm.lock, d)
        return True
    except OSError:
        return False


def beat_worker(folder: str | os.PathLike, note: str = "") -> None:
    """30 Oct: this computer's Topaz worker is alive (a small file in the Enhance folder, rewritten every scan) -- so a
    piece waiting in its line is not given up after 1 hour."""
    try:
        f = Path(folder) / f".topaz_worker_{safe_name(M.host())}_{_kind(None)}.json"
        f.write_text(json.dumps({"computer": M.host(), "time": time.time(), "note": note}), encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass


def worker_alive(folder: str | os.PathLike, now: float | None = None) -> bool:
    """A Topaz worker wrote its heartbeat into the Enhance folder in the last WORKER_ALIVE_S."""
    now = time.time() if now is None else now
    try:
        return any(now - f.stat().st_mtime < WORKER_ALIVE_S for f in Path(folder).glob(WORKER_GLOB))
    except OSError:
        return False


def _reachable(path: str) -> Path | None:
    """A project folder as the PC wrote it (D:\\Shared\\...), as this computer reaches it (the laptop: the share)."""
    if not path:
        return None
    p = Path(path)
    try:
        if p.is_dir():
            return p
        low = str(p).replace("/", "\\").lower()
        if low.startswith("d:\\shared\\"):
            q = Path(rf"\\{M.PC_ADDRESS}\{M.SHARE}") / str(p)[len("D:\\Shared\\"):]
            if q.is_dir():
                return q
    except OSError:
        pass
    return None


def _video_key(data: dict) -> str:
    comp = data.get("competitor") if isinstance(data.get("competitor"), dict) else {}
    return str(comp.get("id") or comp.get("file") or comp.get("url") or "")


def replaced_by(info: dict) -> str:
    """7 Nov (the same video made twice: Topaz enhanced the first, broken copy after the new one was done): the name of
    a newer copy of the piece's project -- the same competitor video, a folder named the same (+ " (2)" ...), its own
    piece sent later -- or "" (none, or nothing can be read: the piece stays)."""
    import glob
    import re
    from . import projects as PJ
    pf = _reachable(str(info.get("project_folder") or ""))
    if pf is None:
        return ""
    try:
        me = PJ.load(pf).data
    except Exception:  # noqa: BLE001
        return ""
    key = _video_key(me)
    rec = me.get("enhance") if isinstance(me.get("enhance"), dict) else {}
    sent = float(rec.get("sent") or info.get("made") or 0.0)
    if not key or not sent:
        return ""
    base = re.sub(r" \(\d+\)$", "", pf.name)
    try:
        sibs = [Path(x) for x in glob.glob(glob.escape(str(pf.parent / base)) + "*")]
    except OSError:
        return ""
    for sib in sibs:
        if sib == pf or re.sub(r" \(\d+\)$", "", sib.name) != base or not sib.is_dir():
            continue
        try:
            d = PJ.load(sib).data
        except Exception:  # noqa: BLE001
            continue
        r2 = d.get("enhance") if isinstance(d.get("enhance"), dict) else {}
        if _video_key(d) == key and float(r2.get("sent") or 0.0) > sent:
            return sib.name
    return ""


def drop_replaced(nm: Names, newer: str) -> None:
    """A replaced piece leaves the line: its files in the Enhance folder go (only the queue's copy -- the project and its
    RAW are never touched), and its project.json says why."""
    from . import projects as PJ
    info = read_info(nm.info) or {}
    clear(nm.folder, nm.name)
    pf = _reachable(str(info.get("project_folder") or ""))
    if pf is None:
        return
    try:
        p = PJ.load(pf)
        rec = p.data.get("enhance") if isinstance(p.data.get("enhance"), dict) else {}
        if rec.get("state") in ("sent", "late"):
            p.update(enhance=dict(rec, state="replaced", why=f"made again as {newer}: not enhanced"))
    except Exception:  # noqa: BLE001
        pass


def waiting_pieces(folder: str | os.PathLike, now: float | None = None, machine: str = "") -> list[Names]:
    """The pieces in the Enhance folder nobody has taken (the oldest first): their json written, no result yet, no
    live lock."""
    folder = Path(folder)
    out = []
    try:
        files = list(folder.glob(f"*{TO}{PIECE_EXT}"))
    except OSError:
        return []
    for p in files:
        nm = Names(folder, p.name[: -len(TO + PIECE_EXT)])
        try:
            if not nm.info.is_file() or nm.enhanced.is_file() or nm.failed.is_file():
                continue
            if nm.lock.exists() and not lock_stale(nm.lock, now):
                continue
            if nm.stop.exists():                 # 21 Nov: "no Topaz" pressed -- nobody takes it, its files go
                remove_piece(nm)
                continue
            if machine:                          # 30 Oct: a piece meant for the other computer is left for it
                want = str((read_info(nm.info) or {}).get("topaz_on") or "")
                if want and want != machine:
                    continue
            newer = replaced_by(read_info(nm.info) or {})
            if newer:                            # 7 Nov: the same video made again -- its old piece is not enhanced
                drop_replaced(nm, newer)
                continue
            out.append((p.stat().st_mtime, nm))
        except OSError:
            continue
    return [nm for _t, nm in sorted(out, key=lambda x: x[0])]


@dataclass
class Job:
    """One piece the laptop enhances (what its Progress page shows)."""
    name: str
    state: str = "enhancing"        # "enhancing" | "done" | "failed" | "waiting" (21 Nov: the folder away, let go)
    share: float = 0.0
    left_s: float | None = None
    why: str = ""
    started: float = field(default_factory=time.time)
    ended: float | None = None
    note: str = ""                  # 18 Nov: something that went another way (the parts could not be used ...)
    wait: str = ""                  # 21 Nov: waiting for the Enhance folder (a network drop) -- the same way goes on

    def line(self) -> str:
        if self.state == "enhancing" and self.wait:
            return f"{self.name}: {self.wait} -- waiting for it, then Topaz goes on the same way"
        if self.state == "enhancing":
            return f"Enhancing {self.name} on this laptop · {int(round(100 * self.share))} %" + (
                f" · {_minutes(self.left_s)} left" if self.left_s else "")
        if self.state == "done":
            return f"{self.name}: enhanced in {_clock((self.ended or time.time()) - self.started)} -- the PC takes it"
        if self.state == "waiting":
            return f"{self.name}: {self.why} -- taken again when the folder answers"
        return f"{self.name}: not enhanced -- {self.why}"


class PartsError(EnhanceError):
    """The played parts could not be enhanced on their own (not Topaz failing: that is an EnhanceError) -- the
    whole piece is enhanced instead."""


def parts_failed_to_project(info: dict, why: str) -> bool:
    """21 Nov (your job 1: "every PartsError must save its full reason in the project and in the piece json at
    once"): the reason written into the project the piece came from -- project.json "enhance" -> "parts_failed" and a
    block in logs/topaz_failed.log -- when this computer reaches that folder (the piece was made here, or on the PC's
    D:\\Shared). Else the PC copies it from the piece's json when it takes the result back (``take_back``). Not a
    failed try: the whole piece is enhanced right after. Never raises; True when written."""
    try:
        pf = _reachable(str((info or {}).get("project_folder") or ""))
        if pf is None:
            return False
        from . import projects as PJ
        p = PJ.load(pf)
        p.update(enhance={"parts_failed": str(why), "parts_failed_at": time.time()})
        d = Path(pf) / "logs"
        d.mkdir(parents=True, exist_ok=True)
        with open(d / FAILURE_LOG, "a", encoding="utf-8") as f:
            f.write(f"=== {time.strftime('%Y-%m-%d %H:%M:%S')}  the played parts of {info.get('project')} could not "
                    f"be used ({M.host()}) -- the whole piece is enhanced instead\n{why}\n\n")
        return True
    except Exception:  # noqa: BLE001
        return False


# ---------------------------------------------------------------------------------------------------------------------
# 21 Nov: a short network drop is not a failed Topaz try (your job 2)
# ---------------------------------------------------------------------------------------------------------------------
# 20 Nov, the laptop: Topaz stopped with "\\192.168.101.11\Shared\Enhance\..._TOENHANCE.mp4: No such file or
# directory" while the file was there -- the network was away for a moment. It counted as a failed try: the failure
# file went to the PC, the PC made a new piece another way and sent it, and what Topaz had made was thrown away. Now a
# failure while the piece cannot be read, or one that names a file of the Enhance folder with a "cannot be read" kind
# of reason, is a drop: the laptop keeps what Topaz made (as after a stop), waits until the folder answers again and
# goes on the SAME way. Nothing is written for the PC (no failure file, no try counted, the parts not marked failed).
SHARE_WAIT_S = 30 * 60.0            # one wait for the folder at most this long (then the piece is let go -- not failed:
SHARE_LOOK_S = 15.0                 #   the worker takes it again when the folder is back); looked at this often
SHARE_DROPS = 6                     # this many drops in one take while the folder answers each time: a failure as before
SHARE_TEXT = "the Enhance folder could not be read"
GONE_TEXT = "the piece is not in the Enhance folder any more"
_DROP_WORDS = ("no such file or directory", "permission denied", "invalid argument", "input/output error",
               "i/o error", "network path was not found", "network name is no longer available",
               "unexpected network error", "specified network name", "cannot find the path",
               "cannot find the file", "semaphore timeout", "device is not ready", "could not be read",
               "cannot be read", "network")
_NET_WINERRORS = frozenset({53, 59, 64, 67, 121, 1231, 1232, 1236})   # Windows: the network went away


class ShareDrop(EnhanceError):
    """A file of the Enhance folder could not be read or written for a moment (the network) -- not Topaz failing,
    not the piece: the same piece goes on the same way when the folder answers again."""


def _readable(path: Path) -> bool:
    try:
        if not Path(path).parent.is_dir():
            return False
        with open(path, "rb") as f:
            return bool(f.read(1))
    except OSError:
        return False


def share_drop(e: BaseException, piece: Path) -> bool:
    """Was ``e`` the Enhance folder away for a moment (and not the piece, Topaz or a stop)?"""
    if isinstance(e, ShareDrop):
        return True
    if isinstance(e, PartsError) or str(e) == "stopped":
        return False
    if isinstance(e, OSError) and getattr(e, "winerror", None) in _NET_WINERRORS:
        return True
    if not _readable(Path(piece)):                       # whatever it was, the piece cannot be read now
        return True
    text = f"{e} {getattr(e, 'details', '')}".lower()
    folder = str(Path(piece).parent).lower()
    name = Path(piece).name.lower()
    stem = name[: -len((TO + PIECE_EXT).lower())] if name.endswith((TO + PIECE_EXT).lower()) else name
    names_it = any(s in text for s in (folder, folder.replace("\\", "/"), stem))
    return names_it and any(w in text for w in _DROP_WORDS)


def _sleep(seconds: float, stop: Any) -> None:
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if stop is not None and stop.is_set():
            return
        time.sleep(min(1.0, max(0.0, end - time.monotonic())))


def _wait_share(nm: Names, stop: Any, token: str) -> str:
    """Until the piece can be read again: '' then; else why not ("stopped", no Topaz, the piece removed by the PC,
    or the folder away for SHARE_WAIT_S)."""
    end = time.monotonic() + SHARE_WAIT_S
    gone = 0
    while True:
        if stop is not None and stop.is_set():
            return NO_TOPAZ_TEXT if getattr(stop, "by_user", False) else "stopped"
        try:
            if nm.folder.is_dir():
                if nm.stop.exists():
                    return NO_TOPAZ_TEXT
                if _readable(nm.piece) and nm.info.is_file():
                    beat(nm, token)
                    return ""
                if not nm.piece.exists() and not nm.info.exists():
                    gone += 1                            # (twice, a look apart: not a folder answering half)
                    if gone >= 2:
                        return GONE_TEXT
                else:
                    gone = 0
        except OSError:
            pass
        if time.monotonic() >= end:
            return f"{SHARE_TEXT} for {SHARE_WAIT_S / 60:.0f} minutes"
        _sleep(SHARE_LOOK_S, stop)


def _note_drop(nm: Names, e: BaseException) -> None:
    """The drop written into the piece's json (best effort: the folder may be away): what, when, which computer."""
    try:
        d = read_info(nm.info)
        if not d:                                        # (gone or unreadable: never written back -- a json the
            return                                       #  PC removed must stay removed)
        drops = [x for x in (d.get("share_drops") or []) if isinstance(x, dict)][-9:]
        drops.append({"when": time.strftime("%Y-%m-%d %H:%M:%S"), "computer": M.host(), "why": str(e)[:300]})
        _write_json(nm.info, dict(d, share_drops=drops))
    except Exception:  # noqa: BLE001
        pass


# ---------------------------------------------------------------------------------------------------------------------
# 21 Nov: "no Topaz for this video" (your job 4)
# ---------------------------------------------------------------------------------------------------------------------
# Until now, moving a video's piece out of the Enhance folder made the PC send a new one by itself (the piece "gone"
# is a failed try: _watch -> _again), so you had to move the whole project away. Now the row has a button: its piece
# leaves the line, a Topaz working on it stops (on either computer: the stop file next to the piece, which the
# enhancing computer looks at every few seconds), the project is marked "off" -- nothing sends it again, not the
# rows, not the sweep, not a new run of the row -- and the video finishes with its zip (the normal RAW's picture).
NO_TOPAZ_TEXT = "no Topaz for this video (you pressed No Topaz)"
STOP_LOOK_S = 5.0                   # the enhancing computer looks for the stop file this often


class _JobStop:
    """The stop of one piece: the worker's own (the app closing) or the stop file (``no_topaz``)."""

    def __init__(self, outer: Any, nm: Names):
        self.outer, self.nm = outer, nm
        self.by_user = False
        self._looked = 0.0

    def is_set(self) -> bool:
        if self.by_user or (self.outer is not None and self.outer.is_set()):
            return True
        if time.monotonic() - self._looked >= STOP_LOOK_S:
            self._looked = time.monotonic()
            try:
                self.by_user = self.nm.stop.exists()
            except OSError:
                pass
        return self.by_user


def remove_piece(nm: Names) -> int:
    """Every file of the piece in the Enhance folder (its parts, Topaz's kept blocks, the stop file); a finished
    _ENHANCED.mp4 -- hours of Topaz -- is moved into _to_delete, never deleted. Returns how many went."""
    gone = 0
    try:
        if nm.enhanced.is_file():
            dst = Path(nm.folder) / TO_DELETE
            dst.mkdir(exist_ok=True)
            target = dst / nm.enhanced.name
            if target.exists():
                target = dst / f"{nm.enhanced.stem}.{int(time.time())}{nm.enhanced.suffix}"
            os.replace(nm.enhanced, target)
            gone += 1
    except OSError:
        pass
    try:
        drop_resume(nm.part)
        mine = [f for f in Path(nm.folder).iterdir() if f.is_file() and f.name.startswith(nm.name + "_ENHANCING")]
    except OSError:
        mine = []
    end = time.monotonic() + REMOVE_WAIT_S              # (the real Topaz, just stopped, holds its files a moment:
    for f in mine + [x for x in nm.all() if x != nm.enhanced]:      # "in use" -- found with the real Topaz, 21 Nov)
        while True:
            try:
                if f.is_file():
                    if f.name.endswith(".mp4") and ".part" in f.name:
                        drop_resume(f)
                    f.unlink()
                    gone += 1
                break
            except PermissionError:
                if time.monotonic() >= end:
                    break
                time.sleep(0.5)
            except OSError:
                break
    return gone


REMOVE_WAIT_S = 15.0


def no_topaz(project: str | os.PathLike) -> dict:
    """"No Topaz" for one video: {ok, why, text}. Never raises."""
    from . import projects as PJ
    try:
        p = PJ.load(project)
        rec = p.data.get("enhance") if isinstance(p.data.get("enhance"), dict) else {}
        if rec.get("state") == "used":
            return {"ok": False, "why": "its edit plays the enhanced picture already"}
        text = NO_TOPAZ_TEXT
        if rec.get("folder") and rec.get("name"):
            nm = names(rec["folder"], rec["name"])
            try:
                if nm.folder.is_dir():
                    if nm.lock.exists() and not lock_stale(nm.lock):
                        who = str((read_lock(nm.lock) or {}).get("computer") or "the other computer")
                        nm.stop.write_text(json.dumps({"by": M.host(), "time": time.time(),
                                                       "project": str(p.folder)}), encoding="utf-8")
                        text += f" -- Topaz on {who} stops it now"
                    elif remove_piece(nm):
                        text += " -- its piece left the Enhance folder"
            except OSError as e:
                text += f" -- its piece could not be removed now ({e}); it is cleaned up later"
        p.update(enhance={"state": "off", "why": NO_TOPAZ_TEXT, "no_topaz": True, "off": time.time()})
        from .enhance_swap import zip_when_done
        zip_when_done(p.folder)                          # (only when its edit is finished; else after the edit)
        return {"ok": True, "why": "", "text": text}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "why": f"{type(e).__name__}: {e}"}


def topaz_again(project: str | os.PathLike) -> dict:
    """"Topaz again": the "no Topaz" undone -- the piece is cut and sent now when the edit is finished (else after
    it). {ok, why, text}."""
    from . import projects as PJ
    try:
        p = PJ.load(project)
        p.update(enhance={"state": "skipped", "why": "Topaz comes after the edit", "after_edit": True,
                          "no_topaz": False, "redos": 0})
        from . import enhance_swap as ES
        r = ES.cover_all(p.folder)
        if r.get("ok") and r.get("clips"):
            return {"ok": True, "why": "", "text": "sent to Topaz again"}
        made = ES.edit_made(p.folder, (PJ.load(p.folder).data.get("steps") or {}).get("edit"))
        return {"ok": True, "why": "", "text": (r.get("why") or "nothing to send") if made else
                "Topaz comes after the edit"}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "why": f"{type(e).__name__}: {e}"}


PART_CHECK_MARGIN = 0.01            # a part's first enhanced frame may not look this much more like a neighbour frame


def _part_file(nm: Names, k: int) -> Path:
    return nm.part.with_name(f"{nm.name}_ENHANCING.p{k + 1}.part.mp4")


def _part_done(dst: Path, frames: int) -> bool:
    """A part finished before a stop (its resume note says complete, with that many frames): run_topaz keeps it."""
    try:
        st = json.loads(_resume_file(Path(dst)).read_text(encoding="utf-8"))
        return bool(st.get("complete")) and Path(dst).is_file() and int(st.get("frames") or -1) == int(frames)
    except (OSError, ValueError, TypeError):
        return False


def enhance_parts(nm: Names, info: dict, tz: Topaz | None, progress: Callable[[float, float | None], None] | None,
                  cancel: threading.Event | None, scale: int) -> None:
    """18 Nov (The Best Business Idea In Key and Peele (2): a 3:24 piece for 57 s of clips, about an hour of Topaz):
    Topaz on each played part of the piece on its own (``valid_ranges``; ``topaz_command``'s ``part``), every part
    exactly ``range_out_frames`` frames, then all joined (the same encoder settings: no re-encode) into
    <name>_ENHANCING.part.mp4. Each part's first enhanced frame is compared with the piece's frames: it must be the
    part's first frame, not the one before or after -- else PartsError (the caller enhances the whole piece). A
    Topaz failure is an EnhanceError, as for a whole piece."""
    from match_cuts.common import ffmpeg_bin
    rs = valid_ranges(info)
    try:
        fps = Fraction(str(info.get("fps")))
    except (ValueError, ZeroDivisionError) as e:
        raise PartsError("the piece's frame rate cannot be read") from e
    if not rs or fps <= 0:
        raise PartsError("no parts")
    counts = [range_out_frames(b - a, fps) for a, b in rs]
    total = sum(counts)
    t0 = time.monotonic()
    files = [_part_file(nm, k) for k in range(len(rs))]
    lst = nm.part.with_name(f"{nm.name}_ENHANCING.parts.txt")
    times = _frame_times(nm.piece)                       # 21 Nov: the frames' own times (above _frame_times)
    try:
        tz = tz or find_topaz()
    except Exception:  # noqa: BLE001 - run_topaz says why
        pass
    nf = int(info.get("frames") or 0) or None
    keep = False
    try:
        done = 0
        for k, ((a, b), n) in enumerate(zip(rs, counts)):

            def prog(share: float, _left: float | None, done=done, n=n) -> None:
                if progress is not None:
                    all_share = max(0.0, min(0.999, (done + share * n) / float(total)))
                    el = time.monotonic() - t0
                    progress(all_share, el * (1 - all_share) / all_share if all_share > 0.02 else None)
            ta = _frame_time(times, a, fps, nf)
            tb = _frame_time(times, b, fps, nf) if b < (nf or 0) else float(Fraction(b) / fps)
            part = (ta, tb - ta, n)                      # (read from frame a's time to just before frame b's)
            if not _part_done(files[k], n):              # 21 Nov: a part that would start wrong fails in seconds
                why = _seek_lands_right(tz, nm.piece, fps, a, part)
                if why:
                    if "could not be" in why and not _readable(nm.piece):
                        raise ShareDrop(f"{SHARE_TEXT} (part {k + 1}'s first frame, before Topaz)")
                    raise PartsError(f"part {k + 1} (piece frame {a}, {ta:.4f} s): {why}")
            run_topaz(nm.piece, files[k], n, tz, prog, cancel, scale=scale, part=part, fps=fps)
            got = _frames_of(files[k])
            if got != n:
                if got == 0 and not (_readable(nm.piece) and _readable(files[k])):
                    raise ShareDrop(f"{SHARE_TEXT} (part {k + 1}'s frames)")
                raise PartsError(f"part {k + 1} has {got} frames, not {n}")
            why = _part_starts_right(nm.piece, fps, a, files[k])
            if why:
                if "could not be" in why and not (_readable(nm.piece) and _readable(files[k])):
                    raise ShareDrop(f"{SHARE_TEXT} (part {k + 1}'s first frame)")
                raise PartsError(f"part {k + 1}: {why}")
            done += n
        lst.write_text("".join(f"file '{f.name}'\n" for f in files), encoding="utf-8")
        tag = [] if (tz is not None and tz.fake) or fake() else ["-tag:v", "hvc1"]
        r = _run([ffmpeg_bin(), "-hide_banner", "-v", "error", "-nostdin", "-y", "-f", "concat", "-safe", "0", "-i",
                  str(lst), "-map", "0:v:0", "-c", "copy", *tag, "-map_metadata", "-1", "-movflags", "+faststart",
                  "-f", "mp4", str(nm.part)], timeout=3600)
        if r.returncode != 0 or not nm.part.is_file():
            if not _readable(nm.piece) or share_drop(EnhanceError(r.stderr or ""), nm.piece):
                raise ShareDrop(f"{SHARE_TEXT} (joining the parts: {(r.stderr or '').strip()[-200:]})")
            raise PartsError(f"the parts could not be joined: {(r.stderr or '').strip()[-200:]}")
        got = _frames_of(nm.part)
        if got != total:
            if got == 0 and not (_readable(nm.piece) and _readable(nm.part)):
                raise ShareDrop(f"{SHARE_TEXT} (the joined parts)")
            raise PartsError(f"the joined parts have {got} frames, not {total}")
    except ShareDrop:
        keep = True
        raise
    except OSError as e:
        if share_drop(e, nm.piece):                      # 21 Nov: the folder away -- the parts made so far stay
            keep = True
            raise ShareDrop(f"{SHARE_TEXT}: {e}") from e
        raise PartsError(f"{type(e).__name__}: {e}") from e
    except EnhanceError as e:
        keep = share_drop(e, nm.piece)
        raise
    finally:
        if not (keep or (cancel is not None and cancel.is_set())):    # 20 Nov: stopped -- the parts made so far are kept
            for f in files + [lst]:
                try:
                    f.unlink(missing_ok=True)
                    drop_resume(f)
                except OSError:
                    pass


def _frames_of(path: Path) -> int:
    try:
        st = _probe(path)
        return int(st.get("nb_read_packets") or st.get("nb_frames") or 0)
    except (EnhanceError, ValueError, TypeError):
        return 0


# 21 Nov (The Worst Employee In Key and Peele, the laptop: 4 parts, "PartsError" twice, each time after about an hour
# of Topaz; found on a copy of a Key and Peele BluRay RAW, S02E08): a part was read from SEEK_EARLY_S (0.2 ms) before
# its first frame's EXACT time (frame / fps). But a RAW the app converted from an MKV keeps the MKV's timestamps, which
# are whole milliseconds: frame 300 of a 23.976 fps piece is at 12.5125 s, the file says 12.512 s. ffmpeg's seek to
# 12.5123 s then throws frame 300 away and the part starts on frame 301 -- one frame late, and the check after the part
# (rightly) refused it. About 3 in 10 frames of such a file are stored that early, so a part failed or not by its first
# frame's number (part 1 starts on frame 0 and always passed: the failure came only after the next part, an hour on).
# Now a part is read from its first frame's OWN time in the file (``_frame_times``), and before Topaz starts on a part
# the same reading is tried on its first frame (seconds): a part that would start wrong fails at once, not an hour on.
_TIMES: dict = {}


def _frame_times(path: Path) -> list[float]:
    """The times of the file's frames as ffmpeg's seek sees them (s after the file's start time, in showing order);
    [] when they cannot be read."""
    from match_cuts.common import ffprobe_bin
    p = Path(path)
    try:
        st = p.stat()
        key = (os.path.normcase(str(p)), st.st_size, int(st.st_mtime))
    except OSError:
        return []
    if key in _TIMES:
        return _TIMES[key]
    try:
        r = _run([ffprobe_bin(), "-v", "error", "-select_streams", "v:0", "-show_entries",
                  "packet=pts_time:format=start_time", "-of", "json", str(p)], timeout=600)
        js = json.loads(r.stdout or "{}") if r.returncode == 0 else {}
        start = float((js.get("format") or {}).get("start_time") or 0.0)
        times = sorted(float(k["pts_time"]) - start for k in js.get("packets") or []
                       if str(k.get("pts_time", "N/A")) not in ("N/A", ""))
    except (OSError, ValueError, TypeError, subprocess.SubprocessError):
        return []
    if len(_TIMES) > 16:
        _TIMES.clear()
    _TIMES[key] = times
    return times


def _frame_time(times: list[float], k: int, fps: Fraction, frames: int | None = None) -> float:
    """Frame ``k``'s own time in the file (``_frame_times``) -- the ideal k / fps when the times are not known or do
    not fit the piece (another count of frames)."""
    if times and (frames is None or len(times) == int(frames)) and 0 <= k < len(times):
        return float(times[k])
    return float(Fraction(int(k)) / fps)


def _seek_lands_right(tz: "Topaz", piece: Path, fps: Fraction, first: int, part: tuple) -> str:
    """'' when a part read as Topaz will read it (the same ffmpeg, the same seek) starts on the piece's frame
    ``first``; else why not (a few seconds: one frame decoded from the keyframe before)."""
    try:
        from match_cuts.common import ffmpeg_bin
        ff = str(tz.ffmpeg) if tz is not None and getattr(tz, "found", False) and getattr(tz, "ffmpeg", None) \
            else ffmpeg_bin()
        from match_cuts.common import ffprobe_bin       # (its size only: _probe counts every packet -- the whole
        r0 = _run([ffprobe_bin(), "-v", "error", "-select_streams", "v:0", "-show_entries",   # piece read again,
                   "stream=width,height", "-of", "csv=p=0", str(piece)], timeout=120)       # over the network)
        w, h = (int(x) for x in (r0.stdout or "0,0").strip().splitlines()[0].split(",")[:2])
        cmd = [ff, "-hide_banner", "-v", "error", "-nostdin", *_part_head(part), "-i", str(piece), "-map", "0:v:0",
               "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "gray", "-"]
        r = subprocess.run(cmd, capture_output=True, timeout=600, creationflags=NO_WINDOW,
                           env=topaz_env(tz) if tz is not None and getattr(tz, "found", False) else None)
        import numpy as np
        if r.returncode != 0 or w <= 0 or h <= 0 or len(r.stdout) < w * h:
            return f"its first frame could not be read before Topaz ({(r.stderr or b'').decode(errors='replace')[-200:]})"
        img = np.frombuffer(r.stdout[: w * h], np.uint8).reshape(h, w)
        import cv2
        out = cv2.resize(img, (160, max(8, int(round(h * 160 / max(1, w))))), interpolation=cv2.INTER_AREA)
        return _first_frame_shows(piece, fps, first, out)
    except Exception as e:  # noqa: BLE001 - cannot be checked: said so
        return f"its first frame could not be checked before Topaz ({type(e).__name__}: {e})"


def _part_starts_right(piece: Path, fps: Fraction, first: int, part: Path) -> str:
    """'' when the enhanced part's first frame shows the piece's frame ``first`` (and not clearly the frame before or
    after it: a still picture, where they all look the same, is fine); else why not."""
    try:
        from .enhance_swap import _gray
        return _first_frame_shows(piece, fps, first, _gray(part, Fraction(OUT_FPS), [0]).get(0))
    except Exception as e:  # noqa: BLE001 - cannot be checked: not used
        return f"its first frame could not be checked ({type(e).__name__}: {e})"


def _first_frame_shows(piece: Path, fps: Fraction, first: int, out: Any) -> str:
    """'' when the picture ``out`` (gray, any size) shows the piece's frame ``first`` and not clearly the one before or
    after it; else why not."""
    try:
        from .enhance_swap import _gray, _zncc
        want = [f for f in (first - 1, first, first + 1) if f >= 0]
        src = _gray(piece, fps, want)
        if out is None or src.get(first) is None:
            return "its first frame could not be read"
        import cv2
        h, w = src[first].shape[:2]
        if out.shape[:2] != (h, w):
            out = cv2.resize(out, (w, h), interpolation=cv2.INTER_AREA)
        own = _zncc(src[first], out)
        others = {f: _zncc(src[f], out) for f in want if f != first and src.get(f) is not None}
        if others:                                     # (21 Nov: the neighbour it shows is named first)
            f, v = max(others.items(), key=lambda kv: kv[1])
            if v > own + PART_CHECK_MARGIN:
                return f"its first frame shows the piece's frame {f}, not {first} (ZNCC {v:.3f} against {own:.3f})"
        if own < 0.5:
            return f"its first frame does not show the piece's frame {first} (ZNCC {own:.2f})"
        return ""
    except Exception as e:  # noqa: BLE001 - cannot be checked: not used
        return f"its first frame could not be checked ({type(e).__name__}: {e})"


def enhance_one(nm: Names, token: str, tz: Topaz | None = None, job: Job | None = None,
                cancel: threading.Event | None = None, on_change: Callable[[Job], None] | None = None) -> Job:
    """Topaz on a taken piece: <name>_ENHANCING.part.mp4, checked, renamed <name>_ENHANCED.mp4; a failure writes
    <name>_ENHANCE_FAILED.txt. The lock goes either way.

    21 Nov: the Enhance folder away for a moment (``share_drop``) is not a failure: what Topaz made is kept, the folder
    is waited for and the piece goes on the same way (job.wait says so meanwhile); away too long, the piece is let go
    -- "waiting", no failure file -- and taken again when the folder is back. "No Topaz" for its video (the stop file,
    ``no_topaz``): Topaz stops and every file of the piece goes."""
    job = job or Job(nm.name)
    tell = on_change or (lambda j: None)
    info = read_info(nm.info) or {}
    last = [0.0]
    stop = _JobStop(cancel, nm)

    def prog(share: float, left: float | None) -> None:
        job.share, job.left_s = share, left
        tell(job)
        if time.monotonic() - last[0] >= LOCK_BEAT_S:
            last[0] = time.monotonic()
            beat(nm, token, share=round(share, 4), left_s=round(left, 1) if left else None)

    def body(first: bool) -> None:
        nonlocal info
        dur = float(info.get("duration") or 0.0)
        if first:
            nm.part.unlink(missing_ok=True)
        scale = topaz_scale(info.get("width"), info.get("height"))
        done_parts = False
        if valid_ranges(info) and not info.get("parts_failed"):   # 18 Nov: only what the edit plays (play_ranges)
            try:
                enhance_parts(nm, info, tz, prog, stop, scale)
                done_parts = True
            except PartsError as e:                      # a part did not start on its frame, the join failed ...:
                job.note = f"the parts could not be used ({e}) -- the whole piece instead"    # the whole piece
                nm.part.unlink(missing_ok=True)
                try:                                     # 20 Nov (The Worst Employee In Key and Peele: the app was
                    _write_json(nm.info, dict(read_info(nm.info) or info,   # closed and opened, the parts ran a
                                              parts_failed=str(e), worker_note=job.note))   # second hour and
                except OSError:                          # failed again): written at once -- a piece taken again goes
                    pass                                 # straight to the whole piece (and its resume), and the
                #                                          reason can be read in the json while Topaz still works
                parts_failed_to_project(info, str(e))    # 21 Nov: and in the project, at once
                info = dict(info, parts_failed=str(e))   # (a drop after this: on with the whole piece)
                if stop.is_set():
                    raise EnhanceError("stopped") from e
        if not done_parts:
            try:
                fps = Fraction(str(info.get("fps"))) if info.get("fps") else None
            except (ValueError, ZeroDivisionError):
                fps = None
            run_topaz(nm.piece, nm.part, int(round(dur * OUT_FPS)), tz, prog, stop, scale=scale, fps=fps)
        chk = check_enhanced(nm.part, info)
        if not chk["ok"]:
            if not (_readable(nm.piece) and _readable(nm.part)):
                raise ShareDrop(f"{SHARE_TEXT} (checking Topaz's result: {chk['why']})")
            raise EnhanceError(f"Topaz's result is not right: {chk['why']}")
        if job.note:                                     # 19 Nov: what went another way is told to the PC too (the
            try:                                         #   piece's json: take_back keeps it in the project)
                _write_json(nm.info, dict(read_info(nm.info) or info, worker_note=job.note))
            except OSError:
                pass
        os.replace(nm.part, nm.enhanced)
        drop_resume(nm.part)

    let_go = False                                       # 21 Nov: the folder away too long -- not a failure
    try:
        if not info:
            raise EnhanceError("its json (where the piece is in the RAW) cannot be read")
        dur = float(info.get("duration") or 0.0)
        need = int(dur * 60e6 / 8 * 1.2) + (50 << 20)       # (OUT_BITRATE: 60 Mb/s since 17 Nov, it asked for 3x)
        try:
            free = shutil.disk_usage(str(nm.folder)).free
        except OSError:
            free = None
        if free is not None and free < need:
            raise EnhanceError(f"the Enhance folder's drive has {free / 1e9:.1f} GB free, the enhanced piece needs "
                               f"about {need / 1e9:.1f} GB")
        drops, first = 0, True
        while True:
            try:
                body(first)
                break
            except Exception as e:  # noqa: BLE001 - a drop waits and goes on; anything else as before
                if stop.is_set() or drops >= SHARE_DROPS or not share_drop(e, nm.piece):
                    raise
                drops, first = drops + 1, False
                log.warning("Topaz on %s: %s (%s) -- waiting for it, then the same way again", nm.name, SHARE_TEXT, e)
                job.wait, job.left_s = f"{SHARE_TEXT} ({str(e)[:160]})", None
                tell(job)
                _note_drop(nm, e)
                why = _wait_share(nm, stop, token)
                job.wait = ""
                tell(job)
                if why == "stopped" or why == NO_TOPAZ_TEXT:
                    raise EnhanceError(why) from e
                if why:
                    let_go = why != GONE_TEXT
                    raise EnhanceError(why) from e
        job.state, job.share, job.left_s = "done", 1.0, None
    except Exception as e:  # noqa: BLE001 - said in plain words, and written for the PC
        job.state, job.why = "failed", (str(e) if isinstance(e, EnhanceError) else f"{type(e).__name__}: {e}")
        if stop.by_user or job.why == NO_TOPAZ_TEXT:     # "No Topaz": every file of the piece goes, nothing for the PC
            job.why = NO_TOPAZ_TEXT
            remove_piece(nm)
        elif job.why == GONE_TEXT:                       # the PC removed it: what is left of it here goes too
            remove_piece(nm)
        elif let_go:                                     # the folder away: what Topaz made is kept, taken again later
            job.state = "waiting"
        else:
            try:
                nm.part.unlink(missing_ok=True)
                if job.why != "stopped":             # 20 Nov: stopped -- what Topaz made is kept for the next try
                    drop_resume(nm.part)
                    for k in range(len(valid_ranges(info))):
                        _part_file(nm, k).unlink(missing_ok=True)
                        drop_resume(_part_file(nm, k))
            except OSError:
                pass
            if job.why != "stopped":
                try:
                    more = getattr(e, "details", "")
                    nm.failed.write_text(f"{job.why}\n\n(the laptop {M.host()}, {time.strftime('%Y-%m-%d %H:%M')})\n"
                                         + (f"\nbefore that: {job.note}\n" if job.note else "")     # (19 Nov)
                                         + (f"\nTopaz's last lines:\n{more}\n" if more else ""), encoding="utf-8")
                except OSError:
                    pass
    finally:
        job.ended = time.time()
        try:
            d = read_lock(nm.lock)
            if d is not None and d.get("token") == token:
                nm.lock.unlink(missing_ok=True)
        except OSError:
            pass
        tell(job)
    return job


class Worker:
    """The laptop's side, in its own thread: every SCAN_S (and at once on ``wake``) the Enhance folder is looked at;
    the oldest piece nobody took is taken and enhanced. ``jobs``: what it did (the newest last)."""

    def __init__(self, folder: Callable[[], Path] | Path, on_change: Callable[[Job], None] | None = None,
                 scan_s: float = SCAN_S, hold: Callable[[], str] | None = None,
                 machine: Callable[[], str] | None = None):
        self.folder = folder if callable(folder) else (lambda f=Path(folder): f)
        self.hold = hold                # 29 Oct: why not to start Topaz now ('' = go): an edit running here
        self.machine = machine          # 30 Oct: "pc" / "laptop" -- only the pieces meant for it (or for either)
        self.on_change = on_change
        self.scan_s = scan_s
        self.jobs: list[Job] = []
        self.cancel = threading.Event()
        self._wake = threading.Event()
        self._stop = threading.Event()
        self.thread: threading.Thread | None = None
        self.note = ""                  # the last scan's problem (the folder not reachable ...)

    def start(self) -> None:
        if self.thread is None or not self.thread.is_alive():
            self._stop.clear()
            self.cancel.clear()
            self.thread = threading.Thread(target=self._loop, name="enhance-worker", daemon=True)
            self.thread.start()

    def stop(self) -> None:
        self._stop.set()
        self.cancel.set()
        self._wake.set()

    def wake(self) -> None:
        self._wake.set()

    @property
    def busy(self) -> bool:
        return any(j.state == "enhancing" for j in self.jobs)

    def once(self) -> Job | None:
        """One look at the folder: the oldest free piece enhanced (None: nothing to do)."""
        folder = Path(self.folder())
        if not folder.is_dir():
            self.note = f"the Enhance folder {folder} cannot be reached"
            return None
        self.note = ""
        try:
            why = self.hold() if self.hold is not None else ""
        except Exception:  # noqa: BLE001
            why = ""
        try:
            me = self.machine() if self.machine is not None else ""
        except Exception:  # noqa: BLE001
            me = ""
        if why:                         # the graphics card is busy with an edit: the piece waits (nobody takes it)
            if waiting_pieces(folder, machine=me):
                self.note = f"a piece waits until {why}"
            return None
        for nm in waiting_pieces(folder, machine=me):
            token = take(nm)
            if token is None:
                continue
            job = Job(nm.name)
            self.jobs = (self.jobs + [job])[-20:]
            return enhance_one(nm, token, None, job, self.cancel, self.on_change)
        return None

    def _loop(self) -> None:
        while not self._stop.is_set():
            beat_worker(self.folder(), self.note)
            try:
                self.once()
            except Exception as e:  # noqa: BLE001 - the loop never dies
                self.note = f"{type(e).__name__}: {e}"
            self._wake.wait(self.scan_s)
            self._wake.clear()


# ---------------------------------------------------------------------------------------------------------------------
# Settings' "Test Topaz"
# ---------------------------------------------------------------------------------------------------------------------

def test_topaz(work: str | os.PathLike | None = None, seconds: float = 2.0) -> dict:
    """Topaz on a 2-second test clip (made with ffmpeg: 960x540, 24 fps, with sound), checked as a real piece is.
    {ok, text (plain words), seconds}. Its files are deleted after."""
    from match_cuts.common import ffmpeg_bin
    from . import paths
    t0 = time.perf_counter()
    tz = find_topaz()
    if not tz.found:
        return {"ok": False, "text": tz.why, "seconds": 0.0}
    folder = Path(work) if work else paths.work_dir() / "enhance_test"
    folder.mkdir(parents=True, exist_ok=True)
    src, dst = folder / "test_clip.mp4", folder / "test_clip_enhanced.mp4"
    try:
        r = _run([ffmpeg_bin(), "-hide_banner", "-v", "error", "-nostdin", "-y", "-f", "lavfi", "-i",
                  f"testsrc2=size=960x540:rate=24000/1001:duration={seconds}", "-f", "lavfi", "-i",
                  f"sine=frequency=440:sample_rate=48000:duration={seconds}", "-c:v", "libx264", "-pix_fmt",
                  "yuv420p", "-c:a", "aac", "-shortest", str(src)], timeout=120)
        if r.returncode != 0:
            return {"ok": False, "text": f"the test clip could not be made: {(r.stderr or '').strip()[-200:]}",
                    "seconds": 0.0}
        n = int(round(seconds * 24000 / 1001))
        info = {"width": 960, "height": 540, "fps": "24000/1001", "duration": n / (24000 / 1001)}
        run_topaz(src, dst, int(round(info["duration"] * OUT_FPS)), tz)
        chk = check_enhanced(dst, info)
        el = time.perf_counter() - t0
        if not chk["ok"]:
            return {"ok": False, "text": f"Topaz ran, but its result is not right: {chk['why']}", "seconds": el}
        return {"ok": True, "seconds": el,
                "text": f"Topaz works: 2 s of 960x540 at 23.976 fps became {chk['width']}x{chk['height']} at "
                        f"60 fps ({chk['frames']} frames) in {el:.0f} s"}
    except EnhanceError as e:
        return {"ok": False, "text": str(e), "seconds": time.perf_counter() - t0}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "text": f"{type(e).__name__}: {e}", "seconds": time.perf_counter() - t0}
    finally:
        for f in (src, dst):
            try:
                f.unlink(missing_ok=True)
            except OSError:
                pass
