"""render_preview (prompt Stage 8; DESIGN §5 render_preview.py).

A short lavfi RAW (testsrc2 + burned-in frame counter, 30000/1001, 640x360, analytic multi-tone PCM audio)
and a hand-made cutlist in a boxed 270x480 @ 30 layout (1.10x, flip, rotation, animated zoom keys, a 6-frame
crossfade, a J-cut, NOT-IN-RAW). The preview is rendered and decoded, and EVERY frame is compared with an
independent reference built here with cv2 on the decoded RAW frames (own CORNER->CV matrices, own AE rule
with exact Fractions, own rounded-box test): PSNR > 35 dB on the box and better than the neighbouring RAW
frames; crossfade weights, placeholder colour; audio: exact length, analytic tape-style positions for the
1.10x segment (xcorr lag 0), crossfade gains, J-cut, silence, remap freeze / reverse; fill / source / MAIN
fps grid modes; blurred background; compare.mp4 from a path, a RenderContext and a callable.
"""
from __future__ import annotations

import copy
import json
import math
import subprocess
from fractions import Fraction
from pathlib import Path
from types import SimpleNamespace

import cv2
import numpy as np
import pytest

from match_cuts import render_preview as rp
from match_cuts.config import Config
from match_cuts.media import VideoReader, extract_audio
from match_cuts.model import Cutlist, Segment

RF = Fraction(30000, 1001)          # RAW fps
CF = Fraction(30)                   # competitor fps
RAW_W, RAW_H, RAW_N = 640, 360, 240
CW, CH, N = 270, 480, 150
SR = 48000
BOX = {"x": 15.0, "y": 110.0, "w": 240.0, "h": 250.0, "corner_radius": 24.0}
BG = "#102030"                      # BGR (48, 32, 16)
BOX_C = (BOX["x"] + BOX["w"] / 2, BOX["y"] + BOX["h"] / 2)

# analytic RAW audio: sum of sinusoids (exact value at any position -> independent resampling reference)
_rng = np.random.default_rng(7)
TONE_F = _rng.uniform(150.0, 6000.0, 24)
TONE_A = _rng.uniform(0.01, 0.035, 24)
TONE_P = _rng.uniform(0.0, 2 * np.pi, 24)


def tone(pos: np.ndarray) -> np.ndarray:
    """RAW audio value at (fractional) sample positions; 0 outside the RAW audio."""
    pos = np.asarray(pos, np.float64)
    t = pos[:, None] / SR
    y = (TONE_A * np.sin(2 * np.pi * TONE_F * t + TONE_P)).sum(axis=1)
    return np.where((pos >= 0) & (pos <= RAW_N / float(RF) * SR - 1), y, 0.0)


def raw_audio(n_samples: int | None = None) -> np.ndarray:
    n = int(n_samples or round(RAW_N / float(RF) * SR))
    return tone(np.arange(n)).astype(np.float32)


def raw_time(j: int, phase: float = 0.5) -> float:
    return float((j + Fraction(phase)) / RF)


def centred(s: float, theta: float = 0.0) -> dict:
    """Sim dict mapping the RAW centre to the box centre."""
    th = math.radians(theta)
    cx, cy = RAW_W / 2, RAW_H / 2
    px = s * (math.cos(th) * cx - math.sin(th) * cy)
    py = s * (math.sin(th) * cx + math.cos(th) * cy)
    return {"scale": s, "rotation_deg": theta, "tx": BOX_C[0] - px, "ty": BOX_C[1] - py}


XF = {"type": "crossfade", "duration_frames": 6, "alpha": [i / 6 for i in range(6)]}


def make_segments() -> list[Segment]:
    k_a, k_b = centred(0.75), centred(0.9)
    return [
        Segment(id=1, type="raw", comp_in=0, comp_out=30, raw_in_seconds=raw_time(10, 0.5), speed=1.0,
                transform=centred(0.75, 2.0), confidence=0.99),
        Segment(id=2, type="raw", comp_in=30, comp_out=60, raw_in_seconds=raw_time(100, 0.3), speed=1.1,
                transform=centred(0.75), confidence=0.97,
                audio={"in_offset_frames": -3, "out_offset_frames": 0, "pitch_preserved": False, "lag_ms": None,
                       "corr": None, "exception": None}),
        Segment(id=3, type="raw", comp_in=60, comp_out=96, raw_in_seconds=raw_time(50, 0.45), speed=1.0,
                flip_h=True, transform=centred(0.8), transition_out=dict(XF)),
        Segment(id=4, type="raw", comp_in=90, comp_out=130, raw_in_seconds=raw_time(170, 0.55), speed=1.0,
                transform=dict(k_a), transition_in=dict(XF),
                transform_keys=[{"comp_frame": 90, **k_a}, {"comp_frame": 129, **k_b}]),
        Segment(id=5, type="not_in_raw", comp_in=130, comp_out=150,
                label="MISSING - not in RAW (00:00:04:10-00:00:05:00)"),
    ]


def make_cutlist(raw_path: str = "", segments: list[Segment] | None = None, background: dict | None = None,
                 box: dict | None = BOX, n: int = N) -> Cutlist:
    bg = background or {"type": "solid", "color": BG}
    layout = {"mode": "match", "layout_kind": "boxed", "canvas_bg": BG, "box": dict(box) if box else None,
              "background": bg["type"], "background_detail": bg, "zones": [], "captions": []}
    comp = {"file": "competitor.mp4", "width": CW, "height": CH, "fps": "30/1", "frames": n}
    raw = {"file": raw_path, "file_abs": raw_path, "width": RAW_W, "height": RAW_H, "fps": "30000/1001",
           "frames": RAW_N, "has_audio": True, "audio_sample_rate": SR, "audio_channels": 1}
    return Cutlist(1, comp, raw, layout, segments if segments is not None else make_segments())


def run(cmd: list[str]) -> None:
    res = subprocess.run(cmd, capture_output=True, text=True)
    assert res.returncode == 0, res.stderr[-2000:]


@pytest.fixture(scope="module")
def raw_clip(tmp_path_factory) -> Path:
    d = tmp_path_factory.mktemp("raw")
    wav = d / "tone.wav"
    import soundfile as sf
    sf.write(str(wav), raw_audio(), SR, subtype="PCM_16")
    out = d / "raw.mov"
    vf = (f"testsrc2=size={RAW_W}x{RAW_H}:rate=30000/1001,"
          "drawtext=text='%{eif\\:n\\:d\\:5}':fontsize=72:fontcolor=white:borderw=4:x=40:y=120")
    run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", vf, "-i", str(wav), "-frames:v", str(RAW_N),
         "-map", "0:v", "-map", "1:a", "-c:v", "libx264", "-preset", "veryfast", "-crf", "12", "-bf", "2",
         "-g", "30", "-pix_fmt", "yuv420p", "-c:a", "pcm_s16le", "-video_track_timescale", "30000", str(out)])
    return out


@pytest.fixture(scope="module")
def raw_frames(raw_clip) -> dict[int, np.ndarray]:
    with VideoReader(raw_clip, fps=RF) as vr:
        frames = dict(vr.frames(0, RAW_N))
    assert len(frames) == RAW_N
    return frames


@pytest.fixture(scope="module")
def preview(raw_clip, tmp_path_factory) -> dict:
    d = tmp_path_factory.mktemp("preview")
    cl = make_cutlist(str(raw_clip))
    cfg = Config(out_dir=str(d))
    out = d / "preview_recreation.mp4"
    res = rp.render_preview(cl, raw_clip, out, cfg)
    with VideoReader(out, fps=CF) as vr:
        frames = dict(vr.frames(0, None))
    return {"res": res, "frames": frames, "path": out, "cutlist": cl, "cfg": cfg}


# ---------------------------------------------------------------------------------------------
# Independent reference (written here from the conventions, not from match_cuts.geometry)
# ---------------------------------------------------------------------------------------------

def exact_frame(seg: Segment, k: int) -> int:
    return math.floor(RF * (Fraction(seg.raw_in_seconds) + Fraction(seg.speed) * Fraction(k - seg.comp_in) / CF))


def sim_at(seg: Segment, k: int) -> tuple[float, float, float, float]:
    if seg.transform_keys:
        a, b = seg.transform_keys
        u = min(1.0, max(0.0, (k - a["comp_frame"]) / (b["comp_frame"] - a["comp_frame"])))
        return tuple(a[n] + u * (b[n] - a[n]) for n in ("scale", "rotation_deg", "tx", "ty"))  # type: ignore
    t = seg.transform
    return t["scale"], t["rotation_deg"], t["tx"], t["ty"]


def ref_warp(img: np.ndarray, sim: tuple[float, float, float, float], flip: bool) -> np.ndarray:
    s, th, tx, ty = sim
    c, sn = math.cos(math.radians(th)), math.sin(math.radians(th))
    M = np.array([[s * c, -s * sn, tx], [s * sn, s * c, ty], [0, 0, 1.0]])      # CORNER, flipped RAW -> comp
    if flip:
        M = M @ np.array([[-1.0, 0, RAW_W], [0, 1, 0], [0, 0, 1]])
    to_cv = np.array([[1, 0, -0.5], [0, 1, -0.5], [0, 0, 1.0]])
    from_cv = np.array([[1, 0, 0.5], [0, 1, 0.5], [0, 0, 1.0]])
    Mcv = (to_cv @ M @ from_cv)[:2]
    return cv2.warpAffine(img, Mcv, (CW, CH), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT).astype(np.float64)


def box_interior(margin: float = 2.0) -> np.ndarray:
    """Pixels whose whole square lies inside the rounded box by `margin` px (own geometry)."""
    ys, xs = np.mgrid[0:CH, 0:CW].astype(np.float64)
    x0, y0, x1, y1, r = BOX["x"] + margin, BOX["y"] + margin, BOX["x"] + BOX["w"] - margin, \
        BOX["y"] + BOX["h"] - margin, BOX["corner_radius"]
    ok = np.ones((CH, CW), bool)
    for ox in (0.0, 1.0):
        for oy in (0.0, 1.0):
            px, py = xs + ox, ys + oy
            inside = (px >= x0) & (px <= x1) & (py >= y0) & (py <= y1)
            cx = np.clip(px, x0 + r - margin, x1 - r + margin)
            cy = np.clip(py, y0 + r - margin, y1 - r + margin)
            ok &= inside & ((px - cx) ** 2 + (py - cy) ** 2 <= (r - margin) ** 2)
    return ok


def gray(img: np.ndarray) -> np.ndarray:
    return cv2.cvtColor(np.clip(img, 0, 255).astype(np.uint8), cv2.COLOR_BGR2GRAY).astype(np.float64)


def psnr(a: np.ndarray, b: np.ndarray, m: np.ndarray) -> float:
    mse = float(np.mean((gray(a)[m] - gray(b)[m]) ** 2))
    return 99.0 if mse <= 1e-9 else 10 * math.log10(255.0 ** 2 / mse)


def expected_frame(k: int, segs: list[Segment], raw: dict[int, np.ndarray], jshift: dict[int, int] | None = None
                   ) -> tuple[np.ndarray, list[tuple[int, int, float]]]:
    """Reference render of the box interior (outside = anything) and [(seg, j, weight)]."""
    active = [s for s in segs if s.type == "raw" and s.comp_in <= k < s.comp_out]
    ws: list[tuple[int, int, float]] = []
    if len(active) == 2:                                  # crossfade: A (earlier) keyed 1 - alpha
        A, B = active
        alpha = (k - B.comp_in) / 6.0
        parts = [(A, 1 - alpha), (B, alpha)]
    else:
        parts = [(s, 1.0) for s in active]
    img = np.zeros((CH, CW, 3))
    for s, w in parts:
        j = exact_frame(s, k) + (jshift or {}).get(s.id, 0)
        img += w * ref_warp(raw[j], sim_at(s, k), s.flip_h)
        ws.append((s.id, j, w))
    return img, ws


# ---------------------------------------------------------------------------------------------
# Video
# ---------------------------------------------------------------------------------------------

def test_every_preview_frame_is_the_expected_raw_frame(preview, raw_frames):
    frames, res = preview["frames"], preview["res"]
    assert res["frames"] == N and len(frames) == N and sorted(frames) == list(range(N))
    assert res["size"] == [CW, CH] and res["fps"] == "30/1" and res["layout_mode"] == "match"
    segs = make_segments()
    m = box_interior()
    worst = 99.0
    for k in range(N):
        got = frames[k].astype(np.float64)
        assert got.shape == (CH, CW, 3)
        if k >= 130:
            continue
        ref, ws = expected_frame(k, segs, raw_frames)
        p = psnr(got, ref, m)
        worst = min(worst, p)
        assert p > 35.0, f"frame {k}: PSNR {p:.1f} dB vs the expected RAW frame(s) {ws}"
        # the neighbouring RAW frames must fit clearly worse (frame-exact, not just similar)
        if len(ws) == 1:
            for dj in (-1, 1):
                pn = psnr(got, expected_frame(k, segs, raw_frames, {ws[0][0]: dj})[0], m)
                assert pn < p - 3.0, f"frame {k}: RAW j{dj:+d} fits too well ({pn:.1f} vs {p:.1f} dB)"
        # renderer bookkeeping: the same RAW frames and crossfade weights
        info = sorted(res["raw_frames"][k])
        assert [(a, b) for a, b, _ in info] == sorted((a, b) for a, b, _ in ws)
        for (_, _, w_got), (_, _, w_exp) in zip(info, sorted(ws)):
            assert abs(w_got - w_exp) < 1e-9
    assert worst > 35.0


def test_background_and_placeholder_colour(preview):
    frames = preview["frames"]
    for k in (0, 45, 140):
        corner = frames[k][2:40, 2:10].reshape(-1, 3).mean(axis=0)
        assert np.all(np.abs(corner - np.array([48, 32, 16])) <= 8), (k, corner)     # yuv420p round trip
    # NOT-IN-RAW placeholder: export_ae's colour (0.85, 0.1, 0.55) -> BGR (140, 26, 217), label drawn in the middle
    ph = np.array(rp._hex_bgr([0.85, 0.1, 0.55]))
    for k in (130, 149):
        band = frames[k][int(BOX["y"]) + 20:int(BOX["y"]) + 60, int(BOX["x"]) + 30:int(BOX["x"] + BOX["w"]) - 30]
        assert np.all(np.abs(np.median(band.reshape(-1, 3), axis=0) - ph) <= 8), np.median(band.reshape(-1, 3), axis=0)
        mid = frames[k][int(BOX_C[1]) - 40:int(BOX_C[1]) + 40, int(BOX["x"]) + 10:int(BOX["x"] + BOX["w"]) - 10]
        assert (mid.astype(int).sum(axis=2) > 600).sum() > 50, "placeholder label not drawn"


def test_rounded_corner_shows_background(preview):
    f = preview["frames"][10].astype(int)
    x, y = int(BOX["x"]) + 1, int(BOX["y"]) + 1                  # inside the box rectangle, outside the rounding
    assert np.all(np.abs(f[y, x] - np.array([48, 32, 16])) <= 8)
    assert np.abs(f[int(BOX_C[1]), int(BOX_C[0])] - np.array([48, 32, 16])).sum() > 20


def test_render_frame_matches_encoded_preview_and_raises_keyerror(preview, raw_frames):
    ctx = rp.make_context(preview["cutlist"], preview["cfg"])
    img = rp.render_frame(40, ctx, raw_frames)
    assert img.dtype == np.uint8 and img.shape == (CH, CW, 3)
    assert psnr(img.astype(float), preview["frames"][40].astype(float), box_interior()) > 38.0
    need = {j for _, j, _ in rp.frame_sources(40, ctx)}
    with pytest.raises(KeyError) as ei:
        rp.render_frame(40, ctx, {j: f for j, f in raw_frames.items() if j not in need})
    assert ei.value.args[0] in need


# ---------------------------------------------------------------------------------------------
# Audio
# ---------------------------------------------------------------------------------------------

def seg_positions(seg: Segment, n0: int, n1: int) -> np.ndarray:
    """RAW sample positions of output samples [n0, n1) under the segment's AE time map (own formula)."""
    n = np.arange(n0, n1, dtype=np.float64)
    return (seg.raw_in_seconds + seg.speed * (n / SR - seg.comp_in / float(CF))) * SR


def test_build_audio_positions_gains_and_length():
    segs = make_segments()
    cl = make_cutlist(segments=segs)
    y = rp.build_audio(cl, raw_audio(), SR)
    assert y.dtype == np.float32 and y.shape == (N * SR // 30,)             # exact: 150 frames at 30 fps
    S = {s.id: s for s in segs}
    fpc = SR // 30                                                          # 1600 samples per comp frame
    # v = 1 (S1) and 1.10x (S2, tape-style) segments: analytic positions; xcorr lag 0
    for sid, a, b in ((1, 0, 27), (2, 30, 60), (4, 96, 130)):
        n0, n1 = a * fpc + 100, b * fpc - 100
        ref = tone(seg_positions(S[sid], n0, n1))
        got = y[n0:n1].astype(np.float64)
        rel = np.sqrt(np.mean((got - ref) ** 2) / np.mean(ref ** 2))
        assert rel < 5e-3, (sid, rel)
        lags = np.arange(-40, 41)
        cc = [np.dot(got[50:-50], ref[50 + L:len(ref) - 50 + L]) for L in lags]
        assert lags[int(np.argmax(cc))] == 0, sid
    # J-cut: S2's audio starts 3 frames early and sums with S1
    n0, n1 = 27 * fpc + 50, 30 * fpc - 50
    ref = tone(seg_positions(S[1], n0, n1)) + tone(seg_positions(S[2], n0, n1))
    assert np.max(np.abs(y[n0:n1] - ref)) < 2e-3
    # crossfade S3 -> S4 (O = 90, D = 6): at every overlap frame boundary the gains are exactly
    # (1 - alpha, max(alpha, 1e-3)) -- AE Audio Levels keys, linear in dB between them
    for i in range(6):
        n = (90 + i) * fpc
        a = i / 6
        want = (1 - a) * tone(seg_positions(S[3], n, n + 1))[0] + max(a, 1e-3) * tone(seg_positions(S[4], n, n + 1))[0]
        assert abs(y[n] - want) < 2e-3, (i, y[n], want)
    n = 96 * fpc + 10                                                       # after the overlap: B alone at 0 dB
    assert abs(y[n] - tone(seg_positions(S[4], n, n + 1))[0]) < 2e-3
    # NOT-IN-RAW placeholder is silent
    assert np.all(y[130 * fpc:] == 0)


def test_build_audio_stereo_remap_freeze_reverse_and_main_grid():
    rev = [{"comp_frame": 0, "raw_seconds": 5.0}, {"comp_frame": 30, "raw_seconds": 4.0}]
    frz = [{"comp_frame": 30, "raw_seconds": raw_time(40, 0.25)}, {"comp_frame": 60, "raw_seconds": raw_time(40, 0.25)}]
    segs = [Segment(id=1, type="raw", comp_in=0, comp_out=30, raw_in_seconds=5.0, speed=-1.0, time_mode="remap",
                    time_remap_keys=rev, transform=centred(0.75)),
            Segment(id=2, type="raw", comp_in=30, comp_out=60, raw_in_seconds=frz[0]["raw_seconds"], speed=0.0,
                    time_mode="remap", time_remap_keys=frz, transform=centred(0.75))]
    cl = make_cutlist(segments=segs, n=60)
    x = raw_audio()
    y = rp.build_audio(cl, np.stack([x, 0.5 * x], axis=1), SR)
    assert y.shape == (60 * 1600, 2)
    assert np.allclose(y[:, 1], 0.5 * y[:, 0], atol=1e-6)
    n = np.arange(200, 30 * 1600 - 200)
    ref = tone((5.0 - n / SR) * SR)
    assert np.sqrt(np.mean((y[200:30 * 1600 - 200, 0] - ref) ** 2) / np.mean(ref ** 2)) < 5e-3
    assert np.all(y[30 * 1600:] == 0)                                        # a frozen remap plays no audio
    # MAIN at RAW fps (fps_mode source): length = frames at 29.97, raw_in re-anchored to the MAIN grid
    main_fps = RF
    Nm = math.floor(Fraction(60) * main_fps / CF + Fraction(1, 2))
    y2 = rp.build_audio(make_cutlist(segments=[make_segments()[0]], n=30), x, SR, fps=main_fps, n_frames=Nm)
    assert y2.shape == (math.floor(Fraction(Nm) * SR / main_fps + Fraction(1, 2)),)


def test_sample_positions_matches_analytic_signal():
    x = raw_audio()
    for p0, v in ((1234.37, 1.0), (20000.77, 1.1), (30000.25, 1 / 1.1), (4000.9, 0.999), (100000.4, -1.0)):
        y = rp.sample_positions(x, p0, v, 20000)
        ref = tone(p0 + v * np.arange(20000))
        assert np.sqrt(np.mean((y[100:-100] - ref[100:-100]) ** 2) / np.mean(ref ** 2)) < 3e-3, (p0, v)
    assert np.all(rp.sample_positions(x, 500.0, 0.0, 100) == 0)


@pytest.mark.parametrize("container", ["mov_pcm", "mp4_aac"])
def test_load_raw_audio_windows_match_full_decode(raw_clip, tmp_path, container):
    """Bounded-memory path (hour-long RAWs): only the played windows are decoded and the cutlist is shifted
    by whole samples into the compact array -- build_audio must give the same audio as the full decode."""
    path = raw_clip
    if container == "mp4_aac":
        path = tmp_path / "raw_aac.mp4"
        run(["ffmpeg", "-v", "error", "-y", "-i", str(raw_clip), "-map", "0:v", "-map", "0:a", "-c:v", "copy",
             "-c:a", "aac", "-b:a", "256k", str(path)])
    segs = make_segments()
    cl = make_cutlist(str(path), segments=segs)
    full, sr, cl_full = rp.load_raw_audio(cl, path)
    assert cl_full is cl and sr == SR and full.shape[1] == 1
    part, sr2, cl_part = rp.load_raw_audio(cl, path, budget_bytes=1000, margin_s=0.25)
    assert sr2 == SR and part.shape[0] < 0.85 * full.shape[0]                    # only the played windows
    assert cl_part is not cl and cl.segments[0].raw_in_seconds == segs[0].raw_in_seconds   # input untouched
    y_full = rp.build_audio(cl_full, full, SR)
    y_part = rp.build_audio(cl_part, part, SR)
    assert y_full.shape == y_part.shape == (N * 1600, 1)
    assert np.max(np.abs(y_full - y_part)) < 1e-4, np.max(np.abs(y_full - y_part))


def test_audio_slow_remap_starting_between_samples_and_speeds_rounding_to_zero():
    """video018 (verify s9_5_audio crashed: ZeroDivisionError in sample_positions): at the 16 kHz analysis rate the
    slow reverse remap S14 (-0.066x) starts between two output samples (frame 1073 = sample 286133.33: its audio
    starts at sample round() = 286133, its first key's piece at ceil() = 286134), which left a 1-sample piece across
    the key (1/3 sample held before it) whose speed (2/3 x -0.066 = -0.044) rounds to 0/1 -- P = 0. A speed that
    rounds to 0 over its piece (the read moves <= 0.05 samples in all) plays like a frozen one: silence."""
    sr = 16000
    v = -3 / 58                                         # -0.052x; frame 31 at 30 fps = sample 16533.33 at 16 kHz
    keys = [{"comp_frame": 31, "raw_seconds": 5.0}, {"comp_frame": 60, "raw_seconds": 5.0 + v * 29 / 30}]
    seg = Segment(id=1, type="raw", comp_in=31, comp_out=60, raw_in_seconds=5.0, speed=v, time_mode="remap",
                  time_remap_keys=keys, transform=centred(0.75))
    cl = make_cutlist(segments=[seg], n=60)
    assert [(a, b) for _s, a, b, _t, _v in rp.audio_pieces(cl, sr)] == [(16533, 16534), (16534, 32000)]
    x = tone(3.0 * np.arange(int(RAW_N / float(RF) * sr))).astype(np.float32)    # the RAW's tones at 16 kHz
    y = rp.build_audio(cl, x, sr)                                                # was: ZeroDivisionError
    assert y.shape == (32000,) and np.all(y[:16534] == 0)
    n = np.arange(16634, 31900)
    ref = tone((5.0 + v * (n / sr - 31 / 30)) * SR)                               # the remap (tone() takes 48 kHz positions)
    assert np.sqrt(np.mean((y[n] - ref) ** 2) / np.mean(ref ** 2)) < 5e-3
    # speeds that round to 0/1 over their piece (|v| <= 0.05 / n, or <= 1/20000): silent, never a crash
    xs = raw_audio()
    for p0, vv, k in ((1000.3, 0.04, 1), (1000.3, -0.04, 1), (5000.5, 0.002, 10), (5000.5, 1e-5, 20000)):
        assert np.all(rp.sample_positions(xs, p0, vv, k) == 0), (vv, k)
    assert abs(rp.sample_positions(xs, 1000.3, 1.0, 1)[0] - tone(np.array([1000.3]))[0]) < 2e-3  # 1 sample, real speed


def test_audio_jcut_on_a_reverse_remap_at_23976_is_held_not_a_crash():
    """A piece wholly before a remap's first key is held (AE), speed 0: silent. On a 23.976 fps
    grid that key's boundary sample is exact (frame 69 = sample 138138 at 48 kHz) but float rounding gave the piece of
    a 2-frame J-cut a speed of -1e-14 instead of 0, which _rational rounds to 0/1: P = 0 on an ordinary -1x reverse."""
    from fractions import Fraction
    f = Fraction(24000, 1001)
    keys = [{"comp_frame": 69, "raw_seconds": 5.0}, {"comp_frame": 89, "raw_seconds": 5.0 - 20 / float(f)}]
    seg = Segment(id=1, type="raw", comp_in=69, comp_out=89, raw_in_seconds=5.0, speed=-1.0, time_mode="remap",
                  time_remap_keys=keys, transform=centred(0.75),
                  audio={"in_offset_frames": -2, "out_offset_frames": 0, "pitch_preserved": None, "lag_ms": None,
                         "corr": None, "exception": None})
    cl = make_cutlist(segments=[seg], n=95)
    cl.competitor["fps"] = "24000/1001"
    assert [(a, b) for _s, a, b, _t, _v in rp.audio_pieces(cl, SR)] == [(134134, 138138), (138138, 178178)]
    y = rp.build_audio(cl, raw_audio(), SR)                                  # was: ZeroDivisionError
    assert y.shape == (190190,) and np.all(y[134134:138138] == 0)           # the J-cut before the first key: held
    n = np.arange(138238, 178078)
    ref = tone((5.0 - (n / SR - 69 / float(f))) * SR)                       # the -1x reverse
    assert np.sqrt(np.mean((y[n] - ref) ** 2) / np.mean(ref ** 2)) < 5e-3


def test_preview_audio_is_muxed_and_aligned(preview):
    res = preview["res"]
    assert res["audio"]["status"] == "ok" and res["audio"]["samples"] == N * 1600
    probe = subprocess.run(["ffprobe", "-v", "error", "-show_streams", "-of", "json", str(preview["path"])],
                           capture_output=True, text=True)
    streams = json.loads(probe.stdout)["streams"]
    assert [s["codec_type"] for s in streams] == ["video", "audio"]
    v, a = streams
    assert v["codec_name"] == "h264" and v["pix_fmt"] == "yuv420p" and int(v["nb_frames"]) == N
    assert a["codec_name"] == "aac"
    got = extract_audio(preview["path"], sr=SR, mono=True)
    assert abs(len(got) - N * 1600) <= 1024, len(got)
    want = rp.build_audio(preview["cutlist"], raw_audio(), SR)
    L = min(len(got), len(want)) - 2000
    lags = np.arange(-48, 49)
    cc = [np.dot(got[1000:L], want[1000 + d:L + d]) for d in lags]
    assert abs(lags[int(np.argmax(cc))]) <= 1
    seg2 = slice(31 * 1600, 59 * 1600)                                      # the 1.10x segment survives AAC
    r = np.corrcoef(got[seg2], want[seg2])[0, 1]
    assert r > 0.98, r


# ---------------------------------------------------------------------------------------------
# Layout modes, MAIN grid, blur background, geometry helpers
# ---------------------------------------------------------------------------------------------

def test_fill_and_source_modes(raw_frames):
    cl = make_cutlist()
    ctx = rp.make_context(cl, Config(layout_mode="fill", comp_size="216x384"))
    assert ctx.size == (216, 384) and ctx.mask is None and ctx.roi == (0, 0, 216, 384) and ctx.fps == CF
    from match_cuts.export_ae import fill_transform
    L2 = next(L for L in ctx.layers if L.seg_id == 2)
    want = fill_transform(rp.Sim.from_dict(make_segments()[1].transform), False, rp.Box.from_dict(BOX),
                          (RAW_W, RAW_H), (216, 384))
    assert L2.sim == want
    img = rp.render_frame(40, ctx, raw_frames)
    assert img.shape == (384, 216, 3)
    j = exact_frame(make_segments()[1], 40)
    ref = cv2.warpAffine(raw_frames[j], rp.to_cv_matrix(want, False, RAW_W), (216, 384), flags=cv2.INTER_LINEAR)
    assert np.abs(img.astype(int) - ref.astype(int)).max() <= 1
    # source: MAIN at RAW size and RAW fps, identity, cuts only (no flip)
    ctx = rp.make_context(cl, Config(layout_mode="source"))
    assert ctx.size == (RAW_W, RAW_H) and ctx.fps == RF and ctx.n_frames == math.floor(Fraction(N) * RF / CF + Fraction(1, 2))
    for K in (5, 70):
        img = rp.render_frame(K, ctx, raw_frames)
        (L, j, w), = rp.frame_sources(K, ctx)
        assert w == 1.0 and np.array_equal(img, raw_frames[j])


def test_render_preview_source_grid_and_odd_fill_size(raw_clip, raw_frames, tmp_path):
    """End-to-end in the other layouts: source = RAW size on the RAW fps grid (every frame is its RAW
    frame, untouched); fill at an odd --comp-size is padded to even for yuv420p (warned)."""
    cl = make_cutlist(str(raw_clip), segments=make_segments()[:2], n=60)
    res = rp.render_preview(cl, raw_clip, tmp_path / "src.mp4", Config(layout_mode="source", out_dir=str(tmp_path)))
    n_main = math.floor(Fraction(60) * RF / CF + Fraction(1, 2))
    assert res["frames"] == n_main and res["size"] == [RAW_W, RAW_H] and res["fps"] == "30000/1001"
    with VideoReader(tmp_path / "src.mp4", fps=RF) as vr:
        got = dict(vr.frames(0, None))
    assert len(got) == n_main
    full = np.ones((RAW_H, RAW_W), bool)
    for K, img in got.items():
        (sid, j, w), = res["raw_frames"][K]
        assert psnr(img.astype(float), raw_frames[j].astype(float), full) > 40, K
    res = rp.render_preview(cl, raw_clip, tmp_path / "fill.mp4",
                            Config(layout_mode="fill", comp_size="215x383", out_dir=str(tmp_path)))
    assert res["size"] == [215, 383] and any("odd" in w for w in res["warnings"])
    with VideoReader(tmp_path / "fill.mp4", fps=CF) as vr:
        frames = dict(vr.frames(0, None))
    assert len(frames) == 60 and frames[0].shape == (384, 216, 3)


def test_main_grid_fps_source_reanchors_raw_in():
    cl = make_cutlist()
    ctx = rp.make_context(cl, Config(fps_mode="source"))
    assert ctx.fps == RF and ctx.size == (CW, CH)
    for seg in make_segments()[:3]:
        L = next(L for L in ctx.layers if L.seg_id == seg.id)
        K_in = math.floor(Fraction(seg.comp_in) * RF / CF + Fraction(1, 2))
        assert L.k_in == K_in
        raw_in_m = Fraction(seg.raw_in_seconds) + Fraction(seg.speed) * (Fraction(K_in) / RF - Fraction(seg.comp_in) / CF)
        for K in range(L.k_in, L.k_out):
            want = math.floor(RF * (raw_in_m + Fraction(seg.speed) * Fraction(K - K_in) / RF))
            assert rp.layer_raw_frame(L, K, ctx) == want
    with pytest.raises(ValueError):
        rp.make_context(cl, Config(comp_size="300x480"))                  # match: aspect must be kept
    ctx2 = rp.make_context(cl, Config(comp_size="540x960"))
    assert ctx2.r == 2.0 and ctx2.roi == (30, 220, 480, 500)


def test_blur_background_and_dip(raw_frames):
    cl = make_cutlist(background={"type": "blur", "color": "#000000", "blurriness": 30.0})
    ctx = rp.make_context(cl, Config())
    assert ctx.bg_type == "blur" and abs(ctx.blur_sigma - 10.0) < 1e-9
    img = rp.render_frame(10, ctx, raw_frames).astype(float)
    solid = rp.render_frame(10, rp.make_context(make_cutlist(), Config()), raw_frames).astype(float)
    top = img[5:100, 5:265]
    assert top.std() > 3.0 and np.abs(top - np.array([48, 32, 16])).mean() > 10       # not the solid colour
    lap = np.abs(cv2.Laplacian(gray(top), cv2.CV_64F)).mean()
    assert lap < 2.0, lap                                                             # blurred
    m = box_interior()
    assert psnr(img, solid, m) > 60                                                   # the box is unchanged
    # dip to black between two RAW segments: the solid is keyed (rising alpha, then falling)
    segs = [Segment(id=1, type="raw", comp_in=0, comp_out=24, raw_in_seconds=raw_time(10), speed=1.0,
                    transform=centred(0.75)),
            Segment(id=2, type="dip", comp_in=20, comp_out=30, color="#000000",
                    transition_in={"type": "dip_black", "duration_frames": 4, "alpha": [0, .25, .5, .75]},
                    transition_out={"type": "dip_black", "duration_frames": 4, "alpha": [0, .25, .5, .75]}),
            Segment(id=3, type="raw", comp_in=26, comp_out=50, raw_in_seconds=raw_time(80), speed=1.0,
                    transform=centred(0.75))]
    ctx = rp.make_context(make_cutlist(segments=segs, n=50), Config())
    dip = next(L for L in ctx.layers if L.kind == "solid")
    assert ctx.layers[0] is dip                                                        # dips on top
    assert [round(dip.op(K), 6) for K in range(20, 31)] == [0, .25, .5, .75, 1, 1, 1, .75, .5, .25, 0]
    for K, a in ((21, 0.25), (22, 0.5), (27, 0.75), (29, 0.25)):          # a = dip opacity
        img = rp.render_frame(K, ctx, raw_frames).astype(float)
        seg = segs[0] if K < 24 else segs[2]
        ref = (1 - a) * ref_warp(raw_frames[exact_frame(seg, K)], sim_at(seg, K), False)
        assert psnr(img, ref, m) > 40, K


def test_rounded_box_coverage_matches_geometry_for_integer_boxes():
    from match_cuts.geometry import rounded_rect_mask
    a = rp.rounded_box_coverage(60, 40, 0.0, 0.0, 60.0, 40.0, 9.0)
    assert np.allclose(a, rounded_rect_mask(60, 40, 9.0))
    b = rp.rounded_box_coverage(30, 20, 2.5, 3.25, 20.0, 10.0, 0.0)
    assert b[5, 10] == 1.0 and b[0, 0] == 0.0 and abs(b[5, 2] - 0.5) < 1e-6 and abs(b[3, 10] - 0.75) < 1e-6


# ---------------------------------------------------------------------------------------------
# compare.mp4
# ---------------------------------------------------------------------------------------------

def _probe_video(path: Path) -> dict:
    out = subprocess.run(["ffprobe", "-v", "error", "-count_frames", "-show_streams", "-of", "json", str(path)],
                         capture_output=True, text=True)
    return {s["codec_type"]: s for s in json.loads(out.stdout)["streams"]}


def test_render_compare_sources(preview, raw_clip, tmp_path):
    cl = copy.deepcopy(preview["cutlist"])
    cl.raw["file_abs"] = str(raw_clip)
    cfg = SimpleNamespace(out_dir=str(tmp_path), compare_height=240, compare_preset="ultrafast")
    comp = preview["path"]                                   # competitor stand-in: the preview itself
    ph, pw = 240, 136                                        # 270 x 480 -> 135 x 240 -> even 136
    geo = rp.compare_geometry(CW, CH, cfg)
    sh = geo["strip"]                                        # label strip above the panels
    assert (geo["ph"], geo["pw"]) == (ph, pw) and sh % 2 == 0 and 30 <= sh <= 60
    outs = {}
    for name, src in (("path", str(preview["path"])), ("ctx", rp.make_context(cl, Config(), target_size=(CW, CH), fps=CF)),
                      ("callable", lambda k: np.full((CH, CW, 3), 128, np.uint8))):
        out = tmp_path / f"compare_{name}.mp4"
        rp.render_compare(comp, src, cl, out, cfg)
        st = _probe_video(out)
        assert int(st["video"]["width"]) == 3 * pw and int(st["video"]["height"]) == sh + ph
        assert int(st["video"]["nb_read_frames"]) == N
        assert "audio" in st                                  # the competitor's audio
        with VideoReader(out, fps=CF) as vr:
            outs[name] = {k: img for k, img in vr.frames(0, None) if k in (15, 93, 140)}
    for k, img in outs["path"].items():
        diff = img[sh:, 2 * pw:]                              # the whole difference picture
        assert diff.mean() < 3.0, (k, diff.mean())            # recreation == competitor -> black difference
        assert (img[:sh, :pw].astype(int).sum(axis=2) > 600).sum() > 30          # text burned in (strip)
    for k, img in outs["ctx"].items():
        assert img[sh:, 2 * pw:].mean() < 12.0, k             # re-rendered in memory: compression noise only
    assert outs["callable"][15][sh:, 2 * pw:].mean() > 20.0   # a wrong recreation shows up


def test_compare_labels_sit_in_a_strip_not_over_the_picture(preview, tmp_path):
    """REQ-9: frame number / timecode / segment id are drawn in a dedicated strip above each panel, so the
    competitor's logo, channel name and title at the top of the picture stay readable: every panel's
    picture area is exactly the scaled source frame (no text over it), and each strip carries the labels."""
    cl = preview["cutlist"]
    cfg = SimpleNamespace(out_dir=str(tmp_path), compare_height=240, compare_preset="ultrafast", compare_crf=10)
    geo = rp.compare_geometry(CW, CH, cfg)
    sh, ph, pw = geo["strip"], geo["ph"], geo["pw"]
    out = tmp_path / "compare_strip.mp4"
    rp.render_compare(preview["path"], lambda k: np.full((CH, CW, 3), 128, np.uint8), cl, out, cfg)
    with VideoReader(out, fps=CF) as vr:
        got = {k: img for k, img in vr.frames(0, None) if k in (15, 93, 140)}
    assert got[15].shape == (sh + ph, 3 * pw, 3)
    for k, img in got.items():
        img = img.astype(int)
        rec = img[sh:, pw:2 * pw]                             # recreation picture: uniform grey, no text
        assert np.abs(rec - 128).max() <= 6, (k, np.abs(rec - 128).max())
        comp = cv2.resize(preview["frames"][k], (pw, ph), interpolation=cv2.INTER_AREA).astype(int)
        top = (slice(sh, sh + 60), slice(0, pw))                # where the logo / name / title sit
        assert np.abs(img[top] - comp[:60]).mean() < 4.0, k   # competitor picture untouched by labels
        assert np.abs(img[sh:, :pw] - comp).mean() < 4.0, k
        for x0 in (0, pw, 2 * pw):                              # every strip: two lines of white text
            strip = img[:sh, x0:x0 + pw]
            assert (strip.sum(axis=2) > 600).sum() > 40, (k, x0)
            assert np.median(strip.reshape(-1, 3), axis=0).max() < 40      # dark strip background


# ---------------------------------------------------------------------------------------------
# Review fix AE-1 / D1: per-period layout (fullscreen shots and own-box segments in MAIN)
# ---------------------------------------------------------------------------------------------

FULL = {"x": 0.0, "y": 0.0, "w": float(CW), "h": float(CH), "corner_radius": 0.0}
OWN = {"x": 30.0, "y": 20.0, "w": 200.0, "h": 80.0, "corner_radius": 16.0}       # above the Video Box
BG_BGR = np.array([48.0, 32.0, 16.0])


def cover_sim(s: float = 1.4) -> dict:
    """RAW centred on the canvas, scaled to cover all of it (fullscreen shot)."""
    return {"scale": s, "rotation_deg": 0.0, "tx": CW / 2 - s * RAW_W / 2, "ty": CH / 2 - s * RAW_H / 2}


def rbox_interior(b: dict, margin: float = 2.0) -> np.ndarray:
    """Pixels whose whole square lies inside the rounded box b by `margin` px (own geometry)."""
    ys, xs = np.mgrid[0:CH, 0:CW].astype(np.float64)
    x0, y0, x1, y1, r = b["x"] + margin, b["y"] + margin, b["x"] + b["w"] - margin, b["y"] + b["h"] - margin, \
        b["corner_radius"]
    ok = np.ones((CH, CW), bool)
    for ox in (0.0, 1.0):
        for oy in (0.0, 1.0):
            px, py = xs + ox, ys + oy
            inside = (px >= x0) & (px <= x1) & (py >= y0) & (py <= y1)
            rr = max(r - margin, 0.0)
            cx = np.clip(px, x0 + rr, x1 - rr)
            cy = np.clip(py, y0 + rr, y1 - rr)
            ok &= inside & ((px - cx) ** 2 + (py - cy) ** 2 <= rr ** 2 + 1e-9)
    return ok


def d1_segments() -> list[Segment]:
    own_sim = {"scale": 0.5, "rotation_deg": 0.0, "tx": 130.0 - 0.5 * RAW_W / 2, "ty": 60.0 - 0.5 * RAW_H / 2}
    return [
        Segment(id=1, type="raw", comp_in=0, comp_out=20, raw_in_seconds=raw_time(10), speed=1.0,
                transform=centred(0.75)),
        Segment(id=2, type="raw", comp_in=20, comp_out=40, raw_in_seconds=raw_time(40), speed=1.0,
                transform=cover_sim(), box=dict(FULL), region=1),                 # fullscreen period
        Segment(id=3, type="raw", comp_in=40, comp_out=60, raw_in_seconds=raw_time(70), speed=1.0,
                transform=own_sim, box=dict(OWN), region=1),                      # own rounded box
        Segment(id=4, type="raw", comp_in=60, comp_out=80, raw_in_seconds=raw_time(100), speed=1.0,
                transform=centred(0.8), transition_out=dict(XF)),
        Segment(id=5, type="raw", comp_in=74, comp_out=100, raw_in_seconds=raw_time(150), speed=1.0,
                transform=cover_sim(1.5), transition_in=dict(XF), box=dict(FULL), region=1),
        Segment(id=6, type="not_in_raw", comp_in=100, comp_out=110, box=dict(FULL), region=1,
                label="MISSING - not in RAW (fullscreen)"),
    ]


def d1_expected(k: int, segs: list[Segment], raw: dict[int, np.ndarray]) -> np.ndarray:
    """Independent reference of the whole MAIN frame for the D1 cutlist (own warps, own box tests)."""
    S = {s.id: s for s in segs}
    bg = np.broadcast_to(BG_BGR, (CH, CW, 3)).astype(np.float64)
    in_box = rbox_interior(BOX)

    def boxed(s: Segment) -> np.ndarray:                  # a Video-Box segment over the background
        img = bg.copy()
        img[in_box] = ref_warp(raw[exact_frame(s, k)], sim_at(s, k), s.flip_h)[in_box]
        return img

    def full(s: Segment) -> np.ndarray:
        return ref_warp(raw[exact_frame(s, k)], sim_at(s, k), s.flip_h)

    if k < 20:
        return boxed(S[1])
    if k < 40:
        return full(S[2])
    if k < 60:
        img = bg.copy()
        own = rbox_interior(OWN)
        img[own] = ref_warp(raw[exact_frame(S[3], k)], sim_at(S[3], k), False)[own]
        return img
    if k < 74:
        return boxed(S[4])
    if k < 80:                                            # crossfade Video Box S4 -> fullscreen S5
        a = (k - 74) / 6.0
        return (1 - a) * boxed(S[4]) + a * full(S[5])
    return full(S[5])


def test_d1_make_context_places_own_box_segments_in_main():
    segs = d1_segments()
    ctx = rp.make_context(make_cutlist(segments=segs, n=110), Config())
    L = {x.seg_id: x for x in ctx.layers}
    assert [x.seg_id for x in ctx.layers[:4]] == [2, 3, 5, 6]                    # MAIN-level layers on top
    assert all(L[i].main for i in (2, 3, 5, 6)) and not L[1].main and not L[4].main
    assert L[2].clip is None and L[5].clip is None and L[6].clip is None          # whole canvas: no mask
    assert L[3].clip == (30.0, 20.0, 200.0, 80.0, 16.0)
    assert L[2].sim == rp.Sim.from_dict(cover_sim())                              # canonical Sim at origin (0, 0)
    # the crossfade into the MAIN-level S5 keys the upper (incoming) layer rising; S4 stays at 100 %
    assert not L[4].opacity
    assert [L[5].op(K) for K in range(74, 81)] == pytest.approx([0, 1 / 6, 2 / 6, .5, 4 / 6, 5 / 6, 1], abs=1e-9)
    ws = {lay.seg_id: w for lay, _j, w in rp.frame_sources(77, ctx)}
    assert abs(ws[5] - 0.5) < 1e-9 and abs(ws[4] - 0.5) < 1e-9
    # a segment box equal to the layout box (within 0.5 px) stays in the Video Box
    segs2 = [Segment(id=1, type="raw", comp_in=0, comp_out=20, raw_in_seconds=raw_time(10), speed=1.0,
                     transform=centred(0.75), box={**BOX, "x": BOX["x"] + 0.3})]
    ctx2 = rp.make_context(make_cutlist(segments=segs2, n=20), Config())
    assert not ctx2.layers[0].main and ctx2.layers[0].clip is None
    # fill mode: the segment's own box frames it (export_ae.fill_transform with that box)
    from match_cuts.export_ae import fill_transform
    ctx3 = rp.make_context(make_cutlist(segments=segs, n=110), Config(layout_mode="fill", comp_size="216x384"))
    L3 = {x.seg_id: x for x in ctx3.layers}
    assert not any(x.main or x.clip for x in ctx3.layers)
    assert L3[2].sim == fill_transform(rp.Sim.from_dict(cover_sim()), False, rp.Box.from_dict(FULL),
                                       (RAW_W, RAW_H), (216, 384))


def test_d1_fullscreen_and_own_box_segments_render_on_the_canvas(raw_frames):
    """AE-1: a fullscreen shot fills the whole canvas (not cropped to the rounded Video Box), an own-box
    segment is clipped to its own rounded box, a crossfade from a boxed shot into a fullscreen one is
    (1 - a) boxed + a fullscreen, and a NOT-IN-RAW placeholder in a fullscreen period covers the canvas."""
    segs = d1_segments()
    ctx = rp.make_context(make_cutlist(segments=segs, n=110), Config())
    everything = np.ones((CH, CW), bool)
    outside = ~rbox_interior(BOX, margin=-2.0)
    for k in (5, 20, 33, 39, 45, 59, 65, 74, 75, 77, 79, 80, 95):
        got = rp.render_frame(k, ctx, raw_frames).astype(np.float64)
        ref = d1_expected(k, segs, raw_frames)
        m = everything
        if 40 <= k < 60:                                  # exclude the own box's anti-aliased edge
            m = rbox_interior(OWN) | ~rbox_interior(OWN, margin=-2.0)
        elif k < 20 or 60 <= k < 80:
            m = rbox_interior(BOX) | outside
        p = psnr(got, ref, m)
        assert p > 40.0, f"frame {k}: PSNR {p:.1f} dB"
        if 20 <= k < 40 or k >= 80:                       # fullscreen: the canvas corners show the RAW
            assert psnr(got, ref, outside) > 40.0 and np.abs(got[5, 5] - BG_BGR).sum() > 20, k
    # the own box's rounded corner shows the background, its inside the RAW
    got = rp.render_frame(50, ctx, raw_frames).astype(int)
    assert np.all(np.abs(got[21, 31] - BG_BGR) <= 2) and np.all(np.abs(got[200, 135] - BG_BGR) <= 2)
    # NOT-IN-RAW in a fullscreen period: placeholder colour over the whole canvas, label drawn
    ph = np.array(rp._hex_bgr([0.85, 0.1, 0.55]))
    img = rp.render_frame(105, ctx, raw_frames).astype(int)
    for y, x in ((3, 3), (CH - 4, CW - 4), (60, 135)):
        assert np.all(np.abs(img[y, x] - ph) <= 1), (y, x, img[y, x])
    assert (img.sum(axis=2) > 600).sum() > 50


def test_d1_boxless_layout_clips_own_box_segments_in_the_main_stack(raw_frames):
    """Without a Video Box every layer is in MAIN; an own-box segment is still clipped to its box."""
    own_sim = {"scale": 0.5, "rotation_deg": 0.0, "tx": 130.0 - 0.5 * RAW_W / 2, "ty": 60.0 - 0.5 * RAW_H / 2}
    segs = [Segment(id=1, type="raw", comp_in=0, comp_out=20, raw_in_seconds=raw_time(70), speed=1.0,
                    transform=own_sim, box=dict(OWN), region=1)]
    ctx = rp.make_context(make_cutlist(segments=segs, n=20, box=None), Config())
    (L,) = ctx.layers
    assert not L.main and L.clip == (30.0, 20.0, 200.0, 80.0, 16.0) and ctx.mask is None
    got = rp.render_frame(10, ctx, raw_frames).astype(np.float64)
    img = np.broadcast_to(BG_BGR, (CH, CW, 3)).astype(np.float64).copy()
    own = rbox_interior(OWN)
    img[own] = ref_warp(raw_frames[exact_frame(segs[0], 10)], sim_at(segs[0], 10), False)[own]
    assert psnr(got, img, own | ~rbox_interior(OWN, margin=-2.0)) > 40.0


def test_d1_render_preview_end_to_end(raw_clip, tmp_path):
    """The whole-render path (sequential decode per layer, ffmpeg) with MAIN-level layers."""
    segs = d1_segments()
    cl = make_cutlist(str(raw_clip), segments=segs, n=110)
    res = rp.render_preview(cl, raw_clip, tmp_path / "d1.mp4", Config(out_dir=str(tmp_path)))
    assert res["frames"] == 110
    with VideoReader(tmp_path / "d1.mp4", fps=CF) as vr:
        frames = dict(vr.frames(0, None))
    with VideoReader(raw_clip, fps=RF) as vr:
        raw = dict(vr.frames(0, RAW_N))
    everything = np.ones((CH, CW), bool)
    for k in (10, 30, 50, 77, 90):
        m = everything if k in (30, 90) else (rbox_interior(BOX) | ~rbox_interior(BOX, margin=-2.0)) if k != 50 \
            else (rbox_interior(OWN) | ~rbox_interior(OWN, margin=-2.0))
        p = psnr(frames[k].astype(float), d1_expected(k, segs, raw), m)
        assert p > 33.0, (k, p)
    assert sorted(sid for sid, _, _ in res["raw_frames"][77]) == [4, 5]


# ---------------------------------------------------------------------------------------------
# FX-08: Frame Mix of a verified frame-blend path; the 'uncertain' solid
# ---------------------------------------------------------------------------------------------

def fx08_segments() -> list[Segment]:
    keys = [{"comp_frame": 30, "raw_seconds": raw_time(100, 0.1)},
            {"comp_frame": 60, "raw_seconds": raw_time(100, 0.1) + 0.25 * 30 / 30}]
    return [
        Segment(id=1, type="raw", comp_in=0, comp_out=30, raw_in_seconds=raw_time(10, 0.5), speed=1.0,
                transform=centred(0.75)),
        Segment(id=2, type="raw", comp_in=30, comp_out=60, raw_in_seconds=keys[0]["raw_seconds"], speed=0.25,
                time_mode="remap", time_remap_keys=keys, retime="frame_blend", transform=centred(0.75)),
        Segment(id=3, type="uncertain", comp_in=60, comp_out=90, uncertain=True, transform=centred(0.75),
                label="UNCERTAIN - best RAW 150-160, ZNCC 0.70-0.85 (00:00:02:00-00:00:03:00)"),
        Segment(id=4, type="raw", comp_in=90, comp_out=120, raw_in_seconds=raw_time(170, 0.5), speed=1.0,
                transform=centred(0.75)),
    ]


def test_frame_mix_blends_adjacent_raw_frames_and_uncertain_renders_its_solid(raw_frames):
    """render_frame: a verified frame-blend path (Frame Mix) shows (1 - f) RAW[floor p] + f RAW[floor p + 1] at its
    continuous position p (reference built here; better than either frame alone wherever f is not ~0 / 1, and both
    frames are needed); without retime 'frame_blend' the same keys show whole frames. An 'uncertain' segment
    renders AE's amber solid with an 'UNCERTAIN' label -- never RAW frames (its evidence is a guide layer)."""
    segs = fx08_segments()
    ctx = rp.make_context(make_cutlist(segments=segs, n=120), Config())
    m = box_interior()
    s2 = segs[1]
    a, b = (Fraction(d["raw_seconds"]) for d in s2.time_remap_keys)
    n_mixed = 0
    for k in range(30, 60):
        p = RF * (a + (b - a) * Fraction(k - 30, 30))
        j, f = math.floor(p), float(p - math.floor(p))
        sim = sim_at(s2, k)
        ref = (1 - f) * ref_warp(raw_frames[j], sim, False) + f * ref_warp(raw_frames[j + 1], sim, False)
        got = rp.render_frame(k, ctx, raw_frames).astype(np.float64)
        pm = psnr(got, ref, m)
        assert pm > 35.0, (k, f, pm)
        if 0.2 < f < 0.8:
            n_mixed += 1
            for jj in (j, j + 1):
                assert psnr(got, ref_warp(raw_frames[jj], sim, False), m) < pm - 3.0, (k, f, jj)
            with pytest.raises(KeyError):
                rp.render_frame(k, ctx, {jj: im for jj, im in raw_frames.items() if jj != j + 1})
    assert n_mixed >= 10
    plain = [s if s.id != 2 else Segment(**{**s.to_dict(), "retime": "none"}) for s in segs]
    ctx2 = rp.make_context(make_cutlist(segments=plain, n=120), Config())
    k = next(k for k in range(30, 60) if 0.3 < float(RF * (a + (b - a) * Fraction(k - 30, 30))) % 1 < 0.7)
    j = math.floor(RF * (a + (b - a) * Fraction(k - 30, 30)))
    assert psnr(rp.render_frame(k, ctx2, raw_frames).astype(float), ref_warp(raw_frames[j], sim_at(s2, k), False),
                m) > 35.0
    amber = np.array(rp._hex_bgr([0.95, 0.62, 0.05]))
    for k in (60, 89):
        img = rp.render_frame(k, ctx, raw_frames)
        band = img[int(BOX["y"]) + 20:int(BOX["y"]) + 60, int(BOX["x"]) + 30:int(BOX["x"] + BOX["w"]) - 30]
        assert np.all(np.abs(np.median(band.reshape(-1, 3), axis=0) - amber) <= 2)
        mid = img[int(BOX_C[1]) - 40:int(BOX_C[1]) + 40, int(BOX["x"]) + 10:int(BOX["x"] + BOX["w"]) - 10]
        assert (mid.astype(int).sum(axis=2) > 600).sum() > 50, "UNCERTAIN label not drawn"
        assert rp.frame_sources(k, ctx) == []
