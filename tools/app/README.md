# MovieRecaps desktop app

One window for the whole job (night 11): **Match cuts** (the queue and Learn) · **Board** (every project from the
link to the upload) · **Progress** (Download, Convert, Template) · **History**.

The app only **runs the tools as they are**: every edit is the same command as `python -m match_cuts batch` runs for
a video (`--premiere`, `--fast` unless you choose Thorough, the video's speed; with a channel: `--frame <its template>
--watermark <its watermark>`), and its result is read with the batch's own checks. Every step of a video project is
also a command of its own (`steps.py`, below) -- the same code, the same `project.json`.

## Open it

* **PC**: double-click **MovieRecaps** on the desktop (the match-cut logo).
* **Laptop** (once, after `git pull`), in the project folder:

  ```
  .venv\Scripts\python tools\app\setup_app.py
  ```

  It installs what is missing (PySide6, yt-dlp, psd-tools, deno), puts the shortcut on the desktop and refreshes
  Windows' icon cache.
* Without the shortcut: `.venv\Scripts\pythonw tools\app\MovieRecaps.pyw`

The top strip shows this computer's graphics card, Premiere, where finished projects are saved (amber when that
folder cannot be reached), a timecode clock, **replay intro** and **settings**. Under it, the quote of this part of the
day (click it for the next one; the quotes are in `quotes.txt` in the shared projects folder -- edit it freely).

Keys: **Space** starts the queue, **Del** removes the queue row under the mouse, **Ctrl+V** pastes a YouTube link
into Download (only when you are not typing in a field).

## Settings

* **Save projects to** -- where finished projects go: the PC `D:\Shared\Projects`, the laptop
  `\\192.168.101.11\Shared\Projects` (the computer is found by itself: both are called "Gvidas", so the laptop is
  the one with a battery; "This computer" overrides it). Only the finished parts go there: the `edit` folder,
  `template.png`, `project.json`, `logs`, `title.txt` -- downloads, conversions and caches stay on this computer.
  Files are hard-linked on the same drive, else copied; the edit's XML is pointed at the copy. When the folder
  cannot be reached the project stays here (an amber note says so) and moves there by itself when it is back.
* **Animations** on / off (everything happens at once when off), **Intro** at the start, **Replay intro**.
* **Goals**: Shorts a day (6) and a month (150) for the motivation strip; how often the Shorts are checked (30 min).

Settings are this computer's own: `%APPDATA%\MovieRecaps\settings.json`.

## Match cuts -- the queue

1. **Drop videos** on the box (or use the buttons):
   * a RAW and a competitor = one video. **Names win**: a file named `raw*` is the RAW, `comp*` / `competitor*` the
     competitor; when the names don't say, the longer one is the RAW; when the two are about as long (within 20 %),
     a portrait video next to a landscape one is the competitor, else a small card asks **"Which one is the RAW?"**.
   * the two files always end up in a **project folder** (like a Download), as copies of their own (a hard link
     would change with your file if another video were later copied over its name).
   * a folder of folders = one video per folder (the batch's rule; a folder whose two videos cannot be told apart
     asks too -- on the command line, `batch` skips it with a note instead).
   * one video alone waits until you drop the other one.
2. Each row: the slate (SCENE · TAKE · date), the name and channel, a status pill, the **title** (the template's
   headline) and the **YouTube title** (the title you upload with -- Edit to type it) with Copy buttons, a progress
   bar, the step and time left, and the date and time of every step. Before the start: Fast / Thorough, 100 % /
   125 %, Template, Swap.
3. **Start** (Space). Before the long run the tool checks in seconds that the competitor really comes from this
   RAW; when it does not, the row says **WRONG PAIR** ("This competitor doesn't seem to come from this RAW") at
   once instead of 20 minutes later. If you know the two belong together, right-click the row: **Run anyway (skip
   the input checks)** -- also after night 6's stops ("the competitor is almost as long as the RAW" ...).
4. Results: **PASS** (green), **OK with a note** (amber), **FAIL**, **WRONG PAIR**, **PICTURE NOT FOUND**, **NOT IN
   THIS RAW** (red), **STOPPED**. A finished row: Open folder, Open in Premiere, Show report.

   **Open in Premiere** (the XML pointed at its media as this computer sees it): with a project open in Premiere,
   the edit is imported into it (your usual template project); with Premiere closed or on its Home screen, the
   edit gets **its own project next to it** (`<name>_edit.prproj`, made from an empty Premiere 2024 project), which
   Premiere opens and imports the edit into -- save it with Ctrl+S. Pressed again later, a saved project just opens
   (nothing is imported twice). Then select all A1 clips and apply the preset `assets\premiere\MovieRecaps audio
   chain.prfpset` (or your LoudVOGOOOD): the Hard Limiter and Amplify an XML cannot carry.
5. Drag rows to change the order. Right-click a row: Open folder, Open in Premiere, Copy title, Run again
   (Thorough), Remove.
6. A finished project's edit goes to the shared folder by itself (Settings). The taskbar button fills with the
   queue's progress; Windows says when the queue is done.

## Match cuts -- Learn

* Drop only your **fixed .prproj** (or a project folder: its `edit` is used). The app finds the run it was made from
  by itself (the media inside the project, a `project.json` next to it, the name) -- or says plainly that it can't
  and lets you pick it.
* The **check card** shows what was found: the RAW, the competitor, the tool's version, your fixed version and what
  differs (captions / cuts / position), leaving out what you do by hand.
* **Learn this one** copies the pair where the nightly LEARN step reads it (`finished\<name>\<name>_fixed.prproj`,
  `output\laptop_<name>`; the name is the next free one: video13, video14 ...) and adds it to `learn_queue.md`.
* A new `fixed.prproj` (or `*_fixed.prproj`) saved into a project folder is noticed: "Learn from this?".
* **Standing rules** (always apply) and **Wishes** (`requests.md`) are kept in the shared projects folder.
  **Copy tonight's prompt** builds a ready-to-paste Claude Code prompt from the learn queue, the rules and the wishes.

## Board

Columns **Link → Downloaded → Converted → Template → Edit made → Fixed → Rendered → Uploaded**. Cards move by
themselves as steps finish; drag a card to any column; ★ pins it to the top. Each card: the channel's dot, the name,
the titles (Copy), and the date and time of every step. Right-click a card: Open folder, Open in Premiere, Copy
title, Set the YouTube title, Pin. Above the board, a chart per channel: uploads per day this week.

The PC and the laptop use the same board (the shared projects folder) at the same time: a lock file keeps two
computers from working on one project at once (a lock without a heartbeat for 2 hours is taken over).
**Clean up** removes finished leftovers (temp files, old copies of the downloads) -- it asks first and shows the
size, and never touches an `edit` folder, `template.png`, `project.json` or `title.txt`.

**Uploaded**: at the start and every 30 minutes the three channels' Shorts tabs are read (yt-dlp, quick). A Short
with the same title (the YouTube title, else the headline; emoji, case and punctuation ignored) moves its card to
Uploaded with the date; a close one asks first ("Is this the same video?"). No internet: tried again later.

## Progress -- Download, Convert, Template

* **Download**: paste the competitor's link and the RAW's link (or drop the RAW file), pick the channel, type the
  title (and, if you like, the YouTube title), **Add to the queue**. Each video gets its project folder
  (`input\downloads\<date>_<short name>`), downloads (the best H.264 + AAC MP4), converts, reads the competitor's
  headline, and with "Then make the edit by itself" joins the Match cuts queue.
* **Convert**: drop any video; H.264 is rewrapped, VP9 / AV1 ... become visually lossless H.264, checked before the
  original goes.
* **Template**: your channel's template with your headline, live.

## History

Everything that happened -- links added, downloads, edits (PASS / notes), fixes, uploads -- with the date and time
and the channel's colour; search by title or channel.

## Each step on its own (`steps.py`)

```
.venv\Scripts\python tools\app\steps.py new COMPETITOR_LINK RAW_LINK_OR_FILE --channel LouLaughs --title "..." [--edit]
.venv\Scripts\python tools\app\steps.py download PROJECT
.venv\Scripts\python tools\app\steps.py convert PROJECT              (or: convert VIDEO ... [--keep-original])
.venv\Scripts\python tools\app\steps.py titlecheck PROJECT [--title "..."]
.venv\Scripts\python tools\app\steps.py template PROJECT [--channel X --title "..."]
.venv\Scripts\python tools\app\steps.py template --channel X --title "..." --out template.png
.venv\Scripts\python tools\app\steps.py edit PROJECT [--thorough] [--speed 125]
.venv\Scripts\python tools\app\steps.py status [PROJECT]
```

PROJECT is a project folder (or its name in `input\downloads`); `MOVIERECAPS_PROJECTS` moves this computer's
working folder.

## For a new tool

Add a class to `mr_app/tools/` (see `tools/__init__.py`) and put its key into a tab in `ui/main_window.py` (`TABS`).
The look is `mr_app/ui/theme.py`, the motion `ui/motion.py` (every hover a `Fader`), the widgets `ui/widgets.py`;
anything slow goes through `ui/background.py`.

Tests: `.venv\Scripts\python -m pytest tools\app\tests`
