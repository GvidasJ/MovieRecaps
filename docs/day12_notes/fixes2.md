# Day 12 — helper "fixes2": the second review's 6 findings

All 6 were checked in the code and were real. Each has a small fix, a test and its own commit.

1. **A torrent that fails to start kept "Turn off the PC" from ever firing.** `TorrentJob.begin`'s `bad()` now sets
   `job.status = {"state": "failed", "error": ..., "name": ...}` before it tells. `_torrent_active` reads "failed"
   (not work), and the card shows FAILED. Test: `test_torrent.py::test_a_start_that_fails_is_not_counted_as_work_for_ever`.
2. **The New video form's RAW field no longer adds `--force-inputs`.** `Job.roles_how` ("form" when the choice is
   the form's RAW field) comes from project.json's `roles.how` (in `flow.edit_job`, and in `project_for_job` when
   the roles were chosen before).
   - `jobs.forced()` is False for "form", so the quick WRONG PAIR check runs.
   - `needs_check` (no amber wait) and `--no-swap` stay as they were.
   - A pick on the row (`choose_raw`) clears `roles_how`, so it is your pick again (forced, as before).
   - Night 6's input check stops a pair when the competitor is at least 90 % of the RAW's length. A 26 s / 30 s pair
     passes, but 28 s / 30 s would stop. So there is a new tool option, **`--allow-short-raw`** (in `cli.py` and
     `inputs_check.problems(..., allow_short_raw)`). It skips only "almost as long as the RAW". The app passes it
     for a "form" pair within 30 % (`jobs.short_raw`). The app's pre-start check (`batch.input_problems(pairs,
     allow_short_raw=names)`) leaves out the same line.
   - The option is not in Config, so no stage changes. `common.stage_code_hash` was checked before and after for
     every stage: identical.

   Tests: `test_queue_day12.py::test_the_forms_raw_field_never_skips_the_wrong_pair_check`, the tool's
   `test_day12.py::test_allow_short_raw_skips_only_the_almost_as_long_check`, and `test_tabs.py` (checks `roles_how`).
3. **The Swap restart race.** `_next()` with nothing runnable while a swapped video waits for its old thread
   (`swap_waiting()`) now updates the counts and returns. It no longer calls `_finished`. `busy()` counts that state.
   Test: `test_queue_day12.py::test_the_queue_is_not_done_while_a_swapped_video_waits_to_start_again`.
4. **The `retry_pending` call every 5 min is no longer flagged as work.** A new `background.counted_work()` context
   manager counts only the publishing parts (`publish.retry_pending`, `publish_or_queue`) as work. The reachability
   probe is not counted. Tests: `test_window_publish.py::test_the_5_minute_retry_counts_as_work_only_while_it_publishes`;
   the source check in `test_fixes_day12.py` was updated.
5. **`batch.stop_message`: the real stop message wins over a "Fatal Python error" line.** The fatal line and its
   thread dump (`_without_fatal_dump`) are taken out, then the normal search runs. The fatal line is used only
   when nothing else is found, and never with "its last error" in front. Test:
   `test_day12.py::test_a_fatal_error_line_never_hides_the_real_stop_message`.
6. **Titles.**
   - `tidy_headline(answer, model=False)`: only the vision model's answer is destuttered. That is
     `read_in_this_process` and `read_on_video`, with `model=True`.
   - `settle` with OCR text and no model answer is `tidy_headline("\n".join(ocr_lines))` again, as before day 12's
     e2e change. This works for any number of lines: the first line, then the rest.

   Test: `test_newvideo.py::test_only_the_models_answer_is_destuttered`. The e2e test now expects the 4-line OCR to
   keep its break.

Files touched: `ui/torrent_panel.py`, `core/jobs.py`, `core/flow.py`, `core/run.py`, `tools/match_cuts_tool.py`,
`ui/background.py`, `ui/main_window.py`, `core/titles.py`, `tools/app/README.md`, the tool's `cli.py`,
`inputs_check.py` and `batch.py`.

Tests:
- App suite: 969 passed, 5 skipped (CUDA hidden, offscreen, own `--basetemp`, this worktree's mr_app).
- Tool: `tests/test_day12.py` + `tests/test_batch.py`: 16 passed.
