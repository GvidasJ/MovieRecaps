# match_cuts

Rebuild a competitor's short-form edit **frame-exactly from its RAW source** and deliver it as an
After Effects project (`build_ae_project.jsx` → `recreated_edit.aep`), plus a cut list, FCP7 XML, a
CMX3600 EDL, a frame-exact preview render, a side-by-side comparison and a verification report.

Nothing is guessed: every cut and source timestamp comes from audio + visual measurement, carries a
confidence, and is checked by Stage 9 verification. Whatever cannot be determined is flagged (for
example a `NOT-IN-RAW` placeholder, an *uncertain* segment or an *ambiguous-identical* frame).

The requirements live in [`MATCH_CUTS_PROMPT.md`](../../MATCH_CUTS_PROMPT.md); the module contract
is [`DESIGN.md`](DESIGN.md).

---------------------------------------------------------------------------------------------------

## Setup

Requirements: Python ≥ 3.10, **ffmpeg/ffprobe ≥ 5.1** on `PATH` (or set `FFMPEG` / `FFPROBE`).
Optional: **Node.js** (runs the generated `.jsx` in a strict ExtendScript/After Effects mock for
criterion 6), **After Effects CC 2019+** (runs the `.jsx` and renders with `aerender`).

```bash
python3 -m venv .venv
. .venv/bin/activate                     # Windows: .venv\Scripts\activate
pip install -r tools/match_cuts/requirements.txt
# scenedetect WITHOUT its opencv extra, so there is only one cv2 package:
pip install --no-deps scenedetect click platformdirs
pip install -e tools/match_cuts          # provides `python -m match_cuts` and `match-cuts`
```

Install exactly one OpenCV wheel (`opencv-contrib-python-headless` or `opencv-contrib-python`, never
together with `opencv-python`). PySceneDetect must use `backend='opencv'` (its PyAV backend crashes with
PyAV ≥ 19). The FCP-XML / CMX3600 readers are the separate OTIO plugins `otio-fcp-adapter` and
`otio-cmx3600-adapter` (in `requirements.txt`).

Installing ffmpeg: Linux `apt install ffmpeg`, macOS `brew install ffmpeg`, Windows
`winget install Gyan.FFmpeg`.

## Usage

```bash
python -m match_cuts --competitor X --raw Y --out Z [--layout match|fill|source] \
                     [--comp-size WxH|competitor] [--fps competitor|source] [--work DIR] \
                     [--workers N] [--force-conform] [--ae-time-mode auto|stretch|remap|frames] [-v]
```

| flag | default | meaning |
|---|---|---|
| `--competitor X` | `./input/competitor.mp4` | the finished edit to rebuild |
| `--raw Y` | `./input/raw.mp4` | the source video it was cut from |
| `--out Z` | `./output` | deliverables folder |
| `--layout` | `match` | `match`: recreate the competitor's canvas, video box (position, size, rounded corners), background and per-shot crop/zoom. `fill`: full-screen 9:16 keeping the per-shot framing. `source`: cuts only, RAW size and RAW fps, no reframing |
| `--comp-size` | `competitor` | AE comp size; `competitor` = same pixels as the competitor, or e.g. `1080x1920`. In `match` mode it must keep the competitor's aspect (rejected otherwise) |
| `--fps` | `competitor` | `competitor`: the competitor's exact frame rate (exact cut timing). `source`: RAW frame rate; cuts are rounded to the nearest MAIN frame and the max error is reported (`cutlist.settings.fps_source_max_error_s`). On a different MAIN grid criterion 3 accepts, per MAIN frame, any RAW frame between the competitor frames bracketing that time; cut timing is exact only with `competitor` |
| `--work DIR` | `./work` | caches and intermediate files |
| `--workers N` | `0` | worker processes (0 = all CPUs) |
| `--force-conform` | off | transcode RAW to an AE-safe copy even if it is already AE-safe |
| `--ae-time-mode` | `auto` | how AE layers are timed: `stretch` (Time Stretch + Start Time), `remap` (time remapping), `frames` (one HOLD time-remap key per frame; immune to AE's time rounding), `auto` (stretch, with a per-layer switch to `frames` when the JSX's read-back self-check finds a mismatch) |
| `-v` | off | debug logging on the console |

Extra flags: `--input-dir DIR` (auto-detection folder, default `./input`), `--seed N`,
`--skip-preview`, `--skip-compare`, `--no-swap`, `--version`.

**Input auto-detection** (prompt Configuration): the competitor is the *portrait* file, failing that the
*shorter* one. If `--competitor`/`--raw` look reversed they are swapped with a warning (`--no-swap`
disables this). If the default files do not exist, `./input` is scanned for exactly two videos. Two files
with the same orientation and duration are genuinely ambiguous: the tool stops and asks you to pass both
flags.

**Exit code**: `0` when no acceptance criterion (and no Stage 9 check) is `fail`, `1` otherwise, `2` on
errors (missing/ambiguous inputs, a crashed stage — see `work/match_cuts.log`).

The final summary prints one line per acceptance criterion, the output paths and the warnings:

```
match_cuts result: PASS
  c1 coverage                    PASS   21 segments (1 NOT-IN-RAW), 1800/1800 frames covered, ...
  c2 frame-exact cuts            PASS   20 cuts: 20 verified both sides, 0 exceptions, 0 failed
  c3 frame-exact source frames   PASS*  AE sim: plan: 1732/1740 exact, 8 timing-tie ...
  c4 speed / framing             PASS   20 raw segments: 0 problems, 0 exceptions
  c5 audio                       PASS*  19 segments measured, max |lag| 0.21 ms, 2 explained exceptions
  c6 After Effects               PASS   mock run: 13/13 checks ok (mock only: After Effects not installed)
  9.7 determinism                PASS   cutlist re-assembled from caches is byte-identical
  (PASS* = passed with listed, explained exceptions)
```

## Outputs

```
output/
  build_ae_project.jsx     run in After Effects -> builds and saves recreated_edit.aep next to itself
  recreated_edit.aep       only when After Effects is installed on this machine (Stage 7.6)
  media/                   RAW (or its AE-safe conformed copy raw_ae.mov / raw_ae.mp4) + competitor_ref.mp4
  cutlist.json             the single source of truth (prompt Stage 6 schema + extras)
  cutlist.csv              one row per segment (timecodes as in the report: drop-frame for 29.97/59.94)
  recreated_edit.xml       FCP7 XML (Premiere Pro / DaVinci Resolve)
  recreated_edit.edl       CMX3600 EDL (cuts + M2 speed lines)
  preview_recreation.mp4   frame-exact render of the recreation from RAW (MAIN size / fps / layout)
  compare.mp4              competitor | recreation | amplified difference; frame number, timecode and segment
                           in a label strip above each panel (never over the picture)
  report.md                inputs, layout, segment table, edit-style breakdown, warnings, criteria, timings
  verify.json              every Stage 9 check and acceptance criterion with its evidence
  debug/                   mapping.png, scores.png, layout.png, layout_refine.png, cuts/cut_XX.png,
                           low_confidence/k#####.png, verify_failures/k#####.png,
                           decisions.jsonl (this run's evidence, cached stages replayed with cached=true)
work/
  cache/<stage>/<key>.*    content-addressed caches (key = input file hashes + analysis parameters)
  decisions.jsonl          every decision with its evidence (truncated at the start of each run; cached
                           stages replay their stored records; copied to <out>/debug/)
  match_cuts.log           full debug log (appended)
  frame_map.npz            m(k): the RAW frame, scores, ranges and transform of every competitor frame
  layout.json, ae_plan.json, ae_mock_runs.json, verify_zncc.npy, verify_rerun/
```

`cutlist.json` notes: frame indices are integers, intervals half-open `[comp_in, comp_out)`, frame
rates exact rationals (`"30000/1001"`), seconds have 9 decimals. `transform` maps RAW pixels (after the
horizontal flip when `flip_h`) to competitor pixels, CORNER convention. `raw_in_seconds` is the
phase-solved RAW time at `comp_in`: any value inside the feasible interval `raw_in_interval` reproduces
every measured frame under AE's floor rule (`raw_in_interval_both`: also under round-to-nearest). Inside
it the phase is chosen from the AUDIO when the segment's audio correlates confidently (`audio.phase_source
= "audio"`, `audio.lag_ms_video` = the lag the interval centre would have had), otherwise the centre;
this removes the systematic quarter-frame audio offset of the centre (8.3 ms at 30p, 10.4 ms at 24p).
`ae_margin_ms` is the distance to the interval edge. The only wall-clock values are in `provenance.timings`, which the determinism check
ignores; everything else is identical on a re-run.

## Pipeline

| stage | module | what |
|---|---|---|
| S0 | `pipeline.check_env` | OS, ffmpeg/ffprobe versions, Python packages, Node, After Effects / aerender search |
| S2 | `probe`, `conform` | ffprobe + a full decode pass per file; AE-unsafe files (VP9/AV1/HEVC, WebM, Opus, VFR, start offsets, edit lists, rotation, SAR ≠ 1) are conformed to `media/` and verified; analysis then uses **only** the files AE imports |
| S3 | `proxies` | memory-mapped grayscale proxies, 16 kHz mono analysis audio (original-rate audio for the final audio check) |
| S4 | `layout` | static mask, video box + corner radius, background, zones, caption/overlay masks, layout periods (fullscreen / split / PiP); after S5.3 the box is re-fitted against the warped RAW (`refine_box_from_raw`) and S5.2–5.3 re-run once if it changed |
| S5.1 | `audio_align` | FFT cross-correlation of 1 s windows (log-mel + onset), speed-scaled windows, sample-precise refine |
| S5.2 | `visual_match` | SIFT index of RAW, voting, RANSAC (also against the flipped RAW), ZNCC-verified anchors |
| S5.3 | `refine` | frame-exact m(k) with masked ZNCC, track transforms, ambiguous-identical ranges, rescue search |
| S5.4–5.5 | `segment` | DP cut placement, speed snap, transitions, framing keys, PySceneDetect cross-check |
| S5.6 | `audio_align` | per-segment lag, J/L offsets, pitch preservation, added music/SFX/VO |
| S6 | `phase_solve` + `pipeline` | exact phase LP per segment -> `raw_in_seconds`; `cutlist.json` |
| S7 | `export_ae` | AE plan (every number the JSX sets), ES3 `build_ae_project.jsx`, strict mock runs |
| S8 | `export_xml_edl`, `render_preview` | CSV, FCP7 XML, EDL (+ re-parse validation), preview and compare renders |
| S9 | `verify` | criteria c1..c6 and checks 9.1..9.7 |
| S10 | `report` | `report.md` and the summary |

Re-runs are fast: the expensive stages are cached in `work/cache`. Changing only export settings
(`--layout`, `--comp-size`, `--fps`, `--ae-time-mode`) never recomputes the analysis. To force a stage to
recompute, delete `work/cache/<stage>/` (stage versions in `common.STAGE_VERSION` invalidate caches
automatically when an algorithm changes).

## Verification (Stage 9) and the acceptance criteria

| criterion | checked by |
|---|---|
| **c1 coverage** | 9.1: segments + labelled NOT-IN-RAW placeholders tile `[0, N)` exactly; overlaps only where a measured transition of exactly that length explains them; raw segments must carry a RAW mapping |
| **c2 frame-exact cuts** | an independent per-cut check: the last frame of A scores higher against A's model (AE sampling rule + A's transform) than against B's model extended back, and the first frame of B the reverse; crossfades: the fitted alpha ramp; NOT-IN-RAW neighbours: the placeholder frame must *not* match the extended neighbour; 9.4 writes `debug/cuts/cut_XX.png` (k-1..k+2, competitor over recreation) |
| **c3 frame-exact source frames** | 9.2: which RAW frame AE shows on every comp frame, simulated from the exact AE plan **and** from the values the JSX actually set in the mock run, must equal refine's MEASURED m(k) (before segmentation) on ≥ 99 % of matched frames; listed exception classes: ambiguous-identical, timing-tie, and frames segmentation re-assigned to its model; a plan that disagrees with the cut list always fails. Crossfade frames (both layers + opacity), dips and NOT-IN-RAW placeholders are checked too. 9.3: masked ZNCC of competitor vs a match-geometry recreation on every frame (each frame in its own box ROI; fullscreen periods on the whole canvas minus active zones) ≥ `verify_zncc`, failures in `debug/verify_failures/`; the delivered `preview_recreation.mp4` is always probed (frame count, fps) |
| **c4 speed / framing** | speed inside the feasible range of the segment's frame constraints (± 0.5 %) and snapped whenever a snap value reproduces refine's measured frames; framing MEASURED independently (ECC from a perturbed start on sampled frames) vs the segment model within ± 1 % scale / ± 4 px; flip must beat the mirrored hypothesis; rotation consistent |
| **c5 audio** | 9.5: per-segment lag of the rebuilt RAW audio vs the competitor's within ± 10 ms, else an explanation from the closed list `too_short, not_in_raw, audio_replaced, pitch_preserved, music_dominated, no_audio` (a confident correlation at a wrong lag always fails; `music_dominated` is only accepted when the per-segment audio analysis found it, and a wide ±2 s search catches grossly misaligned audio) |
| **c6 After Effects** | the `.jsx` in the strict mock (no error alert; MAIN frame rate, duration, work area; saved `recreated_edit.aep` next to the script; one layer per segment with the planned name/startTime/stretch/in/out; the *media missing* scenario aborts cleanly after the relink dialog) + 9.6 `aerender` frame-by-frame comparison with the preview when AE is installed. On Linux `pass` means *mock-verified* |
| 9.7 determinism | segmentation → phase solve → audio → cut list re-run from the cached FrameMap/AudioHints in a fresh context; canonical JSON (without `provenance.timings`) must be byte-identical. When the previous run's `cutlist.json` came from the same inputs, parameters and tool/stage versions it is compared too (a difference fails) |
| 9.8 deliverables | every file of the deliverables tree exists (unless explicitly skipped, e.g. `--skip-preview`, or the `.aep` without AE), XML/EDL re-parse validation passed, no stage error |

Statuses: `pass`, `pass_with_exceptions` (every exception listed and explained), `fail`,
`not_available` (e.g. no Node for the mock, no AE for aerender).

Exit codes: `0` everything passed; `1` a criterion or check failed; `2` the run itself failed (bad
inputs, a crashed stage); `3` nothing failed but a criterion could not be verified (headline
`PASS (criterion 6 not verified: …)`, e.g. Node.js missing so the JSX was never executed).

## Running the result in After Effects

1. Keep `build_ae_project.jsx` and `media/` together (copy the whole output folder).
2. **File → Scripts → Run Script File…** → `build_ae_project.jsx`.
3. It builds the project (`01 Comps`, `02 Source`, `03 Reference`), the `Recreated Edit` comp at the
   competitor's exact size, frame rate and duration, and saves `recreated_edit.aep` next to the script.
4. Guide layers (never rendered) outline the header, title, caption and watermark zones; coloured
   `MISSING – not in RAW` solids mark the ranges you have to fill; comp markers sit on every cut.
5. The `REFERENCE – competitor` layer on top is a switched-off guide layer in *Difference* mode:
   switch it on and black means the recreation matches.

Headless: macOS `osascript -e 'tell application "Adobe After Effects 2024" to DoScriptFile "/abs/path/build_ae_project.jsx"'`,
Windows `"C:\Program Files\Adobe\Adobe After Effects 2024\Support Files\AfterFX.exe" -r C:\abs\path\build_ae_project.jsx`.
When After Effects is installed on the machine running match_cuts, this happens automatically and
`aerender` renders the comp for check 9.6.

## Troubleshooting

**"Could not save recreated_edit.aep"** — enable *Preferences → Scripting & Expressions → Allow
Scripts to Write Files and Access Network* (in versions before 16.1: *Preferences → General*), then run
the script again.

**Media not found / relink** — the script looks for the media next to itself (`media/…`), then at the
absolute path recorded at export time, then opens *Locate the RAW video*. RAW files above
`large_file_bytes` (2 GB) are not copied into `media/`; they are referenced by absolute path, so keep
them where they are or relink when asked.

**VFR, start offsets, WebM/VP9/AV1/HEVC/Opus** — these are conformed automatically to
`media/raw_ae.mov` (ProRes 422 LT, same resolution and frame rate, CFR, start 0, PCM audio; H.264
CRF 12 in `raw_ae.mp4` for RAWs longer than 10 minutes) and verified (frame count + ≥ 50 PTS-sampled
frames matched by SSIM, plus — for VFR — a content check that every source frame the timing requires is
really shown, independent of the ffmpeg rule). A VFR competitor is timed on its nominal rate using the
frame displayed at each output time; millisecond-rounded timestamps (MKV/WebM, OBS recordings) are treated
as quantised so no frame is lost. ffmpeg older than 5.1 works (`-vsync 0` instead of `-fps_mode`). A
truncated or partially downloaded input is detected (decoded length vs the header) and warned about —
otherwise its missing tail would look like NOT-IN-RAW footage. The report's *Inputs* section lists every
issue found and the conform decision.

**Fullscreen shots inside a boxed edit** — detected as layout periods; those segments carry their own
`box` (the whole canvas) and are placed directly in the main comp above the Video Box (no rounded mask),
in the AE project and in the preview. Split-screen / picture-in-picture regions are detected and reported
but not recreated (criterion 1 becomes `pass_with_exceptions`, listed under *Anything AE can't
reproduce*).

**Slow on Windows / macOS** — worker processes are `spawn`ed there (forking is only safe on Linux);
results are identical, start-up costs a few seconds per pool. `MATCH_CUTS_START_METHOD=spawn|fork`
overrides the choice.

**AE shows a different frame rate than expected** — AE sometimes misreads the rate of a file; the JSX
compares the imported `frameRate` with the exact rate from the cut list and sets
`mainSource.conformFrameRate` on any real difference (beyond AE's float32 rounding; a warning with the
drift in frames when it is more than cosmetic), and checks the frame count exactly. It never conforms to
the comp rate: a 29.97 fps source inside a 30 fps edit plays at speed 1.000. Runtime warnings are listed in
the final alert and stored in the comment of the `Recreated Edit` comp.

**Off-by-one frames in AE on some segments** — the report lists *AE-rule-sensitive* segments (phase
margin below `ae_min_margin_ms`, or no start time that satisfies both floor and round sampling). The JSX
already re-checks every stretch-mode layer from the values AE stored and switches mismatching layers to
frame-exact time remapping; to force it for every layer, re-export with `--ae-time-mode frames`.

**A criterion failed** — start with `report.md` (*Warnings*, *Verification details*), then
`verify.json`, `debug/mapping.png` (every segment should be a straight line, every cut a jump),
`debug/scores.png`, `debug/cuts/`, `debug/verify_failures/` and the evidence in `debug/decisions.jsonl`.

| failing | usual causes and fixes |
|---|---|
| c1 coverage | a gap: frames no model explains (check `debug/low_confidence/`; lower `none_thresh` only if the frames are really in RAW); an unexplained overlap: a transition not detected (see `transition_search`, `blend_rel`) |
| c2 cuts | wrong speed snap moving the boundary (compare `speed_measured` / `speed_range` in the cut list); a missed punch-in (`punch_scale_step`, `punch_pos_step`); caption masks too small so text dominates the score (`overlay_dilate_px`) |
| c3 source frames | frame-rate interpretation (probe section of the report: nominal fps, VFR), a start-time offset (conform, `v_start_time`), a missed flip, a transform convention problem (framing numbers in the segment table vs the competitor), masks too small; frames listed as *ambiguous-identical* or *timing-tie* are allowed exceptions |
| c4 speed / framing | a speed left unsnapped although a common value fits (`speed_snap_tol`); animated framing simplified too coarsely (`rdp_pos_tol`, `rdp_scale_tol`); rotation below `rotation_min_deg` dropped |
| c5 audio | J/L cuts (per-segment `audio.in_offset_frames` / `out_offset_frames`), added music or voice-over (codes `music_dominated`, `audio_replaced`), pitch-preserved speed changes (`pitch_preserved`; AE's stretch changes pitch — apply *Time-Stretch* to the audio in AE) |
| c6 After Effects | the mock names the failing JSX step; `not_available` means Node is missing (install Node ≥ 18 to enable the mock check) |
| 9.7 determinism | a non-seeded random step or an unordered iteration in segmentation / phase solve / audio analysis; `verify.json` lists the differing JSON paths |

Stop only when everything passes or the report states precisely what is impossible (for example
"frames 612–655 are stock footage that isn't in RAW").

## Thresholds

Every threshold lives in `match_cuts/config.py` (`Config`); none is hard-coded in a module. Values
below are the defaults (tuned on the synthetic test in `tests/test_synthetic.py`); when a default is
changed, update this table with the reason.

| group | parameter | default | meaning |
|---|---|---|---|
| export | `ae_min_margin_ms` | 1.0 | phase margin below which a segment is reported AE-rule-sensitive |
| export | `large_file_bytes` | 2 GiB | RAW above this is referenced by absolute path instead of copied |
| proxies | `raw_proxy_width` / `comp_proxy_scale` / `comp_proxy_max_width` | 640 / 0.5 / 640 | analysis proxy sizes |
| proxies | `proxy_budget_bytes` / `min_proxy_width` / `long_raw_s` | 3 GiB / 256 / 2700 s | long-RAW handling (sparse proxies around audio hints) |
| layout | `static_std_thresh` / `dynamic_frac_thresh` | 2.0 / 0.5 | static-pixel and video-box detection |
| layout | `overlay_dilate_px` / `overlay_resid_thresh` | 3 / 40 | overlay masks (comp proxy px / 8-bit residual) |
| audio | `audio_window` / `audio_hop` / `audio_min_conf` | 1.0 s / 0.25 s / 1.3 | coarse alignment windows and confidence (peak / second peak) |
| audio | `audio_speed_min` / `audio_speed_max` / `audio_speed_step` | 0.90 / 1.30 / 0.01 | speed-scaled windows |
| visual | `sift_nfeatures` / `raw_index_fps_short` / `raw_index_fps_long` | 500 / 10 / 3 | RAW index |
| visual | `lowe_ratio` / `ransac_reproj_px` / `min_inliers` / `min_inlier_ratio` | 0.75 / 3.0 / 12 / 0.30 | anchor verification (plus masked ZNCC ≥ `match_thresh` − `anchor_zncc_slack` 0.05) |
| refine | `match_thresh` / `none_thresh` | 0.90 / 0.60 | masked ZNCC to accept a match / below which a frame is NOT-IN-RAW |
| refine | `identical_thresh` / `identical_mad` | 0.9995 / 0.75 | ambiguous-identical RAW neighbours |
| refine | `refine_radius` / `score_blur` / `grad_weight` | 3 / 1.0 / 0.0 | candidate window, blur sigma, gradient ZNCC weight (graded material) |
| refine | `uniform_std` / `low_conf_thresh` | 4.0 / 0.5 | dip/flash detection / low-confidence thumbnails |
| segments | `speed_snap_values` / `speed_snap_tol` | 1.00 1.05 1.10 1.15 1.20 1.25 1.50 2.00 and inverses / 0.3 % | speed snapping |
| segments | `lambda_cut` / `lambda_unsnapped` | 1.0 / 3.0 | DP costs |
| segments | `punch_scale_step` / `punch_pos_step` | 0.01 / 4 px | punch-in cut detection |
| segments | `transition_search` / `blend_rel` | 20 / 0.5 | crossfade detection |
| segments | `framing_scale_spread` / `framing_pos_spread` | 0.3 % / 1.5 px | constant vs animated framing |
| segments | `rdp_pos_tol` / `rdp_scale_tol` / `rotation_min_deg` | 0.5 px / 0.1 % / 0.2° | keyframe simplification, rotation |
| verify | `verify_zncc` / `audio_lag_tol_ms` / `frame_exact_min` | 0.90 / 10 ms / 0.99 | Stage 9 thresholds |

Verification constants not (yet) in `Config` (read with a fallback): crossfade alpha tolerance 0.15
(`verify_alpha_tol`), minimum audio correlation 0.3 for a valid lag (`verify_audio_min_corr`),
correlation 0.8 above which a wrong lag always fails (`verify_audio_strong_corr`), and the longest RAW
(900 s, `verify_full_rate_max_s`) whose original-rate audio is loaded for the final audio check (longer
RAWs use the 16 kHz analysis audio).

Changed defaults: none yet.

## Tests

```bash
cd tools/match_cuts
../../.venv/bin/python -m pytest -q -m "not slow"      # unit tests, each file < 60 s
../../.venv/bin/python -m pytest -q tests/test_synthetic.py   # Stage 1 end-to-end (slow, minutes)
```

`tests/test_synthetic.py` builds a synthetic RAW and a competitor made with ffmpeg filtergraphs (jump
cuts, an out-of-order hook, a re-used moment, a 1.10× segment, a flipped segment, a push-in, a punch-in,
a 6-frame crossfade, a 1 s NOT-IN-RAW insert, captions, title, logo, music) and asserts that the whole
CLI recovers the known edit exactly.
