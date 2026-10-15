# Night 8 (15 October): your video7 / video8 / video9 fixes, and a picture not found while its sound is

## In short

Everything on your list is done. In one line each:

1. **Your three fixes** (all three were here; nothing skipped) taught two new rules, and they are now three new test
   cases that check-all scores:
   * **A cartoon drawn on twos no longer looks like a shot change at every new drawing** (video8). The laptop's run
     cut your one take into 25 clips and failed on 6 "flash frames" that were not there; now it makes 5 clips and
     passes.
   * **When your template's narrower window cuts the competitor's only people in half, it slides onto the person**
     (video7: "Hey woman, stop fingering me" -- you moved it +267 px onto the man; the tool now +289 px).
   * check-all has a new score, **framing**: how many of your clips the tool frames as you did.
   * Several of your changes are **not** rules (the pauses with a sound in them, the PETROL / DYNAMITE framing, the
     spelled letters, caption groups) -- section 1 says why for each.
2. **Picture not found, sound found** (item 2): the tool now **searches the picture again** -- the picture area
   measured again (the biggest moving area, the whole frame), then a denser search with a wider zoom range (every
   search also tries the mirrored picture). If it still finds nothing, the run **stops** with **"PICTURE NOT FOUND
   (sound found) -- the RAW may be a different version (for example credits squeezed at the end), mirrored, or
   heavily zoomed"** in the end summary and the batch log, and an empty edit is **never delivered** (also a new hard
   check, XML EMPTY). Tested on made-up competitors made from real footage: a tiny wrong picture area like your
   laptop's (0 % found) is now found 100 %, in 24 s.
3. **Tests**: every test passes (1318 unit tests, 97 GPU tests, 23 slow end-to-end tests). The final check-all (22
   videos, your three new ones included): **20 of 22 pass every hard check** -- the 2 that do not are the known ones
   (video020 `--fast`, video6's last frame) -- and **nothing got worse than night 7**: 20 videos give exactly the
   same edit and captions, byte for byte; only video7 (the framing rule: 3 -> 5 of your 10 clips) and video8 (now
   PASS) changed. A batch of your video7 / video8 / video9 at full size, as on your laptop: **3 of 3 passed** in 15
   min.
4. **On the laptop**: `git pull` (section 4).

## 1. What your three fixes taught (item 1)

### The files

All three projects and runs are here, so nothing was skipped. Each project points at `D:\Shared\...` (the laptop), so
I wrote a relinked copy next to each that points at the run here (your originals are untouched):

| your project | the sequence that is your edit | the laptop's run | here |
|---|---|---|---|
| `finished\video7\video7_fixed.prproj` | the 6th of 6 (23.976 fps, 20.9 s) | `output\002` (a single `--fast` run) | `output\laptop_video7` |
| `finished\video8\video8_fixed.prproj` | the 7th of 8 (23.976 fps, 8.9 s) | `output\video1-3` (`--fast` batch) | `output\laptop_video8` |
| `finished\video9\video9_fixed.prproj` | the 8th of 8 (23.976 fps, 9.1 s) | `output\video2-3` (`--fast` batch) | `output\laptop_video9` |

The copies are `finished\video7\video7_fixed_relinked.prproj` (and the same for 8 and 9). The other sequences in
the projects are your template's earlier videos (video8's project also holds video9's sequence before you edited
it). All three runs used the default settings: 100 %, your template, the competitor's cut points.

### What you changed, and what I did with it

As you said, I left out the audio, the trims at cut points, and the starts and endings (all three: you started later
and ended elsewhere).

**video7** (a restaurant scene; the competitor shows a 4:3 crop of the 16:9 episode and moves it shot by shot):

| | the tool (laptop) | your fix | what I did |
|---|---|---|---|
| "Hey woman, stop fingering me" (2 clips) | the window in the middle of the competitor's picture: the man's face cut in half on its left edge, the woman's on its right | moved +267 px onto the man | **new rule 2** |
| the young man in profile | the back of his head cut | moved +173 px | not reached (below) |
| one more shot of him | his face inside, off centre | moved -168 px | not a rule (below) |
| the wide restaurant shot | the competitor's framing | zoomed to 135 % onto the standing man | not a rule (below) |
| the pause "Hey woman / stop" (0.46 s) | kept | cut | not a rule (below) |
| captions | "Hey woman stop" / "fingering" / "me", "Dad?", "I'm the head of" / "the household" | "Hey woman" / "stop fingering me", "dad?", "I'm the head" / "of the household", a third "you", "\*sits down\*" | not rules (below) |

**video8** (an old DVD cartoon, 720x480, drawn on twos):

| | the tool (laptop) | your fix | what I did |
|---|---|---|---|
| the run | **FAIL**: your one take cut into 25 clips, 6 "flash frames" | 12 clips, no flash | **new rule 1**: the flashes were false |
| the first 6 clips | the middle of the picture | +178 px (and 2 % less zoom) to show the PETROL can while he spells it; the 7th clip panned | not a rule (below) |
| the pauses after "huh" (0.42 s) and after "water" (0.75 s) | kept | cut | not a rule -- but rule 1 now cuts part of the first (below) |
| "P-E-T-R-O-L" | one caption | P / E / T / R / O / L, one letter each | not a rule (below) |
| "This'll" | "This'll" | "this will" | glossary (learn adds your corrections) |
| "\*spills petrol\*", "\*explodes\*" | -- | your action captions | -- (the tool cannot see actions) |

**video9** (an animated film; a landscape competitor):

| | the tool (laptop) | your fix | what I did |
|---|---|---|---|
| the candle being lit (1.0 s, no words) | kept | cut | not a rule (below) |
| the DYNAMITE close-up | the middle | panned over the word | not a rule (below) |
| "Bean on meat cake!", "There's the candle", "Candles light em", "Wow" | as heard | "Dynamite", "Lose the candle", "Candles" / "light them" / "up!", "wow" | glossary (learn: "ah There's" -> "ahh Lose") |

### New rule 1 -- a cartoon drawn on twos has no shot change at every new drawing (video8)

The tool finds the RAW's own shot changes by comparing each frame with the one before (on small 64x36 copies): a
shot change is a difference 4 times bigger than the usual motion around it. In a cartoon drawn on twos every drawing
is held for two frames, so half of the differences around a new drawing are zero -- the "usual motion" came out as
zero, and every new drawing of a fast move looked like a shot change: 34 of them in the 14 s your edit plays. The
tool then cut your one take at each of them (25 clips, some 2 frames long) and its flash check failed the run 6 times
("a piece of a RAW shot under 0.25 s") -- all false.

Now the held frames are left out of the "usual motion" when both sides of a change have at least two frames that do
move. **Checked on all 22 check-all RAWs where their edits play: the other 21 give exactly the same shot changes as
before** (live action, CG cartoons, video021's cut from a still shot to a moving one); video8 goes from 34 to 2, and
both are real cuts (the cut to the PETROL can, and the one after "water"). video8 now makes **5 clips** and **passes**
every hard check (night 7's code: 26 clips, FAIL). With the false shot changes gone, the tool also cuts part of the
pause after "huh" (0.25 s; you cut 0.42 s there).

### New rule 2 -- a person your narrower window cuts in half is shown (video7)

Your template's window (1040 px wide) is narrower than many competitors' pictures. The tool keeps the competitor's
framing and puts the window in the middle of it. In video7 the competitor's 4:3 crop shows the man and the woman
whole -- the middle of it showed half of each. You moved it onto the man, right up to the competitor's edge.

Now, for a competitor that frames each shot itself: when the window in the middle of the competitor's picture shows
**nobody whole** (every face inside the competitor's picture cut by the window's edge -- more than 15 % of it), the
window **slides just far enough to show the person whole** -- the one speaking if they are inside the competitor's
picture, else the biggest face -- **never past the competitor's own picture**, the zoom kept. On video7 this gives
**+289 px** for the clip that plays both of your clips there (yours +267 px: the same within the 40 px the score
allows; the window stops at the competitor's edge, as yours).

It leaves the competitor's framing alone when:
* the window already shows someone whole (the competitor's choice of whom to show stays, as since night 3 --
  laptop010's close-ups);
* three or more faces are inside the competitor's picture (a wide shot -- video7's restaurant: you zoomed in there,
  which no rule does);
* the competitor shows the whole video through **one crop** (it chose nobody shot by shot): your video6 fix kept the
  middle on all 9 clips, even where Mort's face was cut at the window's edge -- a first version of the rule moved two
  of them, so it does not apply to such a competitor (laptop013 is one too);
* the slide would be more than half the window: that is a jump to another part of the picture, not a slide onto
  someone at the edge. (A first version, which centred the face, jumped two of video021's clips by 45-54 % of the
  window -- one of them ended 680 px from your framing. Sliding only as far as needed, it moves none of them.)

### A new score in check-all: framing

Each answer key made from a project now also holds your framing (each picture clip's position and zoom), and
check-all scores it: **framing = how many of your clips the tool frames as you did** (within 40 px sideways and 3 %
zoom -- what learn calls "moved" / "zoomed"; a clip you animated is not compared). Your earlier fixes' keys got
their framing too (laptop009/010/013, mike005, video021, video6), read from the same projects (nothing else in them
changed). With night 7's code the tool framed **55 of your 104 clips** as you did; with tonight's 57.
(mike005's 0 of 14: you zoomed every clip 7-8 % more than the tool -- a known difference, "zoomed in on 4 videos" in
learn's suggestions.)

### Not made into rules

* **The pauses you cut** (video7's 0.46 s between "Hey woman" and "stop", video8's after "huh" and after "water",
  video9's 1 s while the candle is lit): none of them is silent -- the restaurant's chatter, a cartoon sound effect,
  music, the lighter. Measured as the tool measures a pause (how far its loudest 10 % reaches from the background
  noise towards the speech level): 0.29, 0.97, 0.55 and 0.40. The tool cuts a pause only up to 0.12 because your
  earlier fixes **kept** pauses like these: laptop013's 1.4 s music beat (0.23) and a 0.44 s one (0.40), mike005's
  2.3 s one (0.21). Loudness alone cannot tell the restaurant's chatter from laptop013's music, so a rule would cut
  pauses you kept.
* **The rest of video7's framing**: the profile shot (+173 px) -- the face detection finds his face inside the window
  (only the back of his head is cut), so rule 2 leaves it; one shot where you centred a face that was inside but off
  centre (-168 px); the wide shot you zoomed to 135 %. Your other fixes keep the competitor's framing where a face is
  inside (laptop010: 12 of 13 clips), so "centre every face" would undo those.
* **video8's and video9's framing** shows the word on screen that is being said -- the PETROL can while he spells it,
  the pan over DYNAMITE. The tool does not read text in the picture; two examples, both different.
* **"P / E / T / R / O / L"**: one example, and the speech model hears the whole spelling as one word, so each letter's
  time would be a guess.
* **Caption groups and capitals** ("Hey woman" / "stop fingering me", "I'm the head" / "of the household", "dad",
  "wow"): the style model learned from your SRTs decides the groups, and your keys end captions with "of" 12 times
  ("one of") -- one video is too little to change it. "wow" and "this will": a caption after a real pause starts with
  a capital (night 5); you had cut those pauses, so in your edit there was none.
* **Word corrections** ("Dynamite", "Lose the candle", "this will", "light them"): learn added two to the glossary
  (`This'll -> this will`, `ah There's -> ahh Lose`); "Bean on meat cake" -> "Dynamite" is a rewrite (learned.json).

### The three new test cases

`tests\real\video7-fixed`, `video8-fixed`, `video9-fixed` (made with `learn --picture-key`: the competitor, the part
of the RAW your edit plays plus 30 s around it, your captions and your picture clips -- now with your framing -- as
the answer key; `"style": "new"`: check-all runs them thorough and scores them).

## 2. Picture not found, but its sound found (item 2)

### What went wrong on the laptop

The run found 100 % of the competitor's sound in the RAW, so it knew where in the episode the clip was. But the layout
step had taken a tiny box (about 211 x 157 px of the 720 x 1280 competitor) as the picture area, so the picture search
looked only inside that box: 6 anchors on 6 of 368 frames, 0 % of the picture. Nothing stopped it -- night 6's stop
needs the picture AND the sound to be missing -- so it delivered an edit with no clip that looked like a normal run.

### What the tool does now

**1. When the sound is found but the picture is (almost) not, it searches the picture again** -- under 25 % of the
competitor's frames found while at least 50 % of its sound is. (In all 22 check-all videos the first search finds
36-100 % of the picture, so this never runs there and costs nothing.) Each try is one more full picture search,
followed by the usual re-measurement of the picture area against the RAW when it finds something:

* **the picture area measured again**: first the biggest moving area of the competitor (all the pixels whose picture
  changes; the largest connected part), then the whole frame. Captions and other overlays stay masked out.
* **a denser search with a wider zoom range**, on the best picture area so far: every competitor frame is searched
  (`--fast` searches every 3rd), every RAW frame is indexed -- but only where the sound says the clip is (plus a few
  seconds each side), so it stays quick even on a long RAW -- with 2000 keypoints a frame instead of 500 (a small or
  heavily zoomed picture keeps enough of them) and twice as many candidates per frame.
* **mirrored**: every search already compares each frame the other way round too (left-right flipped), so a mirrored
  competitor needs no extra try. The made-up mirrored test below is found 100 % by the first search.

The try that finds the most frames is used (it stops trying once one finds 80 %). The log says what was tried and what
each found, and when a try found the picture the end summary says so: "the competitor's picture was found only after
searching it again (the biggest moving area: 100 % of its frames; the first search 0 %, its picture area was
probably measured wrong) -- check the framing in Premiere".

**2. If it still finds (almost) nothing, the run stops and says so** -- under 10 % of the picture after every try. No
`1_edit.xml` is written. The end of the run's output:

```
match_cuts: PICTURE NOT FOUND (sound found) -- the RAW may be a different version (for example credits squeezed at 
the end), mirrored, or heavily zoomed. 100 % of the competitor's sound is in this RAW (RAW 40.5-49.5 s of 86 s), but 
only 0 % of its frames were found, even after searching the picture again (the first search 0 %; the whole frame 0 %; 
a denser search with a wider zoom range 0 %; every search also tried the mirrored picture). No edit was made: without 
the picture it would hold no clip but look like a normal one. Check that the RAW is the same version as the 
competitor's -- the same frame and cut, not one with the credits squeezed into a corner or another aspect ratio 
(competitor.mp4 / raw.mp4); --force-inputs makes the edit anyway.
```

The batch prints it under that video (`FAIL in ...: the run stopped (exit 2): PICTURE NOT FOUND (sound found) --
the RAW may be ...`) and `batch_summary.md` lists it as the reason. With `--force-inputs` the run goes on anyway,
with that message as its first warning. Between 10 and 25 % found, the edit is made with a warning that most of it is
missing.

**3. An empty edit is never "normal"**: a new hard check, **XML EMPTY** -- `1_edit.xml` with no clip on V1 fails the
run, and in the batch it is FAIL, never "OK (with a note)".

### Tested on made-up competitors (the laptop's files are not here)

Each one made from 10 s of your video7's RAW (real footage, 23.976 fps) and run like your batch (`--premiere
--fast`, your template), on tonight's final code, with `--check-determinism`:

| made-up competitor | the first search | after searching again | the run |
|---|---|---|---|
| **mirrored** (the picture flipped left-right) | 100 % | not needed | normal edit, 3 clips |
| **zoomed 2.4x** (a 304x540 crop blown up to fill the screen) | 100 % | not needed | normal edit (\*) |
| **zoomed 6.75x** (a 160x284 crop) | 100 % | not needed | normal edit |
| **squeezed credits** (the RAW's picture at 40 % in a corner from 35 s on; the competitor full size) | 100 % | not needed | normal edit, 1 clip |
| **a tiny wrong picture area on the picture** (211x157, forced) | 100 % (the area re-measured against the RAW, as before) | not needed | normal edit |
| **a tiny wrong picture area on the black canvas** (211x157, forced -- like your laptop's run) | **0 %** (0 anchors) | **100 %** with the biggest moving area | normal edit, 3 clips; "found only after searching it again" in the summary |
| **another film's picture over the RAW's sound** | 0 % | 0 % (the whole frame; the denser search) | **stopped: PICTURE NOT FOUND (sound found)**, no `1_edit.xml` |
| the same with `--force-inputs` | 0 % | 0 % | an edit with no clip -- **FAIL** (XML EMPTY), the message as its first warning |

Every run that finished passed its determinism check (the cut list made again from the caches is byte-identical --
also after a search again). (\*) The 2.4x-zoomed one fails one check of the 60 fps plan ("XML A1 S01: range 0-88 is
not a V1 cut range") -- night 7's code does exactly the same on it, so it is old, not from tonight; the delivered
`1_edit.xml` passes its own checks. Left for next time (section 7).

So the search itself was already strong -- a mirrored, a 6.75x zoomed or a squeezed picture is found by the first
search. What breaks it is what happened on your laptop: a wrong picture area on a part of the screen with nothing to
match. That is now found again in about half a minute. And a picture that is really not in the RAW (another film's
picture over the RAW's sound) stops with the message instead of an empty edit.

## 3. Test results (item 3)

### Unit tests

On the final code, the whole suite with the GPU hidden: **1318 passed, none failed** (97 skipped: they need the GPU
or are the slow ones; 9.5 min). With the GPU and nothing else running: the GPU's own test files (`test_gpu`,
`test_fullres`, `test_asr`, `test_caption_recheck`, `test_lazy_frames`, `test_quality`, `test_visual_match`) **97
passed**, and the slow end-to-end tests (`--runslow`) **23 passed** -- the 21 on synthetic videos and tonight's two on
real footage (the other 63 are for other test profiles, as always; 15 min).

25 tests are new, in `tests\test_night8.py`: when the picture is searched again (and when not: video020's 36 %, a
missing sound), the biggest moving area, the picture areas tried (not the one already used), the dense search's RAW
index only where the sound was found, the best try used with the run's own settings kept, nothing better found
keeps the first search, PICTURE NOT FOUND (its message, a NotInRaw stop, the batch's line), `--force-inputs`, the
warning between 10 and 25 %, the "found only after searching again" note, XML EMPTY and the batch's FAIL for it, the
cartoon on twos (no shot change) with its real cut (found), a cut from a still shot to a moving one (still found),
the framing score, learn writing your framing, check-all's framing column, the framing rule (onto the speaker when
both people are cut; never past the competitor's picture; not when someone is shown whole, not for three faces, not
for one crop like video6, not a far jump), and two slow ones on real footage (a tiny wrong picture area found again
with a normal edit; another film's picture stopping with PICTURE NOT FOUND and no `1_edit.xml`). One older test
changed: night 6's list of new-style test cases (+ video7/8/9-fixed). The unit suite also caught a bug in my first
version: the framing score would have crashed the batch (it reads each run with a stand-in for a test case) -- fixed
before anything else ran on it.

### check-all (the final code, all 22 videos)

The final code (frozen as `work\snap8e`), the same split as nights 6 and 7 (new-style cases thorough and scored
against your fixes, old ones `--fast` with the hard checks only), two streams, 09:22-10:10 (48 min). The "+/-"
columns are against night 7's final check-all (for video7/8/9: the same night-7 code, run here tonight). Scorecard:
`work\t23\runB\final_scorecard.json`; run folders `work\t23\runB\G1\runs` and `G2\runs`.

```
video               time  hard checks captions exactly    word errors   cuts reproduced   length         framing
deadpool           2m06s  PASS        45/60 +0            4.0 % +0.0    no cut key        -              -
laptop004-fixed    2m32s  PASS        hard checks only (old style, --fast; 125 %)
laptop009-fixed   12m15s  PASS        18/52 +0            8.3 % +0.0    2/13 +0           +0.8 s         15/16 +0
laptop010-fixed    1m16s  PASS        10/34 +0            7.7 % +0.0    1/9 +0            +1.3 s         11/13 +0
laptop013-fixed    1m46s  PASS        9/22 +0             13.3 % +0.0   1/5 +0            +0.3 s         1/6 +0
mike005-fixed      8m34s  PASS        9/46 +0             9.8 % +0.0    2/13 +0           +4.1 s         0/14 +0
spiderman-school   3m33s  PASS        hard checks only (old style, --fast)
video017-fixed     2m01s  PASS        hard checks only (old style, --fast)
video018-fixed     5m50s  PASS        hard checks only (old style, --fast)
video020-fixed     3m06s  FAIL        hard checks only (old style, --fast)
                           -- XML FLASH S10+S12+S13+S14+S15+S16 at 00:00:14:13: 1 frame(s)
video021-fixed    18m59s  PASS        21/40 +0            13.5 % +0.0   0/15 +0           +7.0 s         8/21 +0
video1             6m14s  PASS        hard checks only (old style, --fast)
video2             1m28s  PASS        hard checks only (old style, --fast)
video3             1m59s  PASS        hard checks only (old style, --fast)
video4             5m46s  PASS        hard checks only (old style, --fast)
video5               47s  PASS        hard checks only (old style, --fast)
video6-fixed       5m21s  FAIL        18/32 +0            10.0 % +0.0   5/7 +0            +0.5 s         9/9 +0
                           -- XML FLASH S08+S09+S10+S11 at 00:00:21:00: 1 frame(s) of a di
video7-fixed       1m20s  PASS        1/36 +0             9.3 % +0.0    0/1 +0            +4.2 s         5/10 +2
video8-fixed       1m01s  PASS        1/16 +1             9.1 % -4.6    1/4 +0            +0.3 s         4/11 +0
video9-fixed       3m27s  PASS        0/18 +0             44.4 % +0.0   3/4 +0            +0.5 s         4/4 +0
zendaya            1m41s  PASS        no caption key      -             no cut key        -              -
zendaya-age        2m04s  PASS        hard checks only (old style, --fast)
all caption keys                      132/356 (37 %)      10.5 %
all cut keys                                                            15/71
all framing keys                                                                                         57/104
```

**Hard checks: 20 of 22 pass.** The two that do not are the known ones from night 7: video020 `--fast` (its `--fast`
analysis takes 12.6 s of the RAW for another video) and video6 (its last frame is the competitor's own frame of the
next shot -- you kept it). **video8 now passes** (night 7's code: FAIL, the 6 false flash frames).

**Against night 7, nothing got worse**: 20 of the 22 videos give exactly the same edit, the same delivered
`1_edit.xml` and the same captions, byte for byte (`work\t23\compare_runs.py`). The two that changed are the two the
new rules are for:
* **video7**: only the first clip's framing (+289 px onto the man) -- framing 3 -> **5 of your 10 clips**;
* **video8**: 5 clips instead of 26, no flash, one more pause cut (part of the one after "huh") -- word errors
  13.6 -> **9.1 %**, 1 caption exactly as yours (was 0: "huh" is now its own caption, as yours).

**Framing over all your fixes: 57 of 104 clips** as you framed them (night 7's code: 55).

(A first full check-all of the night, `work\t23\runA`, on the code before the last framing changes, showed the
framing rule moving two of video6's clips and two of video021's against your framing -- the reason for the one-crop
and the "slide, never jump" limits in section 1.)

### Full runs: your three videos at full size, as on your laptop

`batch` (so `--fast`, 100 %, your template) on a folder with your video7, video8 and video9 -- the laptop's own
competitors and full RAWs (14 min, 6.7 min and 81 min), from the laptop's run folders -- on the final code, with
nothing else running, 10:10-10:25:

```
[video 1 of 3] video7 -> work\t23\batch_snap8e\out\video7 (speed 100 %) -- whole batch: about 56 min left
    PASS in 3m50s
[video 2 of 3] video8 -> work\t23\batch_snap8e\out\video8 (speed 100 %) -- whole batch: about 18 min left
    PASS in 2m41s
[video 3 of 3] video9 -> work\t23\batch_snap8e\out\video9 (speed 100 %) -- whole batch: about 19 min left
    PASS in 8m37s
    captions: 1 doubtful line(s), written as they are -- check them:
      00:00:00,050 'Candles light em': rule 5 (real words)
match_cuts batch: 3 of 3 passed in 15m08s; summary: work\t23\batch_snap8e\out\batch_summary.md
```

Against the laptop's runs of the same files (night 7's code) and your fixes:

| | the laptop (night 7) | tonight | your fix |
|---|---|---|---|
| video7 | 12 clips; framing 5 of your 10 clips | 12 clips, the same except the first: +290 px onto the man; framing **7 of 10** | |
| video8 | **FAIL**: 25 clips, 6 false flash frames | **PASS**: 5 clips, every check OK (one more pause cut: part of the one after "huh") | 12 clips |
| video9 | 4 clips, framing 4 of 4 | the same | |

(The captions differ from the laptop's by a few words and marks -- "dad?" instead of "Dad?", an "!" here and
there, a third "you" in video7 as in your fix, one more caption at video9's end. That is most likely the speech
model hearing a little differently on another computer (its results vary slightly between graphics cards), not
tonight's code: on this PC, check-all gives the same captions with tonight's code as with night 7's, byte for byte.)

### Made-up competitors (item 2)

The eight made-up runs of section 2, on the final code with `--check-determinism`, 10:26-10:30: every one did
what it should (the table in section 2).

### The PC

No Adobe or CapCut program was open tonight (only Adobe's background services); the final check-all, the batch and
the made-up runs each ran with nothing else on the GPU, so their times are free-PC times. (The first check-all of the
night shared the GPU for a few minutes with two short tests.) The unit suite ran on the CPU just before the final
check-all.

## 4. On the laptop

Everything is committed and pushed to the branch `claude/new-session-ol4era`. On the laptop, in PowerShell or cmd:

```
cd C:\Users\gvida\Desktop\bh\MovieRecaps
git status
git pull
```

* `git status` first: if it lists files under `tools\match_cuts\` as modified (you changed something there -- the
  censor list `caption_censor.txt`, say), keep a copy of them, then `git stash` before `git pull` and `git stash pop`
  after. Your `input\`, `output\` and `finished\` folders are never touched by `git pull`.
* If `git status` says you are on another branch: `git checkout claude/new-session-ol4era`, then `git pull`.
* Nothing new to install: no new Python packages, no new models.
* Then run the batch as before, e.g. `.venv\Scripts\python -m match_cuts batch input\videos --out output`.
* The video whose picture was not found: run it again. If the tool now finds the picture, the end summary says
  "the competitor's picture was found only after searching it again (...)" -- check the framing in Premiere. If it
  says **PICTURE NOT FOUND (sound found)**, the RAW is most likely another version of the episode: try another copy
  of it (the one the competitor used: same aspect ratio, credits not squeezed).
* `git pull` brings three new test cases (about 160 MB of video in `tests\real\video7-fixed`, `video8-fixed`,
  `video9-fixed`); that is normal.

## 5. What I decided on my own

1. **Which runs your fixes belong to**: the media paths in your projects (`D:\Shared\002`, `video1-3`, `video2-3`)
   name the laptop's run folders, which are here as `output\laptop_video7/8/9`. I relinked copies of your projects
   to them (`*_fixed_relinked.prproj`); your originals are untouched.
2. **Two new rules** (the cartoon shot changes, the person the narrow window cuts), each checked against your
   earlier fixes first -- and several things **not** made into rules (section 1), because one example is too little
   or your earlier fixes did the opposite.
3. **How far the framing rule goes**: only for a competitor that frames each shot (on your video6 fix, a competitor
   showing the whole video through one crop, a first version moved two clips you had kept); only when nobody is
   shown whole; the window slides just far enough to show the person, never past the competitor's own picture
   (yours went exactly to its edge), and never more than half its width (a first version jumped two of video021's
   clips against your framing). It is a constant you can switch off: `SHOW_CUT_PERSON` in `export_xml_edl.py`.
4. **A framing score in check-all**, so a framing rule can be measured on all your fixes. Your earlier fixes' answer
   keys got your framing (read from the same projects); nothing else in them changed.
5. **The two glossary lines learn added** ("This'll -> this will", "ah There's -> ahh Lose") stay: they are what
   learn does with your corrections. Delete them from `tools\match_cuts\caption_glossary.txt` if you prefer.
6. **Item 2's thresholds**: the picture is searched again under 25 % found (with at least 50 % of the sound found);
   the run stops under 10 % after the searches. In all 22 check-all videos the first search finds 36-100 % of the
   picture, so nothing changes for them.
7. **Stop rather than deliver** when the picture is not found: no `1_edit.xml` at all (an empty edit would look like
   a normal one). `--force-inputs` still makes it, with the message on top. And a new hard check, **XML EMPTY**:
   an edit with no clip is a FAIL in every case -- also in the batch, where it is never "OK (with a note)".
8. **Mirrored**: no extra pass -- every search already compares each frame flipped too (your made-up mirrored test
   is found 100 % by the first search).
9. **Made-up tests from real footage** (video7's RAW window, since the laptop's files are not here): mirrored,
   zoomed 2.4x and 6.75x, credits squeezed into a corner, a tiny wrong box inside and outside the picture, another
   film's picture over the RAW's sound.

## 6. Check by hand

1. **video7 in Premiere** (`work\t23\batch_snap8e\out\video7\1_edit.xml`, the batch run of your video7 at full
   size): the first clip ("Hey woman, stop fingering me") shows the man whole, the window at the left edge of the
   competitor's picture -- as you framed it. The other clips: the competitor's framing, as before.
2. **video8 in Premiere** (`work\t23\batch_snap8e\out\video8\1_edit.xml`): 5 clips -- your one take with three
   short pause cuts -- and no flash frame; the batch says PASS.
3. **The picture searched again**: `work\t23\madeup\out_snap8e\tinyout\001` -- the made-up competitor whose picture
   area was forced to a tiny box on the black canvas (as your laptop's run picked): the log says "picture search again
   ... used the biggest moving area -- 100 %", the edit is normal.
4. **PICTURE NOT FOUND**: `work\t23\madeup\out_snap8e\otherpic.console.txt` -- the message as you will see it.

Everything tonight's runs left in `work\t23\` (the check-alls, the made-up cases, the batch) and the frozen code copies
`work\snap8a` ... `work\snap8e` can be deleted once you have looked: none of it is needed by the tool (`work\cache`,
the stage caches, I left alone).

## 7. Left for next time

1. **Pauses with a sound in them** (restaurant chatter, a sound effect, music): you cut four tonight, your earlier
   fixes kept others of the same loudness. Telling room noise from music (by its sound, not its loudness) would
   allow a rule -- tell me if you want me to try.
2. **More of video7's framing**: showing a whole head (not only the face), centring a face that is inside but off
   centre, zooming in on a wide shot. Each conflicts with your earlier fixes as a plain rule.
3. **Framing on words in the picture** (PETROL, DYNAMITE): would need reading the picture's text.
4. **Spelled-out words, one letter per caption**: needs each letter's time; one example so far.
5. **`--fast` and thorough runs hold framings differently** across the RAW's own shot changes (video7: the laptop's
   `--fast` run kept +97 px over four shots, the thorough check-all run -74 px), so check-all's framing score of
   video7 is measured on a slightly different edit than the one you fixed (7 of 10 on the `--fast` batch run).
6. **An old check quirk** found by a made-up test (a 2.4x-zoomed competitor): the 60 fps plan's A1 piece does not
   match V1's cut where a 4-frame piece was joined ("XML A1 S01: range 0-88 is not a V1 cut range") -- night 7's code
   does the same; the delivered `1_edit.xml` passes its own checks, but the run is marked "exports NOT validated".
