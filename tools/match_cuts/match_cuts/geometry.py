"""Coordinate conventions and transform conversions (DESIGN.md §2.2).

CORNER convention (the canonical one, also After Effects'):
    continuous pixel coordinates; pixel (col i, row j) covers [i, i+1) x [j, j+1),
    its centre is (i + 0.5, j + 0.5). Image of width W spans x in [0, W].
CV convention (OpenCV warpAffine / keypoints): pixel centres at integer coordinates,
    p_cv = p_corner - 0.5.

Canonical transform ``Sim`` (stored in cutlist.json):
    p_comp = s * R(theta) * p_src + t          (CORNER convention, FULL-RES pixels)
    where p_src is the RAW pixel *after* horizontal flipping when flip_h is set:
        flip:  x' = W_raw - x,  y' = y
    R(theta) = [[cos, -sin], [sin, cos]] in y-down image coordinates (clockwise-positive on
    screen, identical to After Effects' Rotation and to OpenCV: theta = atan2(M[1,0], M[0,0])).

Proxy images are produced with cv2.resize (corner-aligned for INTER_AREA and INTER_LINEAR),
so a proxy with per-axis ratios (rx, ry) = (w_p / W, h_p / H) maps CORNER coordinates as
    p_proxy = diag(rx, ry) * p_full.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Iterable, Sequence

import numpy as np

# 3x3 homogeneous helpers ---------------------------------------------------------------

CORNER_TO_CV = np.array([[1.0, 0.0, -0.5], [0.0, 1.0, -0.5], [0.0, 0.0, 1.0]])
CV_TO_CORNER = np.array([[1.0, 0.0, 0.5], [0.0, 1.0, 0.5], [0.0, 0.0, 1.0]])


def h3(m: np.ndarray) -> np.ndarray:
    """2x3 -> 3x3 homogeneous."""
    m = np.asarray(m, dtype=np.float64)
    if m.shape == (3, 3):
        return m
    out = np.eye(3)
    out[:2, :] = m
    return out


def diag3(rx: float, ry: float) -> np.ndarray:
    return np.array([[rx, 0.0, 0.0], [0.0, ry, 0.0], [0.0, 0.0, 1.0]])


def translate3(tx: float, ty: float) -> np.ndarray:
    return np.array([[1.0, 0.0, tx], [0.0, 1.0, ty], [0.0, 0.0, 1.0]])


def flip3(width: float) -> np.ndarray:
    """Horizontal flip in CORNER coordinates of an image of the given width: x' = width - x."""
    return np.array([[-1.0, 0.0, width], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]])


# Canonical similarity -------------------------------------------------------------------

@dataclass(frozen=True)
class Sim:
    """Similarity transform  p' = s R(theta) p + t  (CORNER convention)."""

    s: float = 1.0
    theta_deg: float = 0.0
    tx: float = 0.0
    ty: float = 0.0

    # -- construction -------------------------------------------------------------------
    @staticmethod
    def identity() -> "Sim":
        return Sim(1.0, 0.0, 0.0, 0.0)

    @staticmethod
    def from_matrix(m: np.ndarray) -> "Sim":
        """Closest similarity (least squares on the 2x2 part) to an affine 2x3/3x3 matrix.

        Raises ValueError for reflections (det <= 0): a mirrored mapping must be expressed as
        flip_h=True + a proper Sim (DESIGN §2.2), never silently projected (which would give s≈0).
        """
        m = h3(m)
        a, b, c, d = m[0, 0], m[0, 1], m[1, 0], m[1, 1]
        if a * d - b * c <= 0:
            raise ValueError("matrix is a reflection (det <= 0); use flip_h=True with the flipped RAW")
        sc = (a + d) / 2.0   # s cos
        ss = (c - b) / 2.0   # s sin
        s = math.hypot(sc, ss)
        th = math.degrees(math.atan2(ss, sc))
        return Sim(float(s), float(th), float(m[0, 2]), float(m[1, 2]))

    @staticmethod
    def from_dict(d: dict) -> "Sim":
        return Sim(float(d["scale"]), float(d.get("rotation_deg", 0.0)), float(d["tx"]), float(d["ty"]))

    # -- conversion ---------------------------------------------------------------------
    def to_dict(self) -> dict:
        return {"scale": float(self.s), "rotation_deg": float(self.theta_deg),
                "tx": float(self.tx), "ty": float(self.ty)}

    @property
    def theta(self) -> float:
        return math.radians(self.theta_deg)

    def linear(self) -> np.ndarray:
        c, s_ = math.cos(self.theta), math.sin(self.theta)
        return self.s * np.array([[c, -s_], [s_, c]])

    def matrix(self) -> np.ndarray:
        """3x3 homogeneous matrix (CORNER convention)."""
        m = np.eye(3)
        m[:2, :2] = self.linear()
        m[0, 2], m[1, 2] = self.tx, self.ty
        return m

    def m23(self) -> np.ndarray:
        return self.matrix()[:2, :]

    def inverse(self) -> "Sim":
        return Sim.from_matrix(np.linalg.inv(self.matrix()))

    def compose(self, other: "Sim") -> "Sim":
        """self ∘ other (apply other first)."""
        return Sim.from_matrix(self.matrix() @ other.matrix())

    def apply(self, pts: np.ndarray | Sequence[Sequence[float]]) -> np.ndarray:
        p = np.asarray(pts, dtype=np.float64).reshape(-1, 2)
        return p @ self.linear().T + np.array([self.tx, self.ty])

    def translated(self, dx: float, dy: float) -> "Sim":
        return Sim(self.s, self.theta_deg, self.tx + dx, self.ty + dy)


def is_similarity(m: np.ndarray, tol: float = 1e-6) -> bool:
    m = h3(m)
    a, b, c, d = m[0, 0], m[0, 1], m[1, 0], m[1, 1]
    return abs(a - d) <= tol * max(1.0, abs(a)) and abs(b + c) <= tol * max(1.0, abs(a))


# Full-res  <->  proxy / OpenCV ---------------------------------------------------------

def full_to_image_matrix(sim: Sim, flip: bool, raw_w: float,
                         src_ratio: tuple[float, float] = (1.0, 1.0),
                         dst_ratio: tuple[float, float] = (1.0, 1.0)) -> np.ndarray:
    """3x3 CORNER-convention matrix mapping *unflipped* RAW proxy coords -> COMP proxy coords.

    src_ratio = (rx, ry) of the RAW image used (proxy/full), dst_ratio likewise for COMP.
    """
    F = flip3(raw_w) if flip else np.eye(3)
    return diag3(*dst_ratio) @ sim.matrix() @ F @ np.linalg.inv(diag3(*src_ratio))


def to_cv_matrix(sim: Sim, flip: bool, raw_w: float,
                 src_ratio: tuple[float, float] = (1.0, 1.0),
                 dst_ratio: tuple[float, float] = (1.0, 1.0)) -> np.ndarray:
    """2x3 matrix for ``cv2.warpAffine(raw_img, M, (w_dst, h_dst))`` (forward map, no WARP_INVERSE_MAP).

    raw_img: the *unflipped* RAW frame at src_ratio of full res; output at dst_ratio of COMP full res.
    """
    m = CORNER_TO_CV @ full_to_image_matrix(sim, flip, raw_w, src_ratio, dst_ratio) @ CV_TO_CORNER
    return m[:2, :].copy()


def from_cv_matrix(m_cv: np.ndarray, flip: bool, raw_w: float,
                   src_ratio: tuple[float, float] = (1.0, 1.0),
                   dst_ratio: tuple[float, float] = (1.0, 1.0),
                   project: bool = True) -> Sim | np.ndarray:
    """Inverse of :func:`to_cv_matrix`: OpenCV matrix between proxies -> canonical full-res Sim.

    If project=False returns the full-res 3x3 affine (useful to inspect non-similarity residue).
    """
    corner = CV_TO_CORNER @ h3(m_cv) @ CORNER_TO_CV
    F = flip3(raw_w) if flip else np.eye(3)
    full = np.linalg.inv(diag3(*dst_ratio)) @ corner @ diag3(*src_ratio) @ np.linalg.inv(F)
    return Sim.from_matrix(full) if project else full


def warp_raw_to_comp(raw_img: np.ndarray, sim: Sim, flip: bool, raw_w: float,
                     out_size: tuple[int, int],
                     src_ratio: tuple[float, float] = (1.0, 1.0),
                     dst_ratio: tuple[float, float] = (1.0, 1.0),
                     roi: tuple[int, int, int, int] | None = None,
                     interp: int | None = None,
                     border_value: float = 0.0) -> tuple[np.ndarray, np.ndarray]:
    """Warp a RAW image into COMP space. Returns (warped, valid_mask).

    out_size = (w, h) of the full destination image at dst_ratio. If roi=(x, y, w, h) is given
    (destination pixel coords at dst_ratio), only that window is produced (output shape h x w).
    valid_mask marks destination pixels whose source lies inside the RAW frame.
    """
    import cv2

    interp = cv2.INTER_LINEAR if interp is None else interp
    m = to_cv_matrix(sim, flip, raw_w, src_ratio, dst_ratio)
    w, h = out_size
    if roi is not None:
        x0, y0, w, h = roi
        m = (translate3(-x0, -y0) @ h3(m))[:2, :]
    warped = cv2.warpAffine(raw_img, m, (int(w), int(h)), flags=interp,
                            borderMode=cv2.BORDER_CONSTANT, borderValue=border_value)
    ones = np.full(raw_img.shape[:2], 255, np.uint8)
    valid = cv2.warpAffine(ones, m, (int(w), int(h)), flags=cv2.INTER_NEAREST,
                           borderMode=cv2.BORDER_CONSTANT, borderValue=0)
    # erode 1px so bilinear edge pixels that mix in the border are excluded
    valid = cv2.erode(valid, np.ones((3, 3), np.uint8)) > 0
    return warped, valid


# After Effects ------------------------------------------------------------------------

@dataclass(frozen=True)
class AETransform:
    anchor: tuple[float, float]
    scale: tuple[float, float]      # percent, x may be negative for a horizontal flip
    rotation: float                 # degrees, clockwise-positive
    position: tuple[float, float]

    def to_dict(self) -> dict:
        return {"anchor": list(self.anchor), "scale": list(self.scale),
                "rotation": self.rotation, "position": list(self.position)}


def sim_to_ae(sim: Sim, flip: bool, raw_w: float, raw_h: float,
              r: float = 1.0, origin: tuple[float, float] = (0.0, 0.0)) -> AETransform:
    """Canonical Sim (+flip) -> AE layer Anchor/Scale/Rotation/Position (DESIGN.md §2.3).

    r      = target comp pixels / competitor pixels (AE_COMP_SIZE scaling).
    origin = offset subtracted in competitor pixels (the Video Box origin inside the pre-comp).
    """
    c = np.array([raw_w / 2.0, raw_h / 2.0])
    pos = r * (sim.linear() @ c + np.array([sim.tx - origin[0], sim.ty - origin[1]]))
    sx = (-1.0 if flip else 1.0) * 100.0 * sim.s * r
    sy = 100.0 * sim.s * r
    return AETransform((float(c[0]), float(c[1])), (float(sx), float(sy)), float(sim.theta_deg),
                       (float(pos[0]), float(pos[1])))


def ae_to_matrix(ae: AETransform | dict) -> np.ndarray:
    """AE layer transform -> 3x3 CORNER matrix mapping layer (unflipped RAW) px -> comp px.

    p_comp = Position + R(rotation) * diag(sx/100, sy/100) * (p_layer - Anchor)
    """
    if isinstance(ae, dict):
        ae = AETransform(tuple(ae["anchor"]), tuple(ae["scale"]), float(ae["rotation"]), tuple(ae["position"]))
    th = math.radians(ae.rotation)
    R = np.array([[math.cos(th), -math.sin(th)], [math.sin(th), math.cos(th)]])
    S = np.diag([ae.scale[0] / 100.0, ae.scale[1] / 100.0])
    A = R @ S
    m = np.eye(3)
    m[:2, :2] = A
    m[:2, 2] = np.array(ae.position) - A @ np.array(ae.anchor)
    return m


def sim_flip_matrix(sim: Sim, flip: bool, raw_w: float) -> np.ndarray:
    """3x3 CORNER matrix mapping unflipped RAW full-res px -> COMP full-res px."""
    return sim.matrix() @ (flip3(raw_w) if flip else np.eye(3))


# Misc -----------------------------------------------------------------------------------

def rounded_rect_mask(w: int, h: int, radius: float, ss: int = 4) -> np.ndarray:
    """Anti-aliased rounded-rectangle coverage mask (float32 in [0,1]) of size h x w.

    Coverage is computed with ss x ss super-sampling in CORNER coordinates, so it matches
    an AE mask / shape layer drawn on the box rectangle [0,w] x [0,h].
    """
    r = max(0.0, min(float(radius), w / 2.0, h / 2.0))
    offs = (np.arange(ss) + 0.5) / ss
    xs = (np.arange(w)[:, None] + offs[None, :]).reshape(-1)
    ys = (np.arange(h)[:, None] + offs[None, :]).reshape(-1)
    cx = np.clip(xs, r, w - r)
    cy = np.clip(ys, r, h - r)
    dx = (xs - cx)[None, :]
    dy = (ys - cy)[:, None]
    inside = (dx * dx + dy * dy) <= r * r if r > 0 else np.ones((ys.size, xs.size), bool)
    cov = inside.reshape(h, ss, w, ss).mean(axis=(1, 3)).astype(np.float32)
    return cov


def rdp(points: np.ndarray, tol: np.ndarray | float) -> list[int]:
    """Ramer–Douglas–Peucker simplification on an (N, D) polyline whose first column is time.

    tol may be per-dimension (length D-1, applied to columns 1..) -- a point is kept when any
    normalised deviation from the chord exceeds 1. Returns the kept indices (always 0 and N-1).
    """
    pts = np.asarray(points, dtype=np.float64)
    n = len(pts)
    if n <= 2:
        return list(range(n))
    tol = np.broadcast_to(np.asarray(tol, dtype=np.float64), (pts.shape[1] - 1,))
    keep = np.zeros(n, bool)
    keep[0] = keep[-1] = True
    stack = [(0, n - 1)]
    while stack:
        i0, i1 = stack.pop()
        if i1 <= i0 + 1:
            continue
        t0, t1 = pts[i0, 0], pts[i1, 0]
        seg = pts[i0 + 1:i1]
        u = (seg[:, 0] - t0) / (t1 - t0) if t1 != t0 else np.zeros(len(seg))
        interp = pts[i0, 1:] + u[:, None] * (pts[i1, 1:] - pts[i0, 1:])
        dev = np.max(np.abs(seg[:, 1:] - interp) / tol, axis=1)
        j = int(np.argmax(dev))
        if dev[j] > 1.0:
            idx = i0 + 1 + j
            keep[idx] = True
            stack.append((i0, idx))
            stack.append((idx, i1))
    return [int(i) for i in np.nonzero(keep)[0]]


def interpolate_keys(keys: Iterable[dict], k: float, raw_w: float | None = None,
                     raw_h: float | None = None) -> Sim:
    """Interpolate transform keys [{comp_frame, scale, rotation_deg, tx, ty}] at frame k exactly as AE does.

    Keys are always LINEAR in time and space (the JSX sets linear temporal interpolation and linear
    spatial tangents, DESIGN §5 export_ae); measured easing is reproduced by key density, never by AE
    easy-ease, so preview == AE. AE interpolates Scale, Rotation and Position = s R c + t linearly;
    when rotation is constant between two keys that equals linear (s, tx, ty). When rotation changes,
    the AE-space interpolation is used, which needs the RAW size (raw_w, raw_h) for the anchor c.
    """
    ks = sorted(keys, key=lambda d: d["comp_frame"])
    if not ks:
        raise ValueError("no keys")
    if k <= ks[0]["comp_frame"]:
        return Sim.from_dict(ks[0])
    if k >= ks[-1]["comp_frame"]:
        return Sim.from_dict(ks[-1])
    for a, b in zip(ks[:-1], ks[1:]):
        if a["comp_frame"] <= k <= b["comp_frame"]:
            u = (k - a["comp_frame"]) / (b["comp_frame"] - a["comp_frame"])
            ra, rb = a.get("rotation_deg", 0.0), b.get("rotation_deg", 0.0)
            if abs(ra - rb) > 1e-9:
                if raw_w is None or raw_h is None:
                    raise ValueError("interpolate_keys: rotation varies between keys; pass raw_w/raw_h")
                ea = sim_to_ae(Sim.from_dict(a), False, raw_w, raw_h)
                eb = sim_to_ae(Sim.from_dict(b), False, raw_w, raw_h)
                lerp = lambda p, q: tuple(pi + u * (qi - pi) for pi, qi in zip(p, q))  # noqa: E731
                ae = AETransform(ea.anchor, lerp(ea.scale, eb.scale), ea.rotation + u * (eb.rotation - ea.rotation),
                                 lerp(ea.position, eb.position))
                return Sim.from_matrix(ae_to_matrix(ae))
            return Sim(a["scale"] + u * (b["scale"] - a["scale"]),
                       a.get("rotation_deg", 0.0) + u * (b.get("rotation_deg", 0.0) - a.get("rotation_deg", 0.0)),
                       a["tx"] + u * (b["tx"] - a["tx"]), a["ty"] + u * (b["ty"] - a["ty"]))
    return Sim.from_dict(ks[-1])
