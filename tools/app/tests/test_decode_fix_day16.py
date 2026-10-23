"""Day 16, task 6: a RAW that can't be read ("could not decode source frames ... by PTS": the Ted 2 x265 runs) is
converted ONCE by itself (NVENC, 8-bit H.264, every frame re-stamped onto the constant rate) and the same video runs
again on it -- never a stop; the original never deleted; a second failure is said, not looped.

Run: python -m pytest tools/app/tests/test_decode_fix_day16.py"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mr_app.core import convert as CV  # noqa: E402
from mr_app.core import jobs as J  # noqa: E402
from mr_app.core import projects as PJ  # noqa: E402

PTS = ("RuntimeError: C:\\x\\Ted.2.2015.EXTENDED.1080p.BluRay.x265-RBG.mp4: could not decode source frames [0, 1]... "
       "by PTS")


def _thread(monkeypatch, tmp_path, results, converted=None):
    pytest.importorskip("PySide6.QtCore")
    from PySide6.QtCore import QCoreApplication
    QCoreApplication.instance() or QCoreApplication([])
    from mr_app.tools import match_cuts_tool as M
    tmp_path.mkdir(parents=True, exist_ok=True)
    raw, comp = tmp_path / "Ted.2.x265.mp4", tmp_path / "c.mp4"
    raw.write_bytes(b"r")
    comp.write_bytes(b"c")
    runs = []

    class FakeRun:
        def __init__(self, job, **kw):
            self.job, self.run_dir = job, tmp_path / f"run{len(runs)}"
            runs.append(Path(job.raw))

        def start(self):
            pass

        def wait(self, poll):
            return results[min(len(runs) - 1, len(results) - 1)]

    import mr_app.core.run as RUN
    monkeypatch.setattr(RUN, "JobRun", FakeRun)
    from mr_app.core import flow
    monkeypatch.setattr(flow, "template_for_job", lambda j: (None, None))
    calls = []

    def fake_reencode(p, out_dir=None, progress=None, cancel=None, log_path=None):
        calls.append(Path(p))
        if converted is None:
            raise CV.ConvertError("NVENC failed and x264 too")
        out = Path(p).with_name(Path(p).stem + CV.DECODE_FIX_SUFFIX + ".mp4")
        out.write_bytes(b"x")
        return SimpleNamespace(path=out)
    monkeypatch.setattr(CV, "reencode_for_decode", fake_reencode)
    job = J.Job("ted", comp, raw)
    th = M.JobThread(job, {}, tmp_path)
    got, said = [], []
    th.done.connect(lambda i, r: got.append(r))
    th.progress.connect(lambda i, t, s: said.append(t))
    th.run()
    return job, runs, calls, got, said, raw


def test_an_unreadable_raw_is_converted_once_and_the_video_runs_again(monkeypatch, tmp_path):
    fail = {"label": "FAIL", "stop": PTS, "failures": [f"the run stopped (exit 2): {PTS}"]}
    job, runs, calls, got, said, raw = _thread(monkeypatch, tmp_path, [fail, {"label": "PASS"}], converted=True)
    assert calls == [raw] and len(runs) == 2 and runs[1].name == "Ted.2.x265.reencoded.mp4"
    assert got == [{"label": "PASS"}] and job.decode_converted and raw.is_file()       # the original kept
    assert any("converting it once (NVENC)" in t for t in said)


def test_it_never_loops_and_a_failed_conversion_is_said(monkeypatch, tmp_path):
    fail = {"label": "FAIL", "stop": PTS, "failures": [f"the run stopped (exit 2): {PTS}"]}
    job, runs, calls, got, _s, _r = _thread(monkeypatch, tmp_path, [fail, fail], converted=True)
    assert len(calls) == 1 and len(runs) == 2 and got[0]["label"] == "FAIL"           # once, then the stop shown
    job, runs, calls, got, _s, _r = _thread(monkeypatch, tmp_path / "b", [fail], converted=None)
    assert len(runs) == 1 and any("could not be converted by itself either" in f for f in got[0]["failures"])
    other = {"label": "FAIL", "stop": "the disk is full"}
    job, runs, calls, got, _s, _r = _thread(monkeypatch, tmp_path / "c", [other], converted=True)
    assert calls == [] and len(runs) == 1                                                 # another stop: as before


def test_the_project_reads_its_converted_raw(tmp_path):
    p = PJ.create(tmp_path, name="ted")
    (p.folder / "Ted.mp4").write_bytes(b"r")
    conv = tmp_path / "Ted.reencoded.mp4"
    conv.write_bytes(b"x")
    p.update(raw={"file": "Ted.mp4", "decode_copy": str(conv)})
    assert PJ.load(p.folder).file("raw") == conv
    conv.unlink()
    assert PJ.load(p.folder).file("raw") == p.folder / "Ted.mp4"


def test_the_forced_reencode_restamps_every_frame_8_bit(tmp_path):
    from mr_app.core.convert import ffmpeg_bin
    src = tmp_path / "vfr.mkv"
    subprocess.run([ffmpeg_bin(), "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=size=160x90:rate=24000/1001",
                    "-f", "lavfi", "-i", "sine=frequency=300", "-t", "3", "-c:v", "libx265", "-pix_fmt",
                    "yuv420p10le", "-x265-params", "log-level=none", "-c:a", "aac", str(src)], check=True)
    res = CV.reencode_for_decode(src)
    out = Path(res.path)
    assert out.name == "vfr.reencoded.mp4" and src.is_file()
    info = CV.inspect(out)
    assert info.vcodec == "h264" and info.bit_depth == 8 and info.frames == CV.inspect(src).frames
    again = CV.reencode_for_decode(src)                                                   # made before: used again
    assert Path(again.path) == out and "used again" in again.note
