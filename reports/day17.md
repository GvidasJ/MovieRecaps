# Day 17 (24 October): one task at a time

Started 07:52 (PC; you were at it). No shutdown was set (`shutdown /a`: "no shutdown was in progress"), and none will be. C: 86.6 GB free, D: 2104.6 GB free at the start.

## The tasks

| # | task | state | how you use it |
|---|---|---|---|
| 1 | Stop C: from filling up | **done** | nothing to do: the tool's cache is on D:\mr_cache; zips are made only for projects off C: |

## Task 1 -- stop C: from filling up (done)

- **The tool's cache is on D: now.** `work\cache` is a **directory junction** to `D:\mr_cache`. The tool, the check-all copies (work\snapX), the tests and the test cases all still use `work\cache` and find the very same files, so nothing in the tool had to change (and no cache was invalidated).
  - Moved safely (robocopy /MOVE: each file removed from C: only after it was copied; then checked): **3493 files, 44.3 GB**, nothing lost (the same count and size on D:). Nothing of MovieRecaps was running.
  - The setting: `MOVIERECAPS_CACHE_DIR` (an environment variable) when you want another folder; else `D:\mr_cache` on a computer with a fixed D: drive; a computer without D: keeps `work\cache`.
  - `setup_app.py` makes the move by itself (only when MovieRecaps isn't running, never from the app's Fix now). By hand: `.venv\Scripts\python tools\app\mr_app\core\cache_move.py` (`--dry` only says what it would move).
  - The free-space check before a run now counts the cache on D: (where it really is), not on C:.
- **Zips only off C:.** The automatic project zip is made only for a project on another drive than Windows' own (`D:\Shared\Projects`, a network folder). Never in `input\downloads` on C:, also not by the 10-minute sweep, so the zips you deleted there are not made again.
  - Two automatic zips are still in `input\downloads` (made before today: `When Islam Tried An Energy Drink (2)` and `The Worst First Word of All Time`). I did not delete them (your project folders); delete them if you want the space.
- **Real test:** a real run (the video8 test case, 3 min 20 s) after the move:
  - its 37 new cache files (760 MB: the proxy, the RAW index, the sound ...) were all written to `D:\mr_cache`
  - C: free before -> after the run: 216.57 -> 216.18 GB (-0.39 GB: only the run folder itself, `work\t17`, 67 MB, and Windows)
  - C: free before -> after the move: 86.6 GB -> 216.6 GB. (The move gave 44.3 GB; the rest was freed by something else at the same time -- not by me.)
- **Tests:** 5 new (`test_disk_day17.py`): no zip on C: (make, needs_zip and the sweep), a zip on D: / a network folder; the move keeps every file and leaves a junction; no move while MovieRecaps runs; a computer without D: keeps work\cache. One older test (`test_space.py`) now fakes the cache folder (on this PC it is on D:).
