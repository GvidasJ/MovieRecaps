# match_cuts — design contract (v2, after adversarial design review)

This document is the single contract between modules. The user-facing requirements live in
`MATCH_CUTS_PROMPT.md` (repo root) — every stage/criterion number below refers to it. When this
document and the prompt disagree, the prompt wins; fix this document.

Foundation modules (already written, shared by all — do not fork their conventions):
`common.py` (time math, timecodes, cache + `STAGE_VERSION`/`stage_key`, `seed_everything`, decision
log), `geometry.py` (coordinate conventions, `Sim`, OpenCV/AE conversions, `interpolate_keys`,
rounded-rect mask, RDP), `media.py` (PTS-indexed `VideoReader`, `decode_pts`, `extract_audio`,
`FFmpegWriter`), `model.py` (`StreamInfo`, `Proxy` (+ `get`/`has`), `Layout`/`Box`/`Zone`,
`FrameMap`/`Status`/`CAND_W`, `AudioHints`, `Segment`, `Cutlist`, `cutlist_layout`), `scoring.py`
(masked ZNCC in competitor space, `fit_blend`, gradient-domain `grad_zncc`), `config.py` (`Config`: every
threshold), `temporal.py` (competitor-only temporal signature: repeat / move labels of frame pairs).

Environment facts (verified): Linux, 4 CPUs, ffmpeg 6.1.1, Python 3.12 venv `/home/user/MovieRecaps/.venv`
with **OpenCV 5.0** (contrib, headless; `cv2.SIFT_create`), numpy 2.5, PyAV 19, scipy, scikit-image,
OTIO 0.18 + `otio_fcp_adapter` + `otio_cmx3600_adapter`, scenedetect 0.7.1 (use `backend='opencv'`;
the pyav backend crashes with PyAV 19), no librosa. Node 22 at `/opt/node22/bin`. No After Effects.
Package installed editable; tests run from `tools/match_cuts/`: `../../.venv/bin/python -m pytest -q tests/<file>`.

---------------------------------------------------------------------------------------------------

## 1. Pipeline overview

```
cli.main -> pipeline.run(cfg)
  S0  env check                       pipeline.check_env()         -> {os, ffmpeg, ffprobe, versions, ae_app, aerender}
      input auto-detect               cli: competitor = portrait one (else shorter); warn + swap if reversed
  S2  probe both inputs               probe.probe()                -> StreamInfo x2
      conform to AE-safe media        conform.conform()            -> output/media/*  (+ verification, cached by .conform.json)
      re-probe the AE files           probe.probe()                -> analysis happens ONLY on these files
  S3  proxies + audio                 proxies.build_proxy(), proxies.load_audio()
  S4  layout                          layout.analyze_layout()      -> Layout (+ debug/layout.png) + OverlayMasks
  S5.1 audio coarse alignment         audio_align.coarse_align()   -> AudioHints
  S5.2 visual candidate search        visual_match.RawIndex.build / sparse_search() -> list[Anchor]
  S5.3 frame-exact refinement         refine.build_frame_map()     -> FrameMap m(k) (+ overlay masks pass 2, low-confidence pngs)
  S5.4-5.5 segmentation + framing     segment.build_segments()     -> list[Segment] (DP cuts, transitions, keys, debug plots)
  S5.6 audio per segment              audio_align.analyze_segments_audio()
  S6  phase solve + cutlist           phase_solve.solve_raw_in() per segment; pipeline assembles Cutlist -> cutlist.json
  S7  AE project                      export_ae.ae_plan() / write_jsx() / run_jsx_in_mock()
  S8  exports                         export_xml_edl.*, render_preview.render_preview(), render_compare()
  S9  verification                    verify.verify_all()          -> criteria c1..c6 + checks s9_1..s9_8 (+ s9_2b, s9_2c)
  S10 report                          report.write_report()        -> report.md ; cli prints the summary
```

Caching: each stage's result lives in `WORK_DIR/cache/<stage>/<key>` with
`key = common.stage_key(stage, input file hashes, cfg.analysis_params(), stage-specific params)`.
The pipeline-level anchors (`sparse_search`) and FrameMap (`frame_map`) keys of BOTH S5.2 + S5.3 passes
also carry `pipeline.visual_pass_key_parts(layout, overlays)` (`layout_key`: the canonical layout
geometry + the static-pixel mask's content; `overlays_key`: a content hash of the starting overlay
masks), so a FrameMap matched against one box is never reused for another (a new layout algorithm, the
D2 refined box, other overlays). The layout stage's decision store is keyed with `LAYOUT_ALGO_VERSION`
like `analyze_layout`'s own entry. Bump `STAGE_VERSION[stage]` whenever a stage's algorithm or output
semantics change.
Export-only settings (layout mode, comp size, fps mode, AE time mode) are excluded from analysis keys.
Determinism (criterion 9.7): no wall-clock values in cutlist.json (timings go to
`provenance.timings`, excluded from the determinism comparison), `common.seed_everything(cfg.seed)`
before every RANSAC batch and immediately before creating/training every `FlannBasedMatcher`, and at the
start of every worker; deterministic iteration order; multiprocessing results gathered in input order.
`DecisionLog` truncates `work/decisions.jsonl` at run start; every decision is logged with evidence.
Process pools (visual_match.parallel_map): `fork` on Linux, a persistent `spawn` pool on Windows/macOS
(override: `MATCH_CUTS_START_METHOD=fork|spawn`); workers only read memmapped proxies (Proxy pickles as file
references; the RawIndex as its cached files) and results are bit-identical across start methods. Before
forking the parent runs `gc.collect(); gc.freeze()` and sets OpenCV to 1 thread — a forked child must never run a
destructor of an inherited object (verified deadlock: a stray frame-threaded PyAV decoder freed by the
child's GC hangs in avcodec_free_context). Pools that decode video use the 'spawn' context.
Hang protection (D7): no fork while native thread pools run, and a watchdog on every pool (`common`
"Worker pools" section; details in D7).

## 2. Conventions

### 2.1 Time
* Frame rates: `fractions.Fraction` (`common.parse_fps`, `common.fps_str` -> `"30000/1001"`).
* Competitor frame `k` is displayed at `t_k = k / comp_fps`; RAW frame `j` at `j / raw_fps`.
  The AE-imported files (conformed if needed) start at t = 0 — analysis only uses those files.
* Intervals are half-open `[in, out)`. Seconds in `cutlist.json` have 9 decimals (`fmt_seconds`);
  the AE plan / JSX never uses rounded seconds (see §5 export_ae).
* Speed `v` = Δ RAW seconds / Δ competitor seconds. Never from frame counts (29.97 in 30 = 1.000).
* Frame index of a decoded frame = `round((pts - stream_start) * time_base * fps)` (`media.VideoReader`).
* AE sampling rule: at comp frame `k` a stretch-mode layer shows RAW frame
  `floor(raw_fps * (raw_in + v * (t_k - t_in)) + 1e-9)`, `t_in = comp_in / comp_fps`.
* Phase LP (Stage 6), in local frame units — well conditioned for any offset:
  `x = raw_fps·raw_in − base`, `u = v·raw_fps/comp_fps`, `d_k = k − comp_in`. Frame k is consistent
  with `(x, u)` when `lo_k − base − τ ≤ x + u·d_k ≤ hi_k − base + 1 + τ` (closed, tolerant, τ = 1e-6
  frame). Exact timing ties (e.g. ffmpeg `setpts/1.1` resolving an exact .5 either way) therefore do
  not split segments; frames whose Chebyshev slack is < 1e-4 frame are **timing-tie** frames
  (`Segment.tie_frames`, `FrameMap.tie`), listed like ambiguous frames, and may differ by one frame
  from the AE rule (they are < 1 %).
* **AE floor-rule slack (FX-10, §7.3).** Frame d of a layer changes its RAW frame where x + u·d crosses an
  integer: at the BREAKPOINTS x = n − u·d (every integer n), 1-periodic in x; they cut the feasible interval
  into CELLS (every x of a cell shows the same RAW frame on every frame). The SLACK of a placement = the
  distance of x to the nearest breakpoint of ANY frame of the layer, binding or not (= min over frames of
  the distance of x + u·d to an integer), evaluated EXACTLY (`phase_solve.exact_min_slack`, Fractions of the
  values as written: the 9-decimal raw_in, AE's startTime / stretch / key values). raw_in = the midpoint of
  the cell with the most slack inside the allowed interval (floor∩round with the round-rule breakpoints
  too, else floor; `phase_solve.place_in_cells`), never the bare interval centre, which can sit on the
  breakpoint of a non-binding frame. Rational rates make the breakpoints a lattice: 23.976 in 30
  (u = 800/1001) has 4/1001-frame cells at every cadence slip (5 comp frames with 3 RAW advances), so exact
  frames across a slip PIN raw_in to ±2/1001 frame = ±0.083 ms; NTSC-in-integer layers of ≥ 1001 frames
  (≥ 250 at 23.976 → 30) only have 1/1001–5/1001-frame cells. `Segment.ae_margin_ms` = that exact slack
  (ms). Below `cfg.ae_slack_tol_frames` (0.01 RAW frame) After Effects' own time resolution decides, which
  no run has measured yet (no .aep / aerender): `--ae-time-mode auto` exports such a layer frame-exact
  (HOLD keys at j + 0.25: 0.25 frame of slack), and a cadence-pinned phase is reported as information.

### 2.2 Coordinates  (`geometry.py`)
* CORNER convention everywhere outside OpenCV calls: pixel (i, j) covers `[i,i+1)×[j,j+1)`.
  OpenCV (warpAffine, keypoints) uses centres at integers: `p_cv = p_corner - 0.5`.
  Convert only with `geometry.to_cv_matrix` / `from_cv_matrix` / `warp_raw_to_comp`.
  (Verified: swscale scaling, `hflip` (x' = W − x) and `perspective` with the identity quad
  (0,0),(W,0),(0,H),(W,H) are CORNER-aligned.)
* Canonical transform `Sim(s, theta_deg, tx, ty)` maps RAW **full-res** pixels — after horizontal
  flip `x' = W_raw - x` when `flip_h` — to competitor **full-res** pixels:
  `p_comp = s·R(θ)·p_raw' + t`, `R(θ) = [[cos,-sin],[sin,cos]]` (y-down; clockwise-positive, = AE).
  `Sim.from_matrix` / `from_cv_matrix(project=True)` RAISE on reflections (det ≤ 0).
* Proxies (`cv2.resize`, INTER_AREA) have per-axis ratios `(rx, ry) = (w_p/W, h_p/H)`, and CORNER
  coords scale exactly: `p_proxy = diag(rx, ry)·p_full`. Always pass these ratios; never assume rx == ry.
* **RANSAC is always RAW → comp** (src = RAW proxy keypoints, dst = comp proxy keypoints; the
  reprojection threshold is in comp-proxy px). Flip hypothesis: match the UNFLIPPED comp keypoints
  against SIFT of `cv2.flip(raw_proxy, 1)`; `from_cv_matrix(M, flip=False, W_raw, raw_ratio, comp_ratio)`
  of that matrix IS the canonical Sim for `flip_h=True` (then score with `flip=True` on the unflipped
  RAW). `from_cv_matrix(M, flip=True)` only takes a matrix built against the unflipped RAW that
  includes the reflection (e.g. an ECC result initialised from `to_cv_matrix(sim, True)`).
  Descriptors of the flipped comp frame are used only for voting in the index.
* ECC convention (verified): `M = translate3(-x0,-y0) @ h3(to_cv_matrix(sim0, flip, W, rr, cr))`,
  `init = inv(M)[:2]`, `cc, Wm = cv2.findTransformECCWithMask(blur(comp_roi), blur(raw_proxy), comp_mask_u8,
  np.full(raw.shape, 255, np.uint8), init, cv2.MOTION_AFFINE, crit, 5)` (the 6th argument of plain
  findTransformECC masks the INPUT image, not the template), `sim = from_cv_matrix((translate3(x0,y0) @
  inv(h3(Wm)))[:2], flip, W, rr, cr)`. ECC raises `cv2.error` ("Iterations do not converge") — always catch
  and fall back. SIFT: `cv2.SIFT_create(n, enable_precise_upscale=True)` (the default upscale shifts every
  keypoint by 0.25 px, so SIFT of cv2.flip(img, 1) would be offset by 0.5 px).
* Rotation is included only if |θ| > 0.2° (cfg.rotation_min_deg); otherwise θ = 0.
* `geometry.interpolate_keys(keys, k, raw_w, raw_h)` interpolates exactly like AE (linear Scale,
  Rotation, Position); preview, AE simulation and verification all use it.

### 2.3 After Effects transform (`geometry.sim_to_ae`)
`r` = target comp px / competitor px, `c = [W_raw/2, H_raw/2]`:
```
Anchor = c ;  Scale = [(flip?-1:1)·100·s·r, 100·s·r] ;  Rotation = θ
Position (MAIN, no box)    = r·(s·R(θ)·c + t)
Position (inside Video Box) = r·(s·R(θ)·c + t) − [bx0, by0]      (subtract the INTEGER box origin AFTER scaling)
```
Box pre-comp geometry (integers): `bx0 = floor(box.x·r)`, `by0 = floor(box.y·r)`,
`bw = ceil((box.x+box.w)·r) − bx0`, `bh = ceil((box.y+box.h)·r) − by0`. The pre-comp layer in MAIN has
Anchor [0,0], Position [bx0, by0], startTime 0; its rounded-rect mask is at
`(box.x·r − bx0, box.y·r − by0, box.w·r, box.h·r)` with radius `corner_radius·r`.
`r = min(Wt/Wc, Ht/Hc)`; in match mode a `--comp-size` with a different aspect is rejected.
(`sim_to_ae(..., origin=(bx0/r, by0/r))` gives the same Position.) Verified numerically: sim_to_ae /
ae_to_matrix agree with AE's `p = Pos + R·diag(sx,sy)/100·(p − Anchor)` to 3e-12.

### 2.4 Layout modes
* `match`  — MAIN = competitor size × r. A `Video Box` pre-comp (bw × bh) holds the segment layers;
  placed as §2.3 with the rounded-rect mask; background under it; guide layers for zones.
* `fill`   — MAIN 1080×1920 (or `--comp-size`), no box. Per segment: RAW point at the centre of the
  competitor box → frame centre; zoom = s · (cover_scale_frame / cover_scale_box); clamp so no empty
  edges show. `export_ae.fill_transform(sim, flip, box, raw_wh, target_wh)`, used identically by
  the preview renderer.
* `source` — MAIN at RAW size and RAW fps, identity transforms, cuts only.

### 2.5 Timeline fps
`main_fps = comp_fps` unless `fps_mode == 'source'` or `layout_mode == 'source'`, then `main_fps = raw_fps`.
On a different grid: `K_in = floor(comp_in·main_fps/comp_fps + 0.5)` (likewise K_out); per-cut
`error_s = K_in/main_fps − comp_in/comp_fps` stored in `cutlist.settings.fps_source_max_error_s` and in
segment notes; raw_in re-anchored `raw_in' = raw_in + v·(K_in/main_fps − comp_in/comp_fps)`.
s9_2 simulates at main_fps and compares MAIN frame K with m(floor(K·comp_fps/main_fps)), excluding
frames within 1 MAIN frame of a cut. Criterion 2/6 are exact only for `fps_mode=competitor` (the
synthetic test's mode); the report says so otherwise. `preview_recreation.mp4` = MAIN size/fps/layout;
visual verification always uses a match-geometry render at competitor size and fps (§5 verify).

## 3. Data model (`model.py`)
* `StreamInfo` — probe result. `ae_issues` non-empty ⇒ must conform. `rotation` = clockwise degrees
  to display = `(-displaymatrix_rotation) % 360` (matches ffmpeg autorotate).
* `Proxy` — memmapped gray uint8, `ratio`, `full_size`, `fps`, `pts`; access frames ONLY via
  `proxy.get(j)` / `proxy.has(j)` (a long-RAW proxy is sparse: `index_map`).
* `Layout` — `box` (full-res competitor px, CORNER), `background` dict, `zones`, `periods`,
  `extra_regions`, `static_mask_file`, `overlay_mask_file`, `captions`. `cutlist.layout` =
  `model.cutlist_layout(layout, cfg.layout_mode)` (`mode` = LAYOUT_MODE, `layout_kind` = detected).
* `FrameMap` — m(k) column store (`FRAME_MAP_FIELDS`; attribute assignment writes the store):
  `raw` best frame; `raw_lo..raw_hi` = RAW frames **visually identical** to `raw` in the visible
  region (RAW-vs-RAW, warped, the layout's masks only -- never refine's pass-2 residual masks, which hide exactly
  where a wrong match differs; a MAX OVER TILES (`identical_tiles`²): every tile's mean |diff| ≤ `identical_mad` ×
  clip((p98 − p2) / `identical_contrast_ref`, `identical_contrast_min`, 1) or its ZNCC ≥ `identical_thresh`, so a
  small changing region (a dimming dashboard display) is never diluted by a static frame, FX-08) —
  the only criterion-3 exemption; `low_margin` flag = score gap ≤ `low_margin_eps` (never an
  exemption); `soft_lo..soft_hi` = soft range for the LP `{j : S_k(j) ≥ max S_k − δ_k}` with
  `δ_k = scoring.noise_delta(track's best scores) = clip(3·1.4826·MAD(best scores), soft_delta_min,
  soft_delta_max)` — the score NOISE, never the spread of margins (margins measure discriminability; a
  margin-based δ let a frame 0.18 below its own best count as 'explained' and hid a jump cut); `cand_j0` + `cand[k, 0:CAND_W]` = the
  candidate score vector S_k around m (NaN where not evaluated); `widened`, `tie`, `mean`/`std`. The Sim columns
  (`s`, `theta`, `tx`, `ty`) hold the track's smooth framing PATH at k; `sim_meas[k, 0:4]` / `sim_meas_score` = the
  raw per-frame ECC measurement of RAW m(k) (FX-03); `confounded` = m±1 with its own refitted path scores within
  the track's noise (soft range widened to m±1; segmentation reads no time or framing step into it);
  `pair_label` (−1 unmeasured, 0 unknown, 1 repeat, 2 move, 3 cut) / `pair_warp[k, 0:4]` (dx, dy comp full-res
  px, ds, dθ) = the competitor's own pair (k, k+1) signature (temporal.py, refine's masks; FX-07); `detail` =
  the detail-sensitive second score of a gray-zone frame's best hypothesis (FX-08). `status` ∈ {MATCH,
  UNRESOLVED, NONE, UNIFORM, BLEND} -- THREE states for a non-uniform frame (FX-08): MATCH (≥ match_thresh, or a
  gray-zone frame promoted by the detail score), UNRESOLVED (best hypothesis in [none_thresh, match_thresh): it
  keeps that hypothesis' track / score / Sim / candidate vector, raw = −1, conf 0), NONE (EVERY evaluated
  hypothesis < none_thresh -- the only NOT-IN-RAW evidence).
* `AudioHints` — per competitor audio window: `comp_t`, `raw_t`, `speed`, `conf`, `psr`, `peak`.
* `Segment` — prompt Stage 6 fields + extras (see model.py). `type ∈ {raw, not_in_raw, dip, flash, uncertain}`
  (closed list; `uncertain` (FX-08) = an UNRESOLVED stretch: neither a RAW claim nor a NOT-IN-RAW claim, label
  'UNCERTAIN - best RAW a-b, ZNCC x-y (timecodes)', `evidence` = [{comp_frame, raw (−1: none ≥ none_thresh),
  score, sim, flip}] per frame, confidence 0, `audio.exception = 'uncertain'` -- a marker, never one of the
  closed exception codes: criterion 3 counts its frames as failures);
  freeze (v = 0), reverse (v < 0) and ramps are `type = raw` with `time_mode = remap`
  (`time_remap_keys` non-empty). `transform` = canonical Sim dict; `transform_keys` =
  `[{comp_frame, scale, rotation_deg, tx, ty}]` (absolute comp frames, AE-linear). `audio =
  {in_offset_frames, out_offset_frames, pitch_preserved, lag_ms, corr, exception, phase_source, lag_ms_video,
  line}`: the audio range is
  `[comp_in + in_offset, comp_out + out_offset)` (negative in_offset = J-cut, positive out_offset =
  L-cut; the extension uses the same (raw_in, v) map; J/L = the switch differs from the run's measured
  switch baseline, §7 D9). `lag_ms` / `lag_ms_video` are residuals after the run's A/V offset (§7 D9).
  `line` = None, or the AUDIO LINE the segment's audio follows instead of its picture map (FX-14, §7 D9 'audio
  lines': a video-only retime / freeze / uncertain piece / placeholder over continuous audio): {id (comp frame
  where the line is anchored), raw_in_seconds (the line's picture-synced RAW time at the segment's comp_in),
  speed, source, lag_ms, corr}. `time_line` = None, or the time-tied group (segment.py time ties, FX-04 2: the
  comp_in of the group's first segment) whose members show ONE RAW line (one phase, one D3 shift, §7 D3).
  `exception` ∈ {too_short, not_in_raw, audio_replaced, pitch_preserved, music_dominated, no_audio} (closed
  list; criterion 5 adds the RUN-level code `av_offset`, never a segment's own code). `retime` ∈ {none,
  frame_blend, optical_flow} (`frame_blend` with linear `time_remap_keys` carrying the measured continuous RAW
  position and not uncertain = a VERIFIED frame-blend path, `Segment.frame_mix`: AE Frame Mix, FX-08),
  `uncertain`, `unsnapped`, `cut_ambiguity=[a,b]`, `tie_frames`,
  `low_margin_frames`, `ae_margin_ms` (the exact AE floor-rule slack of the written raw_in over EVERY frame
  of the segment, ms, §2.1 / §7.3 -- no longer the distance to the interval edges), `region`, `box`.
* **Crossfade convention (matches ffmpeg `xfade=fade` and linear AE opacity keys):** a crossfade of
  `D` frames starting at `O` means `α_B(k) = (k − O)/D` for `O ≤ k < O+D` (frame O is pure A, frame
  O+D is pure B, only D−1 frames are visibly blended). `B.comp_in = O`, `A.comp_out = O + D`,
  `B.transition_in = {type: crossfade, duration_frames: D, alpha: [α_B(O..O+D−1)]}` (alpha is the
  INCOMING opacity), `A.transition_out` mirrors it. B's RAW frame at O is inferred (invisible).
  Coverage tiles exactly except for these overlaps. Dips: a `dip` segment (uniform colour) with
  `transition_in/out` of type `dip_black|dip_white|dip_color` on the neighbours.

## 4. Thresholds
All in `config.Config`. Tune on the synthetic test; document every changed default in README.

## 5. Module contracts

Signatures are the contract; internals are free. Every module has unit tests
`tests/test_<module>.py` running in < 60 s using small arrays / short ffmpeg lavfi clips in
`tmp_path`. Only `tests/test_synthetic.py` is slow (`@pytest.mark.slow`).

### probe.py
```python
def probe(path, role, work_dir, decode=True) -> StreamInfo
    # ffprobe JSON (-show_streams -show_format, side data) + one full decode pass (media.decode_pts)
    # -> exact decoded count, PTS array saved (pts_file, seconds), CFR/VFR (PTS-delta jitter > 0.1
    # frame => VFR), nominal fps = snap_rate(avg_frame_rate, 0.01) else snap_rate(r_frame_rate, 0.01)
    # else raise 'ambiguous nominal fps'; rotation, SAR/DAR, stream start times, edit lists, audio,
    # file hash, ae_issues. Cached by file hash.
def ae_issues(info) -> list[str]
    # not AE-safe: video codec not in {h264, prores} (HEVC is an issue: CC 2019 target), container not
    # mp4/mov, VFR, start_time != 0 (either stream, > 1 ms) or first decoded PTS != start, an edit list
    # with > 1 entry / an empty edit (dwell) / leaving first PTS != 0 (a single edit equal to the codec
    # delay — B-frame shift or AAC priming — is BENIGN: every ffmpeg-made MP4 has one), audio codec
    # not in {aac, pcm_*} (opus/vorbis/etc.), rotation != 0, SAR != 1, odd dimensions for 4:2:0,
    # interlaced field order.
```

### conform.py
```python
@dataclass class ConformResult: path, conformed: bool, reason: str, verification: dict, source_path: str, file_rel: str, file_abs: str
def conform(info: StreamInfo, role, cfg, dlog) -> ConformResult
    # RAW AE-safe -> copy into output/media/ unchanged (hardlink if possible), or reference the absolute
    #   path if > cfg.large_file_bytes (file_rel empty; the JSX offers a relink dialog).
    # RAW not AE-safe -> output/media/raw_ae.mov: ProRes 422 LT via `-c:v prores -profile:v 1`
    #   (prores_aw, ~50 fps; prores_ks only on request) for <= 10 min, else output/media/raw_ae.mp4
    #   H.264 CRF 12; SAME resolution (display orientation, square pixels) & SAME nominal fps, CFR,
    #   start 0, PCM s16le 48 kHz (AAC for .mp4).
    #   VFR -> CFR: `-vf fps=fps=<num>/<den>:round=up` (= 'frame displayed at t_k'; verified; never
    #   -r / -fps_mode cfr). ms-timebase sources that only need restamping: settb=<1/fps>,setpts=N.
    # COMPETITOR: always output/media/competitor_ref.mp4 (H.264 yuv420p + AAC); copy if AE-safe else
    #   transcode with the same VFR rule.
    # Verification when transcoded: expected frame count (= #{k: k/fps < last_pts + median_dur} for
    #   VFR), and >= 50 frames sampled by PTS: SSIM(conformed[k], original[max{i: pts_i <= k/fps+1e-6}])
    #   > 0.98 and greater than vs original[i±1] (no offset). Look up originals by PTS, not VideoReader
    #   indices (wrong for VFR).
    # Cached: output/media/.conform.json {src_hash, params, out_hash}; skipped when matching.
```

### proxies.py
```python
def build_proxy(info: StreamInfo, role, cfg, cache, windows=None) -> Proxy
    # one sequential decode (media.VideoReader fmt='gray', display orientation) into a np.memmap in
    # WORK_DIR (cached by file hash + size). RAW width = min(cfg.raw_proxy_width, budget-derived width,
    # >= cfg.min_proxy_width); competitor = full * cfg.comp_proxy_scale capped at comp_proxy_max_width;
    # h, w even keeping aspect; ratio = (w/W, h/H). Dense proxies must hold exactly info.nb_frames rows.
    # Long RAW (duration > cfg.long_raw_s AND dense would exceed the budget at min width): SPARSE proxy =
    # every round(raw_fps/raw_index_fps_long)-th frame + `windows` [(j0, j1), ...] (e.g. around audio
    # hints ± long_raw_window_s); index_map int32[n] (row or -1).
def extend_proxy(proxy, windows, cfg, cache) -> Proxy    # sparse only: add dense windows (second pass)
def load_audio(info, sr, cache) -> np.ndarray   # mono float32 at sr, sample 0 = video t 0; empty if no audio
def load_audio_full(info) -> tuple[np.ndarray, int]   # original rate (N, C)
```

### layout.py
```python
def analyze_layout(comp: Proxy, cfg, cache, debug_dir, dlog) -> tuple[Layout, OverlayMasks]
    # 1 static mask: temporal std over all frames (streamed) < cfg.static_std_thresh -> .npy
    # 2 video region: dynamic pixels -> box via row/col dynamic fractions, sub-pixel edges; corner radius
    #   from the corner profile (fit x(y) = r - sqrt(r^2 - (r-y)^2)); border/stroke; layout periods over
    #   time (fullscreen vs boxed) -> Layout.periods; extra regions (split/PiP) -> Layout.extra_regions.
    # 3 background: solid colour sample, else 'blur' (compare with blurred cover-scaled content), 'image'.
    # 4 static zones (logo/header/title/watermark) = connected components of static non-background
    #   pixels, classified by position; captions/stickers inside the box: text-like high-contrast
    #   detection (white text with dark outline: morphological gradient / top-hat / MSER) per frame ->
    #   OverlayMasks (initial) + Layout.captions [{comp_in, comp_out, x, y, w, h}].
    # debug/layout.png shows every zone.
class OverlayMasks:     # per-frame bool masks at comp proxy res (np.packbits per frame, sparse dict)
    def get(self, k) -> np.ndarray | None ; set(k, mask) ; union(k, mask) ; frames() ; save(path) ; load(path)
def box_coverage(layout, comp: Proxy) -> np.ndarray   # float [h, w] rounded-box coverage at proxy res
def allowed_mask(layout, overlays, k, comp: Proxy, dilate_px=None) -> np.ndarray
    # bool [h, w]: coverage >= 0.99 AND NOT static AND NOT dilated overlay(k)
def layout_overlay_masks(layout, shape=None, ratio=None, dilate_px=3) -> LayoutOverlays | None
    # the layout stage's OWN findings: per-frame caption / text-overlay masks (layout.overlay_mask_file; else
    # rectangles of layout.captions) united with its DYNAMIC zones (Zone.static False: the caption band over the
    # caption period, stickers, ...) on their active frames -- a word the per-frame detection missed is still
    # covered. Never refine's pass-2 residual masks. verify's only overlays (get / get_dilated like OverlayMasks).
def masks_from_residuals(residuals: dict[int, np.ndarray], base_allowed, cfg) -> dict[int, np.ndarray]
```
Box semantics: `Box(x, y, w, h, corner_radius)` in competitor full-res CORNER coordinates — the exact
rectangle the video is clipped to (e.g. x=60 means the first video column is pixel 60).
Multi-region (split-screen/PiP): v1 matches the DOMINANT region fully. Detected extra regions are
recorded in `Layout.extra_regions`/`periods`, their frames are reported (report: "anything AE can't
reproduce"), c1 becomes `pass_with_exceptions`, never silently passed. (Data model already has
`Segment.region/box` for a later full implementation.)

### audio_align.py
```python
def features(y, sr, cfg) -> dict   # {'logmel': [T, B] float32 at cfg.audio_feat_rate Hz (own numpy mel), 'onset': [T]}
def coarse_align(comp_y, raw_y, sr, cfg, dlog) -> AudioHints     # empty audio -> AudioHints.empty()
    # FFT-based NCC of ~1 s competitor windows (hop 0.25 s) vs the whole RAW (log-mel + onset, 100 Hz),
    # top peaks; sample-precise refinement on 16 kHz waveform within ±50 ms; weak at v=1 -> time-scaled
    # windows v ∈ [0.90, 1.30] step 0.01 (tape-style speed-ups shift pitch: onset envelope is robust).
    # conf = peak / second peak (outside ±0.3 s), psr = peak-to-sidelobe.
def xcorr_lag(a, b, sr, max_lag_s) -> tuple[float, float]   # (lag_s, peak): b delayed by lag vs a
def xcorr_lag_side(a, b, sr, max_lag_s, inner_s=None) -> tuple[float, float, float]
    # + the best SIDELOBE (outside the peak's main lobe): peak - side says whether the lag is unique (short
    # windows of tonal audio / a music bed repeat every period); inner_s: hypothesis test -- the best lag
    # within ±inner_s vs the best correlation beyond it
def analyze_segments_audio(segments, comp_y, raw_y, sr, comp_fps, cfg, dlog, *, av_offset_s=0.0, pass_name=None) -> dict
    # (§7 D9) every lag search renders the RAW pre-shifted by av_offset_s and searches the residual
    # (±min(audio_residual_search_s, half the range)); J/L against the measured switch baseline; also returns
    # '_av_offset_s', '_switch_baseline', '_measured' (run internals, never cutlist fields); audio lines over
    # video-only retimes / uncertain pieces / placeholders (FX-14, §7 D9) set Segment.audio['line']
def av_offset_prior(hints, segments, comp_fps, cfg, dlog) -> dict        # search centre from the S5.1 windows
def av_offset_probe(segments, comp_y, raw_y, sr, comp_fps, cfg, dlog) -> dict   # fallback: one wide search per segment
def av_offset_estimate(segments, audio_result, cfg, dlog, *, prior=None) -> dict   # published cutlist.audio.av_offset
def stab_intervals(lo, hi, w) -> dict ; solve_av_offset(lo, hi, w, cfg, audio_s=None) -> dict
    # per segment: J/L offsets (sign convention §3), pitch_preserved (speed != 1: log-frequency spectrum
    # correlation at shift log(v) vs 0), lag/corr; added audio (music / sfx / voice-over) ranges from the
    # residual energy vs the RAW-rebuilt track; audio_replaced when nothing correlates.
    # -> {'segments': {id: {...Segment.audio fields}}, 'added_audio': [{type, comp_in, comp_out, level_db}],
    #     'status': 'ok'|'no_audio'|'audio_replaced', 'notes': [...]}
```

### visual_match.py  (Stage 5.2)
```python
class RawIndex:        # SIFT on sampled RAW proxy frames; descriptors stored uint8 (lossless: SIFT values
                       # are integers <= 255), cast to float32 for FLANN; total capped by index_max_descriptors
    @staticmethod
    def build(raw: Proxy, cfg, cache) -> "RawIndex"   # every round(raw_fps/index_fps) frames; cached npz
    frames: np.ndarray            # sampled RAW frame indices
    def query(self, desc, top, window=None) -> list[tuple[int, float]]   # (raw frame, votes)
        # NEVER a plain Lowe ratio across index frames (adjacent near-duplicates kill it): k = index_knn NN;
        # ratio denominator = first neighbour whose frame is > index_far_s away from the first neighbour's
        # frame; if d1 < index_ratio * d_far, vote for every neighbour within index_far_s of f0 with
        # distance <= 1.1 d1 (weight 1/cluster size); smooth votes over ±1 index frame; peaks -> candidates.
@dataclass
class Anchor:          # verified match of one competitor frame
    k: int; raw: int; flip: bool; sim: Sim; inliers: int; inlier_ratio: float; votes: float
    zncc: float; source: str   # 'global' | 'audio' | 'rescue' | 'line'; '<src>_near' / '<src>_gray' (below)
def search_frame(k, comp, raw, index, allowed, cfg, window=None) -> list[Anchor]
    # SIFT on comp frame inside `allowed`; query normal + flipped descriptors for votes; verify each
    # candidate with pairwise ratio 0.75 + estimateAffinePartial2D (RAW->comp, §2.2; flip: vs
    # cv2.flip(raw)); accept inliers >= cfg.min_inliers(12) and ratio >= 0.3 AND masked ZNCC of the
    # warped candidate >= match_thresh - anchor_zncc_slack. Before storing: re-estimate against the best
    # EXACT RAW frame near the index frame: jb = argmax of j-3..j+3 under the RANSAC Sim, then for jb-1..jb+1
    # coarse-to-fine ECC (refine.ecc_measure) from the RANSAC Sim AND its derotated version; a rotation is kept
    # only when the best free result beats the best theta = 0 refit (scale + translation, every frame again) by
    # > 3 soft_delta_max, else the theta = 0 hypothesis decides the frame (a jolting camera: RAW j+1 rotated
    # imitates RAW j). Runner-up within anchor_time_delta -> Anchor.time_ambiguous (evidence only).
    # near_miss=True (sparse search, rescue): when no candidate passes, near-misses (near_miss_inliers <=
    # inliers < min_inliers) passing the same re-estimation + ZNCC test are returned as source '<src>_near':
    # refine lets them only JOIN an existing run whose time line they continue (min_inliers is unchanged).
    # When nothing passes at all, the full RANSAC matches whose re-estimated ZNCC lies in the gray zone
    # [none_thresh, accept) come back as '<src>_gray' (<= 2, FX-08): refine seeds WEAK tracks from them -- UNRESOLVED
    # evidence (a processed / motion-blurred picture of RAW content is not NOT-IN-RAW), never an anchor.
def line_search(k, comp, raw, allowed, cfg, js, flip) -> list[Anchor]        # FX-08 'search before giving up'
    # pairwise SIFT + RANSAC of comp frame k against EACH RAW frame of js (a neighbouring run's predicted window,
    # a handful of frames instead of the whole RAW; RAW features computed on the frames themselves with
    # line_search_nfeatures) with relaxed acceptance (>= near_miss_inliers at inlier ratio >= line_search_min_ratio;
    # the global min_inliers is unchanged) -- CANDIDATES only: the anchor test (Sim re-estimated over the exact RAW
    # frames around it, masked ZNCC >= match_thresh - anchor_zncc_slack) decides, best inliers first, at most
    # line_search_verify per frame. run_line_searches: the worker-pool version (input order kept); it first computes
    # the SIFT features of every RAW frame of the batch's windows once (a pool of their own) and the searches read
    # them from the state (neighbouring frames search largely the same window; identical features, wave 4).
def sparse_search(comp, raw, layout, overlays, index, hints, cfg, dlog, frames=None) -> list[Anchor]
    # every cfg.comp_search_stride frames (or `frames`); audio-restricted (±audio_restrict_s) first,
    # global fallback. multiprocessing (fork; memmaps shared; seed_everything per worker).
```

### refine.py  (Stage 5.3, produces m(k))
```python
def build_frame_map(comp, raw, layout, overlays, anchors, hints, index, cfg, cache, dlog, debug_dir) -> FrameMap
    # TIME LINE FIRST (FX-03): in a moving shot a wrong RAW frame (m±1) plus a compensating shift / zoom /
    #   rotation scores almost like the truth (the first real run: 10 of 12 anchors of one pan one frame off,
    #   fake 0.6-1.0 deg rotations, one track per anchor, the FrameMap HELD between keys). RAW time is decided
    #   before framing and never by a free per-frame or per-candidate framing fit; framing is a smooth PATH.
    # 0 competitor-only temporal signature (temporal.py on the box ROI, refine's masks), measured on the frames
    #   of tracks whose line can repeat a RAW frame at all (slope <= temporal_refine_max_slope, e.g. 23.976 or
    #   25 fps RAW at v = 1 on 30 fps; a 29.97 RAW never repeats): pair labels REPEAT /
    #   MOVE / UNKNOWN / CUT + each pair's editor move -> FrameMap pair_label / pair_warp. REPEAT/MOVE give a
    #   line's speed and fractional phase (never the integer offset); a repeat pair's warp is the editor's crop
    #   velocity (initial model of a one-anchor run). Static / blended content gives no labels (FX-07).
    # 1 RUNS: anchors grouped by RAW time only -- same flip, gaps <= max_gap, one anchor per comp frame,
    #   |integer residual| <= line_time_tol from the run's robust snap-speed line j(k) = floor(x + u k) (one
    #   comp frame so far: the best snap slope through both, 1.0 preferred). No framing gate: a pan / zoom of
    #   any speed is one run; a framing STEP splits the track after measurement (2). RANSAC near-misses
    #   (near_miss_inliers <= inliers < min_inliers, ZNCC-verified) only JOIN an existing run they continue.
    # 2 per track, the time-line-first framing fit (alternating with frame assignment, <= 3 iterations):
    #   a. frames: those it wins + every frame between its first and last anchor no other track explains;
    #      time evidence: anchors + argmax of won frames -> robust snap-speed line (>= line_min_inlier_frac of
    #      the points within line_time_tol; several slopes alike -> the one whose best floor-phase cell disagrees
    #      least with the repeat / move labels: the cadence is speed evidence), else (ramp, jump inside) the
    #      argmax itself is measured.
    #   b. candidate lines = floor-phase cells of x within ±1.5 frames (<= 48), pruned SOFTLY by the repeat /
    #      move labels (cells with the fewest disagreements + 1 stay); central cell c0 = best argmax agreement.
    #   c. framing MEASURED (ecc_measure: pyramid + phase-correlation start + the nearest anchors' Sims as
    #      starts) at c0's RAW frame on EVERY frame, at c0 ± 1 every framing_sample_step frames; ONE smooth
    #      path per family (fit_path: outliers vs the local trend of their neighbours -- a wrong-frame
    #      measurement -- removed, median-3, max-error RDP at the measured noise >= rdp_pos_tol / rdp_scale_tol /
    #      0.05 deg; piecewise linear, NO steps; constant -> 1 key; theta zeroed when every key <= 0.2 deg).
    #   d. every cell scored on every frame under its family's path; the highest summed score wins: per-frame
    #      ±1 alternatives are judged under the SAME path, so a time error cannot hide behind a compensating
    #      framing (an accelerating pan stays <= 3 keys; one key per frame for noise never happens).
    #   e. the chosen line measured where not yet, its path refitted; a framing STEP in the measurements
    #      (two-sided linear trends that cannot meet between the frames: > punch_pos_step px / punch_scale_step;
    #      a velocity knot is not a step) splits the track. Model = the path; support = the line.
    #   Beyond its first / last key the model is EXTRAPOLATED along the edge segment for at most
    #   framing_sample_step frames (then held), never held at once. Growing tracks measure the frames they
    #   grow into on their line first (init = the path extrapolated, i.e. previous frames + velocity; new
    #   samples beyond a framing step stay out).
    # 3 for every frame k and every track active near k: predicted ĵ (the track's line); score RAW frames
    #   ĵ-R..ĵ+R (R = refine_radius) under the track's path (scoring.score_candidates); if the argmax is on the
    #   window edge, extend in that direction (up to track_search_radius, then visual_match.search_frame) until
    #   interior; a best score below none_thresh -- wherever the argmax lies -- says the window missed the frame
    #   (a jump on the line): the whole ±track_search_radius window is scored before the frame can count as
    #   NOT-IN-RAW evidence (FX-08); store S_k in cand/cand_j0 (CAND_W window centred on m). Tracks showing the
    #   same RAW frame within 2e-3 explain a frame equally: the larger track keeps it.
    # 4 overlay pass 2: residual masks (layout.masks_from_residuals), re-score.
    # 5 rescue: frames with score < match_thresh OR score < rolling track median(±5) - max(rel_drop_min,
    #   4·MAD) -> search_frame on them (catches 1–2 frame flash cuts, jump cuts inside a track; runs longer than
    #   2 strides: both ends + every stride-th frame BETWEEN the sparse search's grid); anchors on an
    #   existing track's line join it, others start runs; re-score.
    # 5b SEARCH BEFORE GIVING UP (FX-08), on every run of frames no track explains (< match_thresh, not uniform),
    #   at most twice: (i) the previous and next runs' time lines scored across gaps <= line_gap_s (window widened
    #   as in 3); (ii) line-constrained SIFT re-search (visual_match.line_search) of the gap frames within
    #   line_search_reach of a neighbouring run against that run's line ± track_search_radius -- verified anchors
    #   join an existing track's line or start a run, then overlay pass 2 again (a RAW-only overlay such as a
    #   legal disclaimer is masked by its residuals); (iii) gray-zone RANSAC matches ('_gray') on frames still
    #   unexplained seed WEAK tracks: UNRESOLVED evidence only (no growth, no merges, no pass-2 masks).
    #   THREE STATES (finalize): a gray-zone frame (none_thresh <= best < match_thresh) is promoted to MATCH only by
    #   the detail-sensitive second score -- scoring.detail_score (blur matching: the zero-phase kernel family
    #   applied to the sharper image, then gradient ZNCC) of its best hypothesis >= match_thresh AND above RAW
    #   jb ± 1, ± 2 under their OWN re-measured framing (ecc_measure from the hypothesis) by > detail_margin (a pan
    #   over a static world explains every neighbour alike: no margin, no promotion; FrameMap 'detail'); else
    #   UNRESOLVED. Remaining frames: region std < uniform_std -> UNIFORM; else NONE (every evaluated hypothesis
    #   < none_thresh). Never a promotion on the plain score: ZNCC is non-discriminative on sharpened / blurred
    #   material (the real run's comp 1203 scored 0.989 against a visibly different RAW frame).
    # 6 raw_lo/raw_hi = visually identical frames (§3); low_margin; soft_lo/soft_hi; conf = f(score, margin).
    #   Sim columns = the track's PATH value; sim_meas / sim_meas_score = the per-frame ECC measurement of
    #   RAW m(k) (consistent (RAW frame, Sim) pairs).
    # 7 confound check on EVERY track: m±1 measured every framing_sample_step frames and fitted as their own
    #   path (only the path is refitted); m is compared under a path from the same sampled frames. Within the
    #   track's score noise delta (tie) -> FrameMap 'confounded' + soft range widened to m±1 (segmentation
    #   must not read a time or framing step into it); strictly better by > 3 delta -> the frame is reassigned
    #   (m±1 with that path's framing; dlog confound_reassign). dlog time_translation_confounded when every
    #   frame of a track ties.
    # 8 debug/low_confidence/k#####.png for conf < low_conf_thresh (competitor | best warped | 2nd best), max 200.
def refine_transform(comp_img, raw_img, sim0, flip, raw_w, raw_ratio, comp_ratio, allowed, cfg) -> tuple[Sim, float]
    # single-level ECC (verify's shared primitive; unchanged)
def ecc_measure(comp_img, raw_img, sim0, flip, raw_w, raw_ratio, comp_ratio, allowed, cfg, roi=None, starts=(),
                lock_theta=False, levels=None, phase=True) -> EccResult(sim, z, converged, z0)
    # coarse-to-fine (ecc_pyramid_levels while the template's short side >= ecc_pyramid_min_side): every start
    # (sim0, starts, sim0 moved by the phase-correlation translation) optimised at the coarsest level, the best
    # continues; lock_theta = scale + translation only (Gauss-Newton, rotation 0); kept only if it raises the
    # masked ZNCC over sim0 at proxy resolution.
```

### phase_solve.py  (Stage 6; pure math, no I/O; §2.1 formulation)
```python
def feasible_speed_range(ks, lo, hi, comp_in, comp_fps, raw_fps, v_bounds=(-8, 8), tau=1e-6) -> tuple[float, float] | None
    # scipy.optimize.linprog (HiGHS) in local units: min / max u subject to the tolerant constraints.
def is_feasible(ks, lo, hi, comp_in, comp_fps, raw_fps, v=None) -> bool
    # FX-08: NO single-point tie at v = 0. Every frame of a freeze sits at the SAME x, so frames measured on RAW j
    # and j + 1 would be 'feasible' only at the point x = j + 1 of the tolerant closed constraints (the real run's
    # 10-frame fake freeze over frames measured 1672 and 1673); a freeze needs one common RAW frame of width
    # > 2 TIE_SLACK (freeze_gap(u): 2 TIE_SLACK at u = 0, -2 tau otherwise -- moving lines keep the tie tolerance).
def solve_raw_in(ks, lo, hi, comp_in, v, comp_fps, raw_fps) -> dict
    # Chebyshev LP with u fixed: max t s.t. lo_k+t <= x+u·d_k <= hi_k+1-t, -tau <= t <= 0.5.
    # {'raw_in': seconds (the midpoint of the max-min-slack breakpoint cell of every frame comp_in .. last
    #  constraint inside floor∩round when it overlaps, else inside the floor interval, §2.1 / FX-10), 'slack':
    #  t* (frames), 'interval_floor': [a, b] seconds, 'interval_both': [a, b] | None, 'margin_ms' (to the
    #  interval edges), 'tie_frames': [k where slack < 1e-4], 'ok': bool, 'min_slack' (frames, all those
    #  frames), 'cell' [a, b] s, 'cell_width', 'best_slack', 'pinned' (no breakpoint inside the interval)}
def place_in_cells(a, b, u, d, target=None, margin=None, round_rule=False) -> dict    # FX-10 (§2.1, §7.3)
    # cells of [a, b] (local frames) cut by the floor (+ round) breakpoints of frames d; no target: the
    # widest cell's midpoint (ties: nearest the centre); target (D3): the cell containing / nearest to it, the
    # target clamped margin(cell width) inside. 1-periodic breakpoints: a wide interval is searched in a
    # 4-frame window around the preferred point. place_raw_in(...) = the same in seconds for a whole layer;
    # layer_cell(raw_in, ...) = the cell around a raw_in; breakpoints_in(a, b, u, d).
def exact_min_slack(raw_in, v, comp_in, k0, k1, comp_fps, raw_fps) -> tuple[Fraction, int]
    # exact min over k in [k0, k1) of |raw_fps·(raw_in + v·(t_k − t_in))| to the nearest integer (raw_in / v
    # as written); exact_line_slack(p0, step, d0, d1) for any p0 + step·d (export_ae.layer_min_slack).
def solve_shared_raw_in(parts, v, comp_fps, raw_fps, penalties=None) -> list[dict]
    # TIME-TIED segments (segment.py time ties, FX-04 2): parts = [(ks, lo, hi, comp_in), ...] at one speed v
    # solved ONCE (solve_raw_in of the union at the first comp_in); every part gets that line's values at its own
    # comp_in (raw_in / interval_* shifted by v·Δcomp_in/comp_fps), the shared slack / margin_ms, its own frames'
    # tie_frames / frame_slack and 'shared' = {comp_in, parts, raw_in}.
def ae_frame(raw_in, v, k, comp_in, comp_fps, raw_fps, rule='floor') -> int   # the AE rule (§2.1)
def snap_speed(v_ols, vrange, cfg, preferred=()) -> tuple[float, bool]
    # candidates = cfg.speed_snap_values ∪ preferred (speeds of already-solved segments) inside the
    # tolerant range; prefer (1) dominant speed of the edit, (2) 1.0, (3) closest to v_ols. None inside ->
    # (clip(v_ols, vmin, vmax), unsnapped=True). Never report the LP centre as the speed.
```

### segment.py  (Stage 5.4–5.5)
```python
def build_segments(fm: FrameMap, comp, raw, layout, overlays, cfg, dlog, debug_dir, hints=None,
                   union_cuts=()) -> list[Segment]
    # A CUT MUST BEAT THE CONTINUOUS HYPOTHESIS (FX-04, after the first real run: one smooth pan was chopped into
    #   1-frame layers with fake rotations and 'verified' flash cuts; every repair pass compared HELD framings).
    # FRAMING STEPS (FX-06 4) inside a MATCH run: per-frame increments of the framing (box-centre pre-image
    #   position, log scale, rotation) minus the median of the +-2 framing_sample_step increments around them (a
    #   pan's own velocity and a velocity knot cancel: the step is found at ANY pan speed -- S60's 1-frame
    #   snap-back inside a 0.92 px/frame pan was not) are 'fast' above punch_*_step / (2 framing_sample_step);
    #   a fast stretch [ka, kb] (<= 2 framing_sample_step frames) is a step when the linear trends of up to
    #   framing_sample_step + 1 frames on each side, EXTRAPOLATED across it, differ by more than punch_pos_step
    #   px / punch_scale_step everywhere in between AND by 3x their own residual (two wrong alternating tracks
    #   never make a step). Localised and CONFIRMED by the pixels: every MATCH frame in [ka-n+1, kb+n)
    #   (n = step_confirm_frames) scored on its RAW frame under both extrapolated trends; cut = first frame
    #   after ka where the new one wins by > 3δ_k, confirmed only if the old one wins by > 3δ_k on the (<= n)
    #   scored frames before it and the new one on the (<= n) from it (consecutive steps too). Without pixels
    #   the FrameMap decides. Confirmed steps split the run (a transform change is a cut, never HOLD keys in
    #   one layer; no merge pass crosses them); unconfirmed ones are DP candidates only. Only the transition
    #   frames (ka, cut) lose their Sims; frames [cut, kb) are measured again (below).
    # CUTS = DP over candidate cut positions (NOT greedy maximal prefixes: greedy turns 1-frame-skip jump
    #   cuts into fake 1.02-1.05x speeds and pushes boundaries late). Candidates: frames where the
    #   increment deviates from the floor pattern, track / flip changes, unconfirmed framing steps,
    #   PySceneDetect changes, audio lag steps.
    #   cost(segment) = 0 if a snap/dominant speed is feasible on the soft ranges, lambda_unsnapped if only
    #   an unsnapped speed is, inf if infeasible (after allowing isolated single-frame violations with
    #   margin < 5δ_k that no competing hypothesis explains); + lambda_cut per cut, + lambda_repeat_cut for a
    #   cut between the two frames of a competitor REPEAT pair (FX-07 a: soft evidence). Cuts with no RAW
    #   discontinuity (speed change only): at the intersection of the two lines, cut_ambiguity=[a, b].
    # NEIGHBOUR TESTS (FX-04 4/5/7): a segment's framing beyond its first / last key is EXTRAPOLATED along the
    #   edge key segment for at most framing_sample_step frames (sim_at), never held at once. _compatible: no
    #   confirmed step and the longer side's framing extrapolated to the other side's nearest measured sample
    #   within punch_scale_step / punch_pos_step. _explains (tiny segments, absorb_none): the neighbour's time
    #   line with its framing extrapolated and, with pixels, measured again by ECC on the frame; explained when
    #   within 3δ_k of the frame's own (RAW frame, Sim); a merge is re-checked AFTER the merged segment's refit
    #   (a frame scoring > 3δ_k below the check reverts it). Unmerged 1-2 frame segments are logged
    #   'flash_cut_verified' only when every frame's own pair beats each adjacent neighbour's model by > 3δ_k;
    #   else 'flash_cut_unverified' (uncertain + note); islands next to a NONE / uniform / uncertain run are
    #   logged 'tiny_island_next_to_none'. ISLANDS (FX-08 6): a 1-2 frame RAW island next to a NONE / uncertain run
    #   is kept only when its own model leaves the adjacent unmatched frames (<= 2 per side) below none_thresh;
    #   one it explains at a gray-zone score is part of that unresolved stretch (the real run's S63: comp 1191 and
    #   1192 show one picture, 1191 scored 0.826 under 1192's model) -> island + run = ONE 'uncertain' segment.
    # FREEZE ADMISSION (FX-08 5): v = 0 is a DP candidate only where the competitor itself is STATIC over the
    #   segment: every pair (k, k+1) aligned (temporal.align_pair: editor transform compensated; captions /
    #   overlays masked by the segment masks) has mean |diff| <= max(freeze_static_ratio x the competitor's own
    #   noise floor = the median of its REPEAT pairs (pulldown duplicates, refine's labels) nearby,
    #   freeze_static_mad), and the pairs show no pulldown CADENCE (exact repeats at a regular 1-in-5 spacing with
    #   small changes between them = v = 1 on a near-static shot -- the real run's dimming dashboard 593-604 --
    #   not a freeze). A moving competitor makes the freeze infeasible (never just costlier); log
    #   freeze_not_static per measured span and one freeze_rejected summary (the spans a freeze would otherwise
    #   have explained) after the segments record. MOVING HOLDS: a RAW
    #   segment whose model holds one RAW frame for >= 3 frames where the competitor is not static (and no
    #   frame-blend path was verified) is 'retimed / interpolated - unresolved' -> an 'uncertain' segment.
    # Criterion 2 check per cut (A's last frame scores higher under A's model than B's, and vice versa);
    #   move the cut otherwise; log. A visited set guards the mover (it used to oscillate between two positions
    #   with identical evidence): on a revisit, or after 3 moves, every visited position is re-evaluated (models
    #   refitted for it) by the summed score of A's frames under A + B's frames under B around the positions,
    #   the best is kept, criterion2_fail is logged with {oscillation: positions, scores, repeat_pair (a
    #   position splits a competitor REPEAT pair: FrameMap pair_label, else temporal.py around it), reason} and a
    #   segment note is added (_Seg.c2_oscillation keeps the evidence for the union test). Passing cuts untouched.
    # UNION TEST (FX-04 3/6, FX-07 a), after criterion 2 and the phantom-cut merge, on every hard cut A|B with a
    #   trigger: criterion-2 oscillation, a REPEAT pair at the cut (or at a position criterion 2 moved it over),
    #   confounded frames within +-2, >= 3 refine tracks within +-union_track_window frames, a re-assigned frame
    #   whose own RAW frame still wins near it, the two time lines meet at the cut (|ΔRAW position| <= one comp
    #   frame of RAW time: also audio_align's 'large J/L where two lines meet', FX-09), or the caller's
    #   union_cuts. A framing step at the cut keeps it (a reframe on one time line: the segments share their
    #   phase, below). Else A's line extended over B and B's over A, each frame whose RAW frame changes scored
    #   against the split with the same framing treatment (sim_at + ECC re-measure). A line no frame of which is
    #   worse by > 3δ_k is merged when it is better somewhere by > 3δ_k or changes no frame; inside the noise
    #   independent evidence decides -- a competitor REPEAT pair, or audio (AudioHints) running on without a lag
    #   step with no scene change detected -- else the cut stays, uncertain, with a note (never silently merged
    #   or kept). The merged segment's soft ranges admit the union's frames on the changed frames only.
    # COMPETITOR REPEAT PAIRS (FX-07 c): a NONE frame whose REPEAT partner is matched by the adjacent raw segment
    #   takes the partner's RAW frame when it scores under the partner's (RAW frame, framing) within 3δ_k of the
    #   partner and >= match_thresh - anchor_zncc_slack and the time line holds it (repeat_pair_absorbed);
    #   finally every REPEAT pair is checked (same status, same RAW content -- RAW's own duplicates allowed --,
    #   no time cut inside unless a framing step on one line): violations -> comp_duplicate_conflict + notes.
    # TIME TIES (FX-04 2): adjacent stretch segments at the same speed with a hard cut whose CLAIMED frames
    #   (refine's measured range where the model shows it, the model frame where it re-assigns; drops left out)
    #   fit one line -- a reframe at a RAW-native shot change, a framing step on a continuous clip -- are
    #   phase-solved together (phase_solve.solve_shared_raw_in): each gets the shared line's raw_in at its
    #   comp_in and the shared interval (log time_tie, note 'time line shared').
    # TRANSITIONS (before NONE runs become placeholders): for every cut and every NONE/low run <=
    #   transition_search between two raw segments, for k in [cut-transition_search, cut+transition_search]:
    #   scoring.fit_blend over A ∈ {Â(k)-1..Â(k)+1} × B ∈ {B̂(k)-1..B̂(k)+1} (phase-model predictions
    #   extended past the segment ends, each warped with its own Sim); blend frame if 0.02 < α < 0.98 and
    #   (1 - zncc_fit) <= blend_rel·(1 - best single-source zncc). Fit α_B(k) = (k-O)/D by least squares
    #   over the blend frames: O = round(zero crossing), D = round(1/slope). Apply §3 crossfade convention,
    #   add the chosen A/B frames as constraints, re-solve. Dips: blends with a uniform colour; flash:
    #   UNIFORM runs of 1–2 frames; NONE runs -> 'not_in_raw' placeholders (label with RAW-free timecodes);
    #   UNRESOLVED runs -> 'uncertain' segments (FX-08: label 'UNCERTAIN - best RAW a-b, ZNCC x-y', the evidence
    #   per frame for the guide layer, no timing claim); NONE runs of <= 2 frames touching an unresolved run join
    #   it (calling them NOT-IN-RAW would claim more than the evidence).
    # RETIMING (FX-08): in a v != 1 (v > 0) raw segment every matched frame is fitted as a blend of RAW j0, j0 + 1
    #   under the segment's framing (gain-free alpha); a blend frame fits >= match_thresh with 0.1 < alpha < 0.9
    #   and (1 - zfit) <= blend_rel (1 - best single). With >= 20 % (and >= 3) blend frames their CONTINUOUS
    #   positions j0 + alpha_B give the path (least squares; the single-frame argmax picks the heavier source and
    #   bends the measured speed: 0.2536 for a 0.25x blend); speed snapped to speed_snap_values ∪
    #   retime_snap_values (25 / 20 / 33 % presets) within speed_snap_tol; phase = the blends' median, kept inside
    #   the floor intervals of the measured pairs (2 TIE_SLACK) and never moved further (Frame Mix blends by the
    #   fraction: the measured phase IS the weight). VERIFIED when the fixed mix (1 - f) RAW[floor p] +
    #   f RAW[floor p + 1] scores >= match_thresh on EVERY matched frame -> retime 'frame_blend' with two linear
    #   remap keys (Segment.frame_mix: AE Frame Mix, a linear blend in the preview); the frame it SHOWS as one
    #   frame (framing samples, write-back) is the mix's dominant frame floor(p + 0.5). Not verified ->
    #   'retimed / interpolated - unresolved' = an 'uncertain' segment -- never a fake freeze or a bent unsnapped
    #   speed presented as exact (no optical-flow fitting: unverifiable on sharpened material). Freeze / reverse /
    #   ramps -> time_mode remap + time_remap_keys (freeze key values (j + 0.25)/raw_fps).
    # SPEED: v_ols (robust) -> speed_measured; feasible range -> speed_range; phase_solve.snap_speed.
    # FRAMING (5.5, FX-06 1/2/5): samples = consistent (RAW frame, Sim) pairs on EVERY frame: refine's per-frame
    #   measurement sim_meas (the path value where the ECC fell back below it) where the segment shows the frame
    #   refine measured (or a visually identical one); a frame the segment RE-ASSIGNS carries a Sim fitted to
    #   another RAW frame (the 1444 c2 failure) -> measured again by ECC (refine.ecc_measure, inits: the path, the
    #   samples' trend; accepted when converged or >= match_thresh - anchor_zncc_slack) or left out; its own frame
    #   still winning by > 3δ_k is a 'framing_time_conflict' (a union-test trigger). Summary: samples off the
    #   local trend of their neighbours removed (refine's rule), knots by RDP on a local least-squares reference
    #   (+-2 frames) at the measured noise (>= rdp_pos_tol / rdp_scale_tol / 0.05 deg, 3.5 σ), key VALUES by
    #   least squares over the samples, max-error refinement against the samples, redundant knots pruned; keys
    #   on the first and last sample (edge frames without a measurement are noted, never extrapolated). Stable
    #   (scale spread < framing_scale_spread, position spread < framing_pos_spread) -> constant transform.
    #   Rotation by a PIXEL test, not a vote: kept only when the fitted θ beats θ = 0 (derotated about the box
    #   centre) by > 3 soft_delta_max on most of <= 8 sampled frames (no pixels: |θ| > rotation_min_deg after
    #   outlier removal). Keys are linear (easing reported in `easing` only). Full affine only if clearly
    #   better (report it; AE uses the similarity). The FrameMap write-back stores the re-measured framing of
    #   re-assigned frames (Sim columns + sim_meas: export, preview and verify see a matching pair).
    # PySceneDetect (backend='opencv'; AdaptiveDetector(adaptive_threshold=2.0) + ContentDetector(15)) on
    #   the competitor: each change must coincide (±1 frame) with a cut/transition. An unexplained change inside
    #   a raw segment is checked (FX-06 6): refine's measured framing jump there (f-2, f-1 extrapolated vs f) or
    #   the model's deviation from the measurement above punch_pos_step / punch_scale_step -> 'framing step not
    #   represented' (crosscheck 'framing_steps_not_represented'); else a score dip, a layout caption event, or
    #   continuous framing. A cut it did not see is described by its RAW jump and framing change (position,
    #   scale, rotation): a RAW jump of 0 / 1 is one time line, -1 a 1-frame repeat back, never a 'same-shot jump cut'.
    # confounded frames (refine) are named in their segment's notes.
    # Writes debug/mapping.png (comp time x vs RAW time y; segments lines, cuts jumps, NOT-IN-RAW shaded,
    #   crossfades marked) and debug/scores.png (score, margin, thresholds, cuts).
```
The pipeline then calls `phase_solve.solve_raw_in` per segment (using the soft ranges) and fills
`raw_in_seconds`, `raw_in_interval`, `raw_in_interval_both`, `ae_margin_ms`, `tie_frames` -- or adopts the
segmentation's own solution (time-tied segments: the shared line's values). raw_in is re-placed over the
WHOLE segment [comp_in, comp_out) inside the solved interval (`phase_solve.place_raw_in`; segment.py's solve
saw only the frames up to its last constraint) and `ae_margin_ms` is its exact slack (FX-10, §7.3); a
time-tied group is re-placed as ONE line over all its segments' frames (one common shift), so its layers
keep one time line.

### export_ae.py  (Stage 7)
```python
def ae_plan(cutlist: Cutlist, cfg, footage_meta) -> dict
    # EVERY number the JSX sets, computed in Python — the single source of truth for the JSX and for
    # simulate_ae. Times are integer frames + fps {num, den}; the JSX computes t = k*den/num (one
    # correctly rounded division; simulate_ae uses the identical expression). raw_in as repr(float).
    # Comps: MAIN (w, h, fps, frames), Video Box (bw, bh; §2.3), per layer: {name (ASCII), kind, source,
    # compIn, compOut, time_mode, stretch, rawIn, expect: [RAW frame per comp frame] (self-check),
    # remap keys, transform (AE params from sim_to_ae; keys), opacity keys, flip, audio flags}, solids,
    # guides, markers (merged per frame), background, reference layer. Rejects NaN/None numbers (raise).
    # Per-layer time mode: 'remap' for speed <= 0 / time_remap_keys / |startTime| > 10799 s (AE's ±3 h
    # layer-time limit); else cfg.ae_time_mode ('auto' -> 'stretch'); 'frames' = per-frame HOLD remap keys
    # at (m+0.25)/raw_fps (immune to AE time quantisation; audio from an audio-only stretch duplicate).
    # FX-10: layer_min_slack(L, mode, F, R) = the exact floor-rule slack of every MAIN frame of the layer from
    # the values the JSX writes (startTime/stretch, remap key values, HOLD values, as exact binary values);
    # L['minSlack'] / L['minSlackK']. In 'auto' a stretch / remap layer below cfg.ae_slack_tol_frames is
    # exported 'frames' (decision time_mode_slack; no warning: a cadence-pinned phase is information, the
    # pipeline's single aggregated warning covers real razor edges); a forced stretch / remap mode keeps it
    # and marks aeRuleSensitive (quiet per-layer warning).
def write_jsx(cutlist, plan, out_path, cfg) -> None
    # ES3 ExtendScript, ASCII only (\uXXXX escapes; assert isascii), data via json.dumps(plan,
    # ensure_ascii=True, allow_nan=False, sort_keys=True) as an object literal. Structure: `#target
    # aftereffects`, (function(){ ... })() wrapper; app.newProject() (abort cleanly on null) BEFORE
    # beginUndoGroup; try/catch/finally with endUndoGroup in finally BEFORE app.project.save(new
    # File(here.fsName + "/recreated_edit.aep")); if !file.exists -> alert about 'Allow Scripts to Write
    # Files and Access Network' (Scripting & Expressions in 16.1+, General before). Media: relative to
    # the script, then absolute path, then File.openDialog; missing -> clean abort.
    # Import via ImportOptions + canImportAs(FOOTAGE); fieldSeparationType OFF, removePulldown OFF
    # (try/catch); conformFrameRate whenever |frameRate - num/den| > 2e-7·num/den (AE reports float32,
    # noise < 6e-8); warn when > 1e-5 relative or >= 0.25 frame of drift; frame count must match exactly
    # (else an offset warning). Save detection: no throw && app.project.file is the .aep && out.exists &&
    # its mtime changed; the summary and the preference alert use that flag.
    # Per layer STRICT ORDER: stretch -> startTime -> inPoint -> outPoint -> (timeRemapEnabled + keys) ->
    # transform / opacity / audio keys (keys live in LAYER time; nothing that moves the layer in time may
    # follow a key write). Stretch mode: `L.stretch = s; var vEff = 100 / L.stretch; L.startTime = tIn -
    # rawIn / vEff;` then recompute every expected frame from the READ-BACK startTime/stretch and in/out;
    # any mismatch -> switch that layer to 'frames' mode + warning. THEN (FX-10) After Effects' OWN mapping
    # on every frame of every RAW video layer: ExtendScript has no sourceTime(), so a temporary Slider
    # Control ('ADBE Slider Control') gets the expression thisLayer.sourceTime(time), valueAtTime(t_k, false)
    # / footage.frameDuration is the position AE samples; floor(... + 1e-9) != expect -> a stretch layer
    # switches to 'frames' (+ audio twin, warning), another mode warns; the probe effect is removed. Per
    # layer: frames, frames off, min slack, max |AE - plan| (the plan's own position) -> note() and
    # ae_time_check.txt next to the script (tab-separated, parse_time_check; s9_6 reads it after an AE run);
    # one 'AE time check' line in the alert. Remap: stretch=100, startTime=tIn,
    # in/out, assert canSetTimeRemapEnabled, enable, remove ALL keys, setValuesAtTimes, LINEAR (HOLD in
    # frames mode). Every layer: frameBlendingType NO_FRAME_BLEND, quality BEST, samplingQuality BILINEAR
    # (try), motionBlur false; comp.frameBlending = false -- except a VERIFIED frame-blend path (FX-08,
    # Segment.frame_mix): FRAME_MIX on its LINEAR remap keys (the position's fraction is the blend weight) and
    # frameBlending on the comp holding it; it is never switched to frames mode by the slack rule (HOLD keys at
    # j + 0.25 would make a constant 25 % mix; its picture is continuous in the position, so the floor rule's slack
    # decides nothing visible -- the JSX's AE source-time check judges its POSITION within 1e-3 frame); a forced
    # --ae-time-mode frames exports whole frames without Frame Mix and warns. startTime/inPoint/outPoint set
    # explicitly on EVERY layer (solids, pre-comp, reference).
    # UNCERTAIN segments (FX-08): an amber solid (UNCERTAIN_RGB, labelled 'UNCERTAIN - best RAW a-b, ZNCC x-y') in
    # the stack -- what renders -- under a GUIDE layer 'GUIDE - best RAW evidence: ...' (kind raw_guide, RAW footage,
    # frame-exact remap keys at (j + 0.25)/raw_fps of each frame's best-evidence RAW frame -- frames without evidence
    # hold the nearest one -- under the evidence framing; guideLayer, no audio: visible in the viewer, never
    # rendered, not a RAW video layer for c6) and a comp marker with the label. summary.uncertain counts them.
    # Keys: setValuesAtTimes then per key setInterpolationTypeAtKey(LINEAR, LINEAR); spatial props also
    # setSpatialAutoBezierAtKey(false), setSpatialContinuousAtKey(false), setSpatialTangentsAtKey(z, z)
    # (z = [0,0,0] for ThreeD_SPATIAL). Only matchNames ('ADBE Transform Group'/'ADBE Position',
    # 'ADBE Time Remapping', 'ADBE Mask Parade'/'ADBE Mask Atom'/'ADBE Mask Shape', 'ADBE Effect
    # Parade', 'ADBE Gaussian Blur 2' params by index, 'ADBE Audio Group'/'ADBE Audio Levels').
    # Crossfade: ONLY the UPPER layer of the pair is keyed (LINEAR), the other stays 100 %. Same level
    # (chronological stacking inside one comp): the outgoing A is upper and falls: Opacity 100 at t(O-1),
    # 100·(1-α_B) at O..O+D-1, (0 at O+D = A.outPoint). An incoming MAIN-level B (D1, box != None) sits
    # above the whole Video Box holding a boxed A, so B is keyed rising: 100·α_B at O..O+D-1, 100 at O+D,
    # and A stays 100 %. (A MAIN-level outgoing A over a boxed B is upper anyway: A falls, as in the
    # same-level case.) Audio across a crossfade:
    # Audio Levels keys on both layers at the overlap frames, 20·log10(max(g, 1e-3)) dB with
    # g = 1-α_B (A) / α_B (B); the preview applies the same linear gains. Dips: only the solid (above both)
    # is keyed.
    # Rounded box: mask on the pre-comp layer (Bezier, tangent 0.5522847498·r, feather 0) — no track
    # matte (setTrackMatte is AE 23+). Markers: comp.markerProperty.setValueAtTime (try/catch), merged
    # per frame. Reference layer: guideLayer, audioEnabled=false, blendingMode DIFFERENCE, enabled=false,
    # scaled to the comp. Guides: guideLayer solids/shape outlines per zone. Work area = whole comp;
    # openInViewer; alert summary (segments, cuts, duration, warnings). parseInt(x, 10) only.
    # Forbidden in the generated text: forEach/map/filter/reduce/some/every/indexOf/lastIndexOf/trim/bind
    # calls, JSON, Object.keys/create/defineProperty, Array.isArray, Date.now, let/const/=>/template
    # strings, NaN/Infinity literals, non-ASCII.
def simulate_ae(plan, time_mode_override=None, start_offset_s=0.0) -> dict[int, list[dict]]
    # per MAIN frame: [{layer, raw_frame, opacity}] from the exact plan values (stretch, startTime,
    # in/out, remap keys; floor rule on layer time; HOLD/LINEAR remap semantics). start_offset_s: every
    # stretch layer's startTime that much off (remap / frames keys live in layer time: unaffected); with
    # every stretch layer's slack >= ae_slack_tol_frames, +-1e-6 s changes no frame (tested on mini/film24).
def run_jsx_in_mock(jsx_path, footage_meta: dict, scenario='default') -> dict
    # node + match_cuts/ae_mock/{ae_mock.js, acorn.js (vendored, MIT)}. ES3 gate: '#' lines -> '//#', acorn
    # {ecmaVersion:3, allowReserved:'never'}, the forbidden-pattern ban, ES5+ APIs deleted inside the vm
    # context, all mock-returned arrays/objects created in the context realm. Strict mock: throws on
    # unknown GET and SET (allow-list for members absent in CC 2019), read-only members, enum type checks,
    # integer/range checks (addComp/addSolid ints in [4, 30000], 0 < duration <= 10800, 1 <= fps <= 999,
    # layer times in ±10800), clamping of non-remapped footage layers to the source extent, float32
    # frameRate, matchName-only property(), 3-element spatial values, keys stored in LAYER time
    # (startTime/stretch changes after keys move them), 1-based collections, layers.add at index 1;
    # expressions: valueAtTime(t, false) knows thisLayer.sourceTime(time) only (else expressionError);
    # Slider Control effect (removable); text files written by the script land in files_written.
    # footage_meta = {basename: {width, height, fps_num, fps_den, frames, has_audio}} (from probe).
    # Scenarios: default; media_missing (openDialog stub returns null -> clean abort, dialog called);
    # new_project_null; no_marker_property. Returns the recorded project (comps, layers with every value /
    # key, saved path, alerts, warnings).
def fill_transform(sim, flip, box, raw_wh, target_wh) -> Sim    # §2.4 fill mode
```
If Node is missing, the mock run is reported `not_available` (never a failure). The JSX is also the
deliverable for a user with AE: `File → Scripts → Run Script File…`.

### export_xml_edl.py  (Stage 8)
```python
def write_csv(cutlist, path)      # one row per segment; columns = report segment-table columns
def write_fcp7_xml(cutlist, path, cfg)
    # xmeml v5: sequence rate = comp fps (ntsc flag if den 1001), clipitem rate = RAW rate, speed via
    # <effectid>timeremap</effectid> (speed %), Basic Motion scale/center (from sim_to_ae), 'Horizontal
    # Flip' filter for flips, slug generator for placeholders/dips, cross dissolve transitions, markers at cuts.
def write_edl(cutlist, path, cfg)
    # CMX3600, FCM: NON-DROP FRAME. Record TC at comp nominal rate; source TC = RAW frame counted at
    # nominal round(raw_fps), NDF. M2 field = speed·raw_fps (3 decimals; negative reverse; 0 freeze).
    # NOT-IN-RAW / dip / flash -> BL events. Crossfade -> 'D <frames>' event.
def audio_items(cutlist) -> list[AudioItem]
    # separate audio events (§7 D9 export sync): empty in raw sync without audio lines (the audio follows the
    # picture events: XML audio clipitems at the video ranges, EDL 'B'); else one per audible segment (its own
    # map or its audio line) -- competitor sync: range + round(b·fps) + genuine J/L, RAW time tau + v·g. The
    # formats address whole frames: source in = the NEAREST RAW frame, the sub-frame remainder (ms) written
    # next to the event (XML clip comment, EDL '* AUDIO' comment); EDL video events become 'V', audio events
    # 'A' after them; XML: one audio clipitem per item + an 'Audio sync' marker.
def validate_exports(cutlist, xml_path, edl_path) -> dict
    # OTIO read with rate = comp fps (cmx_3600 uses ONE rate: M2 read as field/rate) and own XML parser
    # (the fcp adapter ignores timeremap); compare frame numbers, not seconds: total == competitor
    # frames, per-clip in/out, speed within 0.2 %.
```

### render_preview.py  (Stage 8)
```python
@dataclass class RenderContext   # cutlist, plan-derived per-segment timing, layout geometry, target size/fps, mode
def make_context(cutlist, cfg, layout_mode=None, target_size=None, fps=None) -> RenderContext
def render_frame(k, ctx, raw_frames: dict) -> np.ndarray   # BGR: background, layers (flip, warpAffine with
    # geometry.interpolate_keys, crossfade out = (1-α_B)·A + α_B·B, dips), rounded box coverage mask,
    # NOT-IN-RAW placeholder (coloured, labelled), UNCERTAIN solid (amber, labelled 'UNCERTAIN': what AE renders;
    # the evidence is a guide layer). RAW frame per layer = phase_solve.ae_frame (AE rule) or the plan's
    # frames-mode value; a Frame Mix layer (Segment.frame_mix, FX-08) = the linear blend (1 - f) RAW[floor p] +
    # f RAW[floor p + 1] at its continuous position p (a second sequential decode stream for RAW floor p + 1).
def render_preview(cutlist, raw_path, out_path, cfg, layout_mode=None) -> dict
    # own frame-exact renderer: per segment one seek + sequential decode (VideoReader); two readers for
    # overlaps; FFmpegWriter H.264 CRF <= 16 yuv420p +faststart; audio via build_audio muxed (AAC).
    # Returns {'frames': n, 'raw_frames': {k: [(seg, j, weight)]}}.
def build_audio(cutlist, raw_audio, sr, *, fps=None, n_frames=None, audio_sync=None, av_offset_lag_s=None,
                switch_baseline_s=None) -> np.ndarray   # sample-accurate; tape-style resample for v != 1
    # audio sync (§7 D9): export_ae.audio_sync_params -- raw = RAW lip-sync; competitor = content + v·g, ranges
    # moved by round(switch baseline · fps) frames (exactly like the AE audio twins)
    # (like AE stretch); J/L ranges; crossfade gains as in export_ae; placeholders silent unless they carry an
    # audio line (render_preview.audio_segment: a segment with Segment.audio['line'] plays that line, FX-14).
def render_compare(comp_path, preview_frames_source, cutlist, out_path, cfg) -> None
    # hstack competitor | recreation (match geometry) | amplified |diff| (×4), same height (scaled to
    # 960 px high), burned-in frame number, timecode, segment id; competitor audio.
```

### verify.py  (Stage 9)
```python
def verify_all(ctx) -> dict
  # {'criteria': {c1_coverage, c2_cuts, c3_source_frames, c4_speed_framing, c5_audio, c6_after_effects}:
  #      {'status': 'pass'|'fail'|'pass_with_exceptions'|'not_available', 'details': {...}},
  #  'checks': {s9_1_coverage, s9_2_ae_sim, s9_3_visual, s9_4_cut_images, s9_5_audio, s9_6_ae_render,
  #             s9_7_determinism}: {...}, 'failures': [...]}
  # c1 <- s9_1: segments + placeholders tile [0, N) exactly; overlaps only = measured transitions;
  #      extra-region frames -> pass_with_exceptions; placeholders and 'uncertain' segments carry labels.
  # Hypothesis-neutral rules (never re-use an analysis decision): verify imports no decision function of
  #   segment.py / refine.py -- only the scorers, temporal.py and the shared ECC primitive
  #   refine.refine_transform; framing is RE-MEASURED (ECC, incl. a global phase-correlation start), never
  #   the model's key held at its boundary (in a pan it lags v px per frame); scoring masks are the layout's own
  #   caption / overlay masks and dynamic zones (layout.layout_overlay_masks), NEVER ctx.overlays (refine's pass-2 residual
  #   masks are computed from the match being judged: a misframed match masks its own mismatch away).
  # c2: NEW independent check per cut: competitor frames comp_out(A)-1 and comp_in(B) scored against
  #      A-model and B-model predicted RAW frames (phase_solve.ae_frame; when the RAW frames differ, each
  #      hypothesis' framing re-measured on that frame by ECC -- own: max(model, refit); other: max(held key,
  #      refit from its own and from the shown framing)); crossfades: the fitted α ramp is checked instead;
  #      NOT-IN-RAW neighbours (FX-08: NOT-IN-RAW only when EVERY hypothesis is below none_thresh -- the same
  #      rule refine applies, implemented independently): the placeholder's boundary frame must stay below
  #      none_thresh under every hypothesis its RAW neighbours offer -- the adjacent neighbour AND the one across
  #      the placeholder, each by its time line extended and by its boundary RAW frame HELD (a freeze), each with
  #      its framing re-measured when the scorer can; the RAW side's own frame must reach none_thresh. An
  #      'uncertain' neighbour claims nothing: only the RAW side is checked (raw_to_uncertain / uncertain_to_raw).
  #      No-cut alternative: A's time line extended over B's first verify_union_frames frames (and B's back over
  #      A's last ones) with re-measured framing; a line within the cut's score noise (scoring.noise_delta of
  #      the shown scores next to the cut) of the split on all of them -> 'spurious cut'; when both lines show
  #      the same RAW frames the re-measured framing decides (each side's linear extrapolation must miss the
  #      other side by a framing step: punch_scale_step / punch_pos_step / rotation, else 'spurious cut').
  #      A hard cut between the two frames of a competitor REPEAT pair (temporal labels) fails. Excursion: a
  #      1-2 frame segment more than verify_excursion_frames RAW frames off the line its neighbours share
  #      (within +-1) must beat that line on its own frames by more than the noise, else 'suspected
  #      misidentification'. Speed-only cuts (cut_ambiguity) and layout changes are exempt from these tests.
  # c3 <- s9_2 (AE simulation from ae_plan AND from the mock-run record == m(k) for >= frame_exact_min of
  #      matched frames; exemptions only ambiguous-identical [raw_lo, raw_hi] and timing-tie frames — both
  #      listed) AND s9_2b AND s9_2c AND s9_3 AND the UNCERTAIN accounting (FX-08: every frame of an 'uncertain'
  #      segment is a criterion-3 FAILURE -- neither matched nor NOT-IN-RAW -- never an exception).
  #      FRAME MIX (FX-08): the simulations carry a Frame Mix layer's weight f of RAW floor(p) + 1 ('mix'); AE
  #      shows (1 - f) RAW j + f RAW j + 1, so refine's single-frame argmax on a frame-blended competitor frame
  #      is compared with the mix's DOMINANT frame (exact, listed as Frame Mix); the lighter source counts only
  #      within verify_mix_tie of an even mix (a listed blend tie). s9_2b renders the recreation's mix and treats
  #      two frames as the same picture only at the same (j, f); s9_2c refits the neighbours of the dominant
  #      frame against the mix itself (ProxyScorer.score_with_mix, one common mask).
  # s9_2b temporal signature (temporal.py): the competitor's comp-only pair labels (repeat / move / unknown /
  #      cut, per competitor shot with a measured noise floor) against the recreation's pairs measured the
  #      same way (RAW m(k), m(k+1) each warped with its own model). Disagreements: competitor MOVE where the
  #      recreation shows the same RAW frame twice; competitor REPEAT where the recreation changes RAW frame
  #      (above the shot's repeat/move split and temporal_mag_ratio x the competitor's residual); both moving
  #      with residuals more than temporal_mag_ratio apart after the shot's measured comp/recreation bias.
  #      They count against frame_exact_min over all pairs considered; a recreation hold of >= 4 frames whose
  #      labelled competitor pairs mostly MOVE is a 'motion mismatch' (always a failure).
  # s9_2c +-1 refit: every matched single-segment frame, RAW j-1 / j+1 each with its own ECC framing (from
  #      the shown framing and its derotated version; j's own refit when a neighbour comes within
  #      verify_refit_margin of the shown score); a neighbour beating max(shown, refit of j) by more than
  #      max(3 delta, verify_refit_margin) (delta = noise_delta of the segment's shown scores) is a frame shown
  #      one RAW frame off with a compensating framing; counted against frame_exact_min, listed otherwise.
  # c4: speed inside [vmin, vmax] ± 0.5 % and snapped where a snap was feasible; framing: per-frame
  #      measured Sims vs segment model within ±1 % scale / ±4 px; flip/rotation consistent. Independent
  #      framing on >= verify_framing_min_samples frames per segment (every frame of segments of <=
  #      verify_framing_all_max frames): ECC from the model perturbed by ±2 % / ±3 px and from a GLOBAL start
  #      (phase correlation of the competitor ROI vs the warped RAW), never from the model itself; a better
  #      framing off by more than the tolerance is a bad frame (> 20 % of the samples -> fail); an
  #      unconverged sample fails when the model scores below verify_zncc or its gradient-domain ZNCC
  #      (scoring.grad_zncc: dark / low-texture frames) lies more than max(3 x noise, verify_low_score_margin)
  #      below the median of the neighbouring segments' measured samples; otherwise it is listed.
  # c5 <- s9_5: per-segment lag (recreated vs competitor audio) within ±10 ms -- the RESIDUAL after the
  #      expected lag (published A/V offset in raw sync, 0 in competitor sync), with verify's own re-estimate of
  #      the offset agreeing with the published one (§7 D9; else fail), short pieces checked as aggregated runs,
  #      inverted ranges fail -- else explained with a code
  #      from the closed list (§3) -> pass_with_exceptions.
  # c6: mock-run (no alert containing 'Error'; MAIN frameRate == main_fps within 1e-9; duration ==
  #      frames·frameDuration; work area == duration; saved path == <script dir>/recreated_edit.aep; one
  #      layer per segment with name/startTime/stretch/in/out == plan (guide layers are not RAW video layers);
  #      media_missing scenario aborts
  #      cleanly and calls openDialog) + s9_6 aerender if available. Linux: 'pass' means mock-verified;
  #      details say 'mock only'. Node missing -> not_available.
  # s9_6 also carries 'ae_time' (verify.ae_time_calibration, FX-10): the slack tolerance, the layers the plan
  #      exported frame-exact because of it, the JSX's AE source-time check of THIS run's After Effects run
  #      (ae_time_check.txt newer than the JSX; frames off the plan, min slack, max |AE - plan|; a layer still
  #      off in frames / remap mode fails), and with a render the smallest plan slack of a stretch frame AE
  #      rendered right + render mismatches below 2 x the tolerance: the evidence that may later justify a
  #      smaller ae_slack_tol_frames.
  # s9_3 visual: masked ZNCC competitor vs match-geometry recreation per frame (reuse preview_recreation.mp4
  #      when mode is match at competitor size, else render_frame in memory); distribution; failures ->
  #      debug/verify_failures/k#####.png. s9_4: debug/cuts/cut_XX.png (k-1, k, k+1, k+2 competitor vs
  #      recreation). s9_7: re-run S5.4 -> S6 assembly (segment, phase solve, audio per segment, Cutlist) from
  #      cached FrameMap/AudioHints in a fresh context and byte-compare cutlist JSON (timings excluded).
```

### temporal.py  (competitor-only temporal signature; shared by verify and segment)
```python
def prepare(img, mask, max_side, blur=0.0) -> (img, mask)   # downscale to max_side, blur; mask loses the blur's reach
def align_pair(a, ma, b, mb, cfg=None) -> PairMeasure
    # phase-correlation translation -> ECC (MOTION_AFFINE, masks) -> closest similarity (an editor move:
    # |ds| <= 10 %, |rot| <= 5 deg, shift <= 25 % of the long side, else the translation only) -> masked ZNCC
    # cc, mad, (dx, dy, ds, dtheta). r = 1 - cc.
def measure(get, ks, cfg, pairs=None, gaps=(1, 2), same=None) -> Signature   # d1[k] = (k, k+1), d2[k] = (k, k+2)
def label_pairs(sig, cfg, breaks=()) -> Labels
    # shots = runs of pairs with cc >= temporal_shot_cc (else CUT); per shot: largest gap of sorted log r; a gap
    # >= temporal_gap_ratio splits the shot: upper cluster MOVE, lower cluster REPEAT when the pair's
    # interchangeability ratios r(k,k+2)/max(r(k,k+1), r(k+1,k+2)) and r(k-1,k+1)/max(..) stay <=
    # temporal_growth_ratio (slow motion grows), else UNKNOWN; without a gap: if the shot's median growth >
    # temporal_growth_ratio, pairs with their own growth above it are MOVE, the rest UNKNOWN ('moving');
    # otherwise all UNKNOWN ('undecided': an all-repeat static run, a noise plate, saturated motion).
def local_labels(get, k0, k1, cfg) -> Labels ; summary(labels) -> dict
```
Nothing in temporal.py needs RAW or a segmentation; the noise floor is measured per shot (the repeat-vs-move
margin is ~0.001 ZNCC at thumbnail scale -- no absolute threshold separates them). refine measures the same
comp-only signature with its own masks (time-line evidence, FX-07; FrameMap pair_label / pair_warp) and verify
re-measures it independently with layout-only masks; the measurement / labelling settings (temporal_max_side,
temporal_shot_cc, temporal_gap_ratio, temporal_growth_ratio, temporal_ecc_*) are therefore analysis parameters
(cache keys), temporal_mag_ratio stays verify-only.

### report.py (Stage 10), pipeline.py, cli.py, README.md
```python
def write_report(ctx, path) -> None      # prompt Stage 10 sections: inputs (codecs, fps, sizes, durations,
    # VFR/offset issues, conform + why, fps-source max error), layout (+ layout.png), segment table
    # (# · comp in–out tc+frames · duration · RAW in–out tc · speed · flip · scale/position or 'animated' ·
    # transition · confidence · notes), edit-style breakdown, warnings (low-confidence, ambiguous-identical,
    # timing-tie, NOT-IN-RAW (every hypothesis below none_thresh), UNCERTAIN ranges with their labels (FX-08),
    # phase pinned by cadence (information) and AE-rule-sensitive segments (one line, §7.3), extra regions,
    # anything AE can't reproduce -- a verified frame-blend path is exported with Frame Mix and says so),
    # criteria table c1..c6, how to open in AE (+ preference, reference layer), timings.
def check_env() -> dict                  # pipeline.py
def run(cfg: Config) -> dict             # pipeline.py: S0..S10; pipeline.Context dataclass holds everything
def main(argv=None) -> int               # cli.py: python -m match_cuts --competitor X --raw Y --out Z
    #   [--layout match|fill|source] [--comp-size WxH|competitor] [--fps competitor|source] [--work DIR]
    #   [--workers N] [--force-conform] [--ae-time-mode auto|stretch|remap|frames] [--audio-sync raw|competitor] [-v]
    # prints one line per criterion c1..c6, output paths, warnings; returns 0 only if none is 'fail'.
```
README.md: setup (venv, `pip install --no-deps scenedetect click platformdirs`), CLI usage and flags,
outputs, running the JSX in AE (preference, relink, reference layer), troubleshooting (AE scripting
preference, relink, VFR, fps misread/conformFrameRate, AE-rule-sensitive (razor-edge) segments -> `--ae-time-mode
frames`, failing-criterion playbook from Stage 9), changed defaults.

## 6. Synthetic test (Stage 1) — `tests/synth.py` + `tests/test_synthetic.py`

`synth.make_synthetic(out_dir, profile='full'|'mini'|'film24') -> dict` (cached by a key that includes the
ffmpeg version string and the synth source hash; byte-stable: pinned x264 args incl. `-threads 4`, film24 one
thread, §6.1).
`mini` = the same feature list at reduced scale (RAW 960×540, ~60 s; competitor 540×960, ~20 s) for fast
integration loops; `full` = the prompt's spec.

**RAW** (`raw.mp4`): 1920×1080, 30000/1001, exactly 5400 frames (assert), H.264
(`-c:v libx264 -preset veryfast -crf 16 -bf 3 -g 250 -threads 4 -pix_fmt yuv420p -video_track_timescale 30000`)
+ AAC 48 kHz of exactly 8,648,640 samples (never `-shortest`). 12 shots × 450 frames, each a DISTINCT
generator configuration (never repeated), every source with `r=30000/1001` and an explicit seed:
cellular generators rendered at ≤ 480×270 and upscaled `scale=1920:1080:flags=neighbor` (≥ 4 px detail);
testsrc2 shots differ by hue/offset; smptehdbars only with a large (≥ 480×360) textured overlay moving
≥ 4 px/frame; mandelbrot at 960×540 upscaled (not used for push-in/punch-in/flipped segments). Each shot
generated in its own process to a lossless intermediate (MP4/MOV timescale 30000 or NUT — never MKV), then
concatenated; after the concat, once: a RAW-anchored `drawgrid` texture and a 5-digit zero-padded
monospace counter `%{eif\:n\:d\:5}` (~160 px, white, black border) inside the SAFE REGION (RAW area
visible in every competitor framing of that shot, away from the caption band). Unique audio: modulated
`aevalsrc` tones + seeded, enveloped `anoisesrc`.

**ID video** (`id.mp4`): 512×64 gray, 16-bit index in 16-px blocks (top row code, bottom row complement,
duplicated in both 256-px halves), `libx264 -qp 0 -pix_fmt yuv420p -video_track_timescale 30000` in MP4;
assert its decoded PTS/time_base equal raw.mp4's exactly (integer pts × time_base as Fractions).

**Competitor** (`competitor.mp4`, 1080×1920 @ 30, ~50–60 s, ~20 cuts): a segment = (RAW start j, speed v,
comp length N_out, geometry). Timing chain, identical for RAW and ID inputs, one `-ss` input per segment
(no split of one decode): `-ss %.6f((j−0.5)·1001/30000) -i SRC` → `setpts=(PTS-STARTPTS)/v,fps=30,trim=end_frame=N_out`
(trailing trim mandatory; never ms-rounded times). Speed-changed segments get a sub-frame phase offset in
setpts that removes exact rounding ties (verify with the ID chain). Geometry (real chain only; the ID chain
omits only whitelisted 1:1 PTS-preserving filters): `hflip` first (on the RAW), `scale=W:H` with explicit
even sizes (truth s = least-squares similarity of diag(sx, sy)), `crop=…:exact=1` with integer offsets;
animation ONLY via `perspective=…:eval=frame:sense=source:interpolation=cubic` on the box-sized stream with
CORNER quads and `(in-1)` (perspective's `in` is 1-based); linear z(n) → exactly 2 keys; punch-in = one
timing chain with a step `if(lt(in,K+1),z1,z2)`. No zoompan, no scale=eval=frame+crop.
Layout: one RGBA `frame.png` rendered once with geq (alpha 0 inside the rounded box tested at pixel centres
(X+0.5, Y+0.5), plus logo, channel name, multicoloured title, watermark), composed with
`[box]pad=1080:1920:60:460:black[v];[v][1:v]overlay=0:0:shortest=1` and `-loop 1 -framerate 30 -i frame.png`
(the `color[c];[c][box]overlay=…:shortest=1` variant drops the last frame; no alphamerge with looped inputs).
`perspective` (sense=source, CORNER quad) maps output pixel INDICES, so a zoom z about the box centre also
shifts the content by −(z−1)/2 px — synth includes that in the truth. lavfi `gradients` needs explicit
colours (its random default ignores `seed`). Video graph = video only:
`concat=n=K:v=1:a=0`; crossfade via `xfade=transition=fade:duration=D/30:offset=O/30` with A trimmed to
exactly O+D frames. Captions (word-by-word, over the box) after the concat with
`enable='between(t,(k_in-0.5)/30,(k_out-0.5)/30)'`. Audio in a separate graph: per segment
`atrim=start_sample=…,asetpts=PTS-STARTPTS[,asetrate=48000*1.1,aresample=48000],atrim=end_sample=N_out*1600`
(tape-style speed-up, pitch not preserved), crossfade `acrossfade=d=D/30`, music `volume=-12dB` +
`amix=inputs=2:duration=first:normalize=0`, muxed with `-c:v copy`. Assert: competitor frame count ==
ID-chain frame count, competitor PTS == k/30.

Required features: two same-shot jump cuts (skip ≥ 3 RAW frames), one out-of-order hook, one re-used
moment, one 1.10× segment (RAW-discontinuous ≥ 3 frames with both neighbours), one hflipped segment,
one slow push-in, one punch-in (same shot, continuous time, framing step), one 6-frame crossfade, one 1 s
NOT-IN-RAW insert (a generator not in RAW, with a tone not in RAW), burned-in captions, static title +
logo + watermark, music under the original audio.

**truth.json** — MEASURED: every segment's timing chain applied to the ID video (never through xfade;
for the crossfade both chains are extended over the overlap) and decoded → per competitor frame
`{seg, raw_a, raw_b|null, alpha_b|null}` (alpha_b = (k−O)/D); NOT-IN-RAW frames decode as invalid.
Per segment: speed, flip, geometry truth computed with synth's OWN numpy code (independent of
match_cuts.geometry) and VERIFIED by pushing a calibration noise texture through each segment's exact
geometry filter string (first/last frame; every frame for animated ones), fitting the affine
(ECC/SIFT), asserting |Δpos| < 0.25 px and |Δs| < 0.05 %; keys for animated segments; crossfade (O, D=6);
NOT-IN-RAW range; overlay zones/timings; audio truth {offsets 0, pitch_preserved false, added_audio music
0..N, NOT-IN-RAW exception}. Self-check before writing: at the DESIGN proxy sizes and under ±0.5 % scale /
±2 px perturbation, every competitor frame's truth RAW frame beats j±1 and j±2 by ≥ 0.01 masked ZNCC;
every shot/crop: ≥ 50 RANSAC inliers.

`test_synthetic.py` (slow) runs the CLI on the files and asserts: FrameMap m(k) == truth for EVERY
matched frame (exact); cuts ±0 frames (speed-only cuts: truth inside cut_ambiguity); AE-simulated frames
== truth except listed timing-tie frames; speeds ±0.5 % (and snapped to the truth value); flip; framing
±1 % / ±4 px; push-in keys reproduce the truth within tolerance at every frame; crossfade (O, D=6);
NOT-IN-RAW placeholder range exact; c1–c5 ∈ {pass, pass_with_exceptions}, c6 == pass (mock), s9_7 pass;
a second CLI run gives a byte-identical cutlist.json.

The paragraphs above are the contract of the `mini` / `full` profiles (RAW 30000/1001, 12 × `shot_len` frames,
the 5400-frame assert). RAW rate, shot list (with per-shot lengths), timing model, RAW overlays, audio plan and
x264 thread count are `Profile` fields whose defaults reproduce those profiles exactly: `test_synth.py` pins the
sha256 of every ffmpeg argument / filtergraph / truth frame of `mini` and `full` to the pre-film24 generator and
compares the generated mini files with the pre-film24 digests.

### 6.1 Profile `film24` — the regimes of the first real run

Why: the real run (competitor 30 fps, RAW H.264 24000/1001, AAC 44.1 kHz) failed on regimes the 30000/1001
profiles never exercise. `film24` (mini-sized: RAW 960×540 @ 24000/1001, 14 shots of 18–150 frames, 1608 frames;
competitor 540×960 @ 30, 532 frames; generation ~2 min, one CLI run ~4 min on 4 cores) reproduces them:

* **30 fps NLE timeline.** The RAW clip sits on a 30 fps timeline with RAW t = 0 on a frame boundary and every split
  on the grid: a clip starting at grid slot n shows RAW `floor(raw_fps·(n+i)/30)` (AE floor rule, raw_in = n/30
  exactly, 0.04–0.2 ms wide floor intervals), so every 5th competitor frame repeats a RAW frame (the 24→30
  pulldown cadence). ffmpeg: `-ss` half a frame before RAW js, `setpts=PTS-STARTPTS+js·1001` (restores the RAW's
  own integer PTS so the grid stays anchored at t = 0), `fps=30:round=up` (slot m shows the last frame with
  PTS ≤ m/30), `trim=start_pts=n:end_pts=n+N` (time base 1/30), `setpts=PTS-STARTPTS`; measured on the ID video
  (time base 1/24000) and equal to the model. A grid slot exactly on a RAW frame boundary is refused (tie).
* **Editor animation via ONE perspective quad on the box-size stream** (the §6 rule, generalised): knots
  `(n, z, dx, dy)`, piecewise linear in the local frame (a step = two knots on adjacent frames); the quad samples
  the source at `(W(1−1/z)/2 − dx/z, …)`, i.e. a zoom z about the box centre plus a content displacement (dx, dy);
  `|dx| ≤ W(z−1)/2`, `|dy| ≤ H(z−1)/2` is asserted, so no edge pixel is ever sampled. Truth: the index quirk
  −(z−1)/2 plus exactly (dx, dy). Pans run at a constant z = 1.6 / 1.5 (pan room), the punch-in steps 1 → 1.7.
  Calibrated on every frame of every animated chain (< 0.25 px).
* **Chains** (`_film24_profile`): `pan` (−5 px/frame over a RAW camera pan of 14 RAW px/frame on a static world
  with a parallax testsrc2 object), `pan_accel` (4.2 then 13.8 px/frame, break at local 19, over a camera pan),
  `pan_step` (≈ −1 px/frame on the last RAW frames of one shot, the framing snaps back at the RAW-native cut to the
  next shot), `punch_pan` (×1.7 punch mid-shot, then −6 px/frame), `two_clip_pans` (two clips on one RAW line,
  +5 RAW frames skipped, opposite pans), `raw_zoom_roll` (constant editor framing over a RAW-native zoom of 2 %
  and roll of 0.07° per RAW frame), `line_across_shots` (ONE v = 1 time line across two RAW-native shot changes,
  an editor reframe at each; the middle shot is dark, low-texture and nearly static: `eq=brightness=-0.25:
  contrast=0.5` — −0.35 crushed the shapes to black, mean luma 6.6 vs ~25 measured on the real dashboard — with a
  small display bar changing every RAW frame), three 3–5 frame `short` chains between long ones, `blend_slow`
  (`framerate=fps=30` blending of setpts/0.25, scene detection off; video only), `freeze` (20 frames, then a TRUE
  10-frame hold via `tpad=stop_mode=clone` under a sliding chain-local caption), `gray` (RAW = a centred 5-frame
  `tmix` motion blur of a camera pan; the competitor's master is the sharp pan, further `unsharp=9:9:2.5,
  eq=contrast=1.25`), `foreign` (NOT-IN-RAW lookalike: the blend shot's generator with another texture seed), and
  a RAW-only legal disclaimer (`drawtext` on one RAW shot that the competitor's master does not carry). Shots with a
  `master` graph make the competitor render from `raw_master.mp4` (the same timeline with those shots substituted,
  same PTS, asserted); this is the only way to show content the RAW copy lacks (sharp detail) or to omit what it
  adds (disclaimer) — the gray / disclaimer regimes cannot be produced from raw.mp4 itself.
* **Audio**: all chain audio plays at v = 1 (blend / freeze are video-only retimes). Split A/V delay: every chain's
  audio starts 1824 samples (38 ms) earlier in RAW than its picture in-point (pre-edit content offset, does not move
  the switch points), and the concatenated original track gets `adelay=2304S` (48 ms post-edit delay, moves every
  switch point) before the music is mixed in: the competitor audio is 86 ms late (xcorr lag −86 ms). One genuine
  6-frame L-cut (A's audio continues 6 frames, B's starts 6 frames late). Competitor AAC at 44.1 kHz.
* **Encoding**: every film24 x264 encode uses one thread — with 4 frame threads x264 produced a different
  bitstream for identical input in 1 of 3 runs on this content (identical decoded frames); one thread is
  byte-stable (verified by generating twice).
* **A/V-offset variants** (`film24_av0`, `film24_avm50`, `film24_av150`; FX-02): the identical edit with only the
  audio plan changed (total lag 0, +50 ms = audio early, −150 ms); test_synthetic.py accepts them, with the film24
  xfails non-strict (not calibrated).

**truth.json additions**: one segment per EDITOR CLIP (chains split at framing steps and at the start of a freeze;
`time_line` ties the clips of one chain), `raw_in_seconds` = the picture in-point (n/30 exact, `raw_in_exact`,
`raw_in_grid_slot`), per-frame `sim` (truth Sim) and `class` (exact | static | gray | blend | not_in_raw),
blend frames with `raw_b`/`alpha_b` MEASURED through an alternating-level probe video (weights within 0.03 of the
source-time model), `cuts[].type` ∈ {cut, reframe, freeze_start} with the freeze start's `ambiguity` (frames that
already show the held frame), `pulldown.pairs` (repeat pairs, equal to the floor-rule positions), `time_lines`,
`audio.av_offset` {content 38 ms, post 48 ms, total 86 ms, `lag_ms` −86 in xcorr convention}, `audio.jl_cuts`, and
`layout.animated_captions` (measured per frame).

**Self-checks** (before truth.json is written): calibration on every frame of animated chains; the margin check
of §6 with the per-frame Sim; segments of `static` RAW content or the `gray` chain may miss the 0.01 margin under
perturbation but must keep a positive nominal margin (listed as `relaxed_margin_frames`); measured inlier floors
replace the 50-inlier floor for the dark shot (measured 24–26 → 20), the disclaimer shot (24–48 → 20), the
blurred gray shot (20–28 → 15), the ×1.7 punch (32–47 → 25) and the ×1.6 accelerating pan (48–56 → 40), recorded as
`lowered_inlier_floors`; every pulldown repeat pair: competitor frame k warped by the truth framing change matches
frame k+1 (masked ZNCC ≥ 0.98; measured ≥ 0.997, other consecutive pairs ≤ 0.979); decoded audio: sound-vs-picture
lag −86 ± 0.5 ms on every v = 1 clip ≥ 12 frames, switch delay 48 ± 1 ms (median; every cut ± 3 ms; the L-cut at
6 frames + 48 ms); gray chain truth ZNCC ∈ [0.65, 0.90) (measured 0.82); the lookalike's best ZNCC over its model
shot ∈ [0.60, 0.90) (measured 0.75–0.81).

**test_synthetic.py with `MATCH_CUTS_PROFILE=film24`** runs the §6 tests (profile-aware: RAW fps from the truth,
no crossfade / fullscreen, lag judged against the truth A/V offset, freeze speed 0, truth cut ambiguity) plus
`test_film24_*`: frames exact against truth.json (AE simulation of the cutlist, never refine), one segment per
editor clip, framing against the truth Sim (c4 vs truth), speeds snapped to the truth, the published A/V offset,
c5 with the measured offset and no D3 clamp warnings, J/L == truth, no cut inside a repeat pair, the dark shot on
its line, no fake freeze / a true freeze at v = 0, the gray chain never NOT-IN-RAW, the lookalike never matched,
and every wrong frame flagged by verification. Each assertion the current pipeline fails is `xfail(strict=True)`
naming its fix (FX-01..FX-09); a fix removes its xfail.

## 7. Decisions from the final adversarial review (v3)

37 confirmed findings (requirements, time math, After Effects realism, real-world robustness,
verification honesty) were fixed under these shared rules:

* **D1 Per-period layout.** `segment.py` sets `Segment.box`/`Segment.region` from `layout.periods`:
  inside a `fullscreen` period `box` = the whole canvas (`corner_radius` 0) and `region` = 1; the dominant
  boxed layout keeps `box = None`, `region = 0`; split/PiP stay unsupported (`region` ≥ 2, flagged, c1
  `pass_with_exceptions`). Segments never straddle a period boundary (beyond a declared transition
  overlap or a merged 1-2 frame sliver, below). `export_ae` and `render_preview` put
  `box != None` segments directly in MAIN (above the Video Box and background, below the reference layer)
  with the canonical Sim at origin (0, 0) × r and a (rounded-)rect mask at the box (none for the full
  canvas); `verify` scores each frame in its own box ROI. A crossfade whose incoming B is such a
  MAIN-level layer keys B rising (100·α_B, then 100 at O+D) and leaves the boxed outgoing A at 100 %
  (§5 export_ae: the UPPER layer of the pair is keyed). A layout-period boundary detected inside a
  declared crossfade/dip overlap between a boxed and a fullscreen segment, or a merged 1-2 frame sliver
  at the boundary, is not a framing error: c1 lists those frames as explained exceptions
  (`verify.boxless_fullscreen_frames`) and the pipeline does not warn (`pipeline.period_mismatch_frames`,
  the same rule mirrored for boxed segments; logged as `period_boundary_explained`).
* **D2 Box refinement against RAW.** `layout.refine_box_from_raw(...)` re-fits the box from pixels where
  the warped matched RAW agrees with the competitor (static pixels included), so single-camera shots with a
  static background are not shrunk to the moving subject; when it changes the box the pipeline re-runs
  S5.2 + S5.3 once.
* **D3 Audio-informed phase.** After the per-segment audio analysis, `raw_in := raw_in + v·residual` (the
  lag after the run's A/V offset, D9) for
  confidently correlated stretch segments, placed inside the floor∩round interval (else the floor interval)
  ∩ the preserved-frames range IN BREAKPOINT CELLS of every frame of the layer (FX-10, §7.3,
  `phase_solve.place_raw_in`): the cell containing the target (else the nearest), the target clamped to a
  margin of `min(cell / 2, max(5 % of the cell, ae_slack_tol_frames))` (+1e-6 frame so the 9-decimal
  rounding cannot take a placement below the tolerance; `pipeline.audio_phase_margin`) from its edges.
  Never an integer number of milliseconds: 1 ms = 24/1001 frame of a 23.976 source and 30 comp frames
  advance 24 − 24/1001 frames, so the old 'edge + 1 ms' put the frame 30 comp frames after the binding one
  1.3 ns from a frame boundary (the real run's S26 k440). A cell is a fraction of a FRAME, never of the
  ambiguity span, so an in-point on the edge of a seconds-wide static interval stays within ~2 ms in audio;
  an in-point in a cadence-narrow cell lands at its midpoint (phase pinned by the audio in-point,
  information + frame-exact export). The residual lag is
  re-measured. A target outside that video-feasible range by more than `audio_lag_tol_ms` never moves
  raw_in (the audio says nothing usable about the phase; clamping would only shrink the AE margin) and
  those segments are listed in ONE run-level warning; `phase_source = 'audio'` only when the audio target
  placed raw_in (inside the range or within the tolerance of it). This removes the systematic quarter-frame audio offset of the interval centre (8.3 ms at
  30p, 10.4 ms at 24p) while keeping every frame exact under both sampling rules
  (`Segment.audio.phase_source`, `lag_ms_video`). Segments whose interval is wider than ±100 ms (static /
  ambiguous-identical) also get a wide search centred on the feasible interval and covering all of it
  (half-width capped at 60 s).
  **Time lines** (`pipeline.time_line_groups`, `place_time_lines`): a time-tied group (`Segment.time_line`,
  written by segment.py's time ties: adjacent stretch segments at one speed whose frames fit ONE RAW line --
  a framing step, a punch-in, reframes at RAW-native shot changes) is used as maximal runs of >= 2 adjacent
  members with a raw_in interval (a member re-solved on its own, a gap or another speed splits it; logged).
  The per-layer placement saw only each member's own frames, so the layers of one continuous clip could show
  different sub-frame phases (drift up to the interval width); the group is re-placed as ONE line: the line's
  raw_in at the first member's comp_in c0 is the max-min-slack cell midpoint of EVERY frame of the group inside
  the members' common interval (floor∩round when all have one), and member i gets
  `raw_in_i = raw_in_0 + v·(comp_in_i − c0)/comp_fps` as a 9-decimal value whose exact slack is then checked
  on its own layer (`ae_margin_ms`). The AE phase class of a member (pinned / razor, FX-10) uses its LINE's
  cell (`phase_slack(..., line)`). D3 moves a group by ONE common shift: the group residual is the weighted
  mean (audio seconds × corr²) of its confidently correlated members' residuals, which must agree within
  `audio_lag_tol_ms` (else the video phase is kept, logged), the feasible range is the intersection of every
  member's range shifted along the line, the target is placed in the cells of every frame of the group, and
  a member without confident audio of its own moves with its line.
* **D4 Verification references.** Criterion 3 compares the AE result with refine's PRE-segmentation
  measurement (`fm.d['pre_segment_*']`); frames segmentation re-assigned to its model are a listed
  `reassigned` class counted against `frame_exact_min`; a plan that disagrees with the cutlist always fails.
* **D5 Exit codes.** 0 = pass; 1 = failure; 2 = run error; 3 = nothing failed but a criterion is
  `not_available` (headline `PASS (criterion 6 not verified: …)`). New check `s9_8_deliverables`.
* **D6 Decision log.** `DecisionLog.capture/replay`: each stage's records are stored with its cache entry
  and replayed (`cached=true`) on a cache hit; `decisions.jsonl` is also copied to `<out>/debug/`.
* **D7 Parallelism.** `fork` pools only on Linux; `spawn` elsewhere (Windows/macOS, or
  `MATCH_CUTS_START_METHOD=spawn`) with picklable state (`Proxy` pickles as paths, `RawIndex` as its cache
  file); results bit-identical across start methods and worker counts.
  * *No fork with native threads alive* (wave 4; a wave-3 CLI run hung 25 min in the S5.3 fork pool with
    scipy's ducc FFT threads in the parent). The package sets `DUCC0_NUM_THREADS=1` at import (every FFT here
    runs with workers=1, so the ducc pool is never started); before forking, `parallel_map` sets OpenCV to 1
    thread and releases PyAV's per-thread swscale context (`common.release_native_threads`: PyAV 19 keeps one
    per thread, with live slice threads, for `to_ndarray`) and resets scipy's HiGHS scheduler (`linprog` leaves
    an idle worker thread; it restarts with the next solve); OpenBLAS stops its own threads around fork
    (pthread_atfork). Right after forking, a census (`common.native_threads`: OS threads of `/proc/self/task`
    minus Python's threads, re-checked for 150 ms) must find no native thread; otherwise the fork pool is
    discarded unused and this call and every later one in the process use spawn workers (warning once;
    identical results). The stage heartbeat (a Python thread) logs only under `common.FORK_LOCK`, which the
    fork holds, so a child never inherits a half-written log line.
  * *Watchdog.* Every pool (`parallel_map` fork / spawn, the layout text-line spawn pool) is collected through
    `common.watched_results` with chunks of `imap(_unordered)` at chunksize 1 (the only iterators with
    `next(timeout)`): no result for `pool_stall_timeout_s` (300 s) or an exited worker process (the OS killed
    it, e.g. out of memory: `multiprocessing.Pool` would lose its task and wait forever; a worker killed while
    holding the task-queue lock also blocks every other worker) raises `PoolFailure`. The pool is then killed
    (`common.close_pool`: `Pool.terminate()` runs in a daemon thread because it blocks on such a lock; workers
    are SIGKILLed; never waits more than ~10 s) and the tasks without a result run in the parent, in input
    order. Results stay bit-identical: every task is seeded on its own (`seed_everything` before each item)
    and placed by its input index. After `pool_max_failures` (2) stops, later `parallel_map` calls run in the
    parent. A task's own exception still propagates unchanged.
  * *Progress.* `common.Progress` counts pool / serial tasks; whenever the package logger was silent for
    `progress_log_s` (30 s), one line is logged: the open counter's `<stage>: <name>: done/total tasks done
    (elapsed)`, or the stage heartbeat's `<stage>: still running (elapsed)` (`common.stage_heartbeat`, entered
    by `pipeline._stage`). `pool_stall_timeout_s`, `pool_max_failures` and `progress_log_s` are run settings,
    never part of the analysis cache keys.
  * *Single-threaded OpenBLAS* (wave 4). `pipeline.run`, every `parallel_map` task (fork, spawn, inline) and the
    layout spawn workers pin numpy's OpenBLAS to one thread (`common.single_thread_blas` / `set_blas_threads`;
    the audio stages did already). The analysis only runs small and medium products -- the ZNCC of a few
    candidates over one box ROI (`scoring.zncc_rows`), pixel dot products (`temporal._zncc`) -- and OpenBLAS splits
    a dot product of >= ~20000 elements between its threads: 7x slower for these sizes (measured 8.4 vs 1.2 ms for
    7 x 60000 under load; with 4 worker processes each waking 4 BLAS threads, worse) and the last bits of the result
    depended on the machine's CPU count. Measured on the base commit vs wave 4 (same machine, 4 CPUs shared with
    another job): mini, film24 and full cutlist.json / csv / XML / EDL / JSX, verify.json and frame_map.npz
    byte-identical; mini wall 639 -> 165 s (CPU 21m07 -> 6m20), film24 764 -> 200-260 s (CPU 27m56 -> ~8m30;
    S5.2 121 -> 42 s, S5.3 359 -> 66-100 s, S9 215 -> 53-65 s), full 1353 -> 797 s (CPU 79m41 -> 30m43; S5.2 309 ->
    151 s, S5.3 521 -> 153 s, S9 338 -> 306 s). Line searches describe each RAW frame once per batch (below).
  * *Windows files.* Outputs are replaced through `common.replace_file` (retried for 5 s on PermissionError --
    a virus scanner, Excel holding `cutlist.csv`, a player holding the preview -- then a PermissionError that
    names the file and says to close the program); preview / compare renders are muxed into their temp
    folder first. Debug images go through `common.write_image` (`cv2.imencode` + Python file I/O:
    `cv2.imwrite` cannot open non-ASCII Windows paths). A console that cannot encode a character writes a
    backslash escape instead of failing (`setup_logging`).
* **D8 Synthetic.** Competitor audio starts at the frame boundary of each segment's first RAW frame (NLE
  convention); one ~1 s fullscreen segment exercises D1. `tests/test_synthetic_av_offset.py` (slow) delays
  the mini competitor's whole audio track by 86 ms (`adelay` + `atrim`, video copied) to exercise D9: the
  published offset interval must contain -86 ms, c5 = pass_with_exceptions(av_offset), no fake J/L, AE twins
  only for genuine J/L in raw sync, lags ~0 in competitor sync (+ the XML / EDL audio events, nearest frame and
  remainder, re-parsed exactly); the original mini publishes offset 0. film24 (§6.1) also asserts that the layers of
  one time line (pan_step, punch_pan, line_across_shots) stay ONE exact line through the placement and D3, and that
  the video-only blend slow motion and freeze keep continuous audio (an audio line within 3 ms of the truth, no
  silent gap in the preview audio) while the foreign NOT-IN-RAW insert stays silent.
* **D9 Global A/V offset (one model for every audio consumer).** A competitor whose whole soundtrack is
  shifted against its picture (repost, platform transcode, NLE export; the first real run: 86 ms late) is
  ONE measured property of the input, not dozens of per-segment failures. Convention (one sign everywhere):
  `g` = the lag of the competitor's audio vs the recreation that keeps RAW's own A/V sync, in
  `xcorr_lag`'s convention (`rebuilt(t) ≈ competitor(t − g)`; g < 0 = the competitor's audio is LATER than
  its picture, relative to RAW's own sync); per-segment `lag_ms` are residuals (measured − g).
  * Prior (`audio_align.av_offset_prior`, start of S6): S5.1 windows (speed 1, waveform NCC ≥
    `av_offset_prior_wave_peak`, inside one speed-1 stretch segment) give the in-point their audio implies;
    accepted with ≥ `av_offset_prior_min_windows` windows within `av_offset_prior_max_mad_ms` MAD; the value
    is the stabbing solution below on the window intervals (0 exactly when 0 explains them). Too few
    windows -> `av_offset_probe`: one wide search (±`av_offset_max_s`, at most half the range) per speed-1
    segment ≥ 0.5 s, solved like the estimate; else 0. The prior only centres the first per-segment pass
    (|offset| > 100 ms and short segments become measurable).
  * Estimate (`av_offset_estimate`, after the first pass): every forward stretch segment with corr ≥
    `verify_audio_strong_corr` over ≥ 0.5 s (or ≥ 0.25 s at corr ≥ 0.9) constrains g to
    `[(x_a − hi)/v, (x_a − lo)/v]` (x_a = video raw_in + v·measured lag, [lo, hi] its floor raw_in interval;
    an offset in competitor time -- exact for an offset of the finished mix; a source-side one scales by
    1/v, which max coverage tolerates), widened by `av_offset_eps_ms`, weight = seconds·corr². g = the
    centre of the weighted max-coverage ('interval stabbing') set; **g = 0 exactly** when 0 is in that set
    or covers ≥ `av_offset_zero_frac` of its weight (zero-offset inputs behave exactly as before). Accepted
    with ≥ 3 segments, ≥ 2 s of audio, ≥ 70 % coverage, |g| ∈ [2 ms, 1 s] and no single segment deciding
    WHERE it is (every leave-one-out set within `av_offset_max_spread_ms` of the published one -- a segment
    may only narrow it); else 0. No drift is fitted (the real data are piecewise constant). Published as
    `cutlist.audio.av_offset = {status: measured|zero|not_measured, lag_ms, lag_ms_interval, centre_ms,
    n_segments, coverage, coverage_zero, spread_ms, loo_distance_ms, audio_s, reason, text, prior,
    segments, switch_baseline_ms, switch_baseline, sync_mode}` with plain text ("competitor audio is 86.0
    ms later than its picture, relative to RAW's own A/V sync"); the report prints that one line instead of
    per-segment warnings. With intervals ~1 frame wide (same-fps edits) g and the per-segment phase are
    confounded: the interval is the honest result, the centre is self-consistent with D3.
  * Consumers: `analyze_segments_audio(av_offset_s=g)` renders RAW pre-shifted by g and searches only the
    residual (no ±100 ms limit, no half-range cap on 3–5 frame segments; lag0 of short/weak cores = g);
    good / too_short / music_dominated / audio_replaced are judged on the residual; D3 moves by the residual;
    the first pass is redone around g when the prior differed by > 1 ms.
  * Audio switches (FX-09): each hard cut's switch time is measured sub-hop; the run's switch baseline b =
    weighted median over decisive cuts between ≥ 10-frame, corr ≥ 0.8 segments (any value: an offset of the
    finished mix moves every switch by −g, one in the source none); |b| below half the J/L threshold is 0;
    unknown b = anywhere in [0, −g]. With fewer than `audio_jl_baseline_min_cuts` such cuts the DECISIVE tier
    decides: cuts between ≥ 10-frame segments with both models whose switch is decisive (local-NCC margin ≥
    `audio_jl_strong_margin` on both sides) whatever the cores' whole correlation (a genuine J/L at a
    segment's other end lowers it -- film24's S02 after the 6-frame L-cut -- without making this switch less
    clear; `switch_baseline.tier`). Once b is known, a model whose band-excluded core was too short to measure
    (3–5 frame segments) is aligned on the window where the competitor plays it (core + b), its lag used only
    when that xcorr peak is UNIQUE (peak − best sidelobe ≥ `audio_peak_unique_margin`: short windows of tonal
    audio repeat every period), and the switches next to it are measured again. A cut is J/L only when
    |switch − b| ≥ max(`audio_jl_min_frames` frame, 3σ) with the margin test passing AND evidence on both
    sides (≥ 2 local-NCC frames each side, decisiveness ≥ `audio_jl_min_decisive`: a switch at the edge of the
    search window, one side never observed, is not a J/L -- film24's fake 1-frame J at 189) (offset_frames =
    round((switch − b)·fps), genuine 1-frame J/L stay detectable); ranges stay ordered (a0 < a1); a J/L ≥
    `audio_jl_large_frames` next to a retimed segment or where B's time line meets A's extended line is logged
    as evidence, not exported. Added audio: residual runs are merged across short gaps / unobservable frames
    FIRST and each merged run is classified on the residual of its observable frames (a steady music bed
    under a dynamic original only crosses the threshold where the original is quiet: typed piece by piece every
    < 1.5 s piece looked like an effect -- film24's −12 dB bed read 'sfx'); the residual is the competitor minus
    the offset-corrected rebuild (each model rendered at g + its residual).
  * Audio lines (FX-14, `audio_align._audio_lines`): a REGION is a maximal run of adjacent pieces whose own
    picture map does not explain their audio (corr < `verify_audio_strong_corr` or residual beyond
    `audio_lag_tol_ms`) and that are a placeholder, a retimed segment (remap / freeze / speed ≠ 1 / frame
    blend), an uncertain segment or a piece shorter than 0.5 s. Candidate lines (picture-synced RAW time,
    played at g like any segment): the confidently explained segment just before the region extended forward,
    the one just after extended backward, and a retimed piece's own picture in-point at speed 1 corrected by
    its measured residual (video-only slow motion / freeze over audio that keeps playing). A piece takes a
    line only on CONFIDENT evidence over its surely-played window: a neighbour's line is a hypothesis test
    (the best lag within ±tol correlates ≥ `verify_audio_strong_corr` and beats every other alignment up to
    the residual search), an own in-point must be the unique peak of the search (≥ strong, margin
    `audio_peak_unique_margin`) -- never `audio_replaced_corr` 0.30; a piece too short to measure is bridged
    only between two verified pieces of the same line. A real NOT-IN-RAW insert with foreign audio verifies no
    line and stays silent. A line piece's exception is None; it says nothing about D3 or the offset estimate;
    a J/L at a cut next to a line piece is removed (inside one line the anchor's audio simply continues; at
    another cut the switch was measured with the piece's picture model, which does not carry its audio). Export: ONE audio-only RAW layer per run of consecutive
    pieces on one line (`export_ae._PlanBuilder.audio_line_layer`, the picture layers silent; competitor sync
    adds v·g and the switch shift like every twin), `render_preview.build_audio` plays the line (also under a
    placeholder), XML / EDL get a separate audio event; c5 measures the piece on its line like any segment
    (never exempted as not_in_raw; a lone piece shorter than `verify_audio_min_s` stays the inconclusive
    too_short like any short piece).
  * Criterion 5 (`verify.check_audio`): lags searched around the expected lag E (g in raw sync, 0 in
    competitor sync) over the samples where both surely play the segment (switch baseline / band), judged as
    residuals within ±tol; verify re-estimates the offset (median measured lag + the offset the recreation
    carries over its confidently correlated segments) and must agree with the published g within (interval
    width + 1 ms), else 'A/V offset not confirmed' fails; a confirmed offset in raw sync is ONE run-level
    explained exception `av_offset` (added to AUDIO_EXCEPTION_CODES as a run-level code). Pieces shorter than
    `verify_audio_min_s` are checked as maximal runs of consecutive pieces (corr ≥ strong, residual within
    tol, leave-one-out flags a piece that does not follow its run); a piece no run covers stays too_short;
    an inverted range fails.
  * Export sync (`--audio-sync`, `cfg.audio_sync`, `cutlist.settings.audio_sync`; `export_ae.audio_sync_params`
    is the single rule): `raw` (default) keeps RAW lip-sync, audio-only twins only for genuine J/L; `competitor`
    puts every RAW segment's audio on an audio-only twin whose source time is shifted by v·g (sample-accurate
    through the twin's own stretch startTime / shifted remap keys) with in/out at cut + round(b·fps) +
    genuine J/L, video layers silent and NO container shift on top (that would count the offset twice).
    `render_preview.build_audio` mirrors it. FCP7 XML / EDL (`export_xml_edl.audio_items`): in competitor sync
    (or with audio lines) every audible segment gets its own audio event at the same range and RAW time; the
    formats address whole frames, so the source in is the NEAREST RAW frame and the sub-frame remainder (ms,
    < half a RAW frame) is written next to the event (XML clip comment, EDL `* AUDIO` comment; EDL video events
    `V`, audio events `A`); `validate_exports` checks the audio events' record / source frames.

### 7.1 Second review round (review of the v3 diff)

* **Crossfade alpha is gain-independent**: `scoring.fit_blend_free` regresses the competitor on
  `[A, B, 1]` and takes `α_A = β_A/(β_A+β_B)` (undefined when A and B are collinear), so a repost's
  contrast change neither breaks a correct dissolve nor confirms an off-by-one one; segment.py keeps the
  constrained fit's ZNCC for its relative blend test, and c2's window refit drops frames within the purity
  tolerance.
* **c4 snap judgement** uses only refine's measured frames: isolated outliers are dropped with
  `pipeline.max_consistent_subset` (≤ max(2, 5 %) of the frames); if still infeasible the snap is
  `undecidable` (an exception) — never the soft ranges.
* **c1 across fullscreen boundaries**: frames of a boxless segment inside a declared crossfade/dip overlap
  with a neighbour carrying the fullscreen box, and a merged 1–2 frame sliver at a period boundary, are
  explained exceptions (`verify.boxless_fullscreen_frames`, the single rule also used by
  `pipeline.layout_period_warnings` and the report).
* **s9_7 previous-run gate** includes `provenance.ffmpeg_version` and `code_hash`; location-only fields
  (source/abs/rel paths) are ignored.
* **c6** accepts the JSX's runtime `  [frames]` fallback rename, checks switched layers as frames-mode
  layers and requires their audio-only twin.
* **D3 margin** = (superseded by §7.3: in breakpoint cells, `min(cell / 2, max(5 % of the cell,
  ae_slack_tol_frames))`, never integer ms); the wide audio search is centred on the feasible interval
  (up to ±60 s), so static / ambiguous-identical shots land on their audio in-point.
* **Cache keys**: pass-1 and pass-2 visual/refine keys include the layout geometry, static mask and starting
  overlays; STAGE_VERSION and LAYOUT_ALGO_VERSION were bumped.
* **Box refinement** never moves an edge inward over pixels the temporal analysis proved dynamic, and grows
  only on positive agreement evidence beyond the edge (no growth over letterbox bars the competitor cropped).
* **Spawn pools** are capped by available RAM when the state holds a RawIndex; the AE MAIN-comment warning
  store stays under AE's 15,999-byte `Item.comment` limit.

### 7.2 Hypothesis-neutral verification (after the first real run)

The first real run (23.976 RAW in a 30 fps competitor, editor pans over moving shots) passed checks that
compared the tool with itself: c2 scored the 'other' side with the neighbour's key HELD at its boundary (a pan
lags v px per frame, so all fake cuts in a pan passed), c3 compared with refine's own m(k) and masked with
refine's residual masks (a 37 px misframe on a dark dashboard masked itself away), c4 re-measured framing at
the model's own RAW frame from starts within 3 px. Frames one RAW frame off with a compensating framing
counted as exact. Rules since then (§5 verify.py, temporal.py): verify only uses scorers, temporal.py and the
shared ECC primitive; framing is re-measured (global start), never held; masks come from the layout only; the
competitor's own temporal signature (comp-only repeat / move labels) and a +-1 RAW frame refit give c3 two
references the model cannot fool; c2 tests the no-cut alternative and repeat pairs; noise floors are measured
per shot / per segment from the data, never by relaxing a spec tolerance. Segmentation's criterion-2 mover
keeps a visited set and reports an oscillation instead of stopping wherever its iterations ended.

### 7.3 AE floor-rule safety from the exact per-frame slack (FX-10)

The first real run (23.976 RAW in 30 fps) flagged 9 segments 'AE-rule-sensitive (0.083333 ms)' and missed the
two frames that really sat on a frame boundary. The old margin measured only the distance of raw_in to the
edges of the feasible interval, which the BINDING frames define:
* the 9 are cadence-slip cells: exact frames across a 5-frame window with 3 RAW advances pin raw_in to one
  4/1001-frame cell (±2/1001 frame = ±0.083 ms) -- maximal information, not weak evidence;
* S32 k594 sat 8e-9 frame from a boundary (the interval centre = the breakpoint of an ambiguous frame) and
  S26 k440 1.3 ns from one (D3's 'edge + 1.000 ms': 1 ms = 24/1001 frame realigns 30 comp frames later); the
  margins were 8.375 and 1.000001 ms.

Rules (§2.1 has the definitions):
* **Exact slack of every frame.** `phase_solve.exact_min_slack` / `exact_line_slack` (Fractions of the values
  as written) for segments, `export_ae.layer_min_slack` for AE layers (startTime / stretch, remap key values,
  HOLD values). `Segment.ae_margin_ms` = the segment's exact slack in ms.
* **Max-min-slack placement.** `solve_raw_in` (frames up to its last constraint), the pipeline over the
  whole segment inside the solved interval (`place_raw_in`), and D3 (the target's cell, margin in cells,
  §7 D3) all place raw_in in breakpoint cells of every frame; never outside interval_floor / floor∩round or
  the preserved-frames range. A breakpoint frame inside a feasible interval is admissible both ways, so this
  is a plan-vs-AE risk (s9_6), never a c3 one.
* **Classes** (`pipeline.phase_slack`, `ae_phase_class`): `ok` = slack ≥ `cfg.ae_slack_tol_frames` (0.01
  RAW frame); `pinned` = below it because the breakpoint cell raw_in lies in is itself narrower than 2 × the
  tolerance (pinned by the measured frames, by the audio in-point, or by the lattice of a long NTSC-in-
  integer layer) and raw_in keeps ≥ half of its slack; `razor` = below it although its cell allows more.
* **Report.** Pinned phases are one INFORMATION line ('Phase pinned by cadence (information, not a risk):
  S18 (±0.083 ms, frames), ...'), never a warning or a segment note; razor segments -- and pinned ones whose
  configured `--ae-time-mode stretch|remap` keeps them out of the frame-exact export -- are the ONE aggregated
  warning (`pipeline.flag_ae_rule_sensitive`, cutlist.warnings) plus an 'AE-rule-sensitive (slack ...)'
  note, and the report's 'AE-rule-sensitive segments' line.
* **Conservative export = frames mode (decided).** After Effects' time representation is unverified (no
  .aep / aerender yet): footage rates read back as float32 (2^-24 relative = 0.01 frame only at RAW frame
  ~168,000, 1.9 h at 24p -- the tolerance covers it, 2/1001 frame does not beyond ~33,500 frames = 23 min);
  startTime / key times may be quantised. A warning would leave a risk the tool can remove; a 1e-6 s
  tolerance would assume what nobody measured (rejected in the diagnosis). So `--ae-time-mode auto` exports
  every RAW layer whose exact slack is below the tolerance with frame-exact HOLD keys at (j + 0.25)/raw_fps
  (0.25 frame of slack against any time representation; audio from the existing audio-only stretch twin).
  That is what makes 'pinned' honest information. Cost: per-frame keys on those layers (cadence-pinned
  short layers and NTSC-in-integer layers longer than ~250 / ~1001 frames, whose lattice never offers 0.01
  frame); a forced `--ae-time-mode stretch|remap` keeps them and says so.
* **AE's own mapping in the JSX.** Besides the read-back recomputation (which only re-does the plan's floor()
  in JS), every RAW video layer is checked with After Effects' OWN sourceTime() (an expression on a
  temporary Slider Control, valueAtTime(t, false)) / footage.frameDuration; a stretch layer it contradicts
  goes frame-exact; residuals and slacks go to `ae_time_check.txt`. s9_6 reports them (`ae_time`) together
  with the frames aerender rendered right -- the evidence on which `ae_slack_tol_frames` may later be lowered.
* Tests: `test_phase_solve` (slip cell n = 12 / 81 pinned at 4/1001; S32 replica ≥ 1/(4·1001) after
  placement, exact frames kept), `test_phase_slack` (the real run's numbers: 9 pinned -> information, S19 /
  S26 / S32 razor; the S26 D3 replica), `test_export_ae` (frames export, startTime ±1e-6 s invariance, the
  mock JSX's sourceTime check catching what the read-back misses), `test_verify` (s9_6 calibration),
  `test_synthetic` (simulate_ae with startTime ±1e-6 s identical for every layer on mini / film24).
* A Frame Mix layer (§7.4) is the one stretch / remap layer allowed below the slack tolerance: its picture
  (1 - f) RAW[j] + f RAW[j + 1] is continuous in the position, so the floor rule decides nothing visible; its
  POSITION is what the JSX's AE source-time check judges (within 1e-3 frame).

### 7.4 Honest NOT-IN-RAW / UNRESOLVED / freeze decisions (FX-08)

The first real run called 22 frames NOT-IN-RAW that were RAW content (596-604: RAW 1061-1070 at v = 1, rejected
only because SIFT found 8-10 inliers where min_inliers is 12; 1180-1191 / 1193: motion-blurred RAW at 0.50-0.89),
failed c2 three times because refine (NONE below match_thresh) and verify (placeholder below none_thresh) applied
different rules, and showed a 10-frame freeze on RAW 1673 while the competitor moved (frames measured 1672 and 1673
'shared' one freeze at the single point x = 1673 of the tolerant constraints). Rules since then (§3, §5):
* **Three states.** NONE / NOT-IN-RAW only when EVERY evaluated hypothesis is below none_thresh; the best in
  [none_thresh, match_thresh) is UNRESOLVED -> an 'uncertain' segment: amber solid + a guide layer of the
  best-evidence RAW frames + a marker 'UNCERTAIN - best RAW a-b, ZNCC x-y', a criterion-3 FAILURE, never an
  exception or a placeholder. Verify's c2 placeholder check keeps its own implementation of the same rule (all
  neighbour hypotheses incl. the boundary frame held and the neighbour across the placeholder).
* **Search before giving up** (refine 3 / 5b): windows widen to track_search_radius whenever the best is below
  none_thresh; neighbouring runs' lines are scored across short gaps; a line-constrained SIFT re-search with
  relaxed RANSAC acceptance in the neighbours' predicted window yields CANDIDATES the anchor's ZNCC test must
  still accept (min_inliers itself is unchanged: inlier counts did not separate good from bad anchors in the
  real run); gray-zone RANSAC matches seed weak tracks that only provide UNRESOLVED evidence.
* **Promotion only by a detail-sensitive score** (blur-matched gradient ZNCC with a margin over RAW jb ± 1, ± 2
  under their own framing) -- never by the plain score: ZNCC is non-discriminative on sharpened / blurred content.
  film24's lookalike (best 0.66-0.70 against its model shot, detail 0.13-0.20) is therefore 'uncertain', not
  NOT-IN-RAW: the spec's rule (every hypothesis below none_thresh) decides, and the gray zone claims nothing.
* **Identity test** contrast-relative, a max over tiles, on the layout's masks only (dark dashboard frames with a
  dimming display were 'ambiguous-identical' on residual-masked pixels).
* **Freeze only when the competitor is static** (transform-compensated, captions masked, noise floor from its own
  repeat pairs; a 1-in-5 repeat cadence means v = 1, not a freeze), and never on a single-point tie (§5
  phase_solve). A hold the competitor does not share is 'uncertain', unless a frame-blend path explains it.
* **Frame blend** (segment RETIMING): the blends' continuous positions give the path, a snapped speed and the
  measured phase; exported as AE Frame Mix only when the fixed mix reaches match_thresh on every frame;
  otherwise 'retimed / interpolated - unresolved'. Verify judges a Frame Mix frame by its dominant source.
* Rejected (diagnosis): promoting every 0.6-0.9 frame to a match, an 'approximate' c3 exception, one shared
  placeholder function for segment and verify (destroys verify's independence), optical-flow fitting of slow
  motion on sharpened frames (unverifiable), making a freeze 'more expensive' instead of infeasible, a dense
  ±2 s ZNCC scan along the audio line, changing the global min_inliers.
* Tests: `test_refine` (window widening below none_thresh, the anchorless clip found by the line-constrained
  search, MATCH / UNRESOLVED / NONE), `test_scoring` (tile identity, detail score), `test_segment` (uncertain vs
  placeholder, freeze admission incl. an animated caption, the cadence, the single-point tie, the 0.25 frame-blend
  path), `test_phase_solve` (freeze_gap), `test_verify` (c2 placeholder hypotheses, uncertain = c3 failure, Frame
  Mix dominant frame), `test_export_ae` / `test_render_preview` (Frame Mix, uncertain solid + guide, mock), film24
  (two-clip pan exact, gray chain one uncertain segment, lookalike never matched, blend 0.25 Frame Mix, true
  freeze v = 0, dark shot on its line).
