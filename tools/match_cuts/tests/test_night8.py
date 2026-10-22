"""Night 8: the picture searched again when the competitor's sound is in the RAW but (almost) none of its picture
(picture_retry; the laptop's --fast run: 100 % of the sound, 0 % of the picture, an edit with no clip), the PICTURE
NOT FOUND stop, and the lessons of your video7 / video8 / video9 fixes."""
from __future__ import annotations

from fractions import Fraction
from types import SimpleNamespace

import numpy as np
import pytest

from match_cuts import batch, picture_retry as PRT, pipeline
from match_cuts.model import Box, FrameMap, Layout, Proxy, Status


# ---------------------------------------------------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------------------------------------------------

def _hints(n: int, found: int, raw0: float = 100.0) -> SimpleNamespace:
    """n sound windows, the first ``found`` found in the RAW from ``raw0`` s on."""
    raw_t = np.full(n, np.nan)
    raw_t[:found] = raw0 + 0.25 * np.arange(found)
    conf = np.where(np.isfinite(raw_t), 3.0, 0.5)
    return SimpleNamespace(comp_t=0.25 * np.arange(n), raw_t=raw_t, conf=conf, window=1.0,
                           confident=lambda c, conf=conf, raw_t=raw_t: np.isfinite(raw_t) & (conf >= c))


def _fm(n: int, matched: int) -> FrameMap:
    fm = FrameMap(n)
    st = np.full(n, Status.NONE)
    st[:matched] = Status.MATCH
    fm.status = st
    return fm


def _proxy(frames: np.ndarray, fps: Fraction = Fraction(30)) -> Proxy:
    n, h, w = frames.shape
    return Proxy("competitor", "", frames, (2 * w, 2 * h), (0.5, 0.5), fps, np.arange(n) / float(fps), n)


def _ctx(fm: FrameMap, hints: SimpleNamespace, force: bool = False) -> SimpleNamespace:
    warned: list[str] = []
    return SimpleNamespace(cfg=SimpleNamespace(force_inputs=force, audio_min_conf=1.3, competitor="comp.mp4",
                                               raw="raw.mp4"),
                           fm_pre=fm, hints=hints, raw_info=SimpleNamespace(duration=1300.0, nb_frames=31000),
                           dlog=SimpleNamespace(record=lambda *a, **k: None), warned=warned,
                           warn=lambda m, analysis=False: warned.append(m))


# ---------------------------------------------------------------------------------------------------------------------
# 2a. when the picture is searched again
# ---------------------------------------------------------------------------------------------------------------------

def test_the_picture_is_searched_again_only_when_the_sound_is_found_and_the_picture_is_almost_not():
    assert PRT.needs_retry(0.0, 1.0)                    # the laptop's run: 0 % of the picture, 100 % of the sound
    assert PRT.needs_retry(0.24, 0.5)
    assert not PRT.needs_retry(0.25, 1.0)               # a quarter of the picture found: a normal run
    assert not PRT.needs_retry(0.36, 0.88)              # video020 --fast (36 % / 88 %): no extra search
    assert not PRT.needs_retry(0.0, 0.49)               # the sound not found either: another episode (night 6's stop)
    assert not PRT.needs_retry(0.0, None)               # no sound to compare


def test_the_shares_of_the_picture_and_the_sound_found():
    assert PRT.matched_share(_fm(368, 6)) == pytest.approx(6 / 368)
    ctx = _ctx(_fm(10, 0), _hints(40, 30))
    assert PRT.sound_share(ctx) == pytest.approx(0.75)
    ctx.hints = SimpleNamespace(comp_t=np.zeros(0))
    assert PRT.sound_share(ctx) is None


def test_the_biggest_moving_area_is_the_largest_part_of_the_picture_that_changes():
    rng = np.random.default_rng(1)
    frames = np.full((40, 128, 72), 40, np.uint8)
    for k in range(40):
        frames[k, 40:100, 8:64] = rng.integers(0, 255, (60, 56))      # the video: changes on every frame
        frames[k, 10:14, 30 + (k % 3):34 + (k % 3)] = 255            # a small blinking sticker
    box = PRT.moving_area(_proxy(frames))
    assert box is not None
    x0, y0, x1, y1 = box
    assert abs(x0 - 8) <= 2 and abs(y0 - 40) <= 2 and abs(x1 - 64) <= 2 and abs(y1 - 100) <= 2
    assert PRT.moving_area(_proxy(np.full((10, 32, 18), 9, np.uint8))) is None   # nothing moves


def test_the_layouts_tried_again_leave_out_the_box_already_used():
    frames = np.full((20, 64, 36), 20, np.uint8)
    rng = np.random.default_rng(2)
    for k in range(20):
        frames[k] = rng.integers(0, 255, (64, 36))                    # every pixel moves: the moving area = the frame
    comp = _proxy(frames)
    tiny = Layout(72, 128, box=Box(25.0, 52.0, 21.0, 15.7))
    ctx = SimpleNamespace(comp_proxy=comp, layout=tiny)
    names = [n for n, _ in PRT.layout_boxes(ctx)]
    assert names == ["the biggest moving area"]                        # the whole frame is the same box: tried once
    ctx.layout = Layout(72, 128, box=Box(0.0, 0.0, 72.0, 128.0))
    assert PRT.layout_boxes(ctx) == []                                 # the whole frame was the first search's box


def test_the_dense_search_indexes_only_where_the_sound_was_found():
    frames = np.arange(50, dtype=np.uint8)[:, None, None] * np.ones((1, 4, 6), np.uint8)
    raw = Proxy("raw", "", frames, (12, 8), (0.5, 0.5), Fraction(24), np.arange(50) / 24.0, 50)
    view = PRT.windowed_proxy(raw, [(5, 10), (30, 33)])
    assert [j for j in range(50) if view.has(j)] == [5, 6, 7, 8, 9, 30, 31, 32]
    assert int(view.get(31)[0, 0]) == 31 and raw.has(0) and not view.has(0)
    ctx = SimpleNamespace(hints=_hints(8, 8, raw0=1.0), cfg=SimpleNamespace(audio_min_conf=1.3),
                          raw_fps=Fraction(24), raw_info=SimpleNamespace(nb_frames=200))
    wins = PRT.sound_windows(ctx, pad_s=0.5)
    assert wins == [(0, 103)]                                          # 1.0-2.75 s +- 1.5 s, merged
    cfg = SimpleNamespace(sift_nfeatures=500, vote_top_candidates=8, vote_min_frac=0.2, audio_restrict_s=2.0,
                          gpu=False, index_max_descriptors=2_000_000, comp_search_stride=3,
                          raw_index_every_frame=False, raw_index_fps_short=10.0, raw_index_fps_long=3.0)
    d = PRT.dense_cfg(cfg)
    assert (d.comp_search_stride, d.sift_nfeatures, d.vote_top_candidates) == (1, 2000, 16)
    assert d.index_max_descriptors >= 4_000_000 and d.raw_index_fps_short >= 100
    assert cfg.comp_search_stride == 3                                 # the run's own settings are left alone


def test_the_best_attempt_is_used_and_the_run_keeps_its_own_settings(monkeypatch):
    fm0 = _fm(100, 0)
    ctx = SimpleNamespace(cfg=SimpleNamespace(audio_min_conf=1.3, name="run"), layout="tiny", overlays="ov0",
                          fm_pre=fm0, raw_proxy="raw", anchors=[], index=None, keys={"frame_map": "k0"},
                          hints=_hints(40, 40), dlog=SimpleNamespace(record=lambda *a, **k: None),
                          raw_fps=Fraction(24), raw_info=SimpleNamespace(nb_frames=10000, duration=400.0))
    shares = {"the biggest moving area": 30, "the whole frame": 70, "comparing lines and shapes (edges)": 40,
              "a denser search with a wider zoom range": 50}
    monkeypatch.setattr(PRT, "layout_boxes", lambda c: [("the biggest moving area", (0, 0, 9, 9)),
                                                        ("the whole frame", (0, 0, 36, 64))])
    monkeypatch.setattr(PRT, "forced_layout", lambda c, name, e: (f"layout:{name}", f"ov:{name}"))
    monkeypatch.setattr(PRT, "dense_cfg", lambda cfg: SimpleNamespace(name="dense"))
    monkeypatch.setattr(PRT, "edge_cfg", lambda cfg: SimpleNamespace(name="run"))
    monkeypatch.setattr(PRT, "windowed_proxy", lambda raw, wins: ("view", tuple(wins)))

    def fake_attempt(c, name, lay, ov, cfg, base_raw, index_raw=None, extra=()):
        st = PRT._state(c)
        st.update(layout=lay, overlays=ov, fm_pre=_fm(100, shares[name]), keys={"frame_map": f"k:{name}"},
                  cfg=SimpleNamespace(name="dense" if index_raw is not None else "run"))
        return {"name": name, "share": shares[name] / 100.0, "state": st}

    monkeypatch.setattr(PRT, "attempt", fake_attempt)
    res = PRT.retry(ctx, "base")
    assert res["used"] == "the whole frame" and res["share"] == pytest.approx(0.7)
    assert [n for n, _ in res["attempts"]] == ["the biggest moving area", "the whole frame",
                                               "comparing lines and shapes (edges)",
                                               "a denser search with a wider zoom range"]
    assert ctx.layout == "layout:the whole frame" and ctx.keys["frame_map"] == "k:the whole frame"
    assert PRT.matched_share(ctx.fm_pre) == pytest.approx(0.7) and ctx.cfg.name == "run"
    shares["the whole frame"] = 85                                     # enough found: no denser search
    ctx.fm_pre, ctx.layout = fm0, "tiny"
    res = PRT.retry(ctx, "base")
    assert [n for n, _ in res["attempts"]] == ["the biggest moving area", "the whole frame"]


def test_nothing_better_found_keeps_the_first_search(monkeypatch):
    fm0 = _fm(100, 3)
    ctx = SimpleNamespace(cfg=SimpleNamespace(audio_min_conf=1.3), layout="tiny", overlays="ov0", fm_pre=fm0,
                          raw_proxy="raw", anchors=[], index=None, keys={"frame_map": "k0"}, hints=_hints(40, 40),
                          dlog=SimpleNamespace(record=lambda *a, **k: None), raw_fps=Fraction(24),
                          raw_info=SimpleNamespace(nb_frames=10000, duration=400.0))
    monkeypatch.setattr(PRT, "layout_boxes", lambda c: [("the whole frame", (0, 0, 36, 64))])
    monkeypatch.setattr(PRT, "forced_layout", lambda c, name, e: ("full", "ovf"))
    monkeypatch.setattr(PRT, "attempt", lambda c, name, *a, **k: {"name": name, "share": 0.01,
                                                                  "state": dict(PRT._state(c), layout="other")})
    res = PRT.retry(ctx, "base")
    assert res["used"] == "the first search" and ctx.layout == "tiny" and ctx.fm_pre is fm0
    assert res["share"] == pytest.approx(0.03)


# ---------------------------------------------------------------------------------------------------------------------
# 2b. PICTURE NOT FOUND
# ---------------------------------------------------------------------------------------------------------------------

def test_still_nothing_found_stops_the_run_with_picture_not_found():
    ctx = _ctx(_fm(368, 6), _hints(100, 100, raw0=1271.0))
    ctx.picture_retry = {"picture": 6 / 368, "sound": 1.0, "share": 0.02, "used": "the whole frame",
                         "attempts": [("the biggest moving area", 0.0), ("the whole frame", 0.02),
                                      ("a denser search with a wider zoom range", 0.01)]}
    with pytest.raises(pipeline.PictureNotFound) as e:
        pipeline.stop_if_picture_not_found(ctx)
    msg = str(e.value)
    assert msg.startswith("PICTURE NOT FOUND (sound found) -- the RAW may be a different version (for example credits "
                          "squeezed at the end), mirrored, or heavily zoomed")
    assert "100 % of the competitor's sound" in msg and "RAW 1271.0-1295.8 s" in msg and "No edit was made" in msg
    assert "the whole frame 2 %" in msg and "mirrored picture" in msg
    assert isinstance(e.value, pipeline.NotInRaw)                     # the CLI's not-in-RAW stop prints it


def test_force_inputs_makes_the_edit_anyway_with_the_message_on_top():
    ctx = _ctx(_fm(100, 0), _hints(40, 40), force=True)
    ctx.picture_retry = {"picture": 0.0, "sound": 1.0, "share": 0.0, "used": "the first search", "attempts": []}
    pipeline.stop_if_picture_not_found(ctx)
    assert ctx.warned and ctx.warned[0].startswith("PICTURE NOT FOUND (sound found)")


def test_some_of_the_picture_found_again_goes_on_with_a_warning():
    ctx = _ctx(_fm(100, 15), _hints(40, 40))
    ctx.picture_retry = {"picture": 0.0, "sound": 1.0, "share": 0.15, "used": "the whole frame", "attempts": []}
    pipeline.stop_if_picture_not_found(ctx)                            # 15 %: an edit, most of it missing -- said
    assert ctx.warned and "only 15 % of the competitor's picture" in ctx.warned[0]
    ctx = _ctx(_fm(100, 90), _hints(40, 40))
    ctx.picture_retry = {"picture": 0.0, "sound": 1.0, "share": 0.9, "used": "the whole frame", "attempts": []}
    pipeline.stop_if_picture_not_found(ctx)                            # found again: said, so the framing is checked
    assert len(ctx.warned) == 1 and "found only after searching it again (the whole frame" in ctx.warned[0]
    ctx = _ctx(_fm(100, 90), _hints(40, 40))
    ctx.picture_retry = {"picture": 0.0, "sound": 1.0, "share": 0.9, "used": "the first search", "attempts": []}
    pipeline.stop_if_picture_not_found(ctx)
    assert ctx.warned == []
    ctx.picture_retry = None                                           # no retry: nothing to say
    pipeline.stop_if_picture_not_found(ctx)


def test_the_batch_says_picture_not_found_in_its_log():
    msg = PRT.NOT_FOUND_HEAD + ". 100 % of the competitor's sound is in this RAW ..."
    stderr = ("12:00:01 INFO match_cuts found in the RAW: 0 % of the competitor's frames, 100 % of its sound\n"
              "12:00:09 ERROR match_cuts STOPPED: " + msg + "\n"
              "match_cuts: " + msg + "\nRun folder: output\\video3\n")
    assert batch.stop_message(stderr).startswith("PICTURE NOT FOUND (sound found) -- the RAW may be a different "
                                                 "version (for example credits squeezed at the end), mirrored, or "
                                                 "heavily zoomed")


# ---------------------------------------------------------------------------------------------------------------------
# 1a. video7: the person the narrower template window cut in half
# ---------------------------------------------------------------------------------------------------------------------

def _narrow_clip(tx: float = -305.6, scale: float = 0.693, band: bool = False):
    """One clip of a competitor whose window (laptop013's crop: 0.693 x, 23-77 % of a 1920 px RAW) is wider than the
    template window: (clip, framing, window, box). ``band``: the competitor shows the whole 16:9 RAW in a band."""
    from match_cuts import export_xml_edl as ex
    from match_cuts.geometry import Sim
    from match_cuts.model import Segment
    win = (22.0, 527.0, 1036.0, 1210.0)
    ty = 437.5 if band else 300.0
    comp = {"scale": scale, "rotation_deg": 0.0, "tx": tx, "ty": ty}
    seg = Segment(id=1, type="raw", comp_in=0, comp_out=300, raw_in_seconds=10.0, speed=1.0, transform=comp,
                  confidence=.9)
    box = Box(0.0, ty, 720.0, 405.0) if band else Box(0.0, 300.0, 720.0, 720.0)
    sim = ex._window_map(Sim.from_dict(comp), box, win)
    ev = SimpleNamespace(seg=seg, rec_in=0, rec_out=300, seg_name="S01", dissolve_in=0)
    clip = ex.PremiereClip(seg, ev, 0, 600, 0, 600, 600, 1200, 1.0, True, 0.0, 1.0, True, [(600, sim)], None, [ev])
    return clip, sim, win, box


def _view(sim, win):
    """The RAW x range the template window shows under ``sim``."""
    return (win[0] - sim.tx) / sim.s, (win[0] + win[2] - sim.tx) / sim.s


def test_the_rule_is_on_and_moves_onto_the_speaker_when_the_window_cut_both_people():
    from match_cuts import export_xml_edl as ex, speakers
    assert ex.SHOW_CUT_PERSON is True and ex.CENTRE_FIXED_CROP is False
    W, H = 1920.0, 1040.0
    clip, sim, win, box = _narrow_clip()
    v0, v1 = _view(sim, win)                                       # what the centred window shows (RAW px)
    man = (v1 - 200.0, 200.0, v1 + 60.0, 640.0)                    # a third of him past its right edge
    woman = (v0 - 160.0, 220.0, v0 + 100.0, 600.0)                 # most of her past its left edge
    sp = SimpleNamespace(same_shot=lambda a, b: True,
                         faces=lambda a, b: speakers.Faces("speaker", True, man, [man], others=[man, woman]))
    assert ex._subject_in_narrow_window([clip], sp, (W, H), win, Fraction(60), box, 250.0) == 0   # night 6: fixed only
    moved = ex._subject_in_narrow_window([clip], sp, (W, H), win, Fraction(60), box, 250.0, cut_only=True)
    assert moved == 1
    new = clip.keys[0][1]
    assert new.s == sim.s and new.tx < sim.tx                       # slid towards him, zoom kept
    assert speakers.shown(speakers.on_screen(new, man, W, False), win)
    n0, n1 = _view(new, win)                                        # never past the competitor's own window (as you:
    cw = ex._comp_window_raw_x(clip, box)                           # video7's starts at the competitor's edge)
    assert cw[0] - 1e-6 <= n0 and n1 <= cw[1] + 1e-6
    assert "centred on the person" in clip.person_note


def test_a_window_that_shows_one_person_whole_keeps_the_competitors_framing():
    # video6: King Julien cut at the window's edge, Mort whole in the middle -- you kept it
    from match_cuts import export_xml_edl as ex, speakers
    W, H = 1920.0, 1040.0
    clip, sim, win, box = _narrow_clip()
    v0, v1 = _view(sim, win)
    cut = (v1 - 150.0, 200.0, v1 + 150.0, 640.0)
    whole = (0.5 * (v0 + v1) - 120.0, 220.0, 0.5 * (v0 + v1) + 120.0, 600.0)
    sp = SimpleNamespace(same_shot=lambda a, b: True,
                         faces=lambda a, b: speakers.Faces("speaker", True, cut, [cut], others=[cut, whole]))
    assert ex._subject_in_narrow_window([clip], sp, (W, H), win, Fraction(60), box, 250.0, cut_only=True) == 0
    assert clip.keys[0][1] is sim


def test_a_wide_shot_of_three_people_and_a_face_outside_the_competitors_window_stay():
    from match_cuts import export_xml_edl as ex, speakers
    W, H = 1920.0, 1040.0
    clip, sim, win, box = _narrow_clip()
    v0, v1 = _view(sim, win)
    a, b, c = ((v1 - 100.0, 200.0, v1 + 100.0, 500.0), (v0 - 100.0, 200.0, v0 + 100.0, 500.0),
               (v1 - 60.0, 520.0, v1 + 60.0, 700.0))
    sp = SimpleNamespace(same_shot=lambda x, y: True,
                         faces=lambda x, y: speakers.Faces("speaker", True, a, [a], others=[a, b, c]))
    assert ex._subject_in_narrow_window([clip], sp, (W, H), win, Fraction(60), box, 250.0, cut_only=True) == 0
    far = (1700.0, 200.0, 1900.0, 640.0)                            # outside the competitor's window: its choice
    sp.faces = lambda x, y: speakers.Faces("speaker", True, far, [far], others=[far])
    assert ex._subject_in_narrow_window([clip], sp, (W, H), win, Fraction(60), box, 250.0, cut_only=True) == 0
    assert clip.keys[0][1] is sim


# ---------------------------------------------------------------------------------------------------------------------
# 1b. video8: a cartoon drawn on twos has no shot change at every new drawing
# ---------------------------------------------------------------------------------------------------------------------

def _thumbs_from_diffs(diffs, rng_seed: int = 3):
    """64 x 36 thumbnails whose mean absolute difference to the frame before follows ``diffs`` (one picture held
    where the difference is 0): each change adds +-d to every pixel."""
    from match_cuts import shots
    rng = np.random.default_rng(rng_seed)
    f = np.full((shots.THUMB[1], shots.THUMB[0]), 120.0)
    out = [f.copy()]
    for d in diffs:
        if d > 0:
            f = np.clip(f + rng.choice([-1.0, 1.0], size=f.shape) * d, 0, 255)
        out.append(f.copy())
    return np.stack(out).astype(np.uint8)


def test_new_drawings_of_a_cartoon_on_twos_are_no_shot_changes_but_its_real_cut_is():
    from match_cuts import shots
    assert shots.VERSION == 3                          # the cache of every RAW's shot changes is made again
    twos = [0, 12, 0, 22, 0, 9, 0, 26, 0, 21, 0, 22, 0, 12, 0, 5, 0, 6, 0]    # video8's numbers: a drawing every 2nd frame
    lead = [0, 6, 0, 7, 0, 5, 0, 8, 0, 6, 0, 7]                               # the drawings before (the same shot)
    th = _thumbs_from_diffs(lead + twos + lead)
    assert shots.changes_of(th) == []                  # night 7 found a shot change at nearly every drawing
    with_cut = [0, 13, 0, 12, 2, 20, 8, 42, 0, 0, 1, 2, 1, 5, 0]               # 29.53 s: the PETROL can cut
    k = len(lead) + 8                                  # the frame after the 42
    assert shots.changes_of(_thumbs_from_diffs(lead + with_cut + lead)) == [k]


def test_a_cut_from_a_still_shot_to_a_moving_one_is_still_found():
    from match_cuts import shots
    # video021 at 87.25 s: a still shot (0.4) cut to a shot with big motion (9-22): only one side moves
    seq = [0.4, 0.4, 0.4, 0.4, 0.5, 0.7, 63.0, 9.0, 9.4, 14.9, 21.8, 21.9, 22.0, 20.0]
    th = _thumbs_from_diffs([0] + seq)
    got = shots.changes_of(th)
    assert 8 in got and len(got) == 1


# ---------------------------------------------------------------------------------------------------------------------
# 1c. the framing score of check-all
# ---------------------------------------------------------------------------------------------------------------------

def test_the_framing_score_counts_your_clips_framed_the_same():
    from match_cuts import framing_score as FS
    from match_cuts.learn import Clip
    key = [{"src_in": 0.0, "src_out": 2.0, "dx": 267.2, "scale": 113.0},       # video7 U1: you moved onto the man
           {"src_in": 2.0, "src_out": 3.0, "dx": 270.5, "scale": 112.5},
           {"src_in": 3.0, "src_out": 5.0, "dx": None, "scale": None}]         # keyframed (a pan): not compared
    tool = [Clip(0.0, 2.0, 0.0, 2.0, 0.2, 112.4), Clip(2.0, 3.0, 2.0, 3.0, 255.0, 112.5),
            Clip(3.0, 5.0, 3.0, 5.0, 0.0, 112.5)]
    sc = FS.score(key, tool)
    assert (sc["clips"], sc["same"]) == (2, 1) and sc["moved"] == [-267.0]
    assert FS.line(sc) == "1/2 (50 %)"


def test_learn_writes_your_framing_into_the_answer_key():
    from match_cuts.learn import Clip, framing_rows
    rows = framing_rows([Clip(0.0, 1.0, 300.0, 301.0, 267.22, 113.0), Clip(1.0, 2.0, 302.0, 303.5, None, None)],
                        offset=261.9)
    assert rows[0] == {"src_in": 38.1, "src_out": 39.1, "dx": 267.22, "scale": 113.0}
    assert rows[1]["dx"] is None and rows[1]["src_out"] == pytest.approx(41.6)


def test_check_all_shows_the_framing_column():
    from match_cuts import check_all
    row = {"case": "video7-fixed", "seconds": 60.0, "deliverables": {"status": "pass"},
           "determinism": {"status": "pass"}, "coverage": {"status": "pass"},
           "framing_score": {"clips": 10, "same": 6, "same_pct": 60.0}}
    card = check_all.scorecard([row], {"rows": [dict(row, framing_score={"clips": 10, "same": 3, "same_pct": 30.0})]})
    assert "framing" in card.splitlines()[0] and "6/10 (60 %) +3" in card and "all framing keys" in card


# ---------------------------------------------------------------------------------------------------------------------
# 2c. end to end on real footage (made-up competitors of video7's RAW window; GPU, slow)
# ---------------------------------------------------------------------------------------------------------------------

def _made_up(tmp_path, vf: str, picture_from=None):
    import subprocess
    from match_cuts.common import ffmpeg_bin
    from match_cuts.testcases import CASES_DIR
    raw = CASES_DIR / "video7-fixed" / "raw.mp4"
    comp = tmp_path / "competitor.mp4"
    src = picture_from or raw
    cmd = [ffmpeg_bin(), "-y", "-v", "error", "-ss", "20" if picture_from else "40", "-t", "6", "-i", str(src),
           "-ss", "40", "-t", "6", "-i", str(raw), "-filter_complex", f"[0:v]{vf}[v]", "-map", "[v]", "-map", "1:a",
           "-c:v", "libx264", "-preset", "veryfast", "-crf", "18", "-pix_fmt", "yuv420p", "-r", "30",
           "-c:a", "aac", "-ar", "48000", str(comp)]
    subprocess.run(cmd, check=True)
    return comp, raw


@pytest.mark.slow
def test_a_tiny_wrong_layout_box_is_found_again_and_the_edit_is_normal(tmp_path, monkeypatch):
    from match_cuts import cli, layout
    comp, raw = _made_up(tmp_path, "scale=720:-2,pad=720:1280:0:(1280-ih)/2:black,setsar=1")
    orig = layout.analyze_layout

    def tiny(comp_, cfg, cache, debug_dir, dlog):                   # the laptop's run: a 211 x 157 box, here on the
        lay, ov = orig(comp_, cfg, cache, debug_dir, dlog)          # black canvas below the picture
        lay.box = Box(250.0, 1000.0, 211.0, 157.0, 0.0)
        for p in lay.periods:
            p.box = lay.box
        return lay, ov

    monkeypatch.setattr(layout, "analyze_layout", tiny)
    code = cli.main(["--competitor", str(comp), "--raw", str(raw), "--out", str(tmp_path / "out"), "--work",
                     str(tmp_path / "work"), "--premiere", "--fast", "--no-frame"])
    assert code in (0, 1)
    run = sorted((tmp_path / "out").glob("0*"))[-1]
    log = (run / "extras" / "match_cuts.log").read_text(encoding="utf-8")
    assert "picture search again: used" in log and (run / "1_edit.xml").is_file()
    assert "<clipitem" in (run / "1_edit.xml").read_text(encoding="utf-8")


@pytest.mark.slow
def test_the_sound_of_the_raw_under_another_films_picture_stops_with_picture_not_found(tmp_path, capsys):
    from match_cuts import cli
    from match_cuts.testcases import CASES_DIR
    comp, raw = _made_up(tmp_path, "scale=720:-2,pad=720:1280:0:(1280-ih)/2:black,setsar=1",
                         picture_from=CASES_DIR / "video9-fixed" / "raw.mp4")
    code = cli.main(["--competitor", str(comp), "--raw", str(raw), "--out", str(tmp_path / "out"), "--work",
                     str(tmp_path / "work"), "--premiere", "--fast", "--no-frame"])
    assert code == 2
    err = capsys.readouterr().err
    assert "PICTURE NOT FOUND (sound found) -- the RAW may be a different version" in err
    run = sorted((tmp_path / "out").glob("0*"))[-1]
    assert not (run / "1_edit.xml").exists()                     # no empty edit delivered as a normal one


# ---------------------------------------------------------------------------------------------------------------------
# 2d. an empty edit is never "OK"
# ---------------------------------------------------------------------------------------------------------------------

def _xml_with(v1_items: str) -> str:
    return ("<xmeml version=\"4\"><sequence><rate><timebase>24</timebase><ntsc>TRUE</ntsc></rate><media><video>"
            f"<track>{v1_items}</track><track><clipitem id=\"frame\"><name>frame.png</name><start>0</start>"
            "<end>48</end><in>0</in><out>48</out></clipitem></track></video><audio><track/></audio></media>"
            "</sequence></xmeml>")


def test_an_edit_with_no_clip_fails_xml_empty(tmp_path):
    from match_cuts import export_xml_edl as ex
    empty = tmp_path / "empty.xml"
    empty.write_text(_xml_with(""), encoding="utf-8")                # the laptop's run: V1 empty, only the frame
    probs = ex.premiere_empty_problems(empty)
    assert probs and "an empty edit" in probs[0]
    full = tmp_path / "full.xml"
    full.write_text(_xml_with("<clipitem id=\"c1\"><name>raw.mp4</name><start>0</start><end>48</end><in>10</in>"
                              "<out>58</out></clipitem>"), encoding="utf-8")
    assert ex.premiere_empty_problems(full) == []


def test_the_batch_says_fail_for_an_empty_edit_not_ok_with_a_note(monkeypatch, tmp_path):
    from match_cuts import check_all
    empty = ("XML/EDL re-parse validation did not pass: ['XML EMPTY V1 holds no clip: the edit shows nothing of the "
             "RAW (an empty edit is never a normal one -- the picture was not found)']")
    row = {"deliverables": {"status": "fail", "summary": "9/9 deliverables present, exports NOT validated",
                            "failures": [empty]}, "coverage": {"status": "pass", "summary": ""}}
    monkeypatch.setattr(check_all, "read_run", lambda case, run_dir, scored=True: row)
    c = batch.checks_of(tmp_path)
    assert not c["pass"] and not c["notes"] and batch.verdict(c) == "FAIL"


def test_a_competitor_showing_everything_through_one_crop_keeps_the_middle():
    # video6: the competitor's framing moves 1.8 % / 8 px over the video (its measuring noise): it chose nobody shot by
    # shot, and you kept the middle on all 9 clips -- even where Mort's face was cut at the window's edge
    from match_cuts import export_xml_edl as ex, speakers
    from match_cuts.geometry import Sim
    from match_cuts.model import Segment
    W, H = 1920.0, 1040.0
    clip, sim, win, box = _narrow_clip()
    comp2 = {"scale": 0.693 * 1.018, "rotation_deg": 0.0, "tx": -305.6 + 8.0, "ty": 300.0}
    seg2 = Segment(id=2, type="raw", comp_in=300, comp_out=600, raw_in_seconds=50.0, speed=1.0, transform=comp2,
                   confidence=.9)
    sim2 = ex._window_map(Sim.from_dict(comp2), box, win)
    ev2 = SimpleNamespace(seg=seg2, rec_in=300, rec_out=600, seg_name="S02", dissolve_in=0)
    other = ex.PremiereClip(seg2, ev2, 600, 1200, 600, 1200, 3000, 3600, 1.0, True, 0.0, 1.0, True, [(3000, sim2)],
                            None, [ev2])
    v0, v1 = _view(sim, win)
    man = (v1 - 200.0, 200.0, v1 + 60.0, 640.0)
    sp = SimpleNamespace(same_shot=lambda a, b: abs(a - b) < 5.0,
                         faces=lambda a, b: speakers.Faces("speaker", True, man, [man], others=[man]))
    assert not ex._fixed_competitor_crop([clip, other])               # not night 6's fixed crop ...
    assert ex._fixed_competitor_crop([clip, other], ex.ONE_CROP_SCALE, ex.ONE_CROP_PX)   # ... but one crop
    assert ex._subject_in_narrow_window([clip, other], sp, (W, H), win, Fraction(60), box, 250.0, cut_only=True) == 0
    other.seg.transform = {**comp2, "tx": -305.6 + 190.0}             # video7: the competitor moves 190 px
    assert ex._subject_in_narrow_window([clip, other], sp, (W, H), win, Fraction(60), box, 250.0, cut_only=True) >= 1


def test_the_window_slides_onto_a_person_at_its_edge_but_never_jumps_far():
    # video021 S54 / S56: the rule would have moved the window 54 % / 45 % of its width onto a face far from the
    # middle -- another part of the competitor's picture, not the person at the edge (video7: 28 %)
    from match_cuts import export_xml_edl as ex, speakers
    from match_cuts.geometry import Sim
    W, H = 1920.0, 1040.0
    clip, sim, win, box = _narrow_clip(tx=0.0, scale=0.375, band=True)   # the RAW's whole width in a band
    s = win[3] / H                                                  # the window covered: the RAW's height fills it
    sim = Sim(s, 0.0, win[0] + win[2] / 2.0 - s * W / 2.0, win[1])
    clip.keys = [(clip.keys[0][0], sim)]
    v0, v1 = _view(sim, win)
    cw = ex._comp_window_raw_x(clip, box)
    assert cw[1] - cw[0] > 1.8 * (v1 - v0)
    far = (cw[1] - 230.0, 200.0, cw[1] - 10.0, 500.0)               # a face at the far edge of the competitor's picture
    sp = SimpleNamespace(same_shot=lambda a, b: True,
                         faces=lambda a, b: speakers.Faces("speaker", True, far, [far], others=[far]))
    assert ex._subject_in_narrow_window([clip], sp, (W, H), win, Fraction(60), box, 250.0, cut_only=True) == 0
    assert clip.keys[0][1] is sim
    near = (v1 - 120.0, 200.0, v1 + 100.0, 500.0)                   # one cut at the window's own edge: it slides
    sp.faces = lambda a, b: speakers.Faces("speaker", True, near, [near], others=[near])
    assert ex._subject_in_narrow_window([clip], sp, (W, H), win, Fraction(60), box, 250.0, cut_only=True) == 1
    new_ = clip.keys[0][1]
    slide = sim.tx - new_.tx                                        # just enough to show the face whole, with a margin
    assert 0 < slide < 0.35 * win[2] and speakers.shown(speakers.on_screen(new_, near, W, False), win)
    assert new_.s * near[2] + new_.tx == pytest.approx(win[0] + win[2] - ex.CUT_MARGIN * (near[2] - near[0]) * new_.s)
