# MATCH CUTS — Rebuild a competitor's edit from the raw source, delivered as an After Effects project

> **For the human:** put the two videos in `./input/`, save this file in the same folder, open Claude Code there and say: *"Read MATCH_CUTS_PROMPT.md and execute it."* Edit the Configuration block first if your file names differ.

Read this whole document before writing any code. Then make a short plan/checklist of the stages and keep it updated as you work.

## Role and mission

You are a senior video-pipeline engineer (FFmpeg, Python, OpenCV, audio DSP, Adobe After Effects ExtendScript).

I will give you two local video files:

- **COMPETITOR** — a finished short-form edit (usually 9:16, 30–90 s) that someone made from a longer source video.
- **RAW** — that source video (the original, unedited upload).

Rebuild the competitor's edit **from RAW**, frame for frame — the same source moments, the same cut points, the same order, the same speed, the same framing (crop, zoom, position, flip) and the same transitions — and deliver it as an **After Effects project** I can open and keep editing, plus files that prove it matches.

You are not allowed to guess. Every cut and every source timestamp must come from measurement (audio + visual matching), carry a confidence score, and pass verification. If something can't be determined, flag it as uncertain instead of inventing a value.

Build this as a **reusable command-line tool** (I'll run it on many clip pairs), prove it on synthetic data, then run it on my files.

## Configuration

```
COMPETITOR    = ./input/competitor.mp4
RAW           = ./input/raw.mp4
OUTPUT_DIR    = ./output
WORK_DIR      = ./work
LAYOUT_MODE   = match        # match  = recreate the competitor's layout: canvas, video-box position/size,
                             #          rounded corners, background, and the per-shot crop/zoom inside the box
                             # fill   = full-screen 9:16, keeping the competitor's per-shot framing
                             # source = cuts only, at RAW resolution, no reframing
AE_COMP_SIZE  = competitor   # competitor (same pixel size as COMPETITOR) or e.g. 1080x1920
AE_FPS        = competitor   # competitor (exact cut timing) or source (cuts rounded to nearest frame; report max error)
```

If the files in `./input/` have other names, the competitor is the portrait one (failing that, the shorter one). Ask me only if it's genuinely ambiguous.

**Recreate:** cuts; RAW in/out points; order (including out-of-order and re-used moments); speed changes; framing (scale, position, rotation, horizontal flip, animated zooms/pans); transitions (dissolves, dips, flash frames); and, in `match` mode, the layout geometry.

**Do not copy** the competitor's own creative assets: captions, title text, logos, watermarks, stickers, music, sound effects, voice-over. Detect them, log where and when they appear, mask them out of the matching, and leave labelled placeholders so I can add my own.

## Definition of done (acceptance criteria)

The job is finished only when all of these pass (Stage 9 checks them):

1. **Full coverage** — every competitor frame is either mapped to a specific RAW frame or covered by a labelled `NOT-IN-RAW` placeholder. No gaps, no unintended overlaps; the total frame count equals the competitor's.
2. **Frame-exact cuts** — at every cut between segments A and B, the last competitor frame of A matches A's source better than B's, and the first frame of B matches B's better than A's. Off by one frame = fail.
3. **Frame-exact source frames** — for ≥ 99 % of matched frames the recreation shows exactly the RAW frame the competitor showed. The only allowed exception is *ambiguous-identical* frames (neighbouring RAW frames that are visually identical, e.g. freeze frames or locked-off static shots); list them.
4. **Speed** within ±0.5 % per segment; **framing** within ±1 % scale and ±4 px position at comp resolution; **flip** and **rotation** correct.
5. **Audio** — per segment, the recreated audio lines up with the competitor's (cross-correlation lag within ±10 ms), or the difference is explained (e.g. the competitor replaced the audio).
6. **After Effects** — `build_ae_project.jsx` runs without errors, builds the comp with the competitor's exact duration and frame rate, and saves `recreated_edit.aep`. If After Effects is installed on this machine, render the comp with `aerender` and confirm it shows the same RAW frame as `preview_recreation.mp4` on every frame.

If a criterion fails: diagnose, fix, re-verify. Stop only when everything passes or you can state precisely what's impossible (e.g. "frames 612–655 are stock footage that isn't in RAW").

## Deliverables

```
output/
  build_ae_project.jsx     run in After Effects → builds and saves recreated_edit.aep next to itself
  recreated_edit.aep       only if you could run After Effects on this machine (Stage 7.6)
  media/                   RAW (or an AE-safe conformed copy) + an AE-safe copy of COMPETITOR for the reference layer
  cutlist.json             the single source of truth (schema in Stage 6)
  cutlist.csv              one row per segment, human-readable
  recreated_edit.xml       FCP7 XML for Premiere Pro / DaVinci Resolve
  recreated_edit.edl       CMX3600 EDL (cuts + speed)
  preview_recreation.mp4   frame-exact render of the recreation from RAW
  compare.mp4              competitor | recreation | difference, frame numbers burned in
  report.md                summary, segment table, edit-style breakdown, warnings
  debug/                   mapping.png, scores.png, layout.png, cuts/cut_XX.png, low-confidence frames
tools/match_cuts/          the reusable tool + README.md + tests/
```

CLI: `python -m match_cuts --competitor X --raw Y --out Z [--layout match|fill|source] [--comp-size WxH] [--fps competitor|source]`

`debug/mapping.png` plots competitor time (x) against RAW time (y): every segment is a line, every cut is a jump. It's the fastest way for both of us to sanity-check the edit.

## Ground rules

- Never modify, overwrite or re-save the input files.
- **Time math:** frame rates as exact rationals (`30000/1001`, never `29.97`); positions as integer frame indices; intervals half-open `[in, out)`. Convert to seconds only when writing outputs (≥ 6 decimals).
- **Speed** = Δ RAW seconds ÷ Δ competitor seconds. Never derive speed from frame counts: a 29.97 fps source inside a 30 fps edit is speed 1.000.
- **Frame access:** decode sequentially (ffmpeg pipe with `-fps_mode passthrough`, or `-vsync 0` on old builds, or PyAV) and index frames by presentation timestamp. Never trust OpenCV `CAP_PROP_POS_FRAMES` seeking or `duration × fps` for anything frame-accurate.
- **Analyse exactly the files After Effects will import** (the conformed copies, if you make them), so every timestamp transfers 1:1.
- **Cache** expensive intermediates in `WORK_DIR`, keyed by input file hash + parameters, so re-runs are fast and deterministic.
- **Log** every decision with its evidence (scores, margins, rejected alternatives).
- Work autonomously. Ask me only when blocked (a dependency you can't install, ambiguous inputs).

## Pipeline

### Stage 0 — Environment
- Detect the OS. Check `ffmpeg`/`ffprobe` (prefer ≥ 5.1); install them with the OS package manager if missing, or ask me.
- Create a Python ≥ 3.10 virtual environment and install `numpy scipy opencv-contrib-python scikit-image av soundfile matplotlib tqdm opentimelineio`, plus `scenedetect` **without** its opencv extra (so you don't end up with two conflicting `cv2` packages). If your OTIO version ships the FCP-XML/CMX3600 adapters as separate plugins, install those too — or write the XML/EDL yourself. Optional, only if needed: `librosa`; `torch` + DINOv2/CLIP for hard visual search.
- Look for After Effects: Windows `C:\Program Files\Adobe\Adobe After Effects *\Support Files\` (`AfterFX.exe`, `aerender.exe`); macOS `/Applications/Adobe After Effects */` (the app and `aerender`). AE is optional; the `.jsx` is always produced.

### Stage 1 — Prove the pipeline on synthetic data first
Before touching my files, build a test case with a known answer:

1. A synthetic RAW (~3 min, 1920×1080, 30000/1001 fps) in which every frame is unique — e.g. `testsrc2` or `mandelbrot` plus a large burned-in frame counter (`drawtext` with `%{n}`) — with unique audio (modulated `anoisesrc`/`aevalsrc`, so every audio window is distinct).
2. A fake COMPETITOR whose geometry and timing are made with **ffmpeg filtergraphs, not your own renderer**, so the transform conventions get cross-checked instead of cancelling out: 1080×1920 at 30 fps; a rounded-corner video box on black; ~20 cuts including two same-shot jump cuts, one out-of-order segment, one segment used twice, one 1.10× segment, one horizontally flipped segment, one slow push-in zoom, one 6-frame crossfade, a 1-second NOT-IN-RAW insert, burned-in captions, a static title and logo, and music mixed under the original audio.
3. Run the whole pipeline and assert that it recovers the known edit exactly (cuts ±0 frames, source frames ±0, speeds, flip, zoom keys, transition, placeholder). Keep it as `tests/test_synthetic.py`.

Continue to my files only when this passes.

### Stage 2 — Probe and conform
For both files record (ffprobe plus one full decode pass for PTS): container; video codec/profile; width × height; SAR/DAR; rotation/display-matrix side data; `r_frame_rate` and `avg_frame_rate`; the actual decoded frame count; CFR vs VFR (PTS-delta jitter); per-stream `start_time`; audio codec/sample rate/channels; A/V start offset or edit lists.

After Effects reliably imports H.264 in MP4/MOV and ProRes MOV (HEVC usually works in recent versions). It is unreliable or fails with VP9, AV1, WebM, Opus audio, variable frame rate, and non-zero start times/edit lists — and YouTube downloads often have several of these.

- If RAW has any of these problems, create `output/media/raw_ae.mov`: ProRes 422 (LT if disk space matters) at the **same resolution and same frame rate**, CFR, starting at 0, PCM 48 kHz audio. Verify it against the original: identical frame count, and ≥ 50 frames sampled by PTS that each match their original counterpart (SSIM > 0.98) better than the neighbouring frames do (proves there's no offset).
- If RAW is already AE-safe, put it in `output/media/` unchanged (if it's larger than ~2 GB, reference it by absolute path instead and let the `.jsx` offer a relink dialog).
- Do the same for COMPETITOR (it's only used for the reference layer): an AE-safe H.264 copy in `output/media/` if needed.
- If COMPETITOR is VFR, build the timeline on its nominal frame rate and map each output frame to the competitor frame displayed at that time.
- If either file has rotation metadata or non-square pixels, normalise the analysis frames to display orientation and square pixels.

From here on, analyse only the files the `.jsx` will import.

### Stage 3 — Analysis proxies
- Decode both videos once, sequentially, into memory-mapped NumPy arrays: grayscale at ~360 px wide for search; colour at ~720 px wide (or on-demand windows decoded sequentially from the previous keyframe) for transform estimation and scoring. Keep a PTS array per file.
- Extract audio as mono 16 kHz float WAV for analysis; keep the original-rate audio for the final checks.

### Stage 4 — Competitor layout analysis
Repost edits usually wrap the source in a layout. The kind I'm targeting: a black 9:16 canvas; channel logo + name at the top; a multicoloured title; the source cropped to a roughly square box with rounded corners in the middle; word-by-word captions burned in over the box; a small watermark under it. Detect — don't assume:

1. **Static mask** — per-pixel temporal standard deviation over all frames; near zero = static background or static overlays (logo, title, watermark, frame). Save as `STATIC_MASK`.
2. **Video region(s)** — the region(s) whose content changes and matches RAW: bounding box, corner radius (from the edge profile at the corners), border/stroke/shadow if any. Check whether the layout changes over time (full-screen vs boxed shots, split-screen, picture-in-picture) and record it per time range.
3. **Background** — solid colour (sample it), blurred copy of the source (compare with a heavily blurred, cover-scaled RAW frame), image, or gradient.
4. **Dynamic overlays inside the video region** — captions, emojis, stickers, arrows, progress bars. Detect them as high-contrast text-like regions and/or regions with consistently high residual after the Stage 5 warp. Build a per-frame `OVERLAY_MASK`, dilate it a few pixels, and exclude those pixels from keypoint detection and from every similarity score. Log caption timing for the report.

Save `debug/layout.png` showing every detected zone.

### Stage 5 — Map every competitor frame to a RAW frame (the core)
Goal: a function `m(k)` giving, for every competitor frame k, the RAW frame shown (or NONE) plus a confidence. **Cuts are the discontinuities in m(k).** Do not rely on shot detection to find cuts: repost edits are full of same-angle jump cuts and mid-shot trims that scene detection misses (my first example squeezes a 2:24 commercial into a 60 s short).

**5.1 Audio coarse alignment (fast, precise in time)**
- Slide ~1.0 s windows of competitor audio (hop 0.1–0.25 s) over the RAW audio with FFT-based normalised cross-correlation: first on a robust low-rate feature (log-mel or onset envelope at ~100 Hz) for the coarse offset, then on the 16 kHz waveform within ±50 ms for sample precision.
- If peaks are weak at speed 1.00, test time-scaled windows (0.90–1.30 in 0.01 steps) — pitch-shifted speed-ups are common in reposts.
- Confidence = peak-to-second-peak ratio (and peak-to-sidelobe), not the raw peak. Windows dominated by added music, SFX or voice-over will score low; visual matching covers them.

**5.2 Visual candidate search (robust to crop, zoom, flip, colour grade)**
- Work inside the video region only, with `STATIC_MASK` and `OVERLAY_MASK` excluded (otherwise keypoints lock onto caption text).
- SIFT (or ORB for speed) keypoints/descriptors on competitor frames (every frame near suspected cuts, every 2–3 frames elsewhere) and on RAW frames (every frame for short RAWs; 2–5 fps samples for long RAWs, then refine).
- Shortlist RAW candidates by descriptor voting (FLANN or brute force, Lowe ratio 0.75). Restrict to ±2 s around the audio candidate when audio is confident; search globally otherwise.
- Verify each candidate with `cv2.estimateAffinePartial2D` + RANSAC (uniform scale, rotation, translation), also against the horizontally flipped RAW frame. Accept at ≥ 25 inliers and inlier ratio ≥ 0.3 (tune on the synthetic test).
- Fallback for textureless or heavily graded material: global embeddings (DINOv2/CLIP) of the video region to shortlist shots, then the same RANSAC verification.

**5.3 Frame-exact refinement**
- Warp each candidate RAW frame into competitor space with its transform and score it with ZNCC over the video region minus the masks (add gradient-magnitude ZNCC if the colour grade differs).
- Evaluate RAW frames m−3 … m+3 for every competitor frame; keep the best score, second-best score and margin.
- If neighbouring RAW frames are visually identical (difference below noise: freeze frames, static shots, duplicated frames), mark the frame *ambiguous-identical* and disambiguate with audio where possible.

**5.4 Segmentation**
- A segment = a maximal run of competitor frames explained by one linear time map `raw_time = a + v · comp_time`, one continuous framing model and one flip state.
- Fit `v` (speed) with robust regression (RANSAC/Huber). Snap to a common value (1.00, 1.05, 1.10, 1.15, 1.20, 1.25, 1.50, 2.00 or their inverses) only if within 0.3 % and the residuals don't get worse.
- Put each cut where the model breaks (jump in raw_time, change of shot/transform/flip) and verify it from both sides (criterion 2).
- Cross-check with PySceneDetect (AdaptiveDetector + ContentDetector at a low threshold) on the competitor: every detected scene change must coincide with a mapping discontinuity or a transition. Investigate and explain every disagreement.
- Detect and label: crossfades (frames fit α·A + (1−α)·B — estimate the α curve and duration), dips to black/white, flash frames, freeze frames (m constant while RAW moves), reverse playback (v < 0), speed ramps (v varies smoothly → time-remap keys), frame blending / optical-flow retiming (frames are blends of neighbouring RAW frames — note it; AE's Frame Blending can approximate it), and `NOT-IN-RAW` frames (no match anywhere: added B-roll, memes, intros, end cards).
- Keep genuine 1–2 frame segments (flash cuts) only if verified; remove them only when the evidence shows they're matching errors.

**5.5 Framing per segment**
- Estimate the transform on frames spread through the segment (every 2–5 frames). Stable (scale spread < 0.3 %, position spread < 1.5 px) → one constant transform. Otherwise it's an animated zoom/pan: smooth lightly, simplify with Ramer–Douglas–Peucker (≈ 0.5 px / 0.1 % scale) into keyframes, and detect ease-in/out.
- Include rotation only if |θ| > 0.2°. Try a full affine (non-uniform scale) only if the similarity fit is clearly worse.

**5.6 Audio per segment**
- Check whether the audio cuts coincide with the video cuts; for J/L cuts (audio leads or trails) record separate audio in/out points.
- Identify what the competitor added (music bed, SFX, voice-over) by comparing RAW-rebuilt audio with the competitor's audio (residual energy over time). Log the timings for the report; don't recreate them.
- For speed-changed segments, determine whether pitch was preserved (compare pitch contours of matched speech).

### Stage 6 — Edit decision list (`cutlist.json`) + exact phase solve

Schema (values below are illustrative; add fields if needed, keep these):

```json
{
  "version": 1,
  "competitor": { "file": "media/competitor_ref.mp4", "width": 1080, "height": 1920, "fps": "30/1", "frames": 1800 },
  "raw": { "file": "media/raw.mp4", "width": 1920, "height": 1080, "fps": "24000/1001", "frames": 3453, "conformed": false },
  "layout": {
    "mode": "match", "canvas_bg": "#000000",
    "box": { "x": 60, "y": 420, "w": 960, "h": 1000, "corner_radius": 36 },
    "background": "solid",
    "zones": [ { "type": "title", "x": 90, "y": 260, "w": 900, "h": 140 } ]
  },
  "segments": [
    {
      "id": 1, "type": "raw",
      "comp_in": 0, "comp_out": 57,
      "raw_in_frame": 812, "raw_in_seconds": 33.888021, "speed": 1.0,
      "flip_h": false,
      "transform": { "scale": 0.9375, "rotation_deg": 0.0, "tx": -360.0, "ty": 413.75 },
      "transform_keys": [],
      "transition_in": null,
      "audio": { "in_offset_frames": 0, "out_offset_frames": 0 },
      "confidence": 0.99, "ambiguous_frames": [], "notes": ""
    }
  ],
  "overlays_detected": [ { "type": "captions", "comp_in": 12, "comp_out": 40 } ],
  "added_audio": [ { "type": "music", "comp_in": 0, "comp_out": 1800 } ]
}
```

`comp_in`/`comp_out` are competitor frame indices, half-open. `transform` maps RAW pixels (after flipping, when `flip_h`) to competitor pixels.

**Exact phase solve.** Integer frame indices aren't enough when frame rates differ or speed ≠ 1 — the continuous `raw_in_seconds` decides which RAW frame appears on each comp frame. With `t_k = k / comp_fps` and `t_in = comp_in / comp_fps`, After Effects shows at comp frame k the RAW frame `floor(raw_fps · (raw_in + v · (t_k − t_in)))` (AE rounds absolute times down). So every matched frame gives the constraint

`raw_in ∈ [ m(k)/raw_fps − v·(t_k − t_in) ,  (m(k)+1)/raw_fps − v·(t_k − t_in) )`

Intersect the constraints of all matched frames in the segment and take the centre of the feasible interval (maximum margin against floating-point error). If part of that interval also satisfies round-to-nearest sampling for every frame, take the centre of that overlap instead, so the result holds under either rule. An empty intersection means the model is wrong: re-fit v, re-check outlier frames, or flag the segment (frame blending / VFR). The AE render test in Stage 9 is the final judge.

### Stage 7 — After Effects project (`build_ae_project.jsx`)

Generate a self-contained ExtendScript file from `cutlist.json`.

**7.1 ExtendScript rules.** AE scripts run in ExtendScript (ECMAScript 3): `var` and `for` loops only — no `let`/`const`, arrow functions, template strings, `forEach`/`map`, or `JSON` (embed the data as a JavaScript object literal). Start with `#target aftereffects`; wrap everything in `app.beginUndoGroup()` / `app.endUndoGroup()` and `try { … } catch (e) { alert(…) }`. It must work on AE CC 2019 and newer.

**7.2 Media and project**
- Find media relative to the script (`var here = new File($.fileName).parent;`), then the absolute path, then `File.openDialog("Locate the RAW video")`.
- `app.newProject()` (abort cleanly if it returns null); project folders `01 Comps`, `02 Source`, `03 Reference`.
- Import RAW with `ImportOptions` as footage. Check `frameRate`, `duration`, `width`, `height` against `cutlist.json`; if AE misreads the frame rate, set `footage.mainSource.conformFrameRate` to the exact value and log it.

**7.3 Comps (by LAYOUT_MODE)**
- MAIN comp `Recreated Edit`: AE_COMP_SIZE, the competitor's exact frame rate (compute `30000/1001` in JS — don't type 29.97), duration = competitor frames × `comp.frameDuration`, black background.
- `match` with a boxed layout: a pre-comp `Video Box` sized to the box holds all segment layers; place it in MAIN at the box position with a rounded-rectangle mask of the measured radius (Bezier corners, tangent length ≈ 0.5523 × radius) or a shape-layer rectangle with Roundness used as an alpha matte. Put the detected background under it (a solid; or, for blurred backgrounds, a duplicated cover-scaled layer with Gaussian Blur — verify the effect matchName in try/catch and skip gracefully if it fails). Add labelled guide layers (`guideLayer = true`; guides never render) outlining the header, title, caption and watermark zones so I can drop in my own.
- `fill`: segments directly in a 1080×1920 MAIN. Per segment, put the RAW point that sits at the centre of the competitor's box at the centre of the frame, and apply the competitor's relative zoom (its scale ÷ the box's cover scale) on top of the 9:16 cover scale; clamp so no empty edges show.
- `source`: MAIN at RAW size and frame rate, identity transforms — cuts only.

**7.4 One layer per segment** (first segment on top, chronological downwards; add background and reference layers after the segments, or reorder at the end):

```javascript
var fd = comp.frameDuration;
var L = comp.layers.add(rawFootage);
L.moveToEnd();                                         // chronological stacking
L.name = "S" + pad(seg.id) + "  RAW " + tc(seg.rawIn, rawFps);   // define pad()/tc() helpers
L.stretch = 100 / seg.speed;                           // set BEFORE startTime
L.startTime = seg.compIn * fd - seg.rawIn / seg.speed; // comp frame compIn shows RAW time rawIn
L.inPoint  = seg.compIn  * fd;                         // integer frames × frameDuration
L.outPoint = seg.compOut * fd;
```

Use time remapping instead of `stretch` for reverse playback, freeze frames and speed ramps (enable it, then replace the two default keys with the measured ones).

**Transform conversion** — `(s, θ, tx, ty)` is the similarity transform from the (flipped, if `flip_h`) RAW frame to competitor pixels, `r` = target pixels ÷ competitor pixels, `c = [rawW/2, rawH/2]`:

```
Anchor Point = c
Scale        = [ (flip_h ? -1 : 1) · 100·s·r ,  100·s·r ]
Rotation     = θ in degrees (clockwise-positive, same as OpenCV's y-down matrix: θ = atan2(M[1][0], M[0][0]))
Position     = r · ( s · R(θ) · c + [tx, ty] )        with R(θ) = [[cos θ, −sin θ], [sin θ, cos θ]]
```

The same Position formula holds for flipped segments because the transform was estimated against the flipped frame. Inside the `Video Box` pre-comp, subtract the box origin from `(tx, ty)` first. Animated framing → `setValueAtTime()` keys at the measured comp times, linear unless easing was measured. Unit-test this conversion in Python against `cv2.warpAffine` before using it.

**7.5 Everything else in the comp**
- Transitions: overlap the two layers by the measured duration and keyframe Opacity (crossfade); dips → a solid with opacity keys; flash frames → a white solid.
- `NOT-IN-RAW` ranges: a coloured solid named e.g. `MISSING – not in RAW (00:12:05–00:13:10)` plus a marker, so the timing gap stays visible.
- Audio: each segment layer carries its own RAW audio. For J/L cuts, add an audio-only duplicate (`enabled = false`, `audioEnabled = true`) and silence the video layer (`audioEnabled = false`). AE's time stretch changes audio speed and pitch together, like tape; if the competitor preserved pitch on a sped-up segment, say so in the report.
- A comp marker at every cut, e.g. `Cut 07 | RAW 00:01:12:04 | speed 1.10 | conf 0.98` (`comp.markerProperty`, in try/catch).
- Reference layer: COMPETITOR (from `media/`) on top of MAIN as a guide layer — `guideLayer = true`, `audioEnabled = false`, `blendingMode = BlendingMode.DIFFERENCE`, `enabled = false`, scaled to the comp if sizes differ, named `REFERENCE – competitor (turn on: black = match)`. Guide layers never render.
- Work area = whole comp; open MAIN in the viewer; save with `app.project.save(new File(here.fsName + "/recreated_edit.aep"))`. If saving fails, tell me to enable *Allow Scripts to Write Files and Access Network* (Preferences → Scripting & Expressions; General in older versions).
- Finish with an `alert()` summary: segments, cuts, duration, warnings.

**7.6 Run it here if you can.** If AE is installed: Windows `"<AE>\Support Files\AfterFX.exe" -r "<absolute path>\build_ae_project.jsx"`; macOS `osascript -e 'tell application "Adobe After Effects <version>" to DoScriptFile "<absolute path>/build_ae_project.jsx"'`. Then, if `aerender` exists, render MAIN with a lossless or PNG-sequence output module (template names differ between versions — list them and pick one) for the Stage 9 check. If AE isn't available here, just produce the `.jsx` and tell me how to run it.

### Stage 8 — Other exports
- `preview_recreation.mp4` — your own frame-exact renderer, not an ffmpeg trim/concat chain: for every comp frame, take the RAW frame given by the Stage 6 sampling rule, flip if needed, `cv2.warpAffine` with the segment transform (interpolated for animated keys), composite the layout (background, rounded box mask), and write through an ffmpeg pipe at the competitor's size and fps (H.264, CRF ≤ 16, yuv420p, `+faststart`). Build the audio sample-accurately from RAW (speed-changed segments resampled the way AE plays them; transitions applied) and mux it. No competitor overlays.
- `compare.mp4` — `hstack` of competitor | recreation | amplified absolute difference, same height, with frame number, timecode and segment id burned in; competitor audio.
- `recreated_edit.xml` (FCP7 XML) and `recreated_edit.edl` (CMX3600) — a sequence at the competitor's size and fps, one clip per segment on the same media, speed as a speed/time-remap effect (EDL: `M2` lines), cut markers; basic motion (scale/centre) in the XML where feasible. Validate by re-parsing and checking the total duration. (Premiere imports the XML, and After Effects can import Premiere projects — an alternative route into AE.)
- `cutlist.csv` and everything in `debug/`.

### Stage 9 — Verification (must pass)
1. **Coverage** — segments + placeholders tile the competitor timeline exactly (equal frame count; no gaps or overlaps except measured transitions).
2. **AE-semantics simulation** — from the exact `startTime`/`stretch`/`inPoint`/`outPoint`/time-remap values written into the `.jsx`, recompute which RAW frame AE shows on every comp frame; it must equal m(k) for every matched frame (ambiguous-identical excepted).
3. **Visual** — masked ZNCC between competitor and `preview_recreation.mp4` on every frame (video region, overlays excluded); report the distribution; every matched frame must clear the threshold tuned on the synthetic test (typically ≥ 0.90); save thumbnails of every failure.
4. **Cuts** — `debug/cuts/cut_XX.png` for each cut: competitor vs recreation for frames k−1, k, k+1, k+2.
5. **Audio** — per-segment cross-correlation lag within ±10 ms; explain every exception.
6. **After Effects** — if available: identify the RAW frame in every frame of the AE render (same method as 5.3); it must equal m(k). Small resampling differences between AE and your preview are fine.
7. **Determinism** — a second run with caches reproduces an identical `cutlist.json`.

On any failure: diagnose (frame-rate interpretation? start-time offset? missed flip? wrong speed snap? masks too small? transform convention?), fix, re-run. Repeat until it passes.

### Stage 10 — Report and final message
`report.md`:
- Inputs: codecs, fps, sizes, durations, VFR/offset issues, any conform step and why.
- Detected layout: box geometry, corner radius, background, overlay zones (with `layout.png`).
- Segment table: # · comp in–out (timecode + frames) · duration · RAW in–out (timecode) · speed · flip · scale/position (or "animated") · transition · confidence · notes.
- Edit-style breakdown: number of cuts, average/median shot length, % of RAW used, which RAW ranges were cut out, reordering/re-use, speed factor(s), zoom punch-ins, flips, rotation, caption timing and style, added music/SFX/VO timings.
- Warnings: low-confidence frames, ambiguous-identical frames, NOT-IN-RAW ranges, anything AE can't reproduce.
- How to open: File → Scripts → Run Script File… → `build_ae_project.jsx`; the preference to enable; how to use the reference layer.

Then give me a short chat summary: pass/fail per acceptance criterion, file paths, warnings. No wall of text.

## Edge cases to handle explicitly
- **RAW is a different upload** than the one the competitor used (4K vs 1080p, different grade, letterboxing, logo bug) → transforms + gradient ZNCC handle it; document the differences.
- **Frame-rate mismatches** (24/25/30/50/60 in either direction, duplicated or dropped frames) → handled by the mapping; never reported as speed changes.
- **Content-ID dodges:** mirrored shots, 1–3° rotation, 102–115 % zoom, slight speed-ups (1.05–1.25×), pitch shift, colour shifts, grain/noise, borders.
- **Non-chronological edits:** a hook from later in the video placed at the start; the same moment used twice.
- **Multi-region layouts:** split-screen (two crops of the same or different moments), picture-in-picture, stacked reaction layouts → one layer per region, each with its own mask and transform.
- **Very long RAWs** (1–3 h podcasts/streams) → audio-first coarse search, memory-mapped low-res proxies, visual search only in shortlisted windows; keep memory bounded.
- **Competitor audio fully replaced** (music + voice-over) → visual-only mapping; say so.
- **Static shots, freeze frames, duplicated frames** → ambiguous-identical handling.
- **Captions/stickers covering most of the frame** → mask them; rely on the uncovered area and on audio.

## Code organisation
Modules: `probe, conform, proxies, layout, audio_align, visual_match, refine, segment, phase_solve, export_ae, export_xml_edl, render_preview, verify, report, cli`. Unit tests for the time math (phase solve, stretch/startTime), the transform → AE conversion (checked against `cv2.warpAffine`), and the synthetic end-to-end test. A README with setup, usage and troubleshooting. Use multiprocessing and vectorised NumPy where it helps; a 90 s competitor against a RAW of up to 30 min should finish in minutes on a normal laptop.
