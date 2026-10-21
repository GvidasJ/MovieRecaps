# Night 15 — MovieRecaps: ONE TASK AT A TIME (PC, from now until about 15:00)

I can't answer. Never stop to ask me. When something is unclear, choose the safest sensible option, write it down in the report, and keep going.

- Repo: `C:\Users\gvida\Desktop\bh\MovieRecaps`. Branch: `claude/new-session-ol4era`.
- Shared folder: `D:\Shared\Projects` (the laptop: `\\192.168.101.11\Shared\Projects`).
- Read `reports\day14_quick.md` and `reports\day13.md` first. Keep every earlier look and hover rule.

## 0. How to work tonight — VERY IMPORTANT

**I may stop you at any moment** (I'm using the PC for a while, then I leave it overnight). So:

1. Work on **ONE task at a time**, in the order below. Never start two tasks at once, and never leave half-done changes in many places.
2. Finish each task completely before the next one:
   - build it
   - test it (all tests pass, plus the real-case test written in the task)
   - commit and push
   - add its line to `reports\night15.md`: done / not done, what changed, how I use it, what I must check by hand
3. Only then start the next task. If a task is too big, split it into small steps, and commit and push each step that works on its own.
4. If a task can't be finished, stop it cleanly: undo the broken part (`git restore` / revert), write why in the report, and go to the next task. **The repo must never be broken when I stop you.**
5. **When I come back and say "continue":** read `reports\night15.md`, `git status` and `git log`, then go on with the first task not marked done.
6. **While I'm using the PC:** before anything heavy (opening Premiere, a GPU render, a long check-all), check how long since my last mouse/keyboard input (PowerShell `GetLastInputInfo`).
   - Less than 10 minutes: don't open Premiere, don't steal windows, and run heavy jobs at **BelowNormal** priority. Do code and the light tests first, and do the Premiere tests later.
   - Before the shutdown at the end, check the same way. If I used the PC in the last 10 minutes, don't shut it down.

## 1. Time and disk rules

- Check the time with PowerShell `Get-Date` at the start and after every task.
- At the start: `shutdown /a`, then set a backup shutdown for 15:30 (calculate the seconds, then `shutdown /s /t <seconds>`).
- **Work until about 15:00, NEVER finish before 14:30.** If all the tasks are done early, go to task 16 (learn by yourself).
- **14:35:** stop starting new things. **14:35–14:55:** run all tests, run `.venv\Scripts\python tools\app\setup_app.py`, finish the report, commit and push. **About 15:00:** `shutdown /a`, then `shutdown /s /t 120` (unless I'm using the PC, see 0.6).
- **Disk:** C: has only about 74 GB free. Check C: and D: after every task. Below 40 GB free, clean up until 60 GB is free:
  1. first your own scratch (`work\t2*` except `t10`, `work\snap*`, helper worktrees, re-run folders; `work\cleanup_day14.ps1` lists some of them),
  2. then `work\cache` older than 3 days that no test or open item needs.
  
  `work\t10` is used by test case video5 — keep it. Never delete my videos, projects, `input\`, `output\`, `finished\`, `D:\Shared` projects or fixed .prproj files.

## 2. The tasks — in this order, one at a time

For each task: **first check whether day 13/14 already fixed it.** If it's fixed, test it on the real case, write "already fixed" in the report, and go on.

### Task 1 — Premiere "generic error" / "File Import Failure" on XML import
- It's very likely caused by **emoji in folder and file names** (e.g. `2026-10-21_The Most Dramatic Dad of All Time 💀😭`).
- **Folder and file names:** keep the title text, but use only letters, numbers, spaces and `- _ ( )`. The title with emoji stays in project.json, title.txt and the board.
- Rename the existing emoji folders safely (nothing running, every link kept).
- Check every `pathurl` is encoded correctly (spaces, `[ ]`, non-English letters).
- **Test:** import real XMLs in Premiere 2024 on this PC. No error.

### Task 2 — WRONG PAIR stops right pairs
- Real cases: **Dr. Dolittle 2 (2001)** (YTS RAW) + `tiktok_cultureratedpv_7357048330179841323.mp4` and the Instagram reel `DdVZePAga2I`; **Are We There Yet** (failed via New video, passed via Match cuts).
- Find why the check gets 0 %. **If the check isn't sure, go on, don't stop.**
- Real wrong pairs must still be caught: the Jumanji promo, and the wrong Family Guy episodes.
- Add them as test cases.

### Task 3 — Cartoons and tilted/zoomed reels don't match
- Real case: **American Dad S03E02** + reel `DczA1q3MTge` (Flick707). The sound found 94 % (RAW 292.7–352.9 s) but the picture got 0 % after 2 hours. The reel is rotated ~5° and scaled 0.79. The right frames were found with s=0.735 but rejected.
- Cartoons fail often in general: flat colours, thin lines, burned-in captions, episodes that look alike.
- Fix:
  1. **Cartoon mode:** compare by lines and shapes (edges) instead of only colour, and mask the burned-in caption text.
  2. **Rotation/zoom:** test ±8° and the scale on a few frames when the sound is found but the picture isn't.
  3. **Trust the sound window:** accept lower picture scores (e.g. ≥ 0.65) inside the place the sound found, if neighbouring frames agree in order and timing.
  4. **Fail fast:** stop within minutes, not hours.
  5. **Never nothing:** if the sound is found but the picture isn't, still make the project with the RAW part placed by sound, marked "placed by sound — check the framing".
- **Test:** American Dad, the Family Guy cases, and normal films. No new wrong matches in check-all.

### Task 4 — Honest progress
- Time left: estimate from earlier runs on THIS computer, per stage, smoothed (≤ ~20 % change per update), shown as a range ("about 25–35 min").
- **The % never goes backwards.**
- **A stuck step** (5 min with no output and no CPU) says "seems stuck — Try again?".

### Task 5 — SceneProof template + its music
- My examples are in `docs\sceneproof\`: `song.prproj`, `blackads.prproj` and two screenshots.
- **The look:**
  - white background, round profile photo, "SceneProof" with a blue verified badge, the `@SceneProof` handle (grey), and the bold black "The fact that…" text centred under it
  - the header picture is `ScenmeProof_00000.png` (in `D:\Shared\Comp 1\` and in `Copied_*_POPW` folders)
  - tracks as in my projects: V1 RAW, V2 Color Matte, V3/V4 header + top-text picture, V5 captions
- **Template choice:** add "SceneProof" as a template I can choose in the app (New video, Match cuts, Settings), next to the current one. The current one stays the default.
- **The music — always on SceneProof projects:**
  - the file: `One Piece OST - Mother Sea (First Part) Extended Version.mp3`. It's in `D:\Shared\Copied_*_POPW\`. Copy one into the app's assets so it's always found.
  - put it on **A2**, starting **0.52 s into the song**
  - split it at **12.32 s**: before that **+5 dB**, after that **−1 dB** (clip gain, exactly as in `song.prproj`)
  - end it where the video ends
  - **A1 stays untouched** (my voice and my audio chain).
- **Test:** make one SceneProof project from a real video, open it in Premiere, and compare it with `song.prproj`: same look, music on A2 with the same volume.

### Task 6 — Pause / Resume
- A **Pause** button on the match-cuts run in progress, and Resume to continue from the same stage (not from the start).
- Pausing must not break the cache. It must still work after I close and reopen the app.

### Task 7 — Auto zip + renders by day
- Every finished project gets a `.zip` automatically (to send to my friend): the XML, the project, the media it needs (not the huge movie if it's on the shared drive; say what's left out), and a short "how to open" text.
- My Media Encoder renders get sorted into folders by the day they were made (e.g. `renders\2026-10-22\`), the old ones too. Never delete any.

### Task 8 — TV episodes cut into pieces too
- Like the movie pieces: the sound places ± 2 min, the whole episode if the pieces are more than 60 %. The XML points to the original episode.
- Show time before → after on one episode.

### Task 9 — Speed
- Captions (speech recognition) running at the same time as the picture search.
- Speech recognition done once, not twice.
- Show time before → after on 2 real runs. Nothing may get worse.

### Task 10 — Torrent ".aria2 control file" error
- Check existing files (`--check-integrity=true`), keep the correct pieces, never truncate a finished file, and show every aria2 error in plain words.

### Task 11 — Ctrl+F search
- In the torrent file list: a live filter, episode search (S09E05 / 9x05 / "ep 5"), "Tick only these", and a "12 of 24" count.
- On every page: a search box, Esc clears it.

### Task 12 — Episode / movie finder
- I give a competitor clip (link or file). The app tells me **which movie or episode** it comes from: only the name (and season/episode), not the exact part or frame. I download the RAW myself.
- Use what's possible without my logins: the post's text, burned-in text, speech → subtitle search, the picture → known titles. Show how sure it is.
- **Test:** the Dolittle 2 reel, American Dad, and a Family Guy clip.

### Task 13 — Suggestions I liked
1. The audio chain put on A1 by itself in Premiere: Normalize All Peaks 0 dB → Volume 0 dB → Hard Limiter (0 / 0 / 7 ms / 100 ms / 1.00) → Amplify +3.0 dB → Panner 0. If it isn't possible, explain why.
2. A list of the pauses to take out.
3. Captions timed on the sound that 1_edit.xml plays.

### Task 14 — Learn queue + problems
- Everything open in `D:\Shared\Projects\learn_queue.md` and `problems_queue.md`: only captions, cuts and position. Nothing may get worse.

### Task 15 — Speaker framing in cartoons
- Only if it's safe: it must never move a clip onto the wrong character. Otherwise keep it off and explain.

### Task 16 — Learn by yourself (until 14:35)
- Re-run check-all, take the biggest remaining difference with my fixes, and find a safe rule. Keep it only if nothing gets worse. Commit and push each one.

## 3. Not now / never

- Not now: Stats, YouTube API, idle learning, Topaz, the GPU queue, the friend's sync folder.
- Never: black cinema bars, thumbnails on cards, mouse spotlight, focus mode, sounds, before/after slider, money goal, streak, next-channel progress, drop zone glow.
- Never use my channels' logins, cookies or AdsPower / Multilogin profiles.
- Never delete my own projects, videos or fixed .prproj files.

## 4. The report — `reports\night15.md`, in plain simple English

Update it after **every** task (not only at the end), so it's right whenever I stop you. At the end it must have:
- each task: done / not done / already fixed, and how I use it
- time before → after
- what's still broken
- what I must check by hand
- **laptop steps** (exact commands)
- 5 new suggestions (don't build them; save them in `suggestions.md`)
