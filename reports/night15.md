# Night 15 (22 October): one task at a time

Started 02:11 (PC). The backup shutdown is set for 15:30. C: 73.7 GB free, D: 2.4 TB free at the start.

## The tasks

| # | task | state | how you use it | check by hand |
|---|---|---|---|---|
| 1 | Emoji in names (Premiere import) | **done** | nothing to do: new folders and files get plain names; old ones were renamed at the start of the app | open the 6 kept folders' projects as before (below) |
| 2 | WRONG PAIR stops right pairs | **done** | nothing to do. A wrong Family Guy episode is now stopped in seconds, not after the long search. | **Dolittle: download the real Dr. Dolittle 2** (the YTS file is the 1998 film) |

## Task 1 -- Premiere "File Import Failure" (emoji in folder names)

- **The cause, proven in Premiere 2024 on this PC:** the same edit (the Halloween run) imported twice.
  - In a folder named `...Costume of All Time 🎃😭`: **"File Import Failure"**.
  - In `...Costume of All Time` (no emoji): imported with no error (the sequence, raw_premiere.mp4, the WAV, the frame and the watermark in the bin).
  - Day 14 had already shown that non-English letters, `[ ]` and `( )` import fine. The `pathurl`s are correct: UTF-8 with `%` codes (`%20` for a space, `%5B %5D` for `[ ]`, `%C4%84` for Ą), and a network path keeps its server (`file://192.168.101.11/Shared/...`). A test checks this now.
- **What changed:**
  - New folder and file names keep only letters (any language), numbers, spaces and `- _ ( )`. Apostrophes go (`she’d` -> `shed`), `&` becomes `and`, and any other sign or emoji becomes a space.
  - The title with its emoji stays in project.json, title.txt and on the board. The sequence name (which Premiere uses to name the render) keeps the apostrophe but has no emoji.
- **Old folders:** the app renames them once, about 20 seconds after it starts. It uses day 13's safe rename: it waits while anything uses a folder, keeps every link, and updates the XML's media paths. On this PC, `D:\Shared\Projects` gave:
  - **renamed (2):** `2026-10-21_The Funniest Halloween Costume of All Time`, and `2026-10-21_The Most Dramatic Dad of All Time` (it was "... 💀😭 (2)").
  - **kept their name (6):** a Premiere project of yours plays their files, so a new name would make it "Media offline":
    - Lab Accident, Math Class and Driving Lesson are uploaded
    - Red Flag is fixed
    - milk -> `D:\Downloads\Copied_milk_POPW\...\milk_POPW.prproj`
    - kid -> `doro_POPW.prproj`

    These are finished, so you don't need to import them again. If you ever do, rename the folder by hand after closing its Premiere project.
  - The laptop renames its own copies when MovieRecaps starts there after `git pull`.
- **Tests:** app 1144 passed. 13 new tests (`test_names_night15.py`) and 9 updated old ones.
  - One old test (`test_locks`) failed only between 00:00 and 03:00, because "3 hours ago" fell on yesterday. I fixed that test.

## Task 2 -- WRONG PAIR on right pairs

I checked every pair you named with the quick check, and also 37 true pairs (every check-all case plus 3 real reels) and 90 crossed pairs (each competitor with other RAWs, the same show first). The results are saved in `work	31\pairs`.

- **Dr. Dolittle 2: the stop was right. The RAW is the wrong film.**
  - The torrent "Dr. Dolittle 2 (2001) [1080p] [BluRay] [5.1] [YTS.GG - YTS.BZ]" contains **Dr. Dolittle (1998)**:
    - its subtitles are the 1998 film's lines ("fluid on his lungs", "the cat napper")
    - it runs 1 h 25 min
    - at 25:00 its frame is exactly the frame of `Doctor.Dolittle.1998.1080p.BRrip.x264.YIFY.mp4`
  - Both reels (the TikTok and `DdVZePAga2I`) are really Dr. Dolittle 2: Raven-Symoné's Charisse, the report card dated 12-2-00, the phone.
  - So: sound 0 %, picture 0 % against both files. **Download a real Dr. Dolittle 2** (about 1 h 27 min) and it will run.
  - The stop message now also says: "...or a download named after one film that holds another (open the RAW and look)".
- **Are We There Yet: already fixed (day 13).** The check had remembered an old WRONG PAIR for that project, so every Try again stopped at once. A WRONG PAIR is never remembered now. Day 13's test case with a piece of the movie still passes.
- **The wrong Family Guy episodes: now caught.**
  - The darkhumor reel with S11E01 and with S08E11 had gone on ("too little to be sure"). Each then took the whole long search, only to end NOT IN THIS RAW.
  - Why it went on: in a cartoon, another episode's rooms look alike, so 8 of 16 frames were *almost* found. But they were scattered over the episode, mirrored one way and then the other, and at most 2 of them agreed in order and timing. A true pair's frames follow its scene: 4 to 16 agree on every check-all case and real reel.
  - **New rule:** WRONG PAIR when none of the competitor's frames is found, fewer than 3 of the almost-found ones agree, and the sound has nothing (0 pieces found, at most 1 almost found).
  - Measured: **no true pair stopped**, 3 more wrong pairs caught (both darkhumor ones, and video6's competitor with S11E01).
  - One wrong pair still goes on, which is the safe side: video19's competitor has 3 frames *really found* in S08E11, on a shot that comes back in many episodes. With frames found, the check is not sure.
- **The Jumanji promo** (Kevin Hart dressed as The Rock is not in the film): still a WRONG PAIR.
- **Right pairs still go on:** American Dad's tilted reel, Blue Mountain State, The Suite Life.
- **Test cases:** `tools\match_cuts	ests	est_night15_pairs.py` uses 14 real results as fixtures. On this PC it also checks the real files: wrong FG episode, Dolittle, American Dad.
- **Tests:** tool 1599 passed, 0 failed. One old test (`test_report`) also failed on yesterday's code: it read the report without UTF-8, so the "—" got garbled. I fixed that test.

## Time before -> after

## Still broken

## Check by hand

## On the laptop

## What I decided on my own
