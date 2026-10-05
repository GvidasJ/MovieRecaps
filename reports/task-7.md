# Task 7: follow-mode caption timing

## What I found, and what my Task 5 report got wrong

In Task 5 I wrote that Deadpool's caption score dropped (47/60 → 37/60) because "the thorough edit plays the RAW
differently from the competitor in a few places", the biggest being the J-cut at "Brad Pitt's". That was wrong in two
ways:

- **"Brad Pitt's" was already 17 frames off in Task 4** (its final run, `work/check-all/runs/deadpool/016`), and so were
  "That's what" (−6 frames) and "gonna" (+10). All three start one competitor frame before a cut of the competitor's
  own edit; my edit plays the frame before that cut earlier, so the caption came early.
- **The 9 new misses came from the answer key, not from the captions.** Deadpool's key is the competitor's own caption
  timing (`answer.srt`) mapped onto the RAW through `answer_edit.json`, the competitor's edit. I made that file in Task 4
  *from Task 4's own analysis* (its cut list), so it carries that analysis's mistakes. The thorough analysis, which
  fixes them, was scored as wrong: `--fast` (the same analysis as Task 4) scored 46/60 on the same video.

I checked the key against the competitor's own picture and sound, measured without any run of the tool
(`work/_tmp/audio_map.py`, `work/_tmp/fix_deadpool_key.py`, `work/_tmp/deadpool_key_v2.py`):

- **The competitor's file plays its sound 54 ms after its picture.** The tool measures the same (53.9 ms, spread
  under 1 ms over 16 shots). Every cut of its sound is heard 1–2 frames after the cut of its picture (1.6 frames =
  54 ms at 30 fps), and its captions change on the picture's cut or one frame before it, never on the late sound
  cut: its editor cut picture and sound together, and the delay came after (the export or the upload). So the
  competitor's timeline is its picture's time.
- **The key mixed two clocks.** Its two cutaways (B-roll over the RAW's sound, 1.03–3.80 s and 19.87–22.30 s) had
  been placed by where the competitor's *sound* plays (54 ms before the picture's time), every other piece by its
  picture.
- **The key had three mistakes of the old analysis**: a one-frame piece at 14.87 s two RAW frames off both of its
  neighbours (the picture cuts into frame 446 and the sound is one take after it); two cuts one and two frames late
  (17.23 → 17.17 s and 18.17 → 18.13 s: the competitor's picture changes most into frames 515 and 544, and its sound
  cuts 1.5 frames later); and the take of 18.13–19.87 s placed 39.5 ms late (its sound is one take from 18.2 s to
  23.1 s, matched with a waveform correlation of 0.95–0.999). Inside the first cutaway, the sound's cut the key put at
  3.30 s is at 3.00 s.

## What changed

**The tool** (the edit itself is unchanged: the same `1_edit.xml` as before):

1. **Follow captions on one clock** (`caption_score.competitor_timeline`): the competitor's edit is mapped in its
   picture's time throughout. A cutaway that was placed by where the competitor's sound plays (broll.py, now marked
   `audio.broll.heard`) is moved by the competitor's measured A/V offset, and so is a cut of the sound between two
   such pieces.
2. **Timed on the words my edit plays where the edits differ** (`caption_style.follow_competitor`):
   - when the competitor cuts between a caption's start and its first word, the caption goes with the take after
     the cut: it starts as long before my edit plays that take as the competitor's caption shows before its cut, and
     on the cut when that is one frame ("Brad Pitt's", "gonna", "That's what", "Vanisher's", "Why is Brad Pitt" on
     Deadpool);
   - when my edit leaves out the moment a caption starts on (a pause my edit cut), the caption starts as long
     before its first word as the competitor's did (at most 0.5 s), never before the word before it ends. Before,
     it started on the word.

**The Deadpool key** (`tests/real/deadpool/answer_edit.json`), corrected as above: the competitor's edit as its editor
made it, in its picture's time. `answer.srt` (your captions) is unchanged. The pieces that changed (start–end in the
competitor's seconds, the RAW second at the start):

| before | after | why |
|---|---|---|
| 1.033–1.800 → 68.680 | 1.033–1.746 → 68.734 | first cutaway: from the sound's time to the picture's (+54 ms); the sound's cut heard at 1.80 s was made 54 ms earlier |
| 1.800–3.300 → 69.813 | 1.746–2.946 → 69.813 | the same; the sound's cut is at 3.00 s, not 3.30 s |
| 3.300–3.800 → 72.429 | 2.946–3.800 → 72.130 | the same |
| 14.867–14.900 → 91.667 and 14.900–16.333 → 91.768 | 14.867–16.333 → 91.734 | a one-frame piece of the old analysis, two RAW frames off the take around it |
| 17.167–17.233 → 99.600 and 17.233–18.100 → 100.033 | 17.167–18.100 → 99.967 | the cut is into frame 515, not 517 |
| 18.100–18.167 → 100.900 | 18.100–18.133 → 100.900 | the cut is into frame 544, not 545 |
| 18.167–19.867 → 101.668 | 18.133–19.867 → 101.595 | ... and that take 39.5 ms earlier (its sound + 54 ms) |
| 19.867–22.300 → 103.275 | 19.867–22.300 → 103.329 | second cutaway: from the sound's time to the picture's (+54 ms); the take of 18.13 s runs on under it |

## Scores

Your captions reproduced exactly (the same text, starting within 2 frames), on the old Deadpool key and on the
corrected one (Spider-Man and Zendaya-age are keyed on your own finished edits and did not change):

| runs | Deadpool, old key | Deadpool, corrected key | Spider-Man | Zendaya-age | **all, old key** | **all, corrected key** |
|---|---|---|---|---|---|---|
| Task 4, final | 47/60 | 34/60 | 14/64 | 3/19 | 64/143 | 51/143 |
| `--fast` (Task 5) | 46/60 | 34/60 | | | | |
| Task 6, before this task | 37/60 | 43/60 | 11/64 | 2/19 | 50/143 | 56/143 |
| **now** | 37/60 | **48/60** | 11/64 | 2/19 | 50/143 | **61/143** |

**The target, 64/143, was Task 4's score on the old key**, which had been built from Task 4's own analysis. On the
corrected key Task 4's runs score 51/143, and the tool now scores 61/143. On the old key the tool now scores
50/143: it cannot get back to 64 there without the old analysis's mistakes (the thorough cuts are what the
competitor's picture and sound say).

Deadpool has no timing miss left on the corrected key; its 12 remaining differences are your wording and capitals
("yes! Avengers!", "power?", "Vanisher And", "get out of this"), and line breaks where you joined a one-word caption
of the competitor's to the next ("the characters", "I was like he's"). Spider-Man and Zendaya-age are not follow mode
(their competitors write one word at a time / in capitals, so the words are grouped in your style); since Task 4 they
lost captions to small differences of a re-transcribed edit: Spider-Man 4 timings at −3, +4, −2.0x and −2.0x frames
(just past the 2-frame edge), one line break, and 2 captions gained; Zendaya-age one misheard number ("20" for "25").
I did not tune anything on them (Spider-Man is not typical of your videos; Task 8 learns your timing from the videos
in `finished\`).

## Test results

- Unit suite: **1046 passed, 12 skipped, 0 failed** (the same Linux-font skips as before). New tests: the competitor's
  timeline in its picture's time (`test_caption_score.py`), and three follow-mode timings
  (`test_caption_style.py`: a caption one frame before the competitor's cut, four frames before it, and a pause my
  edit cut out). All four fail on the code before this task.
- check-all (thorough, every video with the `--fast` comparison): **every video passes the hard checks**
  (deliverables, determinism, coverage), 33 min in all (Deadpool 2m57s, Spider-Man 14m27s, Zendaya 4m06s,
  Zendaya-age 11m37s, Task 5's caches reused). Captions 61/143 (43 %), word errors 7.4 %, 0 rule breaks. The edits
  are the same as Task 6's on all four videos (`1_edit.xml` compared, names and paths aside), so the cut checks did
  not change. Runs: `work/t7/runs/<video>/`.

## To check in Premiere

- **Deadpool** (`work/t7/runs/deadpool/003/2_captions.srt`), the captions that moved since Task 6: "That's what"
  (4.72 s, +6 frames), "Brad Pitt's" (9.98 s, +17), "gonna" (10.75 s, +16) and "Why is Brad Pitt" (18.63 s, +9)
  now start on my edit's cut, with their first word; the captions of the first cutaway ("the X-Force",
  "The studio", "was like", "yes Avengers!", 1.5–3.0 s) start 3 frames later (+7 for "The studio", which now
  starts on the sound's cut). Nothing else moved.

## Decisions

- **Follow captions run on the competitor's picture clock, not its sound's.** Its editor's timeline is the picture's
  (the captions change on picture cuts); if you would rather have them on the sound as heard in its file, they would
  all start 54 ms (3 frames at 60 fps) earlier on Deadpool.
- **I corrected the Deadpool key** instead of keeping a yardstick that rewards one analysis's mistakes. Every change
  is backed by the competitor's own picture and sound; the old file is in git history (Task 4's commit, 7afd631)
  and in `work/t7/deadpool_answer_edit_old.json`.
- The general "timed on the words" rule changes nothing on the current answer keys (every case there is the one-frame
  one, which the cut rule already places exactly); it is there for edits that differ more, and its tests cover it.
