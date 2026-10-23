"""Day 16, task 3: only the movie's piece in the run folder (keep_piece.py) -- the frames every RAW clip plays, grouped,
+- a margin from the keyframe before, cut by stream copy into ONE file (one piece per group, in a row), A1's WAV cut
sample for sample, every RAW clip of the XMLs moved onto the piece; the scores read it back in the movie's frames,
Learn in the piece's; the whole movie when the pieces would be too big or anything does not check out."""
from __future__ import annotations

import json
import subprocess
import wave
import xml.etree.ElementTree as ET
from fractions import Fraction
from pathlib import Path

import numpy as np
import pytest

from match_cuts import keep_piece as K
from match_cuts.common import ffmpeg_bin
from match_cuts.export_xml_edl import _file_url

FPS = Fraction(24000, 1001)


def _movie(path: Path, seconds: float = 60.0) -> Path:
    """testsrc2 at 24000/1001, B-frames, a keyframe every 24 frames (the day 14 test's movie, stereo)."""
    subprocess.run([ffmpeg_bin(), "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=size=320x180:rate=24000/1001",
                    "-f", "lavfi", "-i", "anoisesrc=color=pink:sample_rate=48000:amplitude=0.4:seed=3",
                    "-map", "0:v", "-map", "1:a", "-t", str(seconds), "-c:v", "libx264", "-bf", "2", "-g", "24",
                    "-keyint_min", "24", "-sc_threshold", "0", "-pix_fmt", "yuv420p", "-c:a", "aac", "-ac", "2",
                    str(path)], check=True)
    return path


def _wav(path: Path, seconds: float = 60.0) -> np.ndarray:
    n = int(seconds * 48000)
    data = (np.arange(n * 2, dtype=np.int64) % 65521 - 32760).astype(np.int16)    # every sample its own value
    with wave.open(str(path), "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(48000)
        w.writeframes(data.tobytes())
    return data.reshape(-1, 2)


def _clip(cid: str, fid: str, start: int, end: int, a: int, b: int, define: Path | None) -> str:
    f = (f"<file id=\"{fid}\"><name>{define.name}</name><pathurl>{_file_url(str(define))}</pathurl>"
         "<rate><timebase>24</timebase><ntsc>TRUE</ntsc></rate><duration>1438</duration></file>"
         if define is not None else f"<file id=\"{fid}\"/>")
    return (f"<clipitem id=\"{cid}\"><name>x</name><rate><timebase>24</timebase><ntsc>TRUE</ntsc></rate>"
            f"<duration>1438</duration><start>{start}</start><end>{end}</end><in>{a}</in><out>{b}</out>{f}</clipitem>")


def _run(tmp_path: Path, spans: list[tuple[int, int]]) -> tuple[Path, Path, np.ndarray]:
    run = tmp_path / "edit"
    media = run / "extras" / "media"
    media.mkdir(parents=True)
    movie = _movie(tmp_path / "The.Movie.mp4")
    raw = media / "raw.mp4"
    raw.hardlink_to(movie)
    samples = _wav(media / "raw_stereo.wav")
    v, a, t = [], [], 0
    for i, (x, y) in enumerate(spans):
        v.append(_clip(f"clipitem-{i + 1}", "file-raw", t, t + y - x, x, y, raw if i == 0 else None))
        a.append(_clip(f"clipitem-{100 + i}", "file-raw-sound", t, t + y - x, x, y,
                       media / "raw_stereo.wav" if i == 0 else None))
        t += y - x
    xml = ("<?xml version=\"1.0\" encoding=\"UTF-8\"?>\n<!DOCTYPE xmeml>\n<xmeml version=\"5\"><sequence>"
           f"<rate><timebase>24</timebase><ntsc>TRUE</ntsc></rate><duration>{t}</duration><media>"
           f"<video><track>{''.join(v)}</track></video><audio><track>{''.join(a)}</track></audio>"
           "</media></sequence></xmeml>\n")
    (run / "1_edit.xml").write_text(xml, encoding="utf-8")
    (run / "extras" / "edit_60fps.xml").write_text(xml, encoding="utf-8")
    return run, movie, samples


@pytest.fixture
def short_ok(monkeypatch):
    monkeypatch.setattr(K, "MIN_RAW_S", 0.0)                 # (a one-minute movie stands for a film)
    from match_cuts import export_xml_edl
    monkeypatch.setattr(export_xml_edl, "premiere_item_problems", lambda p: [])


def _ins(xml: Path) -> list[tuple[str, int, int]]:
    root = ET.parse(str(xml)).getroot()
    return [(ci.get("id"), int(ci.findtext("in")), int(ci.findtext("out"))) for ci in root.iter("clipitem")]


def _gray(path: Path, frames: list[int]) -> dict[int, np.ndarray]:
    from match_cuts.media import VideoReader
    with VideoReader(str(path), fps=FPS) as r:
        return r.get_many(frames, fmt="gray")


def test_two_far_groups_make_one_piece_file_and_every_clip_plays_its_frame(tmp_path, short_ok):
    spans = [(100, 130), (150, 190), (1200, 1250)]          # 4-8 s, and 50 s: two groups with a 5 s margin
    run, movie, samples = _run(tmp_path, spans)
    before = _ins(run / "1_edit.xml")
    out = K.keep_run(run, [movie], margin_s=5.0)
    assert out["kept"], out
    gs = [K.Group(**g) for g in out["groups"]]
    assert len(gs) == 2 and gs[0].offset == 0 and gs[1].offset == gs[0].frames
    assert gs[0].k0 % 24 == 0 and gs[1].k0 % 24 == 0                                  # from keyframes
    assert gs[0].k0 == 0 and 1200 - 5 * FPS - 24 < gs[1].k0 <= 1200 - 5 * FPS        # (the movie's start; 5 s before)
    assert gs[1].k1 >= 1250 + 5 * FPS
    media = run / "extras" / "media"
    piece = media / "raw.mov"
    assert piece.is_file() and not (media / "raw.mp4").exists() and movie.is_file()  # the link gone, the movie kept
    after = _ins(run / "1_edit.xml")
    for (cid, a0, b0), (cid2, a1, b1) in zip(before, after):
        g = gs[0] if a0 < gs[0].k1 else gs[1]
        assert cid == cid2 and a1 == a0 - g.shift and b1 == b0 - g.shift
    want = [a for _c, a, _b in before[:3]]
    got_m, got_p = _gray(movie, want), _gray(piece, [a for _c, a, _b in after[:3]])
    for k, (_c, a, _b) in zip(want, after[:3]):
        assert np.array_equal(got_m[k], got_p[a])                                      # the very same pictures
    with wave.open(str(media / "raw_stereo.wav"), "rb") as w:
        new = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).reshape(-1, 2)
    r = 48000 / float(FPS)
    for g in gs:
        p0, s0 = int(round(g.offset * r)), int(round(g.k0 * r))
        assert np.array_equal(new[p0:p0 + 1000], samples[s0:s0 + 1000])                 # sample for sample
    assert out["frames"] == sum(g.frames for g in gs) and json.loads(
        (run / "extras" / K.INFO_NAME).read_text(encoding="utf-8"))["kept"]
    root = ET.parse(str(run / "1_edit.xml")).getroot()
    f = next(fe for fe in root.iter("file") if fe.get("id") == "file-raw" and fe.find("pathurl") is not None)
    assert f.findtext("pathurl") == _file_url(str(piece.resolve())) and f.findtext("name") == "raw.mov"


def test_the_scores_read_it_back_in_the_movies_frames_and_learn_in_the_pieces(tmp_path, short_ok):
    run, movie, _s = _run(tmp_path, [(100, 130), (1200, 1250)])
    out = K.keep_run(run, [movie], margin_s=5.0)
    assert out["kept"]
    from match_cuts.edit_score import Edit, movie_times
    e = movie_times(Edit.from_xml(run / "1_edit.xml"), run)
    raws = [round(p.raw * float(FPS)) for p in e.pieces if p.raw is not None]
    assert raws == [100, 1200]
    from match_cuts.excerpt import movie_cutlist
    cl = {"raw": {"frames": 1438, "fps": "24000/1001"}, "segments": [
        {"raw_in_frame": 100, "raw_out_frame": 129, "raw_in_seconds": 100 / float(FPS)},
        {"raw_in_frame": 1200, "raw_out_frame": 1249, "raw_in_seconds": 1200 / float(FPS)}]}
    assert movie_cutlist(cl, run) == cl                                                 # the scores: the movie's
    played = movie_cutlist(cl, run, as_played=True)
    gs = [K.Group(**g) for g in out["groups"]]
    assert [s["raw_in_frame"] for s in played["segments"]] == [100 - gs[0].shift, 1200 - gs[1].shift]
    assert played["raw"]["frames"] == out["frames"] and played["raw"]["file"] == "media/raw.mov"


def test_clips_near_each_other_make_one_piece_and_too_much_keeps_the_whole_movie(tmp_path, short_ok):
    run, movie, _s = _run(tmp_path, [(100, 130), (400, 450)])
    out = K.keep_run(run, [movie], margin_s=8.0)            # 12.5 s apart < 2 x 8 s: one group
    assert out["kept"] and len(out["groups"]) == 1
    run2, movie2, _s2 = _run(tmp_path / "b", [(100, 130), (1300, 1350)])
    before = (run2 / "1_edit.xml").read_text(encoding="utf-8")
    out2 = K.keep_run(run2, [movie2], margin_s=20.0)        # 2 x 40 s of 60: more than 60 %
    assert not out2["kept"] and "more than 60 %" in out2["why"]
    assert (run2 / "1_edit.xml").read_text(encoding="utf-8") == before                 # nothing changed
    assert (run2 / "extras" / "media" / "raw.mp4").is_file() and not (run2 / "extras" / "media" / "raw.mov").exists()


def test_a_short_raw_or_a_failed_check_changes_nothing(tmp_path, monkeypatch):
    from match_cuts import export_xml_edl
    monkeypatch.setattr(export_xml_edl, "premiere_item_problems", lambda p: [])
    run, movie, _s = _run(tmp_path, [(100, 130)])
    assert not K.keep_run(run, [movie], margin_s=5.0)["kept"]                         # 1 min: under 15 min
    monkeypatch.setattr(K, "MIN_RAW_S", 0.0)
    monkeypatch.setattr(K, "check_frames", lambda *a, **k: {"ok": False, "rows": [], "why": "made-up mismatch"})
    out = K.keep_run(run, [movie], margin_s=5.0)
    assert not out["kept"] and "made-up mismatch" in out["why"]
    media = run / "extras" / "media"
    assert (media / "raw.mp4").is_file() and not list(media.glob("*.new.*")) and not (media / "raw.mov").exists()


def test_the_plan_merges_touching_groups_and_respects_a_speed_step():
    from match_cuts.excerpt import Movie
    n = 10000
    m = Movie(path="m.mp4", fps="24000/1001", tb="1/24000", frames=n, duration=n / float(FPS),
              keyframes=[(k, k * 1001, k * 1001 - 2002) for k in range(0, n, 7)])
    gs, why = K.plan_groups(m, [(3000, 3100), (6000, 6100)], step=5, margin_s=30.0)
    assert gs is not None and len(gs) == 2, why
    assert all((g.k0 - g.offset) % 5 == 0 for g in gs)
    gs2, _ = K.plan_groups(m, [(3000, 3100), (3900, 4000)], margin_s=30.0)      # 800 frames < 2 x 720: one group
    assert len(gs2) == 1
    assert K.to_piece(gs[1].k0 + 3, gs) == gs[1].offset + 3 and K.to_piece(n - 1, gs) is None
