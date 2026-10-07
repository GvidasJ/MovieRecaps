# Task 10: a review of the whole tool

## In short

I went through the tool the way a senior engineer new to it would. I read the parts that write what you import
(the Premiere export and its checks, the captions) and the parts that decide a cut (the speech-safe cuts, the
silence and repeat removal, the B-roll step). I also read the caches and the inputs, the verdict and the check-all
scorecard, `learn` and `restyle`, and everything that touches files on Windows.

I ran a linter over the whole package and looked at every place an error is only logged. I also tested Windows
behaviour directly on this PC: OpenCV with an accented folder name, ffprobe's packet listing run 40 times, and
network paths. The three problems that broke `1_edit.xml` only showed up on real runs: Task 9's full-size video4,
your own video1, and a 29.97 fps version of Zendaya I made to test the new NTSC sequence.

What I found, the ones that matter most first:

| | what | what it did to you | fixed |
|---|---|---|---|
| 1 | B-roll: a short piece whose sound continues another clip was read as "its own sound" | two flash frames in video4's `1_edit.xml` (the run failed its hard check) | yes |
| 2 | the repeat removal cut A1 inside speech (your video1 run) | an audio jump inside "Exactly, so I'm open"; the picture 83 ms behind its sound | yes: checked on your video1 |
| 3 | the speech-safe cuts after a sliver between two clips of one take | A1 cut inside a word (the 29.97 fps Zendaya) | yes |
| 4 | cached results of older code reused after an update | a run could mix stages of two code versions, silently | yes: every cache key holds a fingerprint of the code |
| 5 | a reset or power loss while a cache file is written | a damaged cache file: a crash, or zeros read as frames, silently | yes: flushed before use, a damaged file computed again |
| 6 | a hard check of `1_edit.xml` skipped because its analysis failed | the run still said PASS | yes: "PASS (not checked: ...)", exit 3 |
| 7 | an input file changing during the run (still downloading) | only a line in the log; the run said PASS | yes: the run fails |
| 8 | `learn` reading a run that had not finished | wrong numbers in learned.json (Task 8's video4) | yes: refused |
| 9 | re-timing the replaced caption words failing | a NameError: no captions.srt at all | yes |
| 10 | a 29.97 fps competitor | the whole analysis, then no `1_edit.xml` | yes: a 59.94 fps sequence |
| 11 | Task 9's 4 GPU processes next to the run's own speech models | video4's full-resolution check 4 times slower; a 1080p RAW overflows the GPU | yes: models released first, caches bounded in bytes |
| 12 | a folder with an accent in its name (Windows) | the run failed writing its cut images; After Effects frames unreadable | yes |
| 13 | a RAW on a network share (`\\server\share`) | the clip offline in Premiere | yes |
| 14 | ffprobe's seek sometimes lists nothing (FFmpeg 8.0) | a false "truncated: download it again" for an MKV RAW about 1 run in 8 (an FLV 1 in 40); this was the "flaky" probe test | yes |
| 15 | `restyle --overwrite` with the styled copy open in Premiere | a bare PermissionError | yes: retried, then explained |
| 16 | two runs on one work folder | shared temporary files | yes: one per process |
| 17 | check-all counted a skipped hard check as passed | the scorecard said PASS | yes |
| 18 | 5.1 RAW audio (never tested: every RAW so far is stereo) | A1 may play the front channels only | a warning: check A1 in Premiere |
| 19 | a fresh clone of the project on Windows | 11 caption tests fail: git rewrites the reference subtitle files' line ends | yes: `.gitattributes` |

**The results.** The final check-all ran on the final code: all nine test videos, video5 included, thorough, from
empty caches, on a free PC. **Every hard check passes on all nine.** On the eight that Task 9 also ran, every cut
list is identical to Task 9's (and so to Task 8's). `1_edit.xml` changed only where item 2's fix applies, in video1,
video4 and Zendaya-age; elsewhere it is identical. The scores are Task 9's, except video1's captions: 9 of 111 word
for word instead of 7 (item 2 says why). Overall: 75 of 292 captions word for word (73 before) and 12 of your 78
cuts (the same). video5, the new case, adds 1 of 35 captions and 1 of 14 cuts (*Tests* below). Video4's
full-resolution check, 4 times slower in Task 9, is back to 55 s (item 11).

## The details

**1. Two flash frames in video4's `1_edit.xml`** (`broll.py`: `other_clips_line`). Found in Task 9's timing run of
video4 at full size. Right after the main clip (S08), the competitor showed 1 and then 4 frames of other RAW moments
while S08's speech went on. The audio stage had put those two pieces on S08's audio line ("S08 continued", which
means the sound under them is S08's RAW sound continuing). It also copies that line's match numbers (correlation
0.986, lag 0 ms) into the piece's own audio fields.
The B-roll step then asked "is this piece's sound its own picture's RAW?", read those copied numbers as the
piece's own, and answered yes. Both pieces were kept, and they became flash frames: the export's hard check failed.
Now a piece whose line is another clip's is filled from that line, as the B-roll step's own description always said
(the RAW video of the audio playing there). A piece whose line starts at itself (a slow motion over its own sound)
still counts as its own sound.
On the test-case copy, Task 8 cut that stretch into one 26-frame piece, which was replaced correctly. The full-size
files cut it into 1 + 4 + 23 frames, a borderline split on slightly different pixels, and that exposed the bug. The
Task 8 code puts the same two flash frames there, so this is Task 8's bug, not Task 9's.
On the final code, the full-size run of video4 gives the same cut list, and V1 plays S08 straight through that
stretch as one clip (00:00:07:24 to 00:00:09:24), on its own sound. `1_edit.xml` passes its hard checks, and the
sequence is as long as before.
Tests: `test_broll.py` (the case itself, built from video4's numbers; checked to fail on the old code), and the
own-sound piece left alone.

**2. Your video1 run cut A1 inside speech** (`broll.py`: `slip_onto_line`). Task 8's run of your finished video1
(final.mp4 against the RAW) failed its own hard speech check at 00:00:12:48, inside "Exactly, so I'm open". I first
thought newer code had already changed that. A fresh run on the current code reproduced it exactly, so I traced it.
The competitor's S09 continues S08's sound: the audio step found S09's sound on S08's line. Its picture, though, was
measured 3 frames behind that line, close enough that the B-roll step treats it as the main clip and leaves it as
it is. In the export, the speech-safe step kept the sound playing on across the S08|S09 cut by moving only A1 (the
picture is never touched there). So V1 repeated 5 frames at the cut while A1 played on. The repeat removal then
took those 5 frames out of the sequence, on every track: A1 lost 0.08 s of continuous speech, and the cut landed
inside the word. All through S09 the picture also ran 5 frames (83 ms) behind its own sound.
Now a piece whose sound is on another clip's line, at that line's speed, with its picture within 2 frames of it,
shows the RAW at the time of its sound, its own framing kept. V1 and A1 then agree, nothing repeats and nothing is
cut. That is what the B-roll step already does for B-roll; this extends it to pictures a frame or two off. Test:
`test_broll.py` (fails on the old code). On your video1 again, from the same analysis: the export's hard checks
pass (they failed), nothing is removed as a repeat, V1 and A1 run on together from S08 into S09, and the cut
list itself is identical: only `1_edit.xml` changed. In check-all it applies to 3 pieces, each 2 to 32 ms off
its sound:
- Zendaya-age's S15 (32 ms): V1 ran 2 frames ahead of the sound, skipping 2 frames going in and repeating 2 coming
  out. Now picture and sound are one clip, and the sequence is as long as before.
- video1's S14 (25 ms): V1 and A1 now start together. The clip ends where its picture was planned to, so the
  sequence is 2 frames shorter. The captions are transcribed from the edit's sound, so they came out a little
  differently: 9 of 111 word for word instead of 7, with word errors 12.0 % instead of 11.6 %. That is a side
  effect of the slightly different edit, not a real improvement.
- video4's S03 (2 ms, under a frame): the same frames. A cut that changed nothing, between two pieces of one take
  (S01 and S02), is gone, so the caption "on each other's" is no longer split.
In every other case the caption file is byte for byte the same as Task 9's (and Task 9's as Task 8's).

**3. A cut inside a word after a sliver between two clips of one take** (`speech.snap_edits`). Found by the
29.97 fps test run: its export failed the hard speech check inside "know what's funny". The NTSC sequence itself was
right; the cause is in the speech-safe cuts, and any video can hit it. There, S16 ended inside the word, S17 was two
frames stepping back, and S18 went on two frames after S16's end. The planner removed S17 and let S16 play on through
its place, to exactly where S18 starts: one continuous take. But it only counted S18 as continuing S16 if S16's end
had not moved, so it still treated S18's start as a cut inside the word. It moved that start 4 frames later, and A1
jumped inside the word. Now a clip that starts where the clip before now ends is one take with it. Test:
`test_speech.py` (in the synthetic case the old code even dropped S18 entirely). The 29.97 fps Zendaya again, with
the final code: its export passes every hard check, S10 to S26 now play as one take, and its cut list is identical.

**4. Old cached results after a code update** (`common.py`: `STAGE_CODE`, `stage_code_hash`, `stage_key`).
Every expensive stage is cached in the work folder: probe, conform, proxies, audio, layout, audio alignment, the RAW
index, the search, the frame map, the re-check, scene detection, people, shots, caption reading and speech
recognition. Each was keyed on its inputs, its settings and a version number kept by hand. A code change that
forgot to raise that number reused what the old code had computed, with no warning, so one run could mix stages of
two code versions. The number was last raised in Task 2, while the cached code changed in Tasks 4, 5, 8 and 9.
I went through those changes and found no case where this had actually happened. Tasks 4 and 5 changed settings,
which are in the key; Task 8 changed parts of those modules that are not cached; Task 9's changes give identical
numbers. It would have happened the first time a change slipped through.
Now every cache key also holds a fingerprint of the source of the modules that compute that stage and of what they
import, so changing that code changes the key. The modules that only orchestrate or check (pipeline, verify, the
exports) are left out, because their results are not cached. Test: `tests/test_cache.py`.

**5. A reset or power loss while a cache file is written** (`common.replace_file`, `flush_to_disk`,
`UNREADABLE_CACHE`). Cache files are written to a temporary file and renamed into place. The rename is atomic, but
the data was not flushed to disk first. After a reset or power loss Windows can keep the new name pointing at data
that never reached the disk: a file cut short or full of zeros. The next run loaded it as it was. numpy reads zeros
as valid frames and descriptors, silently; a damaged JSON file crashed the run. This was not theoretical: your PC
shut down by accident during this task. I cleaned that up by hand at the time, deleting every half-written run and
cache. Now the data is flushed before the rename. A cache file that cannot be read is logged, deleted and computed
again, instead of crashing the run.
The test of that fix then caught a Windows catch in it. When numpy fails on a `.npz` file that was cut short, it has
already opened the file, and the file stays open for as long as the error is kept. Windows cannot delete or replace
an open file. In the test, which keeps the logged error, the damaged file could not be replaced ("Access is
denied"). In a run it worked only because Python happened to release the error in time. The cache now opens the file
itself and closes it before deleting it. Test: `tests/test_cache.py` (damaged in three ways: cut short, zeroed,
empty); the cut-short case fails on the code before this fix.

**6. A hard check that could not run said PASS** (`pipeline.run_checks`, `not_verified`, `report.headline`). Three
hard checks of `1_edit.xml` need an analysis first: no cut inside speech (the speech map), no flash frame (the RAW's
shot changes) and the person speaking in frame (who speaks). When that analysis failed, the run logged a warning,
skipped the check and still said PASS, exit 0. Now it says "PASS (not checked: no audio cut inside speech -- the
speech map failed: ...)" and exits with 3, in the report, the headline and check-all. Tests: `test_cli.py`,
`test_report.py`, `test_testcases.py`.

**7. An input file changing during the run** (`pipeline.changed_inputs`). The run notes each input's size and
modification time at the start and compares them at the end. A change, for example a RAW still being downloaded or
re-exported, was only a log line written after the report, and the run said PASS. Now the check `inputs_unchanged`
fails the run: the analysis may mix two versions of the file. Test: `test_cli.py`.

**8. `learn` read runs that had not finished** (`learn.finished_run`). `learn` and `--tool-run` accepted a run folder
as soon as it held `1_edit.xml` and `cutlist.json`. Both are written before the captions, verify and the report, so a
run still going, or one that crashed after its exports, was read as finished. That is how Task 8's video4 learned
"kept 1 of 19" instead of 3. Now a run counts only when `extras/report.md` exists (the last thing a run writes), and
`learn` names the unfinished run. Test: `test_learn.py`.

**9. No captions at all after a re-timing failure** (`captions._time_new_words`). When the forced alignment of
replaced caption words failed, the code called `warn()`, a function that exists only inside another function. The
resulting NameError took the whole captions stage down: no `captions.srt`, where the rough times should have been
kept. The linter found it. Test: `test_captions.py`.

**10. A 29.97 fps competitor** (`export_xml_edl.sequence_fps`, `xml_rate`). The Premiere sequence stayed at 60.00 fps.
A 29.97 fps frame is 2.002 sequence frames, so the export refused at the very end, after the whole analysis, and no
`1_edit.xml` was written. Now a 29.97 fps competitor gets a 59.94 fps sequence (2 frames each, marked NTSC in the
XML) and 23.976 fps gets 47.952; the hard checks accept those rates. None of your nine test videos has an NTSC
competitor: they are at 24, 30 or 60 fps, and the odd ones (24.003, 30.001) snap to 24 and 30. Several RAWs are
29.97 or 59.94 fps, but the sequence follows the competitor. So this had never been run. I made a 29.97 fps copy of Zendaya's competitor
and ran it: a 59.94 fps sequence, every rate and placement check of the XML passed (and it found item 3). Tests:
`test_export_xml_edl.py`.

**11. The full-resolution check got 4 times slower on an HD RAW** (`pipeline.release_gpu_memory`,
`fullres.RAW_CACHE_BYTES`). Task 9 runs the three full-resolution steps in 4 GPU processes. On video4 (a 1280x720
RAW), verify's check took 236 seconds that way, against 57 in one process. The GPU log shows why. While verify
started, the run's own process still held 6.6 GB at 0 % use, mostly the speech-recognition model, which a run loads
for the speech map and the captions and never unloads. The 4 processes then took the card to 15.4 of its 16.3 GB.
On top of that, each process keeps up to 96 RAW frames on the GPU: 1 GB at 720p, 2.4 GB at 1080p. On Deadpool's 1080p
RAW the card did overflow: Windows lent it up to 3.1 GB of the PC's memory, which is much slower. How close it comes
matters: on check-all's smaller copy of video4 (the same 1280x720) the same check took 52 s at 15.1 GB, without
borrowing.
Now the run unloads its speech models (loaded again when needed) and empties torch's cache before starting the GPU
processes, and logs how much memory is free. Each process's frame caches are also bounded in bytes (512 MB of RAW
frames, 256 MB of competitor frames), which changes nothing for a small RAW. A frame dropped from the cache is
computed again the same way, so the numbers do not change. Tests: `test_fullres.py` (bit for bit with room for only
3 frames; the models given back).
Video4 at full size again, on the final code from empty caches: the run gave back 5.5 GB before the check, and
the check took **55 s**, against 236 s on the Task 9 code (and 57 s in Task 8's single process). The card's
memory stayed under 7 GB, against 15.4 GB. The whole run took 44.3 minutes, against 46.5 (verify 3 minutes
faster; the visual search, whose code did not change, a minute slower, which is the usual variation between
runs). The check's numbers are the same as before, the cut list is identical, and the flash frames of item 1
are gone: `1_edit.xml` passes its hard checks. Deadpool's 1080p RAW in check-all no longer overflows the card:
its memory peaked at 11.8 of 16 GB, and what it borrowed from the PC stayed at 0.45 GB (3.1 GB in Task 9's
check-all). The run took 7 minutes against 8, with the same export.

**12. A folder with an accent in its name** (`common.read_image`, `write_image`). OpenCV 5.0 on Windows cannot read
or write an image whose path has non-ASCII letters. I tested this: in a folder named `Vidéos ä`, writing returns
False and reading returns nothing. A run there failed writing its cut images ("could not write cut_XX.png"), and
verify skipped the After Effects frames it could not read. Every image read and write now goes through Python's own
file functions and OpenCV's in-memory codec. Test: `test_windows.py` (and no direct `cv2.imread` / `imwrite` is
left).

**13. A RAW on a network share** (`export_xml_edl._file_url`). `\\server\share\raw.mp4` became
`file://localhost/server/share/raw.mp4` in the XML: the server name turned into a folder name, and Premiere showed
the clip offline. Now it is `file://server/share/raw.mp4`, and the `\\?\` long-path prefix is dropped. Test:
`test_windows.py`.

**14. The "flaky" probe test** (`probe.stream_tails`). The probe checks whether a RAW was cut short (an unfinished
download) by listing the packets at the end of the file with ffprobe. I ran that exact ffprobe command 40 times on
the test's MKV file: 5 times it listed nothing at all, and still exited with success (1 in 40 on the FLV). FFmpeg
8.0's seek sometimes lands past the end of the file. The tool read the empty listing as "the audio stops before the
end": a false "this file looks truncated" for a perfectly good MKV / FLV RAW. That was the probe test that failed now
and then in full test suites since Task 1 (in Tasks 1, 4, 5, 8 and 9). It was put down to load, but in Task 9 it
failed with nothing else running. Now, when the listing misses a stream, the whole file is listed from its start (no
seeking, the same every time, seconds even for an episode). Test: `test_probe.py` (a failed seek).

**15. `restyle --overwrite` with the project open in Premiere** (`restyle.py`). Windows refuses to replace a file
that Premiere has open. That gave a bare PermissionError traceback. Now it retries for a few seconds, then says to
close the project in Premiere.

**16. Two runs on one work folder** (`common.atomic_write_text`, `Cache.npz`, `write_image`). Temporary files had
fixed names, so two runs on the same work folder (or two processes of one run) could write the same temporary file.
They now carry the process number.

**17. check-all counted a skipped hard check as passed** (`check_all.read_run`, `hard_ok`). The scorecard now fails
a case whose hard check could not run or whose input changed, and says why. Test: `test_testcases.py`.

**18. 5.1 RAW audio** (`pipeline.stage_exports`). The XML declares the RAW's channel count, but every A1 clip takes
source track 1. With a 5.1 RAW, Premiere may put only the front channels on A1, while dialogue sits in the centre
channel. Every RAW so far is stereo, so I could not test it. A run now warns when the RAW has more than 2 channels:
check A1 in Premiere.

**19. A fresh clone on Windows fails 11 caption tests** (`.gitattributes`). The tests compare the reference
subtitle files in `srt/` byte for byte. Git's Windows default (`core.autocrlf`) rewrites text files with CRLF line
ends on checkout, so in a fresh clone or worktree these 11 tests fail. I saw it when I ran Task 9's tests in a fresh
worktree: the 11 failures were exactly these, and they passed once the files had the line ends they are stored
with. Your own folder has the original line ends, which is why no earlier test run showed it. `.gitattributes` now
tells git never to convert these files; a Windows-style checkout with it keeps their line ends.

**Also:** a RAW-only run (`raw_only.py`) now follows the same verdict rules: a RAW changed during the run fails it,
and a hard check left unrun gives exit 3. It used to build its own headline from the XML check alone.

## Checked and found sound

- **The Premiere export and its hard checks** (`export_xml_edl.py`): the clips, the framing, the linked pairs, A1, the
  XML writer and every hard check (sequence, items, speech, flash, person, link, repeat, gaps). The checks re-read the
  XML's own numbers instead of trusting the plan that wrote them, so a mistake in the plan cannot hide from them: they
  are how items 1-3 were caught. Those problems were all in what happens before the export (the B-roll step, the
  speech-safe cuts) or in what the checks are given (item 6).
- **The speech-safe cuts, the silence removal and the repeat removal** (`speech.py`, `silence.py`, `repeats.py`):
  sound, except the two gaps of items 2 and 3.
- **The captions:** the timing source (the words your edit plays, on the competitor's picture clock), and the SRT
  times. Each time is rounded to the nearest millisecond and reads back as exactly the same 1/60 s frame.
- **Every reader of a clip's audio numbers**, for the mix-up behind item 1: `learn` places a piece by its sound
  correctly when the piece follows another clip's line, and the audio phase step skips such pieces on purpose.
  Only the B-roll step had it wrong.
- **The verdict:** how verify combines the checks into PASS / FAIL and the exit codes, and that every error that is
  only logged also reaches the report and the end summary.
- **The person-speaking reframing** (`speakers.py`) moves a framing only when it would not show the person (or when
  it was borrowed from a neighbour for a replaced B-roll spot). It keeps the zoom, moves sideways, and every move is
  listed in the report and the end summary.
- **`restyle`** never writes your own project, only the styled copy.
- **Timecodes at 29.97 / 59.94 fps** use drop-frame correctly.
- **The speech recognition cache** keeps one transcript per model, so `--fast` (large-v3-turbo) and thorough
  (large-v3) never reuse each other's words.
- **A busy GPU never changes what a run computes**: whether the GPU is used depends only on CUDA being there, not on
  free memory or load. When the card is full, Windows lends it the PC's memory: slower, the same numbers. Task 9's
  check-all ran video2 and video3 next to your Adobe render (the GPU's memory full and borrowing 5-10 GB): both came
  out identical to Task 8's runs, with no GPU step falling back in their logs.

## Not changed, and why

- **Every real run's headline says FAIL.** Criteria c2-c5 (every cut verified on both sides, every frame
  frame-exact, every clip's framing, every clip's audio) fail on all eight of your real videos, because real edits
  have zooms, B-roll and overlays that the strict After Effects criteria were not made for. What says whether
  `1_edit.xml` is usable is check 9.8 (the hard checks of the XML). As it is, a broken XML cannot stand out from the
  usual FAIL. I recommend headlining `--premiere` runs on 9.8 and showing the criteria as notes, but that changes what
  the tool tells you, so it is your decision, not a bug fix.
- **The same video given twice under two names** (a copy as the competitor and as the RAW) is not rejected; the same
  path is. Nothing breaks: the edit comes out as one clip of the whole video.
- **Settings the pipeline works out before it calls a cached stage** are covered by the cache key only through the
  values they produce, not through the pipeline's own code. A change in how the pipeline prepares a stage's inputs
  changes those values, and so the key, in every case I found.
- **The caption allowlist and the learned glossary** (`caption_allowlist.txt`, `caption_glossary.txt`) are read from
  the folder next to the package. A copy of the package somewhere else captions without them: the allowlist falls
  back to its built-in default and the glossary is skipped, with no warning. Your install (editable, from the
  project) always finds them, and today the default is the same three words as your file (AI, MJ, MCU). I ran into
  this with my own frozen copies of the code: my test copies now include both files, and the earlier runs it
  affected compared like with like.
- **Two runs at the same time on one work folder** now have their own temporary files (item 16), but they still
  share the folder's decisions log, so its lines may interleave. It is a debug file only. Use one work folder per run
  at a time.
- **The pool tests that still skip on Windows** (you asked to fix the old fork / spawn pool tests). Since Task 1, 6 of
  them skipped here. Two now run on Windows too: closing a broken pool (with spawn workers, the kind Windows uses) and
  freeing PyAV's scaler threads (the part that does not need Linux's `/proc`). The 4 still skipped test the
  `fork` start method itself: a stuck and a dead fork worker, and the switch from fork to spawn when native threads
  survive a fork, counted in `/proc`. Windows has neither `fork` nor `/proc`, and on Windows the tool always uses
  spawn workers, so the code they guard never runs on your PC. They run wherever Linux runs the suite.

## Tests

- **Unit suite** on the final code: **1,121 passed, 10 skipped, none failed.** That is two skips fewer than before:
  the two pool tests that now run on Windows too. The 10 left need Linux (`fork`, `/proc`, its fonts, a POSIX
  shell), OpenTimelineIO, or an ffmpeg with `flite`.
- **Slow suite** (the whole tool on synthetic video, thorough, on the GPU): **21 passed, 63 skipped, none failed**
  (the skips: 62 opt-in film24 checks and one byte-for-byte check recorded with Linux's ffmpeg).
- **New tests.** Four of them rebuild a problem a real run showed, and each fails on the code before its fix:
  video4's two flash pieces and video1's S09 (`test_broll.py`), the sliver of the 29.97 fps Zendaya
  (`test_speech.py`), and a cache file cut short (`test_cache.py`). The others: a piece with its own sound left
  alone (`test_broll.py`), zeroed and empty cache files and the code fingerprint in the keys (`test_cache.py`), the
  frame caches bit for bit with room for only 3 frames and the speech models given back (`test_fullres.py`),
  `test_probe.py` (a failed seek), `test_captions.py` (the rough times kept),
  `test_cli.py` and `test_report.py` (the not-checked verdict; an input changed), `test_learn.py` (unfinished runs;
  your edit read from a project), `test_export_xml_edl.py` (the NTSC sequence), `test_testcases.py` (the scorecard),
  `test_windows.py` (accented paths, network paths) and `test_pools.py` (the two pool tests on Windows).
- **Your video1** (final.mp4): the failure reproduced on the code before the fix, then gone with it, from the same
  analysis (item 2).
- **`--fast` runs of three short videos** on the final code (before video5's word was added to the glossary):
  all three exports pass every hard check, with no error in their logs.
  - The 29.97 fps Zendaya: item 3.
  - Deadpool: 43 of 60 captions word for word, against 47 in thorough mode. The extra misses are timing, where
    `--fast` cuts a frame or two apart. The same run on the Task 9 code gives the identical `1_edit.xml` and the
    same 43: that is what `--fast` gives here, not a change of Task 10's.
  - Zendaya-age: the fix of item 2 applied once. Against the same run on the Task 9 code, the cut list and the scores
    are identical (2 of 19 captions word for word, 1 of 9 of your cuts, 3 more near), and `1_edit.xml` differs in one
    place. Before, S12's picture ran 2 frames ahead of its sound, so V1 skipped 2 frames going into S12 and repeated 2
    coming out while A1 played on. Now S08 to S13 are one clip, picture and sound together, the sequence as long as
    before.
- **check-all** on the final code: all nine test videos with video5, thorough, with the determinism re-run and the
  `--fast` comparison, from empty caches, on a free PC (7 Oct 09:12-14:24, 5 h 12 min). Every hard check passes on
  all nine; the comparison with Task 9 is under *The results* at the top. The full-resolution notes (9.9) are
  Task 9's: video3 and video4 fail them as before, and the rest pass. The run times:

  | video | Task 10 | Task 9 |
  |---|---|---|
  | deadpool | 7m02s | 7m53s |
  | spiderman-school | 56m48s | 56m26s |
  | video1 | 96m59s | 115m07s (from 15:14 next to another program holding up to 7 GB of the GPU) |
  | video2 | 19m03s | 32m40s (during your render) |
  | video3 | 17m07s | 26m45s (during your render) |
  | video4 | 61m42s | 66m15s |
  | video5 | 16m48s | (new) |
  | zendaya | 10m10s | 13m30s |
  | zendaya-age | 25m59s | 26m13s |

  I found video1's slowdown in the GPU log while comparing these, and corrected `reports/task-9.md`, which had said
  that run was on a free PC.

  video5, the new case: 1 of 35 captions word for word, with 37 % word errors. That reflects your edit, not a
  misread key. You left out 3.9 s that the competitor plays ("than you did-", "can't believe he said"). You also
  rewrote its descriptive captions: *kisses him* and *hugs him* in place of the competitor's *goes over to him* and
  *kisses his cheek*. The tool follows the competitor's captions there. Cuts: 1 of your 14 (3 more near), and the
  edit is 3.9 s longer than yours, as `learn` found.
