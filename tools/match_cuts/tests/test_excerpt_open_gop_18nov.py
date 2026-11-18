"""18 Nov: the piece of a movie whose keyframes are "open" (H.265 as most of the downloaded episodes are encoded: a
few pictures SHOWN before a keyframe are stored after it).

Key & Peele S05E03 (The Most Jealous Group Leader, made again on the PC): "piece of the movie: no -- the piece has 6458
frames, the plan 6455 (the whole RAW is analysed)". The stream copy took those pictures with it: 3 frames too many,
the piece was refused, the whole 21-minute episode was analysed (187 s of conversion instead of none), stayed in the
project and went into its zip (593 MB). The same line made Topaz pieces be re-encoded first ("the lossless cut
failed: the piece has 619 frames, the plan 614"). ``copy_packets`` leaves them out now: the piece has exactly its
plan's frames, and they are the movie's."""
from __future__ import annotations

import subprocess
from fractions import Fraction

import pytest

from match_cuts import excerpt as X
from match_cuts.common import ffmpeg_bin


@pytest.fixture(scope="module")
def open_gop_movie(tmp_path_factory):
    """30 s of H.265 at 24 fps: a keyframe every 2 s, each one "open" (3 pictures shown before it stored after it)."""
    out = tmp_path_factory.mktemp("open_gop") / "movie.mp4"
    r = subprocess.run([ffmpeg_bin(), "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=size=320x180:rate=24:d=30",
                        "-f", "lavfi", "-i", "sine=duration=30", "-c:v", "libx265", "-preset", "ultrafast",
                        "-x265-params", "open-gop=1:keyint=48:min-keyint=48:bframes=3:b-adapt=0:scenecut=0:"
                        "log-level=error", "-pix_fmt", "yuv420p", "-tag:v", "hvc1", "-c:a", "aac", "-shortest",
                        str(out)], capture_output=True, text=True)
    if r.returncode != 0 or not out.is_file():
        pytest.skip(f"this ffmpeg cannot make H.265 with open keyframes: {(r.stderr or '').strip()[-200:]}")
    return out


def _plan(m, t0: float, t1: float):
    f = float(m.rate)
    keys = sorted(m.keyframes)
    k0 = max(k for k in keys if k[0] <= int(t0 * f))
    k1 = min(k for k in keys if k[0] >= int(t1 * f))
    return X.Plan(int(k0[0]), int(k1[0]), int(k0[1]), int(k1[2]), [t0])


def _leading(m, plan) -> int:
    """How many packets between the plan's two keyframes (in decoding order) are shown BEFORE the first one."""
    import av
    n = 0
    with av.open(m.path) as inp:
        vin = inp.streams.video[m.vindex]
        inp.seek(int(plan.pts0), stream=vin, backward=True, any_frame=False)
        for pkt in inp.demux(vin):
            if pkt.dts is None or pkt.pts is None:
                continue
            if int(pkt.dts) >= int(plan.dts_end):
                break
            n += int(pkt.pts) < int(plan.pts0)
    return n


def test_the_piece_of_a_movie_with_open_keyframes_has_exactly_its_plans_frames(open_gop_movie, tmp_path):
    m = X.movie_facts(str(open_gop_movie))
    assert not m.why and m.vcodec == "hevc" and len(m.keyframes) >= 10
    plan = _plan(m, 8.5, 20.5)                                   # from the keyframe at 8 s to the one at 22 s
    lead = _leading(m, plan)
    if lead == 0:
        pytest.skip("this encoder made closed keyframes: nothing to leave out")
    assert lead == 3                                             # the 3 pictures that made "3 frames too many"
    dst = tmp_path / "piece.mov"
    made = X.cut_piece(m, plan, dst)
    n = X._frames_in(dst)                                        # noqa: SLF001
    # the plan's frames from its first one on, none before it. (With this encoder the pictures shown just before
    # the END keyframe are stored after it too -- they cannot be in a piece that ends before it: it is 3 short at
    # its end, which the run allows; Key & Peele's file gave exactly the plan's 6515 frames.)
    assert plan.k1 - plan.k0 == 14 * 24 and plan.k1 - plan.k0 - lead <= n <= plan.k1 - plan.k0
    chk = X.same_frames(str(dst), m, plan.k0, n)
    assert chk["ok"], chk
    assert all(r["ssim"] > 0.99 for r in chk["rows"])            # ... and they are the movie's own pictures
    assert made["bytes"] > 0


def test_a_movie_with_closed_keyframes_is_cut_as_before(tmp_path):
    src = tmp_path / "closed.mp4"
    subprocess.run([ffmpeg_bin(), "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=size=320x180:rate=24:d=20",
                    "-c:v", "libx264", "-preset", "ultrafast", "-bf", "2", "-g", "48", "-keyint_min", "48",
                    "-sc_threshold", "0", "-pix_fmt", "yuv420p", str(src)], check=True)
    m = X.movie_facts(str(src))
    plan = _plan(m, 4.5, 11.0)
    assert _leading(m, plan) == 0
    dst = tmp_path / "piece.mov"
    X.cut_piece(m, plan, dst)
    n = X._frames_in(dst)                                        # noqa: SLF001
    assert n == plan.k1 - plan.k0 and X.same_frames(str(dst), m, plan.k0, n)["ok"]
    assert Fraction(m.rate) == 24


def test_a_piece_cut_from_a_piece_finds_its_keyframe(open_gop_movie, tmp_path):
    """The Topaz piece is cut from the piece the edit plays (a .mov made by ``cut_piece``): there a seek to a keyframe
    lands on the keyframe BEFORE it ("the seek landed on 1628960, not on the keyframe 1731056": the piece was then
    encoded instead of copied). The cut reads on to its own keyframe."""
    m = X.movie_facts(str(open_gop_movie))
    first = tmp_path / "raw.mov"
    plan = _plan(m, 2.5, 26.0)
    X.cut_piece(m, plan, first)
    m2 = X.movie_facts(str(first))
    assert not m2.why and len(m2.keyframes) >= 8
    inner = _plan(m2, 9.0, 15.0)                                  # a keyframe in the middle of the piece
    assert inner.k0 > 0
    second = tmp_path / "piece_of_the_piece.mov"
    X.cut_piece(m2, inner, second)                               # (it raised "the seek landed on ..." here)
    n = X._frames_in(second)                                     # noqa: SLF001
    assert 0 < n <= inner.k1 - inner.k0 and inner.k1 - inner.k0 - n <= 3
    assert X.same_frames(str(second), m2, inner.k0, n)["ok"]     # the piece's own frames ...
    assert X.same_frames(str(second), m, plan.k0 + inner.k0, n)["ok"]   # ... which are the movie's
