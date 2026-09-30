"""Fast unit tests of the synthetic-data generator (tests/synth.py): ID code, timing chains measured on a
tiny ID video (incl. a 1.10x segment and a 6-frame xfade), geometry calibration helper, layout/captions,
and the consistency of both profiles' edit plans. Runs in well under 60 s."""
from __future__ import annotations

import math
from fractions import Fraction

import numpy as np
import pytest

import synth as S


# ------------------------------------------------------------------------------------------------------
# ID code
# ------------------------------------------------------------------------------------------------------

def test_id_code_round_trip_and_validity():
    rng = np.random.default_rng(3)
    codes = [0, 1, 2, 3, 255, 256, 4095, 12345, 32768, 65535] + [int(x) for x in rng.integers(0, 1 << 16, 40)]
    frames = np.stack([S.id_frame(c) for c in codes])
    left, right = S.decode_id_frames(frames)
    assert left.tolist() == codes and right.tolist() == codes

    # halves are decoded independently (xfade custom split: left = A, right = B)
    mixed = S.id_frame(1234).copy()
    mixed[:, 256:] = S.id_frame(4321)[:, 256:]
    l, r = S.decode_id_frames(mixed)
    assert (l[0], r[0]) == (1234, 4321)

    # complement row broken -> invalid
    bad = S.id_frame(777).copy()
    bad[32:, 16 * 3:16 * 4] = bad[:32, 16 * 3:16 * 4]          # bottom == top for bit 3
    assert S.decode_id_frames(bad)[0][0] == -1

    # a blend of two codes (a real crossfade frame) and a code-less grey frame are invalid
    blend = ((S.id_frame(100).astype(np.float32) + S.id_frame(200)) / 2).astype(np.uint8)
    assert S.decode_id_frames(blend)[0][0] == -1
    grey = np.full((S.ID_H, S.ID_W), 128, np.uint8)
    assert S.decode_id_frames(grey) == (np.array([-1]), np.array([-1]))
    with pytest.raises(ValueError):
        S.id_frame(1 << 16)


# ------------------------------------------------------------------------------------------------------
# Timing chains on a tiny ID video
# ------------------------------------------------------------------------------------------------------

def _independent_expected(j: int, speed: str, n_out: int, phase: float) -> list[int]:
    """Re-implementation (not using synth) of ffmpeg's -ss/setpts/fps=30/trim behaviour: source frame i
    (PTS i*1001 ticks of 1/30000 s after STARTPTS) gets trunc(i*1001/v + phase); fps rounds it to the
    1/30 grid (half away from zero); output frame n shows the last source frame whose slot is <= n."""
    v = Fraction(speed)
    slots = []
    for i in range(int(n_out * float(v)) + 10):
        p = math.floor(Fraction(1001 * i) / v + Fraction(phase))
        slots.append(math.floor(Fraction(p, 1000) + Fraction(1, 2)))
    return [j + max(i for i, s in enumerate(slots) if s <= n) for n in range(n_out)]


def _chain(index: int, j: int, speed: str, n: int, xfade: int = 0) -> S.Chain:
    c = S.Chain(index, S.ChainSpec("t", shot=0, off=j, n=n, speed=speed, xfade=xfade))
    c.j, c.phase, c.ss = j, S.choose_phase(speed, n), S.ss_seconds(j)
    c.timing = S.timing_filters(speed, n, c.phase)
    c.expected = S.expected_chain_frames(j, speed, n, c.phase)
    return c


@pytest.fixture(scope="module")
def id_videos(tmp_path_factory):
    d = tmp_path_factory.mktemp("idv")
    idv = d / "id.mp4"
    S.make_id_video(idv, 400)
    # a lossy 'raw-like' copy (B-frames, long GOP): the -ss selection must be identical to the lossless ID
    lossy = d / "id_lossy.mp4"
    S.run_ffmpeg(["-y", "-i", str(idv), "-c:v", "libx264", "-preset", "veryfast", "-crf", "16", "-bf", "3",
                  "-g", "250", "-threads", "2", "-pix_fmt", "yuv420p", "-video_track_timescale", "30000",
                  str(lossy)])
    return idv, lossy


def test_id_video_is_exact(id_videos):
    idv, lossy = id_videos
    pts, tb = S.decode_pts(idv)
    assert tb == Fraction(1, 30000) and pts == [1001 * i for i in range(400)]
    frames = S.decode_gray(idv, keep=lambda i: i in (0, 1, 199, 399))
    for i, img in frames.items():
        assert np.array_equal(img, S.id_frame(i)), f"ID frame {i} is not bit-exact"
    pl, tbl = S.decode_pts(lossy)
    assert [Fraction(p) * tbl for p in pl] == [Fraction(p) * tb for p in pts]


def test_tiny_chain_truth_equals_expected(id_videos):
    """3 chains: speed 1, speed 1.10 (sub-frame setpts phase), speed 1 -- the last two joined by a 6-frame
    xfade. The ID-measured truth must equal the model, an independent re-implementation, and the same
    chains on a lossy long-GOP copy; the composite has the xfade frame count and left=A / right=B codes."""
    idv, lossy = id_videos
    chains = [_chain(0, 12, "1", 20), _chain(1, 150, "1.1", 30, xfade=6), _chain(2, 260, "1", 24)]
    assert chains[0].phase == 0.0 and chains[1].phase != 0.0
    tie, trunc = S.phase_margins("1.1", 30, chains[1].phase)
    assert tie >= 2 and trunc >= 0.01
    for c in chains:
        c.frames = S.decode_id_chain(idv, c)
        assert c.frames.tolist() == c.expected.tolist(), f"chain {c.index}: measured != model"
        assert c.frames.tolist() == _independent_expected(c.j, c.spec.speed, c.spec.n, c.phase)
        assert S.decode_id_chain(lossy, c).tolist() == c.frames.tolist(), "lossy source selects other frames"
        assert c.frames[0] == c.j
        a, b = S.floor_interval(c.frames, c.spec.speed)        # one linear AE floor-rule map exists
        assert a < b
    steps = np.diff(chains[1].frames)
    assert set(steps.tolist()) <= {1, 2} and (steps == 2).any()        # 1.10x skips frames
    assert set(np.diff(chains[0].frames).tolist()) <= {0, 1}

    # whole edit on the ID video: concat + xfade (custom split) -> O + len(B) frames
    left, right = S.id_composite(idv, chains)
    O = chains[0].spec.n + chains[1].spec.n - 6
    assert len(left) == O + chains[2].spec.n == 68
    exp_l = chains[0].frames.tolist() + chains[1].frames.tolist() + chains[2].frames[6:].tolist()
    exp_r = chains[0].frames.tolist() + chains[1].frames[:24].tolist() + chains[2].frames.tolist()
    assert left.tolist() == exp_l
    assert right.tolist() == exp_r
    # crossfade window [O, O+6): left = A (chain 1), right = B (chain 2, frames 0..5)
    assert right[O:O + 6].tolist() == chains[2].frames[:6].tolist()
    assert left[O:O + 6].tolist() == chains[1].frames[24:30].tolist()


def test_speed_ties_need_a_phase():
    """At 1.10x with no setpts phase exact rounding ties occur (ffmpeg resolves them either way in double
    precision); the chosen half-integer phase removes every tie."""
    assert S.phase_margins("1.1", 300, 0.0)[0] == 0
    ph = S.choose_phase("1.1", 300)
    assert ph % 1 == 0.5 and S.phase_margins("1.1", 300, ph)[0] >= 2
    assert S.choose_phase("1", 120) == 0.0
    f = S.expected_chain_frames(0, "1.1", 300, ph)
    assert abs((f[-1] - f[0]) / 299 - 1.1 * 1000 / 1001) < 0.01


# ------------------------------------------------------------------------------------------------------
# Geometry calibration helper
# ------------------------------------------------------------------------------------------------------

@pytest.fixture(scope="module")
def calib(tmp_path_factory):
    d = tmp_path_factory.mktemp("calib")
    png = d / "calib.png"
    tex = S.make_calibration_texture(png, 320, 180, seed=5, blur=3.0)
    return tex, png


def test_geometry_verification_known_transforms(calib):
    tex, png = calib
    # flip + near-isotropic scale + ODD crop offsets (exact=1) + perspective push-in z = 1 + 0.04 n
    g = S.Geometry(320, 180, True, 256, 144, 41, 7, 160, 130, 10, 20, push_a=0.04)
    r = S.verify_geometry(g, tex, png, 6)
    assert r["ok"], r
    assert r["max_dpos"] < S.CALIB_POS_TOL and r["max_ds"] < S.CALIB_SCALE_TOL
    # punch-in step: z = 1 for n < 3, 1.25 after
    g2 = S.Geometry(320, 180, False, 256, 144, 40, 6, 160, 130, 10, 20, punch_at=3, punch_zoom=1.25)
    r2 = S.verify_geometry(g2, tex, png, 5)
    assert r2["ok"], r2
    sims = [g2.truth_sim(n)["scale"] for n in range(5)]
    assert sims[0] == sims[2] and abs(sims[3] / sims[2] - 1.25) < 1e-9


def test_geometry_verification_rejects_wrong_truth(calib):
    tex, png = calib
    g = S.Geometry(320, 180, False, 256, 144, 41, 7, 160, 130, 10, 20, push_a=0.04)

    def shifted(n):                         # 1 px position error
        s = g.truth_sim(n)
        return {**s, "tx": s["tx"] + 1.0}

    def in_one_based(n):                    # perspective `in` taken as 0-based (one-frame zoom error)
        return S.Geometry(320, 180, False, 256, 144, 41, 7, 160, 130, 10, 20, push_a=0.04).truth_sim(n + 1)

    assert not S.verify_geometry(g, tex, png, 6, truth=shifted)["ok"]
    assert not S.verify_geometry(g, tex, png, 6, truth=in_one_based)["ok"]
    # ffmpeg perspective maps pixel INDICES through the quad: at z = 2 the content moves by -(z-1)/2 = -0.5 px
    g2 = S.Geometry(320, 180, False, 256, 144, 41, 7, 160, 130, 10, 20, push_a=0.2)

    def no_persp_offset(n):
        z, s = g2.zoom(n), g2.truth_sim(n)
        return {**s, "tx": s["tx"] + (z - 1) / 2, "ty": s["ty"] + (z - 1) / 2}

    assert S.verify_geometry(g2, tex, png, 6, frames=[5])["ok"]
    assert not S.verify_geometry(g2, tex, png, 6, frames=[5], truth=no_persp_offset)["ok"]
    # crop without exact=1 semantics (odd offset rounded down to even) must be detected
    g_odd = S.Geometry(320, 180, False, 256, 144, 41, 7, 160, 130, 10, 20)
    g_even = S.Geometry(320, 180, False, 256, 144, 40, 6, 160, 130, 10, 20)
    assert S.verify_geometry(g_odd, tex, png, 2)["ok"]
    assert not S.verify_geometry(g_odd, tex, png, 2, truth=g_even.truth_sim)["ok"]
    # flip convention x' = W - x: hflip output vs a truth that forgets the flip must fail
    g_flip = S.Geometry(320, 180, True, 256, 144, 41, 7, 160, 130, 10, 20)
    g_noflip = S.Geometry(320, 180, False, 256, 144, 41, 7, 160, 130, 10, 20)
    assert S.verify_geometry(g_flip, tex, png, 2)["ok"]
    import cv2
    try:
        mirrored_ok = S.verify_geometry(g_noflip, tex, png, 2, filters=g_flip.filters())["ok"]
    except cv2.error:                  # ECC does not even converge on mirrored content
        mirrored_ok = False
    assert not mirrored_ok


def test_lsq_similarity_of_anisotropic_scale():
    A = S._T(5.0, -3.0) @ S._D(0.93, 0.92)
    sim = S.lsq_similarity(A, (0, 0, 400, 100))
    assert sim["rotation_deg"] == 0.0
    assert 0.92 < sim["scale"] < 0.93 and abs(sim["scale"] - 0.93) < abs(sim["scale"] - 0.92)  # wide region
    iso = S.lsq_similarity(S._T(7.5, 2.0) @ S._D(1.1, 1.1), (0, 0, 50, 50))
    assert iso == {"scale": pytest.approx(1.1), "rotation_deg": 0.0, "tx": pytest.approx(7.5), "ty": pytest.approx(2.0)}


# ------------------------------------------------------------------------------------------------------
# Layout and captions
# ------------------------------------------------------------------------------------------------------

def test_layout_frame_and_caption_measurement(tmp_path):
    p = S.PROFILES["mini"]
    lp = S.layout_plan(p)
    png = tmp_path / "frame.png"
    S.render_frame_png(png, lp)
    zones = S.measure_zones(png, lp)                 # also asserts the alpha hole == analytic rounded box
    types = {z["type"] for z in zones}
    assert types == {"logo", "channel_name", "title", "watermark"}
    bx, by, bw, bh, _ = p.box
    for z in zones:
        assert z["w"] > 4 and z["h"] > 4
        inside = z["x"] < bx + bw and z["x"] + z["w"] > bx and z["y"] < by + bh and z["y"] + z["h"] > by
        assert not inside, f"static zone {z} overlaps the video box"
    caps = [{"text": "HELLO", "k_in": 2, "k_out": 9}, {"text": "WORLD", "k_in": 9, "k_out": 15},
            {"text": "AGAIN", "k_in": 20, "k_out": 27}]
    got = S.measure_captions(caps, lp, 30)
    assert [(c["k_in"], c["k_out"]) for c in got] == [(2, 9), (9, 15), (20, 27)]
    for c in got:
        assert bx <= c["x"] and c["x"] + c["w"] <= bx + bw and by <= c["y"] and c["y"] + c["h"] <= by + bh
    planned = S.plan_captions(p, 546, p.caption_seed)
    assert all(a["k_out"] <= b["k_in"] for a, b in zip(planned[:-1], planned[1:]))
    assert planned[-1]["k_out"] <= 546 and len(planned) >= 20


# ------------------------------------------------------------------------------------------------------
# Edit plans of both profiles
# ------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("name", ["mini", "full"])
def test_profile_plan_has_every_required_feature(name):
    p = S.PROFILES[name]
    chains = S.resolve_chains(p)
    kinds = [c.spec.kind for c in chains]
    for k in ("hook", "flip", "pushin", "speed", "crossfade", "reuse", "not_in_raw", "punchin"):
        assert kinds.count(k) == 1, f"{name}: feature {k} x{kinds.count(k)}"
    assert kinds.count("jump_cut") == 2
    n_comp = sum(c.spec.n - c.spec.xfade for c in chains)
    lo, hi = (50 * 30, 60 * 30) if name == "full" else (15 * 30, 25 * 30)
    assert lo <= n_comp <= hi
    truth_segments = len(chains) + 1                                 # punch-in chain -> two segments
    if name == "full":
        assert 19 <= truth_segments - 1 <= 22                         # ~20 cuts
    raw_ch = [c for c in chains if c.in_raw]
    # hook: first segment, from later in RAW than what follows
    assert chains[0].spec.kind == "hook" and chains[0].j > max(c.j for c in raw_ch[1:4])
    # jump cuts: same shot as the previous chain, skipping >= 3 RAW frames
    for i, c in enumerate(chains):
        if c.spec.kind == "jump_cut":
            prev = chains[i - 1]
            assert prev.spec.shot == c.spec.shot and c.expected[0] - prev.expected[-1] >= 4
        if c.spec.kind == "speed":
            assert c.spec.speed == "1.1"
            for nb in (chains[i - 1], chains[i + 1]):
                gap = min(abs(int(c.expected[0]) - int(nb.expected[-1])), abs(int(nb.expected[0]) - int(c.expected[-1])))
                assert gap >= 3
        if c.spec.kind == "reuse":
            used = {int(x) for d in chains[:i] if d.in_raw for x in d.expected}
            assert len(used & {int(x) for x in c.expected}) >= c.spec.n // 2
        if c.spec.kind == "crossfade":
            assert chains[i - 1].spec.xfade == 6
        if c.spec.kind == "not_in_raw":
            assert c.spec.n == 30                                     # 1 s
        if c.in_raw and "mandelbrot" in S.SHOTS[c.spec.shot].tags:
            assert not (c.spec.flip or c.spec.push_end or c.spec.punch_at)
    # every shot is a distinct generator configuration
    assert len({S.shot_graph(p, s) for s in S.SHOTS}) == len(S.SHOTS) == p.n_shots
    # safe region holds the counter and the grid
    lp = S.layout_plan(p)
    band = (lp.caption_y - p.u(30), lp.caption_y + lp.caption_fs + p.u(40))
    safe = S.safe_region(p, [(c.spec, c.geom) for c in raw_ch], band)
    ov = S.plan_overlays(p, safe)
    gx, gy, gw, gh = ov.grid
    assert safe[0] <= gx and gx + gw <= safe[2] and safe[1] <= gy and gy + gh <= safe[3]
    # push-in truth: exactly two linear keys; punch-in: step at punch_at
    push = next(c for c in chains if c.spec.kind == "pushin")
    s0, s1 = push.geom.truth_sim(0), push.geom.truth_sim(push.spec.n - 1)
    assert abs(s1["scale"] / s0["scale"] - push.spec.push_end) < 1e-9
    mid = push.geom.truth_sim(push.spec.n // 2)
    u = (push.spec.n // 2) / (push.spec.n - 1)
    assert abs(mid["tx"] - (s0["tx"] + u * (s1["tx"] - s0["tx"]))) < 1e-6
