# Day 12 -- helper "queue": sections 4 (RAW vs competitor, always your choice) and 5 (newest on top)

Branch `day12-queue`. Files: `tools/app/mr_app/core/jobs.py`, `core/flow.py`, `core/run.py`,
`tools/match_cuts_tool.py`, new `ui/glide_list.py`, tests `tests/test_queue_day12.py` (new, 22 tests),
`tests/test_queue_ui.py` and `tests/test_tabs.py` (adapted). The tool (`tools/match_cuts`) is not changed.

## What I built

**Section 4 -- RAW vs competitor**

* **The pair line** on every queue row: `RAW episode.mp4 (22:41) · COMP short.mp4 (0:58)` -- RAW in coral, COMP in
  the info blue, names bright, lengths dim (the mock-up's `.pair`), with a **⇄ Swap** mini button. The lengths are
  read in the background (`ui/background.call`); while they are read the line says `(…)` and the row does not start.
  (Text form: `match_cuts_tool.pair_text(job)` -> "RAW: episode.mp4 (22:41) · COMP: short.mp4 (0:58)".)
* **⇄ Swap** (button and right-click -> "Swap RAW and competitor"):
  * waiting row: the two switch, the choice is remembered (`job.roles_chosen`, project.json `roles`);
  * **running row: the run is stopped and the same row starts again at once with the files switched** (next take).
    Nothing is copied again -- the files are already in the project folder; only project.json's roles switch
    (`flow.set_roles`, which also switches what the download / convert steps say about each file);
  * finished row: it comes back as its next take with the files switched.
* **The amber check** (`jobs.needs_check`): both lengths known and **within 30 %** (the shorter at least 70 % of the
  longer), the names don't say (no `raw*` / `comp*`), and you have not chosen: the row turns amber, pill **"Check which
  is the RAW"**, the note "The two videos are almost the same length, so I need you to say which is which", and two
  buttons **"<file A> (0:47) is the RAW" / "<file B> (0:45) is the RAW"**. It never starts by itself: not with Start,
  not with the queue running, not when Download sends it with autostart (Automatic ON). Your click is remembered on
  the job and in project.json (`"roles": {"chosen": true, "raw": ..., "competitor": ..., "how": "picked"|"swapped",
  "at": ...}`); if Start / Automatic had wanted it to run, it starts right after your click.
  Night 11's separate "Which one is the RAW?" card is now this row (two videos); the old card stays only for the rare
  drop of three or more videos nobody can tell apart.
* **Your pick = Run anyway.** A chosen pair within 30 % runs with `--force-inputs` (`jobs.forced`), and every chosen
  pair with `--no-swap` (`run.command(no_swap=...)`).
  * What `--force-inputs` skips in the tool (checked in the code): night 6's input check (`inputs_check.problems`:
    competitor over 3 min, "almost as long as the RAW" (>= 90 %), competitor longer than the RAW, the two look swapped,
    the same video twice), night 11's quick WRONG PAIR check (`pair_check`), and the two "picture not found / empty
    edit" stops in `pipeline.py` (the edit is then made with that message as its first warning).
  * Found on the way: **`--force-inputs` alone lets the tool swap the files back** (`cli.resolve_inputs` swaps when the
    shorter / the portrait one is given as the RAW). So a chosen pair also gets `--no-swap` -- tested with two real
    tiny videos.
  * A pair you swapped although it is clearly far apart (a 22-minute "competitor") keeps its checks: they are right
    there, and the row says "the competitor is longer than the RAW -- swapped?" before the start.
  * The start's pre-check dialog (`batch.input_problems`) leaves out the chosen pairs.
* `flow.convert_step`'s "the longer one is the RAW" swap never undoes your choice; `flow.edit_job` and a dropped
  project folder (`jobs.project_for_job`) read your choice back from project.json.
* The batch / command-line pairing rule (`pairing.py`, 20 %) is unchanged, as allowed: the app's rule is 30 %.

**Section 5 -- newest on top (the order shown; the run order is unchanged)**

* `jobs.display_order`: running rows at the very top, then the waiting rows newest first, then the finished rows
  (the one finished last on top). Rows that change state **glide** there (0.45 s on the soft curve; Settings'
  Animations off = at once). A new row scrolls the page back to the top (gliding).
* **Run order vs display order -- my decision:** videos still run in the order added (or dragged). The waiting rows
  are shown as **the run order upside down** -- which *is* "newest first" as long as you don't drag -- and every
  waiting row says when it runs in its slate: **RUNS 1ST, RUNS 2ND ...** (a row waiting for your RAW choice says
  WAITS FOR YOUR CHOICE). I chose this over "always newest first + numbers only" because then a dragged row would
  glide back to where it was and only its number would change, which feels broken; this way a row stays exactly
  where you drop it, and its run place follows. Only waiting rows can be dragged, and only among the waiting rows
  (running and finished rows keep their places). The next video to run is the lowest waiting row (RUNS 1ST).
* **Board**: already newest first per column (by the date the card came into the column) with the pinned ★ cards
  above (in the order pinned) -- checked in `core/board.ordered` and now also on the real Board widgets (test).
  Not changed: board cards are rebuilt (cards that moved fade in), they do not glide.
* **History**: already newest first (`history.events`) -- test added.
* Not touched (other helpers): the Download tab's rows, the Learn tab's lists.

## The reusable glide helper: `mr_app/ui/glide_list.py`

```python
from mr_app.ui.glide_list import GlideList, scroll_to_top
lst = GlideList(spacing=10)          # a QWidget; put it in your page's layout (it sets its own height)
lst.insert(w, 0)                     # a new widget at a place (0 = top): it appears there, the rest glide down
lst.append(w)
lst.remove(w)                        # leaves the list (not deleted -- setParent(None)/deleteLater() is yours)
lst.set_order([w3, w1, w2])          # the same widgets in a new order: each glides to its place
lst.items(); lst.count()             # the widgets in the order shown
lst.target_y(w)                      # where a widget is going (its y when the glide ends)
lst.gliding()                        # True while something moves
lst.relayout(animate=True)           # done for you when a widget's size changes (LayoutRequest) or the width changes
lst.inserted.connect(lambda w: scroll_to_top(page))   # scroll the page's QScrollArea to the top (gliding)
```

Widgets are as wide as the list and as tall as they want (`heightForWidth`, else `sizeHint`). The glide is retargeted
smoothly when the order changes again mid-glide. A hidden list places everything at once. The queue's `QueueList`
is a `GlideList` subclass (it paints the hover glow and the drag slot), so the Download / New video rows can do the
same: keep their items' list in run order, compute the shown order, and call `set_order` after every state change.

For another helper: `jobs.needs_check(job)` tells whether a job waits for the user's RAW choice; the queue's
`add_project_job(job, autostart=True)` already holds such a job back (and starts it after the click).

## How I tested it

* `tests/test_queue_day12.py` (22): the pair line text and colours, the lengths read off the window's thread with
  the row waiting for them; Swap before the start (project.json roles and step details switched, read back by
  `flow.edit_job`); **Swap during a run** (the run stopped, the same row started again with the same two files
  switched, project.json switched, `project_for_job` never called again); the right-click item; the amber row and
  its two buttons, Start not starting it, "1 to check"; the 30 % rule and the names; Automatic ON (autostart from
  Download) not starting it, the pick remembered in project.json and then starting it; your pick ->
  `--force-inputs --no-swap` (and night 6's check really flags a 47 s / 45 s pair); `resolve_inputs` with real tiny
  videos swapping without `--no-swap` and keeping your roles with it; the pre-start check leaving a chosen pair out;
  `convert_step`'s swap respecting your choice; a dropped project folder keeping it; the display order rules and
  the run order unchanged through a whole fake run; drag landing where dropped and setting the run order; finished
  rows not draggable; a new row scrolling to the top; **a row gliding** (many positions in between, no step over
  60 % of the distance); the GlideList API; the Board's newest-first with a pin on the real widgets; History.
* Night 11's queue tests adapted: the drag test now checks the shown order and the RUNS numbers; the two
  question-card tests check the amber row (a 3-video drop still gets the card); `test_tabs`' Download -> queue
  autostart waits for the background length read.
* The whole app suite: **761 passed, 5 skipped** (3 runs; once, 3 tests of `test_convert.py` failed with "file not
  found" in their own test-file copy helper -- they pass alone and in the other runs; not related to these changes,
  probably the PC busy with the other helpers' runs).
* A picture of the real widgets (Windows platform, not shown on screen): the amber row and a normal row with the
  pair line and ⇄ Swap render like the mock-up.

## Not done / to check by hand

* Board cards do not glide when their order changes (they are rebuilt; moved cards fade in) -- the spec asked the
  gliding for the queue / Download / New video rows; the helper is there if wanted.
* Not clicked in the real running app with real videos (no GPU use allowed today). Worth one try: drop two Shorts of
  about the same length -> amber row -> click one -> it runs (with `--force-inputs --no-swap` in
  `extras\batch_console.log`'s first line); press ⇄ Swap on a running row -> it stops and starts again (TAKE 2).
* A chosen pair within 30 % also skips the quick WRONG PAIR check and the picture-not-found stops (that is what
  `--force-inputs` does; the spec says "counts as Run anyway"). A narrower tool option (skip only the length checks)
  would need a small `cli.py` change -- not done, to keep the tool untouched today.
