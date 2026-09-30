"""Unit tests for match_cuts.conform (DESIGN §5 conform.py) on tiny lavfi clips."""
from __future__ import annotations

import json
import subprocess
from fractions import Fraction
from pathlib import Path

import numpy as np
import pytest

from match_cuts.common import DecisionLog, file_hash
from match_cuts.config import Config
from match_cuts.conform import (ConformResult, conform, output_geometry, source_index_for_output, ssim,
                                verify_transcode, plan_transcode)
from match_cuts.media import VideoReader, extract_audio
from match_cuts.probe import load_pts_int, probe

# 8-bit frame id in 16x16 blocks: top row = bits, bottom row = complement (robust to lossy coding)
ID_GEQ = ("geq=lum='if(lt(Y,16),if(mod(floor(N/pow(2,floor(X/16))),2),235,16),"
          "if(mod(floor(N/pow(2,floor(X/16))),2),16,235))':cb=128:cr=128")


def ff(*args: str) -> None:
    subprocess.run(["ffmpeg", "-v", "error", "-nostdin", "-y", *args], check=True)


def decode_ids(path: Path) -> tuple[list[int], list[int], Fraction]:
    """(pts, ids, time_base) of every decoded frame; the id band is the top 32 rows."""
    import av
    pts, ids = [], []
    with av.open(str(path)) as c:
        s = c.streams.video[0]
        tb = Fraction(s.time_base.numerator, s.time_base.denominator)
        for fr in c.decode(s):
            g = fr.to_ndarray(format="gray")[:32, :128].astype(float)
            top = g[:16].reshape(16, 8, 16).mean(axis=(0, 2)) > 125
            bot = g[16:32].reshape(16, 8, 16).mean(axis=(0, 2)) > 125
            assert (top != bot).all(), "undecodable id band"
            pts.append(int(fr.pts))
            ids.append(int(sum(1 << i for i, b in enumerate(top) if b)))
    return pts, ids, tb


def lag_samples(a: np.ndarray, b: np.ndarray, max_lag: int) -> int:
    """Lag L in [-max_lag, max_lag] maximising sum_n a[n] * b[n + L] (b delayed by L vs a); FFT-based."""
    from scipy.signal import correlate
    n = min(len(a), len(b))
    c = correlate(b[:n].astype(np.float64), a[:n].astype(np.float64), mode="full", method="fft")
    lags = np.arange(-(n - 1), n)
    sel = np.abs(lags) <= max_lag
    return int(lags[sel][np.argmax(c[sel])])


@pytest.fixture(scope="module")
def clips(tmp_path_factory) -> dict[str, Path]:
    d = tmp_path_factory.mktemp("conform_clips")
    c = {k: d / v for k, v in {
        "safe": "raw.mp4", "voff": "voff.mp4", "vfr": "vfr_comp.mp4", "rot": "rot.mp4", "odd": "odd_sar.mkv",
        "webm": "opus.webm", "webm_na": "noaudio.webm", "shift": "shifted.mov"}.items()}
    ff("-f", "lavfi", "-i", "testsrc2=s=160x96:r=30000/1001,trim=end_frame=90", "-f", "lavfi",
       "-i", "anoisesrc=seed=11:r=48000,atrim=end_sample=144144", "-c:v", "libx264", "-preset", "veryfast",
       "-crf", "16", "-bf", "3", "-g", "30", "-pix_fmt", "yuv420p", "-video_track_timescale", "30000",
       "-c:a", "aac", "-b:a", "192k", str(c["safe"]))
    ff("-itsoffset", "0.5", "-i", str(c["safe"]), "-i", str(c["safe"]), "-map", "0:v", "-map", "1:a", "-c", "copy",
       str(c["voff"]))
    # VFR competitor: id band on top of a texture, +-0.45 frame jitter, one dropped frame, with audio
    ff("-f", "lavfi", "-i", "testsrc2=s=128x96:r=30,trim=end_frame=150", "-f", "lavfi",
       "-i", f"color=c=black:s=128x32:r=30,format=yuv420p,{ID_GEQ},trim=end_frame=150",
       "-f", "lavfi", "-i", "anoisesrc=seed=5:r=44100,atrim=end_sample=220500",
       "-filter_complex", "[1:v][0:v]vstack,select='not(eq(n\\,70))',settb=1/90000,"
       "setpts='PTS+0.45*sin(N*1.7)/30/TB'[v]", "-map", "[v]", "-map", "2:a",
       "-c:v", "libx264", "-crf", "12", "-pix_fmt", "yuv420p", "-fps_mode", "passthrough",
       "-enc_time_base:v", "1/90000", "-video_track_timescale", "90000", "-c:a", "aac", str(c["vfr"]))
    ff("-display_rotation", "90", "-i", str(c["safe"]), "-c", "copy", str(c["rot"]))
    ff("-f", "lavfi", "-i", "testsrc2=s=162x92:r=25,crop=161:91:0:0,format=yuv444p,setsar=4/3,trim=end_frame=25",
       "-c:v", "ffv1", str(c["odd"]))
    ff("-f", "lavfi", "-i", "testsrc2=s=160x90:r=30000/1001,trim=end_frame=60", "-f", "lavfi",
       "-i", "anoisesrc=seed=3:r=48000,atrim=end_sample=96096", "-c:v", "libvpx-vp9", "-deadline", "realtime",
       "-cpu-used", "8", "-b:v", "600k", "-c:a", "libopus", str(c["webm"]))
    ff("-f", "lavfi", "-i", "testsrc2=s=160x90:r=24,trim=end_frame=48", "-c:v", "libvpx-vp9", "-deadline",
       "realtime", "-cpu-used", "8", "-b:v", "600k", str(c["webm_na"]))
    # a ProRes copy of raw.mp4 that is one frame late (drops frame 0 and repeats the last) - negative test
    ff("-i", str(c["safe"]), "-an", "-vf", "trim=start_frame=1,setpts=N/(30000/1001)/TB,tpad=stop=1:stop_mode=clone",
       "-c:v", "prores_aw", "-profile:v", "1", "-pix_fmt", "yuv422p10le", str(c["shift"]))
    return c


def make_cfg(tmp_path: Path, **kw) -> Config:
    return Config(out_dir=str(tmp_path / "out"), work_dir=str(tmp_path / "work"), **kw)


def test_ae_safe_raw_linked_unchanged(clips, tmp_path):
    cfg = make_cfg(tmp_path)
    before = file_hash(clips["safe"])
    info = probe(clips["safe"], "raw", cfg.work_dir)
    dlog = DecisionLog(tmp_path / "decisions.jsonl")
    res = conform(info, "raw", cfg, dlog)
    assert not res.conformed and res.file_rel == "media/raw.mp4"
    assert Path(res.path) == (tmp_path / "out" / "media" / "raw.mp4").resolve()
    assert file_hash(res.path) == before == file_hash(clips["safe"])     # untouched input, identical copy
    assert res.source_path == str(clips["safe"].resolve()) and res.verification["ok"]
    st = json.loads((tmp_path / "out" / "media" / ".conform.json").read_text())
    assert st["raw"]["params"]["mode"] == "copy" and st["raw"]["out_hash"] == before
    # re-running replaces the link without writing into the input's inode
    res2 = conform(info, "raw", cfg, dlog)
    assert res2.path == res.path and file_hash(clips["safe"]) == before
    # the AE-safe competitor is copied as competitor_ref.mp4
    rc = conform(probe(clips["safe"], "competitor", cfg.work_dir), "competitor", cfg, dlog)
    assert not rc.conformed and rc.file_rel == "media/competitor_ref.mp4"
    dlog.close()
    lines = [json.loads(x) for x in (tmp_path / "decisions.jsonl").read_text().splitlines()]
    assert any(e["decision"] == "copy" and e["role"] == "raw" for e in lines)


def test_large_ae_safe_raw_referenced_by_path(clips, tmp_path):
    cfg = make_cfg(tmp_path, large_file_bytes=1000)
    info = probe(clips["safe"], "raw", cfg.work_dir)
    res = conform(info, "raw", cfg, None)
    assert not res.conformed and res.file_rel == "" and res.path == res.file_abs == str(clips["safe"].resolve())
    assert not (tmp_path / "out" / "media" / "raw.mp4").exists()


def test_vfr_competitor_shows_frame_displayed_at_tk(clips, tmp_path):
    cfg = make_cfg(tmp_path)
    info = probe(clips["vfr"], "competitor", cfg.work_dir)
    assert info.vfr and info.fps == 30
    res = conform(info, "competitor", cfg, None)
    assert res.conformed and res.file_rel == "media/competitor_ref.mp4"
    ver = res.verification
    assert ver["ok"] and ver["method"] == "fps" and ver["samples"] >= 50 and ver["n_failed"] == 0
    # independent check: every output frame k shows the source frame with max{i : pts_i <= k/30}
    spts, sids, stb = decode_ids(clips["vfr"])
    opts, oids, otb = decode_ids(Path(res.path))
    rel = [Fraction(p - spts[0]) * stb for p in spts]
    med = Fraction(int(np.median(np.diff([p - spts[0] for p in spts])) * 2), 2) * stb
    expected_n = sum(1 for k in range(1000) if Fraction(k, 30) < rel[-1] + med)
    assert len(oids) == expected_n == ver["frames_expected"]
    shown = [sids[max(i for i, t in enumerate(rel) if t <= Fraction(k, 30))] for k in range(len(oids))]
    assert oids == shown
    assert [Fraction(p) * otb for p in opts] == [Fraction(k, 30) for k in range(len(opts))]   # CFR from 0
    out = probe(res.path, "competitor", cfg.work_dir)
    assert out.ae_issues == [] and out.acodec == "aac" and out.a_sample_rate == 48000
    # the naive rounding (fps default round=near) would NOT satisfy the rule -> the test is discriminative
    pts_i, tb, _ = load_pts_int(info)
    near = [min(range(len(rel)), key=lambda i: abs(rel[i] - Fraction(k, 30))) for k in range(len(oids))]
    assert [sids[i] for i in near] != shown
    idx = source_index_for_output(np.arange(len(oids)), "fps", pts_i, tb, Fraction(30))
    assert [sids[i] for i in idx] == shown


def test_raw_video_start_offset_and_audio_alignment(clips, tmp_path):
    cfg = make_cfg(tmp_path)
    info = probe(clips["voff"], "raw", cfg.work_dir)
    res = conform(info, "raw", cfg, None)
    assert res.conformed and res.file_rel == "media/raw_ae.mov"
    out = probe(res.path, "raw", cfg.work_dir)
    assert out.vcodec == "prores" and out.vprofile == "LT" and out.acodec == "pcm_s16le"
    assert out.nb_frames == 90 and out.first_pts_time == 0 and out.ae_issues == [] and out.fps == info.fps
    assert res.verification["ok"] and res.verification["samples"] >= 50
    # frame 0 of the conform == first decoded frame of the source
    with VideoReader(res.path) as a, VideoReader(clips["safe"]) as b:
        assert ssim(a.get(0, fmt="gray"), b.get(0, fmt="gray")) > 0.98
    # audio: conformed sample 0 == source audio 0.5 s after its start (video frame 0)
    y_out = extract_audio(res.path, 16000)
    y_src = extract_audio(clips["voff"], 16000)          # sample 0 = source audio start
    assert abs(lag_samples(y_out, y_src, 9000) - 8000) <= 1


def test_rotation_and_odd_sar_are_baked_in(clips, tmp_path):
    cfg = make_cfg(tmp_path)
    info = probe(clips["rot"], "raw", cfg.work_dir)
    res = conform(info, "raw", cfg, None)
    out = probe(res.path, "raw", cfg.work_dir)
    assert (out.width, out.height, out.rotation) == (96, 160, 0) and out.ae_issues == []
    assert res.verification["ok"]
    # odd 161x91 with SAR 4:3 -> 214x90 square pixels (width stretched to even, odd height cropped)
    cfg2 = make_cfg(tmp_path / "b")
    info2 = probe(clips["odd"], "raw", cfg2.work_dir)
    assert output_geometry(info2)["w"] == 214 and output_geometry(info2)["h"] == 90
    res2 = conform(info2, "raw", cfg2, None)
    out2 = probe(res2.path, "raw", cfg2.work_dir)
    assert (out2.width, out2.height, out2.sar) == (214, 90, 1) and out2.ae_issues == []
    assert res2.verification["ok"] and out2.nb_frames == 25


def test_webm_opus_and_no_audio(clips, tmp_path):
    cfg = make_cfg(tmp_path)
    info = probe(clips["webm"], "raw", cfg.work_dir)
    res = conform(info, "raw", cfg, None)
    out = probe(res.path, "raw", cfg.work_dir)
    assert out.container == "mov" and out.acodec == "pcm_s16le" and out.ae_issues == []
    assert out.nb_frames == 60 and res.verification["method"] == "restamp"
    # opus pre-skip is honoured: conformed audio lines up with the decoded source audio
    y_out, y_src = extract_audio(res.path, 16000), extract_audio(clips["webm"], 16000, offset_s=info.av_offset)
    assert abs(lag_samples(y_out, y_src, 400)) <= 1
    cfg2 = make_cfg(tmp_path / "na")
    info2 = probe(clips["webm_na"], "raw", cfg2.work_dir)
    assert not info2.has_audio
    res2 = conform(info2, "raw", cfg2, None)
    out2 = probe(res2.path, "raw", cfg2.work_dir)
    assert not out2.has_audio and out2.ae_issues == [] and out2.fps == 24 and out2.nb_frames == 48


def test_conform_cache_and_h264_fallback(clips, tmp_path):
    cfg = make_cfg(tmp_path)
    info = probe(clips["webm"], "raw", cfg.work_dir)
    dlog = DecisionLog(tmp_path / "d.jsonl")
    res = conform(info, "raw", cfg, dlog)
    mtime = Path(res.path).stat().st_mtime_ns
    res2 = conform(info, "raw", cfg, dlog)                       # .conform.json matches -> skipped
    assert Path(res2.path).stat().st_mtime_ns == mtime and res2.verification == res.verification
    cfg_h = make_cfg(tmp_path, conform_codec="h264")             # params change -> new transcode
    res3 = conform(info, "raw", cfg_h, dlog)
    assert res3.file_rel == "media/raw_ae.mp4" and res3.verification["ok"]
    out = probe(res3.path, "raw", cfg.work_dir)
    assert out.vcodec == "h264" and out.acodec == "aac" and out.ae_issues == []   # benign edit lists only
    dlog.close()
    decisions = [json.loads(x)["decision"] for x in (tmp_path / "d.jsonl").read_text().splitlines()]
    assert decisions == ["transcode", "cache_hit", "transcode"]
    assert ConformResult.from_dict(res.to_dict()) == res


def test_verification_detects_one_frame_offset(clips, tmp_path):
    cfg = make_cfg(tmp_path)
    src = probe(clips["safe"], "raw", cfg.work_dir)
    shifted = probe(clips["shift"], "raw", cfg.work_dir)
    plan = plan_transcode(src, "raw", cfg)
    ver = verify_transcode(src, shifted, plan)
    assert not ver["ok"] and ver["n_failed"] > 40
    assert any("better-than-neighbours" in p for p in ver["problems"])
