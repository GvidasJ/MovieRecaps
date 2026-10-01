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
  region (RAW-vs-RAW, warped & masked: mean |diff| ≤ `identical_mad` or ZNCC ≥ `identical_thresh`) —
  the only criterion-3 exemption; `low_margin` flag = score gap ≤ `low_margin_eps` (never an
  exemption); `soft_lo..soft_hi` = soft range for the LP `{j : S_k(j) ≥ max S_k − δ_k}` with
  `δ_k = scoring.noise_delta(track's best scores) = clip(3·1.4826·MAD(best scores), soft_delta_min,
  soft_delta_max)` — the score NOISE, never the spread of margins (margins measure discriminability; a
  margin-based δ let a frame 0.18 below its own best count as 'explained' and hid a jump cut); `cand_j0` + `cand[k, 0:CAND_W]` = the
  candidate score vector S_k around m (NaN where not evaluated); `widened`, `tie`, `mean`/`std`.
* `AudioHints` — per competitor audio window: `comp_t`, `raw_t`, `speed`, `conf`, `psr`, `peak`.
* `Segment` — prompt Stage 6 fields + extras (see model.py). `type ∈ {raw, not_in_raw, dip, flash}`;
  freeze (v = 0), reverse (v < 0) and ramps are `type = raw` with `time_mode = remap`
  (`time_remap_keys` non-empty). `transform` = canonical Sim dict; `transform_keys` =
  `[{comp_frame, scale, rotation_deg, tx, ty}]` (absolute comp frames, AE-linear). `audio =
  {in_offset_frames, out_offset_frames, pitch_preserved, lag_ms, corr, exception}`: the audio range is
  `[comp_in + in_offset, comp_out + out_offset)` (negative in_offset = J-cut, positive out_offset =
  L-cut; the extension uses the same (raw_in, v) map). `exception` ∈ {too_short, not_in_raw,
  audio_replaced, pitch_preserved, music_dominated, no_audio} (closed list). `retime` ∈ {none,
  frame_blend, optical_flow}, `uncertain`, `unsnapped`, `cut_ambiguity=[a,b]`, `tie_frames`,
  `low_margin_frames`, `ae_margin_ms`, `region`, `box`.
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
def analyze_segments_audio(segments, comp_y, raw_y, sr, comp_fps, cfg, dlog) -> dict
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
    zncc: float; source: str   # 'global' | 'audio' | 'rescue'
def search_frame(k, comp, raw, index, allowed, cfg, window=None) -> list[Anchor]
    # SIFT on comp frame inside `allowed`; query normal + flipped descriptors for votes; verify each
    # candidate with pairwise ratio 0.75 + estimateAffinePartial2D (RAW->comp, §2.2; flip: vs
    # cv2.flip(raw)); accept inliers >= cfg.min_inliers(12) and ratio >= 0.3 AND masked ZNCC of the
    # warped candidate >= match_thresh - anchor_zncc_slack. Before storing: re-estimate against the best
    # EXACT RAW frame near the index frame (score j-3..j+3 under the Sim, refit on the argmax).
def sparse_search(comp, raw, layout, overlays, index, hints, cfg, dlog, frames=None) -> list[Anchor]
    # every cfg.comp_search_stride frames (or `frames`); audio-restricted (±audio_restrict_s) first,
    # global fallback. multiprocessing (fork; memmaps shared; seed_everything per worker).
```

### refine.py  (Stage 5.3, produces m(k))
```python
def build_frame_map(comp, raw, layout, overlays, anchors, hints, index, cfg, cache, dlog, debug_dir) -> FrameMap
    # 1 link anchors into tracks: same flip, |Δs|/s <= link_scale_tol, |Δpos| <= link_pos_tol (after the
    #   track's linear trend), raw-vs-k slope consistent (incl. 0 / negative).
    # 2 transform = per-TRACK model (constant, or RDP keys if animated), never per-frame free ECC:
    #   ECC on sampled frames against the MODEL frame m(k); accept an update only if it raises masked
    #   ZNCC over its init; robust fit over the track; alternate transform fit <-> frame assignment until
    #   m(k) stops changing (<= 3 iterations). Constant pans where raw_in±1 with a refitted transform
    #   scores within noise -> flag 'time_translation_confounded' (dlog + segment notes).
    # 3 for every frame k and every track active near k: predicted ĵ (robust linear fit); score RAW
    #   frames ĵ-R..ĵ+R (R = refine_radius) under the track transform (scoring.score_candidates); if the
    #   argmax is on the window edge, extend in that direction (up to track_search_radius, then
    #   visual_match.search_frame) until interior; store S_k in cand/cand_j0 (CAND_W window centred on m).
    # 4 overlay pass 2: residual masks (layout.masks_from_residuals), re-score.
    # 5 rescue: frames with score < match_thresh OR score < rolling track median(±5) - max(rel_drop_min,
    #   4·MAD) -> search_frame on them (catches 1–2 frame flash cuts, jump cuts inside a track), new tracks,
    #   re-score. Remaining: region std < uniform_std -> UNIFORM; else NONE.
    # 6 raw_lo/raw_hi = visually identical frames (§3); low_margin; soft_lo/soft_hi; conf = f(score, margin).
    # 7 debug/low_confidence/k#####.png for conf < low_conf_thresh (competitor | best warped | 2nd best), max 200.
def refine_transform(comp_img, raw_img, sim0, flip, raw_w, raw_ratio, comp_ratio, allowed, cfg) -> tuple[Sim, float]
```

### phase_solve.py  (Stage 6; pure math, no I/O; §2.1 formulation)
```python
def feasible_speed_range(ks, lo, hi, comp_in, comp_fps, raw_fps, v_bounds=(-8, 8), tau=1e-6) -> tuple[float, float] | None
    # scipy.optimize.linprog (HiGHS) in local units: min / max u subject to the tolerant constraints.
def is_feasible(ks, lo, hi, comp_in, comp_fps, raw_fps, v=None) -> bool
def solve_raw_in(ks, lo, hi, comp_in, v, comp_fps, raw_fps) -> dict
    # Chebyshev LP with u fixed: max t s.t. lo_k+t <= x+u·d_k <= hi_k+1-t, -tau <= t <= 0.5.
    # {'raw_in': seconds (centre; if the round-to-nearest-feasible set overlaps, its centre), 'slack': t*
    #  (frames), 'interval_floor': [a, b] seconds, 'interval_both': [a, b] | None, 'margin_ms',
    #  'tie_frames': [k where slack < 1e-4], 'ok': bool}
def ae_frame(raw_in, v, k, comp_in, comp_fps, raw_fps, rule='floor') -> int   # the AE rule (§2.1)
def snap_speed(v_ols, vrange, cfg, preferred=()) -> tuple[float, bool]
    # candidates = cfg.speed_snap_values ∪ preferred (speeds of already-solved segments) inside the
    # tolerant range; prefer (1) dominant speed of the edit, (2) 1.0, (3) closest to v_ols. None inside ->
    # (clip(v_ols, vmin, vmax), unsnapped=True). Never report the LP centre as the speed.
```

### segment.py  (Stage 5.4–5.5)
```python
def build_segments(fm: FrameMap, comp, raw, layout, overlays, cfg, dlog, debug_dir, hints=None) -> list[Segment]
    # CUTS = DP over candidate cut positions (NOT greedy maximal prefixes: greedy turns 1-frame-skip jump
    #   cuts into fake 1.02-1.05x speeds and pushes boundaries late). Candidates: frames where the
    #   increment deviates from the floor pattern, track / flip changes, transform steps (punch-in:
    #   |Δs| > punch_scale_step or |Δpos| > punch_pos_step — located by scoring every frame between two
    #   sampled transforms under both and cutting where the new one wins by > 3δ_k), audio lag steps.
    #   cost(segment) = 0 if a snap/dominant speed is feasible on the soft ranges, lambda_unsnapped if only
    #   an unsnapped speed is, inf if infeasible (after allowing isolated single-frame violations with
    #   margin < 5δ_k that no competing hypothesis explains); + lambda_cut per cut. Cuts with no RAW
    #   discontinuity (speed change only): at the intersection of the two lines, cut_ambiguity=[a, b].
    # Criterion 2 check per cut (A's last frame scores higher under A's model than B's, and vice versa);
    #   move the cut otherwise; log. A visited set guards the mover (it used to oscillate between two positions
    #   with identical evidence): on a revisit, or after 3 moves, every visited position is re-evaluated (models
    #   refitted for it) by the summed score of A's frames under A + B's frames under B around the positions,
    #   the best is kept, criterion2_fail is logged with {oscillation: positions, scores, repeat_pair (a
    #   position splits a competitor REPEAT pair, temporal.py), reason} and a segment note is added
    #   (_Seg.c2_oscillation keeps the evidence for the continuous-shot / union test). Passing cuts untouched.
    # TRANSITIONS (before NONE runs become placeholders): for every cut and every NONE/low run <=
    #   transition_search between two raw segments, for k in [cut-transition_search, cut+transition_search]:
    #   scoring.fit_blend over A ∈ {Â(k)-1..Â(k)+1} × B ∈ {B̂(k)-1..B̂(k)+1} (phase-model predictions
    #   extended past the segment ends, each warped with its own Sim); blend frame if 0.02 < α < 0.98 and
    #   (1 - zncc_fit) <= blend_rel·(1 - best single-source zncc). Fit α_B(k) = (k-O)/D by least squares
    #   over the blend frames: O = round(zero crossing), D = round(1/slope). Apply §3 crossfade convention,
    #   add the chosen A/B frames as constraints, re-solve. Dips: blends with a uniform colour; flash:
    #   UNIFORM runs of 1–2 frames; NONE runs -> 'not_in_raw' placeholders (label with RAW-free timecodes).
    # RETIMING: within speed != 1 runs, frames with low single-frame score but fit_blend(RAW[j], RAW[j+1])
    #   >= match_thresh with 0.1 < α < 0.9 on >= 20 % of frames -> retime=frame_blend (excluded from the
    #   phase solve; still infeasible -> uncertain=True + warning). Freeze / reverse / ramps -> time_mode
    #   remap + time_remap_keys (freeze key values (j + 0.25)/raw_fps).
    # SPEED: v_ols (robust) -> speed_measured; feasible range -> speed_range; phase_solve.snap_speed.
    # FRAMING (5.5): track model Sims every framing_sample_step frames; stable (scale spread < 0.3 %,
    #   position spread < 1.5 px) -> constant transform; else animated -> smooth, geometry.rdp
    #   (rdp_pos_tol, rdp_scale_tol) -> transform_keys (linear; easing reported in `easing` only).
    #   Rotation only if |θ| > 0.2°. Full affine only if clearly better (report it; AE uses the similarity).
    # PySceneDetect (backend='opencv'; AdaptiveDetector(adaptive_threshold=2.0) + ContentDetector(15)) on
    #   the competitor: each change must coincide (±1 frame) with a cut/transition; disagreements logged
    #   and explained in notes.
    # Writes debug/mapping.png (comp time x vs RAW time y; segments lines, cuts jumps, NOT-IN-RAW shaded,
    #   crossfades marked) and debug/scores.png (score, margin, thresholds, cuts).
```
The pipeline then calls `phase_solve.solve_raw_in` per segment (using the soft ranges) and fills
`raw_in_seconds`, `raw_in_interval`, `raw_in_interval_both`, `ae_margin_ms`, `tie_frames`.

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
    # any mismatch -> switch that layer to 'frames' mode + warning. Remap: stretch=100, startTime=tIn,
    # in/out, assert canSetTimeRemapEnabled, enable, remove ALL keys, setValuesAtTimes, LINEAR (HOLD in
    # frames mode). Every layer: frameBlendingType NO_FRAME_BLEND, quality BEST, samplingQuality BILINEAR
    # (try), motionBlur false; comp.frameBlending = false. startTime/inPoint/outPoint set explicitly on
    # EVERY layer (solids, pre-comp, reference).
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
def simulate_ae(plan, time_mode_override=None) -> dict[int, list[dict]]
    # per MAIN frame: [{layer, raw_frame, opacity}] from the exact plan values (stretch, startTime,
    # in/out, remap keys; floor rule on layer time; HOLD/LINEAR remap semantics).
def run_jsx_in_mock(jsx_path, footage_meta: dict, scenario='default') -> dict
    # node + match_cuts/ae_mock/{ae_mock.js, acorn.js (vendored, MIT)}. ES3 gate: '#' lines -> '//#', acorn
    # {ecmaVersion:3, allowReserved:'never'}, the forbidden-pattern ban, ES5+ APIs deleted inside the vm
    # context, all mock-returned arrays/objects created in the context realm. Strict mock: throws on
    # unknown GET and SET (allow-list for members absent in CC 2019), read-only members, enum type checks,
    # integer/range checks (addComp/addSolid ints in [4, 30000], 0 < duration <= 10800, 1 <= fps <= 999,
    # layer times in ±10800), clamping of non-remapped footage layers to the source extent, float32
    # frameRate, matchName-only property(), 3-element spatial values, keys stored in LAYER time
    # (startTime/stretch changes after keys move them), 1-based collections, layers.add at index 1.
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
    # NOT-IN-RAW placeholder (coloured, labelled). RAW frame per layer = phase_solve.ae_frame (AE rule)
    # or the plan's frames-mode value.
def render_preview(cutlist, raw_path, out_path, cfg, layout_mode=None) -> dict
    # own frame-exact renderer: per segment one seek + sequential decode (VideoReader); two readers for
    # overlaps; FFmpegWriter H.264 CRF <= 16 yuv420p +faststart; audio via build_audio muxed (AAC).
    # Returns {'frames': n, 'raw_frames': {k: [(seg, j, weight)]}}.
def build_audio(cutlist, raw_audio, sr) -> np.ndarray   # sample-accurate; tape-style resample for v != 1
    # (like AE stretch); J/L ranges; crossfade gains as in export_ae; placeholders silent.
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
  #      extra-region frames -> pass_with_exceptions.
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
  #      NOT-IN-RAW neighbours: placeholder frame scores below none_thresh against the extended neighbour model.
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
  #      listed) AND s9_2b AND s9_2c AND s9_3.
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
  # c5 <- s9_5: per-segment lag (recreated vs competitor audio) within ±10 ms, else explained with a code
  #      from the closed list (§3) -> pass_with_exceptions.
  # c6: mock-run (no alert containing 'Error'; MAIN frameRate == main_fps within 1e-9; duration ==
  #      frames·frameDuration; work area == duration; saved path == <script dir>/recreated_edit.aep; one
  #      layer per segment with name/startTime/stretch/in/out == plan; media_missing scenario aborts
  #      cleanly and calls openDialog) + s9_6 aerender if available. Linux: 'pass' means mock-verified;
  #      details say 'mock only'. Node missing -> not_available.
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
margin is ~0.001 ZNCC at thumbnail scale -- no absolute threshold separates them).

### report.py (Stage 10), pipeline.py, cli.py, README.md
```python
def write_report(ctx, path) -> None      # prompt Stage 10 sections: inputs (codecs, fps, sizes, durations,
    # VFR/offset issues, conform + why, fps-source max error), layout (+ layout.png), segment table
    # (# · comp in–out tc+frames · duration · RAW in–out tc · speed · flip · scale/position or 'animated' ·
    # transition · confidence · notes), edit-style breakdown, warnings (low-confidence, ambiguous-identical,
    # timing-tie, NOT-IN-RAW, AE-rule-sensitive segments, extra regions, anything AE can't reproduce),
    # criteria table c1..c6, how to open in AE (+ preference, reference layer), timings.
def check_env() -> dict                  # pipeline.py
def run(cfg: Config) -> dict             # pipeline.py: S0..S10; pipeline.Context dataclass holds everything
def main(argv=None) -> int               # cli.py: python -m match_cuts --competitor X --raw Y --out Z
    #   [--layout match|fill|source] [--comp-size WxH|competitor] [--fps competitor|source] [--work DIR]
    #   [--workers N] [--force-conform] [--ae-time-mode auto|stretch|remap|frames] [-v]
    # prints one line per criterion c1..c6, output paths, warnings; returns 0 only if none is 'fail'.
```
README.md: setup (venv, `pip install --no-deps scenedetect click platformdirs`), CLI usage and flags,
outputs, running the JSX in AE (preference, relink, reference layer), troubleshooting (AE scripting
preference, relink, VFR, fps misread/conformFrameRate, AE-rule-sensitive segments -> `--ae-time-mode
frames`, failing-criterion playbook from Stage 9), changed defaults.

## 6. Synthetic test (Stage 1) — `tests/synth.py` + `tests/test_synthetic.py`

`synth.make_synthetic(out_dir, profile='full'|'mini') -> dict` (cached by a key that includes the
ffmpeg version string and the synth source hash; byte-stable: pinned x264 args incl. `-threads 4`).
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
* **D3 Audio-informed phase.** After the per-segment audio analysis, `raw_in := raw_in + v·lag` for
  confidently correlated stretch segments, clamped into the floor∩round interval (else the floor interval)
  with a margin of `max(ae_min_margin_ms, min(5 % of its width, 5 % of a RAW frame))` from each edge
  (`pipeline.audio_phase_margin_s`; a fraction of a FRAME, not of the ambiguity span, so an in-point on
  the edge of a seconds-wide static interval stays within ~2 ms in audio); the residual lag is
  re-measured. This removes the systematic quarter-frame audio offset of the interval centre (8.3 ms at
  30p, 10.4 ms at 24p) while keeping every frame exact under both sampling rules
  (`Segment.audio.phase_source`, `lag_ms_video`). Segments whose interval is wider than ±100 ms (static /
  ambiguous-identical) also get a wide search centred on the feasible interval and covering all of it
  (half-width capped at 60 s).
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
* **D8 Synthetic.** Competitor audio starts at the frame boundary of each segment's first RAW frame (NLE
  convention); one ~1 s fullscreen segment exercises D1.

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
* **D3 margin** = max(ae_min_margin_ms, min(5 % of the interval width, 5 % of a RAW frame)); the wide
  audio search is centred on the feasible interval (up to ±60 s), so static / ambiguous-identical shots land
  on their audio in-point.
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
