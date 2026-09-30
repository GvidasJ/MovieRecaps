"""Run configuration + every tunable threshold in one place (DESIGN.md §4).

Thresholds are tuned on the synthetic test (tests/test_synthetic.py); change them here, never inline.
"""
from __future__ import annotations

import dataclasses
from dataclasses import dataclass, field
from pathlib import Path


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
    ae_min_margin_ms: float = 1.0          # phase margin below which a segment is "AE-rule-sensitive"
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
    anchor_zncc_slack: float = 0.05        # anchor accepted only if masked ZNCC >= match_thresh - slack
    audio_restrict_s: float = 2.0          # search +- this around a confident audio hint

    # ---- refinement (Stage 5.3) ---------------------------------------------------------
    refine_radius: int = 3                 # evaluate m-3 .. m+3
    track_search_radius: int = 8           # when propagating a track to a new frame
    score_blur: float = 1.0
    grad_weight: float = 0.0               # >0 adds gradient-magnitude ZNCC (graded material)
    match_thresh: float = 0.90             # masked ZNCC needed to accept a match (tuned on synthetic)
    none_thresh: float = 0.60              # below this for every hypothesis -> NONE candidate
    identical_thresh: float = 0.9995       # RAW-vs-RAW ZNCC above which neighbours are 'identical'
    identical_mad: float = 0.75            # ... or mean |diff| (8-bit, proxy, visible region) below this
    low_margin_eps: float = 0.001          # score gap flagged low_margin (never an ambiguity exemption)
    soft_delta_min: float = 0.001          # soft LP range delta_k = clip(3 * robust std of the track's best scores,
    soft_delta_max: float = 0.01           #   soft_delta_min, soft_delta_max)  (scoring.noise_delta)
    rel_drop_min: float = 0.01             # re-search if score < rolling track median - max(this, 4*MAD)
    uniform_std: float = 4.0               # region luma std below which a frame is UNIFORM
    low_conf_thresh: float = 0.5           # frames below get debug/low_confidence/k#####.png (max 200)
    ecc_iterations: int = 60
    ecc_eps: float = 1e-5

    # ---- segmentation (Stage 5.4 / 6) ---------------------------------------------------
    speed_snap_values: tuple = (1.0, 1.05, 1.10, 1.15, 1.20, 1.25, 1.50, 2.00,
                                1 / 1.05, 1 / 1.10, 1 / 1.15, 1 / 1.20, 1 / 1.25, 1 / 1.50, 0.5)
    speed_snap_tol: float = 0.003
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
                  "ae_time_mode", "ae_min_margin_ms", "verify_zncc", "audio_lag_tol_ms", "frame_exact_min",
                  "conform_h264_preset", "conform_h264_crf", "competitor_h264_preset", "competitor_h264_crf",
                  "preview_crf", "preview_preset", "preview_audio_budget_bytes", "compare_height", "compare_crf",
                  "compare_preset", "verify_alpha_tol", "verify_audio_min_corr", "verify_audio_strong_corr",
                  "verify_full_rate_max_s"):
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
