# Night 11 (18 October): the new look, the board from A to Z, the shared folder, a simpler Learn tab, the audio chain, names-first pairs and a quick wrong-pair check

## In short

Everything on the list is done, in your order, each part committed and pushed on its own -- with two honest limits:
the `hard_cases` folder does not exist (I used last night's laptop videos and the Robot Chicken pair instead), and
two of the five audio effects cannot travel in an XML (they come from a Premiere preset, one click).

1. **RAW / competitor**: names win (`raw*`, `comp*`), then length; when it cannot tell, the app asks "Which one is
   the RAW?". A wrong pair now stops in **seconds** (4-16 s for a 90-minute RAW, was ~20 min) with a red
   **WRONG PAIR** -- 65 real pairs checked, no right pair stopped. Dropped files always end up in a project.
2. **Save projects to** `D:\Shared\Projects` (PC) / `\\192.168.101.11\Shared\Projects` (laptop), changeable; amber
   note and a later move when it cannot be reached.
3. **Learn tab**: drop only the fixed `.prproj` -- the run is found by itself (all three of tonight's fixes were),
   a check card, auto names (video13 ...), a new fix noticed by itself, standing rules, wishes, tonight's prompt.
4. **Learned from video11 / video12 / chocolate**: two caption rules (no "!" in heard captions; follow-mode edge
   words), and the discovery that **Premiere's SRT import puts every caption one frame early** on 23.976 videos --
   measured fairly now. Before → after numbers in section 4 (the three new videos barely move; others gain).
5. **Audio chain** on every A1 clip: normalisation + Volume 0 dB + Panner 0 in the XML, Hard Limiter + Amplify
   +4.5 dB from a preset (= your LoudVOGOOOD) -- verified in Premiere.
6. **The board** (A to Z, drag and drop, dates of every step, titles with Copy and `title.txt`, pins, locks for
   two computers, Clean up, weekly charts).
7. **Uploaded check** every 30 minutes on the three Shorts tabs -- it found chocolate's Short (under a new title).
8. **Hard cases**: no `hard_cases` folder exists; last night's four laptop videos all **PASS** with tonight's
   tool (Meet the Blacks' 94-minute RAW too), and the Robot Chicken Short against another episode stops as
   **WRONG PAIR in 6 s**.
9. **The new look and motion** after `docs\ui_preview.html`: logo, colours, Bebas Neue, fades only, the glides, the
   intro, slate, timecode, motivation strip, quote line, taskbar progress, right-click menus, keys, History.
10. **Tests**: the app 743 (was 285), the tool 1421 with the graphics card, 0 failures; check-all the same as
    night 10 on all earlier videos.

Also fixed on the way: "Open in Premiere" now always works (its own project when Premiere is closed), a 125 % edit
could repeat a frame at a RAW shot change, and the first page with an emoji stalled the window for half a second.

## 1. RAW / competitor: names first, a wrong pair caught in seconds

**Names win over length** -- one rule for the app, the batch and the command line (`pairing.py`):

1. a file named `raw*` (any case: `raw.mp4`, `RAW episode.mkv`, `raw_ae.mp4`) is the RAW, every other video a
   competitor (`raw_stereo.mp4` next to `raw.mp4` is left out);
2. else `comp*` / `competitor*` is the competitor and the other video the RAW;
3. else the longer one is the RAW -- but only when the shorter is under 80 % of it;
4. two videos within 20 % of each other: a portrait one next to a landscape one is the competitor (said in a note);
5. else **the app asks** -- a small card **"Which one is the RAW?"** with the two file names (and their lengths) to
   click; the batch skips that folder with a clear note, the command line stops with it.

**The quick wrong-pair check** (`pair_check.py`, a new stage right after the start, before the long work): first the
sound -- short pieces of the competitor's sound are looked for in the whole RAW in one go (also as if sped up to
125 %) -- and when the sound cannot decide (music or a voice over it, no sound), 16 frames of the competitor at low
resolution are looked for among the RAW's keyframes (mirrored too). Only when both clearly fail does it stop: a red
**WRONG PAIR** pill, "This competitor doesn't seem to come from this RAW", the numbers in the row and the log.

* **Checked on 65 real pairs**: all 42 right pairs went on, all 23 wrong pairs were caught -- including the hard
  ones: another episode of the same show (Family Guy S04 vs S06, the three sitcom episodes in `tests\real\video1-3`,
  whose jingle is in every episode), the two Zendaya interviews, and the Robot Chicken clip in `D:\Shared` against
  "S03E19 Chirlaxx" (it is another episode's sketch: its sound is nowhere in that episode).
* **Time** (quiet PC): a 90-minute RAW **about 4.5 s when the pair is right, about 16 s when it is wrong** (before:
  the whole visual search, often 20 minutes, then "0 %"); a 100-minute RAW with many keyframes 21.5 s; the same pair
  again: instant (remembered).
* It leans to "go on": an unclear answer never stops a video. If it ever stops a pair you know is right,
  **right-click the row → Run anyway (skip the input checks)** (on the command line: `--force-inputs`).
* No stage cache changed (checked: every stage's code fingerprint is the same as before).

**"The RAW is missing from the media"**: every way of adding videos in the app was re-run with real videos (odd
names with `[ ] ( ) # % &` and emoji, two different folders, another drive, a network path, a 5.1 RAW, a VFR RAW
and the queue's converted-RAW reuse, the Download tab with a dropped RAW, a 125 % channel run): every run had both
files in `extras\media` and every path in `1_edit.xml` pointed inside the run folder. **The case you saw is
`D:\Shared\video32`** -- the laptop's run of 16 October on night 9's code: its RAW (3.07 GB) was over the old 2 GB
rule, so it was only *referenced* on the share, and when the folder was moved the RAW was gone. Night 10 removed that
rule; all four of last night's laptop runs have their RAW inside.

**Both files always end up in a project now:** a RAW and a competitor dropped on Match cuts from anywhere become a
project folder (like a Download), with **copies of their own** -- not hard links, because I found a real hazard: a
hard link is the very same file as your original, and copying a new video over the old name (Explorer's "Replace",
`copy /Y`) writes into it and changes every run linked to it (your laptop's 1018(1) took its RAW from
`Downloads\raw.mp4`, exactly that situation). The edit then goes into the project's `edit` folder and the card
appears on the board. (The Download tab still links a dropped RAW on the same drive, as night 10 made it -- so
**don't copy a new video over an old one's name; delete or rename the old one first**.)

## 2. Save projects to the shared folder

**Settings → "Save projects to"**: the PC saves to `D:\Shared\Projects`, the laptop to
`\\192.168.101.11\Shared\Projects`. You can change it (Browse…, or "This computer's default"); the dialog says at once
whether the folder can be reached.

**Which computer is which:** both computers are called **"Gvidas"** -- the laptop's own project files say so too --
so the name alone cannot tell them apart. The app looks at the hardware instead: **the laptop is the one with a
battery** (Windows' power information, a millisecond). Settings → "This computer" (Auto / PC / Laptop) overrides it
if it ever guesses wrong. On this PC it says "PC (Gvidas, no battery)".

**What goes there:** only finished projects -- when an edit is made (PASS or OK with a note), the project's
`edit` folder, `template.png`, `project.json`, `logs` and `title.txt` are put into
`<Save projects to>\<project>`. The downloaded videos, the conversions and the stage caches stay on the local disk.

* Each file is **hard-linked when the shared folder is on the same drive, else copied** (to a `.part` file, then
  renamed, the size checked). Files already there and unchanged are skipped, so publishing again takes a fraction of
  a second.
* The edit's XML files are **pointed at the copy's media** (so Premiere finds the RAW in the shared folder, not on the
  computer that made it). "Open in Premiere" points them again at the media as the computer you are on sees them
  (the PC opens `D:\Shared\...`, the laptop `\\192.168.101.11\Shared\...`).
* `project.json` is written last (a half-copied folder never looks like a project on the board) and merged with the
  shared copy (a card the other computer moved keeps its column).
* **UNC paths work** (only Python file operations; nothing ever changes directory in cmd). Tested for real through a
  network path on this PC (`\\127.0.0.1\D$\Shared`, a test folder, removed afterwards): 7 files, 2.9 MB copied in
  0.8 s, the XML's paths rewritten to the network path, publishing again in 0.2 s.
* **When the shared folder cannot be reached**, the project stays on this computer, the top strip turns amber
  ("Save to ... -- not reachable, saving here"; the reason is in its tooltip, e.g. "no access for this Windows
  user"), and the project moves there by itself when the folder is back (checked at the start and every 5 minutes).
* Projects that finished before tonight (edit made, never published) are published the same way at the start.

**Note:** this PC's own Windows login cannot open `\\192.168.101.11\Shared` ("Access is denied" -- night 10 found the
share is open to the account "render" only). That does not matter on the PC (it uses `D:\Shared\Projects`), but it
means the laptop must be able to open it with its own login. If the laptop's top strip turns amber, open
`\\192.168.101.11\Shared` in the laptop's Explorer once and save the password.

## 3. A simpler Learn tab

Match cuts → **LEARN**:

* **Drop only the fixed .prproj.** The app finds the run it was made from by itself -- the RAW the project plays (also
  a path of the other computer: then by the run folder's name and the RAW's size, rate and length), a `project.json`
  next to it, or the name. Tried on your three real fixes, all found by themselves:
  * video11 → `output\laptop_video11` (also the same run as `output\Super Troopers (2001) [1080p]`),
  * video12 → `output\laptop_video12` (this PC's `output\video1-2` has the same name but is another video -- rejected,
    with the reason),
  * chocolate → the laptop's run in `D:\Shared\2026-10-18_where-the-nestle-crunch-meme-originated\edit`.
* **A project folder** dropped instead: its newest finished `edit` and its fixed project are used.
* **Every drop starts a new form**; nothing of the last one stays.
* When the run cannot be found it says so plainly -- **"I can't find the run for this project — pick it here"** -- with
  a "Pick the run here…" button.
* **The name** is the next free one: today **video13** (video14, … after it).
* **The check card** before anything is copied: the RAW, the competitor, the tool's version (the run folder, when,
  on which computer and graphics card, Fast or Thorough, the tool's code), your fixed version (the project, when you
  saved it, which sequence) and **what differs**: captions / cuts / position, leaving out what you do by hand (all
  audio, the headline PNG and memes, trims at cut points, the ending). On chocolate: "12 of 22 changed (4 words, 4
  groupings, 4 timings)", "0 moved, 0 added, 2 removed", "0 of 7 clips moved or zoomed".
* **Learn this one** copies the pair where the nightly LEARN step reads it (`finished\<name>\<name>_fixed.prproj`,
  `output\laptop_<name>`), adds it to **learn_queue.md**, and moves the project's card to **Fixed** on the board.
* **A new `fixed.prproj` or `*_fixed.prproj` saved into a project folder** is noticed within a minute: "New fix in …
  -- learn from this?" (and a small red count on the LEARN tab).
* **Standing rules** (always apply) and **Wishes** (→ `requests.md`) boxes; **Copy tonight's prompt** builds a
  ready-to-paste Claude Code prompt from learn_queue.md, the standing rules and the open wishes.
* These three files live in the shared projects folder (`D:\Shared\Projects`), so the PC and the laptop see the same
  ones. `requests.md` already holds the two "Later" items of your list (Stats tab; Topaz hand-off).

## 4. Learning from video11, video12 and chocolate

All three fixes were found: `finished\video11\video11_fixed.prproj`, `finished\video12\video12_fixed.prproj` and
your `Desktop\chocolate.prproj` (its third sequence), with the runs they were made from (`output\laptop_video11`,
`output\laptop_video12`, and chocolate's app run in `D:\Shared\...nestle-crunch...\edit`). Your originals were never
written (relinked copies were made next to them in `finished\`, as on earlier nights). Only **captions (words,
timing, look), cuts and video position / scale** were compared; the headline PNG, memes, music, all audio, pitch and
your trims at cut points and the ending were left out. They are now three new test cases
(`tests\real\video11-fixed`, `video12-fixed`, `chocolate-fixed`).

**What you changed, and what of it is a rule** (each checked against every earlier fix of yours):

| what you changed | seen before? | done |
|---|---|---|
| took the **"!"** out of captions the tool made from the heard words ("put those away!") | 10 of the 11 the tool ever wrote, over 6 fixes; a called name ("Ron!") you kept | **rule: no "!" in heard-word captions** (a lone name or interjection keeps it; the competitor's own text keeps it) |
| chocolate: a word at a caption's edge doubled ("Chocolate is scrumptious" + "scrunchous") | laptop009 the same ("your mind? Fag" + "Fa*!") | **rule: in follow mode a heard word written otherwise on the screen counts as that word** -- the doubles go |
| captions one frame earlier than the tool's SRT | almost every caption of every 23.976 video (video7 21 of 23, video12 25 of 33) -- also where you changed nothing | **not you: Premiere's SRT import does it** (below) |
| clips moved sideways onto the person speaking (video11 7 of 8, video12 14 of 20) | other fixes keep the competitor's framing; tried as a rule it moved clips onto the wrong person | not a rule (as on night 6) |
| tiny jumps inside a word joined (video12 "I like") | 4 of 6 times on 4 videos | described, not made: it is a trim at a cut point (you do those) |
| video11's quiet "hey" removed, room-tone pauses cut, the jingle's spelling | one-offs | -- |

**The one-frame finding:** Premiere's SRT import floors every caption time to frames of the *nominal* rate (24 for
23.976), so on a 23.976 video **every caption lands one frame early** (on 30 / 60 fps videos only those whose time
ends in .980 / .990). Your fixed projects show exactly this. Two things follow:

* **check-all now measures it fairly** (done): a key made from a 23.976 delivery is compared with the tool's captions
  placed where Premiere put them, as yours were (case.json `"srt_rate"`, video7-12). This changes the numbers
  without changing any caption -- e.g. tonight's video7 run: 1 → 14 of 36 exactly.
* **The tool's SRT is not changed tonight** (your call later). A fix was written to put every caption exactly on
  its intended frame after Premiere's import -- and then checked in a real Premiere import (video11, both SRTs
  dropped on the same edit): today's captions landed one frame early on every caption, as predicted, but the fix
  put captions 1-12 one frame **late** and 13-23 on frame. Premiere's real rule, fitted on all 46 captions:
  frame = floor(time x 23.976 - 0.31) -- so a time of (frame + 0.8) / 23.976 s would land every caption exactly. It
  was measured on one video at one rate only (30 / 60 fps sequences behave differently), the difference is one
  frame (42 ms, captions appearing a hair early), and in every fix you kept them where Premiere put them -- so I
  left the delivery as it is and only made the measuring fair. New keys learned from a 23.976 sequence get the
  same treatment by themselves.

**Before → after** (captions "exactly": the same text on the same frames; the old count / the count with captions
where Premiere puts them):

| | captions exactly | with Premiere's placement | cuts | framing | hard checks |
|---|---|---|---|---|---|
| video11-fixed | 4 → 4 of 23 | 13 → 14 | 2 of 3 | 1 of 8 | PASS |
| video12-fixed | 11 → 11 of 51 | 8 → 8 | 4 of 13 | 4 of 16 | FAIL (a 2-frame flash in the very last frames: the competitor's own last frames show the next shot -- video6's known case) |
| chocolate-fixed | 7 → 7 of 22 | 7 → 7 | 1 of 3 | 5 of 5 | PASS |
| all 14 keyed cases | 154 → 161 of 469 | 175 → 183 | 23 of 94 (same) | 72 of 139 (same) | -- |

Honestly: the three new videos barely move. What still separates the tool from your fixes there is your framing
(chosen per video, not a pattern), the jingle word chocolate's speech model hears differently on every run, and
one-off changes. The rules help elsewhere: laptop013 9 → 12, laptop009 18 → 21, video7 1 → 2; no case got worse.

**The whole check-all with tonight's tool** (all 26 test videos, two streams, 68 minutes; `work\t26\checkall`,
compared with night 10's run by `work\t26\compare11.py`):

* **All 23 earlier videos: the same result as night 10** -- the same exit codes, the same hard checks, and the
  **same picture track (V1) clip for clip** in every delivered `1_edit.xml` (the 125 % videos laptop004 and mike005
  included). What changed is only what was meant to: every A1 clip now carries the audio chain, the "!" are gone
  from heard-word captions (laptop013 3 → 0, video7 3 → 0), follow-mode boundary words moved (laptop009, video10),
  and a few captions changed by the speech model's re-run (the caption stage ran again: its code changed).
* **Hard-check FAILs: 3, all known** -- video6 (night 10's one-frame flash at 00:00:21), video020 (the known `--fast`
  flash) and the new video12 (its competitor's own last two frames show the next RAW shot -- video6's kind).
* **Captions exactly over all keys: 183 of 469** (night 10's runs scored the same way: 175). laptop009 18 → 21,
  laptop013 9 → 12, video9 0 → 4, video7 1 → 14 (most of that is the fair measuring), video8 1 → 0. The new cases:
  video11 14 of 23, video12 8 of 51, chocolate 7 of 22.

## 5. The audio chain on every audio clip

**What Premiere can take from an XML** (tested in your Premiere Pro 2024, build 24.0.0.58 -- the laptop has the
same build: real XMLs imported, the projects saved and read back, Premiere's own XML export compared):

| your chain | through the XML | how it arrives |
|---|---|---|
| 1. Audio Gain: Normalize All Peaks to 0 dB | **yes, as a Volume** | an XML cannot set a clip's Audio Gain at all; the same gain comes in as a Volume effect after the clip's own (the same sound: a gain is a gain) |
| 2. Volume 0 dB | **yes, exactly** | |
| 3. Hard Limiter (0 dB, 0 dB, 7 ms, 100 ms, 1.00) | **no** | "not translated": Premiere drops it (and shows a Translation Report) |
| 4. Amplify +4.5 dB | **no** | the same |
| 5. Panner 0 | **yes, exactly** | |

So **every A1 clip of 1_edit.xml now carries**: its own normalisation (the loudest sample the clip plays brought to
0 dB, measured like Premiere's own Normalize -- within 0.0002 dB of Premiere's dialog on a real clip; above +15 dB
Premiere needs two or more equal Volumes, so it is split), Volume 0 dB, Panner 0 -- in that order, after the clip's
speed, no keyframes. Nothing stands in for the two effects an XML cannot carry (a "Volume +4.5 dB" stand-in would
double the +4.5 dB once you apply your preset, and would sit before the limiter).

**The Hard Limiter and Amplify come from a preset, in one step:** `assets\premiere\MovieRecaps audio chain.prfpset`
(made and exported by Premiere itself) holds exactly the Hard Limiter (0 dB, 0 dB, 7 ms, 100 ms, 1.00) and Amplify
+4.5 dB. **Your own "LoudVOGOOOD" preset has exactly the same values** (checked byte for byte on chocolate's
clips), so you can keep using it. In Premiere: **select all A1 clips → apply the preset**. Then Effect Controls
shows the whole chain in your order: Volume 0 dB → Volume +12.0 dB (the clip's normalisation) → Hard Limiter →
Amplify +4.5 dB → Panner 0 (screenshot: `work\t26\audio\premiere_effect_controls_chain.png`, a 125 % edit).

* **Don't press Audio Gain → Normalize again**: the tool already normalised (as a Volume).
* A clip you trim later keeps the gain measured on its first range; the Hard Limiter then catches anything over 0 dB.
* **As you asked, the order stays: Amplify comes after the limiter, so the peaks can go up to +4.5 dB over 0 dB.**
* Each A1 clip's comment and the run's report say all this; the hard check XML AUDIO now requires exactly this
  chain, in this order, and re-measures each clip's gain from the RAW.
* Premiere's import with an audio pan written as `level` froze Premiere (twice) -- the tool writes `pan`.
* Your preset store in Premiere was backed up before the test and restored byte for byte afterwards; Premiere and
  After Effects are closed.

Fully automatic (no preset step) would need a small Premiere extension (a CEP panel) that applies the two effects
after the import; it needs an unsigned extension and a registry switch -- **your decision**, so not done (if you
want it, add it as a wish in the Learn tab and the next night's prompt carries it).

**Found on the way, fixed:** a 125 % edit could show one RAW frame twice at a RAW shot change -- the tool counted a
retimed clip from a whole source frame, Premiere starts it a quarter frame later (video10 at 125 % failed its XML
check). Fixed and tested; 100 % edits are unchanged.

## 6. The project board (A to Z)

The **Board** tab: **Link → Downloaded → Converted → Template → Edit made → Fixed → Rendered → Uploaded**.

* **Cards move by themselves** as the steps finish (download, convert, template -- now made right after the
  conversion when the channel and title are set -- the edit, a fixed project found, the upload found). **Drag a card**
  to any column: a slot opens where it will land and the card fades in there. A card dragged by hand stays where you
  put it until a later step finishes.
* **Each card**: the channel's dot, the name, the title with **Copy**, the date and time of **every step** ("Downloaded
  18 Oct 09:21"), the edit's result, a ★ **pin** (keeps it at the top of its column), and right-click: Open folder,
  Open in Premiere, Copy title, Set the YouTube title, Pin.
* **The title is everywhere the project appears** (queue rows, Download rows, cards, history) and in `title.txt` in
  the project. **New: the YouTube title.** Your Shorts go up under other titles than the template's headline (the
  chocolate project's headline is "The fact that this meme was an actual ad is hilarious 🍫😭", its Short is "The
  Commercial Behind The Nestle Crunch Meme 🍫😭"), so each project can also keep the title you upload with: type it in
  Download, with Edit on a queue row, or right-click a card. Copy and `title.txt` give the YouTube title when it is
  set, else the headline.
* **The PC and the laptop use the board at the same time** (the shared folder): a lock file (`.movierecaps.lock`, with
  a heartbeat every 5 minutes) keeps two computers from working on one project at once; a lock without a heartbeat
  for 2 hours is taken over. (Windows' file rename turned out not to be safe for this when two programs race -- all
  three "won" in 2953 of 3000 tries -- so the takeover uses a file only one program can create.)
* **Clean up** lists finished leftovers (temp and part files, old lock claims, and -- for Rendered / Uploaded cards --
  the downloaded originals that the edit's media already holds as a separate copy), shows the size and asks first. It
  never deletes an `edit` folder, `template.png`, `project.json` or `title.txt` (the delete itself refuses them).
* **Above the board**: a small chart per channel, uploads per day this week (Mon–Sun) in the channel's colour, "N this
  week".

Tested for real: a link pasted with Ctrl+V into Download, both files downloaded, converted and checked, the
channel's template made right after, the card in **Template** with the date of every step and both titles, and
`title.txt` in the project; and the chocolate project moved to Uploaded by the check (section 7). The new board
tests found and fixed one bug: a card that landed quickly after a drop deleted the column's drop slot, and the column
then stopped painting.

**chocolate is not on the board yet:** it is the only app project from before tonight (the other folders in
`D:\Shared` are plain tool runs, without a `project.json`), and it is in `D:\Shared` itself, not in
`D:\Shared\Projects` -- the board shows the "Save projects to" folder and this computer's own projects. Move the
folder `D:\Shared\2026-10-18_where-the-nestle-crunch-meme-originated` into `D:\Shared\Projects` and it appears (and
moves to Uploaded by itself: its Short was found). I did not move your folders.

## 7. The automatic "Uploaded" check

At the start and every 30 minutes (Settings), the **Shorts tabs** of @LouLaughs, @MikeMemesYou and @Flick707 are read
with yt-dlp (flat playlist: about 1 s a tab on a quiet PC, 5-6 s under full load).

* **An exact match moves the card to Uploaded** with the Short's own upload date and time. Titles are compared
  without emoji, case, punctuation or #hashtags (Flick707 adds `#usa`). The YouTube title is compared first, then the
  headline.
* **A near match asks first**: a small card "Is this the same video?" with Yes / No (No is remembered). Near = most of
  the rarer words the same: on your 55 real Shorts, every pair of different titles scored at most 0.50, typos at
  least 0.86 (the line is 0.72) -- your titles share a pattern ("The Funniest … of All Time"), so one changed word
  usually means another video.
* **A "probable" match also asks** (with the reason): the same channel, the same distinctive emoji or a similar
  competitor title, about the competitor's length, uploaded after the project was made. This is the one that finds a
  Short uploaded under a new title -- it found chocolate's: "the same 🍫, 24 s like the competitor, uploaded 51 min
  after the edit".
* **No network**: skipped quietly, tried again 5 minutes later.

Tested live: a copy of the chocolate project with its YouTube title typed moved to **Uploaded, 18 Oct 02:34** (the
Short's real time), and the motivation strip counted it.

## 8. The hard_cases folder

**There is no `hard_cases` folder** on this PC (searched C: and D: by name, also deep). As the hardest real material
at hand I used last night's laptop videos in `D:\Shared` (the files you moved there) plus the Robot Chicken Short
with the two RAWs next to it, and ran each with tonight's tool exactly as the laptop app runs it (`--premiere --fast
--speed 100`, the channel's template and watermark from each run's own media; `work\t26\hard\run_hard.py`, results
in `work\t26\hard\runs`):

| video | what makes it hard | tonight | time |
|---|---|---|---|
| Meet the Blacks (the AQO Short) | a 94-minute 1080p film as the RAW | **PASS** -- the pair check said "goes on" in 3.4 s | 30 min (the laptop: 23 min) |
| wasted (Dude, Where's My Car?) | an 83-minute RAW with 5.1 sound | **PASS** -- goes on in 4.1 s; the 5.1 → stereo copy as usual | 16 min |
| 1018 | a 26-second Short, LouLaughs template + watermark | **PASS** | 7 min |
| chocolate | a 31-second 640x480 commercial, a sung jingle | **PASS** | 6 min |
| Robot Chicken Short + S03E19 "Chirlaxx" | the RAW is another episode of the same show | **WRONG PAIR in 6 seconds** (sound 0 of 7 pieces, picture 0 of 16 frames) -- before tonight it first ran the whole visual search | 6 s |
| Robot Chicken Short + the 34-second "Dolly Hates You Too" clip | the right RAW, but only 3 s longer than the Short | stopped at once by night 6's input check ("almost as long as the RAW -- a copy?"); **with Run anyway**: the edit is made, **OK with a note**: a 4-frame flash at its very start (to cut in Premiere), and the 480p clip matches the Short's picture only roughly -- the full episode would be the better RAW | 28 min |

What I fixed from these: the last one showed that the app could not overrule night 6's input stops -- **Run anyway**
now covers them too (right-click the row). Nothing else needed fixing: all four real videos pass with tonight's
tool, and the wrong pair is caught where it should be.

(The tool's own `--fast` verification marks c2 / c4 / c5 on these as it did last night -- the app's result word comes
from the hard checks, which pass.)

## 9. Look and motion

The whole window was rebuilt in Qt after `docs\ui_preview.html` (no web page inside):

* **Tabs: Match cuts · Board · Progress · History.** Download, Convert and Template are under Progress (the mock-up's
  Progress tab shows the downloads and conversions), the Queue and Learn under Match cuts -- small sub-tabs at the top
  of the page. A tab under the mouse fades in 0.45 s; the active tab has a red underline that grows from its middle;
  no sliding line. **A new page glides in from the side of the tab you clicked** (0.45 s, no blur).
* **Hovers only fade** -- in 0.3 s, out 0.48 s, on cubic-bezier(.4,0,.2,1); nothing moves, grows or jumps (a test
  measures sizes and positions before and after). Buttons: a red fill sweeps in from the left; primary buttons are a
  red→coral gradient with a lighter red sweeping in. Queue rows: a red left edge (its own fading layer), a dark red
  border, a warmer background, a faint red glow, the title box's border lighting up, and a very faint warm light
  crossing it. Board cards: the viewfinder corners fade in. The drop zone: clean -- only its dashed border and
  background fade.
* **All animations are the app's own** (Qt's QVariantAnimation), so they run with Windows' "Animation effects" off
  (a test switches Qt's system effects off and checks); Settings → **Animations on / off** makes everything instant.
* **Progress bars glide every frame and never jump**, also the last step to 100 %, with a soft shimmer while running;
  **status pills** tinted grey / blue (a pulsing dot) / green / amber / red (FAIL, PICTURE NOT FOUND, WRONG PAIR);
  the running row has a **slowly circling red border**; a finished video gets a green **"That's a wrap"** sweep and a
  toast; board columns have thin coloured top lines.
* **Brand**: the "match cut" logo (the split play triangle, white and red, inside red viewfinder corners) in the top
  bar, the intro and the app icon (`setup_app.py` refreshes Windows' icon cache); **Movie** in white, **Recaps** in
  red; red #e5484d, coral #ff7a59, the channels' dots (LouLaughs amber, MikeMemesYou red, Flick707 violet); "TITLE" in
  coral; section titles in **Bebas Neue** and times in **JetBrains Mono** (both bundled, free licences).
* **Cinematic extras**: the **intro** (under 1.3 s: the corners draw, the two halves slide together, the name fades
  in -- all centred; "replay intro" in the top strip and in Settings), the **slate line** on every queue row (SCENE 01
  · TAKE 1 · 18.10.2026), the **timecode clock**, a very soft **vignette**, and **dates on everything** (rows, cards,
  history, and now every line of the app's own logs).
* **The motivation strip** (Match cuts tab): Today -- the ring in 6 pieces filled in the channels' colours, "4 / 6",
  "2 more to hit today's goal", "2 Lou · 1 Mike · 1 Flick707"; the goal hit: a green border, a sweep and a toast. This
  month -- "87 / 150 Shorts", a square per day (stronger red = more Shorts, today outlined in coral, future days
  dashed, the mouse over one: "Oct 12 · 5 Shorts") and "▲ 21 ahead of September" (amber when behind). Goals in
  Settings; the counts come from the board's Uploaded dates.
* **The quote line**: from `quotes.txt` (in the shared projects folder; made from the mock-up's quotes, McQueen's
  included), one quote per part of the day (Morning / Afternoon / Evening), the same after a restart, never
  yesterday's; a soft fade when it changes; a click shows the next; a Goal line when the daily goal is hit.
* **Small extras**: the **taskbar button fills** with the queue's progress; **drag rows** to change the queue's order;
  **right-click a row**: Open folder, Open in Premiere, Copy title, Run again (Thorough), Remove; **keys** with small
  hints: Space = Start, Del = remove the row under the mouse, Ctrl+V = a YouTube link into Download; the **History**
  tab (everything with its date and time, the channel's colour, a search box).
* **The window never freezes**: everything that reads a disk, the network, a video or YouTube runs in the background
  (`ui/background.py`); the few things that ran in the window before (yt-dlp's version, a dropped video's length, a
  project's lengths) moved there too. The click-through found one more stall: **the first emoji the app draws makes
  Windows look up its emoji font, about 0.5 s on the window's thread** (the Template page's first show stalled
  0.6-1.8 s). A background thread cannot do that lookup for the window (measured: the window waits all the same), so
  the app now does it once before the window appears. After that: no stall over 100 ms once the window is up.

**How the look and motion were checked** (section 10's checks):

* **Tests on the real widgets** (`test_motion.py`): a hover is part-way after 0.12 s and full after ~0.4 s, out in
  ~0.5 s; buttons, cards and the drop zone keep their exact size and position while fading; Animations off makes
  everything instant; a progress bar glides to 100 % in small steps, never a jump; a page glides in and the real page
  is back afterwards; the tab underline grows on the new tab; the intro lasts at most 1.3 s and its picture is
  centred within 4 px; the fades still run with Windows' animation effects switched off.
* **The real window, clicked through** (`work\t26\click_through.py`: every tab and sub-tab, the start-up work of a
  real start running, a 10 ms timer watching the window's thread): on the quiet PC at the end, after the window
  appeared **no stall over 100 ms** (99 % of the 10 ms ticks within 27 ms); the only longer wait (0.36 s) is
  while the window is being made, before it shows. A hover's fade read 0.03 / 0.42 / 0.75 / 0.91 / 0.98 / 1.0
  at 50 ms steps.
* **The intro on a real start** (quiet PC): 108-111 frames in 1.3 s, never more than 19 ms between two frames.
* Pictures of every page, the intro frame by frame, a page glide and a hover fade: `work\t26\shots\` (night 11's
  final set in `work\t26\shots\final`).

## 10. Tests

| suite | night 10 | night 11 |
|---|---|---|
| the app (`tools\app\tests`) | 285 | **743 passed**, 0 failed |
| the tool with the graphics card and the slow end-to-end tests | 1367 passed, 73 skipped | **1421 passed, 73 skipped, 0 failed** (35 min) |
| the tool with the graphics card hidden | -- | 1396 passed, 97 skipped, 0 failed |
| check-all, 26 test videos | 23 videos | the same results as night 10 on all 23, 3 new (section 4) |

New tests tonight: the pair rule and the quick check (`test_night11_pairs.py`, `test_pairs.py`), the audio chain
(`test_night11_audio.py`), the learned caption rules and the fair measuring (`test_night11_learn.py`), the 125 %
repeat (`test_night7.py`), and for the app: motion, background work, the queue, the board, Learn, History, the
window's chrome (settings, motivation strip, quote line, keys), Open in Premiere, publishing, the shared files, the
uploads check, quotes, history, the taskbar.

## What I decided on my own

1. **The tab layout**: your four tabs (Match cuts · Board · Progress · History). Learn sits under Match cuts (a
   sub-tab next to the queue: both are about the edit) and Download / Convert / Template under Progress (the
   mock-up's Progress tab shows the downloads and conversions).
2. **Which computer is which**: both are called "Gvidas", so the name cannot tell -- the laptop is the one with a
   battery; Settings can override it.
3. **The YouTube title** (new): your Shorts go up under other titles than the template's headline, so a project
   keeps both; Copy and `title.txt` give the YouTube title when it is set. Without it the Uploaded check could only
   match by title and would never have found chocolate's Short.
4. **Dropped files are copied into the project, not hard-linked** (section 1: a hard link changes with your original
   when another video is copied over its name).
5. **The wrong-pair check leans to "go on"**: only a clear double failure (sound and picture) stops a video; and
   you can overrule it ("Run anyway").
6. **The audio chain**: nothing in the XML stands in for the two effects it cannot carry (a Volume +4.5 dB would
   double with your preset); the preset holds exactly your values.
7. **What was learned**: only two caption rules passed the test on every earlier fix; re-framing onto the speaker
   was tried again and rejected (it moves clips onto the wrong person), and "join tiny jumps inside a word" is a trim
   at a cut point, which you said you do by hand -- described, not made.
8. **The three new action captions** learn found (`*burst out laughing*`, `*hands him the pistol*`, `*got it*`)
   were **not** added to `tools\match_cuts\caption_actions.txt`: that list is given to the action model as examples,
   and the effect of new ones was not measured tonight. To add them: append the three lines to that file.
9. **Clean up** offers temp / partial / stale-lock files, and for Rendered / Uploaded cards the downloaded originals
   that the edit's media already holds as a separate copy -- never anything else; it always asks first.
10. **The board's lock takeover** uses a claim file only one program can create (Windows' rename let all three
    racing programs "win" 2953 of 3000 times in a test).
11. **No new effect on the drop zone** and none of the "not wanted" list; the animations are the app's own, so they
    run with Windows' animation effects off.
12. **The one-frame caption fix (R-D1) is not in** -- tested in Premiere, its first form put half the captions a frame
    late; the correct rule is known now (section 4) but measured at one rate only, and the difference is one frame.
    Only the measuring was made fair.
13. **"Open in Premiere" makes the run's own project** next to the edit when Premiere is closed or on its Home
    screen (instead of Premiere's new project "in the folder of the last project you opened"); with a project open it
    imports into that one (your usual template project), never twice.
14. **"Run anyway"** for a WRONG PAIR and for night 6's input stops (both are `--force-inputs`): the checks can be
    wrong, the call is yours.
15. **Your shared files are only written when something changes** (requests.md was being rewritten with the same text
    at every start).
16. **Nothing of mine went into `requests.md`**: it feeds the next night's prompt, so my suggestions (the CEP panel,
    R-D1, R-K1) are only in this report -- add them as wishes if you want them.

## Check by hand

1. **Open MovieRecaps** (desktop shortcut, new icon): the intro, then the four tabs. Move the mouse over buttons,
   queue rows and board cards -- everything should fade (in ~0.3 s, out ~0.5 s), nothing should move or jump.
   **settings** (top right): "This computer: PC", "Save projects to: D:\Shared\Projects -- reachable".
2. **Your audio preset**: in Premiere, Effects panel → Presets → right-click → *Import Presets…* →
   `assets\premiere\MovieRecaps audio chain.prfpset` (once; or keep using your LoudVOGOOOD -- the same values). After
   opening an edit: select all A1 clips → apply it. Listen to one clip: Effect Controls should read Volume 0 dB,
   Volume +N dB, Hard Limiter, Amplify 4.5 dB, Panner 0.
3. **Open in Premiere** from a finished row with Premiere closed: it starts with the run's own
   `<name>_edit.prproj` (next to the edit) and imports the edit -- press Ctrl+S to keep it.
4. **chocolate on the board**: move `D:\Shared\2026-10-18_where-the-nestle-crunch-meme-originated` into
   `D:\Shared\Projects` -- its card appears and moves to Uploaded by itself.
5. **The YouTube title**: type it for your next video (Download → "YouTube title", or Edit on a queue row) -- the
   Uploaded check then finds the Short by title instead of asking.
6. **Clean up** (Board): try it once and read the list before you say Yes.
7. **The three new action captions** (`*burst out laughing*`, `*hands him the pistol*`, `*got it*`): add them to
   `tools\match_cuts\caption_actions.txt` if you want them as examples (I did not -- see "decided on my own").
8. **quotes.txt** (`D:\Shared\Projects`): edit it freely -- `Morning | text | author`, `Goal | text`.

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
* `git pull` brings **about 180 MB** this time: three new test videos (`tests\real\video11-fixed`,
  `video12-fixed`, `chocolate-fixed`), two fonts, the new icon and the two Premiere files in `assets\premiere`.
* `setup_app.py` installs anything missing (nothing new is needed tonight: the wrong-pair check uses numpy, scipy
  and OpenCV, which the tool already has), refreshes the desktop shortcut **and Windows' icon cache**, so the new
  logo shows on the desktop and the taskbar.
* Open **MovieRecaps**. Open **settings** (top right): "This computer" should say **Laptop** (found by the
  battery) and "Save projects to" `\\192.168.101.11\Shared\Projects`.
* **If the top strip is amber** ("Save to ... not reachable"): open `\\192.168.101.11\Shared` in Explorer once,
  sign in with the account the share is set up for, and tick "Remember my credentials". The app tries again every
  5 minutes and moves the waiting projects over by itself.
* The board, the learn queue, the standing rules, the wishes and the quotes are now shared with the PC
  (`\\192.168.101.11\Shared\Projects` = the PC's `D:\Shared\Projects`).
* **Premiere on the laptop**: the audio preset is per computer -- import
  `assets\premiere\MovieRecaps audio chain.prfpset` once there too (Effects → Presets → right-click → Import
  Presets…), or keep using LoudVOGOOOD if the laptop has it.

## The PC

Nothing to do: `setup_app.py` ran (the shortcut and the new icon), everything is committed and pushed, and the PC
shuts down two minutes after the report (as you asked). Tonight's scratch is in `work\t26` (32 GB; C: has 115 GB
free). The largest part, `work\t26\learn\work` (18 GB), is a copy of the test caches the learning runs used; with
the code copies `work\snap11_learn*` it can go when you like (I delete no caches myself):

```
cd C:\Users\gvida\Desktop\bh\MovieRecaps
Remove-Item -Recurse -Force work\t26\learn\work
Get-ChildItem -Directory work\snap11_learn* | Remove-Item -Recurse -Force
```
(in PowerShell)

## Commits

Everything is on the branch `claude/new-session-ol4era`, pushed after each part (26 commits, then this report):

| commit | what |
|---|---|
| `f54cac4` | App look and motion: four tabs, fading hovers, gliding pages and bars, the match-cut logo |
| `df9d141` | Learn tab: drop only the fixed .prproj -- the run is found by itself, a check card, the learn queue, rules, wishes, tonight's prompt |
| `947d067` | Uploaded check, quotes, history, motivation counts, taskbar progress |
| `3e90bab` | Save projects to the shared folder, the board's model, locks for two computers |
| `f8f7b32` | App tests never touch real folders; the queue puts dropped files into a project; Learn waits for the shared folder |
| `66d3223` | Tests: the window publishes a finished project by itself, keeps it here with an amber note when the shared folder is away, and publishes old finished projects at the start |
| `a66fefe` | Download: the channel's template is made right after the conversion when the title is there -- the board's card reaches Template before the edit |
| `65a3e6a` | Learn: a learned fix's project card moves to Fixed (only forwards); test: the fades run with Windows' animation effects off |
| `f7f13ce` | RAW / competitor: names win over length, a question when it can't tell, WRONG PAIR in seconds, dropped files into a project |
| `d51b59d` | App: the emoji font is looked up once before the window shows -- the first page with an emoji (Template) no longer stalls ~0.5 s |
| `b191bf0` | Match cuts: a drop's question is on its card only (not repeated in the status line); dropped files are copies of their own -- docstring and README say so |
| `68e61d6` | Board: Clean up is a small button like its neighbours; Learn: the standing rules' example no longer says 'never touch my audio' |
| `eae925c` | Board: a card that lands quickly no longer deletes the column's drop slot (the column then stopped painting); UI tests for the board |
| `2dfd4c6` | Tests: the Learn tab on the real widgets -- the check card, Learn this one (queue + card to Fixed, never backwards), a new form per drop, the plain 'can't find the run' line, the offer of a new fix, rules, wishes and tonight's prompt |
| `3724d5b` | Tests: the History tab on the real widgets -- newest first, the count, search by channel and title |
| `d46d9cd` | Tests: Settings saved and applied at once (reachability checked in the background), the motivation strip's count and its goal-hit only during a session, the quote line's fade and click, the keys while typing |
| `8c9a2e5` | Learn: the shared requests.md and standing_rules.md are not rewritten when nothing changes (every start rewrote requests.md with the same text) |
| `98812f1` | Match cuts: right-click a WRONG PAIR row -> Run anyway (skip the pair check), the tool's --force-inputs -- the quick check can be overruled from the app |
| `8efec2a` | Audio chain on every A1 clip: each clip normalised to its own peak, Volume 0 dB, Panner 0 in the XML; Hard Limiter + Amplify +4.5 dB as a Premiere preset |
| `6a1d5b1` | Keep the Premiere preset byte for byte on every checkout (no line-end conversion) |
| `290c414` | 125 %: a retimed clip is counted from where Premiere really starts it -- no repeated frame at a RAW shot change (video10 at --speed 125 failed XML REPEAT A1 S02) |
| `60ec4d2` | Learned from video11, video12 and chocolate: no "!" in captions made from the heard words, follow-mode boundary words go to the caption that shows them, check-all scores a 23.976 key as Premiere placed it; the three fixes are new test cases |
| `dfbf506` | Tool README: night 11's quick WRONG PAIR check, the names-first rule (command line and batch), the audio chain and its Premiere preset |
| `15a5ad6` | Open in Premiere works whatever Premiere is doing: closed or on its Home screen, the edit gets its own project next to it (made from an empty Premiere 2024 project) and is imported into it; with a project open, into that one -- never twice |
| `f039afc` | learn: a key from a 23.976 sequence gets case.json srt_rate by itself, so check-all scores it as Premiere placed its captions |
| `06fec24` | Match cuts: Run anyway also after night 6's input-check stops ('the competitor is almost as long as the RAW'), not only after WRONG PAIR -- both are --force-inputs (found by the Robot Chicken hard case) |
