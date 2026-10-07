"""Shared helpers: exact time math, timecodes, hashing, caching, decision logging, JSON I/O.

Conventions (see DESIGN.md §2):
  * Frame rates are ``fractions.Fraction`` everywhere (``Fraction(30000, 1001)``, never 29.97).
  * Positions are integer frame indices; intervals are half-open ``[in, out)``.
  * Frame ``k`` of a CFR stream that starts at 0 is displayed at ``t_k = k / fps``.
  * Seconds are only produced when writing outputs (``fmt_seconds`` gives >= 6 decimals).
"""
from __future__ import annotations

import contextlib
import dataclasses
import hashlib
import json
import logging
import math
import os
import subprocess
import sys
import time
import zipfile
from fractions import Fraction
from pathlib import Path
from typing import Any, Callable, Iterable, Iterator

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
    "probe": 2, "conform": 1, "proxy": 1, "audio": 1, "layout": 2, "audio_align": 2, "raw_index": 1,
    "sparse_search": 4, "frame_map": 6, "segments": 5, "scenedetect": 1, "people": 1,
}


# ... and a cached result is only ever reused by the code that computed it: every key also holds a fingerprint of
# that code (stage_code_hash: the stage's own modules below and every package module they import, read from their
# import statements), so a change that forgets the bump above cannot reuse what the old code computed. The
# orchestration (pipeline, exports, reports, checks) is left out: it decides what is computed and with which
# parameters -- which the keys hold already --, not how.
STAGE_CODE: dict[str, tuple[str, ...]] = {
    "probe": ("probe",), "conform": ("conform",), "proxy": ("proxies",), "audio": ("proxies",),
    "layout": ("layout",), "layout_refine": ("layout",), "audio_align": ("audio_align",),
    "raw_index": ("visual_match",), "sparse_search": ("visual_match",), "frame_map": ("refine",),
    "fullres_recheck": ("fullres",), "scenedetect": ("segment",), "people": ("people",), "raw_shots": ("shots",),
    "captions_spans": ("caption_ocr",), "captions_asr": ("transcribe",), "captions_score": ("transcribe",),
}
NOT_STAGE_CODE = frozenset({"pipeline", "cli", "report", "verify", "export_ae", "export_xml_edl", "render_preview",
                            "check_all", "learn", "testcases", "restyle", "asr_bench", "raw_only", "__main__",
                            "__init__"})
_PACKAGE_IMPORTS: dict[str, dict[str, frozenset[str]]] = {}
_STAGE_CODE_HASH: dict[tuple[str, str], str] = {}


def _package_root(root: str | os.PathLike | None = None) -> Path:
    return Path(root) if root is not None else Path(__file__).resolve().parent


def package_imports(root: str | os.PathLike | None = None) -> dict[str, frozenset[str]]:
    """{module: the package modules it imports} for every module of the package (``root``: this one), read from the
    import statements anywhere in it (those inside functions too)."""
    r = _package_root(root)
    if str(r) not in _PACKAGE_IMPORTS:
        import ast
        mods = {p.stem: p for p in r.glob("*.py")}
        graph: dict[str, frozenset[str]] = {}
        for name, p in mods.items():
            deps: set[str] = set()
            try:
                tree = ast.parse(p.read_bytes())
            except (OSError, SyntaxError, ValueError):
                tree = None
            for n in ast.walk(tree) if tree is not None else ():
                if isinstance(n, ast.ImportFrom):
                    mod = (n.module or "").split(".")
                    if n.level == 1:
                        deps.update([mod[0]] if mod[0] else [a.name for a in n.names])
                    elif n.level == 0 and mod[0] == LOG_NAME:
                        deps.update([mod[1]] if len(mod) > 1 else [a.name for a in n.names])
                elif isinstance(n, ast.Import):
                    deps.update(a.name.split(".")[1] for a in n.names
                                if a.name.split(".")[0] == LOG_NAME and "." in a.name)
            graph[name] = frozenset(d for d in deps if d in mods and d != name)
        _PACKAGE_IMPORTS[str(r)] = graph
    return _PACKAGE_IMPORTS[str(r)]


def stage_modules(stage: str, root: str | os.PathLike | None = None) -> list[str]:
    """The package modules whose code computes ``stage``'s cached result (STAGE_CODE and what they import, the
    orchestration modules left out); [] for a stage not in STAGE_CODE."""
    graph = package_imports(root)
    seen: set[str] = set()
    todo = list(STAGE_CODE.get(stage, ()))
    while todo:
        m = todo.pop()
        if m in seen or m not in graph:
            continue
        seen.add(m)
        todo += [d for d in graph[m] if d not in NOT_STAGE_CODE]
    return sorted(seen)


def stage_code_hash(stage: str, root: str | os.PathLike | None = None) -> str:
    """Fingerprint of the source of :func:`stage_modules` (line ends normalised, so a checkout with CRLF line ends
    keys as one with LF); '' for a stage not in STAGE_CODE. Computed once per process."""
    r = _package_root(root)
    if (str(r), stage) not in _STAGE_CODE_HASH:
        h = hashlib.blake2b(digest_size=8)
        mods = stage_modules(stage, r)
        for m in mods:
            h.update(m.encode() + b"\0" + (r / f"{m}.py").read_bytes().replace(b"\r\n", b"\n") + b"\0")
        _STAGE_CODE_HASH[(str(r), stage)] = h.hexdigest() if mods else ""
    return _STAGE_CODE_HASH[(str(r), stage)]


def stage_key(stage: str, *parts: Any) -> str:
    """Cache key = params_hash(STAGE_VERSION[stage], stage, the code's fingerprint, *parts). Parts must include
    input hashes."""
    return params_hash(STAGE_VERSION.get(stage, 0), stage, stage_code_hash(stage), *parts)


def seed_everything(seed: int) -> None:
    """Seed OpenCV's RNG (RANSAC, FLANN randomised trees). Call before creating/training every
    FlannBasedMatcher (it trains lazily on the first knnMatch) and at the start of every worker."""
    import cv2
    cv2.setRNGSeed(int(seed))


# A cache file cut short or zeroed -- the PC reset or lost power while it was written (the rename that publishes it is
# atomic, the data it points to is not always on disk yet) -- is a cache miss: dropped and computed again, not a crash.
UNREADABLE_CACHE: tuple[type[BaseException], ...] = (ValueError, EOFError, OSError, zipfile.BadZipFile)


def drop_unreadable(path: str | os.PathLike, err: BaseException) -> None:
    """Log and remove an unreadable cache file (UNREADABLE_CACHE) so that it is computed again."""
    log.warning("cache file %s is unreadable (%s: %s) -- computed again", path, type(err).__name__, err)
    with contextlib.suppress(OSError):
        os.remove(path)


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
            try:
                return json.loads(p.read_text(encoding="utf-8"))
            except UNREADABLE_CACHE as e:
                drop_unreadable(p, e)
        val = compute()
        atomic_write_text(p, json.dumps(val, default=json_default, indent=1, sort_keys=True))
        return json.loads(p.read_text(encoding="utf-8"))

    def npz(self, stage: str, key: str, compute: Callable[[], dict[str, np.ndarray]]) -> dict[str, np.ndarray]:
        p = self.path(stage, key, ".npz")
        if p.exists():
            try:
                # the file opened here, closed here: np.load(path) on a damaged .npz fails inside numpy after it has
                # handed its own file over to the NpzFile that failed -- left open until the error is freed, and on
                # Windows an open file cannot be removed or replaced (Task 10: the cache then could not heal itself)
                with open(p, "rb") as f, np.load(f, allow_pickle=False) as z:
                    return {k: z[k] for k in z.files}
            except UNREADABLE_CACHE as e:
                drop_unreadable(p, e)
        val = compute()
        tmp = p.with_name(f"{p.stem}.{os.getpid()}.tmp.npz")      # its own per process: two runs on one work folder
        np.savez_compressed(tmp, **val)
        replace_file(tmp, p)
        return val


# Windows: a file that another program holds open without delete sharing (Excel with cutlist.csv, a video
# player with preview_recreation.mp4, a virus scanner briefly checking a fresh file) cannot be replaced or
# removed -- os.replace raises PermissionError (WinError 5 / 32). Retried for a few seconds, then explained.
REPLACE_RETRY_S = 5.0


def flush_to_disk(path: str | os.PathLike) -> None:
    """The file's data written to the disk now (fsync), before the rename that publishes it: a PC that resets or
    loses power after the rename could otherwise leave the renamed file with blocks of zeros -- a cache entry that
    still loads and silently feeds wrong frames or numbers to the next run. Skipped for what cannot be opened so."""
    try:
        fd = os.open(path, os.O_RDWR | getattr(os, "O_BINARY", 0))
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)


def replace_file(src: str | os.PathLike, dst: str | os.PathLike, retry_s: float | None = None) -> None:
    """``os.replace(src, dst)`` once ``src`` is on the disk (:func:`flush_to_disk`), retried on PermissionError for up
    to ``retry_s`` seconds (REPLACE_RETRY_S); then a PermissionError that names the file and says to close the
    program holding it."""
    limit = REPLACE_RETRY_S if retry_s is None else float(retry_s)
    flush_to_disk(src)
    t0 = time.monotonic()
    delay = 0.05
    while True:
        try:
            os.replace(src, dst)
            return
        except PermissionError as e:
            if time.monotonic() - t0 >= limit:
                raise PermissionError(
                    e.errno, f"cannot replace {dst}: the file is in use or read-only ({e.strerror}). Close the program "
                    f"that has it open (e.g. Excel, a video player, After Effects) and run again", str(dst)) from e
            time.sleep(delay)
            delay = min(0.5, delay * 2)


def atomic_write_text(path: str | os.PathLike, text: str) -> None:
    """Write UTF-8 text atomically. Never the platform default encoding: on Windows that is cp1252, which
    cannot encode the arrows / dashes / ± of report.md and crashed S10 (UnicodeEncodeError)."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(f"{p.name}.{os.getpid()}.tmp")             # its own per process (two runs, one folder)
    tmp.write_text(text, encoding="utf-8")
    replace_file(tmp, p)


def write_image(path: str | os.PathLike, img: np.ndarray, ext: str = ".png") -> bool:
    """Write an image atomically through Python file I/O (``cv2.imencode``): ``cv2.imwrite`` cannot open
    non-ASCII paths on Windows (e.g. C:\\Users\\Žygimantas\\...). Returns False when encoding failed."""
    import cv2
    p = Path(path)
    ok, buf = cv2.imencode(ext, img)
    if not ok:
        return False
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(f"{p.stem}.{os.getpid()}.tmp{ext}")
    tmp.write_bytes(buf.tobytes())
    replace_file(tmp, p)
    return True


def read_image(path: str | os.PathLike, flags: int | None = None) -> np.ndarray | None:
    """``cv2.imread`` through Python file I/O (``cv2.imdecode``; it cannot open non-ASCII paths on Windows either:
    it returns None for every file under C:\\Users\\Žygimantas\\...). None when the file cannot be read or decoded."""
    import cv2
    try:
        data = np.fromfile(str(path), dtype=np.uint8)
    except (OSError, ValueError):
        return None
    if data.size == 0:
        return None
    return cv2.imdecode(data, cv2.IMREAD_COLOR if flags is None else int(flags))


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
    return json.loads(Path(path).read_text(encoding="utf-8"))


# --------------------------------------------------------------------------------------
# Decision log
# --------------------------------------------------------------------------------------

class DecisionCapture(list):
    """The records (JSON-normalised dicts, exactly as written to the log) emitted while a
    ``DecisionLog.capture(tag)`` block was active."""

    def __init__(self, tag: str | None = None):
        super().__init__()
        self.tag = tag


class DecisionLog:
    """Append-only JSONL log of every decision with its evidence (scores, margins, alternatives).

    Usage: ``dlog.record("segment", "cut", comp_frame=412, evidence={...}, rejected=[...])``.
    Also mirrored to the python logger at DEBUG level.

    Cached stages (DESIGN §7 D6): ``with dlog.capture("layout") as recs: ...`` collects every record
    emitted inside the block (nested captures each get them) so the pipeline can store them next to the
    stage's cache entry; on a cache hit ``dlog.replay(recs, cached=True, cache_key=key)`` writes them
    again (with the extra fields), so a cached re-run keeps the full evidence.
    """

    def __init__(self, path: str | os.PathLike | None, truncate: bool = True):
        self.path = Path(path) if path else None
        if self.path:
            self.path.parent.mkdir(parents=True, exist_ok=True)
        self._fh = open(self.path, "w" if truncate else "a", encoding="utf-8") if self.path else None
        self._captures: list[DecisionCapture] = []

    def _emit(self, entry: dict) -> None:
        line = json.dumps(entry, default=json_default, sort_keys=True)
        if self._fh:
            self._fh.write(line + "\n")
            self._fh.flush()
        caps = getattr(self, "_captures", None)
        if caps:
            rec = json.loads(line)
            for cap in caps:
                cap.append(dict(rec))
        log.debug("%s: %s %s", entry.get("stage"), entry.get("decision"), line)

    def record(self, stage: str, decision: str, **fields: Any) -> None:
        self._emit({"stage": stage, "decision": decision, **fields})

    @contextlib.contextmanager
    def capture(self, tag: str | None = None) -> Iterator[DecisionCapture]:
        """Collect the records emitted (or replayed) inside the block into a ``DecisionCapture``."""
        if not hasattr(self, "_captures"):
            self._captures = []
        cap = DecisionCapture(tag)
        self._captures.append(cap)
        try:
            yield cap
        finally:
            for i in range(len(self._captures) - 1, -1, -1):
                if self._captures[i] is cap:
                    del self._captures[i]
                    break

    def replay(self, records: Iterable[dict], **extra: Any) -> int:
        """Write previously captured records again, each updated with ``extra`` (e.g. ``cached=True,
        cache_key=...``). Records without a stage/decision are skipped. Returns the number written."""
        n = 0
        for r in records:
            if not isinstance(r, dict) or "stage" not in r or "decision" not in r:
                continue
            self._emit({**r, **extra})
            n += 1
        return n

    def close(self) -> None:
        if self._fh:
            self._fh.close()
            self._fh = None


def load_decisions(path: str | os.PathLike) -> list[dict]:
    """Records of a decisions .jsonl file (blank / unparsable lines are skipped)."""
    out: list[dict] = []
    p = Path(path)
    if not p.exists():
        return out
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        if isinstance(rec, dict):
            out.append(rec)
    return out


def save_decisions(path: str | os.PathLike, records: Iterable[dict]) -> None:
    """Write records as JSONL (atomically)."""
    atomic_write_text(path, "".join(json.dumps(r, default=json_default, sort_keys=True) + "\n" for r in records))


_NULL_DLOG = DecisionLog(None)


def null_dlog() -> DecisionLog:
    return _NULL_DLOG


def _tolerant_stream(stream: Any) -> None:
    """Characters the console cannot encode become backslash escapes instead of logging errors (Windows: a
    redirected stderr uses the ANSI code page, e.g. cp1252, which has no arrows / >= signs)."""
    try:
        if getattr(stream, "errors", "strict") == "strict" and hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="backslashreplace")
    except (AttributeError, ValueError, OSError):  # pragma: no cover - a replaced / closed stream
        pass


def setup_logging(verbose: bool = False, log_file: str | os.PathLike | None = None) -> None:
    root = logging.getLogger(LOG_NAME)
    root.setLevel(logging.DEBUG)
    for h in list(root.handlers):
        root.removeHandler(h)
    _tolerant_stream(sys.stderr)
    sh = logging.StreamHandler()
    sh.setLevel(logging.DEBUG if verbose else logging.INFO)
    sh.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s", "%H:%M:%S"))
    root.addHandler(sh)
    if log_file:
        Path(log_file).parent.mkdir(parents=True, exist_ok=True)
        fh = logging.FileHandler(log_file, mode="a", encoding="utf-8")
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
    res = subprocess.run(list(map(str, cmd)), capture_output=capture, text=True, encoding="utf-8", errors="replace",
                         **kw)
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


# --------------------------------------------------------------------------------------
# Worker pools: native-thread hygiene, watchdog, progress (DESIGN D7)
# --------------------------------------------------------------------------------------
#
# Hang protection. A ``multiprocessing.Pool`` waits forever when a worker dies (killed by the OS when the
# machine runs out of memory, or a crash) or deadlocks: the lost task never reports back, and a worker that
# died while holding the pool's queue lock blocks every other worker on that semaphore (seen in wave 3: a CLI
# run sat silently in the S5.3 pool for 25 min). Two defences:
#   * hygiene -- never fork while native thread pools run in the parent (their locks would be copied into the
#     child in whatever state they had): scipy's ducc FFT pool is never started (DUCC0_NUM_THREADS=1 at package
#     import; every FFT here runs single-threaded anyway), PyAV's per-thread swscale context (and its slice
#     threads) is released and OpenCV's pool is set to one thread before forking. OpenBLAS stops its own threads
#     around fork (pthread_atfork). A census right after forking counts the OS threads Python does not own; any
#     survivor makes ``visual_match.parallel_map`` discard that fork pool and use spawn workers instead.
#   * watchdog -- results are collected with a timeout: no result for ``POOL_WATCHDOG['stall_s']`` or a dead
#     worker process stops the pool with a clear warning and the remaining tasks run in this process. Every task
#     is seeded on its own, so the results are bit-identical, only slower.

POOL_WATCHDOG: dict[str, float] = {"stall_s": 300.0, "progress_s": 30.0, "poll_s": 0.5, "max_failures": 2}


def configure_pools(stall_s: float | None = None, progress_s: float | None = None,
                    max_failures: int | None = None) -> None:
    """Set the watchdog limits (Config.pool_stall_timeout_s / progress_log_s / pool_max_failures)."""
    if stall_s is not None:
        POOL_WATCHDOG["stall_s"] = max(1.0, float(stall_s))
    if progress_s is not None:
        POOL_WATCHDOG["progress_s"] = max(1.0, float(progress_s))
    if max_failures is not None:
        POOL_WATCHDOG["max_failures"] = max(0, int(max_failures))


class PoolFailure(RuntimeError):
    """A worker pool stopped delivering results (no result within the stall timeout, or a worker died)."""


def native_threads() -> int | None:
    """OS threads of this process that Python does not own (native library pools), or None when the platform
    cannot tell (no /proc/self/task: Windows, macOS)."""
    import threading
    try:
        n = len(os.listdir("/proc/self/task"))
    except OSError:
        return None
    return max(0, n - threading.active_count())


def release_native_threads() -> None:
    """Free the native thread pools of THIS thread that it does not need right now (call before forking).

    PyAV (19) keeps one swscale context per thread (``av.video.frame._thread_local.reformatter``) for
    ``frame.to_ndarray``; its slice threads live as long as the context. A forked child inherits that context
    without its threads and hangs in its next conversion. Dropping the reference frees the context (the next
    conversion makes a new one; pixels are identical). scipy's HiGHS (``linprog``) keeps a scheduler with idle
    worker threads after a solve: reset (it restarts with the next solve). Only touches modules already imported."""
    import sys
    fr = sys.modules.get("av.video.frame")
    tl = getattr(fr, "_thread_local", None) if fr is not None else None
    if tl is not None and getattr(tl, "reformatter", None) is not None:
        try:
            tl.reformatter = None
        except Exception:  # noqa: BLE001 - best effort: the census after forking still catches survivors
            pass
    # scipy's HiGHS LP solver (phase_solve's linprog) keeps a global task scheduler with idle worker threads after a
    # solve; stopped here (it restarts on the next solve, same options -> same results)
    hs = sys.modules.get("scipy.optimize._highspy._core")
    reset = getattr(getattr(hs, "_Highs", None), "resetGlobalScheduler", None) if hs is not None else None
    if callable(reset):
        try:
            reset(True)
        except Exception:  # noqa: BLE001 - best effort, as above
            pass


def limit_native_threads() -> None:
    """Never start scipy's ducc FFT thread pool: every FFT in this package runs with workers=1, and the pool's
    idle threads would otherwise be alive whenever a worker pool forks. Must run before the first scipy FFT
    (the pool size is read once); an explicit DUCC0_NUM_THREADS in the environment wins."""
    os.environ.setdefault("DUCC0_NUM_THREADS", "1")


_BLAS_CTL: list = []


def blas_lib_paths(d: str) -> list[str]:
    """numpy's bundled OpenBLAS in its ``numpy.libs`` folder: ``.so`` on Linux, ``.dll`` on Windows (the
    Windows wheels name it e.g. ``libscipy_openblas64_-<hash>.dll``), ``.dylib`` on macOS builds that bundle it."""
    import glob
    out: list[str] = []
    for pat in ("*openblas*.so*", "*openblas*.dll", "*openblas*.dylib"):
        out += glob.glob(os.path.join(d, pat))
    return sorted(set(out))


def blas_ctl():
    """(set_num_threads, get_num_threads) of numpy's bundled OpenBLAS via ctypes, or None (e.g. macOS Accelerate)."""
    if not _BLAS_CTL:
        found = None
        try:
            import ctypes
            d = os.path.join(os.path.dirname(np.__file__), os.pardir, "numpy.libs")
            for path in blas_lib_paths(d):
                lib = ctypes.CDLL(path)
                for sn, gn in (("scipy_openblas_set_num_threads64_", "scipy_openblas_get_num_threads64_"),
                               ("openblas_set_num_threads64_", "openblas_get_num_threads64_"),
                               ("openblas_set_num_threads", "openblas_get_num_threads")):
                    if hasattr(lib, sn) and hasattr(lib, gn):
                        found = (getattr(lib, sn), getattr(lib, gn))
                        break
                if found:
                    break
        except Exception:          # pragma: no cover - platform specific
            found = None
        _BLAS_CTL.append(found)
    return _BLAS_CTL[0]


def set_blas_threads(n: int) -> int | None:
    """Set numpy's OpenBLAS thread count; returns the previous count (None when not controllable)."""
    ctl = blas_ctl()
    if ctl is None:
        return None
    setter, getter = ctl
    try:
        old = int(getter())
        setter(max(1, int(n)))
    except Exception:              # pragma: no cover
        return None
    return old


@contextlib.contextmanager
def single_thread_blas() -> Iterator[None]:
    """Pin OpenBLAS to one thread for the duration (restored afterwards; no-op if not controllable).

    The analysis runs only small and medium products (ZNCC of a few candidates over one box ROI, pixel dot
    products). A multi-threaded OpenBLAS splits a dot product over >= ~20000 elements between its threads, which
    (a) is up to 7x slower for these sizes (thread wake-ups; worse with worker processes or a busy machine:
    zncc_rows 8.4 ms vs 1.2 ms for 7 x 60000, measured) and (b) makes the last bits of every such result depend on
    the machine's CPU count. ``pipeline.run`` and every ``parallel_map`` task therefore run single-threaded."""
    old = set_blas_threads(1)
    try:
        yield
    finally:
        if old is not None:
            set_blas_threads(old)


_STAGE: list[str] = [""]
_STAGE_T0: list[float] = [time.monotonic()]
_LAST_INFO: list[float] = [time.monotonic()]


def current_stage() -> str:
    """Name of the innermost pipeline stage running now ('' outside a stage) -- prefixes progress lines."""
    return _STAGE[0]


def progress_name(name: str) -> str:
    st = current_stage()
    return f"{st}: {name}" if st else name


class _InfoClock(logging.Filter):
    """Remembers when the last INFO-or-higher line of the package logger was emitted (heartbeat input)."""

    def filter(self, record: logging.LogRecord) -> bool:
        if record.levelno >= logging.INFO:
            _LAST_INFO[0] = time.monotonic()
        return True


if not any(isinstance(f, _InfoClock) for f in log.filters):
    log.addFilter(_InfoClock())


_ACTIVE: list["Progress"] = []          # open Progress counters, innermost last (the heartbeat reports it)


class Progress:
    """Counts finished work items. Whenever the package logger has been silent for POOL_WATCHDOG['progress_s']
    seconds, ``<stage>: <name>: done/total <unit> done (elapsed)`` is logged -- by :meth:`tick` or by the stage
    heartbeat, whichever notices first, so a silent period gives one line. Use as a context manager (or call
    :meth:`close`) so the heartbeat stops reporting it."""

    def __init__(self, name: str, total: int, unit: str = "tasks"):
        self.name, self.total, self.unit = name, int(total), unit
        self.done = 0
        self.t0 = time.monotonic()
        _ACTIVE.append(self)

    def __enter__(self) -> "Progress":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    def close(self) -> None:
        for i in range(len(_ACTIVE) - 1, -1, -1):
            if _ACTIVE[i] is self:
                del _ACTIVE[i]
                break

    def line(self) -> str:
        """'<stage>: <name>: done/total <unit> done (<stage> running for <elapsed>)' -- the stage's elapsed time
        (what a user waits for), the counter's own outside a stage."""
        t0 = _STAGE_T0[0] if current_stage() else self.t0
        return (f"{progress_name(self.name)}: {self.done}/{self.total} {self.unit} done "
                f"({elapsed_str(time.monotonic() - t0)})")

    def step(self, n: int = 1) -> None:
        self.done += n
        self.tick()

    def tick(self) -> None:
        if time.monotonic() - _LAST_INFO[0] >= POOL_WATCHDOG["progress_s"]:
            log.info("%s", self.line())


def elapsed_str(s: float) -> str:
    """'42 s' / '3 min 05 s'."""
    m, sec = divmod(int(round(s)), 60)
    return f"{m} min {sec:02d} s" if m else f"{sec} s"


def watched_results(it: Any, total: int, name: str, procs: Iterable[Any] = (), what: str = "worker pool",
                    count: Callable[[Any], int] | None = None, total_items: int | None = None) -> Iterator[Any]:
    """Yield ``total`` results of a multiprocessing ``IMapIterator`` / ``IMapUnorderedIterator`` (``imap`` /
    ``imap_unordered`` with chunksize 1: only those have ``next(timeout)``), logging progress (``count(result)``
    items of ``total_items`` per result; default one task each); raise :class:`PoolFailure` when no result
    arrives for POOL_WATCHDOG['stall_s'] seconds or one of ``procs`` (the pool's worker processes) has exited --
    its task (or the pool's queue lock) may be lost, so the pool could never finish. A task's own exception
    propagates unchanged."""
    from multiprocessing import TimeoutError as MPTimeout
    procs = list(procs)
    got = 0
    last = time.monotonic()
    poll = float(POOL_WATCHDOG["poll_s"])
    with Progress(name, total if total_items is None else total_items) as prog:
        while got < total:
            try:
                r = it.next(timeout=poll)
            except MPTimeout:
                now = time.monotonic()
                dead = [p for p in procs if getattr(p, "exitcode", None) is not None]
                if dead:
                    p = dead[0]
                    raise PoolFailure(f"{what}: worker process {getattr(p, 'pid', '?')} exited unexpectedly "
                                      f"(exit code {p.exitcode}) after {prog.done}/{prog.total} tasks") from None
                if now - last >= POOL_WATCHDOG["stall_s"]:
                    raise PoolFailure(f"{what}: no result for {now - last:.0f} s after {prog.done}/{prog.total} "
                                      "tasks") from None
                prog.tick()
                continue
            except StopIteration:
                return
            got += 1
            last = time.monotonic()
            yield r
            prog.step(count(r) if count is not None else 1)


def pool_workers(pool: Any) -> list:
    """The worker processes of a multiprocessing pool (private ``_pool`` list; empty if unavailable)."""
    return list(getattr(pool, "_pool", None) or [])


def close_pool(pool: Any, kill: bool = False, wait_s: float = 10.0) -> bool:
    """Terminate a multiprocessing pool without blocking the caller for more than about ``wait_s`` seconds.

    ``Pool.terminate()`` itself hangs when a dead worker still holds the pool's queue lock, so it runs in a
    daemon thread; with ``kill`` (a stalled / broken pool) the worker processes are killed right away, and
    always when terminate does not finish in time (the cleanup thread is then abandoned). Returns True when the
    pool shut down cleanly."""
    import threading

    def _kill() -> None:
        for p in pool_workers(pool):
            try:
                if p.exitcode is None:
                    p.kill()
            except Exception:  # noqa: BLE001 - already gone
                pass

    th = threading.Thread(target=_terminate_quietly, args=(pool,), name="match_cuts-pool-close", daemon=True)
    th.start()
    if kill:
        th.join(0.2)               # terminate() first stops the pool from replacing dead workers
        _kill()
    th.join(wait_s)
    if th.is_alive():
        _kill()
        th.join(1.0)
        log.debug("worker pool shutdown did not finish within %.0f s - workers killed, cleanup left to a "
                  "background thread", wait_s)
        return False
    return True


def _terminate_quietly(pool: Any) -> None:
    try:
        pool.terminate()
        pool.join()
    except Exception:  # noqa: BLE001 - best effort
        pass


def _new_lock() -> Any:
    import threading
    return threading.Lock()


# Held by the fork path of visual_match.parallel_map while it forks, and by the heartbeat while it logs: a
# forked child never inherits a half-written log line (stream / handler locks held by another thread). The
# child gets a fresh, unlocked one (it inherits the lock held by the forking thread).
FORK_LOCK = _new_lock()


def _reset_fork_lock() -> None:
    global FORK_LOCK
    FORK_LOCK = _new_lock()


if hasattr(os, "register_at_fork"):
    os.register_at_fork(after_in_child=_reset_fork_lock)


class _Heartbeat:
    """Daemon thread that logs '<stage>: still running (n s)' -- or the open :class:`Progress` counter's line --
    when the package logger was silent for POOL_WATCHDOG['progress_s'] seconds, so a long stage never looks
    frozen (innermost stage only)."""

    def __init__(self, stage: str):
        import threading
        self.stage = stage
        self.t0 = time.monotonic()
        self.stop = threading.Event()
        self.th = threading.Thread(target=self._run, name="match_cuts-heartbeat", daemon=True)

    def _run(self) -> None:
        while True:
            period = float(POOL_WATCHDOG["progress_s"])
            wait = min(period, max(0.5, period - (time.monotonic() - _LAST_INFO[0])))
            if self.stop.wait(wait):
                return
            if time.monotonic() - _LAST_INFO[0] < period or _STAGE[0] != self.stage:
                continue
            with FORK_LOCK:
                if self.stop.is_set():
                    return
                prog = _ACTIVE[-1] if _ACTIVE else None
                if prog is not None:
                    log.info("%s", prog.line())
                else:
                    log.info("%s: still running (%s)", self.stage, elapsed_str(time.monotonic() - self.t0))


@contextlib.contextmanager
def stage_heartbeat(stage: str) -> Iterator[None]:
    """Run the block as pipeline stage ``stage``: progress lines are prefixed with it and a heartbeat thread
    breaks any console silence longer than POOL_WATCHDOG['progress_s']."""
    prev, prev_t0 = _STAGE[0], _STAGE_T0[0]
    _STAGE[0], _STAGE_T0[0] = stage, time.monotonic()
    _LAST_INFO[0] = time.monotonic()
    hb = _Heartbeat(stage)
    hb.th.start()
    try:
        yield
    finally:
        hb.stop.set()
        hb.th.join(5.0)
        _STAGE[0], _STAGE_T0[0] = prev, prev_t0
