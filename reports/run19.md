# Run 19 (26 October): one task at a time

Started 06:34 (PC; you had just written the prompt, then you were away from about 06:35). `shutdown /a`: "no shutdown was in progress"; the backup shutdown is set for **15:30** (`shutdown /s /t 32136`). C: 48.1 GB free at the start, D: 1528.2 GB.

## The tasks

| # | task | state | how you use it |
|---|---|---|---|
| 1 | Only ONE RAW per project, the edit plays the whole movie | **done** (film + episode real runs PASS, frame-exact) | nothing for new edits; your old ones: Board -> Clean up -> Yes ("One RAW per project": 19 run folders ready, 4 kept for your r1/r2_POPW projects) |
| 2 | No temporary extra Volume effects | **done** (the Premiere check: see the task -- it waits for you to be away) | Open in Premiere, Ctrl+S, as before; the row says when the sound isn't set yet |
| 3 | The live animations | **done** (watched on a real run; pictures in `run19_look`) | nothing: the running video moves by itself; Settings -> Animations: Off stops the movement |
| 4 | The laptop cleans its own cache | not started | |
| 5 | Learn the queue | not started | |
| 6 | The check-all + learn by yourself | not started | |

## Task 1 -- only ONE RAW per project, and the edit plays the whole movie (done)

**Why the Guessing Game played the piece (the bug):** a run on a long RAW analyses only a piece of it (day 14: a film over 15 min, an episode of 15-70 min, when the sound check finds the place), and at its very end moves 1_edit.xml onto the movie ("piece of the movie: 1_edit.xml now plays the movie itself"). Before that move, the run puts the channel's song on A2 (it is added after the checks). The move checks the moved XML again -- and that check counted the song on A2 as "doubled audio" under A1. So the move failed every time a song was there; the run kept 1_edit.xml playing the piece (`raw.mov`) and left the movie's link it had already made (`raw.mp4`) next to it, unused. In D:\Shared that link became a real 7.1 GB copy, and the zip held both. The app showed PASS with no note (the warning came after the checks the app reads), and the log line "V1 plays the original RAW (extras\media\raw.mov ...)" was wrong: it was the piece.

- **Since which commit:** `9f0fafc` (22 Oct, night 15: SceneProof's song) for SceneProof; since `0ac628a` (25 Oct, run 18: the song on every channel) for MikeMemesYou, Flick707 and LouLaughs too. The movie piece in the project (keep_piece, `9757c5b`) had nothing to do with it -- it stays OFF.
- **Which cases:** every SceneProof / MikeMemesYou run on a piece since 22 Oct -- 23 run folders, 11 projects on this PC + 12 in D:\Shared (all of them: the same "A2 MUSIC ... doubled audio" error in their logs): Weight Loss Class, Police Officers In Scottish Comedy, Flight In British Comedy (3), Strategic Team Pick (5), Willing Victim, Worst First Word, Police Chase, **Hardest Guessing Game**, Awkward Helicopter Ride, Smoothest Office Flirt (shared copy only), Weirdest Car Shopping, Worst Thing to Say on a Plane. The 14 other projects with a piece were moved onto the movie correctly (their logs say so).

**The fix** (tool):
1. The song is never "doubled audio" with your voice (it plays under A1 on purpose); only its own two clips must not overlap each other. Any other sound under A1 is still caught.
2. If the move onto the movie ever fails for another reason, it removes the movie link it made -- a run folder never holds the movie AND its piece -- and the app's row says it: **OK with a note: "1_edit.xml plays only the piece of the movie the run analysed (37:54-48:15 of 1:49:31, extras\media\raw.mov), not the movie itself: ..."** (written into `extras\excerpt.json`).
3. The log says what V1 plays while the run works: "V1 plays the piece of the movie the run analysed, for now (...) -- at the end of the run 1_edit.xml is moved onto the movie itself".
4. No cached result changed (every stage's cache key is the same): nothing is analysed again.

**One RAW:** after a run, `edit\extras\media` holds one RAW -- the whole movie, `raw.mp4` -- and 1_edit.xml plays it. On this computer that `raw.mp4` is **not a copy**: it is your RAW file itself under a second, plain name (a hard link: no extra space -- Explorer adds it twice to the folder size, the disk doesn't; Premiere's XML import fails on some file names, and the edit keeps working if you move or delete the RAW in the project folder). Publishing to D:\Shared copies it **once** (`edit\extras\media\raw.mp4`, your RAW's own file stays on this computer, as before) and the shared 1_edit.xml points at it; the zip holds it once. (I kept the plain-name link instead of making the XML point at your RAW's own name: written down under "What I decided".)

**The real test** (07:03-07:14, you were away): the Guessing Game (Dumb and Dumber To, 109.5 min, x265 10-bit, 7.1 GB) and an episode (Strategic Team Pick (5): It's Always Sunny S02E06, 22.9 min), run as the app runs them (the same command, your inputs hard-linked into `work\t19\projects`: nothing of yours touched), then published to a TEST shared folder (`D:\mr_t19\Shared`, not your board) and zipped:

| | the Guessing Game (film) | the episode |
|---|---|---|
| the piece analysed | 37:54-48:15 of 1:49:31 | 4:44-9:13 of 22:53 |
| 1_edit.xml plays | **raw.mp4 = the whole movie** (before: raw.mov, the piece) | **raw.mp4 = the whole episode** (before: raw.mov) |
| frame-exact against your old XML | all 8 clips (V1 4, A1 4) = old frame + 54523 | all 26 clips (V1 13, A1 13) = old frame + 6816 |
| RAW files in the project folder | 1 (raw.mp4, your RAW's hard link) | 1 |
| RAW files in its shared copy | 1 (raw.mp4, 7.11 GB), the XML points at it | 1 (0.54 GB), the XML points at it |
| RAW files in the zip | 1 | 1 |
| this computer's folder, on disk | 8.45 GB -> **7.78 GB** | 1.13 GB -> **1.02 GB** |
| the shared copy (without its zip) | 8.44 GB -> **7.78 GB** | 1.12 GB -> **1.02 GB** |
| the zip | 8.41 GB -> **7.74 GB** | 1.10 GB -> **1.00 GB** |

(Before = your project as it is now; after = the same run with the fix. GB = 1,000,000,000 bytes, as your "7.1 GB". The "after" folders still hold A1's sound file made to the movie's frames: silence up to the piece, then its sound -- 0.53 GB for the Guessing Game; that is day 14's design: the XML's sync check needs A1 on the movie's frames.) Both runs' results are the same as your original runs'.

**Your existing projects -- Board -> Clean up -> "One RAW per project"** (nothing is done until you press Yes): it lists every finished run that holds a RAW nothing plays, with sizes. For these 23 old run folders it **moves the edit onto the whole movie** (exactly what a run does now: every clip at its frame, A1's sound made to the movie's frames, first made aside and checked against the movie at three clips) **and removes the piece** -- the project then holds its RAW once and plays the whole movie; a shared project gets its zip made again. A copy that nothing plays at all would simply go. Your Premiere projects are read, never changed, and a file one of them plays is kept: the projects in the project folder and the ones Premiere opened lately.
- What it finds today (checked, not done): 23 run folders; **19 ready**, every move checked frame for frame (SSIM 1.000 at three clips each). The Guessing Game is on the list (both copies: this computer and D:\Shared).
- **4 kept** (2 projects, both copies): **Awkward Helicopter Ride** and **Strategic Team Pick (5)** -- your recent Premiere projects `r1_POPW.prproj` / `r2_POPW.prproj` (D:\Downloads\e (4)\...) play their piece `raw.mov`. Relink those in Premiere first if you want them done.
- What it frees: the pieces (up to 1.1 GB each), minus A1's sound file growing by the silence up to the piece -- about **1.6 GB** in all (the Guessing Game: 555 MB on each copy; its zip 8.41 -> ~7.74 GB). The big unused movie copies are not deleted: after the move they are the RAW the edit plays.

**Tests:** 4 new tool tests (`test_run19_one_raw.py`: the song is not doubled audio, another sound under A1 still is; a piece run with the song plays the whole movie frame-exact and keeps one RAW; a failed move leaves no movie next to the piece and the app's note; the move keeps a movie already in the media) -- they fail on the old code with your log's exact error; 6 new app tests (`test_rawcopies_run19.py`: a project and its renamed twin both done on Yes, frame-exact; your Premiere project playing the piece keeps it; a move that doesn't check out changes nothing; an unused copy goes, the played one stays; unfinished runs are not offered; the Board button asks first and does it only on Yes).

## Task 2 -- no temporary extra Volume effects (done; the Premiere check runs when you are away)

**What changed:** 1_edit.xml carries **no Gain filter** any more. Until run 18 each A1 clip's "Normalize All Peaks to 0 dB" came as one or more Gain filters, and Premiere imports each as an EXTRA Volume effect (two of +9.6 dB stacked on a loud clip, next to its own Volume) until Ctrl+S made the app rewrite the project. Now each A1 clip has only Volume 0 dB and Panner 0 in the XML -- right after the import Premiere shows **exactly one Volume: the clip's own, at 0 dB**. The gain is written in the clip's comment ("audio chain: Audio Gain +13.84 dB ..."), where the app's save step reads it and writes it as the real **Clip Gain**, with the Hard Limiter (2 dB) and Amplify (+3 dB), as in run 18.

- **Another way for Premiere to import the gain?** No. Premiere 2024's XML import has no form for Clip Gain (tested on day 11: FCP's Gain unit becomes an extra Volume; Audio Levels becomes the clip's own Volume, which you want at 0 dB). So the clips come in at 0 dB and the app's save step adds the Clip Gain.
- **How loud an edit is before -> after the save** (measured on each edit's own A1 sound; the Hard Limiter as ffmpeg's limiter with the same settings):

  | edit (channel) | A1 clips (their Clip Gain) | right after the import | after Ctrl+S (the app's save step) |
  |---|---|---|---|
  | Hardest Guessing Game (SceneProof) | 4 (+8.2 .. +10.1 dB) | -24.1 LUFS, peak -8.2 dBFS | **-10.6 LUFS**, peak +3.0 dBFS |
  | Most Honest Answer (MikeMemesYou) | 12 (+13.4 .. +50.1 dB) | -34.1 LUFS, peak -13.4 dBFS | **-11.9 LUFS**, peak +3.0 dBFS |
  | Funniest Background Squad (Flick707) | 8 (+10.7 .. +15.8 dB) | -27.8 LUFS, peak -10.7 dBFS | **-11.3 LUFS**, peak +3.0 dBFS |
  | When Islam Tried An Energy Drink (2) (LouLaughs) | 3 (+1.0 .. +1.6 dB) | -19.2 LUFS, peak -1.0 dBFS | **-13.5 LUFS**, peak +3.0 dBFS |

  So until the save the voice plays at the RAW's own level -- 6-22 dB quieter (a quiet RAW the most); after it, as in run 18.
- **The save step, every time** (Open in Premiere -> Ctrl+S -> the app closes Premiere, writes, opens it again):
  - it now **holds on as long as Premiere runs** (before: it gave up after 30 minutes);
  - **another project opened** in Premiere meanwhile: it waits; back to this one and saved -> done (before: it gave up);
  - **Premiere doesn't close** (a message): it tries again after your next Ctrl+S (before: it gave up);
  - Premiere closed by you: written when the project was saved, as before.
- **On the row** (the queue and New video), a line under the buttons -- amber until it's done, with why:
  - "the sound isn't set yet: press Ctrl+S in Premiere"
  - "... -- another project is open in Premiere (X.prproj): press Open in Premiere on this video, then Ctrl+S"
  - "... -- Premiere did not close (a message?): answer it, then press Ctrl+S again"
  - "... -- the edit went into X.prproj (the project open in Premiere): the app sets the sound only in the edit's own project -- close it, then press Open in Premiere (or Audio Gain > Normalize All Peaks to 0 dB and your CorrectVolume preset by hand)" -- the app never changes your own projects
  - "... -- Premiere was closed before the project was saved: press Open in Premiere, then Ctrl+S"
  - green when done: "✓ the sound is set: A1: 12 clips with their own Clip Gain (+13.38 to +50.14 dB), Volume 0 dB, Hard Limiter (Input Boost 2 dB) + Amplify +3 dB written into ..."
- **The XML's own check (XML AUDIO)** now wants exactly Volume 0 dB + Panner 0 on every A1 clip (a Gain filter -- an extra Volume -- fails it) and checks each comment's gain against the clip's own sound. The song on A2 is never judged as the voice (it comes after the checks; the same trap as task 1).
- **Without the app** (1_edit.xml imported by hand): select all A1 clips, Audio Gain > Normalize All Peaks to 0 dB, then drop your CorrectVolume preset (each clip's comment says so).
- **Tests:** 4 new tool tests (`test_run19_no_extra_volume.py`) and 6 new app tests (`test_sound_run19.py`: a run-19 import has one Volume and gets its sound on save; the watch waits out another project; Premiere not closing is tried again after the next save; closed before saving says so; the row line; Open in Premiere into another project says the sound is yours to set); night 4 / night 11 / day 17 tests changed to the new form (the old Gain-filter form is now a failure).
- **Real edits made with today's code**, one per channel (`work\t19\prem\...`, your inputs hard-linked): every A1 clip has only Volume 0 dB + Panner 0 and its gain in its comment, XML AUDIO passes.
- **The Premiere test:** <!-- PREMIERE2 -->not run yet -- you came back at about 07:53 (my harness stopped before touching anything) and Premiere is open with your project now; it runs when you are away again and Premiere is closed.

## Task 3 -- the live animations (done)

All in the Mono + red look, calm (no sound, no glow), and Settings -> **Animations: Off** makes every change happen at once. Every number is the run's own -- read from the files the tool writes anyway (`core\live.py`), so **nothing in the tool or its caches changed**: the tool already wrote all of it live, the app just never read it.

| what you see | where it moves | from which real numbers |
|---|---|---|
| **coverage strip fills live** | dark grey "not searched yet"; each piece fades in where it is: green found, amber unsure; at the end red where not in the RAW | every competitor frame the search finds (the decision log's `anchor` records: frame, RAW frame, score -- green from score 0.90), then the cut list's own shots |
| **scan line over the sound wave** | the competitor's sound as a small wave; a thin red line sweeps it with the search (each pass); the wave turns green where found, red not in the RAW | the search's own counter (`search 1/2: 110/262 tasks done`), the found frames, the competitor's sound (240 peaks) |
| **pipeline dots pop** | a short scale tick on the dot of a step that just finished | the row's own steps (download, convert, edit ... done) |
| **status types itself in** | the step's plain name letter by letter when the step changes (the % and time left update as before) | the run's stage line (`== S5.2 visual search` -> "Finding each competitor shot in the RAW") |
| **block bar fills smoothly** | glides to each new share, the last block part-filled, never back while running | the honest estimate (night 15) |
| **big rolling counters** | SHOTS FOUND `23` while it searches, `13 / 13` once the cuts are decided; CAPTIONS; FRAME `00:00:26:09` while it places frames -- each rolls to its new value | shots: the pieces of the RAW the found frames make, then the edit's clips (+ the stretches not in the RAW as the total); captions: `captions: 29 captions`; frame: the furthest competitor frame placed (the `track` records) |
| **the finish** | the status flips to **✓ PASS**; a small dashed **receipt**: ✓ PASS · 13 clips · 29 captions · 0 flashes · 100 % found in the RAW · 24 min 03 s · READY FOR PREMIERE › (click it: Open in Premiere); the row's button becomes the red **OPEN IN PREMIERE**; **DONE n** in the Videos header rolls up by one | the run's last line (`1_edit.xml: 13 clips ... 29 captions`), the cut list, the report's XML FLASH check, the time |

- The New video row shows the same parts while its edit runs in the queue, and its receipt at the end.
- A row from before (the app opened again) gets its receipt read from its run folder.
- **The real test** (08:48-08:59, you were away): the app's own queue page, drawn by Qt offscreen (never your screen), with a row following a REAL run -- the check-all's mik1 (Reno 911, 42 s competitor) -- fed exactly as the queue's job thread feeds a row, every second, 620 times, until the run's end. (A third heavy run of my own did not fit: with the two check-all streams and your open Premiere, free memory fell to 1.6 GB and I stopped my run at once; so I watched a run that was going anyway.) What moved, and from which numbers:
  - the status typed itself in at each step: "Writing the captions" (08:48:30) -> "Putting on the speed and the frame" (08:49:29) -> "Double-checking every cut against the competitor" (08:50:05);
  - the bar glided from 71 % to 99 % in 620 small steps and never went back;
  - SHOTS FOUND **18 / 18** (the edit's 18 clips, no stretch outside the RAW), CAPTIONS **59** (`captions: 59 captions`), FRAME **00:00:42:08** (the last of the 1268 competitor frames placed at 29.97 fps);
  - the strip and the wave: 23 pieces from the cut list (green, one amber unsure piece at 17 s), the competitor's sound as 240 bars coloured the same;
  - at the end: ✓ PASS, the receipt "✓ PASS · 18 clips · 59 captions · 0 flashes · 100 % found in the RAW · 37 min 19 s · READY FOR PREMIERE ›", the red OPEN IN PREMIERE; the dot of EDIT and DONE turned green with their tick.
  - Not seen live in this watch: the search itself (the scan line sweeping and the strip filling piece by piece) -- mik1's search had ended before I attached (and came from its cache). The same reading on a real search's files (the Strategic Team Pick episode's first run, 10-24) gives the search counter (`search 2/2: 254/262`), 523 found frames and their pieces; I watch it live on task 5's first new case (its search is not cached).
- **Pictures** (`reports\run19_look\`, the same moments drawn by run 18's app (`*_before.png`, from git) and by today's (`*_after.png`)): `2_captions`, `3_checking` (the counters, the wave, the strip), `4_finished` (the receipt, ✓ PASS, OPEN IN PREMIERE); `watch_mik1.jsonl` has every second's numbers.
- One fix from the watch: SHOTS FOUND first showed 13 / 13 (a line about the 60 fps planning copy) and then 18 / 18; it now waits for the delivered edit's own count, so a shown number never changes.
- **Tests:** 8 new (`test_live_run19.py`): the live numbers from a run's files written line by line (and never the run before's decisions); the competitor read from a cached probe too; the wave and the timecode; the bar never goes back and is at once with Animations off; a dot pops; the status types in; the counters roll, the receipt, DONE n; a queue row running then finished (the live parts, then the receipt and OPEN IN PREMIERE).

## What I decided on my own

1. **Task 1, the link on this computer:** `edit\extras\media\raw.mp4` stays a hard link of your RAW (the same file under a plain name, no extra space), and 1_edit.xml plays it -- I did not make the XML point at your RAW's own file name. Why: Premiere's XML import fails on some names (emoji; your RAWs come from torrents and YouTube), the edit keeps working when you move or delete the RAW in the project folder, and publishing copies only the edit folder (the shared copy then holds the RAW once). Explorer counts the two names twice; the disk doesn't (`fsutil hardlink list <file>` shows both names). Note: in two projects (Guessing Game, Weirdest Car Shopping) the RAW in the project folder is in your Recycle Bin -- deleting it freed nothing, because the edit's `raw.mp4` is that same file.
2. **Task 1, the old projects:** for a run whose edit plays the piece next to the unused movie, Clean up **moves the edit onto the whole movie and removes the piece** (what the fixed tool makes), rather than deleting the unused movie (which would free more, ~34 GB in D:\Shared, but leave every such edit playing only its piece -- the bug you reported). Your Premiere projects are only read; a piece one of them plays is kept.

## Tests

- After task 1 (07:05-07:26, the graphics card hidden, then again with it): **tool 1676 passed, 0 failed**; **app 1244 passed, 0 failed**; the app's torrent tests apart: **58 passed** (one of them failed first only because my test folder's long path made the downloaded file's name too long -- with pytest's usual folder: 58 of 58).
- After task 2 (07:47-08:19; you came back at about 07:53, so from then on BelowNormal): **tool 1679 passed, 0 failed**; **app 1250 passed, 0 failed**.
- After task 3 (09:01-09:05, BelowNormal): **app 1264 passed, 0 failed** (the tool unchanged since task 2: 1679 passed).
