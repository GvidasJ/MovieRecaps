"""Unit tests for match_cuts.conform (DESIGN §5 conform.py) on tiny lavfi clips."""
from __future__ import annotations

import json
import os
import subprocess
from fractions import Fraction
from pathlib import Path

import numpy as np
import pytest

from match_cuts.common import DecisionLog, file_hash
from match_cuts.config import Config
from match_cuts.conform import (ConformResult, conform, ffmpeg_command, must_show_frames, output_geometry,
                                plan_transcode, source_index_for_output, ssim, sync_args, verify_transcode,
                                vfr_pts_shift)
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


def test_large_ae_safe_raw_is_hard_linked_into_media_else_referenced_by_path(clips, tmp_path, monkeypatch):
    """A big RAW (output/020's 3 GB 4K) goes into media/ as a hard link -- no copy, and the run keeps working when the
    input folder is moved or deleted; only where the drive cannot link is it referenced where it lies."""
    import os
    from match_cuts import conform as C
    cfg = make_cfg(tmp_path, large_file_bytes=1000)
    info = probe(clips["safe"], "raw", cfg.work_dir)
    res = conform(info, "raw", cfg, None)
    dst = tmp_path / "out" / "media" / "raw.mp4"
    assert not res.conformed and res.file_rel == "media/raw.mp4" and os.path.samefile(dst, clips["safe"])
    dst.unlink()
    monkeypatch.setattr(C.os, "link", lambda a, b: (_ for _ in ()).throw(OSError("another drive")))
    cfg2 = make_cfg(tmp_path / "b", large_file_bytes=1000)
    res = conform(probe(clips["safe"], "raw", cfg2.work_dir), "raw", cfg2, None)
    assert not res.conformed and res.file_rel == "" and res.path == res.file_abs == str(clips["safe"].resolve())
    assert not (tmp_path / "b" / "out" / "media" / "raw.mp4").exists()


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


# ----------------------------------------------------------------------------------------------
# Regressions: quantised VFR timestamps (F1), VFR tail (F2), ffmpeg < 5.1 (F7)
# ----------------------------------------------------------------------------------------------


def _id_clip(out: Path, n: int, rate: str, post: str, *enc: str) -> None:
    """Moving texture + 8-bit frame-id band; ``post`` = filters after the vstack (select / setpts)."""
    ff("-f", "lavfi", "-i", f"testsrc2=s=128x96:r={rate},trim=end_frame={n}", "-f", "lavfi",
       "-i", f"color=c=black:s=128x32:r={rate},format=yuv420p,{ID_GEQ},trim=end_frame={n}",
       "-filter_complex", f"[1:v][0:v]vstack,{post}[v]", "-map", "[v]", "-c:v", "libx264", "-preset", "veryfast",
       "-crf", "12", "-pix_fmt", "yuv420p", "-fps_mode", "passthrough", *enc, str(out))


@pytest.fixture(scope="module")
def vfr_clips(tmp_path_factory) -> dict[str, Path]:
    d = tmp_path_factory.mktemp("conform_vfr_clips")
    c = {"ms": d / "obs.mkv", "t600": d / "tail600.mp4", "t90k": d / "tail90k.mp4"}
    # OBS-like MKV: 29.97 fps CFR content, 1 ms time base (PTS rounded), one dropped frame -> flagged VFR
    _id_clip(c["ms"], 240, "30000/1001", "select='not(eq(n\\,100))'")
    # jittery phone-like VFR (dropped frame at n=20) whose last two frames fall inside the FINAL output slot
    # (x = 88.1 and 88.5 slots): the stream ends (last PTS + a 1-tick or pts-delta duration) before slot 89
    for key, tb in (("t600", 600), ("t90k", 90000)):
        _id_clip(c[key], 90, "30", f"select='not(eq(n\\,20))',settb=1/{tb},setpts='(if(lt(N\\,20)\\,N\\,N+1)"
                 f"+0.2*sin(N*1.3)*lt(N\\,87)+0.1*eq(N\\,87)-0.5*eq(N\\,88))/30/TB'",
                 "-enc_time_base:v", f"1/{tb}", "-video_track_timescale", str(tb))
    return c


def _rule_ids(spts: list[int], sids: list[int], stb: Fraction, fps: Fraction, n: int) -> list[int]:
    """Ids the 'frame displayed at t_k' rule shows, with the conform's quantisation shift (exact)."""
    shift = vfr_pts_shift(stb, fps)
    rel = [Fraction(p - spts[0]) * stb for p in spts]
    return [sids[max(i for i, t in enumerate(rel) if t - shift <= Fraction(k) / fps)] for k in range(n)]


def test_vfr_ms_timebase_conform_keeps_every_frame(vfr_clips, tmp_path):
    """F1: ms-rounded PTS must not push frames into their successor's slot (~1/3 were dropped)."""
    cfg = make_cfg(tmp_path)
    info = probe(vfr_clips["ms"], "raw", cfg.work_dir)
    assert info.vfr and info.fps == Fraction(30000, 1001) and info.nb_frames == 239
    pts_i, tb, _ = load_pts_int(info)
    assert tb == Fraction(1, 1000)
    plan = plan_transcode(info, "raw", cfg)
    assert "settb=1/1000,setpts=max(PTS-STARTPTS-1\\,0)" in plan["vf"] and plan["pts_shift"] == "1/1000"
    res = conform(info, "raw", cfg, None)
    ver = res.verification
    assert res.conformed and ver["ok"] and ver["method"] == "fps", ver.get("problems")
    spts, sids, stb = decode_ids(vfr_clips["ms"])
    _, oids, _ = decode_ids(Path(res.path))
    assert len(oids) == ver["frames_expected"] == 240
    # independent of the tool's rule: EVERY source frame is shown; only the dropped frame's gap duplicates
    assert set(oids) == set(sids)
    dups = [k for k in range(1, len(oids)) if oids[k] == oids[k - 1]]
    assert len(dups) == 1 and oids[dups[0]] == 99                      # frame 100 is missing in the source
    assert oids == _rule_ids(spts, sids, stb, info.fps, len(oids))
    cov = ver["coverage"]
    assert cov["full"] and cov["checked"] == 239 and cov["missing"] == 0 and cov["rule_dropped"] == 0


def test_vfr_verification_is_independent_of_the_rule(vfr_clips, tmp_path):
    """F1: a conform made with the UNSHIFTED rule passes the rule-based SSIM samples (they share its
    blind spot) but must fail the content check (source frames with a ~1-slot interval never shown)."""
    import subprocess as sp
    cfg = make_cfg(tmp_path)
    info = probe(vfr_clips["ms"], "raw", cfg.work_dir)
    out = tmp_path / "old_rule.mp4"
    sp.run(["ffmpeg", "-v", "error", "-nostdin", "-y", "-i", str(info.path), "-vf",
            "setpts=PTS-STARTPTS,fps=fps=30000/1001:round=up,tpad=stop=-1:stop_mode=clone,trim=end_frame=240,"
            "format=yuv420p", "-fps_mode", "passthrough", "-c:v", "libx264", "-crf", "12", str(out)], check=True)
    # the plan the old conform claimed to follow: unshifted 'max{i : pts_i <= k/fps}'
    old = dict(plan_transcode(info, "raw", cfg), pts_shift="0", expected_frames=240, codec="h264_ref")
    out_info = probe(out, "raw", cfg.work_dir)
    ver = verify_transcode(info, out_info, old)
    assert ver["n_failed"] == 0 and ver["samples"] >= 50                # the rule check alone is blind
    assert not ver["ok"] and ver["coverage"]["n_unexplained"] > 50
    assert any("must be shown are missing" in p for p in ver["problems"])
    _, oids, _ = decode_ids(out)
    assert len(set(oids)) < 180                                          # really ~1/3 of the frames lost


def test_must_show_frames_rule_independent_requirements():
    """F1: the requirements the content check enforces, stated without the conform's ffmpeg rule."""
    fps = Fraction(30000, 1001)
    ms = [round(Fraction(i * 1001, 30)) for i in range(120) if i != 50]          # 1 ms PTS, frame 50 dropped
    req = must_show_frames(np.array(ms), Fraction(1, 1000), fps, n_out=120)
    assert req["long"] | req["boundary"] == set(range(119))                    # every frame must be shown
    late = [j for j in range(119) if Fraction(ms[j], 1000) * fps > round(Fraction(ms[j], 1000) * fps)]
    assert late and set(late) <= req["boundary"] and not set(late) <= req["long"]    # the frames once dropped
    # real jitter (+-0.2 frame at 1/90000): frames 0.2 slot late sharing a slot with an early successor may
    # be dropped; the unshifted/shifted rule drops exactly frames outside the requirement
    jit = [round(Fraction(90000, 30) * (i + Fraction(1, 5) * (1 if i % 2 else -1))) for i in range(60)]
    jit[0] = 0
    req2 = must_show_frames(np.array(jit), Fraction(1, 90000), Fraction(30), n_out=60)
    idx = source_index_for_output(np.arange(60), "fps", np.array(jit), Fraction(1, 90000), Fraction(30))
    dropped = set(range(60)) - set(idx.tolist())
    assert dropped and not (dropped & (req2["long"] | req2["boundary"]))


@pytest.mark.parametrize("key", ["t600", "t90k"])
def test_vfr_last_frames_inside_the_final_slot(vfr_clips, tmp_path, key):
    """F2: the last source frame(s) falling late inside the final output slot must still be shown there
    (the conform used to end early and clone an older frame -> verification failed -> run aborted)."""
    cfg = make_cfg(tmp_path)
    info = probe(vfr_clips[key], "competitor", cfg.work_dir)
    assert info.vfr and info.fps == 30 and info.nb_frames == 89
    res = conform(info, "competitor", cfg, None)
    ver = res.verification
    assert ver["ok"] and ver["frames_expected"] == 90 and ver["coverage"]["n_unexplained"] == 0
    spts, sids, stb = decode_ids(vfr_clips[key])
    _, oids, _ = decode_ids(Path(res.path))
    assert len(oids) == 90 and oids[-1] == sids[-1] == 89               # final slot shows the last frame
    assert oids == _rule_ids(spts, sids, stb, info.fps, 90)
    plan = plan_transcode(info, "competitor", cfg)
    vf = plan["vf"].split(",")
    assert vf.index(next(x for x in vf if x.startswith("tpad"))) < vf.index(next(x for x in vf if x.startswith("fps")))


def test_ffmpeg_sync_flag_by_version(clips, tmp_path):
    """F7: '-fps_mode' only exists since ffmpeg 5.1; older builds get '-vsync 0'."""
    cfg = make_cfg(tmp_path)
    plan = plan_transcode(probe(clips["webm"], "raw", cfg.work_dir), "raw", cfg)
    old = ffmpeg_command("in.webm", "out.mov", plan, ffmpeg_version=(4, 4, 2))
    assert "-fps_mode" not in old and old[old.index("-vsync") + 1] == "0"
    new = ffmpeg_command("in.webm", "out.mov", plan, ffmpeg_version=(6, 1, 1))
    assert "-vsync" not in new and new[new.index("-fps_mode") + 1] == "passthrough"
    assert sync_args((5, 1)) == ("-fps_mode", "passthrough") and sync_args((5, 0, 3)) == ("-vsync", "0")
    assert sync_args(()) == ("-fps_mode", "passthrough")                # unknown (git build) -> current


@pytest.mark.skipif(os.name == "nt", reason="the fake ffmpeg 4.4 is a POSIX shell wrapper")
def test_conform_runs_on_ffmpeg_older_than_5_1(clips, tmp_path, monkeypatch):
    """F7: with an ffmpeg 4.4 (no -fps_mode) every transcode used to fail with 'Unrecognized option'."""
    import shutil as sh
    real = sh.which("ffmpeg")
    fake = tmp_path / "ffmpeg44"
    fake.write_text("#!/bin/sh\n"
                    "for a in \"$@\"; do\n"
                    "  if [ \"$a\" = \"-version\" ]; then echo 'ffmpeg version 4.4.2-0ubuntu0.22.04.1'; exit 0; fi\n"
                    "  if [ \"$a\" = \"-fps_mode\" ]; then echo \"Unrecognized option 'fps_mode'.\" >&2; exit 1; fi\n"
                    "done\n"
                    f"exec {real} \"$@\"\n")
    fake.chmod(0o755)
    monkeypatch.setenv("FFMPEG", str(fake))
    cfg = make_cfg(tmp_path)
    info = probe(clips["webm"], "raw", cfg.work_dir)
    dlog = DecisionLog(tmp_path / "d.jsonl")
    res = conform(info, "raw", cfg, dlog)
    dlog.close()
    assert res.conformed and res.verification["ok"]
    assert probe(res.path, "raw", cfg.work_dir).nb_frames == 60
