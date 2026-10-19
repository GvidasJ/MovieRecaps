# Day 12 — helper "integrate": joining the five parts, then clicking through the real window

Worktree branch from the merged code (e7ff3f6). Tests: `tools/app/tests/test_integrate_day12.py` (new, 16) plus a few
added / fixed in `test_newvideo_ui.py`, `test_queue_day12.py`, `test_torrent.py`.

## 1. Section 7 everywhere (titles + Just cuts on queue rows and board cards)

- `ui/titles_block.py`: `TitlesBlock(compact=True)` for board cards (~120 px): each key on its own line, the text under
  it (wrapped), its buttons under that; the headline's two "Use as ..." sit in a small **Use…** menu (also in the
  card's right-click menu). New helpers `has_titles(data)` (only when day 12's titles step wrote `post_title` /
  `video_headline` — older projects and Just cuts show no block instead of "reading ..." for ever) and
  `is_just_cuts(data)` (`kind` or `steps.edit.kind`).
- Match cuts queue row: the block under the YouTube title (read from project.json with the row's dates, as before),
  a **Just cuts** pill; Use as YouTube title saves the project's upload title.
- Board card: the compact block, the **Just cuts** pill on its own line.
- **"Use as my headline" — decided** (`core/flow.headline_action` / `set_headline`, used by both):
  - no edit made yet (none, or the last one failed/stopped): the project's headline is simply set (project.json +
    title.txt, line break kept). The edit's start makes template.png from the headline anyway (template_for_job), and
    a template.png made before is made again at once in the background. No question asked.
  - the edit is running (or another computer holds the project's lock): nothing changes, a toast says to use it when
    the edit has finished.
  - an edit is made: a Yes/No question first ("Make the template again with this headline and swap it into the
    finished edit? No new run"), then Change headline (`flow.change_headline`: only template.png, copied over each
    run's frame.png). The question is `page.ask_yes` (replaced in tests).
  - On a queue row the same video's Download row gets the new headline too.

## 2. Download / New video rows = the queue's GlideList

- The newvideo helper's own `GlideList` is gone; `download_tool.RowList` is a thin subclass of `ui/glide_list.GlideList`
  (`set_rows` keeps the old call). Same rules as the queue: running on top, then waiting newest first, then finished
  with **the one finished last on top** (`Item.order_time`: the newest step finish; before, the Download rows sorted
  finished rows by when they were added). A new row scrolls the page to the top with the queue's gliding
  `scroll_to_top` (before: a jump).
- `GlideList` fix found in the click-through: a row gliding UP (the row above it shrank, e.g. the torrent picker
  replaced by the smaller progress card) was cut off at the list's new end until the glide ended; the list now stays
  tall enough while rows glide and shrinks after.

## 3. Torrents in the New video row — tested with a REAL torrent

- `test_a_real_local_torrent_goes_through_the_new_video_row`: a tiny real video + a text file made into a .torrent,
  shared by one aria2c, fetched by a second, a tracker on 127.0.0.1 (DHT/LPD off — never public trackers). A magnet
  typed into the real form → the picker inside the row (BIGGEST VIDEO ticked, 2 files) → Start → the progress card in
  the row → finished → the bytes match, the video is the RAW placed into the project → the row waits for **Convert**
  (Automatic OFF) → Automatic ON → Convert starts at once. It passed the first time: the parallel API matched.
- Fixed around it (`download_tool.py`):
  - "nobody sharing" (stalled) only pauses the download (the card's Resume tries again) — before, it failed the row;
  - **Remove** on the card stops the row (before: the row stayed "running" for ever);
  - **Try again** on a failed torrent resumes the same download (before: a second download of the same torrent);
  - **Stop** on a torrent row pauses it / cancels the file list (before: did nothing);
  - the row shows the torrent's name (`dn=`, else the .torrent's file name), not the whole magnet link.
- **Restart**: each torrent row's video (competitor, channel, headline, mode, speed, kind ...) is kept in
  `%APPDATA%\MovieRecaps\torrent_rows.json` under the torrent job's id (written in the background). The main window
  calls `DownloadPage.restore_torrents()` 1.5 s after the start: when the torrent part has unfinished jobs, each
  comes back on a row with its progress card; a job with no kept row gets a row of its own (Just cuts unless a
  competitor is added). With no unfinished torrent nothing is started (not even aria2c).
- The main tree has no `tools\app\bin\aria2c.exe` yet: **run `setup_app.py`** there (I copied the torrent helper's
  binary into this worktree's git-ignored bin to test).

## 4. Learn hook covers every finished edit

- Full edits sent from Download / New video run in the same Match cuts queue, so `_done` → `learn_tool.after_edit`
  covers them — verified by a test (a real DownloadPage + real MatchCutsPage, the tool thread faked: the waiting card
  lands in the hub) and in the click-through (the New video edit appeared under "Waiting for your fix").
- The one path that did not reach it: **Just cuts** (it runs in the Download page, not the queue). Now a Just cuts
  run that FAILs / has a note goes into problems_queue.md like every run; it gets **no** waiting card (no competitor:
  nothing to learn). `after_edit(..., waiting_card=False)`.

## 5. Click-through of the real window (offscreen)

Script: scratchpad `click_integrate.py` (scratch settings, hub, projects, app data, torrent state; the New video
stages, the Match cuts tool thread and the Shorts check faked; the torrent REAL on 127.0.0.1; `shutdown.SHUTDOWN` and
`turn_off` replaced — **nothing was ever shut down**: 0 calls). Pictures: `work\t27\shots\integrate\` (main tree).

Clicked: every tab; the form with a YouTube link (YOUTUBE · Just cuts), a magnet (TORRENT · Needs a competitor), a
file (FILE); Automatic ON/OFF; the turn-off tick box (the 60 s warning appeared after the last edit, Cancel kept the
PC on); Make the video → Convert → Send to Match cuts → finished; YouTube only → Make the cuts; the torrent row end to
end; the queue's ⇄ Swap and the amber "Check which is the RAW" pick; Use as my headline on a finished queue row (the
question came, No changed nothing); the board card's Use… menu; Board **Clean up** (with a made-up old 5.1 run: the
question listed it); Learn's three columns: I want this (went into requests.md), No, Skip (my mistake), Skip.

Window thread (10 ms timer): the only gap over 100 ms is building the window (170–250 ms, before it is shown;
numpy imported by the channels code at start). Everything after the window is shown: longest gap under 100 ms,
99th percentile ~33 ms (screenshot grabs included).

Found and fixed:
- Board cards cut off their last lines (dates, titles) — the column's layouts never gave a card its height for its
  width; a card now sets its minimum height from its layout. Names break after `_` / `-` (long names were cut at
  the side). The compact titles block and the Just cuts pill fit a 120 px card.
- **A Just cuts project was never saved to the shared folder** (publish only knew `edit*` folders) and the toast said
  "Not saved ... (NothingToPublish: ...) -- tried again later" every time: `publish.EDIT_RE` now takes `cuts`,
  `cuts-2` too; the toast is plain words, and "nothing to publish" is not queued for retrying.
- **Clean up and a published twin** on the same drive (hard links): the same run was listed twice with the same
  name, each "0 bytes -- a hard link of the downloaded RAW" (wrong). Now each copy says which it is ("shared folder" /
  "this computer") and a file whose names are all listed counts its size once ("Space saved: 153 KB").
- The New video form imported the channels code (numpy) and read the channels folder while the window was built
  (~150 ms): the three channel buttons now appear at once and the folder is read in the background (buttons made
  again only when its names differ).
- Emoji and the ⇄ glyph are missing in the offscreen pictures only: the Windows platform renders them
  (`glyphs_windows_platform.png`).

## For the lead

- **C: is nearly full** (320–670 MB free during this run; the scratchpad folders of all helpers are on C:). The
  torrent free-space check refuses anything there (it wants 512 MB spare) — that made the torrent helper's real
  aria2c test fail; it now patches the margin (the refusal has its own tests). My test injects the free space.
- Baseline flake fixed: `test_queue_day12::test_the_queue_shows_newest_on_top...` counted a thread started late by an
  earlier test's page; FakeThread now records only the current page's jobs.
- Once (not reproducible in 3 reruns) pytest died with a Windows fatal exception in a run of 6 test files; worth
  watching in the full runs.
- Files touched that others touch: `download_tool.py` (GlideList → RowList, torrents, restore, Just cuts hook,
  channels), `match_cuts_tool.py` (titles block, use_headline), `board_tool.py` (card), `titles_block.py`,
  `glide_list.py` (one fix), `flow.py` (appended), `newvideo.py` (appended), `learn_tool.after_edit` (a flag),
  `publish.py` (EDIT_RE + sort key), `rawfiles.py` (freed / tags), `main_window.py` (restore_torrents, publish toast).

## Not done

- Board cards still do not glide when their order changes (rebuilt, moved cards fade in — as the queue helper noted).
- The ~200 ms of building the window (before it shows) — mostly imports (numpy via the template editor's channel
  list); not touched.
