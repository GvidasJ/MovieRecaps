"""End-to-end synthetic test (prompt Stage 1.3, DESIGN.md §6 last paragraph).

Runs the real CLI on the synthetic RAW + competitor made by tests/synth.py (profile 'mini' by default,
'full' with MATCH_CUTS_PROFILE=full) and asserts that the pipeline recovers the known edit exactly:

* FrameMap m(k) (``<work>/frame_map.npz``) == truth on EVERY matchable frame
* cuts +-0 frames (speed-only cuts: truth inside ``cut_ambiguity``), segment boundaries exact
* AE-simulated RAW frames (from the cutlist's raw_in / speed / comp_in, AE floor rule) == truth, except
  listed timing-tie frames; verify.json s9_2 (AE plan AND mock-run record vs m(k)): 0 mismatches
* the fullscreen segment (DESIGN §7 D1/D8): exact range, ``box`` == the whole canvas (radius 0), region 1,
  exact frames (m(k) and AE simulation), a fullscreen layout period covering it
* audio: per RAW segment |lag_ms| <= 3 ms after the audio-informed phase (D3; the synthetic audio starts
  at the NLE in-point = lower bound of the floor interval, D8)
* speed +-0.5 % AND snapped to the truth value; flip; framing +-1 % scale / +-4 px (every frame, incl. the
  push-in keys); rotation 0
* crossfade (O, D=6); NOT-IN-RAW placeholder range exact; coverage
* ``<out>/verify.json``: c1-c5 in {pass, pass_with_exceptions}, c6 == pass (mock), s9_7 pass
* audio truth (offsets 0, pitch not preserved on the 1.10x segment, NOT-IN-RAW exception, music added)
* layout truth (box, background, static zones, captions) within lenient tolerances; caption recall is
  computed from per-event caption entries only (zone aggregates and entries > 3 s excluded, REQ-7)
* a second CLI run (same work dir) gives a byte-identical cutlist.json

Failures print precise, actionable tables (which frames / segments differ and how).
Slow: run with ``--runslow`` or ``MATCH_CUTS_SLOW=1``.

``MATCH_CUTS_PROFILE=film24`` (DESIGN §6.1) runs the same tests on the real-run regimes (RAW 24000/1001 on a
30 fps grid, editor pans over moving RAW shots, a split 86 ms A/V delay, retimes, gray / lookalike inserts) plus
the ``test_film24_*`` truth assertions below. Every film24 assertion that the CURRENT pipeline fails is marked
``xfail(strict=True)`` with the fix that must make it pass (FX-xx of the real-run diagnosis); a fix removes its
xfail, and a strict XPASS tells whoever lands it to do so. ``MATCH_CUTS_E2E_REUSE=<dir>`` reuses a finished CLI
run in <dir> (``output/``, ``work/``) instead of running the CLI (test development only).
"""
from __future__ import annotations

import json
import math
import os
import subprocess
import time
from fractions import Fraction
from pathlib import Path

import numpy as np
import pytest

pytestmark = pytest.mark.slow

PROFILE = os.environ.get("MATCH_CUTS_PROFILE", "mini")
FILM = PROFILE.startswith("film24")         # film24 and its A/V-offset variants (synth.PROFILES)
TOOL_DIR = Path(__file__).resolve().parents[1]
CLI_TIMEOUT_S = 3 * 3600 if PROFILE == "full" else 3600
OK = ("pass", "pass_with_exceptions")

# FrameMap status codes (model.Status)
S_NONE, S_MATCH, S_BLEND, S_UNIFORM = 0, 1, 2, 3


def film_xfail(reason: str):
    """xfail(strict=True) for an assertion the CURRENT pipeline fails on film24 (no mark on mini / full). The
    A/V-offset variants (film24_av0 / _avm50 / _av150, FX-02) were not calibrated: non-strict there."""
    return pytest.mark.xfail(FILM, reason=reason, strict=PROFILE == "film24")


# ------------------------------------------------------------------------------------------------------
# fixtures
# ------------------------------------------------------------------------------------------------------

@pytest.fixture(scope="module")
def synthetic(request) -> dict:
    if PROFILE in ("mini", "full", "film24"):
        return request.getfixturevalue(f"synthetic_{PROFILE}")
    import conftest
    import synth
    if PROFILE not in synth.PROFILES:
        raise ValueError(f"MATCH_CUTS_PROFILE must be one of {sorted(synth.PROFILES)}, not {PROFILE!r}")
    return conftest._synthetic(PROFILE)


def _run_cli(py: str, syn: dict, out: Path, work: Path) -> subprocess.CompletedProcess:
    cmd = [py, "-m", "match_cuts", "--competitor", syn["competitor"], "--raw", syn["raw"],
           "--out", str(out), "--work", str(work)]
    return subprocess.run(cmd, cwd=str(TOOL_DIR), capture_output=True, text=True, timeout=CLI_TIMEOUT_S)


@pytest.fixture(scope="module")
def e2e(venv_python, synthetic, tmp_path_factory) -> dict:
    reuse = os.environ.get("MATCH_CUTS_E2E_REUSE")
    root = Path(reuse) if reuse else tmp_path_factory.mktemp(f"e2e_{PROFILE}")
    out, work = root / "output", root / "work"
    t0 = time.perf_counter()
    if reuse:
        log = root / "run.log"
        text = log.read_text() if log.exists() else ""
        rc = 0 if "EXIT 0" in text else (1 if "EXIT" in text else 0)
        proc = subprocess.CompletedProcess([], rc, text, "")
    else:
        proc = _run_cli(venv_python, synthetic, out, work)
    elapsed = time.perf_counter() - t0
    truth = json.loads(Path(synthetic["truth"]).read_text())
    return {"proc": proc, "out": out, "work": work, "root": root, "truth": truth, "elapsed": elapsed,
            "synthetic": synthetic, "python": venv_python}


def _tail(proc: subprocess.CompletedProcess, n: int = 60) -> str:
    return ("\n--- CLI stdout (tail) ---\n" + "\n".join(proc.stdout.splitlines()[-n:]) +
            "\n--- CLI stderr (tail) ---\n" + "\n".join(proc.stderr.splitlines()[-n:]))


def _need(e2e: dict, rel: str, where: str = "out") -> Path:
    p = e2e[where] / rel
    if not p.exists():
        pytest.fail(f"{p} was not written by the CLI (exit code {e2e['proc'].returncode}).{_tail(e2e['proc'])}")
    return p


@pytest.fixture(scope="module")
def cutlist(e2e) -> dict:
    return json.loads(_need(e2e, "cutlist.json").read_text())


@pytest.fixture(scope="module")
def verify(e2e) -> dict:
    return json.loads(_need(e2e, "verify.json").read_text())


@pytest.fixture(scope="module")
def frame_map(e2e) -> dict[str, np.ndarray]:
    with np.load(_need(e2e, "frame_map.npz", "work"), allow_pickle=False) as z:
        return {k: z[k] for k in z.files}


# ------------------------------------------------------------------------------------------------------
# helpers
# ------------------------------------------------------------------------------------------------------

def _table(title: str, header: list[str], rows: list[list], limit: int = 60) -> str:
    cols = [header] + [[str(c) for c in r] for r in rows[:limit]]
    w = [max(len(r[i]) for r in cols) for i in range(len(header))]
    lines = [title, "  " + "  ".join(h.ljust(w[i]) for i, h in enumerate(header))]
    lines += ["  " + "  ".join(c.ljust(w[i]) for i, c in enumerate(r)) for r in cols[1:]]
    if len(rows) > limit:
        lines.append(f"  ... {len(rows) - limit} more")
    return "\n".join(lines)


def _segments(cutlist: dict) -> list[dict]:
    return sorted(cutlist["segments"], key=lambda s: (s["comp_in"], s["comp_out"]))


def _truth_cut_range(truth: dict, k: int) -> tuple[int, int]:
    """Frames where the truth cut at k may sit: [k, k], or a truth ambiguity (film24: the start of a freeze can
    be any frame that already shows the held RAW frame -- identical output)."""
    for c in truth["cuts"]:
        if c["k"] == k and c.get("ambiguity"):
            return int(c["ambiguity"][0]), int(c["ambiguity"][1])
    return k, k


def _match_segments(truth: dict, cutlist: dict) -> tuple[dict[int, dict], list[dict]]:
    """truth segment id -> cutlist segment with the same comp_in (or inside the truth cut's ambiguity) and type;
    unmatched truth segments."""
    by_in: dict[int, list[dict]] = {}
    for s in cutlist["segments"]:
        by_in.setdefault(int(s["comp_in"]), []).append(s)
    matched, missing = {}, []
    for t in truth["segments"]:
        lo, hi = _truth_cut_range(truth, t["comp_in"])
        cands = [s for k in range(lo, hi + 1) for s in by_in.get(k, [])
                 if (s["type"] == "not_in_raw") == (t["type"] == "not_in_raw")]
        if cands:
            matched[t["id"]] = cands[0]
        else:
            missing.append(t)
    return matched, missing


def _fps(v) -> Fraction:
    if isinstance(v, str) and "/" in v:
        n, d = v.split("/")
        return Fraction(int(n), int(d))
    return Fraction(v)


def _ae_frames(seg: dict, comp_fps: Fraction, raw_fps: Fraction) -> dict[int, int]:
    """RAW frame AE shows on each comp frame of the segment (own implementation of the AE rule):
    stretch mode floor(raw_fps * (raw_in + v * (t_k - t_in)) + 1e-9); remap mode: linear time-remap keys."""
    ks = range(int(seg["comp_in"]), int(seg["comp_out"]))
    eps = Fraction(1, 10 ** 9)
    keys = seg.get("time_remap_keys") or []
    if keys:
        kk = sorted(keys, key=lambda d: d["comp_frame"])
        out = {}
        for k in ks:
            if k <= kk[0]["comp_frame"]:
                val = Fraction(kk[0]["raw_seconds"])
            elif k >= kk[-1]["comp_frame"]:
                val = Fraction(kk[-1]["raw_seconds"])
            else:
                a, b = next((a, b) for a, b in zip(kk[:-1], kk[1:]) if a["comp_frame"] <= k <= b["comp_frame"])
                u = (Fraction(k) - Fraction(a["comp_frame"])) / (Fraction(b["comp_frame"]) - Fraction(a["comp_frame"]))
                val = Fraction(a["raw_seconds"]) + u * (Fraction(b["raw_seconds"]) - Fraction(a["raw_seconds"]))
            out[k] = math.floor(raw_fps * val + eps)
        return out
    raw_in = Fraction(seg["raw_in_seconds"])
    v = Fraction(seg["speed"])
    t_in = Fraction(int(seg["comp_in"])) / comp_fps
    return {k: math.floor(raw_fps * (raw_in + v * (Fraction(k) / comp_fps - t_in)) + eps) for k in ks}


def _sim_at(seg: dict, k: int, key_field: str = "transform_keys") -> dict:
    keys = sorted(seg.get(key_field) or [], key=lambda d: d["comp_frame"])
    if not keys:
        return seg["transform"]
    if k <= keys[0]["comp_frame"]:
        return keys[0]
    if k >= keys[-1]["comp_frame"]:
        return keys[-1]
    a, b = next((a, b) for a, b in zip(keys[:-1], keys[1:]) if a["comp_frame"] <= k <= b["comp_frame"])
    u = (k - a["comp_frame"]) / (b["comp_frame"] - a["comp_frame"])
    return {f: a.get(f, 0.0) + u * (b.get(f, 0.0) - a.get(f, 0.0)) for f in ("scale", "rotation_deg", "tx", "ty")}


CAPTION_EVENT_MAX_S = 3.0


def caption_event_entries(cutlist: dict) -> list[dict]:
    """Per-event caption entries of a cutlist (layout.captions + overlays_detected): type 'captions' only
    (not 'text' / stickers), never a zone-derived entry (kind/source 'zone', a '*zone*' type, or the
    aggregate caption zone spanning the whole edit) and never an entry longer than CAPTION_EVENT_MAX_S --
    a word-by-word caption event is short, so any longer entry is an aggregate that would cover every
    caption frame and make the recall check impossible to fail (REQ-7)."""
    fps = float(_fps((cutlist.get("competitor") or {}).get("fps") or 30))
    max_frames = CAPTION_EVENT_MAX_S * fps + 1e-9
    out = []
    for c in list((cutlist.get("layout") or {}).get("captions") or []) + list(cutlist.get("overlays_detected") or []):
        typ = str(c.get("type", "")).lower()
        if typ not in ("captions", "caption") or "zone" in typ:
            continue
        if str(c.get("kind", "")).lower() == "zone" or str(c.get("source", "")).lower() == "zone":
            continue
        a, b = c.get("comp_in"), c.get("comp_out")
        if a is None or b is None or not (0 < int(b) - int(a) <= max_frames):
            continue
        out.append(c)
    return out


def caption_recall(truth_captions: list[dict], cutlist: dict, n: int) -> tuple[float, list[dict]]:
    """Fraction of truth caption frames covered by per-event caption entries; (recall, entries used)."""
    want = np.zeros(n, bool)
    for c in truth_captions:
        want[int(c["k_in"]):int(c["k_out"])] = True
    got = np.zeros(n, bool)
    used = caption_event_entries(cutlist)
    for c in used:
        got[max(0, int(c["comp_in"])):min(n, int(c["comp_out"]))] = True
    return float((want & got).sum() / max(1, want.sum())), used


def _apply(sim: dict, p: tuple[float, float]) -> np.ndarray:
    th = math.radians(sim.get("rotation_deg", 0.0))
    s = sim["scale"]
    R = np.array([[math.cos(th), -math.sin(th)], [math.sin(th), math.cos(th)]])
    return s * R @ np.asarray(p, float) + np.array([sim["tx"], sim["ty"]])


def _inverse_apply(sim: dict, q: tuple[float, float]) -> np.ndarray:
    th = math.radians(sim.get("rotation_deg", 0.0))
    s = sim["scale"]
    R = np.array([[math.cos(th), -math.sin(th)], [math.sin(th), math.cos(th)]])
    return R.T @ (np.asarray(q, float) - np.array([sim["tx"], sim["ty"]])) / s


# ------------------------------------------------------------------------------------------------------
# tests
# ------------------------------------------------------------------------------------------------------

@film_xfail("FX-02/FX-03/FX-04/FX-08: the CLI exits 1 on film24 (criteria c2-c5 fail)")
def test_cli_succeeds(e2e):
    proc = e2e["proc"]
    assert proc.returncode == 0, f"CLI exit code {proc.returncode} after {e2e['elapsed']:.0f}s{_tail(proc)}"


def test_coverage_and_totals(e2e, cutlist):
    truth = e2e["truth"]
    n = truth["competitor"]["frames"]
    assert int(cutlist["competitor"]["frames"]) == n, "cutlist competitor frame count != truth"
    assert int(cutlist["raw"]["frames"]) == truth["raw"]["frames"], "cutlist RAW frame count != truth"
    assert _fps(cutlist["competitor"]["fps"]) == Fraction(30) and \
        _fps(cutlist["raw"]["fps"]) == _fps(truth["raw"]["fps"])
    cover = np.zeros(n, np.int32)
    for s in cutlist["segments"]:
        cover[int(s["comp_in"]):int(s["comp_out"])] += 1
    allowed = np.ones(n, np.int32)
    for tr in truth["transitions"]:
        allowed[tr["O"]:tr["O"] + tr["D"]] = 2
    bad = np.nonzero(cover != allowed)[0]
    rows = [[int(k), int(cover[k]), int(allowed[k])] for k in bad]
    assert not rows, _table("coverage differs from truth (1 layer per frame, 2 in the crossfade overlap):",
                            ["comp_frame", "layers", "expected"], rows)


@film_xfail("FX-08: frames of chains without any anchor or near-miss (the first two-clip pan) stay NONE "
            "(the editor-pan time/translation confound is fixed, FX-03)")
def test_frame_map_equals_truth(e2e, frame_map):
    truth = e2e["truth"]
    status, raw = frame_map["status"], frame_map["raw"]
    n = truth["competitor"]["frames"]
    assert len(status) == n, f"FrameMap has {len(status)} frames, competitor {n}"
    kinds = {s["id"]: s["kind"] for s in truth["segments"]}
    rows = []
    n_checked = 0
    for fr in truth["frames"]:
        k = fr["k"]
        st, got = int(status[k]), int(raw[k])
        if fr["raw_a"] is None:                                        # NOT-IN-RAW
            if st == S_MATCH:
                rows.append([k, fr["seg"], kinds[fr["seg"]], "NOT-IN-RAW", got, st, "matched a RAW frame"])
            continue
        if fr["alpha_b"] is not None and fr["alpha_b"] > 0:            # visibly blended crossfade frame
            if st == S_MATCH and got not in (fr["raw_a"], fr["raw_b"]):
                rows.append([k, fr["seg"], kinds[fr["seg"]], f"{fr['raw_a']}|{fr['raw_b']}", got, st, "blend"])
            continue
        if fr["alpha_b"] == 0 and st == S_BLEND:                       # frame O (pure A) inside the transition
            continue
        if fr.get("class") == "gray":         # film24 gray zone: MATCH or uncertain both honest (test_film24_gray)
            if st == S_MATCH and got != fr["raw_a"]:
                rows.append([k, fr["seg"], kinds[fr["seg"]], fr["raw_a"], got, st, "gray: wrong frame"])
            continue
        n_checked += 1
        if st != S_MATCH or got != fr["raw_a"]:
            rows.append([k, fr["seg"], kinds[fr["seg"]], fr["raw_a"], got, st,
                         f"off by {got - fr['raw_a']:+d}" if st == S_MATCH else "not MATCH"])
    assert n_checked > 0.85 * sum(1 for fr in truth["frames"] if fr["raw_a"] is not None)
    assert not rows, _table(f"FrameMap m(k) != truth on {len(rows)} frames (of {n_checked} matchable):",
                            ["k", "seg", "kind", "truth", "m(k)", "status", "note"], rows)


@film_xfail("FX-08: the only cut off the truth is 445, the edge of the anchorless first two-clip pan (NOT-IN-RAW "
            "placeholder | 1-frame island); the pan / step / punch chains cut exactly since FX-03 / FX-06")
def test_cuts_exact(e2e, cutlist):
    truth = e2e["truth"]
    segs = _segments(cutlist)
    got_cuts = {int(s["comp_in"]): s for s in segs[1:]}
    want = {c["k"]: c for c in truth["cuts"]}
    explained = set(want)
    rows = []
    for k, c in sorted(want.items()):
        lo, hi = _truth_cut_range(truth, k)
        hit = [g for g in got_cuts if lo <= g <= hi]
        explained.update(hit)
        if hit:
            continue
        amb = [s.get("cut_ambiguity") for s in segs if s.get("cut_ambiguity")]
        if any(a[0] <= k <= a[1] for a in amb):
            continue
        near = sorted(got_cuts, key=lambda x: abs(x - k))[:1]
        rows.append([k, c["type"], c["b_kind"], "missing", f"nearest reported cut {near[0]}" if near else "-"])
    for k, s in sorted(got_cuts.items()):
        if k not in explained:
            rows.append([k, "-", "-", "spurious", f"cutlist segment {s.get('id')} type {s.get('type')}"])
    assert not rows, _table("cut positions differ from truth (+-0 frames required):",
                            ["comp_frame", "truth type", "truth kind", "problem", "detail"], rows)
    matched, missing = _match_segments(truth, cutlist)
    def end_ok(t: dict, got: int) -> bool:
        lo, hi = _truth_cut_range(truth, t["comp_out"])
        return lo <= got <= hi
    rows = [[t["id"], t["kind"], f"[{t['comp_in']},{t['comp_out']})",
             f"[{matched[t['id']]['comp_in']},{matched[t['id']]['comp_out']})"]
            for t in truth["segments"] if t["id"] in matched and not end_ok(t, int(matched[t["id"]]["comp_out"]))]
    rows += [[t["id"], t["kind"], f"[{t['comp_in']},{t['comp_out']})", "no segment"] for t in missing]
    assert not rows, _table("segment ranges differ:", ["truth seg", "kind", "truth", "cutlist"], rows)


def test_ae_simulated_frames_equal_truth(e2e, cutlist):
    truth = e2e["truth"]
    comp_fps, raw_fps = _fps(cutlist["competitor"]["fps"]), _fps(cutlist["raw"]["fps"])
    matched, _ = _match_segments(truth, cutlist)
    rows, n_frames = [], 0
    for t in truth["segments"]:
        if t["type"] != "raw" or t["id"] not in matched:
            continue
        s = matched[t["id"]]
        sim = _ae_frames(s, comp_fps, raw_fps)
        ties = set(int(x) for x in (s.get("tie_frames") or []))
        for i, k in enumerate(range(t["comp_in"], t["comp_out"])):
            if k not in sim or truth["frames"][k].get("class") in ("blend", "gray"):
                continue
            n_frames += 1
            want = t["raw_frames"][i]
            if sim[k] != want and k not in ties:
                rows.append([t["id"], t["kind"], k, want, sim[k], f"{sim[k] - want:+d}", s.get("raw_in_seconds"),
                             s.get("speed")])
    assert n_frames > 0
    assert not rows, _table(f"AE-simulated RAW frame != truth on {len(rows)} of {n_frames} frames "
                            "(stretch rule floor(raw_fps*(raw_in+v*(t_k-t_in))+1e-9); tie frames excluded):",
                            ["seg", "kind", "k", "truth", "AE", "diff", "raw_in_s", "speed"], rows)


def test_ae_plan_survives_start_time_error(e2e):
    """FX-10 (DESIGN §7.3): every stretch / remap layer of the AE plan keeps an exact floor-rule slack of at
    least ae_slack_tol_frames on every frame (layers below it were exported frame-exact), so simulate_ae with
    every startTime +-1e-6 s shows identical RAW frames on every layer."""
    from match_cuts import export_ae
    from match_cuts.config import Config
    plan = json.loads(_need(e2e, "ae_plan.json", "work").read_text())
    tol = Config().ae_slack_tol_frames
    low = [[L["id"], L["timeMode"], L.get("minSlack"), L.get("minSlackK")] for L in plan["layers"]
           if L["kind"] == "raw" and L["timeMode"] in ("stretch", "remap") and float(L.get("minSlack", 0.0)) < tol]
    assert not low, _table(f"stretch / remap layers with an exact slack below {tol} RAW frame:",
                           ["layer", "mode", "min slack", "at MAIN frame"], low)
    base = export_ae.raw_frames_by_layer(export_ae.simulate_ae(plan))
    assert base
    rows = []
    for off in (1e-6, -1e-6):
        got = export_ae.raw_frames_by_layer(export_ae.simulate_ae(plan, start_offset_s=off))
        for lid in sorted(set(base) | set(got)):
            if got.get(lid) != base.get(lid):
                diff = [K for K in base.get(lid, {}) if got.get(lid, {}).get(K) != base[lid][K]]
                rows.append([off, lid, diff[:8]])
    assert not rows, _table("simulate_ae changes with startTime +-1e-6 s:", ["offset s", "layer", "frames"], rows)


def _ae_check_part(s92: dict, src: str) -> dict | None:
    d = s92.get(src)
    return d if isinstance(d, dict) else None


def test_verify_ae_sim_has_no_mismatches(verify):
    """verify.json s9_2 (criterion 3): the AE plan AND the mock-run record, simulated with AE's own
    semantics, show m(k) on every matched frame -- n_mismatches == 0 for both (ambiguous-identical /
    timing-tie / reassigned frames are listed classes, reported here but not mismatches)."""
    checks = verify.get("checks") or {}
    s92 = checks.get("s9_2_ae_sim") or checks.get("s9_2") or {}
    rows = []
    for src in ("plan", "mock"):
        d = _ae_check_part(s92, src)
        if d is None:
            rows.append([src, "missing", "-", "-", json.dumps(s92, default=str)[:200]])
            continue
        n_mis = d.get("n_mismatches")
        if n_mis is None:
            n_mis = len(d.get("mismatches") or [])
        classes = {c: (len(d[c]) if isinstance(d.get(c), list) else d.get(c))
                   for c in ("ambiguous_identical", "timing_tie", "reassigned", "n_reassigned") if c in d}
        if d.get("status") in (None, "not_available", "fail", "error") or n_mis != 0:
            first = [(m.get("k"), m.get("ae"), m.get("m")) for m in (d.get("mismatches") or [])[:10]]
            rows.append([src, d.get("status"), n_mis, json.dumps(classes), f"{d.get('summary')} first (k, ae, m): {first}"])
    assert not rows, _table("s9_2 AE simulation vs m(k) must have 0 mismatches (plan and mock record):",
                            ["source", "status", "n_mismatches", "classes", "detail"], rows)


def test_fullscreen_segment(e2e, cutlist, frame_map):
    """DESIGN §7 D1/D8: the ~1 s fullscreen segment is found with its exact range, carries box == the whole
    canvas (corner radius 0) and region 1, sits in a fullscreen layout period, and shows the exact truth
    frames both in m(k) and in the AE simulation."""
    truth = e2e["truth"]
    if FILM:
        pytest.skip("film24 has no fullscreen chain (DESIGN §6.1)")
    fs = [t for t in truth["segments"] if t.get("kind") == "fullscreen"]
    assert len(fs) == 1, f"truth has {len(fs)} fullscreen segments (synthetic data older than SYNTH_VERSION 2?)"
    t = fs[0]
    W, H = truth["competitor"]["width"], truth["competitor"]["height"]
    matched, _ = _match_segments(truth, cutlist)
    s = matched.get(t["id"])
    assert s is not None, (f"no cutlist segment starts at the fullscreen segment's comp_in {t['comp_in']}:\n" +
                           _table("cutlist segments:", ["id", "comp_in", "comp_out", "type", "box"],
                                  [[x.get("id"), x["comp_in"], x["comp_out"], x["type"], x.get("box")]
                                   for x in _segments(cutlist)]))
    rows = []
    if int(s["comp_out"]) != t["comp_out"]:
        rows.append(["range", f"[{t['comp_in']},{t['comp_out']})", f"[{s['comp_in']},{s['comp_out']})"])
    box = s.get("box")
    want = {"x": 0, "y": 0, "w": W, "h": H}
    if not isinstance(box, dict) or any(abs(float(box.get(f, -1e9)) - v) > 0.5 for f, v in want.items()) or \
            abs(float(box.get("corner_radius", 0.0) or 0.0)) > 0.5:
        rows.append(["box", f"{want} r=0", box])
    if int(s.get("region", -1) if s.get("region") is not None else -1) != 1:
        rows.append(["region", 1, s.get("region")])
    periods = [pp for pp in ((cutlist.get("layout") or {}).get("periods") or []) if pp.get("mode") == "fullscreen"]
    got_p = sorted((int(pp["comp_in"]), int(pp["comp_out"])) for pp in periods)
    want_p = [(d["comp_in"], d["comp_out"]) for d in truth["layout"].get("fullscreen", [])]
    if got_p != want_p:
        rows.append(["layout fullscreen periods", want_p, got_p])
    status, raw = frame_map["status"], frame_map["raw"]
    sim = _ae_frames(s, _fps(cutlist["competitor"]["fps"]), _fps(cutlist["raw"]["fps"]))
    ties = set(int(x) for x in (s.get("tie_frames") or []))
    for i, k in enumerate(range(t["comp_in"], t["comp_out"])):
        j = t["raw_frames"][i]
        if int(status[k]) != S_MATCH or int(raw[k]) != j:
            rows.append([f"m({k})", j, f"{int(raw[k])} (status {int(status[k])})"])
        if k in sim and sim[k] != j and k not in ties:
            rows.append([f"AE frame at {k}", j, sim[k]])
    assert not rows, _table(f"fullscreen segment {t['id']} [{t['comp_in']},{t['comp_out']}) differs from truth:",
                            ["field", "truth", "cutlist"], rows)


def test_audio_phase_lag(e2e, cutlist):
    """DESIGN §7 D3/D8/D9: the synthetic audio starts at the NLE in-point (lower bound of the floor interval);
    after the audio-informed phase every measurable RAW segment's residual audio lag is within +-3 ms. lag_ms is
    the residual after the run's published A/V offset (film24: -86 ms, competitor audio late), so the residual
    is judged against 0; without a published offset the raw lag is judged against the truth offset. Segments the
    truth makes unmeasurable (retimed, or shorter than verify_audio_min_s) are not judged here."""
    truth = e2e["truth"]
    published = (cutlist.get("audio") or {}).get("av_offset") or {}
    want = 0.0 if published.get("status") == "measured" else \
        float(((truth["audio"].get("av_offset") or {}).get("lag_ms")) or 0.0)
    min_frames = math.ceil(0.5 * float(_fps(cutlist["competitor"]["fps"])))
    matched, _ = _match_segments(truth, cutlist)
    rows = []
    for t in truth["segments"]:
        s = matched.get(t["id"])
        if t["type"] != "raw" or s is None:
            continue
        if float(t.get("speed", 1) or 0) != 1.0 or int(t["comp_out"]) - int(t["comp_in"]) < min_frames:
            continue
        a = s.get("audio") or {}
        lag = a.get("lag_ms")
        if lag is None or not math.isfinite(float(lag)) or abs(float(lag) - want) > 3.0:
            rows.append([t["id"], t["kind"], lag, a.get("lag_ms_video"), a.get("phase_source"), a.get("corr"),
                         a.get("exception"), s.get("raw_in_seconds"), t["audio"]["raw_in_seconds"]])
    assert not rows, _table("audio lag after the audio-informed phase must be within +-3 ms per RAW segment:",
                            ["seg", "kind", "lag_ms", "lag_ms_video", "phase_source", "corr", "exception",
                             "raw_in_s", "truth audio raw_in_s"], rows)


@film_xfail("FX-08: the blend slow motion is fitted as v=0.2536 (every pan / step / punch framing is within tolerance "
            "since FX-03 / FX-06)")
def test_speed_flip_framing(e2e, cutlist):
    truth = e2e["truth"]
    matched, _ = _match_segments(truth, cutlist)
    box = truth["layout"]["box"]
    centre = (box["x"] + box["w"] / 2, box["y"] + box["h"] / 2)
    rows = []
    for t in truth["segments"]:
        if t["type"] != "raw" or t["id"] not in matched:
            continue
        s = matched[t["id"]]
        v, vt = float(s["speed"]), t["speed"]
        if vt == 0:                                        # film24 freeze: a hold, v = 0 exactly
            if v != 0:
                rows.append([t["id"], t["kind"], "speed (freeze)", vt, v, ""])
        elif abs(v / vt - 1) > 0.005:
            rows.append([t["id"], t["kind"], "speed", vt, v, f"{100 * (v / vt - 1):+.3f} %"])
        elif abs(v - vt) > 1e-9 * max(1.0, vt):
            rows.append([t["id"], t["kind"], "speed not snapped", vt, v, f"measured {s.get('speed_measured')}"])
        if bool(s.get("flip_h")) != t["flip"]:
            rows.append([t["id"], t["kind"], "flip", t["flip"], s.get("flip_h"), ""])
            continue
        animated = bool(t["transform_keys"])
        if animated and not s.get("transform_keys"):
            rows.append([t["id"], t["kind"], "animated framing", "2 linear keys", "no transform_keys", ""])
        worst_s, worst_p, worst_k, worst_rot = 0.0, 0.0, None, 0.0
        for k in range(t["comp_in"], t["comp_out"]):
            ts, cs = _sim_at(t, k), _sim_at(s, k)
            if cs is None:
                break
            ds = abs(cs["scale"] / ts["scale"] - 1)
            # position: where the pipeline puts the RAW point that the truth maps to the box centre
            p_raw = _inverse_apply(ts, centre)
            dp = float(np.linalg.norm(_apply(cs, p_raw) - np.asarray(centre)))
            worst_rot = max(worst_rot, abs(cs.get("rotation_deg", 0.0)))
            if ds > worst_s or dp > worst_p:
                worst_k = k
            worst_s, worst_p = max(worst_s, ds), max(worst_p, dp)
        if s.get("transform") is None and not s.get("transform_keys"):
            rows.append([t["id"], t["kind"], "transform", "Sim", "missing", ""])
        elif worst_s > 0.01 or worst_p > 4.0:
            rows.append([t["id"], t["kind"], "framing", "+-1% / +-4px",
                         f"scale err {100 * worst_s:.3f} %, pos err {worst_p:.2f} px", f"worst at k={worst_k}"])
        if worst_rot > 0.2:
            rows.append([t["id"], t["kind"], "rotation", 0.0, f"{worst_rot:.3f} deg", ""])
    assert not rows, _table("speed / flip / framing differ from truth:",
                            ["seg", "kind", "what", "truth", "cutlist", "detail"], rows)


def test_crossfade(e2e, cutlist):
    truth = e2e["truth"]
    if not truth["transitions"]:
        pytest.skip(f"profile {PROFILE} has no crossfade")
    tr = truth["transitions"][0]
    O, D = tr["O"], tr["D"]
    segs = _segments(cutlist)
    b = [s for s in segs if int(s["comp_in"]) == O and s["type"] != "not_in_raw"]
    a = [s for s in segs if int(s["comp_out"]) == O + D]
    fx = [s for s in segs if (s.get("transition_in") or {}).get("type") == "crossfade"]
    detail = _table("segments carrying a crossfade transition_in:", ["id", "comp_in", "comp_out", "transition_in"],
                    [[s.get("id"), s["comp_in"], s["comp_out"], s.get("transition_in")] for s in fx])
    assert b, f"no segment starts at the crossfade start O={O}\n{detail}"
    tin = b[0].get("transition_in") or {}
    assert tin.get("type") == "crossfade", f"segment at O={O} has transition_in={tin}\n{detail}"
    assert int(tin.get("duration_frames", -1)) == D, f"crossfade duration {tin.get('duration_frames')} != {D}"
    assert a, f"no segment ends at O+D={O + D} (outgoing A must end D frames after O)\n{detail}"
    tout = a[0].get("transition_out") or {}
    assert tout.get("type") == "crossfade", f"outgoing segment transition_out={tout}"
    alpha = tin.get("alpha") or []
    if alpha:
        assert len(alpha) == D and np.allclose(alpha, tr["alpha"], atol=0.05), \
            f"crossfade alpha (incoming) {alpha} != truth {tr['alpha']}"


@film_xfail("FX-08: NOT-IN-RAW placeholders on the anchorless first two-clip pan and the gray chain")
def test_not_in_raw_placeholder(e2e, cutlist):
    truth = e2e["truth"]
    want = [(r["comp_in"], r["comp_out"]) for r in truth["not_in_raw"]]
    got = [(int(s["comp_in"]), int(s["comp_out"])) for s in _segments(cutlist) if s["type"] == "not_in_raw"]
    assert got == want, f"NOT-IN-RAW placeholders {got} != truth {want}"


@film_xfail("FX-01..FX-09: c2-c5 fail on film24")
def test_verify_criteria(verify):
    crit = verify.get("criteria", {})
    rows = []
    for c in ("c1_coverage", "c2_cuts", "c3_source_frames", "c4_speed_framing", "c5_audio"):
        st = (crit.get(c) or {}).get("status")
        if st not in OK:
            rows.append([c, st, json.dumps((crit.get(c) or {}).get("details"), default=str)[:300]])
    c6 = (crit.get("c6_after_effects") or {}).get("status")
    if c6 != "pass":
        rows.append(["c6_after_effects", c6, json.dumps((crit.get("c6_after_effects") or {}).get("details"),
                                                        default=str)[:300]])
    s97 = (verify.get("checks", {}).get("s9_7_determinism") or {})
    if s97.get("status") != "pass":
        rows.append(["s9_7_determinism", s97.get("status"), json.dumps(s97, default=str)[:300]])
    assert not rows, _table("verify.json criteria not satisfied (failures: "
                            f"{json.dumps(verify.get('failures'), default=str)[:500]}):",
                            ["criterion", "status", "details"], rows)


def test_audio_truth(e2e, cutlist):
    truth = e2e["truth"]
    matched, _ = _match_segments(truth, cutlist)
    rows = []
    for t in truth["segments"]:
        s = matched.get(t["id"])
        if s is None:
            continue
        a, ta = s.get("audio") or {}, t["audio"]
        for f in ("in_offset_frames", "out_offset_frames"):
            if int(a.get(f) or 0) != ta[f]:
                rows.append([t["id"], t["kind"], f, ta[f], a.get(f)])
        if t["type"] == "raw" and ta.get("pitch_preserved") is False and a.get("pitch_preserved") is not False:
            rows.append([t["id"], t["kind"], "pitch_preserved", False, a.get("pitch_preserved")])
        if t["type"] == "not_in_raw" and a.get("exception") != "not_in_raw":
            rows.append([t["id"], t["kind"], "exception", "not_in_raw", a.get("exception")])
    n = truth["competitor"]["frames"]
    music = [m for m in cutlist.get("added_audio", []) if m.get("type") == "music"]
    covered = np.zeros(n, bool)
    for m in music:
        covered[int(m["comp_in"]):int(m["comp_out"])] = True
    if covered.mean() < 0.8:
        rows.append(["-", "-", "added_audio music", "0..N (-12 dB)",
                     f"{json.dumps(cutlist.get('added_audio'))[:200]} covers {100 * covered.mean():.0f} %"])
    av = (cutlist.get("audio") or {}).get("av_offset") or {}
    want_av = float(((truth["audio"].get("av_offset") or {}).get("lag_ms")) or 0.0)
    if want_av == 0.0:
        if av.get("lag_ms") != 0.0 or av.get("status") != "zero":   # DESIGN §7 D9: no A/V offset -> exactly 0
            rows.append(["-", "-", "audio.av_offset", "status zero, lag_ms 0.0", f"{av.get('status')} {av.get('lag_ms')}"])
    else:
        lo, hi = (av.get("lag_ms_interval") or [None, None])[:2]
        if av.get("status") != "measured" or lo is None or not (lo <= want_av <= hi):
            rows.append(["-", "-", "audio.av_offset", f"measured, interval containing {want_av}",
                         f"{av.get('status')} {av.get('lag_ms_interval')}"])
    assert not rows, _table("audio analysis differs from truth:", ["seg", "kind", "field", "truth", "cutlist"], rows)


def test_layout_truth(e2e, cutlist):
    truth = e2e["truth"]["layout"]
    lay = cutlist.get("layout") or {}
    rows = []
    box, tb = lay.get("box") or {}, truth["box"]
    for f in ("x", "y", "w", "h"):
        tol = 2.0 if f in ("x", "y") else 4.0
        if f not in box or abs(float(box[f]) - tb[f]) > tol:
            rows.append(["box." + f, tb[f], box.get(f), f"+-{tol}"])
    r = box.get("corner_radius")
    if r is None or abs(float(r) - tb["radius"]) > 0.3 * tb["radius"] + 2:
        rows.append(["box.corner_radius", tb["radius"], r, "+-30 % + 2"])
    if str(lay.get("canvas_bg", "")).lower() not in ("#000000", "#000"):
        rows.append(["canvas_bg", "#000000", lay.get("canvas_bg"), "exact"])
    if lay.get("background") != "solid":
        rows.append(["background", "solid", lay.get("background"), "exact"])
    zones = lay.get("zones") or []

    def overlap(a, b):
        ix = max(0.0, min(a["x"] + a["w"], b["x"] + b["w"]) - max(a["x"], b["x"]))
        iy = max(0.0, min(a["y"] + a["h"], b["y"] + b["h"]) - max(a["y"], b["y"]))
        return ix * iy / (a["w"] * a["h"])
    for z in truth["zones"]:
        best = max((overlap(z, d) for d in zones), default=0.0)
        if best < 0.5:
            rows.append([f"zone {z['type']}", f"({z['x']},{z['y']},{z['w']},{z['h']})", f"covered {best:.0%}",
                         ">= 50 % covered by a detected zone"])
    n = e2e["truth"]["competitor"]["frames"]
    recall, used = caption_recall(truth["captions"], cutlist, n)
    if recall < 0.8:
        n_frames = sum(c["k_out"] - c["k_in"] for c in truth["captions"])
        rows.append(["captions", f"{len(truth['captions'])} events / {n_frames} frames",
                     f"recall {recall:.0%} from {len(used)} per-event entries",
                     ">= 80 % of caption frames (zones and entries > 3 s excluded)"])
    assert not rows, _table("layout differs from truth:", ["field", "truth", "cutlist", "tolerance"], rows)


def test_second_run_identical_cutlist(e2e, cutlist):
    """Criterion 9.7: a second CLI run with the SAME arguments (same inputs, same --out, same --work ->
    caches) writes an identical cutlist.json. Wall-clock provenance.timings is the only field allowed to
    differ (DESIGN §1). (A different --out legitimately changes the absolute media paths the JSX falls
    back to, so the rerun must use the same output directory.)"""
    first = _need(e2e, "cutlist.json").read_bytes()
    out2 = e2e["out"]
    proc = _run_cli(e2e["python"], e2e["synthetic"], out2, e2e["work"])
    p2 = out2 / "cutlist.json"
    assert p2.exists(), f"second run wrote no cutlist.json (exit {proc.returncode}){_tail(proc)}"
    second = p2.read_bytes()
    if first == second:
        return
    a, b = json.loads(first), json.loads(second)
    for d in (a, b):
        d.get("provenance", {}).pop("timings", None)
    diffs: list[list] = []

    def walk(x, y, path):
        if len(diffs) > 40:
            return
        if isinstance(x, dict) and isinstance(y, dict):
            for k in sorted(set(x) | set(y)):
                walk(x.get(k, "<missing>"), y.get(k, "<missing>"), f"{path}.{k}")
        elif isinstance(x, list) and isinstance(y, list) and len(x) == len(y):
            for i, (u, v) in enumerate(zip(x, y)):
                walk(u, v, f"{path}[{i}]")
        elif x != y:
            diffs.append([path, str(x)[:80], str(y)[:80]])
    walk(a, b, "cutlist")
    assert not diffs, _table("second run produced a different cutlist.json:", ["path", "run 1", "run 2"], diffs)


# ======================================================================================================
# film24 truth assertions (DESIGN §6.1). Judged against truth.json -- never against refine's own FrameMap.
# ======================================================================================================

# editor-clip groups (truth segment kinds) of the film24 edit
FILM_GROUPS = {
    "normal": ("normal",), "short": ("short",), "pan": ("pan",), "pan_accel": ("pan_accel",),
    "pan_step": ("pan_step_pan", "pan_step_back"), "punch_pan": ("punch_pan_pre", "punch_pan"),
    "two_clip_pans": ("two_clip_pan_a", "two_clip_pan_b"), "raw_zoom_roll": ("raw_zoom_roll",),
    "line_across_shots": ("line_a", "line_dark", "line_c"), "freeze": ("freeze_play", "freeze"),
    "blend_slow": ("blend_slow",), "gray": ("gray",),
}

# Assertions the CURRENT pipeline fails on film24 -> the fix that must make them pass (strict xfail).
_TWO = ("FX-08: the second pan clip is exact since FX-03, the first has no anchor and no near-miss (RANSAC <= 5 "
        "inliers under the RAW-only disclaimer) -> NOT-IN-RAW placeholder; needs a line-constrained search")
FILM_XFAIL: dict[tuple[str, str], str] = {
    # pan, pan_accel: frames exact, one segment and the truth framing since the time-line-first refine (FX-03) and
    # the measured framing summary (FX-06); pan_step, punch_pan: the framing step is a confirmed cut on one time
    # line with a shared phase (FX-06, FX-04)
    **{(t, "two_clip_pans"): _TWO for t in ("frames_exact", "one_segment_per_clip", "framing")},
    ("one_segment_per_clip", "gray"): "FX-08: the gray-zone chain becomes a NOT-IN-RAW placeholder",
    ("speed", "blend_slow"): "FX-08: the frame-blend slow motion is fitted as v=0.2536 instead of 0.25 / frame_blend",
}


def _film_params(test: str, groups=None) -> list:
    return [pytest.param(g, marks=film_xfail(FILM_XFAIL[(test, g)]) if (test, g) in FILM_XFAIL else ())
            for g in (groups or FILM_GROUPS)]


def _need_film() -> None:
    if not FILM:
        pytest.skip("film24 truth assertion (MATCH_CUTS_PROFILE=film24)")


def _group_segments(truth: dict, group: str) -> list[dict]:
    segs = [t for t in truth["segments"] if t["kind"] in FILM_GROUPS[group]]
    assert segs, f"truth has no {group} segment"
    return segs


def _covering(cutlist: dict, k: int) -> list[dict]:
    return [s for s in cutlist["segments"] if int(s["comp_in"]) <= k < int(s["comp_out"])]


def _ae_frame_at(seg: dict, k: int, comp_fps: Fraction, raw_fps: Fraction) -> int | None:
    """RAW frame AE shows at comp frame k on a RAW segment (None for placeholders)."""
    return _ae_frames(seg, comp_fps, raw_fps).get(k) if seg.get("type") == "raw" else None


@pytest.mark.parametrize("group", _film_params("frames_exact"))
def test_film24_frames_exact(e2e, cutlist, group):
    """Every truth frame of the group (exact / static class) is shown by a RAW cutlist segment whose AE
    simulation gives exactly the truth RAW frame. Blend weights are not judged here; gray-zone frames (FX-08:
    matched OR honestly uncertain) only where a RAW segment claims them."""
    _need_film()
    truth = e2e["truth"]
    cf, rf = _fps(cutlist["competitor"]["fps"]), _fps(cutlist["raw"]["fps"])
    rows, n = [], 0
    for t in _group_segments(truth, group):
        for k in range(t["comp_in"], t["comp_out"]):
            fr = truth["frames"][k]
            cov = _covering(cutlist, k)
            if fr["raw_a"] is None or fr["class"] not in ("exact", "static", "gray") or \
                    (fr["class"] == "gray" and not any(s["type"] == "raw" for s in cov)):
                continue
            n += 1
            got = [_ae_frame_at(s, k, cf, rf) for s in cov]
            if len(cov) != 1 or got[0] != fr["raw_a"]:
                rows.append([k, t["kind"], fr["raw_a"], got, [f"S{s.get('id')} {s['type']}" for s in cov]])
    assert n > 0 or group == "gray"
    assert not rows, _table(f"{group}: {len(rows)} of {n} frames differ from truth (AE simulation of the cutlist):",
                            ["k", "clip", "truth", "AE", "segments"], rows)


def _clip_core(truth: dict, t: dict) -> tuple[tuple[int, int], tuple[int, int], range]:
    """(allowed comp_in range, allowed comp_out range, frames that belong to the clip whatever the truth-ambiguous
    boundary choice) of a truth editor clip."""
    lo_in, hi_in = _truth_cut_range(truth, t["comp_in"])
    lo_out, hi_out = _truth_cut_range(truth, t["comp_out"])
    return (lo_in, hi_in), (lo_out, hi_out), range(hi_in, lo_out)


@pytest.mark.parametrize("group", _film_params("one_segment_per_clip"))
def test_film24_one_segment_per_editor_clip(e2e, cutlist, group):
    """One cutlist segment per editor clip: the same range (a truth-ambiguous freeze start may sit on any frame
    that already shows the held RAW frame), no extra cut inside the clip, never a placeholder."""
    _need_film()
    truth = e2e["truth"]
    rows = []
    for t in _group_segments(truth, group):
        (lo_in, hi_in), (lo_out, hi_out), core = _clip_core(truth, t)
        segs = {id(s): s for k in core for s in _covering(cutlist, k)}
        # FX-08: the gray-zone chain may be ONE honest uncertain segment instead of a match, never a placeholder
        types = ("raw",) if group != "gray" else tuple({s["type"] for s in segs.values()} - {"not_in_raw"})
        ok = len(segs) == 1 and all(s["type"] in types and lo_in <= int(s["comp_in"]) <= hi_in and
                                    lo_out <= int(s["comp_out"]) <= hi_out for s in segs.values())
        if not ok:
            rows.append([t["id"], t["kind"], f"[{t['comp_in']},{t['comp_out']})",
                         " ".join(f"[{s['comp_in']},{s['comp_out']}){'N' if s['type'] != 'raw' else ''}"
                                  for s in sorted(segs.values(), key=lambda s: s["comp_in"]))[:160]])
    assert not rows, _table(f"{group}: editor clips not reproduced as single segments:",
                            ["truth seg", "kind", "truth range", "cutlist segments"], rows)


@pytest.mark.parametrize("group", _film_params("framing", [g for g in FILM_GROUPS if g != "gray"]))
def test_film24_framing_vs_truth(e2e, cutlist, group):
    """c4 against the TRUTH framing: on every frame the cutlist's (AE-interpolated) framing matches the truth
    Sim of that frame within 1 % scale / 4 px (RAW point at the box centre) and has no rotation (> 0.2 deg);
    frames covered by a placeholder fail."""
    _need_film()
    truth = e2e["truth"]
    box = truth["layout"]["box"]
    centre = (box["x"] + box["w"] / 2, box["y"] + box["h"] / 2)
    rows = []
    for t in _group_segments(truth, group):
        worst, uncovered = (0.0, 0.0, 0.0, None), []
        for k in range(t["comp_in"], t["comp_out"]):
            ts = truth["frames"][k]["sim"]
            cov = [s for s in _covering(cutlist, k) if s["type"] == "raw" and s.get("transform")]
            if not cov:
                uncovered.append(k)
                continue
            cs = _sim_at(cov[0], k)
            dp = float(np.linalg.norm(_apply(cs, _inverse_apply(ts, centre)) - np.asarray(centre)))
            ds = abs(cs["scale"] / ts["scale"] - 1)
            rot = abs(cs.get("rotation_deg", 0.0) - ts.get("rotation_deg", 0.0))
            if dp > worst[0] or ds > worst[1] or rot > worst[2]:
                worst = (max(dp, worst[0]), max(ds, worst[1]), max(rot, worst[2]), k)
        if worst[0] > 4.0 or worst[1] > 0.01 or worst[2] > 0.2:
            rows.append([t["id"], t["kind"], worst[3], f"pos {worst[0]:.2f} px, scale {100 * worst[1]:.3f} %, "
                         f"rot {worst[2]:.3f} deg", "+-4 px / 1 % / 0.2 deg"])
        if uncovered:
            rows.append([t["id"], t["kind"], f"{len(uncovered)} frames {uncovered[0]}..{uncovered[-1]}",
                         "no RAW segment / transform", ""])
    assert not rows, _table(f"{group}: framing differs from the truth Sim:", ["seg", "kind", "k", "error", "tol"],
                            rows)


@pytest.mark.parametrize("group", _film_params("speed"))
def test_film24_speed(e2e, cutlist, group):
    """Every RAW cutlist segment covering the group plays at the truth speed (snapped exactly: 1, 0.25, freeze
    0) -- no fake 0.667 / 2.0 / freeze from a time-vs-translation confound."""
    _need_film()
    truth = e2e["truth"]
    rows = []
    for t in _group_segments(truth, group):
        core = _clip_core(truth, t)[2]
        for s in {id(s): s for k in core for s in _covering(cutlist, k)}.values():
            if s["type"] == "raw" and abs(float(s["speed"]) - t["speed"]) > 1e-9:
                rows.append([t["id"], t["kind"], t["speed"], f"S{s.get('id')} [{s['comp_in']},{s['comp_out']})",
                             s["speed"]])
    assert not rows, _table(f"{group}: segment speed differs from the truth:",
                            ["truth seg", "kind", "truth v", "segment", "v"], rows)


def _av_lag_truth(truth: dict) -> float:
    return float(truth["audio"]["av_offset"]["lag_ms"])


def _published_av_offset_ms(cutlist: dict) -> float | None:
    """The run-level A/V offset the cutlist publishes (FX-02: cutlist.audio.av_offset, xcorr convention lag_ms;
    a few spellings accepted until the schema settles). None when absent."""
    au = cutlist.get("audio") or {}
    off = au.get("av_offset")
    if isinstance(off, (int, float)):
        return float(off)
    if isinstance(off, dict):
        for key in ("lag_ms", "lag_ms_offset", "centre_ms", "center_ms", "value_ms"):
            if isinstance(off.get(key), (int, float)):
                return float(off[key])
    return None


def test_film24_av_offset_published(e2e, cutlist):
    """FX-02: the cutlist publishes ONE global A/V offset (measured, xcorr convention) whose interval contains the
    truth split delay (content 38 ms + post-edit 48 ms = competitor audio 86 ms late: lag -86 ms)."""
    _need_film()
    want = _av_lag_truth(e2e["truth"])
    av = (cutlist.get("audio") or {}).get("av_offset") or {}
    lo, hi = (av.get("lag_ms_interval") or [None, None])[:2]
    assert av.get("status") == "measured" and _published_av_offset_ms(cutlist) is not None, av
    assert lo is not None and lo <= want <= hi, f"published A/V offset interval {[lo, hi]} ms, truth {want} ms"


@film_xfail("FX-02: the published interval is [-86.6, -84.3] ms (2.4 ms, centre -85.4). It is already the "
            "intersection of the best possible bounds: of ALL film24 chains (placeholders and 3-5 frame pieces "
            "included) the tightest floor-interval edges lie 0.125 ms (S03 pan) and 1.250 ms (S05 pan_accel) from "
            "the truth, so with the av_offset_eps_ms = 0.5 ms measurement allowance per side no sound estimator "
            "gets below 2.375 ms or a centre closer than 0.56 ms; more eligible segments cannot help")
def test_film24_av_offset_precise(e2e, cutlist):
    """FX-02 with intact segmentation: the published offset is precise -- interval <= 2 ms wide and centre within
    0.5 ms of the truth (the real run's 34 strong segments gave a 0.4 ms interval)."""
    _need_film()
    want = _av_lag_truth(e2e["truth"])
    av = (cutlist.get("audio") or {}).get("av_offset") or {}
    lo, hi = (av.get("lag_ms_interval") or [None, None])[:2]
    got = _published_av_offset_ms(cutlist)
    assert lo is not None and hi - lo <= 2.0, f"published A/V offset interval {[lo, hi]} ms is wider than 2 ms"
    assert got is not None and abs(got - want) <= 0.5, f"published A/V offset {got} ms, truth {want} ms"


def test_film24_c5_with_measured_offset(e2e, verify):
    """c5 passes on the film24 audio once the measured A/V offset is applied (no 'confidently misaligned' segment,
    no D3 clamp warning 'the audio implies raw_in ... outside the video-feasible interval')."""
    _need_film()
    c5 = (verify.get("criteria") or {}).get("c5_audio") or {}
    assert c5.get("status") in OK, json.dumps(c5.get("details"), default=str)[:800]
    clamps = [w for w in _warnings(e2e) if "audio implies raw_in" in w]
    assert not clamps, clamps[:5]


def _warnings(e2e: dict) -> list[str]:
    rep = e2e["out"] / "report.md"
    text = rep.read_text() if rep.exists() else ""
    return [ln.strip() for ln in (text + "\n" + e2e["proc"].stdout).splitlines() if "audio implies raw_in" in ln]


def test_film24_jl_cuts_equal_truth(e2e, cutlist):
    """FX-09: the detected J/L cuts equal the truth exactly: the one genuine 6-frame L-cut (A.out = B.in = +6)
    and nothing else -- the uniform 48 ms post-edit switch delay is a baseline, not 1-2 frame L-cuts."""
    _need_film()
    truth = e2e["truth"]
    want = {d["cut"]: d["offset_frames"] for d in truth["audio"]["jl_cuts"]}
    rows = []
    for s in _segments(cutlist):
        a = s.get("audio") or {}
        out_off, in_off = int(a.get("out_offset_frames") or 0), int(a.get("in_offset_frames") or 0)
        if out_off != want.get(int(s["comp_out"]), 0):
            rows.append([f"S{s.get('id')} out", s["comp_out"], want.get(int(s["comp_out"]), 0), out_off])
        if in_off != want.get(int(s["comp_in"]), 0):
            rows.append([f"S{s.get('id')} in", s["comp_in"], want.get(int(s["comp_in"]), 0), in_off])
    assert not rows, _table("J/L offsets differ from truth:", ["segment", "cut", "truth", "cutlist"], rows)


@pytest.mark.parametrize("group", ["pan_step", "punch_pan", "line_across_shots"])
def test_film24_time_line_kept_as_one_line(e2e, cutlist, group):
    """FX-04 2 x FX-10 (DESIGN §7 D3 'time lines'): the layers of one truth time line split by a framing step, a
    punch-in or reframes at RAW-native shot changes are ONE time-tied group (Segment.time_line) and keep one line
    through the per-layer placement and the audio-informed phase: raw_in_i = raw_in_0 + v (comp_in_i - comp_in_0)
    / fps to the 9-decimal rounding (they used to drift apart by up to the interval width)."""
    _need_film()
    t = _group_segments(e2e["truth"], group)
    c0, c1 = int(t[0]["comp_in"]), int(t[-1]["comp_out"])
    cov = sorted({int(s["id"]): s for k in range(c0, c1) for s in _covering(cutlist, k) if s["type"] == "raw"}.values(),
                 key=lambda s: int(s["comp_in"]))
    rows = [[s["id"], s["comp_in"], s["comp_out"], s.get("speed"), s.get("raw_in_seconds"), s.get("time_line")]
            for s in cov]
    header = ["segment", "comp_in", "comp_out", "speed", "raw_in_s", "time_line"]
    assert len(cov) >= 2 and len({s.get("time_line") for s in cov}) == 1 and cov[0].get("time_line") is not None, \
        _table(f"{group} [{c0},{c1}): not one time-tied group", header, rows)
    cf = _fps(cutlist["competitor"]["fps"])
    s0 = cov[0]
    drift = [abs(Fraction(s["raw_in_seconds"]) - Fraction(s0["raw_in_seconds"])
                 - Fraction(s["speed"]) * Fraction(int(s["comp_in"]) - int(s0["comp_in"])) / cf) for s in cov]
    assert max(drift) <= Fraction(2, 10 ** 9), _table(f"{group}: members off their line by up to "
                                                       f"{float(max(drift)) * 1e3:.6f} ms", header, rows)


def _decode_mono(path: Path, sr: int = 16000) -> np.ndarray:
    from match_cuts.common import ffmpeg_bin
    res = subprocess.run([ffmpeg_bin(), "-v", "error", "-i", str(path), "-map", "0:a:0", "-ac", "1", "-ar", str(sr),
                          "-f", "f32le", "-"], capture_output=True, check=True)
    return np.frombuffer(res.stdout, np.float32)


def test_film24_continuous_audio_over_video_only_retimes(e2e, cutlist, verify):
    """FX-14: the frame-blend slow motion and the true freeze are VIDEO-only retimes -- the competitor's audio keeps
    playing at speed 1. Every cutlist segment over them carries an audio line (cutlist audio.line, speed 1) whose
    picture-synced RAW time is the truth's audio time + the content offset within 3 ms; the recreation has no silent
    gap there (preview audio RMS within 3 dB of the competitor's over the same audio, A/V offset applied) and c5
    measures the piece on its line (ok; a piece shorter than verify_audio_min_s alone stays the inconclusive
    too_short); the NOT-IN-RAW insert with foreign audio stays silent (no line, not_in_raw)."""
    _need_film()
    truth = e2e["truth"]
    cf = _fps(cutlist["competitor"]["fps"])
    content = float(truth["audio"]["av_offset"]["content_offset_ms"]) / 1000.0
    lag_s = float(((cutlist.get("audio") or {}).get("av_offset") or {}).get("lag_ms") or 0.0) / 1000.0
    sr = 16000
    comp_y = _decode_mono(Path(e2e["synthetic"]["competitor"]), sr)
    rec_y = _decode_mono(_need(e2e, "preview_recreation.mp4"), sr)

    def audio_time(k: int) -> float:          # RAW audio time the truth plays at comp frame k (speed-1 audio)
        t = next(x for x in truth["segments"] if x["comp_in"] <= k < x["comp_out"])
        return float(t["audio"]["raw_in_seconds"]) + float(Fraction(k - int(t["comp_in"])) / cf)

    def rms_db(y: np.ndarray, t0: float, t1: float) -> float:
        a, b = max(0, int(round(t0 * sr))), int(round(t1 * sr))
        return 10.0 * math.log10(float(np.mean(y[a:b].astype(np.float64) ** 2)) + 1e-12)

    c5 = {int(r["id"]): r for r in ((verify.get("criteria") or {}).get("c5_audio") or {}).get("details", {}).get(
        "segments", [])}
    rows = []
    for t in truth["segments"]:
        if t["audio"].get("mode") != "v1_video_only_retime":
            continue
        for s in {int(x["id"]): x for k in range(t["comp_in"], t["comp_out"]) for x in _covering(cutlist, k)}.values():
            line = (s.get("audio") or {}).get("line")
            r = c5.get(int(s["id"]), {})
            if not line or float(line["speed"]) != 1.0:
                rows.append([t["kind"], s["id"], "no speed-1 audio line", line, r.get("result")])
                continue
            k0, k1 = int(s["comp_in"]), int(s["comp_out"])
            err = (float(line["raw_in_seconds"]) - (audio_time(k0) + content)) * 1000.0
            t0, t1 = float(Fraction(k0) / cf), float(Fraction(k1) / cf)
            gap_db = rms_db(rec_y, t0, t1) - rms_db(comp_y, t0 - lag_s, t1 - lag_s)
            short = (k1 - k0) / float(cf) < 0.5
            c5_ok = r.get("audio_line") == line.get("id") and (
                r.get("result") == "ok" or (short and r.get("result") == "exception" and r.get("code") == "too_short"))
            if abs(err) > 3.0 or abs(gap_db) > 3.0 or not c5_ok:
                rows.append([t["kind"], s["id"], f"line off by {err:+.3f} ms, recreation {gap_db:+.1f} dB",
                             line.get("source"), f"c5 {r.get('result')} {r.get('code', '')} corr {r.get('corr')}"])
    foreign = next(t for t in truth["segments"] if t["kind"] == "foreign")
    for s in _covering(cutlist, (foreign["comp_in"] + foreign["comp_out"]) // 2):
        a = s.get("audio") or {}
        if a.get("line") or s["type"] != "not_in_raw" or a.get("exception") != "not_in_raw":
            rows.append(["foreign", s["id"], "foreign audio must stay silent", a.get("line"), a.get("exception")])
    assert not rows, _table("continuous audio over video-only retimes (FX-14):",
                            ["chain", "segment", "problem", "line", "c5"], rows)


def test_film24_dark_shot_matched_on_its_line(e2e, cutlist):
    """FX-08 guard: the dark low-texture shot inside the v = 1 line is shown by v = 1 RAW segments -- never a
    NOT-IN-RAW placeholder or a freeze."""
    _need_film()
    t = _group_segments(e2e["truth"], "line_across_shots")[1]
    assert t["kind"] == "line_dark"
    bad = [(s.get("id"), s["type"], s.get("speed")) for k in range(t["comp_in"], t["comp_out"])
           for s in _covering(cutlist, k) if s["type"] != "raw" or float(s["speed"]) != 1.0]
    assert not bad, f"dark shot [{t['comp_in']},{t['comp_out']}) covered by {sorted(set(bad))}"


def test_film24_no_fake_freeze(e2e, cutlist):
    """FX-08 guard: v = 0 only on the true freeze (incl. its ambiguous start); the blend slow motion and every other
    chain keep moving."""
    _need_film()
    truth = e2e["truth"]
    fz = _group_segments(truth, "freeze")[1]
    lo, _ = _truth_cut_range(truth, fz["comp_in"])
    bad = [(s.get("id"), s["comp_in"], s["comp_out"]) for s in cutlist["segments"]
           if s["type"] == "raw" and float(s["speed"]) == 0.0 and not (lo <= int(s["comp_in"]) and
                                                                     int(s["comp_out"]) <= fz["comp_out"])]
    assert not bad, f"v = 0 segments outside the true freeze [{lo},{fz['comp_out']}): {bad}"


def test_film24_true_freeze_is_v0(e2e, cutlist):
    """The true 10-frame freeze (under an animated caption) stays a freeze: its frames are covered by v = 0
    (or remap-hold) segments showing the held RAW frame."""
    _need_film()
    truth = e2e["truth"]
    fz = _group_segments(truth, "freeze")[1]
    cf, rf = _fps(cutlist["competitor"]["fps"]), _fps(cutlist["raw"]["fps"])
    rows = []
    for k in range(fz["comp_in"], fz["comp_out"]):
        cov = _covering(cutlist, k)
        if len(cov) != 1 or (float(cov[0]["speed"]) != 0.0 and not cov[0].get("time_remap_keys")) or \
                _ae_frame_at(cov[0], k, cf, rf) != truth["frames"][k]["raw_a"]:
            rows.append([k, [(s.get("id"), s["type"], s.get("speed")) for s in cov]])
    assert not rows, rows


def test_film24_foreign_lookalike_never_matched(e2e, cutlist, frame_map):
    """The NOT-IN-RAW lookalike (gray-zone ZNCC against its RAW model shot) is a placeholder and never MATCH."""
    _need_film()
    truth = e2e["truth"]
    t = next(s for s in truth["segments"] if s.get("lookalike_shot") is not None)
    bad = [k for k in range(t["comp_in"], t["comp_out"])
           if any(s["type"] == "raw" for s in _covering(cutlist, k)) or int(frame_map["status"][k]) == S_MATCH]
    assert not bad, f"lookalike frames matched to RAW: {bad}"


@film_xfail("FX-08: the sharpened gray-zone chain becomes a NOT-IN-RAW placeholder")
def test_film24_gray_chain_never_not_in_raw(e2e, cutlist):
    """FX-08: the gray-zone chain (truth ZNCC 0.65-0.90: motion-blurred RAW, sharpened competitor) is never a
    NOT-IN-RAW placeholder: matched (detail score) or an honest 'uncertain' segment."""
    _need_film()
    t = _group_segments(e2e["truth"], "gray")[0]
    bad = [k for k in range(t["comp_in"], t["comp_out"])
           if any(s["type"] == "not_in_raw" for s in _covering(cutlist, k))]
    assert not bad, f"gray chain frames under a NOT-IN-RAW placeholder: {bad}"


@film_xfail("FX-08: the one cut left inside a repeat pair is 444|445, the edge of the anchorless first two-clip pan "
            "(NOT-IN-RAW placeholder | 1-frame island; refine's comp labels there are UNKNOWN, so the comp-duplicate "
            "invariant has no repeat pair to act on) -- needs the line-constrained search")
def test_film24_no_cut_inside_repeat_pair(e2e, cutlist):
    """FX-07: no time cut between the two frames of a competitor pulldown repeat pair (24 -> 30 cadence), except
    where the truth itself cuts (a framing step may sit there)."""
    _need_film()
    truth = e2e["truth"]
    truth_cuts = {c["k"] for c in truth["cuts"]}
    got = {int(s["comp_in"]) for s in cutlist["segments"]}
    bad = [p for p in truth["pulldown"]["pairs"] if p[1] in got and p[1] not in truth_cuts]
    assert not bad, f"{len(bad)} cuts inside repeat pairs: {bad[:20]}"


def test_film24_verify_flags_every_wrong_frame(e2e, cutlist, verify):
    """FX-01: every RAW-segment frame whose AE frame differs from the truth is reported by verification (s9_3
    failed frames, s9_2 mismatches, s9_2b temporal-signature disagreements (both frames of the pair) or s9_2c
    +-1 refit neighbour wins) -- a wrong RAW frame hidden behind a compensating shift must not pass."""
    _need_film()
    truth = e2e["truth"]
    cf, rf = _fps(cutlist["competitor"]["fps"]), _fps(cutlist["raw"]["fps"])
    checks = verify.get("checks") or {}
    flagged = set(int(k) for k in (checks.get("s9_3_visual") or {}).get("failed_frames") or [])
    s92 = checks.get("s9_2_ae_sim") or {}
    for src in ("plan", "mock"):
        for m in ((s92.get(src) or {}).get("mismatches") or []):
            if m.get("k") is not None:
                flagged.add(int(m["k"]))
    for d in ((checks.get("s9_2b_temporal") or {}).get("disagreements") or []):
        flagged.update((int(d["k"]), int(d["k"]) + 1))
    for d in ((checks.get("s9_2c_refit") or {}).get("neighbour_wins") or []):
        flagged.add(int(d["k"]))
    wrong = []
    for fr in truth["frames"]:
        if fr["raw_a"] is None or fr["class"] not in ("exact", "static"):
            continue
        cov = [s for s in _covering(cutlist, fr["k"]) if s["type"] == "raw"]
        if cov and _ae_frame_at(cov[0], fr["k"], cf, rf) != fr["raw_a"]:
            wrong.append(fr["k"])
    missed = [k for k in wrong if k not in flagged]
    assert not missed, f"{len(missed)} of {len(wrong)} wrong RAW frames pass verification: {missed[:30]}"
