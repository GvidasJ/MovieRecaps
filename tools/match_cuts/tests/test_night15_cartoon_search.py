"""Night 15, task 3 fixes 1-4 (cartoons; the laptop's American Dad case: 2 hours of retries, then PICTURE NOT FOUND):
the picture search again also compares lines and shapes (edges), then trusts the sound (lower picture scores kept only
where they agree with it), and stops trying after RETRY_BUDGET_S. Only the retry path changed: a run whose first search
finds the picture (every check-all case) never gets here."""
from __future__ import annotations

from fractions import Fraction
from types import SimpleNamespace

import numpy as np
import pytest

from match_cuts import picture_retry as PRT, pipeline
from match_cuts.config import Config
from match_cuts.model import FrameMap, Status


def _hints(n: int, offset: float, hop: float = 0.25) -> SimpleNamespace:
    """n sound windows every ``hop`` s, all found in the RAW at comp time + ``offset``."""
    ct = hop * np.arange(n)
    rt = ct + offset
    conf = np.full(n, 3.0)
    return SimpleNamespace(comp_t=ct, raw_t=rt, conf=conf, window=1.0,
                           confident=lambda c, conf=conf, rt=rt: np.isfinite(rt) & (conf >= c))


def _ctx(hints, comp_fps=24, raw_fps=24) -> SimpleNamespace:
    return SimpleNamespace(cfg=SimpleNamespace(audio_min_conf=1.3), hints=hints, comp_fps=Fraction(comp_fps),
                           raw_fps=Fraction(raw_fps))


def test_the_edge_and_trust_settings_change_only_what_they_say():
    cfg = Config()
    e = PRT.edge_cfg(cfg)
    assert e.grad_weight == PRT.EDGE_WEIGHT and cfg.grad_weight == 0.0           # the run's own settings untouched
    assert e.match_thresh == cfg.match_thresh and e.sift_nfeatures == cfg.sift_nfeatures
    t = PRT.trust_cfg(cfg)
    assert t.match_thresh == PRT.TRUST_MIN and t.anchor_zncc_slack == 0.0
    assert t.none_thresh <= PRT.TRUST_MIN - 0.05 and t.none_thresh <= cfg.none_thresh
    assert t.sift_nfeatures == PRT.dense_cfg(cfg).sift_nfeatures                 # the dense search's, plus the scores
    assert cfg.match_thresh == 0.90


def test_the_sound_puts_each_competitor_frame_at_a_raw_moment():
    ctx = _ctx(_hints(40, offset=300.0))                                          # 10 s of sound, 300 s into the RAW
    want = PRT.sound_raw_times(ctx, 24 * 15)
    assert want[0] == pytest.approx(300.0) and want[24 * 5] == pytest.approx(305.0)
    assert np.isnan(want[24 * 14])                                                # 4 s past the last window: none


def test_only_matches_that_agree_with_the_sound_in_order_and_timing_are_kept():
    n = 120
    ctx = _ctx(_hints(n // 6 + 4, offset=300.0))
    fm = FrameMap(n)
    st = np.full(n, Status.MATCH)
    raw = 300.0 * 24 + np.arange(n, dtype=np.float64)                             # right where the sound says
    raw[40:44] = 10.0                                                             # 4 frames somewhere else: dropped
    raw[60:63] = raw[60:63] + 24 * 3                                              # 3 s off: dropped (and leave
    st[80] = Status.NONE                                                          #   a frame not found)
    raw[90:93] = 300.0 * 24 + 90 + np.array([0, -30, 0])                         # goes backwards: run broken
    fm.status, fm.raw = st, raw
    kept = PRT.trust_filter(ctx, fm)
    out = np.asarray(fm.status)
    assert out[0:40].tolist() == [Status.MATCH] * 40
    assert (out[40:44] == Status.UNRESOLVED).all() and (out[60:63] == Status.UNRESOLVED).all()
    assert out[80] == Status.NONE                                                  # not a match: left as it was
    assert out[44] == Status.MATCH and out[59] == Status.MATCH
    assert out[91] == Status.UNRESOLVED and out[90] == Status.MATCH                # the backward frame alone
    assert kept == int((out == Status.MATCH).sum()) == n - 4 - 3 - 1 - 1


def test_short_runs_are_not_trusted():
    ctx = _ctx(_hints(20, offset=50.0))
    fm = FrameMap(30)
    st = np.full(30, Status.NONE)
    st[3:3 + PRT.TRUST_RUN - 1] = Status.MATCH                                    # one frame short of a run
    fm.status, fm.raw = st, 50.0 * 24 + np.arange(30, dtype=np.float64)
    assert PRT.trust_filter(ctx, fm) == 0 and not (np.asarray(fm.status) == Status.MATCH).any()


def _retry_ctx():
    fm0 = FrameMap(100)
    fm0.status = np.full(100, Status.NONE)
    return SimpleNamespace(cfg=SimpleNamespace(audio_min_conf=1.3, name="run"), layout="tiny", overlays="ov0",
                           fm_pre=fm0, raw_proxy="raw", anchors=[], index=None, keys={"frame_map": "k0"},
                           hints=_hints(40, 100.0), dlog=SimpleNamespace(record=lambda *a, **k: None),
                           raw_fps=Fraction(24), comp_fps=Fraction(24),
                           raw_info=SimpleNamespace(nb_frames=10000, duration=400.0))


def _fake(monkeypatch, shares, calls):
    monkeypatch.setattr(PRT, "layout_boxes", lambda c: [("the whole frame", (0, 0, 36, 64))])
    monkeypatch.setattr(PRT, "forced_layout", lambda c, name, e: (f"layout:{name}", f"ov:{name}"))
    for f in ("dense_cfg", "edge_cfg", "trust_cfg"):
        monkeypatch.setattr(PRT, f, lambda cfg, f=f: SimpleNamespace(name=f))
    monkeypatch.setattr(PRT, "windowed_proxy", lambda raw, wins: "view")
    monkeypatch.setattr(PRT, "trust_filter", lambda c, fm: int(shares["kept"]))

    def fake_attempt(c, name, lay, ov, cfg, base_raw, index_raw=None, extra=()):
        calls.append((name, cfg.name if hasattr(cfg, "name") else None))
        fm = FrameMap(100)
        st = np.full(100, Status.NONE)
        st[: shares[name]] = Status.MATCH
        fm.status = st
        state = PRT._state(c)
        state.update(layout=lay, overlays=ov, fm_pre=fm, keys={"frame_map": f"k:{name}"})
        return {"name": name, "share": shares[name] / 100.0, "state": state}

    monkeypatch.setattr(PRT, "attempt", fake_attempt)


TRUST = "trusting the sound (lower picture scores where the sound found it)"
EDGES = "comparing lines and shapes (edges)"
DENSE = "a denser search with a wider zoom range"


def test_a_cartoon_found_by_its_edges_needs_no_more(monkeypatch):
    shares, calls = {"the whole frame": 2, EDGES: 92, DENSE: 50, TRUST: 60, "kept": 60}, []
    _fake(monkeypatch, shares, calls)
    res = PRT.retry(_retry_ctx(), "base")
    assert res["used"] == EDGES and res["share"] == pytest.approx(0.92) and not res["trusted"]
    assert [n for n, _ in calls] == ["the whole frame", EDGES]                    # enough: no dense, no trust
    assert ("comparing lines and shapes (edges)", "edge_cfg") in calls


def test_the_sound_is_trusted_last_and_only_its_agreeing_frames_count(monkeypatch):
    monkeypatch.setattr(PRT, "TRUST_SOUND", True)                                 # (off by default: see below)
    shares, calls = {"the whole frame": 0, EDGES: 3, DENSE: 5, TRUST: 70, "kept": 64}, []
    _fake(monkeypatch, shares, calls)
    ctx = _retry_ctx()
    res = PRT.retry(ctx, "base")
    assert [n for n, _ in calls] == ["the whole frame", EDGES, DENSE, TRUST]
    assert res["used"] == TRUST and res["trusted"] and res["skipped"] == []
    assert res["seconds"] >= 0.0
    assert ctx.keys["frame_map"] == f"k:{TRUST}"
    shares[DENSE], calls[:] = 30, []                                              # the dense search found enough
    res = PRT.retry(_retry_ctx(), "base")                                         # (over a quarter): no trusting
    assert [n for n, _ in calls] == ["the whole frame", EDGES, DENSE] and res["used"] == DENSE


def test_retries_stop_after_the_time_budget(monkeypatch):
    shares, calls = {"the whole frame": 0, EDGES: 0, DENSE: 0, TRUST: 0, "kept": 0}, []
    _fake(monkeypatch, shares, calls)
    monkeypatch.setattr(PRT, "RETRY_BUDGET_S", 0.0)
    monkeypatch.setattr(PRT, "TRUST_SOUND", True)
    res = PRT.retry(_retry_ctx(), "base")
    assert calls == [] and res["used"] == "the first search"
    assert res["skipped"] == ["the whole frame", EDGES, DENSE, TRUST]


def test_a_trusted_picture_is_said_so_the_cuts_are_checked():
    warned: list[str] = []
    ctx = SimpleNamespace(cfg=SimpleNamespace(force_inputs=False), warn=lambda m, analysis=False: warned.append(m))
    ctx.picture_retry = {"picture": 0.0, "sound": 1.0, "share": 0.64, "used": TRUST, "trusted": True, "attempts": []}
    pipeline.stop_if_picture_not_found(ctx)
    assert len(warned) == 1 and "lower picture scores where the sound agreed" in warned[0]
    assert "64 %" in warned[0] and "check every cut" in warned[0]


def test_trusting_the_sound_is_off_by_default(monkeypatch):
    """Measured on American Dad: the frames it keeps are near, not on, the right RAW frame -- not a normal edit."""
    assert PRT.TRUST_SOUND is False
    shares, calls = {"the whole frame": 0, EDGES: 3, DENSE: 5, TRUST: 70, "kept": 64}, []
    _fake(monkeypatch, shares, calls)
    res = PRT.retry(_retry_ctx(), "base")
    assert [n for n, _ in calls] == ["the whole frame", EDGES, DENSE] and not res["trusted"]
