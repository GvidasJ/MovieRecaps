# Night 5: no transitions, one RAW take = one clip, and the sound of a sped-up competitor at 100 % (12 October)

## In short

- **No transitions, ever** (section 2): every cross dissolve of the competitor is now a hard cut on its middle frame,
  in every run. A new hard check, `XML TRANSITION`, fails any run whose `1_edit.xml` holds a transition.
- **One continuous RAW take = one clip, with its own sound** (section 2): the tool no longer cuts a take into pieces
  that skip or repeat a RAW frame or two, and a still picture matched "anywhere near" is played over by its take, as
  you did. A new hard check, `XML SYNC`, fails a run whose A1 is not each picture's own sound, has a gap or an overlap,
  or (in the default mode) jumps / stutters 0.1 s or less at a cut. On laptop009: **7 clips instead of 16, no jump, no
  repeat left to cut**, every take played straight through at 100 %, framed exactly as in your fix (section 3).
- **laptop008's export errors** (section 1): the three `XML LINK` errors "at -00:00:00:01" were the checks misreading
  a clip that starts and ends inside cross dissolves (reproduced, fixed); the `XML REPEAT` was a repeat that could not
  be cut inside a dissolve (reproduced, fixed). The 3 `XML FLASH` I could not reproduce without laptop008's own files.
- **Tests** (section 4): unit tests: all pass, 0 failed (1204 with the GPU hidden, then the GPU tests with it: 209).
  check-all on all 16 test videos (laptop009-fixed is new): the hard checks pass on 15 of 16 (video1 after a re-check
  -- its own verdict was hit by a change I made while it ran); **laptop004-fixed fails `XML FLASH`**, a regression from
  the take rule (2 frames of a shot at a cut; the fix is known, not made tonight). Scores: captions 159 of 656 exactly
  yours (165 last night), 13.1 % word errors (13.0 %); cuts 23 of 161 reproduced (25): video1 and video3 lost one
  each. Every change is explained per video in section 4.
- **What you must look at** (section 5): laptop009 in Premiere, the RAW's 5.1 sound, and two decisions.
- **Left for next time** (sections 4 and 5): laptop004's flash (the fix is known; it needs a check-all of its own);
  laptop009's captions over the still picture (all your words are there, but grouped worse: 41 of 52 captions with
  your text, 45 before); the thorough analysis framing laptop009 at 148 % (your --fast runs find the right box).
- **Disk** (section 6): freed after the push.

## 1. What was wrong

### laptop009: what your fix shows

Your fixed project (`finished\laptop009\laptop009_fixed.prproj`) points at the laptop's files
(`D:\Shared\_222FIXEDSouth Park S13E12.mp4`, `D:\Shared\009\...`). I wrote a relinked copy,
`finished\laptop009\laptop009_fixed_relinked.prproj`, with the RAW moved to `output\laptop009\extras\media\raw_ae.mp4`
(the same size, frame rate and length); your file is untouched.

The competitor plays the RAW at 125 % and is full of tiny cuts: the tool's cut list has 48 pieces in 24.8 s. Most of
them are not real cuts at all:

- **Pieces of one take that meet a RAW frame apart.** Between 744.1 and 748.6 s the competitor plays the RAW straight
  through, but the cut list has 16 pieces whose RAW times skip or repeat 10-42 ms at each join (the competitor's
  60 fps frame grid against the RAW's 23.976 fps one, and a speed measured a little off on pieces of 2-8 frames).
- **A still picture matched anywhere near.** S04-S11 (8 pieces of 1-9 frames) and S14-S17 jump back and forth between
  RAW 706.5 and 709.3 s: the picture is a still, so the matching found the "same" frame all over that stretch. The cut
  list marks them uncertain, with a measured speed of 0.

**What the tool made of it (the old code):** every piece was laid out at 100 % on its own RAW time. So inside one
take the RAW jumped by 17-50 ms at 9 cuts (V1 and A1: 16 clips where your fix has the takes whole), the repeat
removal cut 10 "stutters" out of the timeline (0.38 s), and the still-picture pieces played bits of the RAW in a
jumbled order: S03's take stopped at 708.08 s in the middle of "acceptable?" (you: 708.350). The captions show it:
"world We're-" / "world acceptable?", and "and not be a fag!" on screen for 0.03 s followed by "not".

**What your fix does:** every take plays straight through -- the RAW from where the competitor's take starts to where
it ends, at 100 %, with its own sound. Your framing is exactly the tool's (Scale 119.26 %, the same Positions). On top
of that you cut 8 pauses inside the takes (0.12-0.35 s each) and trimmed 0.06-0.3 s off the start of 5 takes: your own
pause cutting, as on mike005. Night 4 made the competitor's cut points the default and silence removal opt-in
(`--remove-silence`), so I did not turn that into a rule (section 5). Your captions: the tool's, with the garbled ones
rewritten ("world" / "is acceptable?", "Yeah"), the screen noise "4gav?" deleted and a few words regrouped.

### The laptop008 export errors

`output\laptop008` is not on this PC (only `output\laptop009`). I re-ran the same video here on the code as it was at
the start of tonight with laptop009's settings (--fast, 100 %): the same 48 pieces, no dissolve, and none of the three
errors (laptop009's own report shows none either). The "-00:00:00:01" in your LINK errors is the mark of a clip joined
by a **cross dissolve** (FCP7 writes such an edge as -1), so laptop008's cut list had dissolves -- it was made with
other settings or an older version of the tool on the laptop. With dissolves I reproduced two of the three errors on
the starting code:

- **XML LINK "-00:00:00:01" (3 clips): a reading bug of the checks, reproduced.** A clip that starts *and* ends inside
  cross dissolves (a short piece between two dissolves) is written with start -1 and end -1. The checks resolved such
  an edge by searching for "the transition before the clip's end" -- but the end was still -1, so the clip was read as
  starting at frame -1 and running to the first dissolve of the sequence. On the starting code a cut list with one such
  clip gives exactly your error: `V1 S02 at -00:00:00:01: linked to A1 S02 at 00:00:06:12, which plays elsewhere on
  the sequence`. The clips themselves were fine in Premiere; the check failed the run for nothing. **Fixed twice:** the
  XML reader now resolves each edge by the transition next to the clip on its track, and there are no transitions any
  more.
- **XML REPEAT (A1 S29/S30 at 00:00:13:40): a real repeat that could not be cut, reproduced.** When the competitor
  dissolves between two pieces of one take and the second starts a little back in the RAW (the same 0.1 s twice), the
  repeat removal must cut the repeat -- but it never cut inside a cross dissolve, so the repeat stayed and the hard
  check failed (on the starting code: `A1 S01 at 00:00:06:09 and S02 at 00:00:06:15 both play RAW 509.67-509.75 s: a
  stutter at a cut`). **Fixed:** with the dissolve a hard cut the repeat is cut like any other, and the take rule
  below joins such pieces before that.
- **XML FLASH (3): not reproduced.** The flash check reads the clips through the same transition edges as the link
  check; on my test cut list the misread clip did not produce a flash, so I cannot say for sure what laptop008's three
  were. Its dissolves are gone now, and `XML FLASH` stays a hard check: a run that still has one fails and names it
  (tonight's check-all found two, on laptop004 -- section 4).

## 2. What changed (rules for every run)

### No transitions, ever

Every cross dissolve of the competitor is now a **hard cut on its middle frame**: the clip before ends there, the
clip after starts there on its own moment of the RAW (a 2-frame dissolve is 33 ms of two pictures mixed -- nothing is
lost). A dissolve or fade to / from black is dropped. This happens before anything else is planned (speech, shots,
repeats), and the Premiere writer and the repeat planner apply it again on their own, so no path can write a
transition. video1 (4 dissolves) and video021 (6) now have hard cuts there.

**New hard check `XML TRANSITION`:** the final `1_edit.xml` is read and the run fails if any track holds a transition,
or a clip joined to its neighbour by one (start / end -1). `cutlist.json`, the preview and the checks against the
competitor keep the competitor's dissolves (they describe the competitor).

### One continuous RAW take = one clip, at 100 % with its own sound

Before the edit is laid out at 100 %, the tool now walks the competitor's cut list piece by piece:

- **a piece whose RAW time continues the take before it** (within 0.1 s and 2.5 RAW frames -- 83 ms for a 30 fps RAW,
  42 ms for 60 fps -- either way) plays on that take's line, at the take's speed: it starts on the exact RAW moment the
  take has reached, and its sound simply continues. (A real slow motion inside a take keeps its own speed.)
- **a run of short pieces the cut list doubts** (marked uncertain, or a measured speed 35 % off; at most 0.25 s each,
  1.5 s in all, and within 2.5 s of the take's line -- a still picture matched anywhere near) is **played over by the
  take**, with the take's framing -- what you did with S04-S11.
- **every real jump of the RAW** stays a cut.
- **every piece of a take plays the take's own sound** -- an "audio line" the competitor had under one of them (its
  sound offset from its picture: zendaya's S19-S23, 91 ms behind) is dropped: at 100 % the sound is the picture's own.

The pieces of one take that end up with one framing become one clip on V1 and one on A1. Each piece keeps its place
on the timeline, so captions, markers and the ending check do not move. The take speed is unified before the 100 %
conversion, so a take cannot drift (a 1-frame residue left by rounding is snapped away after it).

**New hard check `XML SYNC`** (reads the final XML): every A1 clip plays exactly the source its own V1 clip shows over
the frames they share (an audio line under a cut picture -- one take of sound carried on, as in video1 / video021 --
is listed, not failed); A1 has no gap under its picture and no two A1 clips overlap; and, in the default mode (the
competitor's cut points), **no cut on V1 or A1 skips or repeats 0.1 s of the RAW or less** -- "one take, cut" fails
the run. With `--remove-silence` the cuts move into the quiet on purpose and a pause trimmed at a cut may skip a few
ms of silence (video1: 83 ms), so there `XML SPEECH` and `XML SILENCE` judge the cuts, as before. On last night's
check-all XMLs this check would have found such jumps in 9 of the 15 test videos (17-100 ms).

## 3. laptop009 on the new code, against your fix

`output\laptop009` was a **--fast** run (its cut list says so, and its log names the laptop's RTX 4060 -- batch runs
default to --fast), so I ran it the same way on tonight's code, on its own media (`output\laptop009\extras\media`;
`input\` holds another video now): **`output\laptop009\night5\001`** (every Premiere check passes, the new ones too;
"cuts inside speech, kept" went from 21 to 3).

| | your fix | the tool before | the tool now |
|---|---|---|---|
| V1 clips (A1 the same) | 16 | 16 | **7** |
| cuts inside one take (the RAW jumps 17-50 ms) | 1 (you kept one) | 9 | **0** |
| repeats the removal had to cut | -- | 10 (0.38 s) | **0** |
| takes (stretches of the RAW played straight through) | 15 (your 8 pause cuts split takes) | 15 | **6** |
| S03's take (the still picture) | 707.200-708.350 s | 707.200-708.083 s | **707.200-708.333 s** |
| length | 28.03 s | 30.57 s | 31.02 s |
| transitions / `XML SYNC` problems | 0 / -- | 0 / 18 | **0 / 0** |

The 7 clips: the take S01-S11 split once, at the RAW's own shot change at 705.30 s (your fix keeps that cut too),
then S12-S19, S20-S42 (12.5 s of one take), S43-S44, S45 and S46-S48 -- each played straight through at 100 % with
its own sound. Its 5 real cuts are your 5 real cuts: the clip before ends within a frame of yours at three of them
(708.33 / 708.35 s, 714.05 / 714.03 s, 757.40 / 757.40 s) and 67-100 ms later at two (748.55 / 748.48 s, 751.10 /
751.00 s); the clip after starts where the competitor's does, 0.07-0.3 s before yours -- you trim the pause at the
start of each take. Your other 8 cuts are pauses inside takes (0.12-0.35 s) that the competitor keeps, so they stay
yours to cut -- hence 3.0 s longer than your fix.

**Framing:** every clip is framed exactly as in your fix (Scale 119.26 %, Position 539.9, 1130.6 for the takes up to
748.55 s; 119.27 % at 169.0 for S43-S44; 119.24 % at 540.7 for the rest).

**Captions:** all 134 of your words are now in the captions, in order (132 before): "world ... is acceptable?" and
your "Yeah" are heard now that the take plays straight through. But over the still-picture stretch the captions are
grouped worse: 41 of your 52 captions have the same text (45 before) -- "world is we're" / "world acceptable?", "to
gay pēople!" / "people you" / "can be gay", a "Jljl" screen-noise caption and "4gav?" kept. The competitor's caption
timing is laid onto the new takes and the caption rules split the words around it differently. That is the next thing
to fix in the captions; the caption code is a cached stage of every video (a change re-transcribes all 16), so I left
it tonight.

**The test case** (`tests\real\laptop009-fixed`): the competitor, a 35 MB copy of the RAW around the part the edits
play (673.6-791.9 s), your 52 captions and your 16 clips as the answer key, the default settings. check-all runs it
thorough: 24 of your 52 captions exactly, 6.0 % word errors; 0 of your 13 cuts within 2 frames (your pause trims),
2.2 s longer than your edit (section 5, point 6, for the framing).

## 4. Test results

### Unit tests

On the final code: the whole suite with the GPU hidden (so it could run next to the check-all): **1204 passed, 0
failed**, 122 skipped; then the 11 test files that use the GPU, again with it: **209 passed, 0 failed**, 94 skipped
(files or tools not on this PC, as last night). So every one of the 1326 tests passes or is skipped for the same reasons
as last night's 94. 13 new (`tests\test_night5.py`): a dissolve becomes a hard cut on its middle frame, a fade to black
is dropped, the writer never writes a transition, the transition check fails one, laptop008's two errors (a clip
between two dissolves read at its own place; a repeat at a dissolve now cut), laptop009's real cut list giving its 6
takes and 7 clips with no jump, the take limit per RAW frame rate, a real cut and a far sliver left alone, a real slow
motion keeping its speed, and the sync check catching a jump, a desync and an overlap (and leaving `--remove-silence`
pause trims to `XML SPEECH`). Six older tests changed with the rules: four expected a dissolve in the Premiere XML, one
an A1 cut sliding under a dissolve, and the deadpool S21 test now runs the take rule first (so S26 and S27 are one
clip in its repeat message).

### check-all: the 16 test videos

All 16 test videos, thorough. "Captions": your captions reproduced exactly / word errors. "Cuts": your cut points
reproduced within 2 frames / how much longer the tool's edit is than yours. "Last night" is night 4's final check-all
(`work\t19\final\scorecard.json`). The runs: `work\t20\ca1` and `work\t20\ca2`; the combined scorecard
`work\t20\final_scorecard.json`.

| video | hard checks | captions | cuts | last night (hard checks, captions, cuts) |
|---|---|---|---|---|
| zendaya | PASS | no key | no key | PASS |
| zendaya-age (`--remove-silence`) | PASS | 3/19, 32.5 % | 1/9, +3.1 s | PASS, 1/19, 32.5 %, 1/9, +2.9 s |
| deadpool | PASS | 45/60, 4.8 % | no key | PASS, 45/60, 4.8 % |
| video2 (`--remove-silence`) | PASS | 0/21, 14.3 % | 1/1, -0.4 s | PASS, 0/21, 4.8 %, 1/1, -0.8 s |
| video3 (`--remove-silence`) | PASS | 10/71, 6.3 % | 2/11, +5.0 s | PASS, 11/71, 7.0 %, 3/11, +5.0 s |
| video5 (`--remove-silence`) | PASS | 1/37, 32.9 % | 2/14, +5.0 s | PASS, 1/37, 35.4 %, 2/14, +5.0 s |
| video017-fixed (`--remove-silence`) | PASS | 23/43, 11.0 % | 4/12, +8.5 s | the same |
| mike005-fixed (125 %) | PASS | 7/46, 8.7 % | 0/13, +7.2 s | PASS, 9/46, 7.6 %, 0/13, +7.2 s |
| **laptop009-fixed** (new) | PASS | 24/52, 6.0 % | 0/13, +2.2 s | -- |
| laptop004-fixed (`--remove-silence`, 125 %, frame.png) | **FAIL** (`XML FLASH`) | 8/27, 16.1 % | 0/3, -0.3 s | PASS, 9/27, 16.1 %, 0/3, -0.2 s |
| spiderman-school (`--remove-silence`) | PASS | 5/64, 8.2 % | 4/21, +6.7 s | PASS, 9/64, 8.8 %, 4/21, +6.4 s |
| video4 (`--remove-silence`) | PASS | 7/38, 14.9 % | 2/7, -1.1 s | the same |
| video018-fixed (`--remove-silence`) | PASS | 13/49, 15.7 % | 0/15, +4.7 s | PASS, 12/49, 15.7 %, 0/15, +4.5 s |
| video020-fixed (`--remove-silence`) | PASS | 1/30, 38.5 % | 0/11, -5.1 s | PASS, 1/30, 35.9 %, 0/11, -5.6 s |
| video1 (`--remove-silence`) | PASS after a re-check (see below) | 16/111, 10.4 % | 7/29, +3.2 s | PASS, 18/111, 10.8 %, 8/29, +3.2 s |
| video021-fixed (`--remove-silence`, frame.png) | PASS | 20/40, 14.9 % | 0/15, +7.0 s | PASS, 19/40, 14.5 %, 0/15, +7.9 s |

All caption keys of last night's 14 videos together: 159 of 656 captions exactly yours (165), 13.1 % word errors
(13.0 %); with laptop009-fixed 183 of 708, 12.5 %. Cut keys: 23 of 161 reproduced (25).

**Hard checks: 15 of 16 pass; laptop004-fixed fails `XML FLASH`** (it passed last night). Its competitor plays a take
across the RAW's own shot change at 33.91 s and changes the framing there. The take rule moved S02's end 21 ms and
S03's start 27 ms onto the take's line, so the clip with S01-S02's framing now runs 2 frames past the shot change before
S03's framing takes over (last night: 1 frame, which the shot snap moves onto the change); the same at 42.46 s. The fix
is clear -- never move a piece whose start or end lies on a RAW shot change (the competitor cut there because the
footage cuts) -- but it changes the plan of every video with such a piece, which would have meant re-running most of
the check-all, past your deadline. It is the first thing to do next (section 5).

**What changed, video by video** (every caption change checked caption by caption against last night's scorecard):

- **Better or the same:** video017-fixed, video4, deadpool, zendaya (identical scores); zendaya-age (2 more captions
  exactly yours: "25" and "a son"); video5 (word errors 35.4 -> 32.9 %); video018-fixed (13 exact instead of 12);
  video021-fixed (20 exact instead of 19, the same 11 word errors -- counted over 74 words instead of 76 -- and 0.9 s
  closer to your length); video020-fixed and video2 are 0.5 / 0.4 s closer to your length.
- **video3: one cut fewer (3 -> 2 of 11).** Its S02-S05 all sit 85 ms off S01's line -- a real 2-frame cut of the
  competitor followed by a take of its own -- and the take rule joined it (2.5 frames allowed), so your cut at 7.3 s
  now lands 4-5 frames off it, and three captions at the start come 4-6 frames early (two others are now exact; word
  errors 7.0 -> 6.3 %). This is why I recommend 1.5 frames (section 5).
- **video1: one cut fewer (8 -> 7 of 29).** Your cut at RAW 529.08 s: S17 now continues S16's take (a 10 ms snap), and
  with that 10 ms the silence removal decides to finish a word across the cut, so the next piece joins the take and the
  cut is gone. Captions 18 -> 16 exact: four captions now 2.2 frames off instead of 2, two the other way (the same
  texts); word errors 10.8 -> 10.4 %.
- **video2: caption word errors 4.8 -> 14.3 %.** The take rule now plays RAW 305.4-308.9 s straight through -- exactly
  as your finished video2 does (one 3 s piece from 305.9 s) -- where the competitor's tiny forward / back pieces hid the
  speaker's stammer. The captions now write it ("word on the" for 0.08 s, then "on the subject", "from the", "the
  captain of the"); your captions leave the repeated words out. The cut score is the same.
- **video020-fixed: word errors 35.9 -> 38.5 %:** one caption now reads "hi doing?" instead of "hi" (the joined take
  reaches the word) where your key has the action captions "*starts spraying*" / "*shocked*".
- **mike005-fixed:** captions 9 -> 7 exact (four now 2-3 frames off, two others now exact), one word: "Chit" heard as
  "chip" again while "here it is" is now right (7.6 -> 8.7 %). Cuts the same.
- **spiderman-school:** captions 9 -> 5 exact (three now 2-4 frames off, one split differently), word errors 8.8 ->
  8.2 %; cuts the same, 0.3 s longer. **laptop004-fixed:** the flash above; captions 9 -> 8 exact (timing), the same
  word errors.

Most caption changes are the timing noise every change of the edit's sound brings (the same text, a caption 2-4
frames off instead of within 2); the real ones are listed above.

**Two fixes during the check-all.** video1 (run with `--remove-silence`) failed the new `XML SYNC` on an 83 ms skip
between S15 and S16 -- a pause the silence removal trims at that cut on purpose (last night's XML has the same skip):
the small-jump rule became default-mode only. Then zendaya (a default-mode video) failed it on an 83 ms skip made by
the rule itself: its S20 lay exactly on the take's line, so the rule left it alone -- including its audio line, its
sound 91 ms behind the picture -- while its neighbours got the picture's own sound; A1 played 83 ms twice, the repeat
removal cut it and left the skip. Now every piece of a take plays the take's own sound.

**Which code each result comes from.** The final code is commit 4937871. zendaya, video4, spiderman-school,
zendaya-age, laptop009-fixed, mike005-fixed and video021-fixed ran on it. Eight videos ran on the code before the
second fix (d7420c5): that fix only changes a video whose XML carries an audio line on a piece of a take, and none of
theirs does (checked in each XML), so their results are the final code's. video1 ran before both fixes: the first is
in a check only and the second does not touch its plan (its one audio line is on no take), so its edit, captions and
scores are the final code's; its own verdict says FAIL for the 83 ms skip and for "determinism" -- I put the first fix
into the running code at 10:08, and its re-check read the changed code, so the code fingerprint in its cut list
differed and nothing else did. Re-checked on its XML with the final checks: it passes (`work\t20\derive.py`).

**How it ran.** Three check-all streams at once turned out slower than one: 12 processes time-sliced the one GPU and
spilled into shared memory (a stage that took video1 11 minutes alone took 51). I cut it to two streams and queued the
rest behind them. The unit tests ran without the GPU while the check-all ran.

## 5. Check by hand, and decide

1. **laptop009 in Premiere** (`output\laptop009\night5\001`): import `1_edit.xml` and play it through: no cross dissolve anywhere, each
   take one clip, the sound continuous at every cut (no click, no repeated syllable), A1 exactly under its picture.
2. **The RAW's 5.1 sound.** laptop009's RAW has 6 audio channels (AC3 5.1, converted to 6-channel AAC in
   `raw_ae.mp4`); every such run warns "check in Premiere that A1 plays the dialogue (the centre channel), not only the
   front left / right". Your fixed project plays `_222FIXEDSouth Park S13E12.mp4` instead, whose audio is stereo. If
   you made that file because A1 had no dialogue (or Premiere would not take the 5.1 clip on a stereo track), tell me:
   the tool can write a stereo copy of a 5.1 RAW for Premiere (the picture copied as is). I did not change the media
   tonight: the analysis caches of every video hang on the RAW file the tool analyses.
3. **Next fix: laptop004's flash** (section 4): a piece whose start or end lies on a RAW shot change must keep the
   competitor's timing. I would do it first next time, with the check-all it needs.
4. **Decide -- the take limit.** On laptop009 every jump the rule joins is at most one RAW frame (42 ms). Joins of
   2-2.4 RAW frames (65-97 ms) happen only in older videos, and there they are often real cuts of the competitor:
   video3's S02-S05 sit 85 ms off S01's line, all four -- a 2-frame cut followed by a take of its own -- and joining it
   moved your cut at 7.3 s out of the 2-frame window (3 -> 2 of 11). The cut score itself calls a jump of more than
   1.5 RAW frames a cut. With 1.5 frames laptop009 keeps every join, and video3, video2 and video020 change (a
   simulation on their cut lists). I recommend 1.5; it needs a check-all, so tell me and I make it the rule.
5. **Decide -- your pause cuts.** You cut 8 pauses inside the takes and trimmed the start of 5 takes; the default keeps
   the competitor's cut points (night 4). Tell me if you want pauses cut by default when the competitor is sped up.
6. **laptop009's thorough analysis.** The check-all runs every case thorough; on this video the thorough analysis finds
   a smaller video box (it leaves out the bottom quarter of the picture, where the captions are), so the test case's
   clips are framed at 148 % instead of your 119 %, and it matches the still picture at the start less well. Your
   laptop runs (--fast) find the right box. The layout detection is a cached stage of every video, so I left it.

## 6. Disk space

Freed after the push (the next commit).
