# Day 12 — helper "torrent" (section 6c)

## What I built

- **Engine: aria2c 1.37.0, not libtorrent.** `pip install libtorrent` fails: PyPI has wheels up to Python 3.13 and the
  venv is 3.14.3. So the spec's fallback is used. aria2c is the official portable build from
  github.com/aria2/aria2 releases. `setup_app.py` downloads it, checks the zip's SHA-256 (pinned) and puts
  `aria2c.exe` into `tools\app\bin`. That folder is git-ignored, so **the main tree needs `setup_app.py` run once**,
  and the laptop too. The app starts one aria2c when it needs it. It talks to it over a local JSON-RPC on 127.0.0.1,
  with a random port and a random secret. aria2c has `--stop-with-process=<app pid>`, so it never outlives the app.
- `tools/app/mr_app/core/torrent.py` (no Qt):
  - It reads .torrent files with its own bencode code, and it reads magnet links (hex and base32 btih).
  - `TorrentSession.probe(source)` returns a `Meta`: the name, the files with their sizes, the total and the seeders.
    For a magnet, aria2c fetches only the file list (`bt-metadata-only`). The seeders come from a tracker "scrape"
    over HTTP and UDP (BEP 15), which is the number qBittorrent shows before a download. aria2c cannot count the
    swarm before it downloads.
  - `biggest_video()` picks the largest *video* file, not the largest file.
  - `check_space` needs the chosen size + max(512 MB, 3 %) free. Otherwise it gives a plain error. An unreachable
    folder also gives a plain error.
  - `start(meta, indexes, save_dir)` downloads only the chosen files (`select-file`,
    `bt-remove-unselected-file`).
  - `poll(id)` returns a dict: state, share/percent, speed, seeders, peers (= connections − seeders), a 0-5 health
    value, eta and the video path. `pause` / `resume` / `remove` are there too.
  - **Stop sharing:** by default each job gets `seed-time=0`, and a finished job is also removed from aria2c. With
    Settings → "Keep sharing after a download" ON, a job seeds up to ratio 1.0 (or until the app closes).
  - **Restart:** jobs live in `%APPDATA%\MovieRecaps\torrents\jobs.json`, together with a copy of each .torrent. Use
    `restore()`, or a poll that finds aria2c has lost the job: the job is added again with the **same gid**, and
    paused jobs stay paused. aria2c continues from its own `.aria2` progress file next to the download.
  - **Plain errors:**
    - a broken magnet or .torrent
    - "No one is sharing this file right now — tried for N minutes": the file list after 5 min, or a running
      download with no peer and no progress for 10 min. The job is then paused; Resume tries again.
    - disk full (aria2 error 9)
    - write or network problems
- `core/torrent_selftest.py` is the real check. It makes a tiny random file and its .torrent, then starts a
  20-line HTTP tracker on 127.0.0.1. One aria2c seeds the file. A `TorrentSession` with a second aria2c then fetches
  the file list over a magnet, downloads the file and compares the bytes. Nothing touches the internet. The laptop
  can run it with: `cd tools\app` then `..\..\.venv\Scripts\python -m mr_app.core.torrent_selftest`.
- `core/settings.py` has 2 new fields. `torrent_dir` ("" = `D:\Shared\Downloads\Torrents` on the PC and
  `\\192.168.101.11\Shared\Downloads\Torrents` on the laptop, from `machine.which`). `torrent_keep_sharing` defaults
  to False. `ui/settings_dialog.py` has a "Save torrents to" row (Browse / This computer's default) and the
  On/Off switch.
- `tools/app/mr_app/ui/torrent_panel.py` (Qt):
  - **`TorrentPicker(source, save_dir=None)`** is the `.tcard` picker. While a magnet is being read it shows
    "Asking the peers for the file list… m:ss" and a gliding bar. Then it shows:
    - the name
    - "N files · total · S seeders · free on D: X" (seeders in green)
    - a row per file with a tick box and its size; the biggest video comes first, ticked, with the coral
      BIGGEST VIDEO label, then the other files from big to small
    - "Save to …" and "N GB selected"
    - Start download (red) / Cancel

    An amber note shows when the selection doesn't fit. Rows fade a background and border on hover and never move.
  - **`TorrentCard(job)`** is the progress card:
    - the name of the chosen video, with a pill DOWNLOADING / PAUSED / STOPPED / FAILED / DOWNLOADED
    - a GlideBar
    - `.tstats`: Seeders (green), Peers (blue), Speed, Done, Time left, Health (5 bars, 4 + 2.5·i px)
    - the footer "size · Added hh:mm" | "stops sharing when finished"
    - Pause⇄Resume, Remove

    When done: a green border, the stats hidden, "finished hh:mm · sharing stopped", and Open folder (which runs in
    the background).
  - All aria2c, disk and network work goes through `ui/background.call`. A poll is a single local RPC, once a second,
    and is skipped while the last one is still running. The session is created lazily, in the background.

## The API for "newvideo" (tools/download_tool.py)

```python
from mr_app.ui import torrent_panel as TP

picker = TP.TorrentPicker(source, save_dir=None)   # source: "magnet:?..." or a .torrent path; None = Settings / PC-laptop default
picker.chosen.connect(lambda idx: ...)             # list[int] of file indexes; free space already checked
picker.cancelled.connect(...)                      # Cancel pressed
picker.failed.connect(lambda msg: ...)             # plain words: broken magnet, nobody shares it, ...
picker.ready.connect(...)                          # the file list is on screen
picker.meta, picker.save_dir                       # after ready

job = picker.start_job(idx)                        # or TP.TorrentJob.begin(meta, idx, save_dir)
job.progress.connect(lambda st: ...)               # dict: state, share, percent, speed_text, seeders, peers, health,
                                                   #       eta_text, video, size, error, sharing, will_share
job.finished.connect(lambda video_path: ...)       # str: full path of the chosen (biggest chosen) video — told once
job.failed.connect(lambda msg: ...)                # plain words (no space, disk full, no one sharing for N min, ...)
job.removed.connect(...)
job.pause(); job.resume(); job.remove()
card = TP.TorrentCard(job)                         # the progress card for that job

TP.restore_jobs(lambda jobs: ...)                  # at app start: the unfinished jobs (list[TorrentJob]), running again
```

`job.id` is the gid, which stays the same across restarts. Keep it in project.json to find the row again after a
restart. `TP.shutdown()` runs by itself on `aboutToQuit`.

## Decisions

- **Remove** deletes the part-downloaded files of an *unfinished* job (its files and the `.aria2` file). A finished
  download is never deleted; Remove then only forgets it.
- "Seeders" before a download comes from the trackers' scrape. With a DHT-only magnet (no `tr=`) it shows
  "seeders unknown". During a download the live numbers are aria2c's connected seeders and peers.
- The stall timer only counts "no peer connected and no new bytes". A swarm of peers that never send data does not
  trip it.
- aria2c is started with `--file-allocation=none`, which suits the laptop's network share.
- The probe folder is `torrents\probe` (short). aria2c (a mingw build) cannot write paths longer than 260 characters.
  The first version used a per-hash subfolder, and pytest's long tmp path broke it. Very deep torrent folders inside a
  long save path could hit the same limit. That has not been seen; this is a note only.

## Not done / for the lead

- Not wired into the window. The newvideo helper places the picker and card in the New video form. They should call
  `TP.restore_jobs` once at start-up.
- No real public torrent was tried, by the rules. Only the localhost check, and only on this PC.
- UNC save folders (`\\192.168.101.11\...`) have not been tried with aria2c on the laptop.

## Tests

`tools/app/tests/test_torrent.py`, 27 tests:
- a fake aria2c covers:
  - the file list and the biggest video
  - single-file torrents
  - magnet hex/base32 and their errors
  - broken .torrent files
  - the health bars and eta text
  - a magnet probe (and that the probe is removed afterwards)
  - "no one is sharing" after the wait
  - free-space refusal, nothing chosen, an unreachable folder
  - select-file / seed-time options and the live numbers
  - pause/resume/remove (the part files are deleted)
  - finished → stop sharing, and the keep-sharing switch
  - a stall after 10 min → paused, then resumed
  - disk full
  - a restart that keeps the same gids, with paused staying paused
  - the settings fields and defaults
- on the real widgets: the picker (rows, the BIGGEST VIDEO label, totals, Start → `chosen`, the space refusal,
  Cancel, a broken magnet), the progress card (stats, health, pause/resume, failed, finished → green and told once),
  Remove, a start refused for space, `restore_jobs`, and the Settings dialog
- one real aria2c↔aria2c transfer on localhost (skipped if aria2c.exe is missing)

Full app suite: 764 passed, 5 skipped. One earlier run had a flaky `test_locks` heartbeat failure under load from the
other helpers' runs; it passes alone and in the rerun.

Files touched that others may touch too: `core/settings.py` (2 fields + `_valid`), `ui/settings_dialog.py` (one new
row), `setup_app.py` (an aria2c step before the shortcut), `requirements.txt` (a comment line), `.gitignore`
(`/tools/app/bin/`).
