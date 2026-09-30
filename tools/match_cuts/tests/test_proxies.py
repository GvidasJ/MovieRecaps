"""Unit tests for match_cuts.proxies (DESIGN §5 proxies.py) on tiny lavfi clips."""
from __future__ import annotations

import dataclasses
import subprocess
from fractions import Fraction
from pathlib import Path

import numpy as np
import pytest

from match_cuts.common import Cache
from match_cuts.config import Config
from match_cuts.media import VideoReader, extract_audio
from match_cuts.probe import probe, reader_sar
from match_cuts.proxies import (build_proxy, even_size, extend_proxy, load_audio, load_audio_full, proxy_plan)


def ff(*args: str) -> None:
    subprocess.run(["ffmpeg", "-v", "error", "-nostdin", "-y", *args], check=True)


@pytest.fixture(scope="module")
def clips(tmp_path_factory) -> dict[str, Path]:
    d = tmp_path_factory.mktemp("proxy_clips")
    c = {k: d / v for k, v in {"raw": "raw.mp4", "comp": "comp.mp4", "voff": "voff.mp4", "rot": "rot.mp4",
                               "sar": "sar.mp4", "stereo": "stereo.mov"}.items()}
    ff("-f", "lavfi", "-i", "testsrc2=s=320x180:r=30000/1001,trim=end_frame=120", "-f", "lavfi",
       "-i", "anoisesrc=seed=2:r=48000,atrim=end_sample=192192", "-c:v", "libx264", "-preset", "veryfast",
       "-crf", "18", "-bf", "3", "-g", "24", "-pix_fmt", "yuv420p", "-c:a", "aac", str(c["raw"]))
    ff("-f", "lavfi", "-i", "testsrc2=s=216x384:r=30,trim=end_frame=30", "-c:v", "libx264", "-crf", "18",
       "-pix_fmt", "yuv420p", str(c["comp"]))
    ff("-itsoffset", "0.5", "-i", str(c["raw"]), "-i", str(c["raw"]), "-map", "0:v", "-map", "1:a", "-c", "copy",
       str(c["voff"]))
    ff("-display_rotation", "-90", "-i", str(c["raw"]), "-c", "copy", str(c["rot"]))
    ff("-f", "lavfi", "-i", "testsrc2=s=180x180:r=25,trim=end_frame=20", "-c:v", "libx264", "-crf", "18",
       "-pix_fmt", "yuv420p", "-aspect", "16:9", str(c["sar"]))
    ff("-f", "lavfi", "-i", "testsrc2=s=64x36:r=25,trim=end_frame=25", "-f", "lavfi",
       "-i", "aevalsrc='sin(2*PI*440*t)|0.5*sin(2*PI*660*t)':s=44100:d=1", "-c:v", "prores_aw",
       "-c:a", "pcm_s16le", str(c["stereo"]))
    return c


def make(tmp_path: Path, **kw) -> tuple[Config, Cache]:
    cfg = Config(out_dir=str(tmp_path / "out"), work_dir=str(tmp_path / "work"), **kw)
    return cfg, Cache(cfg.work_dir)


def reference_frames(path: Path, size, **kw) -> dict[int, np.ndarray]:
    with VideoReader(path, **kw) as rd:
        return {j: img for j, img in rd.frames(0, None, fmt="gray", size=size)}


def test_even_size():
    assert even_size(1920, 1080, 640) == (640, 360)
    assert even_size(1080, 1920, 540) == (540, 960)
    w, h = even_size(1920, 1080, 314)
    assert w % 2 == 0 and h % 2 == 0 and w <= 314 and h == w * 1080 // 1920      # exact aspect preferred
    w, h = even_size(1918, 1078, 400)                                             # no exact size nearby
    assert (w, h) == (400, 2 * round(400 * 1078 / 1918 / 2))
    assert even_size(160, 90, 640) == (160, 90)                                   # never upscale


def test_dense_raw_proxy_equals_videoreader(clips, tmp_path):
    cfg, cache = make(tmp_path, raw_proxy_width=160)
    info = probe(clips["raw"], "raw", cfg.work_dir)
    p = build_proxy(info, "raw", cfg, cache)
    assert p.dense and p.size == (160, 90) and p.ratio == (0.5, 0.5) and p.full_size == (320, 180)
    assert p.frames.shape == (120, 90, 160) and p.frames.dtype == np.uint8 and p.n == 120
    assert isinstance(p.frames, np.memmap) and Path(p.npy_path).exists()
    assert p.fps == Fraction(30000, 1001)
    np.testing.assert_allclose(p.pts, np.arange(120) * 1001 / 30000, atol=1e-9)
    ref = reference_frames(clips["raw"], p.size)
    assert all(np.array_equal(p.get(j), ref[j]) for j in range(120))
    with VideoReader(clips["raw"]) as rd:                       # random access by PTS agrees too
        assert np.array_equal(rd.get(77, fmt="gray", size=p.size), p.get(77))
    assert p.has(0) and p.has(119) and not p.has(120) and not p.has(-1)
    with pytest.raises(KeyError):
        p.get(120)
    # cached: second call reuses the memmap file
    mtime = Path(p.npy_path).stat().st_mtime_ns
    p2 = build_proxy(info, "raw", cfg, cache)
    assert p2.npy_path == p.npy_path and Path(p2.npy_path).stat().st_mtime_ns == mtime


def test_competitor_proxy_scale_and_cap(clips, tmp_path):
    cfg, cache = make(tmp_path)
    info = probe(clips["comp"], "competitor", cfg.work_dir)
    p = build_proxy(info, "competitor", cfg, cache)
    assert p.size == (108, 192) and p.ratio == (0.5, 0.5)
    cfg2, cache2 = make(tmp_path / "b", comp_proxy_max_width=64)
    p2 = build_proxy(info, "competitor", cfg2, cache2)
    assert p2.size[0] <= 64 and p2.size[0] % 2 == 0 and p2.size[1] % 2 == 0
    assert p2.ratio == (p2.size[0] / 216, p2.size[1] / 384)


def test_budget_derived_width(clips, tmp_path):
    info = probe(clips["raw"], "raw", str(tmp_path / "w"))
    cfg = Config(proxy_budget_bytes=120 * 64 * 36, min_proxy_width=32)      # fits exactly 64x36 per frame
    plan = proxy_plan(info, "raw", cfg)
    assert plan["mode"] == "dense" and plan["size"] == (64, 36)


def test_frame_count_assertion(clips, tmp_path):
    cfg, cache = make(tmp_path, raw_proxy_width=64)
    info = probe(clips["raw"], "raw", cfg.work_dir)
    bad = dataclasses.replace(info, nb_frames=info.nb_frames + 1, pts_file="")
    with pytest.raises(RuntimeError, match="decoded 120 frames"):
        build_proxy(bad, "raw", cfg, cache)
    assert not list((Path(cfg.work_dir) / "cache" / "proxy").glob("*.tmp.npy"))


def test_sparse_long_raw_proxy(clips, tmp_path):
    cfg, cache = make(tmp_path, proxy_budget_bytes=20_000, long_raw_s=1.0, min_proxy_width=64,
                      raw_index_fps_long=3.0)
    info = probe(clips["raw"], "raw", cfg.work_dir)
    p = build_proxy(info, "raw", cfg, cache, windows=[(40, 45), (118, 130)])
    assert not p.dense and p.index_map.dtype == np.int32 and p.index_map.shape == (120,)
    held = np.flatnonzero(p.index_map >= 0).tolist()
    expect = sorted(set(range(0, 120, 10)) | set(range(40, 45)) | {118, 119})
    assert held == expect and p.frames.shape[0] == len(expect) and p.n == 120
    ref = reference_frames(clips["raw"], p.size)
    for j in range(120):
        assert p.has(j) == (j in expect)
        if p.has(j):
            assert np.array_equal(p.get(j), ref[j])
        else:
            with pytest.raises(KeyError):
                p.get(j)
    # second pass: add dense windows
    p2 = extend_proxy(p, [(60, 66)], cfg, cache)
    held2 = np.flatnonzero(p2.index_map >= 0).tolist()
    assert held2 == sorted(set(expect) | set(range(60, 66)))
    assert all(np.array_equal(p2.get(j), ref[j]) for j in held2)
    assert not p.has(61)                                          # the old Proxy object is unchanged
    # a fresh build exposes exactly the requested frames even though the store holds more (determinism)
    p3 = build_proxy(info, "raw", cfg, cache, windows=[(40, 45), (118, 130)])
    assert np.flatnonzero(p3.index_map >= 0).tolist() == expect
    assert all(np.array_equal(p3.get(j), ref[j]) for j in expect)
    # extending a dense proxy is a no-op
    cfg_d, cache_d = make(tmp_path / "d", raw_proxy_width=64)
    pd = build_proxy(info, "raw", cfg_d, cache_d)
    assert extend_proxy(pd, [(0, 5)], cfg_d, cache_d) is pd


def test_display_orientation_and_sar(clips, tmp_path):
    cfg, cache = make(tmp_path, raw_proxy_width=90)
    info = probe(clips["rot"], "raw", cfg.work_dir)
    assert info.rotation == 90
    p = build_proxy(info, "raw", cfg, cache)
    assert p.full_size == (180, 320) and p.size == (90, 160)          # portrait = display orientation
    ref = reference_frames(clips["rot"], p.size, rotation=info.rotation)
    assert all(np.array_equal(p.get(j), ref[j]) for j in range(0, 120, 17))
    info_s = probe(clips["sar"], "raw", cfg.work_dir)
    ps = build_proxy(info_s, "raw", Config(raw_proxy_width=160), cache)
    assert ps.full_size == (320, 180) and ps.size == (160, 90)
    ref_s = reference_frames(clips["sar"], ps.size, sar=reader_sar(info_s))
    assert np.array_equal(ps.get(5), ref_s[5])


def test_load_audio_sample0_is_video_t0(clips, tmp_path):
    cfg, cache = make(tmp_path)
    info = probe(clips["voff"], "raw", cfg.work_dir)
    assert info.av_offset == pytest.approx(-0.5)
    y = load_audio(info, 16000, cache)
    y_src = extract_audio(clips["voff"], 16000)                  # sample 0 = start of the audio stream
    assert y.dtype == np.float32 and y.ndim == 1
    assert len(y) == len(y_src) - 8000 and np.array_equal(y, y_src[8000:])
    assert list((Path(cfg.work_dir) / "cache" / "audio").glob("*.npy"))
    assert np.array_equal(load_audio(info, 16000, cache), y)     # cached
    info_na = probe(clips["comp"], "competitor", cfg.work_dir)
    assert load_audio(info_na, 16000, cache).shape == (0,)
    yf, sr = load_audio_full(info_na)
    assert yf.shape[0] == 0 and sr == 48000


def test_load_audio_full_original_rate(clips, tmp_path):
    cfg, cache = make(tmp_path)
    info = probe(clips["stereo"], "raw", cfg.work_dir)
    y, sr = load_audio_full(info)
    assert sr == 44100 and y.shape == (44100, 2) and y.dtype == np.float32
    # channel 0 = 440 Hz at amplitude 1, channel 1 = 660 Hz at 0.5
    assert 0.9 < np.abs(y[:, 0]).max() <= 1.0 and 0.45 < np.abs(y[:, 1]).max() < 0.55
    m = load_audio(info, 16000, cache)
    assert len(m) == 16000
