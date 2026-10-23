# Day 16 (23 October): one task at a time

Started 07:21 (PC); worked until about 15:00, the PC free from about 07:30 on. The backup shutdown was set for 15:30. C: 51.5 GB free, D: 2383 GB free at the start; at the end C: 39.0 GB, D: 2311 GB (see Still broken).

## The tasks

| # | task | state | how you use it |
|---|---|---|---|
| 1 | "Try with another RAW" keeps your title and text | **done** | a row that failed because of its RAW (WRONG PAIR, NOT IN THIS RAW, PICTURE NOT FOUND, a RAW that can't be read) has **Try with another RAW**. History has it too, on the failed event |
| 2 | The zip has the whole project folder | **done** (Premiere test passed) | nothing to do: each finished project's `<name>.zip` now holds the whole folder; its size is on the board card ("zip 2.2 GB") |
| 3 | Only the movie's piece in the project folder | **done** (Premiere: every clip on its frame) | nothing to do: a new edit plays edit\extras\media\raw.mov, the found place ± 10 min; the whole movie stays where you downloaded it |
| 4 | SceneProof song: the right levels and effects | **done** (Premiere: A2 at +13.5 / +7.5 dB) | import assets\premiere\MovieRecaps song chain.prfpset once, then drop it on the two song clips of each SceneProof edit |
| 5 | Find source with NexLev and Algrow | **done** (5 real clips at once: all 5 right, Sure) | Progress > Find source as before: an AI watches each clip first; Sure only when checked (your RAW's sound, IMDb / TVmaze); it uses credits once per clip |
| 6 | A RAW that can't be read is converted by itself | **done** (the real Ted 2 x265 file: the stop reproduced, the converted copy reads) | nothing to do: the row converts the RAW once (NVENC) and runs the video again |
| 7 | Learn: your new fixes | **done** (video28-32 learned, video27 learned again with its hidden captions) | nothing to do: 5 new test cases; the check-all measures the tool against them |
| 8 | Refine faster + the S08 still + the check-all | **done** (both rules ON; 24 of 46 cases ran, nothing worse; S08 not seen on your real case) | nothing to do |
| 9 | Learn by yourself | **not done** (no graphics-card time left after the check-all; what it would be below) | -- |

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
  - Skipped: 101 -- 91 slow tests (they run only with `--runslow`) and Linux-only ones; night 15 counted 74, and I didn't find why the count differs (no test fails). (`input\competitor.mp4` and `input\raw_test.mp4` were already gone when I started: the `input` folder was changed at 07:18.)

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

## Task 4 -- SceneProof song: the right levels and effects (done; the preset drop is yours)

- **Your project (read from `Desktop\song.prproj`):** each song clip has, in this order, Volume, Volume, Hard Limiter, Amplify. Your Input Boost **2.00 dB** is stored as 0.68 (so the scale is -100..+50 dB).
  - Your Amplify there is **+4.5 dB** (your older LoudVOGOOOD value), not +3.00. I used **+3.00 dB** as you wrote in the prompt. If +4.5 is the one you want, say so and I'll change one number.
- **What an edit gets now (SceneProof, A2):**
  - before the cut (12.32 s): **Volume +13.5 dB** (your Clip Gain +5 + your Volume Level 8.5)
  - after the cut: **Volume +7.5 dB** (-1 + 8.5)
  - each song clip's comment says: "drop the preset MovieRecaps song chain on it"
- **The new preset `assets\premiere\MovieRecaps song chain.prfpset`:** Hard Limiter (Maximum Amplitude 0 dB, **Input Boost 2.00 dB**, Look-Ahead 7 ms, Release 100 ms, Link Channels 1.00) + **Amplify +3.00 dB**.
  - It is made from your A1 preset file (Premiere's own export), with its own name and preset id. The values are checked in a test: Input Boost 0.68 on the limiter for 1, 2 and 6 channels; Amplify 0.6875.
  - **Install it once like the A1 preset:** in Premiere's Effects panel, right-click **Presets > Import Presets...**, and pick `assets\premiere\MovieRecaps song chain.prfpset`.
- **Measured -- does +13.5 / +7.5 dB with the preset sound like your way?** An XML can't carry Clip Gain, and I can't render from Premiere without clicking (dropping a preset needs the mouse). So I measured the same chain on the real song with FFmpeg's limiter (ceiling 0 dB, 7 ms, 100 ms), both ways Premiere could order a clip's Volume:

  | the clip's Volume | piece | your way | the tool's way (Volume +13.5 / +7.5 + the preset) |
  |---|---|---|---|
  | before the effects | +5 dB piece | -8.70 LUFS, peak +3.00 | **-8.70 LUFS, peak +3.00** |
  | before the effects | -1 dB piece | -4.30 LUFS, peak +3.00 | **-4.30 LUFS, peak +3.00** |
  | after the effects | +5 dB piece | -7.80 LUFS, peak +8.40 | **-7.80 LUFS, peak +8.40** |
  | after the effects | -1 dB piece | -3.40 LUFS, peak +8.40 | **-3.40 LUFS, peak +8.40** |

  - **The same in every case.** The song's loudest peak is -10.1 dBFS: with the Volume after the effects, the limiter never touches it; with the Volume before, every gain is before the limiter anyway.
  - (For a louder song I added `song_preset(gain)`, which puts a Clip Gain into the Input Boost; it isn't needed for this one.)
- **What you must set by hand:** only drop **MovieRecaps song chain** on the two song clips (A2). Nothing else.
- **Premiere test (08:24, you were away):** a SceneProof edit (the American Dad one) imported with no message. The saved project has A2 with 2 song clips at **Volume +13.5 dB and +7.5 dB** (Premiere kept +13.5, it doesn't cut it to +12). Test folder `work\t16\song\edit`. The preset drop and listening are yours.
- **Tests:** 3 new (`test_day16_song_chain.py`); night 15's music and SceneProof tests unchanged and passing.

## Task 7 -- Learn: your new fixes (done)

- **All five open entries (video28-32) are learned** and marked done in `learn_queue.md`. Each is a new test case in `tests\real\videoNN-fixed`: your finished edit as the key (captions, cuts and framing), with the frame and watermark its run used.
- **The laptop folders were not needed.** The runs of video29, video31 and video32 are on this PC too (`input\downloads\2026-10-22_The Funniest Weight Loss Class of All Time (2)`, `...Most Suspicious Farmer...`, `...Most British Answer...`), so nothing has to be copied over.
- **video27 (blackads.prproj), your captions on a hidden track:** Learn now reads the text graphics of a hidden video track when the project shows no captions anywhere else. Video27 was learned again: **89 captions in its key** (before: none, "your captions are hidden").
  - A hidden **caption** track still never counts. Those are the stale copies Premiere keeps (video1's C84, video2's C41).
- **What your fixes show:**

  | case | video | captions you changed | cuts | framing |
  |---|---|---|---|---|
  | video27 | Extras (faces) | 3 words; 4 action texts | 3 of 20 edges moved, 1 clip removed, 3 pause cuts | 11 clips moved (median -159 px) |
  | video28 | The Benchwarmers (12 Year Old) | 4 words ("1 200" -> "1200"); 7 action texts | 37 of 44 edges moved, 4 removed, 4 added, 6 pause cuts | 28 moved (+189 px), 9 zoomed (+3.5 %) |
  | video29 | Little Britain (Weight Loss Class) | 2 words ("do" -> "say") | 3 of 24 edges moved | 7 moved (+314 px) |
  | video30 | Key & Peele (Personality Switch) | none; 4 action texts | 2 of 12 moved, 1 removed, 2 pause cuts | 7 moved (-195 px) |
  | video31 | The Farmer (British comedy) | 3 words: **k\*ll** (twice), **ars\*s** | 0 of 12 edges moved | 5 moved (+430 px) |
  | video32 | The British Answer | 5 words | 6 of 46 edges moved | 4 moved (-489 px) |

- **Taken:**
  - your censored forms `kill = k*ll` and `arses = ars*s` added to `caption_censor.txt` (whole words; no other key has them uncensored)
  - your action texts added to the action examples, as Learn does for every fix
- **Not taken (as on night 15):** Learn's 3 glossary lines (`1 200 -> 1200`, `kill -> k ll`, `arses -> ars s`). The glossary also goes to the speech recognition as hints, so any new line re-transcribes every video. The two censored words came from the censor list instead.
- **The rules:** only captions, cuts and video position / scale were learned. The check-all below measures the tool against these new keys.

## Task 6 -- a RAW that can't be read is converted by itself (done)

- **What changed (the queue):** when a run stops with "could not decode source frames ... by PTS", the same row goes on by itself:
  1. It **converts the RAW once**: NVENC (the CPU if there is none), 8-bit H.264, every frame on the next slot of its constant rate (nothing dropped or doubled), the sound copied. The row says "the RAW can't be read by its time stamps: converting it once (NVENC) -- the video then runs again by itself", with the progress.
  2. It **runs the same video again** on the converted copy, in the same queue slot.

  - The copy is `<RAW name>.reencoded.mp4` next to the RAW. **The original is never deleted.** A copy made before is used again.
  - project.json remembers the copy (`raw.decode_copy`), so Try again and Run again use it too.
  - Only once: if the converted copy stopped the same way, or the conversion failed, the row shows the stop and why (no loop). Any other stop is as before.
- **Why the Convert tab didn't help before:** it never re-encodes HEVC, it only puts it into MP4. That is why `Ted2_fixed.mp4` stopped the same way (problems_queue.md). This fix re-encodes.
- **Real test, the Ted 2 x265 file** (`D:\Shared\Downloads\Torrents\Ted.2.2015.EXTENDED.1080p.BluRay.x265-RBG.mp4`, 2 h 6 min, 10-bit HEVC, its first 2 frames hidden by an edit list):
  - the tool's own frame reader on the original: **"could not decode source frames [0, 1]... by PTS"**, the very stop of your two runs
  - the app's conversion (the one the queue uses): **181040 of 181040 frames, 10.2 GB, 35 min** (at 11x on a free GPU; 3.4x while three check-all streams ran)
  - the same reader on the copy: **frames 0, 1, 5000 and 100000 read**
  - The copy is in `D:\mr_scratch\ted\` (a folder of mine on D:). You can use it as the RAW, or delete it.
  - The full run of the Lab Accident reel on it was not finished today (it would hold the GPU for hours next to the check-all).
  - **Also found:** since day 14 a film is analysed in a piece, and the Ted piece read fine. With today's code, the Lab Accident reel on the Ted 2 x265 file got past "Preparing the RAW" with no conversion at all. The conversion is the safety net for when the whole file is read.
- **The conversion's check:** a re-stamped copy is checked on the same frame count and the exact constant-rate length. The original's uneven times gave 7550.816 s; the copy is 181040 frames = 7550.877 s. The copy must not end further from its sound than the original did (it is closer: the sound is 7550.954 s).
- **Tests:** 4 new (`test_decode_fix_day16.py`):
  - convert once and run again
  - never a loop
  - a failed conversion is said
  - the project reads its copy
  - a real 10-bit x265 clip re-encoded with every frame kept

## Task 8 -- refine faster + the Benchwarmers S08 + the check-all

- **Refine faster (built, ON):**
  - Refine no longer rescue-searches a competitor frame that has no picture track when it sits inside a stretch of **2 s or more where the sound search found no candidate in the RAW** (another video's sound).
  - "Stop the rounds when a round finds nothing new" was already so (a round that adds nothing ends them), so nothing changed there.
  - Switch: `MATCH_CUTS_RESCUE_ALL=1` searches every frame again.
- **The S08 hold (built, ON):** a "reverse" segment slower than 0.5x backwards, at most 2 s long and moving at most 6 RAW frames, becomes a **still of the RAW frame its competitor frames match most**. Its framing keys (the zoom) are kept. Switch: `MATCH_CUTS_NO_STILL=1`.
- **The check-all:** from a frozen copy of the code (`work\snap16a`), up to 3 streams. Every case recomputes the picture placing (refine changed), so cases took 8-48 min each instead of night 15's 5-30.
  - The 12 new cases (video21-32) have no caches at all: video22 took 44 min, video21 93 min, and video28 (Benchwarmers) was 8 % into its first picture search after 75 min. So I ran the older cases first (they decide "nothing gets worse": they have a "before").
- **Did the rules act, and did anything get worse?**
  - **The rescue skip acted on 1 finished case: zendaya** (121 frames not searched). The same case run again on a copy of the code with both rules off (`work\snap16b`, its own cache keys, a real "before"):
    - **the very same edit** (6 of 6 clips identical)
    - the same frame map (575 matched, 165 none)
    - **rescue searches 165 -> 44**
  - **The still rule acted on no finished case.**
  - Every older case that finished has **exactly its earlier scores**, except two whose difference comes from night 15, not today:
    - **video2** captions 2/21 -> 1/21: today's captions are word for word night 15's run with the episode piece (night 15, task 8); night 15's run without the piece has day 13's
    - **video1** hard checks FAIL: today's edit is clip for clip night 15's piece run (night 15 already reported "one take split into two clips", an XML note)
- **Time before -> after:**
  - **Benchwarmers (video28): not measured.** Your project folder's `.mp4` RAW is gone (only the `.mkv` is in Torrents), so its caches can't be reused, and the test case's first picture search alone would have taken hours today. Your original run: refine 5449 s (91 min), 1126 rescue searches (night 15). Expected with the skip: the 320 other-video frames (3 stretches) no longer searched.
  - **zendaya:** rescue searches 165 -> 44 (-73 %). The refine time (346 s vs 299 s) is hidden by the load: three streams shared the PC, so seconds aren't comparable between runs.
  - **The S08 itself is not verified on your real case today** (video28 too slow, above). It is covered by tests (the rule; the frame it picks).
- **Kept:** both rules, ON. Nothing got worse on any case where they acted (one) or didn't (all the others).

## The check-all (Task 8)

- **How it ran:** from the frozen code `work\snap16a` (the commits of 08:43), 3 streams from 09:27 / 10:00 until 14:05. The table's "before" is day 13's table (night 15 matched it exactly on its 13 cases; liquid-death from night 15).
- **24 of 46 cases finished.** Not run in time: deadpool, laptop009, liquid-death, mike005, video018, video021, video10, video12, video16, video18, video20, video5, video7, zendaya-age, and the new video22 (only stream B's first try: 44 min, then stopped for the reorder), 23, 25, 26, 28, 29, 31, 32.
- "rescue skipped" = frames the new refine rule did not search; "stills" = holds made a still. Neither rule acted anywhere except zendaya (checked A/B: the same edit).

| video | before: hard / captions / word errors / cuts / framing | day 16 | time | rescue skipped | stills |
|---|---|---|---|---|---|
| chocolate-fixed | PASS / 7/22 / 16.67 / 1/3 / 5/5 | PASS / 7/22 / 16.67 / 1/3 / 5/5 | 21 min | - | - |
| laptop004-fixed | PASS / 6/27 / 12.5 / 0/3 / - | PASS / 6/27 / 12.50 / 0/3 / - | 28 min | - | - |
| spiderman-school | PASS / 11/64 / 10.2 / 2/21 / - | PASS / 11/64 / 10.20 / 2/21 / - | 22 min | - | - |
| video020-fixed | fail/pass/pass / 0/30 / 94.12 / 0/11 / - | PASS / 0/30 / 73.33 / 0/11 / - | 25 min | - | - |
| video1 | PASS / 21/111 / 10.83 / 11/29 / - | FAIL / 20/111 / 10.00 / 9/29 / - | 42 min | - | - |
| video11-fixed | PASS / 14/23 / 15.38 / 2/3 / 2/8 | PASS / 14/23 / 15.38 / 2/3 / 2/8 | 14 min | - | - |
| video2 | PASS / 2/21 / 4.76 / 1/1 / - | PASS / 1/21 / 9.52 / 1/1 / - | 11 min | - | - |
| video3 | PASS / 9/71 / 10.76 / 1/11 / - | PASS / 10/71 / 8.86 / 1/11 / - | 15 min | - | - |
| video4 | PASS / 6/38 / 18.42 / 2/7 / - | PASS / 6/38 / 18.42 / 2/7 / - | 40 min | - | - |
| video6-fixed | PASS / 18/32 / 10.0 / 5/7 / 9/9 | PASS / 18/32 / 10.00 / 5/7 / 9/9 | 39 min | - | - |
| video9-fixed | PASS / 4/18 / 44.44 / 3/4 / 4/4 | PASS / 4/18 / 44.44 / 3/4 / 4/4 | 16 min | - | - |
| video21-fixed | (new case) | FAIL / 0/70 / 0.00 / 0/24 / 2/32 | 93 min | - | - |
| video24-fixed | (new case) | PASS / 14/42 / 42.39 / 4/7 / 1/14 | 11 min | - | - |
| video27-fixed | (new case) | PASS / 12/89 / 19.15 / 2/12 / 12/22 | 74 min | - | - |
| video30-fixed | (new case) | PASS / 18/43 / 16.85 / 4/7 / 2/13 | 13 min | - | - |
| laptop010-fixed | PASS / 10/34 / 7.69 / 1/9 / 11/13 | PASS / 10/34 / 7.69 / 1/9 / 11/13 | 9 min | - | - |
| laptop013-fixed | PASS / 12/22 / 13.33 / 1/5 / 2/6 | PASS / 12/22 / 13.33 / 1/5 / 2/6 | 13 min | - | - |
| video017-fixed | PASS / 23/43 / 10.99 / 4/12 / - | PASS / 23/43 / 10.99 / 4/12 / - | 11 min | - | - |
| video14-fixed | PASS / 16/42 / 8.0 / 1/3 / 4/10 | PASS / 16/42 / 8.00 / 1/3 / 4/10 | 10 min | - | - |
| video15-fixed | PASS / 7/22 / 33.93 / 3/8 / 12/21 | PASS / 7/22 / 33.93 / 3/8 / 12/21 | 19 min | - | - |
| video17-fixed | PASS / 9/32 / 14.04 / 5/7 / 13/16 | PASS / 9/32 / 14.04 / 5/7 / 13/16 | 47 min | - | - |
| video19-fixed | PASS / 8/33 / 30.0 / 3/8 / 1/10 | PASS / 8/33 / 30.00 / 3/8 / 1/10 | 42 min | - | - |
| video8-fixed | PASS / 0/16 / 9.09 / 1/4 / 4/11 | PASS / 0/16 / 9.09 / 1/4 / 4/11 | 8 min | - | - |
| zendaya | PASS / - / - / - / - | PASS / - / - / - / - | 26 min | 121 | - |

- **The differences, all from night 15 (not today):** the 4 episode cases (RAW 21-24 min) are analysed in a piece since night 15 (its task 8: "pieces on for episodes").
  - **video1:** FAIL. Today's edit is clip for clip night 15's piece run; night 15 reported the split take.
  - **video2:** captions 2/21 -> 1/21. Today's captions are word for word night 15's piece run.
  - **video020:** better (fail -> PASS, word errors 94 -> 73).
  - **video3:** better (captions 9 -> 10/71).
- **The 4 new cases that finished** have no "before" (their first full run):
  - video24 PASS 14/42 captions
  - video27 PASS 12/89 (its hidden captions are the key now)
  - video30 PASS 18/43
  - video21 FAIL: one stretch the tool would not guess (frames 292-339 all match RAW 963: a hold), and a caption score of 0/70 with 0.00 word errors, which looks like this new case's key not lining up with the run. Both are in "check by hand".

## Task 9 -- learn by yourself (not done: no time left)

- The check-all held the graphics card until 14:05, so no new rule could get a check-all of its own. Building one without that would break "nothing may get worse".
- **What the data points at, for the next free night:**
  - The biggest remaining difference in your fixes is **framing**. Learn's summary over all cases: "the picture moved sideways on 21 videos". The finished cases show framing as low as mike005's 0/14, video8 4/11 and video21 2/32.
  - A careful rule would be "centre on the person speaking" extended to clips the speaker rule now leaves alone. It needs a check-all from scratch.
  - Second: video21's hold (frames 292-339 on one RAW frame) could be a still like today's S08 rule, but it is a hold of a frame the competitor didn't zoom on, so it needs its own look.

## Time and sizes before -> after

- **Benchwarmers piece (Task 3):** the run folder's V1 + A1 files 1673 MB -> 495 MB. faces (episode): 2412 MB -> 1107 MB. Your projects' zips (Task 2): 0.8 GB -> 67.8 GB in all (now the whole folders), made in 92 s.
- **Find source, 5 clips at once (Task 5):** 19 s with the AI watching (each clip 7-19 s; one AI watch: 13 s).
- **Ted 2 x265 conversion (Task 6):** 35 min (10.2 GB) with the PC shared; 11x real time on a free GPU.
- **Refine (Task 8):** zendaya's rescue searches 165 -> 44. The Benchwarmers refine time was not measured (above).
- **Check-all cases:** 8-48 min each today (every case placed its frames again) against night 15's 5-30 min with its caches.

## Tests at the end

- **Tool: 1652 passed, 0 failed** (101 skipped: 91 slow tests that run only with `--runslow`, and Linux-only ones).
- **App: 1235 passed, 0 failed**, the real-aria2c torrent test included (it crashed inside the whole suite on night 15; today it passed twice in full runs).
- `.venv\Scripts\python toolspp\setup_app.py`: "done: everything is as the project expects" (exit 0).
- The keys: a scan of 5771 files (the code, the reports, git, the app's data, the clips) finds neither key anywhere.
- **The shutdown:** you were at the PC at 14:47 (input 1 s before), so I cancelled the 15:30 backup shutdown and did not turn the PC off.
- 11 commits today, all pushed to `claude/new-session-ol4era`.
- A day-13 test said "kill him" must stay uncensored. That was an example, not your fix: no key ever had it uncensored, and video31 shows you write `k*ll`. The test now follows video31.

## Still broken

- **The 12 Year Old (Benchwarmers) S08:** the still rule is built and ON, but not seen on your real case today (video28 was too slow to run). Your current edit still plays it backwards: put the still by hand (RAW 300.59 s), as night 15 said.
- **video28's picture search:** 8-10 % after 53-75 min, in both thorough and `--fast` mode, on a 190 s RAW. Something about this case makes the first search very slow. Worth a look on a free night.
- **video21:** see the check-all above.
- **The zip is not made while its drive has under 40 GB free** (C: went down to 34.6 GB during the check-all). The 10-minute sweep makes it once there is room.
- **Task 6 on the full Lab Accident run:** the conversion and reading were tested on the real Ted 2 file, but the whole run on the converted copy was not finished today.

## Check by hand

1. **Import the song preset** once (Effects > Presets > Import Presets, `assets\premiere\MovieRecaps song chain.prfpset`). Drop it on the two song clips of a SceneProof edit (`work\t16\song\edit\edit_edit.prproj` is ready) and listen. Also tell me whether the song's Amplify should be +3.00 dB (your prompt) or +4.5 dB (your song.prproj).
2. **A piece of the movie (Task 3):** open `work\t16\kp\bench\edit\edit_edit.prproj` (Benchwarmers) or `work\t16\kp\faces\edit\edit_edit.prproj`. Each clip should show the same picture as your old edit.
3. **Try with another RAW:** on your next WRONG PAIR, press it and check the form.
4. **Find source:** paste your next 5 links. It uses NexLev / Algrow credits once per clip.
5. **A zip:** unzip one into another folder and import `edit\1_edit.xml` (I did this once: no Media offline).
6. **video21-fixed:** its frames 292-339 (a hold of RAW 963) and why its caption score is 0/70.
7. **The Ted 2 copy** `D:\mr_scratch\ted\Ted.2...reencoded.mp4` (10.2 GB): keep it as that RAW, or delete it.

## On the laptop

Close MovieRecaps, then in PowerShell:

```
cd C:\Users\gvida\Desktop\bh\MovieRecaps
git pull
.venv\Scripts\python tools\app\setup_app.py
```

- No new packages.
- Start MovieRecaps again. At its start (and every 10 minutes), it remakes the laptop's finished projects' zips as whole-folder zips (Task 2).
- **The NexLev / Algrow keys are only on the PC** (its Windows user environment). On the laptop, Find source works as before (your RAWs' sound, the post's text, IMDb / TVmaze) and says "key not found" for the AI. To use the AI there too, set the two variables yourself in the laptop's Windows settings (System > About > Advanced system settings > Environment Variables > User variables: `NEXLEV_API_KEY`, `ALGROW_API_KEY`), then start MovieRecaps again. The app never writes them anywhere.
- The song preset: in Premiere on the laptop, Effects > right-click **Presets > Import Presets...** and pick `assets\premiere\MovieRecaps song chain.prfpset` (once).

## 5 new suggestions (saved in `D:\Shared\Projects\suggestions.md`, not built)

1. **"Try with another RAW" says which RAW to get:** the row runs Find source on the competitor (free the second time: cached) and fills in the search words (small).
2. **An episode from the AI alone, checked against TVmaze's episode summaries:** the AI was "100 % sure" on 3 wrong episode numbers today (half a night).
3. **A smaller zip to send, without the tool's own checks** (`extras\debug`, compare.mp4, preview_recreation.mp4: about a quarter of a finished edit) (small).
4. **A RAW checked for readability right after the download**, converted there if needed, before the run (small).
5. **The slowest step on the board card**, e.g. "slow: placing frames 91 min" (small).

## What I decided on my own

1. **Find source's AI goes through NexLev's and Algrow's MCP servers** (the same tools as their Claude connectors), with your keys as Bearer tokens read from the user environment. Neither publishes a plain REST API for these tools.
2. **One song preset with Volume +13.5 / +7.5 dB**, as you asked, after measuring it the same as your chain either way Premiere orders the Volume. The two-preset variant (the Clip Gain inside each Input Boost) is in the code for a louder song.
3. **The song's Amplify is +3.00 dB** (your prompt), though your song.prproj has +4.5 dB. One number to change if you want 4.5.
4. **The whole movie leaves the project folder only when it is a link or copy of the download still in place**, never a conversion the app made (that would cost a re-conversion on Run again).
5. **The piece cut for V1 carries the RAW's sound as PCM only when A1 plays it**; otherwise A1's WAV is cut the same way, sample for sample.
6. **The zip is not made when its drive would drop under 40 GB free** (your disk rule).
7. **Task 6 converts in the queue (the app), not in the tool's RAW step:** a change there would remake every RAW's cached conversion (hours).
8. **The check-all ran the older cases first:** the new cases have no caches and took 44-93 min each; the older ones have a "before", so they decide "nothing gets worse".
9. **Task 8's switch-off runs use a second frozen copy of the code (`snap16b`)**, because the switches don't change the cache keys (a run with them would only get the cached result back).
10. **Learn's glossary lines were not taken** (as on night 15); your two censored words went into the censor list.
11. **I cleaned only my own scratch:** night 11's `work\t26\pairs` and `checkall` (11.3 GB), my test copies, the check-all runs' media links, my test temp folders. Stage caches were not touched.
