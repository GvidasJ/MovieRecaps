"""Day 14, only the needed part of the movie (excerpt.py): a long RAW whose sound check found the place is analysed in
a piece cut by stream copy (from a keyframe, every frame as in the movie, its sound sample-exact), and what the run
delivers is moved onto the movie (1_edit.xml's RAW clips at <in> / <out> + the piece's first frame -- on a sped-up
clip's own scale --, A1's sound file made to the movie's frames); the whole movie when the piece would be too big."""
from __future__ import annotations

import json
import subprocess
import wave
import xml.etree.ElementTree as ET
from fractions import Fraction
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from match_cuts import edit_media as em
from match_cuts import excerpt as E
from match_cuts import probe
from match_cuts.common import ffmpeg_bin


def _movie(path: Path, seconds: float = 40.0) -> Path:
    """A small 'movie': testsrc2 at 24000/1001 with B-frames and a keyframe every 24 frames, 5.1 noise."""
    subprocess.run([ffmpeg_bin(), "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=size=320x180:rate=24000/1001",
                    "-f", "lavfi", "-i", "anoisesrc=color=pink:sample_rate=48000:amplitude=0.4:seed=3",
                    "-filter_complex", "[1:a]pan=5.1|FL=0.5*c0|FR=0.4*c0|FC=0.3*c0|LFE=0.2*c0|BL=0.1*c0|BR=0.1*c0[a]",
                    "-map", "0:v", "-map", "[a]", "-t", str(seconds), "-c:v", "libx264", "-bf", "2", "-g", "24",
                    "-keyint_min", "24", "-sc_threshold", "0", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k",
                    str(path)], check=True)
    return path


# ---------------------------------------------------------------------------------------------------------------------
# where the piece goes
# ---------------------------------------------------------------------------------------------------------------------

def _facts(minutes: float = 120.0, gop: int = 48) -> E.Movie:
    fps = Fraction(24000, 1001)
    n = int(minutes * 60 * float(fps))
    return E.Movie(path="m.mp4", fps=str(fps), tb="1/24000", frames=n, duration=n / float(fps),
                   keyframes=[(k, k * 1001, k * 1001 - 2002) for k in range(0, n, gop)])


def test_the_piece_runs_from_a_keyframe_before_the_found_places_to_one_after_them():
    m = _facts()
    p = E.plan_piece(m, [1811.9, 1816.7, 1821.6])
    fps = float(m.rate)
    assert p is not None and p.k0 % 48 == 0 and p.k1 % 48 == 0
    assert p.k0 / fps <= 1811.9 - E.MARGIN_S < p.k0 / fps + 48 / fps                  # the keyframe before
    assert (p.k1 - 48) / fps < 1821.6 + 3.0 + E.MARGIN_S <= p.k1 / fps                 # ... to the one after
    assert p.dts_end == p.k1 * 1001 - 2002 and p.pts0 == p.k0 * 1001


def test_a_sped_up_edit_starts_the_piece_on_a_frame_its_speed_divides():
    m = _facts(gop=7)                                       # (keyframes every 7 frames: not all multiples of 5)
    p = E.plan_piece(m, [3000.0], speed=1.25)
    assert p is not None and p.k0 % 5 == 0 and p.k0 % 7 == 0
    assert E.speed_step(1.25) == 5 and E.speed_step(1.0) == 1 and E.speed_step(1.1) == 11


def test_places_too_far_apart_or_a_short_movie_use_the_whole_movie():
    m = _facts()
    assert E.plan_piece(m, [100.0, 3500.0]) is None         # 58 min + 10 min of 120: more than 40 %
    assert E.plan_piece(m, []) is None
    short = _facts(minutes=20.0)
    assert E.plan_piece(short, [600.0]) is None             # 10 of 20 minutes: more than 40 %


def test_only_a_premiere_run_whose_sound_was_found_uses_a_piece(monkeypatch):
    found = SimpleNamespace(sound=SimpleNamespace(state="found", where=[10.0]))
    cfg = SimpleNamespace(premiere=True)
    assert E.applies(cfg, found) == ""
    assert E.applies(SimpleNamespace(premiere=False), found)
    assert E.applies(cfg, None) and E.applies(cfg, SimpleNamespace(sound=SimpleNamespace(state="unclear", where=[1])))
    monkeypatch.setenv("MATCH_CUTS_NO_PIECE", "1")
    assert E.applies(cfg, found) == "switched off"


# ---------------------------------------------------------------------------------------------------------------------
# the cut: the movie's own frames, its sound sample-exact
# ---------------------------------------------------------------------------------------------------------------------

def test_the_piece_has_the_movies_frames_and_sound(tmp_path, monkeypatch):
    movie = _movie(tmp_path / "movie.mp4")
    m = E.movie_facts(movie)
    assert m.why == "" and m.frames == 959 and len(m.keyframes) >= 30 and m.a_channels == 6
    monkeypatch.setattr(E, "MARGIN_S", 6.0)
    monkeypatch.setattr(E, "MAX_SHARE", 0.9)
    p = E.plan_piece(m, [15.0])
    assert p is not None and p.k0 % 24 == 0 and 0 < p.k0 < 15.0 * float(m.rate)
    made = E.cut_piece(m, p, tmp_path / "work" / E.PIECE_NAME)
    n = E._frames_in(made["path"])
    assert n == p.k1 - p.k0                                  # every frame up to the next keyframe, none hidden
    chk = E.same_frames(made["path"], m, p.k0, n)
    assert chk["ok"], chk["why"]
    assert all(r["ssim"] > 0.999 for r in chk["rows"])     # (the same packets: the same pictures)
    info = probe.probe(made["path"], "raw", str(tmp_path / "work"), decode=True)
    assert info.nb_frames == n and not info.vfr and not info.ae_issues and abs(info.av_offset) < 1e-3
    assert info.acodec == "pcm_s16le" and info.a_channels == 6
    t0 = p.k0 / float(m.rate)                               # its sound: the movie's from the piece's first frame
    for t in (1.0, n / float(m.rate) / 2):
        a, b = em.decode_window(movie, t0 + t, 1.0), em.decode_window(made["path"], t, 1.0)
        lag, corr = em.best_lag(a, b)
        assert lag == 0 and corr > 0.999


def test_a_movie_that_cannot_be_cut_says_why(tmp_path):
    mkv = tmp_path / "movie.mkv"
    subprocess.run([ffmpeg_bin(), "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=size=320x180:rate=24",
                    "-t", "2", "-c:v", "libx264", str(mkv)], check=True)
    assert "mkv" in E.movie_facts(mkv).why
    jumpy = tmp_path / "phone.mp4"
    subprocess.run([ffmpeg_bin(), "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=size=320x180:rate=24000/1001",
                    "-t", "4", "-vf", "settb=1/240000,setpts=N*1001/24000/TB+0.01*mod(N\\,3)/TB", "-fps_mode",
                    "passthrough", "-enc_time_base", "1/240000", "-video_track_timescale", "240000", "-c:v", "libx264",
                    str(jumpy)], check=True)
    assert "not one step apart" in E.movie_facts(jumpy).why


# ---------------------------------------------------------------------------------------------------------------------
# the end of the run: the edit moved onto the movie
# ---------------------------------------------------------------------------------------------------------------------

def _rate_el(parent, tb: int, ntsc: bool) -> None:
    r = ET.SubElement(parent, "rate")
    ET.SubElement(r, "timebase").text = str(tb)
    ET.SubElement(r, "ntsc").text = "TRUE" if ntsc else "FALSE"


def _xml(path: Path, items: list[tuple[str, int, int, int, int, float]], piece: str, wav: str) -> Path:
    """A minimal 1_edit.xml: (id, start, end, in, out, speed) clips of file-raw on V1 and of file-raw-sound on A1."""
    from match_cuts.export_xml_edl import _file_url
    root = ET.Element("xmeml", version="5")
    seq = ET.SubElement(root, "sequence")
    _rate_el(seq, 24, True)
    media = ET.SubElement(seq, "media")
    for kind, fid, url in (("video", "file-raw", piece), ("audio", "file-raw-sound", wav)):
        tr = ET.SubElement(ET.SubElement(media, kind), "track")
        defined = False
        for cid, st, en, i, o, s in items:
            ci = ET.SubElement(tr, "clipitem", id=f"{cid}-{kind}")
            ET.SubElement(ci, "name").text = Path(url).name
            ET.SubElement(ci, "duration").text = "1000" if s == 1.0 else str(int(1000 / s))
            _rate_el(ci, 24, True)
            for tag, v in (("start", st), ("end", en), ("in", i), ("out", o)):
                ET.SubElement(ci, tag).text = str(v)
            fe = ET.SubElement(ci, "file", id=fid)
            if not defined:
                ET.SubElement(fe, "name").text = Path(url).name
                ET.SubElement(fe, "pathurl").text = _file_url(url)
                _rate_el(fe, 24, True)
                ET.SubElement(fe, "duration").text = "1000"
                defined = True
            flt = ET.SubElement(ci, "filter")
            eff = ET.SubElement(flt, "effect")
            ET.SubElement(eff, "effectid").text = "basic"
            par = ET.SubElement(eff, "parameter")
            kf = ET.SubElement(par, "keyframe")
            ET.SubElement(kf, "when").text = str(i)
            if s != 1.0:
                tr_eff = ET.SubElement(ET.SubElement(ci, "filter"), "effect")
                ET.SubElement(tr_eff, "effectid").text = "timeremap"
                sp = ET.SubElement(tr_eff, "parameter")
                ET.SubElement(sp, "parameterid").text = "speed"
                ET.SubElement(sp, "value").text = f"{100 * s:.4f}"
    ET.indent(root)
    path.write_text('<?xml version="1.0" encoding="UTF-8"?>\n<!DOCTYPE xmeml>\n' + ET.tostring(root, "unicode"),
                    encoding="utf-8")
    return path


def _ex(k0: int, movie: str = "movie.mp4") -> E.Excerpt:
    return E.Excerpt(piece="piece.mov", movie=movie, k0=k0, frames=1000, fps="24000/1001", movie_frames=180000,
                     movie_seconds=7507.5, places=[])


def test_the_edit_moves_onto_the_movie_on_each_clips_own_scale(tmp_path):
    src = _xml(tmp_path / "1_edit.xml", [("c1", 0, 24, 100, 124, 1.0), ("c2", 24, 48, 80, 104, 1.25)],
               str(tmp_path / "media" / "raw.mov"), str(tmp_path / "media" / "raw_stereo.wav"))
    out = tmp_path / "moved.xml"
    info = E.shift_xml(src, out, _ex(36200), tmp_path / "media" / "raw.mp4", "raw.mp4", wav_frames=37200)
    assert info["items"] == 4 and info["exact"] and info["shifts"] == [28960, 36200]
    root = ET.parse(out).getroot()
    got = {ci.get("id"): ci for ci in root.iter("clipitem")}
    assert (got["c1-video"].findtext("in"), got["c1-video"].findtext("out")) == ("36300", "36324")
    assert (got["c2-video"].findtext("in"), got["c2-video"].findtext("out")) == ("29040", "29064")   # 36200 / 1.25
    assert got["c1-video"].find(".//keyframe/when").text == "36300"
    assert got["c1-audio"].findtext("in") == "36300"                         # A1 the same frames as V1
    assert got["c1-video"].findtext("duration") == "180000" and got["c2-video"].findtext("duration") == "144000"
    assert got["c1-audio"].findtext("duration") == "37200"
    raw = next(f for f in root.iter("file") if f.get("id") == "file-raw" and f.find("pathurl") is not None)
    assert raw.findtext("pathurl").endswith("/media/raw.mp4") and raw.findtext("name") == "raw.mp4"
    assert got["c1-video"].findtext("name") == "raw.mp4"
    assert out.read_text(encoding="utf-8").startswith('<?xml version="1.0" encoding="UTF-8"?>\n<!DOCTYPE xmeml>')
    odd = E.shift_xml(src, tmp_path / "odd.xml", _ex(36201), tmp_path / "raw.mp4", "raw.mp4", None)
    assert not odd["exact"]                                  # (a frame 125 % does not divide: rounded, and said)


def test_the_movies_sound_file_is_silence_then_the_pieces_sound(tmp_path):
    piece = tmp_path / "raw_stereo.wav"
    y = (np.random.default_rng(1).standard_normal((48000, 2)) * 3000).astype("<i2")
    with wave.open(str(piece), "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(48000)
        w.writeframes(y.tobytes())
    n, tmp = E.movie_wav(piece, piece, _ex(240))
    assert n == 240 * 2002 + 48000                          # 240 frames at 23.976 = 480480 samples exactly
    with wave.open(str(tmp), "rb") as w:
        data = np.frombuffer(w.readframes(w.getnframes()), "<i2").reshape(-1, 2)
    assert len(data) == n and not data[:240 * 2002].any() and np.array_equal(data[240 * 2002:], y)


def test_a_run_made_on_a_piece_reads_as_if_the_whole_movie_was_analysed(tmp_path):
    run = tmp_path / "run"
    (run / "extras").mkdir(parents=True)
    cl = {"raw": {"frames": 1000, "duration_s": 41.7, "fps": "24000/1001", "file": "media/raw.mov"},
          "segments": [{"type": "raw", "raw_in_frame": 10, "raw_out_frame": 30, "raw_in_seconds": 0.42,
                        "raw_in_interval": [0.41, 0.43], "time_remap_keys": [{"comp_frame": 0, "raw_seconds": 0.5}],
                        "evidence": [{"raw": 12}, {"raw": -1}]}, {"type": "not_in_raw"}]}
    assert E.movie_cutlist(cl, run) == cl                    # no piece: as it is
    (run / "extras" / E.INFO_NAME).write_text(json.dumps({**_ex(2400).to_dict(), "moved": {"moved": True}}),
                                              encoding="utf-8")
    got = E.movie_cutlist(cl, run)
    s = got["segments"][0]
    t0 = 2400 * 1001 / 24000
    assert (s["raw_in_frame"], s["raw_out_frame"]) == (2410, 2430) and abs(s["raw_in_seconds"] - (0.42 + t0)) < 1e-9
    assert abs(s["raw_in_interval"][0] - (0.41 + t0)) < 1e-9 and abs(s["time_remap_keys"][0]["raw_seconds"] - (0.5 + t0)) < 1e-9
    assert [e["raw"] for e in s["evidence"]] == [2412, -1]
    assert got["raw"]["frames"] == 180000 and got["raw"]["file"] == "media/raw.mp4"
    assert cl["segments"][0]["raw_in_frame"] == 10           # (a copy)


# ---------------------------------------------------------------------------------------------------------------------
# the whole movie when the piece was not enough
# ---------------------------------------------------------------------------------------------------------------------

def test_a_piece_without_the_competitor_makes_the_run_again_on_the_whole_movie(tmp_path, monkeypatch):
    from match_cuts import pipeline
    piece = tmp_path / "work" / "excerpt" / E.PIECE_NAME
    piece.parent.mkdir(parents=True)
    piece.write_bytes(b"piece")
    cfg = SimpleNamespace(raw=str(piece), raw_movie=str(tmp_path / "movie.mp4"), media_dir=str(tmp_path / "media"))
    calls = []

    def once(c):
        calls.append(c.raw)
        if len(calls) == 1:
            raise pipeline.PictureNotFound("PICTURE NOT FOUND: its picture is not in the piece")
        return {"context": None, "exit_code": 0}
    monkeypatch.setattr(pipeline, "run_once", once)
    res = pipeline.run(cfg)
    assert res["exit_code"] == 0 and calls == [str(piece), str(tmp_path / "movie.mp4")]
    assert cfg.no_excerpt and cfg.raw_movie is None and not piece.exists()


def test_a_wrong_pair_or_a_whole_movie_run_stops_as_before(monkeypatch):
    from match_cuts import pipeline

    def once(c):
        raise pipeline.WrongPair("WRONG PAIR")
    monkeypatch.setattr(pipeline, "run_once", once)
    with pytest.raises(pipeline.WrongPair):
        pipeline.run(SimpleNamespace(raw="x.mp4", raw_movie="movie.mp4"))

    def once2(c):
        raise pipeline.NotInRaw("NOT IN RAW")
    monkeypatch.setattr(pipeline, "run_once", once2)
    with pytest.raises(pipeline.NotInRaw):
        pipeline.run(SimpleNamespace(raw="movie.mp4"))         # (no piece: nothing else to try)
