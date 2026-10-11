# Night 4: your template as the default frame, audio always at 0 dB, the competitor's cuts kept (11 October)

## In short

- **Audio at 0 dB, always** (section 1): the laptop's 125 % A1 imported muted because of tiny fade keyframes the
  XML put on every A1 clip; the XML now has no level effect or keyframe on any audio clip, only its speed, and a new
  hard check (`XML AUDIO`) fails a run that would.
- **The competitor's cut points by default** (section 1): no silence removal, no cut moved off speech, the ending
  never shortened -- the edit ends on the competitor's last RAW moment (new hard check `XML ENDING`).
  `--remove-silence` brings the old behaviour back when you want it.
- **Your template is the default frame** (section 2): `templates\default.png`, committed, used on every
  `--premiere` run (also `batch`); `--frame <png>` for another, `--no-frame` for none. The video always fills the
  box (`XML GAP`), plus 2 px.
- **Learned from mike005** (section 3): your edit ends on the competitor's last RAW moment -- a closing meme not
  from the RAW is now cut off; the captions now hear the RAW sound under a clip that plays on over a meme (they had
  copied the meme's words); your caption track has Premiere's default style, so nothing to learn there; your 62 %
  zoom and your pause cuts stay yours (sections 3 and 6). New test case `mike005-fixed`.
- **Tests** (section 5): check-all on all 15 test videos (mike005, 021 and laptop004 included): **every hard check
  passes on all 15**. Nothing got worse from tonight's changes; video3's caption word errors fell from 42 % to 7 %
  (the caption fix), video020-fixed passes for the first time (a false flash fixed tonight), deadpool's word errors
  8.0 % -> 4.8 %. Unit tests: 1219 passed, 0 failed.
- **Disk** (section 7): 37.2 GiB freed in `work\` (old test runs, check-all run folders, frozen code copies, temp
  files, pip's download cache); 107.5 GiB free on the drive now. Nothing tracked by git, nothing outside `work\`, and
  no cache or video of yours was touched.

## 1. The rules for every run

### Audio: every clip at 0 dB

**Why the laptop's 125 % A1 imported muted:** with silence removal on (the old default) every A1 clip next to a
removed silence got two "Audio Levels" keyframes in `1_edit.xml` -- a one-frame fade to 0 (= -inf dB) so the cut
could not click. All 10 A1 clips of `output\005` had such keyframes. On a sped-up clip Premiere evidently reads the
keyframe times on another scale than the tool wrote them (I cannot open Premiere here to see which), so the fade to
-inf lands inside the clip and the clip stays at -inf from there on: A1 sounds muted.

**Now:** no A1 clip has an Audio Levels effect, a keyframe, a gain, a pitch or any other effect -- only its speed
(Premiere's Speed / Duration, which the XML states as "Time Remap"). Every audio clip imports at 0 dB, switched on;
the A1 track is never switched off or locked. A new hard check, `XML AUDIO`, reads the final `1_edit.xml` and fails
the run if any A1 clip has anything else. Your projects' -inf levels, pitch effects and moved audio are never read
or copied (`learn` uses your picture, V1, for the cuts).

### The competitor's cut points and ending (silence removal off by default)

**Now the default:** the tool keeps the competitor's cut points. It no longer moves a cut out of a word, no longer
cuts silences out, and no longer cuts the end after the last word: **the edit ends on the competitor's last RAW
moment** (the reaction after the last line stays). A new hard check, `XML ENDING`, fails the run if the last clip
does not end where the competitor's last RAW clip ends (within a RAW frame). Cuts inside a word are the competitor's
own -- `report.md` lists them, they do not fail the run.

**`--remove-silence`** turns the old behaviour back on: cuts moved off speech, silences cut out (with `--pad-before`,
`--pad-after`, `--min-silence`, `--silence-db` as before) and the edit ending just after the last word. Still
always on: repeats of the same RAW moment are cut (`--allow-repeats` keeps them), mini clips under 10 frames join
their neighbour, a cut lands on the RAW's own shot change. A run without a competitor (`--raw` only) still cuts its
silences -- that is its whole edit -- unless `--keep-silence`.

### The frame: always filled

With a frame, every clip **fills the frame's hole completely** -- the competitor's framing mapped into the hole,
zoomed and moved only as much as needed to cover it (centred where the competitor centred it, on the person it
shows). The hard check `XML GAP` reads every clip's position and scale in the final XML and fails the run if any
clip leaves any part of the hole uncovered -- so a black strip cannot reach you without a failed run.

## 2. Your template is the default frame

`finished\mike005\template.png` is now **`templates\default.png`** in the repository (committed: `git pull` brings it
to the laptop). It has a transparent rounded box, so the tool finds the box itself: x 22-1058, y 527-1737 on a
1080x1920 picture; the faint "MIKEMEMESYOU" watermark inside the box belongs to the box.

- **Every `--premiere` run uses it** (also `batch`): `1_edit.xml` is a 1080x1920 sequence (the template's own
  size), the template sits on V2 over the whole edit, and every clip fills the box.
- **`--frame <png>`** uses another frame instead (its own size is the sequence size: `input\frame.png` still gives
  2160x3840; `--frame-size` overrides). **`--no-frame`** turns it off: no V2, the competitor's own layout, as before.
- The template's **headline text** ("The fact that every generation hugged him is hilarious") is part of the PNG,
  so every video gets that headline until you change it: give each video its own version with `--frame`, or replace
  `templates\default.png` with a template of the same layout (same size, same box) -- nothing else to change.

**Why your box had a black strip:** the laptop run had no frame, so its clips were sized for the tool's old fixed
window (x 42-1039, y 555-1591, scale 49 %); your box is bigger (down to y 1737), so about 130 px at the bottom of
the box showed black. With the template as the frame the box itself is the window: every clip covers all of it, plus
2 px on each side so Premiere's rounding cannot leave a hairline, and the hard check `XML GAP` fails the run if any
clip leaves any of it uncovered.

## 3. What I learned from your mike005 edit

**Relinked:** your project points at the laptop's `D:\Shared\raw.mp4`, `D:\Shared\competitor.mp4` and
`C:\Users\gvida\Desktop\MikeMems.png`. I wrote a relinked copy, `finished\mike005\mike005_fixed_relinked.prproj`
(the same project, its paths moved to `finished\mike005\raw.mp4`, `competitor.mp4`, `template.png`); your file is
untouched. The laptop run (`output\005`, wrong RAW) was not used for learning.

**The run on the real RAW** (`output\mike005\night4_final_125` on the final code, your template as the default frame,
`--speed 125`; `night4_100` is the same at 100 %): the
competitor has 17 pieces -- 13 of the RAW and 4 memes that are not in it (1.0, 0.7, 1.07 and 1.03 s; the last one
closes the video). Compared with your 14 clips, by the RAW moments they play:

**Cuts.** Your edit is the laptop tool's output with these changes:
1. **The ending:** you deleted the tool's last two clips -- the clip playing on over the competitor's closing meme
   and a speech-safe extension into the next line ("Like your haircut"). You end on RAW 42.19 s; the competitor's
   last RAW frame is 42.36 s. **Learned** (it is also your new rule): a closing piece that is not from the RAW is cut
   off; the edit now ends on RAW 42.30 s (within a RAW frame of the competitor's), and `XML ENDING` checks it.
2. **The three memes in the middle:** the tool lets the clip before play on over each (the competitor's timing
   kept, the RAW's own sound on A1). You kept that where the RAW speaks (RAW 28.38-29.57 s, "okay you got her
   today"), cut it where nobody speaks (RAW 7.85-9.08 s, after "a question? yeah"), and at the third (RAW
   21.23-22.07 s: a pause, then the start of "Chit", which the competitor's meme hides) you cut the pause and kept
   the whole word. In your video3 you kept every such spot. **Not changed** -- see section 6.
3. **Short pieces and a line inside the competitor's clips:** you cut 13 short pieces of 0.1-0.6 s (5.3 s in all,
   at 100 %; pauses, breaths, clip starts) and one whole line of 2.2 s (RAW 32.88-35.07 s). The old silence removal
   found only 4 of those pauses. The new default keeps the competitor's cut points, as you asked, so these stay
   yours to cut by hand (`--remove-silence` cuts the pauses it finds, never a line).

**Size and position in the template.** You gave all 14 clips one framing: Scale 62 %, Position 540.6, 1085.4 -- the
video covers the box with 111 px to spare above it and 18 px below. The tool: 57.5 %, centred 60 px lower -- the
competitor's framing mapped into the box, the smallest zoom that fills it (+2 px on each side). Both fill the whole
box: no black strip. Your 8 % more zoom is a taste I could not turn into a rule from one video: with the frame, the
tool matched your zoom within 1 % on laptop004 and within 5 % on 15 of 19 clips of 021 (night 3). Section 6.

**Captions.** Your caption track has Premiere's default style (Arial, 48, the default position) -- the same as in
your 021 and laptop004 projects -- so there was no style or position to learn there (`restyle` gives the house
style). The text: 86 of your 92 words are the tool's now (83 before tonight). One real fix came out of it: under
the clip that plays on over the third meme, A1 plays the RAW's sound but the captions were made from an audio
where that stretch was silent, so they copied the meme's "hey" / "oh" -- you wrote "okay you got her today". The
captions now hear what A1 plays. What is left: you wrote "Chit" (the name; the tool hears "shit" / "chip"), you
add "?" to questions, and you join some single words into 2-4-word phrases. Across your other edits 17-37 % of the
captions are single words, so I did not change the grouping rule.

**Speed:** your edit is at 125 %; not learned (the tool's speed is only `--speed`). The test case runs at 125 %
because your answer key is at 125 %.

**Test case `mike005-fixed`** (`tests\real\mike005-fixed`: the competitor, an 85 MB copy of the 4K RAW, your 46
captions and your 14 clips as the answer key; `--speed 125`; the default template and cut rules).

## 4. Example commands

From the `MovieRecaps` folder (the inputs are `input\competitor.mp4` and `input\raw.mp4` unless you give
`--competitor` / `--raw`):

```
.venv\Scripts\python -m match_cuts --premiere                                   # 100 %, your template (default)
.venv\Scripts\python -m match_cuts --premiere --speed 125                       # the 125 % channel, your template
.venv\Scripts\python -m match_cuts --premiere --frame input\frame.png           # another frame (2160x3840 sequence)
.venv\Scripts\python -m match_cuts --premiere --frame input\frame.png --speed 125
.venv\Scripts\python -m match_cuts --premiere --no-frame                        # no frame: the competitor's layout
.venv\Scripts\python -m match_cuts --premiere --remove-silence                  # the old silence removal, on request
.venv\Scripts\python -m match_cuts batch "D:\to do"                             # every video of a folder: your
                                                                                #   template, a speed.txt for 125 %
```

The first line of `report.md` and of the end summary says the speed and the frame (`frame: frame.png on V2,
1080x1920 sequence`).

## 5. Test results

### Unit tests

1219 passed, 94 skipped (files or tools not on this PC), 0 failed -- on the final code. 13 new tonight
(`tests\test_night4.py`: a short shot of the RAW's own played straight through is no flash while a cut into it still
is, every A1 clip at 0 dB with no keyframe or effect at 100 % and 125 %, the audio check
catching a level / keyframe / switched-off clip, silence removal off by default and on with `--remove-silence`
(RAW-only runs still cut), the ending check, the default run's checks, the template in the repository with its box,
the default / `--frame` / `--no-frame` choice, `--frame` with `--no-frame` refused, the sequence size from the PNG,
every clip filling the template's box). Four older tests changed with the rules: the two that test the speech-safe
cuts and the silence removal now run with `--remove-silence`, the silence test expects no fade keyframes, and the
frame windows include the 2 px.

### mike005 on the final code

`output\mike005\night4_final_125` (125 %) and `night4_100` (100 %): every Premiere hard check passes (9.8), 13 of 13
clips linked to their audio, the person speaking in the picture in all 11 clips where someone speaks, A1 without any
level effect or keyframe, the template on V2, the box filled with 2 px to spare, the edit ending on RAW 42.30 s.
A `--fast --no-frame` run (`night4_noframe_fast`) passes too: 1080x1920, nothing on V2.

### check-all: the 15 test videos

All 15 videos, thorough, on the final code (`work\t19\final\scorecard.json`; the runs in `work\t19\checkall`, video020
in `work\t19\checkall_020`; 4 h 51 min in all). "Captions": your captions reproduced exactly / word errors. "Cuts":
your cut points reproduced within 2 frames / how much longer the tool's edit is than yours.

| video | hard checks | captions | cuts | before tonight (hard checks, captions, cuts) |
|---|---|---|---|---|
| zendaya | PASS | no key | no key | PASS |
| zendaya-age (`--remove-silence`) | PASS | 1/19, 32.5 % | 1/9, +2.9 s | PASS, 2/19, 22.5 %, 1/9, +3.8 s (night 2) |
| deadpool | PASS | 45/60, 4.8 % | no key | PASS, 45/60, 8.0 % |
| video2 (`--remove-silence`) | PASS | 0/21, 4.8 % | 1/1, -0.8 s | the same |
| video3 (`--remove-silence`) | PASS | 11/71, 7.0 % | 3/11, +5.0 s | PASS, 7/71, 42.4 %, 3/11, +5.0 s |
| video5 (`--remove-silence`) | PASS | 1/37, 35.4 % | 2/14, +5.0 s | PASS, 2/37, 32.9 %, 1/14, +3.7 s (night 2) |
| video017-fixed (`--remove-silence`) | PASS | 23/43, 11.0 % | 4/12, +8.5 s | the same |
| **mike005-fixed** (new, 125 %) | PASS | 9/46, 7.6 % | 0/13, +7.2 s | -- |
| laptop004-fixed (`--remove-silence`, 125 %, frame.png) | PASS | 9/27, 16.1 % | 0/3, -0.2 s | the same |
| spiderman-school (`--remove-silence`) | PASS | 9/64, 8.8 % | 4/21, +6.4 s | the same |
| video4 (`--remove-silence`) | PASS | 7/38, 14.9 % | 2/7, -1.1 s | night 3's run stopped early; night 2: PASS, 8/38, 16.1 %, 2/7, -1.1 s |
| video018-fixed (`--remove-silence`) | PASS | 12/49, 15.7 % | 0/15, +4.5 s | the same |
| video020-fixed (`--remove-silence`) | PASS | 1/30, 35.9 % | 0/11, -5.6 s | **FAIL** (`XML FLASH`), 1/30, 46.2 %, 0/11, -5.6 s |
| video1 (`--remove-silence`) | PASS | 18/111, 10.8 % | 8/29, +3.2 s | **FAIL** (the hold rule fixed late on night 3), 19/111, 10.8 %, 8/29, +3.2 s |
| video021-fixed (`--remove-silence`, frame.png) | PASS | 19/40, 14.5 % | 0/15, +7.9 s | **FAIL** (a 1-frame flash fixed late on night 3), the same scores |

All caption keys together: 165 of 656 captions exactly yours, 13.0 % word errors (night 3's newest runs, without
mike005 and video4: 148 of 572, 18.2 %).

"Before tonight" is each video's newest run before tonight (night 3, or night 2 where night 3 did not run it),
scored the same way as tonight's. Every hard check passes on all 15 (Premiere XML incl. the new `XML AUDIO` and
`XML ENDING`, determinism, coverage). What changed:

- **Better:** video3's captions (word errors 42 % -> 7 %, 11 exact instead of 7: it has long spots where the clip
  plays on over a cutaway, and its captions used to be made from silence there -- the mike005 fix), video020-fixed
  (it failed `XML FLASH` on nights 2 and 3; now passes, word errors 46 % -> 36 %), deadpool's word errors
  (8.0 % -> 4.8 %), video1 (its night-3 run failed the hold rule fixed late that night; now passes, the same scores).
- **The same:** laptop004-fixed, spiderman-school, video017-fixed, video018-fixed, video2, zendaya; video4 matches
  its night-2 run (night 3's run had stopped early) with fewer word errors (16.1 % -> 14.9 %) and one caption split
  differently; video1 has two captions split differently, the same words.
- **New:** mike005-fixed (your new edit): the competitor's cut points against your hand trims, as expected -- 0 of
  your 13 cuts within 2 frames, 7.2 s longer than yours; captions 9 of 46 exactly yours, 7.6 % word errors.

**video020-fixed's flash** (fixed tonight): 5 frames inside one continuous take -- the RAW itself has two shot
changes two RAW frames apart (511.18 s), and the scene-cut split made them a 5-frame piece. A short shot of the
RAW's own that the edit plays straight through is now judged with its neighbours (a cut into it still fails); a unit
test covers both. video020-fixed was re-run on that code (`work\t19\checkall_020`); the other videos' verdicts
cannot change from it except to pass.

Two videos had not been run since night 2
(zendaya-age, video5) and differ from that run: zendaya-age is 0.9 s shorter (closer to your length) with weaker
captions (1 of 19 exact instead of 2), video5 1.3 s longer with one more of your cuts reproduced. **Neither comes
from tonight's changes:** night 3's exact code, run on both, gives the identical edits and captions. It is night 3's
100 % rule: zendaya-age's two slow-motion pieces (the competitor shows 0.03 s of the RAW for 0.4 s each) become
2-frame clips at 100 % and join the clip before.

## 6. Check by hand

1. **A1 in Premiere at 125 %** (I cannot run Premiere here): import `output\mike005\night4_final_125\1_edit.xml` (or run
   a `--speed 125` video on the laptop) and check that A1 plays, every clip at 0 dB (no rubber-band keyframes, the
   clip volume 0.0 dB in Effect Controls). The XML has no level effect or keyframe at all now, so nothing can import
   at -inf -- but this is the one thing only Premiere can confirm.
2. **mike005 in your template:** `output\mike005\night4_final_125` (125 %) and `output\mike005\night4_100`
   (100 %). Check the box is filled everywhere, the size (57.5 % against your 62 %), and the end: it stops on the
   competitor's last RAW frame, the closing meme cut. (`night4_125` is the run I learned from, a few minutes older:
   the box without the 2 px margin.)
3. **The headline in the default template** is mike005's ("The fact that every generation hugged him is
   hilarious"): every run shows it until you give a video its own template with `--frame`, or put a template with the
   headline you want (same size, same box) in `templates\default.png`.
4. **Decide -- the memes in the middle:** the tool lets the clip before play on over a meme the competitor inserts
   (the competitor's timing, the RAW's own sound). On mike005 you kept that where the RAW speaks and cut it where
   nobody speaks; on video3 you kept it everywhere. If you want "a meme over silence is cut", tell me and I make it
   the rule (it shortens the edit against the competitor's timing).
5. **Decide -- zoom in the template:** on mike005 you zoomed 8 % more than the smallest zoom that fills the box (the
   tool's); on laptop004 the tool was within 1 % of your zoom and on 021 within 5 % on 15 of 19 clips. If you want
   the clips always a little bigger than needed, tell me how much (an option for it is quick to add).
6. **The test videos with `--remove-silence`:** 12 of the 15 test cases are answer keys from edits with the
   silences removed (the old default), so check-all runs them with `--remove-silence` and compares like with like.
   Your next fixed edits, made from the new default, will test the competitor's cut points the same way mike005 does.

## 7. Disk space

**Freed: 37.2 GiB.** Measured just before and just after the cleanup, with every file counted once however many
hard links it has (Explorer counts every link, so it shows more -- 199.0 GiB for this folder after the cleanup):

| | before | after |
|---|---|---|
| `MovieRecaps`, really used | 206.0 GiB (126,221 files) | **168.8 GiB** (70,248 files) |
| of it `work\` | 153.4 GiB | 116.1 GiB |
| free on the drive | 70.2 GiB | **107.5 GiB** |

The files deleted add up to 44.1 GiB, but 6.9 GiB of that were hard links of files that stay elsewhere (for
example a run's copy of a RAW that is the same file as one in `output\`): deleting one link of a file frees nothing,
so the drive gained 37.3 GiB.

**Deleted** (all inside `work\`, none of it tracked by git): the old test runs and check-all run folders of nights
1-3 (`work\t5*` ... `work\t18`, `work\check-all`, `work\check-all-before`, `work\check-all-fresh`, `work\runs`,
`work\t10\check-all\runs` and the other `work\t10` run folders), the frozen code copies (`work\frozen`), temporary
files (`work\_tmp`) and pip's download cache (`work\_cache\pip`; the packages stay installed). `work\t8\final4`,
`work\t8\user` and `work\t10\learn` stay: the test cases' notes (`case.json`) name them.

**Kept:** `tests\`, `finished\`, `input\`, `models\`, `.venv\`, every `output\` folder, `reference\`, and the stage
caches (`work\cache`, `work\t10\check-all\work`): a cache entry is only reused when the code that made it is the
same, and that cannot be told from file dates (code is changed before it is committed: I checked one 3 GB entry that
looks stale by date and is still in use) -- so I kept them all; without them the next check-all would compute
everything again (many hours). Tonight's check-all runs (`work\t19\checkall`) stay for the next comparison.
