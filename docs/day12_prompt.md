# Day run 12 — MovieRecaps (PC, from now until about 15:00)

I'm away and can't answer. Never stop to ask me. When something is unclear, choose the safest sensible option, write it down in the report, and keep going.

Repo: `C:\Users\gvida\Desktop\bh\MovieRecaps`. Branch: `claude/new-session-ol4era`.
Visual reference: `docs\ui_preview.html` (open its CSS/JS; the real app is PySide6/Qt, so rebuild the look in Qt, never embed a web page). Keep every night-11 look and hover rule (fades only, in ~0.3 s / out ~0.5 s, nothing moves or jumps, the window never freezes).

## 0. Time rules — work until about 15:00, NOT earlier than 14:30

- Check the time with PowerShell `Get-Date` at the start and after every finished section.
- At the start: `shutdown /a`, then set a backup shutdown for 15:30 local time (calculate the seconds from now until 15:30 and run `shutdown /s /t <seconds>`).
- Read `reports\night11.md` first so you know where things stand.
- Make a task list from the sections below and work **in this order**. Commit and push after every finished section. Never leave the repo broken. Keep all existing tests passing and add tests for new logic.
- If the main list is done early, do **NOT** write the final report yet. Keep going with the **EXTRA WORK** list (section 12).
- **14:35:** stop starting new things and finish what is running.
- **14:35–14:55:** run all tests, run `.venv\Scripts\python tools\app\setup_app.py`, write `reports\day12.md`, then commit and push.
- **About 15:00:** `shutdown /a`, then `shutdown /s /t 120`. Never finish before 14:30.

## 1. Learn from the learn queue

- Do the normal LEARN step for everything open in `learn_queue.md` (in `D:\Shared\Projects`). Also read `standing_rules.md` and `requests.md` there.
- **video13 / video14 (ebola, Meet the Blacks):** if both exist, video13 was a wrong version. Its V1 was a rendered video, so don't learn cuts or position from it. Learn captions only from video13, and everything from video14. If only one exists, check its V1: when V1 is one rendered mp4 and not RAW clips, learn only its captions.
- As always: learn only captions, cuts and video position/scale. Ignore the headline PNG, memes, music, all audio, pitch, and my trims at cut points and the ending.
- Run check-all before and after, and report the numbers. Nothing may get worse.

## 2. Problems list (the tool keeps learning from its own problems)

- From now on, every run that ends FAIL or "OK with a note" is added by itself to `problems_queue.md` in the shared projects folder. Notes include picture not found, a flash, sync / repeat notes, caption problems, the person speaking outside the window, and crashes. Each entry has: video name, run folder, date, computer, the failed check or note, and the key log lines. Keep the run's files so it can be re-run.
- WRONG PAIR and input-check stops are **not** added, because those are wrong input, not a tool problem.
- Learn tab: a **"Problems to fix"** box with the list, a count, **Open log**, and **Skip (my mistake)** on each entry.
- **Copy tonight's prompt** includes the open problems together with `learn_queue.md` and `requests.md`.
- When a night or day run works on the list: re-run each open problem, find the cause, fix it, add it as a test case, and run check-all. A fix must not make any other video worse; if it does, undo it and explain. Mark each entry "fixed <date>" or "not fixed — <reason>".
- **Add these two now and work on them in this run:**
  1. `C:\Users\gvida\Desktop\bh\MovieRecaps\input\downloads\2026-10-19_video-by-mindculpt\edit` (laptop, Rush Hour). FAIL: XML PERSON. The person speaking is outside the template window at 00:00:07:41 (clips S06–S34) and at 00:00:27:03 (clips S35–S39), and nothing was re-framed. Find out why, and whether the tool should re-frame by itself when the face is outside the window (only if it doesn't put clips onto the wrong person — check against my earlier fixes).
  2. `C:\Users\gvida\Desktop\bh\MovieRecaps\input\downloads\2026-10-19_video-by-darkhumor\edit` (laptop, Family Guy S10E18). The run stopped with **exit 3**, and the message shown was only an OpenCV warning. Find the real reason. Make sure the app always shows the real reason in plain words, never just a library warning.

## 3. One RAW file instead of two

- When the RAW has 5.1 (or any non-stereo) sound, the run folder now has two big files: `raw_ae.mp4` and `raw_stereo.mp4`. Make only **one**: copy the video stream as it is (`-c:v copy`, no re-encode, same quality) and mix the sound down to stereo (AAC, the same as now). Use this one file for both V1 and A1 in `1_edit.xml` and the Premiere project.
- Never keep a second copy of the RAW in the run folder. Stereo RAWs stay as they are.
- Board → **Clean up**: for old runs that have both files, make the single file, point that run's `1_edit.xml` and project at it, check that it opens, and only then offer to delete the two old files. Ask first and show the space saved. Never touch my own fixed .prproj files.
- Check in Premiere that picture and sound stay in sync, the audio chain still works, and Learn still finds the run.

## 4. RAW vs competitor — always my choice

- Every pair in the queue shows which file is which, with name and length, e.g. "RAW: episode.mp4 (22:41) · COMP: short.mp4 (0:58)".
- Add a **⇄ Swap** button on the row, and right-click → "Swap RAW and competitor". If the run already started, Swap stops it and starts again with the files switched, without copying the files again.
- When the two lengths are within 30% of each other and the names don't say (raw* / comp*), the row does **not** start by itself, not even with Automatic ON. It turns amber, "Check which is the RAW", with two buttons: "<file A> is the RAW" / "<file B> is the RAW". Remember the choice in project.json.
- A RAW that's only a little longer than the competitor (like a 47 s RAW for a 45 s Short) is allowed once I've chosen. Don't stop it with the "looks like a copy" check after I picked; that counts as Run anyway.

## 5. Newest on top everywhere

- Match cuts queue, Download and New video rows: running rows always at the very top, then waiting rows (newest first), then finished rows (newest first). A row that changes state glides to its new place, with no jump. When a new row appears, scroll the list to the top.
- Board: the newest card on top in every column; pinned ★ cards stay above.
- History, Learn "Waiting for your fix", "Problems to fix" and Suggestions: newest first.
- This does **not** change the run order. Videos still run in the order I added them, or the order I dragged them into.

## 6. New video form + torrents + step buttons

**6a. One "New video" form** (in Download) with:
- **RAW**: a YouTube link, OR a magnet link / dropped .torrent, OR a video file. Recognize the type by itself and show a small label (YOUTUBE / TORRENT / FILE).
- **Competitor (optional)**: a link or a file.
- **Channel**: MikeMemesYou / LouLaughs / Flick707 buttons with their color dots. Remember the last one.
- **Headline**: one box with two lines (Enter = line 2), emoji OK, and a small "READY" label when filled. The line break I type is where line 1 ends and line 2 starts; never re-split it. If I type it without Enter, split it like night 10's renderer does. If a line is too wide, shrink the font the same way.
- **YouTube title (optional)**.
- A "Will make" line that says what it will make before I press anything: **Full Match cuts edit** or **Just cuts (every scene change)**.
- Batch: many pairs, one per line ("RAW link | competitor link").

**6b. The 3 ways:**
- **YouTube + competitor** and **magnet + competitor** → download both, convert both (x264 CRF 13, as now), template, project and board card, then the Match cuts edit.
- **Only a YouTube link** → "Just cuts": download, convert, then a Premiere edit (1_edit.xml + project, like a normal run) of the whole video, cut at every detected scene change (no mini cuts of just a few frames). Sequence at the video's own frame rate, the audio chain on every audio clip, and the channel's template if a headline is given. No captions, no competitor matching, no WRONG PAIR check. The row and the card show "Just cuts". **Add a competitor** on the finished row turns it into a full run without downloading again.
- **Magnet without a competitor** is not allowed: ask for the competitor.

**6c. Torrents:** use libtorrent (the engine qBittorrent uses), or aria2c only if libtorrent can't be installed for this Python.
- Before downloading, show the file list with sizes, the total size, the seeders and the free disk space. Preselect the largest video ("BIGGEST VIDEO"), then a Start button.
- While downloading, show live seeders and peers, speed, % done, time left, a small health meter (more seeders = more bars), and Pause / Resume / Remove. It continues after an app restart.
- Stop seeding when finished (with a Settings switch).
- Save to `D:\Shared\Downloads\Torrents` (changeable; the laptop uses `\\192.168.101.11\Shared\Downloads\Torrents`).
- Check free space before starting.

**6d. Steps wait for my buttons, unless Automatic is ON:**
- Download finished → **Convert** and **Open folder**. Nothing converts until I press it.
- Convert finished → **Send to Match cuts** (or **Make the cuts** for only-YouTube) and **Open folder**. Then template and edit run by themselves.
- The row's bottom line says what it's waiting for ("Downloaded — press Convert when you want").
- Each step has its own gliding progress bar and its finish time, green when done and blue while running.
- An **"Automatic: ON/OFF"** switch at the top of Download, OFF by default and remembered after a restart. ON means every step runs by itself, and rows already waiting continue at once.
- Next to it, a tick box **"Turn off the PC when everything is done"**: after the last download, convert or edit in the whole app, show a 60-second warning with Cancel, then shut down. Never while Premiere has unsaved work or a render is running.
- **No headline + Automatic ON:** don't stop. Use the channel's default template as it is (template.psd unchanged) and finish the edit. Mark it amber "default template", with a **Change headline** button that makes only template.png again and swaps it into the edit, with no new run.
- **No headline + Automatic OFF:** the Template step turns amber, "needs a headline", with the same two-line box and a **Make the template** button on the row.
- This also applies to the existing Download tab, which today converts and sends by itself.

**6e. Clear errors in plain words:** broken link, private or removed video, no peers for a long time, disk full. If a step fails, the row stops with a red pill and a **Try again** button that continues from that step. Everything goes into History with dates.

## 7. Real titles from the competitor's video

- Today's titles like "Video by darkhumor" are useless.
- Get the post's own caption/title with yt-dlp (Instagram, TikTok, YouTube: title + description). Clean it: remove #hashtags, @names, links and emoji spam, and keep it short.
- Read the headline written **on** the competitor's video: look at the first ~3 seconds (several frames) with the vision model the tool already has (Qwen3-VL), keep the line break as two lines, and keep emoji if readable.
- Show both on Download / New video rows, queue rows and board cards: **Title from the post** and **Headline on the video**, each with **Copy**. "Headline on the video" also gets **Use as my headline** (fills the two-line box, which I can still edit) and **Use as YouTube title**.
- Name the project folder from the headline or the post title instead of "video-by-<user>": short and safe for Windows. Save both in project.json and title.txt. If nothing can be read, say so plainly and keep the old name.

## 8. Learn waits by itself

- As soon as an edit finishes (PASS or OK with a note), add a card to the Learn tab under **"Waiting for your fix"**. It has the run, RAW, competitor, channel and the next free name already filled in.
- I only drop my fixed .prproj onto that card, or save a .prproj into the project's folder (notice it within a minute). Then show the check card at once, with the run already chosen, and I press **Learn this one**.
- When a render of that project appears (the board card moves to Rendered), the waiting card turns amber, "Rendered — add your project file", and the Learn tab shows a small count.
- Each card has **Skip**. It works on the PC and the laptop through the shared folder.

## 9. Suggestions for me

- At the end of the report, write 5–10 ideas to improve the app, from what you saw today: slow parts, things that fail often, things I do by hand.
- For each one: one plain sentence, why it helps me, and how long it would take (small / half a night / a full night). Put the most useful first. **Don't build them.**
- Also save them in `suggestions.md` in the shared projects folder. In the Learn tab, show them with **I want this** (moves it into requests.md for the next run) and **No** (removes it).

## 10. Not wanted / not now

- Not now: Stats tab, YouTube API sign-in, idle learning, speed changes to the match-cut search, Topaz.
- Never: black cinema bars, thumbnails on cards, mouse spotlight, focus mode, sounds, before/after slider, money goal, streak, next-channel progress, drop zone glow.
- Never use my channels' logins, cookies or AdsPower / Multilogin profiles.

## 11. Finish (14:35–14:55)

1. Run all tests. Click through every tab and every new button in the real app.
2. Run `.venv\Scripts\python tools\app\setup_app.py`.
3. Write `reports\day12.md` in plain, simple English:
   - what's done, and what's not done and why
   - learn numbers before → after
   - the two problems from section 2 and what was found
   - anything I must check by hand
   - **laptop steps** (exact commands)
   - the **suggestions** list
4. Commit and push.
5. Run `shutdown /a`, then `shutdown /s /t 120`.

## 12. EXTRA WORK (only if sections 1–9 are done before 14:35)

Do these in order, one at a time, and commit and push after each:
1. Any other open items in `problems_queue.md`.
2. Caption accuracy: run check-all and improve the captions that are most often wrong. Keep a change only if no video gets worse.
3. Use everything built today in the real app (every button, drag and drop) and fix what's broken or slow.
4. Re-run the last 10 real videos in `D:\Shared` and `input\downloads` with today's tool. Fix any FAIL or note that's the tool's fault.
5. Anything over 100 ms on the window's thread goes to the background.
6. More tests for anything that has none.

In the report, write what was done from this list and what's left.
