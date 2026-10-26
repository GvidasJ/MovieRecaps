# Run 19 (26 October): one task at a time

Started 06:34 (PC; you had just written the prompt, then you were away from about 06:35). `shutdown /a`: "no shutdown was in progress"; the backup shutdown is set for **15:30** (`shutdown /s /t 32136`). C: 48.1 GB free at the start, D: 1528.2 GB.

## The tasks

| # | task | state | how you use it |
|---|---|---|---|
| 1 | Only ONE RAW per project, the edit plays the whole movie | **done** (film + episode real runs PASS, frame-exact) | nothing for new edits; your old ones: Board -> Clean up -> Yes ("One RAW per project": 19 run folders ready, 4 kept for your r1/r2_POPW projects) |
| 2 | No temporary extra Volume effects | not started | |
| 3 | The live animations | not started | |
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

## What I decided on my own

1. **Task 1, the link on this computer:** `edit\extras\media\raw.mp4` stays a hard link of your RAW (the same file under a plain name, no extra space), and 1_edit.xml plays it -- I did not make the XML point at your RAW's own file name. Why: Premiere's XML import fails on some names (emoji; your RAWs come from torrents and YouTube), the edit keeps working when you move or delete the RAW in the project folder, and publishing copies only the edit folder (the shared copy then holds the RAW once). Explorer counts the two names twice; the disk doesn't (`fsutil hardlink list <file>` shows both names). Note: in two projects (Guessing Game, Weirdest Car Shopping) the RAW in the project folder is in your Recycle Bin -- deleting it freed nothing, because the edit's `raw.mp4` is that same file.
2. **Task 1, the old projects:** for a run whose edit plays the piece next to the unused movie, Clean up **moves the edit onto the whole movie and removes the piece** (what the fixed tool makes), rather than deleting the unused movie (which would free more, ~34 GB in D:\Shared, but leave every such edit playing only its piece -- the bug you reported). Your Premiere projects are only read; a piece one of them plays is kept.

## Tests

- After task 1 (07:05-07:26, the graphics card hidden, then again with it): **tool 1676 passed, 0 failed**; **app 1244 passed, 0 failed**; the app's torrent tests apart: **58 passed** (one of them failed first only because my test folder's long path made the downloaded file's name too long -- with pytest's usual folder: 58 of 58).
