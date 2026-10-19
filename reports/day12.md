# Day 12 (19 October): learning from your fixes and from the tool's own problems, one RAW file, the New video form, torrents, real titles, your choice of RAW, newest on top

## In short

Everything on the main list (sections 1-9) is done, committed and pushed; then the whole extra list (item 2 looked
at, nothing safe to change). Two things work differently from the letter of the prompt, both on purpose ("decided on
my own"):
the torrent engine is **aria2c**, not libtorrent (libtorrent has no version for this Python, 3.14), and the real
titles go into **titles_found.txt**, not title.txt (title.txt holds *your* headline; writing the competitor's titles
there would overwrite it).

1. **Learned** from video14 and video15 (video13: captions only -- the same text as video14). Two caption rules:
   **fu\*k** as you write it, and a **doubled action caption joined**. Two new test cases. check-all before -> after
   (all 28 test videos): **every earlier video exactly the same** (captions 183 -> 183 of 469, cuts 23 -> 23 of 94,
   framing 72 -> 72 of 139, the same hard checks); video14's captions 15 -> 16 of 42 (word errors 17 % -> 9 %).
2. **The problems list**: every FAIL / "OK with a note" run goes into `problems_queue.md` by itself; the Learn tab has
   a "Problems to fix" column; tonight's prompt carries them. **Both problems are found and fixed**:
   * **darkhumor (exit 3)**: the run had *passed* everything. Exit code 3 is the tool's own "nothing failed, but one
     check could not be done" -- every Premiere-only run has no After Effects check, so a video that passes all the
     rest ends with 3. The app (and the batch) took every code except 0 and 1 for a stop and showed the last line
     the tool printed to the error stream, which was a harmless OpenCV warning. Fixed: 3 is a finished run, and a
     library warning is never shown as the reason.
   * **mindculpt (person outside the window)**: the competitor shows the *whole* wide Rush Hour picture; your template
     window shows only its middle half, so the person speaking was cut off (0:07) or nobody was in the window at all
     (0:27). Nothing was re-framed because the tool keeps the competitor's framing. New narrow rule: **only when the
     window shows nobody whole and the person speaking is cut off**, the picture slides sideways until they are in.
     Re-run: the XML PERSON check passes, every moved shot shows the person speaking (pictures checked). It changes
     no earlier video (check-all: all the same).
3. **One RAW file**: a 5.1 run keeps only `raw_stereo.mp4` (picture copied as it is, stereo sound) -- the 8 GB
   analysis copy is removed at the end. Board -> Clean up does the same for old runs, after checking and asking.
4. **Your choice of RAW**: every queue row says "RAW: episode.mp4 (22:41) · COMP: short.mp4 (0:58)", has **⇄ Swap**
   (also on a running row: it restarts with the files switched, nothing copied again), and two videos within 30 % of
   each other wait amber -- "Check which is the RAW" -- until you pick.
5. **Newest on top** in the queue, Download, the board, History, Learn; rows glide; the run order is unchanged.
6. **One New video form** (YouTube / magnet / .torrent / file, competitor, channel, two-line headline, YouTube title,
   "Will make"), **Just cuts** for a YouTube link alone, **torrents**, **steps that wait for your buttons** unless
   **Automatic** is ON, **turn off the PC when done**, plain errors with **Try again**.
7. **Real titles**: the post's own title (cleaned) and the headline written on the competitor's video (read by
   the vision model), each with Copy / Use as my headline / Use as YouTube title; folders named after them.
8. **Learn waits by itself**: a "Waiting for your fix" card for every finished edit; drop the .prproj on it (or save
   it into the project folder) and press Learn this one; amber when the render appears.
9. **Suggestions**: 8 ideas at the end of this report, also in `suggestions.md` and in the Learn tab (I want this /
   No).

Tests: the app **984 passed**, 0 failed (743 last night); the tool **1451 passed**, 0 failed (with the graphics card
and the slow end-to-end tests; 1421 last night). Also: two reviews of all of today's code found **18 real problems**
(the worst: "turn off the PC" could fire while a new video or a copy to the shared folder was running, a failed
torrent start kept it from ever firing, and torrent Remove could delete another download's file) -- all fixed, each
with a test; a **real end-to-end run** of the New video form (real downloads, the real title model, the real edit)
found and fixed six more. Found on the way and fixed too: **C: was full**
(0.5 GB free -- my own re-runs of the Rush Hour problem, 17 GB each; cleaned, about 22 GB free now), the laptop's
**PySceneDetect was too old** (setup_app updates it), and five small bugs from clicking through the real window.

## 1. Learned from video13, video14 and video15

**video13 / video14 (ebola, Meet the Blacks):** both were in the learn queue. video13's V1 is one rendered video
(`D:\Shared\Recreated Edit (Premiere)_apo8_prob3.mp4`), not RAW clips -- the wrong version, as you said -- so from
video13 only the captions were read: its 42 captions have **the same text** as video14's (times within a few
milliseconds), so it adds nothing new. video14 was learned in full, and so was **video15** (the Doritos ad, made
from the laptop's `1018(1)` run), which was also open in the queue. All three are marked learned in learn_queue.md.

Only captions, cuts and video position / scale were compared (not the headline PNG, memes, music, audio, pitch,
or your trims at cut points and the ending).

| what you changed | seen before? | done |
|---|---|---|
| video14: **"fuck" written "fu\*k"** -- all 5 times (also "fu\*king"); "shit" kept | the only other swear word in your fixes, "goofy shit", you kept too | **rule**: `caption_censor.txt` writes fuck, fucking, fucked, motherfucker ... as fu\*k, fu\*king ... ("shit" stays) |
| video15: the same action caption twice in a row (`*gives the ultrasound*` x2) became one | no fix had a doubled action before; words said twice you always keep ("nah nah", "Pam Pam", "you you", "no no") | **rule**: neighbouring action captions with the same text become one caption over both (spoken words untouched) |
| video15: your own actions in every `*...*` spot (`*crunching sound*`, `*baby hits stomach*`, `*doritos AD*` ...) | as always | not a rule (your words); not added to the action examples (as on night 11) |
| video15: single words joined ("you're" + "eating", "Give" + "me" + "That!" -> "Give me that") | over all your fixes: 59 single-word captions kept alone, 36 joined | not a rule -- no clear pattern (in the suggestions) |
| framing: video14 moved 7 clips sideways (median -390 px), video15 9 clips (median -168 px), mostly onto the person speaking | video11 7 of 8, video12 14 of 20 -- the same habit; but video6 kept all, and on 021 / laptop004 you moved re-framed clips back | not a general rule (tried on nights 6 and 11: it put clips on the wrong person); the narrow rule of section 2 instead |
| video14: 2 clip edges moved, a 0.21 s cut inside a clip; video15: 4 small cuts inside clips | trims at cut points | yours by hand, as you asked |

Two new test cases: `tests\real\video14-fixed` and `tests\real\video15-fixed` (each with its channel template and
watermark, as the laptop ran them).

**check-all before -> after** (before = night 11's check-all, the same code until this morning; after = today's
final code, all 28 test videos, `work\t27\checkallB`):

| | before | after |
|---|---|---|
| hard checks | 3 known FAILs (video6: night 10's one-frame flash; video12: the competitor's own last frames; video020: the known --fast flash) | **the same 3**, nothing new |
| captions exactly, the 14 keyed videos | 183 of 469 | **183 of 469** (each video the same) |
| cuts reproduced | 23 of 94 | **23 of 94** |
| framing as you had it | 72 of 139 | **72 of 139** |
| **video14-fixed** (new) | captions 15 of 42, word errors 17 % (the same run without the fu\*k rule) | **16 of 42, word errors 9 %**; cuts 1 of 3, framing 0 of 10 (your 7 sideways moves); hard checks: "XML SYNC" -- a one-frame skip at one cut inside a take (thorough run only; the laptop's run did not have it; not caused by today: run A this morning had it too) |
| **video15-fixed** (new) | -- | captions 5 of 22 (your own action texts), cuts 3 of 8, framing 12 of 21, PASS |

Nothing got worse. The doubled-action rule did not fire in check-all (the model did not suggest the same action twice
in these runs); it is covered by a test.

## 2. The problems list

**How it works now** (`core/problems.py`, in the Learn tab):

* Every app run that ends **FAIL** or **"OK with a note"** (picture not found, a flash, sync / repeat notes, caption
  problems, the person outside the window, a crash) is written into **`problems_queue.md`** in the shared projects
  folder by itself: the video, the run folder, the date, the computer, the failed check or note, and the key lines of
  its log (library warnings left out). The run's files are kept (Clean up never deletes an edit folder).
* **WRONG PAIR, NOT IN THIS RAW, the input checks, a missing file, a project locked by the other computer and your own
  Stop are not added** (wrong input, not a tool problem).
* The Learn tab's middle column **"Problems to fix tonight"**: newest first, a red count, **Open log** and **Skip (my
  mistake)** on each.
* **Copy tonight's prompt** now carries the open problems with the learn queue and the wishes, with the rules (re-run,
  find the cause, fix, test case, check-all, nothing worse, mark "fixed <date>" / "not fixed -- <reason>").
* A night can mark them (`fixed`, `not fixed`, `skipped`); a skipped run never comes back.

**The two problems, re-run on this PC** (`D:\Shared\problems\...`, the folders you put there):

**darkhumor -- "stopped (exit 3)" with only an OpenCV warning -> fixed.**
* The run had finished and **passed** every check (its log: "match_cuts result: PASS (criterion 6 not verified:
  Premiere-only run)"). Re-run here with today's tool: the same, PASS, exit code 3.
* Exit code 3 is the tool's own "nothing failed, but a check could not be done" -- every Premiere-only run (`--premiere`)
  has no After Effects check. Real videos almost always end with 1 (one recreation criterion is always off), so a run
  that passes *everything* is rare -- and it was the one case the app had never seen: it took every code except 0 / 1
  for a stop and showed the last line of the error stream, a harmless OpenCV warning printed 3 minutes earlier.
* Fixed: 3 is a finished run when the run printed its result (the app and the batch) -- a real crash on Windows can
  also end with 3, and then it is a stop with its own words; a library warning ("[ WARN ...", "...Warning:") is
  never shown as the reason; a crash's code is said in plain words ("a memory access error inside a program library").
* My first guess (a crash while Python closed) was wrong and is taken back in the next commit; the learnbox helper
  found the real meaning of 3.
* Also seen in its log: the laptop's **PySceneDetect is too old** ("No module named 'scenedetect.output'" on every run):
  `setup_app.py` now installs 0.7.1.

**mindculpt -- "the person speaking is outside the window" (XML PERSON) -> fixed.**
* The competitor shows the **whole** 2.39:1 Rush Hour picture in a band; your template window is nearly square, so
  it shows only the middle half of the picture. At 0:07 Chris Tucker speaks at the right edge (a third of his face cut
  off); at 0:27, a two-shot, the window's middle shows lanterns and a hand -- nobody.
* **Why nothing was re-framed:** the tool keeps the competitor's own framing (since 021 / laptop004: re-framing onto
  "the speaker" put clips on the wrong person), and the night-8 slide for a person the narrower window cuts is off for
  a competitor that shows one fixed crop (your video6: you kept the middle on all 9 clips).
* **Should it re-frame by itself?** Your fixes say: yes when the window shows nobody -- but carefully. In video11,
  video12, video14 and video15 you moved most clips onto the person speaking; in video6, 021 and laptop004 you kept or
  moved back. What all of them agree on: a window that shows **nobody whole** is never what you keep.
* **The new rule** (on by default; `MATCH_CUTS_SPEAKER_CUT_OFF=0` switches it off for one run): for each RAW shot of
  each clip -- only where the window shows **nobody whole**, the person **speaking** (Light-ASD, never just "the
  biggest face") is cut off, and the competitor's own (wider) window showed them -- the picture slides sideways (zoom
  kept) just until their face is inside with a little margin, at most half the window's width. A clip that shows
  anyone whole keeps the competitor's framing (021 / laptop004 stay as they are).
* Re-run with it: **XML PERSON passes**; 3 clips moved (in 8 of their RAW shots); I looked at every moved shot before
  / after -- each shows the person speaking whole where the laptop's edit cut them off (Jackie Chan's and Chris
  Tucker's close-ups, Chris in the two-shot); none moved onto the wrong person. Pictures:
  `work\t27\p1\sheet\s1.png`, `s2.png` (left = the laptop's edit, right = today's).
* It changes **no earlier video's framing**: check-all's framing scores are the same on all 28 videos (72 of 139 on
  the keyed ones), and no earlier test video has a clip it moves.
* Test cases: unit tests with the mindculpt layouts (tools\match_cuts\tests\test_day12.py). A check-all case needs your
  fixed version -- when you fix mindculpt in Premiere, drop it on its "Waiting for your fix" card and it becomes one.
* Also found: the app saved this run as "OK with a note" although the tool's headline said FAIL -- that is by design
  (the app's word comes from the hard checks of 1_edit.xml; the tool's FAIL is a recreation criterion every real
  video fails).

Both entries are marked "fixed 2026-10-19" in problems_queue.md, with what was found.

## 3. One RAW file instead of two

* **What was there:** a 5.1 RAW's run folder held the RAW twice -- the analysis copy (`raw.mp4` / `raw_ae.mp4`, e.g.
  the Rush Hour conform: **8 GB**) and `raw_stereo.mp4` (8 GB) -- and 1_edit.xml already played only `raw_stereo`.
* **Now:** `raw_stereo.mp4` is the one file -- the picture **copied as it is** (`-c:v copy`, the same frames) with the
  stereo AAC mix as before -- for V1 and A1 in 1_edit.xml and the Premiere project. The analysis copy is removed at the
  very end of the run (after every check has read it), only when raw_stereo has the same number of frames and no XML
  plays the copy. The name stays `raw_stereo.mp4`: every check, Learn, the published projects and your fixed projects
  already know it. Stereo RAWs stay as they are.
* **Checked on a real 5.1 file** (2 minutes of Dude, Where's My Car): all 2880 video packets identical, the sound
  starts at 0 with the same length, 0 samples of lag at 5 s / 60 s / 110 s. Today's mindculpt re-run: "one RAW file:
  media/raw_ae.mp4 removed (7.87 GB)".
* **Board -> Clean up**: finds old runs holding both files, checks raw_stereo opens as the same video (frames, length,
  stereo, sound from the start), checks that **no XML in the edit folder and no .prproj in the project plays the old
  copy** (projects only read -- your `*fixed*.prproj` are never written), shows the space saved and asks; only on Yes
  (after checking again) the old copy is deleted. A run whose XML or project still plays the old copy keeps both
  (swapping the path of a 6-channel file in a project is not safe) and says to relink in Premiere.
* **Learn** finds runs by `raw_stereo` now (it also had a bug from night 6 on: a project that played raw_stereo
  "played no clip of the run's RAW" -- fixed).
* **Premiere**: the 5.1 re-run's edit (Dude, Where's My Car) opened with the app's own **Open in Premiere**: Premiere
  imported it without any warning, its project lists `raw_stereo.mp4` (the only RAW) and the sequence. Watching lips
  in sync I could not do: a Windows Security question (below) took the screen and my clicks no longer reached Premiere
  -- so sync is in "Check by hand" (the XML's own sync checks passed, and V1 / A1 play the same file).
* Cost: a second competitor of the same 5.1 movie converts the RAW again when it is not AE-safe (Rush Hour: about 8
  minutes) -- the conversion comes out byte for byte the same, so the analysis caches are still used (checked on the
  Rush Hour re-runs: "cache hit" for the sound, layout and frame map). In the suggestions.

## 4. RAW vs competitor -- always your choice

* Every queue row: **"RAW episode.mp4 (22:41) · COMP short.mp4 (0:58)"** (RAW in coral, COMP in blue; the lengths are
  read in the background).
* **⇄ Swap** on the row and right-click -> "Swap RAW and competitor": a waiting row switches; a **running** row stops
  and starts again at once with the files switched (TAKE 2) -- nothing is copied again, only project.json's roles
  change; a finished row comes back as its next take.
* **Two videos within 30 % of each other** whose names don't say (raw\* / comp\*): the row turns amber, "Check which is
  the RAW", with **"clipA.mp4 (0:45) is the RAW" / "clipB.mp4 (0:47) is the RAW"**. It never starts by itself before you
  pick -- not with Start, not with the queue running, not with Automatic ON. The pick is remembered in project.json.
* **Your pick counts as Run anyway**: a 47 s RAW for a 45 s Short runs (`--force-inputs`, plus `--no-swap` -- found on
  the way: --force-inputs alone let the tool swap the files back because the shorter one looked like the competitor).
  Note: Run anyway also skips the quick WRONG PAIR check and the picture-not-found stop for that pair.
* **A video from the New video form** needs no pick: its RAW field is your choice (no amber wait), but it keeps the
  WRONG PAIR check -- only night 6's "almost as long as the RAW" stop is skipped (new tool option `--allow-short-raw`).

## 5. Newest on top

* **Match cuts queue**: running rows at the very top, then waiting rows newest first, then finished rows (the one
  finished last on top). A row that changes state **glides** to its new place; a new row glides the list to the top.
  **The run order is unchanged**: videos run in the order added; dragging a waiting row changes its run place, and
  every waiting row says when it runs (RUNS 1ST, RUNS 2ND ...) -- a dropped row stays where you drop it.
* **Download / New video rows**: the same rules and the same gliding list.
* **Board**: the newest card on top in every column, pinned ★ cards above (cards fade in; they don't glide).
* **History**, Learn's three columns: newest first.

## 6. New video, torrents and step buttons

**6a The form** (Progress -> Download): RAW (a YouTube link, a magnet link, a dropped .torrent or a video file) with
a small **YOUTUBE / TORRENT / FILE** label; Competitor (optional, link or file); Channel buttons with their dots (the
last one remembered); **Headline**: a two-line box (Enter = line 2, emoji fine, a green READY label) -- your break
is where line 1 ends; typed without Enter it is split and shrunk like night 10's renderer; YouTube title (optional);
**"Will make: Full Match cuts edit / Just cuts (every scene change)"** before you press anything; **Many at once**:
one pair per line, `RAW link | competitor link`.

**6b The three ways:** YouTube + competitor and magnet + competitor -> download, convert (x264 CRF 13), template,
project, card, Match cuts edit. **YouTube alone -> Just cuts**: download, convert, then 1_edit.xml + project of the
whole video cut at every scene change (no shot under 0.5 s cut out), at the video's own rate, the audio chain on every
audio clip, your channel's template when there is a headline; no captions, no matching, no WRONG PAIR
(`python -m match_cuts.just_cuts`). The row and the card say "Just cuts"; **Add a competitor** on the finished row
turns it into a full edit without downloading the RAW again. **A magnet alone is refused**: it asks for the
competitor.

**6c Torrents** -- with **aria2c** (libtorrent cannot be installed for this Python: its last version is for 3.13;
setup_app downloads the official aria2c and checks it). Before downloading: the file list with sizes, the total, the
seeders (asked from the trackers), the free space; the largest video ticked ("BIGGEST VIDEO"); **Start**. While
downloading: seeders, peers, speed, % done, time left, a 5-bar health meter, **Pause / Resume / Remove**; it continues
after an app restart. Sharing stops when it finishes (Settings: "Keep sharing after a download", off). Saved to
`D:\Shared\Downloads\Torrents` (laptop: `\\192.168.101.11\Shared\Downloads\Torrents`; Settings -> "Save torrents to").
Free space checked first. Tested end to end with a real torrent between two aria2c on this PC (127.0.0.1 only --
nothing from the internet).

**6d Steps wait for your buttons** unless **Automatic: ON** (top of Download; off by default; remembered): Download
finished -> **Convert** + Open folder; Convert finished -> **Send to Match cuts** (or **Make the cuts**) + Open folder;
then template and edit run by themselves. The row's bottom line says what it waits for ("Downloaded -- press Convert
when you want"); each step has its own gliding bar and finish time (blue running, green done). **"Turn off the PC when
everything is done"**: after the last download / convert / edit of the whole app, a 60-second warning with Cancel,
then shut down -- never while Premiere's title shows unsaved work or a render / export is running (it waits and tries
again every 30 s); not remembered after a restart (tick it each time). **No headline + Automatic ON**: the channel's
template as it is, finished, amber "default template" with **Change headline** (makes only template.png again and
swaps it into the edit, no new run). **No headline + Automatic OFF**: the Template step is amber "needs a headline" with
the two-line box and **Make the template**. This replaced the old Download tab's automatic convert-and-send.

**6e Plain errors**: a broken link, a private or removed video, no one sharing a torrent for a long time (it pauses),
a full disk; a failed step stops the row with a red pill and **Try again** continues from that step. Everything goes
into History with its date (also a video that fails before its folder exists).

## 7. Real titles from the competitor's video

* **Title from the post**: the post's own title / description (Instagram, TikTok, YouTube, via yt-dlp), cleaned -- no
  #hashtags, @names, links, at most 3 emoji, at most 90 characters; "Video by darkhumor" counts as nothing.
* **Headline on the video**: four frames of its first ~3 s read by the tool's vision model (Qwen3-VL, in its own
  process after the conversion), the two lines kept as two lines (using the OCR's line break), emoji kept when
  readable. Tried once for real (finished\video3: "They stopped making comedy like this", 80 s with the model's load).
* Shown on Download rows, queue rows and board cards, each with **Copy**; the headline also with **Use as my
  headline** (fills the two-line box -- before the edit; on a finished edit it asks, then Change headline) and **Use
  as YouTube title**.
* **Folder names** from the post title, renamed after the conversion from the headline on the video when the name came
  from a useless title (short, Windows-safe); nothing readable -> it says so and keeps the old name. Both titles are in
  project.json and `titles_found.txt`.

## 8. Learn waits by itself

* When an edit finishes (PASS or OK with a note), a card appears under **"Waiting for your fix"**: the project, its
  channel, RAW, competitor, the date and the next free name ("will be video16").
* Drop your fixed .prproj **on the card** (or save a .prproj into the project's folder -- noticed within a minute):
  the check card opens at once with the run already chosen -> **Learn this one**.
* When the render appears (the board card moves to Rendered), the card turns amber, "Rendered -- add your project
  file", and the LEARN tab shows a small count.
* **Skip** on each card; the list (`learn_waiting.json`) is in the shared folder, so the PC and the laptop see the
  same cards. (A Just cuts edit gets no card: there is nothing to learn without a competitor.)

## 9. Suggestions for you

From what I saw today, most useful first. **Not built.** They are also in `suggestions.md` in the shared projects
folder and in the Learn tab's third column, with **I want this** (goes into requests.md for the next night) and **No**.

| # | idea | why it helps you | size |
|---|---|---|---|
| 1 | **Re-frame onto the person speaking in more clips** | in all four of your newest fixes you moved 7-9 clips sideways onto the person speaking; today's rule only moves shots that show nobody -- a careful speaker check could save most of those moves (3-5 minutes a video) | a full night |
| 2 | **One GPU queue for the whole app** | two edits and a check-all on the graphics card at once made each many times slower today (a 15-minute case took over an hour); the app could run GPU work one at a time and say "waiting for the graphics card" | half a night |
| 3 | **Warn before C: fills up** | C: went down to 0.5 GB today and a run stopped with "No space left on device"; the top strip could show C:'s free space, turn amber under 30 GB and offer Clean up | small |
| 4 | **Better suggestions for the `*...*` spots** | in video15 you replaced every suggested action with your own; your earlier action captions as examples (or the competitor's own on-screen text) would cut that work | half a night |
| 5 | **"Re-run with today's tool" on each problem** | a night re-runs every open problem by hand; one button could re-run it with the same settings and show before / after | small |
| 6 | **The same versions on the laptop as on the PC** | the laptop had an old PySceneDetect (an error in every run); the app could compare its packages with the PC's at start and fix them with one click | small |
| 7 | **Join one-word captions the way you do** | caption grouping is the most common difference between the tool and your fixes (110 of 287 caption differences); but you keep some single words and join others, so it needs a careful rule | a full night |
| 8 | **Keep the converted RAW for the next Short of the same movie** | since today a 5.1 run keeps only raw_stereo, so a second Short from the same film converts the RAW again (minutes); one shared copy per movie would avoid it | half a night |

## Extra work (section 12)

1. **Other open problems**: none (only the two above).
2. **Caption accuracy**: looked at, nothing changed. Over all your keyed fixes the differences are: how captions are
   split (110 of 287), timing (100), words (61), capitals (14). The timing differences are a different shift per
   video (laptop009 mostly +3 frames, video021 mostly -3), not one rule; single words you kept alone 59 times and
   joined 36 times. No change passed "no video gets worse" on paper, so none was made; the clearest one (joining
   words) is suggestion 7. (The two learned rules of section 1 are the caption work of today.)
3. **Everything used in the real app** (on the Windows desktop, not just offscreen; scratch folders, nothing real
   written, the PC never turned off): every tab and new button, drag and drop on the New video zone, the queue and a
   Learn card, a real local torrent. Fixed: a dropped .torrent was treated as a video file; the Download count said
   "1 running" when nothing ran; a Learn column stayed blank after its last card; board cards cut off their last lines;
   a Just cuts project was never published (and a toast with a class name kept coming); Clean up counted a hard-linked
   copy twice; a row gliding up was cut off; torrents: "nobody sharing" failed the row (now pauses), Remove left the
   row "running", Try again started a second download, Stop did nothing. And **Copy tonight's prompt** knew only
   `reports\nightN.md`: after today it would have sent the next night to night 11's report -- it now counts this
   `reports\day12.md` (the next prompt is "Night 13").
4. **The last real videos re-run with today's tool** (from their own media, the laptop's settings, `--fast`):

   | video | the laptop's run | today |
   |---|---|---|
   | darkhumor (Family Guy S10E18) | shown as "stopped (exit 3)" | **PASS** (exit 3 = finished now) |
   | mindculpt (Rush Hour) | OK with a note (XML PERSON) | **PASS** -- the person speaking now in the window |
   | before-tobey-maguire (spider-man) | PASS | **PASS** |
   | family-guy-i-cant-believe-its-not-butter | PASS | **PASS** |
   | wasted (Dude, Where's My Car, a 5.1 RAW) | PASS | **PASS** -- and its run folder holds the RAW once (raw.mp4 removed, 1.53 GB) |
   | AQO (Meet the Blacks) = video14, 1018(1) = video15, chocolate | -- | in check-all (section 1) |
   | always-sunny | OK with a note: "XML SYNC" -- the RAW skips one frame at a cut inside one take (0:04) | **OK with a note**, the same kind (at 0:07 this time: re-run from the stereo copy, so the analysis differs a little) -- the competitor's own one-frame jump, which you trim by hand; not a tool fault |

   Plus a **real end-to-end run of the New video form** by a helper (real downloads, the real conversion, the real
   title model and edit; only the shutdown faked): the full edit PASS (download 9 s, convert + titles 2 min, edit 16
   min while the card was shared), Just cuts in 9 s (11 cuts), Add a competitor PASS without downloading the RAW
   again, a broken and a private link in plain words with Try again. It found and fixed six things: the headline read
   off a tweet-style video included the account name and doubled words; a Download row waited for ever without
   saying why (now: the form's RAW field counts as your choice -- I made that call); long file names on the pick
   buttons; "Add a competitor" made the full edit look like Just cuts in History; "Title from the post: nothing
   readable" after Add a competitor; a "NO TITLE yet" warning with the default template.
   **A review of all of today's code** found 12 real problems, all fixed with tests: "turn off the PC" could fire
   while new work or a copy to the shared folder ran (now it waits for both, and stops its countdown when work starts),
   a refused shutdown was not said, an unreadable Premiere did not block it; torrent Remove could delete another
   download's file of the same pack, a name with ":" was not found; exit 3 from a real crash counted as finished (now
   only with the run's result line); two computers' waiting cards with the same folder name replaced each other; a
   shared project could be renamed; Clean up did not re-check the lock just before deleting; Swap on a running row
   could fail as "locked"; and a guard so tests can never shut the PC down. **A second review** of those fixes found 6
   more, also fixed with tests: a torrent that failed to *start* kept "turn off the PC" waiting for ever; the form's
   RAW choice must not skip the WRONG PAIR check (see "decided on my own" 8); a rare Swap race could end the queue
   before the swapped video restarted; the 5-minute shared-folder retry bounced the shutdown countdown; a "Fatal
   Python error" line hid a WRONG PAIR message; the headline clean-up also removed real doubled words ("had had").
   The last tool changes after check-all are only in `cli.py`, `inputs_check.py` and `batch.py` (none is part of the
   analysis; the new option is not used by check-all), so the check-all numbers stand.
5. **Over 100 ms on the window's thread**: "Send to Match cuts" took up to 138 ms -> 1.2 ms (moved to the background, with
   Add a competitor, the YouTube title and the template dialog's save); reading the channels folder while the form
   is built (~150 ms) moved too. After that no gap over 100 ms once the window is up (the longest handler 28 ms).
   Left: while the graphics card is at 100 % (an edit or check-all running), full-window repaints take 50-140 ms and
   twice ~1.9 s -- that is Windows drawing the window while the GPU is busy, no Python code runs then.
   **The night-11 look rules** were checked on every widget added today (hovers only fade, in 0.3 s / out 0.5 s,
   nothing moves or grows, Animations off makes everything instant, the mock-up's colours and fonts): all new widgets
   follow them (9 new motion tests); fixed: the small labels' letter spacing was 1 % instead of the mock-up's 10 %, the
   YOUTUBE / TORRENT / FILE label switched colour at once (now fades), and a Download row checked the disk (maybe the
   network folder) for its edit on every refresh (now in the background, once).
6. **More tests** for today's untested pieces: 37 more (error paths and plain messages of shutdown, problems,
   waiting, New video, torrents; the titles block, scroll to top, Learn columns, health bars; Just cuts' cut list).

## Tests

| suite | night 11 | day 12 |
|---|---|---|
| the app (`tools\app\tests`) | 743 passed | **984 passed**, 0 failed (offscreen, the graphics card hidden) |
| the tool, graphics card hidden | 1396 passed, 97 skipped | **1422 passed**, 97 skipped, 0 failed (14 min) |
| the tool with the graphics card (and the slow end-to-end tests) | 1421 passed, 73 skipped | **1451 passed**, 73 skipped, 0 failed (23 min) |
| check-all, 28 test videos | 26 videos | all 26 earlier ones the same as night 11, 2 new (section 1) |

New tests today: the tool's `test_day12.py` (exit codes, warnings never the reason, fu\*k, doubled actions, the
speaker rule per RAW shot), `test_just_cuts.py`, the one-RAW-file tests; the app's problems / waiting / suggestions
(`test_learn_day12.py`, `test_learn_columns_ui.py`), the queue (`test_queue_day12.py`), New video
(`test_newvideo*.py`), torrents (`test_torrent.py`, one real transfer on 127.0.0.1), titles, the integration
(`test_integrate_day12.py`), the extra list (`test_extra_day12*.py`), the review's fixes (`test_fixes_day12.py`),
and a guard so **no test can ever turn the PC off**.

## What I decided on my own

1. **Torrents use aria2c, not libtorrent.** libtorrent cannot be installed here: its newest version is for Python 3.13,
   the app runs on 3.14. The prompt allowed aria2c in that case; setup_app downloads the official build and checks its
   checksum. It does what was asked (file list, seeders, pause / resume, continue after a restart, stop sharing).
2. **The real titles go into `titles_found.txt` (and project.json), not title.txt.** title.txt is your headline /
   YouTube title -- the board and the shared folder read it back as yours, so the competitor's titles there would
   overwrite your own.
3. **Exit code 3 is a finished run** in the app and the batch (the tool's "PASS, one check not possible").
4. **The person-speaking rule is narrow and on by default**: only shots that show nobody whole, only the person
   *speaking*, only inside what the competitor itself showed, only a slide (at most half the window). A general "move
   onto the speaker" is not in (it put clips on the wrong person on nights 6 and 11) -- it is the first suggestion.
5. **What was learned**: two caption rules (fu\*k; doubled actions joined). Not rules: one-word joins (59 kept vs 36
   joined in your fixes), your action texts (not added to the examples, as on night 11), your framing moves beyond the
   narrow rule, trims at cut points.
6. **video15 was learned too** -- it was open in the learn queue (the prompt named only 13 / 14).
7. **One RAW file keeps the name raw_stereo.mp4** (every check, Learn and your projects know it). An old run whose
   XML or project still plays the 6-channel copy keeps both files (swapping that path is not safe); Clean up says so.
8. **Your RAW pick on a queue row = Run anyway** (`--force-inputs`), which also skips the quick WRONG PAIR check for
   that pair; plus `--no-swap` so the tool cannot swap your pick back. **The New video form's RAW field also counts
   as your choice** (no amber wait: the real end-to-end run had a 30 s RAW for a 26 s Short stuck there) -- but it
   keeps the WRONG PAIR check (a wrong link still stops) and only skips night 6's "almost as long as the RAW" stop,
   with a new narrow tool option `--allow-short-raw`.
9. **Waiting rows**: shown newest first as the run order upside down, each saying RUNS 1ST / 2ND ...; a dragged row
   stays where you drop it (the alternative made dropped rows glide back).
10. **No headline + Automatic ON** uses the channel's template.psd exactly as saved (its own example headline); **Turn
    off the PC** is not remembered after a restart (tick it each time); a **RAW file without a competitor** also makes
    Just cuts; a Just cuts edit gets no "Waiting for your fix" card (nothing to learn without a competitor).
11. **"Use as my headline"**: before the edit it just sets your headline (the template is made from it); while the
    edit runs it waits; after the edit it asks, then makes only template.png again and swaps it in.
12. **Board cards fade into their new place** instead of gliding (the glide was asked for rows; cards are rebuilt).
13. **check-all ran with one stream**, not two: with two streams, my problem re-runs and the desktop holding 4.3 GB of the
    graphics card's memory, two cases took two hours each. I stopped that run and ran the final one alone on the card
    (one stream, then a second one from the end of the list once nothing else ran): all 28 in 1 h 45 min.
14. **My own scratch was deleted when C: filled up** (only today's re-run folders and finished helper worktrees, 46 GB);
    no cache or file of yours was touched.
15. Made the folder `D:\Shared\Downloads\Torrents` (the laptop's torrent folder is this folder over the network).

## Check by hand

0. **A Windows Security window may be on the screen**: "Do you want to allow public and private networks to access
   this app? aria2c.exe". It came from today's torrent tests (aria2c is the torrent engine). **Allow** (private
   networks is enough) lets other people's torrent programs connect to yours -- faster downloads; **Cancel** works
   too, downloads are just slower. I could not answer it (Windows does not let a program click that window).
1. **One RAW file in Premiere**: in Premiere, File > Import `work\t27\rerun\runs\wasted\1_edit.xml` (or press Open
   in Premiere on your next 5.1 movie): the edit plays `raw_stereo.mp4` on V1 and A1 -- check lips in sync at the start, middle and
   end; apply your audio preset as usual. `extras\media` holds only `competitor_ref.mp4` and `raw_stereo.mp4`. Then
   drop a fixed project on its Learn card: it should find the run.
2. **mindculpt**: the laptop's edit vs today's, the moved shots side by side: `work\t27\p1\sheet\s1.png` and `s2.png`
   (left = the laptop's, right = today's). If you agree, the rule stays; if a move is wrong, say so in a wish.
3. **The New video form**: try one real video with Automatic OFF (the row waits for Convert, then for Send to Match
   cuts) and one with Automatic ON; then a YouTube link alone (Just cuts) and "Add a competitor" on it.
4. **A torrent** of your own: the file list, BIGGEST VIDEO, Start, the progress card; on the laptop it saves to
   `\\192.168.101.11\Shared\Downloads\Torrents` (not tried from the laptop).
5. **Turn off the PC when everything is done**: tick it once while a short video runs -- the 60-second warning with
   Cancel appears; Cancel keeps the PC on.
6. **Board -> Clean up**: read the list before Yes (old 5.1 runs with the RAW twice now show up there).
7. **Learn tab**: the three columns -- your waiting fixes, the problems (both marked fixed today), and today's 8
   suggestions with I want this / No.

## On the laptop

Close MovieRecaps if it is open. Then, in PowerShell:

```
cd C:\Users\gvida\Desktop\bh\MovieRecaps
git status
git pull
.venv\Scripts\python tools\app\setup_app.py
```

* `git status` first: if it lists files under `tools\` as changed by you, keep a copy, then `git stash` before
  `git pull` and `git stash pop` after. Your `input\`, `output\` and `finished\` folders are never touched.
* `git pull` brings about **150 MB** (the two new test videos, `tests\real\video14-fixed` and `video15-fixed`).
* `setup_app.py` now also **updates PySceneDetect** (the laptop's was too old: every run logged "No module named
  scenedetect.output") and **downloads aria2c** (the torrent engine, ~5 MB from its official GitHub page, checksum
  checked) into `tools\app\bin`; then the shortcut and the icon as before.
* Open MovieRecaps -> **settings**: "Save torrents to" should say `\\192.168.101.11\Shared\Downloads\Torrents`.
* The problems list, the waiting cards and the suggestions are in the shared folder, so the laptop sees the same ones.

## The PC

`setup_app.py` ran, everything is committed and pushed, and the PC shuts down two minutes after the report. Today's
scratch is in `work\t27` (about 20 GB: the check-all runs, the re-runs, my problem re-runs' caches). The largest part,
`work\t27\p1\work` (7.4 GB, the Rush Hour re-run's caches) and `work\t27\rerun\work` (12 GB), can go when you like (I delete
no caches myself), in PowerShell:

```
cd C:\Users\gvida\Desktop\bh\MovieRecaps
Remove-Item -Recurse -Force work\t27\p1\work, work\t27\rerun\work
Get-ChildItem -Directory work\snap12* | Remove-Item -Recurse -Force
```

C: has about 22 GB free now (it was at 0.5 GB this morning; another 20 GB come back with the lines above). Keep an
eye on it: a 5.1 film's run used to hold the RAW twice (2 x 8 GB for Rush Hour).

## Commits

Everything is on `claude/new-session-ol4era`, pushed after each part (64 commits today; the helpers' branches
were merged in):

| commit | what |
|---|---|
| `301d20e` | A crash while closing no longer turns a finished edit into a FAIL with a library warning as the reason (darkhumor, exit 3) |
| `827192f` | Learn: problems list (problems_queue.md), waiting cards (learn_waiting.json), suggestions.md, the three Learn columns and the queue's hook; add_problem.py for day 12's two problems |
| `c5f5190` | Learned from video13 / video14 / video15: fu*k as you write it, a doubled action caption joined, the person speaking slid into a window that shows nobody; two new test cases |
| `75f0a87` | setup_app: install PySceneDetect 0.7.1 when it is missing or too old (the laptop's runs logged 'No module named scenedetect.output') |
| `6811b52` | Match cuts queue: RAW vs competitor is always your choice (pair line, Swap before/during a run, amber 'Check which is the RAW' for pairs within 30 %, your pick = Run anyway + --no-swap) and newest on top (running, waiting newe... |
| `0bf930d` | New video (day 12, 6a/6b/6e core): what was pasted is recognised (YOUTUBE / TORRENT / FILE / LINK), the 'Will make' line, batch lines, Automatic and the channel remembered, errors in plain words, newest on top; flow: Just cuts... |
| `faf4c73` | Real titles (day 12, section 7: the post's title cleaned, the headline ON the video read by Qwen3-VL in its own process, the folder named from them, saved in project.json and titles_found.txt) and turning the PC off (6d: never... |
| `96f51a3` | Download tab = the New video form (day 12, 6a/6b/6d/6e/7): RAW + optional competitor with their labels, channel buttons with dots, the two-line headline box, Will make, many at once; one row per video with a gliding bar per st... |
| `c4b0190` | Torrents (day 12, 6c): aria2c engine (libtorrent has no wheel for Python 3.14), core/torrent.py session API (magnet/.torrent file list, BIGGEST VIDEO, tracker-scrape seeders, free-space check, pause/resume/remove, restart resu... |
| `f2bc7a9` | Learn columns: tests for the problems list, the waiting cards and the suggestions (core + real widgets); the channel chip on the left, the columns never hold the page wider |
| `65e95a7` | One RAW file per run (day 12, section 3): a 5.1 RAW's run folder keeps only raw_stereo (picture copied, stereo AAC) -- the analysis copy is removed when the run is done; Learn finds the RAW by raw_stereo |
| `59b0988` | Board Clean up: older runs holding a 5.1 RAW twice -- raw_stereo checked with ffprobe, nothing may play the old copy (projects only read), space shown, old copy removed only on Yes; learnkit finds raw_stereo by name |
| `5a985c8` | day12 notes (torrent): the test count is 26 |
| `412525b` | Learn: the prompt button and the folder line name problems_queue.md and suggestions.md; day 12 notes (learnbox) |
| `f840cca` | Just cuts tool mode (day 12, 6b): python -m match_cuts.just_cuts -- the whole RAW cut at every scene change (no cut leaving a shot under 0.5 s), each shot framed on its main face, the usual Premiere export (the RAW's own rate,... |
| `a31c586` | Day 12 notes: oneraw (one RAW file per run) |
| `13c5150` | Exit code 3 is a finished run, not a stop: darkhumor's real cause (correcting the last commit's guess) |
| `5d21cfe` | Queue: a far-apart pair you swapped keeps its warning and checks; day 12 notes for sections 4 and 5 (docs/day12_notes/queue.md) |
| `b10d447` | Headline on the video: the one real Qwen3-VL run (finished\video3: 80 s with the model's load) wrote the headline on one line and went on with a stray word -- that word is dropped, the line is broken where the video breaks it ... |
| `68c21f4` | History says Just cuts (with its number of cuts) and the default template; the newvideo helper's notes |
| `8433ab2` | New video rows: 'needs a headline' amber with an empty bar (not a finished step), WRONG PAIR / PICTURE NOT FOUND keep their own red word, an edit stopped in the queue before it ran says STOPPED; Try again clears the old result... |
| `e750f10` | New video: a magnet / .torrent RAW opens the torrent part's file picker and then its progress card inside the row; its progress moves the Download bar, its finished video becomes the RAW's file and the row goes on; without tha... |
| `71eede8` | History: a New video that fails before its folder is made (a broken or private link) is still in History with its date (a small log on this computer); the real download / convert stages tested with the slow parts faked (folder... |
| `e7ff34b` | Real titles on queue rows and board cards too (compact there), Just cuts on both; Use as my headline: set before the edit, wait while it runs, ask then Change headline after; queue test no longer counts a leftover page's threa... |
| `cd14d30` | Download / New video rows use the queue's GlideList and its order rules (finished: the one finished last on top); a new row glides the page to the top |
| `66423ce` | Torrents joined to the New video rows for real: tested end to end with a real magnet on 127.0.0.1 (picker in the row, Start, progress card, finished -> RAW -> waits for Convert / Automatic goes on); stalled only pauses, Remove... |
| `e9db614` | Learn hook covers every finished edit: verified that New video edits finish in the same queue and get their waiting card (test); a Just cuts run that fails or has a note goes into problems_queue.md too (no waiting card: nothin... |
| `c8aaa3f` | Fixes from clicking through the real window: board cards as tall as their wrapped lines (dates and titles were cut off), names break after _ and -, compact titles block stacked, Just cuts pill on its own line; a torrent row sh... |
| `71192d8` | New video form: the channel buttons at once, the channels folder read in the background (was ~150 ms on the window thread); integrate helper's notes |
| `2be37eb` | READMEs: day 12 (New video form, torrents, real titles, queue pair line and Swap, Learn columns, Clean up, one RAW file, Just cuts, exit code 3, fu*k, speaker framing) |
| `3b37efb` | Speaker-cut-off rule: judged per RAW shot inside a clip, and the reasons a clip is not moved are logged |
| `9db4bc7` | Real-window click-through fixes: a dropped/chosen .torrent is a torrent (was copied as a video file), the dropped file's note clears when a link is typed; Send to Match cuts writes project.json/title.txt off the window thread ... |
| `edf56f9` | Window thread: Add a competitor, the row's YouTube title and the queue's template dialog write project.json / title.txt in the background; the Download count line follows every state change (said '1 running' after all were don... |
| `9d0489e` | Tests for today's untested UI pieces (titles block Use as YouTube title, scroll_to_top, Learn column count/fold, health bars) and just_cuts.build_cutlist; a Learn column shows its empty line once its last card has folded away ... |
| `bd81b64` | Notes of the extra helper (day 12: 12.3 real window, 12.5 window thread, 12.6 tests) |
| `2ac5100` | e2e fixes: the headline on the video loses a tweet-style account name/@handle and the model's stutter (the OCR's words win when both read the same text); a Download row whose edit waits in the queue for your pick of the RAW sa... |
| `20e83ef` | Queue row: long downloaded file names shortened in the middle on the pick buttons and the pair line (start and [id].mp4 kept; full path in the tooltip) |
| `170df6f` | Tests can never turn the PC off: an autouse conftest fixture makes shutdown.SHUTDOWN raise and refuses any 'shutdown ...' process started through subprocess |
| `6e49cdc` | Turn off the PC: never while new work runs, a refused shutdown said, an unreadable Premiere blocks it |
| `ff0a8b9` | Turn off the PC waits for background jobs: background.call(work=True) counts a job until it returns; publishing a finished project (and the retry), Clean up, Change headline / Set headline are flagged, and a torrent download u... |
| `f8f7ced` | Torrents: Remove deletes only this job's chosen files (never another known job's, never unselected ones); aria2c no longer removes unselected files; the finished video's path comes from aria2.getFiles (aria2c escapes ':' on Wi... |
| `056d9c8` | Exit code 3 counts as a finished run only when the run printed its 'match_cuts result:' line or wrote extras/verify.json (batch.finished); otherwise it is a stop (abort / Fatal Python error) with its stop message in plain word... |
| `3be547c` | Learn waiting cards: the same project is the same path (relative to the hub) or the same folder name made on the same computer -- two computers' projects with the same fallback name keep their own cards |
| `2b052ca` | A project folder is never renamed from its titles once it was published to the shared folder, has an edit (a run folder or a started edit step) or while someone holds its work lock -- the row says why it keeps its name |
| `af9daa6` | Clean up: remove() checks the project's work lock again right before deleting an old RAW copy (work that started after the yes keeps the files) |
| `89e87a0` | Swap during a run: the restarted run waits until the old run's thread has ended (it releases the project's edit lock last) instead of 2 s, so it never fails as locked by this computer |
| `d51bd80` | Add a competitor (e2e run): the Just cuts edit step is kept apart (just_cuts_before) so the full edit is not called Just cuts on the queue/board/History; the title from the post comes from project.json when the link was not re... |
| `74a95d8` | Notes of the fixes helper (day 12: the review's 12 findings) |
| `fcdddd1` | Notes of the e2e helper (day 12: the real New video flow end to end) |
| `3e2c2ca` | New video: the form's RAW field is your choice of RAW -- a 30 s RAW for a 26 s Short no longer waits amber in the queue (found by the real end-to-end run) |
| `f733016` | README: a New video's RAW field counts as your choice of RAW |
| `93f3589` | Torrent: a start that fails sets the job's state to failed -- a failed start no longer counts as work for ever (Turn off the PC fires) |
| `c2373da` | New video form's RAW field: --no-swap and no amber wait, but never --force-inputs -- a wrong link still stops as WRONG PAIR; new --allow-short-raw skips only "almost as long as the RAW" (cli/inputs_check/batch, no stage module... |
| `b7543af` | Match cuts queue: a video swapped during its run, waiting for the old run's thread, keeps the queue running (no "the queue is done" with it left READY) and counts as busy |
| `7d84735` | The 5-minute retry of waiting projects counts as work only while it publishes (BG.counted_work), not for its reachability probe -- it no longer bounces the 60 s shutdown warning |
| `5e7ebd3` | batch.stop_message: the real stop message (WRONG PAIR, The inputs look wrong ...) wins over a Fatal Python error line; the fatal line (never its thread dump) only when nothing better is there |
| `c1a728f` | Headline on the video: only the vision model's answer loses its stutter (OCR text and real headlines keep "had had"); the OCR alone keeps its line break for any number of lines, as before |
| `5d21b7d` | Notes of the fixes2 helper (day 12: the second review's 6 findings) |
| `e81ac76` | Third review: a crash names itself, not an older unrelated line; the batch's check before the start honours --allow-short-raw; the command line's project step reads a chosen pair's lengths like the queue |
| `11e699e` | Copy tonight's prompt: a day run's reports\dayN.md counts too -- the next prompt is Night 13 and reads the newest report (it would have said night11.md after today) |
| `5581231` | Look audit: the Download row's 1_edit.xml look and Open folder run off the window's thread; motion tests for day 12's new widgets |
| `19dc5e7` | Look audit: the form's YOUTUBE / TORRENT / FILE label fades its colours (0.3 s, like the reference's .det); mono labels get the reference's letter spacing |
| `f7403d3` | Notes of the look helper (day 12: the night-11 look and motion rules on today's widgets) |
| `ea64136` | READMEs: --allow-short-raw; Turn off the PC also waits for copies to the shared folder, Clean up, Change headline and torrents |
