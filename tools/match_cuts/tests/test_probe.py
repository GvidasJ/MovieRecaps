"""Unit tests for match_cuts.probe (DESIGN §5 probe.py) on tiny lavfi clips."""
from __future__ import annotations

import subprocess
from fractions import Fraction
from pathlib import Path

import numpy as np
import pytest

from match_cuts.model import StreamInfo
from match_cuts.probe import (ae_issues, display_geometry, input_warnings, load_pts, load_pts_int, nearest_common_rate,
                              nominal_fps, probe, probe_extra, read_edit_lists, reader_sar, timing_stats,
                              truncation_info)

ID_GEQ = ("geq=lum='if(lt(Y,16),if(mod(floor(N/pow(2,floor(X/16))),2),235,16),"
          "if(mod(floor(N/pow(2,floor(X/16))),2),16,235))'")


def ff(*args: str) -> None:
    subprocess.run(["ffmpeg", "-v", "error", "-nostdin", "-y", *args], check=True)


@pytest.fixture(scope="module")
def clips(tmp_path_factory) -> dict[str, Path]:
    d = tmp_path_factory.mktemp("probe_clips")
    c = {k: d / v for k, v in {
        "safe": "safe.mp4", "voff": "voff.mp4", "trim": "trim.mp4", "rot": "rot.mp4", "rotm": "rotm.mp4",
        "sar": "sar.mp4", "vfr": "vfr.mp4", "webm": "opus.webm", "noaudio": "noaudio.mp4"}.items()}
    ff("-f", "lavfi", "-i", "testsrc2=s=160x90:r=30000/1001,trim=end_frame=90", "-f", "lavfi",
       "-i", "anoisesrc=seed=7:r=48000,atrim=end_sample=144144", "-c:v", "libx264", "-preset", "veryfast",
       "-crf", "18", "-bf", "3", "-g", "30", "-pix_fmt", "yuv420p", "-video_track_timescale", "30000",
       "-c:a", "aac", str(c["safe"]))
    # video starts 0.5 s after the audio (empty edit + start_time 0.5)
    ff("-itsoffset", "0.5", "-i", str(c["safe"]), "-i", str(c["safe"]), "-map", "0:v", "-map", "1:a", "-c", "copy",
       str(c["voff"]))
    # stream-copy trim at a non-keyframe -> edit list that trims decoded frames
    ff("-ss", "0.5", "-i", str(c["safe"]), "-c", "copy", str(c["trim"]))
    ff("-display_rotation", "90", "-i", str(c["safe"]), "-c", "copy", str(c["rot"]))
    ff("-display_rotation", "-90", "-i", str(c["safe"]), "-c", "copy", str(c["rotm"]))
    ff("-f", "lavfi", "-i", "testsrc2=s=180x180:r=25,trim=end_frame=25", "-c:v", "libx264", "-crf", "18",
       "-pix_fmt", "yuv420p", "-aspect", "16:9", str(c["sar"]))
    # VFR: +-0.45 frame PTS jitter and one dropped frame; container avg_frame_rate ~29.78 (not a common rate)
    ff("-f", "lavfi", "-i", f"color=c=black:s=128x32:r=30,format=gray,{ID_GEQ},trim=end_frame=150,"
       "select='not(eq(n\\,70))',settb=1/90000,setpts='PTS+0.45*sin(N*1.7)/30/TB'",
       "-c:v", "libx264", "-qp", "0", "-pix_fmt", "yuv420p", "-fps_mode", "passthrough",
       "-enc_time_base:v", "1/90000", "-video_track_timescale", "90000", str(c["vfr"]))
    ff("-f", "lavfi", "-i", "testsrc2=s=160x90:r=30000/1001,trim=end_frame=60", "-f", "lavfi",
       "-i", "anoisesrc=seed=3:r=48000,atrim=end_sample=96096", "-c:v", "libvpx-vp9", "-deadline", "realtime",
       "-cpu-used", "8", "-b:v", "500k", "-c:a", "libopus", str(c["webm"]))
    ff("-f", "lavfi", "-i", "testsrc2=s=160x90:r=24000/1001,trim=end_frame=48", "-c:v", "libx264", "-crf", "18",
       "-pix_fmt", "yuv420p", str(c["noaudio"]))
    return c


def test_ae_safe_mp4_benign_edit_lists(clips, tmp_path):
    tracks = read_edit_lists(clips["safe"])
    by_handler = {t.handler: t for t in tracks}
    # ffmpeg writes a single-entry elst for the B-frame delay (2 frames) and the AAC priming (1024)
    assert by_handler["vide"].entries[0][1] == 2002 and len(by_handler["vide"].entries) == 1
    assert by_handler["soun"].entries[0][1] == 1024
    info = probe(clips["safe"], "raw", tmp_path)
    assert info.ae_issues == [] and not info.edit_list          # benign -> NOT an issue
    assert info.container == "mp4" and info.vcodec == "h264"
    assert info.fps == Fraction(30000, 1001) and not info.vfr
    assert info.nb_frames == 90 and info.width == 160 and info.height == 90
    assert (info.display_width, info.display_height) == (160, 90)
    assert info.has_audio and info.acodec == "aac" and info.a_sample_rate == 48000 and info.a_channels == 1
    assert abs(info.av_offset) < 1e-6 and info.first_pts_time == 0.0
    assert info.file_hash and info.file_size == clips["safe"].stat().st_size
    pts = load_pts(info)
    np.testing.assert_allclose(pts, np.arange(90) * 1001 / 30000, atol=1e-9)
    pi, tb, origin = load_pts_int(info)
    assert tb == Fraction(1, 30000) and origin == 0 and pi[1] == 1001
    assert info.duration == pytest.approx(90 * 1001 / 30000)


def test_probe_cache_refreshes_path_and_role(clips, tmp_path):
    a = probe(clips["safe"], "raw", tmp_path)
    b = probe(clips["safe"], "competitor", tmp_path)
    assert b.role == "competitor" and a.to_dict() | {"role": "x"} == b.to_dict() | {"role": "x"}
    assert list((tmp_path / "cache" / "probe").glob("*.json"))


def test_video_start_offset(clips, tmp_path):
    info = probe(clips["voff"], "raw", tmp_path)
    assert info.v_start_time == pytest.approx(0.5) and info.first_pts_time == pytest.approx(0.5)
    assert info.av_offset == pytest.approx(-0.5, abs=1e-6)       # audio starts 0.5 s BEFORE video frame 0
    codes = {i.split(":")[0] for i in info.ae_issues}
    assert {"start_time", "edit_list"} <= codes and info.edit_list
    assert any("empty edit" in i for i in info.ae_issues)
    assert info.nb_frames == 90


def test_trimming_edit_list_is_an_issue(clips, tmp_path):
    info = probe(clips["trim"], "raw", tmp_path)
    assert info.edit_list
    assert any(i.startswith("edit_list: video edit starts") for i in info.ae_issues)
    assert info.nb_frames == 75                                   # measured by decoding, not the sample table


def test_rotation_side_data(clips, tmp_path):
    info = probe(clips["rot"], "raw", tmp_path)       # display matrix +90 (CCW) -> 270 clockwise
    assert info.rotation == 270 and (info.display_width, info.display_height) == (90, 160)
    assert any(i.startswith("rotation") for i in info.ae_issues)
    info2 = probe(clips["rotm"], "raw", tmp_path)
    assert info2.rotation == 90 and (info2.display_width, info2.display_height) == (90, 160)


def test_rotation_matches_ffmpeg_autorotate(clips, tmp_path):
    from match_cuts.media import VideoReader
    info = probe(clips["rot"], "raw", tmp_path)
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", str(clips["rot"]), "-frames:v", "1", "-f", "rawvideo",
                          "-pix_fmt", "gray", "-"], capture_output=True, check=True).stdout
    ref = np.frombuffer(raw, np.uint8).reshape(info.display_height, info.display_width).astype(int)
    with VideoReader(clips["rot"], rotation=info.rotation, sar=reader_sar(info)) as rd:
        img = rd.get(0, fmt="gray").astype(int)
    assert img.shape == ref.shape and np.abs(img - ref).mean() < 1.0


def test_sar(clips, tmp_path):
    info = probe(clips["sar"], "raw", tmp_path)
    assert info.sar == Fraction(16, 9) and info.dar == Fraction(16, 9)
    assert (info.display_width, info.display_height) == (320, 180)
    assert info.fps == 25 and any(i.startswith("sar") for i in info.ae_issues)
    assert not info.has_audio


def test_vfr_nominal_rate_from_pts_cadence(clips, tmp_path):
    info = probe(clips["vfr"], "competitor", tmp_path)
    assert info.vfr and info.pts_jitter > 0.1
    # avg_frame_rate ~29.78 is nearer to 30000/1001 than to 30, but the PTS cadence is exactly 30
    assert float(info.avg_frame_rate) < 29.9 and info.fps == Fraction(30)
    assert info.nb_frames == 149
    assert any(i.startswith("vfr") for i in info.ae_issues)


def test_webm_opus(clips, tmp_path):
    info = probe(clips["webm"], "raw", tmp_path)
    codes = {i.split(":")[0] for i in info.ae_issues}
    assert info.container == "webm" and info.vcodec == "vp9" and info.acodec == "opus"
    assert {"container", "vcodec", "acodec"} <= codes
    assert info.fps == Fraction(30000, 1001) and not info.vfr     # ms timestamps are not VFR
    assert info.nb_frames == 60


def test_no_audio(clips, tmp_path):
    info = probe(clips["noaudio"], "raw", tmp_path)
    assert not info.has_audio and info.av_offset == 0.0 and info.ae_issues == []
    assert info.fps == Fraction(24000, 1001) and info.nb_frames == 48


def test_nominal_fps_rules():
    assert nearest_common_rate(Fraction(2999, 100)) == 30             # nearest, not the first within 1 %
    assert nearest_common_rate(Fraction(2987, 100)) == Fraction(30000, 1001)
    assert nearest_common_rate(17.3) is None
    pts = np.arange(300) / 30.0
    pts = np.delete(pts, [50, 120])                                     # dropped frames
    assert nominal_fps(Fraction(2980, 100), Fraction(90000), pts)[0] == 30
    ms = np.round(np.arange(300) * 1001 / 30000, 3)                     # ms-rounded 29.97
    assert nominal_fps(Fraction(30000, 1001), Fraction(30000, 1001), ms)[0] == Fraction(30000, 1001)
    assert nominal_fps(Fraction(20), Fraction(20))[0] == 20            # exact uncommon CFR rate
    with pytest.raises(ValueError, match="ambiguous nominal fps"):
        nominal_fps(Fraction(173, 10), Fraction(90000))


def test_timing_stats_detects_drift_and_jitter():
    f = Fraction(30)
    assert timing_stats(np.arange(100) / 30.0, f) == (pytest.approx(0.0, abs=1e-9), False)
    j, vfr = timing_stats(np.arange(3000) / 29.97, f)                  # tiny per-frame error, big drift
    assert vfr and j > 0.1
    j, vfr = timing_stats(np.round(np.arange(300) / 30.0, 3), f)        # ms rounding only
    assert not vfr and j < 0.05


def test_display_geometry_and_reader_sar():
    assert display_geometry(1920, 1080, 90, Fraction(1)) == (1080, 1920, 1)
    assert display_geometry(360, 360, 0, Fraction(16, 9)) == (640, 360, Fraction(16, 9))
    w, h, s = display_geometry(360, 240, 90, Fraction(2))              # rotate first: SAR applies to y
    assert (w, h, s) == (240, 720, Fraction(1, 2))
    info = StreamInfo(path="x", role="raw", rotation=90, sar=Fraction(2))
    assert reader_sar(info) == Fraction(1, 2)


def test_ae_issues_rules():
    base = dict(path="x", role="raw", container="mp4", vcodec="h264", pix_fmt="yuv420p", width=1920, height=1080,
                fps=Fraction(30))
    assert ae_issues(StreamInfo(**base)) == []
    assert ae_issues(StreamInfo(**{**base, "vcodec": "hevc"}))[0].startswith("vcodec")
    assert ae_issues(StreamInfo(**{**base, "pix_fmt": "yuv420p10le"}))[0].startswith("vcodec")
    assert ae_issues(StreamInfo(**{**base, "vcodec": "prores", "container": "mov", "pix_fmt": "yuv422p10le"})) == []
    assert ae_issues(StreamInfo(**{**base, "width": 1919}))[0].startswith("odd_dims")
    assert ae_issues(StreamInfo(**{**base, "has_audio": True, "acodec": "mp3"}))[0].startswith("acodec")
    assert ae_issues(StreamInfo(**{**base, "has_audio": True, "acodec": "pcm_s24le"})) == []
    carried = StreamInfo(**{**base, "ae_issues": ["interlaced: field order tt", "vfr: stale"]})
    assert ae_issues(carried) == ["interlaced: field order tt"]      # probe-only codes carried, others recomputed


def test_probe_without_decode_pass(clips, tmp_path):
    info = probe(clips["safe"], "raw", tmp_path, decode=False)
    assert info.pts_file == "" and info.nb_frames == 90 and not info.vfr
    assert info.fps == Fraction(30000, 1001) and info.ae_issues == []
    np.testing.assert_allclose(load_pts(info), np.arange(90) * 1001 / 30000)


# ----------------------------------------------------------------------------------------------
# Regression F8: truncated / partially downloaded inputs are reported (input_warnings, not ae_issues)
# ----------------------------------------------------------------------------------------------


@pytest.fixture(scope="module")
def partial(tmp_path_factory) -> dict[str, Path]:
    d = tmp_path_factory.mktemp("probe_partial")
    c = {k: d / v for k, v in {"mkv": "full.mkv", "mkv_part": "part.mkv", "mp4": "full.mp4",
                                "mp4_part": "part.mp4", "moov_end": "moov_end.mp4", "moov_part": "moov_part.mp4",
                                "long_audio": "long_audio.mp4", "long_audio_mkv": "long_audio.mkv",
                                "late_video_mkv": "late_video.mkv"}.items()}
    src = ["-f", "lavfi", "-i", "testsrc2=s=320x180:r=30,trim=end_frame=300", "-f", "lavfi",
           "-i", "anoisesrc=seed=3:r=48000,atrim=end_sample=480000"]
    ff(*src, "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p", "-c:a", "libopus",
       str(c["mkv"]))
    ff(*src, "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p", "-c:a", "aac",
       "-movflags", "+faststart", str(c["mp4"]))
    ff(*src, "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p", "-c:a", "aac",
       str(c["moov_end"]))
    for full, part in (("mkv", "mkv_part"), ("mp4", "mp4_part"), ("moov_end", "moov_part")):
        data = c[full].read_bytes()
        c[part].write_bytes(data[:len(data) // 3])           # an interrupted download / copy
    # legitimately longer audio (5 s) than video (3 s): NOT a truncation
    for key, acodec in (("long_audio", "aac"), ("long_audio_mkv", "libopus")):
        ff("-f", "lavfi", "-i", "testsrc2=s=160x90:r=30,trim=end_frame=90", "-f", "lavfi",
           "-i", "anoisesrc=seed=3:r=48000,atrim=end_sample=240000", "-c:v", "libx264", "-preset", "veryfast",
           "-pix_fmt", "yuv420p", "-c:a", acodec, str(c[key]))
    # video starting 4 s after the audio: Matroska's DURATION tag holds the track END timestamp (7 s)
    ff("-itsoffset", "4", "-i", str(c["long_audio_mkv"]), "-i", str(c["long_audio_mkv"]), "-map", "0:v", "-map", "1:a",
       "-c", "copy", str(c["late_video_mkv"]))
    return c


def test_truncated_mkv_is_reported(partial, tmp_path):
    full = probe(partial["mkv"], "raw", tmp_path)
    assert input_warnings(full) == [] and truncation_info(full) is None
    info = probe(partial["mkv_part"], "raw", tmp_path)
    assert 0 < info.nb_frames < 150 and info.container_duration > 9.9
    w = input_warnings(info)
    assert len(w) == 1 and w[0].startswith("truncated:") and "NOT-IN-RAW" in w[0]
    t = truncation_info(info)
    assert t["header_s"] == pytest.approx(10.0, abs=0.05) and t["missing_s"] > 5
    assert t["decoded_s"] == pytest.approx(info.nb_frames / 30, abs=0.05)
    assert not any("truncat" in i for i in info.ae_issues)           # ae_issues stay AE issues
    assert probe_extra(info)["truncation"]["missing_s"] == t["missing_s"]
    # the role of a cached probe decides the wording
    comp = probe(partial["mkv_part"], "competitor", tmp_path)
    assert "competitor video" in input_warnings(comp)[0] and "NOT-IN-RAW" not in input_warnings(comp)[0]


def test_truncated_faststart_mp4_is_reported(partial, tmp_path):
    assert input_warnings(probe(partial["mp4"], "raw", tmp_path)) == []
    info = probe(partial["mp4_part"], "raw", tmp_path)
    assert 0 < info.nb_frames < 200
    assert input_warnings(info) and truncation_info(info)["header_source"] == "stream header duration"
    # the missing samples are an incomplete index, not an edit list (the conform still happens)
    assert not info.edit_list and not any(i.startswith("edit_list") for i in info.ae_issues)
    assert any(i.startswith("incomplete:") for i in info.ae_issues)


def test_mp4_without_moov_names_the_cause(partial, tmp_path):
    with pytest.raises(RuntimeError, match="moov atom.*incomplete"):
        probe(partial["moov_part"], "raw", tmp_path)


def test_longer_audio_is_not_a_truncation(partial, tmp_path):
    for key in ("long_audio", "long_audio_mkv", "late_video_mkv"):
        info = probe(partial[key], "raw", tmp_path)
        assert info.nb_frames == 90 and info.container_duration > 4.5
        assert input_warnings(info) == [] and truncation_info(info) is None
    assert probe(partial["late_video_mkv"], "raw", tmp_path).v_start_time > 3.9


def test_input_warnings_for_probe_cache_entries_without_the_finding(partial, tmp_path):
    import json
    info = probe(partial["mkv_part"], "raw", tmp_path)
    side = Path(info.pts_file).with_name(Path(info.pts_file).name.replace(".pts.npy", ".extra.json"))
    ex = json.loads(side.read_text())
    for k in ("truncation", "warnings"):
        ex.pop(k)
    side.write_text(json.dumps(ex))                                  # an entry written by an older probe
    again = probe(partial["mkv_part"], "raw", tmp_path)               # cache hit
    w = input_warnings(again)
    assert len(w) == 1 and "DURATION tag" in w[0]                   # re-read from the stored ffprobe JSON
    Path(again.pts_file).with_name(Path(again.pts_file).name.replace(".pts.npy", ".ffprobe.json")).unlink()
    w = input_warnings(again)
    assert len(w) == 1 and "container duration" in w[0]


# ----------------------------------------------------------------------------------------------
# Regression real-world-new-paths:probe-flv-trunc: a container duration that is the end of a longer
# AUDIO track (FLV, Matroska without DURATION tags) is not a truncated file
# ----------------------------------------------------------------------------------------------


@pytest.fixture(scope="module")
def audio_tail(tmp_path_factory) -> dict[str, Path]:
    d = tmp_path_factory.mktemp("probe_audio_tail")
    c = {"flv": d / "tail.flv", "mkv_tagged": d / "tagged.mkv", "mkv": d / "notag.mkv", "flv_part": d / "part.flv"}
    src = ["-f", "lavfi", "-i", "testsrc2=s=160x90:r=30,trim=end_frame=90", "-f", "lavfi",
           "-i", "anoisesrc=seed=3:r=48000,atrim=end_sample=240000"]          # 3 s video, 5 s audio
    ff(*src, "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p", "-c:a", "aac", str(c["flv"]))
    ff(*src, "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p", "-c:a", "libopus",
       "-write_crc32", "0", str(c["mkv_tagged"]))
    data = c["mkv_tagged"].read_bytes()
    assert data.count(b"DURATION") == 2
    c["mkv"].write_bytes(data.replace(b"DURATION", b"DURATIOX"))    # a muxer that writes no DURATION tags
    fl = c["flv"].read_bytes()
    c["flv_part"].write_bytes(fl[:len(fl) // 3])                    # an interrupted download of the FLV
    return c


def test_container_duration_of_a_longer_audio_is_not_a_truncation(audio_tail, tmp_path):
    for key in ("flv", "mkv"):
        info = probe(audio_tail[key], "raw", tmp_path)
        ex = probe_extra(info)
        assert info.nb_frames == 90 and info.has_audio and info.container_duration > 4.9, key
        assert ex["header_duration_source"] == "container duration", key       # the path under test
        assert input_warnings(info) == [] and truncation_info(info) is None, key
        tails = ex["stream_tails"]
        assert tails["video_end_s"] == pytest.approx(3.0, abs=0.05), key
        assert tails["audio_end_s"] == pytest.approx(5.0, abs=0.1), key
        assert any("before the audio" in n and "not truncated" in n for n in ex["notes"]), key
        # a cache hit keeps the verdict without re-listing packets
        assert input_warnings(probe(audio_tail[key], "competitor", tmp_path)) == [], key
    # the tagged Matroska takes the DURATION-tag path and never needs the packet listing
    tagged = probe(audio_tail["mkv_tagged"], "raw", tmp_path)
    assert input_warnings(tagged) == [] and probe_extra(tagged)["stream_tails"] is None
    # a REALLY truncated FLV (both streams end early; onMetaData still announces 5 s) is still reported
    part = probe(audio_tail["flv_part"], "raw", tmp_path)
    w = input_warnings(part)
    assert len(w) == 1 and w[0].startswith("truncated:") and "container duration" in w[0]
    t = truncation_info(part)
    assert t["kind"] == "truncated" and t["audio_end_s"] < 3.0 and t["missing_s"] > 2.0


def test_a_seek_that_lists_no_packets_is_not_read_as_a_truncation(audio_tail, tmp_path, monkeypatch):
    """Task 10: ffprobe's seek to the listing's start now and then lands past the end of the file and lists no packet
    at all, exit status 0 (FFmpeg 8.0: 5 runs in 40 of the same command on this Matroska file, 1 in 40 on the FLV)
    -- read as 'the audio does not reach the container end', a false 'truncated' (the test above failed in every
    full suite run since Task 1). A listing that misses a stream is redone over the whole file."""
    from match_cuts import probe as P
    real = P._packet_ends
    calls: list = []

    def failed_seek(path, intervals, timeout=P.TAIL_TIMEOUT_S):
        calls.append(intervals)
        return {} if intervals is not None else real(path, intervals, timeout)
    monkeypatch.setattr(P, "_packet_ends", failed_seek)
    for key in ("flv", "mkv"):
        calls.clear()
        info = probe(audio_tail[key], "raw", tmp_path / key)
        assert input_warnings(info) == [] and truncation_info(info) is None, key
        tails = probe_extra(info)["stream_tails"]
        assert tails["intervals"] == "whole file" and calls == [calls[0], None] and calls[0] is not None, key
        assert tails["video_end_s"] == pytest.approx(3.0, abs=0.05), key
        assert tails["audio_end_s"] == pytest.approx(5.0, abs=0.1), key
    part = probe(audio_tail["flv_part"], "raw", tmp_path / "part")      # really truncated: still reported
    assert truncation_info(part)["kind"] == "truncated"


def test_old_probe_cache_entries_with_a_false_truncation_are_re_measured(audio_tail, tmp_path):
    import json
    for key, expect_warning in (("flv", False), ("flv_part", True)):
        info = probe(audio_tail[key], "raw", tmp_path)
        side = Path(info.pts_file).with_name(Path(info.pts_file).name.replace(".pts.npy", ".extra.json"))
        ex = json.loads(side.read_text())
        ex.pop("stream_tails")
        dec = float(load_pts(info)[-1]) + 1 / 30
        hdr = float(ex["header_video_duration"])
        # what the probe of the previous round stored: a truncation finding without the packet check
        ex["truncation"] = {"header_s": hdr, "header_source": "container duration", "decoded_s": dec,
                            "missing_s": hdr - dec}
        side.write_text(json.dumps(ex))
        again = probe(audio_tail[key], "raw", tmp_path)               # cache hit
        assert bool(input_warnings(again)) is expect_warning, key


def test_truncation_check_wording_by_stream_tails():
    from match_cuts.probe import truncation_check
    base = ("raw", 5.0, "container duration", 3.0)
    t = truncation_check(*base)
    assert t["kind"] == "truncated" and t["warning"].startswith("truncated:")
    # audio reaches the container end, video packets end where the decode ended: complete file
    assert truncation_check(*base, {"audio_end_s": 4.99, "video_end_s": 3.02}) is None
    # ... video packets run on past the decoded end: a damaged video stream, not a truncated download
    u = truncation_check(*base, {"audio_end_s": 5.0, "video_end_s": 4.9})
    assert u["kind"] == "undecodable" and "truncated" not in u["warning"] and "do not decode" in u["warning"]
    # ... no video packet found near the end: worded as a shorter video, never as a truncated download
    v = truncation_check(*base, {"audio_end_s": 5.0, "video_end_s": None})
    assert v["kind"] == "video_shorter" and "before its audio" in v["warning"] and "truncated /" not in v["warning"]
    # audio ends early too: truncated
    assert truncation_check(*base, {"audio_end_s": 3.1, "video_end_s": 3.0})["kind"] == "truncated"
    # tails only matter for a container duration (a stream header duration is the video's own)
    assert truncation_check("raw", 5.0, "stream header duration", 3.0,
                            {"audio_end_s": 5.0, "video_end_s": 3.0})["kind"] == "truncated"
