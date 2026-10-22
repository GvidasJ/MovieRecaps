# Night 15 (22 October): one task at a time

Started 02:11 (PC). The backup shutdown is set for 15:30. C: 73.7 GB free, D: 2.4 TB free at the start.

## The tasks

| # | task | state | how you use it | check by hand |
|---|---|---|---|---|
| 1 | Emoji in names (Premiere import) | **done** | nothing to do: new folders and files get plain names; old ones were renamed at the start of the app | open the 6 kept folders' projects as before (below) |
| 2 | WRONG PAIR stops right pairs | **done** | nothing to do. A wrong Family Guy episode is now stopped in seconds, not after the long search. | **Dolittle: download the real Dr. Dolittle 2** (the YTS file is the 1998 film) |
| 3 | Cartoons / tilted and zoomed reels | **partly done:** the stretch + "placed by sound" are done; the cartoon search changes are not (see below) | nothing to do: the stretch is found by itself (`--raw-stretch off` / `4:3` to force). On PICTURE NOT FOUND, import `1_edit_placed_by_sound.xml` | open the American Dad edit and one placed-by-sound XML in Premiere (below) |

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

I checked every pair you named with the quick check, and also 37 true pairs (every check-all case plus 3 real reels) and 90 crossed pairs (each competitor with other RAWs, the same show first). The results are saved in `work\t31\pairs`.

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
- **Test cases:** `tools\match_cuts\tests\test_night15_pairs.py` uses 14 real results as fixtures. On this PC it also checks the real files: wrong FG episode, Dolittle, American Dad.
- **Tests:** tool 1599 passed, 0 failed. One old test (`test_report`) also failed on yesterday's code: it read the report without UTF-8, so the "—" got garbled. I fixed that test.

## Task 3 -- American Dad and stretched cartoons (step 1 of 5 done)

- **The real cause was not a rotation.** The reel **stretched the 4:3 episode sideways to 16:9**:
  - a full fit on 13 frames gives width / height **1.32**, with rotation 0.1-0.3°
  - the picture search fits one zoom for both axes plus a rotation, so it could never line such a frame up. Its best guess was "rotated 5°, scale 0.79", with a score of 0.735 where 0.90 is needed.
  - Cartoons and old sitcoms are often 4:3, and clip channels often stretch them. That is likely part of "cartoons fail often".
- **What changed** (`tools\match_cuts\match_cuts\stretch.py`):
  - **The check:** right after the quick pair check (S1), the tool takes a few frames where the sound placed the scene and fits a full fit with a separate zoom per axis. When 3 or more frames agree on a sideways stretch of 10 % or more, the stretch is used (snapped to 4:3 when it is within 4 % of it).
  - **What it does then:** the RAW gets that shape before S2. S2 converts it once on the graphics card into a 1920x1080 picture, the whole analysis runs on it, and V1 plays that copy.
    - The report and the warnings say: "the competitor shows the RAW stretched 1.33 times sideways ... check the framing in Premiere".
    - With a stretch, no piece of the movie is cut.
  - **Switch:** `--raw-stretch auto` (default) / `off` / `4:3` to force a value.
- **American Dad, before -> after:**

  | | before | after |
  |---|---|---|
  | where | laptop, old code | this PC, new code |
  | picture found | 0 % (after 2 h) | **100 %** |
  | result | PICTURE NOT FOUND, no edit | **1_edit.xml + captions** |
  | time | 6522 s (109 min) | **858 s (14.3 min)** |

  - The side-by-side `compare.mp4` lines up frame for frame with the reel.
  - The run says FAIL on two XML notes that you check by hand:
    - **PERSON:** the live-action face check on a cartoon puts Stan's face at the window's edge, where the reel itself shows him. Day 13 also left cartoon PERSON notes to you.
    - **FLASH:** 10 frames of another shot at 0:00.
  - Run folder: `work\t31\ad\run2`.
- **No false alarms:** all 36 other true pairs (every check-all case plus Blue Mountain State and The Suite Life) say "not stretched" (ratio 0.995-1.003). Competitors sped up to 125 % are not checked (the safe side).
  - So check-all cannot change: the stretch is off on all its cases, and their conversion keys stay the same.
- **Tests:** tool 1606 passed, 0 failed. 8 new tests (`test_night15_stretch.py`), including the real American Dad files on this PC.

### Task 3, fix 5 -- never nothing: "placed by sound" (done)

- **What changed:** when the sound is found but the picture is not, even after the second search (PICTURE NOT FOUND), the run still stops, but it first makes **`1_edit_placed_by_sound.xml`** in the run folder (`placed_by_sound.py`):
  - the reel's length on the usual sequence (your frame on V2, the RAW's rate, the audio chain on A1)
  - V1/A1: the RAW where its **sound** placed each part of the reel (one clip per jump the sound found)
  - the RAW fills the window, centred, because the reel's framing is not known
  - a marker over the whole sequence: **"PLACED BY SOUND -- check the framing"**, and "NOT IN RAW" markers where the sound placed nothing
  - the stop message ends with: "A project with the RAW placed by its sound was made instead: 1_edit_placed_by_sound.xml (96 % of the competitor) -- placed by sound, check the framing."
- **How you use it:** when a run says PICTURE NOT FOUND, open its run folder and import `1_edit_placed_by_sound.xml` in Premiere. Then fix the framing (and any cut that is a little off) by hand.
- It is never named `1_edit.xml`, so the app still shows the stop, and nothing treats it as a normal edit.
- It is not made when the sound places less than 25 % of the reel, with `--force-inputs` (that makes the normal edit anyway), or while only a piece of the movie was analysed (the run is then made again on the whole movie first).
- **Real test:** American Dad, from the sound alone: 5 clips covering 100 % of the reel. Against the edit the picture search made, 80 % of the points are within 0.1 s (median 0.045 s). The ones further off are next to cuts, up to 0.9 s, which is why the marker says to check.
- **Tests:** tool 1643 passed, 0 failed; app 1144 passed. 9 new tests (`test_night15_placed_by_sound.py`), one of them on the real American Dad sound.

### Task 3, fixes 1-4 -- not done (left on purpose)

- 1. the edge (lines) comparison for cartoons, with the burned-in captions masked
- 2. trying ±8° and other zooms (American Dad was a stretch, not a rotation: its frames fit with 0.1-0.3°)
- 3. accepting lower scores inside the place the sound found
- 4. stopping within minutes

These change the picture search itself, so every check-all case would have to be run again to show that nothing gets worse. That is hours of GPU time while you're using the PC. The named case is now fixed (the stretch), and the placed-by-sound project means a cartoon whose picture is not found still gives you something to work from. I left them for a night when the PC is free.

## Time before -> after

- American Dad S03E02 + reel `DczA1q3MTge`: **6522 s, no edit -> 858 s (14.3 min), an edit**. Before = the laptop, after = this PC.
- A wrong Family Guy episode: **the whole long search, then NOT IN THIS RAW -> about 5 s at the quick check (WRONG PAIR)**.

## Still broken

- **Task 3, fixes 1-4** (the cartoon search changes): not done, they need a free PC for a whole check-all. **Tasks 4-16:** not started yet.
- 6 finished projects keep their emoji folder names, because your Premiere projects play their files (task 1).
- The PC's backup shutdown is still set for 15:30 (`shutdown /a` cancels it).

## Check by hand

1. **American Dad** (`work\t31\ad\run2\1_edit.xml`): open it in Premiere. The picture should fill the window like the reel's. Then look at Stan at about 0:06-0:07 (the PERSON note) and the first 10 frames (the FLASH note).
2. **Placed by sound** (`work\t31\pbs\run_auto\1_edit_placed_by_sound.xml`, American Dad made from the sound alone): import it in Premiere and check that it opens with no error, with the marker on top.
3. **Dr. Dolittle 2:** the YTS download named "Dr. Dolittle 2" is the 1998 film. Download a real Dr. Dolittle 2 (about 1 h 27 min).

## On the laptop

Close MovieRecaps, then in PowerShell:

```
cd C:\Users\gvida\Desktop\bh\MovieRecaps
git pull
```

No new packages. Start MovieRecaps again. About 20 seconds after it starts, it renames the laptop's own emoji project folders (the ones no Premiere project uses), once.

## What I decided on my own

1. **Apostrophes are dropped from folder names** (`she’d` -> `shed`), the safest reading of "only letters, numbers, spaces and - _ ( )".
2. **Folders your Premiere projects play keep their emoji names**: a rename would make those projects "Media offline", and they are finished.
3. **The pair check's new stop needs the sound to have nothing** and not one frame found, so no measured true pair can be stopped. One look-alike Family Guy pair still goes on (the safe side).
4. **A stretched RAW is converted and V1 plays the copy.** Premiere's XML import has no tested way to stretch a clip sideways, so the stretch is done in the copy instead of in the XML.
5. **The placed-by-sound project is a separate file** (`1_edit_placed_by_sound.xml`), not `1_edit.xml`, and the run still says PICTURE NOT FOUND, so it is never mistaken for a checked edit.
6. **The cartoon search changes (task 3, fixes 1-4) wait** for a night when the PC is free: they touch every run and need a full check-all to prove nothing gets worse.
7. **No screenshots of the desktop.** One Premiere test caught other windows on your screen, so I deleted those pictures and checked Premiere by its window title and messages instead.
