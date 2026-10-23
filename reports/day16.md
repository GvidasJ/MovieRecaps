# Day 16 (23 October): one task at a time

Started 07:21 (PC); you were using the PC, so code and light tests first. The backup shutdown is set for 15:30. C: 51.5 GB free, D: 2383 GB free at the start.

## The tasks

| # | task | state | how you use it |
|---|---|---|---|
| 1 | "Try with another RAW" keeps your title and text | **done** | a row that failed because of its RAW (WRONG PAIR, NOT IN THIS RAW, PICTURE NOT FOUND, a RAW that can't be read) has **Try with another RAW**. History has it too, on the failed event |
| 2 | The zip has the whole project folder | **done** (Premiere test passed) | nothing to do: each finished project's `<name>.zip` now holds the whole folder; its size is on the board card ("zip 2.2 GB") |
| 3 | Only the movie's piece in the project folder | **done** (Premiere: every clip on its frame) | nothing to do: a new edit plays edit\extras\media\raw.mov, the found place ± 10 min; the whole movie stays where you downloaded it |
| 5 | Find source with NexLev and Algrow | **done** (5 real clips at once: all 5 right, Sure) | Progress > Find source as before: an AI watches each clip first; Sure only when checked (your RAW's sound, IMDb / TVmaze); it uses credits once per clip |

## Task 1 -- "Try with another RAW" (done)

- **On the queue:** a row that failed because of its RAW gets the button **Try with another RAW**. "Because of its RAW" means one of these:
  - WRONG PAIR, NOT IN THIS RAW or PICTURE NOT FOUND
  - a RAW that can't be read: "could not decode source frames ... by PTS" (the Ted 2 stops), or FFmpeg's "damaged video" words about the RAW
- **What it does:** it opens New video with everything already filled in:
  - the **YouTube title**
  - the **headline** (the template text, both lines)
  - the **channel**
  - the **competitor clip** (its file, or its link when the file is gone)
  - Fast / Thorough and 100 % / 125 %
- **The RAW field is empty and has the cursor.** You only paste a link, pick a file or drop a torrent, then **Make the video**. The amber line says what was kept and which RAW gave which stop.
- **History:** the failed event ("Edit failed · WRONG PAIR") now shows the YouTube title and the headline on a third line, with the same button. History reads every project's project.json, so this works after a restart too. The search also finds the YouTube title.
- **Real test:** your real WRONG PAIR project, the Dolittle one (`D:\Shared\laptop_cases\2026-10-20_When Your Dad Takes Your Phone Away`: the YTS "Dr. Dolittle 2" that holds the 1998 film). Try with another RAW filled in:
  - the headline "The fact that she thought her dad / would care is hilarious 📱😭"
  - the YouTube title "When Your Dad Takes Your Phone Away 📱😭"
  - the channel MikeMemesYou
  - the competitor `Video by wasted [DdVZePAga2I].mp4`
  - and the RAW field empty

  This was only read: nothing in that folder was changed.
- **Tests:** app 1214 passed, 0 failed (6 new: `test_another_raw_day16.py`, one of them on the real Dolittle project). The real-aria2c torrent test still crashes inside the whole suite, as on night 15. It passes alone (1 passed).

## Task 2 -- the zip has the whole project folder (done)

- **What's in it now:** everything in the project folder, with the same folders inside, under one folder named after the project (without emoji):
  - `edit\` (1_edit.xml, the captions, `extras\` with its media: the RAW or its piece, its WAV, the frame, the watermark, the song)
  - `logs\`, the competitor clip, `project.json`, `template.png`, `title.txt`, `titles_found.txt`
  - `HOW TO OPEN.txt`
- **Videos, sounds and pictures are stored as they are** (no compressing again), so it is fast and lossless. Text is compressed.
- **Never in it:** any `.zip` / `.7z` / `.rar` (itself or an older one), half-written files (`.tmp`, `.part`, `.aria2`), and the work locks.
  - A file that is the same file twice (a hard link) goes in once, as the copy the edit plays. HOW TO OPEN names the other one.
  - A file the edit plays from outside the project folder goes into `outside\` when it is small. When it is big, it is left out and named.
- **It is made in the background** right after the edit (the queue doesn't wait). The 10-minute sweep makes it again when a file changes.
  - project.json alone doesn't count as a change, because the board changes it.
  - A zip of night 15's kind is made again by itself.
- **The board card** shows "zip 2.2 GB" (the tooltip says where it is).
- **A safety rule:** the zip is not made when it would leave its drive with under 40 GB free.
- **The existing zips were remade** (`D:\Shared\Projects`, 35 finished projects): 0.8 GB -> **67.8 GB** in all, in **92 s**.
  - The big ones still hold the whole movie, because they were made before Task 3: mindculpt 8.6 GB, Are We There Yet 7.4 GB, Funniest Accent 7.2 GB, Lab Accident 5.9 GB, Borat 5.2 GB.
  - The smallest: 69-110 MB.
  - I checked one (Coldest Dad, 2.2 GB) with a full CRC check: every file whole.
  - HOW TO OPEN of a Just cuts project names `cuts\1_edit.xml`.
- **Premiere test (07:45, you were away):** I copied the "milk" project, pointed its XML at that copy, zipped it, then **hid the copy** (renamed it) so the XML's paths led nowhere, and unzipped it into another folder. Then I imported the XML with Open in Premiere and saved.
  - **No message, no "Link Media", no Media offline.**
  - The saved project plays every file from the unzipped folder: `raw.mp4`, `raw_stereo.wav`, the frame and the watermark, each with its real size and length.
  - Premiere finds the files next to the XML by itself, so your friend needs nothing else: unzip, then File > Import `edit\1_edit.xml`.
  - Test folder: `work\t16\zt`.
- **Tests:** app 1220 passed, 0 failed (7 new: `test_zip_day16.py`; night 15's zip test changed to the whole folder).

## Task 3 -- only the movie's piece in the project folder (done)

- **What changed** (`tools\match_cuts\match_cuts\keep_piece.py`, at the very end of every Premiere run):
  - The tool takes the RAW frames every V1 / A1 clip of 1_edit.xml plays and groups them. Clips closer than 20 minutes are one group.
  - Each group is kept **± 10 minutes**, from the keyframe before to the keyframe after, cut by **stream copy**: the very same packets, nothing re-encoded.
  - All the groups go one after the other into **one file**, `edit\extras\media\raw.mov`. One group gives one piece. A montage from far-apart places gives one piece per group, in a row in that file.
  - A1:
    - `raw_stereo.wav` is cut the same way, sample for sample.
    - When A1 plays the RAW's own sound, the piece carries that sound as exact PCM.
  - Every RAW clip of 1_edit.xml (and of the planning copy `extras\edit_60fps.xml`) is moved onto the piece, at the right frame.
  - **Checked before anything changes:**
    - the piece's frames are the movie's at the start, middle and end of every group
    - three clips per group show the movie's frame
    - the moved XML has no Premiere problem

    Then it is swapped in, and the run's link or copy of the whole movie is removed from `extras\media`.
  - **The whole movie stays when:**
    - the RAW is under 15 min
    - the pieces would be over 60 % of it
    - it can't be cut that way (VFR, an edit list ...)
    - any check fails (nothing is changed then, and the log says why)
- **The analysis is as fast as before:** it still uses its own smaller piece (± 5 min films, ± 2 min episodes). This step comes after it and takes 2-5 s.
- **The project folder (the app):** after such an edit, the project folder's own copy or hard link of the whole movie is removed. This only happens when:
  - the movie is still where you downloaded it (the same size)
  - and the app did not convert it

  project.json remembers where the movie is (`raw.kept_at`), and Run again reads it there. A RAW the app converted, or a YouTube download made into the project folder, stays.
- **Learn and the scores:** `extras\keep_piece.json` says where each piece is in the movie.
  - Learn reads your fixes in the piece's frames, because your Premiere project plays the piece.
  - The check-all scores map the XML back to the movie's frames, because the answer keys count in them.
- **Existing finished projects: not changed.**
- **Real test** (copies of two finished runs in `work\t16\kp`; your projects untouched):

  | | the movie | piece kept | before -> after (V1 + A1 files) | time |
  |---|---|---|---|---|
  | **Benchwarmers** (12 Year Old, film, 1:25:10) | raw.mp4 1090 MB + raw_stereo.wav 664 MB | 40:53-1:03:06 (26 %) | **1673 MB -> 495 MB** | 2.5 s |
  | **faces** (episode, 28:30; A1 plays the RAW's own sound) | raw.mp4 2412 MB | 16:10-28:30 (43 %) | **2412 MB -> 1107 MB** | 4.1 s |

  - **In Premiere 2024 (08:00, you were away):** both imported with no message, `raw.mov` online with the piece's exact length (1333.0 s, 740.2 s).
  - From the saved projects: **every V1 clip (44 of 44, and 23 of 23) starts exactly at its frame of the old XML** (the whole movie) minus the piece's start.
  - The tool's own frame checks on the same clips: the same pictures.
  - The pieces are HEVC with B-frames and the same edit list as the original files Premiere already plays.
- **What it would do on your finished projects** (a dry run, nothing changed):
  - films keep 12-26 % of the movie: Lab Accident 16 % of 4.4 GB, Funniest Accent 17 % of 5.7 GB, Awkward Interview 12 %, Coldest Dad 22 %
  - 28-min episodes 43-60 %: faces, aqoevqvh, Personality Switch, Honest Conversation
  - most 21-28 min episodes stay whole: ± 10 min of a 22-min episode is over 60 %
- **Tests:**
  - tool: **1643 passed, 0 failed** (5 new: `test_day16_keep_piece.py` -- two far-apart groups in one file with the very same pixels and samples, the shifts, the read-back for scores and Learn, the whole movie when too big or a check fails)
  - app: **1223 passed** (2 new: `test_keep_piece_app_day16.py`)
  - Skipped tests went up from 74 to 101: those tests need `input\competitor.mp4` and `input\raw_test.mp4`, which were gone before I started (the `input` folder was changed at 07:18).

## Task 5 -- Find source with NexLev and Algrow (done)

- **How the two are called:** both run an MCP server over HTTP that takes your key as a Bearer token:
  - NexLev: `https://prod.dashboard.nexlev.io/api/mcp`, tools `watch_instagram_video_and_ask` / `watch_tiktok_video_and_ask` / `watch_youtube_video_and_ask`
  - Algrow: `https://mcp.algrow.online/mcp`, tools `analyze_video` (1 credit per 4 min of video) and the free `fetch_transcript`
  - The code is `tools\app\mr_app\core\ai_watch.py`.
- **The keys:** read from the Windows user environment variables `NEXLEV_API_KEY` / `ALGROW_API_KEY` of this PC, and never written anywhere. The only thing the app ever says about them is "key found" / "key not found". Any error text has a key replaced by `***` before it is shown or saved.
  - I checked this: **5645 files** (the code, the reports, git, the app's data, the downloaded clips) hold **no key**.
- **How Find source works now, for each clip** (the same table, buttons, Copy all and History; 5 at the same time):
  1. **The AI watches it** (picture and sound): NexLev, once more on a failure, then Algrow. It gives every spoken line, the on-screen text, the actors, the setting, and its guess of the film / show + season / episode, with how sure it is.
  2. **Its guess is checked:**
     - your RAWs on this PC: the AI's title is looked at first, and the clip's sound is looked for in it (the quick sound check)
     - else the public lists: IMDb for a film (the same year ± 1); TVmaze for an episode (that season / episode has the episode title the AI named)
  3. **Sure only when checked**:
     - the sound found in your RAW, or
     - the AI is at least 85 % sure **and** the lists confirm it

     Otherwise **Check** ("only the AI's guess: the public lists don't confirm it"), or **Not found** with why ("the AI watched it and does not know it ...").
- **Credits:** a clip is never watched twice. The answer is remembered by its link (`%APPDATA%\MovieRecaps\ai_watch.json`), also after a restart. The page says small: "uses NexLev / Algrow credits (each clip is watched once, then remembered)".
- **Fixed on the way** (found in the real test):
  - a project's `raw_stereo.wav` made from a piece of the movie (day 14 / Task 3) holds one scene, so it no longer counts as "your RAW" of that film. The whole movie is used (the folder's, or where it was downloaded).
  - two downloads named after one film are both looked in: the YTS "Dr. Dolittle 2" that holds the 1998 film no longer hides your real Dolittle 2 WEBRip.
  - Algrow's answer comes wrapped in `analysis_text`, which is now read. An empty answer is not remembered.
  - a clip download that fails while 5 run is tried once more.
- **Real test, 5 at once (19 s in all, 7-19 s each):**

  | clip | the AI said | Find source says | right? |
  |---|---|---|---|
  | `instagram.com/p/Dd1e2BmyHCX` | The Dictator (2012), "the famous helicopter scene", 100 % | **The Dictator (2012) — Sure 97 %** (its sound in your RAW) | yes |
  | American Dad reel `DczA1q3MTge` | American Dad! S03E03, 100 % | **American Dad! — S03E02 — Sure 98 %** (its sound in your RAW) | yes |
  | Dolittle 2 reel `DdVZePAga2I` | Dr. Dolittle 2 (2001), 100 % | **Dr. Dolittle 2 (2001) — Sure 99 %** (its sound in your WEBRip; the YTS file ruled out) | yes |
  | Family Guy reel `DZJfIY3IlTD` | Family Guy S11E07, 100 % | **Family Guy — S09E11 — Sure 99 %** (your project's episode) | yes |
  | Blue Mountain State reel `DZ56TJHuM75` | Blue Mountain State S03E12, 100 % | **Blue Mountain State — S03E05 — Sure 99 %** | yes |

  - Without your RAWs (an empty library), The Dictator is **Sure 95 %** (the AI + IMDb). The American Dad reel stays **Check 80 % (S03E03)**, because TVmaze's S03E03 is another episode title.
- **What this shows:** the AI is always right about the title, but **it says "100 % sure" on wrong episode numbers** (3 of 3 shows here). So an episode is never Sure from the AI alone: only with its sound in your RAW, or TVmaze's title matching.
- **Not built:** the optional "Algrow transcript of a YouTube clip of that scene" step. The sound check and TVmaze already decide every case above. The free `fetch_transcript` is in `ai_watch.transcript()` for later.
- **Tests:** 9 new (`test_ai_watch_day16.py`, fakes only: no network, no credits). App tests never spend credits (`MOVIERECAPS_NO_AI`).
