# Night 15 (22 October): one task at a time

Started 02:11 (PC). The backup shutdown is set for 15:30. C: 73.7 GB free, D: 2.4 TB free at the start.

## The tasks

| # | task | state | how you use it | check by hand |
|---|---|---|---|---|
| 1 | Emoji in names (Premiere import) | **done** | nothing to do: new folders and files get plain names; old ones were renamed at the start of the app | open the 6 kept folders' projects as before (below) |
| 2 | WRONG PAIR stops right pairs | **done** | nothing to do. A wrong Family Guy episode is now stopped in seconds, not after the long search. | **Dolittle: download the real Dr. Dolittle 2** (the YTS file is the 1998 film) |
| 3 | Cartoons / tilted and zoomed reels | **partly done:** the stretch + "placed by sound" are done; the cartoon search changes are not (see below) | nothing to do: the stretch is found by itself (`--raw-stretch off` / `4:3` to force). On PICTURE NOT FOUND, import `1_edit_placed_by_sound.xml` | open the American Dad edit and one placed-by-sound XML in Premiere (below) |
| 4 | Honest progress | **done** (also the plain stage names and the "What it's doing now" console) | nothing to do: the queue line shows "Finding each competitor shot in the RAW · 40 % · about 25-35 min left"; click "What it's doing now" under the running row for the live lines, "Copy log" to send them | watch one long run in the queue |
| 5 | SceneProof template + its music | **done** (imported in Premiere 2024: no error, the song on A2 at +5 / -1 dB) | choose the channel **SceneProof** (New video buttons, Match cuts > Template) -- its card, its watermark and its song come by themselves | listen to `work\t31\sp\prem_test\edit\edit_edit.prproj` once |
| 6 | Pause / Resume | **done** | **Pause** on the running row; **Resume** on the paused row (also after closing the app -- closing it while a video is made pauses it) | pause one long run and resume it |
| 7 | Auto zip + renders by day | **done** | nothing to do: `<project>.zip` appears in each finished project; Media Encoder's renders go into `renders\<day>\` by themselves | send one zip to your friend; look in `D:\Shared\renders\` |
| 10 | aria2 ".aria2 control file" error | **already fixed** (day 13), checked again | nothing to do | -- |
| 12 | Find source (many clips at once) | **done** | Progress > **Find source**: paste the links (one per line) or drop clips, **Find all (N)**; Copy all; Torrent search per row | try it on your next 5 clips |
| 13 | The 3 suggestions | **1 not possible from the app (why below), 2 done, 3 already so** | open `3_pauses.txt` next to `1_edit.xml` and jump to each timecode | the audio chain: still the preset on A1 |
| 14 | Learn queue + problems | **done**: 7 learned (new test cases), 6 problems given their status | nothing to do; the S08 still in the 12 Year Old edit by hand | the 12 Year Old S08 at 0:05 |
| 11 | Ctrl+F search | **done** | **Ctrl+F** on any page; in a torrent's file list type `S09E05` / `9x05` / `ep 5`, then "Tick only these" | try it on a series torrent |

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

## Task 4 -- Honest progress (done)

- **Time left, from this computer:** the tool already estimates each stage from earlier runs on THIS PC (`work\run_times.jsonl`, 86 runs; night 6). The app now smooths it: each new estimate can move the whole run's time by **at most 20 %**. It is shown as a range: "about 25-35 min left", "about 3-5 min left", "about 1 h 40 min - 2 h 20 min left".
- **The % never goes backwards.**
- **A stuck step:** when the run writes nothing new (log and output) and its processes use no CPU for **5 minutes**, the queue line says "seems stuck (no output and no CPU for 5 min) -- Stop, then Try again?". A long quiet step that still uses the CPU (for example a conversion) is not called stuck.
- **Real case:** the progress lines of two real runs replayed through the old bar and the new one (`work\t31\pbs\progress_replay.py`):
  - American Dad (this PC): the old bar showed 16 % then 63 % within a minute, while its time went 41 -> 94 -> 29 -> 4 min, and it went backwards once. The new bar went 12 % -> 23 % -> 41 % -> 69 % (35-50 -> 20-30 -> 10-15 -> 3-5 min) and never back.
  - laptop009 (check-all): the old bar went backwards twice (93 % -> 88 %). The new one never did.
- **Tests:** app 1152 passed. 9 new tests (`test_progress_night15.py`). One old test (`test_locks` heartbeat) failed once on a Windows file-access clash and passes when run again (17 of 17). It is not related to this change.

### Task 4, second part -- plain stage names + "What it's doing now" (done)

- **Plain stage names:** the running row now says "Finding each competitor shot in the RAW · 40 % · about 25-35 min left", with the S-number small and grey after it. New video's edit line shows the same words. The report's timings table has a "step" column in plain words first.
  - S1 "Checking the RAW and the competitor are the same scene", S2 "Preparing the RAW (checking / converting)", S5.2 "Finding each competitor shot in the RAW", S5.3 "Placing every frame exactly", S5.4-S6 "Deciding the cuts", S8 "Making the Premiere files", S8 captions "Writing the captions", S9 "Double-checking every cut against the competitor", S10 "Writing the report" (all of them: `tools\match_cuts\match_cuts\stage_names.py`).
  - The taskbar button shows only the fill, no words, so nothing changed there.
- **"What it's doing now":** under the running row, "▾ What it's doing now" opens or closes a small panel. It is remembered (Settings file, `console_open`), and one click sets it for every row.
  - Each line has the time and plain words, newest at the bottom (it stays where you are if you scroll up). For example:
    - `07:52  The sound of the competitor was found in the RAW (6 of 7 pieces) -- the same scene, going on`
    - `07:53  Finding shots -- 118 of 278 pieces searched, 42 %, about 2 min left in this step`
    - `07:55  Found in the RAW: 100 % of the picture, 94 % of the sound`
    - `07:57  Double-checking cuts -- the cuts: needs a look`
  - The last line is live and changes every 2 s: `now: Preparing the RAW -- converting 13.1x real time, 19 of 22 min done -- 1 min 34 s in this step`. The conversion speed comes from how much of the RAW the ffmpeg process has read (no change to the tool, so no cache is touched).
  - A step's newer count replaces its older one, so a long step doesn't fill the panel. Steps that take under 1.5 s are not shown. The checks' technical notes stay in the report, not here.
  - **Copy log** copies all the lines. The full technical log stays in the run folder (`extras\match_cuts.log`).
- **Real test:** American Dad as SceneProof, run as the queue runs it, the console read every 2 s: 166 updates in 338 s, never more than 2 s apart. The RAW's conversion showed 22 min of RAW done in about 100 s (13x real time).
- **Not done:** per-shot lines like "Shot 24 found at 1:12:05 (very sure)" or "Cut 13 is 2 frames off, fixing it". The tool doesn't log those as it goes, and changing what the search logs means changing the search modules, which would throw away every cache (hours of re-runs). The counts ("118 of 278 pieces searched") and each check's result are shown instead.
## Task 5 -- SceneProof template + its music (done, checked in Premiere)

- **What I found in your projects:**
  - `kid.png` (V4 in song.prproj) is the per-video card: the white card, the round photo, "SceneProof", the blue badge, the grey @SceneProof, the bold "The fact that..." text, and a transparent video window. The window is exactly where your other channels have theirs.
  - `ScenmeProof_00000.png` (V3) is the faint white "SCENEPROOF" word low in the picture, so it is the channel's **watermark**.
  - The V2 Color Matte pieces are short bits you placed by hand at a few moments. I can't tell what rule places them, so they are not made (see the decisions below).
  - The music: two clips of "Mother Sea" on A2, from 0.52 s into the song, cut at 12.32 s, Clip Gain 1.778 (+5 dB) then 0.891 (-1 dB).
- **What changed:**
  - **A new channel, `assets\channels\SceneProof`**: `template.png` (the card without a headline, made from your kid.png), `example.png` (your kid.png, the "default template"), `watermark.png` (= ScenmeProof_00000.png), the song, and `channel.json` with the music settings.
    - A channel can now be a `template.png` instead of a `template.psd`. A new PNG channel needs no code change either.
  - **Choosing it:** SceneProof appears with the other channels in New video (a blue dot) and in Match cuts (Template). The channel you used last stays the default, so nothing changes until you pick SceneProof. Settings has no template choice today, so I didn't add one there (the choice is per video, as for your other channels).
  - **The headline:** the same font, size and place as your kid.png (checked: same rows and size, same letters). Only the line break can differ: the app keeps both lines inside 920 px, and your kid.png put "the" on the second line. Press Enter in the title to break where you want.
  - **The music, on every SceneProof edit:** A2, from 0.52 s into the song, cut at 12.32 s, +5 dB before and -1 dB after, ending where the video ends. **A1 is not touched.**
    - The song is added after the edit's checks, so the checks of your A1 sound stay exactly as before.
    - **The level is the clip's Volume, not its Clip Gain:** day 11 proved that no XML can set Clip Gain in Premiere, while Audio Levels arrives as the clip's Volume. So you get Volume +5 dB / -1 dB, which sounds the same.
    - Premiere makes the song a mono clip of its first channel, as it does with A1.
    - The song is put into the run's `extras\media` next to the RAW.
  - Tool switches: `--music FILE --music-start 0.52 --music-split 12.32 --music-gains 5,-1`.
- **Real test:** American Dad made as a SceneProof video (`work\t31\sp\run_ad\1_edit.xml`, your header example's headline "The fact that his dad was this honest is hilarious"):
  - V1 RAW (18 clips), V2 the SceneProof card, V3 the watermark
  - A1 the RAW's sound (18 clips, unchanged), **A2 the song: 0.00-12.30 s at +5 dB, 12.30-25.15 s at -1 dB** (the edit is 25.15 s)
  - (The run's FAIL is the American Dad notes from task 3, not the music.)
- **Imported in Premiere 2024 (07:41, the PC free):** the app's Open in Premiere made `work\t31\sp\prem_test\edit\edit_edit.prproj` and imported the edit with **no error and no message**. Saved and read back:
  - V1 the RAW (18 clips), V2 the SceneProof card, V3 the watermark; A1 the RAW's sound (18 clips, its levels as before)
  - **A2: "Mother Sea" 0.00-12.30 s, Volume +5.0 dB, then 12.30-25.15 s, Volume -1.0 dB** -- the same two levels as song.prproj (Premiere stores them as 1.778 x and 0.891 x)
  - The song starts 0.50 s into the file, not 0.52 s, and the cut is at 12.30 s, not 12.32 s: an XML places clips on whole frames (this edit is 23.976 fps, one frame = 0.042 s). 20 ms is not audible.
- Also imported: the placed-by-sound American Dad (`work\t31\pbs\run_auto\1_edit_placed_by_sound.xml`): no error, V1 5 clips + V2 the frame, A1 5 clips, the marker in it.
- **Tests:** tool 1649 passed, 0 failed; app 1159 passed (the upload check now also lists @SceneProof's public Shorts tab, with no login). 12 new tests (`test_sceneproof_night15.py`, `test_night15_music.py`).

## Task 6 -- Pause / Resume (done)

- **How you use it:** the running row has a **Pause** button. The computer is free at once, the row says "paused at Placing every frame exactly -- Resume goes on from there", and the queue stops (the other videos wait). **Resume** on that row runs it first, from where it was.
- **It goes on from the same step:** Resume runs the tool again into the **same run folder** (`--run-dir <it> --resume`). The steps it had finished come back from the tool's caches in seconds (the RAW's conversion, the sound search, the picture search ...). The step it was at starts again, and its log goes on in the same file.
- **The cache can't break:** Pause stops the process like Stop does, and the tool writes every cached result whole or not at all (a temporary file renamed into place), so a stop never leaves half a result.
- **After closing the app:** a paused video is kept in `%APPDATA%\MovieRecaps\paused.json` and comes back in the queue, paused, when the app opens. **Closing the app while a video is being made now pauses it** instead of throwing it away (the close question says so). Remove on a paused row forgets it but leaves its folder.
- A project's edit that is paused shows as "waiting" in project.json, not failed.
- **Real test** (Blue Mountain State S03E05 + its reel, through the app's own runner): the first run reached "Placing every frame exactly" after **869 s**. Pause stopped it in under a second. **Resume was back at that step after 48 s** (the RAW's conversion, the sound and the picture search all came from the caches), and the run finished: **PASS, 619 s after Resume**, `1_edit.xml` in the same folder. The pause cost about 40 s in all (869 + 619 s, against about 1450 s in one go).
- **Tests:** app 1192 passed, 0 failed.
- **Tests:** 7 new (`test_pause_night15.py`): the paused list survives a restart, Resume runs into the same folder with `--resume`, Pause stops a real child process and leaves the note, the rows' buttons, the queue stops on a pause, a paused project waits.

## Task 7 -- Auto zip + renders by day (done)

- **The zip:** every finished project gets `<project name>.zip` in its folder, made as soon as its edit is done. It is made again when its files change (for example when Open in Premiere saved the project). Projects made before got theirs at the app's start (a sweep every 10 minutes).
  - In it: `1_edit.xml`, `2_captions.srt`, the run's `.prproj` when there is one, `title.txt`, `media\` (every file the edit plays that is under 200 MB: the frame, the watermark, the song ...), and **`HOW TO OPEN.txt`**: how to import it, and what was **left out and where it is**.
  - Left out: a file over 200 MB that is on the shared drive (the movie, its WAV): the edit finds it there (the XML points at `\\192.168.101.11\Shared\...`). A big file that is NOT on the shared drive is listed as "send it yourself".
  - The zip and its folder have the name **without emoji** (Premiere can't import from an emoji folder).
  - **Real run:** 26 zips made for the finished projects in `D:\Shared\Projects` in 19 s, mostly 0.1-5 MB; the biggest is 294 MB (Weight Loss Class, which plays several small videos).
- **Renders by day:** every video Media Encoder made (from its own log, `Documents\Adobe\Adobe Media Encoder\<version>\AMEEncodingLog.txt`) is **moved** into `renders\<day>\` next to where it was written. `D:\Shared\xasd.mp4` made on 22 October is now `D:\Shared\renders\2026-10-22\xasd.mp4`.
  - **Never deleted:** a move on the same drive. A name already there gets "-2". A render newer than 2 minutes or still in use waits. Every move is written to `renders\moved.log`.
  - **Real run:** 9 renders moved (3 in `D:\Shared`, 6 in `Documents`), the old ones too.
  - To put them back: `.venv\Scripts\python -c "from mr_app.core import renders; print(renders.put_back(r'D:\Shared\renders\moved.log'))"` (from `tools\app`).
- **Tests:** 6 new (`test_zip_renders_night15.py`).

## Task 10 -- the ".aria2 control file" error (already fixed on day 13)

- It was already done on day 13, and I checked it again today:
  - a file already there without its `.aria2` is checked against the torrent (`--check-integrity=true`): the right pieces are kept and only the missing ones downloaded. A complete file is finished at once.
  - a file that can't be this torrent's (bigger, or none of its pieces right) is renamed "<name> (old)" and downloaded fresh. **A finished file is never truncated or overwritten** (`--allow-overwrite=false`).
  - aria2's error 13 coming back later is checked once by itself. Every aria2 error code is said in plain words, with what to do.
- **Test today:** the 6 tests that run the real aria2c (a finished file, a half file, a wrong-size file, error 13 ...) pass, and so do the 15 others about this.

## Task 11 -- Ctrl+F search (done)

- **The torrent's file list:** a search box over the files (when there is more than one): a live filter, "12 of 24", **Tick only these** (ticks what the search shows and unticks the rest), Esc clears it. It understands episodes written any usual way: `S09E05`, `s9e5`, `9x05`, `ep 5` (any season), `episode 7`, `e15`; anything else is words in any order.
- **Every page:** **Ctrl+F** opens a search box over the page you are on. It finds your words in everything the page shows (the queue's rows, the projects, the board's cards ...), outlines the one it is at, scrolls to it and says "3 of 12". Enter goes to the next one, Shift+Enter to the one before, and Esc clears and closes it. Switching to another page searches that page.
- **Tests:** 14 new (`test_find_night15.py`), one on the real window.

## Task 12 -- Find source, many clips at once (done)

- **Where:** Progress > **Find source** (next to Download). Paste links, one per line, or drop many files, then **Find all (N)**. Five are looked at side by side and the rest wait in line. Each row fills in as soon as its clip is done.
- **One row per clip, always the same columns:** `#` · **Clip** (its name, length, Instagram / TikTok / YouTube / file) · **Comes from** (big: "American Dad! — S03E02" or "Dr. Dolittle 2 (2001)"; small under it: the episode's title and what found it) · **How sure** (green **Sure 90 %+**, yellow **Check** with the 2nd guess, red **Not found** with why).
- **Torrent search** on each row copies the words to search ("American Dad S03E02 1080p") and opens a web search with them. The app has no torrent search of its own to fill, so paste the words where you search. **Copy all** copies the list: `3. Reel DczA1q3MTge — American Dad! — S03E02 — sure 98 %`.
- **History:** every list is kept (newest first, on the page), and the last list is back after a restart (`%APPDATA%\MovieRecaps\find_source.json`).
- **How it finds it (no login, no cookies):**
  1. the post's text (title, description, tags; yt-dlp reads them without downloading)
  2. **your RAWs on this computer** (30 movies and episodes: the projects' RAWs and the laptop cases). When the text names one, the clip's **sound** is looked for in it (the tool's own quick sound check), and found = **Sure**. When the text names nothing, the sound is looked for in your other RAWs too (episodes first, 12 at most, 4 minutes at most).
  3. the public title lists (IMDb's search suggestions, TVmaze for episode titles) for a name the text says that is not on this computer = **Check**.
- **Real batch, 5 at once, 54 s in all:**
  - the Dolittle 2 reel and the TikTok: **Dr. Dolittle 2 (2001) — check 65 %**. The text names it, but its sound is not in your "Dr. Dolittle 2" file, because that file is really the 1998 film (task 2). Right.
  - American Dad: **American Dad! — S03E02 — sure 98 %** (5 s)
  - the Family Guy clip (darkhumor): **Family Guy — check 60 %: "the episode is not one of your 4 (S11E10, S09E11, S11E01, S08E11)"**. Right: both of the episodes you had for it were wrong pairs (task 2).
  - Blue Mountain State (its post only says "...this is Jack Reacher..."): **Blue Mountain State — S03E05 — sure 99 %**, found by its sound in your RAW.
  - From the real Instagram link (not the file): American Dad sure 98 % in 8 s, with no login.
- **Not used yet:** the burned-in text and the speech. No free service matches them to a title without a login or a key, so they are named in a "Not found" reason instead.
- **Tests:** 24 new (`test_find_source_night15.py`): the name reading, the 6 answers above as fakes, the history, and the page (7 links: 5 at once at most, the rows, Copy all, back after a restart).

## Task 13 -- the three suggestions you liked

1. **The audio chain on A1 by itself in Premiere -- not possible from the app; the preset stays the one step.**
   - What 1_edit.xml already carries on every A1 clip: Normalize All Peaks to 0 dB (as the clip's Volume, measured on its own sound), Volume 0 dB, Panner 0.
   - What it can't: Premiere drops the Hard Limiter and Amplify from an XML. That was proved on night 11: written with Premiere's names or its filter IDs, they come in "not translated".
   - Doing it by itself needs a script running *inside* Premiere. Premiere can't be handed a script from outside (the app only starts it with the file). It needs an installed panel (a CEP extension), and an unsigned one needs a registry switch (`PlayerDebugMode`). I didn't install that on your PC without asking: it changes how Premiere loads add-ons. If you want it, say so and I'll build the panel ("Apply the audio chain to A1").
   - Today: select all A1 clips and drop **MovieRecaps audio chain** (Effects > Presets) on them: Hard Limiter (0 / 0 / 7 ms / 100 ms / 1.00) + Amplify +3.0 dB.
2. **A list of the pauses to take out -- done:** next to `1_edit.xml` every run now writes **`3_pauses.txt`**: each gap of 0.4 s or more between two words *inside* one clip (a gap at a cut is left out), with its timecode on the sequence, its length and the words around it:
   `1. 00:00:02:04 - 00:00:03:10  1.25 s  clip 2:  "certificate." ... "He's"`. Nothing is cut: no rule predicts which ones you cut (day 13: of 38 such pauses you cut 9). It follows `--speed` (125 %: the times of the 125 % edit).
   - Real: the 12 Year Old project: 1 pause (1.25 s). Blue Mountain State: none.
3. **Captions timed on the sound 1_edit.xml plays -- already so (since day 13):** the captions are made from the edit at 100 % (every clip at its own speed, as A1 plays it), with its silences, repeats and speech-safe moves cut out the same way as 1_edit.xml. Then the day-13 step moves any caption that runs off the sound onto it. The "28 captions start more than 2 frames from their first word" notes come from the caption rules (shortest length, the competitor's timing), not from the sound used.
- **Tests:** 4 new (`test_night15_pauses.py`).

## Task 14 -- the learn queue and the problems (done; the 7 new test cases' check-all is running)

- **Learn queue: all 7 open entries learned** (video21-27) and marked done in `learn_queue.md`. Each is a new test case in `tests\real\videoNN-fixed`: your finished edit as the key (captions, cuts and framing), with the channel's frame and watermark the run used (video21-24). The 7 now run in a third check-all stream, so the next nights are measured against your fixes.
  - What your fixes show, captions only:
    - **4 words you censor** that the censor list didn't have: `fuc*ity`, `s*ck` (video21), `prostit*tion` (video22), `cond*m` (video24). They are now in `caption_censor.txt` (whole words only: "sucking" stays as you keep it in video6).
    - Learn also wanted 5 caption-glossary lines: the same 4 words with the `*` dropped ("fuc ity", "prostit tion"), "Oh -> Ohhh" and "Fim -> FIM". **Not taken:** the glossary also goes to the speech recognition as hints, so any new line re-transcribes every video; "Oh -> Ohhh" would change every "Oh"; and the censored words now come from the censor list instead.
    - Your action texts (`*raises his hand*`, `*police rushes in*` ...) were added to the action examples, as learn does for every fix.
- **Problems queue: all 6 open entries have their status** in `problems_queue.md`:
  - **American Dad PICTURE NOT FOUND: fixed** (the stretch, task 3).
  - **12 Year Old (Benchwarmers), found, not fixed:** S08 (00:00:05:15, 0.6 s) is the competitor holding and zooming on 4 RAW frames (also used in S09-S10). The tool read it as a slow reverse (-0.30 speed), and the XML plays it backwards. **By hand:** put a still of RAW 300.59 s there. The fix is in the segment step, which needs a check-all from scratch, so it waits for a free night.
  - aqoevqvh: one take in two clips (S01 / S02) with the same framing. Nothing you'd see; join them if you like.
  - The Funniest Grandma (PERSON): who speaks is unclear there, so nothing moves on its own. Check S12 by hand.
  - The two Ted 2 stops ("could not decode source frames by PTS"): not captions, cuts or position, so not touched (task 14's scope). Convert that file in the Convert tab and press Try again.
## Time before -> after

- American Dad S03E02 + reel `DczA1q3MTge`: **6522 s, no edit -> 858 s (14.3 min), an edit**. Before = the laptop, after = this PC.
- A wrong Family Guy episode: **the whole long search, then NOT IN THIS RAW -> about 5 s at the quick check (WRONG PAIR)**.

## Still broken

- **Task 3, fixes 1-4** (the cartoon search changes): not done, they need a free PC for a whole check-all. **Tasks 6-16:** not started yet. **Task 9b** (refine faster, the Benchwarmers case): your message names it, but the saved `docs\night15_prompt.md` has no Task 9b yet (the Task 4 console part is there). I do it after Task 9 from your message's words.
- 6 finished projects keep their emoji folder names, because your Premiere projects play their files (task 1).
- The PC's backup shutdown is still set for 15:30 (`shutdown /a` cancels it).

## Check by hand

1. **American Dad** (`work\t31\ad\run2\1_edit.xml`): open it in Premiere. The picture should fill the window like the reel's. Then look at Stan at about 0:06-0:07 (the PERSON note) and the first 10 frames (the FLASH note).
2. **Placed by sound** (`work\t31\pbs\run_auto\run_auto_edit.prproj`, American Dad made from the sound alone; it imported with no error): look at the framing.
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
7. **SceneProof is a channel**, like your three others (its card, its watermark, plus its song), not a separate kind of template. That way it works everywhere a channel does.
8. **The V2 Color Matte pieces of song.prproj are not made:** they are a few short bits placed by hand, and I can't tell what rule places them.
9. **No screenshots of the desktop.** One Premiere test caught other windows on your screen, so I deleted those pictures and checked Premiere by its window title and messages instead.
