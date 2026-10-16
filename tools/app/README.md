# MovieRecaps desktop app

One window for the project's tools. Tonight (night 9) it has **Match cuts**; **Download** (yt-dlp) and **Convert**
(FFmpeg, no quality loss) are tabs marked "soon" -- the room for them is there.

The app only **runs the tool as it is**: every video is the same command as `python -m match_cuts batch` runs for
it (`--premiere`, `--fast` unless you choose Thorough, the video's speed), into its own folder in `output\`, and its
result is read with the batch's own checks.

## Open it

* **PC**: double-click **MovieRecaps** on the desktop (red icon with a white M).
* **Laptop** (once, after `git pull`), in the project folder:

  ```
  .venv\Scripts\python tools\app\setup_app.py
  ```

  It installs the window toolkit (PySide6, about 100 MB, once) and puts the shortcut on the desktop.
* Without the shortcut: `.venv\Scripts\pythonw tools\app\MovieRecaps.pyw`

## Match cuts

1. **Drop videos** on the big box (or use the buttons):
   * a RAW and a competitor together = one video. The RAW is the file named `raw.*`, else the longest video. If the
     app guessed wrong, press **Swap** on the row.
   * a folder of folders (like `input\videos` with `video1`, `video2` ... inside) = one video per folder (a folder
     with several competitors: one per competitor) -- the batch's own rule.
   * one video alone waits (an orange line) until you drop the other one.
   * drop more pairs at any time, also while the queue runs: they join the queue.
2. On each row choose **Fast** (the default) or **Thorough**, and the speed (**100 %** / **125 %**: click it; a
   `speed.txt` in the video's folder sets it too).
3. **Start** runs the queue one video after another; **Stop** stops the running video (the others stay in the
   queue). Before it starts, the app warns when something looks wrong (only one video, the competitor longer than
   the RAW, and the tool's own wrong-input check).
4. Each row shows the step the tool is in and the time left (read from the tool's own progress lines), then the
   result:

   | result | means |
   |---|---|
   | **PASS** | every check passed |
   | **OK with a note** | every file is there, a check of `1_edit.xml` found something to look at in Premiere |
   | **FAIL** | a check failed (Show report says which) |
   | **PICTURE NOT FOUND** | the competitor's sound is in the RAW, its picture is not (another version of the RAW?) |
   | **NOT IN THIS RAW** | neither its picture nor its sound: another episode? |
   | **STOPPED** | you pressed Stop |

   with **Open folder**, **Open in Premiere** (opens `1_edit.xml` in Premiere Pro) and **Show report** (why it
   failed, what to check by hand, doubtful captions; buttons for `report.md` and the run's log).
5. When the queue is done, Windows shows a notification.

## Learn from your fix

Drop your fixed Premiere project (`.prproj`) and the tool's output folder it was made from (`output\video4` ...) on
the second box. The app copies them to the names the nightly LEARN item reads -- `finished\<name>\<name>_fixed.prproj`
and `output\laptop_<name>` (the name comes from the project: `video11_fixed.prproj` -> `video11`; you can change it)
-- and shows the **LEARN line** for the next night's list. **Copy the LEARN line** puts it on the clipboard. Several
fixes in one session go into one line.

## For a new tool (later)

Add a class to `mr_app/tools/` (see `tools/__init__.py`: `key`, `title`, `ready`, `page(ctx)`) and list it in
`tools()`. The theme (`mr_app/ui/theme.py`) and the widgets (`mr_app/ui/widgets.py`: cards, tags, drop zones,
toggles) are shared.

Tests: `.venv\Scripts\python -m pytest tools\app\tests`
