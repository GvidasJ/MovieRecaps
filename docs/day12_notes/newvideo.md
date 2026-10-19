# Day 12 — helper "newvideo": the New video form (6a, 6b, 6d, 6e) and real titles (7)

## What was built

**The Download tab is now one "New video" form** (`tools/app/mr_app/tools/download_tool.py`, logic in
`core/newvideo.py`):

- RAW field (YouTube link / magnet / .torrent / video file) and an optional Competitor field (link or file), each with
  a small label that recognises what was pasted: YOUTUBE / TORRENT / FILE / LINK (Instagram, TikTok ...) / EMPTY.
  "File..." buttons and a drop zone (RAW first, then the competitor).
- Channel buttons with their colour dots; the last one is remembered (settings.json `newvideo_channel`).
- Headline: a two-line box (Enter = line 2, a second Enter does nothing, a pasted text keeps at most two lines),
  emoji OK, a green READY label when filled. The typed break is passed to the template as it is; without a break the
  night-10 renderer (`core/headline.layout`) splits and shrinks it exactly as before (nothing new there).
- YouTube title (optional), Fast / Thorough and 100 % / 125 % (kept from the old form).
- "WILL MAKE": Full Match cuts edit / Just cuts (every scene change) / "Needs a competitor" (a torrent alone; "Make
  the video" refuses it and says so).
- "Many at once": one pair per line, `RAW link | competitor link` (a line without a competitor makes Just cuts; each
  problem names its line).

**Rows** (one per video, the reference's `.pipe`): name, channel, a "Full Match cuts" / "Just cuts" pill, a state
pill, one gliding bar per step (Download, Convert ×2, Template, Edit — or Download, Convert, Cuts) with its finish
time (green done, blue running, amber note, red failed), the pair line, the two real titles, your headline, a
bottom line that says what the row waits for, and the buttons.

- **Steps wait for buttons unless Automatic is ON**: Download finished → Convert + Open folder; Convert finished →
  Send to Match cuts (or Make the cuts) + Open folder; then the template and the edit run by themselves. The bottom
  line says "Downloaded — press Convert when you want" etc.
- **Automatic: ON/OFF** switch at the top (OFF by default, remembered: settings.json `newvideo_automatic`). Turning
  it ON lets every waiting row go on at once (a row waiting for its headline then uses the default template).
- **No headline + Automatic ON**: the channel's template.psd as it is (its own example headline kept) becomes
  template.png (`flow.default_template_step`, project.json `steps.template.default = true`); the row and its pill say
  "default template" (amber) and **Change headline** makes only template.png again and copies it over each run's
  `extras\media\frame.png` (the file 1_edit.xml points at) — no new run (`flow.change_headline`).
- **No headline + Automatic OFF**: the Template step turns amber "needs a headline"; the row shows the two-line box
  and **Make the template**.
- **Errors in plain words** (`newvideo.plain_error`): already-plain messages (download.py / convert.py) stay; a full
  disk, a library warning line (OpenCV ...), a bare exception name become a sentence; a library warning is never shown
  as the reason. A failed step stops the row with a red FAIL pill, the step's bar red, and **Try again** continues
  from that step (the done steps are kept: nothing is downloaded again).
- **History**: everything comes from project.json as before; new: "Just cuts made · PASS" (with "37 cuts"), "Just cuts
  failed", "Template made — default template (no headline)" in amber. A video that fails before its folder exists
  (a broken / private link at the first read) has no project.json, so it goes into a small log on this computer
  (`%APPDATA%\MovieRecaps\history_log.jsonl`, the last 500) that `history.events()` adds (``log=True`` by default).
- **Turn off the PC when everything is done** tick box (not remembered on purpose: it must be ticked each time).
  When nothing runs or waits to run by itself in the whole app (this page, the Match cuts queue, every page's
  busy()), after something ran: a 60 s warning with Cancel, then `core/shutdown.turn_off()` — which never turns the
  PC off while Premiere's title has a "*" (unsaved), Premiere shows an export / render progress box, or Adobe Media
  Encoder is using the processor (open and idle does not count); then it says why and tries again every 30 s. The
  real `shutdown /s /t 0` is `shutdown.SHUTDOWN`, replaced in every test. Rows waiting for your buttons do not
  count as work.
- **Newest on top**: running first, then waiting (newest first), then finished (newest first); rows glide to their
  new place (my own small `GlideList` in download_tool.py — the lead may swap in the queue's ui/glide_list.py); a new
  row scrolls the list to the top. The run order stays the order of adding (one stage of one video at a time, FIFO).

**Just cuts** (6b): `tools/match_cuts/match_cuts/just_cuts.py` — `python -m match_cuts.just_cuts --raw RAW
--run-dir DIR [--work W] [--frame PNG] [--watermark PNG]`. It reuses the RAW-only run's pieces (raw_only.framing,
the probe / conform stages, pipeline.shots_of for the whole RAW, the usual write_premiere_xml + its validation +
apply_final for the RAW's own rate): one segment per shot, each framed on its main face, the export's Scene Edit
Detection cuts V1 / A1 at every kept shot change. No shot shorter than 0.5 s is cut out (a flash plays on). The audio
chain is on every A1 clip, the frame (the channel template with your headline, else templates\default.png) on V2, the
watermark on top. No captions, no competitor, no WRONG PAIR. extras\just_cuts.json holds the result. **No change to
model.py / common.py or any existing match_cuts module** (no stage cache is invalidated). In the app,
`flow.just_cuts_step` runs it in `<project>\cuts` (cuts-2 ...) and records it in project.json (`steps.edit.kind =
"just_cuts"`, `cuts`). **Add a competitor** on a finished Just cuts row writes the competitor into project.json, then
downloads / converts only the competitor (`roles=("competitor",)` in download_step / convert_step / convert_project)
and makes the full edit — the RAW is not downloaded again.

**Real titles** (7): `core/titles.py` + `ui/titles_block.py`.
- *Title from the post*: yt-dlp's info of the competitor link (already read when the folder is made) → `post_title()`:
  title, else the description's first line; no #tags, @names, links, at most 3 emoji (2 in a row), ≤ 90 characters
  cut at a word; "Video by darkhumor", "xyz on TikTok", "Reel" ... count as nothing.
- *Headline on the video*: four frames of the first ~3 s, read by the tool's Qwen3-VL-2B (action_captions.LocalVLM's
  loader) in **its own process** (`python -m mr_app.core.titles VIDEO`, one JSON line) after the conversion. Tests
  replace `titles.READER`. I ran it for real ONCE on finished\video3\competitor.mp4 (80 s with the model load): it
  read "They stopped making comedy like this" but on one line plus a stray "This". So: a stray repeated word at the
  end is dropped, and the line is broken where the video breaks it, using the existing OCR title check's lines (now
  kept in project.json `steps.title_check.lines`); when the model reads nothing, the OCR's lines are used.
- Both are shown on the rows with Copy; the headline also has **Use as my headline** (fills the row's two-line box,
  still editable — on a finished row it opens Change headline) and **Use as YouTube title**.
- The project folder: made from the post title when it says something (else the link id, as before) and, after the
  conversion, renamed from the headline on the video when the folder's name came from a useless title
  (`titles.rename_folder`: `<date>_<short-name>`, -2 when taken, Windows-safe via projects.short_name; a failed
  rename keeps the old name and says so).
- Saved in project.json (`post_title`, `video_headline`) and in **titles_found.txt** — NOT title.txt: title.txt is
  your headline / upload title and the board and the shared-folder merge read it back as yours (publish.py
  fold_title_txt), so writing the competitor's titles there would overwrite your title. Nothing readable → "nothing
  readable" in plain words, old name kept.

## Hooks left for the lead

- **Torrents (6c)**: `DownloadPage.start_torrent(item)` imports `mr_app.ui.torrent_panel`; without it the row fails
  with "torrent support is coming -- for now download it yourself and drop the video file into RAW". With it (written
  against the torrent helper's API as it stood at 08:30 in worktree agent-ac1e3672...): `TorrentPicker(source,
  save_dir)` is shown inside the row (`chosen(list)`, `cancelled()`, `failed(str)`), then `picker.start_job(indexes)`
  and a `TorrentCard(job)` inside the row; `job.progress(dict)` (share, percent, speed_text, seeders, eta_text) moves
  the Download bar, `job.failed(str)` stops the row red, `job.finished(video_path)` calls
  `DownloadPage.torrent_finished(item, path)`: the video becomes the RAW's file and the row goes on like a dropped
  file (into the project folder, Convert ...). The save folder: `core.torrent.default_dir()`. Tested with a fake
  panel (test_newvideo_ui.py); check it once with the real one after the merge.
- **Queue rows / board cards**: `ui/titles_block.TitlesBlock` — `from_project(project_json_dict)` shows both titles;
  connect `use_headline` / `use_title`. A board card can show "Just cuts" from project.json `kind == "just_cuts"`
  (or `steps.edit.kind`).
- **ui/glide_list.py**: my rows use their own `GlideList` (download_tool.py); swap when unifying.

## Files touched (merge care)

New: core/newvideo.py, core/titles.py, core/shutdown.py, ui/titles_block.py, match_cuts/just_cuts.py, tests
test_newvideo.py, test_newvideo_ui.py, match_cuts/tests/test_just_cuts.py.
Changed: tools/download_tool.py (rewritten), core/flow.py (roles, Just cuts, default template, Change headline, OCR
lines kept), core/download.py (a competitor's file placed like a dropped RAW), core/convert.py (`roles`),
core/history.py (Just cuts / default template events), tests/test_tabs.py (its two Download tests rewritten for the
new form — another helper may touch this file).

## Not done / decided

- Rows are not kept across an app restart (as before).
- The default template = the PSD's merged image as it is (its example headline). If that is not what "as it is"
  means, `flow.default_template` is the one place to change.
- A RAW file without a competitor makes Just cuts too (the spec names only a YouTube link; a file is the same work).
- "Turn off the PC" is not remembered (safer).
- Window thread: a few small project.json writes on button presses (send to edit, add a competitor), as the old tab
  did; everything slow (links, downloads, conversion, titles, the cuts, Premiere, the shutdown checks) runs in the
  background.

## Tests

`tools/app/tests/test_newvideo.py` (38: detection, Will make, batch, prefs, plain errors, order, titles with a fake
reader, rename, shutdown never real, default template + Change headline on the real PSD, Just cuts result, the Just
cuts step end to end on a synthetic video, history), `test_newvideo_ui.py` (11, real widgets offscreen: labels,
the headline box, buttons gating, Automatic, default template, Try again, Just cuts + Add a competitor, order,
shutdown warning, titles block), `tools/match_cuts/tests/test_just_cuts.py` (3: no mini cuts, a 4-shot synthetic
video end to end — 2 cuts, 3 clips at 30 fps, the audio chain on all 3, frame + watermark, no captions).
