# Day 16 — MovieRecaps: ONE TASK AT A TIME (PC, from now until about 15:00)

I can't answer. Never stop to ask me. When something is unclear, choose the safest sensible option, write it down in the report, and keep going.

- Repo: `C:\Users\gvida\Desktop\bh\MovieRecaps`. Branch: `claude/new-session-ol4era`.
- Shared folder: `D:\Shared\Projects` (the laptop: `\\192.168.101.11\Shared\Projects`).
- Read `reports\night15.md` first. Keep every earlier look and hover rule.

## 0. How to work — the same as night 15 (it worked well)

1. **ONE task at a time**, in the order below. Finish each one completely before starting the next:
   - build it
   - test it (all tests, plus the real-case test written in the task)
   - commit and push
   - add its line to `reports\day16.md`
2. If a task can't be finished, undo the broken part, write why, and go on. **The repo must never be broken when I stop you.**
3. **When I say "continue":** read `reports\day16.md`, `git status` and `git log`, and go on with the first task not done.
4. **While I'm using the PC** (input in the last 10 min, `GetLastInputInfo`): no Premiere, no stealing windows, heavy jobs at BelowNormal. Do code and light tests first.
5. **Never ask me to stop or wait.** Only a message from me pauses you.

## 1. Time and disk rules

- `Get-Date` at the start and after every task. At the start: `shutdown /a`, then a backup shutdown for 15:30.
- **Work until about 15:00, NEVER finish before 14:30.** If everything is done early, go to the last task (learn by yourself).
- **14:35:** stop starting new things. **14:35–14:55:** all tests, `.venv\Scripts\python tools\app\setup_app.py`, the report, commit and push. **About 15:00:** `shutdown /a`, then `shutdown /s /t 120` (not if I used the PC in the last 10 min).
- **Disk:** check C: and D: after every task. Below 40 GB free, clean up your own scratch first (`work\t2*`/`t3*` except `t10`, `work\snap*`, re-run folders), then `work\cache` older than 3 days that no test needs. Keep `work\t10`. Never delete my videos, projects, `input\`, `output\`, `finished\`, `D:\Shared` projects or fixed .prproj files.

## 2. The tasks — in this order

For each one: first check whether it's already done.

### Task 1 — "Try with another RAW" keeps my title and text
- After a run fails because of the RAW (WRONG PAIR, NOT IN THIS RAW, PICTURE NOT FOUND, a RAW that can't be read), the failed row gets a button **"Try with another RAW"**.
- It keeps **everything I already typed or chose**: the YouTube title, the template text (the headline, e.g. "The fact that…"), the channel and the competitor clip. I only pick or download the new RAW.
- Every failed project also keeps its title and template text in History, so I can reuse them later, also after a restart.
- **Test:** a real WRONG PAIR project (the Dolittle 1998 one), Try with another RAW → the title and text are already filled in.

### Task 2 — The zip has the WHOLE project folder
- Night 15's zip only had the edit files. I want **everything in the project folder**: `edit\`, `logs\`, the RAW (or its piece, see Task 3), the competitor clip, `project.json`, `template.png`, `title.txt`, `titles_found.txt`, everything, with the same folders inside.
- Videos are put in **without compressing them again** (store), so it's fast and lossless. The zip never contains itself or an older zip.
- Show the zip's size on the project card.
- Remake the zips of the existing finished projects the new way.
- **Test:** unzip one on another folder, import its XML in Premiere: no "Media offline".

### Task 3 — Only the movie piece in the project folder (scene ± 10 min)
- Today the analysis uses a piece, but at the end the piece is deleted, the XML plays the **whole movie**, and the whole movie stays in the project folder.
- **New:**
  - keep a piece of **the found place ± 10 minutes**, cut by stream copy (lossless, from the keyframe before) into the project folder
  - **the XML plays that piece** (every clip at the right frame inside it)
  - the **whole movie is NOT put into the project folder**; it stays where I downloaded it
- The analysis itself can keep using the current smaller margin (± 5 min films, ± 2 min episodes), so it stays as fast as now.
- A montage with places far apart: one piece per group. If the pieces would be more than ~60 % of the RAW, keep the whole RAW as now.
- Existing finished projects: don't change them.
- **Test:** one film (Benchwarmers or Borat) and one episode: import in Premiere, every clip frame-exact against the old XML (the original movie), the folder much smaller. Write the sizes before → after.

### Task 4 — SceneProof song: the right levels and effects
- On SceneProof edits the song on A2 must have, exactly like my own project:
  - before the cut (12.32 s): **+5 dB**, after the cut: **−1 dB**
  - on both song clips: **Volume Level 8.5 dB**, a **Hard Limiter** (Maximum Amplitude 0 dB, **Input Boost 2.00 dB**, Look-Ahead 7 ms, Release 100 ms, Link Channels 1.00), and **Amplify +3.00 dB**
- An XML can't carry Hard Limiter / Amplify or Clip Gain (night 11). So:
  1. make a second effect preset **"MovieRecaps song chain"** (Hard Limiter with Input Boost 2 dB + Amplify +3 dB), installed like the A1 preset, so I drop it on the two song clips
  2. put the +5 / −1 and the 8.5 dB into the XML in the way that sounds the same as my project (e.g. Volume +13.5 / +7.5 dB). **Measure it:** render my way and the tool's way with the preset on, and compare the loudness. If they differ, find the way that matches, and say in the report what I must set by hand.
- **Test:** import a SceneProof edit in Premiere (when I'm not using the PC), drop the preset, and compare.

### Task 5 — Find source with NexLev and Algrow
- **The API keys are in Windows user environment variables `NEXLEV_API_KEY` and `ALGROW_API_KEY` on this PC.** Read them from there. **Never write the keys into any file, log, report, commit, test fixture or screenshot.** In logs, write only "key found / not found".
- Find out how to call NexLev's and Algrow's APIs with these keys (their docs or MCP servers). What I need from them:
  - **NexLev:** watch a public Instagram / TikTok / YouTube clip with AI (picture AND speech) and answer a question: `watch_instagram_video_and_ask`, `watch_tiktok_video_and_ask`, `watch_youtube_video_and_ask`
  - **Algrow:** `analyze_video` (the same for YouTube / TikTok / Instagram; 1 credit per 4 min of video) and the free `fetch_transcript` (YouTube transcripts)
- **How Find source works then, for each clip:**
  1. ask NexLev to watch it: every spoken line, on-screen text, the actors and setting, and its best guess of the film or show + episode with how sure it is. If NexLev fails, ask Algrow `analyze_video`.
  2. check the guess: the episode title on TVmaze / IMDb, and when I have the RAW, the clip's sound in it (the existing quick check). Optionally, Algrow's free transcript of a YouTube clip of that scene, matching the lines.
  3. ✅ Sure only when checked; ⚠️ Check when it's only the AI's guess; ❌ Not found with the reason.
- Keep the same table and buttons as now (Clip | Comes from | S/E | How sure, Copy all, History), 5 at the same time.
- Each "watch" costs credits: don't watch the same clip twice (cache the answer by its link). Show "uses NexLev / Algrow credits" small on the page.
- **Real test (5 at once):**
  - `https://www.instagram.com/p/Dd1e2BmyHCX/` → The Dictator (2012), the helicopter scene
  - American Dad reel `DczA1q3MTge` → S03E02
  - the Dolittle 2 reel `DdVZePAga2I` → Dr. Dolittle 2 (2001)
  - one Family Guy and one more from my projects

### Task 6 — A RAW that can't be read is converted by itself
- The Ted 2 x265 runs still stop with "could not decode source frames by PTS". When a RAW can't be read like that, convert it by itself (once, NVENC, the day 14 way) and go on. Never stop for it.
- **Test:** the Ted 2 x265 file.

### Task 7 — Learn: my new fixes
- Learn everything open in `D:\Shared\Projects\learn_queue.md`: **video28–32** (ball, littleb, p[resd, farm, are you br .prproj) plus anything new.
- video29, video31 and video32 point to the **laptop's** `input\downloads`. Look for their run folders in `D:\Shared` / `D:\Shared\Projects` first. If they're not on this PC, write in the report exactly which folders I must copy over, and do the others.
- **video27 (blackads.prproj):** its captions are on V5 with the track's output switched off (the eye closed). Make learn read text graphics on a hidden track too (they're still my captions), and learn it again.
- The usual rules: only captions, cuts and video position/scale; add each as a test case; nothing may get worse; numbers before → after.

### Task 8 — Refine faster + the Benchwarmers S08 + the full check-all (one check-all for both)
- **Refine faster (night 15's design):** don't rescue-search frames whose sound the sound search already put outside the RAW (the other-video stretches), and stop the rescue rounds when a round finds nothing new. Benchwarmers: 52 min of rescue search, expected about 30 min saved. Also look for other safe savings (the graphics card, skipping frames already sure).
- **Benchwarmers S08:** a held + zoomed moment (the competitor holding 4 RAW frames and zooming) was read as a slow reverse and plays backwards. Make it a still (freeze frame) of the right RAW frame.
- Then run **one full check-all** for both changes: all 34 older test videos, the 7 from night 15 (video21–27) and the new ones from Task 7. Night 15 only finished 13. Run them all this time. Use up to 3 streams on the GPU when I'm not using the PC.
- Keep each change only if nothing gets worse. Show time before → after on Benchwarmers and 2 others.

### Task 9 — Learn by yourself (until 14:35)
- From the check-all, take the biggest remaining difference with my fixes and find a safe rule. Keep it only if nothing gets worse. Commit and push each one.

## 3. Not now / never

- Not now: Stats, idle learning, Topaz, the friend's sync folder, the cartoon speaker rule, installing a Premiere add-on (CEP) or changing the registry.
- Never: black cinema bars, thumbnails on cards, mouse spotlight, focus mode, sounds, before/after slider, money goal, streak, next-channel progress, drop zone glow.
- Never use my channels' logins, cookies or AdsPower / Multilogin profiles.
- Never delete my own projects, videos or fixed .prproj files.
- **Never write the NexLev / Algrow keys anywhere.**

## 4. The report — `reports\day16.md`, in plain simple English

Update it after **every** task. At the end it must have:
- each task: done / not done / already done, and how I use it
- time and sizes before → after
- the check-all table (before → after for every video)
- what's still broken
- what I must check by hand
- **laptop steps** (exact commands; remind me the keys are only on the PC)
- 5 new suggestions (saved in `suggestions.md`, not built)
