# match_cuts — design contract

This document is the single contract between modules. The user-facing requirements live in
`MATCH_CUTS_PROMPT.md` (repo root) — every stage/criterion number below refers to it. When this
document and the prompt disagree, the prompt wins; fix this document.

Foundation modules (already written, shared by all — do not fork their conventions):
`common.py` (time math, timecodes, cache, decision log), `geometry.py` (coordinate conventions,
Sim, OpenCV/AE conversions, rounded-rect mask, RDP), `media.py` (PTS-indexed `VideoReader`,
`decode_pts`, `extract_audio`, `FFmpegWriter`), `model.py` (dataclasses: `StreamInfo`, `Proxy`,
`Layout`/`Box`/`Zone`, `FrameMap`/`Status`, `AudioHints`, `Segment`, `Cutlist`), `scoring.py`
(masked ZNCC in competitor space), `config.py` (`Config`: every threshold).

Python ≥ 3.10, venv at repo root `.venv/` (`/home/user/MovieRecaps/.venv/bin/python`). Package is
installed editable (`pip install -e tools/match_cuts`). Run tests from `tools/match_cuts/`:
`../../.venv/bin/python -m pytest -q tests/<file>`.

---------------------------------------------------------------------------------------------------

## 1. Pipeline overview

```
cli.main -> pipeline.run(cfg)
  S0  env check                       (pipeline)
  S2  probe both inputs               probe.probe()                -> StreamInfo x2 (work/probe_*.json)
      conform to AE-safe media        conform.conform()            -> output/media/*  (+ verification)
      re-probe the AE files           probe.probe()                -> analysis happens ONLY on these files
  S3  proxies + audio                 proxies.build_proxy(), proxies.load_audio()
  S4  layout                          layout.analyze_layout()      -> Layout (+ debug/layout.png)
  S5.1 audio coarse alignment         audio_align.coarse_align()   -> AudioHints
  S5.2 visual candidate search        visual_match.RawIndex / sparse_search() -> list[Anchor]
  S5.3 frame-exact refinement         refine.build_frame_map()     -> FrameMap m(k)  (+ overlay masks pass 2)
  S5.4-5.5 segmentation + framing     segment.build_segments()     -> list[Segment] (transitions, keys)
  S5.6 audio per segment              audio_align.analyze_segments_audio()
  S6  phase solve + cutlist           phase_solve.solve_segment(); pipeline assembles Cutlist -> cutlist.json
  S7  AE project                      export_ae.write_jsx()        -> build_ae_project.jsx (+ mock run)
  S8  exports                         export_xml_edl.*, render_preview.render_preview(), render_compare()
  S9  verification                    verify.verify_all()          -> VerifyReport (+ debug/cuts/*)
  S10 report                          report.write_report()        -> report.md
```

Each stage is a pure-ish function of (inputs, cfg) with results cached in `WORK_DIR/cache/<stage>/`
keyed by `params_hash(file_hash(input), cfg.analysis_params(), <stage-specific>)`. A second run with
the same inputs reproduces a byte-identical `cutlist.json` (criterion 9.7): no wall-clock times,
no randomness without `cfg.seed` (`cv2.setRNGSeed`, `np.random.default_rng(seed)`), deterministic
iteration order, multiprocessing results gathered in input order.

Every decision is logged with evidence through `common.DecisionLog` (`work/decisions.jsonl`):
`dlog.record(stage, decision, comp_frame=..., evidence={...}, rejected=[...])`.

## 2. Conventions

### 2.1 Time
* Frame rates: `fractions.Fraction` (`common.parse_fps`, `common.fps_str` -> `"30000/1001"`).
* Competitor frame `k` is displayed at `t_k = k / comp_fps`; RAW frame `j` at `j / raw_fps`.
  The AE-imported files (conformed if needed) start at t = 0 — analysis only uses those files.
* Intervals are half-open `[in, out)`. Seconds only in outputs (≥ 6 decimals; we write 9).
* Speed `v` = Δ RAW seconds / Δ competitor seconds. Never from frame counts (29.97 in 30 = 1.000).
* Frame index of a decoded frame = `round((pts - stream_start) * time_base * fps)` (`media.VideoReader`).
* AE sampling rule (Stage 6): at comp frame `k` a layer shows RAW frame
  `floor(raw_fps * (raw_in + v * (t_k - t_in)) + 1e-9)` with `t_in = comp_in / comp_fps`.

### 2.2 Coordinates  (`geometry.py`)
* CORNER convention everywhere outside OpenCV calls: pixel (i, j) covers `[i,i+1)×[j,j+1)`.
  OpenCV (warpAffine, keypoints) uses centres at integers: `p_cv = p_corner - 0.5`.
  Convert only with `geometry.to_cv_matrix` / `from_cv_matrix` / `warp_raw_to_comp`.
* Canonical transform `Sim(s, theta_deg, tx, ty)` maps RAW **full-res** pixels — after horizontal
  flip `x' = W_raw - x` when `flip_h` — to competitor **full-res** pixels:
  `p_comp = s·R(θ)·p_raw' + t`, `R(θ) = [[cos,-sin],[sin,cos]]` (y-down; clockwise-positive, = AE).
* Proxies (`cv2.resize`, INTER_AREA) have per-axis ratios `(rx, ry) = (w_p/W, h_p/H)`, and CORNER
  coords scale exactly: `p_proxy = diag(rx, ry)·p_full`. Always pass these ratios to the geometry
  helpers; never assume rx == ry.
* RANSAC/ECC results between proxies are converted back with `from_cv_matrix(M_cv, flip, W_raw,
  raw_ratio, comp_ratio)` and projected to a similarity at full resolution.
* Rotation is included only if |θ| > 0.2° (cfg.rotation_min_deg); otherwise θ = 0 and the Sim is
  re-fitted without rotation.

### 2.3 After Effects transform (`geometry.sim_to_ae`)
`r` = target comp px / competitor px, `c = [W_raw/2, H_raw/2]`:
```
Anchor = c ;  Scale = [(flip?-1:1)·100·s·r, 100·s·r] ;  Rotation = θ
Position = r·(s·R(θ)·c + t − origin)      origin = Video Box origin inside the pre-comp, else (0,0)
```
`geometry.ae_to_matrix` is the inverse used by tests and by the AE simulation. Unit test:
`tests/test_geometry_ae.py` compares `cv2.warpAffine` renders of both paths.

### 2.4 Layout modes
* `match`  — MAIN = competitor size (× r). A `Video Box` pre-comp (box size × r) holds the segment
  layers with `origin = (box.x, box.y)`; it is placed in MAIN at the box position with a rounded-rect
  mask of `box.corner_radius`; background under it; guide layers for zones.
* `fill`   — MAIN 1080×1920 (or `--comp-size`), no box. Per segment: RAW point at the centre of the
  competitor box -> frame centre; zoom = s · (cover_scale_frame / cover_scale_box); clamp so no empty
  edges show. Implemented by `export_ae.fill_transform(sim, flip, box, raw_wh, target_wh)` and used
  identically by the preview renderer.
* `source` — MAIN at RAW size and RAW fps, identity transforms, cuts only.

## 3. Data model (`model.py`)
* `StreamInfo` — probe result. `ae_issues` non-empty ⇒ must conform. `rotation` = clockwise degrees
  to display = `(-displaymatrix_rotation) % 360`.
* `Proxy` — memmapped gray uint8 `[N, h, w]`, `ratio`, `full_size`, `fps`, `pts` (seconds).
* `Layout` — `box` (full-res competitor px), `background`, `zones`, `periods`, `static_mask_file`
  (`.npy` bool at competitor proxy res, True = static), `overlay_mask_file` (`layout.OverlayMasks`).
* `FrameMap` — m(k) column store (see `FRAME_MAP_FIELDS`), statuses in `model.Status`.
  `raw_lo..raw_hi` (inclusive) = RAW frames visually identical to `raw` in the visible region
  (ambiguous-identical); equal to `raw` when unique.
* `AudioHints` — per competitor audio window: `comp_t`, `raw_t`, `speed`, `conf`, `psr`, `peak`.
* `Segment` / `Cutlist` — `cutlist.json` schema = prompt Stage 6 schema + the extra fields in
  `model.Segment` (keep all prompt fields). `transform` is the canonical Sim dict
  `{scale, rotation_deg, tx, ty}`; `transform_keys` = `[{comp_frame, scale, rotation_deg, tx, ty}]`
  (absolute competitor frame numbers, linear interpolation — `geometry.interpolate_keys`).
  `time_remap_keys` = `[{comp_frame, raw_seconds}]` for freeze/reverse/ramp segments (else empty).
  Segment `type`: `raw`, `not_in_raw` (placeholder), `dip` (uniform colour frames),
  `flash` (1–2 frame uniform white/colour). Crossfades are two `raw` segments that overlap by
  `transition_in.duration_frames` (the incoming segment carries `transition_in`, the outgoing one
  `transition_out`); coverage tiles exactly except for these overlaps.

## 4. Thresholds
All in `config.Config`. Tune on the synthetic test; document every changed default in README.

## 5. Module contracts

Signatures are the contract; internals are free. Every module has unit tests in
`tests/test_<module>.py` that run in < 60 s using small synthetic arrays / clips made on the fly
(short ffmpeg `lavfi` clips in `tmp_path` are fine). Only `tests/test_synthetic.py` is slow.

### probe.py
```python
def probe(path, role, work_dir, decode=True) -> StreamInfo
    # ffprobe JSON (-show_streams -show_format, side data) + one full decode pass (media.decode_pts)
    # -> exact decoded count, PTS array saved to work_dir (pts_file, seconds), CFR/VFR (PTS-delta
    # jitter > 0.1 frame => VFR), rotation (display matrix), SAR/DAR, stream start times, edit list
    # detection (first PTS != start_time or ffprobe side data), audio info, file hash, ae_issues.
def ae_issues(info) -> list[str]
    # not AE-safe: codec not in {h264, prores, (hevc: warn only)}, container not mp4/mov, VFR,
    # start_time != 0 (either stream, > 1 ms), edit list, opus/vorbis audio, rotation != 0, SAR != 1,
    # odd dimensions for 4:2:0, missing nb_frames consistency.
```

### conform.py
```python
def conform(info: StreamInfo, role, cfg, dlog) -> ConformResult  # dataclass(path, conformed, reason, verification)
    # RAW: AE-safe -> copy into output/media/ unchanged (or reference absolute path if > cfg.large_file_bytes).
    #      else -> output/media/raw_ae.mov: ProRes 422 LT (cfg.conform_codec 'auto': LT if <= 10 min,
    #      else H.264 CRF 12) at SAME resolution & SAME fps, CFR, start 0, PCM s16le 48 kHz, rotation
    #      baked in, square pixels. VFR RAW -> CFR at its nominal rate (the mapping handles it).
    # COMPETITOR: always output/media/competitor_ref.mp4 (H.264 yuv420p + AAC); copy if AE-safe,
    #      else transcode (VFR -> CFR at nominal rate via fps filter = "frame displayed at t_k").
    # Verification when transcoded: identical frame count (or expected count for VFR->CFR), and
    #      >= 50 frames sampled by PTS: SSIM(conformed[j], original[j]) > 0.98 and greater than
    #      SSIM vs original[j±1] (proves no offset). Results in ConformResult.verification.
```

### proxies.py
```python
def build_proxy(info: StreamInfo, role, cfg, cache) -> Proxy
    # one sequential decode (media.VideoReader, fmt='gray', display orientation) into a np.memmap
    # [N, h, w] uint8 in WORK_DIR (cached by file hash + size). RAW width = min(cfg.raw_proxy_width,
    # budget-derived width), competitor = full * cfg.comp_proxy_scale capped at comp_proxy_max_width.
    # h, w rounded to even ints keeping aspect; ratio = (w/W, h/H). Frame count must equal
    # info.nb_frames (else raise). Uses multiple threads for decode where possible.
def load_audio(info, sr, cache) -> np.ndarray   # mono float32 at sr, aligned so sample 0 = video t 0
def load_audio_full(info) -> tuple[np.ndarray, int]   # original rate, all channels (N, C)
```

### layout.py
```python
def analyze_layout(comp: Proxy, cfg, cache, debug_dir, dlog) -> Layout
    # 1 static mask: temporal std over all frames (streamed; e.g. Welford over the memmap) < cfg.static_std_thresh
    # 2 video region: dynamic pixels -> largest dynamic rectangle via row/col dynamic fractions,
    #   refined to sub-pixel edges; corner radius from the corner profile (fit r on the first dynamic
    #   x per row in the corner: x(y) = r - sqrt(r^2 - (r - y)^2)); border/stroke if present.
    #   Layout changes over time (fullscreen vs boxed, split, PiP): per-time-window dynamic masks ->
    #   Layout.periods; extra regions -> Layout.extra_regions + warning.
    # 3 background: sample colour outside the box (solid), else compare with blurred cover-scaled
    #   content (type 'blur'), else 'image'/'gradient'.
    # 4 static zones (logo/header/title/watermark) = connected components of static non-background
    #   pixels, classified by position; dynamic overlays inside the box (captions, stickers): text-like
    #   high-contrast detection (morphological gradient / MSER on white-with-dark-outline text) ->
    #   OverlayMasks initial pass + caption timing -> Layout.captions.
    # Writes debug/layout.png showing all zones.
class OverlayMasks:     # per-frame bool masks at comp proxy res, stored compactly (packbits / RLE per frame)
    def get(self, k) -> np.ndarray | None           # None = no overlay on frame k
    def set(self, k, mask) ; def union(self, k, mask) ; save(path) ; load(path)
def allowed_mask(layout, overlays, k, comp: Proxy) -> np.ndarray
    # bool [h, w] at comp proxy res: inside rounded box AND NOT static AND NOT dilated overlay(k)
def box_mask_proxy(layout, comp: Proxy) -> np.ndarray   # float coverage of the rounded box at proxy res
def masks_from_residuals(residuals: dict[int, np.ndarray], base_allowed, cfg) -> dict[int, np.ndarray]
    # regions with consistently high |comp - warped raw| (>= cfg.overlay_min_frames consecutive) -> overlay masks
```
Box semantics: `Box(x, y, w, h, corner_radius)` in competitor full-res CORNER coordinates — the exact
rectangle the video is clipped to (e.g. x=60 means the first video column is pixel 60).

### audio_align.py
```python
def features(y, sr, cfg) -> dict   # {'logmel': [T, B] float32 at cfg.audio_feat_rate Hz, 'onset': [T]}
def coarse_align(comp_y, raw_y, sr, cfg, dlog) -> AudioHints
    # FFT-based normalised cross-correlation of ~1 s competitor windows (hop 0.25 s) against the
    # whole RAW (log-mel + onset at 100 Hz), top peaks; sample-precise refinement on the 16 kHz
    # waveform within +-50 ms; if weak at v=1, time-scaled windows v in [0.90, 1.30] step 0.01.
    # conf = peak / second peak (second peak outside +-0.3 s of the best), psr = peak-to-sidelobe.
def xcorr_lag(a, b, sr, max_lag_s) -> tuple[float, float]   # (lag_s, peak): b delayed by lag vs a
def analyze_segments_audio(segments, comp_y, raw_y, sr, comp_fps, cfg, dlog) -> dict
    # per segment: J/L cut offsets (in/out offset frames), pitch preserved? (for speed != 1),
    # added audio (music/sfx/voice-over) ranges from residual energy vs the RAW-rebuilt track,
    # 'audio_replaced' flag when the competitor audio doesn't correlate with RAW anywhere.
    # returns {'segments': {id: {...}}, 'added_audio': [...], 'notes': [...]}
```

### visual_match.py  (Stage 5.2)
```python
class RawIndex:        # SIFT (cv2.SIFT_create(nfeatures=cfg.sift_nfeatures)) on sampled RAW proxy frames
    @staticmethod
    def build(raw: Proxy, cfg, cache) -> "RawIndex"       # sampled every round(raw_fps/index_fps) frames; cached npz
    frames: np.ndarray            # sampled RAW frame indices
    def query(self, desc, top: int, window=None) -> list[tuple[int, float]]   # (raw frame, votes)
@dataclass
class Anchor:          # verified match of one competitor frame
    k: int; raw: int; flip: bool; sim: Sim  # canonical full-res
    inliers: int; inlier_ratio: float; votes: float; source: str   # 'global' | 'audio' | 'track'
def search_frame(k, comp: Proxy, raw: Proxy, index, allowed, cfg, window=None) -> list[Anchor]
    # SIFT on the comp frame restricted to `allowed` (box minus static/overlay), query normal AND
    # horizontally-flipped descriptors, shortlist by temporally-smoothed votes, verify each candidate
    # with pairwise matching (ratio 0.75) + cv2.estimateAffinePartial2D RANSAC; flipped candidates
    # are verified against the flipped RAW frame. Accept inliers >= 25 and ratio >= 0.3.
def sparse_search(comp, raw, layout, overlays, index, hints: AudioHints, cfg, dlog, frames=None) -> list[Anchor]
    # every cfg.comp_search_stride frames (or `frames`); restrict to +-2 s around confident audio
    # hints first, fall back to global. Multiprocessing over frames (fork; memmaps shared).
```

### refine.py  (Stage 5.3, produces m(k))
```python
def build_frame_map(comp, raw, layout, overlays, anchors, hints, index, cfg, cache, dlog) -> FrameMap
    # 1 link anchors into tracks (same flip, similar Sim, consistent raw-vs-k slope incl. 0/negative)
    # 2 for every frame k: candidate tracks active near k; predicted raw ĵ from the track's robust
    #   linear fit; transform interpolated; score RAW frames ĵ-R..ĵ+R (R = cfg.refine_radius, widened
    #   to cfg.track_search_radius when the prediction is uncertain) with scoring.score_candidates.
    # 3 refine the transform (ECC, cv2.findTransformECC MOTION_AFFINE on proxies, then projected to
    #   a similarity at full res) on the best (k, j) — every frame near cuts, every
    #   cfg.framing_sample_step elsewhere, interpolated between.
    # 4 overlay pass 2: residual-based masks (layout.masks_from_residuals), re-score with them.
    # 5 frames with best score < cfg.match_thresh: run visual_match.search_frame on them (catches 1–2
    #   frame flash cuts the sparse search missed), add tracks, re-score. Remaining: region uniform
    #   (std < cfg.uniform_std) -> UNIFORM; else NONE.
    # 6 fill raw_lo/raw_hi: extend around `raw` while ZNCC(RAW[j], RAW[j±1]) over the visible region
    #   >= cfg.identical_thresh or |score diff| <= cfg.ambiguous_eps; second/margin = best score
    #   outside [raw_lo, raw_hi]; conf = f(score, margin).
def refine_transform(comp_img, raw_img, sim0, flip, raw_w, raw_ratio, comp_ratio, allowed, cfg) -> tuple[Sim, float]
```

### phase_solve.py  (Stage 6 exact phase solve; pure math, no I/O)
```python
def frame_constraints(ks, lo, hi, comp_fps, raw_fps) -> ...   # half-planes for (raw_in, v)
def feasible_speed_range(ks, lo, hi, comp_in, comp_fps, raw_fps, v_bounds=(-4, 4)) -> tuple[float, float] | None
    # is there (raw_in, v) with floor(raw_fps*(raw_in + v*(t_k - t_in))) in [lo_k, hi_k] for all k?
    # 2-variable LP (scipy.optimize.linprog, HiGHS) -> [vmin, vmax] or None.
def longest_feasible_prefix(ks, lo, hi, start, comp_fps, raw_fps) -> int   # binary search on the end
def solve_raw_in(ks, lo, hi, comp_in, v, comp_fps, raw_fps) -> dict
    # {'raw_in': centre of the floor-feasible interval (or of its overlap with the round-to-nearest
    #   feasible interval when non-empty), 'interval_floor': [a, b), 'interval_both': [a, b) | None,
    #   'margin_s': ..., 'ok': bool}
def ae_frame(raw_in, v, k, comp_in, comp_fps, raw_fps, rule='floor') -> int
def snap_speed(v_measured, vrange, cfg) -> float     # snap to cfg.speed_snap_values within 0.3 % if feasible
```

### segment.py  (Stage 5.4–5.5)
```python
def build_segments(fm: FrameMap, comp, raw, layout, overlays, cfg, dlog, debug_dir) -> list[Segment]
    # runs of MATCH frames with the same track & flip -> split greedily into maximal
    # phase-feasible pieces (phase_solve.longest_feasible_prefix) and at transform jumps
    # (punch-in cuts: scale step > 1 % or position step > 4 px between consecutive frames).
    # speed: robust fit + feasible range + snap. Cuts verified from both sides (criterion 2):
    # last frame of A scores higher against A's model than B's and vice versa; move the cut
    # otherwise. Transitions: frames around each cut with low single-source scores are fitted
    # with scoring.fit_blend(A_pred(k), B_pred(k)) -> crossfade (alpha curve, duration) and
    # A/B extended to overlap; dips (blend with a uniform colour) and flash frames (UNIFORM runs
    # of 1–2 frames); NONE runs -> 'not_in_raw' placeholder segments; freeze (v≈0), reverse (v<0),
    # ramps (consecutive pieces with continuous m and smoothly varying v -> time_remap_keys).
    # Framing (5.5): refined Sim every cfg.framing_sample_step frames; stable -> constant transform;
    # else animated: smooth, geometry.rdp with (rdp_pos_tol, rdp_scale_tol) -> transform_keys;
    # easing detection is informational (keys stay linear). Rotation only if |θ| > 0.2°.
    # PySceneDetect cross-check (AdaptiveDetector + ContentDetector, low thresholds) on the
    # competitor: each detected change must coincide (±1 frame) with a cut or transition; log and
    # explain disagreements (returned in segment notes / dlog).
    # Writes debug/mapping.png (comp time x vs RAW time y; segments as lines, cuts as jumps,
    # NOT-IN-RAW shaded) and debug/scores.png (score, margin per frame, thresholds, cuts).
```
The pipeline then calls `phase_solve.solve_raw_in` per segment and fills `raw_in_seconds`,
`raw_in_interval`, `raw_in_interval_both`, `speed`, `speed_range`.

### export_ae.py  (Stage 7)
```python
def ae_plan(cutlist: Cutlist, cfg) -> dict
    # Every number the JSX will set, computed in Python (single source of truth for the JSX AND the
    # AE-semantics simulation): comps (size, fps num/den, duration frames), per-layer
    # {name, source, startTime, stretch, inPoint, outPoint, time_remap keys, transform (AE params
    # from geometry.sim_to_ae, keys), opacity keys, flip, audio flags}, solids, guides, markers.
def write_jsx(cutlist, plan, out_path, cfg) -> None      # ES3 ExtendScript, data embedded as object literal
def simulate_ae(plan, comp_fps, raw_fps) -> dict[int, list[tuple[layer, raw_frame, opacity]]]
    # from the exact startTime/stretch/inPoint/outPoint/time-remap values: which RAW frame each layer
    # shows at every comp frame (floor rule on AE's layer time).
def run_jsx_in_mock(jsx_path, media_dir) -> dict
    # executes the JSX with node + match_cuts/ae_mock/ae_mock.js (a strict mock of the AE DOM subset
    # used: app, Project, CompItem, FootageItem, FolderItem, LayerCollection, AVLayer, Property,
    # Shape, MarkerValue, ImportOptions, File/Folder, $.fileName, alert, enums). Unknown members throw.
    # Returns the recorded project (comps, layers with every property value/key set, saved path,
    # alerts). ES3 syntax check (no let/const/arrow/template/forEach/map/JSON) is part of it.
def fill_transform(sim, flip, box, raw_wh, target_wh) -> Sim    # §2.4 fill mode
TIME_MODE in the JSX: "stretch" (default, per prompt 7.4) or "remap" (every layer time-remapped with
two keys; immune to sub-frame startTime quantisation). Both are simulated in verification.
```

### export_xml_edl.py  (Stage 8)
```python
def write_csv(cutlist, path) ; def write_fcp7_xml(cutlist, path, cfg) ; def write_edl(cutlist, path, cfg)
def validate_exports(cutlist, xml_path, edl_path) -> dict   # re-parse (OTIO fcp_xml / cmx_3600
    # adapters + own parser), check total duration == competitor frames and per-clip in/out/speed.
```

### render_preview.py  (Stage 8)
```python
def render_frame(k, ctx) -> np.ndarray               # BGR at output size: background, layers (flip,
    # warpAffine with interpolated keys, crossfade alpha, dips), rounded box mask, placeholders.
def render_preview(cutlist, raw_path, out_path, cfg, layout_mode=None) -> dict
    # frame-exact own renderer: per segment one seek + sequential decode (VideoReader), RAW frame
    # from phase_solve.ae_frame (the AE rule), writes via media.FFmpegWriter (H.264 CRF <= 16,
    # yuv420p, +faststart), audio built sample-accurately from RAW (tape-style resample for speed,
    # crossfades applied) and muxed. No competitor overlays. Returns {'frames': n, 'raw_frames': [...]}.
def build_audio(cutlist, raw_audio, sr) -> np.ndarray
def render_compare(comp_path, preview_path, cutlist, out_path, cfg) -> None
    # hstack competitor | recreation | amplified |diff|, same height, burned-in frame number,
    # timecode and segment id; competitor audio.
```

### verify.py  (Stage 9)
```python
def verify_all(ctx) -> dict   # {'criteria': {name: {'pass': bool, 'details': ...}}, 'failures': [...]}
    # 1 coverage; 2 AE-semantics simulation (from export_ae.ae_plan values; and from the mock-run
    # record) == m(k) for every matched frame (ambiguous-identical excepted: m in [raw_lo, raw_hi]);
    # 3 visual: masked ZNCC competitor vs preview per frame (distribution, failures -> thumbnails);
    # 4 cut images debug/cuts/cut_XX.png (k-1, k, k+1, k+2 competitor vs recreation);
    # 5 audio lag per segment within ±10 ms (explain exceptions);
    # 6 AE render check if aerender exists (else 'not available');
    # 7 determinism: re-run segmentation from caches and compare cutlist JSON bytes.
    # Also: frame-exact source frames >= 99 % (criterion 3), speed/framing/flip (criterion 4)
    # against measured per-frame values.
```

### report.py (Stage 10), pipeline.py, cli.py
```python
def write_report(ctx, path) -> None      # sections exactly as prompt Stage 10
def run(cfg: Config) -> dict             # pipeline.py: orchestrates S0..S10, returns summary
def main(argv=None) -> int               # cli.py:  python -m match_cuts --competitor X --raw Y --out Z
    #   [--layout match|fill|source] [--comp-size WxH|competitor] [--fps competitor|source]
    #   [--work DIR] [--workers N] [--force-conform] [-v]
```
`ctx` is `pipeline.Context` (dataclass holding cfg, infos, conform results, proxies, layout,
overlays, hints, anchors, frame_map, segments, cutlist, plan, paths, verify results, dlog).

## 6. Synthetic test (Stage 1) — `tests/synth.py` + `tests/test_synthetic.py`

`synth.make_synthetic(out_dir) -> dict` (cached; deterministic) creates:
* `raw.mp4` — 1920×1080, 30000/1001 fps, 5400 frames (~180 s), H.264 (B-frames) + AAC 48 kHz.
  12 "shots" of 450 frames from different lavfi generators with motion (testsrc2, mandelbrot, life,
  cellauto, smptehdbars + moving overlay, …) so every frame is unique, plus a large burned-in frame
  counter (`drawtext … %{n}`); unique audio (modulated `aevalsrc` tones + enveloped `anoisesrc`).
* `competitor.mp4` — 1080×1920 @ 30 fps, ~50–60 s, black canvas, rounded-corner box (e.g. x=60 y=460
  w=960 h=1000 r=40), channel logo+name at the top, multicoloured static title, word-by-word
  captions over the box (drawtext enable=between), static watermark under the box, music mixed under
  the original audio. ~20 cuts incl.: two same-shot jump cuts, one out-of-order hook, one re-used
  moment, one 1.10× segment, one hflipped segment, one slow push-in (animated zoom), one 6-frame
  crossfade (xfade), a 1 s NOT-IN-RAW insert (a generator not used in RAW), one punch-in (same shot,
  continuous time, framing jumps). Geometry and timing are made ONLY with ffmpeg filtergraphs.
* `truth.json` — the known edit, MEASURED, not assumed: the same timing chains are applied to a
  lossless "ID video" (each frame encodes its RAW index as a binary block code; identical timestamps
  to raw.mp4 — asserted by comparing decoded PTS arrays) and decoded, giving the exact RAW frame for
  every competitor frame; plus per-segment geometry (flip, scale, position/keys in canonical Sim
  terms), speed, transition frames, NOT-IN-RAW range, overlay zones/timings.
`test_synthetic.py` runs the full pipeline (CLI) on these files and asserts exact recovery:
cuts ±0 frames, source frames ±0 (every frame), speeds ±0.5 %, flip, framing ±1 % / ±4 px,
zoom keys reproduce the truth within tolerance, crossfade (6 frames), NOT-IN-RAW placeholder range,
plus all Stage 9 criteria pass.
