"""Shared helpers: exact time math, timecodes, hashing, caching, decision logging, JSON I/O.

Conventions (see DESIGN.md §2):
  * Frame rates are ``fractions.Fraction`` everywhere (``Fraction(30000, 1001)``, never 29.97).
  * Positions are integer frame indices; intervals are half-open ``[in, out)``.
  * Frame ``k`` of a CFR stream that starts at 0 is displayed at ``t_k = k / fps``.
  * Seconds are only produced when writing outputs (``fmt_seconds`` gives >= 6 decimals).
"""
from __future__ import annotations

import dataclasses
import hashlib
import json
import logging
import math
import os
import subprocess
import time
from fractions import Fraction
from pathlib import Path
from typing import Any, Callable, Iterable

import numpy as np

LOG_NAME = "match_cuts"
log = logging.getLogger(LOG_NAME)

# --------------------------------------------------------------------------------------
# Time math
# --------------------------------------------------------------------------------------

_COMMON_RATES = [
    Fraction(24000, 1001), Fraction(24), Fraction(25), Fraction(30000, 1001), Fraction(30),
    Fraction(48000, 1001), Fraction(48), Fraction(50), Fraction(60000, 1001), Fraction(60),
    Fraction(120000, 1001), Fraction(120), Fraction(15), Fraction(12), Fraction(90), Fraction(100),
]


def parse_fps(value: Any) -> Fraction:
    """Parse '30000/1001', '30', 29.97, Fraction, (num, den) into an exact Fraction.

    Floats are snapped to the nearest common broadcast rate when within 0.01 %, otherwise
    limited to denominator 1001*1000 -- callers should avoid floats.
    """
    if isinstance(value, Fraction):
        return value
    if isinstance(value, np.integer):
        return Fraction(int(value))
    if isinstance(value, np.floating):
        value = float(value)
    if isinstance(value, (tuple, list)) and len(value) == 2:
        return Fraction(int(value[0]), int(value[1]))
    if isinstance(value, int):
        return Fraction(value)
    if isinstance(value, str):
        v = value.strip()
        if "/" in v:
            n, d = v.split("/", 1)
            if int(d) == 0:
                raise ValueError(f"invalid frame rate {value!r}")
            return Fraction(int(n), int(d))
        if "." not in v and "e" not in v.lower():
            return Fraction(int(v))
        value = float(v)
    if isinstance(value, float):
        for r in _COMMON_RATES:
            if abs(float(r) - value) <= float(r) * 1e-4:
                return r
        return Fraction(value).limit_denominator(1001000)
    raise TypeError(f"cannot parse frame rate {value!r}")


def snap_rate(fr: Fraction, tol: float = 1e-4) -> Fraction:
    """Snap a measured (e.g. avg_frame_rate) rational to a common rate if within tol (relative).

    For nominal-rate detection of VFR phone/TikTok files use tol=0.01 (DESIGN §5 conform).
    Returns the NEAREST common rate within tolerance (not the first one listed)."""
    best, best_d = fr, None
    for r in _COMMON_RATES:
        d = abs(float(fr) - float(r)) / float(r)
        if d <= tol and (best_d is None or d < best_d):
            best, best_d = r, d
    return best


def fps_str(fr: Fraction) -> str:
    fr = Fraction(fr)
    return f"{fr.numerator}/{fr.denominator}"


def frame_time(k: int | float, fps: Fraction) -> float:
    """Start time in seconds of frame k (float; use only for output or tolerance math)."""
    return float(Fraction(k) / fps) if isinstance(k, int) else float(k) / float(fps)


def frame_time_exact(k: int, fps: Fraction) -> Fraction:
    return Fraction(int(k)) / Fraction(fps)


def time_to_frame_floor(t: float | Fraction, fps: Fraction, eps: float = 1e-9) -> int:
    """Index of the frame displayed at time t (floor rule, tolerant to fp error)."""
    x = float(Fraction(t) * fps) if isinstance(t, Fraction) else float(t) * float(fps)
    return int(math.floor(x + eps))


def time_to_frame_round(t: float | Fraction, fps: Fraction) -> int:
    x = float(Fraction(t) * fps) if isinstance(t, Fraction) else float(t) * float(fps)
    return int(math.floor(x + 0.5))


def fmt_seconds(t: float) -> float:
    """Round seconds for output with >= 6 decimals (we use 9)."""
    return float(f"{t:.9f}")


def is_drop_frame(fps: Fraction) -> bool:
    """SMPTE drop-frame applies only to 30000/1001 and 60000/1001 (not 24000/1001)."""
    return Fraction(fps) in (Fraction(30000, 1001), Fraction(60000, 1001))


def timecode(frame: int, fps: Fraction, drop_frame: bool | None = None) -> str:
    """SMPTE timecode HH:MM:SS:FF (';' separator for drop-frame 29.97/59.94).

    Non-integer rates are counted at the nominal integer rate (e.g. 23.976 -> 24),
    exactly like editing systems do. Drop-frame is only applied to 30000/1001 and 60000/1001.
    """
    fps = Fraction(fps)
    nominal = int(round(float(fps)))
    if drop_frame is None:
        drop_frame = fps in (Fraction(30000, 1001), Fraction(60000, 1001))
    f = int(frame)
    neg = f < 0
    f = abs(f)
    if drop_frame and nominal in (30, 60):
        drop = 2 if nominal == 30 else 4
        frames_per_10min = nominal * 600 - drop * 9
        frames_per_min = nominal * 60 - drop
        d, m = divmod(f, frames_per_10min)
        if m > drop:
            f = f + drop * 9 * d + drop * ((m - drop) // frames_per_min)
        else:
            f = f + drop * 9 * d
        sep = ";"
    else:
        sep = ":"
    ff = f % nominal
    ss = (f // nominal) % 60
    mm = (f // (nominal * 60)) % 60
    hh = f // (nominal * 3600)
    return f"{'-' if neg else ''}{hh:02d}:{mm:02d}:{ss:02d}{sep}{ff:02d}"


def seconds_tc(t: float, fps: Fraction) -> str:
    """Timecode of the frame displayed at time t (floor rule)."""
    return timecode(time_to_frame_floor(t, fps), fps)


def hms(t: float) -> str:
    """Human readable mm:ss.mmm / h:mm:ss.mmm (for reports)."""
    sign = "-" if t < 0 else ""
    t = abs(t)
    h, rem = divmod(t, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{sign}{int(h)}:{int(m):02d}:{s:06.3f}"
    return f"{sign}{int(m):02d}:{s:06.3f}"


# --------------------------------------------------------------------------------------
# Hashing & caching
# --------------------------------------------------------------------------------------

def file_hash(path: str | os.PathLike, chunk: int = 1 << 22) -> str:
    """Content hash (blake2b-160 over the whole file), memoised on (path, size, mtime)."""
    p = Path(path)
    st = p.stat()
    key = (str(p.resolve()), st.st_size, st.st_mtime_ns)
    cached = _HASH_MEMO.get(key)
    if cached:
        return cached
    h = hashlib.blake2b(digest_size=20)
    with open(p, "rb") as f:
        while True:
            b = f.read(chunk)
            if not b:
                break
            h.update(b)
    digest = h.hexdigest()
    _HASH_MEMO[key] = digest
    return digest


_HASH_MEMO: dict[tuple, str] = {}


def params_hash(*parts: Any) -> str:
    """Stable short hash of JSON-serialisable parameters (dataclasses and Fractions allowed)."""
    blob = json.dumps(parts, sort_keys=True, default=json_default, separators=(",", ":"))
    return hashlib.blake2b(blob.encode(), digest_size=10).hexdigest()


# Bump a stage's version whenever its algorithm changes so stale cache entries are never reused.
STAGE_VERSION: dict[str, int] = {
    "probe": 1, "conform": 1, "proxy": 1, "audio": 1, "layout": 1, "audio_align": 1, "raw_index": 1,
    "sparse_search": 1, "frame_map": 2, "segments": 2, "scenedetect": 1,
}


def stage_key(stage: str, *parts: Any) -> str:
    """Cache key = params_hash(STAGE_VERSION[stage], stage, *parts). Parts must include input hashes."""
    return params_hash(STAGE_VERSION.get(stage, 0), stage, *parts)


def seed_everything(seed: int) -> None:
    """Seed OpenCV's RNG (RANSAC, FLANN randomised trees). Call before creating/training every
    FlannBasedMatcher (it trains lazily on the first knnMatch) and at the start of every worker."""
    import cv2
    cv2.setRNGSeed(int(seed))


class Cache:
    """Content-addressed cache inside WORK_DIR.

    ``cache.path(stage, key, suffix)`` -> Path; ``cache.get_or_compute(...)`` for JSON / npy / npz.
    Keys must include the input file hash(es) and every parameter that affects the result.
    """

    def __init__(self, work_dir: str | os.PathLike):
        self.root = Path(work_dir) / "cache"
        self.root.mkdir(parents=True, exist_ok=True)

    def path(self, stage: str, key: str, suffix: str) -> Path:
        d = self.root / stage
        d.mkdir(parents=True, exist_ok=True)
        return d / f"{key}{suffix}"

    def json(self, stage: str, key: str, compute: Callable[[], Any]) -> Any:
        p = self.path(stage, key, ".json")
        if p.exists():
            return json.loads(p.read_text())
        val = compute()
        atomic_write_text(p, json.dumps(val, default=json_default, indent=1, sort_keys=True))
        return json.loads(p.read_text())

    def npz(self, stage: str, key: str, compute: Callable[[], dict[str, np.ndarray]]) -> dict[str, np.ndarray]:
        p = self.path(stage, key, ".npz")
        if p.exists():
            with np.load(p, allow_pickle=False) as z:
                return {k: z[k] for k in z.files}
        val = compute()
        tmp = p.with_suffix(".tmp.npz")
        np.savez_compressed(tmp, **val)
        os.replace(tmp, p)
        return val


def atomic_write_text(path: str | os.PathLike, text: str) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_text(text)
    os.replace(tmp, p)


# --------------------------------------------------------------------------------------
# JSON
# --------------------------------------------------------------------------------------

def json_default(o: Any) -> Any:
    if isinstance(o, Fraction):
        return fps_str(o)
    if dataclasses.is_dataclass(o) and not isinstance(o, type):
        return dataclasses.asdict(o)
    if isinstance(o, np.integer):
        return int(o)
    if isinstance(o, np.floating):
        return float(o)
    if isinstance(o, np.bool_):
        return bool(o)
    if isinstance(o, np.ndarray):
        return o.tolist()
    if isinstance(o, Path):
        return str(o)
    raise TypeError(f"not JSON serialisable: {type(o)}")


def dump_json(obj: Any, path: str | os.PathLike, indent: int = 2) -> None:
    atomic_write_text(path, json.dumps(obj, default=json_default, indent=indent, sort_keys=False) + "\n")


def load_json(path: str | os.PathLike) -> Any:
    return json.loads(Path(path).read_text())


# --------------------------------------------------------------------------------------
# Decision log
# --------------------------------------------------------------------------------------

class DecisionLog:
    """Append-only JSONL log of every decision with its evidence (scores, margins, alternatives).

    Usage: ``dlog.record("segment", "cut", comp_frame=412, evidence={...}, rejected=[...])``.
    Also mirrored to the python logger at DEBUG level.
    """

    def __init__(self, path: str | os.PathLike | None, truncate: bool = True):
        self.path = Path(path) if path else None
        if self.path:
            self.path.parent.mkdir(parents=True, exist_ok=True)
        self._fh = open(self.path, "w" if truncate else "a") if self.path else None

    def record(self, stage: str, decision: str, **fields: Any) -> None:
        entry = {"stage": stage, "decision": decision, **fields}
        line = json.dumps(entry, default=json_default, sort_keys=True)
        if self._fh:
            self._fh.write(line + "\n")
            self._fh.flush()
        log.debug("%s: %s %s", stage, decision, line)

    def close(self) -> None:
        if self._fh:
            self._fh.close()
            self._fh = None


_NULL_DLOG = DecisionLog(None)


def null_dlog() -> DecisionLog:
    return _NULL_DLOG


def setup_logging(verbose: bool = False, log_file: str | os.PathLike | None = None) -> None:
    root = logging.getLogger(LOG_NAME)
    root.setLevel(logging.DEBUG)
    for h in list(root.handlers):
        root.removeHandler(h)
    sh = logging.StreamHandler()
    sh.setLevel(logging.DEBUG if verbose else logging.INFO)
    sh.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s", "%H:%M:%S"))
    root.addHandler(sh)
    if log_file:
        Path(log_file).parent.mkdir(parents=True, exist_ok=True)
        fh = logging.FileHandler(log_file, mode="a")
        fh.setLevel(logging.DEBUG)
        fh.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
        root.addHandler(fh)


class Timer:
    def __init__(self, label: str):
        self.label = label

    def __enter__(self):
        self.t0 = time.perf_counter()
        return self

    def __exit__(self, *exc):
        log.info("%s took %.1fs", self.label, time.perf_counter() - self.t0)


# --------------------------------------------------------------------------------------
# Subprocess helpers
# --------------------------------------------------------------------------------------

def run(cmd: list[str], check: bool = True, capture: bool = True, **kw) -> subprocess.CompletedProcess:
    log.debug("run: %s", " ".join(map(str, cmd)))
    res = subprocess.run(list(map(str, cmd)), capture_output=capture, text=True, **kw)
    if check and res.returncode != 0:
        raise RuntimeError(f"command failed ({res.returncode}): {' '.join(map(str, cmd))}\n{res.stderr[-4000:]}")
    return res


def ffmpeg_bin() -> str:
    return os.environ.get("FFMPEG", "ffmpeg")


def ffprobe_bin() -> str:
    return os.environ.get("FFPROBE", "ffprobe")


def chunks(seq: list, n: int) -> Iterable[list]:
    for i in range(0, len(seq), n):
        yield seq[i:i + n]
