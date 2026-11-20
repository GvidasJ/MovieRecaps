# Report -- 19 Nov 2026, the session on the PC (Claude Code)

For Gvidas. The short part first, the details after it.

## What you must do

1. **PC:** nothing. Open the app as always. It uses the new code from its next start.
2. **Laptop:** close the app. Open a terminal in the MovieRecaps folder and type `git pull`. (If you did not yet do
   yesterday's two lines, type `git checkout -- tools` first, then `git pull`.) Then open the app again.
3. One decision is waiting for you, see "A question only you can answer" below. Nothing stops without it.
4. When you have time: look at "Space I used" at the end and say what may go. I deleted nothing.

## What is different for you now

1. **No flash frame at the end of a clip in the Topaz picture.** This was found with the first whole video through
   the real Topaz (The Best Business Idea (2)): 5 of its 55 clips ended with ONE frame (1/60 s) of the next shot.
   Nobody put it there: Premiere shows the normal RAW's frame "that is on now", Topaz's 60 fps picture shows the
   "nearest" frame, so the last 1/60 s before a shot change showed the shot after it. Now, when the Topaz picture
   is put into the edit, the app looks at the first and the last frame of every clip, and where a clip runs into
   another shot it moves that clip's picture by 1 or 2 frames of 1/60 s (the cuts stay where they are). On that
   video: 5 clips moved, 0 flash frames left. If a clip cannot be fitted, the row says "look at N clip end(s)".
2. **A failed Topaz piece is tried another way each time**, not the same piece 3 times:
   1st the RAW's own frames, only the played parts -> 2nd an encoded piece, whole -> 3rd the RAW's own frames,
   whole -> 4th an encoded piece where every frame is a keyframe. A Topaz result whose picture does not line up
   with the edit is now also a failed try (before: the normal RAW simply stayed in the timeline).
3. **Every failure's full reason stays in the project:** `project.json` -> "enhance" -> "tries" (which try, how
   the piece was made, the reason, when), and `logs\topaz_failed.log` (the reason and everything Topaz said).
   Before, each new try wiped the reason of the one before.
4. **One more kind of flash frame is repaired by the tool itself:** "N frame(s) of a different RAW shot" when it is
   1 or 2 RAW frames long (up to 5 frames of a 60 fps sequence). It is the commonest note left in your projects
   (50 notes in 219 projects, half of them this short). The repair existed since October but could never work on
   these, because the flash was a tiny clip of its own and the repair was not allowed to remove a whole clip.
   Now the tiny clip goes and the next clip starts 1-2 frames earlier, on its own picture and sound.
5. **An edit never asks for RAW frames that do not exist.** Found in your project "The Worst Prank In The Office"
   (17 Nov): the competitor plays 0.2 s on after the RAW's last frame, and the edit's last clip asked for frames
   1602-1622 of a RAW that has 1611. The result was FAIL "outside its media", Premiere would skip or misplace that
   clip, Topaz had no picture for it -- so the piece was sent 3 times and the clip stayed on the normal RAW. Now the
   edit ends with the RAW's last frame, picture and sound together (12 frames of 1/60 s shorter there). The same
   video today: PASS, no note.
6. **The Topaz picture also goes on a clip that plays the RAW to its very last frame.** Two more things broke at
   the end of that short RAW: the app looked for frames after the end of the file and stopped ("frames not found"),
   and the real Topaz gave back a file one frame shorter than the RAW (1610 of 1611), so the last clip did not fit.
   Now the last clip plays the enhanced picture 1 frame of 1/60 s earlier (2 at most; the cut and the sound stay).
   The same video through the real Topaz today: 25 of 25 clips play the enhanced picture, zip 252 MB.
7. **A little faster, the result exactly the same.** The two slowest steps of an edit (search and refine) do the
   same sums with fewer copies of the pictures in memory. The Best Business Idea (2), nothing cached, free PC:
   34 min 56 s -> 33 min 19 s (about 5 %). The planned edit is the same byte for byte.

## What I tested, and how

- **All tests before every commit.** At the last commit: tool 1688 passed; app 1492 passed and 1 failed -- a test
  of a fade on the screen that measures time (`test_motion.py`), run while the PC was full with my other runs;
  alone it passes (11 of 11 in its file). 39 new tests today (7 retry, 5 clip ends after Topaz, 5 "the same
  numbers bit for bit", 10 flash clip, 7 the edit ends with the RAW, 5 the end of the Topaz file).
- **Real Topaz, whole video** (copies in `D:\mr_scratch\h19`, nothing of yours was touched, the app stayed closed):
  - **The Best Business Idea In Key and Peele (2):** edit -> ONE piece (3 parts, 2:28 of its 3:24) -> the real
    Topaz 46 minutes (8873 frames, 3.2 frames a second) -> accepted -> "55 of 55 V1 clips play it". Then every clip
    was checked at its first, middle and last frame against the normal RAW (`work\h19\check_clips.py`): 165 frames,
    all on the right frame; the 5 flash frames at clip ends found, fixed, checked again: none left.
    Zip 880 MB (the Topaz picture inside is 662 MB, about 36 Mb/s), opens without errors, `1_edit.xml` and
    `HOW TO OPEN.txt` inside. After the clean-up round only the zip is left and the Enhance folder is empty.
  - **The Funniest Rant In It's Always Sunny:** edit (PASS, no notes) -> ONE piece (1 part, 0:44 of its 4:25) ->
    the real Topaz -> accepted -> "9 of 9 V1 clips play it" -> 27 frames checked, all right, no clip had to be
    moved -> zip 376 MB -> clean-up round: only the zip is left. Topaz took 95 minutes for this short piece, but
    this is NOT a speed number: my test of the old videos was using the PC at the same time (see the notes).
  - **The Worst Prank In The Office** (a 27 s RAW from YouTube, 60 fps; the video that failed 3 times on 17 Nov):
    edit PASS, no notes -> ONE piece (the whole RAW) -> the real Topaz 17 minutes -> accepted -> "25 of 25 V1
    clips play it" -> 75 frames checked: 74 on the right frame, and the middle of the last clip 1 frame of 1/60 s
    early -- that is the clip moved on purpose because the Topaz file is 1 frame short (point 6) -> zip 252 MB ->
    clean-up round: only the zip is left.
- **The retry** was tested with the fake Topaz and made-up failures (a broken result, a result of the wrong
  length, a result that does not line up, a full disk): each next piece is made another way, and all reasons stay.
  The real Topaz did not fail once today, so no real second try happened.
- **Speed:** one edit with empty caches on the old code (2096 s) and one on the new code (1999 s), nothing else
  running. Where the time goes (old code): refine 968 s, search 702 s, segments + cut list 139 s, exports 93 s,
  convert 70 s, captions 44 s, checks 23 s. So search + refine are 80 % of an edit, and inside them almost all
  the time is two functions (measured with py-spy): the scoring of candidate frames and the framing fit. Those
  two are what I made lighter.
- **Why search / refine looked 3x slower on 17 Nov:** it was not slower. `work\run_times.jsonl` shows that run
  analysed a whole episode (21 minutes of RAW) with nothing cached: search 771 s, refine 1100 s. Today's clean run
  of The Best Business Idea (2) cost the same (702 s and 968 s). The quick runs you compared it with had these two
  steps partly in the cache. In the history, search costs between 1.2 s and 18 s for each second of the competitor
  video: it depends on the video (how many shots, how much of the RAW must be looked at), not on the day.
- **The old videos (accuracy):** all 11 old test videos (`tests\real`: video1, video2, video3, video4, video5,
  video017-fixed, video018-fixed, video020-fixed, laptop004-fixed, spiderman-school, zendaya-age), each made the
  way the app makes it for Topaz (60 fps sequence, fast mode, the checks of 1_edit.xml) three times from the same
  caches: the 60 fps fixes off (as before 18 Nov), as on 18 Nov, and as today. **33 of 33 edits PASS, no note, no
  problem in any of them** -- the fixes made nothing worse on any old video. (7 were done on 19 Nov, the other 4
  on 20 Nov, when the 19 Nov session had been cut off at 14:08.)
  The last change of the day (the edit ends with the RAW's last frame) was checked on its own: the edits made with
  the code as it is now are **byte for byte the same** as those of the morning (1_edit.xml, 2_captions.srt and the
  60 fps XML) on every old video. Then **check-all** (the slow, thorough checks: deliverables, determinism -- the
  same edit twice --, coverage) on all 11: **all hard checks passed**.

## What I did NOT do, and why

- **A real failed Topaz try** -- see above: Topaz did not fail, and I did not break it on purpose with your look.
- **The bigger speed step.** In the framing fit, one sum (numpy "lstsq") is about 30 % of the time and another
  way to do it is about 5x faster. But its numbers differ in the last digits, so some edit somewhere could come
  out one frame different. You said "without changing the result", so I left it. `work\h19\bench_lstsq.py`
  measures it.
- **More helpers at once** do not help: all 16 are already 100 % busy (measured again today).
- **The 8-frame flash** (see the question below) stays a note.
- **check-all on the newer test videos in the slow, thorough way** (it needs many hours with empty caches -- my
  speed change emptied them). The 11 old ones were done (above, finished on 20 Nov). The 21 newer ones (your fixed
  projects, `video8-fixed` ... `chocolate-fixed`) were NOT run: each needs 15-40 minutes from empty caches, about
  8-10 hours for all, one at a time. They are the next thing to run on a free PC night (`work\h19\ab60.py` with
  the list in `D:\mr_scratch\h19\ab60\queue_not_run.txt`).
- **A crash at the very end of the app's test run.** All tests pass, then Windows reports that the test program
  died while closing (code 0xC0000409, inside Qt). I can repeat it with one group of tests
  (`work\h19\app_crash_group.txt`), and the same group dies the same way on yesterday's code, so it is not from
  today. It is only in the tests' closing, not in the app. Not fixed.
- **The tests write into your real crash log** (`%APPDATA%\MovieRecaps\crash.log`: lines "the app started" /
  "closed normally"). Harmless, but it should not be so. Not fixed.
- **Not tested: the laptop**, pause + resume in the middle of a real Topaz run, and an edit running while Topaz
  works (one number by accident: with my test of the old videos running, Topaz was about 7x slower -- so two heavy
  things at once on one PC is a bad idea).

## What I decided for you (you said: do not ask)

1. The two videos for the real Topaz: The Best Business Idea In Key and Peele (2) and The Funniest Rant In It's
   Always Sunny. Only the competitor video and the RAW were copied (to `D:\mr_scratch\h19\src`).
2. The order of the ways of a retry (point 2 above). The first try stays as it was, so a good run is not slower.
3. A Topaz picture that does not line up with the edit is a failed try and goes again.
4. A clip that ends on the next shot: the clip's picture is moved, never the cut, and by 2 frames of 1/60 s at
   most. More than that is not "nearest frame" any more, so the row then asks you to look.
5. Speed: only changes that give the same numbers bit for bit.
6. Flash clips: only 1-2 RAW frames are removed. Your rule "3 frames and more are the competitor's own short shot"
   stays.
7. The proof on the old videos was made in the way that shows the 60 fps fixes alone: the same video three times
   from the same caches, fixes off / as on 18 Nov / as today.

## A question only you can answer

- A shot of the competitor's own that is shorter than 0.25 s but 3 RAW frames or longer (for example 8 frames of a
  60 fps sequence, 0.13 s): the tool still leaves it as a note "needs a look" and does not touch it -- an earlier
  decision says "3 frames and more are the competitor's own short shot". If you want these removed too (the next
  clip would start that much earlier), say so and the limit can be raised (`CUT_FLASH_MAX` in
  `match_cuts\export_xml_edl.py`).

## Space I used (nothing deleted -- you decide)

All of it is test data of today; none of it is needed by the app.

| folder | size | what |
|---|---|---|
| `D:\mr_scratch\h19` (all of it) | 56 GB | everything below; delete the whole folder when you want the space |
| `D:\mr_scratch\h19\ab60` | 27 GB | the old videos three times (fixes off / 18 Nov / 19 Nov) and their caches |
| `D:\mr_scratch\h19\pt_app`, `pt_*` | 13 GB | the test runs' temporary folders |
| `D:\mr_scratch\h19\time_A`, `time_B` | 7 GB | the two timed edits (old code / new code) |
| `D:\mr_scratch\h19\work_bi2r`, `work_rant`, `work_prank` | 6 GB | the caches of the three real-Topaz videos |
| `D:\mr_scratch\h19\src` | 1 GB | copies of the three videos' competitor + RAW |
| `D:\mr_scratch\h19\e2e_*`, `ca`, `dbg_bi2` | 3 GB | the end-to-end projects (only their zips are left), check-all |
| `work\snap19*` (8 folders) | 128 MB | frozen copies of the tool's code for the comparisons |
| `work\h19` | 0.4 MB | my scripts and logs (worth keeping) |

(Measured on 20 Nov, 07:50. The 20 Nov session still uses `D:\mr_scratch\h19\ab60` and `ca` until its job 1 is
done.)

## Notes for the next Claude session

- The real Topaz's 60 fps picture shows the NEAREST RAW frame; the tests' fake Topaz (ffmpeg `fps=60`) shows the
  frame that is on. A test about frame positions after the swap must use the `nearest` fixture of
  `tools\app\tests\test_swap_ends_19nov.py`, and a change to the swap needs one real-Topaz run checked with
  `work\h19\check_clips.py PROJECT`.
- Tools of today in `work\h19` (scratch, not committed): `e2e.py` (one video end to end in a scratch folder,
  `--real-topaz`), `check_clips.py`, `reswap.py`, `ab60.py` (fixes off / 18 Nov / today from one frozen copy),
  `ca_follow.py` (check-all behind it on the same caches), `spy.ps1` + `spy_top.py` (py-spy on the helpers),
  `time_edit.sh`, `dbg_flash.py` + `try_flash.py` (what the flash repair decided and why).
- Any edit of `scoring.py` or `refine.py` empties every stage cache of every case. A check-all after it starts
  from nothing: plan hours.
- A timed run needs the PC alone: pytest or a second edit at the same time made it 10-300 % slower today.
- Git Bash shows exit 127 when a Windows program dies with a fatal status; PowerShell's `$LASTEXITCODE` shows the
  real code.
- Open, in the order I would take them: (1) the app suite's crash while closing; (2) the tests writing into the
  real crash.log; (3) the user's answer about 3+ frame flashes; (4) a real failed Topaz try through the retry
  ladder; (5) pause + resume in a real Topaz run; (6) the laptop.
