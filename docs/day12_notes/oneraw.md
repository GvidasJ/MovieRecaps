# Day 12 -- helper "oneraw": section 3, one RAW file instead of two

## What I found

* A 5.1 RAW's run folder held the RAW twice: the **analysis copy** (`extras\media\raw.<ext>` -- a hard link or copy of
  an AE-safe RAW -- or `raw_ae.<ext>`, the conform of a RAW that is not AE-safe: start offset, MKV, VFR ...) and
  `raw_stereo.<ext>` (stereo.py, night 6).
* `raw_stereo` already **is** the file the prompt asks for: `-c:v copy` of the analysed picture + one stereo AAC track
  (FL + 0.707 FC / FR + 0.707 FC, constant gain to -1 dB peak at most), and 1_edit.xml already plays it on V1 and A1.
  The extra file was the analysis copy, which nothing needs once the run is done.

## What I built

* **The single file keeps its name `raw_stereo.<ext>`** (decision): every check, Learn, the published hub projects and
  the user's fixed projects already know that name -- a new name would only add a rename and risk. A ProRes conform
  (`raw_ae.mov`) gives `raw_stereo.mov` with PCM, as before.
* **Non-AE-safe RAW** (decision): the single file is the conform's picture (`raw_ae`, CFR, start 0 -- the frame numbers
  of 1_edit.xml are the conform's) copied as it is, with the stereo sound. Same as stereo.py already did.
* `match_cuts/stereo.py`: `one_raw_file(analysed, single, xmls, raw_input)` removes the analysis copy only when the
  stereo file is there with the same video frame count, no XML of the run plays the copy, and the copy is not the
  run's input itself. `xml_media_names(xml)`.
* `match_cuts/pipeline.py`: `one_raw_file(ctx)` at the very end of `run()` (after verify and the report: the analysis,
  previews, compare video and all checks still read the analysis copy as before). Premiere runs only (an AE project
  imports the analysis copy). Never fails the run; logs "one RAW file: media/raw_ae.mp4 removed (x GB) -- 1_edit.xml
  plays media/raw_stereo.mp4" or why it was kept; decisions.jsonl gets `premiere/one_raw_file`.
* The analysis is unchanged: still reads the 5.1 analysis copy, same hashes, same caches.
* `match_cuts/learn.py`: `run_media` falls back to `media/raw_stereo.<ext>` when the analysis copy is gone (after
  the original download, which is preferred: it is what the run analysed); new `played_raw_name` -- learning from a
  project that plays `raw_stereo.mp4` while the cut list names `raw_ae.mp4` / `raw.mp4` now finds the clips (before,
  it looked only for the cut list's name: "no clip plays the run's RAW" for every night-6+ 5.1 run).
* `app/core/learnkit.py`: `project_raw` also recognises `raw_stereo.mp4/.mov` by name (it found it before only by size,
  rate and length, with a "plays its RAW under another name" note).
* `app/core/rawfiles.py` (new) + Board **Clean up** (`tools/board_tool.py`, only `clean_up` and a progress signal; no
  card-order code touched): finds finished edit folders (board projects and this computer's twin copies) with
  `raw_stereo.*` AND `raw.*`/`raw_ae.*`; in the background with progress in the status line checks: raw_stereo opens
  (ffprobe: same frame count and length as each old file, 2 channels, audio start = video start), no XML of the edit
  folder plays an old file, no `.prproj` in the project folder (the run's `<name>_edit.prproj`, the user's
  `*fixed*.prproj`) plays one -- projects are only READ. Then the question shows each run, the space saved (a hard
  link frees 0 and says so) and the runs that keep both files with the reason. Only on Yes: each item checked again,
  then the old copies deleted (only raw/raw_ae files right in that edit's extras\media).
* Not done (decision): rewriting an XML or a .prproj that plays the old copy. Every night-6+ run's XMLs and generated
  project already play raw_stereo; an XML/project that plays raw_ae has a 6-channel file in it, and swapping only the
  path is not safe -- such a run keeps both files and the message says to relink in Premiere (or Open in Premiere
  again). Pre-night-6 runs (no raw_stereo) are not offered.

## Stage caches

Edited modules: stereo, pipeline, learn (match_cuts) -- none is in any stage's code. Checked against HEAD with
`stage_code_hash(st, base) != stage_code_hash(st, new)` for all 17 stages: **invalidated: []**.

## Picture / sound sync (real 5.1 file)

First 2 min of `D:\Shared\Dude.Wheres.My.Car.2000...AAC5.1-[YTS.MX].mp4` (`-c copy`) -> stereo_copy:
* video: 2880 packets, pts / dts / duration / size / flags / MD5 of every packet **identical**;
* audio: start 0.000 = 0.000, duration 120.021 s = 120.021 s; stereo AAC 48 kHz;
* the sound against the source's 5.1 mixed the same way, cross-correlated at 5 s, 60 s and 110 s: **lag 0 samples**
  (correlation 0.9999).

End to end (CPU only, CUDA hidden): 90 s of that 5.1 RAW + an 8 s made-up competitor, `--premiere --fast --no-frame`
-> exit 1 (OK with notes), extras\media holds `competitor_ref.mp4` and `raw_stereo.mp4` only; log: "one RAW file:
media/raw.mp4 removed (0.04 GB, a hard link of the downloaded RAW: no space freed) -- 1_edit.xml plays
media/raw_stereo.mp4"; 1_edit.xml's only RAW pathurl is raw_stereo.mp4; XML AUDIO OK, XML SYNC OK.

## To check in Premiere (I can't click there)

Open a new 5.1 run's 1_edit.xml (or Open in Premiere): V1 and A1 both play `raw_stereo.mp4`, the run's
extras\media has no raw.mp4 / raw_ae.mp4 any more, lips match the dialogue at the start, the middle and the end, A1 is
one stereo clip per cut with your audio chain (Normalize to 0 dB, Volume 0, Panner 0), no echo. Then drop the fixed
project into Learn: the run is found ("your project plays its media").

## Costs / to know

* A re-run of a finished 5.1 run folder places the RAW again (a hard link: instant; another drive: a copy) or, for a
  non-AE-safe RAW, converts it again (minutes). The app's "next competitor of the same RAW starts from the converted
  RAW" (batch.converted_raw) no longer finds raw_ae after a 5.1 run, so that RAW is converted again for the next
  competitor (x264 is deterministic, so the analysis caches should still be reused).
* The PC has no two-file runs today (D:\Shared\Projects: 0 found, read-only dry run); the laptop's local copies do.

## Tests

* `tools/match_cuts/tests/test_day12_oneraw.py` (10): one file = identical video packets + stereo AAC, same starts;
  stereo RAW untouched; 1_edit.xml V1 and A1 play raw_stereo; analysis copy removed / kept (XML plays it, it is the
  input, the single file missing or another video); hard link frees 0; pipeline hook Premiere-only; Learn run_media
  and played_raw_name.
* `tools/app/tests/test_rawfiles.py` (9): fake old run -> checked, offered, removed only after remove(); fixed prproj
  byte- and time-identical; projects / XMLs playing the old copy keep both; another video keeps both; remove()
  re-checks; stereo-RAW and unfinished runs not offered; learnkit by name; the Board button on the real widgets
  (No keeps, Yes removes, progress signal, space saved in the question).

Test counts: `tools/match_cuts/tests -k "stereo or conform or media or xml or oneraw or learn"`: 154 passed, 3 skipped;
`tools/app/tests`: 746 passed, 6 skipped (both with CUDA hidden, own --basetemp, this worktree's code).

## Incident (my mistake)

At about 07:48 I killed leftover python processes by a command-line filter on the scratch folder's session id --
other helpers share that scratch prefix, so some of THEIR `pytest tools/app/tests` runs were stopped too. Their test
runs need to be started again; no files were changed by it.
