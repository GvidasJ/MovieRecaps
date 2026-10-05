"""Stage 3 — analysis proxies and audio (DESIGN.md §5 proxies.py).

* ``build_proxy``: ONE sequential decode (``media.VideoReader``, gray, display orientation) of an
  AE-imported file into a memory-mapped uint8 array ``[rows, h, w]`` in ``WORK_DIR/cache/proxy``,
  cached by file hash + proxy size. Sizes are even and keep the aspect ratio (an exact-aspect size a few
  px below the target is preferred); ``ratio = (w/W, h/H)`` per axis, exactly.
  - dense  (default): row j == frame j; exactly ``info.nb_frames`` rows (asserted).
  - sparse (long RAW: duration > cfg.long_raw_s AND a dense proxy at cfg.min_proxy_width would exceed
    cfg.proxy_budget_bytes): every round(fps / cfg.raw_index_fps_long)-th frame + the requested dense
    ``windows``; ``Proxy.index_map`` maps frame -> row (-1 = absent). Frames live in an append-only
    per-file frame store; a returned Proxy exposes exactly the frames requested so far (determinism).
* ``extend_proxy``: add dense windows to a sparse proxy (second pass); dense proxies are returned as is.
* ``load_audio``: mono float32 at ``sr`` with sample 0 == video t 0 (cached); ``load_audio_full``:
  original rate, all channels.
"""
from __future__ import annotations

import json
import math
import os
from fractions import Fraction
from pathlib import Path
from typing import Iterable, Iterator, Sequence

import numpy as np

from .common import Cache, atomic_write_text, file_hash, log, replace_file, stage_key
from .model import Proxy, StreamInfo
from .probe import load_pts, reader_sar, video_stream_ordinal

# ----------------------------------------------------------------------------------------------
# Sizes
# ----------------------------------------------------------------------------------------------

def even_size(full_w: int, full_h: int, target_w: float, search_px: int = 16) -> tuple[int, int]:
    """Even (w, h) with w <= target_w (and <= full_w) keeping the aspect ratio. Prefers the largest w in
    [target - search_px, target] whose h = w·H/W is an exact even integer (then rx == ry exactly)."""
    W, H = int(full_w), int(full_h)
    t = int(min(float(target_w), W))
    t -= t % 2
    t = max(2, t)
    for w in range(t, max(1, t - search_px) - 1, -2):
        if (w * H) % W == 0 and (w * H // W) % 2 == 0 and w * H // W >= 2:
            return w, w * H // W
    h = max(2, int(2 * round(t * H / W / 2.0)))
    return t, h


def _budget_width(W: int, H: int, rows: int, budget: float) -> float:
    return math.sqrt(max(1.0, float(budget)) * W / (max(1, rows) * H))


def proxy_plan(info: StreamInfo, role: str, cfg) -> dict:
    """Decide dense/sparse and the proxy size (deterministic in info + cfg only)."""
    W, H = int(info.display_width or info.width), int(info.display_height or info.height)
    n = int(info.nb_frames)
    budget = float(getattr(cfg, "proxy_budget_bytes", 3 * 1024 ** 3))
    min_w = min(int(getattr(cfg, "min_proxy_width", 256)), W)
    if role == "competitor":
        tw = min(W * float(getattr(cfg, "comp_proxy_scale", 0.5)), float(getattr(cfg, "comp_proxy_max_width", 640)))
        w, h = even_size(W, H, tw)
        return {"mode": "dense", "size": (w, h), "stride": 1, "W": W, "H": H, "n": n}
    wmin, hmin = even_size(W, H, min_w)
    dense_min_bytes = n * wmin * hmin
    sparse = (float(info.duration) > float(getattr(cfg, "long_raw_s", 2700.0)) and dense_min_bytes > budget)
    if not sparse:
        # the budget may shrink the width down to min_proxy_width, never below; an explicitly smaller
        # raw_proxy_width is honoured
        tw = min(float(getattr(cfg, "raw_proxy_width", 640)), max(_budget_width(W, H, n, budget), float(min_w)))
        w, h = even_size(W, H, tw)
        if n * w * h > budget:
            log.warning("raw proxy %dx%d x %d frames = %.2f GB exceeds proxy_budget_bytes (%.2f GB); "
                        "RAW is not long enough for sparse mode", w, h, n, n * w * h / 1e9, budget / 1e9)
        return {"mode": "dense", "size": (w, h), "stride": 1, "W": W, "H": H, "n": n}
    stride = max(1, int(round(float(info.fps) / float(getattr(cfg, "raw_index_fps_long", 3.0)))))
    rows_est = int(math.ceil(n / stride) * 1.5)          # index frames + 50 % reserve for windows
    tw = min(float(getattr(cfg, "raw_proxy_width", 640)), max(_budget_width(W, H, rows_est, budget), float(min_w)))
    w, h = even_size(W, H, tw)
    return {"mode": "sparse", "size": (w, h), "stride": stride, "W": W, "H": H, "n": n}


# ----------------------------------------------------------------------------------------------
# Decoding helpers
# ----------------------------------------------------------------------------------------------

def _reader(info_path: str, fps: Fraction, vindex: int, rotation: int, sar: Fraction):
    from .media import VideoReader
    return VideoReader(info_path, fps=fps, stream_index=vindex, rotation=rotation, sar=sar)


def _seek_to(rd, index: int) -> None:
    """Seek to the keyframe at or before frame ``index`` (same target rule as VideoReader)."""
    stream = rd.stream
    if index <= 0:
        rd.container.seek(rd.origin_pts if rd.origin_pts else 0, stream=stream, backward=True, any_frame=False)
        return
    t = Fraction(index, 1) / rd.fps - Fraction(1, 2) / rd.fps
    target = rd.origin_pts + int(math.floor(t / rd.time_base))
    rd.container.seek(max(target, 0), stream=stream, backward=True, any_frame=False)


def iter_selected_frames(rd, wanted: Sequence[int], size: tuple[int, int],
                         gap_frames: int | None = None) -> Iterator[tuple[int, np.ndarray]]:
    """Yield (index, gray image at ``size``) for every frame in ``wanted`` (sorted, unique), decoding
    sequentially per cluster of nearby frames and converting ONLY the wanted ones (same conversion as
    ``VideoReader.frames(fmt='gray', size=size)``). Raises if a wanted frame is not found."""
    want = sorted(set(int(j) for j in wanted))
    if not want:
        return
    gap = int(gap_frames if gap_frames is not None else max(64, int(float(rd.fps) * 4)))
    clusters: list[list[int]] = [[want[0]]]
    for j in want[1:]:
        if j - clusters[-1][-1] > gap:
            clusters.append([j])
        else:
            clusters[-1].append(j)
    for cl in clusters:
        wset = set(cl)
        lo, hi = cl[0], cl[-1]
        found: set[int] = set()
        for attempt in (lo, lo - int(float(rd.fps) * 10), 0):
            _seek_to(rd, max(0, attempt))
            first = True
            overshoot = False
            for frame in rd.container.decode(rd.stream):
                p = frame.pts if frame.pts is not None else getattr(frame, "dts", None)
                if p is None:
                    continue
                idx = rd.index_of_pts(int(p))
                if first:
                    first = False
                    if idx > lo and attempt > 0:
                        overshoot = True
                        break
                if idx in wset and idx not in found:
                    found.add(idx)
                    yield idx, rd._convert(frame, "gray", size, None)
                if idx >= hi:
                    break
            if not overshoot:
                break
        if len(found) != len(cl):
            missing = sorted(wset - found)
            raise RuntimeError(f"{rd.path}: frames {missing[:10]} (of {len(missing)}) not found while decoding")


# ----------------------------------------------------------------------------------------------
# Dense proxies
# ----------------------------------------------------------------------------------------------

def _build_dense(info: StreamInfo, size: tuple[int, int], path: Path) -> None:
    from numpy.lib.format import open_memmap
    w, h = size
    n = int(info.nb_frames)
    tmp = path.with_name(path.name[:-4] + ".tmp.npy")
    mm = open_memmap(tmp, mode="w+", dtype=np.uint8, shape=(n, h, w))
    count = 0
    try:
        with _reader(info.path, info.fps, video_stream_ordinal(info), info.rotation, reader_sar(info)) as rd:
            for idx, img in rd.frames(0, None, fmt="gray", size=(w, h)):
                if idx != count:
                    raise RuntimeError(f"proxy decode of {info.path}: frame index {idx} where {count} was expected "
                                       "(gap/duplicate PTS: the file is not CFR - conform it first)")
                if count >= n:
                    raise RuntimeError(f"proxy decode of {info.path}: more than nb_frames={n} frames")
                mm[count] = img
                count += 1
        if count != n:
            raise RuntimeError(f"proxy decode of {info.path}: decoded {count} frames, probe said {n}")
        mm.flush()
    except BaseException:
        del mm
        if tmp.exists():
            tmp.unlink()
        raise
    del mm
    replace_file(tmp, path)


# ----------------------------------------------------------------------------------------------
# Sparse frame store (long RAW)
# ----------------------------------------------------------------------------------------------

class _FrameStore:
    """Append-only store of proxy rows for one file + size: ``<key>.u8`` (rows) + ``<key>.frames.npy``
    (frame index of each row, row order) + ``<key>.store.json`` (decode parameters)."""

    def __init__(self, cache: Cache, key: str):
        self.data = cache.path("proxy", key, ".u8")
        self.meta = cache.path("proxy", key, ".frames.npy")
        self.params = cache.path("proxy", key, ".store.json")

    def stored(self) -> np.ndarray:
        if self.meta.exists():
            return np.load(self.meta).astype(np.int64)
        return np.zeros(0, np.int64)

    def ensure(self, info_like: dict, frames: np.ndarray) -> np.ndarray:
        """Decode and append every frame of ``frames`` not yet stored. Returns the row-order frame array."""
        w, h = info_like["size"]
        fs = w * h
        have = self.stored()
        if len(have) and (not self.data.exists() or self.data.stat().st_size < len(have) * fs):
            log.warning("sparse proxy store %s is truncated; rebuilding it", self.data)
            have = np.zeros(0, np.int64)
            if self.data.exists():
                self.data.unlink()
        missing = np.setdiff1d(np.unique(frames.astype(np.int64)), have)
        if len(missing) == 0:
            return have
        rows = len(have)
        new_ids: list[int] = []
        with open(self.data, "r+b" if self.data.exists() else "w+b") as f:
            f.seek(rows * fs)
            with _reader(info_like["path"], Fraction(info_like["fps"]), info_like["vindex"], info_like["rotation"],
                         Fraction(info_like["sar"])) as rd:
                for idx, img in iter_selected_frames(rd, missing.tolist(), (w, h)):
                    f.write(np.ascontiguousarray(img, dtype=np.uint8).tobytes())
                    new_ids.append(idx)
            f.flush()
            os.fsync(f.fileno())
        allf = np.concatenate([have, np.asarray(new_ids, np.int64)])
        tmp = self.meta.with_name(self.meta.name[:-4] + ".tmp.npy")
        np.save(tmp, allf)
        replace_file(tmp, self.meta)
        log.info("sparse proxy %s: +%d frames (%d stored)", self.data.name, len(new_ids), len(allf))
        return allf

    def memmap(self, shape_hw: tuple[int, int], rows: int) -> np.memmap:
        h, w = shape_hw
        return np.memmap(self.data, dtype=np.uint8, mode="r", shape=(max(rows, 1), h, w)) if rows else \
            np.zeros((0, h, w), np.uint8)


def _windows_frames(windows: Iterable[Sequence[int]] | None, n: int) -> np.ndarray:
    parts = []
    for win in windows or []:
        j0, j1 = int(win[0]), int(win[1])
        j0, j1 = max(0, j0), min(n, j1)
        if j1 > j0:
            parts.append(np.arange(j0, j1, dtype=np.int64))
    return np.unique(np.concatenate(parts)) if parts else np.zeros(0, np.int64)


def _sparse_proxy(store: _FrameStore, info_like: dict, requested: np.ndarray, role: str, pts: np.ndarray) -> Proxy:
    allf = store.ensure(info_like, requested)
    w, h = info_like["size"]
    n = int(info_like["n"])
    row_of = np.full(n, -1, np.int32)
    row_of[allf] = np.arange(len(allf), dtype=np.int32)
    index_map = np.full(n, -1, np.int32)
    req = np.unique(requested.astype(np.int64))
    index_map[req] = row_of[req]
    if np.any(index_map[req] < 0):  # pragma: no cover - ensure() guarantees presence
        raise RuntimeError("sparse proxy: requested frames missing from the store")
    frames = store.memmap((h, w), len(allf))
    W, H = int(info_like["W"]), int(info_like["H"])
    p = Proxy(role=role, path=info_like["path"], frames=frames, full_size=(W, H), ratio=(w / W, h / H),
              fps=Fraction(info_like["fps"]), pts=pts, n=n, npy_path=str(store.data), index_map=index_map)
    return p


# ----------------------------------------------------------------------------------------------
# Public API
# ----------------------------------------------------------------------------------------------

def build_proxy(info: StreamInfo, role: str, cfg, cache: Cache, windows=None) -> Proxy:
    """Memory-mapped gray proxy of an AE-imported file (DESIGN §5 proxies.py). ``windows`` =
    [(j0, j1), ...] half-open RAW frame ranges kept dense in sparse (long-RAW) mode; ignored when dense."""
    plan = proxy_plan(info, role, cfg)
    w, h = plan["size"]
    fh = info.file_hash or file_hash(info.path)
    pts = load_pts(info)
    if len(pts) != int(info.nb_frames):
        raise RuntimeError(f"{info.path}: PTS array has {len(pts)} entries but nb_frames={info.nb_frames}")
    W, H = plan["W"], plan["H"]
    if plan["mode"] == "dense":
        key = stage_key("proxy", fh, "dense", w, h, int(info.nb_frames))
        path = cache.path("proxy", key, ".npy")
        if path.exists():
            frames = np.load(path, mmap_mode="r")
            if frames.shape != (int(info.nb_frames), h, w):
                log.warning("cached proxy %s has shape %s, rebuilding", path, frames.shape)
                del frames
                path.unlink()
        if not path.exists():
            import time
            t0 = time.perf_counter()
            _build_dense(info, (w, h), path)
            dt = time.perf_counter() - t0
            log.info("proxy %s [%s]: %d frames %dx%d in %.1fs (%.0f fps)", Path(info.path).name, role,
                     info.nb_frames, w, h, dt, info.nb_frames / max(dt, 1e-9))
        frames = np.load(path, mmap_mode="r")
        return Proxy(role=role, path=info.path, frames=frames, full_size=(W, H), ratio=(w / W, h / H),
                     fps=Fraction(info.fps), pts=pts, n=int(info.nb_frames), npy_path=str(path), index_map=None)

    # sparse (long RAW)
    key = stage_key("proxy", fh, "sparse", w, h, int(info.nb_frames))
    store = _FrameStore(cache, key)
    info_like = {"path": info.path, "fps": str(Fraction(info.fps)), "vindex": video_stream_ordinal(info),
                 "rotation": int(info.rotation), "sar": str(reader_sar(info)), "size": [w, h], "n": int(info.nb_frames),
                 "W": W, "H": H, "stride": int(plan["stride"]), "file_hash": fh, "role": role}
    atomic_write_text(store.params, json.dumps(info_like, indent=1, sort_keys=True))
    index_frames = np.arange(0, int(info.nb_frames), int(plan["stride"]), dtype=np.int64)
    requested = np.union1d(index_frames, _windows_frames(windows, int(info.nb_frames)))
    log.info("proxy %s [%s]: SPARSE mode (duration %.0fs > %.0fs and dense > budget): stride %d, %d frames, %dx%d",
             Path(info.path).name, role, info.duration, float(getattr(cfg, "long_raw_s", 2700.0)), plan["stride"],
             len(requested), w, h)
    return _sparse_proxy(store, info_like, requested, role, pts)


def extend_proxy(proxy: Proxy, windows, cfg, cache: Cache) -> Proxy:
    """Sparse proxies: a new Proxy that also holds every frame of ``windows`` [(j0, j1), ...] (decoded and
    appended to the frame store). Dense proxies already hold every frame and are returned unchanged."""
    if proxy.index_map is None:
        return proxy
    data = Path(proxy.npy_path)
    params = data.with_name(data.name[:-len(".u8")] + ".store.json")
    if not params.exists():
        raise RuntimeError(f"extend_proxy: store parameters {params} missing")
    info_like = json.loads(params.read_text(encoding="utf-8"))
    key = data.name[:-len(".u8")]
    store = _FrameStore(cache, key)
    if store.data != data:           # cache root moved: use the proxy's own files
        store.data, store.meta, store.params = data, data.with_name(key + ".frames.npy"), params
    current = np.flatnonzero(np.asarray(proxy.index_map) >= 0).astype(np.int64)
    requested = np.union1d(current, _windows_frames(windows, int(proxy.n)))
    return _sparse_proxy(store, info_like, requested, proxy.role, proxy.pts)


def load_audio(info: StreamInfo, sr: int, cache: Cache) -> np.ndarray:
    """Mono float32 audio at ``sr`` Hz whose sample 0 is video t = 0 (probe's av_offset applied: late
    audio is zero-padded, early audio trimmed). Empty array when the file has no audio. Cached."""
    if not info.has_audio:
        return np.zeros(0, np.float32)
    from .media import extract_audio
    fh = info.file_hash or file_hash(info.path)
    key = stage_key("audio", fh, int(sr), round(float(info.av_offset), 9), "mono")
    p = cache.path("audio", key, ".npy")
    if p.exists():
        return np.load(p)
    y = extract_audio(info.path, sr=int(sr), mono=True, offset_s=float(info.av_offset))
    y = np.ascontiguousarray(y, dtype=np.float32)
    tmp = p.with_name(p.name[:-4] + ".tmp.npy")
    np.save(tmp, y)
    replace_file(tmp, p)
    return y


def load_audio_full(info: StreamInfo) -> tuple[np.ndarray, int]:
    """(samples [N, C] float32, sample rate) at the ORIGINAL rate, all channels, sample 0 = video t 0.
    ((0, C) array when the file has no audio.)"""
    sr = int(info.a_sample_rate or 48000)
    if not info.has_audio:
        return np.zeros((0, max(1, int(info.a_channels or 1))), np.float32), sr
    from .media import extract_audio
    y = extract_audio(info.path, sr=sr, mono=False, offset_s=float(info.av_offset))
    if y.ndim == 1:
        y = y[:, None]
    return np.ascontiguousarray(y, dtype=np.float32), sr
