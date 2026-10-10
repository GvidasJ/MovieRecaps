# Night 3: learning from your 021 and laptop004 fixes, --speed, --frame (10 October)

## Where your two projects were

You wrote `finished\021_fixed.prproj` and `finished\laptop004_fixed.prproj`; they are in `output\021\021_fixed.prproj`
and `output\laptop004\laptop004_fixed.prproj` (`finished\` only holds video1-5). I used those two and changed neither.
laptop004 points at `D:\Shared\004\...`; I gave `learn` the run folder (`--run output\laptop004`) so it reads that
run's own `extras\media\` files (see 1.3: it would otherwise have taken 021's RAW).

## In short

- **Learned from both edits** (section 1): your re-framing is the frame's hole (now `--frame`); the 00:00:02:02 flash
  was a 0.5 s reaction shot trimmed to 5 frames (short action-captioned beats are now kept whole); three of the four
  CHECK BY HAND moves were cuts inside applause / "ewww" that the tool treated as words (long wordless sounds are now
  noise: the competitor's cut stays -- your picture agrees at S44 and S50, not at S11; S32 you kept); the captions
  lost the competitor's correct words where the transcript missed them (now kept), and laptop004's animated
  captions became fragments (now cleaned). Your finished edits of both mirrored competitors are unmirrored, so the
  edit is now made unmirrored (`--mirror` keeps the flip); the competitor's framing now wins over the speech
  detection's "speaker" (you framed 3 of 5 such clips back; `--follow-speaker` keeps the old rule).
- **`learn` itself** picked the wrong sequence in 021's project and would have taken 021's RAW for laptop004 -- both
  fixed; the cuts can come from your picture (`--picture-key`), and a long RAW goes into the test case as a sharp
  window. Both edits are test cases now (`video021-fixed`, `laptop004-fixed`).
- **`--speed`** (section 2): 100 % is the default; `--speed 125` makes the edit at 100 % and then plays all of it at
  125 % in Premiere -- clips, cuts, captions; batch `speed.txt`; the speed is the first line of the report.
- **`--frame`** (section 3): the PNG's transparent hole is found, the sequence is 2160x3840, every clip covers the
  hole, the PNG sits on V2, captions are checked against the hole, and preview_recreation.mp4 shows the edit through
  the frame.
- **Tests** (section 4): 021 with your frame (`output\027`) passes every Premiere check; inside the hole 14 of 19
  clips are within 5 % of your zoom (none before), the 00:00:02:02 flash and the S44 / S50 moves are gone, nothing
  is moved onto "the speaker". laptop004 at 125 % on its full RAW (`output\laptop004\night3`) passes, every clip
  within 1 % of your zoom, 0.1 s from your length, caption word errors 52.6 % -> 12.5 %. 020 at 100 % and 125 %
  both pass and play the same RAW moments (the old 629 s bug stays fixed). Unit tests: 1197 passed, 0 failed.
  check-all on all 14 test videos: section 4.
- **Decide** (section 5): the mirror, the pauses on the 125 % channel, and `--follow-speaker` (the competitor's
  framing is now kept even where the speaker is elsewhere -- this changes the Zendaya interview).

## 1. What I learned from your two edits

I compared every V1 clip of your projects with the tool's `1_edit.xml` (by the RAW they play, not by time), every
framing (what part of the RAW each clip shows **inside your frame's hole**) and every caption. As you asked, your
audio clips, their positions and the pitch effect were ignored: the cuts come from your picture (V1).

### 1.1 021 (normal channel, 100 %)

Your edit: 21 picture clips, 29.7 s. The tool's: 32 clips, 36.1 s. The differences, biggest first:

1. **Framing -- every clip.** Your clips are 15-20 % bigger than the tool's (scale 115-125 % instead of 98-110 %,
   close-ups 145-165 %) and sit lower. Measured inside your frame's hole, the tool's old framing showed RAW down to
   y 1170 of 1080 (black at the bottom of the hole) on every clip. That is the frame's hole (1036 x 1210 px at
   1080x1920) against the old template window (998 x 1037): **`--frame` now does this** (section 3). Re-run with
   your frame, what each clip shows inside the hole is within 5 % of your zoom on 14 of the 19 clips both edits
   play (before: none); the other 5 are your close-ups of the reactions, zoomed in further (section 4).
2. **The flash at 00:00:02:02** (5 frames). The competitor shows a 0.5 s reaction shot after "ladies and gentlemen"
   (`*looks over*` on its screen). The speech-safe cuts end a clip 0.05 s after its last word, so the shot was
   trimmed to the 5 frames of quiet after "gentlemen" -- a separate RAW shot that short is a flash (the run's own
   XML FLASH check failed it). You put the whole 0.5 s back. Now: **a beat the competitor shows an action caption
   over for 1 s or less (`*looks over*`, `*high five*`) is never trimmed into**, and the silence removal never
   leaves a piece of a clip shorter than 0.25 s at a cut. Longer action beats follow the usual rules: you cut the
   1.4 s `*disgusted*` (S27) and most of the 2.2 s `*laughing*` (S30) the competitor showed, and so does the tool.
3. **The CHECK BY HAND cut moves.** Three had the same cause: the competitor cut inside a long sound with no word
   in it -- applause and cheering (S44, 3.4 s; S50, the 1.3 s of applause after "this"), the women's "ewww" (S11,
   1.7 s) -- and the tool treated that sound as a word, so the clip played on to its end (S44 +3.08 s, S11 +0.53 s)
   or started at its beginning (S50 -0.60 s). Judged on your picture only (your audio was moved by hand):
   **S44 and S50** -- your picture never plays that applause: after the competitor's S44 you go straight to S45,
   and you start S50 0.8 s after the competitor's cut (the tool had started it 0.6 s before). Now: **a sound of
   0.6 s or more with no word in it is noise -- a cut may land inside it, so the competitor's cut stays**; a word
   whose sound runs on into applause with no quiet between ends 0.12 s after the word. **S11** is the exception:
   your picture keeps exactly the 0.53 s the tool played on (the next shot: the women saying "ewww") and drops the
   competitor's S11 before it -- a swap the rule cannot make; the tool now ends S11 at the competitor's cut, so
   that reaction is not in it (check it, section 5). **S32** (-0.37 s, "this. We've") was two short sounds with
   words: you kept that move, and the tool still makes it.
4. **Captions.** The competitor's `*...*` captions were copied, but the tool replaced several of the competitor's
   correct words with what it heard: "I'm currently" -> "currently", "why are you" -> "are you", "I'm still" ->
   "still", and "children" -> "I'm" -- with the `*disgusted*` after it dropped (its first moment was trimmed out of
   the edit) and "I'm" stretched over its place. Cause: the transcript of the edit's audio skipped "I should get
   away from the children" and heard "I've- I'm" as one word, and the recheck turned a correct "Why" into "are".
   You kept the screen's words every time. Now: **a clearly read competitor caption keeps its words at its start or
   end that the transcript lacks**, a heard word that really opens the next caption ("I'm" of "I'm still") does not
   replace a screen word, **an action caption whose first moment the edit leaves out starts at the first moment of
   it the edit plays** (never dropped while part of it is shown), and "Ishould" (the OCR lost the space) is "I
   should" (also in the glossary now). Your "*grabs the kid*" for the competitor's "*tosses kid*" is a rewrite I
   cannot learn; your "of pigs" for the screen's "of peeing" either.
5. **Re-framing onto "the speaker".** The tool moved 5 clips to show the face its speech detection picked. On 3 you
   framed the competitor's subject instead -- the detected "speaker" was a small face at the edge of a wide pool
   shot, or the second man in the laughing shot; on 2 your bigger zoom shows both. My first fix (re-frame only where
   the competitor's framing shows nobody) was not enough: in the laughing shots (S49, S51, S52) the face detection
   does not find the laughing man at all, so it still moved them 1000-1500 px. Now: **a clip that shows the
   competitor's own framing is never moved onto "the speaker"** (nor one held from the clip before by `--min-move`);
   only a framing the tool borrowed (a B-roll / NOT-IN-RAW spot the RAW replaces, or one that would leave the hole
   uncovered) is re-framed, and only where it shows nobody. The XML PERSON check lists those clips for you to look at instead of failing the run. This undoes part of
   an earlier task of yours ("the person speaking is always in the picture"): on the Zendaya interview the tool moved
   5 stretches (most of the edit) from the listener the competitor showed onto the speaker. **`--follow-speaker`**
   brings that rule back for a video where you want it (section 5).
6. **Cuts I could not turn into rules** (your taste, one video): you removed S17 (0.6 s after "safety"), S21's end
   (1.0 s), the 1.4 s `*disgusted*` (S27) and "no we haven't" (S35), started S50 0.8 s later than the competitor,
   lengthened the ending by 0.9 s (`*dying*`) and used the lounge-chair shot after S11 as a cutaway over the pool
   sound. They are in the new test case's answer key, so check-all measures every future change against them.

### 1.2 laptop004 (125 % channel, made on the laptop with --fast)

Your edit: 7 clips at 125.1 %, 15.6 s; the tool's: 10 clips at 125 %, 16.7 s. The cuts are almost the same (8 of 18
clip edges within 0.05 s). The differences:

1. **Mirror.** The competitor mirrored the picture, so every clip of the tool's XML had a horizontal flip (Flop).
   Your project has **no flip on any clip** -- and neither does your fixed 020, the other mirrored competitor.
   Compared the right way round, your framing shows the same part of the picture as the tool's on 7 of 8 clips
   (within ~70 px). Either Premiere dropped the flip on import or you took it off; both finished edits are unmirrored,
   so **the Premiere edit is now made unmirrored** (each clip shows the same part of the RAW the right way round;
   `--mirror` keeps the competitor's flip). Please confirm (section 5).
2. **One wrong re-frame.** S08 was moved 1420 px sideways onto "the speaker" -- the dad -- while the competitor (and
   you) show Po and the goose: the speech detection does not work on cartoon faces. The new rule (1.1.5) keeps the
   competitor's framing there. The same run's hard checks also failed two "person not in the window" clips (S04, S07):
   the competitor shows someone, so these are now listed, not failures.
3. **Silences.** The laptop run removed no silence (at 125 % the removal used to be skipped: it worked on 100 %
   clips only). You cut one pause by hand -- 1.15 s inside the shocked reaction (RAW 1888.93-1890.08) -- and left
   four short ones (0.2-0.6 s). With `--speed` the edit is made, cut and checked at 100 % and only then sped up, so
   the silences come out like on your 100 % channel: on laptop004 it removes 0.5 s of your 1.15 s pause and those
   four short ones too (2.0 s at 100 %), and ends 0.2 s shorter than yours. On 021 (100 %) you kept
   the tool's pause cuts -- the 0.47 s one exactly, the 0.22 s one with more around it -- so I left the removal on
   for `--speed 125` too. **Your call** (section 5): if the 125 % channel should keep its short pauses, add
   `--keep-silence`, or tell me the rule (for example: only pauses over 1 s).
4. **1-frame flashes.** The laptop run had three 1-frame clips at RAW shot changes (3.12, 9.95, 12.20) and a 1-frame
   repeat at S08/S09 -- its hard checks failed. They came from cutting the competitor's 125 % clips at shot changes;
   at 100 % the mini-clip rule (no clip under 10 frames, scaled to 13 before a 125 % speed-up) applies as everywhere.
5. **Captions.** The competitor's captions pop in word by word, so the OCR read each caption 5-20 times with
   different errors ("Iguess it wuld be", "I guêssit would be", "A", "1A", "'A", "1", "_____"), and many of those
   became captions of their own ("A", "'A", "I'guessit would be", "be)", "Cole"). Now: **a read with no letter is
   dropped, a one- or two-letter flicker joins the caption next to it, and consecutive reads that are alike become one
   caption with the spelling read on the most frames** (a real word first) -- laptop004's 60-odd reads become 19
   captions, 021's clean ones are untouched. Your `*shocked*` where the competitor shows an emoji (read as "1") cannot
   be guessed.
6. **Framing size.** Your clips are at 160 % (the tool's 156 %) and lower in the picture: again the frame's hole --
   with `--frame` the zoom is set by the hole.

### 1.3 What was wrong in `learn` itself

- **021's project holds three sequences** (your template's 020 edit first, with the most clips; a nest; then the
  021 edit). `learn` took the one with the most clips: the 020 edit. Now it takes the sequence that plays the run's
  RAW (the same size, frame rate and length).
- **laptop004's cut list names `input\raw.mp4`** -- the laptop's input; on this PC that file is 021's RAW. `learn`
  took the first existing path and would have made the test case from 021's RAW. Now a run's own `extras\media\`
  copy comes first, and another path only when its length matches.
- **The answer key came from your audio track** (A1), which you moved by hand: `learn --picture-key` builds it
  from your picture clips (used for both cases).
- **A test case of a 21- or 95-minute RAW** would have been squeezed to 90 MB for the whole RAW: 0.4 Mbit/s for
  021, impossible for laptop004 (its sound alone is over 200 MB). Now such a RAW keeps **the part the edits play
  plus 30 s around it, sharp** (021: 108 s, 82 MB; laptop004: 81 s, 78 MB), its key timed on that window
  (`case.json` `raw_offset`); `check-all --full-size` runs the full RAW and moves the key back.
- A mirrored run is compared as the unmirrored picture, and both edits' speeds are recorded.

Both are now test cases: `tests/real/video021-fixed` (run with `--frame`) and `tests/real/laptop004-fixed` (run
with `--speed 125 --frame`), each with a copy of your frame.png.

## 2. --speed

**100 % is now the default for every Premiere run** (what `--keep-speed` did; the option still works and changes
nothing). The competitor's speed is still measured and used for the matching -- only the edit you import is at 100 %.

**`--speed 125`** plays the whole edit at 125 % in Premiere, whatever speed the competitor used:

1. the edit is made at 100 % exactly as a normal run: cuts moved off speech, silences and repeats cut out, every hard
   check on `1_edit.xml`, the captions transcribed and timed;
2. then `1_edit.xml` is written again from the same plan with **every V1 and A1 clip at 125 %** and every cut,
   dissolve and marker at its frame / 1.25 (two clips that meet at a cut still meet), and `2_captions.srt` is timed
   the same way (every caption's frames / 1.25);
3. the check **XML SPEED** compares the fast edit with the 100 % one clip by clip (the same RAW, 1.25 x as fast, at
   its place / 1.25), plus the usual item / frame checks. The 100 % edit stays in `extras\edit_100pct.xml` and
   `extras\captions_100pct.srt`.

The retimed clips use the in/out encoding fixed after output/020 (Premiere counts `<in>` / `<out>` on the retimed
clip), each in-point on the grid Premiere can start a 125 % clip on, and the pieces of one take stay seamless (a
unit test writes a 125 % XML and checks Premiere would play RAW 503.52 s, not 629 s). **Your laptop004 project
confirms Premiere reads that encoding**: it was imported from the laptop's 125 % XML and every clip starts on the RAW
moment the XML asked for (1871.86 s against 1871.85 s, and so on).

No clip ends up shorter than `--min-clip` (10 frames) after the speed-up: the minimum is scaled up to 13 frames before
it. The speed is the first line of `report.md` and of the end summary (`Speed: 125 % (--speed: made at 100 %,
1_edit.xml plays it this fast)`).

```
..\..\.venv\Scripts\python -m match_cuts --premiere --speed 125
..\..\.venv\Scripts\python -m match_cuts --premiere --speed 125 --frame input\frame.png
```

**Batch mode:** a `speed.txt` holding `125` in a video's subfolder runs that video at 125 %; without one, 100 (a
`--speed` after `--` sets the default for the others). `batch_summary.md` has a speed column.

```
D:\to do\video A\competitor.mp4, raw.mp4                -> 100 %
D:\to do\video B\competitor.mp4, raw.mp4, speed.txt     -> 125 % (speed.txt holds: 125)
..\..\.venv\Scripts\python -m match_cuts batch "D:\to do" -- --frame input\frame.png
```

## 3. --frame

`--frame input\frame.png` (your PNG is 2160x3840, not 1080x1920):

- **The hole** is found from the alpha channel: the largest transparent area (the faint FLICK707 watermark inside
  it belongs to it), as its bounding box -- x 44-2114, y 1054-3473 on your PNG (the rounded corners are covered by
  the frame). Your older 1080x1920 frames on the Desktop have exactly the same hole at half the size.
- **1_edit.xml** is a **2160x3840** sequence (60 fps as before; `--frame-size` changes it) with the PNG on **V2** over
  the whole edit -- straight alpha, Scale 100 % (a 1080x1920 PNG would get 200 %), centred. The PNG is copied into
  the run's `extras\media\` so the XML keeps working when `input\` changes.
- **Every clip covers the hole**: the competitor's framing is mapped into the hole (zoomed only as much as needed,
  cropped at the sides when the hole is narrower) instead of the old 998x1037 template window; `--min-move` keeps
  its meaning (it now counts px of the 1080-wide picture on the 4K sequence). Inside the hole the zoom is now
  within 5 % of yours on 14 of the 19 clips 021 and your edit share (before: none -- 7-18 % too small, close-ups
  31-37 %) and on all 7 of laptop004's, and the XML GAP check confirms no clip leaves any of the hole uncovered.
  "Centred on the person speaking": the competitor already centres its framing on the person it wants you to see,
  so that framing is mapped into the hole as it is; the tool moves the picture onto the person speaking only where
  the framing is borrowed and shows nobody (see 1.1.5 for why it no longer does more; `--follow-speaker` moves
  every clip that does not show the person speaking onto them).
- **Captions stay off the header and headline**: the run writes `extras\frame.json` with the hole and the caption
  zone (inside the hole, from 50 % to 85 % of the height). `restyle` checks the styled captions against it and lists
  any caption outside it; the reference style puts them at 58 %, your template's at 68 % -- both inside. It also
  warns when the donor caption comes from a
  sequence of another size: Premiere keeps a text graphic's size in pixels, so a style made on a 1080x1920 sequence
  looks half as big on the 2160x3840 one (section 5).
- **preview_recreation.mp4** is now your edit seen through the frame: `1_edit.xml` rendered clip by clip (its RAW,
  speed, Position / Scale), the PNG on top and A1's sound, at 1080x1920. The competitor-timed recreation that
  compare.mp4 and the checks read moved to `extras\debug\recreation_check.mp4`.

```
..\..\.venv\Scripts\python -m match_cuts --premiere --fast --frame input\frame.png
```

`--mirror` (new): keep the competitor's horizontal flip; without it the edit is the RAW the right way round (1.2.1).

## 4. Test results

All on this PC, on the final code (the GPU shared between several runs at once, so no timings here). Every run's
own report still opens with "Result: FAIL": that is the frame-exact rebuild of the competitor (criteria c2-c5),
which fails on every real video, as before; the Premiere hard checks below are what your import depends on.

### 021 with your frame -- `output\027`

`--premiere --fast --frame input\frame.png` on your 021 inputs, reusing your run's caches; scored against your
finished edit (the new test case's answer key: your picture clips and your captions).

| | the run you fixed (`output\021`) | tonight (`output\027`) |
|---|---|---|
| Premiere hard checks | fail (XML FLASH at 00:00:02:02) | **pass** -- 2160x3840, frame.png on V2, every clip covers the hole, 34 of 34 clips linked to their sound |
| zoom inside the hole within 5 % of yours | 0 of 18 clips (7-18 % smaller; close-ups 31-37 %) | **14 of 19** |
| centre within 100 px of yours | 11 of 18 | 13 of 19 |
| clips moved onto "the speaker" | 5 | 0 |
| your cuts reproduced (within 2 frames) | 2 of 15 | 3 of 15 |
| length against yours | +6.4 s | +6.1 s |
| captions exactly yours | 21 of 40 | 20 of 40 |
| word errors against your captions | 16.2 % | 15.8 % |

- The `*looks over*` shot at 00:00:02:02 plays its whole 0.5 s (RAW 1144.62-1145.12 s, as in yours): no flash.
- S44 no longer plays 3.08 s on into the applause, and S50 starts at the competitor's cut. Two moves are left as
  CHECK BY HAND: S32 (0.37 s earlier, "this. We've" -- you kept it) and S46 (0.65 s on, "I can tell": the second
  speech model hears words in the applause; your picture stops at the competitor's cut there).
- The 5 clips whose zoom still differs from yours: your close-ups of the reactions (you zoomed them to 145-165 %;
  the tool keeps the competitor's framing, 0.71-0.81 of your zoom) and the `*looks over*` shot (10 % tighter).
- Captions: an earlier run tonight scored 11.8 % word errors; this one heard your "I'm done!" in the applause as
  "Got this I" (confidence 0.09) and made one caption of the twice-said "It's a lot". All of the competitor's
  `*laughing*` / `*applause*` / `*high five*` / `*dying*` are action captions, and `*disgusted*` is back.
- Silences: 3 cut (1.03 s in all), 39.10 s -> 35.80 s.

### laptop004 at 125 % -- the test case, and `output\laptop004\night3` on the full RAW

The test case `laptop004-fixed` (the part of the RAW the edits play, `--speed 125 --frame`), against your edit:

| | the laptop run (`output\laptop004`) | tonight |
|---|---|---|
| Premiere hard checks | fail (1-frame clips, a repeat, 2 x PERSON) | **pass** -- 125 %, frame on V2, 12 of 12 clips linked |
| zoom inside the hole within 5 % of yours | (the old window) | **7 of 7** (1.01 of yours) |
| centre within 100 px of yours | | 4 of 7 (the others 103-129 px) |
| length against yours | +1.2 s | -0.2 s |
| your cuts reproduced (within 2 frames) | 1 of 3 | 0 of 3 (the silences, 1.2.3) |
| captions exactly yours | 9 of 27 | 9 of 27 |
| word errors against your captions | 52.6 % (the screen fragments) | 16.1 % |

On the full 95-minute RAW -- `output\laptop004\night3`, the one to import (`--fast --speed 125 --frame
input\frame.png` on the laptop run's media): the Premiere hard checks pass, 12 of 12 clips linked, nothing moved
onto "the speaker"; 10 of 27 captions exactly yours, 12.5 % word errors; 0.1 s shorter than yours (4 pauses cut,
1.58 s at 100 %).

### 020 at 100 % and 125 %

Your 020 inputs, `--premiere` and `--premiere --speed 125` (`output\020\night3_final\speed100` and `\speed125`):
the Premiere hard checks pass at both speeds (8 of 8 clips linked; at 125 % also XML SPEED). Read the way Premiere
reads a sped-up clip (its `<in>` / `<out>` count on the sped-up clip), every clip of the 125 % XML plays the same
RAW moments as the 100 % one (worst 12.5 ms, under one RAW frame) at its place / 1.25 (within 0.4 frame): the first
clip starts on RAW 506.27 s, where the old bug would have played 632.8 s. Both edits are 15.38 s at 100 % (12.30 s
at 125 %), from 24.85 s before the speech-safe cuts and silences -- the same as last night; your fixed 020 is
19.58 s.

### Unit tests

1197 passed, 94 skipped (files or tools not on this PC), 0 failed -- 29 new tonight: `--speed` (the 125 % XML
plays the same RAW moments; the encoding Premiere reads), `--frame` (the hole from the alpha, V2, coverage, the
captions' zone), the noise / action-beat cut rules, the caption cleanups, batch `speed.txt`, `learn` (the template's
sequence, a moved run, the RAW window), unmirroring, the competitor's own framing and `--follow-speaker`, and the
mini-clip join (below).

### check-all: the 14 test videos

Still running when I pushed this (it takes over 3 hours; the 12 old test videos and the 2 new ones, on the final
code); this section gets its table when it ends. Done so far: deadpool (Premiere checks pass; it now ends where the
competitor's own edit ends, 0.63 s of laughter shorter than last night) and laptop004-fixed (above).

**What check-all and the final runs found on the way** (fixed, each with a unit test): deadpool lost its "And I
was" shot -- the
competitor cut that shot into two 4-frame pieces, and the "no mini cuts" rule of last night judged each piece alone
and let the clip before play on over both; such pieces now go into their own take (`S04+S05`). The first version of
that fix failed 021's XML checks (the pieces of one take can meet 1 tick apart), so the joined clip now keeps its own
source timeline. 021's S49 kept the competitor's framing correctly but the person check still failed it: the
speech-safe cut had played it 0.65 s past its own RAW, and the check now judges such a clip on its whole stretch.
A first full check-all on the final code then failed two old videos' `--min-move` check: video3 changed its
framing by 1 px and video1 by 71 px for nothing. A clip gave up the held framing because it hid the detected
speaker -- but its own framing hid them too (the competitor showed someone else); before tonight such clips were
then moved onto the speaker, which is what you undid. Now it holds unless its own framing shows the person, and a
clip that held a framing follows it when that framing changes later (video1's S15). That fix, run on 021, moved
S49 / S51 / S52 onto "the speaker" again: a held framing still counted as borrowed. It is under 250 px from the
clip's own and never hides a person the clip's own framing shows, so it counts as the competitor's own now; only
replaced spots and uncovered windows count as borrowed. The new 021 test case (the
thorough analysis of its RAW window) also showed a 1-frame flash: the first frame of a new RAW shot that the
competitor cut a frame late, left at the framing of the shot before; it now goes into the clip that runs on that
shot. check-all was then run again from the start on the fixed code (the table above).

## 5. Check by hand in the morning

1. **The mirror (decide).** Both mirrored competitors (020, laptop004) ended unmirrored in your projects, so the
   Premiere edit is now unmirrored by default. Did Premiere import the tool's flip and you took it off, or was the
   flip missing after import? If you want the competitor's mirror, run with `--mirror` -- and tell me if it does not
   show in Premiere (Effect Controls > Horizontal Flip on each clip).
2. **021 with your frame** (`output\027`): import `1_edit.xml` -- a 2160x3840 sequence, frame.png on V2. Check that
   the frame lines up (header and headline crisp, the video only inside the hole) and the framing per clip;
   `extras\preview_recreation.mp4` shows the same without Premiere.
3. **Caption size on the 4K sequence.** Your restyle donor caption comes from a 1080x1920 sequence. Premiere keeps a
   text graphic's size in pixels, so on 2160x3840 the captions may come out half as big: `restyle` prints a SIZE
   line when that happens. Scale them x 2 (select all, Effect Controls > Motion > Scale), or style one caption at the
   right size and restyle with it as the donor.
4. **--speed 125** (`output\020\night3_final\speed125`; laptop004 on its full RAW in
   `output\laptop004\night3`): every clip at 125 %, the cuts and captions where they belong, the sound at
   125 % (pitch: your own effect, untouched).
5. **The pauses on the 125 % channel (decide).** On laptop004 you left four short pauses (0.2-0.6 s) that the tool
   now cuts, and cut one long one (1.2.3). On 021 you kept the tool's pause cuts, so I left them on. If the 125 %
   channel should keep its short pauses, add `--keep-silence` (in batch: after `--`), or tell me a rule.
6. **The new cut rules on 021:** the `*looks over*` shot at 00:00:02:02 is its full 0.5 s again; at S44 / S50 the
   cut stays inside the applause where the competitor cut it (no 3 s play-on) -- listen to those two cuts. **S11**:
   your picture showed the women's "ewww" shot (the 0.53 s after the competitor's cut) instead of the competitor's
   S11; the tool now ends S11 at the competitor's cut -- swap it by hand if you want the reaction. S46 still plays
   0.65 s on ("I can tell", CHECK BY HAND in the run's summary).
7. **"I'm done!" (021, about 00:00:28):** this run's transcript heard it in the applause as "Got this I"; correct it
   by hand. Next for the tool: a clearly read competitor caption beats words the transcript is unsure of there.
8. **Re-framing (decide):** clips are no longer moved onto the face the speech detection picks; each shows the
   competitor's own framing (inside your hole), as in your three finished edits. The run's summary lists the clips
   where the detected speaker is outside the picture ("keeps the competitor's own framing -- check"). This changes
   the Zendaya interview: the competitor shows the listener's reaction for most of it, and the tool used to move
   those stretches onto the speaker (your earlier task "the person speaking is always in the picture"). If you want
   that on a video, add `--follow-speaker`; if you want it always, tell me and I make it the default again.
