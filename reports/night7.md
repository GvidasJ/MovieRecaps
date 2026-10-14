# Night 7 (14 October): your video6 fix, censored captions, captions always written, OK with a note, the RAW's frame rate

## In short

Everything on your list is done. In one line each:

1. **Your video6 fix** (`finished\video6\video6_fixed.prproj` against the laptop's `output\video1-2`): your framing was
   exactly the tool's; two of your cut changes became rules -- **a pause at one of the RAW's own shot changes is cut
   even with a faint sound in it**, and **a pause after a sound that is not a word keeps that sound's soft end** --
   and one caption change: **no caption of 2+ words stays on screen longer than 1.5 s** ("you mean succeed?" ->
   "you mean | succeed?", as you did). Your fix is also a new test case, `video6-fixed`, so every check-all scores it.
2. **Your answers**: (a) **sexual / crude words are censored** in the captions with one letter as `*` -- `p*rn`,
   `se*ual`, `perv*rt`, `d*ck` -- from a list you can edit, `tools\match_cuts\caption_censor.txt`; gay / fag and every
   word not on the list stay written out. (b) No trims at the competitor's cut points or the ending (unchanged).
   (c) The zoom stays. (d) video020: nothing changed.
3. **Captions are always written.** A line that breaks a rule is written as it is and listed as *doubtful* (end
   summary, report, batch log). Spelled-out letters (`A-N`, `O-N`, `R-E`, `T-H-E`) are fine and written in capitals
   with dashes; a single "A" can never be an allowlist entry; a caption under 2 frames is left out; a missing
   `2_captions.srt` is said in capitals and fails the deliverables check (never "8/8 deliverables present" again).
4. **The batch**: when every file is there and the only problem is "exports NOT validated", the video is
   **OK (with a note)**, not FAIL.
5. **The sequence runs at the RAW's frame rate** (23.976 RAW -> 23.976 sequence, 29.97 -> 29.97, ...; an unusual or
   variable rate -> the nearest normal one). Each clip starts on exactly the RAW frame it started on before, a cut at a
   RAW shot change lands exactly on it, A1 stays frame for frame in sync with its picture, and the captions and the
   frame move with the cuts. Tested on 23.976, 24, 25, 29.97, 30 and 59.94 fps RAWs.
6. **Tests** (section 6): every test passes (1295 unit tests, 96 GPU tests, 21 slow end-to-end tests). The final
   check-all (19 videos, every one now also checked on its delivered XML at the RAW's rate): **17 of 19 pass every
   hard check**; the two that do not are known -- video020 `--fast` (as on night 6) and your new video6 case, whose
   last frame is the competitor's own frame of the next shot (you kept it). Against night 6 nothing got worse: the
   same cuts, and the captions the same except laptop013 / laptop010 matching your censored words now (word errors
   26.7 -> 13.3 % / 10.3 -> 7.7 %) and one 33 ms caption left out in laptop009. A batch run on four folders (video1,
   Deadpool, Anchorman, two competitors of one episode) passed 5 of 5, video1 as **"OK (with a note)"**, at 23.976 /
   29.97 fps. It also showed one lost word in video1's captions ("not suck" for your "not" / "succeed"); fixed, and
   video1 run again: now as in your fix.
7. **On the laptop**: `git pull` (section 7).

## 1. What your video6 fix taught

### The files

There is no `output\laptop_video1` here; your project plays `D:\Shared\video1-2\...` -- the laptop's batch run
`output\video1-2` (made at 01:56 from the laptop's `input\videos\video1`: a 24 s competitor at 30 fps from a 12-minute
RAW at 23.976 fps, `--fast`, 100 %, your default template). So that is the run I compared. I wrote a relinked copy of
your project that points at `output\video1-2\extras\media\raw.mp4`, `frame.png` and `2_captions.srt`:
`finished\video6\video6_fixed_relinked.prproj` (your original is untouched). The project holds five sequences; the
fifth (60 fps, 20.58 s) is the edit -- three are template sequences of other videos, and one is an **empty 23.976 fps
sequence** (I guess you tried a 23.976 sequence: item 5 now gives you that).

### What you changed, and what I did with it

| | the tool (laptop) | your fix | what I did |
|---|---|---|---|
| framing | 11 clips, Scale 115.28 %, one position | the same on all 9 clips | nothing to learn |
| start | RAW 331.57 s | 331.83 s (0.27 s of quiet trimmed) | nothing (your trim, as you said) |
| pause after the high five | kept: RAW 333.9-334.5 s | cut: 334.03-334.53 s (0.50 s) | **new rule** (below) |
| "suc... ceed" (RAW 344.85-345.17 s) | cut (0.32 s) | put back | not a rule (below) |
| pause before "No one" | 0.17 s cut (345.75-345.92 s) | 0.60 s cut (345.67-346.27 s) | not a rule (below) |
| the other 5 pause cuts | | exactly the same frames | -- |
| ending | RAW 355.07 s; its last frame is the next shot's (the penguins) -- the run said FAIL (`XML FLASH`) | the same: you kept that frame | nothing (your ending); the batch now says OK with a note (item 4) |
| "you mean succeed?" (2.4 s on screen) | one caption | "you mean" / "succeed?" | **new rule** (below) |
| "*gives high five*" | -- | your action caption over the high five | nothing: the tool cannot see actions |
| "no" / "one", "we" / "will all" / "be" / "eating popcorn!", "I have" / "confidence" | | "no one", "we will all" / "be eating" / "popcorn", "I" / "have confidence" | not rules (below) |

**New rule 1 -- a pause at one of the RAW's own shot changes.** Your 0.50 s cut after the high five holds the RAW's
own shot change at 334.38 s. The tool kept that pause because it was "not a clear silence": a faint sound in it, 20 %
of the way from the background noise up to the speech level, and the rule cuts only up to 12 % (music, a breath, the
room keep a pause -- you kept those). But where the RAW cuts to another shot, the picture changes anyway, so the cut
cannot be seen. Now such a pause (at most 1 s long) is cut up to 25 %. I checked it against every pause of your
earlier fixes that holds a RAW shot change: laptop010's three (all cut by you -- the rule cuts them too) and
laptop013's 1.4 s music beat (23 %, kept by you -- kept, because it is longer than 1 s).

**New rule 2 -- the soft end of a sound that is not a word stays.** With rule 1 the tool cut your pause, but from
0.15 s earlier than you: night 6's "a word's soft tail goes with the pause" (learned on laptop010's word endings)
also cut the quiet end of the high five. Now that tail is cut only after a word; after a sound with no word in it
(a high five, a door) it stays, as you kept it.

**New rule 3 -- no caption of two or more words on screen longer than 1.5 s.** Of your 990 captions of 2+ words (all
your SRTs and answer keys), one stays longer than 1.5 s (2.03 s, "of £7.63"); 3-word captions at most 1.47 s. The
tool's "you mean succeed?" stayed 2.4 s. Now a caption grouped in your style that stays longer is split before the
word that leaves the longer piece shortest, never inside a pair kept together ("you mean") -- here exactly your
"you mean" / "succeed?". (Only for captions grouped in your style; a competitor's captions followed as they are stay.)

**Not made into rules (one example each, or against your other fixes):**

* **"suc... ceed"**: the character says "suck... seed" (the pun), and the speech model heard it as two words, so the
  0.26 s gap between them looked like a pause between words and was cut; you put it back. A pause inside a word the
  model splits in two cannot be told from a real pause between two words (your other fixes cut pauses inside phrases
  too: laptop010's "prison | you're", "a | adults").
* **Before "No one"** you cut 0.6 s, 0.3 s of it into the soft start of "No" (the tool keeps 0.03 s before the word).
  Your other fixes keep a median 0.026 s before the next word, so I kept that.
* **Caption groups** ("no one", "we will all" / "be eating", "I" / "have confidence") come from the style model learned
  from your SRTs; one video is too little to change it. With tonight's cut changes the tool now writes "we will all"
  like you. You also dropped the "!" of "popcorn!", but your keys keep "!" in five other places, so no rule.
* You have the caption "sucking seed" twice (the second only 0.2 s, at your cut at 13.95 s) -- it looks like a piece
  left over from cutting the caption track; I did not learn from it.

**Your fix is now a test case**: `tests\real\video6-fixed` (made with `learn --picture-key`: the competitor, the part
of the RAW your edit plays plus 30 s around it, 57 MB, your 32 captions and 9 picture clips as the answer key;
`"style": "new"`: check-all runs it thorough and scores it). The glossary did not change (no word was corrected).

## 2. Your answers to night 6's questions

**(a) Censored captions.** The words of `tools\match_cuts\caption_censor.txt` are written with one letter as `*`, as
you wrote them in laptop010 / laptop013. The file holds your four (`porn = p*rn`, `sexual = se*ual`,
`pervert = perv*rt`, `dick = d*ck`) with their other forms (porno, sexy, sexually, perverted, dicks, ...) and more
sexual / crude words in the same style (cock, pussy, boobs, tits, horny, slut, whore, cum, orgasm, masturbate, nude,
...). Edit it as you like -- one word per line; `word = f*rm` says exactly how to write it; a word alone gets its
first vowel after the first letter starred (`boobs` -> `b*obs`). It matches whole words in any case (`Porn` ->
`P*rn`), and a word's `'s` form. **A word that is not on the list is never changed**: gay, fag (laptop009 kept them),
pissed, damn, and the swear words (fuck, shit) are not on it -- your keys never show what you do with those; add
them if you want them starred. The end summary and the report list every caption censored (*Captions censored*).

On tonight's check-all: laptop010 now writes "at a p\*rn" and laptop013 "a perv\*rt!", "about the se\*ual", "of
d\*ck!" -- your own forms.

**(b) No trims at the competitor's cut points or the ending.** Nothing changed: those stay yours.

**(c) The zoom stays as it is.** Nothing changed.

**(d) video020.** Nothing changed (run such videos thorough).

## 3. Captions are always written (item 3)

* **Always written.** Before, a line that still broke rule 1-4 held the whole file back -- the laptop's video2 got no
  `2_captions.srt` because of `A-N`, `O-N`, `R-E`, `N`. Now the file is always written, and every line that still
  breaks a rule (1-5) is listed as doubtful: in the end summary (*Doubtful caption lines (written as they are --
  check them)*), in `report.md` (Captions section), in `extras\debug\captions.json`, and in the batch's console and
  `batch_summary.md` under the video.
* **Spelled-out letters are fine**: a word spelled letter by letter (`A-N`, `O-N`, `R-E`, `T-H-E`, `U-S-A`) is a real
  word for rule 5 and is written in capitals with dashes (rule 4 writes `a-n` as `A-N`); a lone capital letter (`N`)
  is not flagged either. (A stray letter the OCR reads where nothing is said is still left out as screen noise.)
* **A single "A" can never be an allowlist entry**: the allowlist reader skips every single letter except `I`, so
  nobody can turn every "a" into "A" by mistake.
* **A missing `2_captions.srt` is said clearly**: the end summary says `2_captions.srt  NOT WRITTEN -- this edit has
  no captions: <why>`; the deliverables check now counts the captions file (`9/9 deliverables present`, or `8/9
  deliverables present (2_captions.srt MISSING)` -- and the check fails); the batch prints `NO CAPTIONS:
  2_captions.srt was not written -- <why>` under the video, its summary line says **NO CAPTIONS**, and the table has a
  *captions* column (written / N doubtful lines / MISSING).
* **No caption shorter than 2 frames**: a caption shorter than 2 frames of the sequence you get (at 23.976 fps:
  under 83 ms; the laptop's video2 had a 1-frame `wh****`) is left out -- the caption before runs on over its frames
  -- and listed. (A heard "whore" is now written `wh*re` anyway: it is on the censor list.)

## 4. The batch: OK with a note (item 4)

When every file is there and the only problem is "exports NOT validated" -- a hard check of `1_edit.xml` found
something, like the laptop's video1 (one RAW frame of the next shot at the very end, `XML FLASH`) -- the video is
**OK (with a note)**: the console says `OK (with a note) in 4m28s: every file is there, but a check of 1_edit.xml
found something to look at in Premiere: XML FLASH ...`, the summary's line says "done ... Note: ...", and it counts
as passed (exit code 0 when nothing else failed). A missing file -- the captions included -- is still FAIL.

## 5. The sequence at the RAW's frame rate (item 5)

**What you get**: `1_edit.xml`'s sequence runs at the RAW's own rate: a 23.976 RAW gives a 23.976 sequence, 24 -> 24,
25 -> 25, 29.97 -> 29.97, 30 -> 30, 50 -> 50, 59.94 -> 59.94, 60 -> 60. Any other rate (15, 20, 48 fps ...) or a
variable one (the RAW is first made constant at its own nominal rate, as before -- a test file that switched between
30 and 20 fps came out at 30) gets the nearest of those (15 and 20 -> 23.976, 48 -> 50, 100 and 120 -> 60). The end
summary's first line says which ("sequence 23.976 fps (the RAW's rate)"). `--sequence-fps 29.97` (or any other
normal rate) chooses the rate yourself; `--sequence-fps 60` gives the old 60 fps sequence.

**How** (so nothing else had to change): the edit is still planned and checked on the 60 fps grid exactly as before
-- every rule, every hard check, the captions --, and the last step writes it again at the RAW's rate:

* every cut goes to the nearest frame of the new rate;
* **every clip starts on exactly the RAW frame it started on before**: at the RAW's rate one sequence frame is one
  RAW frame, so the clip after a competitor cut starts on the competitor's own first RAW frame there (video1-2's
  first clip: RAW frame 7949, the cut list's `raw_in_frame`);
* the pieces of one take stay seamless (each starts exactly where the one before ends -- no frame skipped or shown
  twice);
* **a cut at one of the RAW's own shot changes lands exactly on it**, and a clip never shows a frame of a RAW shot
  its 60 fps version does not reach (no flash);
* A1 plays exactly the frames its picture shows (in sync, still linked one to one);
* markers, the frame PNG on V2 (the whole edit) and `2_captions.srt` move with the cuts: a caption that started on a
  cut still does, the captions stay back to back, and none is shorter than 2 frames;
* the 60 fps versions stay in `extras\edit_60fps.xml` and `extras\captions_60fps.srt` (check-all scores those, so
  its numbers stay comparable with the earlier nights').

**Checked on every run**: the new XML gets the hard checks again on its own numbers -- `XML ITEM`, `REPEAT`,
`FLASH`, `AUDIO`, `TRANSITION`, `LINK`, `SYNC` -- and a new one, `XML RATE`: every V1 clip and A1 clip plays, on each
of its frames, the RAW its twin in the 60 fps edit plays at that moment (within a frame), at the same speed; at most
one frame at each end may sit where the 60 fps edit already shows the next clip (a cut moved to the nearest frame or
onto a shot change); no clip of the 60 fps edit is missing; the length is the same within a frame. Any problem fails
the run like every other hard check. `--speed 125` goes through the same step (made at 100 %, then played faster and
written at the RAW's rate). RAW-only runs (no competitor) too -- they also got `--speed` that way, which they ignored
before.

## 6. Test results

### Unit tests

On the final code, the whole suite with the GPU hidden: **1295 passed, none failed** (95 skipped: they need the GPU
or are the slow ones; 11 min, run next to check-all). With the GPU and nothing else running: the GPU's own test
files (`test_gpu`, `test_fullres`, `test_asr`, `test_caption_recheck`, `test_lazy_frames`, `test_quality`,
`test_visual_match`) **96 passed**, and the slow end-to-end tests on synthetic videos (`--runslow`) **21 passed**
(the other 63 are for other test profiles, as always; 14 min). (The GPU and slow tests ran on the code before the
lost-word fix; the fix's own test is in the unit suite above.)

34 tests are new: one in `tests\test_caption_recheck.py` (the lost word, below) and 33 in `tests\test_night7.py`:
spelled-out letters (A-N, O-N, R-E, T-H-E, U-S-A) and a lone capital, the allowlist without single letters, the
file always written with its doubtful lines, captions under 2 frames left out, the shipped censor list (your four forms, never gay / fag) and its default form, a missing `2_captions.srt` failing
the deliverables check with its reason, the batch's OK with a note (and still FAIL with a missing caption file), the
batch summary's NO CAPTIONS and doubtful lines, the sequence rate for every normal and odd rate, the edit at
23.976 / 29.97 / 30 / 25 fps playing the same RAW frames at the same moments with every new take on its exact first
RAW frame, a cut at a RAW shot change landing exactly on it, `--speed 125` together with the RAW's rate, the
captions following the cuts onto the new frames, `XML RATE` passing a cut one frame off and failing one three frames
off, video021's cut a tick after a RAW shot change landing on it, laptop004's cut on a shot change at 125 % (both
sides held half its first frame) landing on it, a sped-up clip never starting on the frame before its first, a
one-frame framing piece going into its take, the pause at a shot change (cut; kept without a shot change, when
longer than 1 s, or with a louder sound), only a word's soft tail cut, the long caption split as you did ("you mean"
/ "succeed?"), and which files check-all scores. Older tests changed with the rules: the SRT writer (it always
writes now), the RAW-only run (its sequence is the RAW's 30 fps now) and night 6's list of new-style test cases
(+ video6-fixed).

### A batch run, as on your laptop (items 1, 3, 4, 5)

`batch` (so `--fast`, as on your laptop) on a folder of four, with the code before the lost-word fix below (video1
was run again after it): `video1` (your video6 video: its laptop RAW at 23.976 and competitor), `Deadpool` (a 29.97
RAW), `Anchorman`, and `Show 013` (one episode, two competitors); 10:42-10:53:

```
[video 1 of 5] Anchorman -> work\t22\batch\out\Anchorman (speed 100 %) -- whole batch: about 23 min left
    PASS in 3m05s
[video 2 of 5] Deadpool -> work\t22\batch\out\Deadpool (speed 100 %) -- whole batch: about 16 min left
    PASS in 3m19s
[video 3 of 5] Show 013_competitor1 -> ...
    PASS in 2m10s
[video 4 of 5] Show 013_tiktok clip 2 -> ...
    PASS in 49s
[video 5 of 5] video1 -> work\t22\batch\out\video1 (speed 100 %) -- whole batch: about 7 min left
    OK (with a note) in 1m37s: every file is there, but a check of 1_edit.xml found something to look at in
    Premiere: ['XML FLASH S01+S02+S03+S04+S05+S06+S07 at 00:00:21:01: 1 frame(s) of a different RAW shot (0.04 s,
    under 0.25 s) -- a flash frame']
match_cuts batch: 5 of 5 passed in 11m01s; summary: work\t22\batch\out\batch_summary.md
```

`batch_summary.md` starts "5 video(s) in 11m01s: **5 passed (1 with a note), 0 failed**", and video1's line says
"done ... Note: every file is there, but ...". What the runs delivered:

* **video1**: a **23.976 fps** sequence (11 clips, 21.10 s; the 60 fps edit 21.10 s), every new take on its exact RAW
  frame; the only note is the last frame you kept. **Your high-five pause is cut**: RAW 334.12-334.49 s (yours:
  334.03-334.53 s -- the tool keeps the end of the high five and 0.03 s before "Which"; within 2-3 frames of your cut on
  both sides), counted as "1 with a faint sound in it cut at a RAW shot change". 33 captions on 23.976 frames (34
  after the fix below); no doubtful line; nothing to censor in this video.
* **Deadpool**: a **29.97 fps** sequence, 18 clips, every check OK.
* **Anchorman / Show 013** (parts of your laptop010 / laptop013 videos): 23.976 sequences, every check OK; their
  captions censored 1 word ("p\*rn") and 3 words ("perv\*rt", "se\*ual", "d\*ck") -- listed under *Captions censored*.
* In all five, all 43 clips that start a new take start on exactly the RAW frame the 60 fps edit starts them on.

**One thing the batch showed, fixed: a lost word.** video1's captions read "not suck" where your fix has "not" /
"succeed" -- "seed" was gone. The caption step double-checks unsure words against the RAW (the "recheck"), one piece
of the edit at a time. The edit's speech model heard "succeed" over the cut that removes the pause of "suck... seed";
the RAW heard for the piece *before* that cut has only "suck" -- more confidently -- so "suck" replaced "succeed" and
the rest of the word was lost. (The laptop's run and the thorough check-all had kept "succeed": the recheck's choice
there is close and flips with small changes of the edit.) Now being more confident is not enough for the RAW's
version of one side of a cut to replace edit words that run on past that cut with more syllables: the rest is the
other piece's sound. Of the 41 recheck changes in tonight's and night 6's final runs, this was the only one of its
kind (the other changes at a cut were backed by the competitor's caption, which still decides). The batch's video1
again with the fix (`work\t22\batch2\out\video1`, 11:15): "not" / "succeed" / "no" / "one", as in your fix; the edit
itself is identical, and no other caption changed.

### check-all (the final code, all 19 videos)

The final code (frozen as `work\snap7i`), the same split as night 6 (new-style cases thorough and scored against
your fixes, old ones `--fast` with the hard checks only), two streams, 11:17-12:09 (52 min). The "+/-" columns are
against night 6's final check-all. Scorecard: `work\t22\final6\final_scorecard.json`; run folders
`work\t22\final6\G1\runs` and `G2\runs`. (The run before it, `final5`, on the code before the lost-word fix, gave
the very same edits and caption files, byte for byte: that fix changes none of these 19 videos.)

```
video                   time  hard checks  captions exactly       word errors      cuts reproduced      length
deadpool               3m37s  PASS         45/60 (75 %) +0        4.0 % +0.0       no cut key           -
laptop004-fixed        2m27s  PASS         hard checks only (old style, --fast; 125 %)
laptop009-fixed       13m59s  PASS         18/52 (35 %) -1        8.3 % +0.8       2/13 (15 %) +0       +0.8 s (+3 %)
laptop010-fixed        1m45s  PASS         10/34 (29 %) +0        7.7 % -2.6       1/9 (11 %) +0        +1.3 s (+6 %)
laptop013-fixed        2m14s  PASS         9/22 (41 %) +0         13.3 % -13.3     1/5 (20 %) +0        +0.3 s (+2 %)
mike005-fixed          8m50s  PASS         9/46 (20 %) +0         9.8 % +0.0       2/13 (15 %) +0       +4.1 s (+23 %)
spiderman-school       2m50s  PASS         hard checks only (old style, --fast)
video017-fixed         2m26s  PASS         hard checks only (old style, --fast)
video018-fixed         6m06s  PASS         hard checks only (old style, --fast)
video020-fixed         3m05s  FAIL         hard checks only (old style, --fast) -- XML FLASH: 1 frame at 00:00:14:13
video021-fixed        18m54s  PASS         21/40 (52 %) +0        13.5 % +0.0      0/15 (0 %) +0        +7.0 s (+24 %)
video1                 7m06s  PASS         hard checks only (old style, --fast)
video2                 1m55s  PASS         hard checks only (old style, --fast)
video3                 2m14s  PASS         hard checks only (old style, --fast)
video4                 6m19s  PASS         hard checks only (old style, --fast)
video5                 1m50s  PASS         hard checks only (old style, --fast)
video6-fixed           5m51s  FAIL         18/32 (56 %)  (new)    10.0 %           5/7 (71 %)           +0.5 s (+2 %)
                                           -- XML FLASH: 1 frame at 00:00:21:00 (the competitor's last frame)
zendaya                2m00s  PASS         no caption key         -                no cut key           -
zendaya-age            1m46s  PASS         hard checks only (old style, --fast)
all caption keys                           130/286 (45 %)         8.7 %
all cut keys                                                                       11/62 (18 %)         +14.0 s (+11 %)
```

**Hard checks: 17 of 19 pass**, every one of them now also on the delivered XML at the RAW's own rate (23.976,
24, 25, 29.97, 30 and 59.94 fps here; mike005 and laptop004 at 125 % too). The two that do not are known:

* **video020** `--fast`: the same flash as on night 6 (its `--fast` analysis takes 12.6 s of the RAW for another
  video; thorough passes) -- now 1 frame at 23.976 instead of 3 at 60 fps.
* **video6-fixed** (your new case): the competitor's own last frame is the first frame of the next shot (the
  penguins), and the edit ends where the competitor ends -- you kept that frame in your fix, and you said the ending
  is yours, so I left it. In a batch this video is "OK (with a note)".

**Against night 6** the edits are the same: every scored video reproduces as many of your cuts and comes out the
same length. Captions: the same, except **laptop013 word errors 26.7 -> 13.3 %** and **laptop010 10.3 -> 7.7 %** --
that is the censor list: p\*rn, perv\*rt, se\*ual, d\*ck now match your keys -- and **laptop009 one caption fewer
exactly (19 -> 18)**: night 6 wrote a 33 ms caption "he's" that is now left out (under 2 frames, item 3), so the
caption before runs on. No other caption changed.

**Every new take starts on the same RAW frame**: on the 19 delivered XMLs, 183 of the 186 clips that start a new
take start on exactly the RAW frame the checked 60 fps edit starts them on (every one at 100 %); the other 3 are in
mike005 at 125 %, one frame later -- Premiere starts a sped-up clip only every 1.25 RAW frames, and the tool then takes
the next frame rather than the one before. (`work\t22\first_frames.py`)

**Your video6 fix as a test** (thorough, on the part of the RAW your edit plays): 18 of your 32 captions exactly,
5 of your 7 cuts reproduced, 0.5 s longer than yours. The laptop's own run of it (`--fast`, the full RAW, last
night's code) scores 20 of 32, 5 of 7 and 0.9 s longer. The two cuts not reproduced are the "suck... seed" pause
(cut by the tool, kept by you) and your 0.6 s cut before "No one" (section 1). The captions differ mostly in
timing and grouping near cuts (section 10).

**The PC**: CapCut was open all night (about 1.1 GB of GPU memory, idle as far as the counters show); no Adobe
program used the GPU. The unit suite ran next to the check-all for 11 minutes. So the run times above are not
free-PC times.

## 7. On the laptop

Everything is committed and pushed to the branch `claude/new-session-ol4era`: tonight's code in five commits, from
`1c62bbb` ("Night 7: the sequence at the RAW's frame rate, ...") to `7b3ab25` ("Caption recheck: ..."), then this
report. On the laptop, in PowerShell or cmd:

```
cd C:\Users\gvida\Desktop\bh\MovieRecaps
git status
git pull
```

* `git status` first: if it lists files under `tools\match_cuts\` as modified (you changed something there), keep a
  copy of them, then `git stash` before `git pull` (and `git stash pop` after, if you want your change back). Your
  `input\`, `output\` and `finished\` folders are never touched by `git pull`.
* If `git status` says you are on another branch: `git checkout claude/new-session-ol4era`, then `git pull`.
* Nothing new to install: no new Python packages, no new models.
* Then run the batch as before, e.g.
  `.venv\Scripts\python -m match_cuts batch input\videos --out output`
  -- the edits come out at each RAW's own frame rate, with censored captions, always with `2_captions.srt`.
* To change what is censored: edit `tools\match_cuts\caption_censor.txt` (Notepad is fine). Note: `git pull` would
  overwrite your edits there only if I change that file again -- git then refuses and tells you; keep a copy.

## 8. What I decided on my own

1. **Which run your video6 fix belongs to**: `output\video1-2` (your project's media paths), not a folder called
   laptop_video1 (there is none).
2. **Your start trim, the ending and the trims at cut points** of video6: not learned, as you said.
3. **Three new rules from video6** (section 1) -- each checked against your earlier fixes first; and **four things
   not made into rules** (the "suck... seed" pause, the cut before "No one", the caption groups, the "!").
4. **The censor list** holds sexual / crude words only. Swear words (fuck, shit) are not on it: none of your keys
   shows what you do with them -- add them to the file if you want them starred.
5. **Doubtful caption lines are listed, not warnings**: they appear under *Check by hand*, in the report and in the
   batch, but they do not add to the run's warning count (a rule-5 flag never did).
6. **A missing captions file fails the deliverables check** of `--premiere` runs (your runs) -- with its reason.
7. **"Shorter than 2 frames"** counts frames of the sequence you get (at 23.976 fps: under 83 ms).
8. **OK with a note counts as passed** in the batch (exit code 0), as long as every file is there.
9. **The frame-rate change happens in the last step**: everything is still planned and checked at 60 fps (the rules
   were all tuned there; your answer keys are 60 fps edits), then written at the RAW's rate and checked again --
   rather than planning at 23.976, which would have changed every rule's frame counts. RAW-only runs (no competitor)
   get the same last step (and so, for the first time, `--speed`). `--sequence-fps` lets you choose another rate
   (`--sequence-fps 60` gives the old sequence).
10. **check-all scores the 60 fps copies** (`extras\edit_60fps.xml`, `captions_60fps.srt`), so tonight's numbers can
    be compared with night 6's: your keys were made on 60 fps sequences, and the "exact caption" score allows 2
    frames at 60 fps (33 ms), less than one 23.976 frame.
11. **Your video6 fix became the test case `video6-fixed`** (new style: thorough and scored in every check-all).
12. **Six check-all runs**, the first four each finding something in the new frame-rate step that I then fixed:
    * the 1st (stopped halfway at 07:49): my new `XML RATE` check was too strict at a cut moved onto a shot change,
      and `--speed` runs needed their 60 fps copy for scoring;
    * the 2nd (07:49-08:50) said 19 of 19 pass -- wrongly: the last step logged a flash in five delivered XMLs but
      let the deliverables check pass (it set the run's "ok" but not the "validated" flag the check reads; the old
      `--speed` step had the same gap). Two of the five are the known flashes (video6's ending, video020 `--fast`);
      three (video021, video5, laptop004) were real: where the 60 fps edit cut one tick (17 ms) after a RAW shot
      change, the clip before showed 0.4 of the new shot's first frame -- at the RAW's rate a whole frame at its
      framing;
    * the 3rd (08:54-09:39): video021 and video5 fixed; laptop004 (125 %) still showed one: Premiere starts a sped-up
      clip only every 1.25 RAW frames, so its start had rounded onto the previous shot's last frame;
    * the 4th (only the two `--speed` videos): rounding that start up moved the problem to the next cut, where both
      sides of laptop004's cut on a shot change held half of the new shot's first frame;
    * the 5th (09:57-10:42, all 19 videos): a cut -- or a split of one take -- with a RAW shot change within a frame
      of both its sides now lands exactly on it, whatever the speed: 17 of 19, the two known;
    * the 6th (11:17-12:09, the final code, after the lost-word fix below): the same, byte for byte -- the numbers
      in section 6.
    I replayed laptop004's exact data offline to find the 3rd and 4th (`work\t22\dbg_replay.py`).
13. **The lost word** (section 6, the batch run): fixed in the caption recheck rather than left for you, because a
    word missing from a caption is worse than a word heard a little wrong. The fix is narrow -- it only stops the
    RAW's version of one side of a cut replacing edit words that run past that cut with more syllables, on
    confidence alone -- and of the 41 recheck changes in tonight's and night 6's final runs it touches only this
    one. Where the edit's word is really wrong there, it now stays as the edit heard it (it was always kept that
    way before the recheck existed).

## 9. Check by hand

1. **A 23.976 edit in Premiere**: import `work\t22\batch2\out\video1\1_edit.xml` (your video6 video, made tonight by
   the batch on the final code) and `2_captions.srt`. The sequence should say 23.976 fps; the clips play your RAW
   with no jump or repeated frame inside a take; every cut lands where the picture changes; the frame PNG covers the whole edit; A1
   is linked to V1 and in sync; the captions sit on the speech.
2. **A 29.97 edit**: the same with `work\t22\batch\out\Deadpool\1_edit.xml`.
3. **The censor list** `tools\match_cuts\caption_censor.txt`: add or remove words you want (fuck / shit are not on it).
4. **The batch summary** `work\t22\batch\out\batch_summary.md`: video1 should be "OK (with a note)" (the last frame of
   the penguin shot, as in your fix), with the note saying so.
5. **The new pause rule** on video1: the pause after the high five (00:00:02 in the edit) cut, the end of the high
   five kept, "Which is fine" starting cleanly.

Everything tonight's runs left in `work\t22\` (the test runs, the check-alls, the batch demos) and the frozen code
copies `work\snap7a` ... `work\snap7i` can be deleted once you have looked: none of it is needed by the tool
(`work\cache`, the stage caches, I left alone).

## 10. Left for next time

1. **A comic pause inside a word** ("suck... seed"): the speech model hears two words, so it looks like any pause.
   If you see more of these, tell me -- a rule needs more than one example.
2. **Word timing near a cut**: on video6 the speech model timed "Which" a little before the cut although it is said
   after it, so the caption split into "which" / "is fine" at the cut where you have "which is fine". This happens
   2-5 times a video (night 6's runs the same). I tried two fixes and scored both on your keys, offline: starting
   every caption on the RAW's measured speech onset -- much worse (deadpool 45 -> 26 of 60 captions exact,
   laptop010 10 -> 2); starting only such a caption on the cut (joining the piece split off) -- also worse (113 ->
   110 of 254 exact over the six videos). Your captions follow the word timings, so I left them.
3. **Caption groups** like "no one" / "we will all" / "be eating": the style model learned from your SRTs decides
   them; it still splits a few phrases you keep together.
4. **Action captions** ("\*gives high five\*", "\*thinks\*"): the tool cannot see actions; they stay yours.
5. **The swear words** for the censor list: your call (section 2).
