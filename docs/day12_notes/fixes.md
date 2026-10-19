# Day 12 — helper "fixes": the review's 12 findings

Every finding was checked in the code first, and all 12 turned out to be real. Each one has a small fix and a
test. Most tests are in `tools/app/tests/test_fixes_day12.py`. The rest are next to the code they test:
`test_extra_day12.py` (the shutdown guard), `test_rawfiles.py` (10), `test_queue_day12.py` (11) and the tool's
`tests/test_day12.py` (5).

## What changed

1. **Turn off the PC never runs over new work.** `ShutdownWarning` gets a `working()` callback, which is the
   page's `anything_working`. The warning asks it at three points:
   - every second of the countdown
   - every 30 s retry
   - right before the PC is turned off. `shutdown.turn_off(stop=...)` reads the warning's `stop_now` flag after
     the checks.

   If work has started again, the warning hides. It does not reject, so "Turn off the PC" stays ticked. It emits
   `stopped_for_work` and the page sets `_worked = True`. When that work is done, `_check_shutdown` shows a fresh
   60 s warning. `_check_shutdown` also closes a visible warning as soon as work starts. If `working()` itself
   fails, that counts as work.
2. **Background jobs count as work.** `background.call(..., work=True)` adds the job to a thread-safe counter. The
   counter goes down in the worker thread when the function returns or raises. `background.working()` reads it.
   These calls are flagged:
   - publishing a finished project (`project_done`) and the retry of waiting projects (`retry_pending`)
   - Clean up's delete step
   - Change headline and Set headline in the Board, the queue and the Download row

   `anything_working()` also counts a row's torrent job that is starting, downloading or stalled. A torrent you
   paused does not count.

   **The titles reader was already counted:** it runs inside the Download page's stage thread (`self.current`).
3. **Torrent Remove deletes only this job's files.**
   - `_delete_parts` deletes only this job's chosen indexes. It uses aria2c's own paths (asked before the job is
     forgotten), else the paths built from the torrent's names.
   - It never deletes a file that another known job of the same torrent and folder uses. It keeps the `.aria2`
     progress file while such a job exists.
   - `bt-remove-unselected-file` is gone from both the command line and the per-job options.

   **What stays on disk:** aria2c does not download files you did not choose. A piece that crosses a file boundary
   can still leave a small partial file of a file you did not choose next to the video. Those are never deleted
   now.

   **Still not covered:** the other computer's jobs in the same shared folder. Its jobs.json is per computer, so
   this computer cannot see them. Deleting only our own chosen files is the guard. If both computers choose the
   same file of the same torrent, they still share it.
4. **The finished video's real path, and nothing outside the save folder.**
   - When a job finishes, `aria2.getFiles` gives the real path of the chosen video. aria2c escapes ':' as `%3A`
     on Windows.
   - `T.inside(base, path)` refuses "..", drive letters and full paths.
   - `Meta.disk_path` raises TorrentError when a name leads outside the save folder, so `start` refuses such a
     torrent in plain words.
   - Paths from aria2c that are outside the folder are ignored, both for the video and in `_delete_parts`.
5. **Exit code 3 is checked.** `match_cuts.batch.finished(code, stdout, run_dir)` counts 3 only when stdout has
   "match_cuts result:" or `extras/verify.json` exists. The batch, `core/results.read_result` (`label_of` takes
   `finished=`) and `core/run.py` all use it. `stop_message` now gives the "Fatal Python error: ..." line rather
   than the thread dump under it. When code 3 comes with nothing at all, it says `batch.ABORT_WORDS` in plain
   words. `crash_words(3)` is still "" (an existing test needs that).
6. **A refused shutdown is said.** `_real_shutdown` checks the return code. When Windows refuses, it raises
   `ShutdownFailed` with Windows' own words. The warning then shows "Not turned off yet — the PC could not be
   turned off: ..." without the error class name, and tries again every 30 s.
7. **An unreadable Premiere blocks the shutdown.** There is a new check, `premiere_unreadable`. It is true when
   Premiere runs but `state()` fails (the process list still finds it), or when no main window title could be
   read. The blocker reads: "can't tell whether Premiere has unsaved work -- save it or close Premiere, then the PC
   turns off".
8. **Waiting cards match by path, or by name on the same computer.** `waiting._same_project` is true for the same
   path (relative to the hub), or for the same folder name made on the same computer.
9. **Some project folders are never renamed.** `titles.rename_folder` keeps the name when the project:
   - was published
   - has an edit (an `edit` / `edit-N` folder, or an edit step that is not "waiting")
   - has a work lock that someone holds (any holder)

   The row's note says why.
10. **Clean up checks the lock again.** `rawfiles.remove()` checks `_busy(project)` again right before it deletes.
11. **Swap during a run waits for the old run.** `_done` no longer restarts after `th.wait(2000)`. It polls
    `th.isFinished()` every 250 ms, for up to 10 min. The old thread releases the edit lock last, so the restart
    always finds it free. If another video started meanwhile, the swapped one runs after it.
12. **Tests can never turn the PC off.** An autouse fixture in conftest makes `shutdown.SHUTDOWN` raise. It also
    refuses any `shutdown ...` started through `subprocess.run` or `subprocess.Popen`, so even
    `_real_shutdown()` raises in tests.

## For the lead (merge)

These files are also touched by others today:
- `tools/app/mr_app/tools/download_tool.py` (ShutdownWarning, `_check_shutdown`, `anything_working`, the Change
  headline call)
- `ui/background.py` (`call` takes `work=False`)
- `ui/main_window.py`, `tools/board_tool.py`, `tools/match_cuts_tool.py` (`work=True` on a few calls, the Swap
  restart)
- `core/torrent.py`, `core/results.py`, `core/run.py`, `core/titles.py`, `core/waiting.py`, `core/rawfiles.py`,
  `core/shutdown.py`
- `tools/match_cuts/match_cuts/batch.py` (`finished`, `ABORT_WORDS`, the Fatal-error line). This is not a stage
  module, so no cache is invalidated.
- `tests/conftest.py` (the autouse fixture)

## Tests

App suite: 957 passed, 5 skipped (was 764+ before today's helpers; 24 new tests here). App suite: `CUDA_VISIBLE_DEVICES= QT_QPA_PLATFORM=offscreen` and its own
`--basetemp`. Tool: `tests/test_day12.py` and `tests/test_batch.py`, run from `tools/match_cuts`: 14 passed.
