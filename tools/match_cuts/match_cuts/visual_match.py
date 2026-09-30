"""Stage 5.2 -- visual candidate search (DESIGN.md §5 visual_match.py).

A competitor frame is matched to RAW with keypoints, never with shot detection:

* :class:`RawIndex` holds SIFT descriptors of sampled RAW proxy frames (uint8 storage -- SIFT values
  are integers <= 255, so this is lossless -- cast to float32 for a FLANN kd-tree). Voting is
  cluster-aware (:meth:`RawIndex.query`): a plain Lowe ratio across index frames is never used,
  because adjacent index frames hold near-duplicate descriptors.
* :func:`search_frame` verifies the voted candidates of ONE competitor frame: pairwise Lowe ratio 0.75
  against a single RAW frame, ``cv2.estimateAffinePartial2D`` RANSAC estimated RAW -> comp (§2.2),
  the flip hypothesis against the SIFT features of ``cv2.flip(raw, 1)`` (the result of
  ``from_cv_matrix(M, flip=False)`` IS the canonical Sim for ``flip_h=True``), and masked ZNCC of the
  warped candidate (``scoring``). Accepted matches are re-estimated against the best EXACT RAW frame
  near the index frame (ECC via :func:`refine.refine_transform`) before they become :class:`Anchor`s.
  SIFT features of a mirrored image are an exact permutation of the originals (precise-upscale SIFT,
  :func:`mirror_features`), so flip votes and flipped verification need no second SIFT pass.
* :func:`sparse_search` runs :func:`search_frame` every ``cfg.comp_search_stride`` competitor frames
  (audio-restricted first, global fallback) in a fork pool.

Shared helpers used by ``refine`` live here too: :class:`AllowedMasks` (box & ~static & ~overlay
masks), :func:`parallel_map` (deterministic fork pool), :func:`proxy_id` (cache identities).
"""
from __future__ import annotations

import hashlib
import math
import multiprocessing as mp
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable, Sequence

import numpy as np

from . import scoring
from .common import (Cache, DecisionLog, file_hash, fps_str, log, null_dlog, params_hash, seed_everything,
                     stage_key)
from .geometry import Sim, from_cv_matrix
from .model import AudioHints, Layout, Proxy

__all__ = ["RawIndex", "Anchor", "search_frame", "sparse_search", "run_searches", "AllowedMasks", "parallel_map",
           "proxy_id", "box_roi", "audio_window", "detect_sift", "mirror_features"]


# ---------------------------------------------------------------------------------------------
# Deterministic fork pool
# ---------------------------------------------------------------------------------------------

_WSTATE: dict[str, Any] = {}


def _invoke(item: Any) -> Any:
    """Pool entry point: runs ``state['__fn__'](state, item)`` with a per-item RNG seed."""
    st = _WSTATE
    seed_everything(st["__seed__"])
    return st["__fn__"](st, item)


def _fork_available() -> bool:
    return "fork" in mp.get_all_start_methods()


def parallel_map(fn: Callable[[dict, Any], Any], items: Sequence[Any], workers: int, state: dict,
                 seed: int, chunksize: int | None = None, min_items: int = 8) -> list[Any]:
    """Apply ``fn(state, item)`` to every item; results in INPUT order (deterministic).

    Workers are forked (big read-only objects in ``state`` -- memmapped proxies, the FLANN index --
    are inherited copy-on-write, never pickled; only items and results are pickled). OpenCV's thread
    pool is set to 1 thread in the parent before forking and restored afterwards: calling
    ``cv2.setNumThreads`` inside a forked child deadlocks with the pthreads backend (verified), and one
    OpenCV thread per worker avoids oversubscription. ``common.seed_everything(seed)`` runs before
    every item, so results do not depend on which worker processed which item.
    """
    global _WSTATE
    items = list(items)
    prev_state = _WSTATE
    _WSTATE = dict(state)
    _WSTATE["__fn__"] = fn
    _WSTATE["__seed__"] = int(seed)
    try:
        if workers <= 1 or len(items) < max(2, min_items) or not _fork_available():
            return [_invoke(it) for it in items]
        import cv2
        prev_threads = cv2.getNumThreads()
        cv2.setNumThreads(1)
        try:
            ctx = mp.get_context("fork")
            n = min(int(workers), len(items))
            cs = chunksize or max(1, min(16, len(items) // (n * 6) or 1))
            with ctx.Pool(n) as pool:
                return pool.map(_invoke, items, chunksize=cs)
        finally:
            cv2.setNumThreads(prev_threads)
    finally:
        _WSTATE = prev_state


# ---------------------------------------------------------------------------------------------
# Identities for cache keys
# ---------------------------------------------------------------------------------------------

def _hash_array(a: np.ndarray | None) -> str:
    if a is None:
        return "none"
    h = hashlib.blake2b(digest_size=12)
    arr = np.asarray(a)
    h.update(str(arr.dtype).encode())
    h.update(str(arr.shape).encode())
    if arr.ndim >= 1 and arr.size > (1 << 22):
        for i in range(arr.shape[0]):           # stream big (memmapped) arrays row by row
            h.update(np.ascontiguousarray(arr[i]).tobytes())
    else:
        h.update(np.ascontiguousarray(arr).tobytes())
    return h.hexdigest()


def proxy_id(p: Proxy) -> str:
    """Stable identity of a proxy for cache keys: media file hash (or pixel hash for in-memory
    proxies) + geometry + fps + sparse index map."""
    parts: list[Any] = [p.role, int(p.n), list(p.size), list(p.full_size), [float(r) for r in p.ratio],
                        fps_str(p.fps)]
    path = Path(p.path) if p.path else None
    if path is not None and path.is_file():
        parts.append(file_hash(path))
    else:
        parts.append(_hash_array(p.frames))
    parts.append(_hash_array(p.index_map) if p.index_map is not None else "dense")
    return params_hash(*parts)


def layout_id(layout: Layout | None) -> str:
    if layout is None:
        return "none"
    d = layout.to_dict()
    extra = []
    for f in (layout.static_mask_file, layout.overlay_mask_file):
        extra.append(file_hash(f) if f and Path(f).is_file() else "")
    d.pop("static_mask_file", None)
    d.pop("overlay_mask_file", None)
    return params_hash(d, extra)


def overlays_id(overlays: Any) -> str:
    if overlays is None:
        return "none"
    h = hashlib.blake2b(digest_size=12)
    try:
        frames = sorted(int(k) for k in overlays.frames())
    except Exception:  # pragma: no cover - unknown overlay container
        return params_hash(repr(type(overlays)))
    for k in frames:
        m = overlays.get(k)
        if m is None:
            continue
        h.update(str(k).encode())
        h.update(np.packbits(np.asarray(m, bool)).tobytes())
    return h.hexdigest()


def hints_id(hints: AudioHints | None) -> str:
    if hints is None:
        return "none"
    return params_hash([_hash_array(np.asarray(getattr(hints, f))) for f in
                        ("comp_t", "raw_t", "speed", "conf", "psr", "peak")], hints.window, hints.hop)


# ---------------------------------------------------------------------------------------------
# Masks and ROI
# ---------------------------------------------------------------------------------------------

def box_roi(layout: Layout | None, comp: Proxy) -> tuple[int, int, int, int]:
    """Integer (x, y, w, h) ROI of the video box at competitor PROXY resolution (whole frame if no box)."""
    w, h = comp.size
    if layout is None or layout.box is None:
        return (0, 0, int(w), int(h))
    b = layout.box.scaled(float(comp.ratio[0]), float(comp.ratio[1]))
    x0, y0 = max(0, int(math.floor(b.x))), max(0, int(math.floor(b.y)))
    x1, y1 = min(int(w), int(math.ceil(b.x + b.w))), min(int(h), int(math.ceil(b.y + b.h)))
    if x1 <= x0 or y1 <= y0:
        return (0, 0, int(w), int(h))
    return (x0, y0, x1 - x0, y1 - y0)


def _box_coverage_local(layout: Layout | None, comp: Proxy, ss: int = 4) -> np.ndarray:
    """Rounded-box coverage [h, w] at proxy res (CORNER convention, ss x ss super-sampling)."""
    w, h = comp.size
    if layout is None or layout.box is None:
        return np.ones((int(h), int(w)), np.float32)
    rx, ry = float(comp.ratio[0]), float(comp.ratio[1])
    b = layout.box
    x0, y0, x1, y1 = b.x * rx, b.y * ry, (b.x + b.w) * rx, (b.y + b.h) * ry
    r = max(0.0, min(b.corner_radius * (rx + ry) / 2.0, (x1 - x0) / 2.0, (y1 - y0) / 2.0))
    offs = (np.arange(ss) + 0.5) / ss
    xs = (np.arange(int(w))[:, None] + offs[None, :]).reshape(-1)
    ys = (np.arange(int(h))[:, None] + offs[None, :]).reshape(-1)
    inx = (xs >= x0) & (xs <= x1)
    iny = (ys >= y0) & (ys <= y1)
    cx = np.clip(xs, x0 + r, x1 - r)
    cy = np.clip(ys, y0 + r, y1 - r)
    dx = (xs - cx)[None, :]
    dy = (ys - cy)[:, None]
    inside = iny[:, None] & inx[None, :]
    if r > 0:
        inside &= (dx * dx + dy * dy) <= r * r
    return inside.reshape(int(h), ss, int(w), ss).mean(axis=(1, 3)).astype(np.float32)


class AllowedMasks:
    """Per-frame bool masks [h, w] at comp proxy res of pixels usable for keypoints and scores:
    box coverage >= 0.99 AND NOT static AND NOT dilated overlay(k) AND NOT extra(k).

    Uses ``layout.allowed_mask`` when the layout module is importable (``use_layout_module=True``),
    else the identical local definition (DESIGN §5 layout.allowed_mask). ``extra`` holds masks added by
    the overlay pass 2 of refine (True = excluded). Masks are recomputed on every call (no memo): the
    overlay container may be updated in place (refine's pass 2 ``union``), and a stale memo would make
    results depend on which process computed a frame first (inline vs forked workers).
    """

    def __init__(self, layout: Layout | None, overlays: Any, comp: Proxy, cfg, use_layout_module: bool = True,
                 base_fn: Callable[[int], np.ndarray] | None = None):
        import cv2
        self.layout, self.overlays, self.comp, self.cfg = layout, overlays, comp, cfg
        self.extra: dict[int, np.ndarray] = {}
        self._base_fn = base_fn
        self._layout_fn = None
        if base_fn is None and use_layout_module and layout is not None:
            try:
                from .layout import allowed_mask as _am  # lazy: written by another agent
                self._layout_fn = _am
            except ImportError:
                self._layout_fn = None
        cov = _box_coverage_local(layout, comp)
        base = cov >= 0.99
        if layout is not None and layout.static_mask_file and Path(layout.static_mask_file).is_file():
            st = np.load(layout.static_mask_file).astype(bool)
            if st.shape == base.shape:
                base &= ~st
            else:
                log.warning("static mask %s has shape %s != proxy %s - ignored", layout.static_mask_file,
                            st.shape, base.shape)
        self.base = base
        d = int(getattr(cfg, "overlay_dilate_px", 3))
        self._kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * d + 1, 2 * d + 1)) if d > 0 else None

    def _compute(self, k: int) -> np.ndarray:
        import cv2
        if self._base_fn is not None:
            m = np.asarray(self._base_fn(k), bool).copy()
        elif self._layout_fn is not None:
            m = np.asarray(self._layout_fn(self.layout, self.overlays, k, self.comp), bool).copy()
        else:
            m = self.base.copy()
            ov = self.overlays.get(k) if self.overlays is not None else None
            if ov is not None and np.any(ov):
                ov = np.asarray(ov, np.uint8)
                if self._kernel is not None:
                    ov = cv2.dilate(ov, self._kernel)
                m &= ov == 0
        ex = self.extra.get(int(k))
        if ex is not None:
            m &= ~ex
        return m

    def __call__(self, k: int) -> np.ndarray:
        return self._compute(int(k))

    def add_extra(self, k: int, mask: np.ndarray) -> None:
        """Exclude more pixels at frame k (overlay pass 2)."""
        k = int(k)
        mask = np.asarray(mask, bool)
        self.extra[k] = mask if k not in self.extra else (self.extra[k] | mask)


def mask_bbox(mask: np.ndarray | None, shape: tuple[int, int]) -> tuple[int, int, int, int]:
    """Bounding box (x, y, w, h) of True pixels (whole image if None/empty)."""
    h, w = shape[:2]
    if mask is None:
        return (0, 0, int(w), int(h))
    rows = np.flatnonzero(mask.any(axis=1))
    cols = np.flatnonzero(mask.any(axis=0))
    if rows.size == 0 or cols.size == 0:
        return (0, 0, int(w), int(h))
    return (int(cols[0]), int(rows[0]), int(cols[-1] - cols[0] + 1), int(rows[-1] - rows[0] + 1))


# ---------------------------------------------------------------------------------------------
# SIFT
# ---------------------------------------------------------------------------------------------

_SIFT: dict[int, Any] = {}


def _sift(nfeatures: int):
    """cv2.SIFT with ``enable_precise_upscale=True``: the default 2x upscale shifts every keypoint by
    0.25 px (measured: SIFT of the mirrored image is then offset by 0.5 px); the precise variant is
    unbiased, exactly mirror-symmetric (see mirror_features) and equally fast."""
    import cv2
    s = _SIFT.get(int(nfeatures))
    if s is None:
        try:
            s = cv2.SIFT_create(int(nfeatures), enable_precise_upscale=True)
        except TypeError:  # pragma: no cover - OpenCV < 4.8
            s = cv2.SIFT_create(int(nfeatures))
        _SIFT[int(nfeatures)] = s
    return s


def detect_sift(img: np.ndarray, mask: np.ndarray | None, nfeatures: int,
                roi: tuple[int, int, int, int] | None = None) -> tuple[np.ndarray, np.ndarray]:
    """SIFT on ``img`` (uint8 gray), restricted to ``mask`` (bool/uint8, full image) and, for speed, to
    the crop ``roi`` (x, y, w, h). Returns (pts float32 [n, 2] in OpenCV pixel-centre coordinates of the
    FULL image, desc uint8 [n, 128])."""
    x0, y0 = 0, 0
    sub, msub = img, mask
    if roi is not None:
        x0, y0, w, h = roi
        sub = img[y0:y0 + h, x0:x0 + w]
        msub = mask[y0:y0 + h, x0:x0 + w] if mask is not None else None
    m8 = None if msub is None else (np.asarray(msub, bool).astype(np.uint8) * 255)
    kps, desc = _sift(nfeatures).detectAndCompute(np.ascontiguousarray(sub), m8)
    if desc is None or len(kps) == 0:
        return np.zeros((0, 2), np.float32), np.zeros((0, 128), np.uint8)
    pts = np.array([kp.pt for kp in kps], np.float32) + np.array([x0, y0], np.float32)
    return pts, np.clip(np.rint(desc), 0, 255).astype(np.uint8)


# SIFT descriptors of a horizontally mirrored image are an exact permutation of the originals
# (OpenCV layout 4 x 4 cells x 8 orientation bins): the cell rows reverse and orientation bin o -> -o
# (verified: median relative difference 0.0 against SIFT run on cv2.flip(img, 1), keypoints mirrored).
_MIRROR_IDX = np.arange(128).reshape(4, 4, 8)[::-1][:, :, (-np.arange(8)) % 8].reshape(-1)


def mirror_features(pts: np.ndarray, desc: np.ndarray, width: int) -> tuple[np.ndarray, np.ndarray]:
    """SIFT features of ``cv2.flip(img, 1)`` from the features of ``img`` (image ``width`` px): keypoint
    x -> width - 1 - x (OpenCV pixel-centre coordinates), descriptors permuted (``_MIRROR_IDX``)."""
    p = np.array(pts, np.float32, copy=True)
    if len(p):
        p[:, 0] = (width - 1) - p[:, 0]
    return p, np.ascontiguousarray(desc[:, _MIRROR_IDX])


# ---------------------------------------------------------------------------------------------
# RAW index
# ---------------------------------------------------------------------------------------------

def _index_worker(state: dict, frames: list[int]) -> list[tuple[int, np.ndarray, np.ndarray]]:
    raw: Proxy = state["raw"]
    out = []
    for j in frames:
        pts, desc = detect_sift(np.asarray(raw.get(j)), None, state["nfeat"])
        out.append((int(j), pts, desc))
    return out


class RawIndex:
    """SIFT descriptors of sampled RAW proxy frames with cluster-aware voting (DESIGN §5)."""

    def __init__(self, frames: np.ndarray, desc: np.ndarray, owner: np.ndarray, pts: np.ndarray,
                 offsets: np.ndarray, raw_fps, step: int, cfg, key: str = ""):
        self.frames = np.asarray(frames, np.int32)          # sampled RAW frame indices (sorted)
        self.desc = np.asarray(desc, np.uint8)              # [N, 128] uint8 (lossless SIFT values)
        self.owner = np.asarray(owner, np.int32)            # [N] RAW frame of each descriptor
        self.pts = np.asarray(pts, np.float32)              # [N, 2] OpenCV coords at RAW proxy res
        self.offsets = np.asarray(offsets, np.int64)        # [F + 1] descriptor ranges per sampled frame
        self.fps = raw_fps
        self.step = int(step)
        self.key = key
        self.knn = int(getattr(cfg, "index_knn", 24))
        self.ratio = float(getattr(cfg, "index_ratio", 0.8))
        self.far = float(getattr(cfg, "index_far_s", 2.0)) * float(raw_fps)
        self.seed = int(getattr(cfg, "seed", 12345))
        self._flann = None
        self._data32: np.ndarray | None = None

    # -- construction ---------------------------------------------------------------------------
    @staticmethod
    def build(raw: Proxy, cfg, cache: Cache | None) -> "RawIndex":
        """SIFT every round(raw_fps / index_fps) RAW proxy frames (index_fps = raw_index_fps_short for
        RAW <= 10 min, else raw_index_fps_long); nfeatures per frame lowered so the total stays below
        cfg.index_max_descriptors. Cached (stage 'raw_index', npz) by proxy identity + parameters."""
        fps = float(raw.fps)
        duration = raw.n / fps if fps > 0 else 0.0
        index_fps = cfg.raw_index_fps_short if duration <= 600.0 else cfg.raw_index_fps_long
        step = max(1, int(round(fps / float(index_fps))))
        frames = [j for j in range(0, raw.n, step) if raw.has(j)]
        if not frames:
            raise ValueError("RawIndex.build: the RAW proxy holds no index frames")
        nfeat = int(min(cfg.sift_nfeatures, max(32, cfg.index_max_descriptors // max(1, len(frames)))))
        key = stage_key("raw_index", proxy_id(raw), cfg.analysis_params(), step, nfeat, "sift_precise_upscale")

        def compute() -> dict[str, np.ndarray]:
            workers = cfg.resolved_workers()
            chunks = [frames[i:i + 8] for i in range(0, len(frames), 8)]
            res = parallel_map(_index_worker, chunks, workers, {"raw": raw, "nfeat": nfeat}, cfg.seed,
                               chunksize=1, min_items=2)
            flat = [r for chunk in res for r in chunk]
            descs = [d for _, _, d in flat]
            ptss = [p for _, p, _ in flat]
            counts = np.array([len(d) for d in descs], np.int64)
            owner = np.repeat(np.array([j for j, _, _ in flat], np.int32), counts)
            offsets = np.concatenate([[0], np.cumsum(counts)]).astype(np.int64)
            return {
                "frames": np.array([j for j, _, _ in flat], np.int32),
                "desc": np.concatenate(descs).astype(np.uint8) if descs else np.zeros((0, 128), np.uint8),
                "pts": np.concatenate(ptss).astype(np.float32) if ptss else np.zeros((0, 2), np.float32),
                "owner": owner, "offsets": offsets,
                "fps": np.array([raw.fps.numerator, raw.fps.denominator], np.int64),
                "step": np.array(step), "nfeat": np.array(nfeat),
            }

        data = cache.npz("raw_index", key, compute) if cache is not None else compute()
        idx = RawIndex(data["frames"], data["desc"], data["owner"], data["pts"], data["offsets"], raw.fps,
                       int(data["step"]), cfg, key)
        log.info("RAW index: %d frames (step %d), %d descriptors (%.1f MB uint8)", len(idx.frames), idx.step,
                 len(idx.desc), idx.desc.nbytes / 1e6)
        return idx

    # -- FLANN ----------------------------------------------------------------------------------
    def ensure_built(self) -> None:
        """Train the FLANN kd-tree (seeded, deterministic). Call before forking workers."""
        if self._flann is not None:
            return
        import cv2
        self._data32 = np.ascontiguousarray(self.desc, dtype=np.float32)   # kept alive: FLANN references it
        seed_everything(self.seed)
        self._flann = cv2.flann_Index(self._data32, dict(algorithm=1, trees=4))

    def frame_features(self, j: int) -> tuple[np.ndarray, np.ndarray] | None:
        """(pts, desc) stored for index frame j, or None if j is not an index frame."""
        i = int(np.searchsorted(self.frames, j))
        if i >= len(self.frames) or int(self.frames[i]) != int(j):
            return None
        a, b = int(self.offsets[i]), int(self.offsets[i + 1])
        return self.pts[a:b], self.desc[a:b]

    # -- voting ---------------------------------------------------------------------------------
    def votes(self, desc: np.ndarray, window: tuple[int, int] | None = None) -> np.ndarray:
        """Cluster-aware vote vector over index frames (smoothed over +-1 index frame).

        For each query descriptor: k = index_knn nearest neighbours; f0 = frame of the first one; the
        ratio denominator is the first neighbour whose frame is > index_far_s away from f0 (NOT the
        second neighbour, which is usually a near-duplicate from an adjacent index frame). If
        d1 < index_ratio * d_far (or no far neighbour exists), every neighbour within index_far_s of f0
        with distance <= 1.1 d1 votes with weight 1 / cluster size."""
        F = len(self.frames)
        v = np.zeros(F, np.float64)
        if desc is None or len(desc) == 0 or len(self.desc) == 0:
            return v
        self.ensure_built()
        k = int(min(self.knn, len(self.desc)))
        q = np.ascontiguousarray(desc, dtype=np.float32)
        ind, d2 = self._flann.knnSearch(q, k, params=dict(checks=64))
        ind = np.asarray(ind, np.int64).reshape(len(q), k)
        dist = np.sqrt(np.maximum(np.asarray(d2, np.float64).reshape(len(q), k), 0.0))
        valid = ind >= 0
        own = self.owner[np.clip(ind, 0, None)].astype(np.int64)
        f0 = own[:, :1]
        near = np.abs(own - f0) <= self.far
        farm = (~near) & valid
        has_far = farm.any(axis=1)
        first_far = np.argmax(farm, axis=1)
        d_far = dist[np.arange(len(q)), first_far]
        passed = valid[:, 0] & (~has_far | (dist[:, 0] < self.ratio * d_far))
        cluster = near & valid & (dist <= 1.1 * dist[:, :1] + 1e-6) & passed[:, None]
        sizes = cluster.sum(axis=1)
        w = np.where(sizes > 0, 1.0 / np.maximum(sizes, 1), 0.0)
        pos = np.searchsorted(self.frames, own)          # owner frames are index frames
        np.add.at(v, pos[cluster], np.repeat(w, sizes))
        outside = None
        if window is not None:
            j0, j1 = window
            outside = (self.frames < j0) | (self.frames >= j1)
            v[outside] = 0.0
        sm = v.copy()
        sm[1:] += v[:-1]
        sm[:-1] += v[1:]
        if outside is not None:
            sm[outside] = 0.0
        return sm

    def query(self, desc: np.ndarray, top: int, window: tuple[int, int] | None = None) -> list[tuple[int, float]]:
        """Candidate RAW frames for one set of query descriptors: [(raw frame, votes)], best first.

        Peaks (local maxima) of the smoothed cluster-aware vote vector, restricted to RAW frames in
        ``window`` = [j0, j1) when given. Deterministic ordering (votes desc, frame asc)."""
        sm = self.votes(desc, window)
        if not np.any(sm > 0):
            return []
        left = np.concatenate([[-np.inf], sm[:-1]])
        right = np.concatenate([sm[1:], [-np.inf]])
        peaks = np.flatnonzero((sm > 0) & (sm >= left) & (sm > right))
        order = sorted(peaks.tolist(), key=lambda i: (-sm[i], int(self.frames[i])))
        return [(int(self.frames[i]), float(sm[i])) for i in order[:max(1, int(top))]]


# ---------------------------------------------------------------------------------------------
# Anchors
# ---------------------------------------------------------------------------------------------

@dataclass
class Anchor:
    """A verified match of one competitor frame (DESIGN §5)."""
    k: int
    raw: int
    flip: bool
    sim: Sim
    inliers: int
    inlier_ratio: float
    votes: float
    zncc: float
    source: str = "global"        # 'global' | 'audio' | 'rescue'

    def to_dict(self) -> dict:
        return {"k": int(self.k), "raw": int(self.raw), "flip": bool(self.flip), "sim": self.sim.to_dict(),
                "inliers": int(self.inliers), "inlier_ratio": float(self.inlier_ratio),
                "votes": float(self.votes), "zncc": float(self.zncc), "source": self.source}

    @staticmethod
    def from_dict(d: dict) -> "Anchor":
        return Anchor(int(d["k"]), int(d["raw"]), bool(d["flip"]), Sim.from_dict(d["sim"]), int(d["inliers"]),
                      float(d["inlier_ratio"]), float(d["votes"]), float(d["zncc"]), str(d.get("source", "global")))


_FLIP_FEAT: OrderedDict[tuple[int, int], tuple[np.ndarray, np.ndarray]] = OrderedDict()


def _raw_features(raw: Proxy, index: RawIndex | None, j: int, flip: bool, nfeat: int) -> tuple[np.ndarray, np.ndarray]:
    """SIFT of RAW proxy frame j or of cv2.flip(raw_j, 1): from the index storage when j is an index frame
    (the mirror via :func:`mirror_features`), else computed (per-process LRU)."""
    import cv2
    if index is not None:
        f = index.frame_features(j)
        if f is not None:
            return mirror_features(f[0], f[1], raw.size[0]) if flip else f
    key = (int(j), int(flip))
    hit = _FLIP_FEAT.get(key)
    if hit is not None:
        _FLIP_FEAT.move_to_end(key)
        return hit
    img = np.asarray(raw.get(j))
    if flip:
        img = cv2.flip(img, 1)
    feat = detect_sift(img, None, nfeat)
    _FLIP_FEAT[key] = feat
    if len(_FLIP_FEAT) > 64:
        _FLIP_FEAT.popitem(last=False)
    return feat


def _ransac(comp_pts: np.ndarray, comp_desc: np.ndarray, raw_pts: np.ndarray, raw_desc: np.ndarray,
            cfg) -> tuple[np.ndarray | None, int, int]:
    """Pairwise Lowe ratio (cfg.lowe_ratio) against ONE RAW frame + estimateAffinePartial2D RAW -> comp.
    Returns (M 2x3 or None, inliers, good matches)."""
    import cv2
    if len(comp_desc) < 3 or len(raw_desc) < 3:
        return None, 0, 0
    bf = cv2.BFMatcher(cv2.NORM_L2)
    mm = bf.knnMatch(comp_desc.astype(np.float32), raw_desc.astype(np.float32), k=2)
    good = [p[0] for p in mm if len(p) == 2 and p[0].distance < cfg.lowe_ratio * p[1].distance]
    if len(good) < 3:
        return None, 0, len(good)
    src = np.float32([raw_pts[g.trainIdx] for g in good])       # RAW (or flipped RAW) proxy, cv coords
    dst = np.float32([comp_pts[g.queryIdx] for g in good])      # comp proxy, cv coords
    M, inl = cv2.estimateAffinePartial2D(src, dst, method=cv2.RANSAC,
                                         ransacReprojThreshold=float(cfg.ransac_reproj_px),
                                         maxIters=2000, confidence=0.995, refineIters=10)
    if M is None or inl is None:
        return None, 0, len(good)
    return M, int(inl.sum()), len(good)


class _Scorer:
    """Scores RAW candidates for one competitor frame under a Sim (thin wrapper over scoring.py)."""

    def __init__(self, comp_img: np.ndarray, roi: tuple[int, int, int, int], allowed: np.ndarray | None,
                 raw: Proxy, comp_ratio: tuple[float, float], cfg):
        self.region = scoring.prepare_comp(comp_img, roi, allowed, cfg.score_blur, with_grad=cfg.grad_weight > 0)
        self.raw, self.cr, self.cfg = raw, tuple(comp_ratio), cfg
        self.W = float(raw.full_size[0])
        self.rr = tuple(raw.ratio)

    def scores(self, js: Sequence[int], sim: Sim, flip: bool) -> np.ndarray:
        js = [int(j) for j in js]
        ok = [j for j in js if self.raw.has(j)]
        out = np.full(len(js), np.nan)
        if not ok:
            return out
        s = scoring.score_candidates(self.region, [np.asarray(self.raw.get(j)) for j in ok], sim, flip, self.W,
                                     self.rr, self.cr, blur=self.cfg.score_blur, grad_weight=self.cfg.grad_weight)
        pos = {j: i for i, j in enumerate(ok)}
        for i, j in enumerate(js):
            if j in pos:
                out[i] = s[pos[j]]
        return out


def _nanargmax(a: np.ndarray) -> int:
    if a.size == 0 or not np.any(np.isfinite(a)):
        return -1
    return int(np.nanargmax(np.where(np.isfinite(a), a, -np.inf)))


def search_frame(k: int, comp: Proxy, raw: Proxy, index: RawIndex, allowed: np.ndarray | None, cfg,
                 window: tuple[int, int] | None = None, source: str = "global",
                 roi: tuple[int, int, int, int] | None = None,
                 report: list | None = None) -> list[Anchor]:
    """Find verified RAW matches (normal and flipped) of competitor frame k (DESIGN §5).

    SIFT inside ``allowed`` (bool [h, w] at comp proxy res; None = whole frame); the mirrored frame's
    descriptors (a permutation, :func:`mirror_features`) only vote; candidates = cluster-aware index
    votes (restricted to RAW ``window`` = [j0, j1) when given). Each candidate index frame j is
    verified: Lowe 0.75 against that single RAW frame (flip hypothesis: SIFT features of
    cv2.flip(raw_j, 1) against the UNFLIPPED comp keypoints), RANSAC RAW -> comp,
    ``inliers >= cfg.min_inliers`` and ``ratio >= cfg.min_inlier_ratio``; then the Sim is re-estimated
    against the best EXACT RAW frame within the index spacing (score j-R..j+R under the Sim, ECC refit
    on the argmax) and accepted only if its masked ZNCC >=
    match_thresh - anchor_zncc_slack. Returns anchors best-first (one per (raw, flip)); rejected
    candidates are appended to ``report`` (evidence for the decision log) when given.
    """
    from .refine import refine_transform   # lazy: refine imports this module

    img = np.asarray(comp.get(k))
    h, w = img.shape[:2]
    if roi is None:
        roi = mask_bbox(allowed, (h, w))
    cpts, cdesc = detect_sift(img, allowed, cfg.sift_nfeatures, roi)
    if len(cdesc) < max(3, cfg.min_inliers):
        if report is not None:
            report.append({"k": int(k), "reason": "few_keypoints", "n": int(len(cdesc))})
        return []
    cdesc_f = cdesc[:, _MIRROR_IDX]          # = SIFT of the mirrored frame (votes for the flip hypothesis)
    top = int(cfg.vote_top_candidates)
    cands = [(j, False, v) for j, v in index.query(cdesc, top, window)]
    cands += [(j, True, v) for j, v in index.query(cdesc_f, top, window)]
    if not cands:
        if report is not None:
            report.append({"k": int(k), "reason": "no_votes"})
        return []
    cands.sort(key=lambda c: (-c[2], c[0], c[1]))
    vmax = cands[0][2]
    min_frac = float(getattr(cfg, "vote_min_frac", 0.2))
    cands = [c for c in cands if c[2] >= min_frac * vmax][:top]

    scorer = _Scorer(img, roi, allowed, raw, comp.ratio, cfg)
    W = float(raw.full_size[0])
    radius = max(int(cfg.refine_radius), index.step // 2 + 1)
    accept = cfg.match_thresh - cfg.anchor_zncc_slack
    found: dict[tuple[int, bool], Anchor] = {}
    for j, flip, votes in cands:
        if any(a.flip == flip and abs(a.raw - j) <= radius + 1 for a in found.values()):
            continue      # would re-estimate onto an already accepted exact frame
        rpts, rdesc = _raw_features(raw, index, j, flip, cfg.sift_nfeatures)
        M, n_inl, n_good = _ransac(cpts, cdesc, rpts, rdesc, cfg)
        ratio = n_inl / n_good if n_good else 0.0
        if M is None or n_inl < cfg.min_inliers or ratio < cfg.min_inlier_ratio:
            if report is not None:
                report.append({"k": int(k), "raw": int(j), "flip": bool(flip), "votes": round(votes, 2),
                               "reason": "ransac", "inliers": n_inl, "good": n_good})
            continue
        try:
            sim = from_cv_matrix(M, False, W, tuple(raw.ratio), tuple(comp.ratio))
        except ValueError:
            continue
        if not (0.02 < sim.s < 50.0):
            continue
        # re-estimate against the best EXACT RAW frame near the index frame
        js = [i for i in range(j - radius, j + radius + 1) if raw.has(i)]
        sc = scorer.scores(js, sim, flip)
        ib = _nanargmax(sc)
        if ib < 0:
            if report is not None:
                report.append({"k": int(k), "raw": int(j), "flip": bool(flip), "reason": "no_score"})
            continue
        jb = js[ib]
        sim2, z2 = refine_transform(img, np.asarray(raw.get(jb)), sim, flip, W, tuple(raw.ratio), tuple(comp.ratio),
                                    allowed, cfg, roi=roi)
        js2 = [i for i in range(jb - 2, jb + 3) if raw.has(i)]
        sc2 = scorer.scores(js2, sim2, flip)
        ib2 = _nanargmax(sc2)
        if ib2 >= 0 and js2[ib2] != jb:
            jb = js2[ib2]
            sim3, z3 = refine_transform(img, np.asarray(raw.get(jb)), sim2, flip, W, tuple(raw.ratio),
                                        tuple(comp.ratio), allowed, cfg, roi=roi)
            sim2, z2 = sim3, z3
        if not np.isfinite(z2) or z2 < accept:
            if report is not None:
                report.append({"k": int(k), "raw": int(jb), "flip": bool(flip), "reason": "zncc",
                               "zncc": None if not np.isfinite(z2) else round(float(z2), 4), "inliers": n_inl})
            continue
        a = Anchor(int(k), int(jb), bool(flip), sim2, int(n_inl), float(ratio), float(votes), float(z2), source)
        key = (a.raw, a.flip)
        if key not in found or a.zncc > found[key].zncc:
            found[key] = a
    return sorted(found.values(), key=lambda a: (-a.zncc, a.raw, a.flip))


# ---------------------------------------------------------------------------------------------
# Sparse search
# ---------------------------------------------------------------------------------------------

def audio_window(hints: AudioHints | None, k: int, comp_fps, raw_fps, cfg, raw_n: int) -> tuple[int, int] | None:
    """RAW frame window [j0, j1) of +- cfg.audio_restrict_s around the confident audio hint nearest to
    competitor frame k (None when no confident hint covers k)."""
    if hints is None or len(hints.comp_t) == 0:
        return None
    conf = hints.confident(cfg.audio_min_conf)
    if not np.any(conf):
        return None
    t = float(k) / float(comp_fps)
    idx = np.flatnonzero(conf)
    d = np.abs(np.asarray(hints.comp_t)[idx] - t)
    i = int(idx[int(np.argmin(d))])
    if float(d.min()) > max(float(hints.window), 2.0 * float(hints.hop)):
        return None
    sp = float(hints.speed[i]) if np.isfinite(hints.speed[i]) else 1.0
    rt = float(hints.raw_t[i]) + sp * (t - float(hints.comp_t[i]))
    j = int(math.floor(rt * float(raw_fps) + 1e-9))
    r = int(math.ceil(cfg.audio_restrict_s * float(raw_fps)))
    j0, j1 = max(0, j - r), min(int(raw_n), j + r + 1)
    return (j0, j1) if j1 > j0 else None


def _search_worker(state: dict, task: tuple[int, tuple[int, int] | None, str]) -> tuple[int, list[dict], list]:
    k, window, source = task
    comp, raw, index, cfg = state["comp"], state["raw"], state["index"], state["cfg"]
    allowed = state["allowed"](k)
    roi = state["roi"]
    rep: list = []
    anchors: list[Anchor] = []
    if window is not None:
        anchors = search_frame(k, comp, raw, index, allowed, cfg, window=window, source="audio", roi=roi, report=rep)
    if not anchors:
        anchors = search_frame(k, comp, raw, index, allowed, cfg, window=None, source=source, roi=roi, report=rep)
    keep = int(getattr(cfg, "anchors_per_frame", 3))
    return int(k), [a.to_dict() for a in anchors[:keep]], rep[:12]


def run_searches(comp: Proxy, raw: Proxy, index: RawIndex, allowed: Callable[[int], np.ndarray],
                 roi: tuple[int, int, int, int], hints: AudioHints | None, frames: Iterable[int], cfg,
                 source: str = "global") -> list[tuple[int, list[Anchor], list]]:
    """search_frame on many frames in a fork pool (audio window first, then global). Input order kept."""
    index.ensure_built()
    tasks = [(int(k), audio_window(hints, k, comp.fps, raw.fps, cfg, raw.n), source) for k in frames]
    state = {"comp": comp, "raw": raw, "index": index, "cfg": cfg, "allowed": allowed, "roi": roi}
    res = parallel_map(_search_worker, tasks, cfg.resolved_workers(), state, cfg.seed, min_items=4)
    return [(k, [Anchor.from_dict(d) for d in ads], rep) for k, ads, rep in res]


def sparse_search(comp: Proxy, raw: Proxy, layout: Layout | None, overlays: Any, index: RawIndex,
                  hints: AudioHints | None, cfg, dlog: DecisionLog | None, frames: Iterable[int] | None = None,
                  cache: Cache | None = None, allowed_fn: Callable[[int], np.ndarray] | None = None) -> list[Anchor]:
    """Anchors for every cfg.comp_search_stride-th competitor frame (or ``frames``) (DESIGN §5).

    Audio-restricted (+- cfg.audio_restrict_s around a confident hint) first, global fallback; frames
    whose video region is uniform (std < cfg.uniform_std) are skipped. Runs in a fork pool (memmaps
    shared, seeded per item, results in input order). Cached as JSON (stage 'sparse_search') when a
    ``cache`` is given. ``allowed_fn`` overrides the default :class:`AllowedMasks`. Returns anchors
    sorted by (k, -zncc).
    """
    dlog = dlog or null_dlog()
    if frames is None:
        frames = list(range(0, comp.n, max(1, int(cfg.comp_search_stride))))
    else:
        frames = sorted(set(int(k) for k in frames))
    allowed = allowed_fn or AllowedMasks(layout, overlays, comp, cfg)
    roi = box_roi(layout, comp)
    key = stage_key("sparse_search", proxy_id(comp), proxy_id(raw), index.key, layout_id(layout),
                    overlays_id(overlays), hints_id(hints), cfg.analysis_params(), frames,
                    "custom_allowed" if allowed_fn is not None else "")

    def compute() -> list[dict]:
        todo = []
        skipped = []
        for k in frames:
            m = allowed(k)
            _, sd = scoring.region_stats(np.asarray(comp.get(k)), roi, m)
            if not np.isfinite(sd) or sd < cfg.uniform_std:
                skipped.append(k)
                continue
            todo.append(k)
        if skipped:
            dlog.record("visual_match", "skip_uniform", frames=skipped)
        out: list[dict] = []
        for k, anchors, rep in run_searches(comp, raw, index, allowed, roi, hints, todo, cfg, source="global"):
            if anchors:
                dlog.record("visual_match", "anchor", comp_frame=k,
                            evidence=[{"raw": a.raw, "flip": a.flip, "zncc": round(a.zncc, 4), "inliers": a.inliers,
                                       "ratio": round(a.inlier_ratio, 3), "votes": round(a.votes, 2),
                                       "source": a.source, "sim": a.sim.to_dict()} for a in anchors],
                            rejected=rep)
            else:
                dlog.record("visual_match", "no_anchor", comp_frame=k, rejected=rep)
            out.extend(a.to_dict() for a in anchors)
        return out

    data = cache.json("sparse_search", key, compute) if cache is not None else compute()
    anchors = [Anchor.from_dict(d) for d in data]
    anchors.sort(key=lambda a: (a.k, -a.zncc, a.raw, a.flip))
    log.info("sparse search: %d anchors on %d/%d searched frames", len(anchors), len({a.k for a in anchors}),
             len(frames))
    return anchors
