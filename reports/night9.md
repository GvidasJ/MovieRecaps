# Night 9 (16 October): your video10 fix, Scene Edit Detection, a desktop app, action captions

## In short

Everything on your list is done, item 5 too (you gave me 10 hours). In one line each:

1. **Your video10 fix** (it was here): it is now a test case (`tests\real\video10-fixed`), but it taught **no new
   rule** -- I tried three caption rules and each one made your earlier fixes worse or pulled two of your videos in
   opposite directions (section 1 says what and why). Its four action captions became examples for item 5.
2. **Scene Edit Detection** was already on (since overnight 2) -- tonight I **checked it on all 23 test videos**:
   every RAW shot change inside a clip is split, every split is seamless (no frame skipped or repeated, the same
   framing, A1 split and linked, in sync), and the splits never count as cuts -- check-all already ignored them, and
   now `learn` does too. The switch to turn it off is `--no-scene-split`. I also found 3 shot changes the tool does
   not see in video9 (a dark, grainy film); finding them changes two other rules there, so that is left for another
   night (section 2).
3. **A desktop app**: double-click **MovieRecaps** on the desktop. Black with a thin grid, flat cards, small tags,
   red accents. Drop videos (a pair, a folder of folders, or pairs one after another), choose Fast / Thorough, press
   Start; each row shows the step and time left, then PASS / OK with a note / FAIL / PICTURE NOT FOUND / NOT IN THIS
   RAW with Open folder / Open in Premiere / Show report; a Windows notification at the end. Learning from a fix is
   drag and drop too: it copies the files to the right names and gives you the LEARN line. Download and Convert are
   tabs marked "soon". Tested with your real videos (section 3).
4. **Tests**: check-all of all 23 videos, four times (after items 1-2, and three times for item 5) -- **nothing got
   worse than night 8**: the same edit in every video (and with items 1-2 alone also the same captions, byte for
   byte); the 2 known FAILs stay. The scene splits checked in every `1_edit.xml`; every unit test passes; the app
   tested with your real videos, which found two bugs, now fixed (sections 3 and 4).
5. **Action captions**: each `*...*` placeholder now gets a **suggested** action (*claps hands*, *laughs
   hysterically*, *holds the dynamite*) from a free vision model on the graphics card (the same on the PC and the
   laptop). The model is asked again before a suggestion is used; when it is unsure, the placeholder stays, and every
   suggestion is listed as "check it". Tested on your 47 action captions: 15 suggestions, 11 of them what you wrote or
   the same thing in other words, 2 half right, 2 wrong; the other 32 stay `*...*` (mostly reactions like *realizes
   it*, which the frames do not show). On the test videos only the `*...*` lines change (section 5).
   `--no-action-captions` turns it off.
6. **On the laptop**: `git pull`, then one line for the app (section 6).

## 1. What your video10 fix taught (item 1)

### The files

Both were here, so nothing was skipped: `finished\video10\video10_fixed.prproj` (your edit is its first sequence,
"Recreated Edit (Premiere)", 23.976 fps, 11.4 s; the other seven are your template's sequences) and
`output\laptop_video10` (the laptop's batch run `output\video4`, `--fast`, 100 %, your template). Your project points
at `D:\Shared\video4\...`, so I wrote a copy that points at the run here:
`finished\video10\video10_fixed_relinked.prproj` (your original is untouched). Tonight's code (night 8) on the same
two files gives the same 5 clips and the same captions as the laptop's run (night 7's code).

### What you changed

As you said, I left out the audio, the trims at cut points and the ending (you played on 1.2 s past the
competitor's end with "*everyone leaves*").

| | the tool | your fix | what I did |
|---|---|---|---|
| picture | 5 clips, the competitor's framing (115 %, the same on all) | the same framing on all 6 clips | nothing to learn |
| after "protect me" | a 1.1 s pause plays on, then a heard "A" across the RAW's own shot change, then "lady" | you cut 0.29 s from the shot change up to "lady" (the end of that "A") | not a rule (below) |
| captions | follows the competitor: "Oh God" / "No we're not going" / "anywhere these" / "are my new family" / "They're" / "gonna protect" / "me a lady" / "doesn't wanna go" / "a lady doesn't go" / ... | your own groups on the words: "No" / "God" / "no we're not" / "going anywhere" / "these are my" / "new family" / "they are gonna" / "protect me" / ... | not a rule (below) |
| action captions | none | "\*gets closer to friends\*", "\*understands it\*", "\*claps hands\*", "\*everyone leaves\*" | the examples for item 5 |
| "They're" | "They're" | "they are" | kept in the case's learned.json only (an ordinary word, not a spelling) |

### Why the captions went wrong there, and why I made no rule

The competitor's captions were bad for copying: they leave words out ("me", "A"), they show up 0.1-0.6 s after
the words, and the OCR read four pieces of the picture as captions ("1", "r" and two signs). The tool copied the
competitor's captions and its rules then split them at the wrong moments ("gonna protect" came 0.9 s late, "me a
lady" joined two sentences). I tried three rules and checked each against your earlier fixes first. None was good
enough:

1. **The competitor's sound plays 0.32 s after its picture** (the tool measured it clearly but did not trust it:
   only 58 % of the video could be measured). Moving the copied captions by that much would fit a competitor that
   timed its captions to its own sound -- but on your earlier fixes it made the captions much worse (deadpool:
   45 -> 14 of 60 captions exactly as yours; laptop013: 9 -> 1), and video10's captions are not 0.32 s late either
   (between 0.13 s early and 0.56 s late).
2. **A caption that starts long after its first word moves onto the word**: no gain on video10 (0 of 17 exactly as
   yours either way), and 3 earlier fixes lost one caption each.
3. **Not counting the OCR's pieces of the picture as captions** when the tool decides whether to copy the
   competitor's captions. Without them, video10's competitor writes 3.3-word sentences, so the tool makes the
   captions in your style from the words instead -- on the words, 2-3 words each, with `*...*` exactly where you
   wrote three of your four action captions. It looks much closer to yours, but it scores the same (0 of 17 exactly
   as yours: your groups differ), and it switches video020 the other way (its competitor would then be copied,
   while in your video020 fix you split its captions into 2-word groups). Two videos pull in opposite directions at
   the same limit, so I left the rule as it is.

**The 0.29 s cut after "protect me"**: one example; the pause (1.6 s from "me" to "lady") is longer than the
1-second limit of night 7's "pause at a shot change" rule and holds a sound ("A"). Not a rule.

### The new test case

`tests\real\video10-fixed` (made with `learn --picture-key`, as on nights 7-8: the competitor, RAW 684-757 s of the
episode, your 17 captions and your 6 picture clips with their framing as the answer key; "new style": check-all runs
it thorough and scores it). With night 8's code it passes every hard check; captions 0 of 17 exactly as yours, cuts
1 of 4, framing 5 of 6.

## 2. Scene Edit Detection, always on (item 2)

### It was already on -- now checked on every test video

The tool has split every V1 clip (and its A1 clip) at each of the RAW's own shot changes since overnight 2 (the
switch was `--no-scene-cuts`). So the work tonight was to make sure it holds everywhere and that the splits never
count as cuts:

* **Checked on all 23 test videos** (tonight's check-all outputs, every delivered `1_edit.xml`): every RAW shot
  change the tool finds inside a clip is split (none left inside a clip); every split is **seamless** -- the next
  piece starts on the very next RAW frame, nothing skipped or shown twice (also at 125 %); **A1 is split at the same
  frames** and each piece is linked to its own audio; the framing is the same on both sides unless the competitor
  frames the new shot otherwise; the captions do not change (the splits change no sound).
* **The switch is now `--no-scene-split`** (the old `--no-scene-cuts` still works).
* **Splits are not cuts:** check-all's "cuts reproduced" already counted only jumps in the RAW (a split is no jump).
  `learn` compared clip by clip, so a split you kept or removed looked like a cut you moved; now it joins the
  pieces of one take (yours and the tool's) before it compares the cuts. A cut of yours inside a take (video10's
  0.29 s) is now listed on its own: "1 cut(s) of yours inside one of the run's clips (0.29 s of the RAW left out)".
* Tests: `tests\test_night9.py` (three pieces at two shot changes: seamless, the same framing, A1 linked; the
  switch; learn and check-all not counting splits).

### Found, but not changed tonight: three shot changes the tool does not see (video9)

A second opinion (PySceneDetect, like an editor's scene detection) on all 23 videos found 22 places the tool does
not split. I looked at each picture: 20 are camera shake, motion blur or a cartoon character moving in one shot
(rightly not split); **2 are real cuts the tool misses, both in video9** (a dark, grainy animated film whose frames
differ a lot even inside a shot), and looking closer at video9 I found a third: the DYNAMITE close-up -> the boat at
1825.1 s (the very frame your fix starts its last clip on -- the tool's clip starts one frame earlier, so it shows
one frame of the close-up), the boat -> another angle, and the boat -> the dynamite pile.

I wrote a rule that finds exactly these 3 and nothing else on the other 25 RAWs (a big change of the whole picture,
2.5 times the motion around it -- a moving character changes only part of the picture). But with it, two other rules
change video9's edit: the pause-cut guard then drops a pause cut you kept (instead of extending it to the shot
change, as you did: 1824.865 -> 1825.115 s), and the competitor's own last 4 frames (the start of the next shot)
fail the flash check. Fixing those two rules needs its own careful night, so I took the detector change out again.
It is in section 9 (left for next time).

## 3. The desktop app (item 3)

### How to open it

* **On the PC: double-click "MovieRecaps" on the desktop** (a red square with a white M). It opens in its own
  window (not a browser).
* **On the laptop**, once after `git pull`, in the project folder:

  ```
  .venv\Scripts\python tools\app\setup_app.py
  ```

  That one line installs what the window needs (PySide6, about 100 MB, once) and puts the same shortcut on the
  laptop's desktop.
* Without the shortcut: `.venv\Scripts\pythonw tools\app\MovieRecaps.pyw`

### What it looks like

The style reference `style.png` was **not in the project folder** (I searched the PC: not on the Desktop, Downloads,
Pictures or D:\ either -- it is probably still on the laptop). So I took the look from the website
(highimpression.org; it hides behind a "please wait" check, so I opened it in a hidden Edge window and read its
colours and sizes from the page): an almost black background (rgb 14,14,14) with a thin grid that fades out
downwards, a thin strip of small grey words at the top, flat cards with a thin light border and square corners,
small outlined capital tags, white bold names with grey labels, small coloured squares for a state, Open Sans --
with **red instead of its green**. Only the look; none of its content.

Pictures of it, in `work\t24\app_e2e\`: `run1\2_running_14.png` (the queue while it runs), `run1\3_done.png` (the
queue at the end), `run1\4_report.png` (Show report), `learn2\learn_box.png` (learning from a fix),
`warn_out\warning.png` (the warning before the start), `premiere3.png` (Open in Premiere), `notify_corner.png` (the
notification).

### What it does (Match cuts tab)

* **Drop videos here** (or "Choose videos..." / "Choose a folder..."):
  * a RAW and a competitor = one video. The RAW is the file named `raw.*`, else the longest video (the batch's own
    rule); **Swap** on the row changes them;
  * a folder of folders (like `input\videos` with `video1`, `video2` ... inside) = one video per folder;
  * one video alone waits (an orange line) for the next one you drop;
  * drop more at any time -- also while it runs; they join the queue.
* **The queue**: one row per video -- its name, the two files with their lengths, **Fast | Thorough** (Fast by
  default), the speed (100 % / 125 %, click to change; a `speed.txt` in the folder sets it), a progress bar with the
  step the tool is in and the time left (read from the tool's own progress lines), then the result -- **PASS / OK
  with a note / FAIL / PICTURE NOT FOUND / NOT IN THIS RAW** (or STOPPED) with **Open folder**, **Open in Premiere**
  and **Show report** (why it failed or its note, what to check by hand, doubtful captions; buttons for report.md
  and the run's log).
* **Start** runs the queue one video after another; **Stop** stops the running one.
* **Before the start** it warns when something looks wrong: only one video (no RAW), the competitor longer than the
  RAW, and the tool's own wrong-input check.
* **A Windows notification** when the queue is done ("2 PASS, 1 FAIL -- 25m 10s").
* **Learn from your fix**: drop your fixed `.prproj` and the tool's output folder it came from (together or one
  after the other). It copies them to `finished\<name>\<name>_fixed.prproj` and `output\laptop_<name>` (the name
  from the project: `video11_fixed.prproj` -> `video11`, you can change it) and shows the **LEARN line** for the next
  night's list, with a button that copies it.
* **Download** and **Convert** are tabs marked "soon": the room for the next tools (each tool is one small module;
  `tools\app\README.md` says how to add one).

It **only runs the tool**: each video is exactly the command `python -m match_cuts batch` runs for it (`--premiere`,
`--fast` unless Thorough, its `--speed`), into its own folder in `output\` (`video4`, `video4-2` if that exists), the
console saved as `extras\batch_console.log`, the result read with the batch's own checks. Nothing in the tool
changed for the app.

### Tested end to end, with your real videos

Through the app's own window (a test program dropped the files and pressed the buttons, as you would):

1. **A single pair**: your video10 (the laptop's competitor and the 21-minute RAW) dropped as two files -> one row,
   "video10", the RAW guessed right (21:22 against 0:12).
2. **A folder of folders**: night 8's batch folder (your video7, video8 and video9 at full size: 14 min, 7 min and
   81 min RAWs) dropped as one folder -> three rows, video7 / video8 / video9, each with its RAW.
3. **Start**: no warning (the inputs were right), the four ran one after another, each row showing the step and the
   time left from the tool's own progress lines ("S4 layout -- about 16 min left", "S5.2 visual search (51 %) -- about
   3 min left"):

   | video | result | time |
   |---|---|---|
   | video10 | PASS | 3m50s |
   | video7 | PASS | 3m56s |
   | video8 | PASS | 2m57s |
   | video9 | PASS | 9m04s |

   20 minutes for the four, every cache made fresh. **The files are exactly the batch's**: the same clips in
   `1_edit.xml` and the same `2_captions.srt` as `batch` makes from the same videos (night 8's batch of video7/8/9,
   tonight's run of video10).
4. **Show report** opens the run's result in plain words (what to check by hand, the captions to look at).
5. **Stop** on a running video: the row said STOPPED within 3 seconds and nothing of the tool kept running (no
   Python, no ffmpeg left).
6. **Open in Premiere**, on this PC's Premiere Pro 2024: Premiere ignores an XML it is *started* with (it showed its
   Home screen), but opens one handed to it while it runs. So the button starts Premiere when it is not running,
   waits for its window, then hands it the run's `1_edit.xml` (as a copy named after the run, `video10_edit.xml`,
   so the new project is called "video10_edit", not one more "1_edit"). Tested: Premiere started and opened the edit
   by itself in about 15 seconds -- the sequence with your template on V2, the clips on V1 / A1. **Premiere saves
   such a new project in the folder of the last project you opened** (here
   `Desktop\scene\Copied_survey_POPW`); I deleted my two test projects there again and closed Premiere -- its Recent
   list may still show "1_edit" and "video10_edit".
7. **Learn from your fix**: a copy of your video10 project (renamed `e2etest_fixed.prproj`) and a run folder dropped
   together -> copied to `finished\e2etest\e2etest_fixed.prproj` and `output\laptop_e2etest`, the LEARN line shown
   and copied to the clipboard (then I deleted the test copies).
8. **The desktop shortcut** is on this PC's desktop (MovieRecaps, red M icon); it starts the app with no console
   window, with its own icon in the taskbar and a dark title bar.
9. **The Windows notification** at the end of the queue: a toast from "MovieRecaps" with the red M -- "Match cuts:
   the queue is done -- 4 PASS -- 20m 00s".
10. **A second full run, with the suggestions' check in** (10:36-10:47; most steps came from the caches of the first
    run): the same video10 + video7/8/9, with **video8 switched to Thorough** on its row, and a **wrong pair**
    (video10's competitor with video8's RAW) dropped next to them:

    | video | result |
    |---|---|
    | video10, video7, video9 (Fast) | PASS |
    | video8 (Thorough) | PASS |
    | wrong | **NOT IN THIS RAW** -- "the competitor's scenes are not in this RAW: only 0 % of its frames and 0 % of its sound were found in it, so I stopped instead of making an empty edit" |

    video7's run filled 2 of its 3 placeholders: *walks away* (you wrote *sits down* there) and *covers her mouth*;
    video9's one placeholder stayed `*...*` (the model's "sits down" there was doubted when asked again).
11. **Your current `input\` pair** (`competitor.mp4` + `raw.mp4`: video021's episode, a 20-minute RAW), Fast, on the
    final code (13:05-13:16, no caches for it): **OK with a note** -- every file there, but two short flash frames
    to look at in Premiere (2 frames at 00:00:05:16, 5 frames at 00:00:32:00). Night 8's code makes exactly the same
    edit (61 clips), the same captions and the same two notes from these files, so they are not new tonight (in
    check-all, video021's test window runs Thorough and passes). The competitor's captions are copied here, with no
    `*...*` placeholder, so there was nothing to suggest.
12. **The warning before the start**, with real files: a pair whose competitor (0:26) is longer than its RAW (0:12)
    and one video alone -> "Some videos look wrong: the competitor (0:26) is longer than the RAW (0:12) -- swapped?
    (the swap button changes them) / comp.mp4: one video alone -- no RAW (or no competitor) for it; it will not run
    / ... Start anyway?" -- Cancel leaves the queue as it was (`work\t24\app_e2e\warn_out\warning.png`).

**Found and fixed by these tests:** two drops made quickly one after the other (the second before the first was
read -- a big RAW takes a moment) were read at the same time, and the second could lose the first one's video
waiting for its partner (a RAW dropped alone, then its competitor: no pair). Now the drops are read one after the
other. The warning said "the competitor is longer than the RAW" twice (the app's words and the tool's); now once.
And video7's `report.md` did not list its two suggestions (when the competitor's captions are copied, the report
skipped the placeholders); now it does in every caption mode.

26 unit tests for the app (`tools\app\tests`): the pairing, the names, folders, `speed.txt`, the command line, the
progress text, the result words, learn's copy and line, the shortcut, the run's copy for Premiere, Stop ending the
tool and every process it started, and (on the window itself, without a screen) drops read one after the other and
the warning.

## 4. Tests (item 4)

### check-all (items 1-2 code, all 23 videos)

The code of items 1-2 (frozen as `work\snap9e`), the same split as nights 6-8 (new-style cases thorough and scored
against your fixes, old ones `--fast` with the hard checks only), two streams, 07:33-08:20 (47 min, nothing else on
the graphics card):

* **Hard checks: 21 of 23 pass.** The two that do not are the known ones (video020 `--fast`, video6's last frame),
  as on nights 7 and 8. The new **video10-fixed passes**.
* **Nothing got worse than night 8: all 22 earlier videos give exactly the same edit -- the 60 fps plan and the
  delivered `1_edit.xml` -- and the same captions, byte for byte** (`work\t24\compare_runs.py`). That is expected:
  tonight's items 1-2 changed no rule the edit uses (the scene splits were already in night 8's edits).
* Scores (the same as night 8; video10-fixed new): captions exactly as yours 132/356 + 0/17 (video10), cuts
  reproduced 15/71 + 1/4, framing 57/104 + 5/6.

### The scene splits in the delivered 1_edit.xml files

A checker (`work\t24\scene\check_splits2.py`) read every delivered `1_edit.xml` of the 23 videos and the RAW
frames they play:

* **81 splits** in all (61 on a RAW shot change; the others are a framing change of the competitor inside a take);
* **no RAW shot change left inside a clip** (the tool's own shot detection);
* **every split seamless**: the next piece starts on the very next RAW frame -- no frame skipped or shown twice
  (also at 125 %);
* **no flash**: no piece of a shot under 0.25 s at a cut (the hard check XML FLASH; the two known FAILs are the
  same as on nights 7-8, and the splits there are night 8's);
* **A1**: every V1 piece has its own A1 piece on the same frames, linked, playing the same source moment (in sync);
* the framing is the same on both sides of a split unless the competitor frames the new shot itself.

### Unit tests

On the final code, the whole suite with the graphics card hidden: **1335 passed, none failed** (97 skipped: they
need the graphics card or are the slow ones; 8 min). Then the ones that need the graphics card: **97 passed**, and
the slow end-to-end ones: **23 passed** (15 min). The app's own tests: **26 passed**.

17 tests are new in `tests\test_night9.py`: the scene-split switch; three pieces at two shot changes (seamless, the
same framing, A1 split and linked); learn and check-all not counting splits, and learn still listing a cut of yours
inside a take; the action suggestions (your style from the answer, generic and unsure answers keep the placeholder,
a sure one replaces it in `2_captions.srt` and `captions.json`, the lines around the moment go to the model, the 2B
by default and the 4B on request, `--no-action-captions`, the report's list in every caption mode, learn adding your
new action captions, a suggestion the model doubts when asked again stays a placeholder, a laugh suggested only when
no action is sure, and no other reaction).
Two older tests changed: night 6's list of new-style test cases (+ video10-fixed), and the test that every cached
stage carries a code fingerprint now also sees the suggestions' own cache key. My first full run of the suite found
one problem in my code (that cache key) before anything else ran on it.

### The app with real videos

Section 3: one pair and a folder of three of your videos at full size, through the app's own window -- 4 PASS, the
same files as the batch; then again on the final code with video8 on Thorough and a wrong pair (NOT IN THIS RAW), and
the warning before the start. These runs found two bugs, both fixed and tested: drops made quickly one after the other
could lose a video waiting for its partner (the app), and `report.md` did not list the suggested actions when the
competitor's captions are copied (the tool; the end summary and the app's "Show report" did). Plus a warning line
the app said twice.

## 5. Action captions (item 5)

**Done** (there was time): each `*...*` placeholder is filled with a **suggested** action, by a vision model on the
computer's own graphics card (free; nothing leaves the computer). `--no-action-captions` turns it off.

### How it works

* For each placeholder the tool takes **six frames of what your edit shows there** (from the RAW) and **the line said
  just before and just after it** ("lady doesn't go" ... "The lady's fifteen"), and asks the model what the main
  person does.
* **Your style**, taken from your own action captions (74 of them, in your finished projects and your SRTs; they are
  now in `tools\match_cuts\caption_actions.txt`, and `learn` adds the new ones of every fix): 1-4 words, lower case,
  a verb in its -s form first (*claps hands*, *knocks on the door*), a feeling as one word (*shocked*), no subject, no
  full stop.
* **The model**: Qwen3-VL-2B (free, Apache-2.0 licence), the same on the PC and the laptop (4 GB, downloaded once;
  a few seconds to load, about a second per placeholder). Its bigger brother, the 4B (`--action-model big`), is not
  better on your 47 moments (with the same check below: 8 suggestions, 5 of them right or close; the 2B: 11 and 8)
  and needs twice the memory: in a first try of tonight's check-all, next to a second run on the 16 GB card, it
  spilled out of the card and took 9 minutes for 3 placeholders.
* **When it is unsure, the placeholder stays**: it says so, its answer is no action, or a generic one ("looks up",
  "stares ahead", "leans forward"), or its words were unlikely to it.
* **Then it is asked again**, in two other ways, whether its suggestion is what the frames show ("Does the main
  person in them claps hands? Answer yes or no." and "Is this a good short caption for what happens: ...?"). Unless
  it says yes both times, the placeholder stays. This removed most of its wrong suggestions -- above all "sits down",
  its answer when it is lost (below).
* **No sure action? Then it is asked how the main person reacts**, in one word. Only a laugh becomes a suggestion,
  `*laughing*` (asked again the same way) -- a laugh is something six frames show; its other answers ("shocked",
  "confused") were mostly wrong, so they keep the placeholder. About one second per placeholder in all.
* **Marked as suggestions**: the text in `2_captions.srt` is just the action (nothing for you to delete), and every
  suggestion is listed in the run's end summary under "Captions worth a look" ("00:00:03,467-00:00:04,550
  '*claps hands*': a suggested action (what the picture shows, Qwen/Qwen3-VL-2B-Instruct) -- check it"), in
  `report.md` ("Suggested actions") and in the app's "Show report".
* **An online model later**: the model is one small class (`action_captions.py`: `describe(pictures, examples,
  context)`); an online one (stronger at reading the scene) is one more class -- it would get your action captions as
  examples too. (The small local models copied an example instead of looking when they got the list -- see below.)

### How good it is -- tested on your own action captions

Every moment where your finished edits have an action caption (47 in your fixes) went to the model the same way
(`work\t24\actions\`):

| | the first try (your 74 captions as examples) | your style as rules + what is said around it | + asked again | **+ a laugh when no action is sure (the final one)** |
|---|---|---|---|---|
| suggestions (the rest stay `*...*`) | 46 | 27 | 11 | **15** |
| right or close | not counted | 11, and 3 half right | 8, and 1 half right | **11**, and 2 half right |
| wrong | most: it copied an example instead of looking ("starts spraying" 15 times, "awkward silence" for people leaving) | 13 -- 6 of them "sits down", its answer when it is lost | 2 | **2** |

The final suggestions, next to what you wrote:

| your caption | suggested |
|---|---|
| *can't believe it* (video017) | *covers her face* |
| *grabs the kid* (video021) | *lifts the child* |
| *applause* (video021) | *claps hands* |
| *laughing* (video021) | *laughs hysterically* |
| *laughs* (zendaya-age) | *laughing* |
| *hugs him*, *kisses him* (video5) | *hugs the other person*, *hugs the person* |
| *takes it out* (video9, the dynamite) | *holds the dynamite* |
| *Laughter* (deadpool), *laughs* (video018), *laughing* (video021) | *laughing* (the reaction step) |
| *dying* (video021) | *high fives* -- half right: they do high-five there, while laughing hard |
| *realizes it* (video017) | *laughing* -- half right: she giggles there |
| *laughing* (video021) | *looks at child* -- wrong |
| *claps hands* (video10) | *stares at the door* -- wrong |

What asking again cost: three good ones are now left as `*...*` -- *walks in* (you wrote *comes in*), *leaves the
room* (*everyone leaves*: in its six frames the party still stands) and *puts out the candle* (*tries to blow it out*:
the candle is not out yet). Your action captions often say what a moment means (*realizes it*, *understands it*),
which six frames rarely show. So: **fewer suggestions, but most of them right -- for a plain action (clapping,
hugging, holding something) and a laugh; for any other reaction the placeholder stays.**

Two more tries that are not used: letting it give three answers and keeping the one it is surest of when asked again
(23 suggestions instead of 11, two of them exactly yours, *high five* and *laughing*, but about 10 wrong ones, like
*points at wall* and *floats in the water*); and taking every reaction, not only a laugh (13 more suggestions, 8 of
them "shocked" -- right once).

### check-all with the suggestions on (the final code, all 23 videos)

The final code (frozen as `work\snap9i`), the same split as before, two streams, 12:01-12:48 (47 min, nothing else on
the graphics card):

* **Hard checks: 21 of 23 pass** -- the same two known ones as before.
* **The edits are the same as night 8's in all 23 videos** (every clip of the 60 fps plan and of the delivered
  `1_edit.xml`); **the captions differ only where a `*...*` placeholder became a suggestion**
  (`work\t24\compare_AE.py`).
* **The suggestions**: the tool's own edits have 22 placeholders in 10 of the 23 videos; 10 got a suggestion, in 6
  videos -- video017 *covers her face* (you wrote *realizes it* / *can't believe it* there) and *smiles broadly*
  twice, video4 *points at the others*, video1 *puts hand on chest*, video7 *walks away* (you wrote *sits down*
  there), spiderman-school *laughing* twice (the talk-show guests and host laugh there), mike005 *holds the bag* twice
  (where you wrote words: the tool had heard none there). The other 12 stayed `*...*`, among them the model's "sits
  down" in video9 and twice in video3 (doubted when asked again).
* The time of a video with placeholders grows by a few seconds (video7: 1m28s against 1m18s; laptop013 1m55s against
  1m51s).
* Scores: captions exactly as yours, cuts reproduced and framing **unchanged** (132/373, 16/75, 62/110). **Word errors
  go up a little in two videos**: mike005 9.8 -> 16.3 % (the two *holds the bag*) and video7 9.3 -> 10.7 % (*walks
  away*) -- a wrong suggestion counts as wrong words, an empty `*...*` as missing ones. All others unchanged.

Three earlier check-alls of item 5 on the way, all with the same edits: the first used the 4B model, which next to
the second stream spilled out of the 16 GB card (video7's 3 placeholders took 9 minutes instead of seconds -- the
reason the 2B is the default; I stopped that run); the second (09:20-10:08, the 2B without the check) had 13
suggestions -- among them the three "sits down" and *smokes the cigarette*; the third (10:52-11:40, with the check,
before the laugh) had the 8 above without spiderman-school's.


## 6. On the laptop

Everything is committed and pushed to the branch `claude/new-session-ol4era`. On the laptop, in PowerShell, in the
project folder:

```
cd C:\Users\gvida\Desktop\bh\MovieRecaps
git status
git pull
.venv\Scripts\python tools\app\setup_app.py
```

* `git status` first: if it lists files under `tools\match_cuts\` as changed by you (the censor list, say), keep a
  copy, then `git stash` before `git pull` and `git stash pop` after. Your `input\`, `output\` and `finished\`
  folders are never touched by `git pull`.
* On another branch: `git checkout claude/new-session-ol4era`, then `git pull`.
* **The app**: the last line installs what it needs (PySide6, about 100 MB, once) and puts the **MovieRecaps**
  shortcut on the desktop. Then double-click it. (Without the shortcut: `.venv\Scripts\pythonw
  tools\app\MovieRecaps.pyw`.)
* **The action suggestions**: the first run that has a `*...*` placeholder downloads the vision model (about 4 GB,
  once, where the speech models are kept; a few minutes) -- after that it works without the internet. The 2B model
  is loaded for a few seconds, after the speech models are unloaded: measured on the PC (your video7, no caches),
  the run uses about 6 GB of graphics memory while it suggests, less than in its own heavier steps before -- so it
  fits on the laptop's 8 GB card.
  If you do not want it: `--no-action-captions` (or in the batch: `batch input\videos -- --no-action-captions`).
* `git pull` brings one new test case (about 90 MB of video in `tests\real\video10-fixed`); that is normal.
* If a run's end summary ever says "no action suggested for the *...* placeholders (ImportError ...)", the laptop's
  `transformers` package is older than the PC's (5.18): the run is fine, only the suggestions are missing -- tell me
  and I will give you the exact install line for the laptop.

## 7. What I decided on my own

1. **video10: no new rule.** Three caption rules were tried and each made your earlier fixes worse or pulled two
   videos in opposite directions (section 1). The fix is a new test case instead.
2. **Scene splits were already on**: I checked them on all 23 test videos instead of rewriting them, renamed the
   switch to `--no-scene-split` (the old name still works), and made `learn` join the pieces of one take before
   comparing cuts.
3. **The 3 missed shot changes of video9 stay missed tonight**: finding them changes two other rules there (a pause
   cut you kept is dropped, and the competitor's own ending fails the flash check). Better done carefully on its own.
4. **The app's look comes from the website**, because `style.png` was not on this PC.
5. **The app is built with Qt (PySide6)**: a real Windows program in its own window, dark title bar included; it
   needs one install (about 100 MB) on the laptop (`setup_app.py`).
6. **The app runs exactly the batch's command for each video** and reads the result with the batch's own checks, so
   the app and `batch` always agree. Its run folders go to `output\` like the batch's.
7. **Speed per row** (100 % / 125 %, click to change) -- not asked, but it is your two channels and costs one small
   button; `speed.txt` still sets it.
8. **Action suggestions are ON by default** (as you asked: an option to turn it off), with the 2B model on every
   computer (the same suggestions on the PC and the laptop; the 4B, `--action-model big`, was not better on your
   moments and needs twice the memory); the suggestion is plain text in `2_captions.srt` (no mark you would have to
   delete) and is marked as a suggestion in the end summary, the report and the app's "Show report".
9. **Your action captions with obvious typos** were corrected in the examples file ("caughs" -> "coughs", "akward"
   -> "awkward", "chockes" -> "chokes", "jumnping" -> "jumping") so the model does not copy the typos; your answer
   keys are unchanged.
10. **Unit tests never load the vision model** (an environment switch the tests set, like the existing one for
    After Effects); the tests of the suggestions use a stand-in.
11. **The local models get your style as rules, not your list of action captions**: with the list they copied an
    example instead of looking (section 5). The list stays for a stronger online model later.
12. **"Open in Premiere" opens the edit as its own new project** (Premiere's way with an XML); your usual way --
    importing `1_edit.xml` into your template project -- stays as it was.
13. **Fewer suggestions, but right ones**: every suggestion is checked by asking the model again (section 5). It
    leaves more placeholders (32 of your 47 moments instead of 20) but drops 11 of the 13 wrong suggestions; you
    asked to keep the placeholder when it is unsure, and a wrong action you have to spot is worse than an empty one
    you fill anyway.
14. **Of the reactions, only a laugh** is suggested (`*laughing*`): laughing is in 7 of your 47 action captions and
    the frames show it (3 of the model's 4 were yours); its "shocked", "confused" and "thinking" were guesses.

## 8. Check by hand

1. **The app**: double-click MovieRecaps on the desktop, drop a RAW and a competitor (or `input\videos`), press
   Start. If the look should be closer to your `style.png`, put the picture in the project folder and tell me.
2. **The action suggestions** in a real run (the final code): `work\t24\runE\G2\runs\video017-fixed\001` (3
   suggestions), `...\spiderman-school\001` (2 x *laughing*), `...\video4\001`, `...\video7-fixed\001`; at full
   size: `work\t24\app_e2e\run2\output\video7` (*walks away*, *covers her mouth*). Open `1_edit.xml` with its
   `2_captions.srt`, or read `extras\report.md` ("Suggested actions"). The "Captions worth a look" list at the end of
   every run lists them too.
3. **Open in Premiere** once: it opens the edit as a new project named after the run, in the folder of your last
   project -- your usual import into the template project works as before.
4. **The new test case** `tests\real\video10-fixed` -- your fix as the answer key (nothing to do; check-all scores it).

Everything tonight's runs left in `work\t24\` (the check-alls, the app's test runs, the model tests) and the frozen
code copies `work\snap9a` ... `work\snap9i` can be deleted once you have looked: none of it is needed by the tool
(`work\cache`, the stage caches, I left alone). The two vision models are in `models\hf` (12 GB together): keep the
2B one (the default, 4 GB); the 4B one (8 GB) only matters for `--action-model big`.

## 9. Left for next time

1. **The 3 shot changes the tool misses in dark, grainy films** (video9): the detector rule is ready (it finds
   exactly these 3 and nothing else on 25 RAWs), but the pause-cut guard and the ending's flash check must learn to
   handle a shot change one frame after a pause cut / 4 frames before the competitor's end first.
2. **Captions of a competitor that writes whole sentences** (video10): your fix used your own 2-3 word groups; the
   tool copies a competitor whose captions look like yours by their length -- video10's were counted short because
   of OCR noise, video020's would then go the other way. Needs more examples.
3. **`style.png`**: if it differs from the website's look, put it in the project folder and I will match it.
4. **Two short flash frames in video021 at full size with `--fast`** (your `input\` pair: 2 frames at 00:00:05:16, 5
   at 00:00:32:00) -- the same with night 8's code; the run says "OK with a note". Worth a look in Premiere, and a
   look at the rule next time.
5. **Action suggestions for reactions other than a laugh** (*realizes it*, *shocked*, *can't believe it*): the local
   model rarely gets them from six frames. An online model (one more class in `action_captions.py`, with your 74
   action captions as examples) is the next step if you want them -- it costs money per use and sends the frames out
   of the PC, so I did not add one without asking you.

## 10. The PC

No Adobe program was open during tonight's runs (only Adobe's background services). I opened **Premiere Pro 2024
twice** for about a minute each (08:58-09:03) to test the app's "Open in Premiere" button, with nothing else running,
then closed it; my two test projects in your last project folder (`Desktop\scene\Copied_survey_POPW`) are deleted
again, your files there untouched. The check-alls, the app's first and third test runs and the night-8 comparison
of your `input\` pair ran with nothing else on the graphics card, so their times are free-PC times. The app's second
test run shared the PC with a unit suite (on the processor) and one model test, so its times say nothing; none of
them are used above. Your `input\` and `output\` folders are as they were (the app's test runs wrote into
`work\t24\app_e2e\`; the learn test's copies are deleted again); in `finished\video10` I added one file,
`video10_fixed_relinked.prproj` (section 1).

At the end, as you asked, I checked that no run was still going and that this report was saved, committed and
pushed, and then shut the PC down (`shutdown /s /t 120`).
