# Night 6 (13 October): pauses cut carefully, 5.1 in stereo, wrong inputs stop at once, time left, a better batch

## In short

Everything on your list is done (sections 1.1-1.11), plus three fixes tonight's checks turned up (1.12). In one line
each:

1. **laptop004's flash** fixed: a piece that starts on a RAW shot change keeps the competitor's timing; the piece before
   ends exactly there (the sound runs on).
2. **Takes join within 1.5 RAW frames** (your answer B).
3. **5.1 RAWs**: `1_edit.xml` plays `raw_stereo.mp4` -- the picture copied, the dialogue (centre) with the front left /
   right, no surround (your answer A). The analysis stays on the RAW.
4. **Pauses are cut by default, carefully** (your answer C): only a clear silence of 0.15 s or more between words,
   cut from 0.05 s after the word (a long soft tail goes too) to 0.03 s before the next, at least 0.1 s, never at the
   competitor's cut points, the start or the ending. `--keep-silence` turns it off.
5. **laptop009 thorough is framed at 119.2 %** like your fix (148 % last night).
6. **Captions**: false starts are left out ("on the... on the subject" -> "on the subject"), and a competitor that
   writes one word at a time no longer breaks up the grouping (mike005).
7. **laptop010 and laptop013** are new test cases (section 1.7): their pauses taught the pause rule; their framing
   showed that a competitor which frames each shot is right, and that a competitor with one fixed crop needs each
   shot centred on its person -- a rule for that is written but off: tonight's face detection got 1 of your 5 moves.
8. **Wrong inputs stop at once** with a plain message (a movie as the competitor stops in about a second), and a
   competitor whose scenes are not in the RAW stops right after the visual search. `--force-inputs` skips both.
9. **Time left**: every 30 s the log says how far the stage is and how long the whole run still takes.
10. **The batch**: one RAW with several competitors, any file names, every pair checked before anything runs,
    progress and time left for the whole batch, the summary in plain words with each video's folder.
11. **check-all**: the new-style cases thorough and scored, the old ones `--fast` with the hard checks only
    (`--old-way` for everything as before).
12. **Also fixed**: a 1-frame flash `--remove-silence` could leave (rounding), the batch summary's reason for a stopped
    video (its plain message, not its whole log) and the batch's progress lines (they only said "running").

**Tests** (section 2): every unit test passes (1261, and 115 more on the GPU). One check-all on the final code
(48 min): **17 of 18 videos pass every hard check** (a valid Premiere XML, no flash, the sound in sync, no transition).
The one that does not is video020 run `--fast`: its `--fast` analysis takes 12.6 s of the RAW for another video, which
leaves a 3-frame flash -- last night's code does exactly the same in `--fast` (section 6, item 7). Against your fixes
nothing got worse in the edits: more of your cuts reproduced and every new-style edit closer to your length; the
captions the same or better except laptop009's first sentence, which the speech model heard differently on the
pause-cut sound (section 1.6).

**Disk** (section 5): 26.7 GiB free (24.4 GiB before the cleanup; 5.0 GiB freed in `work\`, the same safe way as last
night). Still under 40 GiB: 27.5 GiB more are stage caches no current code can use -- one command frees them, if you
agree.

## 1. What changed

### 1.1 laptop004's flash: a piece that starts on a RAW shot change keeps the competitor's timing

Night 5's take rule moved every piece that continues a take onto the take's line. laptop004's competitor plays one
take across the RAW's own shot change at 33.91 s and changes the framing there; moved onto the line, S03 started
27 ms early, so the framing of S01-S02 showed 2 frames of the next shot (`XML FLASH`). Now a piece that starts on one
of the RAW's own shot changes (within a RAW frame) keeps its own moment -- the competitor cut there because the
footage cuts -- and the piece before it ends exactly there (its length at 100 % follows), so the sound still runs on
without a jump and no framing shows a frame of the other shot. The shot changes are found on the competitor's cut list
before the takes are joined. laptop004 (`--fast`, as check-all now runs it): no flash, every Premiere check passes, the
cut list re-built from the caches is byte-identical (5 pieces start on a shot change at their own moment).

### 1.2 The take limit: 1.5 RAW frames (your answer B)

A piece joins the take before it when its RAW time is within 1.5 RAW frames (63 ms at 23.976 fps, 50 ms at 30 fps) and
0.1 s of the take's line -- 2.5 frames until tonight. `XML SYNC`'s "one take, cut" rule uses the same limit.

### 1.3 5.1 RAWs: a clean stereo copy for Premiere (your answer A)

When the RAW's sound has more than two channels, `1_edit.xml` plays `extras\media\raw_stereo.mp4`: the RAW's picture
copied as it is (stream copy: the same frames, every frame number in the XML stays valid) and one stereo track --
left = front left + centre x 0.707, right = front right + centre x 0.707 (the dialogue is in the centre channel), the
surround and LFE channels left out, so no echo. A constant gain is applied only if the mix would clip (peak above
-1 dBFS; laptop009's test RAW peaks at -12.5 dBFS: no gain). It is used on V1 and A1 (one master clip, the clips stay
linked; the picture is identical). The analysis, its caches and `cutlist.json` keep the RAW itself. If the copy fails,
the run uses the RAW as before and warns. (It costs disk space: a second copy of the RAW's picture in the run folder,
3.5 GB for a 2-hour movie.)

### 1.4 Pauses cut by default, carefully (your answer C)

What your fixes show (every quiet stretch inside the tool's takes in laptop009, mike005, laptop010 and laptop013, and
whether you cut it -- measured on the RAW's sound):

| quiet stretch | you cut it | you kept it |
|---|---|---|
| under 0.10 s | 2 | 9 |
| 0.10-0.15 s | 0 | 8 |
| 0.15-0.20 s | 2 | 2 |
| 0.20-0.30 s | 5 | 2 |
| 0.30 s or more | 15 | 1 |

- You never removed less than 0.117 s (7 frames).
- The quiet you **kept** was not silent: music under laptop013's 1.7 s and 1.4 s beats, mike005's 2.3 s pause, breaths.
  Measured as "90 % of the stretch is at most this far from the background noise up to the speech level", the pauses
  you cut sit at -0.46 ... +0.10 (two at 0.28 / 0.35 had a sound in them you cut with them), the ones you kept at
  0.16-0.40.
- Where you cut: right after the word before (a median 0.013 s after its loud part ends) and just before the next one
  (a median 0.026 s before it).

**The rule now (the default; `--keep-silence` turns it off, `--remove-silence` is the full silence removal as before):**

- only a real pause inside a take: at least 0.15 s between two words (the RAW's speech map, so never inside a word);
- only a clear silence: 90 % of it within 12 % of the way from the background noise up to the speech level (music, a
  breath or the room under it keeps it);
- the cut starts 0.05 s after the word before it and ends 0.03 s before the next word; a soft tail of more than 0.1 s
  after the word stops being loud goes with the pause (as in your laptop010 cuts; mike005's short tails stay -- cut,
  its cuts came 0.04-0.26 s before yours);
- only when at least 0.1 s goes: never a cut of a few frames;
- never at the competitor's cut points (a pause that holds or touches a V1 or A1 cut is kept whole), never at the
  edit's start, never the ending (`XML ENDING` unchanged).

The end summary lists every pause cut ("Silences: N removed, x s in all ...") with a settings line saying how many
quiet stretches there were, how many were cut and how many were not a clear silence.

**Against your fixes** (the final code; "before" is last night's check-all for laptop009 / mike005 -- no pause cut
then -- and your own laptop runs for laptop010 / laptop013; a cut is "reproduced" when both its sides are within 2
frames of yours):

| video | pauses cut | your cuts reproduced, before -> now | longer than your edit, before -> now |
|---|---|---|---|
| laptop009 | 4 (1.33 s) | 0 -> 2 of 13 | +2.17 s -> **+0.77 s** |
| mike005 (125 %) | 8 (4.12 s) | 0 -> 2 of 13 | +7.15 s -> **+4.12 s** |
| laptop010 | 6 (3.05 s) | 1 -> 1 of 9 (5 more within 0.5 s) | +4.33 s -> **+1.28 s** |
| laptop013 | 2 (1.33 s) | 0 -> 1 of 5 | +1.61 s -> **+0.30 s** |

Where a pause cut is not exactly yours, the clip before the pause ends a median 0.006 s (mike005) / 0.015 s
(laptop009) / 0.076 s (laptop010) after yours, and the clip after it starts within 0.02 s of yours. The rest of the
difference in length is your cuts at the competitor's cut points (trims at the start / end of takes, the ending),
which your rules keep yours (section 6). deadpool and zendaya (no cut key) had 1 short pause each cut (0.10 / 0.12 s).

How I got there (three rounds on laptop010 / laptop013 / mike005 / laptop009): the first rule cut every quiet stretch
of 0.15 s -- laptop013 came out 1.5 s **shorter** than your edit (it cut 3.1 s, music beats included); the clear-silence
test fixed that; trimming every word's soft tail then made laptop010's cuts better but mike005's worse (4 -> 0
reproduced), so only a tail of more than 0.1 s is trimmed.

### 1.5 laptop009's thorough framing (148 % instead of 119 %)

The thorough analysis re-measures the video box against the RAW on 16 confidently matched frames. On laptop009, 9 of
the 16 came from one RAW moment: an 8.5 s take that, seen through the small first-pass box (Cartman's face), looked
static and was matched to one RAW frame throughout (wrongly). That one picture -- with the competitor's captions at the
bottom -- decided every edge: the box came out 1017 px high instead of 1333 and the clips were framed at 148 %. Now,
when more than a quarter of the measured frames sit on one RAW moment that the competitor holds at least 3 times longer
than the RAW moves there (a still), that moment keeps only its best frame for the measurement. laptop009 thorough now
finds x 0, y 458, 1080 x 1333 -- the box your --fast runs find (119 % framing). The rule triggers on no other video
(checked on the frames every case measured last night, thorough and --fast). It sits in the pipeline, not in a cached
stage: no other video's caches changed.

### 1.6 Captions: grouped by what is said; false starts left out

**Grouped by what is said, not by a one-word competitor's pieces.** A competitor that captions about one word at a
time (mike005: 1.1 words a caption) used to push its breaks into the grouping as hints -- "fry | cook prison | guard
maybe | you're a | lighting guy" where you wrote "fry cook | prison guard | maybe you're | a lighting guy". Such a
competitor's caption changes are no hint any more: the words heard are grouped in your style (the grouping learned
from your SRTs: where you break between two words, the pause between them, your caption lengths), never across a
sentence end or a video cut.

I also tried grouping **every** competitor by what is said, never following its own caption pieces. It made the
scores much worse: deadpool 45 -> 11 of your 60 captions exactly, laptop009 24 -> 7 of 52 -- your keys for those were
made from the tool's follow captions, and you mostly kept them. So a competitor whose captions look like yours (mixed
case, 1.6-3.2 words) is still followed, as before.

**False starts left out, as you do.** A phrase of 2-3 words said again right after itself (within 0.6 s) loses its
first copy -- video2: "on the... on the subject" -> "on the subject", "from the the captain" -> "from the captain" --
and so does a short or weak word or an interjection said twice in a row within 0.3 s ("And and then" -> "And then";
mike005: "Yeah, yeah, success" -> "Yeah, success"). Kept, as your keys keep them: a repeat across a sentence end
("It's a lot. It's a lot of pig"), a word said three times or more ("nah | nah | nah"), a longer word said twice
("This | this is", "very, very lonely"). Each one is listed under *Caption stutters kept once*.

**Against your keys** (tonight's final check-all against last night's; "exactly" = your caption's words, split and
timing):

| video | your captions exactly, night 5 -> now | word errors, night 5 -> now |
|---|---|---|
| deadpool | 45 -> **45** of 60 | 4.8 -> **4.0 %** |
| mike005 (one word at a time) | 7 -> **9** of 46 | 8.7 -> 9.8 % |
| laptop009 | 24 -> **19** of 52 | 6.0 -> 7.5 % |
| video021 | 20 -> **21** of 40 | 14.9 -> **13.5 %** |

laptop009's 5 fewer are not the caption rules: the pause cuts changed the edit's sound, and the speech model heard
laptop009's first sentence differently. Your "referring to gay people as fags in today's world is acceptable?" was
"referring to gay people as as today's world acceptable?" last night and is "referring to fags is fags as today's is"
now. Its caption timing did not get worse: 18 timing differences both nights, and every caption now starts within
1 s of yours (up to 2.4 s off before the pause cuts). The model moves a few words whenever the sound under it
changes, either way: over the whole text it got better on mike005 (8.7 -> 6.5 % of the words wrong) and deadpool
(4.0 -> 2.4 %) and worse on laptop009 (3.7 -> 5.2 %). mike005's +2 is the one-word rule and the false starts.

### 1.7 What your laptop010 and laptop013 fixes taught (two new test cases)

Both projects point at the laptop's files (`D:\Shared\...`); I wrote relinked copies next to them
(`finished\laptop010\laptop010_fixed_relinked.prproj`, `finished\laptop013\laptop013_fixed_relinked.prproj`) that use
`output\laptop010\extras\media\raw.mp4` / `output\laptop013\extras\media\raw.mp4` (the same RAWs: same size, frame
rate and length -- no other RAW was needed for laptop013). Your originals are untouched. laptop013's project holds
three sequences; the third is the edit (the first two are template sequences of other videos). Both runs were
`--fast` laptop runs (RTX 4060 Laptop GPU) at 100 % with the default frame; both competitors play the RAW at 100 %.
In all four projects (laptop009, mike005, laptop010, laptop013) A1 sits exactly under V1: you moved no audio by hand
there, so your V1 cuts are your sound cuts too.

**The test cases** (`tests\real\laptop010-fixed`, `tests\real\laptop013-fixed`): the competitor, an 86 s / 73 s copy
of the RAW around what the edits play (RAW 418.1-504.4 s of Anchorman; 2484.5-2558.0 s of laptop013's movie), your
captions (34 / 22) and your picture clips (13 / 6) as the answer key, the default settings, `"style": "new"`.

**Cuts.** laptop010: you cut 8 pauses inside takes (0.12-0.97 s) and, at the competitor's cut points, trimmed the first
take's start by 0.42 s (dropping "With your life") and most take ends by 0.08-0.3 s. laptop013: one pause (0.38 s),
the tool's three slivers (0.05-0.42 s, laptop code from before night 5's take rule) removed, and the end shortened by
0.55 s of silence. The pauses taught the pause rule (1.4); the trims at the competitor's cut points and at the ending
go against your rule "the competitor's cut points stay / never shorten the ending", so I did not make them a rule
(section 6).

**Framing.** laptop010: 12 of your 13 clips are exactly the tool's (Scale 182.5-182.8 %, the same Positions); you moved
one by +328 px (RAW 460.9-463.1 s, a three-person shot: centred, the competitor's window cut both the boy and the
blonde in half; you centred the boy). laptop013: the competitor shows the whole video through one fixed centre crop
(23-77 % of the width, the same on every clip), your template shows 45 % of the width, and you moved 5 of 6 clips:
-361 px onto the man, +228 px onto the woman's close-ups (the first, wide shot stays centred). laptop009 and mike005:
every clip at the tool's position (mike005 zoomed 62 % instead of 57 % on every clip). So: **a competitor that frames
each shot itself is right; a competitor with one fixed crop chose no subject.** I wrote a rule for the second case
(centre each RAW shot of a fixed-crop competitor on the single person it shows; the clip keeps the competitor's cut
points and the XML's shot split gives each shot its framing) and tried it on laptop013: the faces found reproduced one
of your moves (the man: -276 px for your -361) but also moved your centred wide shot by +286 px, and the woman's
close-ups were not found at all. Half right is worse than nothing here, so the rule is **off** (`CENTRE_FIXED_CROP`);
laptop013 is framed as before, and the test case scores it. laptop010's single moved clip is not covered either (a rule
for it would have moved its other 12 clips too).

**Captions** (tool / yours): laptop010 38 / 34 captions, 10 / 7 of one word; mike005 52 / 46, 24 / 16 of one word;
laptop013 20 / 19. You group by phrase -- "when you grow | up?" -> "when you grow up?", "He's old | enough to hear
this" -> "He's old enough | to hear this", "please | stop" -> "please stop", "My | sister's been" -> "My sister's been"
--, one sentence per caption ("a child Ron!" -> "a child | Ron"), you drop false starts ("yeah | yeah yeah success" ->
"yeah | success") and keep deliberate repeats ("nah | nah | nah", "This | this is"). New in laptop010 / 013: you
censor words (p\*rn, se\*ual, perv\*rt, d\*ck) -- laptop009 writes "fag" and "gay" out, so I made no rule (section 6);
and action captions where nobody speaks ("\*akward silence\*", "\*thinks\*", "\*confused\*").

**In tonight's check-all** (thorough, the final code; both pass every hard check):

| | captions exactly | word errors | your cuts reproduced | longer than your edit |
|---|---|---|---|---|
| laptop010 | 10 of 34 | 10.3 % | 1 of 9 (5 more within 0.5 s) | +1.3 s (your laptop run: +4.3 s) |
| laptop013 | 9 of 22 | 26.7 % | 1 of 5 | +0.3 s (your laptop run: +1.6 s) |

laptop013's word errors are mostly the two things the tool does not do: 4 of its 7 word differences are your censored
words (perv\*rt, se\*ual, d\*ck) and your action captions (\*akward silence\*, \*thinks\*, \*confused\*) -- see
section 6. laptop010's are mostly the grouping (13 of its 24 differences are a different split: "when you grow |
up?", "maybe | you're a | lighting guy").

### 1.8 Wrong inputs stop at once (6c)

Right after the two files' lengths are read (ffprobe -- before anything is converted) the run stops with a plain
message when the competitor is longer than a Short (3 minutes), almost as long as the RAW (90 % or more: a copy of the
movie), longer than the RAW, the same video as the RAW, or when the two look swapped (the "competitor" long and
landscape, the "RAW" short and portrait -- before tonight such files were swapped silently; with `--force-inputs` they
still are). Example (the two files of laptop010 given the wrong way round; 1.4 s, nothing created):

```
match_cuts: The inputs look wrong, so I stopped before converting anything:
  - the two files look swapped: the competitor is the long landscape video and the RAW the short portrait one
  competitor: raw.mp4 (1:26, 1920x800, 39 MB)
  RAW:        competitor.mp4 (0:24, 720x1280, 5 MB)
What to do: give the competitor's Short (under 3 minutes, usually portrait) as the competitor and the full episode or
movie it was cut from as the RAW (--competitor / --raw, or the two files in the input folder). If these really are the
right files, run again with --force-inputs.
```

Your 109-minute copy of the movie as the competitor now stops in about a second ("the competitor is 1:49:00 long --
much longer than a Short ... is it the movie / episode itself?").

**Not in this RAW:** right after the visual search, when under 10 % of the competitor's frames were found in the RAW
**and** under 10 % of its sound, the run stops: "the competitor's scenes are not in this RAW: only 0 % of its frames
and 2 % of its sound were found in it, so I stopped instead of making an empty edit. Probably a different episode, a
different version of it (4:3 instead of widescreen, censored, dubbed) or another language ...". On a normal run the log
says how much was found ("found in the RAW: 100 % of the competitor's frames, 96 % of its sound"). The thresholds are
far from every test video in tonight's check-all (the fewest frames: video020 `--fast`, 36 %, with 88 % of its sound;
the least sound: zendaya, 66 %, with 78 % of its frames). `--force-inputs` skips both checks.

### 1.9 Time left (6d)

At the start the log estimates the whole run ("time estimate: the whole run about 12 min (NVIDIA GeForce RTX 5080,
thorough, competitor 24 s, RAW 1 min; from 3 earlier run(s) on this machine)"), and at most every 30 s a stage's
progress line ends with the stage's share done and the time left:

```
S5.2 visual search: search 2/3: 243/245 tasks done (43 s) | S5.2 visual search: 66 % -- under a minute left; whole run: about 12 min left
```

Also for the slow steps without a counter (probe + conform, the full-resolution re-check: from the time spent against
the estimate). The estimate: each stage's time per second of video (the RAW's length for conform, proxies and the
visual search's index; the competitor's for the rest) from the earlier runs on the same machine (its GPU: the laptop
or the PC) in the same mode (`work\run_times.jsonl`, one line per finished run; stages answered from the cache left
out), else built-in times measured on your laptop; within a run it corrects itself by how the finished stages went.
A stage made of phases that restart their counters (the refine: eval, measure, final) is estimated from its time.

### 1.10 The batch (6e)

* **One RAW, several competitors:** a subfolder may hold one RAW and any number of competitors cut from it; each
  competitor gets its own edit and run folder (`<subfolder>_<competitor name>`), and the RAW is analysed once: the
  runs share the stage caches, and a RAW the first run had to convert (`raw_ae.mp4`: a 5.1 AC3 track, an odd
  container) is taken from that run's media folder for the next competitors, so it is not converted again.
* **Any file names:** the RAW is the file called `raw.*` if there is one, else the longest video; every other video is
  a competitor (`.mp4`, `.mov`, `.mkv`, `.m4v`, `.webm`, `.avi`, `.ts` ...).
* **The wrong-input check on every pair before anything runs**, every problem listed at the start (the pairs with a
  problem are skipped, the rest run), so you can fix them before you leave.
* **A failed video never stops the batch** (as before; the reason goes into the summary).
* **Progress and time left for the whole batch** every 30 s: `video 2 of 5 (Anchorman): S5.2 visual search: 40 % --
  about 3 min left; whole run: about 6 min left -- whole batch: about 40 min left` (a stage without a counter: its
  name, `video 1 of 4 (Anchorman): S8 exports -- whole batch: about 12 min left`); the start of each video says the
  batch's time left too, and the estimate corrects itself by how the finished videos went.
* **The summary in plain words** (`batch_summary.md`): first "Where your edits are" -- one line per video: "done.
  Your files: `<run folder>` -- import 1_edit.xml and 2_captions.srt into Premiere", or "FAILED: <why>. Its folder:
  ..." --, then what did not run and why, a table, and per video what to check by hand; the console ends with the same
  list.

### 1.11 check-all: the new-style cases scored, the old ones --fast with the hard checks (7)

The default since tonight (`--old-way` runs everything as before): the cases made with your current rules (`case.json`
`"style": "new"`: mike005-fixed, laptop009-fixed, laptop010-fixed, laptop013-fixed, video021-fixed, deadpool, zendaya)
run thorough and are scored against your fixes (captions and cuts); the old ones (video1-5, video017/018/020-fixed,
spiderman-school, zendaya-age, laptop004-fixed) run `--fast` with the hard checks only (a valid Premiere XML, no flash,
the sound in sync, no transition, coverage, determinism; no caption or cut score -- the scorecard says "hard checks
only (old style)"). Every check-all run also gets `--quick-checks` -- the After Effects recreation checks (c2-c5,
9.9), which the scorecard never used, are not run (they took 5-10 minutes a video) -- and `--skip-compare` (no
compare.mp4, 0.5-1 minute a video). The `--compare-fast` second analysis is off in the new default too.

video021-fixed keeps its own options (`--remove-silence`, its frame.png): its answer key is an edit made with the
silences removed, so it stays like-for-like. The old cases keep theirs too -- all of them `--remove-silence` (the
default when their keys were made; laptop004 also `--speed 125` and its frame) --, so their hard checks also cover
`--remove-silence`.

### 1.12 Found by tonight's checks, fixed

* **`--remove-silence`: a clip that plays on into the RAW's next shot shows it a whole 0.25 s.** When a cut moved off
  a word runs a few frames into the next RAW shot, the rule shows that shot for 0.25 s (no flash), but the cut then
  lands on a whole frame, and 0.25 s from a shot change that lies between two frames was rounded down to 14 frames at
  60 fps -- video020 `--fast`: S02+S03 showed 14 frames of the next shot, finishing "I" (`XML FLASH`). It now aims one
  frame further, so it is at least 0.25 s on whole frames; if that lands in a word, the clip plays on to the word's
  end, as before. The default (the competitor's cut points) never moves a cut, so this was only ever in
  `--remove-silence` runs.
* **The batch's summary says in plain words why a video stopped.** The demo batch's "wrong episode" line held that
  run's whole console log from its first line; it now holds the run's own message ("the competitor's scenes are not
  in this RAW: only 0 % of its frames and 2 % of its sound were found ...") or, for a crash, its last line.
* **The batch's progress lines show the stage and the time left.** They said "running" every time: the batch read
  only the last 64 KB of the run's log, and the run's debug lines between two progress lines are far more than that.
  It reads 4 MB now; a progress line older than a minute is shown as the run's time left counted down since, and a
  run that logged none (a short run, a stage without a counter) shows the stage it is in ("S8 exports").

## 2. Test results

### Unit tests

On the final code: the whole suite with the GPU hidden **1261 passed, none failed** (95 skipped: they need the GPU;
7 min), and the GPU's own test files with the GPU (full-resolution check, GPU search, speech models, captions,
lazy frames, quality, visual match) **115 passed**.

30 tests are new (`tests\test_night6.py`): the shot-change rule (a piece on a RAW shot change keeps its own moment, the
take's sound runs on; a change elsewhere leaves the piece on the line), a 2-frame jump is a cut now, the stereo mix and
a stereo copy of a real 6-channel video, the pause cuts (a real pause cut with the gaps kept; under 0.15 s, a few
frames, at a cut point, at the start and at the ending: kept), the default switches (`--keep-silence`,
`--remove-silence`), the wrong-input check (a movie given as the competitor, swapped, longer than the RAW, the same
video -- and every test case passes it), the "not in this RAW" stop (and `--force-inputs`, a competitor whose sound is
there, one with 30 % of its frames found: all go on), the run clock, a batch folder with one RAW and two competitors
of any names (and a pair with the wrong lengths listed), the check-all split, seven false-start cases (and the
repeats your keys keep), one-word competitors, the fixed-crop framing rule (off by default; its pieces when on), a
clip played on into the next RAW shot (`--remove-silence`'s rounding: 14 frames before, 15 now), and the batch's
progress line far back in a run's log, a short run's stage and its plain stop messages.
Older tests changed with the rules: the take limit (1.5 frames), deadpool's S21 (S27 is 83 ms off S26's take: a cut
now), the CLI swap test (swapped files stop now; with `--force-inputs` they are swapped as before), the batch's skip
message, and check-all's options per case.

### check-all (the final code, one run in two streams)

The final code (frozen as `work\snapG` for the run), the new default split: the 7 new-style cases thorough and scored
against your fixes, the 11 old ones `--fast` with the hard checks only; two streams on the PC (RTX 5080), 08:29-09:17
(48 min). The "+/-" columns are against last night's check-all (night 5 ran everything thorough; the old cases have no
score now, and laptop010 / laptop013 are new cases). Scorecard: `work\t21\final2\final_scorecard.json`,
run folders `work\t21\final2\G1\runs` and `G2\runs`.

```
video                   time  hard checks  captions exactly       word errors      cuts reproduced      length
deadpool               3m10s  PASS         45/60 (75 %) +0        4.0 % -0.8       no cut key           -
laptop004-fixed        2m26s  PASS         hard checks only (old style, --fast)
laptop009-fixed       13m23s  PASS         19/52 (36 %) -5        7.5 % +1.5       2/13 (15 %) +2       +0.8 s (+3 %)
laptop010-fixed        1m34s  PASS         10/34 (29 %)           10.3 %           1/9 (11 %)           +1.3 s (+6 %)
laptop013-fixed        2m17s  PASS         9/22 (41 %)            26.7 %           1/5 (20 %)           +0.3 s (+2 %)
mike005-fixed          9m35s  PASS         9/46 (20 %) +2         9.8 % +1.1       2/13 (15 %) +2       +4.1 s (+23 %)
spiderman-school       4m32s  PASS         hard checks only (old style, --fast)
video017-fixed         2m26s  PASS         hard checks only (old style, --fast)
video018-fixed         6m22s  PASS         hard checks only (old style, --fast)
video020-fixed         3m44s  FAIL         hard checks only (old style, --fast) -- XML FLASH: 3 frames at 00:00:14:33
video021-fixed        18m30s  PASS         21/40 (52 %) +1        13.5 % -1.3      0/15 (0 %) +0        +7.0 s (+24 %)
video1                 6m37s  PASS         hard checks only (old style, --fast)
video2                 2m05s  PASS         hard checks only (old style, --fast)
video3                 2m51s  PASS         hard checks only (old style, --fast)
video4                 6m38s  PASS         hard checks only (old style, --fast)
video5                 1m45s  PASS         hard checks only (old style, --fast)
zendaya                2m00s  PASS         no caption key         -                no cut key           -
zendaya-age            1m48s  PASS         hard checks only (old style, --fast)
all caption keys                           113/254 (44 %)         9.9 %
all cut keys                                                                       6/55 (11 %)          +13.5 s (+13 %)
```

**Hard checks: 17 of 18 pass** -- every new-style case, and every old case but video020 (section 6, item 7: its
`--fast` analysis takes 12.6 s of the RAW for another video; last night's code gives the same `--fast` cut list, and
night 5 ran it thorough, where it passed). No rule breaks anywhere; the cut list re-built from the caches was identical
on every video (`--check-determinism`).

**Against night 5, nothing got worse in the edits:** every new-style video reproduces as many of your cuts or more
(laptop009 0 -> 2, mike005 0 -> 2) and comes out closer to your length (laptop009 +2.2 -> +0.8 s, mike005 +7.2 ->
+4.1 s; the old night-5 numbers are in section 1.4). Captions: deadpool and video021 the same or better, mike005 +2,
laptop009 -5 -- the speech model hearing its first sentence differently on the pause-cut sound, not the caption rules
(section 1.6).

One more check-all ran before it (07:06-07:57, the same code without the `--remove-silence` rounding fix of 1.12): the
same results except video020 (two flashes then: S02+S03's is the one fixed) and video021 / video1 (that fix made each
edit one frame longer; video021's captions then read 20 of 40, 14.9 %).

## 3. The batch: the exact command and the folder layout

The command (PowerShell or cmd; `--out` is where the run folders go):

```
cd C:\Users\gvida\Desktop\bh\MovieRecaps
.venv\Scripts\python -m match_cuts batch C:\Users\gvida\Desktop\todo --out C:\Users\gvida\Desktop\bh\MovieRecaps\output
```

(`--thorough` for full runs instead of `--fast`; options for every run after `--`, e.g. `-- --keep-silence`.)

The folder: one subfolder per RAW, any names:

```
C:\Users\gvida\Desktop\todo\
    South Park S13E12\                  one RAW, two competitors -> two edits
        South Park S13E12.mkv             the RAW: the longest video (or a file named raw.*)
        competitor1.mp4                   -> output\South Park S13E12_competitor1\
        tiktok clip 2.mov                 -> output\South Park S13E12_tiktok clip 2\
        speed.txt                         optional: "125" = every competitor of this folder at 125 %
    Anchorman\                          one RAW, one competitor -> output\Anchorman\
        Anchorman.2004.1080p.mp4
        short.mp4
```

What you see at the start and while it runs (from tonight's test batch in `work\t21\batchdemo`):

```
match_cuts batch: THESE VIDEOS WILL NOT RUN -- the inputs look wrong (fix them and start the batch again; the others run now):
  swapped by mistake:
    - the two files look swapped: the competitor is the long landscape video and the RAW the short portrait one
    - competitor competitor.mp4 (1:58, 1920x1080, 35 MB), RAW raw.mp4 (0:25, 1080x1920, 35 MB)
match_cuts batch: 4 video(s) to run, about 14 min in all:
  1. Anchorman: short.mp4 from Anchorman part.mp4 (about 5 min)
  2. Show 013_competitor1: competitor1.mp4 from episode.mp4 (about 3 min)
  3. Show 013_tiktok clip 2: tiktok clip 2.mov from episode.mp4 (about 3 min)
  4. wrong episode: clip.mp4 from Anchorman.mp4 (about 3 min)
[video 1 of 4] Anchorman -> work\t21\batchdemo\out\Anchorman (speed 100 %) -- whole batch: about 14 min left
  video 1 of 4 (Anchorman): S5.2 visual search -- whole batch: about 13 min left
  video 1 of 4 (Anchorman): S5.2 visual search (refined box) -- whole batch: about 13 min left
  video 1 of 4 (Anchorman): S5.3 refine (refined box) -- whole batch: about 12 min left
  video 1 of 4 (Anchorman): S8 exports -- whole batch: about 12 min left
  video 1 of 4 (Anchorman): S8 speed + frame -- whole batch: about 11 min left
    PASS in 2m57s
[video 2 of 4] Show 013_competitor1 -> work\t21\batchdemo\out\Show 013_competitor1 (speed 100 %) -- whole batch: about 5 min left
  ...
    PASS in 2m05s
[video 3 of 4] Show 013_tiktok clip 2 -> work\t21\batchdemo\out\Show 013_tiktok clip 2 (speed 100 %) -- whole batch: about 4 min left
    PASS in 48s
[video 4 of 4] wrong episode -> work\t21\batchdemo\out\wrong episode (speed 100 %) -- whole batch: about 2 min left
    FAIL in 13s: the run stopped (exit 2): the competitor's scenes are not in this RAW: only 0 % of its frames and 2 % of its sound were found in it, so I stopped instead of making an empty edit. Probably a different episode, a different version of it (4:3 instead of widescreen, censored, dubbed) or another language. Check that the RAW is the episode or movie the competitor was cut from (clip.mp4 / Anchorman.mp4); to make the edit anyway, run again with --force-inputs.
match_cuts batch: 3 of 4 passed in 6m02s; 1 not run (wrong inputs); summary: work\t21\batchdemo\out\batch_summary.md
  Anchorman: PASS -- work\t21\batchdemo\out\Anchorman
  Show 013_competitor1: PASS -- work\t21\batchdemo\out\Show 013_competitor1
  Show 013_tiktok clip 2: PASS -- work\t21\batchdemo\out\Show 013_tiktok clip 2
  wrong episode: FAIL -- work\t21\batchdemo\out\wrong episode
```

The test folder (hard links of test-case videos): `Anchorman\` (one RAW, one competitor), `Show 013\` (`episode.mp4`
with `competitor1.mp4` and `tiktok clip 2.mov`: one RAW analysed once, two edits), `swapped by mistake\` (a RAW and a
competitor the wrong way round: listed at the start, not run) and `wrong episode\` (a clip from another movie: stopped
after 13 s). In these short demo runs no stage takes long enough for a progress line of its own, so each line shows
the stage the run is in; in a long run it shows the stage's share and time left too ("S5.2 visual search: 40 % --
about 3 min left; whole run: about 6 min left -- whole batch: about 40 min left"). The first estimate was twice the
real time here (14 min for 6); after the first video it corrected itself. `batch_summary.md` starts with:

```
1. **Anchorman** -- done. Your files: `work\t21\batchdemo\out\Anchorman` -- import `1_edit.xml` and `2_captions.srt` into Premiere.
...
4. **wrong episode** -- FAILED: the run stopped (exit 2): the competitor's scenes are not in this RAW: only 0 % of its frames ...
## Not run
- swapped by mistake: the inputs look wrong -- the two files look swapped: the competitor is the long landscape video and the RAW the short portrait one
```

## 4. Check by hand

1. **The stereo copy** (a 5.1 RAW: laptop009 in tonight's check-all, `work\t21\final2\G1\runs\laptop009-fixed\001`,
   its `extras\media\raw_stereo.mp4`): import `1_edit.xml` into Premiere and listen: the dialogue clear, no echo, no surround music on top; A1 should show a
   stereo clip. If the dialogue is too quiet next to the music, tell me (the centre's share is 0.707 -- I can raise it).
2. **The pause cuts** (laptop010, laptop009, mike005 in tonight's check-all, `work\t21\final2\G1\runs\...` and
   `work\t21\final2\G2\runs\...`): play each cut the end summary lists under *Silences*: the pause gone, no word
   clipped at either side, no jump in the picture you would not have made.
3. **laptop013's framing** is as before (the fixed-crop rule is off): the man and the woman still half cut in some
   shots, as in the tool's earlier run -- yours to fix by hand, as you did.
4. **Captions** on mike005 (a one-word competitor: grouped from the speech now) and the false starts left out (the end
   summary lists them under *Caption stutters kept once*); and laptop009's first sentence (section 1.6).
5. **The batch** on your `todo` folder (section 3): the list of problems at the start, the progress lines, and
   `batch_summary.md`.
6. **A wrong input**: give a movie as the competitor once -- the run should stop within seconds with the message in
   section 1.8.

## 5. Disk space

**Freed: 5.0 GiB in `work\`**, the same safe way as last night: only what tonight left behind, every file counted
once however many hard links it has.

| | free on the drive |
|---|---|
| this morning, before tonight's runs | about 32 GiB |
| 08:40, before the cleanup (tonight's test runs and the first check-all) | 24.4 GiB |
| after the first cleanup | 27.3 GiB |
| now: after the final check-all, the batch demo and the second cleanup | **26.7 GiB** |

**Deleted** (all inside `work\`, none of it tracked by git): tonight's test runs (`work\t21\test1` ... `test11`,
`warm`, `l004`, the video020 runs), the layout-fix experiments (`work\t21\layoutfix`, 11 GiB listed but nearly all hard
links of the stage caches, which stay), the unit tests' temporary folders (`pt_*`), the first final check-all (the
final one replaced it) and tonight's frozen code copies (`work\snapBase`, `work\snapF`): 2.8 + 2.2 GiB freed of
18.5 GiB listed.

**Kept:** the final check-all's run folders (`work\t21\final2`: 1.2 GiB of their own -- for your checks in section 4),
the batch demo (`work\t21\batchdemo`: 1.1 GiB -- delete both once you have looked), the code the check-all ran
(`work\snapG`, 14 MB), the stage caches (new tonight: laptop010's and laptop013's, 0.6 GiB each), `output\` and
everything else.

**Still under 40 GiB** after the cleanup, so the check-all ran with 27 GiB free (it needs about 3). More space can only
come from the stage caches, and deleting those needs your OK:

**What I did not delete, and why you may want to:** `work\cache` (60 GiB) holds 27.5 GiB of stage-cache entries --
410 files, mostly RAW proxies (18 GiB) and RAW indexes (9 GiB) of the videos you ran on 1-4 October -- that no run of
today's code can ever use again. Every cache key holds a fingerprint of the code of the stage that made it, and every
stage's code includes `common.py`, which changed on 6 and 7 October (Task 10), after the search modules' changes of
5 October (Task 9); an entry written before 5 October was made by older code. (Last night's caution, "cache age is
not commit time", is about entries written shortly before a commit by code committed later -- hence a full day of
margin before the first of those changes.) I did not delete them: deleting cannot be undone and last night's safe way
keeps every stage cache. To free them (lists every file first; `--delete` removes them):

```
cd C:\Users\gvida\Desktop\bh\MovieRecaps
.venv\Scripts\python work\t21\prune_dead_caches.py            (shows the 410 files and the total)
.venv\Scripts\python work\t21\prune_dead_caches.py --delete   (deletes them: +27.5 GiB free)
```

The drive's free space fell from 58.4 GiB after last night to about 32 GiB this morning; inside `MovieRecaps` only
your two laptop RAWs are new (`output\laptop010` 3.5 GB, `output\laptop013` 3.9 GB) plus tonight's caches -- the rest
went to files outside `MovieRecaps`, which I did not look at.

## 6. Left for next time, and questions

1. **Censoring in captions?** In laptop010 / laptop013 you wrote p\*rn, se\*ual, perv\*rt, d\*ck; in laptop009 you
   wrote "fag" and "gay" out. Should the captions censor such words, and which ones? (Nothing changed tonight.)
2. **Trims at the competitor's cut points.** In laptop010 you trimmed the first take's start by 0.42 s and most take
   ends by 0.08-0.3 s; in laptop013 you shortened the end by 0.55 s of silence. Your rules say the competitor's cut
   points stay and the ending is never shortened, so I left them yours. If you want them, tell me which: the pause at
   a take's start / end (as `--remove-silence` does, but only the silence), or the ending's silence too.
3. **laptop010's three-person shot** (you centred the boy, +328 px): no rule covers it -- one clip of 13, and a rule
   for it would have moved the other 12 you kept.
4. **mike005's zoom**: you zoomed every clip to 62 % where the tool used 57.3 % (the same position). If that is how you
   want 4K RAWs (a little tighter than the competitor), tell me.
5. **The caption grouping itself** (your style model) still splits some phrases you keep together ("please | stop",
   "Is | there | a black"); tuning it needs an offline harness over every answer key -- next time.
6. **The dead caches** (section 5): 27.5 GiB you can free yourself with one command, if you agree.
7. **video020 `--fast` still fails `XML FLASH`** (check-all now runs it `--fast`; last night's thorough run passed).
   The `--fast` analysis finds only 36 % of video020's competitor's frames in the RAW (but 88 % of its sound;
   thorough: 77 % of the frames): it takes three stretches (12.6 s in all; thorough leaves 4.2 s) for another video
   (`OTHER VIDEO` markers) where your finished edit plays the RAW. After the third stretch the competitor's last
   0.15 s of a shot is left alone -- a flash -- and another video's stretch must stay empty, so no export rule can fix
   it: the run fails right where the edit needs your hand anyway.
   Last night's code gives exactly the same `--fast` cut list. The fix belongs in the `--fast` search, a cached stage
   (changing it means hours of re-analysis for every video) -- next time. Until then, a video like this one is better
   run thorough.
8. **The captions move when the sound moves.** The speech model hears a few words differently whenever the edit's
   sound changes (laptop009's first sentence tonight). Transcribing the edit as it was before its pauses were cut
   (and moving the words' times by the cuts) would make the captions the same with or without pause cuts -- an idea
   for next time, not needed for correctness.
