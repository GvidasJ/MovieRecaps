"""gpu.py: the GPU (CUDA, through PyTorch) where it helps the cut matching -- exact nearest-neighbour search for the
RAW index, and warping / masked ZNCC at full resolution (fullres.py). Everything here has a CPU fallback in its
caller; nothing here changes a decision rule, only where (and how exactly) the numbers are computed.

Exactness: SIFT descriptors are integers 0..255 (any uint8 vectors: 128 x 255^2 < 2^24), so every squared norm, dot
product and partial sum is an integer below 2^24. The index is kept in float16 (every integer up to 2048 exactly) and
multiplied on the tensor cores with float32 accumulation and output (``out_dtype``): each product is exact and so is
every partial sum, in any summation order. The GPU search therefore returns the TRUE k nearest neighbours (FLANN's
kd-trees return approximate ones: on a real index only ~60 % of their first neighbours are the true one), with the
squared distances FLANN would report for them; equal distances are ordered by index (deterministic).

Two-stage selection (exact): each block's row of distances is cut into groups of GROUP columns, and the k groups
with the smallest minimum -- ordered by (minimum, group), which is the order of their smallest members -- hold the
block's k nearest: a group holding one of them has its minimum at or before it, and every member of any other group
comes after all k of them. Only those k groups' members are ranked; topk over every column cost more than the
distances themselves.

Memory: every block of work stays within BLOCK_BYTES of GPU memory -- on Windows a CUDA allocation past the card's
memory spills into shared system memory over PCIe and runs tens of times slower.
"""
from __future__ import annotations

import os
from typing import Sequence

import numpy as np

BLOCK_BYTES = 512 << 20     # one distance block (queries x index rows, float32) at most this big
CHUNK_ROWS = 1 << 18        # index rows per block
GROUP = 64                  # columns per group of the two-stage selection
_PAD = float(1 << 25)       # a padding column's distance: after every real one (|value| < 2^24)
_STATE: dict = {}


def available() -> str | None:
    """None when a CUDA GPU can be used here, else why not (MATCH_CUTS_NO_GPU=1 turns it off)."""
    if os.environ.get("MATCH_CUTS_NO_GPU"):
        return "turned off (MATCH_CUTS_NO_GPU)"
    if "ok" in _STATE:
        return _STATE["ok"]
    try:
        import torch
        why = None if torch.cuda.is_available() else "no CUDA device"
    except Exception as e:  # noqa: BLE001
        why = f"PyTorch not usable ({type(e).__name__}: {e})"
    _STATE["ok"] = why
    return why


def device_name() -> str:
    try:
        import torch
        return torch.cuda.get_device_name(0)
    except Exception:  # noqa: BLE001
        return ""


def _exact_fp32() -> None:
    import torch
    torch.backends.cuda.matmul.allow_tf32 = False
    torch.backends.cudnn.allow_tf32 = False


class KnnIndex:
    """The RAW index's descriptors on the GPU (float16: every uint8 value exactly) for exact k-nearest-neighbour
    queries (squared L2)."""

    def __init__(self, desc_u8: np.ndarray, chunk: int = CHUNK_ROWS, block_bytes: int = BLOCK_BYTES,
                 group: int = GROUP):
        import torch
        _exact_fp32()
        self.dev = torch.device("cuda")
        self.n = int(len(desc_u8))
        self.group = max(1, int(group))
        self.chunk = max(1, min(int(chunk), self.n))
        self.batch = max(16, int(block_bytes) // (4 * self.chunk))
        self.x = torch.empty((self.n, 128), dtype=torch.float16, device=self.dev)            # [N, 128]
        self.xn = torch.empty(self.n, dtype=torch.float32, device=self.dev)                 # [N] exact integers
        for c in range(0, self.n, self.chunk):                       # uploaded in pieces: no float copy of it all
            xc = torch.from_numpy(np.ascontiguousarray(desc_u8[c:c + self.chunk])).to(self.dev)
            self.x[c:c + len(xc)] = xc.half()
            self.xn[c:c + len(xc)] = (xc.float() ** 2).sum(1)

    def search(self, queries: Sequence[np.ndarray], k: int) -> list[tuple[np.ndarray, np.ndarray]]:
        """For each query set (SIFT values: integers 0..255 [m, 128]): (indices int64 [m, k], squared distances
        float32 [m, k]), nearest first (equal distances: lower index first) -- like cv2.flann_Index.knnSearch, but
        exact. Two-stage selection per block (module docstring): the k groups of ``group`` columns with the smallest
        minimum, their members ranked by (distance, index)."""
        import torch
        k = int(min(k, self.n))
        sizes = [len(q) for q in queries]
        if not sizes or sum(sizes) == 0 or k <= 0:
            return [(np.zeros((s, max(k, 0)), np.int64), np.zeros((s, max(k, 0)), np.float32)) for s in sizes]
        q_all = np.concatenate([np.asarray(q, np.float32).reshape(-1, 128) for q in queries])
        if not (np.all(q_all >= 0) and np.all(q_all <= 255) and np.array_equal(q_all, np.round(q_all))):
            raise ValueError("KnnIndex.search: the queries must be SIFT values (integers 0..255) to be searched exactly")
        out_i = np.empty((len(q_all), k), np.int64)
        out_d = np.empty((len(q_all), k), np.float32)
        G, n = self.group, self.n
        shift = 1 << 25                                   # |x|^2 - 2 q.x > -2^24: shifted, every value is positive
        with torch.inference_mode():
            members = torch.arange(G, device=self.dev)
            for a in range(0, len(q_all), self.batch):
                q = torch.from_numpy(q_all[a:a + self.batch]).to(self.dev)
                qh = q.half()
                B = len(q)
                vals, idxs = [], []
                for c in range(0, n, self.chunk):
                    w = min(self.chunk, n - c)
                    kk = min(k, w)
                    # |x|^2 - 2 q.x ranks like |q - x|^2 (|q|^2 is the same along a row): one fused block, exact
                    d = torch.addmm(self.xn[c:c + w].unsqueeze(0), qh, self.x[c:c + w].T, beta=1.0, alpha=-2.0,
                                    out_dtype=torch.float32)
                    if w >= 2 * kk * G:                   # stage 1: the kk groups with the smallest minimum
                        ng = -(-w // G)
                        if ng * G != w:
                            d = torch.nn.functional.pad(d, (0, ng * G - w), value=_PAD)
                        gmin = d.view(B, ng, G).amin(2)
                        gkey = (gmin.long() + shift) * ng + torch.arange(ng, device=self.dev)
                        sel = torch.topk(gkey, kk, dim=1, largest=False, sorted=False).indices
                        cols = (sel.unsqueeze(2) * G + members).reshape(B, kk * G)
                        v = d.gather(1, cols)
                    else:
                        cols = torch.arange(w, device=self.dev).expand(B, w)
                        v = d
                    key = (v.long() + shift) * (n + 1) + (cols + c)      # stage 2: distance first, then index
                    s = torch.topk(key, kk, dim=1, largest=False, sorted=False).indices
                    vals.append(v.gather(1, s))
                    idxs.append(cols.gather(1, s) + c)
                    del d
                v = torch.cat(vals, 1)
                i = torch.cat(idxs, 1)
                key = (v.long() + shift) * (n + 1) + i
                sel = torch.topk(key, k, dim=1, largest=False, sorted=True).indices
                v = torch.gather(v, 1, sel)
                i = torch.gather(i, 1, sel)
                qn = (q * q).sum(1, keepdim=True)
                out_i[a:a + B] = i.cpu().numpy()
                out_d[a:a + B] = torch.clamp(v + qn, min=0.0).cpu().numpy()
        res, p = [], 0
        for s in sizes:
            res.append((out_i[p:p + s], out_d[p:p + s]))
            p += s
        return res

    def close(self) -> None:
        self.x = self.xn = None
        try:
            import torch
            torch.cuda.empty_cache()
        except Exception:  # noqa: BLE001
            pass
