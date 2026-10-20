# Day 13 — helper "errors" (items 1.2 and 1.6)

## 1.2 The real reason a run stopped, in plain words

**One shared function**: `tools/match_cuts/match_cuts/plain_reason.py` (in the tool's package, so the batch and the
app use the same words; no cached stage imports it — editing it re-runs nothing).

- `explain(stderr, log_text, code)` → `Reason(text, todo, kind, detail, drive)`; `explain_run(run_dir, stderr, code)`
  also reads the run's `extras\match_cuts.log` (its last 2 MB), `extras\batch_console.log` when the console is not
  given, and the full drive's free space now. `Reason.line()` = "What happened. What to do."
- What it knows, first match wins:
  1. the tool's own stops stay **exactly** as they are: WRONG PAIR, PICTURE NOT FOUND, NOT IN THIS RAW, "The inputs
     look wrong" (kind `tool stop`, no extra words);
  2. a **full disk**: "No space left on device", `[Errno 28]`, Windows' "There is not enough space on the disk"
     (112), the tool's `NoSpaceError`, and an ffmpeg / ffprobe exit code that is −28 as unsigned 32-bit
     (**4294967268 = ENOSPC**; ffmpeg 7 exits with its own error code). The drive comes from the line — for an
     ffmpeg command line, the last path (the file it writes). Borat edit-2/3:
     *"The disk C: ran out of space: the run stopped while converting the RAW. Free space on C: (Board → Clean up),
     then press Try again."* (with "(only N GB free now)" while C: has under 30 GB; not when space came back —
     "full (90 GB free now)" read as nonsense once the half-written file was removed)
  3. **the worker pool's lost file** (`FileNotFoundError …\match_cuts_pool_…\state-4.pkl`): `visual_match._spawn_map`
     writes the pickled state of every parallel call into `%TEMP%\match_cuts_pool_*\state-N.pkl`, its spawn worker
     processes read it from there. Said as *"The run crashed after C: filled up: the tool's helper processes could
     not read their working file in the temp folder (C:\Users\gvida\AppData\Local\Temp)"* when the console or the
     log shows a full disk anywhere; else as the likely cause ("… — this happens when C: is full (or when something
     empties the temp folder during a run)"). The lead investigates the root cause; this only says it plainly.
  4. other ffmpeg / ffprobe codes: negative errno (−12 out of memory, −2 file not found, −13 access denied, −5 an
     input/output error, −22 an invalid setting, −27 file too big, −38 name too long, …) and FFmpeg's AVERROR tags
     (−1094995529 INVALIDDATA = a damaged video, EOF = cut short, *_NOT_FOUND = a missing part …); code 1: FFmpeg's
     own words (moov atom not found, Invalid data …);
  5. Python / Windows errors: the graphics card's memory (CUDA out of memory), RAM (MemoryError, WinError 1455
     "paging file is too small"), a file in use (WinError 32), access denied, a missing file (with its name), a
     network folder that dropped (WinError 53/64/…), a disk error, a path too long, a missing part of the tool (No
     module named / DLL load failed → run setup_app.py);
  6. Windows' crash codes of the tool's process — **one table** now (`plain_reason.CRASH_CODES`, `batch.CRASH_CODES`
     is the same object): day 12's 0xC0000005 / 0xC0000409 / 0xC00000FD / 0xC0000017 plus 0xC0000374 (heap),
     0xC0000006 (in-page: disk), 0xC000001D, 0xC0000094, 0xC0000135 / 0xC0000142 (a DLL missing / not starting →
     setup_app), 0xC000041D, 0xE06D7363, 0xC000013A / 0x40010004 (closed from outside); exit 3 without a result = an
     abort (day 12's words); 130 = interrupted;
  7. anything else: the last meaningful ERROR line (the console's "match_cuts: ERROR: …", else match_cuts.log's
     last ERROR line — its first line only, never the traceback logged under it, never a library warning, never
     "details in …") in plain form (no time/level/logger, no command line, `RuntimeError:` dropped, other exception
     names in brackets at the end), plus *"Press Try again; if it stops the same way again, the full log: Open log."*
- **Only the message that stopped the run decides**: cli prints the exception that stopped the run only to the
  console. Its last meaningful line ("match_cuts: ERROR: X: …", a fatal error, an ffmpeg failure) and the lines
  printed right under it as part of it (FFmpeg's own words after its command line — they also give the file's name
  and, from the head line, the step "while converting the RAW") decide alone: an older soft error in match_cuts.log
  ("speech map failed …" of a step that went on) or an older library line in the console ("CUDA out of memory,
  falling back to the CPU") never wins. The console's other lines and the log's ERROR lines count only when there is
  no such message (a run read back later). A full disk anywhere is still the reason (decision 4).
- Checked against this PC's real FFmpeg 8.0: a missing input exits 4294967294 (= −2), a junk file AVERROR_INVALIDDATA,
  an unknown encoder AVERROR_ENCODER_NOT_FOUND — each comes out right.
- `batch.stop_message` never returns cli's "details in … match_cuts.log" line any more (that was all the laptop
  showed for edit-2/3: the RuntimeError's message ends with ffmpeg's stderr, so a blank line stood between the real
  line and "details in"). Its other answers are unchanged (labels and tests depend on them).
- `batch.stop_reason(stderr, code, run_dir, stop)`; the batch summary's failure line is now "the run stopped (exit
  N): <plain line>"; writing batch_console.log can no longer crash the batch on a full disk.

**Where the app shows it** (all read in the worker thread that already reads the run's files):

- `core/results.read_result` adds `reason`, `todo`, `reason_line`, `reason_kind`, `reason_detail` for a stop;
  `failures[0]` = "the run stopped (exit N): <plain line>" (format kept: tests and problems.check_text rely on it);
  `res["stop"]` stays the raw stop message (the labels WRONG PAIR / PICTURE NOT FOUND / NOT IN THIS RAW come from it).
- the **queue row**: its line is the plain line (`results.short`); a stop that is not the input's fault gets a
  **Try again** button (same video, same mode, next take; `results.stopped_by_itself`). The toast says "Queued
  again (Fast)" / "(Thorough)" (it said Thorough always).
- the **report**: "Why it stopped", "What to do", "From the log" (the technical line); the log button is "Open log"
  (was "Open the run's log", so the words "the full log: Open log" match a button).
- the **New video row** and project.json's `steps.edit.error`: the plain line.
- **History**: the plain line; older project.json entries with the tool's technical line are translated when shown
  (`plain_reason.plain_text`, text only — nothing is read on the window's thread); an old "details in …" alone
  becomes "The run stopped; the reason is in its log. Press Try again; the full log: Open log."
- **problems_queue.md**: the entry's title carries the plain line ("stopped (exit 2) -- The disk C: ran out of space …"); the
  key log lines keep the technical lines for the night run, without the "details in" line.
- the **Learn tab's "Problems to fix" cards**: older entries (the three Borat ones written by day 12's app, titled
  "details in …" / "ERROR: FileNotFoundError …") show the plain reason read from each entry's own key log lines
  (`problems.plain_check`, text only; problems_queue.md itself is not rewritten).
- `newvideo.plain_error` uses the shared words for technical text (an errno, "failed (N)", the pool's file, "details
  in"); plain text stays as it is. The app's own conversion (`convert._ffmpeg_problem`) reads ffmpeg's exit code too.
- **Just cuts** (`flow.just_cuts_result`): a technical stop line ("the cuts could not be made: RuntimeError: …") gets
  the shared words; its own plain lines ("raw file not found: …", "the template … cannot be used") stay.
- cli's own "raw file not found: X" / "competitor file not found: X" → "The RAW file is not there any more (X). Put
  it back where it was (or drop the video again), then press Try again."

## 1.6 Torrents: "File exists, but a control file (*.aria2) does not exist"

What aria2c does (tried with real aria2c 1.19 — the one on this PC's PATH — and 1.37 — the one setup_app installs):
without a .aria2 file, an existing file stops the download with error 13 (1.37's message is the raw one you saw);
with `check-integrity` a complete file is finished at once (nothing uploaded by the sharer, mtime unchanged), a half
file fetches only the missing half — but **a bigger file is truncated and overwritten**. Where the error comes from
in practice: aria2c deletes its `.aria2` when a torrent is complete — a one-file torrent, or all of a folder's files
chosen — so adding the same torrent again (the next Short of the same film) stopped with error 13; when only some of
a folder's files were chosen it keeps the `.aria2` and simply goes on (tested for real: both ways now finish at once,
the film not written, and the card says it was already complete).

So `core/torrent.py` now, before giving a download to aria2c (`_prepare_existing`, also on Resume / restore /
"aria2c lost it"): when there is no `.aria2` but some of the torrent's files are in the folder —

- a chosen file **bigger** than the torrent's file, or whose sampled pieces (up to 32, read and compared with the
  torrent's SHA-1s; one right piece is enough — a part-download has holes) **all differ**, is renamed
  **"<name> (old)<ext>"** ("(old 2)" … when taken) and the video is downloaded fresh;
- everything else is checked by aria2c (`check-integrity=true`, `bt-hash-check-seed=false` unless Settings → Keep
  sharing): the right pieces are kept, only the missing ones are downloaded; complete → finished at once;
- always `allow-overwrite=false`, `auto-file-renaming=false` (also on aria2c's command line);
- a file with its `.aria2` goes on exactly as before (no check, no rename);
- if aria2c still says error 13 (a file the app did not see, e.g. a name aria2c escapes another way — `aria2_path`
  copies its `%XX` escaping), the session asks aria2c for its real paths, prepares the file the same way and adds
  the download again with the check — **once**; a second time it is a plain failure.
- the card says what happened (grey line): "checked against the torrent first …", "already complete … nothing was
  downloaded again", "partly in the folder: the right parts were kept …", "An older file … was renamed to … and the
  video is downloaded fresh" (the rename also as a toast on the New video page, once).
- **every aria2 error** in plain words with what to do: `plain_aria2(code, message, folder)` maps all 32 codes of
  aria2's "EXIT STATUS"; the Windows cause aria2c quotes wins over the bare code (no space, a file in use, no
  permission, a network folder gone, a missing folder, plus "already registered", "piece length", bad magnet /
  .torrent, name lookup, time-out). jobs.json entries saved with day 12's "The download stopped: <aria2c's sentence>"
  are said plainly when loaded.
- a long check (a big film over the laptop's network takes minutes with no peer and no progress) is never "No one is
  sharing": aria2c 1.37's `verifiedLength` counts as progress, and for an aria2c that says nothing while it checks
  the stall time grows by the file's size at 20 MB/s (`CHECK_BPS`).
- **Remove never deletes a file the job found already in the folder** (it may be your earlier finished download,
  being checked: `Job.found`, with its .aria2) — before, Remove on an unfinished job deleted its chosen files
  whatever they were; a part file the job started itself is still deleted.
- Resume after aria2c's error 13 checks again (with aria2c's own paths), so the advice "press Resume" works.
- the free-space check (`check_space`) does not count what is already in the folder (the next Short of the same film
  on a nearly full disk was refused although nothing had to be downloaded).
- **The .aria2 next to the download over a network folder**: nothing in the app moves or deletes it (Clean up does
  not touch `.aria2`; Remove deletes it only for an unfinished download you remove). Tested over
  `\\127.0.0.1\C$\…` (the admin share; the laptop saves over `\\192.168.101.11\Shared` the same way): aria2c keeps
  `<file>.aria2` in the network folder while it downloads and removes it itself when finished.

## The Windows firewall question (the lead's request during the day)

The question "allow public and private networks to access aria2c.exe?" popped up during the torrent tests and
blocked every click. Now every aria2c a **test or the self-check** starts listens on 127.0.0.1 only:
`torrent_selftest.LOCAL_ONLY` = `--interface=127.0.0.1 --disable-ipv6=true --enable-dht=false --bt-enable-lpd=false`
(checked with netstat on 1.19 and 1.37: the RPC and the BitTorrent sockets on 127.0.0.1, nothing on IPv6), used by the
self-check, day 12's real New video torrent test and the day-13 tests; and `tests/conftest.py` adds the same to any
aria2c daemon (`--enable-rpc=true`) a test starts through subprocess, so a future test cannot forget it (a test proves
it on a real aria2c started with the app's own command line). The app's own aria2c is unchanged (it needs the
internet). My two early experiments (before the request) ran aria2c without it — probably the source of that window.
Watched live during the final app test run: 9 aria2c processes, 16 listening sockets, every one on 127.0.0.1.

## Checked on real data, and an independent review

- **Real logs on this PC** (read-only: 742 consoles in output\, input\downloads\, D:\Shared\Projects,
  D:\Shared\problems and earlier nights' work\t2*): 25 stopped runs — 11 "The disk C: ran out of space: the run
  stopped while converting the RAW …" (day 12's own runs when C: filled), 11 WRONG PAIR and 1 PICTURE NOT FOUND
  exactly as the tool said them, 2 "The run stopped without saying why …" (runs you stopped). It found one bug,
  fixed: a console read back from batch_console.log gave its own command line as the reason.
- **The real path end to end**: the app's own `JobRun` ran the real tool (GPU hidden) with a junk RAW → the row
  said "The video raw.mp4 is damaged or not complete, so FFmpeg could not read it. Download or copy that video again
  (or use another copy), then press Try again."; with a missing competitor → "The competitor's file is not there any
  more (…nope.mp4). Put it back where it was (or drop the video again), then press Try again."
- **Rendered offscreen** (the real queue row and torrent card, pictures in `work\t28\errors\shots`): fixed the
  wording "full (90 GB free now)" and a nested-bracket torrent note.
- **A review sub-agent** read the whole diff (read-only) and found 10 points; all fixed, each with a test: a crash
  code beat by an older soft error; Try again dropping "Run anyway" (`--force-inputs`); an older pool line counted;
  Python's exit noise after "Run folder:" taken as the reason; UNC paths losing a backslash; download errors given
  the edit's advice; "(NoSpaceError)" in the words and History re-explaining a plain line; the torrent space check
  counting a same-name other file; Remove keeping a job's own part file when only a sample was there (now: it keeps
  exactly the chosen files that were in the folder before the job); Resume after error 13 not checking again.

## Decisions (the user is away)

1. The shared function lives in the tool (`match_cuts/plain_reason.py`), not in the app: the batch summary needs the
   same words, and the app already imports `match_cuts.batch`.
2. `stop_message` keeps its answers (labels and ~20 tests use them); the plain words are a layer on top.
3. The pool-file crash is said as *certain* only when a full disk shows in the console or the log; otherwise as the
   likely cause, with the other possibility (something emptying the temp folder) named.
4. A disk-full line anywhere in the console or log wins over a later error (it is the root of the cascade: lost
   pool files, ffmpeg failures, logging errors).
5. Queue rows get a **Try again** button only for stops that are not the input's fault (not WRONG PAIR / the input
   checks — those have Run anyway), so the "press Try again" advice is true everywhere; it runs the same video in
   the same mode as its next take, and a row you had run anyway is run anyway again.
6. A same-size or smaller file whose sampled pieces are all wrong counts as "not this torrent's" → renamed, never
   overwritten; a smaller file with nothing checkable (shorter than one piece) is renamed too; a same-size file with
   nothing checkable (under 2 pieces) is checked by aria2c.
7. "Finished at once" is judged by "no peers and no download speed seen before complete" (aria2c 1.19 reports no
   verification progress keys).

## Not done / for the lead

- **This PC's torrent engine today is aria2c 1.19.0 (2015)** from `C:\aria2` on PATH. My branch's base
  `setup_app.py` installed 1.37.0 only when no aria2c was found; the health helper's version check (already on the
  main branch) installs it into `tools\app\bin` when the found one is flagged, and `find_aria2c` looks there first.
  Everything here works with both (tested).
- The Borat runs' real cause (why C: filled, whether Storage Sense emptied %TEMP%) is the lead's (problems_queue).
- Not tried on the real laptop share `\\192.168.101.11\Shared` (cannot be reached from the PC); `\\127.0.0.1\C$` was.

## Tests

- tool: `tools/match_cuts/tests/test_day13_errors.py` (22 tests: the Borat lines, errno/AVERROR/crash codes, tool
  stops kept, never a traceback / library warning, the stopping message decides, the log's last ERROR line, no
  technical brackets, a missing input, a console read back, the review's cases, plain_text, the batch summary).
- app: `tools/app/tests/test_errors_day13.py` (15: read_result, short, problems entry, project.json, History, the
  New video words, Just cuts, the app's conversion, the real queue row with Try again (also after Run anyway), the
  report dialog), `test_errors_day13_newvideo.py` (1: the real New video row), `test_errors_day13_learn.py` (2: the
  Learn tab's old Borat entries) and `tools/app/tests/test_torrent_day13.py` (24: escaping / old name / piece check, the fake-engine
  options and notes, error 13 once, every aria2 code, the card's note, old jobs.json, the space check, the long check,
  Remove keeps a file that was there, 127.0.0.1-only aria2c; **real aria2c on 127.0.0.1**: the next Short of the
  same film (a folder torrent downloaded once, added again with nobody sharing, with and without its .aria2) finished
  at once with the film not written, a finished file finished at once with
  bytes and mtime unchanged and nobody sharing, a half file fetching only the missing pieces (the sharer's upload
  ≤ 60 %), a bigger file renamed "(old)" and fetched fresh, error 13 from aria2c checked by itself, a UNC folder
  with the .aria2 next to the download while it runs). The tests use the aria2c the app itself would use
  (`find_aria2c`: 1.19.0 from `C:\aria2` on this PC today; 1.37.0 from `tools\app\bin` once setup_app has run). Run
  both ways today: on 1.19.0 and with `MOVIERECAPS_ARIA2C=<scratch>\aria2_137\aria2c.exe` (the official 1.37.0, the
  app's real version, fetched with setup_app's own `install_aria2c`): `test_torrent_day13.py`, `test_torrent.py`,
  `test_integrate_day12.py` — all passed on each (67 with the final code on 1.37.0). Where the two differ, both are handled: 1.19 gives error 13 with an
  empty errorMessage (the code alone is enough), writes the .aria2 only at its save interval (the UNC test saves every
  second), and reports no check progress (the stall time by the file's size).
