"""fullres.py: the full-resolution pass of the thorough default (Task 5) -- the cut matching works on small proxies
(the RAW at 640 px wide); this pass decodes the frames that matter at their full size and compares them on the GPU.

* :func:`recheck` -- before the cuts are decided: every slightly uncertain matched frame (refine's ``low_margin`` /
  ``confounded`` / a soft time range wider than the identical frames) is compared with each candidate RAW frame
  (its soft range +- NEIGHBOURS) at full resolution, each candidate with its own framing refined there (a few
  Gauss-Newton steps from the frame's Sim, so a pan cannot let a neighbour stand in through a shift). The frames
  within DECIDE of the best are the new soft range -- only ever narrower than the proxy's (a full-resolution result
  outside it keeps the proxy's decision and is listed). The re-checked FrameMap is cached as a frame map of its own
  key, so the determinism re-run (s9_7) reads exactly what this run used.
* :func:`verify` -- after the cut list: every frame a RAW segment shows, warped with its segment's own model at full
  resolution: its masked ZNCC as delivered, the framing error the refinement finds, and whether a neighbouring RAW
  frame fits better; and every cut between two RAW segments: each side's frame against the other side's model.

Scores are the masked ZNCC of scoring.py (layout masks, warped-RAW valid pixels) on lightly blurred (BLUR_PX)
full-resolution gray frames. Everything runs on the GPU (gpu.py); without one the pass is skipped and said so.
"""
from __future__ import annotations

import math
from collections import OrderedDict
import time
from fractions import Fraction
from typing import Any, Iterable, Sequence

import numpy as np

from .common import log
from .geometry import Sim, to_cv_matrix

VERSION = 4
BLUR_PX = 1.0             # Gaussian sigma (full-res px) on both images before the ZNCC
NEIGHBOURS = 2            # re-check candidates: the soft range +- this many RAW frames
MAX_CANDS = 9             # at most this many candidates per frame (centred on refine's best)
EXTEND = 4                # ... and up to this many more past an edge while the score keeps rising towards it
JUMP_AROUND = 2           # the frames this near a RAW jump (backwards, or more than JUMP_STEP on) are re-checked too
JUMP_STEP = 2
DECIDE = 0.004            # full-res ZNCC: frames within this of the best stay in the soft range
REFINE_ITERS = 10         # Gauss-Newton steps of the framing refinement (similarity, full res)
OVERRULE = 0.01         # the re-check takes a RAW frame outside the proxy's soft range when it beats every one inside
OVERRULE_MIN = 0.9      # ... by more than this, and itself scores at least this (a clear picture, not a blur)
VERIFY_NOISE = 0.002      # verification: a neighbour fitting better by more than this is listed ...
VERIFY_FAIL = 0.01        # ... and by more than this (or a cut whose other side fits better by it) fails the check
REPEAT_EPS = 1e-3         # two frames are one picture (a repeat) when 1 - ZNCC is under this (Zendaya-age at full
                          # resolution: repeats 2e-5 to 1.4e-4, the smallest move 2.9e-3) ...
REPEAT_REL = 0.2          # ... and under this share of the change on each side of them
MIN_PIXELS = 4096


def available(cfg: Any) -> str | None:
    """None when the pass can run (cfg.full_res and a usable GPU), else why not."""
    from . import gpu
    if not getattr(cfg, "full_res", False):
        return "off (--fast)" if getattr(cfg, "fast", False) else "off"
    if not getattr(cfg, "gpu", False):
        return "no GPU"
    return gpu.available()


# ---------------------------------------------------------------------------------------------------------------------
# full-resolution frames
# ---------------------------------------------------------------------------------------------------------------------

def open_reader(info: Any) -> Any:
    from .media import VideoReader
    from .probe import reader_sar, video_stream_ordinal
    return VideoReader(info.path, fps=info.fps, stream_index=video_stream_ordinal(info), rotation=info.rotation,
                       sar=reader_sar(info))


class FrameStore:
    """Full-resolution gray frames (display orientation) of one video, decoded in sequential runs, kept in memory."""

    def __init__(self, info: Any, gap: int = 12):
        self.info = info
        self.gap = int(gap)
        self.frames: dict[int, np.ndarray] = {}

    def load(self, idx: Iterable[int]) -> None:
        need = sorted({int(i) for i in idx if int(i) >= 0} - set(self.frames))
        if not need:
            return
        runs: list[list[int]] = []
        for i in need:
            if runs and i - runs[-1][1] <= self.gap:
                runs[-1][1] = i
            else:
                runs.append([i, i])
        want = set(need)
        with open_reader(self.info) as rd:
            for a, b in runs:
                for j, img in rd.frames(a, b + 1, fmt="gray"):
                    if j in want:
                        self.frames[int(j)] = np.ascontiguousarray(img)

    def get(self, i: int) -> np.ndarray | None:
        return self.frames.get(int(i))


class LazyFrames:
    """Full-resolution gray frames of one video decoded on demand -- ``around`` frames on each side of one asked for
    come along (criterion 2 looks at a cut's two frames and moves it frame by frame) -- at most ``keep`` in memory,
    the least recently used going first. One decoder serves every read (seeking): a decoder opened and closed for
    each read starts and ends its frame threads every time, and in a process that has loaded CUDA that keeps memory
    for good -- ~70 MB per 1080x1920 decoder (Task 8: 92 GB on video1's 1,654-frame finished video)."""

    def __init__(self, info: Any, keep: int = 192, around: int = 2):
        self.info, self.keep, self.around = info, int(keep), int(around)
        self.n = int(getattr(info, "nb_frames", 0) or 0)
        self.frames: OrderedDict[int, np.ndarray] = OrderedDict()
        self._rd: Any = None

    def _reader(self) -> Any:
        if self._rd is None:
            self._rd = open_reader(self.info)
        return self._rd

    def close(self) -> None:
        self.frames.clear()
        if self._rd is not None:
            self._rd.close()
            self._rd = None

    def prefetch(self, a: int, b: int) -> None:
        """Frames [a, b) in one sequential decode (a segment's frames), when they fit in ``keep``."""
        a = max(0, int(a))
        b = min(int(b), self.n) if self.n else int(b)
        if b <= a or b - a > self.keep or all(i in self.frames for i in range(a, b)):
            return
        for j, img in self._reader().frames(a, b, fmt="gray"):
            self.frames[int(j)] = np.ascontiguousarray(img)
            self.frames.move_to_end(int(j))
        while len(self.frames) > self.keep:
            self.frames.popitem(last=False)

    def get(self, i: int) -> np.ndarray | None:
        i = int(i)
        if i < 0 or (self.n and i >= self.n):
            return None
        if i not in self.frames:
            a, b = max(0, i - self.around), i + self.around
            if self.n:
                b = min(b, self.n - 1)
            for j, img in self._reader().frames(a, b + 1, fmt="gray"):
                self.frames[int(j)] = np.ascontiguousarray(img)
                self.frames.move_to_end(int(j))
            while len(self.frames) > self.keep:
                self.frames.popitem(last=False)
        img = self.frames.get(i)
        if img is not None:
            self.frames.move_to_end(i)
        return img


# ---------------------------------------------------------------------------------------------------------------------
# GPU scoring
# ---------------------------------------------------------------------------------------------------------------------

class Scorer:
    """Masked ZNCC of competitor frames against warped RAW frames at full resolution, on the GPU."""

    def __init__(self, raw_wh: tuple[float, float], blur_px: float = BLUR_PX):
        import torch
        from . import gpu
        gpu._exact_fp32()
        self.t = torch
        self.dev = torch.device("cuda")
        self.raw_wh = (float(raw_wh[0]), float(raw_wh[1]))
        r = max(1, int(math.ceil(3.0 * blur_px)))
        x = torch.arange(-r, r + 1, dtype=torch.float32)
        k = torch.exp(-0.5 * (x / float(blur_px)) ** 2)
        self.kern = (k / k.sum()).to(self.dev)
        self.r = r
        self._raw: dict[int, Any] = {}
        self._comp: dict[int, tuple] = {}
        self._grids: OrderedDict[tuple[int, int, int, int], tuple[Any, Any]] = OrderedDict()

    # -- images ----------------------------------------------------------------------------------------------------
    def _blur(self, img: Any) -> Any:
        F = self.t.nn.functional
        x = img[None, None]
        x = F.pad(x, (self.r, self.r, 0, 0), mode="replicate")
        x = F.conv2d(x, self.kern.view(1, 1, 1, -1))
        x = F.pad(x, (0, 0, self.r, self.r), mode="replicate")
        x = F.conv2d(x, self.kern.view(1, 1, -1, 1))
        return x[0, 0]

    def raw(self, j: int, img: np.ndarray) -> tuple[Any, Any, Any]:
        """(blurred RAW frame, d/du, d/dv) on the GPU, kept for the next calls."""
        hit = self._raw.get(int(j))
        if hit is None:
            b = self._blur(self.t.from_numpy(np.ascontiguousarray(img)).to(self.dev).float())
            gu = self.t.zeros_like(b)
            gv = self.t.zeros_like(b)
            gu[:, 1:-1] = 0.5 * (b[:, 2:] - b[:, :-2])
            gv[1:-1, :] = 0.5 * (b[2:, :] - b[:-2, :])
            hit = (b, gu, gv)
            self._raw[int(j)] = hit
            if len(self._raw) > 96:
                self._raw.pop(next(iter(self._raw)))
        return hit

    def comp(self, k: int, img: np.ndarray, allowed: np.ndarray) -> tuple[Any, Any, tuple[int, int, int, int]] | None:
        """(blurred competitor ROI, its mask, the ROI (x, y, w, h)) on the GPU, kept for the next calls; None when the
        layout leaves too few pixels."""
        hit = self._comp.get(int(k))
        if hit is None:
            H, W = img.shape[:2]
            m = np.asarray(allowed, bool)
            if m.shape != (H, W):
                import cv2
                m = cv2.resize(m.astype(np.uint8), (W, H), interpolation=cv2.INTER_NEAREST) > 0
            ys, xs = np.nonzero(m)
            if len(xs) < MIN_PIXELS:
                return None
            x0, x1, y0, y1 = int(xs.min()), int(xs.max()) + 1, int(ys.min()), int(ys.max()) + 1
            roi = (x0, y0, x1 - x0, y1 - y0)
            full = self._blur(self.t.from_numpy(np.ascontiguousarray(img)).to(self.dev).float())
            hit = (full[y0:y1, x0:x1].contiguous(), self.t.from_numpy(m[y0:y1, x0:x1]).to(self.dev), roi)
            self._comp[int(k)] = hit
            if len(self._comp) > 48:
                self._comp.pop(next(iter(self._comp)))
        return hit

    # -- geometry --------------------------------------------------------------------------------------------------
    def inverse_map(self, sim: Sim, flip: bool) -> np.ndarray:
        """2x3 map competitor full-res pixel -> RAW full-res pixel (OpenCV pixel centres) of a canonical Sim."""
        m = np.vstack([to_cv_matrix(sim, bool(flip), self.raw_wh[0]), [0.0, 0.0, 1.0]])
        return np.linalg.inv(m)[:2, :]

    def _sample(self, img: Any, u: Any, v: Any) -> Any:
        H, W = img.shape
        grid = self.t.stack([2.0 * u / (W - 1) - 1.0, 2.0 * v / (H - 1) - 1.0], dim=-1)[None]
        return self.t.nn.functional.grid_sample(img[None, None], grid, mode="bilinear", padding_mode="zeros",
                                                align_corners=True)[0, 0]

    def _coords(self, A: np.ndarray, p: np.ndarray, roi: tuple[int, int, int, int]) -> tuple[Any, Any, Any, Any]:
        """RAW coordinates of the ROI pixels under A o R(p) (R: a small similarity about the ROI centre)."""
        x0, y0, w, h = roi
        cx, cy = x0 + 0.5 * (w - 1), y0 + 0.5 * (h - 1)
        grid = self._grids.get(tuple(roi))
        if grid is None:                             # the ROI's pixel grid about its centre: the same for every call
            grid = self.t.meshgrid(self.t.arange(h, device=self.dev, dtype=self.t.float32) + (y0 - cy),
                                   self.t.arange(w, device=self.dev, dtype=self.t.float32) + (x0 - cx), indexing="ij")
            self._grids[tuple(roi)] = grid
            while len(self._grids) > 8:
                self._grids.popitem(last=False)
        ys, xs = grid
        a, b, tx, ty = (float(v) for v in p)
        X = (1.0 + a) * xs - b * ys + tx + cx
        Y = b * xs + (1.0 + a) * ys + ty + cy
        u = float(A[0, 0]) * X + float(A[0, 1]) * Y + float(A[0, 2])
        v = float(A[1, 0]) * X + float(A[1, 1]) * Y + float(A[1, 2])
        return u, v, xs, ys

    # -- scores ----------------------------------------------------------------------------------------------------
    @staticmethod
    def _zncc_t(t: Any, a: Any, b: Any, m: Any) -> float:
        return Scorer._zncc_i(t, a, b, m.nonzero(as_tuple=True))

    @staticmethod
    def _zncc_i(t: Any, a: Any, b: Any, idx: tuple) -> float:
        """Masked ZNCC over the mask's pixels ``idx`` (its nonzero(as_tuple=True): the pixels a[m] takes, in the same
        order -- found once and shared by every use of the mask)."""
        n = int(idx[0].numel())
        if n < MIN_PIXELS:
            return float("nan")
        a = a[idx].double()
        b = b[idx].double()
        a = a - a.mean()
        b = b - b.mean()
        den = t.sqrt((a * a).sum() * (b * b).sum())
        return float((a * b).sum() / den) if float(den) > 1e-9 else float("nan")

    def _at(self, comp: tuple, raw: tuple, A: np.ndarray, p: np.ndarray) -> tuple:
        """What both the score at p and the Gauss-Newton step from p use: (u, v, xs, ys, the mask's pixels, the RAW
        frame sampled at them) -- computed once for each p."""
        T, M, roi = comp
        img = raw[0]
        H, W = img.shape
        u, v, xs, ys = self._coords(A, p, roi)
        valid = (u >= 1.0) & (u <= W - 2.0) & (v >= 1.0) & (v <= H - 2.0)
        return u, v, xs, ys, (M & valid).nonzero(as_tuple=True), self._sample(img, u, v)

    def score(self, comp: tuple, raw: tuple, A: np.ndarray, p: np.ndarray | None = None) -> float:
        """Masked ZNCC of a prepared competitor ROI against a prepared RAW frame under map A (o R(p))."""
        st = self._at(comp, raw, A, np.zeros(4) if p is None else p)
        return self._zncc_i(self.t, comp[0], st[5], st[4])

    def refine(self, comp: tuple, raw: tuple, A: np.ndarray, iters: int = REFINE_ITERS, start: bool = False
               ) -> tuple[np.ndarray, float] | tuple[np.ndarray, float, float]:
        """The small similarity R(p) (about the ROI centre, competitor px) that best aligns the RAW frame warped by
        A o R(p) with the competitor ROI: Gauss-Newton on the normalised difference (ZNCC's own measure). Returns
        (p = [scale-1 cos part, sin part, tx, ty], ZNCC at p), and the ZNCC at A itself with ``start``. Each p is
        sampled once: the score at it and the next step from it share the samples (Task 9; the same numbers)."""
        t = self.t
        T, M, roi = comp
        img, gu, gv = raw
        p = np.zeros(4)
        st = self._at(comp, raw, A, p)
        best_p, best_z = p.copy(), self._zncc_i(t, T, st[5], st[4])
        z0 = best_z
        A2 = t.tensor(A[:, :2], dtype=t.float32, device=self.dev)
        for _ in range(int(iters)):
            u, v, xs, ys, idx, I = st
            if int(idx[0].numel()) < MIN_PIXELS:
                break
            Iu = self._sample(gu, u, v)
            Iv = self._sample(gv, u, v)
            Tm, Im = T[idx], I[idx]
            ts, is_ = Tm.std() + 1e-6, Im.std() + 1e-6
            e = (Tm - Tm.mean()) / ts - (Im - Im.mean()) / is_
            gx = (A2[0, 0] * Iu[idx] + A2[1, 0] * Iv[idx]) / is_          # d I / d X (competitor x), normalised
            gy = (A2[0, 1] * Iu[idx] + A2[1, 1] * Iv[idx]) / is_
            X, Y = xs[idx], ys[idx]
            J = t.stack([gx * X + gy * Y, -gx * Y + gy * X, gx, gy], dim=1)
            # the 4 x 4 normal equations solved on the CPU (a GPU solve of so small a system costs ~0.1 s a call),
            # brought over in one copy
            Hg = t.cat([J.T @ J, (J.T @ e)[:, None]], 1).double().cpu().numpy()
            Hm, g = Hg[:, :4], Hg[:, 4]
            try:
                dp = np.linalg.solve(Hm + 1e-9 * np.eye(4), g)
            except np.linalg.LinAlgError:
                break
            if not np.all(np.isfinite(dp)):
                break
            p = p + dp
            st = self._at(comp, raw, A, p)
            z = self._zncc_i(t, T, st[5], st[4])
            if np.isfinite(z) and (not np.isfinite(best_z) or z > best_z):
                best_p, best_z = p.copy(), z
            if abs(dp[2]) < 0.01 and abs(dp[3]) < 0.01 and abs(dp[0]) < 1e-5 and abs(dp[1]) < 1e-5:
                break
        return (best_p, float(best_z), float(z0)) if start else (best_p, float(best_z))

    def refined_sim(self, sim: Sim, flip: bool, p: np.ndarray, roi: tuple[int, int, int, int]) -> Sim:
        """The canonical Sim whose map is A o R(p) (``refine``'s result as a framing): RAW -> competitor is
        R(p)^-1 o to_cv_matrix(sim)."""
        from .geometry import from_cv_matrix
        x0, y0, w, h = roi
        cx, cy = x0 + 0.5 * (w - 1), y0 + 0.5 * (h - 1)
        a, b, tx, ty = (float(v) for v in p)
        R = np.array([[1.0 + a, -b, tx + cx - (1.0 + a) * cx + b * cy],
                      [b, 1.0 + a, ty + cy - b * cx - (1.0 + a) * cy],
                      [0.0, 0.0, 1.0]])
        M = np.vstack([to_cv_matrix(sim, bool(flip), self.raw_wh[0]), [0.0, 0.0, 1.0]])
        return from_cv_matrix((np.linalg.inv(R) @ M)[:2, :], bool(flip), self.raw_wh[0])

    def shift_px(self, p: np.ndarray, roi: tuple[int, int, int, int]) -> float:
        """How far R(p) moves the ROI's corners at most (competitor px): the framing error it corrects."""
        x0, y0, w, h = roi
        hw, hh = 0.5 * (w - 1), 0.5 * (h - 1)
        a, b, tx, ty = (float(v) for v in p)
        return float(max(math.hypot(a * x - b * y + tx, b * x + a * y + ty)
                         for x, y in ((-hw, -hh), (hw, -hh), (-hw, hh), (hw, hh))))

    def close(self) -> None:
        self._raw.clear()
        self._comp.clear()
        try:
            self.t.cuda.empty_cache()
        except Exception:  # noqa: BLE001
            pass


# ---------------------------------------------------------------------------------------------------------------------
# the re-check of slightly uncertain frames (before the cuts are decided)
# ---------------------------------------------------------------------------------------------------------------------

def uncertain_frames(fm: Any, match_status: int) -> list[int]:
    """Matched frames whose RAW frame refine could not tell from its neighbours (low_margin, confounded, a timing
    tie, or a soft range wider than the identical frames), and the JUMP_AROUND frames on each side of a RAW jump --
    the first frames after a jump cut in a fast pan are blurred, and the proxy's exact answer is least sure there."""
    n = int(fm.n)
    ok = [int(fm.status[k]) == match_status and int(fm.raw[k]) >= 0 for k in range(n)]
    out = set()
    for k in range(n):
        if not ok[k]:
            continue
        wide = int(fm.soft_hi[k]) - int(fm.soft_lo[k]) > int(fm.raw_hi[k]) - int(fm.raw_lo[k])
        if bool(fm.low_margin[k]) or bool(fm.confounded[k]) or bool(fm.tie[k]) or wide:
            out.add(k)
        if k > 0 and ok[k - 1]:
            d = int(fm.raw[k]) - int(fm.raw[k - 1])
            if d < 0 or d > JUMP_STEP:
                out.update(i for i in range(k - JUMP_AROUND, k + JUMP_AROUND) if 0 <= i < n and ok[i])
    return sorted(out)


def _candidate_scores(sc: "Scorer", k: int, comp_store: Any, raw_store: Any, mask: Any, sim: Sim, flip: bool,
                      cands: Sequence[int], n_raw: int) -> dict[int, float] | None:
    """:func:`recheck`'s full-resolution scores of competitor frame k's candidate RAW frames (each with its framing
    refined), following a score still rising past the candidates' edge; None when the frame cannot be read."""
    img = comp_store.get(k)
    if img is None:
        return None
    prep = sc.comp(k, img, mask)
    if prep is None:
        return None
    A = sc.inverse_map(sim, bool(flip))
    z: dict[int, float] = {}

    def try_j(j: int) -> None:
        r = raw_store.get(j)
        if r is None:
            return
        _p, zj = sc.refine(prep, sc.raw(j, r), A)
        if np.isfinite(zj):
            z[j] = zj
    for j in cands:
        try_j(j)
    if not z:
        return z
    for step in (1, -1):                         # a score still rising at an edge: the best lies further on
        for _ in range(EXTEND):
            edge = max(z) if step > 0 else min(z)
            if max(z, key=lambda j: z[j]) != edge or not 0 <= edge + step < n_raw:
                break
            n0 = len(z)
            try_j(edge + step)
            if len(z) == n0:
                break
    return z


class _GivenFrames:
    """Frames handed to a worker (a store with no video file behind it: the tests' synthetic frames)."""

    def __init__(self, frames: dict[int, np.ndarray]):
        self.frames = frames

    def load(self, idx: Iterable[int]) -> None:
        pass

    def get(self, i: int) -> np.ndarray | None:
        return self.frames.get(int(i))


def _recheck_part(task: tuple) -> dict[int, dict[int, float] | None]:
    """One process's part of the re-check (:func:`recheck`, ``workers``): its frames decoded and scored on the GPU,
    the same computation as in the parent."""
    comp_src, raw_src, raw_wh, n_raw, items, masks = task
    comp_store = FrameStore(comp_src) if not isinstance(comp_src, dict) else _GivenFrames(comp_src)
    raw_store = FrameStore(raw_src) if not isinstance(raw_src, dict) else _GivenFrames(raw_src)
    comp_store.load(k for k, *_ in items)
    raw_store.load(j for _k, js, *_ in items for j in range(js[0] - EXTEND, js[-1] + EXTEND + 1) if 0 <= j < n_raw)
    sc = Scorer(raw_wh)
    try:
        return {int(k): _candidate_scores(sc, k, comp_store, raw_store, masks[mi], sim, flip, js, n_raw)
                for k, js, sim, flip, mi in items}
    finally:
        sc.close()


def _recheck_parallel(fm: Any, ks: list[int], cands: dict[int, list[int]], comp_store: Any, raw_store: Any,
                      allowed: Any, raw_wh: tuple[float, float], n_raw: int, workers: int
                      ) -> dict[int, dict[int, float] | None] | None:
    """The candidates' scores of ``ks`` in ``workers`` processes sharing the GPU, each a run of consecutive frames
    (decoded there); None when that cannot run (the caller then scores them itself: the same numbers)."""
    import multiprocessing as mp
    from concurrent.futures import ProcessPoolExecutor
    n = max(1, min(int(workers), len(ks)))
    size = -(-len(ks) // n)
    tasks = []
    for p in range(0, len(ks), size):
        part = ks[p:p + size]
        masks: list[np.ndarray] = []
        seen: dict[tuple, int] = {}
        items = []
        for k in part:
            m = np.ascontiguousarray(np.asarray(allowed(k), bool))
            mi = seen.setdefault((m.shape, m.tobytes()), len(masks))
            if mi == len(masks):
                masks.append(m)
            items.append((int(k), list(cands[k]), fm.sim(k), bool(fm.flip[k]), mi))
        if getattr(comp_store, "info", None) is not None and getattr(raw_store, "info", None) is not None:
            srcs = (comp_store.info, raw_store.info)
        else:                                       # given frames (no video file): handed over as they are
            js = {j for _k, c, *_ in items for j in range(c[0] - EXTEND, c[-1] + EXTEND + 1)}
            srcs = ({k: comp_store.get(k) for k in part if comp_store.get(k) is not None},
                    {j: raw_store.get(j) for j in js if raw_store.get(j) is not None})
        tasks.append((srcs[0], srcs[1], tuple(raw_wh), int(n_raw), items, masks))
    try:
        out: dict[int, dict[int, float] | None] = {}
        with ProcessPoolExecutor(max_workers=len(tasks), mp_context=mp.get_context("spawn")) as ex:
            for part in ex.map(_recheck_part, tasks):
                out.update(part)
        return out
    except Exception as e:  # noqa: BLE001 - the parent scores them itself (the same numbers, more slowly)
        log.warning("full-resolution re-check: the %d GPU processes failed (%s: %s); scoring in this process",
                    len(tasks), type(e).__name__, e)
        return None


def recheck(fm: Any, comp_store: FrameStore, raw_store: FrameStore, allowed: Any, raw_wh: tuple[float, float],
            n_raw: int, match_status: int, dlog: Any = None, workers: int = 0) -> tuple[Any, dict]:
    """A copy of ``fm`` with the slightly uncertain frames re-checked at full resolution (module docstring), and a
    summary {frames, decided, narrowed, kept, outside, seconds, rows}. ``workers`` > 1: the frames are scored in that
    many processes sharing the GPU (Task 9: one process leaves the GPU mostly idle); each frame's scores, and so every
    decision, are the same."""
    t0 = time.time()
    fm = fm.copy()
    ks = uncertain_frames(fm, match_status)
    rows: list[dict] = []
    res = {"frames": len(ks), "decided": 0, "narrowed": 0, "kept": 0, "outside": 0, "overruled": 0, "rows": rows}
    if not ks:
        res["seconds"] = round(time.time() - t0, 1)
        return fm, res
    cands: dict[int, list[int]] = {}
    for k in ks:
        lo = min(int(fm.soft_lo[k]), int(fm.raw_lo[k])) - NEIGHBOURS
        hi = max(int(fm.soft_hi[k]), int(fm.raw_hi[k])) + NEIGHBOURS
        c = int(fm.raw[k])
        js = [j for j in range(lo, hi + 1) if 0 <= j < n_raw]
        js = sorted(js, key=lambda j: (abs(j - c), j))[:MAX_CANDS]
        cands[k] = sorted(js)
    scores = None
    if int(workers) > 1 and len(ks) >= 2 * int(workers):
        scores = _recheck_parallel(fm, ks, cands, comp_store, raw_store, allowed, raw_wh, n_raw, int(workers))
        res["workers"] = int(workers) if scores is not None else 1
    if scores is None:
        comp_store.load(ks)
        raw_store.load(j for js in cands.values() for j in range(js[0] - EXTEND, js[-1] + EXTEND + 1)
                       if 0 <= j < n_raw)
        sc = Scorer(raw_wh)
        try:
            scores = {k: _candidate_scores(sc, k, comp_store, raw_store, allowed(k), fm.sim(k), bool(fm.flip[k]),
                                           cands[k], n_raw) for k in ks}
        finally:
            sc.close()
    for k in ks:
        z = scores.get(k)
        if not z:
            continue
        best = max(z, key=lambda j: (z[j], -abs(j - int(fm.raw[k]))))
        near = sorted(j for j in z if z[j] >= z[best] - DECIDE)
        old = (int(fm.soft_lo[k]), int(fm.soft_hi[k]))
        lo, hi = max(old[0], near[0]), min(old[1], near[-1])
        row = {"k": int(k), "soft": list(old), "full": {str(j): round(v, 5) for j, v in z.items()},
               "best": int(best)}
        inside = [j for j in z if old[0] <= j <= old[1]]
        lead = z[best] - max((z[j] for j in inside), default=float("-inf"))
        if not (old[0] <= best <= old[1]) and lead > OVERRULE and z[best] >= OVERRULE_MIN:
            # full resolution is clearly sure where the proxy is not (a frame inside a fast pan): it decides
            near_b = sorted(j for j in z if z[j] >= z[best] - DECIDE)
            fm.soft_lo[k], fm.soft_hi[k] = near_b[0], near_b[-1]
            fm.raw[k] = best
            fm.raw_lo[k] = fm.raw_hi[k] = best
            fm.low_margin[k] = False
            res["overruled"] = res.get("overruled", 0) + 1
            row["result"] = f"full resolution chose RAW {best} (by {lead:.4f}) outside {old[0]}-{old[1]}"
        elif not (old[0] <= best <= old[1]):
            res["outside"] += 1
            row["result"] = "outside the soft range: kept"
        elif lo > hi or (lo, hi) == old:
            res["kept"] += 1
            row["result"] = "within noise: kept"
        else:
            res["narrowed"] += 1
            if lo == hi or (lo >= int(fm.raw_lo[k]) and hi <= int(fm.raw_hi[k])):
                res["decided"] += 1
                fm.low_margin[k] = False
            fm.soft_lo[k], fm.soft_hi[k] = lo, hi
            if int(fm.raw[k]) < lo or int(fm.raw[k]) > hi:
                fm.raw[k] = best
            # refine's measured range stays inside the narrowed one: what full resolution ruled out is no longer
            # "measured" (else the segmenter charges every line for leaving a frame it may not show)
            m_lo, m_hi = max(int(fm.raw_lo[k]), lo), min(int(fm.raw_hi[k]), hi)
            if m_lo > m_hi or not m_lo <= int(fm.raw[k]) <= m_hi:
                m_lo = m_hi = int(fm.raw[k])
            fm.raw_lo[k], fm.raw_hi[k] = m_lo, m_hi
            row["result"] = f"soft range {old[0]}-{old[1]} -> {lo}-{hi}"
            row["measured"] = [m_lo, m_hi]
        rows.append(row)
        if dlog is not None:
            dlog.record("fullres", "recheck", **row)
    res["seconds"] = round(time.time() - t0, 1)
    return fm, res


class SideScorer:
    """segment.py's criterion 2 at full resolution (the thorough default): the masked ZNCC of competitor frame k
    against each (RAW frame, Sim, flip) item -- with the framing the segment models give it, or (``refine``) that
    framing refined by Gauss-Newton first, when the question is WHICH RAW frame the competitor shows: in a fast pan
    both models' framings are off at a cut (extrapolated, or an edge key held), and the unrefined scores then pick
    the wrong RAW frame (the thorough Deadpool 271: RAW 2498 0.830 against 2509 0.823 as given, 0.841 against 0.993
    refined; 412 alike). ``(k, items, refine) -> scores``, or None for a frame it cannot read."""

    def __init__(self, comp_info: Any, raw_info: Any, allowed: Any, raw_wh: tuple[float, float], n_raw: int,
                 workers: int = 0):
        self.comp = LazyFrames(comp_info, keep=600)
        self.raw = LazyFrames(raw_info, keep=600)
        self.allowed = allowed
        self.sc = Scorer(raw_wh)
        self.n_raw = int(n_raw)
        self.calls = 0
        self._measured: dict[tuple[int, int, bool], tuple[Sim, float, float] | None] = {}
        # Task 9: requests the segmenter will make, computed beforehand in ``workers`` processes sharing the GPU
        self.workers = int(workers)
        self._spec = (comp_info, raw_info, tuple(raw_wh), int(n_raw))
        self._pool: Any = None
        self._called: dict[tuple, np.ndarray | None] = {}

    def __call__(self, k: int, items: Sequence[tuple[int, Sim, bool]], refine: bool = False) -> np.ndarray | None:
        key = _call_key(k, items, refine)
        if key in self._called:                      # computed beforehand (prefetch): the same numbers
            out = self._called.pop(key)
            if out is not None:
                self.calls += 1
            return out
        return self._call_now(k, items, refine)

    def _call_now(self, k: int, items: Sequence[tuple[int, Sim, bool]], refine: bool = False) -> np.ndarray | None:
        img = self.comp.get(int(k))
        if img is None:
            return None
        prep = self.sc.comp(int(k), img, self.allowed(int(k)))
        if prep is None:
            return None
        out = []
        for j, sim, flip in items:
            j = int(j)
            r = self.raw.get(j) if 0 <= j < self.n_raw else None
            if r is None:
                return None
            A = self.sc.inverse_map(sim, bool(flip))
            if refine:
                _p, z = self.sc.refine(prep, self.sc.raw(j, r), A)
            else:
                z = self.sc.score(prep, self.sc.raw(j, r), A)
            out.append(z)
        self.calls += 1
        return np.asarray(out, np.float64)

    def measure(self, k: int, j: int, sim: Sim, flip: bool) -> tuple[Sim, float, float] | None:
        """(the framing of competitor frame k on RAW frame j refined at full resolution from ``sim``, its ZNCC, the
        ZNCC at ``sim``), or None when a frame cannot be read. Remembered per (k, j, flip): the first start decides
        (deterministic: the segmenter asks in the same order every run)."""
        key = (int(k), int(j), bool(flip))
        if key in self._measured:
            return self._measured[key]
        out = self._measure_now(k, j, sim, flip)
        self._measured[key] = out
        return out

    def _measure_now(self, k: int, j: int, sim: Sim, flip: bool) -> tuple[Sim, float, float] | None:
        out = None
        img = self.comp.get(int(k))
        r = self.raw.get(int(j)) if 0 <= int(j) < self.n_raw else None
        if img is not None and r is not None:
            prep = self.sc.comp(int(k), img, self.allowed(int(k)))
            if prep is not None:
                rp = self.sc.raw(int(j), r)
                A = self.sc.inverse_map(sim, bool(flip))
                p, z, z0 = self.sc.refine(prep, rp, A, start=True)     # z0: the score at sim (refine's start)
                if np.isfinite(z):
                    out = (self.sc.refined_sim(sim, bool(flip), p, prep[2]), float(z), float(z0))
        return out

    def raw_get(self, j: int) -> np.ndarray | None:
        """RAW frame j (None outside the RAW)."""
        return self.raw.get(int(j)) if 0 <= int(j) < self.n_raw else None

    def measured(self, k: int, j: int, flip: bool) -> bool:
        """Whether (k, j, flip) is measured already (``measure`` will not read a frame for it)."""
        return (int(k), int(j), bool(flip)) in self._measured

    # -- requests computed beforehand in processes sharing the GPU (Task 9) --------------------------------------
    def prefetch(self, calls: Sequence[tuple[int, Sequence[tuple[int, Sim, bool]], bool]]) -> None:
        """The scores of these (k, items, refine) requests -- ones the segmenter is about to make -- computed in the
        GPU processes; each later call with the same request takes its result (the same numbers as computing it
        then). Does nothing without ``workers`` or for fewer than PREFETCH_MIN requests."""
        todo, seen = [], set()
        for k, items, refine in calls:
            key = _call_key(k, items, refine)
            if key not in self._called and key not in seen:
                seen.add(key)
                todo.append((key, ("call", int(k), [(int(j), s, bool(f)) for j, s, f in items], bool(refine))))
        for (key, _r), out in zip(todo, self._run([r for _key, r in todo]) or []):
            self._called[key] = out

    def prefetch_measure(self, reqs: Sequence[tuple[int, int, Sim, bool]]) -> None:
        """:meth:`measure` of these (k, j, sim, flip) requests, in the order the segmenter will ask, computed in the
        GPU processes: as ``measure`` keeps the first start of each (k, j, flip), so does this -- one measured
        already, or asked again later in the list, is left out."""
        todo, seen = [], set()
        for k, j, sim, flip in reqs:
            key = (int(k), int(j), bool(flip))
            if key not in self._measured and key not in seen:
                seen.add(key)
                todo.append((key, ("measure", int(k), int(j), sim, bool(flip))))
        for (key, _r), out in zip(todo, self._run([r for _key, r in todo]) or []):
            self._measured[key] = out

    def _run(self, reqs: list[tuple]) -> list | None:
        if self.workers < 2 or len(reqs) < PREFETCH_MIN:
            return None
        try:
            if self._pool is None:
                self._pool = GpuPool(self.workers, *self._spec)
            return self._pool.run(reqs, self.allowed)
        except Exception as e:  # noqa: BLE001 - computed when asked then (the same numbers, more slowly)
            log.warning("full resolution: the %d GPU processes failed (%s: %s); computing in this process",
                        self.workers, type(e).__name__, e)
            self.workers = 0
            self.close_pool()
            return None

    def close_pool(self) -> None:
        if self._pool is not None:
            self._pool.close()
            self._pool = None

    def close(self) -> None:
        self.close_pool()
        self._called.clear()
        self.sc.close()
        self.comp.close()
        self.raw.close()


PREFETCH_MIN = 12          # requests in one batch before the GPU processes are worth starting / asking


def _call_key(k: int, items: Sequence[tuple[int, Sim, bool]], refine: bool) -> tuple:
    return (int(k), bool(refine), tuple((int(j), float(s.s), float(s.theta_deg), float(s.tx), float(s.ty), bool(f))
                                        for j, s, f in items))


_SIDE: dict = {}


def _side_init(comp_info: Any, raw_info: Any, raw_wh: tuple[float, float], n_raw: int) -> None:
    """A GPU process's own SideScorer (decoders, scorer, CUDA), kept for every batch it is given."""
    _SIDE["sc"] = SideScorer(comp_info, raw_info, None, raw_wh, n_raw)


def _side_run(task: tuple) -> list:
    """A GPU process's batch: [("call", k, items, refine) | ("measure", k, j, sim, flip) | ("vframe", k, j, f, sim,
    flip)], with the frames' masks."""
    reqs, masks = task
    side = _SIDE["sc"]
    side.allowed = masks.__getitem__
    out = []
    for r in reqs:
        if r[0] == "call":
            out.append(side._call_now(r[1], r[2], r[3]))
        elif r[0] == "measure":
            out.append(side._measure_now(r[1], r[2], r[3], r[4]))
        else:
            out.append(_verify_frame(side.sc, side.comp.get, side.raw_get, masks[int(r[1])], r[1], r[2], r[3],
                                     r[4], r[5]))
    return out


class GpuPool:
    """Processes sharing the GPU, each with its own SideScorer for a whole stage (Task 9): one process leaves the GPU
    mostly idle while it decodes, launches small kernels and waits on them. A batch is cut in runs of consecutive
    requests (one per process) and comes back in order."""

    def __init__(self, workers: int, comp_info: Any, raw_info: Any, raw_wh: tuple[float, float], n_raw: int):
        import multiprocessing as mp
        from concurrent.futures import ProcessPoolExecutor
        self.n = max(1, int(workers))
        self.ex = ProcessPoolExecutor(max_workers=self.n, mp_context=mp.get_context("spawn"), initializer=_side_init,
                                      initargs=(comp_info, raw_info, tuple(raw_wh), int(n_raw)))

    def run(self, reqs: list[tuple], allowed: Any) -> list:
        size = -(-len(reqs) // self.n)
        tasks = []
        for p in range(0, len(reqs), size):
            part = reqs[p:p + size]
            tasks.append((part, {int(r[1]): np.asarray(allowed(int(r[1])), bool) for r in part}))
        out: list = []
        for res in self.ex.map(_side_run, tasks):
            out.extend(res)
        return out

    def close(self) -> None:
        self.ex.shutdown(wait=True, cancel_futures=True)


# ---------------------------------------------------------------------------------------------------------------------
# verification of every frame and every cut (after the cut list)
# ---------------------------------------------------------------------------------------------------------------------

def _change(a: np.ndarray, b: np.ndarray, mask: np.ndarray | None = None) -> float:
    """How much two decoded frames differ: 1 - ZNCC of their grey levels (every 4th pixel; ``mask``: only there);
    inf when they cannot be compared."""
    if a is None or b is None or a.shape != b.shape:
        return float("inf")
    sel = (slice(None, None, 4), slice(None, None, 4))
    x, y = np.asarray(a[sel], np.float32), np.asarray(b[sel], np.float32)
    if mask is not None:
        m = np.asarray(mask, bool)
        if m.shape != a.shape:
            import cv2
            m = cv2.resize(m.astype(np.uint8), (a.shape[1], a.shape[0]), interpolation=cv2.INTER_NEAREST) > 0
        m = m[sel]
        x, y = x[m], y[m]
    x, y = x.reshape(-1), y.reshape(-1)
    if x.size < 64:
        return float("inf")
    x, y = x - x.mean(), y - y.mean()
    d = float(np.sqrt((x * x).sum() * (y * y).sum()))
    return 1.0 - float((x * y).sum()) / d if d > 0 else float("inf")


def _repeat(get: Any, a: int, mask: Any = None) -> bool:
    """Frames a and a + 1 of a video are one picture (``get(i)``: its decoded frame; ``mask(i)``: the pixels to
    compare): they differ by under REPEAT_EPS, and by under REPEAT_REL of the change on each side of them (a repeat
    sits between two real changes -- in a still shot every pair differs that little, and none is a repeat)."""
    def ch(i: int) -> float:
        m = None
        if mask is not None:
            try:
                m = np.asarray(mask(i), bool) & np.asarray(mask(i + 1), bool)
            except Exception:  # noqa: BLE001 - no mask: the whole frame
                m = None
        return _change(get(i), get(i + 1), m)
    x = ch(a)
    if not x < REPEAT_EPS:
        return False
    sides = [v for v in (ch(a - 1), ch(a + 1)) if np.isfinite(v)]
    return bool(sides) and x < REPEAT_REL * min(sides)


def cadence(k: int, j: int, jb: int, shown: dict[int, tuple[int, float, float]], pair_label: Any,
            same_picture: Any = None) -> str:
    """Why frame k -- showing RAW j where RAW jb fits better -- is the repeat cadence ('' when it is not).
    ``shown``: k -> (RAW frame, blend fraction, speed) of the edit; ``pair_label``: the competitor pairs (k, k + 1)
    -- an array of refine's labels, or a function of k (verify: refine's, else measured at full resolution) --: 1 the
    same picture, 2 / 3 it moves on; ``same_picture(i, j)``: RAW frames i and j are one picture
    (the RAW file repeats it: a 30 fps file of 25 fps footage repeats every 6th frame). One constant-speed clip
    cannot follow a cadence that is not its own: the competitor shows one picture on two frames where the edit's
    time line steps between them (jb is what the edit shows on the other frame), or the edit shows one picture on two
    frames where the competitor moves on -- one RAW frame twice, or two RAW frames that are one picture (jb is the
    next RAW frame in the direction of play)."""
    def label(a: int) -> int:
        if callable(pair_label):
            return int(pair_label(a))
        return int(pair_label[a]) if 0 <= a < len(pair_label) else -1

    def one(a: int, b: int) -> bool:
        return a == b or (same_picture is not None and abs(a - b) == 1 and bool(same_picture(a, b)))
    if k not in shown or shown[k][1] != 0.0:
        return ""
    step = 1 if shown[k][2] >= 0 else -1
    for q in (k - 1, k + 1):
        if q not in shown or shown[q][1] != 0.0:
            continue
        a = min(q, k)
        if label(a) == 1 and one(shown[q][0], jb):
            return (f"the competitor shows one picture on frames {a}-{a + 1} and the time line steps between them "
                    f"(RAW {shown[q][0]} is shown on frame {q})")
        if label(a) in (2, 3) and one(shown[q][0], j) and jb == j + (step if q < k else -step):
            if shown[q][0] == j:
                return f"the time line shows RAW {j} on frames {a}-{a + 1} where the competitor moves on (to RAW {jb})"
            return (f"the time line shows one picture on frames {a}-{a + 1} (the RAW repeats it on its frames "
                    f"{min(j, shown[q][0])}-{max(j, shown[q][0])}) where the competitor moves on (to RAW {jb})")
    return ""


def _verify_frame(sc: "Scorer", comp_get: Any, raw_get: Any, mask: Any, k: int, j: int, f: float, sim: Sim,
                  flip: bool) -> tuple[float, float, float, dict[int, float]] | None:
    """:func:`verify`'s measurement of competitor frame k as delivered (RAW frame j, or the mix of j and j + 1 by f):
    (the ZNCC at the segment's framing, the refined ZNCC, how far the refinement moves the framing, the neighbouring
    RAW frames' ZNCC at the refined framing); None when a frame cannot be read."""
    img = comp_get(k)
    r = raw_get(j)
    if img is None or r is None:
        return None
    prep = sc.comp(k, img, mask)
    if prep is None:
        return None
    A = sc.inverse_map(sim, bool(flip))
    if f > 0.0 and raw_get(j + 1) is not None:
        mix = (1.0 - f) * r.astype(np.float32) + f * raw_get(j + 1).astype(np.float32)
        rr = sc.raw(-(j + 1) * 1000 - int(round(f * 999)), np.clip(np.rint(mix), 0, 255).astype(np.uint8))
    else:
        rr = sc.raw(j, r)
    p, z_ref, z_model = sc.refine(prep, rr, A, start=True)      # z_model: the score at A (refine's start)
    shift = sc.shift_px(p, prep[2])
    z_nb = {}
    for jj in (j - 1, j + 1):
        rj = raw_get(jj)
        if rj is not None and f == 0.0:
            z_nb[jj] = sc.score(prep, sc.raw(jj, rj), A, p)
    return z_model, z_ref, shift, z_nb


def _verify_parallel(shown: dict, comp_info: Any, raw_info: Any, allowed: Any, raw_wh: tuple[float, float],
                     n_raw: int, workers: int) -> dict | None:
    """:func:`_verify_frame` of every shown frame in ``workers`` processes sharing the GPU; None when they fail (the
    caller then measures in this process: the same numbers)."""
    pool = None
    try:
        pool = GpuPool(workers, comp_info, raw_info, raw_wh, n_raw)
        ks = list(shown)
        res = pool.run([("vframe", int(k), int(shown[k][1]), float(shown[k][2]), shown[k][3],
                         bool(shown[k][0].flip_h)) for k in ks], allowed)
        return dict(zip(ks, res))
    except Exception as e:  # noqa: BLE001
        log.warning("9.9: the %d GPU processes failed (%s: %s); measuring in this process", workers, type(e).__name__, e)
        return None
    finally:
        if pool is not None:
            pool.close()


def verify(segments: Sequence[Any], n_comp: int, comp_store: FrameStore, raw_store: FrameStore, allowed: Any,
           raw_wh: tuple[float, float], comp_fps: Fraction, raw_fps: Fraction, n_raw: int,
           pair_label: np.ndarray | None = None, workers: int = 0) -> dict:
    """Every frame each RAW segment shows and every cut between two RAW segments, at full resolution (module
    docstring). ``pair_label``: refine's competitor pairs (k, k + 1): 1 the same picture, 2 / 3 it moves on. A
    neighbouring RAW frame fitting better is EXPLAINED only by the repeat cadence -- one constant-speed clip cannot
    follow a cadence that is not its own: the competitor shows one picture on two frames where the edit's time line
    steps between them (the better RAW frame is the one the edit shows on the other frame), or the edit shows one
    RAW frame on two frames where the competitor moves on (the better RAW frame is the next one in the direction of
    play); a cut is explained when the competitor repeats a picture across it. Every other better-fitting neighbour
    is listed (beyond VERIFY_FAIL a failure). ``workers`` > 1: the frames are scored in that many processes sharing
    the GPU, with the same numbers (Task 9). Returns {status, summary, failures, frames, cuts, explained, rows}."""
    from .verify import seg_shown, seg_sim, single_raw_segments
    t0 = time.time()
    seg_at = single_raw_segments(segments, int(n_comp))
    shown: dict[int, tuple[Any, int, float, Sim]] = {}
    for k in sorted(seg_at):
        s = seg_at[k]
        sh = seg_shown(s, int(k), comp_fps, raw_fps, n_raw)
        sim = seg_sim(s, int(k), *raw_wh)
        if sh is None or sim is None:
            continue
        shown[int(k)] = (s, int(sh[0]), float(sh[1]), sim)
    # cuts between two RAW segments next to each other
    cuts = []
    for k in sorted(shown):
        if k - 1 in shown and shown[k - 1][0] is not shown[k][0]:
            cuts.append(k)
    other: dict[tuple[int, int], tuple[int, Sim]] = {}
    for c in cuts:
        for k, s in ((c - 1, shown[c][0]), (c, shown[c - 1][0])):      # each side under the other's model
            sh = seg_shown(s, int(k), comp_fps, raw_fps, n_raw)
            sim = seg_sim(s, int(k), *raw_wh)
            if sh is not None and sim is not None:
                other[(k, int(s.id))] = (int(sh[0]), sim)
    need = set()
    for k, (_s, j, f, _sim) in shown.items():
        need.update(x for x in (j - 1, j, j + 1, j + 2 if f > 0 else j) if 0 <= x < n_raw)
    need.update(j for j, _ in other.values() if 0 <= j < n_raw)
    sc = Scorer(raw_wh)
    rows: list[dict] = []
    scores, shifts = [], []
    neighbour_better, failures, explained, pending = [], [], [], []
    pl = np.asarray(pair_label if pair_label is not None else np.zeros(0), dtype=np.int64)
    plain = {k: (j, f, float(getattr(s, "speed", 1.0) or 1.0)) for k, (s, j, f, _sim) in shown.items()}
    try:
        got = None
        if int(workers) > 1 and len(shown) >= PREFETCH_MIN and getattr(comp_store, "info", None) is not None \
                and getattr(raw_store, "info", None) is not None:
            got = _verify_parallel(shown, comp_store.info, raw_store.info, allowed, raw_wh, n_raw, int(workers))
        if got is None:
            comp_store.load(shown)
            raw_store.load(need)
            got = {k: _verify_frame(sc, comp_store.get, raw_store.get, allowed(k), k, j, f, sim, bool(s.flip_h))
                   for k, (s, j, f, sim) in shown.items()}
        else:                                      # the cuts' frames only (each frame was scored in a GPU process)
            comp_store.load({k for c in cuts for k in (c - 1, c)})
            raw_store.load({shown[k][1] for c in cuts for k in (c - 1, c)} | {j for j, _ in other.values()})
        for k, (s, j, f, sim) in shown.items():
            if got.get(k) is None:
                continue
            z_model, z_ref, shift, z_nb = got[k]
            row = {"k": int(k), "segment": int(s.id), "raw": int(j), "zncc": round(z_model, 5),
                   "zncc_refined": round(z_ref, 5), "framing_px": round(shift, 2),
                   "neighbours": {str(a): round(b, 5) for a, b in z_nb.items()}}
            if np.isfinite(z_model):
                scores.append(z_model)
                shifts.append(shift)
            nb = max(z_nb.values(), default=float("nan"))
            if np.isfinite(nb) and np.isfinite(z_ref) and nb > z_ref + VERIFY_NOISE:
                row["neighbour_better"] = round(nb - z_ref, 5)
                row["better_raw"] = int(max(z_nb, key=z_nb.get))
                pending.append(row)
            rows.append(row)
        same: dict[tuple[int, int], bool] = {}

        def same_picture(a: int, b: int) -> bool:          # the RAW file repeats a picture (full resolution)
            key = (min(a, b), max(a, b))
            if key not in same:
                raw_store.load(range(key[0] - 1, key[0] + 3))
                same[key] = _repeat(raw_store.get, key[0])
            return same[key]
        measured: dict[int, int] = {}

        def pair_of(a: int) -> int:                          # refine's label of the competitor pair (a, a + 1); one it
            lab = int(pl[a]) if 0 <= a < len(pl) else -1     # did not measure, measured here at full resolution
            if lab in (1, 2, 3):
                return lab
            if a not in measured:
                comp_store.load(range(a - 1, a + 3))
                if comp_store.get(a) is None or comp_store.get(a + 1) is None:
                    measured[a] = lab
                else:
                    measured[a] = 1 if _repeat(comp_store.get, a, allowed) else 2
            return measured[a]
        for row in pending:
            k, jb = row["k"], row["better_raw"]
            why = cadence(k, row["raw"], jb, plain, pair_of, same_picture)
            if why:
                row["explained"] = why
                explained.append(row)
            else:
                neighbour_better.append(row)
                if row["neighbour_better"] > VERIFY_FAIL:
                    failures.append(f"frame {k} (S{row['segment']:02d}): RAW {jb} fits better than the shown RAW "
                                    f"{row['raw']} by {row['neighbour_better']:.4f} at full resolution")
        cut_rows = []
        for c in cuts:
            for k, s_own, s_other in ((c - 1, shown[c - 1][0], shown[c][0]), (c, shown[c][0], shown[c - 1][0])):
                o = other.get((k, int(s_other.id)))
                img = comp_store.get(k)
                if o is None or img is None:
                    continue
                prep = sc.comp(k, img, allowed(k))
                j_own = shown[k][1]
                if prep is None or raw_store.get(j_own) is None or raw_store.get(o[0]) is None:
                    continue
                z_own = sc.score(prep, sc.raw(j_own, raw_store.get(j_own)), sc.inverse_map(shown[k][3], s_own.flip_h))
                z_oth = sc.score(prep, sc.raw(o[0], raw_store.get(o[0])), sc.inverse_map(o[1], s_other.flip_h))
                cr = {"cut": int(c), "frame": int(k), "own": round(z_own, 5), "other": round(z_oth, 5)}
                cut_rows.append(cr)
                if np.isfinite(z_own) and np.isfinite(z_oth) and z_oth > z_own + VERIFY_FAIL:
                    if 0 <= int(c) - 1 < len(pl) and int(pl[int(c) - 1]) == 1:
                        cr["explained"] = "the competitor repeats a picture across the cut"
                        explained.append(cr)
                    else:
                        failures.append(f"cut at frame {c}: frame {k} fits the other side's model better by "
                                        f"{z_oth - z_own:.4f} at full resolution")
    finally:
        sc.close()
    status = "fail" if failures else ("pass_with_exceptions" if neighbour_better or explained else "pass")
    sc_arr = np.asarray(scores, float)
    summary = (f"{len(rows)} frames and {len(cuts)} cuts at full resolution on the GPU"
               + (f": ZNCC min {sc_arr.min():.4f}, median {np.median(sc_arr):.4f}; framing within "
                  f"{max(shifts):.1f} px" if len(sc_arr) else "")
               + (f"; {len(explained)} explained by the repeat cadence" if explained else "")
               + (f"; {len(neighbour_better)} frame(s) where a neighbouring RAW frame fits a little better"
                  if neighbour_better else "") + (f"; {len(failures)} failure(s)" if failures else ""))
    return {"status": status, "summary": summary, "failures": failures, "frames": len(rows), "cuts": len(cuts),
            "explained": explained[:50], "neighbour_better": neighbour_better[:50], "cut_rows": cut_rows,
            "rows": rows, "seconds": round(time.time() - t0, 1)}
