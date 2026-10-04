"""Task 5: the thorough default and --fast -- the two profiles, the run without a GPU, what the thoroughness changed
against --fast (compare_cutlists) and the end summary's run time / thoroughness lines."""
from __future__ import annotations

from fractions import Fraction
from types import SimpleNamespace

from match_cuts import cli, gpu, pipeline
from match_cuts.config import Config
from match_cuts.model import Segment

F30 = Fraction(30)


def test_the_default_is_thorough_and_fast_is_the_quick_profile():
    c = Config()
    assert (c.fast, c.comp_search_stride, c.raw_index_every_frame, c.full_res, c.compare_fast) == (False, 1, True,
                                                                                                    True, True)
    f = c.fast_twin()
    assert (f.fast, f.comp_search_stride, f.raw_index_every_frame, f.full_res, f.compare_fast) == (True, 3, False,
                                                                                                    False, False)
    assert f.index_max_descriptors == 2_000_000 and f.speech_map_model == "large-v3-turbo"
    assert not c.fast and c.comp_search_stride == 1                     # the twin is a copy
    assert c.analysis_params() != f.analysis_params()                   # their caches never mix
    fast = cli.config_from_args(cli.build_parser().parse_args(["--fast"]), "c.mp4", "r.mp4")
    assert fast.fast and fast.comp_search_stride == 3 and not fast.full_res


def test_without_a_gpu_the_index_samples_the_raw(monkeypatch):
    monkeypatch.setattr(gpu, "available", lambda: "no CUDA device")
    c = Config()
    notes = pipeline.resolve_gpu(c)
    assert not c.gpu and not c.raw_index_every_frame and c.index_max_descriptors == 2_000_000
    assert any("no GPU" in n for n in notes) and any("samples" in n for n in notes)
    f = Config().apply_fast()
    assert pipeline.resolve_gpu(f) == [] or all("samples" not in n for n in pipeline.resolve_gpu(f))


def seg(i, comp_in, comp_out, raw_in_frame=None, kind="raw"):
    return Segment(i, kind, comp_in, comp_out, raw_in_frame=raw_in_frame,
                   raw_in_seconds=None if raw_in_frame is None else raw_in_frame / 30.0, speed=1.0)


def test_the_comparison_lists_what_the_thoroughness_changed():
    fast = [seg(0, 0, 12, 100), seg(1, 12, 30, 300), seg(2, 30, 40, kind="uncertain")]
    thorough = [seg(0, 0, 10, 100), seg(1, 10, 30, 299), seg(2, 30, 34, 500), seg(3, 34, 40, kind="not_in_raw")]
    r = pipeline.compare_cutlists(thorough, fast, 40, F30, F30, 10_000)
    assert (r["cuts_thorough"], r["cuts_fast"]) == (3, 2)
    assert r["cuts_moved"] == 1 and r["examples"]["moved"] == [(12, 10)]          # 12 -> 10
    assert r["cuts_added"] == 1 and r["examples"]["added"] == [34]
    assert r["frames_other_raw"] == 20 and r["frames_other_raw_by_one"] == 18      # 10-11 jump, 12-29 by one frame
    assert r["frames_other_kind"] == 10 and (r["uncertain_fast"], r["uncertain_thorough"]) == (1, 0)
    assert "1 cut(s) placed differently (by -2 frame(s))" in r["summary"]
    assert pipeline.compare_cutlists(fast, fast, 40, F30, F30, 10_000)["summary"] == "the same cut list"


def test_the_end_summary_says_the_run_time_and_what_thoroughness_changed(monkeypatch):
    monkeypatch.setattr(gpu, "device_name", lambda: "TEST GPU")
    cfg = Config()
    ctx = SimpleNamespace(cfg=cfg, full_res={"recheck": {"frames": 40, "narrowed": 12, "decided": 9, "kept": 27,
                                                         "outside": 1},
                                             "verify": {"summary": "700 frames and 20 cuts at full resolution"}},
                          fast_compare={"summary": "2 cut(s) placed differently (by +1 frame(s))", "fast_seconds": 250,
                                        "thorough_seconds": 731})
    lines = cli.quality_lines({"context": ctx, "timings": {"total": 1260.0}})
    assert lines[0] == "Run time: 21m00s (thorough, the default; the GPU: TEST GPU)"
    assert "40: 12 narrowed (9 decided), 0 decided against the proxy (full resolution clearly sure), 27 still within noise, 1 left to the proxy" in lines[1]
    assert lines[2].endswith("700 frames and 20 cuts at full resolution")
    assert lines[3] == ("  Against --fast: 2 cut(s) placed differently (by +1 frame(s)); the --fast analysis of this "
                        "video takes 4m10s, the thorough one took 12m11s")
    fast = cli.quality_lines({"context": SimpleNamespace(cfg=Config().apply_fast()), "timings": {"total": 59.4}})
    assert fast == ["Run time: 59s (--fast; the GPU: TEST GPU)"]


def test_only_the_repeat_cadence_explains_a_better_neighbouring_raw_frame():
    """Task 5 (s9_9): a neighbouring RAW frame fitting better at full resolution is explained only where one
    constant-speed clip cannot follow the competitor's repeat cadence: the competitor shows one picture on two
    frames where the time line steps (the better frame is the one shown on the other), or the time line shows one
    RAW frame twice where the competitor moves on (the better frame is the next one in the direction of play).
    Anything else -- a wrong time model -- is reported."""
    import numpy as np
    from match_cuts.fullres import cadence
    pl = np.array([2, 1, 2, 2, 2, 2])                     # competitor frames 1 and 2: one picture
    shown = {0: (10, 0.0, 1.0), 1: (11, 0.0, 1.0), 2: (12, 0.0, 1.0), 3: (12, 0.0, 1.0), 4: (13, 0.0, 1.0)}
    assert "the time line steps between them" in cadence(2, 12, 11, shown, pl)
    assert "where the competitor moves on" in cadence(3, 12, 13, shown, pl)
    assert cadence(3, 12, 11, shown, pl) == ""             # the other direction: not the cadence
    assert cadence(4, 13, 14, shown, pl) == ""             # no repeat on either side
    back = {k: (j, f, -1.0) for k, (j, f, _v) in shown.items()}
    assert "moves on" in cadence(3, 12, 11, back, pl)       # playing backwards: the next frame is the previous one
    blend = {**shown, 2: (12, 0.5, 1.0)}
    assert cadence(2, 12, 11, blend, pl) == ""             # a blended frame is no repeat


def test_the_frames_around_a_raw_jump_are_rechecked_at_full_resolution():
    """The first frames after a jump cut in a fast pan are blurred: the proxy's exact answer is least sure there
    (Deadpool 447: the proxy's RAW 2749, at full resolution 2750 by 0.035)."""
    import numpy as np
    from match_cuts import fullres
    from match_cuts.model import FrameMap, Status
    raw = [10, 11, 12, 13, 40, 41, 42, 43, 44, 45, 45, 46]
    fm = FrameMap(len(raw))
    fm.status = np.full(len(raw), int(Status.MATCH), np.int8)
    fm.raw = fm.raw_lo = fm.raw_hi = fm.soft_lo = fm.soft_hi = np.array(raw, np.int32)
    assert fullres.uncertain_frames(fm, int(Status.MATCH)) == [2, 3, 4, 5]     # around 13 -> 40 only (45 45 is a repeat)
