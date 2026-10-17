"""CONVERT (mr_app.core.convert) on tiny videos FFmpeg makes here (lavfi): H.264 + AAC in MP4 is kept, H.264 in MKV
rewrapped bit for bit, VP9 + Opus encoded to H.264 + AAC with every frame, the original deleted only after the
check, a failed check / Stop keeps it; 5.1, HEVC, ProRes, 10-bit, variable frame rate; one NVENC test (skipped
without NVENC). Run: python -m pytest tools/app/tests/test_convert.py"""
from __future__ import annotations

import shutil
import subprocess
import sys
import threading
from fractions import Fraction
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mr_app.core import convert as C, projects as P   # noqa: E402

pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
                                reason="FFmpeg is not installed")

FPS = "30000/1001"
VIDEO = ["-f", "lavfi", "-i", f"testsrc2=s=320x240:r={FPS}:d=2"]
SINE = ["-f", "lavfi", "-i", "sine=f=440:r=48000:d=2"]
BT709 = ["-vf", "setparams=color_primaries=bt709:color_trc=bt709:colorspace=bt709:range=tv"]
X264_FAST = ["-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p"]
VP9_FAST = ["-c:v", "libvpx-vp9", "-deadline", "realtime", "-cpu-used", "8", "-b:v", "400k"]

RECIPES = {
    "h264_aac.mp4": VIDEO + SINE + X264_FAST + BT709 + ["-c:a", "aac", "-b:a", "128k"],
    "h264_aac.mkv": VIDEO + SINE + X264_FAST + ["-c:a", "aac", "-b:a", "128k"],
    "h264_opus.mkv": VIDEO + SINE + X264_FAST + ["-c:a", "libopus", "-b:a", "96k"],
    "h264_opus.mp4": VIDEO + SINE + X264_FAST + ["-c:a", "libopus", "-b:a", "96k"],
    "vp9_opus.webm": VIDEO + SINE + VP9_FAST + ["-pix_fmt", "yuv420p"] + BT709 + ["-c:a", "libopus", "-b:a", "96k"],
    "h264_flac51.mkv": VIDEO + SINE + X264_FAST + ["-af", "pan=5.1(side)|FL=c0|FR=c0|FC=c0|LFE=c0|SL=c0|SR=c0",
                                                   "-c:a", "flac"],
    "hevc_aac.mkv": VIDEO + SINE + ["-c:v", "libx265", "-preset", "ultrafast", "-x265-params", "log-level=error",
                                    "-pix_fmt", "yuv420p", "-c:a", "aac"],
    "prores_pcm.mov": VIDEO + SINE + ["-c:v", "prores_ks", "-profile:v", "0", "-c:a", "pcm_s16le"],
    "vp9_10bit.webm": VIDEO + VP9_FAST + ["-pix_fmt", "yuv420p10le", "-profile:v", "2"],
    "vp9_vfr.webm": VIDEO + ["-vf", "settb=1/1000,setpts='(N*0.0333+if(mod(N,3),0,0.017))/TB'", "-fps_mode",
                             "passthrough", "-enc_time_base:v", "1:1000"] + VP9_FAST,
    "sound_only.m4a": SINE + ["-c:a", "aac"],
}


@pytest.fixture(scope="module")
def media(tmp_path_factory):
    """name -> a made file (None when this FFmpeg cannot make it)."""
    d = tmp_path_factory.mktemp("media")
    out = {}
    for name, args in RECIPES.items():
        p = d / name
        r = subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", *args, "-shortest", str(p)],
                           capture_output=True, text=True)
        out[name] = p if r.returncode == 0 and p.is_file() else None
    return out


def _copy(media, name: str, to: Path, as_name: str | None = None) -> Path:
    if media[name] is None:
        pytest.skip(f"this FFmpeg cannot make {name}")
    to.mkdir(parents=True, exist_ok=True)
    p = to / (as_name or name)
    shutil.copy2(media[name], p)
    return p


def _probe(p: Path, entries: str, stream: str = "v:0") -> str:
    return subprocess.run(["ffprobe", "-v", "error", "-select_streams", stream, "-show_entries", entries, "-of",
                           "csv=p=0", str(p)], capture_output=True, text=True).stdout.strip()


def _nframes(p: Path) -> int:
    """The video frames counted by decoding (the real number, not the header's)."""
    return int(subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-count_frames", "-show_entries",
                               "stream=nb_read_frames", "-of", "csv=p=0", str(p)], capture_output=True,
                              text=True).stdout.strip())


def _frame_hashes(p: Path) -> list[str]:
    out = subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-i", str(p), "-map", "0:v:0", "-f",
                          "framemd5", "-"], capture_output=True, text=True).stdout
    return [l.split(",")[-1].strip() for l in out.splitlines() if l and not l.startswith("#")]


# ---------------------------------------------------------------------------------------------------------------------
# keep / rewrap / encode
# ---------------------------------------------------------------------------------------------------------------------

def test_h264_and_aac_in_mp4_is_kept_and_nothing_is_deleted(media, tmp_path):
    src = _copy(media, "h264_aac.mp4", tmp_path, "Clip \U0001F602 [abc].mp4")
    n = _nframes(src)
    res = C.convert_file(src, log_path=tmp_path / "logs" / "convert_raw.log")
    assert (res.how, res.encoder, res.path, res.deleted_original) == ("keep", None, src, False)
    assert sorted(p.name for p in tmp_path.iterdir()) == ["Clip \U0001F602 [abc].mp4", "logs"]
    assert res.checks["frames"] == [n, n] and n >= 59 and "nothing to do" in res.note


def test_h264_in_mkv_is_rewrapped_bit_for_bit_and_the_original_deleted(media, tmp_path):
    src = _copy(media, "h264_aac.mkv", tmp_path / "proj", "Episode 1 [xyz].mkv")
    before = _frame_hashes(src)
    n = _nframes(src)
    seen = []
    res = C.convert_file(src, progress=lambda f, t: seen.append((f, t)))
    assert res.how == "rewrap" and res.encoder == "copy" and res.deleted_original
    assert res.path == tmp_path / "proj" / "Episode 1 [xyz].mp4" and not src.exists()
    assert sorted(p.name for p in (tmp_path / "proj").iterdir()) == ["Episode 1 [xyz].mp4"]
    assert _frame_hashes(res.path) == before                              # 100 % lossless
    assert res.checks["frames"] == [n, n] and len(before) == n and "without re-encoding" in res.note
    assert _probe(res.path, "stream=codec_name", "a:0") == "aac"         # AAC copied
    assert seen[0][0] == 0.0 and seen[-1][0] == 1.0


def test_opus_sound_beside_h264_is_made_aac_and_said(media, tmp_path):
    src = _copy(media, "h264_opus.mkv", tmp_path)
    res = C.convert_file(src)
    assert res.how == "rewrap" and "Opus" in res.note and "AAC 320 kb/s" in res.note
    assert _probe(res.path, "stream=codec_name,bit_rate", "a:0").startswith("aac,")


def test_vp9_and_opus_become_h264_and_aac_with_every_frame_and_its_colours(media, tmp_path, monkeypatch):
    src = _copy(media, "vp9_opus.webm", tmp_path, "Short \U0001F602 [4v3FsM0BzWQ].webm")
    n = _nframes(src)
    real_check = C.check
    there_at_check = []

    def spy(*a, **k):
        there_at_check.append(src.exists())                              # the original is still there
        return real_check(*a, **k)
    monkeypatch.setattr(C, "check", spy)
    res = C.convert_file(src, gpu="off", log_path=tmp_path / "convert.log")
    assert there_at_check == [True] and not src.exists() and res.deleted_original
    assert res.how == "encode" and res.encoder == "libx264" and res.from_codec == "vp9"
    assert res.path.name == "Short \U0001F602 [4v3FsM0BzWQ].mp4"
    out = C.inspect(res.path)
    assert (out.vcodec, out.acodec, out.width, out.height, out.fps, out.frames) == \
        ("h264", "aac", 320, 240, Fraction(30000, 1001), n)
    assert (out.color_primaries, out.color_transfer, out.color_space, out.color_range) == \
        ("bt709", "bt709", "bt709", "tv")
    assert _probe(res.path, "stream=profile,pix_fmt") == "High,yuv420p"
    durations = set(_probe(res.path, "packet=duration").split())
    assert len(durations) == 1                                            # exact constant-rate frame times
    assert res.checks["frames"] == [n, n]
    log = (tmp_path / "convert.log").read_text(encoding="utf-8")
    assert "-crf 13" in log and "-enc_time_base:v 1001:30000" in log and "-- OK" in log


def test_a_failed_check_keeps_the_original_and_removes_the_bad_copy(media, tmp_path, monkeypatch):
    src = _copy(media, "vp9_opus.webm", tmp_path)
    monkeypatch.setattr(C, "check", lambda *a, **k: (False, {"frames": [60, 59]}, ["59 frames instead of 60"]))
    with pytest.raises(C.ConvertError) as e:
        C.convert_file(src, gpu="off")
    assert "59 frames instead of 60" in str(e.value) and "the original is kept" in str(e.value)
    assert sorted(p.name for p in tmp_path.iterdir()) == ["vp9_opus.webm"]


def test_stop_removes_the_temp_file_and_keeps_the_original(media, tmp_path):
    src = _copy(media, "vp9_opus.webm", tmp_path)
    cancel = threading.Event()
    cancel.set()
    with pytest.raises(C.Cancelled):
        C.convert_file(src, gpu="off", cancel=cancel)
    assert sorted(p.name for p in tmp_path.iterdir()) == ["vp9_opus.webm"]


def test_keeping_the_original_of_an_mp4_gives_the_copy_another_name(media, tmp_path):
    src = _copy(media, "h264_opus.mp4", tmp_path)                       # H.264 + Opus in .mp4: rewrapped
    res = C.convert_file(src, delete_original=False)
    assert res.how == "rewrap" and not res.deleted_original and src.exists()
    assert res.path.name == "h264_opus_converted.mp4"
    res2 = C.convert_file(src)                                            # deleted: the original's own name
    assert res2.path == src and res2.deleted_original and _probe(src, "stream=codec_name", "a:0") == "aac"


def test_out_dir_and_ffmpeg_runs_hidden_at_below_normal_priority(media, tmp_path, monkeypatch):
    src = _copy(media, "vp9_opus.webm", tmp_path / "in")
    flags = []
    real_popen = subprocess.Popen

    def spy(*a, **k):
        cmd = a[0] if a else k.get("args")
        flags.append((cmd[0], "-progress" in cmd, k.get("creationflags", 0)))
        return real_popen(*a, **k)
    monkeypatch.setattr(C.subprocess, "Popen", spy)
    res = C.convert_file(src, out_dir=tmp_path / "out", gpu="off")
    assert res.path == tmp_path / "out" / "vp9_opus.mp4" and not src.exists()
    assert all(f & 0x08000000 for _, _, f in flags)                      # every FFmpeg / ffprobe hidden
    encodes = [f for _, enc, f in flags if enc]
    assert encodes and all(f & 0x4000 for f in encodes)                   # the encode at below-normal priority


def test_51_sound_stays_51_as_aac_640(media, tmp_path):
    src = _copy(media, "h264_flac51.mkv", tmp_path)
    res = C.convert_file(src)
    assert res.how == "rewrap"
    assert _probe(res.path, "stream=codec_name,channels,channel_layout", "a:0") == "aac,6,5.1"
    assert "640" in res.note


def test_hevc_in_mkv_is_rewrapped_with_the_tag_premiere_reads(media, tmp_path):
    src = _copy(media, "hevc_aac.mkv", tmp_path)
    res = C.convert_file(src)
    assert res.how == "rewrap" and _probe(res.path, "stream=codec_name,codec_tag_string") == "hevc,hvc1"


def test_prores_in_mov_is_kept(media, tmp_path):
    src = _copy(media, "prores_pcm.mov", tmp_path)
    res = C.convert_file(src)
    assert res.how == "keep" and src.exists() and "ProRes" in res.note


def test_10_bit_goes_to_x264_high10_on_the_cpu(media, tmp_path):
    src = _copy(media, "vp9_10bit.webm", tmp_path)
    info = C.inspect(src)
    assert info.bit_depth == 10 and C.plan(info).ten_bit
    assert C.choose_encoder(info, gpu="on")[0] == "libx264"
    res = C.convert_file(src, gpu="on")
    assert res.encoder == "libx264" and _probe(res.path, "stream=profile,pix_fmt") == "High 10,yuv420p10le"


def test_a_variable_frame_rate_keeps_its_own_frame_times(media, tmp_path):
    src = _copy(media, "vp9_vfr.webm", tmp_path)
    info = C.inspect(src)
    assert not C.video_timing(info).on_grid
    times_in = [float(t) for t in _probe(src, "packet=pts_time").split()]
    res = C.convert_file(src, gpu="off", log_path=tmp_path / "c.log")
    times_out = [float(t) for t in _probe(res.path, "packet=pts_time").split()]
    assert res.checks["frames"][0] == res.checks["frames"][1] == len(times_in)
    a, b = sorted(times_in), sorted(times_out)
    assert all(abs((x - a[0]) - (y - b[0])) < 0.0015 for x, y in zip(a, b))   # the same spacing (1 ms rounding)
    assert "-enc_time_base:v demux" in (tmp_path / "c.log").read_text(encoding="utf-8")


@pytest.mark.parametrize("name, cut, how", [("h264_aac.mp4", "cut.mov", "rewrap"), ("vp9_opus.webm", "cut.mp4", "encode")])
def test_a_trimmed_file_counts_only_the_frames_it_shows(media, tmp_path, name, cut, how):
    """A cut made without re-encoding keeps the frames before its start (for decoding) behind an edit list: the
    header counts them, the player does not show them -- the check counts what is shown, on both files."""
    src = _copy(media, name, tmp_path / "src")
    trimmed = tmp_path / cut
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-ss", "0.5", "-i", str(src), "-t", "1",
                    "-c", "copy", str(trimmed)], check=True)
    info = C.inspect(trimmed)
    shown = C.count_frames(trimmed, info.video_index)
    assert shown == _nframes(trimmed) and info.frames > shown              # the header counts the hidden ones
    res = C.convert_file(trimmed, gpu="off")
    assert res.how == how and res.checks["frames"] == [shown, shown] and _nframes(res.path) == shown


def test_not_a_video_is_said_in_plain_words(media, tmp_path):
    t = tmp_path / "notes.txt"
    t.write_text("hello")
    with pytest.raises(C.ConvertError, match="not a video"):
        C.convert_file(t)
    b = tmp_path / "junk.mp4"
    b.write_bytes(b"\x00" * 1000)
    with pytest.raises(C.ConvertError, match="not a video or it is damaged"):
        C.convert_file(b)
    a = _copy(media, "sound_only.m4a", tmp_path)
    with pytest.raises(C.ConvertError, match="has no picture"):
        C.convert_file(a)
    with pytest.raises(C.ConvertError, match="is not there"):
        C.convert_file(tmp_path / "gone.mp4")


@pytest.mark.skipif(not C.nvenc_available(), reason="no NVENC on this computer")
def test_nvenc_keeps_every_frame_too(media, tmp_path):
    src = _copy(media, "vp9_opus.webm", tmp_path)
    n = _nframes(src)
    res = C.convert_file(src, gpu="on")
    assert res.encoder == "h264_nvenc" and res.checks["frames"] == [n, n]
    assert _probe(res.path, "stream=codec_name,profile") == "h264,High"


# ---------------------------------------------------------------------------------------------------------------------
# the plan, the frame grid, the command line (no files)
# ---------------------------------------------------------------------------------------------------------------------

def _info(vcodec="h264", container="mp4", ext=".mp4", acodecs=("aac",), depth=8, channels=2, layout="stereo"):
    return C.MediaInfo(path=Path("C:/v/clip" + ext), container=container, vcodec=vcodec, profile=None,
                       pix_fmt="yuv420p", bit_depth=depth, width=1920, height=1080, fps=Fraction(24000, 1001),
                       frames=100, duration=4.17, acodec=acodecs[0] if acodecs else None, channels=channels,
                       video_index=0, audio=[{"index": k + 1, "codec": a, "channels": channels, "layout": layout,
                                              "rate": "48000"} for k, a in enumerate(acodecs)])


@pytest.mark.parametrize("kw, how", [
    (dict(), "keep"),
    (dict(acodecs=("mp3",)), "keep"),
    (dict(acodecs=()), "keep"),
    (dict(vcodec="hevc"), "keep"),
    (dict(ext=".m4v"), "rewrap"),
    (dict(container="mov", ext=".mov"), "rewrap"),
    (dict(container="mkv", ext=".mkv"), "rewrap"),
    (dict(acodecs=("opus",)), "rewrap"),
    (dict(acodecs=("aac", "ac3")), "rewrap"),
    (dict(vcodec="prores", container="mov", ext=".mov", acodecs=("pcm_s16le",)), "keep"),
    (dict(vcodec="dnxhd", container="mxf", ext=".mxf"), "keep"),
    (dict(vcodec="prores", container="mkv", ext=".mkv"), "encode"),
    (dict(vcodec="vp9", container="webm", ext=".webm", acodecs=("opus",)), "encode"),
    (dict(vcodec="av1"), "encode"),
    (dict(vcodec="vp8", container="webm", ext=".webm", acodecs=("vorbis",)), "encode"),
    (dict(vcodec="mpeg2video", container="mpegts", ext=".ts"), "encode"),
    (dict(vcodec="mpeg4", container="avi", ext=".avi", acodecs=("mp3",)), "encode"),
    (dict(vcodec="wmv3", container="asf", ext=".wmv", acodecs=("wmav2",)), "encode"),
])
def test_the_plan(kw, how):
    assert C.plan(_info(**kw)).how == how


def test_the_sound_plan():
    pl = C.plan(_info(vcodec="vp9", container="webm", ext=".webm", acodecs=("opus",), channels=6, layout="5.1(side)"))
    assert [(a.index, a.action, a.bitrate, a.remap_51) for a in pl.audio] == [(1, "aac", "640k", True)]
    pl = C.plan(_info(container="mkv", ext=".mkv", acodecs=("aac", "dts"), channels=8, layout="7.1"))
    assert [(a.action, a.bitrate) for a in pl.audio] == [("copy", None), ("aac", "768k")]
    assert "DTS" in pl.why


def test_the_frame_grid():
    fps = Fraction(30000, 1001)
    ms = [round(k * 1001 / 30000, 3) for k in range(3000)]               # WebM: times rounded to the millisecond
    assert C.on_grid(ms, fps)
    ms60 = [round(k * 1001 / 60000, 3) for k in range(3000)]
    assert C.on_grid([t + 0.0065 for t in ms60], Fraction(60000, 1001))  # an offset (Opus pre-skip) is fine
    assert not C.on_grid([t + 0.0083 for t in ms[:100]], Fraction(60, 1))  # half a frame: rounding could go either way
    vfr = [k * 0.0333 + (0.017 if k % 3 == 0 else 0) for k in range(100)]
    assert not C.on_grid(vfr, Fraction(30))
    assert not C.on_grid([0.0, 0.0, 0.033], Fraction(30))                 # two frames on one slot
    assert C.grid_time_base(fps) == "1001:30000" and C.grid_time_base(Fraction(25)) == "1:25"


def test_the_encoders_command_line():
    info = _info(vcodec="vp9", container="webm", ext=".webm", acodecs=("opus",))
    info.color_primaries = info.color_transfer = info.color_space = "bt709"
    pl = C.plan(info)
    cmd = C.ffmpeg_command(info, pl, Path("C:/v/clip.converting.mp4"), list(C.X264), "1001:24000")
    s = " ".join(cmd)
    for part in ("-map 0:0", "-map 0:1", "-c:v libx264", "-crf 13", "-preset slow", "-fps_mode passthrough",
                 "-enc_time_base:v 1001:24000", "-g 48", "-color_primaries bt709", "-colorspace bt709",
                 "-c:a:0 aac", "-b:a:0 320k", "-progress pipe:1", "-f mp4"):
        assert part in s
    rw = C.ffmpeg_command(_info(container="mkv", ext=".mkv"), C.plan(_info(container="mkv", ext=".mkv")),
                          Path("o.mp4"))
    assert "-c:v copy" in " ".join(rw) and "-c:a:0 copy" in " ".join(rw) and "libx264" not in rw
    assert C.choose_encoder(info, gpu="off")[0] == "libx264"
    assert C.choose_encoder(info, gpu="auto")[0] == ("h264_nvenc" if C.NVENC_SAME_QUALITY and C.nvenc_available()
                                                     else "libx264")


def test_ffmpegs_own_errors_in_plain_words():
    cmd = "command: ffmpeg -i in.webm -c:v h264_nvenc -cq 17 out.mp4"
    assert C._ffmpeg_problem(cmd + "\n[out#0/mp4] Error writing trailer: No space left on device") == \
        "the disk is full: free some space and try again"
    assert C._ffmpeg_problem(cmd + "\n[h264_nvenc @ 0x1] OpenEncodeSessionEx failed: out of memory (10)") == \
        "the GPU encoder (NVENC) failed"
    assert C._ffmpeg_problem(cmd + "\n[vp9 @ 0x1] Invalid data found when processing input").startswith(
        "the video seems damaged")
    assert C._ffmpeg_problem(cmd + "\nConversion failed!") == "FFmpeg failed: Conversion failed!"   # not 'NVENC'


def test_stop_during_convert_project_is_written_as_stopped(media, tmp_path):
    p = P.create(tmp_path, competitor_url="https://youtu.be/4v3FsM0BzWQ")
    raw = _copy(media, "vp9_opus.webm", p.folder)
    p.update(raw={"file": raw.name})
    cancel = threading.Event()
    cancel.set()
    with pytest.raises(C.Cancelled):
        C.convert_project(p, cancel=cancel, gpu="off")
    s = P.load(p.folder).step("convert")
    assert s["state"] == "failed" and s["error"] == "stopped" and raw.exists()
    assert sorted(x.name for x in p.folder.iterdir()) == ["logs", "project.json", "vp9_opus.webm"]


def test_progress_text():
    assert C.progress_text("converting to H.264 (CPU)", 30.0, 2.0, 100.0) == \
        (0.3, "converting to H.264 (CPU) 30 % -- 2x, about 0:35 left")
    assert C.progress_text("copying into MP4", 0.0, 0.0, None) == (0.0, "copying into MP4 0 %")
    assert C.progress_text("x", 120.0, 1.5, 100.0)[0] == 0.99


# ---------------------------------------------------------------------------------------------------------------------
# a project's convert step
# ---------------------------------------------------------------------------------------------------------------------

def test_convert_project_makes_both_files_premiere_ready_and_writes_the_step(media, tmp_path):
    p = P.create(tmp_path, competitor_url="https://www.youtube.com/shorts/4v3FsM0BzWQ",
                 raw_url="https://www.youtube.com/watch?v=hkFYOzYx_6M")
    comp = _copy(media, "h264_aac.mp4", p.folder, "Short [4v3FsM0BzWQ].mp4")
    raw = _copy(media, "vp9_opus.webm", p.folder, "Long \U0001F602 [hkFYOzYx_6M].webm")
    n = _nframes(raw)
    p.update(competitor={"file": comp.name}, raw={"file": raw.name})
    seen = []
    out = C.convert_project(p, progress=lambda r, f, t: seen.append(r), gpu="off")
    d = P.load(p.folder).data
    assert d["steps"]["convert"]["state"] == "done"
    assert d["competitor"]["file"] == "Short [4v3FsM0BzWQ].mp4"
    assert d["raw"]["file"] == "Long \U0001F602 [hkFYOzYx_6M].mp4"
    assert d["steps"]["convert"]["competitor"]["how"] == "keep" and d["steps"]["convert"]["raw"]["how"] == "encode"
    assert d["steps"]["convert"]["raw"]["checks"]["frames"] == [n, n]
    assert out["raw"].deleted_original and not raw.exists() and {"competitor", "raw"} <= set(seen)
    assert (p.folder / "logs" / "convert_raw.log").is_file()
    assert sorted(x.name for x in p.folder.iterdir()) == \
        ["Long \U0001F602 [hkFYOzYx_6M].mp4", "Short [4v3FsM0BzWQ].mp4", "logs", "project.json"]


def test_a_failed_convert_is_written_in_plain_words(media, tmp_path, monkeypatch):
    p = P.create(tmp_path, competitor_url="https://youtu.be/4v3FsM0BzWQ")
    raw = _copy(media, "vp9_opus.webm", p.folder)
    p.update(raw={"file": raw.name})
    monkeypatch.setattr(C, "check", lambda *a, **k: (False, {}, ["59 frames instead of 60"]))
    with pytest.raises(C.ConvertError):
        C.convert_project(p, gpu="off")
    s = P.load(p.folder).step("convert")
    assert s["state"] == "failed" and s["error"].startswith("RAW: the converted file is not right (59 frames")
    assert raw.exists()
