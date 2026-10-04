"""gpu.py: the GPU (CUDA, through PyTorch) where it helps the cut matching -- exact nearest-neighbour search for the
RAW index, and warping / masked ZNCC at full resolution (fullres.py). Everything here has a CPU fallback in its
caller; nothing here changes a decision rule, only where (and how exactly) the numbers are computed.

Exactness: SIFT descriptors are integers 0..255 with an L2 norm of about 512, so every squared norm and dot product
is an integer below 2^24 -- computed in float32 with TF32 off (cuBLAS accumulates in float32), each is exact, in any
summation order. The GPU search therefore returns the TRUE k nearest neighbours (FLANN's kd-trees return approximate
ones: on a real index only ~60 % of their first neighbours are the true one), with the squared distances FLANN would
report for them; equal distances are ordered by index (deterministic).

Memory: every block of work stays within BLOCK_BYTES of GPU memory -- on Windows a CUDA allocation past the card's
memory spills into shared system memory over PCIe and runs tens of times slower.
"""
from __future__ import annotations

import os
from typing import Sequence

import numpy as np

BLOCK_BYTES = 512 << 20     # one distance block (queries x index rows, float32) at most this big
CHUNK_ROWS = 1 << 18        # index rows per block
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
    """The RAW index's descriptors on the GPU (float32) for exact k-nearest-neighbour queries (squared L2)."""

    def __init__(self, desc_u8: np.ndarray, chunk: int = CHUNK_ROWS, block_bytes: int = BLOCK_BYTES):
        import torch
        _exact_fp32()
        self.dev = torch.device("cuda")
        self.n = int(len(desc_u8))
        self.chunk = max(1, min(int(chunk), self.n))
        self.batch = max(16, int(block_bytes) // (4 * self.chunk))
        self.x = torch.from_numpy(np.ascontiguousarray(desc_u8)).to(self.dev).float()    # [N, 128]
        self.xn = (self.x * self.x).sum(1)                                                  # [N] exact integers

    def search(self, queries: Sequence[np.ndarray], k: int) -> list[tuple[np.ndarray, np.ndarray]]:
        """For each query set (uint8 / float [m, 128]): (indices int64 [m, k], squared distances float32 [m, k]),
        nearest first (equal distances: lower index first) -- like cv2.flann_Index.knnSearch, but exact."""
        import torch
        k = int(min(k, self.n))
        sizes = [len(q) for q in queries]
        if not sizes or sum(sizes) == 0 or k <= 0:
            return [(np.zeros((s, max(k, 0)), np.int64), np.zeros((s, max(k, 0)), np.float32)) for s in sizes]
        q_all = np.concatenate([np.asarray(q, np.float32).reshape(-1, 128) for q in queries])
        out_i = np.empty((len(q_all), k), np.int64)
        out_d = np.empty((len(q_all), k), np.float32)
        shift = 1 << 21                                   # makes |x|^2 - 2 q.x (>= -|q|^2 > -2^20) positive
        with torch.inference_mode():
            for a in range(0, len(q_all), self.batch):
                q = torch.from_numpy(q_all[a:a + self.batch]).to(self.dev)
                vals, idxs = [], []
                for c in range(0, self.n, self.chunk):
                    xc = self.x[c:c + self.chunk]
                    # |x|^2 - 2 q.x ranks like |q - x|^2 (|q|^2 is the same along a row): one fused block
                    d = torch.addmm(self.xn[c:c + self.chunk].unsqueeze(0), q, xc.T, beta=1.0, alpha=-2.0)
                    v, i = torch.topk(d, min(k, d.shape[1]), dim=1, largest=False, sorted=False)
                    vals.append(v)
                    idxs.append(i + c)
                    del d
                v = torch.cat(vals, 1)
                i = torch.cat(idxs, 1)
                key = (v.round().long() + shift) * (self.n + 1) + i          # distance first, then index
                sel = torch.topk(key, k, dim=1, largest=False, sorted=True).indices
                v = torch.gather(v, 1, sel)
                i = torch.gather(i, 1, sel)
                qn = (q * q).sum(1, keepdim=True)
                out_i[a:a + len(q)] = i.cpu().numpy()
                out_d[a:a + len(q)] = torch.clamp(v + qn, min=0.0).cpu().numpy()
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
