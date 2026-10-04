# Task 6: learn from your corrections

## Result

After you finish a video in Premiere:

```
..\..\.venv\Scripts\python -m match_cuts learn "<your finished project>.prproj"
```

It finds the run the project was made from and compares your finished project with what the tool generated
(`1_edit.xml` and `2_captions.srt`):

1. **Caption words you changed go into a glossary** (`tools/match_cuts/caption_glossary.txt`). The speech models get
   them as hints next time, and the glossary is used when choosing between what was heard and what was read. A
   heard word is replaced only where the audio fits.
2. **Your cut and framing changes are recorded** in the new test case's `learned.json`. The same kind of change on 3
   or more videos is printed as a **suggested new default**; nothing is ever changed for you.
3. **A new test case** in `tests/real/<name>/` holds the video and your finished version as the answer key, so
   check-all tests everything you ever corrected.
4. **A short summary** of what was learned.
5. **A RAW over 100 MB** is copied smaller automatically, with the same size and frame rate.
6. **The exact git commands** that push the new case, printed at the end.

### Tried on your real Spider-Man project

`reference/dsafxcv.prproj`, your finished Spider-Man edit, was read only and never committed. The test library and
glossary went to a scratch folder, so nothing in `tests/real` changed.

- **First, the right run.** Your project plays `output\media\raw.mp4`, from the older flat layout. That folder keeps
  only its latest run: a different video, written after you saved the project. `learn` refuses it, with what differs:

  ```
  learn: dsafxcv.prproj: the run's RAW (...\output) is not the video your project plays (1920x1080 against
  1280x720; 23.976 fps against 59.940; 143.7 s against 283.8 s) -- a later run in the same folder? Give the run it
  was made from with --run
  ```

- **With `--run` on tonight's Spider-Man run** (the same competitor, the same RAW):

  ```
  Learned from your finished edit (run ..\..\work\t5final\runs\spiderman-school\001):
    Captions: 2 word(s) you wrote differently, 3 with your capitals
      to the glossary: 'want to' -> 'wanna' (new)
      kept in learned.json only: 'school' -> 'School' (an ordinary word: emphasis, not a name); 'be' -> 'BE' (an ordinary word: emphasis, not a name); 'like' -> 'LIKE' (an ordinary word: emphasis, not a name); 'eventually' -> 'and then' (other words, not a spelling: similarity 0.24)
      (8 word(s) removed, 1 added, 1 longer passage(s) rewritten: kept in learned.json, not in the glossary)
    Cuts: 24 of 34 clip edges moved, 0 clip(s) removed, 1 added
    Framing: not compared: your picture is not the RAW's clips (an After Effects comp?)
    This video's habits: clips start later (+0.1); clips end earlier (-0.192)
    Test case: ..\..\work\_tmp\learn_cases\dsafxcv (new)
      competitor.mp4     copied (50 MB)
      raw.mp4            copied (73 MB)
      answer.srt         your 63 captions (the answer key)
      answer_edit.json   your timeline (24 pieces of the RAW)
      case.json          the case's notes
      learned.json       what you changed (the suggestions read every case's)
  To push the new test case:
    cd "C:\Users\gvida\Desktop\bh\MovieRecaps"
    git add "work/_tmp/learn_cases/dsafxcv" "work/_tmp/learn_glossary.txt"
    git commit -m "Test case dsafxcv: learned from my finished edit"
    git push
  ```

  - Run again on the same project, `learn` updated that case instead of making a second one, and the glossary
    kept one `want to -> wanna` line (`# dsafxcv`).
  - "wanna" is the one glossary word: you write it where both speech models and the competitor have "want to"
    (Task 4 found the same).
  - Your capitals on ordinary words and the one-off mishearing are recorded but kept out of the glossary.
  - Your picture is an After Effects linked comp on V2, with no RAW clips on any video track. So the cuts come from
    A1, and framing is not compared.
  - On every clip you trim tighter than the tool's padding: starts 0.1 s later and ends 0.19 s earlier (medians).
- **A RAW over 100 MB.** The first attempt, against the wrong run, also showed the automatic smaller copy: its 260 MB
  RAW became 84 MB, 1920×1080 at 25 fps as before.

### check-all

Every hard check passes on all four videos (42m28s, reusing Task 5's analysis caches: Task 6 changes no cut
matching). The caption scores are the same as Task 5's (50/143 exact, 7.4 % word errors, 0 rule breaks). There is
no glossary yet, so the captions run exactly as before. The first `learn` creates it.

## What changed

### `learn.py` (new), the `learn` command (`cli.py`)

- **Finding the run.** The project's clips play the RAW from `<run>\extras\media\`, or `<out>\media\` in the
  older flat layout, and that folder is the run. `--run` names it.
  - The run's RAW must be the video your project plays: the same size, frame rate and length, as Premiere recorded
    them in the project (`prproj.py` now reads them). A smaller copy keeps all three.
  - It must also play at least half of what your project plays.
- **Captions.**
  - Your captions come from the project's caption track, or its text graphics when the captions were upgraded.
    They are aligned with `2_captions.srt` word by word, in order, not by time, because a moved cut shifts every
    caption after it.
  - A change counts between 2 unchanged words on each side and is at most 3 words long. Longer passages are
    "rewritten" and stay out.
  - The glossary keeps only corrections:
    - a changed word spelled like the heard one (similarity ≥ 0.5: "zendeya" → "Zendaya", "want to" → "wanna");
    - the capitals of a name or acronym ("tom" → "Tom": the word list writes it that way, or does not know the
      word).
  - A plain word in capitals ("like" → "LIKE") is emphasis. Other words ("eventually" → "and then") are a
    mishearing. Both go to `learned.json` only, with the reason.
  - The glossary file holds `heard -> written	# videos`. A correction already there gets the new video added, not a
    second line.
- **Cuts and framing.**
  - The cuts come from the sound clips: A1 plays the RAW in both edits, even when the picture is an After Effects
    comp. Each of the run's clips is matched with yours by the RAW it plays, and the change at each edge is recorded
    in RAW seconds, along with clips removed and added.
  - Framing is compared on the picture clips when your project still has them: moved sideways (sequence px, from
    the Motion position) or zoomed (%).
- **Suggestions.**
  - A video "does" a kind of change when 30 % of its clips do (at least 3 clips): clips start earlier or later, end
    earlier or later, the picture moved sideways, zoomed in or out, clips removed.
  - The same kind on 3 or more videos (every `tests/real/*/learned.json`) prints a suggested new default with the
    videos and the median, for example `--pad-after 0.38 instead of 0.15`.
  - Nothing is ever applied.
- **The test case.**
  - Files: `competitor.mp4` and `raw.mp4` (`testcases.small_copy`: over 100 MB, H.264 near 90 MB with the same
    size, frame rate and frames, the audio copied), `answer.srt` (your captions), `answer_edit.json` (your timeline,
    as check-all's answer keys expect), `case.json` and `learned.json`.
  - A case of the same competitor (by file hash) is updated, not doubled: your new key replaces the old one, the
    summary says so, and `git diff` shows the change before you push.

### The glossary in the captions (`captions.py`, `caption_recheck.py`)

- **Hot words.** The written side of every entry goes to both speech models, as `caption_allowlist.txt` already did.
- **Heard vs the glossary** (`glossary_readings`, new). Where the models still heard an entry's old words, both
  readings of the phrase (with 3 heard words on each side) are scored against the audio by both models. The written
  form is taken only where every model finds it at most 1 nat less likely (about a third as likely) than what it
  heard. You corrected it before, so it does not have to be likelier, but it must fit; it is never put in blindly.
  An entry that only adds your capitals is written that way with no audio check, because no word changes.
- **Heard vs read** (`screen_readings`). Where the competitor's screen shows exactly what you once corrected the
  heard words to, the screen's reading needs only to fit the audio (the same 1 nat), not to be likelier.

## Tests

- **Unit suite** (`-m "not slow"`): **1041 passed, 12 skipped, 0 failed** (6m36s).
- **Slow suite** (`-m slow --runslow`): **21 passed, 63 skipped, 0 failed** (28m14s, alongside check-all).
- **check-all**: every hard check passes (above).
- **New tests:**
  - `test_learn.py`: the words you changed, between unchanged words; only real corrections reaching the glossary;
    the glossary keeping each correction once with its videos; the run found from the project's media, in both
    layouts; a run whose RAW is another video refused (size, frame rate, length); a project of another run
    refused; the cuts taken from the sound when the picture is an After Effects comp; a whole `learn` making the
    test case, the glossary and the git commands, and updating the case the second time; the same change on 3
    videos suggesting a new default without changing it.
  - `test_caption_recheck.py`: a learned word replacing the heard one only where both models find the audio fits;
    a learned capital written that way without the audio; the screen showing a learned correction needing only to
    fit.
  - `test_prproj.py`: the size, frame rate and length Premiere records for a media file.

## Things to check

- Run `learn` on your next finished project. Check the summary, then `git diff` before you push.
- **Do not run it on `reference/dsafxcv.prproj` against the Spider-Man case** unless you mean to. It would update
  `tests/real/spiderman-school` (the same competitor), replacing your fixed `answer.srt` from Task 4 with the
  project's 63 caption graphics. Review that with `git diff` before you commit.
- `caption_glossary.txt` does not exist yet, so the first `learn` creates it. Edit or delete any line you disagree
  with.

## Decisions

- **The cuts are compared on A1, not V1.** Your Spider-Man project has no RAW clips on V1: the picture is an After
  Effects comp. A1 cuts where the edit cuts, in both edits.
- **The glossary is for spellings and names, not every change.** Every change is recorded in `learned.json`, but
  only corrections reach the glossary, because each glossary word is offered to every future video.
- **1 nat of slack.** The glossary is prior knowledge (your own correction), so it needs to fit the audio, not to
  beat what the models heard. Both models must agree.
- **Suggestions at 3 videos.** Two videos can share a habit by chance. The threshold and the 30 % share are
  constants at the top of `learn.py`.
- **No test case committed tonight.** You had no new finished project, and the one you have is already the
  Spider-Man case. The real-project test went to a scratch library.
