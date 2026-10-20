# Day 13 -- helper "names" (section 4: file names = the video's title)

## What I built

* **The name rules** (`tools/app/mr_app/core/names.py`, new):
  * the title used for names: the **YouTube title** (project.json `upload_title`), else the **headline** (its two
    lines joined by a space), else the **title from the competitor's post** (`post_title`, cleaned on day 12; "Video
    by darkhumor" counts as nothing), else the **headline read on the competitor's video** (day 12's folder names came
    from it -- kept as the last fallback);
  * Windows-safe: `\ / |` become a space (they part words: "AC/DC", "this | that"), `: * ? " < >` and control /
    invisible characters are removed, **emoji kept**, spaces tidied, no space or dot at the end, never a device name
    (`CON`, `NUL`, `COM1`, also `Con. Air` -> `Con_. Air`), at most **100 characters**, cut at a word and never
    inside an emoji or before an accent;
  * a name that is taken gets **(2), (3)** ...;
  * project.json `"names": {"rule": "title", "from": "youtube" | "headline" | "post" | "video", "at": ...}` marks a
    project whose names follow its title. Projects made before today have no `names` and keep everything as it was
    (also their old `<folder>_edit.xml` / `<folder>_edit.prproj`) until **Rename to title** is pressed.
* **New projects** get `<date>_<title>` folders: the New video form (`download_tool._create`), files dropped on
  Match cuts (`jobs.project_for_job`, when a title is typed there already) and `steps.py new`. When there is no
  title yet the old name is used, and the folder follows the title as soon as one comes (after the conversion -- the
  post / video headline -- or after the template step -- your headline), before the edit.
* **The edit** (`premiere.py`): next to `1_edit.xml` the app writes **`<title>_edit.xml`** -- the same bytes, only the
  sequence's `<name>` changed from "Recreated Edit (Premiere)" to the title (so Premiere names the render after it)
  -- at the end of every good edit (`flow.record_edit`), and again on Open in Premiere if missing. 1_edit.xml is never
  changed by this (every check / Learn finds runs through it). Open in Premiere opens `<title>_edit.xml` in
  **`<title>.prproj`** (made from assets\premiere\empty_project.prproj, as before); a project of the run folder you
  saved with the edit in it is opened again rather than a new empty one. The app's own `<title>.prproj` is never
  taken for your fix (waiting cards' 10-minute rule, the board's Fixed column, Learn's watcher and a dropped folder
  -- a title may end with "fixed").
* **Renaming later** (`tools/app/mr_app/core/renames.py`, new) -- after every title / headline change in the app
  (board card, queue row, New video row, Use as my headline, Change headline, the Template dialog) and on **Rename to
  title**:
  * renamed: this computer's folder and its copy in the shared folder (the shared copy first; a twin is only a folder
    linked by where it was published); in each run folder the XML copies the app wrote (project.json `names.copies`,
    night 10's own names -- never a copy or backup you made) and their still-empty own .prproj; 1_edit.xml keeps its
    name, only its media paths are pointed at the new folder (publish.relink_edit, as publishing does);
  * kept linked: project.json (`name`, `old_names`, `published.to`, the steps' `run_dir`; both copies hold the titles
    merged the way publishing merges them -- a title set on one copy only is kept, set on both the newer title.txt
    wins), the Learn waiting card (`learn_waiting.json`; a card the other computer wrote with its own path follows by
    the new name when its RAW / competitor names agree), `_pending_publish.json`, the paths in `problems_queue.md`
    (only paths; each entry keeps its heading and id), the queue's / New video rows' paths (`moved(old, new)` on the
    pages), the board, the Learn tab's columns (read again);
  * **waits** (the pending list in the app's folder, tried again every minute) while something uses the folder: a
    work lock (an edit, a copy to the shared folder -- also its first copy, which locks `<shared>\<name>` --, Clean
    up), a step running in project.json (started < 6 h ago),
    a row of this app (the queue's waiting / running videos, New video rows working, the window's own copies to the
    shared folder -- `renames.register_busy`),
    Premiere with a project open that is saved in it (its window title, as day 12's shutdown code reads it), or
    Windows refusing the rename (a file inside open);
  * **keeps its name** -- said in a toast, remembered in project.json (`names.kept`), no Rename button then -- when a
    Premiere project plays its files (see "decided" 3).
* **Shown as the name**: the queue row, the board card, History and the New video row show the title; the folder name
  is in the tooltip (board card: also a small dim "folder: ..." line when it is not called after the title yet).
  **Rename to title** on the card (a Mini button, fades only; also in the card's menu), only when the folder is not
  called after the title, in the background (ui.background.call).
* **Publishing** after a rename on the other computer: the old published.to is gone -> the shared copy with the same
  "created" stamp whose `old_names` has the old name is used (never a second copy under the old name); the board shows
  such a local twin once.
* **Tool fix** (separate commit, `tools/match_cuts/match_cuts/premiere_preview.py`): the framed preview wrote ffmpeg's
  concat list as `file '<path>'`; an apostrophe in the folder name ("He couldn't ...") ended the quotes -> "Error
  opening input files" -> the run recorded an error. Now quoted the ffmpeg way (`'\''`). premiere_preview is imported
  only by pipeline (no cached stage): no cache is invalidated, check-all is unchanged.

## What I decided (the user is away)

1. **The app writes `<title>_edit.xml`, not a new tool option.** The title can change after the run (the YouTube title
   is usually set later) -- the copy has to be written by the app anyway then; 1_edit.xml stays byte for byte what
   the tool wrote; no change to export_xml_edl.py (three other helpers change it today); it works for old runs too.
   Only the sequence's `<name>` text is replaced (checked to read back with an XML parser; `&` escaped), the copy
   keeps 1_edit.xml's file times (Open in Premiere compares the edit's time with the project's last save).
2. **`<title>.prproj`** (not `<title>_edit.prproj`) for new projects, as the brief says; old projects keep night 10's
   names. The app recognises its own project by `<stem>_edit.xml` next to `<stem>.prproj`.
3. **A folder that a Premiere project plays from is never renamed -- it keeps its name.** I found that your fixed
   projects are saved on the Desktop and in `D:\Shared\Copied_*` and play the RAW from the shared folder by its full
   path (cop.prproj and survive.prproj play `D:\Shared\Projects\2026-10-19_...\edit\extras\media\raw_stereo.mp4`).
   Renaming that folder would make your projects "Media offline" (and Premiere's recent list shows you keep projects in
   several more places). So the name is kept when: a .prproj saved with an edit is in the folder; the edit was opened
   in Premiere through the app (project.json "premiere" -- written by Open in Premiere); the card is Fixed / Rendered /
   Uploaded; a fix of it is in learn_queue.md; or a .prproj in Premiere's recent list, on the Desktop or next to the
   shared folder (`D:\Shared\*\*.prproj`, read only) plays a file from it. The card then still shows the title as its
   name; only the folder keeps its old name, and the app's own XML copy still follows the title.
   In practice: a title changed **before** you open the edit in Premiere renames everything; after that only the
   names shown change. A read-only preview of today's shared folder: the 5 projects you already fixed / rendered /
   uploaded would keep their names; family-guy-1999present and interesting-fact would be renamed by Rename to title
   (no saved project uses them); borat and are-we-there-yet have no title in their project.json, so they show their
   folder name and get no button.
   **What it cannot see**: a project saved on the OTHER computer (its Desktop) that plays the shared folder's files
   by path, imported there by hand (File > Import, not Open in Premiere). The other computer's app sees its own
   Desktop and recent list when the title is changed there -- so change a title on the computer where you fix the
   video. (A file of the folder open over the network makes the rename wait, as here.)
4. **The shared copy is renamed too** (not a mapping): the shared copy first, then this computer's. A copy that cannot
   be renamed now waits (pending), and the other computer finds the renamed shared copy by its "created" stamp +
   `old_names` when it publishes again.
5. **Length**: 100 characters at most, and less when the title would make a path longer than Windows' 260 characters:
   the title is twice in `<projects>\<date>_<title> (99)\edit-99\.<title>_edit.xml.<pid>.<tid>.tmp` (the temp file
   the XML copy is written and relinked through), so with the usual projects folder (`...\MovieRecaps\input\downloads`,
   54 characters) a title longer than about 65 characters is cut at a word for the folder and the files (an emoji
   counts 2, as Windows counts; a flag is never cut in half). The sequence keeps up to 100 characters (not a path).
   A longer projects folder makes the names shorter (the tests' own folders are ~175 characters: names are cut there).
   The **files' name** (`<stem>_edit.xml`, `<stem>.prproj`) also leaves room for what Premiere writes next to a project
   (I checked your Copied_* folders: `Adobe Premiere Pro Auto-Save\<name>--<id>-<date_time>.prproj`, Video / Audio
   Previews, Media Cache -- ~95 more characters), so for a usual title of 40-55 characters the stem is ~25-40
   characters (cut at a word, at least 24); the sequence -- and so the render -- keeps the whole title.
   Capitals alone never rename a folder (Windows sees "He couldn't" and "He Couldn't" as one name).
6. **`\ / |` become a space** instead of disappearing ("AC/DC" -> "AC DC", not "ACDC"); the other forbidden characters
   are removed as asked.
7. **The headline on the competitor's video stays the 4th choice** (after the post title): day 12 named folders from it
   and it is better than a link id.
8. **(2), (3)** only for this computer's folders; the shared folder keeps publish's own `-2` for a different project
   of the other computer with the same name (unchanged, night 11).
9. **The rename of an empty own .prproj**: renamed with its XML copy; a project you saved is never renamed, moved or
   removed, and its XML copy stays next to it.

## A review of my own code (an independent reviewer, read-only, with probes in a scratch folder)

It found 13 problems; 12 are fixed with tests, 1 is left:

* fixed: a rename copied one copy's titles over the other's (could wipe a YouTube title set on the other computer)
  -> merged like publishing; a rename could delete your own backup of 1_edit.xml (any XML equal to it but for the
  sequence name) -> only copies the app wrote; paths with `&amp;` / `\\?\UNC\` in your .prproj were not recognised by
  the scan; a path followed by an apostrophe ("The dog's revenge") or a space ("The Truth (2)") was rewritten in
  problems_queue.md; the New video row's own rename skipped the other pages and the scan and never retried; Premiere's
  own files next to `<title>.prproj` were not in the length budget; a first copy to the shared folder and the moment
  `published.to` is written were not waited for; a half-done rename in the minute's retry was not followed by the pages;
  a waiting rename was dropped while the shared folder could not be reached; a twin / the board's "shown once" relied
  on the "created" second alone; the other computer's waiting card could follow a different video with the same
  folder name; renames did not count as work for "Turn off the PC".
* left: a queue row reads its project.json on the window thread when it refreshes (also after a rename) -- the
  pattern the queue already had (a small local file; on the laptop the project is local too).

## Not done / check by hand

* **Premiere with the new names** (I did not open Premiere today -- the lead's GPU / timing runs): press Open in
  Premiere on a new project whose title has emoji and an apostrophe: the media must not be offline (their paths are
  percent-encoded UTF-8 in the XML, the standard way), the sequence is called after the title, and File > Export >
  Media suggests `<title>.mp4`. The quickest way: the next New video with a headline like "He couldn't stop 🍫😭"
  (its folder is named so from the start) -> when its edit is made, Open in Premiere on its board card.
* A full real run of the tool inside a folder named with spaces, an apostrophe and emoji: not done (GPU). I checked
  ffmpeg, ffprobe, OpenCV 5.0, PySceneDetect and PyAV open such paths (probe), the tool's images go through its
  Unicode-safe readers, and the After Effects script escapes non-ASCII; the one place found broken (the framed
  preview's concat list) is fixed and tested with a real ffmpeg.
* Old cards on the board now show "Rename to title" when they have a headline; pressing it on a project you already
  fixed says why it keeps its name (once) and the button goes.

## How I tested it

* `tools/app/tests/test_names_day13.py` (core, 29 tests): forbidden characters, emoji kept, 100 characters, cutting
  never inside an emoji, trailing dots / spaces, CON / NUL / COM1, the path budget (also a long projects folder), the
  choice order, (2) / (3), new projects, the title copy (1_edit.xml byte for byte, the times, `&` escaped), old
  projects keep night 10's names, the copy written at the edit's end, the own project never a fix, renaming both
  copies with everything linked (project.json, published.to, 1_edit.xml's media paths, the XML copy, the waiting
  card, the pending publish, problems_queue.md), the other computer's card, the other computer publishing into the
  renamed copy (and the board showing it once), waiting for a work lock / a row / Premiere / Windows and the retry,
  keeping the name (a Desktop project, a saved project in the folder, Open in Premiere before), the empty own project
  renamed, old projects only by Rename to title, no title, the .prproj scan, and **Learn finding the run** of a
  project made from `<title>_edit.xml` (learnkit.search_run and the tool's own learn.find_run).
* `tools/app/tests/test_names_day13_ui.py` (real widgets, 8 tests): the board card's name / tooltip / folder line,
  Rename to title (fades only, nothing moves, renames in the background, the button goes), a kept card says why, the
  queue row (title as the name, its folder in use, following a move), History rows, the New video rows (in use,
  following a move, the folder following the headline after the template step), the window (a rename that waits for
  an edit, the minute's retry doing it, every page told).
* A real tool-made 1_edit.xml (copied from the shared folder into my scratch folder): the title copy, publishing and a
  rename -- the laptop's network media URLs relinked to the new folder, every media path found, the sequence named
  after the title. A read-only preview of the real shared folder's cards (see "decided" 3).
* Two day-12 tests updated to the new names (test_newvideo_ui, test_pairs).
* The tool: `tools/match_cuts/tests/test_day13_names.py` (2: the quoting, a real ffmpeg concat in such a folder) and
  test_night10.py: 9 passed.
