"""End-to-end synthetic test (prompt Stage 1.3, DESIGN.md §6 last paragraph).

Runs the real CLI on the synthetic RAW + competitor made by tests/synth.py (profile 'mini' by default,
'full' with MATCH_CUTS_PROFILE=full) and asserts that the pipeline recovers the known edit exactly:

* FrameMap m(k) (``<work>/frame_map.npz``) == truth on EVERY matchable frame
* cuts +-0 frames (speed-only cuts: truth inside ``cut_ambiguity``), segment boundaries exact
* AE-simulated RAW frames (from the cutlist's raw_in / speed / comp_in, AE floor rule) == truth, except
  listed timing-tie frames
* speed +-0.5 % AND snapped to the truth value; flip; framing +-1 % scale / +-4 px (every frame, incl. the
  push-in keys); rotation 0
* crossfade (O, D=6); NOT-IN-RAW placeholder range exact; coverage
* ``<out>/verify.json``: c1-c5 in {pass, pass_with_exceptions}, c6 == pass (mock), s9_7 pass
* audio truth (offsets 0, pitch not preserved on the 1.10x segment, NOT-IN-RAW exception, music added)
* layout truth (box, background, static zones, captions) within lenient tolerances
* a second CLI run (same work dir) gives a byte-identical cutlist.json

Failures print precise, actionable tables (which frames / segments differ and how).
Slow: run with ``--runslow`` or ``MATCH_CUTS_SLOW=1``.
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
TOOL_DIR = Path(__file__).resolve().parents[1]
CLI_TIMEOUT_S = 3 * 3600 if PROFILE == "full" else 3600
OK = ("pass", "pass_with_exceptions")

# FrameMap status codes (model.Status)
S_NONE, S_MATCH, S_BLEND, S_UNIFORM = 0, 1, 2, 3


# ------------------------------------------------------------------------------------------------------
# fixtures
# ------------------------------------------------------------------------------------------------------

@pytest.fixture(scope="module")
def synthetic(request) -> dict:
    if PROFILE not in ("mini", "full"):
        raise ValueError(f"MATCH_CUTS_PROFILE must be mini or full, not {PROFILE!r}")
    return request.getfixturevalue(f"synthetic_{PROFILE}")


def _run_cli(py: str, syn: dict, out: Path, work: Path) -> subprocess.CompletedProcess:
    cmd = [py, "-m", "match_cuts", "--competitor", syn["competitor"], "--raw", syn["raw"],
           "--out", str(out), "--work", str(work)]
    return subprocess.run(cmd, cwd=str(TOOL_DIR), capture_output=True, text=True, timeout=CLI_TIMEOUT_S)


@pytest.fixture(scope="module")
def e2e(venv_python, synthetic, tmp_path_factory) -> dict:
    root = tmp_path_factory.mktemp(f"e2e_{PROFILE}")
    out, work = root / "output", root / "work"
    t0 = time.perf_counter()
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


def _match_segments(truth: dict, cutlist: dict) -> tuple[dict[int, dict], list[dict]]:
    """truth segment id -> cutlist segment with the same comp_in and type; unmatched truth segments."""
    by_in: dict[int, list[dict]] = {}
    for s in cutlist["segments"]:
        by_in.setdefault(int(s["comp_in"]), []).append(s)
    matched, missing = {}, []
    for t in truth["segments"]:
        cands = [s for s in by_in.get(t["comp_in"], []) if (s["type"] == "not_in_raw") == (t["type"] == "not_in_raw")]
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

def test_cli_succeeds(e2e):
    proc = e2e["proc"]
    assert proc.returncode == 0, f"CLI exit code {proc.returncode} after {e2e['elapsed']:.0f}s{_tail(proc)}"


def test_coverage_and_totals(e2e, cutlist):
    truth = e2e["truth"]
    n = truth["competitor"]["frames"]
    assert int(cutlist["competitor"]["frames"]) == n, "cutlist competitor frame count != truth"
    assert int(cutlist["raw"]["frames"]) == truth["raw"]["frames"], "cutlist RAW frame count != truth"
    assert _fps(cutlist["competitor"]["fps"]) == Fraction(30) and _fps(cutlist["raw"]["fps"]) == Fraction(30000, 1001)
    cover = np.zeros(n, np.int32)
    for s in cutlist["segments"]:
        cover[int(s["comp_in"]):int(s["comp_out"])] += 1
    tr = truth["transitions"][0]
    allowed = np.ones(n, np.int32)
    allowed[tr["O"]:tr["O"] + tr["D"]] = 2
    bad = np.nonzero(cover != allowed)[0]
    rows = [[int(k), int(cover[k]), int(allowed[k])] for k in bad]
    assert not rows, _table("coverage differs from truth (1 layer per frame, 2 in the crossfade overlap):",
                            ["comp_frame", "layers", "expected"], rows)


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
        n_checked += 1
        if st != S_MATCH or got != fr["raw_a"]:
            rows.append([k, fr["seg"], kinds[fr["seg"]], fr["raw_a"], got, st,
                         f"off by {got - fr['raw_a']:+d}" if st == S_MATCH else "not MATCH"])
    assert n_checked > 0.9 * n
    assert not rows, _table(f"FrameMap m(k) != truth on {len(rows)} frames (of {n_checked} matchable):",
                            ["k", "seg", "kind", "truth", "m(k)", "status", "note"], rows)


def test_cuts_exact(e2e, cutlist):
    truth = e2e["truth"]
    segs = _segments(cutlist)
    got_cuts = {int(s["comp_in"]): s for s in segs[1:]}
    want = {c["k"]: c for c in truth["cuts"]}
    rows = []
    for k, c in sorted(want.items()):
        if k in got_cuts:
            continue
        amb = [s.get("cut_ambiguity") for s in segs if s.get("cut_ambiguity")]
        if any(a[0] <= k <= a[1] for a in amb):
            continue
        near = sorted(got_cuts, key=lambda x: abs(x - k))[:1]
        rows.append([k, c["type"], c["b_kind"], "missing", f"nearest reported cut {near[0]}" if near else "-"])
    for k, s in sorted(got_cuts.items()):
        if k not in want:
            rows.append([k, "-", "-", "spurious", f"cutlist segment {s.get('id')} type {s.get('type')}"])
    assert not rows, _table("cut positions differ from truth (+-0 frames required):",
                            ["comp_frame", "truth type", "truth kind", "problem", "detail"], rows)
    matched, missing = _match_segments(truth, cutlist)
    rows = [[t["id"], t["kind"], f"[{t['comp_in']},{t['comp_out']})",
             f"[{matched[t['id']]['comp_in']},{matched[t['id']]['comp_out']})"]
            for t in truth["segments"] if t["id"] in matched and int(matched[t["id"]]["comp_out"]) != t["comp_out"]]
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
            if k not in sim:
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
        if abs(v / vt - 1) > 0.005:
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


def test_not_in_raw_placeholder(e2e, cutlist):
    truth = e2e["truth"]
    want = [(r["comp_in"], r["comp_out"]) for r in truth["not_in_raw"]]
    got = [(int(s["comp_in"]), int(s["comp_out"])) for s in _segments(cutlist) if s["type"] == "not_in_raw"]
    assert got == want, f"NOT-IN-RAW placeholders {got} != truth {want}"


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
        if t["type"] == "raw" and t["speed"] != 1.0 and a.get("pitch_preserved") is not False:
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
    want = np.zeros(n, bool)
    for c in truth["captions"]:
        want[c["k_in"]:c["k_out"]] = True
    got = np.zeros(n, bool)
    for c in (lay.get("captions") or []) + [o for o in cutlist.get("overlays_detected", [])
                                            if "caption" in str(o.get("type", ""))]:
        got[int(c["comp_in"]):int(c["comp_out"])] = True
    recall = (want & got).sum() / max(1, want.sum())
    if recall < 0.8:
        rows.append(["captions", f"{want.sum()} frames", f"recall {recall:.0%}", ">= 80 % of caption frames"])
    assert not rows, _table("layout differs from truth:", ["field", "truth", "cutlist", "tolerance"], rows)


def test_second_run_identical_cutlist(e2e, cutlist):
    """Criterion 9.7: a second CLI run (same inputs, same work dir -> caches) writes a byte-identical
    cutlist.json. Wall-clock provenance.timings is the only field allowed to differ (DESIGN §1)."""
    first = _need(e2e, "cutlist.json").read_bytes()
    out2 = e2e["root"] / "output_run2"
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
