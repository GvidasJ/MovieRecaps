# MovieRecaps desktop app

One window for the whole job, one tab per step, in the order of the work (night 10):
**Download** (yt-dlp) -> **Convert** (FFmpeg, no visible loss) -> **Template** (your channel's template with your
headline) -> **Match cuts** (the edit for Premiere).

The app only **runs the tools as they are**: every edit is the same command as `python -m match_cuts batch` runs for
a video (`--premiere`, `--fast` unless you choose Thorough, the video's speed; with a channel: `--frame <its template>
--watermark <its watermark>`), and its result is read with the batch's own checks. Every step of a video project is
also a command of its own (`steps.py`, below) -- the same code, the same `project.json`.

## Open it

* **PC**: double-click **MovieRecaps** on the desktop (red icon with a white M).
* **Laptop** (once, after `git pull`), in the project folder:

  ```
  .venv\Scripts\python tools\app\setup_app.py
  ```

  It installs what is missing (PySide6, the window, about 100 MB; yt-dlp; psd-tools) and puts the shortcut on the
  desktop. It says so when the laptop has no JavaScript runtime for YouTube (deno or Node.js).
* Without the shortcut: `.venv\Scripts\pythonw tools\app\MovieRecaps.pyw`

## Download

1. Paste the **competitor's link** and the **RAW's link** -- or drop the RAW file (or "Choose the RAW file...")
   instead of its link.
2. Pick the **channel** (LouLaughs, MikeMemesYou, Flick707 ... or Default) and type the **title** (or later, with
   the row's Template button).
3. **Add to the queue**. Add as many videos as you like; they are prepared one after the other.

Each video gets its own **project folder**, `input\downloads\<date>_<short name>` (the short name from the
competitor's title), with both videos, `project.json` (the state of every step, for the next night's project board)
and `logs\`. Then, by itself:

* **download** (yt-dlp, updated at most once a day): the best H.264 + AAC MP4; VP9 / AV1 only when that version is
  bigger than every H.264 one (then it is converted); a progress bar per file; errors in plain words (a private
  video, blocked in this country, a wrong link, YouTube's "are you a bot" check ...);
* **convert** (below), both files;
* the **competitor's headline** is read from its video and your title is checked against it;
* with "Then make the edit by itself" (on by default): the video joins the **Match cuts** queue and the queue
  starts; its run folder is `edit` in the project folder. A channel without a title waits for the title instead.

A RAW file you drop goes into the project folder too: a hard link when it is on the same drive (instant, no space
used), else a copy.

## Convert

Drop any video (or many, or a folder). Like Shutter Encoder, with FFmpeg:

| the video | what happens |
|---|---|
| H.264 (or HEVC) with AAC in an MP4 | nothing to do: kept as it is |
| H.264 / HEVC in another container (MKV, WebM, MOV ...) | **rewrapped** to MP4: the picture copied bit for bit (100 % lossless) |
| VP9, AV1, VP8, MPEG-2, DivX, WMV ... | **visually lossless H.264**: the same size, frame rate, frames and colours; the sound copied (AAC) or made AAC |
| ProRes / DNxHD / CineForm in MOV | kept (Premiere edits them well) |

The new file is **checked** (the same number of frames and the same length) before the original is deleted ("Delete
the original when the new file is checked", on by default). Encoder: **Auto = the processor** (x264 CRF 13, slow):
measured on night 10, the graphics card (NVENC) reaches the same quality only at about twice the file size
(reports\night10.md, section 3); "Graphics card" uses it anyway (never worse, about twice as big). FFmpeg runs at
below-normal priority, so an edit running at the same time keeps its speed.

## Template

Pick the channel, type the title (emojis: Win + . opens Windows' emoji panel; Enter = your own line break) and see
the template live. It is drawn exactly like the channel's Photoshop file: NeueMontreal Bold 122 pt, Faux Bold, black,
tracking +10, centred, 2 lines broken in a natural place inside 920 px; the emojis in Noto Color Emoji. Only when no
break fits is it shrunk, and the tab says so in amber. Drop the competitor's video to read its headline: your title
is checked against it (a warning at 80 % of the same words, in any order). "Save the PNG..." writes the 1080x1920
template.

A **new channel** is a new folder `assets\channels\<Channel>\` with its `template.psd` (2160x3840, the white card,
the transparent video window and an example headline layer) and `watermark.png` -- no code change. `channel.json`
there keeps its link (for the next night's "uploaded" check).

## Match cuts

1. **Drop videos** on the big box (or use the buttons):
   * a RAW and a competitor together = one video. The RAW is the file named `raw.*`, else the longest video. If the
     app guessed wrong, press **Swap** on the row.
   * a folder of folders (like `input\videos` with `video1`, `video2` ... inside) = one video per folder (a folder
     with several competitors: one per competitor) -- the batch's own rule.
   * one video alone waits (an orange line) until you drop the other one.
   * drop more pairs at any time, also while the queue runs: they join the queue.
   * videos from the Download tab join by themselves.
2. On each row choose **Fast** (the default) or **Thorough**, the speed (**100 %** / **125 %**: click it; a
   `speed.txt` in the video's folder sets it too) and the **Template** (your channel and title, with the live preview
   and the competitor's headline next to it; Default = `templates\default.png`, no watermark).
3. **Start** runs the queue one video after another; **Stop** stops the running video (the others stay in the
   queue). Before it starts, the app warns when something looks wrong (only one video, the competitor longer than
   the RAW, the tool's own wrong-input check, a channel without a title, a title too close to the competitor's
   headline, not enough free space for the run folders).
4. Each row shows the step the tool is in and the time left, then the result:

   | result | means |
   |---|---|
   | **PASS** | every check passed |
   | **OK with a note** | every file is there, a check of `1_edit.xml` found something to look at in Premiere |
   | **FAIL** | a check failed (Show report says which) |
   | **PICTURE NOT FOUND** | the competitor's sound is in the RAW, its picture is not (another version of the RAW?) |
   | **NOT IN THIS RAW** | neither its picture nor its sound: another episode? |
   | **STOPPED** | you pressed Stop |

   with **Open folder**, **Open in Premiere** and **Show report**. **Open in Premiere** starts Premiere Pro when it is
   not running, then hands it the run's edit as `<run>_edit.xml` (a new project named after the run, saved by
   Premiere in the folder of the last project you opened). Your usual way -- importing `1_edit.xml` into your
   template project -- is unchanged.
5. When the queue is done, Windows shows a notification.

Every run folder holds its own media in `extras\media` (a hard link on the same drive, else a copy -- also to
another drive, a network folder or a USB drive); the app warns before the start when a drive has no room for that.

## Learn from your fix

Drop your fixed Premiere project (`.prproj`) and the tool's output folder it was made from (`output\video4` ...) on
the second box of the Match cuts tab. The app copies them to the names the nightly LEARN item reads --
`finished\<name>\<name>_fixed.prproj` and `output\laptop_<name>` -- and shows the **LEARN line** for the next night's
list. **Copy the LEARN line** puts it on the clipboard.

## Each step on its own (`steps.py`)

The same code as the tabs, one step at a time; every step writes its state into the project's `project.json`:

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

PROJECT is a project folder (or its name in `input\downloads`). The projects' folder moves with the environment
variable `MOVIERECAPS_PROJECTS` (the next night: `D:\Shared\Projects`, for both computers).

## For a new tool (later)

Add a class to `mr_app/tools/` (see `tools/__init__.py`: `key`, `title`, `ready`, `page(ctx)`) and list it in
`tools()`. The theme (`mr_app/ui/theme.py`) and the widgets (`mr_app/ui/widgets.py`: cards, tags, drop zones,
toggles) are shared; the steps of a video project are `mr_app/core/flow.py`.

Tests: `.venv\Scripts\python -m pytest tools\app\tests`
