# Day 12 — helper "learnbox": the Learn tab's problems list, waiting cards and suggestions

Branch `day12-learnbox`. Sections 2 (app side), 8 and 9 (app side).

## What I built

**Problems list (section 2)** — `tools/app/mr_app/core/problems.py`
- `problems_queue.md` in the hub (the shared projects folder, next to `learn_queue.md` / `requests.md`).
- One entry per problem, Markdown a night run can read and change by hand:

  ```
  - [ ] **2026-10-19_video-by-mindculpt** · FAIL: XML PERSON -- ...
    - status: open
    - run: C:\...\edit                (relative .\ / ..\ when in or next to the hub)
    - copy on the PC: D:\Shared\problems\...\edit
    - date: 2026-10-19 02:44
    - computer: laptop
    - result: FAIL
    - log: ...\extras\batch_console.log
    - exit: 3
    - id: 68f8cdf40e
    - key log lines:
      > match_cuts result: FAIL
      > 02:38:43 WARNING premiere export validation: ...
  ```
  `status:` is `open`, `fixed <date>`, `not fixed — <reason>` or `skipped (my mistake) <date>`; `[x]` = fixed or
  skipped (`not fixed` stays `[ ]` and is still shown and still goes into the prompt, with "Tried before: ...").
  Unknown `- key: value` lines are kept. Helpers: `problems.fixed(hub, id)`, `not_fixed(hub, id, reason)`, `skip`.
- Written by itself: `learn_tool.after_edit(ctx, job, res)` is the ONE call in `match_cuts_tool.MatchCutsPage._done`
  (right after the result word is decided; 2 lines). It runs in the background and writes for FAIL, OK with a note,
  PICTURE NOT FOUND, crashes / stops with an exit code. NOT written: PASS, a stop by you, WRONG PAIR, NOT IN THIS RAW,
  "The inputs look wrong ..." (input checks), "raw/competitor file not found", "another computer is working on this
  project" (the work lock).
- Dedupe: the same run + check (its `id`) or the same run folder replaces the open entry in place; an entry already
  fixed or skipped is left alone (a skipped run never comes back). Writes go through `learn_hub._change`: under the
  file's lock, atomic, only when the text changes.
- Key log lines: from the run's `batch_console.log` (else `match_cuts.log`), ranked: the result line, ERROR /
  Traceback (its last line), XML checks / PICTURE NOT FOUND, the summary's FAIL rows, then warnings. Library warnings
  (`[ WARN:` OpenCV, `UserWarning` ...) are never picked; the same message at another time once; at most 10.
- Learn tab: column "Problems to fix tonight", newest first, red count, **Open log** (the log, else the same run in
  the hub after the project was published, else the PC copy; if none is here it says so) and **Skip (my mistake)**.
- **Copy tonight's prompt** now has a "Problems to fix (from problems_queue.md)" section (after the learn queue,
  before the wishes) with the spec's rules (re-run, find the cause, fix, test case, check-all, nothing worse, mark
  fixed / not fixed) and each problem's run, PC copy and key log lines; the finish asks for each problem's result.

**The two problems of today** — `tools/app/add_problem.py`
- `.venv\Scripts\python tools\app\add_problem.py day12` writes both into the app's "Save projects to" folder
  (`--hub D:\Shared\Projects` to be explicit, `--dry-run` to only print). Run it twice: nothing is added twice.
  The run folder is the laptop path the user gave, plus "copy on the PC: D:\Shared\problems\...\edit". Key lines were
  read (read only) from the PC copies. `add_problem.py add --name ... --run ... --check ... [--from-log]` adds any
  other one.
- **The lead must run it** (tests never write the real hub): `.venv\Scripts\python tools\app\add_problem.py day12`.

**Learn waits by itself (section 8)** — `tools/app/mr_app/core/waiting.py`
- `learn_waiting.json` in the hub: `{"version": 1, "items": [{id, name, project, run, run_in_project, raw,
  competitor, channel, title, result, finished, computer, state, changed}]}`; state `waiting` / `skipped` / `learned`.
  Paths relative to the hub when inside/next to it. Same lock + atomic + only-when-changed writes.
- Added by the same hook when an edit ends PASS or OK with a note. A newer run of the same project replaces its card.
- The card: project name, channel chip, "edit made 19 Oct 01:40 · will be video16" (the next free name of
  learnkit.next_free_name; several cards get consecutive numbers, the oldest the lowest), RAW / COMP names, buttons
  **Drop .prproj here** (a file dialog; the card itself also takes a dropped .prproj — no glow) and **Skip**.
- A dropped / chosen .prproj opens the check card at once with the card's run already chosen ("the run of its waiting
  card"); the user presses Learn this one. When it is learned the card goes (`waiting.learned`); a run already in
  learn_queue.md is never shown.
- A .prproj saved into the project folder or its edit folders after the edit was made is found by the one-minute
  watcher (`waiting.find_fixes`): any name ending in fixed*.prproj, or any other .prproj; the app's own
  `<name>_edit.prproj` (Open in Premiere makes it) counts only once it was saved 10 min after it was made. When the
  check card is free the fix opens there by itself (+ a toast), else the card gets a **Check it** button. The old
  "Learn from this?" offer is not shown for a project that has a waiting card.
- A project moved to the shared folder (published when the edit is made) is found there by its name.
- Rendered: when `board.card_of(project)` is in Rendered / Uploaded (or has a Rendered date), the card turns amber
  "Rendered — add your project file", and the Learn tab's badge counts these (plus the new-fix offers).

**Suggestions (section 9)** — `tools/app/mr_app/core/suggestions.py`
- `suggestions.md` in the hub. Format (the lead writes it at the end of the day):

  ```
  - **Re-frame onto the speaker by itself**
    - why: when the face is outside the window -- saves about 3 minutes per video
    - size: half a night
    - from: day 12 (2026-10-19)
  ```
  `size`: `small` / `half a night` / `a full night`. A run's ideas most useful first; later runs append at the end.
  The tab shows the newest run's ideas first (by the date in `from:`), the file's order within a run. A plain
  `- title` bullet works too. `suggestions.add(hub, [Suggestion(...)])` writes them from code (no duplicates by title).
- **I want this**: `H.add_wish(...)` (an open wish in requests.md: "title — why (size: ...) [suggested by ...]"), and
  only when that worked, the idea is removed from suggestions.md. **No**: removed. Count badge.

**Look** — `tools/app/mr_app/tools/learn_board.py`: the reference's `.lgrid/.lcol/.lc` in Qt with theme tokens: mono
10.5 px uppercase dim headings with a red count, flat PANEL cards with a LINE border fading to LINE2 under the mouse
(amber border for rendered), 13 px titles, 11.5 px dim lines, Mini buttons. Hover only fades. New cards open
(height + opacity from 0, so cards below glide down), removed cards fold the same way; a card whose key stays is
updated in place. Three columns side by side from 804 px, else stacked. All reads/writes in `ui.background.call`.

## Files touched
- new: `core/problems.py`, `core/waiting.py`, `core/suggestions.py`, `tools/learn_board.py`, `tools/app/add_problem.py`,
  tests `test_learn_day12.py` (22), `test_learn_columns_ui.py` (8).
- changed: `core/learn_hub.py` (prompt: the problems section + docstring), `tools/learn_tool.py` (the columns, the
  hook `after_edit`, watcher), `tools/match_cuts_tool.py` (**2 lines** in `_done`, after `project_done`).

## Things the lead should know
- **darkhumor exit 3**: the run's console says `match_cuts result: PASS (criterion 6 not verified: Premiere-only run
  ...)`. Exit 3 is `pipeline.EXIT_NOT_VERIFIED`. `core/results.label_of` treats every code other than 0 / 1 as a
  stop and `batch.stop_message` took stderr's last non-log line — an OpenCV `[ WARN:0@720.058] ...
  setPreferableTarget` line. So the app showed a PASS as "FAIL: the run stopped (exit 3): <OpenCV warning>".
- **mindculpt**: the tool said FAIL (XML PERSON, "the run fails"), but the app recorded "OK with a note"
  (`batch.checks_of` turns validation-only deliverable failures into notes). Both still go into problems_queue.md.
- Clean up never deletes edit folders (board.protected), so a problem's run stays re-runnable. For section 3's new
  Clean up (raw_ae / raw_stereo): `problems.open_runs(hub)` lists the open problems' run folders to keep.
- The waiting card's "will be videoN" is the next free name at display time; the check card's own auto name is
  computed again when the fix is dropped (the same number for the first card; for a later card the check card may
  show the lower free number — the name box can be changed before Learn this one).
- Not done: the batch (`python -m match_cuts batch`) does not write problems (spec: "only the app").

## How I tested
- `test_learn_day12.py`: format, written for FAIL / note / crash, never for WRONG PAIR / NOT IN RAW / input checks /
  missing file / lock / stop / PASS, dedupe, skip, fixed, not fixed with reason, a hand-written entry, key lines
  (OpenCV / UserWarning dropped, traceback's last line, limit), the prompt, the day-12 script (twice, dry run), Open
  log fallbacks; waiting: added on finish only for PASS / note, names, newest first, amber on Rendered, skip (never
  back), learned, moved project, queued run left out, write only on change, a saved .prproj found once (settle time,
  the app's own _edit.prproj ignored); suggestions: format, newest first, want -> requests.md + prompt, no, hand-written.
- `test_learn_columns_ui.py` on the real widgets: the three columns with cards and counts, amber + tab badge, Open log,
  Skip (my mistake), I want this, No, a .prproj dropped on a waiting card opens the check with its run, Skip, the hook
  writes a problem / a waiting card (WRONG PAIR not), the Match cuts queue calls the hook from `_done`, cards open /
  fold (fades, no hover movement), narrow page stacks.
- Whole app suite: 768 passed, 5 skipped (738 + 30 new). Looked at offscreen screenshots at 1040 px and 600 px.
