# Day 12 — helper "e2e": the real New video flow end to end

Real yt-dlp downloads (no cookies, no logins), the real conversion, the real Qwen3-VL-2B headline read, the real
`match_cuts` run (`--fast`, as the app does) and the real Just cuts. Only the PC shutdown was replaced (0 calls).
Driver: `work\t27\shots\e2e\e2e_drive.py` (offscreen window, every state file in a scratch folder: settings, save_to
hub, projects root, MOVIERECAPS_APPDATA, torrents, %APPDATA%; asserted before the window is built). Pictures and logs:
`work\t27\shots\e2e\` (p1.out / p23.out / p2b.out are the full logs), the fixed Just cuts → Add a competitor rerun in
`work\t27\shots\e2e\rerun_p2\`. The GPU was shared with the lead's check-all + a re-run stream the whole time
(one GPU process of mine at a time), so the GPU times below are slow.

## 1. Full edit, Automatic OFF (RAW 0IKJEnLg7G8 + Short f18B0Accoig, LouLaughs, two-line headline with 😭)

| step | time |
|---|---|
| Download (both links read, 6.3 MB + 1.6 MB) | 9.4 s (31.6 s on the first try: the links are read twice, ~5 s each) |
| waits for **Convert** (checked 3 s: nothing ran) | — |
| Convert (both already H.264 MP4: kept) + OCR title check 9 s + Qwen headline read | 125.9 s (57.6 s on the first try; the read alone 47 s → ~110 s under GPU sharing) |
| waits for **Send to Match cuts** | — |
| Template (LouLaughs PSD, your break kept, shrunk to 81 %) | < 1 s |
| queue: **Check which is the RAW** (26 s Short vs 30 s RAW — within 30 %) → picked the RAW | — |
| the edit (`match_cuts --premiere --fast --force-inputs --no-swap`, frame + watermark) | 937.8 s (15m37s), **PASS, exit 1** |

- Title from the post: "Before Tobey Maguire Was Spider-Man He Had One Job 🕷️📺". Headline on the video (after the fix):
  "In 1996, Tobey Maguire had exactly one second of screen time in a Tropicana Twister commercial." (4 lines on the
  video: kept as one line). Folder: `2026-10-19_before-tobey-maguire-was-spider-man-he` (from the post title).
- project.json: post_title, video_headline, roles {chosen, picked}, steps with dates; titles_found.txt both; title.txt =
  your headline with its break. 1_edit.xml 111 KB. Waiting for your fix card in the hub (learn_waiting.json), no
  problems_queue.md (PASS, no note) — correct. Board card in EDIT MADE with both titles; queue row PASS; History:
  Link added / Downloaded / Converted / Template made (shrunk note) / Edit made · PASS, each with its date.

## 2. Just cuts, Automatic ON (the same RAW only) → Add a competitor

- Download + convert + **Cuts by themselves: 8.0 s / 9.4 s** (cuts 3.5 s): 11 cuts, 12 clips (shortest 0.8 s), sequence
  30 / ntsc TRUE (the RAW's 30000/1001), the audio chain (Gain, Audio Levels 1, Audio Pan 0 — the same as the full
  edit's A1) on all 12 A1 clips, frame on V2 (default template, no headline), watermark on V3. Row: "Just cuts",
  "Cuts ready", "Cut at 11 scene changes".
- Add a competitor (link) → competitor download 1.5 s, convert + headline read 255-302 s (GPU sharing), default
  template, the queue's pick, edit 214-294 s (RAW stages cached in work\) — PASS, exit 1. **The RAW was not
  downloaded again** (its file's time stayed 11:02:05 / 11:13).

## 3. Broken and private links

- `watch?v=xxxxxxxxxxx`: 1.5 s → red FAIL, "Stopped at download: RAW: this video is not available: it was removed or
  made unavailable (or the link is wrong)", Try again → the same in 2.1 s.
- `watch?v=s7_qI6_mIXc` (private): 1.2 s → "RAW: this video is private: only its owner (and people they shared it
  with) can watch it", Try again 0.9 s. All four in History with dates (history_log.jsonl: no folder was made).

## Found and fixed (commits on this branch, with tests)

1. **The headline on the video was garbage on a tweet-style Short**: the 2B model copied the account name and @handle
   ("History Exposed / @HistoryyExposed In 1996, …") and stuttered ("Tobeyey", "had had", "one one", "time time").
   `titles.tidy_headline` now drops a leading account name + @handle and @handles, and says a doubled word once;
   new `titles.settle(model, ocr_lines)`: when the model and the title check's OCR read the same text (≥ 0.8 alike,
   not equal) the OCR's words win (the model's emoji kept at the end), then the break where the video breaks it. The
   OCR alone when the model reads nothing (a 2-line OCR keeps its break; more lines: one line).
2. **A full edit from New video sat for ever with "In the Match cuts queue — it starts when the queue gets to it"**: the
   26 s Short and the 30 s RAW are within 30 % and yt-dlp's names say nothing, so section 4's amber "Check which is the
   RAW" card waits in the queue — but the Download row did not say so. Now the row says "Which one is the RAW? … pick
   the RAW on its row in the Match cuts tab", amber, pill "Check which is the RAW" (`download_tool.waits_for_pick`).
   Decided NOT to count the form's fields as your pick (night 10's `_longer_is_raw` also does not trust the fields);
   the lead may decide otherwise (then `flow.set_roles(folder, raw_file, "form")` before `edit_job`).
3. The pick buttons and the pair line cut long downloaded names ("fore Tobey … is the RA"): `short_file_name` shortens
   in the middle and keeps the `[id].mp4`.
4. **Add a competitor**: project steps merge their details, so the full edit's step kept `kind: just_cuts` and
   `cuts: 11` — History said "Just cuts made · PASS · 11 cuts" for the full edit (and lost the real Just cuts event),
   the queue row showed "Just cuts". Now `newvideo.keep_just_cuts` moves the old step to `just_cuts_before` (History
   shows it), the edit step starts empty, and `is_just_cuts` trusts the project's `kind` first.
5. Add a competitor showed "Title from the post: nothing readable" (its link is not read by `_create`): the title
   yt-dlp stored in project.json `competitor.title` is used.
6. A project job without a headline said "NO TITLE yet (press Template)" and listed "has no title yet" before the
   start, though the run uses the channel's default template: now "its default template (no headline …)", no warning.

Verified by the rerun (rerun_p2): History "Just cuts made · PASS · 11 cuts" then "Edit made · PASS", no Just cuts pill on
the queue row, the post title shown.

## Seen, not changed

- The links are read twice (once to name the folder, again in download_step): ~5-10 s per video.
- The Qwen headline read: 47 s with the GPU nearly free, 2-5 min while check-all runs (READ_TIMEOUT_S 600 s). It runs
  inside the Convert bar, so "Convert ×2" finishes minutes after the files are converted.
- The folder name from the post title is cut at 38 characters mid-phrase ("…-spider-man-he").
- exit 1 = finished with things to look at (here 23 captions worth a look); PASS label.

## Tests

App suite: 940 passed, 5 skipped (own --basetemp, CUDA hidden, this worktree's mr_app). New tests: test_newvideo.py
(tweet headline / settle, keep_just_cuts + History, default-template line), test_newvideo_ui.py (the row says the
queue waits for your pick, Add a competitor's post title), test_queue_day12.py (short_file_name).

Files touched: core/titles.py, core/newvideo.py (appended), core/history.py, tools/download_tool.py,
tools/match_cuts_tool.py (short_file_name, template_line / template_problems), ui/titles_block.py (is_just_cuts).
