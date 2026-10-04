# Task 1 — Linked audio and video

The linking itself was written by the cloud session (commit `fee6cd2`). On this PC I verified it on three real
videos and the full test suite, made the result visible in the summary and the report, and fixed what stopped
the runs and the tests from working on Windows.

## What changed

**Linked clips (from `fee6cd2`, verified here).** Every V1 clip in `1_edit.xml` is written linked to its own A1 clip,
with the `<link>` layout Premiere uses in its own XML (`linkclipref`, `mediatype`, `trackindex`, `clipindex`, and
`groupindex` on the audio). Moving, trimming or cutting a clip in Premiere therefore takes its audio with it.

- **One take under several clips:** where one take of audio runs under several V1 clips, A1 is split at those cuts
  into seamless pieces, so nothing changes in what you hear.
- **An audio cut inside a clip:** where an A1 cut falls inside a V1 clip, that clip is split at the cut the same
  way.
- **Shifted audio:** audio moved a few frames from its picture to close a jump stays linked to that picture.
- **Hard check `XML LINK`:** it re-reads the final XML. Every V1 clip with audio under it must be linked to exactly
  one A1 clip, every A1 clip under a picture to exactly one V1 clip, and both sides of each pair must point at each
  other and overlap. Otherwise the run fails.
- **Left unlinked on purpose:** a silent piece (a freeze, a muted cutaway), audio under an empty V1, and another
  video's `OTHER VIDEO` stretch. There is no clip there at all. These are allowed and listed.

**New in this session:**

1. **You can now see the result.** The end summary prints `Linked clips: 14 of 14 V1 clips linked to their own A1
   clip …` and lists every clip left unlinked on purpose. A link problem also prints as a run warning.
   `report.md` has a new section, *15. Premiere XML checks*, with one row per hard check on `1_edit.xml` (`XML
   ITEM`, `GAP`, `REPEAT`, `SPEECH`, `FLASH`, `SILENCE`, `OTHER VIDEO`, `LINK`) and its result, followed by the
   linked-clip count.
2. **A crash fix that this PC needed.** The first Deadpool run crashed in the visual search with `SystemError:
   cv2.flann.Index returned a result with an exception set` / `Insufficient memory`.
   - **Why:** After Effects was holding about 47 GB, so Windows had only 22 GB of memory it could still hand out.
     The tool sizes its worker pools by free RAM (34 GB), and 28 workers of about 1 GB each went over the limit.
   - **Fix:** worker pools are now sized by whichever is smaller, free RAM or what Windows can still hand out
     (`visual_match._available_ram`). Every worker pool is capped at about 1 GB a worker, not only the RAW-index
     search, and the memory the pool's own workers hold is counted. The run warns when it uses fewer workers.
3. **The test suite now runs on Windows.** 20 tests failed here before any change of mine. I checked this by
   running them on the untouched commit `fee6cd2` in a separate git worktree. All of it was test-side
   Linux-isms, fixed without changing what the tests check on Linux:
   - **Test media:** the tests read `input/competitor.mp4` and `input/raw_test.mp4`. Your `input/` now holds another
     video, so they would have tested the wrong clip. They now use `tests/real/spiderman-school/competitor.mp4` and
     `tests/real/deadpool/{competitor,raw}.mp4`. These are byte-identical to the files already in git history, so
     the repo doesn't grow, and your `input/` folder is no longer read by any test.
   - **OpenTimelineIO:** it has no wheel for Python 3.14, which stopped 6 test files from even loading. Its
     re-parse checks now skip when it isn't installed.
   - **Fonts:** DejaVu fonts at Linux paths are now found through matplotlib's copy of the same font, so text
     renders as on Linux. The drive-letter colon is escaped correctly for ffmpeg.
   - **ffmpeg 8.0 crash:** this Windows build crashes (access violation) on `drawtext` with an expression as
     `fontsize`. The caption tests now draw the same pop-in with three fixed sizes, which gives identical frames.
   - **Smaller fixes:** the parts that need Linux's fork or `/proc` skip on Windows. Fake `AfterFX` / `aerender`
     programs are now `.cmd` wrappers. Path separators are handled, and the hard-coded cloud paths are gone.
4. **A real fix for pop-in captions (`caption_ocr.py`).** The first, smallest frame of a pop-in caption is often
   misread, for example `bullds a tearn` for `builds a team`. It now joins its caption when the two readings match
   after the letters OCR typically mixes up are made the same (`rn`/`m`, `vv`/`w`, `i`/`l`/`1`, `0`/`o`). Before,
   that frame became a 1-frame caption of its own. The span cache version was bumped so no stale reads are reused.
5. **Tests never open your After Effects.** See *Something you should know* below.

## Test results (this PC: Windows 11, Python 3.14.3, ffmpeg 8.0)

| check | result |
|---|---|
| unit suite (`pytest -m "not slow"`) | **935 passed, 12 skipped, 0 failed** (5 min 21 s, on the committed code). The skips are platform-only: fork / `/proc` (5), POSIX shell fake (1), no `flite` in this ffmpeg (1), OpenTimelineIO (2), plan hashes recorded with Linux fonts (2), Linux-only start method (1) |
| slow suite (`pytest -m slow --runslow`, the whole tool on synthetic video) | **21 passed, 0 failed, 63 skipped**. The skips: 62 film24 checks that only run on request (`MATCH_CUTS_PROFILE=film24`), and 1 byte-for-byte check recorded with Linux's ffmpeg 6.1. Getting there needed a Windows file lock in the synthetic-video generator, `MATCH_CUTS_NO_AE`, and one stale test path (`out/cutlist.json` from before the numbered run folders) |
| Deadpool, full run (`--premiere`, fresh caches) | **14 of 14** V1 clips linked to their own A1 clip; every Premiere XML check OK; 9.7 determinism and 9.8 deliverables PASS |
| Zendaya, full run | **12 of 12** linked; the `OTHER VIDEO` stretch 00:00:04:49–00:00:10:19 left empty and listed; every Premiere XML check OK |
| Spider-Man school, full run (full-size RAW, 59.94 fps) | **16 of 17** linked; the 17th is a 1-frame silent piece, left unlinked on purpose and listed. `XML LINK` OK, but `XML SPEECH` (3) and `XML FLASH` (3) fail. These are identical on `fee6cd2`, so they predate this task; see *Not fixed here* |
| independent re-check of the three XMLs (my own parser, not the tool's) | no clip linked twice; every link points back; each `clipindex` matches the clip's position in its track |

The overall headline of every real-video run is FAIL, as on all 15 of your earlier runs in `output/`. That comes
from the strict frame-exact criteria of the After Effects recreation (c2 to c5), not from the Premiere export:
those criteria fail on every real video so far.

All three videos were run once more on exactly the committed code and gave the same results. The run folders are
in `work/runs/task1final/`.

## What to check in Premiere after importing `1_edit.xml`

1. Make sure **Linked Selection** is on: the chain-link button at the top left of the timeline.
2. Click a clip on V1. Its audio on A1 should be selected too. Drag it: the audio moves with it. Trim an edge: both
   trim. Cut through V1 with the Razor (C): A1 is cut at the same frame.
3. Right-click a V1 clip. The menu offers **Unlink**, which means it is linked.
4. **Zendaya only:** S18, S19 and S21 may show small red sync numbers (+1 or +2 frames). Their audio was moved 1–2
   frames (at 60 fps) from the picture to close a jump, as you asked, and they are still linked. Don't use "Move
   into sync" on them.
5. **Zendaya only:** at 00:00:04:49–00:00:10:19, V1 and A1 are empty with an `OTHER VIDEO` marker. That's the
   other interview, left for you to fill.
6. **Spider-Man only:** at 00:00:33:32, a 1-frame piece with no audio has no link. That's on purpose. The 8 frames
   before it are the end of a linked clip whose audio stops early (a muted cutaway).

## Decisions I made for you

- **Unlinked but allowed:** a silent V1 clip and an A1 clip with no picture over it are listed, not failed. Neither
  can be linked. In `--premiere` mode V1 is never left empty except for `OTHER VIDEO`, where A1 is empty too.
- **Test media:** the Deadpool and Spider-Man school clips now live in `tests/real/` as committed test cases (the
  same git blobs as before). The Spider-Man RAW is 195 MB, so only its competitor is committed. Its full-size RAW
  (`output/003/extras/media/raw.mp4`) is used locally.
- **Model downloads:** models downloaded during my runs are stored in `models/` inside the project, which is
  git-ignored, not in your user profile.

## Not fixed here (pre-existing, next)

- **Spider-Man school export:** three audio cuts land inside speech, and there are two holes where V1 **and** A1
  are both empty, 14 and 8 frames (00:00:17:24 and 00:00:20:13), plus a 1-frame flash at 00:00:17:23. These
  failures are identical on the commit before this task, so they don't come from the links. The holes look like
  removed pieces whose gap was never closed. I'm fixing this first in task 2, which retests "the other test clips".
- **One flaky test on Windows:** `test_probe.py::test_container_duration_of_a_longer_audio_is_not_a_truncation`
  fails about 1 time in 8 in longer sessions, and passes 10 times out of 10 on its own. It concerns a deliberately
  truncated FLV, and the truncation warning itself is right every time; only an optional detail is sometimes
  missing.

## Something you should know: the tests opened After Effects on this PC

The test suite was written on a Linux machine without After Effects. On this PC, tests that run the whole tool found
After Effects 2024 and used it. This happened twice:

1. **06:41 and 06:52 (unit tests).** Two tests (`test_end_to_end_*` in `test_cli.py`) sent their **empty stub
   script** (`(function(){ /* stub */ })();`) to the After Effects you had open. It does nothing, so nothing was built
   or changed in your project. I stopped those tests when I noticed.
   - **Your apps afterwards:** after that, your After Effects and Premiere Pro were no longer running. The Windows
     event log shows no crash for either, so they appear to have been closed normally. If you had unsaved work open,
     please check it.
2. **07:39 (slow tests).** These run the tool as a separate program, which my first fix did not reach. They
   **started a new After Effects** with a synthetic test project. It sat waiting, on "Untitled Project", for a
   project that never came. Each of these tests waited 600 s for it.
   - **How I closed it:** at about 08:45 I asked it to close, and it stayed open, presumably at a save dialog. I then
     ended that one process. It was started by the test and held nothing of yours. I checked by its parent process
     before ending it.

**The fix:** the tool now honours `MATCH_CUTS_NO_AE=1`, which makes After Effects count as not installed.
`tests/conftest.py` sets it for every test and for every tool run a test starts. A test confirms it. No test can
open your After Effects again.
