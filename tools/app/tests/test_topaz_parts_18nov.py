"""18 Nov (The Best Business Idea In Key and Peele (2): 57 s of clips spread over 3:04 of the RAW -- Topaz enhanced a
3:24 piece, about an hour): only the parts of the piece the edit plays are enhanced (enhance_swap.play_ranges: each
clip +- 1 s, parts closer than 2 s joined (20 Nov: were 2 s and 6 s), the outer ends 10 s as before), each part on its own, joined into one
enhanced file; the swap finds every clip's frames in it. Tested hard, with a fake Topaz, on a movie in which every
frame is a different picture (so "the same moment" means the same frame, not a look-alike):

* which parts are made, and when the whole piece is enhanced instead (clips next to each other, little to save, a
  sequence that is not 60 fps, a piece sent again after a failure, the switch off);
* the command of one part: the user's filters and encoder exactly, read frame-exactly, an exact number of frames;
* the whole way: one piece in the line, its parts enhanced and joined, every swapped clip shows its own RAW frame;
* a part that does not start on its frame, or a Topaz computer that does not know about parts (an older app):
  the whole piece, and the swap still right."""
from __future__ import annotations

import subprocess
import sys
import xml.etree.ElementTree as ET
from fractions import Fraction
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "match_cuts"))

from mr_app.core import enhance as E  # noqa: E402
from mr_app.core import enhance_swap as ES  # noqa: E402
from mr_app.core import projects as PJ  # noqa: E402

FPS = 24
F = Fraction(FPS)


# ---------------------------------------------------------------------------------------------------------------------
# which parts
# ---------------------------------------------------------------------------------------------------------------------

def test_clips_far_apart_make_parts_with_their_margins_and_the_outer_ends_are_the_pieces(monkeypatch):
    monkeypatch.delenv(ES.RANGES_ENV, raising=False)
    # the piece: movie frames 1000-4600 (150 s at 24 fps); the edit plays 1240-1360 and 4240-4360
    rs = ES.play_ranges([(1240, 1360), (4240, 4360)], F, 1000, 3600)
    assert rs == [[0, 360 + 24], [3240 - 24, 3600]]               # 1 s more at the gap, the piece's own ends outside
    assert E.valid_ranges({"ranges": rs, "frames": 3600}) == [(0, 384), (3216, 3600)]
    assert E.range_out_frames(384, "24") == 960 and E.ranges_out_frames({"ranges": rs, "frames": 3600,
                                                                          "fps": "24"}) == 1920


def test_clips_closer_than_the_gap_are_one_part_and_little_to_save_keeps_the_whole_piece(monkeypatch):
    monkeypatch.delenv(ES.RANGES_ENV, raising=False)
    # 5 s between the clips: with 1 s each side 3 s are left out (more than 2 s) -- a part of its own
    assert ES.play_ranges([(1240, 1360), (1480, 1600), (4240, 4360)], F, 1000, 3600) == [[0, 384], [456, 624],
                                                                                         [3216, 3600]]
    # 3 s between the clips: with 1 s each side only 1 s would be left out -- one part
    assert ES.play_ranges([(1240, 1360), (1432, 1600), (4240, 4360)], F, 1000, 3600) == [[0, 624], [3216, 3600]]
    assert ES.play_ranges([(1240, 1360), (1480, 1600)], F, 1000, 840) == []       # next to each other: the whole piece
    # 12 s to save of 100 s: under 20 % and under 20 s -- not worth another start of Topaz
    assert ES.play_ranges([(1240, 1960), (2344, 3160)], F, 1000, 2400) == []
    assert ES.play_ranges([], F, 1000, 3600) == [] and ES.play_ranges([(1240, 1360)], F, 1000, 0) == []
    monkeypatch.setenv(ES.RANGES_ENV, "0")                                           # the switch: as before 18 Nov
    assert ES.play_ranges([(1240, 1360), (4240, 4360)], F, 1000, 3600) == []


def test_what_the_cut_added_to_reach_the_keyframes_is_left_out(monkeypatch):
    monkeypatch.delenv(ES.RANGES_ENV, raising=False)
    # the piece starts 5 s before "10 s before the first clip" and ends 8 s after "10 s after the last" (keyframes)
    assert ES.play_ranges([(1240, 1360), (1480, 1600)], F, 880, 840 + 120 + 192) == [[120, 960]]   # one part: no gap
    assert ES.play_ranges([(1240, 1360), (4240, 4360)], F, 880, 3600 + 120 + 192) == [[120, 504], [3336, 3720]]
    # a gap that is not worth another start of Topaz: still one part, without the lead-in and the tail
    assert ES.play_ranges([(1240, 1960), (2344, 3160)], F, 880, 2400 + 120 + 192) == [[120, 2520]]


def test_many_small_parts_are_joined_down_to_twenty(monkeypatch):
    monkeypatch.delenv(ES.RANGES_ENV, raising=False)
    clips = [(1000 + 600 * i, 1000 + 600 * i + 48) for i in range(30)]               # 30 clips, 23 s apart
    rs = ES.play_ranges(clips, F, 760, 600 * 29 + 48 + 480)
    assert len(rs) == ES.MAX_RANGES and rs[0][0] == 0 and rs[-1][1] == 600 * 29 + 48 + 480
    assert all(b > a for a, b in rs) and all(rs[i][1] < rs[i + 1][0] for i in range(len(rs) - 1))


def test_parts_that_are_not_sound_mean_the_whole_piece():
    ok = {"frames": 3600, "fps": "24", "ranges": [[0, 408], [3192, 3600]]}
    assert E.valid_ranges(ok) == [(0, 408), (3192, 3600)]
    for bad in ([[0, 3600]], [[0, 408], [3192, 3700]], [[-5, 408], [3192, 3600]], [[0, 408], [400, 3600]],
                [[0, 408], [408, 3600]], [[500, 408]], [[0, "x"], [3192, 3600]], None, "nonsense"):
        assert E.valid_ranges(dict(ok, ranges=bad)) == [], bad
    # one part is sound too: the piece without what the cut added to reach the RAW's keyframes
    assert E.valid_ranges(dict(ok, ranges=[[100, 3500]])) == [(100, 3500)]
    assert E.valid_ranges(dict(ok, ranges=[[10, 408], [3192, 3500]])) == [(10, 408), (3192, 3500)]
    assert E.valid_ranges({"ranges": ok["ranges"]}) == []                            # (no length: not known)


# ---------------------------------------------------------------------------------------------------------------------
# the command of one part
# ---------------------------------------------------------------------------------------------------------------------

def test_a_parts_command_is_the_users_topaz_exactly_plus_the_part():
    tz = E.Topaz(Path("ffmpeg.exe"), Path("models"), Path("."))
    whole = E.topaz_command(tz, "piece.mp4", "out.mp4", 1.0, 2)
    part = E.topaz_command(tz, "piece.mp4", "out.mp4", 1.0, 2, (133.0, 17.0, 1020))
    vf_w, vf_p = whole[whole.index("-vf") + 1], part[part.index("-vf") + 1]
    assert vf_p == vf_w + ",tpad=stop_mode=clone:stop_duration=2"                    # Proteus + Apollo as they were
    assert vf_w.startswith("tvai_up=model=prob-3:scale=2") and "tvai_fi=model=apo-8" in vf_w
    enc = lambda c: c[c.index("-c:v"):c.index("-tag:v") + 2]                         # noqa: E731
    assert enc(part) == enc(whole) and "60M" in enc(whole)                           # the same H.265, 60 Mb/s
    assert part[part.index("-ss") + 1] == "132.999800" and part[part.index("-t") + 1] == "17.000000"
    assert part.index("-ss") < part.index("-i")                                      # read from there, frame-exactly
    assert part[part.index("-frames:v") + 1] == "1020"
    assert "-c:a" not in part and "0:a:0?" not in part                               # no sound in a part
    assert "-ss" not in whole and "-frames:v" not in whole and "0:a:0?" in whole     # a whole piece: as before
    only_apollo = E.topaz_command(tz, "piece.mp4", "out.mp4", 1.0, 1, (0.0, 5.0, 300))
    assert only_apollo[only_apollo.index("-vf") + 1].startswith("tvai_fi=") and "tvai_up" not in " ".join(only_apollo)


# ---------------------------------------------------------------------------------------------------------------------
# the whole way, on a movie whose every frame is another picture
# ---------------------------------------------------------------------------------------------------------------------

@pytest.fixture(scope="module")
def movie(tmp_path_factory) -> Path:
    """200 s, 24 fps, 160x90, a keyframe every 2 s; every frame is its own random picture (no two look alike)."""
    from match_cuts.common import ffmpeg_bin
    out = tmp_path_factory.mktemp("parts_movie") / "movie.mp4"
    subprocess.run([ffmpeg_bin(), "-v", "error", "-y", "-f", "lavfi", "-i",
                    f"nullsrc=s=40x22:r={FPS}:d=200,geq=lum='random(1)*255':cb=128:cr=128,scale=160:90:flags=neighbor",
                    "-f", "lavfi", "-i", "sine=duration=200", "-c:v", "libx264", "-preset", "ultrafast", "-crf", "12",
                    "-g", "48", "-keyint_min", "48", "-sc_threshold", "0", "-bf", "0", "-pix_fmt", "yuv420p",
                    "-c:a", "aac", "-shortest", str(out)], check=True)
    return out


def _url(p: Path) -> str:
    return "file://localhost/" + p.resolve().as_posix().lstrip("/")


def _edit(tmp_path: Path, movie: Path, spans, seq_fps: int = 60) -> PJ.Project:
    """A finished edit as the tool writes it for Topaz: a ``seq_fps`` sequence, V1 clips at that rate playing
    extras/media/raw.mp4 (24 fps); ``spans``: (first RAW frame, frame after the last) of each clip."""
    p = PJ.create(tmp_path / "projects", name="parts")
    media = p.folder / "edit" / "extras" / "media"
    media.mkdir(parents=True)
    raw = media / "raw.mp4"
    raw.hardlink_to(movie)
    k = Fraction(seq_fps, FPS)                              # sequence frames per RAW frame
    clips = ""
    for i, (a, b) in enumerate(spans):
        f = (f"<file id=\"file-raw\"><name>raw.mp4</name><pathurl>{_url(raw)}</pathurl><rate><timebase>{FPS}</timebase>"
             f"<ntsc>FALSE</ntsc></rate><duration>4800</duration></file>" if i == 0 else "<file id=\"file-raw\"/>")
        clips += (f"<clipitem id=\"v{i}\"><name>raw.mp4</name><duration>{int(4800 * k)}</duration><rate><timebase>"
                  f"{seq_fps}</timebase><ntsc>FALSE</ntsc></rate><in>{int(a * k)}</in><out>{int(b * k)}</out>{f}"
                  f"<filter><effect><name>Basic Motion</name><effectid>basic</effectid><parameter><parameterid>scale"
                  f"</parameterid><value>86.0</value></parameter></effect></filter></clipitem>")
    (p.folder / "edit" / "1_edit.xml").write_text(
        f"<?xml version=\"1.0\"?>\n<xmeml version=\"5\"><sequence><rate><timebase>{seq_fps}</timebase><ntsc>FALSE</ntsc>"
        f"</rate><media><video><track>{clips}</track></video></media></sequence></xmeml>", encoding="utf-8")
    p.set_step("edit", PJ.DONE, result="PASS", run_dir=str(p.folder / "edit"), finished=PJ.now())
    enh = tmp_path / "Enhance"
    enh.mkdir(exist_ok=True)
    p.update(enhance={"state": "skipped", "why": "Topaz comes after the edit", "after_edit": True, "topaz_on": "",
                      "name": E.safe_name(p.folder.name), "folder": str(enh)})
    return PJ.load(p.folder)


SPANS = [(600, 720), (720, 780), (3600, 3720)]             # 25-30 s, 30-32.5 s ... and 150-155 s of the movie


@pytest.fixture
def topaz(monkeypatch):
    monkeypatch.setenv(E.FAKE_ENV, "1")
    monkeypatch.delenv(ES.RANGES_ENV, raising=False)


def _shown(p: PJ.Project, movie: Path) -> list[tuple[str, int, int]]:
    """For every V1 clip of the swapped edit: (the file it plays, the RAW frame its first enhanced frame shows, the
    RAW frame its last one shows) -- found by comparing the pictures with the movie's own frames."""
    root = ET.parse(str(p.folder / "edit" / "1_edit.xml")).getroot()
    enh = p.folder / "edit" / "extras" / "media" / E.PROJECT_FILE
    out = []
    for c in root.iter("clipitem"):
        fid = c.find("file").get("id")
        a, b = int(c.findtext("in")), int(c.findtext("out"))
        if fid != "file-enhanced":
            out.append((fid, a, b))
            continue
        got = ES._gray(enh, Fraction(E.OUT_FPS), [a, b - 1])                          # noqa: SLF001
        res = []
        for want_frame, pic in ((a, got[a]), (b - 1, got[b - 1])):
            near = _nearest(movie, pic)
            res.append(near)
        out.append((fid, res[0], res[1]))
    return out


_MOVIE_FRAMES: dict = {}


def _nearest(movie: Path, pic) -> int:
    """The movie's frame that ``pic`` (gray, 160 px) shows: the best ZNCC over all its frames."""
    import numpy as np
    if movie not in _MOVIE_FRAMES:
        g = ES._gray(movie, F, list(range(0, 4800)))                                 # noqa: SLF001
        ks = sorted(g)
        arr = np.stack([g[k].astype(np.float32).ravel() for k in ks])
        arr -= arr.mean(axis=1, keepdims=True)
        arr /= np.linalg.norm(arr, axis=1, keepdims=True) + 1e-9
        _MOVIE_FRAMES[movie] = (ks, arr)
    ks, arr = _MOVIE_FRAMES[movie]
    v = pic.astype(np.float32).ravel()
    v -= v.mean()
    v /= np.linalg.norm(v) + 1e-9
    return int(ks[int(np.argmax(arr @ v))])


def test_only_the_played_parts_are_enhanced_and_every_clip_shows_its_own_frames(tmp_path, movie, topaz):
    p = _edit(tmp_path, movie, SPANS)
    r = ES.cover_all(p.folder)
    assert r["ok"] and r["clips"] == 3, r
    rec = PJ.load(p.folder).data["enhance"]
    info = rec["piece"]
    enh = Path(rec["folder"])
    assert len(E.waiting_pieces(enh)) == 1                                           # ONE piece in the line, as before
    start, n = info["start_frame"], info["frames"]
    assert start <= 600 - 240 and start + n >= 3720 + 240                            # 10 s before and after, as before
    rs = E.valid_ranges(info)
    assert len(rs) == 2 and start + rs[0][0] == 600 - 240 and start + rs[-1][1] == 3720 + 240   # 10 s, as before
    assert start + rs[0][1] == 780 + 24 and start + rs[1][0] == 3600 - 24            # 1 s more at the gap
    assert not info.get("encoded") and all(r["ssim"] > 0.999 for r in info["check"])  # the RAW's own frames
    assert E.read_info(E.names(enh, rec["name"]).info)["ranges"] == [list(x) for x in rs]   # Topaz reads the same
    assert ES.outside(p.folder)["need"] == []                                        # every clip is in a part
    job = E.Worker(enh).once()
    assert job.state == "done" and not job.note, (job.why, job.note)
    nm = E.names(enh, rec["name"])
    assert not list(enh.glob("*.p?.part.mp4")) and not list(enh.glob("*.parts.txt"))  # the parts' own files are gone
    assert E._frames_of(nm.enhanced) == E.ranges_out_frames(info)                    # noqa: SLF001
    assert E._frames_of(nm.enhanced) < 0.45 * n * 60 / FPS                           # noqa: SLF001 - under half of it
    w = E.watch(p.folder)
    assert w.get("state") == "used" and w.get("swapped"), w
    rec = PJ.load(p.folder).data["enhance"]
    assert "3 of 3 V1 clips play it" in rec["text"] and "only the 2 played parts were enhanced" in rec["text"]
    assert rec["swap"]["kept"] == 0 and len(rec["swap"]["check"]) >= 2
    shown = _shown(PJ.load(p.folder), movie)
    assert shown == [("file-enhanced", 600, 719), ("file-enhanced", 720, 779), ("file-enhanced", 3600, 3719)]
    assert ES.outside(p.folder)["need"] == [] and ES.cover_all(p.folder)["clips"] == 0   # nothing is sent again


def test_a_part_that_does_not_start_on_its_frame_means_the_whole_piece_and_the_swap_is_still_right(tmp_path, movie,
                                                                                                    topaz, monkeypatch):
    p = _edit(tmp_path, movie, SPANS)
    assert ES.cover_all(p.folder)["clips"] == 3
    rec = PJ.load(p.folder).data["enhance"]
    info = rec["piece"]
    monkeypatch.setattr(E, "_part_starts_right", lambda *a, **k: "its first frame shows the piece's frame 409, not 408")
    job = E.Worker(Path(rec["folder"])).once()
    assert job.state == "done" and "the whole piece instead" in job.note
    nm = E.names(rec["folder"], rec["name"])
    assert abs(E._frames_of(nm.enhanced) - info["frames"] * 60 // FPS) <= 4          # noqa: SLF001 - all of the piece
    w = E.watch(p.folder)
    assert w.get("state") == "used" and w.get("swapped"), w
    text = PJ.load(p.folder).data["enhance"]["text"]
    assert "3 of 3 V1 clips play it" in text and "played parts" not in text
    assert _shown(PJ.load(p.folder), movie) == [("file-enhanced", 600, 719), ("file-enhanced", 720, 779),
                                                ("file-enhanced", 3600, 3719)]
    assert ES.outside(p.folder)["need"] == []                                        # the whole piece covers them


def test_the_real_check_of_a_parts_first_frame_tells_a_frame_too_late(tmp_path, movie, topaz):
    from match_cuts.common import ffmpeg_bin
    tz = E.find_topaz()
    for first, shift, ok in ((408, 0, True), (408, 1, False), (408, -1, False)):
        out = tmp_path / f"part_{shift}.mp4"
        cmd = E.topaz_command(tz, movie, out, 1.0, 2, (float(Fraction(first + shift, FPS)), 2.0, 120))
        subprocess.run(cmd, check=True, capture_output=True)
        why = E._part_starts_right(movie, F, first, out)                             # noqa: SLF001
        assert (why == "") is ok, (shift, why)
        assert E._frames_of(out) == 120                                              # noqa: SLF001 - exactly as asked
    assert ffmpeg_bin()


def test_a_topaz_computer_that_does_not_know_about_parts_enhances_the_whole_piece_and_it_is_used(tmp_path, movie,
                                                                                                 topaz, monkeypatch):
    p = _edit(tmp_path, movie, SPANS)
    assert ES.cover_all(p.folder)["clips"] == 3
    rec = PJ.load(p.folder).data["enhance"]
    monkeypatch.setattr(E, "valid_ranges", lambda info: [])                           # an app from before 18 Nov
    job = E.Worker(Path(rec["folder"])).once()
    monkeypatch.undo()
    monkeypatch.setenv(E.FAKE_ENV, "1")
    assert job.state == "done"
    w = E.watch(p.folder)
    assert w.get("state") == "used" and w.get("swapped"), w
    assert _shown(PJ.load(p.folder), movie) == [("file-enhanced", 600, 719), ("file-enhanced", 720, 779),
                                                ("file-enhanced", 3600, 3719)]


def test_no_parts_for_a_sequence_that_is_not_60_fps_or_a_piece_sent_again(tmp_path, movie, topaz):
    p = _edit(tmp_path / "a", movie, SPANS, seq_fps=24)                              # the RAW's own rate
    assert ES.cover_all(p.folder)["clips"] == 3
    assert "ranges" not in PJ.load(p.folder).data["enhance"]["piece"]
    p = _edit(tmp_path / "b", movie, SPANS)
    assert ES.cover_all(p.folder)["clips"] == 3
    rec = PJ.load(p.folder).data["enhance"]
    assert E.valid_ranges(rec["piece"])
    p.update(enhance={"state": "failed", "why": "Topaz could not enhance it: Topaz stopped (code 69)"})
    assert ES.cover_all(p.folder)["clips"] == 3                                      # sent again: whole, as before
    rec = PJ.load(p.folder).data["enhance"]
    assert rec["redos"] == 1 and "ranges" not in rec["piece"]
    assert rec["piece"].get("encoded")                     # 19 Nov: ... and made another way (test_retry_19nov.py)


def test_a_broken_result_is_neither_the_whole_piece_nor_its_parts(tmp_path, movie, topaz):
    info = {"frames": 3600, "fps": "24", "duration": 150.0, "width": 160, "height": 90,
            "ranges": [[0, 408], [3192, 3600]]}
    from match_cuts.common import ffmpeg_bin
    out = tmp_path / "short.mp4"
    subprocess.run([ffmpeg_bin(), "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=size=320x180:rate=60:d=30",
                    "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", str(out)], check=True)
    chk = E.check_enhanced(out, info)                                                # 1800 frames: not 2040, not 9000
    assert not chk["ok"] and "played parts" in chk["why"] and not chk.get("ranged")
