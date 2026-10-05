# Task 8: learning from your finished videos

## The four folders, by their content

| folder | your video | competitor | RAW | captions used | what happened |
|---|---|---|---|---|---|
| video1 | 55.1 s, 30 fps, the tweet-card template | 56.4 s, 60 fps | 23.5 min, 700x480, 25 fps | the project's 111 caption graphics (all show on `final.mp4`'s screen) | learned: 29 cuts (you kept 19 of the competitor's 24); your sound 35 ms late; the tool reproduces 6 exactly and 11 near |
| video2 | 10.1 s, 30 fps | 11.7 s, 24 fps | 24.4 min, 624x480, 25 fps | none: the project's 67 captions are another video's | learned: 1 cut (you kept 1 of the competitor's 18); your sound 35 ms after your picture (taken out); the tool's edit 0.6 s shorter (you open on "Look," before the competitor's start and play "OK?" and "word on" to their ends) |
| video3 | 32.8 s, 30 fps | 41.8 s, 30 fps | 24.2 min, 640x480, 25 fps | none: the same 67 captions as video2's project | learned: 11 cuts, none of them the competitor's 18; your sound 38 ms late; you play 14.4 s of the RAW the competitor dropped; framing x1.41 (zoomed in) |
| video4 | 20.0 s, 60 fps | 18.0 s, 60 fps | 12.7 min, 1280x720, 59.94 fps | read from `final.mp4`'s screen (as you asked) | learned from `final.mp4` only: 7 cuts (12 in the picture -- the other 5 are cutaways and Topaz/AE shifts over continuing sound); 38 captions from the screen; you kept 3 of the competitor's 27 cuts |

- **Your edit is in After Effects in all four projects.** V1 is a linked AE comp, then a PNG overlay, the caption
  graphics, and one replaced sound file on A1 (the Adobe Podcast audio). No project holds clips of the RAW, so your
  cuts and framing could only come from `final.mp4`: `learn` matches it against `raw.mp4` frame by frame, the way the
  tool matches a competitor.
- **video2's and video3's projects hold another video's captions**: the same 67 caption graphics in both ("Excuse
  me.", "Did you phone us?", "the breakdown service" ...), none of them on either video's screen (video2's starts
  "Look", video3's "selling me dodgy"). I did not guess: those two cases have a cut answer key and no caption key.
  If you want, their captions can be read from the screen like video4's (`learn --final-only video2 video3`).
- **video1's project captions are this video's**: all 111 caption graphics show on `final.mp4`'s screen (113 read
  there: two of them split on screen), so they are its caption key.
- **video4**: the project, the AE project and the Auto-Save folder were left out, as you asked; the captions are read
  from `final.mp4`'s screen.

### What `topaz.mp4` is

Your edited picture, not footage: the After Effects comp's output (the RAW placed on a black 9:16 canvas, the
framing you chose), upscaled 2x to 2160x3840, with no sound. It is the length of `final.mp4` within 0.05 s and cuts
where it cuts. `final.mp4` shows a crop of it inside the template's rounded box, under the logo, title, captions and
watermark. So `learn` never matches anything against `topaz.mp4`; it reports what it is (2160x3840, no sound; it cuts
where `final.mp4` cuts for 81 % of video1's cuts, 100 % of video2's and video3's, 67 % of video4's -- the ones it
misses are the subtle ones: an 11-frame insert of the same take, a 2-frame shift).

What differs, and how it is kept out of your cuts and framing:
- **Timing:** a piece of `final.mp4`'s picture can sit a frame or two off its own sound (video4: two pieces, 0.9
  and 1.4 RAW frames; the upscale / frame-rate conversion, or After Effects). The key places each piece by its sound,
  so such a shift is never a cut of yours (video4's 12 picture cuts were 7 cuts).
- **Framing:** the framing `learn` reports is what your final video shows of the RAW (the comp's framing and the
  template's crop together), against the competitor's -- what you see on screen, which is what the tool has to make.
- **Resolution / sharpening:** not compared (the tool's match is made on the RAW and `final.mp4`).

## What `learn` does now

`python -m match_cuts learn finished` runs on every folder of `finished\` (or one folder):

1. **Checks the files** (`final.mp4`, `competitor.mp4`, `raw.mp4`, `project.prproj`; with `--final-only` no project)
   and skips a folder that misses one, saying which.
2. **Makes the test case's videos first** (`tests/real/<folder>/`: the competitor, the RAW smaller when over 100 MB,
   every frame and timestamp kept).
3. **Two runs of the tool**: on the case's competitor + RAW (the files check-all runs, with check-all's work folder:
   check-all then reuses everything), and on `final.mp4` + the original `raw.mp4` (your edit). A finished run of the
   same files is used again; `--fresh` makes new ones.
4. **Checks the files belong together, by their content**: `final.mp4` and `competitor.mp4` each show the RAW at
   least half their time and play the same part of it (30 % or more of yours). No name inside a project is used.
5. **Your captions**: the project's caption graphics when at least 60 % of them show on `final.mp4`'s screen within
   0.25 s (read the way the tool reads a competitor's captions, from the row of text that changes most); else none,
   and the summary says why. With `--final-only` (video4) they are read from the screen.
6. **Reads your edit the way you cut it** (`learn.sound_edit`). Your picture is matched frame by frame, and then:
   - your video's own A/V offset is taken out (video4's sound runs 24 ms after its picture, video2's 35 ms: the
     render / the replaced Adobe Podcast file), so the key is on the RAW's clock, like an edit;
   - a piece whose picture is a frame or two off its own sound (Topaz / After Effects) is placed by its sound, so
     such a shift is never counted as a cut of yours (video4: two pieces, 0.9 and 1.4 RAW frames);
   - where your picture shows something else over the RAW's continuing sound (a cutaway, an insert, a picture the
     match could not follow), the key plays the RAW of that sound -- only where the tool's own cutaway check proves
     it (the audio there correlates >= 0.8 with the RAW within 10 ms). video4: its first 0.43 s, an 11-frame insert,
     and its last 2.4 s ("trying not to stare at each others crotches") are your RAW's sound under other pictures.
   So video4's 12 picture cuts are your 7 real cuts.
7. **Compares** your edit with the tool's (the cut score below) and with the competitor's (the cuts of its you kept),
   the three lengths, the RAW only one of them plays, how you trim the cuts you both make (your RAW moment minus the
   tool's: this is where "you end clips earlier" is measured now -- your finished video is not an edit of the tool's
   clips, so matching clips one by one, as for a project, compared two different edits), your framing against the
   competitor's, and your caption words (the glossary, as before; a spoken form against its written one, "gonna" /
   "going to", is your style per video and stays out of the glossary).
8. **Writes the case**: `answer_edit.json` (your timeline: the cut answer key, and what `answer.srt` is timed on),
   `answer.srt` when your captions could be read, `case.json`, `learned.json`.

## The cut score (check-all)

How many of your cut points the tool's edit reproduces: the RAW moment your picture leaves and the one it cuts to,
both within 2 frames of your video (compared by the RAW, so the two edits may run at other times and in another
order), plus the ones it makes trimmed otherwise (within 0.5 s), and how much longer or shorter the tool's edit is.
It runs on every case whose answer key is your own edit: the four finished videos, Spider-Man (your project's A1)
and Zendaya-age (your finished XML). Deadpool's key is the competitor's timeline, so it has none.

The scorecard now has two more columns, *cuts reproduced* ("6/29 (21 %)") and *length* ("+4.0 s (+7 %)"), and a
total row for all cut keys; a case whose key is not your own edit says "no cut key" (the trimmed-otherwise count is
in `scorecard.json`). The numbers are in *Scores before and after* below. Your cuts reproduced exactly are few by
nature: you keep few of the competitor's cuts (1 of 18, 0 of 18, 3 of 27 on video2-4), and the tool follows the
competitor.

## Tuning (item 4)

How I measured: every finished video's run was saved just before the Premiere export, and the export replayed with
other settings (the analysis is the same; only silence removal, padding and cut placement change), each replay
scored against your edit -- your cuts reproduced, how you trim the cuts you both make, the length -- and checked for
a cut inside a word. Spider-Man was not tuned on (as you asked; its run is the hard checks in check-all).

**Where the tool and you cut the same place, you leave earlier.** At every cut you both make, your out point minus
the tool's (median): video2 -0.09 s, video4 -0.11 s, Zendaya-age -0.12 s (Spider-Man, for the record: -0.07 s) --
the "you end clips about 0.19 s earlier" of Task 6, measured where it means something. Your in points are where the
tool's are on these three (+0.00 / +0.00 / +0.03 s; video1 and video3: decision 13). The tool keeps `--pad-after`
0.15 s after the last word; you keep about 0.05.

`--pad-after` replayed on each saved run (the same analysis, the export redone); "exact" = your cuts reproduced
within 2 frames, "out" = your out point minus the tool's at the cuts you share (median):

| `--pad-after` | video2 exact / out | video3 exact / out | video4 exact / out | Zendaya-age exact / out | cut inside a word |
|---|---|---|---|---|---|
| 0.15 (before) | 0/1, -0.09 s | 0/11, -0.08 s | 0/7, -0.11 s | 0/9, -0.12 s | none |
| 0.10 | 1/1, -0.04 s | 0/11, -0.03 s | 0/7, -0.06 s | 0/9, -0.07 s | none |
| **0.05 (now)** | **1/1, -0.00 s** | 0/11, +0.02 s | **2/7, -0.02 s** | **1/9, -0.02 s** | none |
| 0.03 | 1/1, +0.02 s | 0/11, +0.03 s | 3/7, +0.00 s | 1/9, 0.00 s | none |

video1, your most typical video (29 cuts, 19 of them the competitor's): at 0.15 s 1 of your cuts exact, at 0.05 s 6
exact and 11 near, and at the 17 cuts the final tool shares with you your out points are +0.01 s from its own -- the
same answer from the video that follows the competitor most.

**The tighter cuts cost no captions overall.** The captions are the transcript of the edit's own audio, so a
tighter edit is transcribed and timed a little differently: with 0.05 s against 0.15 s, video4 has 8 of 38 captions
exactly instead of 4, video1 6 of 111 instead of 8, Deadpool 47 of 60 instead of 48, Zendaya-age 1 of 19 instead of
2 -- 62 of 228 either way (the same edits replayed, the glossary fix in place).

The lengths barely move with the pads (a few tenths of a second): what is left of each length difference is which
parts of the RAW you and the competitor chose (*What it cannot*, below). The other settings tried:
`--pad-before` 0.03 / 0 moves your in points away on video2, video4 and Zendaya-age (they match at 0.05);
`--min-silence` 0.25 / 0.2 / 0.15 changes
no cut point and at most 0.25 s of length (Zendaya-age at 0.15; nothing on video2, 0.08 s on video4); counting a
pause with a breath in it (`silence_breaths`) -- see *Decisions*.

## Item 5

### The Spider-Man `answer.srt`: kept as it is

There is no uncommitted change to it in the repository (`git status` is clean there). The change you mean is what
`learn` writes when run on `reference/dsafxcv.prproj`: that project's 63 caption graphics in place of the 64
captions of your finished SRT (`srt/spiderman-undercover-school.srt`, the key since Task 4). The difference, caption
by caption (time in seconds):

```
       committed key (your SRT)            learn (the project's graphics)
4.817  and it was                          And it was
5.733  a joke            (5.733-6.100)     --  (no caption: a gap where "a joke" is said)
21.133 and be like                         and BE LIKE
28.400 what's your deal  (to 29.200)       what's your deal (to 29.133)
29.200 man?                                man?  (from 29.133)
```

I kept the SRT. The project has no caption where the speech says "a joke" (5.73-6.10 s; "And it was completely a
joke. And Marvel took it completely seriously"), which breaks your own rule that captions run back to back; its
"a joke" graphic exists only at 0.27 s ("So as a joke"). The other three are a capital, emphasis capitals and a
4-frame boundary, where the SRT you gave me as your final captions decides.

### Zendaya-age's 25 → 30 fps cadence (S03, S19)

**Fixed: check 9.9 on Zendaya-age went from FAIL (16 failures) to PASS with exceptions (none; 51 frames explained
by the repeat cadence, 2 a neighbour fits better by under 0.01).** What I found when I looked at the full-resolution
evidence was not what I expected in Task 5:

- **The phase is now solved from full-resolution evidence** (`segment.py`, `_full_res_phase`): where the proxy's
  phase solve leaves a choice -- a tie on a breakpoint, or a range of phases that show other RAW frames -- each frame's
  candidate RAW frames are scored at full resolution, their framing refined, and the phase is solved again with
  those scores. It moved two clips to the better phase (S04: +0.138 ZNCC over its 31 frames; S14: +0.060 over 53),
  and left the clips where full resolution does not clearly prefer another one (gain under 0.01).
- **S03 and S19 were already at the best phase.** Zendaya-age's RAW is a 30 fps file of 25 fps footage: it shows
  every 6th picture twice (RAW 284/285, 290/291 ... measured at full resolution: 1 - ZNCC 2e-5 to 1e-4 against 0.03
  to 0.05 between other frames). The competitor's copy repeats on other frames (58/59, 64/65 ...). Two cadences
  that far apart cannot both be followed by one constant-speed clip: with the delivered phase 2 frames in 6 show the
  RAW frame next to the competitor's; with the other phase, 3 in 6. Full resolution confirms the delivered phase
  (the new phase solve keeps it).
- **The check now recognises this cadence** (`fullres.cadence` / `verify`): the time line showing two RAW frames the
  RAW file itself repeats where the competitor moves on is the same cadence as the time line showing one RAW frame
  twice. And where refine had not labelled a pair of competitor frames (it had not, on all of S03), the check
  measures it at full resolution. A repeat counts only when the two frames differ by under 1e-3 *and* by under a
  fifth of the change on each side of them (a still shot, where every pair differs little, has no repeats).

Playing every frame of S03 and S19 exactly would need the RAW as the 25 fps footage it holds (its repeated frames
taken out, a new file for Premiere). I did not do that: the frames are one picture (40 ms) apart, it only happens with
a RAW like this one, and it would change the file you edit with.

## What the finished videos found wrong in the tool (fixed)

video4's competitor is the hardest video so far: the RAW full-screen throughout, cutaways to other moments of the
RAW over the continuing speech, a "rewind" effect, flashes, added music, and its sound 58 ms after its picture. Its
first run (the tool as it was) failed coverage, the speech check (two cuts inside words) and the flash check, and
its edit was 3.5 s longer than yours. Each fix below has a unit test made from the case; the replays of video4 from
its saved analysis went from 3.5 s too long with 2-3 export problems to 0.7 s shorter than yours with none (the
final run: 1.2 s shorter).

1. **A competitor's A/V offset was rejected by too strict a rule.** One segment may not move the offset's estimate
   by more than 2 ms. video4's nine segments all put the sound ~58 ms late, and one moved it by 3.1 ms, so the
   offset was set to 0: then no segment's sound followed its picture within 10 ms, nothing was an anchor, and the
   cutaways could not be checked -- the tool played the competitor's cutaways (RAW 254-257 s, 263.7-265.6 s) with
   their own sound, 4.6 s of speech you never play. Now a segment may move it by a quarter of a RAW frame when that
   is more than 2 ms (the segments' intervals are their RAW frame's phase): 4.2 ms at 59.94 fps
   (`audio_align.solve_av_offset`). Only video4 (and your video4 final) change: Deadpool's and Spider-Man's offsets
   were accepted already, Zendaya / Zendaya-age's fail on coverage and segment count.
2. **Cutaways** (`broll.py`): a piece too short to hear between two cutaways replaced by the same line follows that
   line (7 frames of another moment inside S10's speech were a blip of other speech); a main-clip shot whose own
   sound is a little off its picture (22 ms: too far for an anchor) is never replaced by the clip before playing on
   (17 frames of the wrong moment); a RAW piece shorter than a shot can be (0.25 s) whose sound cannot be measured
   is no longer left as it is -- it would be a flash frame, a hard failure -- the clip before plays on over it.
3. **A picture glitch inside one take** (`export_xml_edl.play_on_slivers`): 6 frames 81 ms ahead of the take in
   the middle of "each other's Spidey". Its sound already played on, but the picture jumped ahead and back, and the
   repeat it showed was cut out -- inside the word. A piece of at most 0.25 s inside one take, off the take's line by
   at most 0.1 s (speech.py's own limit for playing on), now plays on the line, picture too. And a sliver played on
   the line is now placed with the take's frame-exact interval: it kept its own, and the export clamped it back to
   where it was (a hole in the 24 fps sliver fix too). And a sliver whose sound in the competitor follows a line
   of its own, off the take's, is left as the competitor has it: the final check-all caught Zendaya's S19 (6 frames
   at 120 %, its sound 91 ms behind the take) -- played on, its picture no longer cut there, and its sound line
   could only be shifted onto the take's by whole frames, which left a 20 ms jump inside "you know what's funny".
4. **The end of a word heard as a breath** (`speech.py`, video2): "team, OK?" is timed to end before the "-kay"
   its speaker says, which is voiced only 0.04 s (under the 0.05 s that makes an untranscribed sound speech): the
   tool cut there, inside "OK?", and its own speech check could not see it. A sound starting at most 0.08 s after a
   word, voiced at least 0.02 s, is now that word's end, one sound with it (a breath is not voiced at all). In
   video2 the tool now plays "OK?" to its end like you.
5. **The coverage check failed a video that is full-screen throughout** (`verify.check_coverage`): it wanted every
   clip of a full-screen period to carry its own box, but when the whole video is full-screen that box is the
   layout's own (the segments stage gives no clip a box of its own then).
6. **A memory leak crashed video1's runs** (`fullres.LazyFrames`): the full-resolution frames the segments stage
   reads on demand each opened a new decoder, which starts and ends its frame threads; in a process that has loaded
   CUDA every such decoder kept ~70 MB for good (measured in isolation: no leak without CUDA, none when the frames
   are read once). The full-resolution phase solve (*Item 5*, above) reads many scattered frames: on video1's finished
   video (1,654 frames) the stage reached 92 GB and the runs died (`MemoryError`). Now one decoder serves every read
   of a video, seeking: the same stage peaks at 5.9 GB and takes 6.4 minutes.
7. **video1's competitor was mostly "uncertain"** (43 of its 56 s, though its best RAW frames matched at ZNCC 0.99):
   it is 60 fps over a 25 fps RAW, so at 100 % every RAW frame shows 2 or 3 times, and its slow zoom keeps the
   picture moving; the rule that marks a held still where the competitor moves (FX-08) counted 3 frames on one RAW
   frame as a hold. A hold now has to last longer than one RAW frame lasts by itself (`segment.hold_frames`: 4 at
   60 / 25 fps; 3, as before, when the rates are close). Afterwards: no uncertain stretch left in video1.
8. **Cuts inside words at cross dissolves** (video1's competitor cuts with 2-frame dissolves inside "tippex tippex"
   and "breaking even"): a dissolve is locked in place and its sound may only slide inside its own 2 frames. A
   dissolve of at most 0.1 s whose cut is inside speech now becomes a hard cut the speech rule can move
   (`export_xml_edl.harden_dissolves`; 33 ms of two pictures mixed are not missed).
9. **Two export checks judged differently from the plan they check** (video1): the framing rule (--min-move) was
   judged on the final clips, after the silence removal had moved their edges and joined pieces (a clip played on
   0.28 s into the next shot of the RAW; 5 frames framed for their own person joined with the next clip) -- a change
   is now also accepted when the plan made it for a reason that holds on the plan's own clips; and the silence
   check allowed an edge held 0.25 s from a RAW shot change only within 1.5 sequence frames, while the hold lands on
   the RAW's own frames (0.28 s with a 25 fps RAW) -- it now allows that one RAW frame.
10. **The glossary lowered every video's captions** (found by the first final check-all: Deadpool 48 -> 42 of 60
    exactly). `learn` writes your corrections to `caption_glossary.txt`, and their written words went to the speech
    model as hot words. A hot word changes how the whole transcript is punctuated and capitalised, also in a video
    that never says it: Deadpool says neither "others" nor "Tobey", yet with them it lost question marks and
    capitals ("do this?" -> "do this", "He's playing" -> "he's playing") and heard "cuz" for "because". Which word
    does what cannot be foreseen (Deadpool with "Tobey" alone: 42 of 60; "others" alone: 48; "Tobey" and "Stacey":
    47). Now only `caption_allowlist.txt` goes to the model, as in Task 7, and the glossary is applied after the
    transcription where the audio fits, as it already was (`captions.caption_hints`). Measured on every caption key
    (the same edits, only the hot words differ; the glossary's four entries):

    | captions exactly, word errors | glossary as hot words | glossary after the transcription (now) |
    |---|---|---|
    | Deadpool | 47/60, 4.8 % | 47/60, 2.4 % |
    | video1 | 6/111, 12.3 % | 6/111, 11.1 % |
    | video4 | 8/38, 18.4 % | 8/38, 14.9 % |
    | Zendaya-age | 1/19, 30.0 % | 1/19, 25.0 % |

## Two more bugs the finished videos found

1. **A 24 fps competitor made no `1_edit.xml` at all** (video2: 24.003 fps, conformed to 24). The Premiere sequence
   was always 60 fps, and the export needs a whole number of sequence frames per competitor frame (60 / 24 = 2.5):
   the speech map, the shot changes, the people and the silence removal all failed, and so did the XML. Now a
   competitor whose rate does not divide 60 gets the whole multiple of its rate nearest to 60 (24 fps: 48; 25 fps:
   50), and the run says so (`export_xml_edl.sequence_fps`). 30 and 60 fps competitors keep 60.
2. **A cut inside a word around a 4-frame slow-motion sliver** (video2, "definitely"): the competitor shows 4
   frames at 50 % (a stall of its 24 fps conversion) while the take runs on under them. The Premiere export kept the
   slow sliver with its sound slowed, and the cut after it landed inside the word (the hard speech check failed).
   Now a speed change of at most 0.25 s inside one continuous take plays at 100 % in the Premiere edit -- on the
   take's line when the clip after continues the clip before's -- so the take runs on and nothing is slowed inside
   a word (`export_xml_edl.play_on_slivers`; the faithful `cutlist.json` keeps the speed change). Your finished
   video2 plays that take at 100 % too.

## Scores before and after

Before: Task 7's runs (Deadpool, Spider-Man, Zendaya-age) and the first run of each finished video with the tool as
it was at the start of this task. Now: the final check-all (`work/t8/final4/`). Both scored against today's keys.

| video | hard checks | captions exactly | word errors | your cuts reproduced (+ trimmed otherwise) | length against yours |
|---|---|---|---|---|---|
| Deadpool | PASS | 48/60 -> 47/60 | 3.2 -> 2.4 % | (its key is the competitor's) | - |
| Spider-Man | PASS | 11/64 -> 10/64 | 6.1 -> 8.2 % | 3/21 (+7) -> 2/21 (+10) | +5.8 -> +6.7 s |
| video1 | PASS | new key: 7/111 | 11.6 % | 43 of its 56 s uncertain -> 6/29 (+11) | +4.0 s |
| video2 | PASS | (no caption key) | - | no `1_edit.xml` (24 fps) -> 1/1 | +0.1 s |
| video3 | PASS | (no caption key) | - | 0/11 (+1) -> 0/11 (+1) | -4.8 -> -5.6 s |
| video4 | PASS | 3/38 -> 8/38 | 24.7 -> 14.9 % | 0/7 (+2) -> 2/7 (+2) | +3.5 -> -1.2 s |
| Zendaya | PASS | (no keys) | - | - | - |
| Zendaya-age | PASS | 2/19 -> 1/19 | 25.0 -> 27.5 % | 0/9 (+3) -> 1/9 (+2) | +4.3 -> +3.7 s |
| all keys | | 73/292 (25 %) | 10.5 % | 12/78 (15 %) | +7.6 s (+5 %) |

- **Cuts are closer to yours on every finished video**: video2 from no edit at all to your one cut exactly, video4
  from 3.5 s too long with no cut of yours to 2 exact and 1.2 s short, video1 from unusable to 6 of your cuts exactly
  and 11 trimmed otherwise; Zendaya-age 1 exact and 0.6 s closer. video3's edit is 0.8 s shorter than before (the
  tighter cuts): you play 14.4 s of the RAW there that the competitor dropped, so its length is your choice, not a
  setting.
- **Spider-Man** (not tuned on): two of the competitor's shots, "I was like, well" (0.5 s) and its laugh at the end
  (0.7 s), were taken for cutaways in Task 7 and replaced by the clip before. They are the interview's own shots
  with their sound a little off their picture (fix 2), so the tool now plays them as the competitor does; you cut
  both. Its 3 extra word errors are those shots' words, which your captions don't have.
- **Captions**: the tighter cuts cost none overall (62 of 228 either way on the four caption keys, *Tuning* above).
  video4's 3 -> 8 is the A/V offset fix (its captions had been 3-6 frames late) and the tighter cuts; Deadpool,
  Spider-Man and Zendaya-age lose one each, because a tighter edit is transcribed and timed a little differently.
- **video1's captions** (a new key, 111 captions): 7 exactly. Your captions there run about two words, as the
  tool's do, but split at other places (50 of the differences).

## What the tool now does on its own, and what it cannot

**On its own now:**
- Leaves a clip where you do at the cuts it shares with your edits: 0.05 s after the last word (your out points
  within 0.02 s on every finished video), starting 0.05 s before the next (as before).
- Plays the end of a word the transcript cut short ("OK?" to its end).
- Recognises a competitor's cutaways over continuing speech when the competitor's sound is offset from its picture,
  plays a picture glitch inside a take on the take's line, and leaves no flash frame or blip of other speech at the
  end of a cutaway sequence (video4).
- Reads a finished video the way you made it (`learn <folder>`): your cuts by picture, placed by their sound,
  cutaways given their sound, Topaz/After Effects frame shifts and your render's A/V offset left out; your captions
  from the project when they are this video's, else from the screen with `--final-only`; the case for check-all.

**Not on its own** (your choices, not rules the videos share -- the tool follows the competitor's edit):
- **Which of the competitor's cuts you keep, and what you add or drop.** You kept 1 of video2's 18 competitor cuts,
  0 of video3's 18, 3 of video4's 27: your edits are your own cut of the same stretch of the RAW. video3 plays 12.9 s
  of speech the competitor cut (a 5.4 s passage at 363.5 s); video2 opens on "Look," before the competitor's start;
  video4 drops two of the competitor's clips (1.8 s and 1.5 s); Zendaya-age drops "And", "couple", "son to" ...
  These are most of the remaining length differences, and no setting reproduces them.
- **Your framing** (zoomed in x1.41 against the competitor on video3, out x0.80-0.90 on video2 / video4) and your
  picture inserts (video4's 11-frame flash-forward at 1.17 s): the tool frames like the competitor in your window.
- **Your caption splits and spoken / written forms per video** ("going to" in video4, "gonna" in Deadpool's key).
  Your captions run about two words each, as the tool's do, but you split them where you like ("banknote forging |
  gang in" where the tool writes "banknote | forging gang" in video1), and in video4 you joined the competitor's
  shortest ones (38 captions; the tool, following the competitor, writes 46). Most of the caption differences in
  the scores are these splits.
- **A name you spell differently from the speech models** ("Tobey", which they write "Toby"): the glossary is
  applied only where the audio favours the written form (at most 1 nat less likely, Task 6's rule), and the audio
  cannot tell two spellings of one sound apart -- the models prefer "Toby" by 4.9 nats, so video4's entry never
  applies. I left that rule: the entries were learned from these same videos (applying them would only raise video4's
  and video1's own scores), and `other's -> others` would write that spelling into every video.

## Test results

- **Unit suite** (`pytest -m "not slow"`): 1,076 passed, 12 skipped (fork / `/proc` are Linux-only, OpenTimelineIO has
  no wheel for Python 3.14, two plan hashes need Linux's fonts), none failed -- on the final code. New tests, each
  made from what a finished video showed:
  `test_edit_score.py` (the cut score: cuts by the RAW, reproduced / near, the trims at shared cuts),
  `test_learn.py` (finished folders: belonging by content, the captions only when they are this video's, the caption
  row that changes most, a spoken form is style not glossary, ordinary words heard wrong are not glossary, the
  glossary never goes to the speech model, your edit placed by its sound), `test_av_offset.py`
  (video4's offset accepted within a quarter RAW frame; two equally supported offsets still not),
  `test_broll.py` (a short piece between two cutaways on one line; a main-clip shot with its own sound off by 22 ms;
  a RAW piece too short to be a shot never left as a flash), `test_export_xml_edl.py` (a 24 fps competitor's
  sequence rate; a speed sliver; a stall; a picture glitch plays on the take's line, placed with the take's
  interval), `test_speech.py` (the end of a word heard as a breath), `test_verify.py` (a video full-screen
  throughout), `test_quality.py` (the RAW's own repeated frames are the cadence too). The tests of the fixes fail
  on the code before them (checked for the cutaway, glitch, flash and word-end fixes).
- The load-sensitive probe test (`test_probe.py`, the MKV/FLV duration check) failed once in a run while three
  pipelines shared the machine and passed alone; Task 10 makes it robust.
- **check-all** (thorough, every video with the `--fast` comparison, on a frozen copy of the committed code):
  **every video passes the hard checks** (deliverables, determinism, coverage), all eight: Deadpool, Spider-Man,
  video1-4, Zendaya, Zendaya-age. 138 minutes in all, the analyses reused from the earlier runs (video1 46 min:
  its segments stage and its determinism re-run take 17 minutes each -- Task 9). Runs: `work/t8/final4/runs/`.

## To check in Premiere

(The `1_edit.xml` of each case in the final check-all: `work/t8/final4/runs/<case>/001/`.)

1. **Every cut: clips now end 0.05 s after the last word** (0.15 s before). Listen for a clipped word ending,
   above all soft ones ("s", "f", a trailing "-ty"); a voiced ending the transcript cut short is kept with its word
   now ("OK?" in video2).
2. **video4, the opening "each other's Spidey"**: the competitor's 6-frame glitch (81 ms ahead) now plays on the
   take, picture and sound -- no jump, no cut inside the words. At 00:01:16 (S02, 6 frames) the clips S01-S04 play
   the RAW straight on (269.17-271.37 s): four clips in Premiere, one continuous take.
3. **video4, the cutaways to other moments** (the competitor's 7.97-8.88 s, four pieces of RAW 256.6-264.4 s):
   the interview now plays on through them in one take ("and they said we're gonna do the meme"), instead of 0.9 s
   of other moments' own sound: 00:07:24-00:09:24 is one clip, S10+S11 (RAW 281.92-283.92 s).
4. **video1, "tippex tippex" and "breaking even"**: the competitor's 2-frame dissolves there are hard cuts outside
   the words now: at 00:16:24 and about 00:39:26, moved out of the words; at 00:44:52 S22 runs straight on into S23
   (RAW 545.90 s), so "tippex tippex" plays whole.
5. **Zendaya, the 120 % sliver at about 15 s** (S19): left as the competitor has it, its sound continuous.
6. **video1 overall**: a 60 fps competitor over a 25 fps RAW, matched everywhere now -- the first case like it.

## Decisions

1. **video4: only `final.mp4`, `competitor.mp4` and `raw.mp4`**, as you asked: its project, AE project and Auto-Save
   folders were never opened; its cuts are from `final.mp4` and its 38 captions are read from its screen
   (`learn --final-only`). The screen reading needed one fix: the caption row is the row of text that changes most
   (your @-handle under the picture, one 12-second event, had been taken for the caption band).
2. **The answer key is your edit as you cut it** (cuts matched by picture, each piece placed by its sound, cutaways
   given the RAW sound the tool's own check proves), not the picture alone: otherwise a cutaway or a Topaz frame
   shift counts as a cut you made, and the 24-35 ms A/V offset of your renders eats most of the 2-frame tolerance.
3. **`--pad-after` 0.15 -> 0.05 s.** At the cuts your finished videos share with the tool you leave 0.08-0.12 s
   earlier than 0.15 s, on every one of them; at 0.05 your out points are within 0.02 s. No setting cut inside a
   word. Run 011 (your hand fix of the tool's Zendaya XML in an earlier task) points the other way (9 of its 12 edges
   at 0.15,
   7 at 0.05), but your finished Zendaya-age edit, made later, is tighter -- and you asked me to weight the finished
   videos. `--pad-before` stays 0.05 (decision 13).
4. **No other silence setting changed.** `--min-silence` changed nothing that matters; counting a pause with a
   breath in it as a pause (`silence_breaths`, kept as an option, off) brings Zendaya-age 0.6 s closer but makes
   video3 3.8 s shorter than yours (you keep those pauses there): it does not help across videos.
5. **A spoken form against its written one is not a glossary entry** ("gonna" -> "going to"): you wrote "wanna" in
   Spider-Man and "going to" in video4 -- a style per video. This reverses a Task 6 rule ("want to" -> "wanna" was
   glossary).
6. **The A/V offset may move by a quarter of a RAW frame per segment** (DESIGN D9), not 2 ms: the evidence is the
   RAW frames' phase, and a 58 ms offset rejected for 3 ms cost video4 every cutaway check.
7. **Spider-Man was not tuned on** (its numbers are listed for the record and it stays in check-all's hard checks).
8. **video2's and video3's captions were not guessed**: their projects hold another video's 67 captions. If you
   want them as caption keys, `learn --final-only video2 video3` reads them from the screen like video4's.
9. **The Spider-Man `answer.srt` is kept** (item 5, above).
10. **The glossary takes spellings and names only.** Besides spoken forms, a change made only of ordinary words is
    what was heard at one moment ("of" -> "to", "you're getting" -> "you get in" in video1): as entries they would
    change every "of" the audio allows. The glossary now holds four entries: `other's -> others`, `Toby -> Tobey`
    (video4), `banknote-forging -> banknote forging`, `Stationery's -> Stacey has` (video1). None of them goes to
    the speech model any more (fix 10).
11. **A short cross dissolve inside speech becomes a cut** (0.1 s at most, only where its cut is inside a word),
    rather than keeping the competitor's 2-frame dissolve and cutting inside the word.
12. **The final check-all ran on frozen code**: a copy of exactly the code committed here, run from that copy, so
    nothing changed while it ran could leak into it. `learn` then ran on its runs (the test cases' `case.json` name
    them). A first final check-all found the hot-word problem (fix 10); this is the second, after the fix.
13. **`--pad-before` stays 0.05 s.** At the cuts you share with the tool, you come in where it does in video2 and
    video4 (+0.00 s) and 0.08 s later in video1 and video3: a change would help two videos and hurt two, and a later
    start risks the first sound of a word (the word timings are good to about 35 ms).

## Left for Task 10

- **`learn` on a run that is still being written** reads what is there: run on video4 while its check-all run was in
  its checks, it reported "you kept 1 of its 19" instead of 3 (rerun afterwards: 3). It should refuse an unfinished
  run (no `report.md` yet).
- **Your video1 run** (`final.mp4` as the competitor, `work/t8/user/video1/002`, what `learn` reads your edit from)
  failed its own speech check at S08/S09 ("Exactly, so I'm open"): a stutter removed in V1 after a speech-safe
  extension ripples A1. Your edit is read from its frame matches, not that export, so the case is right; but the
  same could happen in a normal run, where the check would fail it (never silently).
