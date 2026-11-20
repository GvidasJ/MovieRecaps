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
| 3 | a real failed Topaz try | waiting for the PC |
| 4 | pause and resume | not started |
| 5 | speed ("lstsq"), only if every edit is the same | not started |

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
