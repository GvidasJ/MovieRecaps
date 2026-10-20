# Day 13 -- helper "health": free space (1.3), setup_app's crash (1.4), the same versions everywhere (1.5)

## What I built

### 1.3 Free space before a start, and in the top strip

**One place for every estimate: `tools/app/mr_app/core/space.py`.** The numbers are named constants at its top
(change them there):

| what | number | where it comes from |
| --- | --- | --- |
| the tool's temporary analysis copy of a RAW it cannot read as it is | 25 Mbit/s for 1080p, scaled by the picture's size (4K = 4 x), never less than the RAW itself | Borat: 84 min x265 1920x1040 -> a 15.0 GB (14.0 GiB) `raw_stereo.mp4` = 23.9 Mbit/s = 24.8 for a full 1080p picture; estimate 14.1 GiB |
| A1's stereo WAV (rawkeep: 16-bit 48 kHz) when the sound is 5.1, not AAC / PCM, starts late, or V1 is a copy | length x 192 kB/s | Borat: 921 MB |
| V1's own copy when the picture cannot be played as it is (rawkeep) | a remux (MKV, a late start) = the RAW's size; a CRF 17 slow copy (VFR, rotation, interlacing, WebM / VP9 / AV1 ...) = 0.6 x the analysis copy | +5 CRF ~ half the bitrate, rounded up |
| the tool's saved analysis (`work\cache`) | 300 MB + the proxy (frames x 640x360, at most 3 GB) + 30 MB a minute | this PC's `work\cache`: proxies up to 3.2 GB (budget 3 GB), a feature film's RAW index 2.3 GB, its sound 0.4 GB -> Borat 5.8 GB |
| the run's own files | 1 GB | night 10 |
| a copy of the RAW / the competitor | its size, only when the run folder is on another drive (else hard-linked) | night 10 |
| a download | yt-dlp's filesize / filesize_approx, else bitrate x length, else a guess by height (1080p 6 Mbit/s ...) -- x 4 for VP9 / AV1 (converted), x 1.1 for H.264 / HEVC | night 10 + the guess |
| the app's conversion | rewrap 1.05 x, encode 3 x the original, next to it | day 12 (a 1.08 GB 4K VP9 became 2.9 GB) |
| safety margin | + 10 %, at least 512 MB | |
| the strip | amber under 30 GB, red under 10 GB; read every 2 min; a network folder gives up after 8 s | the brief |

When the RAW needs the analysis copy follows the tool's own rule (`match_cuts.probe.ae_issues`) as far as one
ffprobe call can tell: not H.264 / ProRes (x265 / HEVC, AV1, VP9 ...), 10-bit H.264, not MP4 / MOV, a variable frame
rate, a picture or sound that starts late, sound that is not AAC / PCM (AC-3, DTS ...), rotation / non-square pixels /
odd sizes / interlacing. What Premiere then plays follows the rawkeep helper's `match_cuts/edit_media.py` (read in its
worktree today, not merged yet): the original (linked), a remux, or a CRF 17 copy, plus the WAV (`premiere_picture`,
`stereo_bytes` in space.py). A New video's RAW file is judged as the app's own conversion will leave it
(`after_convert`: a rewrap keeps the picture, an encode makes H.264 + AAC). Files under 1 MB are not read with
ffprobe (no real video is that small; the night-10 name rule then).

**Where it is checked -- nothing starts without room:**

* **Match cuts Start** (`MatchCutsPage.start` -> `_checked`): the runs going to one drive are added up -- what stays
  (copies, stereo files, saved analysis, run files) adds up, the temporary analysis copies count once (the biggest:
  the runs go one after another and each deletes its copy). Not enough room: **never started** (also when the queue
  starts by itself from New video), the status line and a toast say so, and the window asks "Not enough free space --
  nothing was started" with the numbers and **Clean up** / Not now. No "Start anyway" for space.
* **Just before each run** (`JobThread.run`, in its thread): the same check for that one video (a video added while
  the queue runs, or a download that ate the space meanwhile). Not enough: the video is not started and waits in the
  queue again, the queue stops (the next ones need the same drive), Clean up is offered.
* **New video** (`StageThread._create`): the whole video -- its downloads (or the RAW file copied in from another
  drive), their conversions, and the edit's run (saved analysis, the analysis copy of a RAW file the tool cannot
  read as it is, the edit's files). Not enough: the row stops red with the numbers, Clean up is offered.
* **Conversions** (`core/convert.py`, the Convert tab and the New video's Convert step): rewrap and encode (encode was
  only a log note before). Not enough: the row fails with the numbers, Clean up is offered.
* **Torrents**: unchanged -- `core/torrent.py` already checks the chosen files + a margin before the start.

The words, e.g. (the real Borat RAW, its run folder on C:, the RAW on D:): `not enough free space on C: -- about
28.1 GB is needed there, only 9.2 GB is free (borat: a temporary copy of the RAW for the analysis (x265 / HEVC, sound
that starts late; deleted at the end) 14.1 GB; the tool's saved analysis of the RAW (work\cache) 5.7 GB; a copy of the
RAW (3.8 GB: another drive, it cannot be linked) 3.8 GB; the run's own files 1.0 GB; the RAW's stereo sound for
Premiere (WAV) 921 MB) -- free some space (Board -> Clean up), then press Start again`.
`newvideo.plain_error` now keeps a disk-full message that says how much is free (it used to turn every one into the
generic "the disk is full").

**The top strip:** `Free C: 64.2 GB · D: 2.6 TB` (C:, plus the drives of this computer's projects and of "Save
projects to" when they are other ones; the laptop's share is named "Shared"). Each drive's colour fades (0.5 s) to
amber under 30 GB and red under 10 GB; "?" when it cannot be read. Its width is fixed by the longest numbers (nothing
moves when a number changes); in a narrow window it drops the word "Free". Read in the background (`ui/background`,
each drive in its own thread with an 8 s limit) every 2 minutes, after each run (`_done`), after each New video step
and conversion, and when the last busy page goes idle. Hover: dim -> coral (a fade); a click opens the Board's
**Clean up** (it looks first and asks before removing anything). The tooltip says free / total and what each drive
holds. To make room for it, the strip's GPU / Premiere / Save to texts now end in "…" in a narrow window (at 980 px
the clock overlapped otherwise) -- checked with screenshots of the real window at 1280 and 980 px.

### 1.4 setup_app.py no longer crashes on a broken / old PySceneDetect

The check reads pip's records first (`importlib.metadata`, nothing imported): missing or not 0.7.1 -> install. Then
`importlib.util.find_spec("scenedetect.output")` inside try/except: a broken package (ModuleNotFoundError, anything)
-> "PySceneDetect 0.7.1 is broken: it does not load (...)" -> install. The install is as before:
`pip install --no-deps scenedetect==0.7.1 click platformdirs`.

### 1.5 The same versions on the laptop as on the PC

**ONE table: `tools/app/mr_app/core/versions.py` (`WANTS`)**, used by setup_app.py AND the app's start-up check:
PySceneDetect ==0.7.1, PyTorch (+ CUDA when the computer has an NVIDIA card), torchaudio, yt-dlp >=2026.8.19,
aria2c >=1.37.0, FFmpeg / ffprobe >=6.0, PySide6-Essentials ==6.12.0, faster-whisper >=1.0, psd-tools >=1.25.0, the
matching tool (installed from this project), deno -- plus the tool's own packages read from
`tools/match_cuts/pyproject.toml` (numpy, scipy, OpenCV, av, soundfile, matplotlib, psutil). `requirements.txt` says
the same (a test compares them).

* **setup_app.py** now installs only what the check finds missing, old or broken (and prints "found: ..." and
  "left: ..." lines); `--from-app` (Fix now) leaves PySide6 and the shortcut alone. Exit 0 = all as expected, 1 = pip
  failed, 2 = something left for a step by hand or for a run with the app closed.
* **The app**, 4 s after the start, in the background: `python -m mr_app.core.versions --json` in its own process
  (a broken package can never touch the window; PyTorch's CUDA is asked in a process of its own too). Anything wrong
  -> ONE amber line under the top strip (glides open 0.45 s, fades in): "This computer differs from what the project
  expects: PySceneDetect 0.6.4 is too old (0.7.1 needed) · aria2c 1.19.0 is too old (1.37.0 needed)" + **Fix now**.
  Fix now -> `setup_app.py --from-app` in the background (its output into `%APPDATA%\MovieRecaps\setup_app.log`), the
  line turns blue "Fixing ... installing PySceneDetect ..." (+ **open log**), then the check again: green "Fixed ✓"
  (glides closed after 5 s) or amber "Not everything could be fixed: ... -- <the step by hand>" with Try again and
  open log. A step setup_app cannot do (FFmpeg missing, an NVIDIA driver too old) is said in plain words without Fix
  now. Fix now waits while a video is made, downloaded or converted (pip would replace files a run is using), and it
  counts as work (Turn off the PC waits for it).

## Decisions (the user was away)

1. **No "Start anyway" for space** -- the brief says "don't start". If an estimate proves too pessimistic, lower the
   constant at the top of space.py.
2. The analysis copy is counted on the run folder's drive (where the tool's conform writes it today,
   `<run>\extras\media`); the saved analysis on the work folder's drive (`MovieRecaps\work` = C: on both computers).
   A second run of the same RAW reuses its saved analysis, but that cannot be told cheaply: it is counted anyway.
3. Sound that is not AAC / PCM counts as "needs the analysis copy" (the tool converts it for its analysis today, and
   rawkeep's change keeps that; only V1 / A1 change). **If that changes, add those codecs to `ANALYSIS_AUDIO` in
   space.py** (one line). My brief said "~ duration x 320 kbit/s" for the 5.1 sound; rawkeep's edit_media.py makes a
   16-bit 48 kHz WAV instead (6 x bigger), so the estimate follows edit_media.py.
4. A New video is checked for the whole chain at its start (download + conversion + edit), so a 1-hour torrent / a
   long download never ends in an edit that cannot start. Nothing known about its files (no size, no length): not
   checked, as before.
5. Torrents: left to core/torrent.py ("errors" owns its texts today); no Clean up question there (Clean up frees
   project leftovers, rarely the torrent drive).
6. PyTorch has no version pin (the repo has none): present, and with an NVIDIA card a CUDA build that sees it.
   Missing / CPU-only with a card -> setup_app installs torch + torchaudio from the cu130 index (the PC's build,
   ~3 GB). CUDA not available with a CUDA build (driver too old) -> a step by hand, never a reinstall.
7. FFmpeg is never installed by setup_app (a step by hand: `winget install Gyan.FFmpeg`).
8. The matching tool installed from another folder is only *said* (a step by hand) and the tool's packages are
   installed by their requirement strings, never `pip install -e` -- a setup_app run from a second copy of the project
   (a git worktree, like today's helpers) must never repoint the shared venv to that copy.
9. PySide6 (the window's own files) is replaced only with the app closed: Fix now says so.
10. The start-up check does not run inside the tests (pytest) or with `MOVIERECAPS_NO_START_CHECKS=1`.

## Found -- the lead must know

* **A start-up freeze (fixed, `main_window._preload`)**: under heavy load a test froze for good while building the
  window: the Download page reads the channels' names in a worker (`template_editor` -> `core.channels` ->
  `core.headline`, a slow first import) while the Template page imports the same modules on the window's thread --
  the stack dump showed the two threads waiting for each other's import locks (Python 3.14). The same race exists at
  every start of the real app. Now those modules are imported on the window's thread before the pages are built (the
  Template page imported them there anyway: no extra time). Seen 2 times in ~10 runs of the same tests while the PC
  ran check-all + an 18-process match_cuts run; 0 times since.
* `test_queue_day12.py::test_a_row_that_changes_state_glides_to_its_new_place` fails under heavy load (the row is
  still 4 px from its place after 1 s) -- also on the untouched base code (3 of 3 under the same load); it passes when
  the PC is calmer. Not caused by today's changes.

* **The PC's aria2c is 1.19.0 (2015)**: `C:\aria2\aria2c.exe` on PATH, and the main tree has no `tools\app\bin`, so
  the app's torrents run on 1.19.0 (day 12's setup_app only installed aria2c when none was found at all). The new
  check calls it too old; **section 13's setup_app run will download aria2c 1.37.0 (2.4 MB, checksum-checked) into
  `tools\app\bin`**, which the app prefers from then on. Tell the "errors" helper (aria2's behaviour around `.aria2`
  control files differs between versions).
* The biggest space user on C: is the tool's saved analysis: this PC's `work\cache` holds proxies 62 GB, RAW indexes
  15 GB, sound 2.4 GB. Clean up does not touch it (and caches must never be pruned by date). A suggestion: Clean up
  could list the saved analysis of RAWs whose projects are uploaded, and ask.
* From Git Bash, `CUDA_VISIBLE_DEVICES=` (empty) did not hide the GPU from a grandchild PyTorch process
  (`torch.cuda.is_available()` was True). The tests here never import PyTorch (fake readers).
* My first look probe let the app create its hub files in a new folder `D:\Shared\Projects_probe_never_written`
  (quotes.txt, requests.md); I removed that folder (it was mine, minutes old). The probe now keeps every folder in
  scratch.

## Not done

* The torrent picker does not offer Clean up (its own check stays as it was).
* A Just cuts run has no separate check at its own start (the New video check at the start covers its edit).
* Not tried on the laptop (the share's free space over the network; Fix now on a real old PySceneDetect).

## How it was tested

* `tests/test_versions.py` (14): versions compared as numbers; PySceneDetect missing / old / broken in plain words;
  programs missing / too old / by hand; PyTorch on a computer with and without a card; the tool from another folder
  (only said) and its packages; the one line and setup_app's pip plan (torch + torchaudio once, PySide6 left while
  the app is open); requirements.txt = the table; the app reading the check's JSON; Fix now's process and its log;
  **setup_app with `find_spec` raising ModuleNotFoundError goes on, installs 0.7.1 and ends 0**; an old one is
  updated; `--from-app` leaves PySide6.
* `tests/test_space_day13.py` (12): the analysis-copy rule and size (Borat 13.8-14.6 GiB; real: 14.0), what Premiere
  plays (original / remux / CRF 17 copy) and the WAV, the saved analysis, a queue counting the temporary copy once,
  the plain words (needed / free / Clean up), a folder that does not answer gives up in 0.2 s, the strip's drives and
  levels, a download's size or guess, a conversion, a New video (the words reach the row unchanged through
  plain_error), a New video's RAW judged after the app's own conversion. (`test_space.py`'s 3 night-10 tests pass
  unchanged.)
* `tests/test_health_ui.py` (10, real widgets): the strip's colours fade to amber / red and its size never changes,
  a click; the share's name; the version line glides open / Fixing / Fixed / glides closed, a step by hand without
  Fix now; the window reads the free space in the background and again after "a run ended"; the check opens the line
  and Fix now fixes; Fix now waits while a video is made; Clean up offered -> the Board's clean_up; the queue never
  starts without room (no "Start anyway"); a run without room just before its start waits again and the queue stops.
* The real window rendered at 1280 / 980 px (offscreen, fake numbers): `work\t28\health\look\look_1280.png`,
  `look_980.png`, `look_fixing.png`.
* App tests: TOTAL_APP. Tool tests: not run (the tool was not changed).
