# Report -- 21 Nov 2026, the session on the PC (Claude Code)

For Gvidas. The short part first, the details after it. This file is written while I work: each job is added when
it is finished (or when I stop working on it).

## What you must do

1. **PC:** nothing. Open the app as always. It uses the new code from its next start.
2. **Laptop:** close the app, open a terminal in the MovieRecaps folder, type `git pull`, open the app again.
   (The fixes of jobs 1, 2, 3 and 4 matter most on the laptop. Then press Try again on "The Weirdest Gym Goal In
   Its Always Sunny" -- it should find its picture now.)
3. New on every video's row: a **No Topaz** button (and **Topaz again** to undo it). See job 4.

## The jobs

| # | job | state |
|---|---|---|
| 0 | start: the chat session's "parts_failed" change | done (commit d9177fc, pushed; e31b3ba was already pushed) |
| 1 | the parts fail on real videos | found and fixed, proven with the real Topaz (commit 1effbb6) |
| 2 | a short network drop is not a failed Topaz try | done, proven with the real Topaz |
| 3 | DVD RAW shape (SAR 64/81) | found and fixed (commit 3c0e677) |
| 4 | a way to stop Topaz for one video | done ("No Topaz" button), proven with the real Topaz |
| 5 | all tests + one real video end to end | done: all tests pass; two real videos end to end with the real Topaz |

## Job 0 -- start

- `git status`: the two files of your chat session were there (`core/enhance.py`: "parts_failed" is written into the
  piece's json at once; `tests/test_parts_failed_20nov.py`). I read them: right as they are.
- All tests with them: **app 1512 passed, tool 1700 passed**, your real crash.log not touched.
- Committed (d9177fc) and pushed. Commit e31b3ba (the smaller Topaz parts) was already on GitHub.

## Job 1 -- why the parts failed (The Worst Employee In Key and Peele)

**Its files were not there.** `D:\Shared\Enhance\_to_delete` holds only "The Scariest Trash Talk In Key and Peele"
(and nothing of The Worst Employee is anywhere on this PC: its project is on the laptop, and the laptop shares no
folder I can read). The piece was most likely tidied away by the laptop's app when the whole piece came back.
So I made the same kind of piece myself: a Key and Peele RAW of yours (1080p, 23.976 fps, H.265, the same release
group), copied to a scratch folder, cut by the app's own code, 4 played parts, through the app's own Topaz code.

**What I found:**

- With the real Topaz and a Key and Peele **HMAX** RAW (season 5): 4 parts, all right (1876 of 1876 frames). So it is
  not "parts + the 20 Nov resume" in general.
- With a Key and Peele **BluRay** RAW (season 2, from your project The Worst First Day In Key and Peele (2)):
  **part 2 started one frame late** -- "its first frame shows the piece's frame 301, not 300". That is the
  PartsError. (Found in 8 seconds with Topaz's own ffmpeg and encoder, the AI filters swapped for a plain resize.)
- **Why:** your RAWs are converted from MKV files by the app (a copy, no re-encoding). An MKV stores every frame's
  time in whole milliseconds. Frame 300 really is at 12.5125 s, the file says 12.512 s. The app asked ffmpeg to read
  the part from 12.5123 s (0.2 ms before the exact time) -- so ffmpeg threw frame 300 away, because the file says it
  comes 0.3 ms before that. The part then began one frame late, and the app's own check after the part (correctly)
  refused it. Some frames are stored early like this, most are not, so a part failed or not by luck of its first
  frame's number. Part 1 starts on frame 0 and always passed -- that is why it failed only after the next part had
  been made, about an hour later on the laptop.
- The 20 Nov resume had the same mistake: a stopped Topaz went on from the frame's exact time, so on these RAWs the
  part after the stop could start one frame late (it was then made again from the start: no wrong picture, but the
  saved time was lost).

**What I changed:**

1. A part is now read from its first frame's **own time in the file**, not from "frame number / frame rate". The
   same for a Topaz that goes on after a stop.
2. **Before Topaz starts on a part**, the app reads that part's first frame the same way Topaz will (the same
   ffmpeg, the same place, about 1 second) and checks it is the right frame. If not, the parts fail **at once**
   (seconds, not an hour) and the whole piece is enhanced, as before.
3. **Every PartsError saves its full reason at once:** in the piece's json (`parts_failed`, your chat session's
   change), and in the project -- `project.json` -> "enhance" -> "parts_failed" and a block in
   `logs\topaz_failed.log` -- when the computer doing Topaz can reach the project (it made the edit itself, or the
   project is on the PC's D:\Shared). Otherwise the PC copies it into the project when it takes the result back.

**Tested so far:**

- The BluRay piece, 14 parts (the plain resize): failed before, **all 14 right** now.
- 48 frames one after the other (two whole rounds of the millisecond pattern) on both RAWs: the old way was wrong on
  1 of them, the new way on none.
- New tests (`tests\test_parts_seek_21nov.py`, 5): a movie made like your converted RAWs (an MKV copied into an mp4),
  the old reading skips a first frame, the new one never; a part that would start wrong fails before Topaz; the
  reason is in the json and the project while the whole piece is still being made.
- **The real Topaz** (your Proteus + Apollo settings, nothing changed) on the BluRay piece, through the laptop's own
  worker code: 4 parts like The Worst Employee, two of them starting on frames where the old reading was one frame
  late (300 and 1034). **Passed: the parts were used (no fallback), 2418 of 2418 frames, every part on its first
  frame**, 11.5 minutes of Topaz on this PC. Then 61 frames inside the 4 parts were compared with the RAW's own
  frames: 60 show exactly the right frame; the one left is a picture Topaz makes exactly halfway between two RAW
  frames (it blends the two), so either is right.

## Job 2 -- a short network drop is not a failed Topaz try

Until now, when the laptop's Topaz said "...\\192.168.101.11\Shared\Enhance\..._TOENHANCE.mp4: No such file or
directory" (the network gone for a moment, the file still there), it was a failed try: the failure file went to the
PC, the PC made a new piece another way, and what Topaz had made was thrown away.

Now:
- When Topaz (or the app's own reading of a file) fails while the piece cannot be read, or fails with a "cannot be
  read" kind of reason that names a file of the Enhance folder, it is a **drop**, not a failure.
- The laptop then **waits** until the folder answers again (it looks every 15 s), and goes on **the same way**:
  what Topaz made so far is kept (as after a stop: the finished parts, the blocks of the part it was making).
- **Nothing is written for the PC**: no failure file, so the PC sends no new piece and counts no failed try; the
  parts are not marked "failed". The laptop's Topaz page says "the Enhance folder could not be read ... waiting for
  it, then Topaz goes on the same way". Each drop is written into the piece's json ("share_drops").
- If the folder stays away more than 30 minutes, the laptop lets the piece go (not failed) and takes it again by
  itself when the folder is back. If the PC removed the piece meanwhile, the laptop just tidies what is left.
- A real failure is still a failure (for example "Invalid data found"). And if the "drop" comes back 6 times in one
  take while the folder answers each time, it is a failure as before (then the PC tries the same way again, not a
  new way: "no such file" says nothing about the piece).

**Proof with the real Topaz:** a piece was moved away the moment Topaz started and put back 40 s later. Topaz failed
with exactly the laptop's message ("...TOENHANCE.mp4: No such file or directory"); the worker waited, started Topaz
again the same way when the file was back, finished (1242 of 1242 frames, the check OK) and wrote **no failure
file**. 10 new tests (`tests\test_share_drop_21nov.py`).

## Job 4 -- "No Topaz" for one video

A new button on the video's row: **No Topaz** (next to Open folder). It shows while the video can still get Topaz.
Press it and:
1. its piece leaves the Topaz line (every file of it in the Enhance folder; a finished `_ENHANCED.mp4` -- hours of
   Topaz -- is moved to `Enhance\_to_delete`, never deleted);
2. a Topaz working on it stops -- on this PC or on the laptop (a small "stop" file next to the piece; the computer
   enhancing it looks for it every 5 s), and that computer then removes the piece's files itself;
3. the project is marked "off": nothing sends it again (not the row, not the round over all projects, not Try
   again);
4. the video finishes with its zip (the normal RAW's picture -- your choice for this one video).
The row then shows "no Topaz for this video" and a button **Topaz again**, which undoes it (the piece is cut and sent
after the edit, as normal).

**Proof with the real Topaz:** Topaz at 5 % of a 40 s piece, "No Topaz" pressed: the worker stopped 2.3 s later,
Topaz's process ended, every file of the piece was gone, the project "off", nothing waiting. (The first try left two
files behind: the stopped Topaz still held them for a moment -- the app now waits up to 15 s for that.) 8 new tests
(`tests\test_no_topaz_21nov.py`, the row's button too).

**The laptop must get this code too** (`git pull`): a laptop with older code does not know the stop file and
finishes its Topaz; its result is then not used and is tidied away after 7 days.

## Job 3 -- the DVD RAW 12.5 % too tall (The Weirdest Gym Goal In Its Always Sunny)

**The probe was not wrong.** It read 8:9, as the file says. The mistake was in another check: the **stretch check**
(night 15: does the competitor show the RAW pulled wider, like the American Dad reel?). It looked at the RAW's
pictures in their stored shape -- 720x480, 1.5 times as wide as high -- not in the shape they are shown (4:3). Next to
a competitor that shows the picture right, the RAW then looks 9/8 too wide, so the check measured "the competitor
squeezes it to 8:9" and multiplied that onto the RAW's real 8:9: **8/9 x 8/9 = 64/81**, a 720x608 copy, every
picture 12.5 % too tall, and the picture search found almost nothing (your 3 %).

- I made it happen on this PC: a DVD-shaped HEVC-in-MP4 RAW (a copy of It's Always Sunny S02E06, 480p DVD x265
  10bit -- the same release as S04E10 -- with SAR 8:9 in the HEVC stream and in the mp4), and a competitor that
  shows its picture at the true 4:3. The app's own check said "0.889 -> pixel aspect 8:9" and S2 would make
  **720x608**. With the fix: "not stretched (1.000)", **720x540**.
- Your project "The Funniest Misread In Its Always Sunny" (12 Nov, S03E06, the same DVD release) had the same
  problem but was lucky: its check measured 0.911, just inside the 10 % limit, so nothing was multiplied. With the
  fix the same pair measures 1.002.
- The fix: the check makes the pixels square first (with the SAR the probe reads), so it measures only a real
  stretch. A real stretch on top (a 4:3 DVD pulled out to 16:9) is still found (tested). It would have hit an
  anamorphic 1440x1080 RAW the same way (16/9 instead of 4/3).
- The piece of the movie and Topaz: they read the SAR themselves, the same way as the probe (I checked on your real
  S03E06 file: ffprobe, ffmpeg's filters and Topaz's own ffmpeg all read the same value), and they were never
  touched by the stretch check. So they were right before and stay right.
- About "the readings disagree": in your S03E06 file they really do (the HEVC stream says 8:9, the mp4 box
  480:527). Every tool here (ffprobe, ffmpeg, Topaz's ffmpeg, players) uses the mp4 box's value, and the competitor
  of that video shows exactly that shape (0.9108 measured, 480/527 = 0.9108). So I did not change which one the probe
  uses. (Changing probe.py would also have made 8 of the tool's cached steps run again on every video.)
- New tests: `tools\match_cuts\tests\test_stretch_dvd_21nov.py` (5).

## Job 5 -- all tests, and one real video end to end

**All tests before every commit** (both suites, GPU hidden, your real crash.log checked before and after: never
touched). The last run: see the table at the end of this section.

**One real video, the real Topaz, everything as the app does it** (copies of the competitor and the RAW of The Best
Business Idea In Key and Peele (2) in `D:\mr_scratch\h21\e2e_bi2`, empty caches, the app closed):

| step | result |
|---|---|
| new project, convert | done (both kept as they are) |
| edit (60 fps) | **OK with a note** in 1892 s. The note: "8 frame(s) of a different RAW shot (0.13 s)" -- the competitor's own short shot, your open question of 19 Nov |
| the piece | ONE piece, 3:24 of the RAW, **10 played parts, 1:53** (the smaller parts of 20 Nov) |
| the real Topaz | 1911 s (32 min), **all 10 parts used** (each checked before and after Topaz), no fallback to the whole piece |
| the swap | **"56 of 56 V1 clips play it"**, 7 clip ends moved by 1-2 frames of 1/60 s (the 19 Nov rule: no flash at a clip end) |
| every clip checked (first, middle, last frame against the normal RAW) | **168 of 168 right**, 0 wrong, 0 clips on the normal RAW |
| zip | **883 MB** (under 1 GB), opens without errors, `1_edit.xml` + `HOW TO OPEN.txt` inside, the Topaz picture 665 MB, no normal RAW picture |
| clean-up round | only the zip is left; the Enhance folder is empty |

(On 19 Nov the same video made 3 parts, 2:28 of Topaz work and 46 minutes; today 10 parts, 1:53 and 32 minutes.)

**A second real video, for job 3: a DVD RAW** (your 12 Nov "The Funniest Misread In Its Always Sunny", It's Always
Sunny S03E06, 480p DVD x265 10bit, copies in `D:\mr_scratch\h21\e2e_dvd`):

| step | result |
|---|---|
| stretch check | "not stretched (1.002)" (12 Nov: 0.911, just inside the limit by luck) |
| piece of the movie | used: 4.5 of the 22.4 minutes analysed (12 Nov: the whole episode) |
| edit | **PASS, no notes**, 146 s |
| the real Topaz | 142 s, 1 part, 1308x960 60 fps (square pixels, the right shape) |
| the swap | "5 of 5 V1 clips play it"; every clip checked: **15 of 15 frames right** |
| zip, clean-up | 327 MB; only the zip is left, the Enhance folder empty |

The last full test run (with every change of today): **app 1535 passed, tool 1700 passed** (13 + 129 skipped: tests
for files that are not on this PC, as before).

## What I did NOT do

- **The Worst Employee itself** could not be run again: its files are not on this PC (see job 1). The laptop gets
  the fix with `git pull`; if that video is made again, its parts should pass (or, if anything is still wrong, fail
  within seconds, with the reason in the project).
- **The Weirdest Gym Goal (S04E10)** is on the laptop only. I proved the fix on the same release (S02E06 and
  S03E06). Try it again on the laptop after `git pull` (Try again on its row).
- **The laptop was not tested** (I cannot reach its disk). Jobs 1, 2 and 4 matter most there.
- The words "(the laptop Gvidas, ...)" in a Topaz failure text are still the same (20 Nov note).
- The 8-frame flash note (19 Nov question) is still yours to answer -- it came up again in today's test video.

## The space (nothing of yours deleted -- you decide)

Free now: C: 105 GB, D: 1.2 TB. Everything below is mine (copies and test files).

| folder | size | what it is | can go? |
|---|---|---|---|
| `D:\mr_scratch\h21\pt_*` | 28 GB | the tests' temporary folders of today | yes, all |
| `D:\mr_scratch\h21\work_bi2`, `work_dvd` | 3.2 + 2.5 GB | the caches of the two end-to-end videos | yes |
| `D:\mr_scratch\h21\e2e_bi2`, `e2e_dvd` | 0.8 + 0.3 GB | the two test videos' zips (worth a look in Premiere if you want) | yes |
| `D:\mr_scratch\h21\src` | 1 GB | copies of a Key and Peele BluRay RAW and the DVD video | yes |
| `D:\mr_scratch\h21\real*`, `emul*`, `dropstop`, `dvd`, `dev` | 1.8 GB | today's Topaz tests, my code copy | yes |
| `D:\mr_scratch\h19`, `h20` | 71 GB + about 70 GB (the 20 Nov report's list) | the 19 and 20 Nov tests (see those reports; `h20\dev` must go with `git worktree remove`) | yes |

`D:\Shared\Enhance\_to_delete` still holds "The Scariest Trash Talk In Key and Peele" (85 MB; a stopped piece you
moved there on 20 Nov) and older pieces. Not touched. The laptop was enhancing "Rons Biggest Enemy In Parks and Rec"
in `D:\Shared\Enhance` all morning: not touched.

## For the next Claude session

- Scripts of today: `work\h21` (`STATE.md` says what each is): `parts_real.py` (a parts piece through the app's own
  code, `--emul` = Topaz's own ffmpeg and encoder without the AI filters: seconds), `seek_sweep.py`, `dvd_stretch.py`,
  `drop_stop_real.py`, `leading.py`, `suites.ps1 -Tag NAME`. The end-to-end script is still `work\h19\e2e.py`
  (`--base D:/mr_scratch/h21`); pass Windows paths (`D:/...`) when a file name has brackets.
- RAWs converted from MKV keep whole-millisecond frame times. Anything that seeks by "frame / fps" can land one frame
  off: use `enhance._frame_times`. Test seeking on a BluRay Key and Peele copy and on frame numbers that are not
  multiples of 24 (multiples of 24 are exact and hide the problem).
- The stretch check (`match_cuts\stretch.py`) is in no cached stage's fingerprint; `probe.py` is in 8 of them.
- New in `core\enhance.py`: `_frame_times`, `_seek_lands_right`, `parts_failed_to_project`, `ShareDrop`,
  `share_drop`, `_wait_share`, `no_topaz`, `topaz_again`, `remove_piece`, the stop file `<name>_TOENHANCE.stop`,
  Job state "waiting". Project enhance state "off" (+ `no_topaz: true`). Row buttons "No Topaz" / "Topaz again".
- Commits of today: d9177fc, 3c0e677, 1effbb6, 4eeee9a, b2a427e and the report commits, all pushed.

## What I decided for you (you said: do not ask)

1. Job 1: The Worst Employee's own files were gone, so I proved the cause on a copy of another Key and Peele RAW of
   yours (the same shape and release group) instead.
2. Job 1: before Topaz starts on a part, its first frame is checked (about 1 s). If it would be wrong, the whole
   piece is enhanced at once (as before, only an hour earlier).
3. Job 2: a drop = the piece cannot be read at that moment, or the reason names a file of the Enhance folder and
   says "cannot be read / no such file / network ...". The laptop waits up to 30 minutes at a time, then lets the
   piece go and takes it again by itself. 6 such drops in one take while the folder answers each time: a real
   failure (as before).
4. Job 3: the fix is in the stretch check, not in probe.py (the probe was right; see above).
5. Job 4: the button is called "No Topaz" and has an undo ("Topaz again"). "No Topaz" stays over Try again. A
   finished enhanced picture that comes back for a "No Topaz" video is moved to `_to_delete`, never deleted. A video
   whose edit already plays the enhanced picture has no button (nothing to stop).
6. Jobs 2 and 4 are one commit: they change the same part of the worker (what happens when Topaz ends early).
7. Job 5: the video was The Best Business Idea In Key and Peele (2) (its clips are spread over minutes, so it makes
   many parts; its RAW is converted from an MKV: millisecond times, the case of job 1). Copies only, in
   `D:\mr_scratch\h21`; the app stayed closed; the laptop's pieces in `D:\Shared\Enhance` were not touched.
