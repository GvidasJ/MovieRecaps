# Day 12 — helper "extra": 12.3 (the real window), 12.5 (window thread), 12.6 (more tests)

## How it was driven

`work\t27\shots\extra\click_extra.py` (a copy; run: `.venv\Scripts\python click_extra.py WORKTREE OUTDIR [--events]`)
starts the REAL window on the **Windows platform** (QT_QPA_PLATFORM unset — the window really appears on the screen),
with every state file in a scratch folder: MOVIERECAPS_SETTINGS (save_to = scratch hub, torrent_dir = scratch),
MOVIERECAPS_APPDATA (history_log.jsonl, torrent_rows.json, quotes, uploads), MOVIERECAPS_TORRENTS (jobs.json),
MOVIERECAPS_PROJECTS, and %APPDATA% itself pointed at scratch (learn_seen.json's fallback). It asserts the settings
path and app folder are the scratch ones before the window is built. `shutdown.SHUTDOWN` and `turn_off` are replaced
(0 calls in every run). The New video stages, the Match cuts tool run and the Shorts check are fakes; the torrent is
REAL (two aria2c + a tracker on 127.0.0.1, `core/torrent_selftest`). Drops are real `QDragEnterEvent` /
`QDragMoveEvent` / `QDropEvent` with file URLs sent to the widgets. A 10 ms QTimer measures the window thread's gaps,
a watchdog thread samples the window thread's Python stack (and the other threads') during a gap, an overridden
`QApplication.notify` times every event over 50 ms, `gc.callbacks` times collections, and the day's handlers are
wrapped with timers. Pictures: `work\t27\shots\extra\` (45 PNG).

## 12.3 — what was clicked, what was found and fixed

Clicked: every tab and sub-tab; New video with a YouTube link / magnet / file / **dropped episode + competitor** /
**dropped .torrent** / dropped .txt; Automatic ON/OFF; Turn off the PC (warning shown, Cancel, never shut down); Make
the video → Convert → Send to Match cuts → PASS; YouTube only → Make the cuts; on the finished Just cuts row **Change
headline** (a real template.png from the channel's PSD) and **Add a competitor** (file); a convert that fails with a
full disk → red row → **Try again**; the real torrent (picker → Start → progress card → done → Convert); Settings
dialog; the queue's drop zone (two look-alike clips → amber "Check which is the RAW", a clear pair → ⇄ Swap, the pick
buttons, Use as my headline → the question); Board (titles block, Use… menu, Just cuts pill, Clean up question);
Learn (three columns, **a .txt and a broken .prproj dropped on a waiting card**, I want this, No, Skip (my mistake),
Skip); History.

Looked at the pictures: emoji (😭 💀 😂 🔥 🔒) render in colour in headlines, titles, board cards and History; ⇄
renders; Bebas Neue (section heads, Will make) and JetBrains Mono (labels, stats) are loaded (checked by name in
QFontDatabase too). A first-time emoji paint costs ~435 ms without the warm-up and 2-15 ms with `theme.warm_fonts`
(night 11's warm-up works).

Fixed:
- **A dropped (or File…-chosen) .torrent was treated as a video file**: the label said TORRENT, but `add()` passed it
  as `raw_file`, so the row would have "downloaded" the .torrent by copying it into the project and converting it.
  Now a .torrent goes in as the torrent's source (picker inside the row); alone it asks for the competitor.
- The "RAW: episode.mp4" note of a dropped file stayed under the form after a link was typed over it: cleared.
- The Download tab's count line said "1 running" after every video had finished or was waiting (it was only updated
  on some changes): now after every state change.
- A Learn column whose last card was skipped stayed blank (no "Nothing waiting ..." line) until the next refresh: the
  empty line now shows when the card has folded away.
- `torrent.read_torrent`: a .torrent whose file list is broken raised a bare AttributeError/ValueError; now the plain
  "This is not a working .torrent file — its file list is broken. Download the .torrent again."

Seen, not changed: the waiting card takes a broken .prproj and the check card says "I can't read fixed_video16.prproj:
it is not a Premiere project, or it is damaged (ParseError)" (the exception name in brackets; plain enough). A .txt
dropped on a waiting card gives the toast "Drop your fixed .prproj (a Premiere project) on the card".

## 12.5 — the window thread (before → after)

Before (HEAD 3b37efb, same script, log `before_HEAD_3b37efb.log`) and the first run: after the window was shown, the
Python-caused stall was **Send to Match cuts: 67 ms (one run 138 ms gap)** — project.json update, title.txt via
`board.set_title` (which reads the card back), `flow.edit_job`, all on the window thread. Also on the window thread:
Add a competitor's project.json write (sampled inside a 163 ms gap), the row's YouTube title, the queue's template
dialog save.

After: `send_to_edit` **1.2 ms** (the write + job in `ui.background.call` via a small `download_tool.off_thread`; the
row is SENT at once so "Turn off the PC" counts it as work; a second press, a removed row, a failure — red row in plain
words — are handled), Add a competitor / YouTube title / the queue's template dialog save also in the background.
Final run (`after_final.log`): **0 gaps over 100 ms after the window is shown** (3845 ticks, 99th percentile 45 ms);
longest handler on the window thread: `DownloadPage.add` 28 ms, `MatchCutsPage.add_project_job` 25 ms (building the
row widgets), `MainWindow.select` 24 ms.

Not code (for the lead): full-window paints (`UpdateRequest`) take 4-37 ms with the PC quiet but 50-140 ms, and twice
**1.8-1.9 s**, while the GPU was at 100 % (check-all). The widgets' own paintEvents total < 5 ms (`paint_probe.py`);
the time is in Qt's flush to the screen / DWM, which waits for the busy GPU. No Python code runs then, so nothing can
be moved to the background; it will also show while the app's own edit uses the GPU. Building the window (before it
shows) is 395-755 ms; `template_editor.channel_names()` is ~115 ms of it (the numpy import of the channels code) —
not changed (before the window shows; the editor's dialogs need the names at once).

## 12.6 — tests for what had none

Listed with a small AST scan (public functions/classes of today's modules whose name appears in no test). Added:
- `tests/test_extra_day12.py` (30): shutdown (Media Encoder busy / idle / not open / closed while sampled / no
  psutil; blockers in plain words, a crashing check skipped, never turned off while blocked), problems (status words,
  stable ids, set_status messages for no file / unknown id, not fixed without a reason, check_text, open_runs, the
  prompt section with "Tried before"), waiting (from_job with the run inside / outside the project, record never
  raises, set_state of an unknown id, is_fix settle / older than the edit / the app's own _edit.prproj), newvideo
  (prefs with a broken settings file, torrent rows kept / forgotten / not a dict, is_link), torrent (sizes, speeds,
  "no one is sharing" singular / plural, bencode round trip and refusals, broken file lists, a missing .torrent,
  trackers that never answer → None within the timeout), the dropped .torrent, the stale note, the count line,
  Send to Match cuts and Add a competitor in the background (+ failure and removed row).
- `tests/test_extra_day12_ui.py` (6): titles block (reading / nothing readable / Use as YouTube title one line /
  menu enabled), has_titles / is_just_cuts, scroll_to_top (at once / gliding / not in a scroll area), a Learn
  column's count, badge and empty line, closing a card twice, health bars 0-5.
- `tools/match_cuts/tests/test_just_cuts.py` +1: `build_cutlist` (a segment per shot at its frames, centred without a
  face, moved onto a face, the face finder told the previous view, the window covered, no captions).

Still without a direct test (lower value): `torrent.install_aria2c` (network; setup_app), `TorrentSession.poll_all /
peek_seeders` (covered through the picker / card paths only), `torrent_panel.open_folder`, `FileRow` mouse events,
`GlideList.eventFilter / showEvent` (covered by the queue's glide tests), `just_cuts.run_just_cuts` (covered end to end
through the CLI test).

## Tests

App suite: **898 passed, 5 skipped** before → **934 passed, 5 skipped** after (own --basetemp, CUDA hidden, this
worktree's mr_app). `tools/match_cuts/tests/test_just_cuts.py`: 4 passed.

## Files touched (merge care)

`tools/download_tool.py` (`off_thread`, `_set_raw_file`, `_raw_typed`, `send_to_edit`, `add_competitor`,
`set_upload_title`, `_order`), `tools/match_cuts_tool.py` (the template dialog's save), `tools/learn_board.py`
(`LCol.remove` / `_show_empty`), `core/torrent.py` (`read_torrent`'s file list), tests: `test_tabs.py` (one test
waits for the background write), `test_newvideo_ui.py` (the fixture runs `off_thread` at once), new
`test_extra_day12.py`, `test_extra_day12_ui.py`, `match_cuts/tests/test_just_cuts.py`.
