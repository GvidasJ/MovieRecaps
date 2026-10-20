# Day 13 (20 October): yesterday's problems fixed, the audio chain at +3 dB, the original RAW in Premiere, names from the title, five fixes learned, the person speaking in the middle

## In short

Everything on the list (sections 1-11) is done, committed and pushed; what is not, and why, is under *What's left*. Two wishes stay open with what I learned: captions a little too early (now moved onto the sound where the edit's transcript runs off it -- mindculpt -- but not Always Sunny's) and Liquid Death's pause cuts (no safe rule yet). The final check-all (33 of the 34 test videos, on the final code) is better than this morning and **no video is lower on any measure**: hard-check FAILs **5 -> 1** (only video020's known --fast flash), captions exactly as yours **239 -> 248**, framing as yours **107 -> 113**, cuts the same.

1. **Yesterday's problems** (section 1):
   * **New video -> WRONG PAIR**: the New video path gives the tool exactly the same pair as a drop (checked with the
     same files, both pass). The real bug: the quick pair check **remembered a WRONG PAIR** for a pair of files, so
     every Try again of that project stopped in a second; a drop made a new project and was checked fresh. Fixed (a
     WRONG PAIR is always checked again), and the stop now names both files with their length, picture, fps, codec,
     size and sound -- a test case with a piece of the real movie.
   * **The real reason a run stopped**, in plain words everywhere (queue row, report, History, the problems cards):
     ffmpeg 4294967268 = "The disk C: ran out of space"; the lost state-4.pkl = "the run crashed after C: filled up".
   * **Free space**: nothing starts (edit, New video, download, conversion) without room -- it says what is needed and
     what is free, and offers Clean up. The top strip shows **Free C: … · D: …**, amber under 30 GB, red under 10 GB.
   * **setup_app.py** no longer crashes on a broken PySceneDetect (it installs 0.7.1). **The same versions**: the app
     compares its packages at the start and shows one amber line with **Fix now**.
   * **Torrents**: "File exists, but a control file (*.aria2) does not exist" never shows again -- the file is checked
     against the torrent, the right parts kept, a finished file finished at once, a wrong file renamed "(old)"; every
     aria2 error in plain words.
2. **The audio chain is +3.0 dB** (Amplify), in the tool, the Premiere preset (made again in Premiere: it shows 3.00
   dB), the checks, the READMEs and `standing_rules.md`.
3. **The original RAW plays in Premiere**: a run keeps the original (hard link or copy) + a small stereo WAV for A1;
   the converted copy is only for the analysis and is deleted at the end. Borat re-run: **15 GB -> 4.0 GB copy + 0.97 GB
   WAV** (0 GB for the picture when the RAW is on the same drive); in Premiere the planned frame at start, middle and
   end, A1 in sync, Learn finds the run. Clean up offers old runs (Borat: 13.1 GB).
4. **File names = the video's title** (YouTube title > headline > the post's title), Windows-safe, emoji kept, at
   most 100 characters, (2) / (3); the queue, board and History show the title; **Rename to title** for old projects.
5. **Learned** video16-video20 (survive, add, monkey, butter, cop / mindculpt): one new rule (censored words as you
   write them: bit\*h, murde\*ed, cra\*kheads) + the caption fixes of section 6.1. check-all before -> after in section 5 (nothing lower).
6. **Wishes**: 6.1 -- the one-frame Premiere shift is where you leave your captions (unchanged); a new step moves
   captions onto the sound where the edit's transcript runs off it (mindculpt: 27 moved, exactly as yours 5 -> 7),
   but not Always Sunny's, so the wish stays open with a note; 6.2 -- Liquid Death runs and is a test case with your
   edit as the key -- your 10 cuts are pauses you took out yourself, no safe rule yet; 6.3 -- Stats and Topaz marked
   "later -- not now".
7. **The problems list**: all 5 open entries done -- 4 fixed (Are We There Yet, Borat x3), Family Guy: the flash and
   the ending fixed, the person speaking at 2:12 left for you (a cartoon -- see section 7).
8. **The person speaking in the middle**: a new careful rule (Light-ASD, the person speaking, never the biggest face).
   Framing as you had it **107 -> 113 of 223** (video14 0 -> 4 of 10, video11 1 -> 2, laptop013 1 -> 2), never the wrong person; its side effect on two captions removed.
9. **Small edit notes fixed by the tool itself**: A1 follows a snapped V1 cut ("A1 range is not a V1 cut range"), a
   1-2 frame flash at the very end is cut, a burst of tiny cartoon shots is the RAW's own (not a flash); word pairs
   kept together (a, the, my ... + their word; Hong | Kong). video6 and video12 now pass their hard check.
10. **Learned by myself**: a **one-frame jump inside a take** is put back (video14 and video16 failed their hard check on it; your fixes play those takes straight through), the speaker rule's side effect on two captions removed, a word cut off on board cards fixed, Borat checked in Premiere; the biggest differences left measured (sideways framing: 77 of the 103 clips framed unlike you; your own pause cuts: no rule fits).
11. **7 new suggestions** at the end, also in `suggestions.md`.

Tests: the app **1129 passed**, 0 failed; the tool **1539 passed**, 0 failed (both on the final code, the graphics card hidden); 138 new tool tests and 147 new app tests today.

## 1. Yesterday's problems

### 1.1 New video -> WRONG PAIR (Are We There Yet)

**What each way gave the tool** (compared on this PC: the stopped project itself stayed on the laptop, so I rebuilt it
here with the same files and the app's own code -- new project, download, convert, roles, edit command):

| | the drop (ran) | New video (rebuilt here) |
|---|---|---|
| competitor | the .webm as it is: VP9 1080x1920, 30 fps, 748 frames, 0:25, Opus 2 ch | the same video converted by the app: H.264 1080x1920, 30 fps, 748 frames, 0:25, AAC 2 ch |
| RAW | Are We There Yet …mp4: H.264 1920x1072, 23.976 fps, 1:34:53, AAC 6 ch, 7.49 GB, hard-linked | the same file (the torrent .mkv rewrapped by the app: byte for byte the laptop's size), copied in |
| command | --premiere --fast, the template + watermark | the same + --no-swap (the form's RAW field) |
| quick pair check | sound 6 of 6 | **sound 6 of 6** (picture forced: 13 of 16 frames; also over a network path) |

So the New video path gives the tool the same pair. **Why it "always" stopped:** the quick pair check kept its answer
for a pair of files (by their paths, sizes and times) -- **including a WRONG PAIR**. Once a project's run had stopped,
every Try again of that project got the same stop from that memory in a second; a drop makes a new project (new paths)
and was checked fresh -- so the drop ran. **Fixed: a WRONG PAIR is never remembered** (checked again: seconds).

The very first stop could not be rebuilt: "sound 0 of 4" means that run read *another* competitor sound (this
competitor always gives 6 pieces), and "picture 0 of 16" that its picture was not in the RAW. So it can never be a
mystery again: the run's log, the stop message and the app's result (project.json) now say exactly what each file was
(length, picture, fps, codec, size, every sound stream, how the app made it), and a WRONG PAIR of a project names the
copies that are not your files. Test case: `tools\match_cuts\tests\data\are_we_there_yet` (2 minutes of the movie +
the .webm), `test_day13_pairs.py` (the tool) and `test_day13_newvideo_pair.py` (the app: both ways give the same pair,
both pass).

### 1.2 The real reason a run stopped

One shared function (`match_cuts\plain_reason.py`) used by the batch and the app. The tool's own stops (WRONG PAIR,
PICTURE NOT FOUND, NOT IN THIS RAW, the input checks) stay exactly as they are; everything else in plain words with
what to do:

* **Borat edit-2 / edit-3** (ffmpeg 4294967268 = -28): *"The disk C: ran out of space: the run stopped while converting
  the RAW. Free space on C: (Board -> Clean up), then press Try again."*
* **Borat edit** (state-4.pkl): *"The run crashed after C: filled up: the tool's helper processes could not read their
  working file in the temp folder."*
* every other ffmpeg code (out of memory, file not found, access denied, a damaged video …), Python / Windows errors
  (graphics card memory, RAM, a file in use, a network folder gone …), Windows' crash codes, and otherwise the last
  real ERROR line -- never a traceback or a library warning.

Shown on the queue row (with **Try again** when it was not the input's fault), in the report ("Why it stopped / What
to do / From the log"), History, the New video row, project.json, problems_queue.md and the Learn tab's problem cards
(the three old Borat cards now read in plain words). Checked on 742 real logs on this PC: 25 stopped runs, each said
right.

### 1.3 Free space before every start, and in the top strip

* **Nothing starts without room**: Match cuts Start (the queue added up per drive), each run again just before it
  starts, New video (download + conversion + edit together), conversions; torrents keep their own check. It says, e.g.:
  *"not enough free space on C: -- about 28.1 GB is needed there, only 9.2 GB is free (borat: a temporary copy of the
  RAW for the analysis 14.1 GB; the tool's saved analysis 5.7 GB; a copy of the RAW 3.8 GB; the run's own files 1.0
  GB; the RAW's stereo sound for Premiere 921 MB) -- free some space (Board -> Clean up), then press Start again"* with
  **Clean up** / Not now. No "Start anyway". The numbers are named constants at the top of `core\space.py`.
* **The top strip**: `Free C: 64.2 GB · D: 2.6 TB` (C:, the projects' drive and "Save projects to"), each fading to
  amber under 30 GB and red under 10 GB, read in the background every 2 minutes and after every run; a click opens
  Clean up.

### 1.4 setup_app.py and a broken PySceneDetect

It reads pip's records first (nothing imported), then `find_spec("scenedetect.output")` inside try / except: missing,
old or broken -> "PySceneDetect 0.7.1 is broken: it does not load (…)" -> installs 0.7.1. A test makes `find_spec`
raise ModuleNotFoundError: setup_app goes on, installs and ends 0.

### 1.5 The same versions on both computers

One table (`core\versions.py`) for setup_app.py and the app: PySceneDetect 0.7.1, PyTorch (+ CUDA with an NVIDIA
card), yt-dlp, aria2c 1.37, FFmpeg 6+, PySide6 6.12.0, faster-whisper, psd-tools, the tool and its packages, deno. 4 s
after the start the app checks in its own process; anything wrong -> one amber line under the top strip, e.g.
*"This computer differs from what the project expects: aria2c 1.19.0 is too old (1.37.0 needed)"* + **Fix now** (runs
setup_app in the background, the line turns blue "Fixing …", then green "Fixed ✓" or says the step by hand). Fix now
waits while a video is made, downloaded or converted. Seen in the real window on this PC: **this PC's aria2c is 1.19.0
(2015)** from `C:\aria2` -- the app flags it; setup_app (section 13 run) puts 1.37.0 into `tools\app\bin`.

### 1.6 Torrents: "File exists, but a control file (*.aria2) does not exist"

Where it came from: aria2c deletes its `.aria2` when a torrent is complete, so adding the same torrent again (the next
Short of the same film) stopped with error 13. Now, before aria2c starts and there is no `.aria2`:

* a file **bigger** than the torrent's, or whose sampled pieces **all** differ from the torrent's -> renamed
  **"<name> (old)"** and the video is downloaded fresh;
* everything else is checked by aria2c: the right pieces are kept, only the missing ones are downloaded; **complete
  -> finished at once** (nothing downloaded, the file not written);
* a file with its `.aria2` goes on exactly as before; the `.aria2` stays until finished (also over a network folder:
  tested on `\\127.0.0.1\C$`);
* **all 32 aria2 error codes** in plain words with what to do; Remove never deletes a file the job found in the folder.

Tested with real aria2c 1.19 and 1.37 on 127.0.0.1. Every aria2c a test starts now listens on 127.0.0.1 only (the
Windows firewall question of this morning came from a test -- see Check by hand 0).

## 2. The audio chain: Amplify +3.0 dB

`Normalize All Peaks 0 dB -> Volume 0 dB -> Hard Limiter (0 / 0 / 7 ms / 100 ms / 1.00) -> Amplify +3.0 dB -> Panner 0`
everywhere: the tool (`AMPLIFY_DB = 3.0`), the run report's note, the XML AUDIO check, both READMEs, the tests, and
**`assets\premiere\MovieRecaps audio chain.prfpset` made again in Premiere** (its contents have a checksum of an
unknown kind, so I edited Amplify in Premiere and exported the preset). Checked in Premiere: dropped on a clip, Effect
Controls shows Hard Limiter 0 / 0 / 7 / 100 / 1.00 and **Amplify 3.00 dB**, Panner 0; the saved project reads back
Amplify 0.6875 (= +3.0 dB) on both clips (picture: `work\t28\premiere\premiere_chain_3dB.png`). Your Premiere preset
folder was backed up before and put back byte for byte after. `standing_rules.md` (shared folder, the Learn tab's
Standing rules) now says: *"Audio chain: Normalize All Peaks 0 dB → Volume 0 dB → Hard Limiter (0 / 0 / 7 ms / 100 ms /
1.00) → Amplify +3.0 dB → Panner 0. Never change the audio from my fixes."* (Seen: your own preset "GoodAUDIO" is the
same chain with Amplify +4.5 dB and the Hard Limiter's Input Boost not at 0 -- use "MovieRecaps audio chain" from now
on.)

## 3. The original RAW in Premiere

**What a Premiere run keeps now** (`match_cuts\edit_media.py`):

* **V1 = the original RAW** (`extras\media\raw.<ext>`): a hard link on the same drive (no space), else a copy. An .mkv,
  or a picture that starts late (the x265 web Family Guy RAWs, 21 ms), is **remuxed** (nothing re-encoded) -- checked
  frame for frame after.
* **A1 = `raw_stereo.wav`** when the sound is 5.1 / not AAC / starts late: 16-bit 48 kHz stereo made with the analysis
  copy's own sound steps, checked **sample for sample** against the analysis copy at 5 s / middle / end. A WAV, not
  .m4a: AAC starts with 1024 "priming" samples that a program must skip from the file's own notes -- Borat's old
  raw_stereo.mp4 has exactly that (21 ms); a WAV has none, so it is in sync in every program.
* **Only when the original cannot be used** (a variable frame rate, rotation, VP9 / AV1 / WebM, interlacing …): a
  copy at **H.264 CRF 17, preset slow**, and the reason is written in the run's report and the end summary.
* The analysis copy is **deleted at the end** of the run ("the analysis copy media/raw_ae.mp4 removed (14.01 GB)").

**Borat** (the laptop's settings, the real x265 10-bit 5.1 film, re-run here; section 7): finished, deliverables
PASS. `extras\media`: `raw.mp4` 4.04 GB (the original, copied: the run folder was on C:, the RAW on D: -- a hard link
on one drive = 0 GB) + `raw_stereo.wav` 0.97 GB. **Before: a 15 GB raw_stereo.mp4 stayed.** C: during the run: 87.2 ->
63.3 GB free at the peak, back up at the end.

**In Premiere 2024** (the app's Open in Premiere on that run): nothing offline, the HEVC 10-bit original plays. I
exported frames from Premiere (V2 / V3 hidden) at 0:00.58, 0:11.80 and 0:22.90 and compared them with the planned RAW
frames 53607 / 54109 / 54392 decoded from the file: **each is exactly the planned frame** (match 0.999; its neighbours
0.94-0.99), so the picture is frame-exact, and A1 is the sample-exact WAV -> **in sync** at start, middle and end. A1's
clips carry the XML's volume steps; the preset goes on top as always. Saved, then Learn: **it finds the run** (V1
raw.mp4, A1 raw_stereo.wav, 9 + 9 clips) and the app's learn card opens.

**Space**: a new 5.1 film run keeps ~0 GB of picture (hard link) + 0.69 GB of WAV an hour, instead of ~10 GB an hour.
**Board -> Clean up** has a new offer, *Keep the original RAW*, for older runs: only when the original is still here
and is the very file the run was made from (checked by its hash), Premiere can play it as it is, and **no Premiere
project plays that run's raw_stereo.mp4** (Desktop, the project folder, D:\Shared searched, only read). It shows each
run and the space saved, and changes nothing before Yes. Dry run on this PC's shared folder: **Borat 13.1 GB**; the
other 5 old copies are kept (2 originals gone, mindculpt's RAW has a variable frame rate, the 2 Family Guy pictures
start 21 ms late). I did not press Yes (your projects).

## 4. File names = the video's title

* The title: the **YouTube title**, else the **headline** (both lines joined), else the **competitor post's title**,
  else the headline read on the competitor's video. Windows-safe (`\ / |` become a space, `: * ? " < >` removed),
  **emoji kept**, never a device name (CON, NUL …), at most 100 characters (less when the path would pass Windows'
  260), cut at a word; taken -> (2), (3).
* New projects: `<date>_<title>` folders (New video, a drop with a title, steps.py); the folder follows the title as
  soon as one comes, before the edit. Next to `1_edit.xml` the app writes **`<title>_edit.xml`** (the same edit, the
  sequence named after the title -- so Premiere names the render after it); Open in Premiere opens it in
  **`<title>.prproj`**. `1_edit.xml` itself never changes (every check and Learn find runs through it).
* **Renaming later** (a title changed, or **Rename to title** on an old card): this computer's folder and its shared
  copy, the app's XML copy and its empty project; project.json, the Learn card, the problems list, the queue and board
  keep their links. It **waits** while a run, a copy, Clean up or Premiere uses the folder.
* **A folder a Premiere project plays from keeps its name** (decided: your fixed projects on the Desktop / in
  `D:\Shared\Copied_*` play the RAW from the shared folder by its full path -- renaming would make them "Media
  offline"). Its card still shows the title.
* The queue row, board card, History and New video row show the title (the folder in the tooltip).

## 5. Learned from your fixes (video16 - video20)

From the app's copies (`finished\videoNN`, byte for byte your Desktop files): video16 = survive.prproj (Always Sunny),
video17 = add.prproj (Tobey Maguire), video18 = monkey.prproj (Family Guy S10E18), video19 = butter.prproj (Family Guy
S11E10), **video20 = cop.prproj** (mindculpt / Rush Hour, its run in
`D:\Shared\Projects\2026-10-19_video-by-mindculpt\edit` -- added to the learn queue). All marked learned. Only
captions, cuts and video position / scale were compared.

| what you changed | done |
|---|---|
| **censored words**: bit\*h (video14 too -- day 12 missed it), murde\*ed (video19), cra\*kheads (video20) | **rule**: `caption_censor.txt` writes bitch / bitches / murdered / crackhead(s) as you do (Learn's glossary lines "murde ed" / "cra kheads" -- the \* dropped -- not adopted) |
| caption timing: video16 30 of 37 captions later (median 0.33 s), mindculpt 17 of 27 earlier in its second half | **section 6.1** (moved onto the sound) |
| word pairs: "my \| daddy", "Hong \| Kong", "on the \| island" joined | **section 9** (word pairs kept together) |
| framing: you moved 9-16 clips sideways / zoomed in video16, 17, 19, 20 | **section 8** (the person speaking) |
| your own action texts in `*...*` spots | not a rule (your words) |
| trims at cut points, 1-8 small cuts inside clips (pauses) | yours by hand, as you asked (the pauses: section 10) |

Five new test cases `tests\real\video16-fixed` … `video20-fixed` (each with its channel template and watermark as the
laptop ran it). **check-all before -> after** (before = this morning's code; after = tonight's final code, 33 of the
34 test videos -- all but Liquid Death):

| | before | after |
|---|---|---|
| hard-check FAILs | 5 (video6, video12, video14, video16, video020) | **1** (video020: the known --fast flash) |
| captions exactly as yours (the scored videos) | 239 of 734 | **248 of 734** |
| cuts reproduced | 49 of 142 | 49 of 142 |
| framing as you had it | 107 of 223 | **113 of 223** |

Before = day 12's final check-all for the 28 older videos (the analysis code was the same this morning) and this morning's runs for the 5 new ones; after = tonight's final code (the streams ran on the day's last snapshots; where those differ from the final code they cannot touch the videos they ran -- checked by replays; video11, video14, video16 and laptop013 ran on the final code itself). Liquid Death was left out: a new case with no before (its own run: `work\t28\ld\run1`). Per video (bold = changed; hard checks = deliverables / determinism / coverage):

| video | hard checks | captions exactly as yours | word errors % | cuts reproduced | framing as yours |
|---|---|---|---|---|---|
| chocolate-fixed | pass/pass/pass | 7/22 | 16.67 | 1/3 | 5/5 |
| deadpool | pass/pass/pass | **45/60 -> 46/60** | 4.0 | - | - |
| laptop004-fixed | pass/pass/pass | 6/27 | 12.5 | 0/3 | - |
| laptop009-fixed | pass/pass/pass | **21/52 -> 22/52** | 6.77 | 2/13 | 15/16 |
| laptop010-fixed | pass/pass/pass | 10/34 | 7.69 | 1/9 | 11/13 |
| laptop013-fixed | pass/pass/pass | 12/22 | 13.33 | 1/5 | **1/6 -> 2/6** |
| mike005-fixed | pass/pass/pass | 9/46 | 13.04 | 2/13 | 0/14 |
| spiderman-school | pass/pass/pass | **10/64 -> 11/64** | 10.2 | 2/21 | - |
| video017-fixed | pass/pass/pass | 23/43 | 10.99 | 4/12 | - |
| video018-fixed | pass/pass/pass | 12/49 | 14.81 | 1/15 | - |
| video020-fixed | fail/pass/pass | 0/30 | 94.12 | 0/11 | - |
| video021-fixed | pass/pass/pass | **21/40 -> 22/40** | 13.51 | 0/15 | 8/21 |
| video1 | pass/pass/pass | 21/111 | 10.83 | 11/29 | - |
| video10-fixed | pass/pass/pass | 0/17 | 31.58 | 1/4 | 5/6 |
| video11-fixed | pass/pass/pass | 14/23 | 15.38 | 2/3 | **1/8 -> 2/8** |
| video12-fixed | **fail/pass/pass -> pass/pass/pass** | 8/51 | 4.08 | 4/13 | 4/16 |
| video14-fixed | **fail/pass/pass -> pass/pass/pass** | 16/42 | **9.0 -> 8.0** | 1/3 | **0/10 -> 4/10** |
| video15-fixed | pass/pass/pass | **5/22 -> 7/22** | 33.93 | 3/8 | 12/21 |
| video16-fixed | **fail/pass/pass -> pass/pass/pass** | 7/56 | 16.96 | 5/8 | 7/12 |
| video17-fixed | pass/pass/pass | 9/32 | 14.04 | 5/7 | 13/16 |
| video18-fixed | pass/pass/pass | **6/25 -> 8/25** | 9.26 | 0/1 | 1/1 |
| video19-fixed | pass/pass/pass | 8/33 | **33.33 -> 30.0** | 3/8 | 1/10 |
| video2 | pass/pass/pass | 2/21 | 4.76 | 1/1 | - |
| video20-fixed | pass/pass/pass | **5/55 -> 7/55** | **13.04 -> 11.3** | 9/13 | 1/14 |
| video3 | pass/pass/pass | 9/71 | 10.76 | 1/11 | - |
| video4 | pass/pass/pass | 6/38 | 18.42 | 2/7 | - |
| video5 | pass/pass/pass | 1/37 | 35.44 | 2/14 | - |
| video6-fixed | **fail/pass/pass -> pass/pass/pass** | 18/32 | 10.0 | 5/7 | 9/9 |
| video7-fixed | pass/pass/pass | 14/36 | 10.67 | 0/1 | 5/10 |
| video8-fixed | pass/pass/pass | 0/16 | 9.09 | 1/4 | 4/11 |
| video9-fixed | pass/pass/pass | 4/18 | 44.44 | 3/4 | 4/4 |
| zendaya | pass/pass/pass | - | - | - | - |
| zendaya-age | pass/pass/pass | **1/19 -> 2/19** | 21.62 | 1/9 | - |

## 6. Wishes

### 6.1 Captions a little too early

Measured on all 23 of your fixes that still have their run: **the one-frame Premiere shift is real, and you leave
it** -- on every 23.976 sequence your untouched captions sit one frame before the SRT's frame (Premiere's import); on
29.97 / 25 / 30 / 60 they sit on it. So "fixing the one-frame shift" (night 11's R-D1) would put every caption one
frame later than where you accepted it -- **R-D1 stays off**.

What you do move is not one frame: **video16 30 of 37 captions later (median 0.33 s), mindculpt 17 of 27 earlier**
(median 7 frames) -- in both your starts land on the moment the word is heard. Found why: the captions are timed on a
rendering of the edit at the competitor's speed, while 1_edit.xml plays every clip at 100 % -- they run apart where
the competitor changed the speed (video16: 50 % / 67 % pieces; mindculpt: 105 %). **Built**
(`match_cuts\post_captions.py`, no cached stage): after the captions are made, each caption's words are paired with
the RAW's own words where 1_edit.xml plays them; a stretch of 3+ captions off by 0.25 s or more moves onto the sound
(start and end together, never across a cut). The run report lists every move. Offline on day 12's runs: no case
lower; video16 7 -> **16** of 56 exactly as you have them, mindculpt 9 -> 10 (and 9 -> **15** within a frame of yours).

**In the final check-all** it moved **mindculpt's** captions (video20: 27 captions, where the transcript ran 0.20 s
off the sound): exactly as you have them **5 -> 7 of 55**, word errors **13.0 -> 11.3 %**. On **video16 it moved
nothing**: the step pairs the captions' words with the RAW's speech map where 1_edit.xml plays it, and there the two
agree within 0.03 s (median of 113 words) -- the offline test had stood in for the speech map with a new CPU
transcription of the edit's sound, which showed 0.27 s; the pipeline's own speech map does not. So video16's captions
stay 7 of 56, and **the wish stays open** (with a note): mindculpt-like drifts are fixed, Always Sunny's is not --
the real cure is suggestion 3 (time the captions on a transcript of the sound 1_edit.xml plays). The word-pair half
of the same step works (section 9).

### 6.2 Liquid Death "Small Ones" -- the hard case

* The laptop's run never matched anything: it **stopped at the input check** ("the competitor (0:45) is almost as long
  as the RAW (0:47)"). With your RAW pick it runs (the form passes --allow-short-raw, a queue pick --force-inputs).
* Both competitor files (1019.mp4 and the Instagram original) are **the whole commercial uncut**: their only picture
  changes are the RAW's own 7 shot changes. So "matching the cuts" finds no cuts -- there are none.
* **Your edit**: 13 clips of the RAW in order with **10 pieces left out** (0.2-0.84 s each, 4.8 s in all) -> 40.24 s.
  Each is a pause between two lines (from a shot change to the next word, or the middle of a long pause); music
  plays under every one (-30 to -39 dBFS), so the tool's careful pause cuts (clear silence only) cut none.
* **No safe rule yet** (section 10: over all your fixes such pauses are cut about as often as kept). Left open in
  requests.md, and **`tests\real\liquid-death-fixed`** is a test case with your edit as the key (13 clips, 55
  captions), so a later rule is measured against it. Learn needed one fix for it: your edit plays the RAW by its own
  name from D:\Shared, not the run's raw.mp4 -- Learn now accepts the same file under another name (same size, same
  media).

### 6.3 Stats and Topaz

Marked "later -- not now (day 13)" in requests.md.

## 7. The problems list

| problem | cause | status |
|---|---|---|
| **Are We There Yet -2**: 3 x "A1 range is not a V1 cut range" + a flash at the end | V1's cut moved a frame onto the RAW's own shot change, A1 kept the competitor's frame; the last clip ran a frame into the next shot | **fixed 2026-10-20** (section 9) |
| **Borat edit**: FileNotFoundError state-4.pkl | the laptop's C: filled up during the run (the 15 GB converted copy + caches); the helper processes' file in the temp folder was lost | **fixed 2026-10-20**: section 3 (no 15 GB copy), the space check (1.3), the plain reason (1.2); re-run here: PASS, see section 3 |
| **Borat edit-2 / edit-3**: ffmpeg failed (4294967268) | -28 = no space left: C: still full from the first try | **fixed 2026-10-20** (as above) |
| **Family Guy 1999-present**: 2 x FLASH, ENDING, PERSON | FLASH: three tiny cartoon shots in a row (1 + 2 + 1 frames) played straight through; ENDING: the check compared with a stutter the repeats step had cut out; PERSON: at 2:12 ("Both") the policeman on the right speaks, the competitor shows the whole wide picture, the tool's window shows the woman in the middle and cuts him off | FLASH and ENDING **fixed**; **PERSON not fixed** -- the speaker rule of section 8 leaves cartoons alone on purpose (their faces flicker: a wrong move is worse than none). Check by hand 3. |

Each entry in `problems_queue.md` has its status, what was found and the fix. Tests: `test_day13_problems.py`,
`test_day13_editnotes.py`, `test_day13_errors.py`, `test_day13_rawkeep.py`.

## 8. The person speaking in the middle

A new rule at the end of the framing (`export_xml_edl.py`, `SPEAKER_CENTRE`, on by default; one run without it:
`MATCH_CUTS_SPEAKER_CENTRE=0`): per RAW shot of a clip, when the person **speaking** is clear, the picture slides
sideways (zoom and height kept, the window always covered) so their face is in the middle; when their face is cut off
at the edge, only until it is inside.

"Clear" (Light-ASD, never the biggest face): the top speaking face on 60 %+ of the speech, its score 1.0+ above every
other face (a lone face: 1.5+), the face found on 98 %+ of the frames (cartoon faces flicker: skipped), the face at
least 22 % of the picture's height (smaller = a wide shot: kept), inside the competitor's own window, and that window
wider than the template's. Never moved: a close-up already shown whole, a move under 10 % or over 60 % of the window,
a move that would push out someone shown whole (021's two women stay), a replaced spot, anything against the XML's own
--min-move check.

**Never the wrong person**: video6 and laptop004 (cartoons) -- nothing moves; video021 -- nothing moves (the two-shot
at 82 s and the laughing men at 54 s stay). Every move on every test video was checked on the pictures
(`work\t28\framing\pics\final\`): each is onto the person speaking.

**One side effect, found by the final check-all and fixed**: where the rule re-frames one RAW shot of a clip, the XML's
split at that shot change becomes a visible framing change, and caption rule 10 ("no caption across a cut") split a
caption there -- video14 "in my face" -> "in my" | "face" (captions 16 -> 15), video11 14 -> 13. Your video14 fix
re-frames at that very shot change (dx +651 -> -176 at RAW 41.58 s) and keeps "in my face" whole. Now the caption
after-step joins a caption split only at such a re-frame of the speaker rule inside one clip (380b1f1) -- video14's
captions are then exactly as before tonight (plus the new bit\*h) -- and moves a caption edge that rule 10 had pulled
onto such a re-frame back to the frame its next word starts in (4c17011; video11: "that says" | "shenanigans" 2.783 s
-> 2.717 s, as before). The rule itself changes only the framing. Final code, check-all: video14 captions 16 of 42, video11 14 of 23, laptop013 12 of 22 -- each as before tonight -- with the framing gains.

**check-all (final code)**: framing as you had it **107 -> 113 of 223** -- video14 0 -> 4 of 10, video11 1 -> 2 of 8, laptop013 1 -> 2 of 6 (exactly what the framing helper's replays had said); no video's framing lower; the captions of those three as before (16, 14, 12); no hard check changed by it.

## 9. Small edit notes fixed by the tool itself

| note | the fix |
|---|---|
| "XML A1 S12: range 650-706 is not a V1 cut range" (Are We There Yet x3) | A1 starts and ends with its V1 clip where a V1 cut was snapped onto the RAW's shot change (each picture's own sound, still in sync) |
| a 1-2 frame flash at the very end (Are We There Yet, video12, video6) | 1_edit.xml ends on that shot change (V1, A1, markers, the frame / watermark and the last caption) -- only a sliver under 0.25 s of the last take; written in the report as "Fixed in the edit by itself" |
| "XML FLASH S17+S18+S19+S20" (Family Guy) | not a flash: a burst of tiny shots the edit plays straight through is the RAW's own |
| a 1-2 frame flash at a cut inside the edit | the cut moves onto the RAW's shot change (kept only if the whole edit's flash check gets better); no current case needs it -- a safety net |
| word pairs split across two captions (rule 9) | joined only where you always join them: a / an / the / my / your + its word, a name split in two (Hong \| Kong) -- only with no pause, comma or cut between and every caption rule kept |
| **"XML SYNC ... the RAW skips 42 ms at the cut: one take, cut"** (video14 at 00:00:20:12, video16 at 00:00:04:13 and 00:00:07:04 -- both failed their hard check; the Always Sunny laptop run had it too) -- found by me, section 10 | the competitor cut about 2 RAW frames out of one take (83 ms in the 60 fps plan); the RAW's rate rounds that to a 1-frame skip. At all three spots **your fixes play the take straight through**. Now 1_edit.xml plays the skipped frame too -- with the clip whose RAW shot it is -- and everything after the cut comes a frame later (A1, markers, captions). Replayed on all 27 test videos that have saved export inputs: **only video14's 1_edit.xml changes** (its hard check passes); every other one is byte for byte the same. video16 (not replayable: no saved inputs) showed in the final check-all that the rate check had to compare with the put-back frames taken out (two frames put back put everything after them 2 frames off the plan: 15 "XML RATE" lines) -- corrected (8b0e049); on the same 1_edit.xml every hard check then passes; end to end on the corrected code video16 passes every hard check (a 20-minute check-all run). |

Your video12 fix cut exactly those 2 end frames; in video6 you kept the 1 frame -- I followed the newer fix (video6's
1_edit.xml is now 1 frame shorter than your fix). The end sliver is cut only in the delivered 1_edit.xml (not in the
60 fps edit check-all compares and the captions are made from).

**check-all (final code)**: video6 and video12 now **pass** their hard check (the end flash cut), video14 and video16 too (the skipped frame put back); captions exactly as yours +9 on the scored videos from the word pairs and the timing step (deadpool, laptop009, video021 +1; video15, video18, video20 +2) and +1 each on two unscored ones (spiderman-school, zendaya-age); word errors lower on video14 (9.0 -> 8.0 %), video19 (33.3 -> 30.0 %) and video20 (13.0 -> 11.3 %); nothing lower anywhere.

## 10. Learned by myself

1. **The biggest differences left between the tool and your fixes:**
   * **Framing**: of the 209 picture clips in your keyed fixes the tool framed 106 as you did (before tonight); of the
     other 103, **77 differ by a sideways move** (the biggest difference), 21 by zoom -- 13 of those in mike005 alone,
     where you zoom every clip 7.3 % more (62 % for the tool's 57.5 %), 7 in video021. video19 (Family Guy, a cartoon):
     you moved 9 of 10 clips a little to the right -- you re-frame cartoons too (suggestion 7). Tonight's speaker rule
     goes after the sideways moves where the speaker is clear (section 8).
   * **Your own pause cuts** inside the tool's clips (Liquid Death 10, video12 8, video15 4, laptop009 4, video14 2,
     chocolate 1, video18 1; words from Whisper on the CPU, `work\t28\pauses`): **no rule fits**. Of 38 pauses between
     words inside the tool's clips you cut 9; pauses of 0.40-0.60 s: 5 cut, 6 kept; a new shot's dead start of 0.35 s
     or more: trimmed 3 times of 9. Any rule would cut about as often where you keep as where you cut, so none is on;
     Liquid Death is the test case for a later one (suggestion 2).
   * **Caption timing and word pairs**: built tonight (6.1, 9).
   * **A one-frame jump inside a take** (found by going through every hard-check note): your fixes play the take
     straight through -- fixed (last row of section 9).
2. **Real videos with tonight's tool**: **Borat** (the x265 5.1 film that filled the laptop's C:): PASS, the original
   4.0 GB + a 0.97 GB WAV kept instead of 15 GB, frame-exact in Premiere at start / middle / end, Learn finds it
   (section 3). The newest real projects (interesting-fact, Family Guy 1999-present, Are We There Yet -2) were checked
   on their own files instead of a full re-run: their A1 notes, the flash at the end, Family Guy's flash and ending
   notes come out right with tonight's checks (sections 7, 9). A full re-run of them did not fit beside the final
   check-all on the graphics card (two streams at most) -- see What's left.
3. **New open items**: none new in `problems_queue.md`. Found on the way: **this PC's aria2c is from 2015 (1.19.0)**
   -- the new version line flags it, setup_app (run at the end) puts 1.37.0 into `tools\app\bin`; a rare **start-up
   freeze** of the app under heavy load (two threads importing the same modules at once) -- fixed by the health helper;
   the XML `--min-move` check reads a clip's last frame half a 1/60 s step late (the framing helper's find; left as
   it is -- a hard check is not something to loosen at the end of a day).
4. **Everything built today, used in the real app** (the real window, scratch folders, nothing of yours written): the
   free-space strip (`C: 79.9 GB`), the amber version line *"aria2c 1.19.0 is too old (1.37.0 needed)"* with Fix now
   (not pressed: it would have updated packages while check-all ran on them), a board card named after its YouTube
   title with **Rename to title** -- pressed: the folder became `2026-10-20_The Most Questionable Ad of All Time 📻😭`,
   the app's copy `The Most Questionable Ad of All Time_edit.xml` appeared next to `1_edit.xml`, project.json
   followed, the button went; Clean up from the strip and from the board. **Found and fixed**: a long word in a
   card's title was cut off in a 1280 px window ("Questional", e8ace38). In Premiere: the +3 dB preset and the Borat
   run with its original RAW (sections 2, 3).
5. **More tests**: 129 new tool tests and 147 new app tests today (the helpers' and mine: `test_day13_pairs`,
   `_audio`, `_learn`, `_problems`, `_jumps`, `test_board_day13` ...).

## 11. Suggestions for you

From what I saw today, most useful first. **Not built.** Also in `suggestions.md` (the Learn tab: I want this / No).
Taken out of suggestions.md because built today: *Join one-word captions the way you do* (its safe part -- a, the,
my ... + their word, names split in two -- is section 9; the other pairs you keep split two times in three). Built
from requests.md today: *Warn before C: fills up*, *The same app versions on the laptop*, *Re-frame onto the person speaking in more clips* (section 8).

| # | idea | why it helps you | size |
|---|---|---|---|
| 1 | **The audio chain put on by itself** | after Open in Premiere you select every A1 clip and drop the preset on them, every video; the app could apply it through Premiere's scripting -- always exactly the standing rule | half a night |
| 2 | **A list of the pauses you might take out** | you cut pauses inside the tool's clips by hand (Liquid Death 10, video12 8 ...) and no rule predicts which; the report could list each pause over 0.4 s with a play button and a one-click cut | half a night |
| 3 | **Captions timed on the sound 1_edit.xml plays** | today's fix moves captions onto the sound afterwards; transcribing what A1 plays at 100 % would time them right from the start and hear sped-up pieces' words right (check-all re-transcribes once, ~1 h) | half a night |
| 4 | **Clean up the tool's saved analysis** | the biggest space user on C: is `work\cache` (~80 GB here, 62 GB of it proxies); Clean up could list the analysis of films whose projects are uploaded and ask | small |
| 5 | **Keep the projects on the films' drive** | with the RAW on D: and the project on C:, a run copies the original (Borat: 4 GB); on one drive it is a hard link (0 GB) and C: stops filling up | small |
| 6 | **Censored words learned by themselves** | when your fix writes a word with a \* (murde\*ed), Learn could add it to the censor list itself -- today it was by hand, and Learn's glossary wrote "murde ed" | small |
| 7 | **The person speaking in cartoons too** | the speaker rule leaves cartoons alone (live-action face detection flickers on them), so Family Guy's policeman stayed outside the window; you re-frame cartoons too (video19: 9 of 10 clips) | a full night |

## Tests

| suite | day 12 | day 13 (final code) |
|---|---|---|
| the app (`tools\app\tests`) | 984 passed | **1129 passed**, 2 skipped, 0 failed (4 min) |
| the tool (`tools\match_cuts\tests`, the graphics card hidden) | 1422 passed, 97 skipped | **1539 passed**, 123 skipped, 0 failed (12 min) |
| check-all | 28 test videos | 33 of 34, nothing lower, hard-check FAILs 5 -> 1 |

New tests today: 138 for the tool (`test_day13_*.py`: pairs, audio, learn, problems, errors, rawkeep, captions, editnotes, framing, names, jumps, caption cuts) and 147 for the app (versions, space, the health strip, errors, torrents, names, rawkeep, the New video pair, the board). The tool's skips are 123 instead of 97 because the graphics card was really hidden this time; with it this morning the whole suite had 1537 passed, 0 failed.

## What I decided on my own

1. **A WRONG PAIR is never remembered** (section 1.1); a right pair still is (seconds saved on every Try again).
2. **No "Start anyway" for space** (the prompt says don't start); the estimates are constants at the top of
   `core\space.py`.
3. **The firewall question about aria2c.exe: Cancel** (the safe answer -- see Check by hand 0).
4. **The audio preset was made in Premiere** (its checksum is of an unknown kind); your preset folder backed up and
   restored byte for byte.
5. **A1 = a WAV** (not .m4a: no priming samples, in sync in every program); the original keeps a fixed name
   `raw.<ext>` (Learn, the app and Clean up find runs by fixed names); a variable frame rate always gets the CRF 17
   slow copy; a late-starting picture a remux (not a 30-minute re-encode).
6. **The next competitor of the same converted film converts again** (the analysis copy is deleted at each run's end;
   "one converted RAW per movie" is *not now*).
7. **Names**: the app writes `<title>_edit.xml` (1_edit.xml stays byte for byte what the tool wrote); `<title>.prproj`
   for new projects; a folder a Premiere project plays keeps its name; `\ / |` become a space.
8. **Learned rules**: only the censored words. Learn's glossary lines with the \* dropped (`murde ed`) not adopted.
9. **Caption timing**: R-D1 stays off (your accepted captions sit where Premiere puts them); only stretches of 3+
   captions off by 0.25 s+ move (you moved 0.27 s and 0.16-0.47 s, kept 0.22 s).
10. **Word pairs**: only determiner + word and split names (you join 16 of 50 flagged pairs -- "join all" would be wrong
    2 times in 3).
11. **Pause cuts**: no rule (section 10).
12. **The speaker rule is on by default**, skips cartoons; it centres the speaker (your moves land closest to the
    middle: +188 / -154 / +228 px for the rule's +211 / -169 / +244).
13. **The end sliver** is cut in the delivered 1_edit.xml only; video6 follows the newer video12 fix.
13b. **A one-frame jump inside a take is put back** (a fix I added from your fixes, not on the list): the edit gets a
    frame longer at each such cut rather than keeping a stutter the hard check fails; the checked 60 fps edit (what
    check-all scores) keeps the competitor's jump, so no score moves.
13c. **The speaker rule stays on, with its caption side effect removed** rather than switched off: it cost video14 and
    video11 one caption each through rule 10; your video14 fix shows the caption stays whole across such a re-frame,
    so the caption after-step joins those (only those). Not in `captions.py` itself: that file is part of the cached
    transcription stages, and changing it would have re-transcribed every test video (about an hour).
14. **Family Guy's PERSON** left for you (a cartoon).
15. **check-all with two streams** (not three: the graphics card was full with two).

## What's left (not done, and why)

* **A full re-run of the newest real projects** (interesting-fact, Family Guy 1999-present, Are We There Yet -2): the
  final check-all needed the graphics card until the end (its two streams took longer than planned: video20's first
  run alone took 105 minutes); they were checked on their own files instead (section 10).
* **Family Guy's person speaking at 2:12** (a cartoon) -- by hand (Check by hand 3); suggestion 7.
* **Your pause cuts** (Liquid Death): no safe rule yet (section 10); suggestion 2.
* **A frame shown twice at a cut inside a take** (the other half of the XML SYNC note, "repeats"): not put back like
  a skipped frame -- no test video has one today.
* **Not tried on the laptop**: Fix now on an old package, the free space of the shared folder over the network, a
  torrent saved over `\\192.168.101.11\Shared` (tested here over `\\127.0.0.1`).
* **Premiere with a title-named project** (emoji + apostrophe): the rename was tried in the real app; opening such a
  project in Premiere was not (Check by hand 2).
* The torrent picker does not offer Clean up (its own space check stays as it was).

## Check by hand

0. **Windows Security asked about aria2c.exe** this morning ("allow public and private networks") during a torrent
   test; I pressed **Cancel** (incoming torrent connections stay blocked for that aria2c.exe; downloads still work, a
   bit slower). To allow later: Windows Security -> Firewall & network protection -> Allow an app through firewall ->
   aria2c -> tick Private.
1. **The audio preset**: in Premiere, Effects -> Presets -> right-click -> Import Presets… ->
   `assets\premiere\MovieRecaps audio chain.prfpset` (the +3.0 dB one; on the laptop too). Drop it on the A1 clips as
   always: Amplify shows 3.00 dB.
2. **A title with emoji and an apostrophe**: on your next New video with a headline like "He couldn't stop 🍫😭", press
   Open in Premiere when the edit is made: nothing offline, the sequence is called after the title, Export -> Media
   suggests `<title>.mp4`.
3. **Family Guy 1999-present at 2:12** ("Both"): slide that clip about 250 px left so the policeman speaking is in the
   window (the tool leaves cartoons alone).
4. **Board -> Clean up**: the new *Keep the original RAW* offer (Borat 13.1 GB) -- read the list before Yes.
5. **On the laptop**: the amber version line should appear if anything differs -> **Fix now**.
6. **The speaker moves**: `work\t28\framing\pics\final\<case>_moves.png` (red = before, blue = after, green = yours).
   If one looks wrong to you, say so in a wish; the rule is one switch.
7. **Liquid Death**: the pauses you take out are still yours by hand (section 6.2).

## On the laptop

Close MovieRecaps if it is open. Then, in PowerShell:

```
cd C:\Users\gvida\Desktop\bh\MovieRecaps
git status
git pull
.venv\Scripts\python tools\app\setup_app.py
```

* `git status` first: if it lists files under `tools\` as changed by you, keep a copy, then `git stash` before
  `git pull` and `git stash pop` after. Your `input\`, `output\` and `finished\` folders are never touched.
* `git pull` brings about **380 MB** (six new test cases: video16-20 and Liquid Death, and the Are We There Yet piece).
* `setup_app.py` now checks every package against one table: it updates only what is missing, old or broken
  (PySceneDetect 0.7.1 even when the old one is broken), and puts **aria2c 1.37.0** into `tools\app\bin` when the one
  found is older (this PC had 1.19.0 from 2015). It prints "found: …" and "left: …".
* Import the +3.0 dB audio preset in Premiere (Check by hand 1).
* Then open MovieRecaps: the top strip shows the laptop's free space (C: and the shared folder); if the amber version
  line appears, press **Fix now**.

## The PC

`setup_app.py` ran at the end (it found only aria2c too old -- 1.19.0 -- and put **aria2c 1.37.0** into
`tools\app\bin`, which the app uses from now on; the shortcut is on the desktop; "everything is as the project
expects"), everything is committed and pushed, and the PC shuts down two minutes after the report. C: has about 80 GB free. Today's scratch, all of it the tool's own (I delete no caches myself):

* `work\t28` -- about 21 GB: the check-all runs, the Borat re-run (8.2 GB: its run 4.9 GB with the 4 GB copy of the
  original, its saved analysis 3.2 GB), the framing helper's runs and pictures (4.4 GB), the RAW pieces of the rawkeep
  tests (2.8 GB), Liquid Death's run (1.5 GB);
* the seven helpers' worktrees `.claude\worktrees\agent-*` -- about 19 GB (all merged, nothing uncommitted);
* `work\snap13a` ... `snap13d` -- the frozen code of the check-all streams (60 MB).

When you like, in PowerShell:

```
cd C:\Users\gvida\Desktop\bh\MovieRecaps
Get-ChildItem .claude\worktrees -Directory | ForEach-Object { git worktree remove --force $_.FullName }
git worktree prune
Remove-Item -Recurse -Force work\t28\borat, work\t28\rawkeep, work\t28\framing\runs, work\t28\framing\runs_fast
Get-ChildItem -Directory work\snap13* | Remove-Item -Recurse -Force
```

## Commits

Everything is on `claude/new-session-ol4era`, pushed after each part (89 commits today; the helpers' branches were merged in):

| commit | what |
|---|---|
| `28d6552` | setup_app: the versions the project expects in ONE table (core/versions.py) that setup_app and the app's start-up check both use; a broken or old PySceneDetect no longer crashes setup_app (checked safely, then 0.7.1 i... |
| `d1ff69c` | Plain reasons for a stopped run (day 13, 1.2): match_cuts/plain_reason.py reads the console, match_cuts.log and the exit code -- ffmpeg/ffprobe errno and AVERROR codes (4294967268 = -28 = the disk is full), the worker... |
| `8fea33f` | App: a stopped run always says why in plain words and what to do (day 13, 1.2) -- results.read_result's reason / todo / reason_line from match_cuts.plain_reason (console + match_cuts.log + exit code), shown on the que... |
| `1557c5b` | Day 13 edit notes (9.1, 9.2): A1 follows V1 cuts moved onto a RAW shot change; the edit's last frames showing a sliver of the next RAW shot are cut from 1_edit.xml; a burst of the RAW's own short shots played straight... |
| `3cefc03` | New video WRONG PAIR (day 13, 1.1): a WRONG PAIR answer is never taken from the pair check's cache (one stop made every Try again of a project stop at once, while a drop's new project was checked fresh); the run's log... |
| `50d633f` | Framed preview: an apostrophe in the run folder's path no longer breaks ffmpeg's concat list (day 13: project folders are named after the video's title, "He couldn't ...") |
| `c509a3a` | Learned from video16-19 and mindculpt (video20) (day 13, section 5): five new test cases with their channel template + watermark (new style, scored); the censored words you write by hand -- bit*h (video14), murde*ed (... |
| `4a4d9f0` | Day 13 edit notes (9.1): a flash of 1-2 frames at a cut moves the cut onto the RAW's shot change at the RAW's rate (kept only when the flash check then finds fewer problems and nothing new, A1 moving with it); shorter... |
| `69f1244` | Torrents (day 13, 1.6): files already in the folder without their .aria2 are checked against the torrent (check-integrity, never truncated: the right pieces kept, only the missing ones fetched, a complete file finishe... |
| `d1daa65` | Keep the original RAW: V1 plays the original, A1 a sample-exact stereo WAV; the converted copy is scratch |
| `3af4354` | Board Clean up: replace an older run's big raw_stereo.mp4 by the original RAW + raw_stereo.wav (after asking) |
| `94a420e` | Speaker centring rule (day 13, section 8): a clear person speaking by Light-ASD, inside the competitor's wider window, centred per RAW shot (zoom kept); MATCH_CUTS_SPEAKER_CENTRE switches it (off while being checked) |
| `44338c5` | Tests and the torrent self-check start aria2c on 127.0.0.1 only (--interface=127.0.0.1, no IPv6, no DHT / local discovery): Windows' firewall question about aria2c.exe popped up during the tests and blocked the screen... |
| `ba6e7fa` | File names = the video's title (day 13, section 4): <date>_<title> folders, <title>_edit.xml with the sequence named after the title, <title>.prproj; renamed later after a title change with everything linked; Rename t... |
| `c35e3a2` | Learn tab: an older problems_queue.md entry whose title is the tool's technical line ('details in ... match_cuts.log', ffmpeg failed (4294967268), the pool's state-4.pkl) shows the plain reason read from its own key l... |
| `c0d7f52` | Day 13 notes of the editnotes helper (9.1, 9.2): what is fixed, the decisions, per case before / after, what is not done |
| `5c9e171` | Names review: Learn's watcher and a dropped folder never take the app's own <title>.prproj for your fix (a title may end with "fixed"); the other computer's waiting card finds the shared copy renamed here; problems_qu... |
| `c733e2f` | Day 13 edit notes: the cut fixer moves only cuts between clips at 100 % (a retimed clip starts on Premiere's own grid) |
| `8d201a5` | Day 13 edit notes: the A1 fix line names the note it removes |
| `8357fd9` | Torrents: checking a big file already in the folder (minutes over the laptop's network) is never 'no one is sharing' -- aria2c's verifiedLength counts as progress, and an older aria2c that says nothing gets the time t... |
| `85a6d5f` | Original RAW: a late-starting picture is remuxed to 0 (not re-encoded); export reads stay on the analysed copy; Clean up matches the project |
| `aa0dffd` | Just cuts: a stop for a known reason (a full disk, a crash, out of memory ...) says it in plain words with what to do, like the full edit (its own plain messages stay) |
| `a466cab` | Audio chain: Amplify +3.0 dB (day 13, section 2) -- the same chain in the same order (Normalize All Peaks 0 dB, Volume 0 dB, Hard Limiter 0/0/7 ms/100 ms/1.00, Amplify, Panner 0); the preset made again with Premiere's... |
| `aa2e714` | Names: a project's first copy to the shared folder makes its rename wait (publish locks <hub>\<name> while it copies from here); the Learn tab reads its waiting cards again after a rename |
| `a10a9b5` | Free space before every start and in the top strip (1.3); the amber version line with Fix now (1.5) |
| `29910f3` | README and notes (day 13, health): free space, versions, Fix now; the decisions and what the lead must know |
| `fbfef5e` | Names: an old project's Just cuts copy keeps night 10's name (cuts_edit.xml) |
| `b2bf590` | Plain reasons: the exception that stopped the run (printed only to the console) decides -- an older soft error in match_cuts.log no longer wins; no technical brackets in the words (ENOMEM, AVERROR_* only in the detail... |
| `ac381ab` | Notes (day 13, health): the final app test count |
| `9d6c834` | Names tests: the window renames after a title change (waits for an edit, the minute's retry does it); a New video row's folder follows the headline after its template step |
| `425bf3c` | Captions after the caption rules (post_captions.py, in no cached stage): word pairs kept together (rule 9: a determiner or a name split across two captions joined where the caption rules allow) and captions moved onto... |
| `147ba5f` | Family Guy 1999-present's XML ENDING (day 13, section 7): a tail the repeats step cut out (the competitor's last 0.3 s replayed RAW 619.35-619.65 s, played a moment before) is no longer the competitor's ending -- the... |
| `41a0f47` | Merge branch 'worktree-agent-a88b763d5c7b12254' into claude/new-session-ol4era |
| `7567041` | Plain reasons: a sentence that ends with a bracket gets its full stop too (checked against this PC's real FFmpeg 8.0: a missing input exits 4294967294 = -2, a junk file AVERROR_INVALIDDATA, an unknown encoder ENCODER_... |
| `52e7b45` | Open in Premiere records 'opened' in project.json before anything else (a rename retried at that moment keeps the name) |
| `ccff378` | Names: the rename messages in plain sentences (Renamed later, when it is free: ... / It keeps its name: ...) |
| `c573911` | Speaker centring: guards from the replays of the keyed videos -- a lone face needs Light-ASD 1.5 (021: the laughing man the detection missed), never hide someone the window shows (021's two-shot), the competitor piece... |
| `a9065a7` | Wording from the rendered row and card: 'the disk C: ran out of space' (the free space added only while it is still low -- 'full (90 GB free now)' read as nonsense once the half-written file was gone); the torrent's '... |
| `c4195e9` | Queue glide test: waits for the row to arrive (at most 3 s) and wants 3+ positions in between -- it failed only on a busy PC, where the event loop draws fewer frames of the same glide |
| `45f9625` | Torrents: Remove never deletes a file the job found already in the folder (maybe your earlier finished download, being checked); a download the job started itself is cleaned up as before |
| `7b1316b` | Notes of the errors helper: Remove keeps files that were already there |
| `9b3037a` | Names fixes: problems_queue.md paths that only start like the old folder ('The Truth (2)') are never rewritten; capitals alone never rename; the path budget counts the temp file the XML copy is written through; a flag... |
| `a95b257` | post_captions: a timing stretch needs 3 captions (video021's lone stammer stays), stretch threshold documented; captions.json by_mode kept right after a join; tests for the stretch rules |
| `6cdb59b` | Day 13 edit notes: the end cut and the cut fixer only in the default mode (with --remove-silence those cuts sit off speech and 1_edit.xml is not checked for speech again); more tests; notes: the sweep of every run, th... |
| `c08ec72` | Plain reasons: only the message that stopped the run decides (its last meaningful console line and FFmpeg's own words printed under it) -- an older library line ('CUDA out of memory, falling back ...') no longer wins... |
| `959f147` | Torrents, the real case 'the next Short of the same film' tested end to end (a folder torrent, the film chosen, downloaded once, added again with nobody sharing): with its .aria2 (aria2c keeps it after a download of s... |
| `7a145a7` | Notes of the errors helper: where error 13 comes from in practice, the stop-block rule, test counts |
| `a851c48` | Notes of rawkeep: the end-to-end run, the AAC priming edit list of the old raw_stereo.mp4, measured sizes |
| `8039580` | post_captions: a caption heard after a video cut starts on it and the edge behind it moves with the stretch; a caption the move leaves under 0.1 s joins the neighbour on its other side (it would lose its words at the... |
| `93dc4e4` | pairs_fix: a determiner whose move would leave one weak word alone takes its noun back, or joins all three when the noun was all the next caption held ("from the children", "on my ultrasound" as you wrote them) |
| `f438f3b` | Review fixes: a crash code wins over older soft errors; only the stopping message's pool line counts; nothing Python prints after 'Run folder:' and cli's own ERROR line first; network folders keep their two backslashe... |
| `e04c9d9` | Notes of the errors helper: the real-data check, the review's 10 findings (all fixed), test counts |
| `8384033` | Notes of rawkeep: the whole-suite counts, the stage keys, the remux check's time |
| `b1f1185` | Merge branch 'worktree-agent-ac8026882a3702404' into claude/new-session-ol4era |
| `cae69fa` | Notes of the errors helper: the real JobRun probe, the 127.0.0.1-only aria2c watched live, setup_app and aria2c 1.37 on the main branch |
| `1a0e5ea` | Speaker centring: a clip framed per shot is covered only when every shot's framing covers the window; tests for the two-shot, the lone face and the XML --min-move reading |
| `35fc248` | Liquid Death hard case (day 13, 6.2): a test case with your hand edit as the key (13 clips, 40.24 s; the competitor plays the commercial uncut, your 10 pause cuts are your own); Learn finds the run's RAW when your pro... |
| `e95e9ac` | Names: the review's findings -- titles merged across the two copies, only the app's own XML copies ever removed, Premiere's files next to <title>.prproj in the length budget, safer scans and links |
| `d7e5642` | New video rows: a stage thread without the moved signal (tests' fakes) still runs; day 13 notes of the names helper |
| `30f28f3` | Notes of the framing helper (day 13, section 8): the speaker centring rule, how it was checked (exact replays of the keyed videos, pictures of every move), the results per video, what I decided, what is not done |
| `0540866` | Day 13 notes (editnotes): tests, video10 replayed, why the end-to-end run was left |
| `c5ad947` | Merge the main branch (claude/new-session-ol4era: health, rawkeep, pairs, problems, audio, learn, Liquid Death) into the errors helper's branch -- one conflict, flow.record_edit: both main's inputs= and the plain error= |
| `47af7d4` | Speaker centring: a speaker cut off at the window's edge is slid in (until the face is in with CUT_MARGIN to spare), not jumped onto (night 8's lesson, 021 S54 / S56) -- the Family Guy doctor of video19: 85 px instead... |
| `66f1410` | Test: a speaker cut off at the window's edge is slid in, not jumped onto |
| `006de10` | Names: the second review -- titles merged per title from the last synced base (a headline changed on the shared card or a cleared YouTube title is never undone), copies synced right after each publish, the shared copy... |
| `89cc8e1` | Speaker centring: sideways only -- the height stays the competitor's (a tiny sideways move came with a vertical fix on 021's first clip); a face cut at the top or bottom is not shown, so not moved |
| `50466e7` | Framing notes: all 15 keyed videos replayed exactly (84 -> 90 of 170, none worse), the slide for a speaker cut off at the edge |
| `51dac75` | Notes of the errors helper: merged with the main branch, the final test counts (the one tool failure is pre-existing on the base commit; the one app failure a fade timing test under load, passes alone) |
| `a590149` | Day 13 notes of the captions helper: what your fixes show about caption timing (the one-frame import shift kept, the real moves onto the words, why the tool was off), what was built (post_captions: pairs kept together... |
| `66bfb06` | Day 13 notes (names): the two reviews, the per-title merge, the test counts |
| `10b1af4` | Merge branch 'worktree-agent-a2c20b5d6c019526d' into claude/new-session-ol4era |
| `9441bf6` | Merge branch 'worktree-agent-ae4d7ca5cf0f15bd5' into claude/new-session-ol4era |
| `96b6056` | Framing notes: final test counts (1447 passed, 96 skipped), video19 replayed, what is not done |
| `40d5957` | Tool README (day 13): a WRONG PAIR is checked again every run, the stop names both files; bit*h / murde*ed / cra*kheads; XML ENDING skips a tail cut out as a repeat; learn matches the RAW played under its own name |
| `b830980` | Day 13 notes (editnotes): the test counts (tool 1444 passed / 97 skipped, app 979 / 5) |
| `72ca19b` | Integration test: learn_waiting.json read with retries (the app's background write can hold it for a moment on a busy PC) |
| `d61cd15` | Merge branch 'worktree-agent-adf9d556c1c3d2c3e' into claude/new-session-ol4era |
| `c0d7499` | Framing notes: the other test videos replayed (video018, video1, video5, zendaya-age: every moved clip onto the person speaking; the rest unmoved) |
| `79c6ad8` | Framing notes: wording (height kept, the pictures folders, the XML check's half step, video11's one move) |
| `5dbdb37` | export_xml_edl.py: the base file's non-ASCII characters back (a PowerShell edit had read the file as ANSI and written it back as UTF-8: the section sign in comments became mojibake); the file is now the day-12 file pl... |
| `618789e` | Merge branch 'worktree-agent-a868431a46f54f8f8' into claude/new-session-ol4era |
| `e8ace38` | Board card: a long word in the title may break inside the word in a narrow column (the click-through: 'Questionable' was cut to 'Questional' at 1280 px); never inside an emoji |
| `5aeb27c` | 1_edit.xml plays a take straight through where the RAW's rate left a one-frame jump inside it (XML SYNC 'one take, cut': video14 at 20:12, video16 at 4:13 / 7:04 -- your fixes play those takes through): the skipped fr... |
| `c538be2` | Tool README: a one-frame skip inside a take at the RAW's rate is put back (day 13, XML SYNC 'one take, cut') |
| `8b0e049` | The rate check compares 1_edit.xml with the frames put back taken out again (their positions from take_jumps), not only its length: video16's two put-back frames moved everything after them 2 frames, beyond the per-fr... |
| `380b1f1` | Captions: a caption split only where the speaker rule re-framed one competitor clip at a RAW shot change inside its take is whole again (post_captions.rule_cuts / rejoin; your video14 fix keeps 'in my face' across tha... |
| `9707281` | Tool README: caption rule 10 -- a re-frame of the speaker rule inside one competitor clip is no caption cut (day 13) |
| `f902fa3` | App README (day 13): names = the video's title and Rename to title, plain stop reasons with Try again, Clean up's Keep the original RAW, torrents checked against the folder |
| `4c17011` | Captions: a caption edge rule 10 moved onto a speaker-rule re-frame goes back to the frame the next caption's first word starts in (video11: 'that says' \| 'shenanigans' 2.783 s -> 2.717 s, as before the rule -- the f... |
