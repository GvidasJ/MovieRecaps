"""picture_retry.py: the competitor's sound is in the RAW but (almost) none of its picture was found (night 8).

On the laptop a --fast run found 100 % of a competitor's sound in its RAW and 0 % of its picture: the layout step had
taken a tiny box (about 211 x 157 px of a 720 x 1280 competitor) for the picture area, the search found 6 anchors on 6
of 368 frames, and the run delivered an edit with no clip as if it were a normal one. The clip was from that episode.

Now, when under PICTURE_RETRY_BELOW of the competitor's frames were matched while at least SOUND_FOUND of its sound
windows were found in the RAW's sound, the picture is searched again (each attempt one more S5.2 + S5.3 pass, with the
box re-measurement against the RAW when it finds something, cached under its own keys):

1. **the layout measured again**: the biggest moving area of the competitor (every pixel whose picture changes, the
   largest connected part), then the whole frame -- each a new layout with its own overlay masks (captions stay masked);
2. **a denser search with a wider zoom range** on the best layout so far: every competitor frame searched, every RAW
   frame indexed where the competitor's sound was found in the RAW (with a margin), DENSE_FEATURES keypoints per frame
   instead of 500 (a small or heavily zoomed picture keeps enough of them), twice the candidates per frame.

Night 15 (cartoons, the laptop's American Dad case: 2 hours of retries, then PICTURE NOT FOUND):

* before the dense search, **comparing lines and shapes (edges)**: the best layout searched again with half of each
  picture score from the edges (grad_weight EDGE_WEIGHT) -- a cartoon's flat colours change with the copy's grading,
  its outlines do not;
* after it, when still under PICTURE_RETRY_BELOW and TRUST_SOUND is on (it is OFF: see TRUST_SOUND),
  **trusting the sound**: the dense search on the RAW where the sound
  was found with picture scores from TRUST_MIN accepted, every match then kept only when it agrees with the sound
  (trust_filter: within TRUST_SLACK_S of the RAW moment the sound puts it at, in forward runs of TRUST_RUN+ frames);
  a run that uses it says so (pipeline: "placed with lower picture scores where the sound agreed -- check");
* **fail fast**: no new attempt starts after RETRY_BUDGET_S of retries (the skipped ones are named in the log).

Every search also tries the mirrored (left-right flipped) picture: visual_match checks the flip hypothesis of every
candidate (the mirrored frame's keypoints vote too), so a mirrored competitor needs no pass of its own.

The attempt that matches the most competitor frames is used (the first pass when none matches more). When even the
best finds under pipeline.NOT_FOUND_PICTURE of the frames the run stops (pipeline.PictureNotFound): "PICTURE NOT
FOUND (sound found) -- the RAW may be a different version (for example credits squeezed at the end), mirrored, or
heavily zoomed" -- instead of delivering an empty edit as if it were a normal one (--force-inputs makes the edit
anyway, with that warning on top).
"""
from __future__ import annotations

import copy
import dataclasses
import math
from typing import Any

import numpy as np

from .common import log
from .model import Box, Proxy, Status

PICTURE_RETRY_BELOW = 0.25  # under this share of the competitor's frames matched in the RAW ...
SOUND_FOUND = 0.50          # ... while at least this share of its sound windows was found there: search again
ENOUGH = 0.80               # an attempt that matches this share of the frames ends the retries
SAME_BOX_IOU = 0.85         # a re-measured box this alike (IoU) to one already tried is not tried again
MOVING_STD = 2.0            # 8-bit temporal std of a moving pixel (layout's static_std_thresh)
MOVING_SAMPLES = 80         # competitor frames sampled for the moving area
DENSE_FEATURES = 2000       # SIFT keypoints per frame in the dense search (the default 500)
WINDOW_PAD_S = 4.0          # RAW seconds indexed before and after each RAW moment the competitor's sound was found at
REFINE_MIN = 0.02           # an attempt matching at least this share gets the box re-measurement against the RAW
# night 15 (task 3, cartoons): three more attempts, only here -- where the sound is found and the picture is not
EDGE_WEIGHT = 0.5           # "lines and shapes": half of each picture score from the edges (gradient magnitude) --
                            # a cartoon's flat colours change with the copy's grading, its outlines do not
TRUST_SOUND = False         # OFF: measured on American Dad (night 15) the frames it keeps sit where the sound says but
                            # not on the right frame (median 1.5 frames off, up to 37: a distorted picture scores ~0.72
                            # on every neighbouring frame) -- 1_edit_placed_by_sound.xml is the honest output there
TRUST_MIN = 0.65            # "trusting the sound": inside the place the sound found, a picture score this high counts
TRUST_RUN = 5               # ... for runs of at least this many neighbouring frames in the order and timing ...
TRUST_SLACK_S = 1.5         # ... each within this of the RAW moment the sound puts it at
RETRY_BUDGET_S = 600.0      # fail fast: no new attempt starts after the retries took this long (the laptop: 2 hours)

NOT_FOUND_HEAD = ("PICTURE NOT FOUND (sound found) -- the RAW may be a different version (for example credits squeezed "
                  "at the end), mirrored, or heavily zoomed")


def matched_share(fm: Any) -> float:
    """The share of the competitor's frames the frame map matched to a RAW frame."""
    st = np.asarray(getattr(fm, "status", ()))
    return float(np.mean(st == Status.MATCH)) if st.size else 0.0


def sound_share(ctx: Any) -> float | None:
    """The share of the competitor's sound windows found in the RAW's sound (S5.1), None without sound."""
    h = getattr(ctx, "hints", None)
    if h is None or not len(getattr(h, "comp_t", ())):
        return None
    return float(np.mean(h.confident(ctx.cfg.audio_min_conf)))


def needs_retry(picture: float, sound: float | None) -> bool:
    """The sound matches but the picture (almost) does not."""
    return sound is not None and sound >= SOUND_FOUND and picture < PICTURE_RETRY_BELOW


# ---------------------------------------------------------------------------------------------------------------------
# the attempts
# ---------------------------------------------------------------------------------------------------------------------

def moving_area(comp: Proxy, samples: int = MOVING_SAMPLES, thresh: float = MOVING_STD
                ) -> tuple[int, int, int, int] | None:
    """The biggest moving area of the competitor proxy: the bounding box (proxy x0, y0, x1, y1) of the largest
    connected part of the pixels whose temporal std over ``samples`` frames spread over the video is at least
    ``thresh`` (after an opening and a closing, as the layout's dynamic mask); None when nothing moves."""
    import cv2
    n = int(comp.n)
    if n < 2:
        return None
    idx = np.unique(np.round(np.linspace(0, n - 1, min(n, samples))).astype(np.int64))
    s1 = s2 = None
    for k in idx:
        f = np.asarray(comp.get(int(k)), np.float64)
        s1 = f if s1 is None else s1 + f
        s2 = f * f if s2 is None else s2 + f * f
    m = s1 / len(idx)
    std = np.sqrt(np.maximum(s2 / len(idx) - m * m, 0.0))
    dyn = (std >= thresh).astype(np.uint8)
    dyn = cv2.morphologyEx(dyn, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
    dyn = cv2.morphologyEx(dyn, cv2.MORPH_CLOSE, np.ones((5, 5), np.uint8))
    cnt, _lab, st, _ = cv2.connectedComponentsWithStats(dyn, connectivity=8)
    if cnt <= 1:
        return None
    i = 1 + int(np.argmax(st[1:, 4]))
    x, y, w, h = (int(v) for v in st[i, :4])
    return x, y, x + w, y + h


def _iou(a: tuple[float, float, float, float], b: tuple[float, float, float, float]) -> float:
    ix = max(0.0, min(a[2], b[2]) - max(a[0], b[0]))
    iy = max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
    inter = ix * iy
    union = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / union if union > 0 else 0.0


def _box_edges(box: Box | None, comp: Proxy) -> tuple[float, float, float, float] | None:
    """A full-res box as proxy edges (x0, y0, x1, y1)."""
    if box is None:
        return None
    rx, ry = float(comp.ratio[0]), float(comp.ratio[1])
    return box.x * rx, box.y * ry, (box.x + box.w) * rx, (box.y + box.h) * ry


def layout_boxes(ctx: Any) -> list[tuple[str, tuple[float, float, float, float]]]:
    """The re-measured layouts to try, as (name, proxy edges): the biggest moving area, then the whole frame -- each
    left out when it is (almost) the box already used or one already listed."""
    comp = ctx.comp_proxy
    w, h = float(comp.size[0]), float(comp.size[1])
    tried = [e for e in (_box_edges(getattr(ctx.layout, "box", None), comp),) if e is not None]
    out: list[tuple[str, tuple[float, float, float, float]]] = []
    mv = moving_area(comp)
    for name, e in (("the biggest moving area", tuple(float(v) for v in mv) if mv else None),
                    ("the whole frame", (0.0, 0.0, w, h))):
        if e is None or (e[2] - e[0]) < 8 or (e[3] - e[1]) < 8:
            continue
        if any(_iou(e, t) >= SAME_BOX_IOU for t in tried):
            continue
        out.append((name, e))
        tried.append(e)
    return out


def forced_layout(ctx: Any, name: str, edges: tuple[float, float, float, float]) -> tuple[Any, Any]:
    """A new layout whose video box is ``edges`` (proxy px): every part that depends on the box measured again
    (layout._analyze with a forced box: background, zones, captions and other overlays, masks)."""
    from . import layout as L
    comp = ctx.comp_proxy
    rx, ry = float(comp.ratio[0]), float(comp.ratio[1])
    x0, y0, x1, y1 = edges
    box = Box(x0 / rx, y0 / ry, (x1 - x0) / rx, (y1 - y0) / ry, 0.0)
    fb = L._ForcedBox(box=box, edges=(float(x0), float(y0), float(x1), float(y1)), radius=0.0, raw_match=None,
                      evidence={"source": f"picture search again: {name}"},
                      notes=[f"picture area measured again ({name}): the competitor's sound was found in the RAW "
                             "but almost none of its picture"])
    return L._analyze(comp, ctx.cfg, ctx.cache, None, ctx.dlog, fb)


def sound_windows(ctx: Any, pad_s: float = WINDOW_PAD_S) -> list[tuple[int, int]]:
    """RAW frame ranges [a, b) around every RAW moment the competitor's sound was found at (merged)."""
    h = ctx.hints
    conf = h.confident(ctx.cfg.audio_min_conf)
    fps = float(ctx.raw_fps)
    n = int(ctx.raw_info.nb_frames)
    pad = pad_s + float(getattr(h, "window", 1.0))
    wins: list[tuple[int, int]] = []
    for t in sorted(float(x) for x in np.asarray(h.raw_t)[conf]):
        a, b = max(0, int(math.floor((t - pad) * fps))), min(n, int(math.ceil((t + pad) * fps)) + 1)
        if b <= a:
            continue
        if wins and a <= wins[-1][1]:
            wins[-1] = (wins[-1][0], max(wins[-1][1], b))
        else:
            wins.append((a, b))
    return wins


def windowed_proxy(raw: Proxy, windows: list[tuple[int, int]]) -> Proxy:
    """A view of ``raw`` that holds only the frames inside ``windows`` (its index_map): the RAW index of the dense
    search covers only where the sound was found, so every frame there can take many keypoints."""
    m = np.full(int(raw.n), -1, np.int32)
    for a, b in windows:
        for j in range(int(a), int(b)):
            if raw.has(j):
                m[j] = j if raw.index_map is None else int(raw.index_map[j])
    return dataclasses.replace(raw, index_map=m)


def dense_cfg(cfg: Any) -> Any:
    """The search settings of the dense attempt: every competitor frame, every RAW frame of the windows indexed,
    DENSE_FEATURES keypoints a frame, twice the candidates and half the vote floor, a wider audio window."""
    c = copy.deepcopy(cfg)
    c.comp_search_stride = 1
    c.sift_nfeatures = max(int(cfg.sift_nfeatures), DENSE_FEATURES)
    c.vote_top_candidates = 2 * int(cfg.vote_top_candidates)
    c.vote_min_frac = float(cfg.vote_min_frac) / 2.0
    c.audio_restrict_s = 2.0 * float(cfg.audio_restrict_s)
    c.raw_index_every_frame = bool(cfg.gpu)
    c.raw_index_fps_short = c.raw_index_fps_long = 1000.0     # without the GPU search: every frame of the windows too
    c.index_max_descriptors = max(int(cfg.index_max_descriptors), 12_000_000 if cfg.gpu else 4_000_000)
    return c


def edge_cfg(cfg: Any) -> Any:
    """The settings of the "lines and shapes" attempt (night 15): each picture score half from the edges."""
    c = copy.deepcopy(cfg)
    c.grad_weight = max(float(cfg.grad_weight), EDGE_WEIGHT)
    return c


def trust_cfg(cfg: Any) -> Any:
    """The settings of the "trusting the sound" attempt (night 15): the dense search's, with a picture score of
    TRUST_MIN accepted (anchors and matches) -- only ever run on the RAW where the sound was found, and every match
    checked against the sound afterwards (trust_filter)."""
    c = dense_cfg(cfg)
    c.match_thresh = TRUST_MIN
    c.anchor_zncc_slack = 0.0
    c.none_thresh = min(float(cfg.none_thresh), TRUST_MIN - 0.05)
    return c


def sound_raw_times(ctx: Any, n: int) -> np.ndarray:
    """For each competitor frame, the RAW second its sound puts it at (the nearest confident sound window's offset;
    NaN without one within two windows)."""
    h = ctx.hints
    conf = h.confident(ctx.cfg.audio_min_conf)
    ct, rt = np.asarray(h.comp_t, np.float64)[conf], np.asarray(h.raw_t, np.float64)[conf]
    out = np.full(n, np.nan)
    if not ct.size:
        return out
    tc = np.arange(n, dtype=np.float64) / float(ctx.comp_fps)
    i = np.abs(tc[:, None] - ct[None, :]).argmin(axis=1)
    near = np.abs(tc - ct[i]) <= 2.0 * float(getattr(h, "window", 1.0)) + 1e-9
    out[near] = tc[near] + (rt[i] - ct[i])[near]
    return out


def trust_filter(ctx: Any, fm: Any) -> int:
    """The matches of the "trusting the sound" attempt kept only where the picture agrees with the sound: the RAW
    frame within TRUST_SLACK_S of the sound's moment, in runs of TRUST_RUN or more neighbouring frames whose RAW frames
    go forward (order and timing); every other match becomes UNRESOLVED. Returns the matches kept."""
    st = np.asarray(fm.status).copy()
    raw = np.asarray(fm.raw, np.float64)
    want = sound_raw_times(ctx, int(fm.n))
    ok = (st == Status.MATCH) & (raw >= 0) & np.isfinite(want) & \
        (np.abs(raw / float(ctx.raw_fps) - want) <= TRUST_SLACK_S)
    keep = np.zeros_like(ok)
    k, n = 0, len(ok)
    while k < n:
        if not ok[k]:
            k += 1
            continue
        e = k
        while e + 1 < n and ok[e + 1] and raw[e + 1] >= raw[e] and raw[e + 1] - raw[e] <= 4.0:
            e += 1
        if e - k + 1 >= TRUST_RUN:
            keep[k:e + 1] = True
        k = e + 1
    st[(st == Status.MATCH) & ~keep] = Status.UNRESOLVED
    fm.status = st
    return int(keep.sum())


_STATE = ("cfg", "layout", "overlays", "fm_pre", "raw_proxy", "anchors", "index")


def _state(ctx: Any) -> dict:
    d = {k: getattr(ctx, k, None) for k in _STATE}
    d["keys"] = dict(ctx.keys)
    return d


def _restore(ctx: Any, st: dict) -> None:
    for k in _STATE:
        setattr(ctx, k, st[k])
    ctx.keys = dict(st["keys"])


def attempt(ctx: Any, name: str, lay: Any, overlays: Any, cfg: Any, base_raw: Any, index_raw: Any = None,
            extra: tuple = ()) -> dict:
    """One more S5.2 + S5.3 pass with layout ``lay`` / settings ``cfg`` (then the box re-measurement against the RAW
    and one more pass when the box changed, as the first passes); returns {name, share, state}. ctx is left as it
    was."""
    from . import pipeline as P
    saved = _state(ctx)
    try:
        ctx.cfg, ctx.layout = cfg, lay
        ov0 = copy.deepcopy(overlays)
        tag = ("picture_retry", name, *extra)
        P.visual_refine_pass(ctx, base_raw, overlays, f" (picture search again: {name})",
                             *P.visual_pass_key_parts(lay, ov0), *tag, index_raw=index_raw)
        if matched_share(ctx.fm_pre) >= REFINE_MIN and P.refine_layout_from_raw(ctx):
            ov2 = P.initial_overlays(ctx.layout, ov0)
            P.visual_refine_pass(ctx, base_raw, ov2, f" (picture search again: {name}, refined box)",
                                 *P.visual_pass_key_parts(ctx.layout, ov2), *tag, index_raw=index_raw)
        share = matched_share(ctx.fm_pre)
        log.info("picture search again -- %s: %.0f %% of the competitor's frames found", name, 100 * share)
        ctx.dlog.record("picture_retry", "attempt", name=name, share=round(share, 4),
                        box=ctx.layout.box.to_dict() if getattr(ctx.layout, "box", None) else None)
        return {"name": name, "share": share, "state": _state(ctx)}
    finally:
        _restore(ctx, saved)


def retry(ctx: Any, base_raw: Any) -> dict | None:
    """The retries (module docstring) when needs_retry; adopts the best attempt into ctx. Returns {picture, sound,
    attempts: [(name, share)], used} or None when no retry was needed."""
    pic, snd = matched_share(ctx.fm_pre), sound_share(ctx)
    if not needs_retry(pic, snd):
        return None
    log.warning("picture: only %.0f %% of the competitor's frames were found in the RAW although %.0f %% of its "
                "sound is there -- searching the picture again (the layout measured again: the biggest moving area, "
                "the whole frame; a denser search with a wider zoom range; every search also tries the mirrored "
                "picture)", 100 * pic, 100 * snd)
    import time
    t0 = time.monotonic()
    skipped: list[str] = []

    def late(name: str) -> bool:
        """Fail fast (night 15): no new attempt after RETRY_BUDGET_S."""
        if time.monotonic() - t0 < RETRY_BUDGET_S:
            return False
        skipped.append(name)
        log.info("picture search again -- %s not tried: the retries took %.0f min already", name,
                 (time.monotonic() - t0) / 60.0)
        return True

    first = {"name": "the first search", "share": pic, "state": _state(ctx)}
    tries: list[dict] = []
    best = first
    for name, edges in layout_boxes(ctx):
        if late(name):
            continue
        try:
            lay, ov = forced_layout(ctx, name, edges)
            r = attempt(ctx, name, lay, ov, ctx.cfg, base_raw)
        except Exception as e:  # noqa: BLE001 - a failed attempt leaves the others (and the first search)
            log.warning("picture search again -- %s failed: %s: %s", name, type(e).__name__, e)
            continue
        tries.append(r)
        if r["share"] > best["share"] + 1e-9:
            best = r
        if best["share"] >= ENOUGH:
            break
    edge_name = "comparing lines and shapes (edges)"
    if best["share"] < ENOUGH and not late(edge_name):         # night 15: cartoons -- the outlines, not the colours
        try:
            st = best["state"]
            r = attempt(ctx, edge_name, st["layout"], copy.deepcopy(st["overlays"]), edge_cfg(ctx.cfg), base_raw,
                        extra=("grad_weight", EDGE_WEIGHT, "from", best["name"]))
            tries.append(r)
            if r["share"] > best["share"] + 1e-9:
                best = r
        except Exception as e:  # noqa: BLE001 - as above
            log.warning("picture search again -- %s failed: %s: %s", edge_name, type(e).__name__, e)
    wins = sound_windows(ctx)
    dense_name = "a denser search with a wider zoom range"
    if best["share"] < ENOUGH and wins and not late(dense_name):
        try:
            st = best["state"]
            view = windowed_proxy(base_raw, wins)
            r = attempt(ctx, dense_name, st["layout"], copy.deepcopy(st["overlays"]),
                        dense_cfg(ctx.cfg), base_raw, index_raw=view,
                        extra=("windows", [list(w) for w in wins], "from", best["name"]))
            tries.append(r)
            if r["share"] > best["share"] + 1e-9:
                best = r
        except Exception as e:  # noqa: BLE001 - as above
            log.warning("picture search again -- the denser search failed: %s: %s", type(e).__name__, e)
    trust_name = "trusting the sound (lower picture scores where the sound found it)"
    if TRUST_SOUND and best["share"] < PICTURE_RETRY_BELOW and wins and not late(trust_name):     # night 15: the last try
        try:
            st = best["state"]
            view = windowed_proxy(base_raw, wins)
            r = attempt(ctx, trust_name, st["layout"], copy.deepcopy(st["overlays"]), trust_cfg(ctx.cfg), base_raw,
                        index_raw=view, extra=("windows", [list(w) for w in wins], "trust", TRUST_MIN, TRUST_RUN,
                                               TRUST_SLACK_S, "from", best["name"]))
            fm = r["state"]["fm_pre"]
            if fm is not None:
                fm = r["state"]["fm_pre"] = fm.copy()
                kept = trust_filter(ctx, fm)
                r["share"] = matched_share(fm)
                log.info("picture search again -- %s: %d frames agree with the sound in order and timing (%.0f %%)",
                         trust_name, kept, 100 * r["share"])
                r["trusted"] = True
            tries.append(r)
            if r["share"] > best["share"] + 1e-9:
                best = r
        except Exception as e:  # noqa: BLE001 - as above
            log.warning("picture search again -- %s failed: %s: %s", trust_name, type(e).__name__, e)
    if best is not first:
        cfg = ctx.cfg
        _restore(ctx, best["state"])
        ctx.cfg = cfg                                  # the run's own settings for everything after the search
    out = {"picture": pic, "sound": snd, "attempts": [(r["name"], round(r["share"], 4)) for r in tries],
           "used": best["name"], "share": best["share"], "trusted": bool(best.get("trusted")),
           "skipped": skipped, "seconds": round(time.monotonic() - t0, 1)}
    ctx.dlog.record("picture_retry", "result", **out)
    log.info("picture search again: %s -- %.0f %% of the competitor's frames found (the first search: %.0f %%)",
             "used " + best["name"] if best is not first else "nothing found more than the first search",
             100 * best["share"], 100 * pic)
    return out


def not_found_message(ctx: Any, res: dict) -> str:
    """The plain message of the PICTURE NOT FOUND stop."""
    from pathlib import Path
    h = ctx.hints
    conf = h.confident(ctx.cfg.audio_min_conf)
    rt = np.asarray(h.raw_t)[conf]
    where = (f" (RAW {float(rt.min()):.1f}-{float(rt.max()):.1f} s of {float(ctx.raw_info.duration):.0f} s)"
             if rt.size else "")
    tried = "; ".join(f"{n} {100 * s:.0f} %" for n, s in res.get("attempts") or []) or "nothing else to try"
    return (f"{NOT_FOUND_HEAD}. {100 * res['sound']:.0f} % of the competitor's sound is in this RAW{where}, but only "
            f"{100 * res['share']:.0f} % of its frames were found, even after searching the picture again (the first "
            f"search {100 * res['picture']:.0f} %; {tried}; every search also tried the mirrored picture). No edit was "
            "made: without the picture it would hold no clip but look like a normal one. Check that the RAW is the "
            "same version as the competitor's -- the same frame and cut, not one with the credits squeezed into a "
            f"corner or another aspect ratio ({Path(ctx.cfg.competitor).name} / {Path(ctx.cfg.raw).name}); "
            "--force-inputs makes the edit anyway.")
