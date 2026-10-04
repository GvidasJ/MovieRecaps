# Task 5: maximum-quality cuts by default, `--fast` for quick runs

## Result

Every run is now thorough unless you add `--fast`:

| | the default (thorough) | `--fast` |
|---|---|---|
| RAW index | **every RAW frame**, searched **exactly** on the GPU | 10 frames a second (3 for a RAW over 10 min) |
| competitor frames searched | **every one** | every 3rd (refine fills in the rest) |
| slightly uncertain frames | **re-checked at full resolution** before the cuts are decided (and the frames around every jump) | – |
| where each cut goes | decided **at full resolution** | on the proxy |
| each clip's framing | **every frame measured at full resolution**, keys fitted to that | the proxy's per-frame measurements |
| verification | the proxy checks **plus every frame and every cut at full resolution** (new check 9.9) | the proxy checks |
| speech-safe cuts (where words are) | large-v3, the most accurate model of Task 4 | large-v3-turbo |
| framing check (who speaks) | YuNet + Light-ASD on every RAW frame the edit plays (unchanged) | the same |
| worker processes | one per physical core | the same |
| end summary | `Run time`, what the full-resolution pass did, and `Against --fast` | `Run time` |

### check-all, the thorough default

**Every hard check passes on all four videos** (coverage, determinism, deliverables: XML / speech / flash / person /
link checks). The other checks, against Task 4's final runs. Like c2–c5, the new 9.9 is a measurement, not a hard check:

| video | c2 cuts verified | c4 framing problems | c5 audio failures | 9.9 full resolution (new) |
|---|---|---|---|---|
| deadpool | 19 of 24 → **18 of 22** (4 failed, was 5) | 13 → **8** | 6 → 6 | **pass** |
| spiderman-school | 25 of 34 → **25 of 30** (5 failed, was 9) | 5 → **1** | 19 → **4** | **pass** |
| zendaya | 14 of 25 → **17 of 26** (9 failed, was 10) | 3 → 3 | 8 → **6** | **pass with exceptions** (the cadence) |
| zendaya-age | 5 of 14 → **10 of 18** (8 failed, was 8) | 11 → **5** | 6 → **5** | fail: 16 frames, one cadence-phase pattern |

**The caption score went down on Deadpool**, and the thorough edit is why:

| video | Task 4 | now, thorough | now, `--fast` |
|---|---|---|---|
| deadpool | 47/60 (78 %), 1.6 % word errors | 37/60 (62 %), 3.2 % | 46/60 (77 %), 3.2 % |
| spiderman-school | 14/64 (22 %), 6.1 % | 11/64 (17 %), 6.1 % | not run |
| zendaya-age | 3/19 (16 %), 22.5 % | 2/19 (10 %), 25.0 % | 1/19 (5 %), 20.0 % |
| all keys | 64/143 (45 %), 6.4 % | 50/143 (35 %), 7.4 % | |

Rule breaks stay at 0 everywhere. On Deadpool the loss is caption **timing**: 12 captions with your text start more
than 2 frames off (3 with `--fast`). Deadpool's captions follow the competitor's own caption timing, mapped onto my
edit, and the thorough edit plays the RAW differently from the competitor in a few places. The biggest is the J-cut
at "Brad Pitt's going" (bug 2 below), where the cut now waits for the picture's cut: "Brad Pitt's" starts 17 frames
early there. The cuts fit the competitor better at full resolution, while the captions, timed on the competitor's
captions, fit them worse. Fixing that means timing follow-mode captions on the words my edit actually plays wherever
the edit differs from the competitor's. It is the first thing I would do next. I have not done it here, as you asked
me to wrap Task 5 up.

### Run times on this PC

`python -m match_cuts check-all` from empty caches (nothing reused), the thorough default:

| video | RAW | competitor | **run time** | search | refine | re-check | segments | `--fast` comparison | verify | your render |
|---|---|---|---|---|---|---|---|---|---|---|
| deadpool | 203 s, 1080p, 29.97 fps | 700 frames | **9m01s** | 2m34s | 55 s | 26 s | 33 s | 1m24s | 1m26s | no: the clean measurement |
| spiderman-school | 284 s, 720p, 59.94 fps | 1,965 frames | **82m07s** | 33m (clean) | 9m15s | 6m | 4m | 15m | 9m15s | from its refine on (the last 40 min) |
| zendaya | 278 s, 1080p, 25 fps | 740 frames | **16m21s** | 5m20s | 1m49s | 40 s | 40 s | 3m17s | 2m23s | the whole run |
| zendaya-age | 91 s, 720p, 30 fps | 569 frames | **34m38s** | 11m56s | 4m41s | 2m48s | 1m34s | 7m26s | 4m17s | the whole run |

**tests/real/zendaya, the task's test video:**

- thorough, from scratch: **16m21s**. Of that, the analysis is 8m29s, and the `--fast` analysis made inside the same
  run takes 3m17s.
- `--fast`, from scratch: **6m16s**.
- thorough again, from its caches: **4m11s**.

All three ran while your Media Encoder render shared the GPU (from 22:16, see below). Deadpool is the only run of the
final set on a free GPU. The render slows the GPU stages most: Zendaya-age's search took 11m56s for a RAW of 91 s,
while Deadpool's took 2m34s for 203 s.

**What takes the time.** The exact GPU search grows with the RAW's length times the competitor's frames.
Spider-Man's every-frame index has 8.0 M descriptors (17,008 RAW frames), searched for all 1,965 competitor frames
and their mirror images: 33 minutes on a free GPU. Then the refine measures every frame (7,184 tasks), and the
`--fast` comparison repeats a quick analysis for the end summary. A long RAW with a long competitor video takes over
an hour. `--fast` is the quick run.

**The render.** The GPU log of the final runs (`nvidia-smi` every 30 s) shows Media Encoder / After Effects from
22:16:13 to the end:
- Deadpool, 21:25–21:34: **free GPU**.
- Spider-Man, 21:34–22:56: free until 22:16, its search included (33 min); its refine and everything after shared
  the GPU.
- Zendaya (22:56–23:13), Zendaya-age (23:13–23:47), and the `--fast` and cached Zendaya runs after them: the render
  throughout.
- An earlier set (18:36 onwards, also during a render) is not used: there the tool spilled 2.2 GB into system memory
  and its people analysis took 504 s instead of 20 s.


## What the thoroughness changes

Every thorough run also makes the analysis a `--fast` run would make of the same video (from the same caches) and
compares them. The end summary says what differs. From the final runs:

| video | against `--fast` (the thorough cut list) | re-checked at full resolution | 9.9 (every frame and cut) |
|---|---|---|---|
| deadpool | 3 cuts moved (+2, −1, −2 frames), 3 new, 5 gone; 3 frames another RAW frame (2 by one); 3 another kind; uncertain segments 2 → 1 | 112 frames: 24 narrowed (20 to one), 5 decided against the proxy, 83 within noise | **pass**: 543 frames, 16 cuts, ZNCC min 0.7505, median 0.9923 |
| spiderman-school | 3 cuts moved (−1, −2), 2 new, 5 gone; 191 frames another RAW frame (173 by one); uncertain segments 4 → 3 | 1,119 frames: 114 narrowed, 999 within noise, 6 left to the proxy | **pass**: 1,965 frames, 30 cuts, min 0.9025, median 0.9934, framing within 2.0 px |
| zendaya | 1 cut moved (−1), 2 new; 1 frame another RAW frame; uncertain segments 4 → 5 | 208 frames: 50 narrowed (20 to one), 158 within noise | **pass with exceptions**: 575 frames, 24 cuts, min 0.8595, median 0.9762; 40 frames of the repeat cadence, 2 a neighbour fits by < 0.004 |
| zendaya-age | 4 cuts moved (+1, −1, −2), 7 new, 3 gone; 123 frames another RAW frame (59 by one); 28 another kind; uncertain segments 2 → 4 | 352 frames: 93 narrowed (17 to one), 256 within noise, 3 left to the proxy | **fail**: 541 frames, 15 cuts, min 0.8948, median 0.9841; 16 failures, all one cadence-phase pattern (see *What is left*) |

The same cut lists compared frame by frame at full resolution, from the same caches:

| video | 9.9 failures thorough / `--fast` | mean ZNCC thorough / `--fast` | frames under 0.95 | under 0.90 |
|---|---|---|---|---|
| deadpool | **0** / 3 | **0.9877** / 0.9854 | **15** / 27 | **7** / 11 |
| zendaya | **0** / 1 | **0.9724** / 0.9715 | **24** / 25 | 12 / 12 |
| zendaya-age | **16** / 18 | **0.9762** / 0.9739 | **49** / 65 | 2 / 2 |

Thorough fits better than `--fast` by more than 0.01 on 40 frames of Deadpool (`--fast` on 34), 10 of Zendaya (`--fast` on none) and 22 of Zendaya-age (`--fast` on 1).

## What changed

### On the GPU (`gpu.py`, new; `visual_match.py`)

- **Exact nearest neighbours.** FLANN's kd-trees on the CPU are approximate: on a real index only ~60 % of their
  first neighbours are the true nearest one. The GPU now searches the RAW index exactly.
  - SIFT descriptors are integers 0–255. Every squared norm and dot product is therefore an integer below 2^24, so
    float32 with TF32 off computes each exactly, in any order. Equal distances are ordered by index, so the result
    is deterministic.
  - Checked against brute force: 40 of 40 query sets identical.
  - 0.17 s per 1,000 query descriptors against the 3.47 million of Zendaya's every-frame index.
  - Every block stays under 512 MB of GPU memory. On Windows a CUDA allocation past the card's memory spills into
    system memory and runs tens of times slower.
- **Every RAW frame indexed** (6,939 frames, 3.47 M descriptors for Zendaya) and **every competitor frame
  searched**, flipped copies included. Without a CUDA GPU the default samples the RAW like `--fast`, skips the
  full-resolution pass, and says so in the summary. An every-frame index is only worth it with an exact search.
- **Tried and dropped: integer / bf16 tensor cores.** Top-k selection takes 60 % of the search time, not the
  arithmetic. An int8 matrix product needs padded shapes, bf16 with float32 output ran 13× slower here, and an
  integer top-k is ~30× slower. The exact float32 search stays.

### Full resolution (`fullres.py`, new; S5.5 and check 9.9 in `pipeline.py`)

- **Re-check of uncertain frames (S5.5),** before the cuts are decided.
  - Which frames: refine's low-margin, confounded and tied frames, plus the 2 frames on each side of every RAW
    jump. The first frames after a jump cut in a fast pan are blurred, so the proxy's "exact" answer is least
    reliable there. Deadpool's frame 447 was certain on the proxy (RAW 2749), while full resolution prefers 2750 by
    0.035.
  - How: each candidate RAW frame is warped with its own framing, refined by Gauss–Newton at full size, and scored
    by masked ZNCC. When the score is still rising at the edge of the candidates, the search goes on (up to 4 frames
    further). Deadpool's frame 446 rose all the way to the window's edge, and the best frame was one beyond it.
  - What it changes: it narrows which RAW frames a frame may show. It decides against the proxy only when full
    resolution is clearly sure: a frame outside the proxy's range that beats every frame inside by more than 0.01
    ZNCC and scores at least 0.9. Refine's measured range always stays inside the narrowed one. My first version
    left it outside (Deadpool 271), and the segmenter then got contradictory inputs.
- **Where each cut goes, at full resolution (criterion 2).** The segmenter checks every cut by asking whether each
  side's frame fits its own model better than the other side's, and moves the cut when it doesn't. On the proxy,
  inside a fast pan, both models' framings are off at the cut (extrapolated, or an edge key held), and the check
  moved Deadpool's cuts at 271 and 412 one frame the wrong way. In the thorough mode it asks at full resolution, and
  asks the right question:
  - two different RAW frames on the two sides: which one the competitor shows, each with its framing refined;
  - the same RAW frame (a framing-only cut): its framing as delivered.
- **Each clip's framing, measured at full resolution.** Every matched frame's framing is refined at full size
  (sub-pixel), including the transition frames of a fast pan, which the proxy left out and over which it held the
  edge key. The keys are then fitted to those measurements. A measurement below 0.95 ZNCC (motion blur, a dissolve)
  is not used, and the proxy's stays.
- **Check 9.9: every frame and every cut at full resolution.** Each frame as delivered, the framing error the
  refinement finds, whether a neighbouring RAW frame fits better, and for every cut, whether each side fits its own
  model better than the other side's.
  - A neighbouring RAW frame that fits better by more than 0.01 ZNCC is a failure. The one exception is the
    **repeat cadence**, which a constant-speed clip cannot follow: either the competitor shows one picture on two
    frames where the edit's time line steps between them, or the edit shows one RAW frame on two frames where the
    competitor moves on.
  - A cut is explained when the competitor repeats a picture across it.
  - My first version also explained any frame refine had measured that RAW frame for. That hid the slow-motion
    regression described below, so it is gone.

### The models of the speech and framing checks

- **Speech-safe cuts** (cuts only in gaps between words, and the repeat check): the RAW's word map now comes from
  Whisper large-v3, the most accurate model of Task 4 (4.2 % word errors against 6.5 % for turbo). `--fast` keeps
  turbo.
- **The flash-frame check is unchanged.** It finds the RAW's shot changes with the same thumbnail detector as before.
  I looked at a learned shot-boundary model (TransNetV2) for it but did not get to test it against the detector, so
  nothing changed there.
- **Framing check** (the person speaking is in the picture): YuNet faces and Light-ASD, the models of Task 2,
  already run on every RAW frame the edit plays, at 25 fps (Light-ASD's own rate). There is no more accurate model
  in the project. I tried finding faces at the RAW's full width instead of 960 px: on Zendaya the same person was
  found speaking on 688 of 699 frames (the other 11 are an overlap where both speak). It added only two tracks, a
  34 px background face and an 11-frame fragment, for 4× the pixels, so 960 px stays.

### All the cores (`config.py`)

The worker pools now default to one process per **physical** core (psutil), not one per hardware thread. A second
process on a core's other thread makes the matching slower, not faster. The same 120 searches on Zendaya's
every-frame index:

| workers | 4 | 6 | 8 | 10 | 12 | 14 | 15 | 16 | 30 |
|---|---|---|---|---|---|---|---|---|---|
| seconds | 44.9 | 41.5 | 38.5 | 32.6 | 32.5 | 33.6 | 34.4 | 49.6 | 47.2 |

**Windows only sees 15 of your 16 cores.** The Ryzen 9 9950X reports 16 enabled cores and 32 threads, but Windows
exposes 15 cores / 30 logical processors (the process affinity mask has 30 bits). That is usually the "Number of
processors" box in msconfig (Boot → Advanced options; `bcdedit /deletevalue numproc` undoes it) or a BIOS core
setting. It is outside this project, so I did not touch it. The tool uses every core Windows offers.

### End summary and report (`cli.py`, `report.py`)

- The end summary starts its last block with `Run time: … (thorough, the default; the GPU: NVIDIA GeForce RTX
  5080)`, then the re-check, the full-resolution verification and `Against --fast` lines.
- `report.md` has a 9.9 row in the criteria table and a new *Thoroughness* section with the settings used, the
  frames the re-check narrowed, and every difference against `--fast`, frame by frame.

### Bugs the thorough runs found, fixed

1. **A slow-motion segment instead of a cut** (`segment.py`, Zendaya frames 133–140). The competitor shows RAW 3968
   3969 3969 3970 3971 3971 3971: a 1.0 stretch, then its last frame twice more (a cut back to RAW 3971 at 138).
   Those seven frames are also one exact 0.6× line. Whether the DP even considered the cut at 138 depended on where
   a greedy pass happened to start, 40 frames earlier. When the full-resolution re-check narrowed some frames
   there, the candidate vanished, and the edit got a 0.6× segment showing another RAW frame than the competitor on
   2 of its 7 frames (137 and 139). Now a free run shorter than 10 frames never hides the phase breaks of the
   dominant speed / 1.0. The DP then finds the cut at the same cost as before the re-check (11.25 against 12.30 for
   the slow motion).
2. **An audio cut inside "Pitt's"** (`speech.py`, Deadpool S08/S09). The competitor's sound switches to the next
   take 4 frames before its picture does (a J cut), 0.38 s on, inside a word, at a frame where the picture runs
   on. Edges only moved where the picture also cuts, so nothing moved it, and the hard speech check failed. Now an
   audio line that jumps inside speech, too far to shift, where the picture does not cut, takes its picture's own
   sound. A1 then cuts where V1 cuts, in the quiet: "Brad Pitt's | going to do this?".
3. **A framing change under `--min-move`** (`export_xml_edl.py`, Zendaya S25+S26). A clip took its own framing
   (61 px from the one before) because the framing before would not show the person of its own short piece. Once
   joined with the rest of its take, the framing before does show the person, and the XML check failed. The
   `--min-move` hold is now judged again on the joined clips.
4. **A zero-length clip** (`repeats.py`, Zendaya S07). Removing a repeat could leave a sliver of 0–2 frames of a
   clip, written as a clip with in = out. Slivers under 3 frames now go with the removed range.
5. **Gap linking tied to the search stride** (`refine.py`). With every competitor frame searched (stride 1), the
   distance over which refine links anchors shrank with the stride. It is now at least 3 frames, as before.
6. **The thorough default was worse than `--fast` on Deadpool** at first: 9 full-resolution failures against 3, and
   more failed cuts in c2 (8 against 5). Its competitor pans constantly (tx −209…−257 px), like Premiere's Auto
   Reframe. Three causes, each fixed (see *Full resolution* above):
   - the re-check left refine's measured range outside the range it narrowed (frame 271);
   - criterion 2 on the proxy moved cuts the wrong way where both models' framings were off (271, 412);
   - clips held their edge framing key through fast pans (165, 222, 268: 6–9 px off on the frames next to the
     cut).

   After the fixes, Deadpool's thorough cut list passes check 9.9 and `--fast`'s does not (3 failures), from the same
   caches. Over its 540 frames: mean full-resolution ZNCC 0.9877 against 0.9854, frames under 0.95: 15 against 27,
   under 0.90: 7 against 11. On Zendaya: 0 failures against 1, mean 0.9724 against 0.9715, and thorough is
   better by more than 0.01 on 10 frames, `--fast` on none.

## Tests

- **Unit suite** (`-m "not slow"`): **1027 passed, 12 skipped, 1 failed** (8m18s).
  - The failure is `test_probe`'s truncation check on an FLV file. These checks time a decode, and both failures
    tonight came while a render loaded the machine: MKV in one run, FLV in the next.
  - The idle runs earlier tonight passed them, and all 23 probe tests pass alone (3 runs of 3).
  - Task 5 does not touch the probe code. Task 4 saw the same under load.
- **Slow suite** (`-m slow --runslow`): **21 passed, 63 skipped, 0 failed** (23m48s, during the render; the same
  opt-in skips as before). Its end-to-end runs now take the thorough default.
- **check-all**: every hard check passes on all four videos (142m08s; see *Result*).
- **New tests:**
  - `test_fullres.py` (GPU): the scorer and the framing refinement; the re-check narrowing; a pan staying
    undecided; refine's measured range kept inside; full resolution overruling the proxy only when clearly sure,
    following a rising score past the window; the refined framing coming back as a framing that fits.
  - `test_quality.py`: both profiles and `--fast`; the no-GPU fallback; the `--fast` comparison and the end
    summary; the cadence rule; the frames around a jump re-checked.
  - `test_visual_match.py` (GPU): the every-frame index exact against brute force, pickled without a kd-tree, the
    pooled searches equal to direct ones, the memory cap.
  - `test_segment.py`: a short free run keeping the 1.0 phase break; criterion 2 asking the full-resolution scorer
    the right question; the framing samples measured at full resolution.
  - `test_speech.py`: an audio line jumping far inside speech taking its picture's own sound.
  - `test_speakers.py`: `--min-move` judged again after the takes are joined.
  - `test_repeats.py`: no sliver of a clip left after a repeat removal.

## What is left

- **Zendaya-age's 25 → 30 fps cadence is placed one frame off in two clips** (S03, S19). Every 6th frame shows the RAW
  frame before or after the competitor's: 61 frames, and 16 of them beyond 0.01, failing check 9.9. `--fast` has the
  same 61 frames, since the phase is solved from the proxy's measurements, which cannot see it. The new
  full-resolution check found it. Solving the phase from full-resolution evidence is the natural fix.
- **Follow-mode caption timing** where the edit differs from the competitor's (above).
- **Run time for long videos.** The exact search of every RAW frame dominates (33 min for Spider-Man). Moving the
  top-k selection to a two-stage search (approximate candidates, exact re-ranking) could cut that a lot. It would
  need its own exactness test, so it is not in this task.

## Things to check in Premiere

The final runs are in `work/t5final/runs/<video>/001/` (`1_edit.xml`, `2_captions.srt`, `extras/report.md`).
uns\<video>\` (`1_edit.xml`, `2_captions.srt`, `extras
eport.md`).

- **Deadpool, around 10.4 s:** "Brad Pitt's | going to do this?". The audio cut moved from inside "Pitt's" to the
  pause after it, and the next clip starts just before "going".
- **Deadpool, the panning shots** (2.5–17 s): the picture should follow the competitor's pans up to every cut, with
  no jump of a few pixels on the frames next to a cut.
- **Zendaya, 4.4–4.7 s (frames 133–140):** two clips with a cut at frame 138, no slow motion.
- **Zendaya, S25+S26:** the framing is held from S24 (no 61 px jump).
- **Zendaya-age, S03 and S19:** every 6th frame may look a frame early or late against the competitor (*What is
  left*).
- **A run's summary** now ends with the `Run time` block. Check that it reads clearly to you.

## Decisions

- **No GPU, no every-frame index.** Without CUDA the default falls back to the sampled index and skips the
  full-resolution pass, and the summary says so. A CPU kd-tree over every frame would be both slower and
  approximate.
- **The `--fast` comparison runs inside every thorough run.** The summary has to say what the thoroughness changed,
  and that is the only honest way to know. It costs the `--fast` analysis time on top (see the run times). There is
  no switch to turn it off; say if you want one.
- **Full resolution overrules the proxy only when clearly sure** (better by more than 0.01 ZNCC, at least 0.9).
  Otherwise it only narrows, and the count of frames where it disagreed less clearly is shown.
- **Full resolution for decisions, not for everything.** The segmenter scores frames in about 17 places. Only the
  ones that decide what the edit shows moved to full resolution: the re-check, where cuts go, and each clip's
  framing. The rest stay on the proxy, because full resolution everywhere would mean decoding tens of GB of frames
  for checks the proxy already gets right.
- **Faces stay at 960 px**, and the worker count is one per physical core (measured above).
- **Run times: the final set, not repeated.** As you asked, when your Media Encoder render started (22:16) the
  final runs went on and were not redone. *Run times on this PC* lists which stages of which runs shared the GPU.
  Deadpool's 9m01s and Spider-Man's 33-minute search are clean measurements. An earlier set (18:36 onwards, during
  your first render, which held ~13 GB of the card's 16 GB) is not used.
