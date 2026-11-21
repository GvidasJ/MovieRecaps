"""21 Nov (The Worst Employee In Key and Peele, the laptop: "PartsError" twice, each after about an hour of Topaz): a
part was read from 0.2 ms before its first frame's exact time (frame / fps), but a RAW the app converted from an MKV has
the MKV's whole-millisecond timestamps -- up to 0.5 ms early -- and ffmpeg's seek then skipped the part's first frame:
the part started one frame late and its check refused it, an hour on. Now a part is read from its first frame's own
time in the file, and that reading is tried on the first frame before Topaz starts (seconds). Every parts failure is
written into the piece's json and the project at once."""
from __future__ import annotations

import json
import subprocess
import sys
from fractions import Fraction
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "match_cuts"))

from mr_app.core import enhance as E  # noqa: E402
from mr_app.core import projects as PJ  # noqa: E402

FPS = Fraction(24000, 1001)


@pytest.fixture(scope="module")
def ms_movie(tmp_path_factory) -> Path:
    """60 s at 23.976 fps, 160x90, every frame its own random picture; made as an MKV (whole-ms timestamps) and copied
    into an mp4, as the app's Convert makes a RAW of an MKV episode."""
    from match_cuts.common import ffmpeg_bin
    d = tmp_path_factory.mktemp("ms_movie")
    mkv, mp4 = d / "movie.mkv", d / "movie.mp4"
    subprocess.run([ffmpeg_bin(), "-v", "error", "-y", "-f", "lavfi", "-i",
                    "nullsrc=s=40x22:r=24000/1001:d=60,geq=lum='random(1)*255':cb=128:cr=128,"
                    "scale=160:90:flags=neighbor", "-c:v", "libx264", "-preset", "ultrafast", "-crf", "12", "-g", "48",
                    "-bf", "2", "-pix_fmt", "yuv420p", str(mkv)], check=True)
    subprocess.run([ffmpeg_bin(), "-v", "error", "-y", "-i", str(mkv), "-c", "copy", str(mp4)], check=True)
    return mp4


@pytest.fixture
def fake_topaz(monkeypatch):
    monkeypatch.setenv(E.FAKE_ENV, "1")
    return E.find_topaz()


def _exact(a: int) -> tuple:
    return (float(Fraction(a) / FPS), 4.0, 96)


def test_a_converted_mkv_stores_whole_milliseconds_and_the_exact_time_skips_some_first_frames(ms_movie, fake_topaz):
    times = E._frame_times(ms_movie)                                                # noqa: SLF001
    n = E._frames_of(ms_movie)                                                      # noqa: SLF001
    assert len(times) == n > 1400
    assert all(abs(t * 1000 - round(t * 1000)) < 1e-6 for t in times[:200])         # whole ms
    assert max(abs(times[k] - k / float(FPS)) for k in range(1000, 1024)) > 0.0002  # up to 0.5 ms off
    old = [a for a in range(1000, 1024) if E._seek_lands_right(fake_topaz, ms_movie, FPS, a, _exact(a))]  # noqa
    assert old, "the exact time never skipped a first frame (the bug of 20 Nov not reproduced)"
    for a in range(1000, 1024):                                                     # every rounding of one cycle
        own = (E._frame_time(times, a, FPS, n), 4.0, 96)                         # noqa: SLF001
        assert E._seek_lands_right(fake_topaz, ms_movie, FPS, a, own) == "", a      # noqa: SLF001


def _piece(tmp_path: Path, movie: Path, ranges) -> E.Names:
    nm = E.names(tmp_path / "Enhance", "2026-11-21_employee_all")
    nm.folder.mkdir(parents=True, exist_ok=True)
    nm.piece.write_bytes(movie.read_bytes())
    proj = PJ.create(tmp_path / "projects", name="employee")
    n = E._frames_of(nm.piece)                                                      # noqa: SLF001
    E._write_json(nm.info, {"project": nm.name, "project_folder": str(proj.folder), "frames": n,     # noqa: SLF001
                            "fps": str(FPS), "duration": round(n / float(FPS), 6), "width": 160, "height": 90,
                            "ranges": ranges})
    return nm


def _late_frame(movie: Path, tz) -> int:
    return next(a for a in range(1000, 1048) if E._seek_lands_right(tz, movie, FPS, a, _exact(a)))  # noqa: SLF001


def test_parts_starting_on_such_frames_are_right_now(tmp_path, ms_movie, fake_topaz):
    a = _late_frame(ms_movie, fake_topaz)
    nm = _piece(tmp_path, ms_movie, [[0, 240], [a, a + 120], [1300, 1430]])
    info = E.read_info(nm.info)
    E.enhance_parts(nm, info, fake_topaz, None, None, 2)
    assert E._frames_of(nm.part) == E.ranges_out_frames(info)                       # noqa: SLF001
    assert E.check_enhanced(nm.part, info)["ranged"]


def test_the_old_reading_fails_before_topaz_starts_on_that_part(tmp_path, ms_movie, fake_topaz, monkeypatch):
    a = _late_frame(ms_movie, fake_topaz)
    nm = _piece(tmp_path, ms_movie, [[0, 240], [a, a + 120], [1300, 1430]])
    monkeypatch.setattr(E, "_frame_times", lambda path: [])                         # the old way: frame / fps
    runs: list = []
    real = E.run_topaz

    def spy(src, dst, *a_, **k):
        runs.append(Path(dst).name)
        return real(src, dst, *a_, **k)
    monkeypatch.setattr(E, "run_topaz", spy)
    with pytest.raises(E.PartsError) as e:
        E.enhance_parts(nm, E.read_info(nm.info), fake_topaz, None, None, 2)
    assert f"part 2 (piece frame {a}" in str(e.value) and f"frame {a + 1}, not {a}" in str(e.value)
    assert len(runs) == 1                                                           # part 2 never went to Topaz


def test_a_parts_failure_is_in_the_json_and_the_project_at_once(tmp_path, ms_movie, fake_topaz, monkeypatch):
    nm = _piece(tmp_path, ms_movie, [[0, 240], [600, 720], [1300, 1430]])
    monkeypatch.setattr(E, "_seek_lands_right", lambda tz, piece, fps, first, part:
                        "its first frame shows the piece's frame 601, not 600" if first == 600 else "")
    seen: list = []
    real = E.run_topaz

    def whole(src, dst, *a, **k):                       # while the whole piece runs: both already say why
        if Path(dst) != nm.part:
            return real(src, dst, *a, **k)
        info = E.read_info(nm.info)
        proj = Path(info["project_folder"])
        seen.append((info.get("parts_failed"), PJ.load(proj).data.get("enhance", {}).get("parts_failed"),
                     (proj / "logs" / E.FAILURE_LOG).read_text(encoding="utf-8")))
        return real(src, dst, *a, **k)
    monkeypatch.setattr(E, "run_topaz", whole)
    E.take(nm)
    job = E.enhance_one(nm, json.loads(nm.lock.read_text(encoding="utf-8"))["token"], fake_topaz)
    assert job.state == "done", job.why
    in_json, in_project, log = seen[0]
    assert "part 2 (piece frame 600" in in_json and "601, not 600" in in_json
    assert in_project == in_json and "601, not 600" in log and "whole piece is enhanced instead" in log


def test_take_back_keeps_the_parts_failure_in_the_project(tmp_path, ms_movie, fake_topaz):
    nm = _piece(tmp_path, ms_movie, [[0, 240], [600, 720], [1300, 1430]])
    info = E.read_info(nm.info)
    E.run_topaz(nm.piece, nm.enhanced, int(round(info["duration"] * 60)), fake_topaz, scale=2, fps=FPS)
    E._write_json(nm.info, dict(info, parts_failed="part 2: one frame late"))       # noqa: SLF001
    proj = Path(info["project_folder"])
    r = E.take_back(nm.folder, nm.name, proj, info)
    assert r["ok"], r
    assert PJ.load(proj).data["enhance"]["parts_failed"] == "part 2: one frame late"
    got = json.loads((proj / E.PROJECT_SUB / "enhance.json").read_text(encoding="utf-8"))
    assert got["parts_failed"] == "part 2: one frame late"
