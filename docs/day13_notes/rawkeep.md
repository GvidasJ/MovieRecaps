# Day 13 — "rawkeep": keep the original RAW, no more 14 GB copies (section 3)

## What I built

**The tool (`tools/match_cuts`)**

* `match_cuts/edit_media.py` (new; no cached stage imports it) — the media step of every Premiere run
  (`--premiere`: the app, the batch, check-all). It decides and places the files `1_edit.xml` plays:
  * **V1 = the original RAW**, `extras\media\raw.<ext>`: a hard link of the input on the same drive (no space),
    else a copy (`conform._link_or_copy`; not enough space → falls back to the old way, with a warning).
    An AE-safe RAW (H.264 8-bit .mp4) was already placed there by the conform and simply stays.
  * **remux** (`-c copy`, nothing re-encoded) into `raw.mp4` when the picture is H.264/HEVC but sits in an .mkv, or
    starts late (a start time / an empty edit — the x265 web Family Guy RAWs start 21 ms late). The remux moves the
    first frame to 0 and is then probed again (full decode): the same number of frames, CFR, no picture issue —
    else the fallback below.
  * **A1** = the same file's sound when it is plain stereo/mono AAC or PCM that starts with the picture (as before);
    otherwise **`raw_stereo.wav`**: 16-bit PCM 48 kHz stereo (5.1 → FL/FR + 0.707·FC, no surround/LFE, the night-6
    mix and peak rule from `stereo.py`), made from the original with the **analysis copy's own audio steps**
    (`conform.plan_transcode`'s `af`: resampled, re-based to video frame 0, Borat's 20 ms late start = 960 samples of
    silence). It is then checked **sample-exact against the analysis copy** at 5 s / middle / end−10 s; if it were
    off, it is remade from the analysis copy's own sound (the same samples by construction); still off → old way.
  * **Fallback** only when frame N of the original cannot be the plan's frame N (VFR, rotation, SAR, odd size,
    interlacing, a picture edit list a remux can't fix, VP9/AV1/webm, H.264 4:4:4 …): `raw_premiere.mp4` made with
    the analysis copy's **picture steps** (the same frames) at **H.264 CRF 17, preset slow**, no sound (A1 = the WAV),
    8 frames compared by SSIM with the analysis copy. The reason goes into a run warning, `report.md` ("The RAW in
    Premiere") and the end summary's *Check by hand* block (`The RAW in Premiere: …`), which the batch and the app show
    (that line only when something differs from before: not for an AE-safe stereo RAW, which plays as it always did).
* `pipeline.py`: `premiere_media()` replaces `stereo_for_premiere()` in `stage_exports` (the old function stays as the
  fallback when the new step fails); `drop_analysis_copy()` at the very end of the run (after the report, the checks
  and the 9.7 re-run) removes the converted analysis copy `raw_ae.<ext>` — only when no XML plays it and V1's file
  has the same number of frames (`stereo.one_raw_file`'s checks). The day-12 `one_raw_file` still runs for the old
  fallback path.
* The export's cut list keeps `raw.file` = the **analysed copy** (whatever the export still reads from the RAW — the
  faces of a stretch to frame — reads exactly the frames the analysis read: no framing change); the delivered files
  are `raw.picture_file` / `raw.sound_file` (`edit_media.premiere_block`), which only the XML's `<file>` elements use.
* `export_xml_edl.py` (small, own functions): A1 clip items on a second `<file id="file-raw-sound">` (sound only, the
  RAW's rate and length, own master clip `masterclip-raw-sound`); `file-raw`'s audio says the original's real channels
  (6 for Borat). `a1_peaks` measures the audio chain's normalisation on the WAV. New `sound_file_problems()` runs inside
  `premiere_sync_problems` (so **XML SYNC** in both `validate_premiere_exports` and `final_xml_problems`): every A1 clip
  plays the RAW or its sound file, the sound file is sound only, at the RAW's rate, as long, linked only to V1 clips of
  the RAW, and on disk a 2-channel sound starting at 0. XML LINK and XML AUDIO needed no change (ids / per-clip URLs).
* `premiere_preview.py`: the framed preview takes A1's sound from the sound file.
* `learn.py`: `run_media` finds the run's `raw.<ext>` once `raw_ae` is gone; `played_raw_name` knows `raw.mp4/.mov/.m4v`;
  new `played_sound_name` → `user_clips(..., sound_name)` and `write_case(..., sound_name)` read A1 from
  `raw_stereo.wav` (the answer key's audio pieces).
* `batch.converted_raw`: hands a converted copy to the next run of the same RAW only when the run's XML still plays it
  (pre-day-13 runs). A day-13 run never makes another run's V1 a converted copy ("one converted RAW per movie": not now).
* `cli.py` / `report.py`: the summary line and the report lines.

**The app (`tools/app`)**

* `core/rawkeep.py` (new) + `tools/board_tool.py`: **Board → Clean up** gets a second offer, *Keep the original RAW*:
  an older run that plays a big `raw_stereo.mp4` gets the original back, only when ALL hold:
  1. the original is still on this computer and is **the very file the run was made from** (whole-file hash = the cut
     list's `source_hash`), found where the run read it, else by its name in the project folder, the local projects
     folder and the shared folder (D:\Shared, 2 levels);
  2. Premiere plays it as it is (the run's own full probe of it, `source_issues`: no VFR / late start / rotation …;
     .mp4/.mov/.m4v; H.264/HEVC/ProRes);
  3. **no Premiere project plays that run's `raw_stereo.mp4`** — the project folder (4 levels), the Desktop and
     D:\Shared (3 levels) are searched, only read. The match is `<project>\<edit>\extras\media\raw_stereo.mp4` (the
     day-12 check matches any `edit\extras\media\…`, which made unrelated projects block — mine names the project);
  4. it frees space.

  The question shows each run (`13.1 GB  2026-10-20_borat\edit: raw_stereo.mp4 (14.0 GB) -> the original … (a hard
  link: no space) + raw_stereo.wav (921 MB)`), the kept ones with their reason, and the total "Space saved". On Yes
  (worker thread, `work=True`): each run checked again (the original unchanged since the check, still no project),
  the original hard-linked in as `raw.<ext>`, `raw_stereo.mp4`'s own sound decoded into `raw_stereo.wav` (sample for
  sample: the same A1), **only the tool's own XMLs** (`1_edit.xml`, `extras\edit_*.xml`) rewritten to the two files
  (`edit_media.retarget_xml`: only file references change; every clip's in/out, framing, speed, chain, links stay) and
  they must pass the tool's XML checks (ITEM, LINK, SYNC incl. the sound file, AUDIO) at least as well as before — then
  `raw_stereo.mp4` is deleted and one line is added to the run's `report.md`. Any failure leaves the run exactly as it
  was (the new files removed, the XMLs untouched).
* `core/learnkit.py` / `core/learn_check.py` (small): `raw.<ext>` counts as the run's RAW by name; A1 is read from
  `raw_stereo.wav` (`sound_name_of`).

## Decisions (the user is away — the safest sensible option)

* **WAV, not .m4a, for A1.** AAC in .mp4/.m4a starts with 1024 priming samples that every program must skip from the
  edit list itself. I also measured on Borat's analysis copy that ffmpeg 8's *seek* and *time-based trim* inside AAC
  in .mp4 land 8–56 samples off (full decodes agree to the sample). A WAV has no priming and no per-frame timestamps:
  sample k is at k/48000 in every program — provably in sync. Cost: 0.69 GB per hour (Borat: 921 MB) instead of
  ~0.14 GB; still ~13 GB less than before.
* **The original keeps a fixed name, `raw.<ext>`** (not its own long name): learn, the app, Clean up and the day-12 code
  all find runs by fixed names; the original's name is in the cut list (`source_path`) and the report.
* **VFR always → the CRF 17 slow copy** (as the brief says), even for a small jitter (mindculpt's Rush Hour: 0.141
  frames). Premiere might place it right, but it can't be guaranteed. Cost: x264 *slow* ran at ~16 fps at 1080p here
  (a 2-minute piece: 3 min) — a 90-minute VFR film would add ~2 h. Rare (phone videos are short).
* **A late-starting picture → a remux, not a conversion** (a full copy of the original's packets, fast, lossless) —
  otherwise every x265 web episode would get a 30-minute CRF 17 slow encode.
* **Old runs are never changed by a run**; only Clean up offers them, and a run whose project (any found one) plays
  `raw_stereo.mp4` keeps it: the user's fixed projects keep working. The user's projects are only ever read.
* **conform.py untouched** — no stage key changes. Checked: `common.stage_code_hash` of all 17 cached stages is the
  same for HEAD and my tree (`changed: 0`). The analysis copy is byte-identical to before (CRF 12 veryfast is scratch).
* **The batch / app queue convert the RAW again for each competitor of the same RAW** (the analysis copy is removed at
  the end of each run). Before, the second competitor reused the first run's `raw_ae.mp4` — but that would make the
  second run's V1 a converted copy, and "one converted RAW kept per movie" is listed as *not now*. Cost: one more
  conversion per extra competitor of a converted RAW (Borat: ~17 min at 117 fps).

## Real tests on real RAWs (2-minute `-c copy` pieces in `work\t28\rawkeep\`)

| piece | what the step chose | sound check (lag vs the analysis copy / vs the original's own sound at 5, 60, 110 s) |
|---|---|---|
| Borat x265 10-bit 5.1, sound +20 ms (`borat_cfr.mp4`: the video re-encoded CFR, the sound copied as is) | V1 original (hard link), A1 WAV with `adelay=960S` | 0 / 0 / 0 samples (corr 0.99 — the analysis copy is AAC) and 0 / 0 / 0 against the original (corr 1.00000) |
| Borat `-c copy` cut (`borat_2min.mp4`: the cut made it VFR, jitter 2 frames) | fallback `raw_premiere.mp4` CRF 17 slow (192 MB vs the 365 MB CRF 12 copy, the original 145 MB), A1 WAV | 0 / 0 / 0, and 0 / 0 / 0 |
| Dude Where's My Car H.264 5.1 (`dude_2min.mp4`) | V1 the RAW itself (AE-safe), A1 WAV | 0 / 0 / 0 (corr 1.0), 0 / 0 / 0 |

WAV length = the analysis copy's sound to the sample (Borat 5,760,960 = 5,760,960); start 0; 2 ch; 48 kHz. The mix:
max |L − (FL + 0.707·FC)| = 0.00002 (one 16-bit step is 0.00003). The full Borat film is **CFR** (the laptop run's
probe: restamp, 120,553 frames; issues only HEVC + the audio start/edit list), so a real Borat run takes the
"original + WAV" path: V1 = the 3.7 GB x265 file (hard link), A1 = ~921 MB WAV, no 14 GB copy.

## Space (measured, read only)

`D:\Shared\Projects` holds 32.7 GB of RAW copies in 9 runs (Borat 13.97, mindculpt 7.83, Are We There Yet 6.74,
Always Sunny 2.97, Family Guy 0.71 + 0.50, small raw.mp4s 0.02). `input\downloads` is empty on this PC: the
originals of 8 of these runs are gone (only Borat's is in `D:\Shared\borat\`, and Dude's in `D:\Shared\`).
Clean up dry run (read only, 27 s): it offers **Borat: 13.1 GB freed** (14.0 GB → hard link + 921 MB WAV); keeps
Are We There Yet + Always Sunny (originals gone — their raw_stereo is the only copy of the picture), mindculpt (VFR),
the two Family Guy runs (picture starts 21 ms late). New runs of such films keep ~0 GB of picture copy (hard link)
+ 0.69 GB/hour of WAV, instead of ~10 GB/hour. (The user's `.zip`/`.7z` archives of these projects, ~37 GB, are theirs.)

## What a run now writes (for the "health" helper's space estimate)

* during the run, on the run folder's drive: the analysis copy as before (AE-safe input: a hard link = 0, else a
  copy = 1× input; not AE-safe: ≤ 10 min ProRes LT ≈ 0.75 GB/min at 1080p, > 10 min H.264 CRF 12 veryfast ≈ 4× an x265
  original, ~10 GB/hour) **plus** the original for V1 (0 when hard-linked on the same drive, else 1× input; a remux:
  1× input) **plus** `raw_stereo.wav` when needed (0.69 GB/hour) **plus**, only in the fallback, `raw_premiere.mp4`
  (CRF 17 slow ≈ 1.3× an x265 original / ~55 % of the CRF 12 copy);
* at the end of the run the analysis copy is deleted: what stays is the original (link/copy/remux) + the WAV
  (+ the fallback copy).

## What the lead should check in Premiere (I prepared it; one person drives the GUI)

1. Run Borat with this code (e.g. `D:\Shared\borat\Borat (2006) (1080p BluRay x265 Silence).mp4` + its competitor),
   on the same drive as the output. In `<run>\extras\media`: `raw.mp4` (3.7 GB, a hard link — Explorer shows the
   size, `fsutil hardlink list` shows both names), `raw_stereo.wav` (~0.9 GB), **no** `raw_ae.mp4`, **no**
   `raw_stereo.mp4`. The end summary says "The RAW in Premiere: V1 plays the original RAW … A1 plays raw_stereo.wav".
2. Import `1_edit.xml` into Premiere 2024: nothing offline; the Project panel shows `raw.mp4` (HEVC 10-bit opens) and
   `raw_stereo.wav`.
3. The clips are linked: select a V1 clip — its A1 clip is selected too; drag it — both move.
4. **Sync** at the first clip, one in the middle and the last: lips on the words. Objective check: import the old
   Borat `1_edit.xml` (it plays `raw_stereo.mp4`) into the same project — at the same sequence time the A1 waveforms of
   the two sequences must be identical (same peaks on the same frame), and the V1 pictures the same frame (the cut
   points did not move: compare a clip's Source In timecode).
5. The audio chain: select all A1 clips, apply the preset; the Volume/Gain from the XML is there (XML AUDIO checked it
   on the WAV).
6. Learn: save the project, then Learn → the waiting card finds the run (V1 `raw.mp4`, A1 `raw_stereo.wav`).
7. Optional: Board → Clean up on the PC — it offers Borat (13.1 GB) as above; Yes rewrites only the run's own
   `1_edit.xml`/`extras\edit_50fps.xml`; then import that `1_edit.xml` once more and repeat 2–4 (the old Borat run
   with the original + WAV).

If Premiere shows A1 off by ~21 ms or V1 off by a frame on HEVC, tell me: the fallback can be made the default for
that case in `edit_media.picture_choice` (one line).

## Not done / left

* No full run on a real feature film (the GPU is the lead's) — the step was run on real 2-minute pieces.
* The Premiere check itself (above).
* Clean up does not remux/convert old runs whose original starts late or is VFR (they keep their copy).
* The fallback's CRF 17 *slow* encode is slow (~16 fps at 1080p here); *medium* would be ~2× faster at almost the
  same size — not changed, the user asked for slow.
* ffmpeg's AAC-in-.mp4 seek/trim being 8–56 samples off may also touch other ffmpeg -ss reads of AAC (e.g. the A1 peak
  windows) — not checked (sub-ms; outside this section).

## Tests

* Tool: `tests/test_day13_rawkeep.py` — 22 tests (the choices; real small files: a 5.1 RAW whose sound starts late →
  original + sample-exact WAV vs the analysis copy and vs the original's own channels; a stereo AE-safe RAW unchanged;
  an .mkv remuxed; a late-starting picture remuxed to 0; a VFR RAW → CRF 17 slow copy; the sync check catching a
  48-sample shift; the XML with two files; the checks passing and catching a wrong/missing/odd sound file; the end of
  run removal; learn; batch; the fallbacks; the summary line).
* App: `tests/test_rawkeep.py` — 6 tests (found by hash, replaced only on yes, the XMLs rewritten clip for clip and
  passing the checks, a project playing it keeps it and is never changed — another run's project does not block, the
  original gone / another file / VFR kept, a failing check leaves the run as it was, the Board UI question + Yes).
* Counts: see the reply (whole suites run at the end).
