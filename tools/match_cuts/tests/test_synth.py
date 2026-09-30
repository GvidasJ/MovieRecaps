"""Fast unit tests of the synthetic-data generator (tests/synth.py): ID code, timing chains measured on a
tiny ID video (incl. a 1.10x segment and a 6-frame xfade), geometry calibration helper, layout/captions,
the fullscreen chain (canvas-size geometry, composite with the glyph layer), the NLE audio in-point
(DESIGN §7 D8), the caption-recall helper of test_synthetic.py, and the consistency of both profiles' edit
plans. Runs in well under 60 s."""
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
    for k in ("hook", "flip", "pushin", "speed", "crossfade", "reuse", "not_in_raw", "punchin", "fullscreen"):
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
    # fullscreen (D8): ~1 s of plain RAW cover-scaled to the WHOLE canvas, hard cuts on both sides
    fs = [c for c in chains if c.spec.kind == "fullscreen"]
    assert len(fs) == 1
    c = fs[0]
    g = c.geom
    assert (g.bx, g.by, g.bw, g.bh) == (0, 0, p.comp_w, p.comp_h)
    assert S.chain_box(p, c.spec) == (0, 0, p.comp_w, p.comp_h)
    assert g.sw >= p.comp_w and g.sh >= p.comp_h and min(g.sw - p.comp_w, g.sh - p.comp_h) <= 4   # cover scale
    assert not (g.flip or g.animated) and c.spec.speed == "1" and 27 <= c.spec.n <= 33
    assert abs(g.truth_sim(0)["scale"] / (p.comp_h / p.raw_h) - 1) < 0.003
    x0, y0, x1, y1 = S.visible_raw_rect(g, 0)
    assert -0.01 <= x0 and x1 <= p.raw_w + 0.01 and -0.01 <= y0 and y1 <= p.raw_h + 0.01
    assert ov.grid[0] >= x0 and ov.grid[0] + ov.grid[2] <= x1          # the RAW counter/grid stay visible
    i = chains.index(c)
    for nb in (chains[i - 1], chains[i + 1]):
        assert nb.in_raw and not nb.spec.xfade and nb.spec.kind != "fullscreen" and nb.spec.shot != c.spec.shot
    assert not chains[i - 1].spec.xfade and not c.spec.xfade


# ------------------------------------------------------------------------------------------------------
# Fullscreen chain (DESIGN §7 D1/D8)
# ------------------------------------------------------------------------------------------------------

def test_fullscreen_geometry_calibration(tmp_path):
    """The canvas-size geometry of the fullscreen chain passes the calibration (ffmpeg scale+crop == own
    numpy truth), like every boxed chain."""
    p = S.PROFILES["mini"]
    c = next(c for c in S.resolve_chains(p) if c.spec.kind == "fullscreen")
    png = tmp_path / "calib.png"
    tex = S.make_calibration_texture(png, p.raw_w, p.raw_h)
    r = S.verify_geometry(c.geom, tex, png, 2)
    assert r["ok"], r


def _lavfi_chain(colour: str, w: int, h: int, n: int) -> list[str]:
    return ["-f", "lavfi", "-i", f"color=c={colour}:s={w}x{h}:r=30,trim=end_frame={n},format=yuv420p"]


def test_competitor_composite_fullscreen_and_glyph_layer(tmp_path):
    """Composite of the real competitor filtergraph on flat-colour chains (mini canvas): boxed chains are
    padded onto black, the crossfade stays in the box, the fullscreen chain covers the canvas, the static
    zones (glyph layer) are identical on boxed and fullscreen frames, and the fullscreen frames are
    measured back exactly."""
    import cv2
    p = S.PROFILES["mini"]
    lp = S.layout_plan(p)
    bx, by, bw, bh, _ = p.box
    specs = [S.ChainSpec("normal", shot=0, n=8, xfade=2), S.ChainSpec("crossfade", shot=1, n=8),
             S.ChainSpec("fullscreen", shot=2, n=6), S.ChainSpec("normal", shot=3, n=7)]
    colours = ["0x606060", "0x909090", "0xc8c8c8", "0x404040"]
    chains, k = [], 0
    for i, sp in enumerate(specs):
        chains.append(S.Chain(i, sp, comp_in=k))
        k += sp.n - sp.xfade
    n = k
    assert S.fullscreen_ranges(chains) == [(14, 20)]
    frame_png, glyph_png = tmp_path / "frame.png", tmp_path / "glyph.png"
    S.render_frame_png(frame_png, lp)
    S.render_glyph_png(glyph_png, lp)
    assert S.check_glyph_png(frame_png, glyph_png, lp)["max_diff"] <= 2
    zones = S.measure_zones(frame_png, lp)
    caps = [{"text": "HI", "k_in": 2, "k_out": 22}]
    args = []
    for sp, col in zip(specs, colours):
        w, h = (p.comp_w, p.comp_h) if sp.kind == "fullscreen" else (bw, bh)
        args += _lavfi_chain(col, w, h, sp.n)
    args += ["-loop", "1", "-framerate", "30", "-i", str(frame_png), "-loop", "1", "-framerate", "30", "-i",
             str(glyph_png)]
    out = tmp_path / "comp.nut"
    S.run_ffmpeg(["-y", *args, "-filter_complex", S.competitor_video_filter(p, chains, caps, lp), "-map", "[vout]",
                  *S.FFV1, str(out)])
    assert S.count_frames(out) == n
    got, frac = S.measure_fullscreen_frames(out, p, lp, zones)
    assert got == list(range(14, 20)), frac.round(3).tolist()
    fr = S.decode_gray(out)
    assert len(fr) == n
    side = (slice(300, 500), slice(2, 25))                     # background left of the box
    centre = (by + bh // 3, bx + bw // 2)                      # inside the box, above the caption
    for kk in range(n):
        bg = float(fr[kk][side].mean())
        if 14 <= kk < 20:
            assert abs(bg - 200) < 6 and abs(int(fr[kk][centre]) - 200) < 6, kk
        else:
            assert bg < 4, (kk, bg)
    assert abs(int(fr[0][centre]) - 96) < 6 and abs(int(fr[21][centre]) - 64) < 6
    assert 96 + 3 < int(fr[7][centre]) < 144 - 3                   # crossfade interior (O = 6, D = 2)
    # glyph pixels (opaque in the glyph layer) look the same on boxed and fullscreen frames
    gl = cv2.imread(str(glyph_png), cv2.IMREAD_UNCHANGED)
    opaque = gl[:, :, 3] == 255
    er = cv2.erode(opaque.astype(np.uint8), np.ones((3, 3), np.uint8)) > 0     # away from chroma edges
    d = np.abs(fr[3].astype(int) - fr[16].astype(int))[er]
    assert er.sum() > 1000 and d.max() <= 3, d.max()
    lx, ly, lr = lp.logo
    px = (ly, int(lx - 0.8 * lr))                                                # logo disc, off the letter
    assert abs(int(fr[3][px]) - int(fr[16][px])) <= 2 and 60 < int(fr[16][px]) < 130


# ------------------------------------------------------------------------------------------------------
# Audio in-point (DESIGN §7 D8)
# ------------------------------------------------------------------------------------------------------

def test_competitor_audio_starts_at_nle_in_point(tmp_path):
    """Each chain's competitor audio starts at the NLE in-point = the LOWER bound of its floor-rule raw_in
    interval (first sample at/after it), not at the interval centre (which hid the tool's quarter-frame
    audio bias); the rendered audio really is the RAW audio from that sample (xcorr lag 0)."""
    import soundfile as sf
    p = S.PROFILES["mini"]
    n_raw = 4 * S.AUDIO_SR
    raw = tmp_path / "raw.wav"
    S.run_ffmpeg(["-y", "-filter_complex", S.raw_audio_graph(n_raw), "-map", "[a]", "-c:a", "pcm_f32le", str(raw)])
    chains = [_chain(0, 12, "1", 20), _chain(1, 60, "1.1", 24)]
    chains[1].comp_in = 20
    for c in chains:
        c.frames = c.expected
    dst = tmp_path / "comp.wav"
    info = S.build_competitor_audio(p, chains, raw, dst, 44)
    ref = sf.read(str(raw), dtype="float64")[0]
    comp = sf.read(str(dst), dtype="float64")[0][:, 0]
    for c in chains:
        lo, hi = S.floor_interval(c.frames, c.spec.speed)
        s0 = info[c.index]["start_sample"]
        assert s0 == math.ceil(lo * S.AUDIO_SR) == S.audio_start_sample(c.frames, c.spec.speed)
        assert lo <= Fraction(s0, S.AUDIO_SR) < lo + Fraction(1, S.AUDIO_SR)
        assert info[c.index]["raw_in_seconds"] == s0 / S.AUDIO_SR
        centre = (lo + hi) / 2
        assert abs(s0 - float(centre) * S.AUDIO_SR) > 50           # the old convention is far away
    # speed-1 chain: the competitor audio of chain 0 is RAW audio from s0 (+ music under it) -> lag 0
    s0 = info[0]["start_sample"]
    seg = comp[:20 * S.SAMPLES_PER_COMP_FRAME]
    L = 480
    win = ref[s0 - L:s0 + len(seg) + L]
    xc = np.correlate(win, seg, mode="valid")
    assert int(np.argmax(xc)) - L == 0


# ------------------------------------------------------------------------------------------------------
# test_synthetic.py helpers (REQ-7: caption recall from per-event entries only)
# ------------------------------------------------------------------------------------------------------

def test_caption_recall_uses_per_event_entries_only():
    """The aggregate caption ZONE (one entry spanning the whole edit) and any entry longer than 3 s must not
    count as detected caption events: with only 3 of 10 events detected the recall is 0.3, not 1.0."""
    import test_synthetic as TS
    truth = [{"k_in": 10 + 20 * i, "k_out": 20 + 20 * i} for i in range(10)]
    n = 220
    ev = [{"type": "captions", "comp_in": t["k_in"], "comp_out": t["k_out"], "x": 1, "y": 2, "w": 3, "h": 4}
          for t in truth]
    aggregate = {"type": "captions", "comp_in": 10, "comp_out": 200, "static": False, "notes": "10 caption events"}
    cut = {"competitor": {"fps": "30/1"}, "layout": {"captions": ev[:3] + [
               {"type": "text", "comp_in": 0, "comp_out": 220}]},
           "overlays_detected": [aggregate, {"type": "captions_zone", "comp_in": 0, "comp_out": 60},
                                 {"type": "captions", "kind": "zone", "comp_in": 70, "comp_out": 90},
                                 {"type": "captions", "comp_in": 0, "comp_out": 91}] + ev[:3]}
    recall, used = TS.caption_recall(truth, cut, n)
    assert recall == pytest.approx(0.3) and len(used) == 6
    cut["layout"]["captions"] = ev
    assert TS.caption_recall(truth, cut, n)[0] == pytest.approx(1.0)
    # a 3 s entry (90 frames at 30 fps) still counts, 91 frames do not
    only_long = {"competitor": {"fps": 30}, "layout": {"captions": [
        {"type": "captions", "comp_in": 10, "comp_out": 100}]}, "overlays_detected": []}
    assert TS.caption_recall(truth, only_long, n)[0] == pytest.approx(0.5)
