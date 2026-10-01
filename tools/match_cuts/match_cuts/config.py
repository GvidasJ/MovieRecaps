"""Run configuration + every tunable threshold in one place (DESIGN.md §4).

Thresholds are tuned on the synthetic test (tests/test_synthetic.py); change them here, never inline.
"""
from __future__ import annotations

import dataclasses
from dataclasses import dataclass, field
from pathlib import Path

# verification-only settings (hypothesis-neutral checks): never part of the analysis cache keys. The temporal
# signature's measurement / labelling settings are shared with refine (comp-only repeat cadence as time-line
# evidence, FX-07), so they ARE analysis parameters.
VERIFY_ONLY_PARAMS = ("temporal_mag_ratio", "verify_refit_margin",
                      "verify_union_frames", "verify_excursion_frames", "verify_framing_min_samples",
                      "verify_framing_all_max", "verify_low_score_margin", "verify_mix_tie")


@dataclass
class Config:
    # ---- I/O -----------------------------------------------------------------------------
    competitor: str = "./input/competitor.mp4"
    raw: str = "./input/raw.mp4"
    out_dir: str = "./output"
    work_dir: str = "./work"
    layout_mode: str = "match"             # match | fill | source
    comp_size: str = "competitor"          # 'competitor' or 'WxH'
    fps_mode: str = "competitor"           # competitor | source
    force_conform: bool = False            # conform RAW even if AE-safe (testing)
    conform_codec: str = "auto"            # auto | prores_lt | prores | h264   (auto: ProRes LT via prores_aw <= 10 min, else H.264 CRF 12 in .mp4)
    ae_time_mode: str = "auto"             # auto | stretch | remap | frames  (auto: stretch, per-layer fallback, see export_ae)
    ae_slack_tol_frames: float = 0.01      # exact AE floor-rule slack (RAW frames; min over EVERY frame of a layer, from the
                                           # values written) below which --ae-time-mode auto exports the layer frame-exact
                                           # (HOLD keys at j + 0.25) -- AE's time resolution is unverified until s9_6 (FX-10)
    run_ae: bool = True                    # open After Effects (when installed) to run the JSX and save the .aep
    ae_timeout_s: float = 600.0            # how long to wait for After Effects to save recreated_edit.aep
    conform_h264_preset: str = "veryfast"  # libx264 preset for raw_ae.mp4 (RAW > 10 min or conform_codec=h264)
    conform_h264_crf: int = 12
    competitor_h264_preset: str = "medium" # competitor_ref.mp4 when the competitor must be transcoded
    competitor_h264_crf: int = 12
    # ---- exports (Stage 8) ----
    preview_crf: int = 14                  # <= 16 (prompt)
    preview_preset: str = "fast"
    preview_audio_budget_bytes: int = 1 << 30   # above this the RAW audio is decoded per played window
    compare_height: int = 960
    compare_crf: int = 18
    compare_preset: str = "veryfast"
    large_file_bytes: int = 2 * 1024 ** 3  # RAW above this is referenced by absolute path, not copied
    workers: int = 0                       # 0 = os.cpu_count()
    pool_stall_timeout_s: float = 300.0    # hang protection (DESIGN D7): a worker pool that delivers no result for this
                                           # long (or loses a worker process) is stopped and its remaining tasks run in
                                           # this process -- identical results, only slower
    pool_max_failures: int = 2             # after this many stopped pools, later steps run without worker pools
    progress_log_s: float = 30.0           # long steps log progress (and a stage heartbeat) at least this often
    verbose: bool = False
    seed: int = 12345
    skip_preview: bool = False             # tests may skip long renders
    skip_compare: bool = False

    # ---- proxies (Stage 3) --------------------------------------------------------------
    raw_proxy_width: int = 640             # upper bound; lowered for long RAWs to fit the byte budget
    comp_proxy_scale: float = 0.5          # competitor proxy = this fraction of full res
    comp_proxy_max_width: int = 640
    proxy_budget_bytes: int = 3 * 1024 ** 3
    min_proxy_width: int = 256
    long_raw_s: float = 2700.0             # beyond this AND over budget: sparse RAW proxy (index frames + windows)
    long_raw_window_s: float = 20.0
    audio_sr: int = 16000

    # ---- layout (Stage 4) ---------------------------------------------------------------
    static_std_thresh: float = 2.0         # temporal std (8-bit) below which a pixel is static
    dynamic_frac_thresh: float = 0.5       # row/col fraction of dynamic px to belong to the box
    overlay_dilate_px: int = 3             # at comp proxy res
    overlay_resid_thresh: float = 40.0     # |comp - warped raw| (8-bit) for residual-overlay detection
    overlay_min_frames: int = 3

    # ---- audio (Stage 5.1) --------------------------------------------------------------
    audio_window: float = 1.0
    audio_hop: float = 0.25
    audio_feat_rate: int = 100             # Hz
    audio_n_mels: int = 40
    audio_min_conf: float = 1.3            # peak / second-peak ratio for a confident window
    audio_speed_min: float = 0.90
    audio_speed_max: float = 1.30
    audio_speed_step: float = 0.01
    audio_refine_ms: float = 50.0
    audio_fmin: float = 80.0               # log-mel lower edge (Hz)
    audio_fmax: float = 7600.0             # log-mel upper edge (Hz); also capped at 0.49*sr/audio_speed_max
    audio_replaced_corr: float = 0.30      # per-segment xcorr peak below which the audio does not follow RAW
    audio_jl_max_s: float = 1.0            # max J/L audio offset searched at a hard cut (s)
    audio_added_thresh_db: float = -20.0   # residual level (re the rebuilt original) that counts as added audio
    audio_residual_search_s: float = 0.1   # per-segment lag search around the run's A/V offset (+-, also <= half the range)

    # ---- global A/V offset (S6, DESIGN §7 D9) --------------------------------------------
    av_offset_max_s: float = 1.0           # largest |competitor A/V offset| accepted (prior and estimate)
    av_offset_prior_wave_peak: float = 0.8 # S5.1 windows used for the prior: waveform NCC >= this ...
    av_offset_prior_min_windows: int = 8   # ... at least this many of them ...
    av_offset_prior_max_mad_ms: float = 10.0   # ... agreeing within this MAD (ms) around their median
    av_offset_seg_min_s: float = 0.5       # segments used for the estimate: >= this much audio at corr >= strong,
    av_offset_seg_short_s: float = 0.25    #   or >= this much at corr >= av_offset_seg_short_corr
    av_offset_seg_short_corr: float = 0.9
    av_offset_eps_ms: float = 0.5          # every segment's offset interval is widened by this (ms) on both sides
    av_offset_min_segments: int = 3        # acceptance: >= 3 segments ...
    av_offset_min_audio_s: float = 2.0     # ... >= 2 s of audio ...
    av_offset_min_coverage: float = 0.7    # ... the offset explains >= 70 % of the segment weight ...
    av_offset_max_spread_ms: float = 2.0   # ... no single segment moves the max-coverage set by more than 2 ms ...
    av_offset_min_ms: float = 2.0          # ... and |offset| >= 2 ms (smaller offsets are indistinguishable from phase)
    av_offset_zero_frac: float = 0.9       # offset = 0 exactly when 0 explains >= this fraction of the best coverage

    # ---- J/L audio cuts against the run's switch baseline (DESIGN §7 D9) ----------------------
    audio_jl_strong_frames: int = 10       # baseline cuts: both segments >= this many frames ...
    audio_jl_strong_corr: float = 0.8      # ... both models correlate >= this ...
    audio_jl_strong_margin: float = 0.5    # ... and the switch is this decisive (local NCC margin on both sides)
    audio_jl_baseline_min_cuts: int = 3    # fewer baseline cuts -> the baseline is not measured
    audio_jl_min_frames: float = 0.5       # J/L threshold: |switch - baseline| >= max(this many frames, 3 sigma)
    audio_jl_min_decisive: float = 0.3     # J/L evidence on both sides: each model explains its side of the switch by this local-NCC margin
    audio_peak_unique_margin: float = 0.1  # a short window's lag (or an audio line) counts only when its xcorr peak beats the best sidelobe by this
    audio_jl_large_frames: int = 4         # a J/L this large next to a retimed segment / on a continuous line is evidence, not exported
    audio_sync: str = "raw"                # raw | competitor: export audio keeps RAW lip-sync, or reproduces the competitor's offset

    # ---- visual search (Stage 5.2) ------------------------------------------------------
    sift_nfeatures: int = 500
    raw_index_fps_short: float = 10.0      # RAW index sampling rate for RAW <= 10 min
    raw_index_fps_long: float = 3.0        # for longer RAWs
    index_max_descriptors: int = 2_000_000 # cap (uint8 storage); nfeatures per sample lowered to fit
    comp_search_stride: int = 3            # sparse competitor frames searched globally
    index_knn: int = 24                    # k-NN per query descriptor in the multi-frame index
    index_ratio: float = 0.8               # cluster-aware ratio: vs first NN > index_far_s away
    index_far_s: float = 2.0
    vote_top_candidates: int = 8
    vote_min_frac: float = 0.2             # verify only candidates with >= this fraction of the best vote
    anchors_per_frame: int = 3             # max anchors kept per searched frame
    lowe_ratio: float = 0.75               # pairwise verification against ONE RAW frame only
    ransac_reproj_px: float = 3.0          # comp-proxy px (RANSAC is RAW -> comp)
    min_inliers: int = 12
    min_inlier_ratio: float = 0.30
    near_miss_inliers: int = 6             # RANSAC near-misses (>= this, < min_inliers; ZNCC-verified) may only JOIN an
                                           # existing track whose RAW time line they continue (refine, FX-03 step 3)
    anchor_zncc_slack: float = 0.05        # anchor accepted only if masked ZNCC >= match_thresh - slack
    audio_restrict_s: float = 2.0          # search +- this around a confident audio hint

    # ---- refinement (Stage 5.3) ---------------------------------------------------------
    refine_radius: int = 3                 # evaluate m-3 .. m+3
    track_search_radius: int = 8           # when propagating a track to a new frame
    score_blur: float = 1.0
    grad_weight: float = 0.0               # >0 adds gradient-magnitude ZNCC (graded material)
    match_thresh: float = 0.90             # masked ZNCC needed to accept a match (tuned on synthetic)
    none_thresh: float = 0.60              # EVERY evaluated hypothesis below this -> NONE (NOT-IN-RAW); the best in
                                           #   [none_thresh, match_thresh) -> UNRESOLVED ('uncertain' segment) unless the
                                           #   detail score promotes it (FX-08); verify c2 uses the same threshold
    detail_margin: float = 0.02            # detail-score promotion: blur-matched gradient ZNCC >= match_thresh AND above
                                           #   RAW m+-1, m+-2 under their own framing by more than this (FX-08)
    line_gap_s: float = 1.0                # FX-08: the neighbours' time lines are scored across unexplained gaps up to
                                           #   this long (s): NOT-IN-RAW only after the obvious hypotheses were tried
    line_search_reach: int = 30            # FX-08: gap frames within this many frames of a neighbouring run are
                                           #   searched pairwise against that run's line +- track_search_radius ...
    line_search_min_ratio: float = 0.6     # ... accepting RANSAC candidates with >= near_miss_inliers at this inlier
                                           #   ratio (a handful of RAW frames, not the whole RAW; ZNCC still decides)
    line_search_nfeatures: int = 1000      # ... with SIFT on the frames themselves (a RAW-only overlay such as a legal
                                           #   disclaimer takes part of a small budget)
    line_search_verify: int = 3            # ... verifying at most this many candidates per frame (best inliers first)
    identical_thresh: float = 0.9995       # RAW-vs-RAW ZNCC above which neighbours are 'identical' ...
    identical_mad: float = 0.75            # ... or mean |diff| (8-bit, proxy) below this x the contrast factor -- on
                                           #   EVERY tile (scoring.identical_images, FX-08; layout mask, no pass-2 masks)
    identical_tiles: int = 4               # tiles per side of the identity test (a max over tiles: no dilution)
    identical_contrast_ref: float = 128.0  # contrast factor = clip((p98 - p2) / this, identical_contrast_min, 1):
    identical_contrast_min: float = 0.25   #   a dark low-contrast frame needs a proportionally smaller change
    low_margin_eps: float = 0.001          # score gap flagged low_margin (never an ambiguity exemption)
    soft_delta_min: float = 0.001          # soft LP range delta_k = clip(3 * robust std of the track's best scores,
    soft_delta_max: float = 0.01           #   soft_delta_min, soft_delta_max)  (scoring.noise_delta)
    rel_drop_min: float = 0.01             # re-search if score < rolling track median - max(this, 4*MAD)
    uniform_std: float = 4.0               # region luma std below which a frame is UNIFORM
    low_conf_thresh: float = 0.5           # frames below get debug/low_confidence/k#####.png (max 200)
    ecc_iterations: int = 60
    ecc_eps: float = 1e-5
    ecc_pyramid_levels: int = 3            # coarse-to-fine framing measurement (refine.ecc_measure): proxy, 1/2, 1/4 ...
    ecc_pyramid_min_side: int = 40         # ... while the coarsest template's short side stays >= this (px)
    anchor_time_delta: float = 0.003       # anchor re-estimation: runner-up RAW frame within this ZNCC -> time_ambiguous
    # time-line-first refine (DESIGN §5 refine.py, FX-03): anchors are grouped by RAW time, framing is a path
    line_time_tol: float = 2.0             # an anchor joins a run within +-this many RAW frames of its snap-speed line
    line_min_inlier_frac: float = 0.7      # a track follows ONE snap-speed line when this fraction of its points is within line_time_tol
    path_median: int = 5                   # outlier window (frames) of the per-frame measurements before the path's RDP keys
    temporal_refine_max_slope: float = 0.95  # refine measures the comp-only repeat cadence where a line can repeat RAW
                                           #   frames (RAW frames per comp frame <= this: 23.976 / 25 -> 30 at v = 1)

    # ---- segmentation (Stage 5.4 / 6) ---------------------------------------------------
    speed_snap_values: tuple = (1.0, 1.05, 1.10, 1.15, 1.20, 1.25, 1.50, 2.00,
                                1 / 1.05, 1 / 1.10, 1 / 1.15, 1 / 1.20, 1 / 1.25, 1 / 1.50, 0.5)
    speed_snap_tol: float = 0.003
    retime_snap_values: tuple = (0.25, 0.2, 1 / 3)   # FX-08: extra snap values of a FRAME-BLEND path only (NLE slow-motion
                                           #   presets 25 / 20 / 33 %); its blend positions measure the speed directly
                                           #   and the snap still needs speed_snap_tol (not DP candidates)
    freeze_static_mad: float = 0.25        # FX-08 freeze admission: every aligned competitor pair inside a v = 0 segment
    freeze_static_ratio: float = 10.0      #   within max(this ratio x its repeat-pair noise floor, freeze_static_mad)
                                           #   mean |diff| (8-bit, temporal proxy, captions / overlays masked)
    min_segment_frames: int = 1
    lambda_cut: float = 1.0                # DP cost per cut
    lambda_unsnapped: float = 3.0          # DP cost of a segment whose speed cannot be snapped (> lambda_cut)
    lambda_nondominant: float = 1.5        # DP cost of a snapped speed other than the dominant one / 1.0 (> lambda_cut)
    lambda_one: float = 0.25               # DP cost of speed 1.0 when it is not the edit's dominant speed
    lambda_drop: float = 0.4               # DP cost per tolerated isolated low-margin frame
    lambda_tie: float = 0.05               # DP cost when a segment is feasible only at a timing tie
    dp_max_consecutive_fail: int = 6       # DP inner-loop break after this many infeasible ranges in a row
    transition_stop_after: int = 3         # crossfade scan stops after this many consecutive non-blend frames per side
    segment_score_max_pixels: int = 60000  # pixel cap (regular subsample) for blend fits / cross-transform ZNCC
    scenedetect: bool = True               # run the PySceneDetect cross-check (decodes the competitor once, cached)
    scenedetect_min_len: int = 2           # min scene length (frames); the default 15 hides close cuts
    link_scale_tol: float = 0.005          # anchors join a track only within 0.5 % scale ...
    link_pos_tol: float = 2.0              # ... and 2 px (comp full res) after the track trend
    punch_scale_step: float = 0.01         # transform step between frames that is a cut (punch-in)
    punch_pos_step: float = 4.0
    step_confirm_frames: int = 2           # a framing step is a cut only when the pixels confirm it on up to this many
                                           #   frames per side (old framing wins before, new after, by > 3 delta_k)
    lambda_repeat_cut: float = 1.0         # DP cost of a time cut between the two frames of a competitor REPEAT pair
                                           #   (FX-07: the same image on both frames; soft evidence, never a hard rule)
    union_track_window: int = 6            # union-test trigger: >= 3 refine tracks within +-this many frames of a cut
    transition_search: int = 20            # frames either side of a cut examined for blends
    blend_rel: float = 0.5                 # blend frame: (1 - zncc_fit) <= blend_rel * (1 - best single zncc)
    framing_scale_spread: float = 0.003    # < -> constant framing
    framing_pos_spread: float = 1.5        # px at comp full res
    framing_sample_step: int = 3
    rdp_pos_tol: float = 0.5               # px (comp full res)
    rdp_scale_tol: float = 0.001           # relative
    rotation_min_deg: float = 0.2
    scenedetect_adaptive: float = 2.0
    scenedetect_content: float = 15.0

    # ---- verification (Stage 9) ---------------------------------------------------------
    verify_zncc: float = 0.90
    audio_lag_tol_ms: float = 10.0
    frame_exact_min: float = 0.99
    verify_alpha_tol: float = 0.15         # crossfade: max |fitted alpha_B - declared alpha_B| per overlap frame
    verify_audio_min_corr: float = 0.30    # xcorr peak below which a segment's audio lag is not trusted
    verify_audio_strong_corr: float = 0.80 # peak above which a lag outside audio_lag_tol_ms always fails c5
    verify_full_rate_max_s: float = 900.0  # RAWs up to this long: final audio check at the original rate
    verify_audio_min_s: float = 0.5        # c5: shorter audio ranges are checked as aggregated runs of consecutive pieces
    verify_audio_run_search_ms: float = 20.0   # c5: residual lag search of an aggregated run of short pieces (+-)
    # ---- hypothesis-neutral verification (DESIGN §5 verify / temporal.py) ----------------------------
    # verify never re-uses a decision of the analysis: comp-only temporal labels, free (ECC) re-measurement
    # of framing and +-1 RAW frame refits, masks from the layout only (never refine's residual masks)
    temporal_max_side: int = 200           # temporal signature measured on the box ROI downscaled to this long side (px)
    temporal_shot_cc: float = 0.8          # aligned consecutive-frame ZNCC below this = competitor shot change (pair not compared)
    temporal_gap_ratio: float = 2.5        # repeat vs move: the shot's residual (1 - cc) clusters must be this factor apart
    temporal_growth_ratio: float = 1.5     # residual over 2 frames / over 1 frame above this = the content moves (a repeat stays ~1)
    temporal_mag_ratio: float = 3.0        # competitor vs recreation pair residuals (bias-corrected per shot) more than this factor apart disagree
    temporal_ecc_iterations: int = 40      # pair alignment (affine ECC from a phase-correlation start): converges in a few iterations
    temporal_ecc_eps: float = 1e-5
    verify_refit_margin: float = 0.01      # +-1 refit: a neighbour RAW frame (own ECC framing) must beat the shown frame by > max(3*delta, this)
    verify_union_frames: int = 2           # c2 no-cut alternative: frames per side scored under the other side's (extended) time line
    verify_excursion_frames: int = 3       # c2: a 1-2 frame segment more than this many RAW frames off its neighbours' common line
    verify_framing_min_samples: int = 5    # c4: independently measured framing samples per segment (at least) ...
    verify_framing_all_max: int = 6        # ... and every frame of segments up to this length
    verify_low_score_margin: float = 0.02  # c4: unconverged sample whose model gradient score is this far below its neighbours' median -> failure
    verify_mix_tie: float = 0.1            # c3 Frame Mix (FX-08): refine's single-frame argmax on the LIGHTER source of a
                                           #   frame-blend mix counts as a blend tie only within this of an even (0.5) mix

    def resolved_workers(self) -> int:
        import os
        return self.workers or max(1, (os.cpu_count() or 2))

    def to_dict(self) -> dict:
        return dataclasses.asdict(self)

    def analysis_params(self) -> dict:
        """Parameters that influence ANALYSIS results (cache keys). Excludes paths, verbosity and the
        export-only settings (layout_mode, comp_size, fps_mode, ae_*), and conform settings (those go
        only into the conform key), so changing --layout never recomputes the analysis."""
        d = self.to_dict()
        for k in ("competitor", "raw", "out_dir", "work_dir", "verbose", "workers", "skip_preview", "skip_compare",
                  "layout_mode", "comp_size", "fps_mode", "force_conform", "conform_codec", "large_file_bytes",
                  "ae_time_mode", "ae_slack_tol_frames", "run_ae", "ae_timeout_s", "verify_zncc", "audio_lag_tol_ms", "frame_exact_min",
                  "conform_h264_preset", "conform_h264_crf", "competitor_h264_preset", "competitor_h264_crf",
                  "preview_crf", "preview_preset", "preview_audio_budget_bytes", "compare_height", "compare_crf",
                  "compare_preset", "verify_alpha_tol", "verify_audio_min_corr", "verify_audio_strong_corr",
                  "verify_full_rate_max_s", "verify_audio_min_s", "verify_audio_run_search_ms", "audio_sync",
                  "pool_stall_timeout_s", "pool_max_failures", "progress_log_s", *VERIFY_ONLY_PARAMS):
            d.pop(k, None)
        return d

    @property
    def out(self) -> Path:
        return Path(self.out_dir)

    @property
    def work(self) -> Path:
        return Path(self.work_dir)

    @property
    def debug_dir(self) -> Path:
        return Path(self.out_dir) / "debug"

    @property
    def media_dir(self) -> Path:
        return Path(self.out_dir) / "media"
