"""Run 20: the scene's piece enhanced with Topaz Video AI on the laptop, by itself.

The PC (``make_piece``, right after the conversion): the quick sound check (match_cuts.pair_check) finds where the
competitor's scene is in the RAW; a piece of at most MAX_PIECE_S around it (the scene in the middle, the rest as
margin) is cut by stream copy from the keyframe before (match_cuts.excerpt: the movie's own packets, its frames
checked against the RAW at three places), its sound made AAC (Topaz's ffmpeg cannot put PCM in an mp4), and put in
the Enhance folder (``share_dir``: D:\\Shared\\Enhance on the PC) as ``<project>_TOENHANCE.mp4`` with
``<project>_TOENHANCE.json`` (where the piece starts in the RAW, its rate, length, size, which project). The json is
written last: a piece without it is not finished yet.

The laptop (``Worker``, every SCAN_S and at the start): a ``_TOENHANCE.mp4`` with its json that nobody has taken is
taken (``<project>_TOENHANCE.lock``, made with O_EXCL: two computers never take the same one; the owner rewrites it
every few seconds with its progress, and a lock nobody rewrote for LOCK_STALE_S is free again), then Topaz's own
ffmpeg runs on it (``topaz_command``: the user's settings, exactly) into ``<project>_ENHANCING.part.mp4``, which is
checked (``check_enhanced``) and renamed ``<project>_ENHANCED.mp4`` -- the PC never sees a half-written file. A
failure writes ``<project>_ENHANCE_FAILED.txt`` (the reason in plain words).

The PC again (``status`` / ``take_back``): ``_ENHANCED.mp4`` checked (the same length as the piece within one of its
frames, 60 fps, twice its size, it decodes from the first frame), copied into the project (``enhance\\raw_enhanced.mp4``:
the edit's RAW from then on, ``edit_raw``) and the share's files deleted. Nothing back within TIMEOUT_S, or the
failure file: the edit is made with the normal RAW, and a late enhanced piece is only used when you press "Use the
enhanced piece".

MOVIERECAPS_FAKE_TOPAZ=1 (the tests): a plain 2x scale + 60 fps with the normal ffmpeg instead of Topaz."""
from __future__ import annotations

import json
import math
import os
import shutil
import subprocess
import threading
import time
from dataclasses import dataclass, field
from fractions import Fraction
from pathlib import Path
from typing import Any, Callable

from . import machine as M

MAX_PIECE_S = 80.0                  # the piece: at most 1:20 (29 Oct, your call: 2 min took the laptop ~1 h 45 min)
TIMEOUT_S = 3600.0                  # nothing back from the laptop within this: the normal RAW
TIMEOUT_WHY = "the laptop did not answer in 1 hour"
STILL_WHY = "the laptop was still enhancing it"
MAX_WAIT_S = 4 * 3600.0             # ... but a laptop that is still enhancing it (its lock rewritten) is waited for up
                                    #   to this long (this laptop: 1080p -> 4K runs at ~1/50 of real time)
SCAN_S = 30.0                       # the laptop looks for new pieces this often
LOCK_STALE_S = 15 * 60.0            # a lock not rewritten for this long: its computer stopped -- free again
LOCK_BEAT_S = 5.0                   # the owner rewrites its lock this often (its progress)
OUT_FPS = 60
OUT_BITRATE = "180M"
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
PROJECT_SUB = "enhance"             # the project's own copy: <project>\enhance\raw_enhanced.mp4
PROJECT_FILE = "raw_enhanced.mp4"
NO_WINDOW = 0x08000000 if os.name == "nt" else 0

# the user's Topaz settings (run 20): Proteus (prob-3) Manual -- revert compression 80, improve detail 80, sharpen 60,
# reduce noise 80, dehalo 10, anti-alias/deblur -60, add noise 0, recover original detail 10 -- at 2x the piece's own
# size; Apollo (apo-8) to 60 fps, no slow motion, duplicate frames not replaced; no stabilization, motion deblur or
# grain; H.265 Main10 at a constant 180 Mb/s in an mp4, the sound copied
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

    def all(self) -> list[Path]:
        return [self.piece, self.info, self.lock, self.part, self.enhanced, self.failed]


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


def find_scene(competitor: str | os.PathLike, raw: str | os.PathLike, work: str | os.PathLike | None = None,
               progress: Callable[[str], None] | None = None) -> list[float]:
    """Where the competitor's sound is in the RAW (RAW seconds), by the tool's quick sound check. Raises EnhanceError
    when it cannot say."""
    from match_cuts import pair_check as PC
    res = PC.check(str(competitor), str(raw), work, progress)
    snd = getattr(res, "sound", None)
    if getattr(res, "wrong", False):
        raise EnhanceError("the quick check says the competitor does not come from this RAW")
    if snd is None or getattr(snd, "state", "") != PC.FOUND or not list(getattr(snd, "where", None) or []):
        why = str(getattr(snd, "why", "") or "").strip()
        raise EnhanceError("the sound check could not find the scene in the RAW" + (f" ({why})" if why else ""))
    return [float(x) for x in snd.where]


def make_piece(competitor: str | os.PathLike, raw: str | os.PathLike, name: str, out_dir: str | os.PathLike,
               project: str | os.PathLike | None = None, work: str | os.PathLike | None = None,
               progress: Callable[[float, str], None] | None = None, max_s: float = MAX_PIECE_S) -> dict:
    """The piece to enhance, put in ``out_dir`` (the Enhance folder) as <name>_TOENHANCE.mp4 + .json. Returns its
    info (the json's content); raises EnhanceError with the reason in plain words -- then the edit uses the normal
    RAW."""
    from match_cuts import excerpt as X
    from match_cuts.pair_check import WIN_S
    say = progress or (lambda share, text: None)
    raw = Path(raw)
    nm = names(out_dir, name)
    work = Path(work) if work else Path(project or raw.parent) / PROJECT_SUB
    t0 = time.perf_counter()
    say(0.05, "finding the scene in the RAW (the sound)")
    places = find_scene(competitor, raw, work / "pair_check", lambda s: say(0.1, f"finding the scene: {s}"))
    say(0.4, "reading the RAW's keyframes")
    m = X.movie_facts(str(raw))
    if m.why:
        raise EnhanceError(f"the RAW cannot be cut losslessly: {m.why}")
    p = plan(m, places, WIN_S, max_s)
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
    except EnhanceError:
        nm.piece.unlink(missing_ok=True)
        raise
    except Exception as e:  # noqa: BLE001 - said in plain words
        nm.piece.unlink(missing_ok=True)
        raise EnhanceError(f"the piece could not be made: {e}") from e
    finally:
        mov.unlink(missing_ok=True)
    fps = m.rate
    a, b = p.k0 / float(fps), (p.k0 + n) / float(fps)
    info = {"project": safe_name(name), "project_folder": str(project or ""), "computer": M.host(),
            "raw": os.path.abspath(str(raw)), "raw_name": raw.name, "start_frame": int(p.k0), "start_s": round(a, 6),
            "frames": int(n), "fps": str(fps), "duration": round(n / float(fps), 6), "width": int(m.width),
            "height": int(m.height), "scene": [round(min(places), 3), round(max(places) + WIN_S, 3)],
            "places": [round(x, 3) for x in places], "raw_duration": round(float(m.duration), 3),
            "bytes": int(nm.piece.stat().st_size), "check": chk.get("rows"), "made": time.time(),
            "seconds": round(time.perf_counter() - t0, 1),
            "where": f"{_clock(a)}-{_clock(b)} of {_clock(m.duration)}"}
    _write_json(nm.info, info)
    say(1.0, f"the piece {info['where']} is in the Enhance folder")
    return info


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

def _probe(path: Path) -> dict:
    from match_cuts.common import ffprobe_bin
    r = _run([ffprobe_bin(), "-v", "error", "-select_streams", "v:0", "-count_packets", "-show_entries",
              "stream=codec_name,width,height,r_frame_rate,avg_frame_rate,nb_read_packets,nb_frames,start_time",
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
        want_w, want_h = 2 * int(info.get("width") or 0), 2 * int(info.get("height") or 0)
        if (w, h) != (want_w, want_h):
            raise EnhanceError(f"it is {w}x{h}, not twice the piece's size ({want_w}x{want_h})")
        if fr != OUT_FPS:
            raise EnhanceError(f"it is {float(fr):.3f} fps, not {OUT_FPS}")
        dur = n / float(OUT_FPS)
        out["duration"] = round(dur, 4)
        piece_fps = float(Fraction(str(info.get("fps") or "0/1")) or 0) or OUT_FPS
        want = float(info.get("duration") or 0.0)
        if abs(dur - want) > 1.0 / piece_fps + 1e-3:
            raise EnhanceError(f"it is {dur:.3f} s long, the piece {want:.3f} s (more than one frame apart)")
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
            return Back("failed", (why[0] if why else "the laptop could not enhance it"), 0.0, nm.failed)
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
    info = info or read_info(nm.info) or {}
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
    return {"ok": True, "why": "", "file": str(dst), "check": chk}


def clear(folder: str | os.PathLike, name: str) -> None:
    """The project's files in the Enhance folder go (only its own: the piece, its json, the lock, the result)."""
    for f in names(folder, name).all():
        try:
            f.unlink(missing_ok=True)
        except OSError:
            pass


def watch(project: str | os.PathLike, now: float | None = None, timeout_s: float = TIMEOUT_S) -> dict:
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
    if state == "late":
        return {"state": "late_ready"} if b.state == "enhanced" else {}
    if b.state == "enhanced":
        r = take_back(folder, name, p.folder, (rec.get("piece") or None))
        if r["ok"]:
            c = r.get("check") or {}
            text = f"enhanced on the laptop: {c.get('width')}x{c.get('height')}, {OUT_FPS} fps"
            p.update(enhance={"state": "used", "used": now, "file": f"{PROJECT_SUB}\\{PROJECT_FILE}", "text": text})
            return {"state": "used", "text": text}
        why = f"the enhanced piece is not right ({r['why']}) -- the normal RAW is used"
        clear(folder, name)
        p.update(enhance={"state": "failed", "why": why})
        return {"state": "failed", "why": why}
    if b.state in ("failed", "gone"):
        why = b.text if b.state == "gone" else f"the laptop could not enhance it: {b.text}"
        clear(folder, name)
        p.update(enhance={"state": "failed", "why": why})
        return {"state": "failed", "why": why}
    waited = now - float(rec.get("sent") or now)
    if waited > timeout_s and not (b.state == "enhancing" and waited < MAX_WAIT_S):
        why = TIMEOUT_WHY if b.state != "enhancing" else f"{STILL_WHY} after {_clock(waited)}"
        p.update(enhance={"state": "late", "why": why, "late": now})
        return {"state": "late", "why": why}
    return {"state": "progress", "share": b.share, "text": b.text + (" · over 1 hour, still working"
                                                                       if waited > timeout_s else "")}


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


def edit_raw(project_data: dict, folder: str | os.PathLike) -> Path | None:
    """The enhanced piece the edit plays instead of the RAW (project.json's "enhance": used), else None."""
    e = project_data.get("enhance") if isinstance(project_data, dict) else None
    if not isinstance(e, dict) or e.get("state") != "used":
        return None
    f = Path(folder) / PROJECT_SUB / PROJECT_FILE
    return f if f.is_file() else None


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
          r"C:\Program Files"]
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


def topaz_command(tz: Topaz, src: str | os.PathLike, dst: str | os.PathLike) -> list[str]:
    """Topaz's ffmpeg with the user's settings: Proteus at 2x the piece's size, then Apollo to 60 fps; H.265 Main10
    at a constant 180 Mb/s; the sound copied. The fake Topaz: a plain 2x scale and 60 fps (the tests)."""
    head = [str(tz.ffmpeg), "-hide_banner", "-nostdin", "-y", "-i", str(src), "-map", "0:v:0", "-map", "0:a:0?"]
    tail = ["-c:a", "copy", "-map_metadata", "-1", "-movflags", "+faststart", "-f", "mp4", str(dst)]
    if tz.fake:
        return head + ["-vf", f"scale=iw*2:ih*2:flags=lanczos,fps={OUT_FPS}", "-c:v", "libx264", "-preset",
                       "ultrafast", "-crf", "18", "-pix_fmt", "yuv420p"] + tail
    chain = f"tvai_up={_opts({**PROTEUS, **GPU})},tvai_fi={_opts({**APOLLO, **GPU})}"
    return head + ["-vf", chain, "-c:v", "hevc_nvenc", "-profile:v", "main10", "-pix_fmt", "p010le", "-rc", "cbr",
                   "-b:v", OUT_BITRATE, "-maxrate", OUT_BITRATE, "-bufsize", "360M", "-preset", "p5",
                   "-tag:v", "hvc1"] + tail


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
    return f"Topaz stopped (code {code})" + (f": {last[-1][:200]}" if last else "")


def run_topaz(src: str | os.PathLike, dst: str | os.PathLike, out_frames: int, tz: Topaz | None = None,
              progress: Callable[[float, float | None], None] | None = None,
              cancel: threading.Event | None = None) -> None:
    """Topaz on ``src`` into ``dst``; ``progress(share, seconds left)`` from its "frame=" lines. Raises EnhanceError
    (plain words) when it fails or was stopped."""
    tz = tz or find_topaz()
    if not tz.found:
        raise EnhanceError(tz.why)
    cmd = topaz_command(tz, src, dst)
    t0 = time.monotonic()
    pr = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                          env=topaz_env(tz), creationflags=NO_WINDOW)
    tail: list[str] = []
    buf = b""
    try:
        while True:
            if cancel is not None and cancel.is_set():
                pr.kill()
                raise EnhanceError("stopped")
            ch = pr.stderr.read(256)
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
                if line.startswith("frame=") and out_frames > 0 and progress is not None:
                    try:
                        n = int(line.split("=", 1)[1].strip().split()[0])
                    except (ValueError, IndexError):
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


def lock_stale(path: Path, now: float | None = None) -> bool:
    try:
        age = (now if now is not None else time.time()) - Path(path).stat().st_mtime
    except OSError:
        return False
    return age > LOCK_STALE_S


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


def waiting_pieces(folder: str | os.PathLike, now: float | None = None) -> list[Names]:
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
            out.append((p.stat().st_mtime, nm))
        except OSError:
            continue
    return [nm for _t, nm in sorted(out, key=lambda x: x[0])]


@dataclass
class Job:
    """One piece the laptop enhances (what its Progress page shows)."""
    name: str
    state: str = "enhancing"        # "enhancing" | "done" | "failed"
    share: float = 0.0
    left_s: float | None = None
    why: str = ""
    started: float = field(default_factory=time.time)
    ended: float | None = None

    def line(self) -> str:
        if self.state == "enhancing":
            return f"Enhancing {self.name} on this laptop · {int(round(100 * self.share))} %" + (
                f" · {_minutes(self.left_s)} left" if self.left_s else "")
        if self.state == "done":
            return f"{self.name}: enhanced in {_clock((self.ended or time.time()) - self.started)} -- the PC takes it"
        return f"{self.name}: not enhanced -- {self.why}"


def enhance_one(nm: Names, token: str, tz: Topaz | None = None, job: Job | None = None,
                cancel: threading.Event | None = None, on_change: Callable[[Job], None] | None = None) -> Job:
    """Topaz on a taken piece: <name>_ENHANCING.part.mp4, checked, renamed <name>_ENHANCED.mp4; a failure writes
    <name>_ENHANCE_FAILED.txt. The lock goes either way."""
    job = job or Job(nm.name)
    tell = on_change or (lambda j: None)
    info = read_info(nm.info) or {}
    last = [0.0]

    def prog(share: float, left: float | None) -> None:
        job.share, job.left_s = share, left
        tell(job)
        if time.monotonic() - last[0] >= LOCK_BEAT_S:
            last[0] = time.monotonic()
            beat(nm, token, share=round(share, 4), left_s=round(left, 1) if left else None)

    try:
        if not info:
            raise EnhanceError("its json (where the piece is in the RAW) cannot be read")
        dur = float(info.get("duration") or 0.0)
        need = int(dur * 180e6 / 8 * 1.2) + (50 << 20)
        try:
            free = shutil.disk_usage(str(nm.folder)).free
        except OSError:
            free = None
        if free is not None and free < need:
            raise EnhanceError(f"the Enhance folder's drive has {free / 1e9:.1f} GB free, the enhanced piece needs "
                               f"about {need / 1e9:.1f} GB")
        nm.part.unlink(missing_ok=True)
        run_topaz(nm.piece, nm.part, int(round(dur * OUT_FPS)), tz, prog, cancel)
        chk = check_enhanced(nm.part, info)
        if not chk["ok"]:
            raise EnhanceError(f"Topaz's result is not right: {chk['why']}")
        os.replace(nm.part, nm.enhanced)
        job.state, job.share, job.left_s = "done", 1.0, None
    except Exception as e:  # noqa: BLE001 - said in plain words, and written for the PC
        job.state, job.why = "failed", (str(e) if isinstance(e, EnhanceError) else f"{type(e).__name__}: {e}")
        try:
            nm.part.unlink(missing_ok=True)
        except OSError:
            pass
        if job.why != "stopped":
            try:
                more = getattr(e, "details", "")
                nm.failed.write_text(f"{job.why}\n\n(the laptop {M.host()}, {time.strftime('%Y-%m-%d %H:%M')})\n"
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
                 scan_s: float = SCAN_S):
        self.folder = folder if callable(folder) else (lambda f=Path(folder): f)
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
        for nm in waiting_pieces(folder):
            token = take(nm)
            if token is None:
                continue
            job = Job(nm.name)
            self.jobs = (self.jobs + [job])[-20:]
            return enhance_one(nm, token, None, job, self.cancel, self.on_change)
        return None

    def _loop(self) -> None:
        while not self._stop.is_set():
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
