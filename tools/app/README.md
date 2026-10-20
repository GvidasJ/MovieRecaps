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

  It installs what is missing (PySide6, yt-dlp, psd-tools, deno, PySceneDetect, aria2c for torrents), puts the shortcut on the desktop and refreshes
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

* **Save torrents to** -- where torrents are downloaded: the PC `D:\Shared\Downloads\Torrents`, the laptop
  `\\192.168.101.11\Shared\Downloads\Torrents` (or choose a folder).
* **Keep sharing after a download** -- off by default: a torrent stops sharing the moment it is finished. On: it keeps
  sharing until it has given back as much as it took (or until the app closes).

Settings are this computer's own: `%APPDATA%\MovieRecaps\settings.json`.

## Match cuts -- the queue

1. **Drop videos** on the box (or use the buttons):
   * a RAW and a competitor = one video. **Names win**: a file named `raw*` is the RAW, `comp*` / `competitor*` the
     competitor; when the names don't say, the longer one is the RAW; when the two are about as long (within 30 %),
     the row asks (see below).
   * the two files always end up in a **project folder** (like a Download), as copies of their own (a hard link
     would change with your file if another video were later copied over its name).
   * a folder of folders = one video per folder (the batch's rule; a folder whose two videos cannot be told apart
     asks too -- on the command line, `batch` skips it with a note instead).
   * one video alone waits until you drop the other one.

   **Which one is the RAW is always your choice.** Every row has a **pair line**: `RAW name (22:41) · COMP name
   (0:58)`, with a **Swap** button (also before the start and while it runs: a running row stops and starts again
   with the two files switched; nothing is copied again). When the two videos are within 30 % of each other in length
   and their names don't say, the row turns amber, **Check which is the RAW**, and does not start (not with Start, not
   with Automatic) until you click one of its two buttons. Your pick runs with the input checks skipped. A video made
   with the **New video** form needs no click: what you put in its RAW field is your choice (only the check "almost
   as long as the RAW" is skipped for it -- a wrong link in the form still stops as WRONG PAIR).
   **Newest on top:** the running row first, then the waiting rows (newest first), then the finished rows (the one
   finished last on top); rows glide to their place. The run order does not change: a waiting row says **RUNS 1ST,
   RUNS 2ND ...** (drag a waiting row to change it).
2. Each row: the slate (SCENE · TAKE · date), the name and channel, a status pill, the **title** (the template's
   headline) and the **YouTube title** (the title you upload with -- Edit to type it) with Copy buttons, a progress
   bar, the step and time left, and the date and time of every step. Before the start: Fast / Thorough, 100 % /
   125 %, Template, Swap.
3. **Start** (Space). Before the long run the tool checks in seconds that the competitor really comes from this
   RAW; when it does not, the row says **WRONG PAIR** ("This competitor doesn't seem to come from this RAW") at
   once instead of 20 minutes later. If you know the two belong together, right-click the row: **Run anyway (skip
   the input checks)** -- also after night 6's stops ("the competitor is almost as long as the RAW" ...).
4. Results (a run that ends with exit code 3 -- nothing failed, one check could not be run -- is shown as the finished
   PASS it is): **PASS** (green), **OK with a note** (amber), **FAIL**, **WRONG PAIR**, **PICTURE NOT FOUND**, **NOT IN
   THIS RAW** (red), **STOPPED**. A finished row: Open folder, Open in Premiere, Show report.

   **Open in Premiere** (the XML pointed at its media as this computer sees it): with a project open in Premiere,
   the edit is imported into it (your usual template project); with Premiere closed or on its Home screen, the
   edit gets **its own project next to it** (`<name>_edit.prproj`, made from an empty Premiere 2024 project), which
   Premiere opens and imports the edit into -- save it with Ctrl+S. Pressed again later, a saved project just opens
   (nothing is imported twice). Then select all A1 clips and apply the preset `assets\premiere\MovieRecaps audio
   chain.prfpset` (Hard Limiter + Amplify +3.0 dB since day 13 -- not your older LoudVOGOOOD / GoodAUDIO, they have
   +4.5 dB): the Hard Limiter and Amplify an XML cannot carry.
5. Drag rows to change the order. Right-click a row: Open folder, Open in Premiere, Copy title, Run again
   (Thorough), Remove.
6. A finished project's edit goes to the shared folder by itself (Settings). The taskbar button fills with the
   queue's progress; Windows says when the queue is done.

## Match cuts -- Learn

The tab has three columns (stacked when the window is narrow):

* **Waiting for your fix** -- a card for every finished edit (PASS or OK with a note): "edit made 19 Oct 01:40 · will
  be video16". **Drop .prproj here** (or drop the file on the card) opens the check card with that run already chosen;
  **Skip** removes the card for good. A fixed project saved into the project folder is found by itself and the card
  gets **Check it**. Once the video is Rendered the card turns amber, **Rendered -- add your project file**. The
  cards live in `learn_waiting.json` in the shared folder.
* **Problems to fix** -- every run that ended FAIL, OK with a note, PICTURE NOT FOUND or crashed is written to
  `problems_queue.md` in the shared folder (a night run reads and changes it; **Copy tonight's prompt** includes it).
  **Open log** shows the run's log; **Skip (my mistake)** removes it. Input stops (WRONG PAIR ...) are not problems.
* **Suggestions** -- ideas for the tool from `suggestions.md` in the shared folder, each with why and how big.
  **I want this** adds it to your wishes (`requests.md`), **No** removes it.

The rest of the tab is as below.

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
size, and never touches an `edit` folder, `template.png`, `project.json` or `title.txt`. It also finds **older 5.1
runs that hold the RAW twice** (`raw_stereo` and `raw` / `raw_ae`): it checks that `raw_stereo` is the same video and
that no edit file or Premiere project plays the old copy (it only reads your projects), shows each run and the space
saved, and removes the old copy only when you say Yes. A run that still plays the old copy keeps both and says why.

**Uploaded**: at the start and every 30 minutes the three channels' Shorts tabs are read (yt-dlp, quick). A Short
with the same title (the YouTube title, else the headline; emoji, case and punctuation ignored) moves its card to
Uploaded with the date; a close one asks first ("Is this the same video?"). No internet: tried again later.

## Progress -- Download (the New video form), Convert, Template

**Download** is the form for a new video:

* **RAW** -- paste a YouTube link, a magnet, a `.torrent` file or a video file (or drop it). A small label says what
  it was taken for: YOUTUBE, TORRENT, FILE or LINK (Instagram, TikTok ...). **Competitor** (optional) the same.
* **Channel** (the buttons with their dots; the last one is remembered), the **headline** (two lines: Enter starts
  line 2) and the **YouTube title** (optional), Fast / Thorough, 100 % / 125 %.
* **Will make** says what you will get: **Full Match cuts edit** (RAW and competitor), **Just cuts** (a RAW alone:
  the whole video cut at every scene change, framed on its face, with the audio chain, frame and watermark, no
  captions), or **Needs a competitor** (a torrent alone: add the competitor first).
* **Many at once**: one video per line, `RAW link | competitor link` (a line without a competitor makes Just cuts).
  It says what it added, and which line is wrong.

Each video is a row with a bar per step (Download, Convert, Template, Edit), the real titles, and a line that says
what it waits for. **Steps wait for your buttons** (Convert, Send to Match cuts ...) unless **Automatic** (top of
the page, off at first, remembered) is ON; then everything goes on by itself. Newest rows on top, like the queue.

* **No headline**: with Automatic ON the channel's template is used as it is (the row says **default template**);
  **Change headline** then makes only the template again and puts it into the finished edit (no new run). With
  Automatic OFF the Template step says **needs a headline** and waits for you to type one.
* **Errors in plain words**: a red pill and the step in red. **Try again** goes on from that step; what is done is
  kept (nothing is downloaded again). A video that fails at the very first read of its link is still in History.
* **Just cuts** rows can get **Add a competitor**: only the competitor is downloaded and the full edit is made.
* **Turn off the PC when everything is done** (tick box, not remembered): when nothing is running or going to run by
  itself, a 60 s warning (Cancel), then the PC turns off -- never while Premiere has unsaved work or a render runs; it
  tries again every 30 s. A project still being copied to the shared folder, Clean up, Change headline and a running
  torrent count as work too, and work that starts during the warning stops it (a fresh warning comes when it is done).

**Torrents.** A magnet or `.torrent` opens a list of the files (the biggest video ticked, seeders, free space), then
a progress card in the row. The engine is **aria2c** (libtorrent has no build for Python 3.14); `setup_app.py`
installs it. Files go to the **Save torrents to** folder (PC / laptop, Settings) and it stops sharing when finished
unless **Keep sharing** is ON. "No one is sharing" pauses it (Resume tries again); **Try again** on a failed row
continues the same download. If the app is closed, it continues when you open it again.

**Real titles** on the rows (also on queue rows and board cards): **Title from the post** (the competitor's post,
cleaned) and **Headline on the video** (read from its first seconds), each with **Copy**; the headline also has
**Use as my headline** (before the edit it is just set; after it, it asks and then does Change headline) and **Use as
YouTube title**. The project folder is named from them. They are saved in `project.json` and `titles_found.txt`
(never in `title.txt`, which is yours).

* **Convert**: drop any video; H.264 is rewrapped, VP9 / AV1 ... become visually lossless H.264, checked before the
  original goes.
* **Template**: your channel's template with your headline, live.

## History

Everything that happened -- links added, downloads, edits (PASS / notes, Just cuts), fixes, uploads -- with the date and time
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
