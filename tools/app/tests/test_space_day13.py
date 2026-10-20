"""Day 13, item 1.3: ONE estimate of what a run, a download and a conversion write (core.space) -- the analysis copy
of a RAW the tool cannot read as it is (counted once per queue: it is deleted after each run), the 5.1 stereo file,
the tool's saved analysis, the margin -- the plain words when it does not fit, and the top strip's drives. The disk
is never really asked: the free space is faked."""
from __future__ import annotations

import shutil
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mr_app.core import newvideo as NV  # noqa: E402
from mr_app.core import space as S  # noqa: E402

BORAT = dict(duration=5028.064, width=1920, height=1040, fps=23.976, frames=120553, vcodec="hevc", bit_depth=10,
             pix_fmt="yuv420p10le", container="mp4", acodecs=("aac",), channels=6)


def _media(path: Path, n: int, **kw) -> S.Media:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"\0" * 1000)
    return S.Media(str(path), n, **kw)


def test_the_tool_s_own_rule_for_its_temporary_analysis_copy(tmp_path):
    m = _media(tmp_path / "borat.mp4", int(3.76 * S.GB), **BORAT)
    assert S.analysis_copy_why(m) == "x265 / HEVC"
    # Borat (x265, 84 min, 1920x1040): the real raw_stereo.mp4 was 15.0 GB = 14.0 GiB
    assert 13.8 * S.GB < S.analysis_copy_bytes(m) < 14.6 * S.GB
    ok = S.Media("a.mp4", S.GB, 600.0, 1920, 1080, 25.0, 15000, "h264", 8, "yuv420p", "mp4", ("aac",), 2)
    assert S.analysis_copy_why(ok) == ""
    assert "AC3 sound" in S.analysis_copy_why(S.Media("a.mp4", 1, 60.0, 1920, 1080, 25.0, 0, "h264", 8, "yuv420p",
                                                      "mp4", ("ac3",), 6))
    assert S.analysis_copy_why(S.Media("a.mkv", 1, 60.0, 1920, 1080, 25.0, 0, "h264", 8, "yuv420p", "mkv",
                                       ("aac",), 2)) == "MKV"
    assert "variable frame rate" in S.analysis_copy_why(S.Media("a.mp4", 1, 60.0, 1920, 1080, 25.0, 0, "h264", 8,
                                                                "yuv420p", "mp4", ("aac",), 2, vfr=True))
    assert S.analysis_copy_why(None) == ""
    big = S.Media("a.mp4", 1, 600.0, 3840, 2160, 25.0, 0, "vp9", 8, "yuv420p", "mp4", (), 0)
    assert S.analysis_copy_bytes(big) == pytest.approx(4 * S.ANALYSIS_MBIT_1080P * 1e6 / 8 * 600, rel=0.01)


def _m(**kw) -> S.Media:
    base = dict(path="a.mp4", size=S.GB, duration=600.0, width=1920, height=1080, fps=25.0, frames=15000,
                vcodec="h264", bit_depth=8, pix_fmt="yuv420p", container="mp4", acodecs=("aac",), channels=2)
    base.update(kw)
    return S.Media(**base)


def test_what_premiere_plays_keeps_the_original_when_it_can():
    """match_cuts.edit_media's rules (the "keep the original RAW" change): the original, a remux or a CRF 17 copy."""
    borat = S.Media("borat.mp4", int(3.76 * S.GB), **BORAT)
    assert S.premiere_picture(borat) == ("original", "")                  # x265 10-bit in .mp4: Premiere opens it
    assert S.premiere_picture(_m()) == ("original", "")                   # nothing to convert at all
    assert S.premiere_picture(_m(vcodec="hevc", container="mkv"))[0] == "remux"
    late = _m(v_start=0.021, a_start=0.021)
    assert "a picture that starts late" in S.analysis_copy_why(late) and S.premiere_picture(late)[0] == "remux"
    webm = _m(vcodec="vp9", container="webm", acodecs=("opus",))
    assert S.premiere_picture(webm)[0] == "convert"
    assert S.premiere_copy_bytes(webm, "convert") == int(S.DELIVERY_SHARE * S.analysis_copy_bytes(webm))
    assert S.premiere_picture(_m(vfr=True))[0] == "convert" and S.premiere_picture(_m(vcodec="av1"))[0] == "convert"
    assert S.premiere_copy_bytes(_m(size=5 * S.GB), "remux") == 5 * S.GB


def test_the_stereo_file_and_the_tool_s_saved_analysis():
    m = S.Media("a.mp4", S.GB, **BORAT)
    assert S.stereo_bytes(m) == int(5028.064 * S.WAV_BYTES_S)          # 5.1: a 16-bit 48 kHz stereo WAV (~0.9 GB)
    assert S.stereo_bytes(S.Media("a.mp4", 1, 60.0, channels=2)) == 0   # no sound at all
    assert S.stereo_bytes(_m()) == 0                                     # plain stereo AAC: V1's own sound
    assert S.stereo_bytes(_m(acodecs=("ac3",))) == int(600 * S.WAV_BYTES_S)
    assert S.stereo_bytes(_m(a_start=0.02)) > 0 and S.stereo_bytes(_m(), "convert") > 0     # late / V1 is a copy
    long = S.cache_bytes(m)
    assert long == int(S.CACHE_FIXED + S.CACHE_PROXY_MAX + S.CACHE_PER_MIN * 5028.064 / 60)     # ~5.8 GB
    short = S.cache_bytes(S.Media("a.mp4", 1, 60.0, 1920, 1080, 25.0, 1500))
    assert short == int(S.CACHE_FIXED + 1500 * S.CACHE_PROXY_FRAME + S.CACHE_PER_MIN)          # ~0.7 GB
    assert S.cache_bytes(None, 5 * S.MB) == S.CACHE_FIXED + 5 * S.MB


def test_a_queue_counts_what_stays_and_the_biggest_temporary_copy_once(tmp_path, monkeypatch):
    m = _media(tmp_path / "in" / "borat.mp4", int(3.76 * S.GB), **BORAT)
    monkeypatch.setattr(S, "probe", lambda p, timeout_s=0: m)
    comp = tmp_path / "in" / "comp.mp4"
    comp.write_bytes(b"x")
    one = S.run_parts(comp, Path(m.path), tmp_path / "out" / "video1", tmp_path / "work")
    kinds = {p.what.split(" (")[0]: p for p in one}
    assert "a temporary copy of the RAW for the analysis" in kinds and kinds[
        "a temporary copy of the RAW for the analysis"].temp
    assert "the RAW's stereo sound for Premiere" in kinds and not kinds["the RAW's stereo sound for Premiere"].temp
    assert not any("a copy of" in w or ".mp4 for Premiere" in w for w in kinds)          # V1 = the original, linked
    two = S.run_parts(comp, Path(m.path), tmp_path / "out" / "video2", tmp_path / "work")
    d = S.per_drive([("video1", one), ("video2", two)])
    assert len(d) == 1                                            # all on this test's drive
    temp = S.analysis_copy_bytes(m)
    keep_one = sum(p.bytes for p in one if not p.temp)
    assert d[0].temp == temp and d[0].keep == 2 * keep_one          # the copy once: the runs go one after another
    assert d[0].total == d[0].keep + temp + S.margin(d[0].keep + temp)


def test_not_enough_room_says_how_much_is_needed_and_free_and_offers_clean_up(tmp_path, monkeypatch):
    m = _media(tmp_path / "in" / "borat.mp4", int(3.76 * S.GB), **BORAT)
    monkeypatch.setattr(S, "probe", lambda p, timeout_s=0: m)
    monkeypatch.setattr(S, "free_within", lambda p, timeout_s=0: 9 * S.GB)
    probs = S.problems([("borat", tmp_path / "in" / "comp.mp4", Path(m.path), tmp_path / "out" / "borat")],
                       tmp_path / "work")
    assert len(probs) == 1
    t = probs[0]
    assert t.startswith(f"not enough free space on {S.drive_of(tmp_path)} -- about ") and "is needed there" in t
    assert "only 9.0 GB is free" in t and "borat: a temporary copy of the RAW for the analysis (x265 / HEVC" in t
    assert "Board -> Clean up" in t
    one = S.run_problem("borat", tmp_path / "in" / "comp.mp4", Path(m.path), tmp_path / "out" / "borat",
                        tmp_path / "work")
    assert "only 9.0 GB is free" in one and len(one) < 400
    monkeypatch.setattr(S, "free_within", lambda p, timeout_s=0: 200 * S.GB)
    assert S.problems([("borat", tmp_path / "in" / "comp.mp4", Path(m.path), tmp_path / "out" / "borat")]) == []
    monkeypatch.setattr(S, "free_within", lambda p, timeout_s=0: None)      # a drive that cannot be read: not full
    assert S.run_problem("borat", None, Path(m.path), tmp_path / "out" / "borat") == ""


def test_a_folder_that_does_not_answer_gives_up_quickly(tmp_path, monkeypatch):
    def slow(p):
        time.sleep(3.0)
        return shutil.disk_usage(tmp_path)
    monkeypatch.setattr(S.shutil, "disk_usage", slow)            # (a network folder that is away: ~20 s)
    t0 = time.perf_counter()
    assert S.usage_within(tmp_path, 0.2) is None
    got = S.read_strip([("C:", str(tmp_path)), (r"\\192.168.101.11\Shared", str(tmp_path / "share"))], 0.2)
    assert time.perf_counter() - t0 < 2.5 and [d.free for d in got] == [None, None]
    assert got[1].text() == r"\\192.168.101.11\Shared not reachable" and got[1].level == "unknown"


def test_the_strip_shows_c_and_the_other_drives_and_its_colours(monkeypatch, tmp_path):
    monkeypatch.setenv("SystemDrive", "C:")
    names = [n for n, _ in S.strip_folders(r"D:\Shared\Projects", r"C:\x\input\downloads")]
    assert names[0] == "C:" and "D:" in names
    names = [n for n, _ in S.strip_folders(r"\\192.168.101.11\Shared\Projects", r"C:\x\input\downloads")]
    assert r"\\192.168.101.11\Shared" in names
    assert [n for n, _ in S.strip_folders(r"C:\Users\x\Projects", r"C:\x")][0] == "C:"
    assert S.DriveFree("C:", 64 * S.GB).level == "ok" and S.DriveFree("C:", 29 * S.GB).level == "low"
    assert S.DriveFree("C:", 9 * S.GB).level == "very low" and S.DriveFree("C:", None).level == "unknown"
    assert S.gb(64.2 * S.GB) == "64.2 GB" and S.gb(2.6 * 1024 * S.GB) == "2.6 TB" and S.gb(300 * S.MB) == "300 MB"


def test_a_download_s_size_from_yt_dlp_or_a_guess(monkeypatch):
    li = SimpleNamespace(duration=600.0, height=1080, info={"formats": []}, title="x")
    from mr_app.core import download as D
    fmt_h264 = {"format_id": "137", "filesize": 100 * S.MB}
    monkeypatch.setattr(D, "pick_formats", lambda f: SimpleNamespace(parts=[fmt_h264], video_kind="h264"))
    assert S.download_bytes(li) == int(100 * S.MB * S.DOWNLOAD_KEEP)
    monkeypatch.setattr(D, "pick_formats", lambda f: SimpleNamespace(parts=[{"tbr": 4000}], video_kind="vp9"))
    assert S.download_bytes(li) == int(4000 * 125 * 600 * S.DOWNLOAD_CONVERT)            # bitrate x length, x 4
    monkeypatch.setattr(D, "pick_formats", lambda f: SimpleNamespace(parts=[{}], video_kind="vp9"))
    assert S.download_bytes(li) == int(S.guess_bytes(600.0, 1080) * S.DOWNLOAD_CONVERT)   # nothing said: a guess
    assert S.guess_bytes(600.0, 1080) == int(600 * (6e6 + 160e3) / 8)
    assert S.guess_bytes(None, 1080) == 0


def test_a_conversion_needs_the_new_file_next_to_the_original(tmp_path, monkeypatch):
    monkeypatch.setattr(S, "free_within", lambda p, timeout_s=0: 2 * S.GB)
    t = S.convert_problem(int(1.08 * S.GB), "encode", tmp_path, "4k.webm")
    assert t.startswith(f"not enough free space on {S.drive_of(tmp_path)} -- converting it to H.264 needs about")
    assert "only 2.0 GB is free" in t and "4k.webm: 1.1 GB" in t
    assert S.convert_problem(int(1.08 * S.GB), "rewrap", tmp_path) == ""                 # 1.1 GB + margin fits
    assert S.convert_problem(10 * S.GB, "keep", tmp_path) == ""


def test_a_new_video_counts_its_downloads_and_its_edit_and_the_words_reach_the_row(tmp_path, monkeypatch):
    monkeypatch.setattr(S, "free_within", lambda p, timeout_s=0: 3 * S.GB)
    li = SimpleNamespace(duration=1300.0, height=1080, info={}, title="Family Guy S10E18")
    t = S.new_video_problem([li], None, tmp_path / "projects", sizer=lambda x: 2 * S.GB, work_dir=tmp_path / "work")
    assert "only 3.0 GB is free" in t and "the download of Family Guy S10E18 2.0 GB" in t and len(t) < 300
    assert NV.plain_error(t) == t                       # the row says the numbers (not only "the disk is full")
    assert NV.plain_error("not enough free space on D: ...") == NV.DISK_FULL
    assert S.new_video_problem([li], None, tmp_path, sizer=lambda x: 0) == ""     # nothing known: not checked
    monkeypatch.setattr(S, "free_within", lambda p, timeout_s=0: 500 * S.GB)
    assert S.new_video_problem([li], None, tmp_path, sizer=lambda x: 2 * S.GB) == ""


def test_a_new_video_s_raw_is_judged_as_the_app_s_conversion_leaves_it():
    webm = _m(vcodec="vp9", container="webm", acodecs=("opus",), size=2 * S.GB)
    enc = S.after_convert(webm, "encode")                     # the app makes it H.264 + AAC in .mp4: read as it is
    assert S.analysis_copy_why(enc) == "" and enc.size == int(2 * S.GB * S.CONVERT_ENCODE)
    mkv = S.after_convert(_m(vcodec="hevc", container="mkv", acodecs=("eac3",), channels=6), "rewrap")
    assert mkv.container == "mp4" and mkv.acodecs == ("aac",) and S.analysis_copy_why(mkv) == "x265 / HEVC"
    assert S.premiere_picture(mkv) == ("original", "") and S.stereo_bytes(mkv) > 0       # 5.1: the WAV
    ten = S.after_convert(_m(vcodec="vp9", container="webm", bit_depth=10, pix_fmt="yuv420p10le"), "encode")
    assert "10-bit" in S.analysis_copy_why(ten)               # x264 High 10: the tool still makes its copy
    assert S.after_convert(None, "encode") is None and S.after_convert(webm, "keep") is webm


def test_space_errors_are_recognised():
    assert S.is_space_error("not enough free space on C: -- about 26.7 GB is needed there")
    assert S.is_space_error("the disk is full: free some space and press Try again")
    assert not S.is_space_error("this video is private")
