"""Day 14, the RAW's conversion (S2 conform, S8 edit_media): a RAW whose timestamps only drift slowly (< 0.5 frame, every
step one frame) is a constant frame rate and is not converted; a real conversion is made once, on the graphics card
(H.264 NVENC cq 17, preset p5; the CPU when NVENC is missing or fails), and V1 plays it (no second conversion); a RAW
whose timestamps are scrambled (an x265 release muxed with PTS = DTS: Ted 2 RBG) is re-stamped in decoding order and
checked in that order instead of stopping with "could not decode source frames [0, 1] by PTS"."""
from __future__ import annotations

import subprocess
from pathlib import Path

import numpy as np
import pytest

from match_cuts import conform, pipeline, probe
from match_cuts.common import ffmpeg_bin
from match_cuts.config import Config

RATE = "24000/1001"


def _clip(path: Path, setpts: str | None = None, seconds: float = 10.0, vcodec: str = "libx264",
          extra: tuple[str, ...] = ()) -> Path:
    """A small RAW: testsrc2 at 24000/1001 with stereo sine sound; ``setpts``: its frames' times (kept to 1/240000 s:
    not rounded to whole frames)."""
    vf = ["-vf", f"settb=1/240000,setpts={setpts}", "-fps_mode", "passthrough", "-enc_time_base", "1/240000",
          "-video_track_timescale", "240000"] if setpts else []
    subprocess.run([ffmpeg_bin(), "-v", "error", "-y", "-f", "lavfi", "-i", f"testsrc2=size=320x180:rate={RATE}",
                    "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000", "-t", str(seconds), *vf,
                    "-c:v", vcodec, "-pix_fmt", "yuv420p", *extra, "-c:a", "aac", "-ac", "2", str(path)], check=True)
    return path


def _probe(path: Path, tmp_path: Path):
    return probe.probe(str(path), "raw", str(tmp_path / "work"), decode=True)


# ---------------------------------------------------------------------------------------------------------------------
# a slow drift is a constant frame rate
# ---------------------------------------------------------------------------------------------------------------------

def test_a_slow_drift_under_half_a_frame_is_a_constant_frame_rate(tmp_path):
    # every frame 0.15 % longer than 1001/24000 s (as Ted 2's YTS x264 runs at exactly 23.976 fps): 0.36 frames off
    # the grid after 10 s -- the probe calls it VFR (> 0.1), the rule a constant rate
    info = _probe(_clip(tmp_path / "drift.mp4", "N*1.0015*1001/24000/TB"), tmp_path)
    assert info.vfr and 0.1 < info.pts_jitter < 0.5 and any(i.startswith("vfr:") for i in info.ae_issues)
    st = pipeline.steady_timing(info)
    assert st is not None and not st.vfr and not any(i.startswith("vfr:") for i in st.ae_issues)
    assert info.vfr                                                    # (a copy: the probe's own stays as it was)
    cfg = Config(out_dir=str(tmp_path / "out"), work_dir=str(tmp_path / "work"))
    res = conform.conform(st, "raw", cfg)                              # what S2 does with it: no conversion
    assert not res.conformed and "unchanged" in res.reason and Path(res.path).name == "raw.mp4"


def test_frames_that_jump_around_or_drift_more_than_half_a_frame_stay_vfr(tmp_path):
    jumpy = _probe(_clip(tmp_path / "phone.mp4", "N*1001/24000/TB+0.01*mod(N\\,3)/TB"), tmp_path)
    assert jumpy.vfr and pipeline.steady_timing(jumpy) is None        # 0 / 10 / 20 ms late in turn: a phone's VFR
    far = _probe(_clip(tmp_path / "far.mp4", "N*1.004*1001/24000/TB"), tmp_path)
    assert far.vfr and far.pts_jitter > 0.5 and pipeline.steady_timing(far) is None
    plain = _probe(_clip(tmp_path / "plain.mp4"), tmp_path)
    assert not plain.vfr and pipeline.steady_timing(plain) is None   # nothing to change


# ---------------------------------------------------------------------------------------------------------------------
# one conversion, on the graphics card
# ---------------------------------------------------------------------------------------------------------------------

def test_a_raws_conversion_is_planned_on_the_graphics_card_when_it_can(tmp_path):
    info = _probe(_clip(tmp_path / "hevc.mp4", vcodec="libx265", extra=("-x265-params", "log-level=none")), tmp_path)
    cfg = Config(out_dir=str(tmp_path / "out"), work_dir=str(tmp_path / "work"))
    gpu = conform.plan_transcode(info, "raw", cfg, gpu=True)
    assert gpu["codec"] == "h264" and gpu["name"] == "raw_ae.mp4"     # a short RAW too (no ProRes on the card)
    v = gpu["vargs"]
    assert v[:2] == ["-c:v", "h264_nvenc"] and v[v.index("-preset") + 1] == "p5" and v[v.index("-cq") + 1] == "17"
    cpu = conform.plan_transcode(info, "raw", cfg, gpu=False)
    assert cpu["codec"] == "prores_lt" and "h264_nvenc" not in cpu["vargs"]          # as before day 14
    cfg.conform_codec = "h264"
    assert "libx264" in conform.plan_transcode(info, "raw", cfg, gpu=False)["vargs"]
    comp = conform.plan_transcode(info, "competitor", cfg, gpu=True)                 # the competitor: never
    assert "h264_nvenc" not in comp["vargs"] and comp["codec"] == "h264_ref"
    assert conform.plan_transcode(info, "raw", cfg)["vargs"] == conform.plan_transcode(info, "raw", cfg, gpu=False)[
        "vargs"]                                                                     # MATCH_CUTS_NVENC=0 (conftest)


@pytest.mark.skipif(not conform._nvenc_works(ffmpeg_bin()), reason="no NVENC on this machine")
def test_a_real_conversion_runs_once_on_the_graphics_card_and_is_checked(tmp_path, monkeypatch):
    monkeypatch.setenv(conform.NVENC_ENV, "1")
    info = _probe(_clip(tmp_path / "hevc.mp4", vcodec="libx265", extra=("-x265-params", "log-level=none")), tmp_path)
    cfg = Config(out_dir=str(tmp_path / "out"), work_dir=str(tmp_path / "work"))
    res = conform.conform(info, "raw", cfg)
    assert res.conformed and "NVENC" in res.reason and res.verification["ok"], res.verification.get("problems")
    out = _probe(Path(res.path), tmp_path)
    assert out.vcodec == "h264" and out.nb_frames == info.nb_frames and not out.ae_issues


def test_a_graphics_card_that_fails_converts_on_the_cpu_instead(tmp_path, monkeypatch):
    monkeypatch.setattr(conform, "NVENC_ARGS", ("-c:v", "h264_nvenc", "-preset", "no_such_preset_day14"))
    monkeypatch.setattr(conform, "nvenc_ok", lambda: True)
    info = _probe(_clip(tmp_path / "hevc.mp4", seconds=4.0, vcodec="libx265",
                        extra=("-x265-params", "log-level=none")), tmp_path)
    cfg = Config(out_dir=str(tmp_path / "out"), work_dir=str(tmp_path / "work"))
    cfg.conform_codec = "h264"
    res = conform.conform(info, "raw", cfg)
    assert res.conformed and res.verification["ok"] and "NVENC" not in res.reason and "CRF 12" in res.reason


# ---------------------------------------------------------------------------------------------------------------------
# scrambled timestamps (an x265 release muxed with PTS = DTS)
# ---------------------------------------------------------------------------------------------------------------------

def test_a_raw_with_scrambled_timestamps_is_converted_in_decoding_order(tmp_path):
    clean = _clip(tmp_path / "clean.mp4", seconds=6.0, extra=("-bf", "3", "-g", "48"))
    bad = tmp_path / "pts_eq_dts.mp4"                  # every frame's PTS = its DTS: the timestamps of B-frames lie
    subprocess.run([ffmpeg_bin(), "-v", "error", "-y", "-i", str(clean), "-c", "copy", "-bsf:v", "setts=pts=DTS",
                    str(bad)], check=True)
    info = _probe(bad, tmp_path)
    ref = _probe(clean, tmp_path)
    # its edit list, worked out from those timestamps, hides the first two packets' frames (the clean frames 0 and 3)
    assert conform.scrambled_pts(info) and conform.bogus_video_editlist(info) and info.nb_frames == ref.nb_frames - 2
    cfg = Config(out_dir=str(tmp_path / "out"), work_dir=str(tmp_path / "work"))
    cfg.conform_codec = "h264"
    plan = conform.plan_transcode(info, "raw", cfg)
    assert plan["mode"] == "restamp"                   # (never the timestamps' fps rule: they are not the truth)
    assert plan["ignore_video_editlist"] and plan["expected_frames"] == ref.nb_frames
    res = conform.conform(info, "raw", cfg)
    ver = res.verification
    assert res.conformed and ver["ok"] and ver["checked_in_decoding_order"] > 0, ver.get("problems")
    # the converted frames are the clean file's frames, every one, in their order (frame 0 with sound sample 0)
    got = _probe(Path(res.path), tmp_path)
    assert got.nb_frames == ref.nb_frames and abs(got.av_offset) < 1e-3
    import av

    def gray(p):
        with av.open(str(p)) as c:
            return [f.to_ndarray(format="gray").astype(np.float32) for f in c.decode(c.streams.video[0])]
    a, b = gray(clean), gray(res.path)
    errs = [float(np.abs(x - y).mean()) for x, y in zip(a, b)]
    assert len(a) == len(b) and max(errs) < 3.0, max(errs)
