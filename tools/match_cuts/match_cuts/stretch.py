"""stretch.py: a competitor that stretched the RAW's picture sideways (night 15, task 3).

The laptop's American Dad S03E02 run (a 4:3 HDTV episode, 1440 x 1080) found 94 % of the Instagram reel's sound
(RAW 292.7-352.9 s) and 0 % of its picture after three searches and 2 hours: the reel shows the 4:3 picture pulled
out to 16:9 -- 1.32 times wider than high. The picture search fits one zoom (the same on both axes), a position and a
rotation, so no fit can line such a frame up with the RAW (the best: "rotated 5 degrees, scaled 0.79", ZNCC 0.735,
under the 0.90 needed). Cartoons and old sitcoms are often 4:3, and clip channels often stretch them.

The check (seconds, CPU, right after S1's pair check): where the pair check's sound placed the competitor in the RAW
(its pieces found at the normal speed), a few competitor frames and the RAW frames under them are matched with SIFT
and a FULL affine (one zoom per axis) is fitted; when the frames agree on a sideways stretch (``detect``: at least
MIN_FRAMES frames, MIN_INLIERS agreeing points each, their width/height ratios within AGREE of one another and at
least MIN_STRETCH away from 1, a small rotation), the stretch is snapped to a usual one (4:3 -> 16:9 is 4/3) and the
run gives the RAW that pixel aspect (pipeline.stage_probe_conform): S2 converts it once (on the graphics card) into a
picture as wide as the competitor shows it, the whole analysis runs on that copy, and V1 plays it (edit_media's
checked copy) -- so every clip is framed as in the competitor. ``--raw-stretch off`` never does it, ``--raw-stretch
4:3`` (or any w:h) forces one.

A piece of the movie (excerpt.py) is not cut when the RAW is stretched (V1 plays the stretched copy of the whole RAW).
"""
from __future__ import annotations

import subprocess
from fractions import Fraction
from typing import Any

import numpy as np

from .common import ffmpeg_bin, log

MIN_FRAMES = 3              # frames that must agree on the stretch
MIN_INLIERS = 20            # RANSAC inliers of a frame's affine fit
AGREE = 0.06                # the frames' width / height ratios within this (relative) of their median
MIN_STRETCH = 0.10          # a stretch under 10 % is no stretch (zoom noise, letterboxing rounding)
MAX_ROT_DEG = 3.0           # a real rotation is the similarity search's own business
USUAL = (Fraction(4, 3), Fraction(3, 4))   # 4:3 pulled out to 16:9, and 16:9 squeezed into 4:3
SNAP = 0.04                 # a measured stretch within 4 % of a usual one is that one
MAX_FRAMES = 8              # competitor frames tried at most
RAW_NEIGHBOURS = (-0.25, -0.125, 0.0, 0.125, 0.25)   # RAW seconds around the sound's place (the best frame wins)
FRAME_W = 960               # px: the frames are compared at this width (the competitor's), the RAW's at its own aspect
TIMEOUT_S = 30.0


def parse(text: Any) -> Fraction | None | str:
    """--raw-stretch: 'auto' (the default), 'off', or 'w:h' / a number (the RAW's pixels that many times wider)."""
    t = str(text or "auto").strip().lower()
    if t in ("auto", ""):
        return "auto"
    if t in ("off", "no", "none", "0", "1", "1:1"):
        return None
    try:
        if ":" in t:
            a, b = t.split(":", 1)
            v = Fraction(int(a), int(b))
        else:
            v = Fraction(t).limit_denominator(100)
    except (ValueError, ZeroDivisionError):
        raise ValueError(f"--raw-stretch must be auto, off or w:h (e.g. 4:3), not {text!r}")
    if v <= 0:
        raise ValueError(f"--raw-stretch must be positive, not {text!r}")
    return v


def _frame(path: str, t: float, w: int) -> np.ndarray | None:
    """One gray frame at ``t`` s, ``w`` px wide (its display aspect kept)."""
    cmd = [ffmpeg_bin(), "-v", "error", "-ss", f"{max(0.0, t):.3f}", "-i", str(path), "-frames:v", "1",
           "-vf", f"scale={w}:-2:flags=area,format=gray", "-f", "image2pipe", "-vcodec", "png", "-"]
    try:
        out = subprocess.run(cmd, capture_output=True, timeout=TIMEOUT_S).stdout
    except (OSError, subprocess.TimeoutExpired):
        return None
    if not out:
        return None
    import cv2
    img = cv2.imdecode(np.frombuffer(out, np.uint8), cv2.IMREAD_GRAYSCALE)
    return img


def fit(comp: np.ndarray, raw: np.ndarray, sift: Any = None) -> dict | None:
    """The full affine RAW -> competitor of one pair of frames: {'sx', 'sy', 'ratio' (sx / sy), 'rot' (degrees),
    'inliers'}; None without enough points."""
    import cv2
    sift = sift or cv2.SIFT_create(3000)
    kc, dc = sift.detectAndCompute(comp, None)
    kr, dr = sift.detectAndCompute(raw, None)
    if dc is None or dr is None or len(kc) < 8 or len(kr) < 8:
        return None
    pairs = cv2.BFMatcher().knnMatch(dr, dc, k=2)
    good = [p[0] for p in pairs if len(p) == 2 and p[0].distance < 0.75 * p[1].distance]
    if len(good) < 8:
        return None
    src = np.float32([kr[m.queryIdx].pt for m in good])
    dst = np.float32([kc[m.trainIdx].pt for m in good])
    cv2.setRNGSeed(15)
    A, inl = cv2.estimateAffine2D(src, dst, ransacReprojThreshold=3.0, maxIters=4000, confidence=0.995)
    if A is None or inl is None:
        return None
    sx, sy = float(np.hypot(A[0, 0], A[1, 0])), float(np.hypot(A[0, 1], A[1, 1]))
    if sx <= 1e-6 or sy <= 1e-6:
        return None
    return {"sx": round(sx, 4), "sy": round(sy, 4), "ratio": round(sx / sy, 4),
            "rot": round(float(np.degrees(np.arctan2(A[1, 0], A[0, 0]))), 2), "inliers": int(inl.sum())}


def places(pair: Any) -> list[tuple[float, float]]:
    """(competitor s, RAW s) of the middle of each sound piece the pair check found at the normal speed."""
    s = getattr(pair, "sound", None)
    det = getattr(s, "details", None) or {}
    out = []
    for p in (det.get("tried") or {}).get("1.00") or []:
        if p.get("found"):
            out.append((float(p["at"]) + 1.5, float(p["raw_at"]) + 1.5))
    return out


def snap(r: float) -> Fraction:
    for u in USUAL:
        if abs(r / float(u) - 1.0) <= SNAP:
            return u
    return Fraction(r).limit_denominator(48)


def detect(comp_path: str, raw_path: str, pair: Any, raw_aspect: float | None = None) -> dict:
    """Does the competitor show the RAW stretched sideways (module docstring)? {'stretch': Fraction | None, 'why',
    'frames': [per-frame fits]} -- never raises."""
    out: dict = {"stretch": None, "why": "", "frames": []}
    try:
        pl = places(pair)
        if not pl:
            out["why"] = "the sound did not place the competitor in the RAW"
            return out
        import cv2
        sift = cv2.SIFT_create(3000)
        idx = np.unique(np.round(np.linspace(0, len(pl) - 1, min(MAX_FRAMES, len(pl)))).astype(int))
        for i in idx:
            tc, tr = pl[int(i)]
            c = _frame(comp_path, tc, FRAME_W)
            if c is None:
                continue
            best = None
            for d in RAW_NEIGHBOURS:
                r = _frame(raw_path, tr + d, FRAME_W)
                if r is None:
                    continue
                f = fit(c, r, sift)
                if f is not None and (best is None or f["inliers"] > best["inliers"]):
                    best = {**f, "comp_s": round(tc, 2), "raw_s": round(tr + d, 3)}
            if best is not None:
                out["frames"].append(best)
        ok = [f for f in out["frames"] if f["inliers"] >= MIN_INLIERS and abs(f["rot"]) <= MAX_ROT_DEG]
        if len(ok) < MIN_FRAMES:
            out["why"] = f"only {len(ok)} frames fitted well enough (at least {MIN_FRAMES} needed)"
            return out
        med = float(np.median([f["ratio"] for f in ok]))
        agree = [f for f in ok if abs(f["ratio"] / med - 1.0) <= AGREE]
        if len(agree) < MIN_FRAMES or len(agree) < 0.6 * len(ok):
            out["why"] = f"the frames do not agree on one width / height ratio (median {med:.3f})"
            return out
        if abs(med - 1.0) < MIN_STRETCH:
            out["why"] = f"not stretched (width / height {med:.3f})"
            out["ratio"] = round(med, 4)
            return out
        out["stretch"] = snap(med)
        out["ratio"] = round(med, 4)
        out["why"] = (f"{len(agree)} of {len(ok)} frames show the RAW {med:.3f} times as wide as high -> pixel aspect "
                      f"{out['stretch'].numerator}:{out['stretch'].denominator}")
        return out
    except Exception as e:  # noqa: BLE001 - the run goes on as before
        out["why"] = f"could not check ({type(e).__name__}: {e})"
        return out


def apply_to(info: Any, stretch: Fraction) -> Any:
    """The RAW's probe info with its pixels ``stretch`` times wider (S2 converts it to square pixels: a picture as wide
    as the competitor shows it)."""
    import dataclasses
    from .probe import display_geometry
    sar = Fraction(info.sar or 1) * Fraction(stretch)
    dw, dh, _ = display_geometry(info.width, info.height, info.rotation, sar)
    kw = {"sar": sar, "display_width": dw, "display_height": dh, "dar": Fraction(dw, dh)}
    issue = f"sar: stretched {float(stretch):.3f}x sideways as the competitor shows it (night 15)"
    if hasattr(info, "ae_issues"):
        kw["ae_issues"] = [i for i in (info.ae_issues or []) if not str(i).startswith("sar:")] + [issue]
    return dataclasses.replace(info, **kw)


def check(ctx: Any) -> Fraction | None:
    """S1's stretch check (pipeline): the stretch the RAW gets, or None; logged and written to the decision log."""
    cfg = ctx.cfg
    want = parse(getattr(cfg, "raw_stretch", "auto"))
    if want is None:
        return None
    if want != "auto":
        log.info("stretch: the RAW's pixels %s times wider (--raw-stretch)", f"{float(want):.3f}")
        return want
    if getattr(cfg, "force_inputs", False) and getattr(ctx, "pair", None) is None:
        return None
    res = detect(str(cfg.competitor), str(cfg.raw), getattr(ctx, "pair", None))
    log.info("stretch check: %s", res["why"] or "-")
    try:
        ctx.dlog.record("inputs", "stretch_check", stretch=None if res["stretch"] is None else str(res["stretch"]),
                        why=res["why"], frames=res["frames"])
    except Exception:  # noqa: BLE001
        pass
    return res["stretch"]


__all__ = ["parse", "fit", "places", "snap", "detect", "apply_to", "check"]
