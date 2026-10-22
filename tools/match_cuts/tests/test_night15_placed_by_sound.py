"""Night 15, task 3 step 5 -- never nothing: a PICTURE NOT FOUND run (the sound found, the picture not) still writes
1_edit_placed_by_sound.xml, the RAW placed where its sound is, marked "PLACED BY SOUND -- check the framing"."""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from match_cuts import pipeline
from match_cuts import placed_by_sound as PB


def _hints(pieces, hop=0.25):
    """Sound windows: [(from s, to s, offset)] -> comp_t, raw_t, ok."""
    ct, rt = [], []
    for a, b, d in pieces:
        for t in np.arange(a + 0.5, b - 0.5 + 1e-9, hop):
            ct.append(t)
            rt.append(t + d)
    return np.asarray(ct), np.asarray(rt), np.ones(len(ct), bool)


def test_runs_group_windows_of_one_offset_and_drop_lone_ones():
    ct, rt, ok = _hints([(0, 4, 292.2), (4, 6, 307.9), (6, 19, 309.1)])
    ct, rt, ok = np.append(ct, 30.0), np.append(rt, 900.0), np.append(ok, True)     # a lone window: noise
    rs = PB.runs(ct, rt, ok)
    assert [round(r["offset"], 1) for r in rs] == [292.2, 307.9, 309.1]
    assert rs[0]["from"] == pytest.approx(0.0) and rs[-1]["to"] == pytest.approx(19.0)


def test_runs_skip_windows_not_at_the_normal_speed_and_not_found():
    ct, rt, ok = _hints([(0, 4, 10.0)])
    sp = np.full(len(ct), 1.25)
    assert PB.runs(ct, rt, ok, sp) == []
    rt2 = rt.copy()
    rt2[:] = np.nan
    assert PB.runs(ct, rt2, ok) == []


def test_spans_tile_the_competitor_and_stay_inside_the_raw():
    rs = [{"from": 0.5, "to": 4.0, "offset": 100.0}, {"from": 4.5, "to": 10.0, "offset": 200.0}]
    sp = PB.spans(rs, 12.0, 1000.0)
    assert [(s["from"], s["to"]) for s in sp] == [(0.0, 4.25), (4.25, 12.0)]
    near_start = PB.spans([{"from": 1.0, "to": 5.0, "offset": -0.5}], 5.0, 100.0)    # RAW would start before 0
    assert near_start[0]["from"] == pytest.approx(0.5)
    far = PB.spans([{"from": 10.0, "to": 12.0, "offset": 0.0}], 20.0, 100.0)          # reaches only MAX_REACH_S
    assert (far[0]["from"], far[0]["to"]) == (10.0 - PB.MAX_REACH_S, 12.0 + PB.MAX_REACH_S)


def test_the_cutlist_places_the_raw_and_marks_the_gaps():
    raw = {"file": "raw.mp4", "width": 1920, "height": 1080, "fps": "24000/1001", "frames": 30000}
    sp = [{"from": 1.0, "to": 4.0, "offset": 100.0}, {"from": 4.0, "to": 8.0, "offset": 200.0}]
    cl = PB.build_cutlist(raw, 10.0, sp, (20.0, 525.0, 1040.0, 1214.0), (1080, 1920))
    kinds = [(s.type, s.comp_in, s.comp_out) for s in cl.segments]
    assert kinds == [("not_in_raw", 0, 60), ("raw", 60, 240), ("raw", 240, 480), ("not_in_raw", 480, 600)]
    assert cl.segments[1].raw_in_seconds == pytest.approx(101.0) and cl.segments[2].raw_in_seconds == pytest.approx(204.0)
    assert all(s.label == "placed by sound" for s in cl.segments if s.type == "raw")
    t = cl.segments[1].transform                                       # the RAW covers the window, centred
    assert t["scale"] * 1080 >= 1214.0 - 1e-6 and t["scale"] * 1920 >= 1040.0 - 1e-6


def test_add_marker_goes_into_the_sequence(tmp_path):
    x = tmp_path / "a.xml"
    x.write_text('<?xml version="1.0" encoding="UTF-8"?>\n<!DOCTYPE xmeml>\n<xmeml><sequence><name>s</name>'
                 "</sequence></xmeml>\n", encoding="utf-8")
    PB.add_marker(x, PB.MARKER, "a & b")
    import xml.etree.ElementTree as ET
    mk = ET.parse(x).getroot().find("sequence/marker")
    assert mk.findtext("name") == "PLACED BY SOUND -- check the framing" and mk.findtext("comment") == "a & b"


def _stop_ctx(**cfg):
    raw_t = 100.0 + 0.25 * np.arange(40)
    conf = np.full(40, 3.0)
    hints = SimpleNamespace(comp_t=0.25 * np.arange(40), raw_t=raw_t, conf=conf, window=1.0,
                            confident=lambda c: np.isfinite(raw_t) & (conf >= c))
    warned: list[str] = []
    ctx = SimpleNamespace(cfg=SimpleNamespace(force_inputs=cfg.pop("force", False), audio_min_conf=1.3,
                                              competitor="comp.mp4", raw="raw.mp4", **cfg),
                          hints=hints, raw_info=SimpleNamespace(duration=1300.0, nb_frames=31000),
                          dlog=SimpleNamespace(record=lambda *a, **k: None), warned=warned,
                          warn=lambda m, analysis=False: warned.append(m))
    ctx.picture_retry = {"picture": 0.0, "sound": 1.0, "share": 0.0, "used": "the first search", "attempts": []}
    return ctx


def test_picture_not_found_still_stops_but_names_the_placed_by_sound_project(monkeypatch):
    calls = []
    monkeypatch.setattr(PB, "write", lambda ctx: calls.append(1) or {"path": "x/1_edit_placed_by_sound.xml",
                                                                        "covered": 0.96})
    with pytest.raises(pipeline.PictureNotFound) as e:
        pipeline.stop_if_picture_not_found(_stop_ctx(premiere=True))
    assert calls and "1_edit_placed_by_sound.xml (96 % of the competitor) -- placed by sound, check the framing" \
        in str(e.value)
    calls.clear()
    with pytest.raises(pipeline.PictureNotFound):                     # a piece of the movie: run() goes on, no file
        pipeline.stop_if_picture_not_found(_stop_ctx(premiere=True, raw_movie="movie.mkv"))
    pipeline.stop_if_picture_not_found(_stop_ctx(premiere=True, force=True))   # --force-inputs: the normal edit
    assert calls == []


def test_nothing_placed_keeps_the_old_message(monkeypatch):
    monkeypatch.setattr(PB, "write", lambda ctx: {"path": None, "why": "too little"})
    with pytest.raises(pipeline.PictureNotFound) as e:
        pipeline.stop_if_picture_not_found(_stop_ctx(premiere=True))
    assert "placed by its sound" not in str(e.value)


def test_write_never_raises():
    assert PB.write(SimpleNamespace(cfg=None))["path"] is None
    assert PB.write(SimpleNamespace(cfg=SimpleNamespace(), hints=None, raw_info=None))["path"] is None


REAL = Path("D:/Shared/laptop_cases/2026-10-21_The Most Dramatic Dad of All Time \U0001F480\U0001F62D")


@pytest.mark.slow
def test_the_real_american_dad_sound_places_the_raw_where_the_picture_is(tmp_path):
    """The real pair's sound runs (from the audio hints of a run): within 0.1 s of run2's picture-matched cut list for
    most of the reel -- checked by work/t31/pbs/ad_pbs.py (80 % of the points, median 0.045 s)."""
    if not REAL.is_dir():
        pytest.skip("the real files are on the PC's D:\\Shared only")
    from match_cuts.model import AudioHints
    hits = sorted(Path("C:/Users/gvida/Desktop/bh/MovieRecaps/work/cache").rglob("a54e9f1149ea5f776d47.npz"))
    if not hits:
        pytest.skip("the American Dad audio hints are not cached here")
    h = AudioHints.load(hits[0])
    rs = PB.runs(h.comp_t, h.raw_t, h.confident(1.5), h.speed, h.window)
    sp = PB.spans(rs, 27.8, 1312.0)
    assert sum(s["to"] - s["from"] for s in sp) / 27.8 > 0.9
    assert 290.0 < sp[0]["offset"] < 295.0 and all(290.0 < s["offset"] < 330.0 for s in sp)
