"""Stage 2b — conform inputs to AE-safe media (DESIGN.md §5 conform.py, prompt Stage 2).

RAW:
  * AE-safe            -> hard-linked (or copied) into ``output/media/`` unchanged, or referenced by
                          absolute path when larger than ``cfg.large_file_bytes`` (the JSX relinks).
  * not AE-safe        -> ``output/media/raw_ae.mov`` (ProRes 422 LT, prores_aw profile 1, PCM s16le
                          48 kHz) for <= 10 min, else ``raw_ae.mp4`` (H.264 CRF 12, AAC 48 kHz).
COMPETITOR (reference layer + analysis): always ``output/media/competitor_ref.mp4`` (H.264 + AAC):
  copied when AE-safe mp4/H.264/AAC, else transcoded.

Every transcode keeps the resolution (display orientation, square pixels — rotation and SAR are baked
in, odd sizes cropped by one px) and the NOMINAL frame rate, is CFR and starts at 0:
  * CFR sources are re-stamped by frame index (``settb=1/fps,setpts=N``): immune to ms-rounded
    timestamps (WebM/MKV), frame k of the output IS decoded frame k of the source;
  * VFR sources use ``fps=fps=N/D:round=up`` = "the frame displayed at t_k" (verified), padded with
    the last frame / trimmed to exactly #{k : k/fps < last_pts + median_frame_duration} frames;
  * audio is re-based so that sample 0 is video frame 0 (start offsets / edit lists removed).
Transcodes are verified: exact frame count, AE-safety of the result, and >= 50 frames sampled by PTS
whose SSIM against their source frame is > 0.98 and higher than against the source's neighbours.
Results are cached in ``output/media/.conform.json`` ({src_hash, params, out_hash}).
"""
from __future__ import annotations

import dataclasses
import json
import math
import os
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from fractions import Fraction
from pathlib import Path
from typing import Any

import numpy as np

from .common import (DecisionLog, STAGE_VERSION, atomic_write_text, ffmpeg_bin, file_hash, fps_str, log,
                     null_dlog, params_hash)
from .model import StreamInfo
from .probe import (ae_issues, display_geometry, load_pts_int, probe, reader_sar, video_stream_ordinal)

CONFORM_JSON = ".conform.json"
SSIM_MIN = 0.98
MIN_SAMPLES = 50
SAMPLE_TARGET = 64
COMPARE_MAX_SIDE = 480
PRORES_MAX_S = 600.0                 # 'auto': ProRes LT up to 10 min, H.264 beyond
AUDIO_RATE = 48000


@dataclass
class ConformResult:
    """Where the AE-imported (and analysed) copy of one input lives and how it was made."""
    path: str                 # absolute path of the file AE imports and every later stage analyses
    conformed: bool           # True = transcoded (verification applies)
    reason: str
    verification: dict = field(default_factory=dict)
    source_path: str = ""     # the original input (never modified)
    file_rel: str = ""        # path relative to the output dir ('media/raw_ae.mov'); '' = absolute reference
    file_abs: str = ""        # absolute path (== path)

    def to_dict(self) -> dict:
        return dataclasses.asdict(self)

    @staticmethod
    def from_dict(d: dict) -> "ConformResult":
        names = {f.name for f in dataclasses.fields(ConformResult)}
        return ConformResult(**{k: v for k, v in d.items() if k in names})


# ----------------------------------------------------------------------------------------------
# Plan
# ----------------------------------------------------------------------------------------------

def _even(x: float) -> int:
    return max(2, int(2 * round(x / 2.0)))


def output_geometry(info: StreamInfo) -> dict:
    """Display-oriented, square-pixel, even-sized output geometry of a conform.

    Returns {w, h, crop: (cw, ch) | None applied to the rotated frame, scale: (tw, th) | None}.
    The SAR axis is stretched (never shrunk) to an even size; an odd size on an unscaled axis is
    cropped by one pixel at the right/bottom (keeps the CORNER geometry of every other pixel)."""
    rot = int(info.rotation) % 360
    w, h = (info.height, info.width) if rot in (90, 270) else (info.width, info.height)
    _, _, s = display_geometry(info.width, info.height, rot, info.sar)
    scale_x, scale_y = s > 1, (0 < s < 1)
    cw = w if (scale_x or w % 2 == 0) else w - 1
    ch = h if (scale_y or h % 2 == 0) else h - 1
    tw = _even(cw * float(s)) if scale_x else cw
    th = _even(ch / float(s)) if scale_y else ch
    return {"w": int(tw), "h": int(th), "crop": (int(cw), int(ch)) if (cw, ch) != (w, h) else None,
            "scale": (int(tw), int(th)) if (scale_x or scale_y) else None, "rotated": (int(w), int(h)),
            "scale_x": bool(scale_x), "scale_y": bool(scale_y)}


def _vfr_expected_frames(pts: np.ndarray, tb: Fraction, fps: Fraction) -> int:
    """#{k : k/fps < last_pts + median_frame_duration} with PTS relative to the first frame (exact)."""
    rel = pts - pts[0]
    if len(rel) < 2:
        return 1
    med = Fraction(int(np.median(np.diff(rel)) * 2), 2)          # median of integer deltas (x.5 exact)
    end = (Fraction(int(rel[-1])) + med) * tb * fps               # in output frames
    n = math.ceil(end)
    return int(n)


def source_index_for_output(k: np.ndarray, mode: str, pts: np.ndarray, tb: Fraction, fps: Fraction) -> np.ndarray:
    """Decoded-order index of the source frame shown at conformed frame k.

    restamp: k itself. fps (VFR): max{i : pts_i - pts_0 <= k/fps}, evaluated EXACTLY with Python ints
    ((pts_i - pts_0)·tb_num·fps_num <= k·tb_den·fps_den) so exact-tie frames are never misplaced."""
    import bisect
    ks = [int(x) for x in np.asarray(k).ravel()]
    if mode == "restamp":
        return np.minimum(np.asarray(ks, dtype=np.int64), len(pts) - 1)
    p0 = int(pts[0])
    lhs = [(int(p) - p0) * tb.numerator * fps.numerator for p in pts]     # increasing
    scale = tb.denominator * fps.denominator
    return np.asarray([max(0, bisect.bisect_right(lhs, x * scale) - 1) for x in ks], dtype=np.int64)


def plan_transcode(info: StreamInfo, role: str, cfg) -> dict:
    """Everything that determines a transcode (also the cache 'params')."""
    fps = Fraction(info.fps)
    geo = output_geometry(info)
    codec = "h264_ref" if role == "competitor" else str(getattr(cfg, "conform_codec", "auto") or "auto")
    if codec == "auto":
        codec = "prores_lt" if info.duration <= PRORES_MAX_S else "h264"
    if codec not in ("prores_lt", "prores", "prores_ks", "h264", "h264_ref"):
        raise ValueError(f"unknown conform_codec {codec!r} (auto|prores_lt|prores|prores_ks|h264)")
    pts, tb, _origin = load_pts_int(info)
    if info.vfr:
        mode = "fps"
        expected = _vfr_expected_frames(pts, tb, fps)
        timing = [f"setpts=PTS-STARTPTS", f"fps=fps={fps.numerator}/{fps.denominator}:round=up",
                  "tpad=stop=-1:stop_mode=clone", f"trim=end_frame={expected}"]
    else:
        mode = "restamp"
        expected = int(info.nb_frames)
        timing = [f"settb={fps.denominator}/{fps.numerator}", "setpts=N",
                  f"fps=fps={fps.numerator}/{fps.denominator}", f"trim=end_frame={expected}"]
    vf = list(timing)
    if geo["crop"]:
        vf.append(f"crop={geo['crop'][0]}:{geo['crop'][1]}:0:0:exact=1")
    if geo["scale"]:
        vf.append(f"scale={geo['scale'][0]}:{geo['scale'][1]}:flags=bicubic")
    vf.append("setsar=1")
    if any(i.startswith("interlaced") for i in info.ae_issues):
        vf.append("setfield=prog")
    prores = codec.startswith("prores")
    vf.append("format=yuv422p10le" if prores else "format=yuv420p")

    af = None
    if info.has_audio:
        af = [f"aresample={AUDIO_RATE}", "asetpts=PTS-STARTPTS"]
        off = int(round(float(info.av_offset) * AUDIO_RATE))
        if off > 0:
            af.append(f"adelay=delays={off}S:all=1")
        elif off < 0:
            af += [f"atrim=start_sample={-off}", "asetpts=PTS-STARTPTS"]

    g = max(1, int(round(float(fps) * (1 if codec == "h264_ref" else 2))))
    if codec == "prores_lt":
        vargs = ["-c:v", "prores_aw", "-profile:v", "1", "-vendor", "apl0"]
    elif codec == "prores":
        vargs = ["-c:v", "prores_aw", "-profile:v", "2", "-vendor", "apl0"]
    elif codec == "prores_ks":
        vargs = ["-c:v", "prores_ks", "-profile:v", "1", "-vendor", "apl0"]
    elif codec == "h264":
        vargs = ["-c:v", "libx264", "-preset", str(getattr(cfg, "conform_h264_preset", "veryfast")),
                 "-crf", str(getattr(cfg, "conform_h264_crf", 12)), "-g", str(g), "-bf", "2",
                 "-profile:v", "high"]
    else:  # h264_ref (competitor)
        vargs = ["-c:v", "libx264", "-preset", str(getattr(cfg, "competitor_h264_preset", "medium")),
                 "-crf", str(getattr(cfg, "competitor_h264_crf", 12)), "-g", str(g), "-bf", "2",
                 "-profile:v", "high"]
    if af is None:
        aargs = []
    elif prores:
        aargs = ["-c:a", "pcm_s16le", "-ar", str(AUDIO_RATE)]
    else:
        aargs = ["-c:a", "aac", "-b:a", "320k", "-ar", str(AUDIO_RATE)]
    ext = ".mov" if prores else ".mp4"
    name = "competitor_ref.mp4" if role == "competitor" else f"raw_ae{ext}"
    return {"version": STAGE_VERSION.get("conform", 1), "mode": mode, "codec": codec, "name": name,
            "fps": fps_str(fps), "expected_frames": int(expected), "width": geo["w"], "height": geo["h"],
            "vf": ",".join(vf), "af": ",".join(af) if af else None, "vargs": vargs, "aargs": aargs,
            "vindex": video_stream_ordinal(info)}


def ffmpeg_command(src: str, out: str, plan: dict) -> list[str]:
    cmd = [ffmpeg_bin(), "-v", "error", "-nostdin", "-y", "-i", src, "-map", f"0:v:{plan['vindex']}"]
    if plan["af"]:
        cmd += ["-map", "0:a:0"]
    cmd += ["-filter:v", plan["vf"]]
    if plan["af"]:
        cmd += ["-filter:a", plan["af"]]
    cmd += ["-fps_mode", "passthrough", *plan["vargs"], *plan["aargs"], "-map_metadata", "-1", "-map_chapters", "-1"]
    if plan["codec"] == "h264_ref":
        cmd += ["-movflags", "+faststart"]       # small reference file; a multi-GB RAW would be rewritten
    return cmd + [out]


# ----------------------------------------------------------------------------------------------
# Verification
# ----------------------------------------------------------------------------------------------

def ssim(a: np.ndarray, b: np.ndarray) -> float:
    """Mean SSIM of two 8-bit-range grayscale images (Gaussian window sigma 1.5, K1=0.01, K2=0.03)."""
    import cv2
    a = a.astype(np.float32)
    b = b.astype(np.float32)
    c1, c2 = (0.01 * 255) ** 2, (0.03 * 255) ** 2
    blur = lambda x: cv2.GaussianBlur(x, (11, 11), 1.5)  # noqa: E731
    mu_a, mu_b = blur(a), blur(b)
    saa = blur(a * a) - mu_a * mu_a
    sbb = blur(b * b) - mu_b * mu_b
    sab = blur(a * b) - mu_a * mu_b
    num = (2 * mu_a * mu_b + c1) * (2 * sab + c2)
    den = (mu_a * mu_a + mu_b * mu_b + c1) * (saa + sbb + c2)
    return float(np.mean(num / den))


def _compare_size(w: int, h: int) -> tuple[int, int]:
    f = min(1.0, COMPARE_MAX_SIDE / max(w, h))
    return max(8, int(round(w * f))), max(8, int(round(h * f)))


def decode_source_frames(info: StreamInfo, indices: list[int], geo: dict, size: tuple[int, int]) -> dict[int, np.ndarray]:
    """Gray display-oriented source frames by DECODED-ORDER index, located by their exact PTS (valid for
    VFR files, unlike VideoReader indices). Rotation/SAR as VideoReader; the conform crop is applied;
    resized to ``size`` (INTER_AREA)."""
    import cv2
    from .media import VideoReader

    pts, tb, _origin = load_pts_int(info)
    want = sorted(set(int(i) for i in indices if 0 <= int(i) < len(pts)))
    out: dict[int, np.ndarray] = {}
    if not want:
        return out
    gap = int(4 / float(tb)) if tb else 0
    clusters: list[list[int]] = [[want[0]]]
    for i in want[1:]:
        if int(pts[i]) - int(pts[clusters[-1][-1]]) > gap:
            clusters.append([i])
        else:
            clusters[-1].append(i)
    rd = VideoReader(info.path, fps=info.fps, stream_index=video_stream_ordinal(info), rotation=info.rotation,
                     sar=reader_sar(info))
    try:
        stream = rd.stream
        for cl in clusters:
            wanted = {int(pts[i]): i for i in cl}
            lo, hi = int(pts[cl[0]]), int(pts[cl[-1]])
            targets = [lo - max(1, int(0.5 / float(tb))), lo - int(10 / float(tb)), None]
            done = False
            for target in targets:
                if target is None:
                    rd.container.seek(0, stream=stream, backward=True, any_frame=False)
                else:
                    rd.container.seek(max(target, int(stream.start_time or 0)), stream=stream, backward=True,
                                      any_frame=False)
                first = True
                got: dict[int, np.ndarray] = {}
                for frame in rd.container.decode(stream):
                    p = frame.pts if frame.pts is not None else frame.dts
                    if p is None:
                        continue
                    p = int(p)
                    if first:
                        first = False
                        if p > lo and target is not None:
                            break               # seek overshot -> retry further back
                    if p in wanted:
                        img = rd._convert(frame, "gray", None, None)
                        if geo.get("crop"):
                            # the conform crops only an odd UNSCALED axis (after rotation, before scaling)
                            if not geo.get("scale_y"):
                                img = img[:geo["crop"][1]]
                            if not geo.get("scale_x"):
                                img = img[:, :geo["crop"][0]]
                        got[wanted[p]] = cv2.resize(img, size, interpolation=cv2.INTER_AREA)
                    if p >= hi:
                        break
                if len(got) == len(cl):
                    out.update(got)
                    done = True
                    break
            if not done:
                raise RuntimeError(f"{info.path}: could not decode source frames {cl[:5]}... by PTS")
    finally:
        rd.close()
    return out


def verify_transcode(src: StreamInfo, out: StreamInfo, plan: dict) -> dict:
    """Frame count + AE safety + >= 50 PTS-sampled SSIM checks (DESIGN §5 conform verification)."""
    import cv2
    from .media import VideoReader

    t0 = time.perf_counter()
    res: dict[str, Any] = {"method": plan["mode"], "frames_expected": plan["expected_frames"],
                           "frames_actual": int(out.nb_frames), "fps_expected": plan["fps"],
                           "fps_actual": fps_str(out.fps), "size": [out.display_width, out.display_height],
                           "ae_issues_after": list(out.ae_issues)}
    problems: list[str] = []
    if out.nb_frames != plan["expected_frames"]:
        problems.append(f"frame count {out.nb_frames} != expected {plan['expected_frames']}")
    if fps_str(out.fps) != plan["fps"]:
        problems.append(f"fps {fps_str(out.fps)} != {plan['fps']}")
    if (out.display_width, out.display_height) != (plan["width"], plan["height"]):
        problems.append(f"size {out.display_width}x{out.display_height} != {plan['width']}x{plan['height']}")
    if out.ae_issues:
        problems.append("conformed file is still not AE-safe: " + "; ".join(out.ae_issues))
    if out.has_audio != src.has_audio:
        problems.append(f"audio presence changed ({src.has_audio} -> {out.has_audio})")

    n = int(min(out.nb_frames, plan["expected_frames"]))
    pts, tb, _ = load_pts_int(src)
    fps = Fraction(plan["fps"])
    if n > 0:
        m = min(n, SAMPLE_TARGET)
        ks = np.unique(np.round(np.linspace(0, n - 1, m)).astype(np.int64))
        src_idx = source_index_for_output(ks, plan["mode"], pts, tb, fps)
        need = sorted({int(j) for i in src_idx for j in (i - 1, i, i + 1) if 0 <= j < len(pts)})
        size = _compare_size(plan["width"], plan["height"])
        geo = output_geometry(src)
        orig = decode_source_frames(src, need, geo, size)
        with VideoReader(out.path, fps=out.fps) as rd:
            conf = {k: cv2.resize(img, size, interpolation=cv2.INTER_AREA)
                    for k, img in rd.get_many([int(k) for k in ks], fmt="gray").items()}
        samples, fails = [], []
        n_inf = 0
        for k, i in zip(ks.tolist(), src_idx.tolist()):
            c = conf[k]
            s0 = ssim(c, orig[i])
            nb = {}
            informative = False
            ok = s0 > SSIM_MIN
            for j in (i - 1, i + 1):
                if j not in orig:
                    continue
                snb = ssim(c, orig[j])
                d_nb = 1.0 - ssim(orig[i], orig[j])        # how different the neighbour really is
                inf = d_nb > 3.0 * max(1.0 - s0, 1e-4)      # clearly above the codec noise
                nb[j] = round(snb, 5)
                if inf:
                    informative = True
                    ok = ok and s0 > snb
            n_inf += int(informative)
            rec = {"k": int(k), "src": int(i), "ssim": round(s0, 5), "neighbours": nb, "informative": informative}
            samples.append(rec)
            if not ok:
                fails.append(rec)
        ss = np.array([r["ssim"] for r in samples])
        margins = [r["ssim"] - max(r["neighbours"].values()) for r in samples if r["informative"] and r["neighbours"]]
        res.update({"samples": len(samples), "informative": n_inf, "failed": fails[:20], "n_failed": len(fails),
                    "min_ssim": float(ss.min()), "median_ssim": float(np.median(ss)),
                    "min_margin": float(min(margins)) if margins else None,
                    "compare_size": list(size)})
        if len(samples) < min(MIN_SAMPLES, n):
            problems.append(f"only {len(samples)} samples (< {min(MIN_SAMPLES, n)})")
        if fails:
            problems.append(f"{len(fails)} sampled frames fail SSIM > {SSIM_MIN} / better-than-neighbours "
                            f"(e.g. k={fails[0]['k']} src={fails[0]['src']} ssim={fails[0]['ssim']} nb={fails[0]['neighbours']})")
        if n_inf == 0 and n > 2:
            res["note"] = "no sampled frame differs from its neighbours: offset check uninformative (static video)"
    res["problems"] = problems
    res["ok"] = not problems
    res["seconds"] = round(time.perf_counter() - t0, 3)
    return res


# ----------------------------------------------------------------------------------------------
# .conform.json cache + file placement
# ----------------------------------------------------------------------------------------------

def _load_state(media: Path) -> dict:
    p = media / CONFORM_JSON
    if p.exists():
        try:
            return json.loads(p.read_text())
        except Exception:
            log.warning("%s unreadable; ignoring the conform cache", p)
    return {}


def _save_state(media: Path, state: dict) -> None:
    atomic_write_text(media / CONFORM_JSON, json.dumps(state, indent=1, sort_keys=True))


def _link_or_copy(src: Path, dst: Path) -> str:
    """Place ``src`` at ``dst`` without ever writing into an existing inode (dst may be a hard link to an
    input file: it is unlinked, never truncated). Returns 'same' | 'hardlink' | 'copy'."""
    if dst.exists():
        try:
            if os.path.samefile(src, dst):
                return "same"
        except OSError:
            pass
        dst.unlink()
    try:
        os.link(src, dst)
        return "hardlink"
    except OSError:
        tmp = dst.with_name(dst.name + ".tmp")
        shutil.copy2(src, tmp)
        os.replace(tmp, dst)
        return "copy"


def _media_name_for_raw(src: Path) -> str:
    name = src.name
    if name in ("competitor_ref.mp4", CONFORM_JSON) or name.startswith("raw_ae."):
        name = "raw_" + name
    return name


# ----------------------------------------------------------------------------------------------
# conform()
# ----------------------------------------------------------------------------------------------

def conform(info: StreamInfo, role: str, cfg, dlog: DecisionLog | None = None) -> ConformResult:
    """Make the AE-imported copy of one input (DESIGN §5 conform.py). Never modifies the input.

    role 'raw' | 'competitor'. Returns a ConformResult whose ``path`` every later stage must analyse.
    Raises RuntimeError when a transcode fails its verification (nothing is cached then)."""
    dlog = dlog or null_dlog()
    media = Path(cfg.out_dir) / "media"
    media.mkdir(parents=True, exist_ok=True)
    src = Path(info.path).resolve()
    if not src.is_file():
        raise FileNotFoundError(f"conform: input not found: {src}")
    src_hash = info.file_hash or file_hash(src)
    issues = ae_issues(info)
    force = bool(getattr(cfg, "force_conform", False))
    out_root = Path(cfg.out_dir).resolve()
    state = _load_state(media)

    if role == "competitor":
        ref_ok = (not issues and info.container in ("mp4", "m4v") and info.vcodec == "h264"
                  and (not info.has_audio or info.acodec == "aac"))
        need = force or not ref_ok
        why_not_copy = issues or (["forced (--force-conform)"] if force else
                                  [] if ref_ok else [f"reference must be H.264/AAC .mp4 (is {info.container}/"
                                                     f"{info.vcodec}/{info.acodec or 'no audio'})"])
    else:
        need = force or bool(issues)
        why_not_copy = issues or (["forced (--force-conform)"] if force else [])

    # ------------------------------------------------------------------ untouched placement
    if not need:
        if role != "competitor" and info.file_size > int(getattr(cfg, "large_file_bytes", 2 * 1024 ** 3)):
            params = {"mode": "reference", "version": STAGE_VERSION.get("conform", 1)}
            res = ConformResult(str(src), False, f"AE-safe; {info.file_size / 1e9:.2f} GB > large_file_bytes: "
                                "referenced by absolute path (the JSX offers a relink dialog)",
                                {"method": "reference", "ok": True}, str(src), "", str(src))
            state[role] = {"src_hash": src_hash, "params": params, "out_hash": src_hash, "out_file": "",
                           "result": res.to_dict()}
            _save_state(media, state)
            dlog.record("conform", "reference", role=role, evidence={"file": str(src), "bytes": info.file_size})
            log.info("conform %s: AE-safe, referenced by absolute path (%s)", role, src)
            return res
        name = "competitor_ref.mp4" if role == "competitor" else _media_name_for_raw(src)
        dst = media / name
        how = _link_or_copy(src, dst)
        params = {"mode": "copy", "name": name, "version": STAGE_VERSION.get("conform", 1)}
        res = ConformResult(str(dst.resolve()), False, f"AE-safe; {how} into media/ unchanged",
                            {"method": "copy", "ok": True, "identical": True, "frames": int(info.nb_frames)},
                            str(src), os.path.relpath(dst.resolve(), out_root).replace(os.sep, "/"), str(dst.resolve()))
        state[role] = {"src_hash": src_hash, "params": params, "out_hash": src_hash, "out_file": name,
                       "result": res.to_dict()}
        _save_state(media, state)
        dlog.record("conform", "copy", role=role, evidence={"file": str(src), "method": how, "ae_issues": []})
        log.info("conform %s: AE-safe -> %s (%s)", role, dst, how)
        return res

    # ------------------------------------------------------------------ transcode
    plan = plan_transcode(info, role, cfg)
    dst = media / plan["name"]
    prev = state.get(role)
    if (prev and prev.get("src_hash") == src_hash and prev.get("params") == plan and dst.exists()
            and file_hash(dst) == prev.get("out_hash")):
        res = ConformResult.from_dict(prev["result"])
        res.path = res.file_abs = str(dst.resolve())
        res.source_path = str(src)
        res.file_rel = os.path.relpath(dst.resolve(), out_root).replace(os.sep, "/")
        dlog.record("conform", "cache_hit", role=role, evidence={"file": str(dst), "params": params_hash(plan)})
        log.info("conform %s: cached %s", role, dst)
        return res

    tmp = dst.with_name(".tmp_" + dst.name)
    cmd = ffmpeg_command(str(src), str(tmp), plan)
    t0 = time.perf_counter()
    log.info("conform %s: %s -> %s (%s, %s, %d frames)", role, src.name, dst.name, plan["codec"], plan["mode"],
             plan["expected_frames"])
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        if tmp.exists():
            tmp.unlink()
        raise RuntimeError(f"conform {role}: ffmpeg failed ({r.returncode}): {' '.join(cmd)}\n{r.stderr[-3000:]}")
    enc_s = time.perf_counter() - t0
    os.replace(tmp, dst)
    out_info = probe(dst, role, cfg.work_dir, decode=True)
    ver = verify_transcode(info, out_info, plan)
    ver["encode_seconds"] = round(enc_s, 3)
    ver["encode_fps"] = round(plan["expected_frames"] / enc_s, 2) if enc_s > 0 else None
    dlog.record("conform", "transcode", role=role,
                evidence={"source": str(src), "out": str(dst), "issues": why_not_copy, "plan": plan,
                          "verification": ver})
    if not ver["ok"]:
        raise RuntimeError(f"conform {role}: verification of {dst} failed: " + "; ".join(ver["problems"]))
    codec_desc = {"prores_lt": "ProRes 422 LT (prores_aw) + PCM 48 kHz", "prores": "ProRes 422 (prores_aw) + PCM 48 kHz",
                  "prores_ks": "ProRes 422 LT (prores_ks) + PCM 48 kHz", "h264": "H.264 CRF 12 + AAC 48 kHz",
                  "h264_ref": "H.264 + AAC 48 kHz"}[plan["codec"]]
    reason = ("not AE-safe: " + "; ".join(why_not_copy) + f" -> transcoded to {codec_desc}, "
              f"{'VFR->CFR fps round=up' if plan['mode'] == 'fps' else 'CFR re-stamped by frame index'} at "
              f"{plan['fps']} fps, {plan['width']}x{plan['height']}, start 0")
    res = ConformResult(str(dst.resolve()), True, reason, ver, str(src),
                        os.path.relpath(dst.resolve(), out_root).replace(os.sep, "/"), str(dst.resolve()))
    state = _load_state(media)
    state[role] = {"src_hash": src_hash, "params": plan, "out_hash": out_info.file_hash, "out_file": plan["name"],
                   "result": res.to_dict()}
    _save_state(media, state)
    log.info("conform %s: %s verified (%d samples, min SSIM %.4f) in %.1fs (%.1f fps encode)", role, dst.name,
             ver.get("samples", 0), ver.get("min_ssim", float("nan")), time.perf_counter() - t0,
             ver["encode_fps"] or 0)
    return res
