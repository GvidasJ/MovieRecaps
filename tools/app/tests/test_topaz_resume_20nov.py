"""20 Nov (job 4, pause and resume): a Topaz run that was stopped -- the app closed, the PC turned off, Topaz hanging
-- goes on from where it stopped, and its file is the same as a run that never stopped: the same number of frames,
every frame the piece's own frame at the same place, the frames made before the stop the very same pictures.

The fake Topaz (ffmpeg's 2x scale + 60 fps), slowed down so it can be stopped half way. The piece: 60 s at 23.976 fps
where every frame is its own random picture (no two look alike), with sound.

Run: python -m pytest tools/app/tests/test_topaz_resume_20nov.py"""
from __future__ import annotations

import os
import subprocess
import sys
import threading
from fractions import Fraction
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "match_cuts"))

from mr_app.core import enhance as E  # noqa: E402

FPS = Fraction(24000, 1001)
SECONDS = 60
W, H = 160, 90


def _ffmpeg() -> str:
    from match_cuts.common import ffmpeg_bin
    return ffmpeg_bin()


@pytest.fixture(scope="module")
def piece(tmp_path_factory) -> Path:
    out = tmp_path_factory.mktemp("resume_piece") / "piece.mp4"
    subprocess.run([_ffmpeg(), "-v", "error", "-y", "-f", "lavfi", "-i",
                    f"nullsrc=s=40x22:r={FPS}:d={SECONDS},geq=lum='random(1)*255':cb=128:cr=128,"
                    f"scale={W}:{H}:flags=neighbor", "-f", "lavfi", "-i", f"sine=duration={SECONDS}",
                    "-c:v", "libx264", "-preset", "ultrafast", "-crf", "12", "-g", "48", "-bf", "0",
                    "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(out)], check=True)
    return out


@pytest.fixture
def fake(monkeypatch):
    """The fake Topaz, reading the piece at 6x its speed (so that it can be stopped half way)."""
    monkeypatch.setenv(E.FAKE_ENV, "1")
    monkeypatch.delenv(E.RESUME_ENV, raising=False)
    real = E.topaz_command
    calls = []

    def command(tz, src, dst, vram=1.0, scale=2, part=None):
        cmd = real(tz, src, dst, vram, scale, part)
        calls.append(part)
        i = cmd.index("-i")
        return cmd[:i] + ["-readrate", "6"] + cmd[i:]
    monkeypatch.setattr(E, "topaz_command", command)
    return calls


def _out_frames() -> int:
    return int(round(SECONDS * E.OUT_FPS))


def _gray(path: Path) -> np.ndarray:
    r = subprocess.run([_ffmpeg(), "-v", "error", "-i", str(path), "-map", "0:v:0", "-vf", f"scale={W}:{H}:flags=area",
                        "-f", "rawvideo", "-pix_fmt", "gray", "pipe:1"], capture_output=True, check=True)
    return np.frombuffer(r.stdout, np.uint8).reshape(-1, H, W).astype(np.int16)


def _shows(out: np.ndarray, src: np.ndarray, first: int = 0) -> list[int]:
    """For every 60 fps frame: the piece frame it shows (the closest of the frames near its time)."""
    got = []
    for k in range(len(out)):
        c = first + int(Fraction(k) * FPS / E.OUT_FPS)
        cand = [j for j in range(c - 2, c + 3) if 0 <= j < len(src)]
        got.append(min(cand, key=lambda j: np.abs(out[k] - src[j]).mean()))
    return got


def _stopper(at: float):
    cancel = threading.Event()

    def prog(share, _left):
        if share >= at:
            cancel.set()
    return cancel, prog


def _stopped_then_again(piece: Path, dst: Path, at: float, part=None, n=None):
    n = n or _out_frames()
    tz = E.find_topaz()
    cancel, prog = _stopper(at)
    with pytest.raises(E.EnhanceError, match="stopped"):
        E.run_topaz(piece, dst, n, tz, prog, cancel, scale=2, part=part, fps=FPS)
    state = E._resume_file(dst)                                            # noqa: SLF001
    assert state.is_file()                                                 # what was made is kept
    E.run_topaz(piece, dst, n, tz, None, threading.Event(), scale=2, part=part, fps=FPS)


def test_a_finished_run_gives_the_very_same_pictures_as_before(tmp_path, piece, fake):
    """No stop: the file in blocks, then written as a normal mp4 -- the same frames, times and sound as Topaz's own
    file of before (no ``fps``: the way it was until today)."""
    tz = E.find_topaz()
    old, new = tmp_path / "old.mp4", tmp_path / "new.mp4"
    E.run_topaz(piece, old, _out_frames(), tz, scale=2)
    E.run_topaz(piece, new, _out_frames(), tz, scale=2, fps=FPS)
    assert np.array_equal(_gray(old), _gray(new))
    po, pn = E._probe(old), E._probe(new)                                  # noqa: SLF001
    assert (po["r_frame_rate"], po["start_time"]) == (pn["r_frame_rate"], pn["start_time"])
    for f in (old, new):
        a = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "a", "-show_entries", "stream=codec_name",
                            "-of", "csv=p=0", str(f)], capture_output=True, text=True).stdout.strip()
        assert a == "aac"                                                  # the sound is in it
    assert not E._resume_file(new).exists() or E._resume_file(new).read_text()     # noqa: SLF001


def test_stopped_half_way_it_goes_on_and_every_frame_is_the_right_one(tmp_path, piece, fake):
    tz = E.find_topaz()
    ref = tmp_path / "ref.mp4"
    E.run_topaz(piece, ref, _out_frames(), tz, scale=2)                    # (never stopped, as before)
    dst = tmp_path / "x_ENHANCING.part.mp4"
    _stopped_then_again(piece, dst, 0.7)
    assert any(p is not None for p in fake)                                # it went on as a part, from the stop
    a, b = _gray(ref), _gray(dst)
    assert len(b) == _out_frames() and len(a) in (len(b), len(b) - 1)
    kept = next(i for i, p in enumerate(fake) if p is not None)            # (the call that went on)
    first = int(round(float(fake[kept][0]) * float(FPS)))                  # the piece frame it went on from
    k0 = int(Fraction(first) * E.OUT_FPS / FPS)
    assert k0 > 0 and k0 % E.grid_step(FPS)[1] == 0                         # on the grid: 1001, 2002 ... frames
    assert np.array_equal(a[:k0], b[:k0])                                  # before the stop: the very same pictures
    src = _gray(piece)
    sa, sb = _shows(a, src), _shows(b, src)                                # after it: every frame shows the same
    assert sb[:len(sa)] == sa                                              #   piece frame as in the run never stopped
    assert all(np.abs(b[k] - a[k]).mean() < 6.0 for k in range(k0, len(a)))


def test_a_part_stopped_half_way_goes_on_too(tmp_path, piece, fake):
    tz = E.find_topaz()
    a0, b0 = 120, 1320                                                     # piece frames 120 .. 1319
    n = E.range_out_frames(b0 - a0, FPS)
    part = (float(Fraction(a0) / FPS), float(Fraction(b0 - a0) / FPS) - E.SEEK_EARLY_S, n)
    ref = tmp_path / "ref.mp4"
    E.run_topaz(piece, ref, n, tz, scale=2, part=part)
    dst = tmp_path / "p1.mp4"
    _stopped_then_again(piece, dst, 0.75, part=part, n=n)
    a, b = _gray(ref), _gray(dst)
    assert len(a) == len(b) == n
    src = _gray(piece)
    assert _shows(b, src, a0) == _shows(a, src, a0)
    assert all(np.abs(b[k] - a[k]).mean() < 6.0 for k in range(n))


def test_a_finished_part_is_not_made_again_after_a_stop(tmp_path, piece, fake):
    nm = E.names(tmp_path, "v")
    nm.piece.write_bytes(piece.read_bytes())
    info = {"fps": str(FPS), "frames": int(SECONDS * FPS), "duration": SECONDS, "width": W, "height": H,
            "ranges": [[0, 480], [720, 1200]]}
    tz = E.find_topaz()
    cancel = threading.Event()
    total = sum(E.range_out_frames(b - a, FPS) for a, b in info["ranges"])
    first = E.range_out_frames(480, FPS)

    def prog(share, _left):
        if share * total > first + 300:                                    # in the second part
            cancel.set()
    with pytest.raises(E.EnhanceError, match="stopped"):
        E.enhance_parts(nm, info, tz, prog, cancel, 2)
    assert E._part_file(nm, 0).is_file()                                   # noqa: SLF001 - the finished part stays
    fake.clear()
    E.enhance_parts(nm, info, tz, None, threading.Event(), 2)
    assert all(p is not None and abs(p[0] - 720 / float(FPS)) < 1.0 for p in fake), fake   # only the second part
    assert E._frames_of(nm.part) == total                                  # noqa: SLF001
    assert not E._part_file(nm, 0).exists() and not E._resume_file(E._part_file(nm, 0)).exists()  # noqa: SLF001


def test_a_failure_for_a_reason_keeps_nothing(tmp_path, piece, fake, monkeypatch):
    tz = E.find_topaz()
    dst = tmp_path / "f.mp4"
    cancel, prog = _stopper(0.7)
    with pytest.raises(E.EnhanceError):
        E.run_topaz(piece, dst, _out_frames(), tz, prog, cancel, scale=2, fps=FPS)

    def boom(*_a, **_k):
        raise E.EnhanceError("Topaz could not read the piece")
    monkeypatch.setattr(E, "_run_topaz_once", boom)
    with pytest.raises(E.EnhanceError, match="could not read"):
        E.run_topaz(piece, dst, _out_frames(), tz, None, threading.Event(), scale=2, fps=FPS)
    assert not E._resume_file(dst).exists() and not list(tmp_path.glob("f.seg*"))     # noqa: SLF001


def test_another_piece_or_an_old_half_file_starts_from_the_beginning(tmp_path, piece, fake):
    tz = E.find_topaz()
    dst = tmp_path / "g.mp4"
    cancel, prog = _stopper(0.7)
    with pytest.raises(E.EnhanceError):
        E.run_topaz(piece, dst, _out_frames(), tz, prog, cancel, scale=2, fps=FPS)
    other = tmp_path / "other.mp4"
    other.write_bytes(piece.read_bytes())                                  # (another piece: other name, time)
    fake.clear()
    E.run_topaz(other, dst, _out_frames(), tz, None, threading.Event(), scale=2, fps=FPS)
    assert fake == [None]                                                  # the whole of it, from frame 0
    h = tmp_path / "h.mp4"
    h.write_bytes(b"\0" * 1000)                                            # a half file of before today (no note)
    fake.clear()
    E.run_topaz(piece, h, _out_frames(), tz, None, threading.Event(), scale=2, fps=FPS)
    assert fake == [None] and E._frames_of(h) in (_out_frames(), _out_frames() - 1)   # noqa: SLF001


def test_a_file_with_frames_out_of_order_is_not_cut(tmp_path):
    f = tmp_path / "b.mp4"
    subprocess.run([_ffmpeg(), "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc=s=160x90:r=60:d=40", "-c:v",
                    "libx264", "-bf", "2", "-movflags", E.FRAG_FLAGS, str(f)], check=True)
    assert E._salvage(f, 1001) == 0 and not f.exists()                     # noqa: SLF001


def test_switched_off_it_is_as_before(tmp_path, piece, fake, monkeypatch):
    monkeypatch.setenv(E.RESUME_ENV, "0")
    tz = E.find_topaz()
    dst = tmp_path / "o.mp4"
    cancel, prog = _stopper(0.5)
    with pytest.raises(E.EnhanceError):
        E.run_topaz(piece, dst, _out_frames(), tz, prog, cancel, scale=2, fps=FPS)
    assert not E._resume_file(dst).exists()                                # noqa: SLF001


def test_the_grid_steps():
    assert E.grid_step(Fraction(24000, 1001)) == (400, 1001)
    assert E.grid_step(Fraction(30000, 1001)) == (500, 1001)
    assert E.grid_step(25) == (5, 12) and E.grid_step(24) == (2, 5) and E.grid_step(60) == (1, 1)


def test_a_topaz_that_works_is_not_taken_for_hanging_without_a_progress_bar(tmp_path, piece, fake, monkeypatch):
    """20 Nov: the new frames were only noticed when somebody asked for the progress -- without it a Topaz that
    worked was stopped as "not answering" after STALL_S (here 3 s; the run takes ~10 s)."""
    monkeypatch.setattr(E, "STALL_S", 3.0)
    dst = tmp_path / "s.mp4"
    E.run_topaz(piece, dst, _out_frames(), E.find_topaz(), scale=2)
    assert E._frames_of(dst) in (_out_frames(), _out_frames() - 1)       # noqa: SLF001


def test_a_file_still_held_for_a_moment_after_topaz_stopped_goes_once_let_go(tmp_path):
    f = tmp_path / "held.mp4"
    f.write_bytes(b"x")
    h = open(f, "rb")                                                     # noqa: SIM115 - held, as by the dying Topaz
    threading.Timer(1.5, h.close).start()
    E._unlink_soon(f)                                                     # noqa: SLF001
    assert not f.exists()
    g = tmp_path / "held2.mp4"
    g.write_bytes(b"x")
    with open(g, "rb"):
        with pytest.raises(E.EnhanceError, match="still in use"):
            E._unlink_soon(g, wait_s=1.0)                                 # noqa: SLF001
