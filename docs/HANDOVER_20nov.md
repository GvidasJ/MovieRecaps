# Report -- 20 Nov 2026, the session on the PC (Claude Code)

For Gvidas. The short part first, the details after it. This file is written while I work: each job is added when
it is finished (or when I stop working on it).

## What you must do

1. **PC:** nothing. Open the app as always. It uses the new code from its next start.
2. **Laptop:** close the app, open a terminal in the MovieRecaps folder, type `git pull`, open the app again.
3. Nothing waits for you. The question of 19 Nov (3+ frame flashes) is still open, see the 19 Nov report.

## The jobs

| # | job | state |
|---|---|---|
| 1 | finish 19 Nov: the old videos, check-all, the 19 Nov report | done |
| 2 | tests: the crash at the end, the timing test, the real crash.log | done |
| 3 | a real failed Topaz try | done |
| 4 | pause and resume | done, proven with a real edit and the real Topaz |
| 5 | speed ("lstsq"), only if every edit is the same | done: every edit the same, so it is kept |

## Job 1 -- finishing 19 Nov

The 19 Nov session was cut off at 14:08 in the middle of the proof on the old videos. Today:

- The 4 old videos that were not done (video018-fixed, video4, video1, video5), each three times (the 60 fps fixes
  off / as on 18 Nov / as on 19 Nov): **12 of 12 PASS, no note, no problem.** With the 7 of 19 Nov: **33 of 33**.
- The edits of the code as it is now against those of the morning of 19 Nov: **byte for byte the same** on every
  old video (the last two were checked today).
- **check-all** on the 6 old videos it had not reached: all hard checks passed. **11 of 11** with those of 19 Nov.
- The three TODO places of `docs\HANDOVER_19nov.md` are filled (the old videos, the newer test videos, the space).
- The two run folders the cut-off left half written were moved to `D:\mr_scratch\h19\ab60\_cut_off_19nov`.

## Job 2 -- the tests

What was wrong, in simple words:

1. **The app's tests died with "0xC0000409".** It was not at the end and not in the app: one test file made a
   "half" Qt program object (a QCoreApplication: Qt without windows). The next test files asked "is there a Qt
   program already?", got that half one back, and their first window made Qt stop the whole test run at once.
   It depended on the order of the files, so the test that died passed alone. Now the tests make ONE full Qt
   program at the very start and keep it to the end. The group of 16 test files that died every time: **238
   passed**.
2. **A second, rarer crash** (an "access violation" in PySide6, the bridge between Python and Qt -- Windows' own
   crash list showed it twice today, at the same place both times: once in the middle of the tests, once while
   the test program closed). Windows of earlier tests were left to Python's "garbage collector", which can start
   in any thread at any moment and then destroys a Qt window in the wrong thread. Now, while a test runs, that
   collector waits; it runs after each test, in the main thread. The windows left at the end are closed before
   Python closes.
3. **The fade test failed when the PC was busy.** It said "120 ms after the start the fade must be part-way" and
   measured with the wall clock; on a full PC the first step of the fade came late. Now the motion tests look at
   the fade's own clock (exactly where it is 120 ms after its start), so they check the same rules (fades in in
   0.3 s, out in 0.48 s, never jumps) and do not depend on how busy the PC is.
4. **The tests wrote into your real crash.log.** The app's start wrote "the app started / closed normally" into
   `%APPDATA%\MovieRecaps\crash.log` itself, not into the app folder the tests give it. Now it uses the same
   folder as the settings. The two "find_box" errors in your crash.log (19 Nov 13:50) were from a test window
   too; the search box no longer gives that error while its window closes.

How it was tested: with the final code the whole app suite ran **twice, no crash: 1498 passed** each time, exit
0, while check-all and other edits were using the PC (the hard case); the tool suite **1688 passed**. Your real
crash.log: the same date and size before and after them. Before the fixes, 3 of the 5 full runs I made today
crashed (twice at the end, once in the middle). Two clean runs prove less than "never again": if it ever comes
back, Windows' crash list (Event Viewer -> Application, "python.exe") says where.

One test (`test_torrent_day17`, a real torrent download into a network folder) fails when the test's temporary
folder has a very long path -- correct behaviour of the app (it shortens a name that would pass Windows' path
limit). It happened only because I gave the tests a long temporary folder; with a short one it passes.

## Job 3 -- the real Topaz failing, and the next try made another way

Until now the retry ladder (19 Nov) was only tested with a fake Topaz. Today, on a scratch copy of The Worst Prank
In The Office (the competitor + the RAW copied; nothing of yours touched; the app closed), the edit was made as the
app makes it (PASS, no note) and then every piece the app sent to the real Topaz was damaged before Topaz took it:

| try | how the app made the piece | what I did to it | what happened |
|---|---|---|---|
| 1 | the RAW's own frames, whole | cut to its first 100 KB | the **real Topaz failed**: "Invalid data ... Conversion failed!" |
| 2 | an encoded piece, whole | only the first half of its frames | Topaz worked (5 min), the **app refused the result**: "13.4 s long, the piece 26.85 s" |
| 3 | an encoded piece, every frame a keyframe | nothing | Topaz worked (10 min), accepted: **"25 of 25 V1 clips play it"** |

- Each try was made another way, as decided on 19 Nov. The third skipped "the RAW's own frames, the whole piece",
  because try 1 had already been made exactly that way.
- Both failures stayed in the project with their full reason: project.json "tries" and `logs\topaz_failed.log`
  (with Topaz's own last lines for try 1).
- Then every clip of the finished edit was checked at its first, middle and last frame against the normal RAW
  (`work\h19\check_clips.py`): 74 of 75 right. The one left is the middle of the last clip, a still picture (0.9993
  against 0.9994), 1/60 s early on purpose: the Topaz file is one frame shorter than this RAW (19 Nov, point 6).
  That is exactly the result of yesterday's run of this video that never failed.
- One small thing: the failure text says "(the laptop Gvidas, ...)" also when the PC did the work. Both your
  computers are named Gvidas; the words come from the time only the laptop ran Topaz. Not changed (only words).

## Job 4 -- pause and resume

### An edit

Until today Pause stopped the tool, and Resume ran it again: the steps already finished came back from the cache,
but the step that was running -- usually the search or the refine, 80 % of an edit -- started again from zero.
And after the PC was turned off (no pause at all) the row said "Try again" and that started from zero too.

Now the search and the refine keep their finished work on disk as they go (every few seconds, in the cache
folder, `cache\partial`). When the step runs again -- Resume, Try again after the PC was off, any new run of the
same video with the same settings -- only the work that is missing is done. When the step is finished, its kept
work is deleted (its result is in the cache as before). The row of a video stopped by a closed app or PC now says
"Try again goes on from where it stopped".

**The proof** (a copy of a real test video, video017-fixed, everything from empty caches, through the app's own
Pause / Resume code):
- once without any stop: 352 s, PASS;
- once paused at 32 % of the search -> Resume -> killed hard at 80 % of the refine (as when the PC is turned
  off: no pause, no note) -> Try again: PASS. The last part took 151 s instead of starting over: the refine took
  34 s instead of 132 s, because almost all its work came from what was kept.
- The two edits: `1_edit.xml`, `2_captions.srt`, the 60 fps XML -- **byte for byte the same**; the cut list the
  same (only the times of the steps it writes down differ).

Why it is the same: every small task of these steps gives the same answer whatever runs with it, before or after
it (the tool was built that way: the check-all "determinism" check proves it on every test video), so a task
done before the pause and kept is exactly the task done again. A task cut off by the kill is simply done again.

### A Topaz piece

Until today a stopped Topaz (the app closed, the PC turned off, Topaz hanging) started again from frame 0. Its
half-made file could not even be read: an mp4 can only be read when it is finished ("moov atom not found" -- the
two half-made files of 17 Nov in `D:\Shared\Enhance` are like that, 150 MB and 114 MB of Topaz work lost).

Now:
- Topaz writes its file in small self-contained blocks, so a stopped file can be read up to its last block. When
  Topaz is done, the file is written as a normal mp4 (copied, not encoded again). The sound is put in at that
  moment (with the sound inside a block file every picture was 21 ms late -- found and avoided).
- Every 1001 frames (16.7 s at 60 fps; 600 frames for 24, 25, 30 or 60 fps RAWs) Topaz is asked for a keyframe the
  file can be cut at. There a 60 fps frame falls exactly on a frame of the RAW, so what comes after the cut has the
  same frames at the same places. (The real Topaz stores frames out of order -- "B-frames": a cut anywhere else
  could lose a frame. My first real test found this; the fake Topaz of the tests has none.)
- The next try keeps the frames up to the last such keyframe and Topaz goes on from there; the two files are joined
  without encoding again (as the played parts are since 18 Nov). A finished part is not made again.
- Something wrong (another piece, other settings, a file that cannot be read, a failure that is not a stop): nothing
  is kept, Topaz starts from 0 as before. `MOVIERECAPS_TOPAZ_RESUME=0` switches it all off.

**The proof with the real Topaz** (35 s of It's Always Sunny, 1080p 23.976 fps -> 4K 60 fps, 2100 frames; a scratch
copy, the app closed):

| | frames | the very same pictures | difference (gray, of 255) | each frame the same RAW frame |
|---|---|---|---|---|
| no stop, the new way -- against the way until today | 2100 / 2100 | the first 1001 | mean 0.13, max 0.34 | 2100 of 2100 |
| stopped at 75 % and started again -- against no stop | 2102 / 2100 | the first 1001 (all before the stop) | mean 0.14, max 3.4 | 2092 of 2100 |

- The stopped run kept 1001 frames: the second try took **303 s instead of 580 s**.
- The keyframes change the picture by 0.13 of 255 on average: not visible, the same frames. Your Proteus / Apollo
  settings are not changed.
- After the stop Topaz starts fresh, so its pictures differ by about the same tiny amount. The 8 frames counted as
  "another RAW frame" are all pictures Apollo makes halfway between two RAW frames (it blends the two): there the
  blend leans a hair towards the other one (the two fit 8.3 against 9.1). No frame shows a wrong moment.
- 2 frames more at the end: the last picture held twice (nothing plays it; the check accepts it).
- On the way, two small faults in the Topaz code were found and fixed: a Topaz that works was stopped as "not
  answering" after 8 minutes when nothing showed its progress, and the next try failed with a Windows "file in use"
  error when the stopped Topaz still held its file for a moment.

## Job 5 -- the faster sum in the framing fit

The framing fit (inside the search and the refine) solves two small "least squares" sums many thousand times per
edit. numpy's way (lstsq) is careful and slow; the "normal equations" give the same answer about 5 times faster for
these sums, but differ in the last digits. You said: keep it only if every edit comes out exactly the same.

- All 11 old test videos were made twice, each from the same caches up to the search, with only this sum changed
  (the search and the refine really ran again for the new way, they were not taken from a cache): **11 of 11
  edits byte for byte the same** -- 1_edit.xml, 2_captions.srt and the 60 fps XML. So it is kept.
- In a test of the fit itself the framing moves by at most 0.0000000004 of a pixel.
- If a flat picture makes the faster sum impossible, the fit falls back to the old way for that step.
- How much faster a whole edit is: The Best Business Idea (2), from empty caches, one edit at a time, the PC
  otherwise free:

  | | the whole edit | the search | the refine |
  |---|---|---|---|
  | the old sum (lstsq) | 2010 s | 667 s | 933 s |
  | the new sum | **1820 s** | 521 s | 891 s |

  About **3 minutes (9 %) less** per edit of this size. The two edits of this timing are also the same (the XMLs
  differ only in the name of their run folder). The old sum's 2010 s is about what this edit took on 19 Nov (1999
  s), so what the pause-and-resume of job 4 writes to disk as it goes costs no visible time.

## What I decided for you (you said: do not ask)

1. Job 2: the tests make one Qt program for the whole run, and Python's clean-up runs between tests. The app
   itself is not changed by this (only its crash log's folder and the search box's Esc key).
2. Job 3: the video for the real failures was The Worst Prank In The Office (short: three real Topaz tries in
   20 minutes). The damage: a broken file, then half a file.
3. Job 4, edit: the search and the refine keep their work (80 % of an edit). The other steps are short and were
   already kept whole by the cache.
4. Job 4, Topaz: a keyframe every 1001 / 600 frames in every Topaz file from now on, also when nothing stops. It
   changes the picture by an invisible amount (0.13 of 255) and keeps the same frames; without it a stopped real
   Topaz file cannot be cut safely.
5. Job 4: a row stopped by a closed app or PC still says "Try again" (an earlier decision of yours), now with the
   words "goes on from where it stopped" -- that is what it does now.
6. Job 5: "exactly the same" = the edit files byte for byte (not only the frames). That was met.
7. Jobs 1, 4 and 5: "the same" always means the new code's edit against the old code's edit of the same copies
   (or a run without a pause) -- never against one of your projects. It proves "nothing changed", not "right".

## The space (nothing big deleted -- you decide)

Free now: C: 115 GB, D: 1384 GB. Everything below is mine (copies and test files), none of it is your work.

| folder | size | what it is | can go? |
|---|---|---|---|
| `D:\mr_scratch\h20\pt_*` (27 folders) | about 50 GB | the tests' temporary folders of today's test runs | yes, all |
| `D:\mr_scratch\h20\time_A20`, `time_B20` | 3.6 GB each | the two timed edits of job 5 | yes |
| `D:\mr_scratch\h20\pause` | 5.5 GB | job 4's pause proof (video017-fixed copy, two edits) | yes |
| `D:\mr_scratch\h20\dev` | 5.5 GB | my second copy of the code for testing (a "git worktree") | yes, but with `git worktree remove D:/mr_scratch/h20/dev --force` typed in the MovieRecaps folder, not by deleting the folder |
| `D:\mr_scratch\h20\topaz_resume` | 0.8 GB | job 4's real Topaz pieces (A, N, C) | yes |
| `D:\mr_scratch\h20\e2e_prankF` | 0.25 GB | job 3 (the real failed Topaz) | yes |
| `D:\mr_scratch\h19` | 71 GB | the copies of 19 Nov and of job 1 today (`src` = the RAW and competitor copies the scripts use) | yes when no more old-video proof is needed; `src` is worth keeping until then |
| `work\snap20_A`, `snap20_B`, `snap20_solve` | 16 MB each | code copies of job 5 | small, can stay |

The two half-made 17 Nov files in `D:\Shared\Enhance` (`*_ENHANCING.part.mp4`) are still there, untouched: the
laptop's app owns that folder. They cannot be resumed (they were made before today's change) -- yours to delete.

## What I did NOT do

- The 21 newer test videos with "fixed" in their name were not made again today (only the 11 old ones). The 19
  Nov report says what they are; check-all on them is the next big proof if you want one.
- The laptop was not tested. It gets today's code with `git pull` (see the top).
- The words "(the laptop Gvidas, ...)" in the Topaz failure text are not changed (job 3).
- The 3+ frame flash question of 19 Nov is still yours to answer.
- Only the edit and Topaz go on from where they stopped (what you asked for); the other steps were not changed.

## For the next Claude session

- Scripts of today: `work\h20` (`STATE.md` says what each one is). `suites.ps1 -Tag NAME` runs both test suites
  with a SHORT temporary folder (`D:\mr_scratch\h20\pt_NAME`): a long one makes `test_torrent_day17` fail.
- Switches: `MOVIERECAPS_TOPAZ_RESUME=0` (Topaz starts from 0 as before), `MATCH_CUTS_CHECKPOINTS=0` (no kept
  work in the search and the refine).
- Editing `refine.py`, `visual_match.py` or `pipeline.py` makes every cached search and refine invalid (the cache
  keys include their code): hours of work again on big videos.
- The real Topaz's H.265 has B-frames (frames stored out of order); the fake Topaz of the tests has none. Anything
  that cuts a Topaz file must be tested once with the real Topaz.
- If the app's test suite crashes again: Windows' event log (Application, Id 1000, python.exe) names the DLL.
