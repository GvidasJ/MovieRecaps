# Day 17 (24 October): one task at a time

Started 07:52 (PC; you were at it, then away from about 08:00 on). No shutdown was set (`shutdown /a`: "no shutdown was in progress"), and the PC was not turned off. **C: 86.6 GB free at the start -> 174.9 GB at the end**; D: 2104.6 -> 2056.9 GB.

## The tasks

| # | task | state | how you use it |
|---|---|---|---|
| 1 | Stop C: from filling up | **done** | nothing to do: the tool's cache is on D:\mr_cache; zips are made only for projects off C: |
| 2 | Find source much better | **done** (right 4 -> 7 of 10 with your RAWs OFF; wrong "Sure" 3 -> 0) | Progress > Find source as before; a row that isn't sure shows its top 3 |
| 3 | SceneProof song: real Clip Gain + your preset | **done** (Premiere test passed) | Open in Premiere, save (Ctrl+S): the app closes Premiere once, writes Clip Gain +5 / -1 dB + Volume 8.5 dB, opens it again. Import `assets\premiere\CorrectVolume.prfpset` once and drop it on the 2 song clips |
| 4 | Both "Try with another ..." buttons on every failed row | **done** | any failed or stopped row (queue, New video, History): **Try with another RAW** or **Try with another competitor** |
| 5 | Torrents that stopped for no reason | **done** (the laptop's case reproduced for real, then fixed) | nothing to do: a too-long path gets a shorter name by itself; Settings shows whether the torrent folder is reachable |
| 6 | Test the Convert fix from this morning | **done** (the real Key & Peele file converts) | nothing to do |
| 7 | Look-alike scenes: the sound decides (The Dictator) | **built, OFF** (it helps this case little, and ON made video14's captions worse in the check-all) | nothing to do; `MATCH_CUTS_SOUND_TAKES=1` switches it on |
| 8 | Learn by yourself | **partly**: your 6 new fixes learned (video33-38: test cases, action captions); no new rule (below) | nothing to do |

## Task 1 -- stop C: from filling up (done)

- **The tool's cache is on D: now.** `work\cache` is a **directory junction** to `D:\mr_cache`. The tool, the check-all copies (work\snapX), the tests and the test cases all still use `work\cache` and find the very same files, so nothing in the tool had to change (and no cache was invalidated).
  - Moved safely (robocopy /MOVE: each file removed from C: only after it was copied; then checked): **3493 files, 44.3 GB**, nothing lost (the same count and size on D:). Nothing of MovieRecaps was running.
  - The setting: `MOVIERECAPS_CACHE_DIR` (an environment variable) when you want another folder; else `D:\mr_cache` on a computer with a fixed D: drive; a computer without D: keeps `work\cache`.
  - `setup_app.py` makes the move by itself (only when MovieRecaps isn't running, never from the app's Fix now). By hand: `.venv\Scripts\python tools\app\mr_app\core\cache_move.py` (`--dry` only says what it would move).
  - The free-space check before a run now counts the cache on D: (where it really is), not on C:.
- **Zips only off C:.** The automatic project zip is made only for a project on another drive than Windows' own (`D:\Shared\Projects`, a network folder). Never in `input\downloads` on C:, also not by the 10-minute sweep, so the zips you deleted there are not made again.
  - Two automatic zips are still in `input\downloads` (made before today: `When Islam Tried An Energy Drink (2)` and `The Worst First Word of All Time`). I did not delete them (your project folders); delete them if you want the space.
- **Real test:** a real run (the video8 test case, 3 min 20 s) after the move:
  - its 37 new cache files (760 MB: the proxy, the RAW index, the sound ...) were all written to `D:\mr_cache`
  - C: free before -> after the run: 216.57 -> 216.18 GB (-0.39 GB: only the run folder itself, `work\t17`, 67 MB, and Windows)
  - C: free before -> after the move: 86.6 GB -> 216.6 GB. (The move gave 44.3 GB; the rest was freed by something else at the same time -- not by me.)
- **Tests:** 5 new (`test_disk_day17.py`): no zip on C: (make, needs_zip and the sweep), a zip on D: / a network folder; the move keeps every file and leaves a junction; no move while MovieRecaps runs; a computer without D: keeps work\cache. One older test (`test_space.py`) now fakes the cache folder (on this PC it is on D:).

## Task 2 -- Find source much better (done)

### The real failures (from Find source's History, `ai_watch.json` and `work\find_source`)

The right answers I found by reading the clips' lines, searching them on YouTube, and comparing with TVmaze's episode summaries.

| clip | what it said | the right answer | why it was wrong |
|---|---|---|---|
| Instagram `Ddm3LHisOm6` (memezar, "Did he have a 40 for breakfast?") | Everybody Hates Chris S03E11 "Everybody Hates the Port Authority" -- **Sure 95 %** | **S03E07 "Everybody Hates Houseguests"** (Greg stays the week at Chris's house; a YouTube clip of this very scene is titled "Everybody Hates Houseguests - Mrs. Morello") | the AI gave a wrong episode number with its matching title, and day 16 called that "TVmaze confirms it" |
| Instagram `DZPlV3EMKoZ` ("I'm getting married ... she's your sister") | Not found | not known (a sketch). **Not** We're the Millers, as the post's text says (the AI says so too) | -- (Not found is fair) |
| YouTube `-thitow5F3s` (T-Bag "Not that hot?") | Prison Break S01E06 -- Check | S01E06 "Riots, Drills and the Devil (1)" (the heat wave), most likely; both AIs agree, no clip names the episode | -- (right, not proven) |
| YouTube `H15KjMR3mmo` (Family Guy, "fat guys wrestling club") | Not found (night 15, no AI); today's first try with day 16's code: Family Guy S10E17 -- **Sure** | Family Guy, most likely **S11E04 "Yug Ylimaf"** (time runs backwards: the lines are a conversation played in reverse; NexLev's new answer). Not proven | the AI's episode number + TVmaze's title for that number was taken as proof |
| YouTube `yiooj4IXHAs` (Fairly OddParents, "why aren't you miserable") | "Nickelodeon (1976)", a film -- Check | The Fairly OddParents **S02E13 "Totally Spaced Out"** (TVmaze's numbering: Mark Chang takes Vicky away, Timmy leaves Happy Town) | the hashtag #nickelodeon was taken for a title; the AI then said "Spaced Out" S01E01 (another episode) |
| day 16: The Dictator `Dd1e2BmyHCX` | The Dictator (2012) Sure | the same | -- |
| day 16: American Dad `DczA1q3MTge` | (your RAWs off) S03E03 Check | **"The American Dad After School Special"** (your file S03E02; TVmaze S02E02) | the AI's number |
| day 16: Dr. Dolittle 2 `DdVZePAga2I` | Sure | the same | -- |
| day 16: Family Guy `DZJfIY3IlTD` | (your RAWs off) S11E07 **Sure** | **S09E11 "German Guy"** (your RAW's sound) | the AI's number + TVmaze's title for it |
| day 16: Blue Mountain State `DZ56TJHuM75` | (your RAWs off) S03E12 Check | **S03E05 "Training Day"** (your RAW's sound) | the AI's number |

### What changed

1. **The AI is asked better** (NexLev, Algrow): every spoken line word for word, the on-screen text, the actors and characters, the show or film, **what happens in the scene** (it is compared with the summaries) and its **top 3 guesses**, each with why. A clip answered with day 16's question is asked once more with the new one (then remembered); when that fails, the older answer is used.
2. **The lines are searched for** (new `core\source_proof.py`): the 3 most distinctive lines, each with the show's name, in Algrow's free `youtube_search`. The first results' titles are read (clips of a scene are often titled with the episode), and the short results' transcripts (Algrow's free `fetch_transcript`) are compared with the clip's lines word for word (4 words in a row).
3. **The episode is checked, not only the show:** every episode summary of the show on TVmaze is compared with the clip (its lines, characters, setting and what the AI says happens); rare words count most, and a long summary doesn't win by its length. The AI's number alone never makes an episode Sure (it was wrong on 6 of 7 real clips). Its episode title counts more than its number, and a close title counts a little ("Spaced Out" -> "Totally Spaced Out").
4. **A second opinion:** when the episode isn't proven, Algrow's AI watches the clip too (once, remembered); its guesses join NexLev's, and the row says "Algrow's AI says ... too / instead".
5. **The top 3 in the row** when it isn't sure: the best one big, the other two small ("also: 2. ... (why) · 3. ... (why)"); Copy all has them too. "Not found" says what it tried.
6. **Sure only when proven:** your RAW's sound (as before); or the clip's words in the transcript of a YouTube clip titled with it; or the top YouTube clips of 2+ different lines titled with that episode; or the episode's summary fitting best and a titled clip naming it (or fitting clearly best and being the AI's own guess). A film: IMDb's year **and** a YouTube clip of its lines titled with the film. Otherwise Check.
7. **Nothing is paid twice:** every AI answer by link (`ai_watch.json`; the second opinion as `second:<clip>`); every search, transcript and TVmaze list in `%APPDATA%\MovieRecaps\source_proof.json`. Searches and transcripts are free (Algrow's subscription); only the AI watches use credits. The test below used 10 new watches + 9 second opinions once; running it again cost nothing (30 s).
8. Found on the way: a video without a transcript answers "No transcript available ..."; that text is no longer taken as its transcript.
9. **Added later: the web's quote sites.** The end of the 2 most distinctive lines (at most 8 words) is searched as an exact phrase on the web (DuckDuckGo's plain page; cached):
   - a quote site's result with the clip's own words that names the episode by its **title** (IMDb's quotes: `"Everybody Hates Chris" Everybody Hates Houseguests (TV Episode 2007)`) proves it (Sure)
   - one that names only its **number** (YARN: `Family Guy (1999) - S09E11 ... clip with quote "So, technically it's not prostitution, it's a porno."` -- the right episode, where both AIs said S16E09) puts it first, but is Sure only when another check agrees, because quote sites and TVmaze can number episodes differently (American Dad!)
   - for a film: the film named with the clip's words proves it; and when the AI knows nothing, the quote site's name is used
   - **Limitation found today:** DuckDuckGo refuses this PC's searches for a while after a burst (my first test sent 20 at once). The app now searches one at a time, 3 s apart, and makes no web search for 15 minutes after a refusal (then it works as before, without the web). In today's re-test it answered 2 searches, then refused again, so the before -> after table above is without the web step; the YARN result for the Family Guy clip was seen in a manual search.

### Test: the 10 clips, your RAW library OFF

| | before (day 16's code) | after |
|---|---|---|
| right (show and episode) | **4 of 10** | **7 of 10** |
| a **wrong** answer said **Sure** | **3** (EHC S03E11, Family Guy S10E17, Family Guy S11E07) | **0** |
| right and Sure | The Dictator, Dr. Dolittle 2 | The Dictator, Dr. Dolittle 2, **Everybody Hates Chris S03E07** (proven: 3 of its lines' top YouTube clips are titled "Everybody Hates Houseguests") |
| the right episode in the top 3 | -- | 8 of 10 |

- After, per clip: Prison Break S01E06 Check; Fairly OddParents **S02E13** Check (right); American Dad **S02E02 "After School Special"** Check (right); Yug Ylimaf in the top 3 (the first is Algrow's S20E06); the sketch: Check of a show the AI named, never Sure; Family Guy `DZJfIY3IlTD` and Blue Mountain State still the wrong episode (Check): a cutaway gag no summary describes, and a scene the summaries don't mention, with no titled clip naming either episode.
- With your RAWs ON (as you use it), day 16's 5 stay Sure by their sound (that part is unchanged).
- Time: 3.5 min for the 10 at once with the 19 new AI watches; 30 s again from the caches.
- **Tests:** 8 new (`test_find_source_day17.py`, fakes only: no network, no credits). Day 16's test changed: TVmaze having the AI's own episode title no longer makes an episode Sure. App: 1251 passed, plus the real aria2c test alone (inside the whole suite it crashed Python once again: the known night-15 crash, see Still broken).
- The keys: a scan of the app's code, the reports and the three Find source files finds neither key.

## Task 3 -- SceneProof song: real Clip Gain + your preset (done)

- **What happens now** (a SceneProof edit, with its song on A2):
  1. Open in Premiere imports the edit as before. The XML still gives the song Volume +13.5 / +7.5 dB (an XML can't carry Clip Gain), so it already sounds right before step 3.
  2. You save (Ctrl+S).
  3. The app sees the save (the title without "*", the file newer), **closes Premiere** (nothing is lost: everything is saved), writes into the saved project for each song clip:
     - the real **Clip Gain**: `<Gain>1.7782794237136841</Gain>` (+5 dB) on the first, `0.89125096797943115` (-1 dB) on the second -- your `song.prproj`'s own values
     - **Volume 8.5 dB** on both (the Volume's Level, stored as 0.4732)

     and **opens it again**. Only those values change; the project as Premiere saved it is kept next to it (`<name>.before_song_gain.prproj`).
  - Also: a saved project whose song has no Clip Gain yet gets it the next time you press Open in Premiere (before Premiere opens it); Premiere closed by you after saving: written, not opened again; another project opened, a message on closing, or no save in 30 minutes: nothing is changed, and the next Open in Premiere does it.
  - The gains come from the edit's own song clip comments ("your Clip Gain +5 + Volume 8.5 dB"), so older SceneProof runs work too.
- **Your preset:** `docs\sceneproof\CorrectVolume.prfpset` is now `assets\premiere\CorrectVolume.prfpset` (the same bytes), and the song clips' comments say "drop the preset CorrectVolume on it". **Import it once** like the A1 preset: Effects panel > right-click Presets > Import Presets... > `assets\premiere\CorrectVolume.prfpset`.
  - Its Hard Limiter has Input Boost **2 dB for a mono clip** and 0 dB for stereo / 5.1. Premiere makes the song clips **mono** from the XML (the saved project: `[{"channellabel":0}]`, one channel), so they get **2.00 dB, as your project has**. (Your song.prproj's clips are stereo with 2.00 dB set by hand; the preset gives a stereo clip 0 dB -- so the mono clips are the ones that match.)
- **Premiere test (08:32-08:45, you were away 39 min):** a copy of day 16's SceneProof edit (`work\t17\song\edit`):
  - Open in Premiere: "Premiere Pro started with edit_edit.prproj ... and imported the edit into it -- save it (Ctrl+S) -- then the app closes Premiere once, writes the song's Clip Gain ... and opens it again"
  - saved with Ctrl+S (as you would): within 2 s the app closed Premiere, wrote the values and opened the project again, with no message
  - then Premiere saved the project itself once more (a marker added and undone, Ctrl+S): its own file still has **Clip Gain +5.00 / -1.00 dB and Volume +8.50 / +8.50 dB on A2, exactly as `song.prproj`** -- so Premiere read them
  - The preset drop itself needs the mouse: yours, once per edit (as day 16).
- **Tests:** 7 new (`test_song_gain_day17.py`: the values, only those lines change, once only, a backup; the watch: closed after your save, written, opened again; another project or Premiere closed by you; day 16's real saved project). App: 1258 passed. Day 16's song test now expects "CorrectVolume" in the clip comment.

## Task 4 -- both "Try with another ..." buttons on every failed row (done)

- **Where:** every row that failed or stopped, for any reason (a download stop, WRONG PAIR, a convert stop, a full disk, a crash ...):
  - the Match cuts queue's rows
  - the New video rows (a row that stopped while downloading has no edit yet: it gets them too)
  - History's failed edits (now any failed edit, not only the RAW ones)
- **Try with another RAW** (as day 16): New video keeps the competitor, the headline, the YouTube title, the channel, Fast / Thorough and 100 / 125 %; the RAW field is empty and has the cursor.
- **Try with another competitor** (new): New video keeps **the RAW** -- its file when it is on this PC (the download where it is, or the project's copy: **no new download**), else its link or magnet -- and the headline, the YouTube title, the channel, the mode and the speed; the competitor field is empty and has the cursor. The amber line says what was kept and which competitor gave which stop.
- A row that finished well (PASS, OK with a note, Cuts ready) has neither.
- **Tests:** 5 new (`test_another_comp_day17.py`): a WRONG PAIR on the queue, a download stop (a torrent removed) and a convert stop on New video, and History's failed edit each show both, and each button fills in everything. App: 1262 passed.

## Task 5 -- torrent downloads that stopped for no reason (done)

- **The cause, measured:** the Everybody Hates Chris season torrent's names are 95 (its folder) + 22 (`Season 3 (AMZN WEB-DL)`) + 110 (the episode) characters.
  - on the PC, in `D:\Shared\Downloads\Torrents`, the episode's path is **just under** Windows' 260
  - on the laptop, `\192.168.101.11\Shared\Downloads\Torrents` is 14 characters longer: **over 260** -> aria2 "cannot find the path specified" -> the app said "the folder is not there any more"
  - **the network folder itself is not a problem:** aria2 writes a network folder as well as a local one (day 13's real test over `\127.0.0.1\C$`, run again today: passed). Its longer path is what took the laptop's torrent over the limit. So nothing is downloaded locally and moved; it goes straight there as before.
- **Reproduced for real** (aria2, a local seeder, the very names, into a network folder `\127.0.0.1\C$\...`): it stopped the same way, and it was **not even the ticked episode**: aria2 also writes the small piece the ticked episode shares with the next one (S03E08), and that file's path was over the limit.
- **The fix:** every file of the torrent whose path would be over 240 characters (260 less room for aria2's `.aria2` and the like) is given a shorter path with aria2's own `index-out`: `<save folder>\<the torrent's name, cut>\<the episode's own name>` (the season folder left out; the episode keeps its name; its name is cut too only when it alone is too long). Short paths are as before. The same real test then **downloaded the episode whole**, with its own name, in a path under 240.
  - A download that stopped this way before goes on with **Resume** (it is given to aria2 again with the short names).
- **The words:** a stop whose file path is over 260 says "**The path is too long for Windows** (N characters, the limit is 260) ...", never "the folder is not there". A folder that is really missing still says that.
- **Settings:**
  - "Save torrents to" and "Save projects to" turn `/` into `\` by themselves (`//192.168.101.11/Shared/...` -> `\192.168.101.11\Shared\...`), also for a settings file written before
  - under "Save torrents to": "**reachable and can be written**" (green), "reachable, but it can't be written" or "not reachable now" (amber), checked in the background like the projects folder (it writes and removes a tiny test file)
- **A restarted download** (Remove + Try again) reads "Save torrents to" at that moment, so it uses the **current** folder (checked in a test).
- **Tests:** 7 new (`test_torrent_day17.py`, the real aria2 one included); all torrent tests: 57 passed. App: 1269 passed.

## Task 6 -- the Convert fix from this morning, tested (done)

- **The rule (295a8d7):** a copy whose sound ends with the picture may be up to 1.5 s shorter than the original's sound, when the original's sound ran on **after** the picture's last frame. Nothing a clip can play is lost.
- **New tests** (`test_convert_day17.py`, 4): the real Key & Peele numbers (sound 1277.48 s, picture 1276.65 s, the copy's sound 1276.64 s): **OK**; sound cut **inside** the picture (1270.00 s; or 0.75 s short of a sound that ended with the picture): **still a stop**; more than 1.5 s gone: still a stop; a longer sound in the copy: still a stop.
- **Real run:** Convert on a copy of `input\downloads\2026-10-24_The Most Suspicious Question of All Time\Key.&.Peele.S05E11...mkv` (HEVC + AC-3 5.1): **done in 83 s** -- the picture copied as it is (MP4, lossless, 30609 of 30609 frames, 1276.651 s both), the sound made AAC: 1277.484 s -> 1276.640 s, accepted by the new rule. (My copy and its result were deleted afterwards; your file is untouched.)

## Task 7 -- look-alike scenes: the sound decides (built, OFF behind a switch)

- **What I found in your Dictator case** (`2026-10-23_The Most Awkward Interview of All Time`):
  - The competitor's sound is found at **one RAW offset from 17.25 s to its end** (79 sound windows, the film +99.79 s): the dialogue plays straight through.
  - But the competitor **re-edited the picture** over that dialogue: e.g. at 18.7 s it shows Larry King's close-up while the film, at the same moment of the dialogue, shows the wide shot; at 20.7 s the wide ABN shot where the film is on Larry. At the sound's own moment the film's picture is another shot (picture score ~0.1 against ~0.85 for the frames the tool chose). So "the frame where the sound sits" is not the picture there.
  - The competitor has **17 shot changes** (PySceneDetect), the tool's cut list 62 cuts. Inside the competitor's shots the tool's pieces jump between near-identical frames of static interview shots, and part of the competitor is slower than the film (38.3 s against 29.2 s at 100 %).
- **What I built** (`tools\match_cuts\match_cuts\sound_takes.py`, a step of the Premiere plan before the takes are joined; it changes nothing in the cached analysis, so no case has to be analysed again):
  1. the sound's takes: confident sound windows at one RAW offset (at least 8)
  2. the take's exact line from the picture of the pieces already on it (the sound can sit a few frames off the picture)
  3. a piece off that line goes on it **only when the line's picture scores as well** (within 0.03) as the piece's own frames -- so the re-edited shots above rightly stay
  4. the take goes on past the sound while the picture fits
  5. **one competitor shot, one take**: inside each competitor shot, the doubted pieces go on one line (a piece's own moment played on at 100 % or at 80 %) when every piece fits it as well as its own frames; the line nearest the sound wins a tie
  - never the same RAW moment twice inside a take (one line)
- **Measured on your case** (the same caches, switch off vs on):

  | | before (off) | after (on) |
  |---|---|---|
  | V1 clips in 1_edit.xml | 27 | **26** |
  | RAW jumps | 20 | 19 |
  | clips of 8 frames or less | 14 | 15 |
  | pairs of clips showing the same RAW moment | 23 | 22 |

  One competitor shot (frames 0-19) became one take at 80 %; in the other 7 shots no single line fits every piece (the cuts between near-identical angles that even PySceneDetect doesn't see). **That is far from the competitor's ~18 shots**, so it does not fix this case.
- **The switch:** `MATCH_CUTS_SOUND_TAKES=1` on, `=0` off; **OFF by default.**
- **The check-all with it ON** (09:54-10:28, 2 streams, a frozen copy of the code `work\snap17a`; every case analysed from scratch -- the check-all's old caches were gone):

  | case | it acted? | ON | OFF (the same caches) | day 16 |
  |---|---|---|---|---|
  | video14-fixed | yes: 7 pieces on the sound's take, 1 competitor shot one take | PASS / captions **11/42** / word errors 7.0 % / cuts 1/3 / framing 4/10 | PASS / **16/42** / 8.0 % / 1/3 / 4/10 | PASS / 16/42 / 8.0 / 1/3 / 4/10 |
  | video3 | yes: the whole competitor one take (4 clips) | hard checks PASS | not run | PASS |
  | video8-fixed | found the take, moved nothing | PASS / 0/16 / 9.1 % / 1/4 / 4/11 | (the same XML) | the same |
  | laptop010-fixed | no | PASS / 10/34 / 7.7 % / 1/9 / 11/13 | (the same XML) | the same |
  | video30-fixed | found the take, moved nothing | PASS / 18/43 / 16.9 % / 4/7 / 2/13 | (the same XML) | the same |

  - **video14 got worse** with it ON: 5 fewer captions exactly right (their timing follows the pieces it moved), so by your rule it **stays OFF**. I stopped this check-all there (10:28) and used the graphics card for a check-all of today's default code instead (below).
- **Tests:** 5 new (`test_day17_sound_takes.py`): look-alike pieces put on the sound's take, never a repeat; a real cut the line doesn't fit stays; straight through past the sound; one competitor shot one take at 80 %; off by the switch and without sound.

## Task 8 -- learn by yourself (partly done)

- **The new learn-queue items, all six learned** (video33-38, your fixes of 23 October; `learn_queue.md` marks them done). Each is a new test case in `tests\real\videoNN-fixed` with your finished edit as the key (captions, cuts, framing) and the frame and watermark its run used:

  | case | video | captions you changed | cuts | framing |
  |---|---|---|---|---|
  | video33 | The Coldest Dad (Dr. Dolittle 2) | none (5 action texts) | 1 of 12 edges moved, 1 clip removed | 14 clips moved sideways (median +104 px) |
  | video34 | Luck Not On His Side (Family Guy) | none (5 action texts) | 4 of 6 edges moved | 1 moved (-102 px) |
  | video35 | Specific Punishment (The Cleveland Show) | 3 words ("Tivo" -> "TiVo"); 3 action texts | 2 of 14 moved, 1 removed | 11 moved (+82 px) |
  | video36 | Funniest Accent (Hot Fuzz) | none (1 action text) | 2 of 8 moved, 1 removed | none |
  | video37 | Worst Bodyguard (The Dictator) | 1 word | 1 of 16 moved, 1 removed | 12 moved (-260 px) |
  | video38 | Police Officers (Scottish comedy) | none (6 action texts) | 2 of 8 moved | 13 moved (-173 px) |

  - **Taken:** your 20 new action texts (the examples of the action suggestions), as Learn does.
  - **Not taken** (as on days 15-16): Learn's glossary line `Tivo -> TiVo` -- the glossary also goes to the speech recognition as hints, so a new line re-transcribes every video.
  - Fixed on the way: Learn's new cases had no `"style": "new"` (day 16 added it by hand), so a check-all ran them unscored; they have it now.
- **The biggest difference with your fixes: still framing** -- the picture moved sideways on 4 of the 6 new videos and on 25 of all cases. But the moves differ clip by clip (video37: from -653 to +292 px), not one shift: it is *which person* each clip is centred on. A rule for that needs its own check-all from scratch (3-4 hours on today's numbers), and the graphics card was busy with Task 7's and the default code's check-all. Building a rule without its check-all would break "nothing may get worse", so **no new rule today**.
- **Incident (fixed):** a script of mine that marked the six lines in `D:\Shared\Projects\learn_queue.md` failed half-way and left the file empty. I rewrote it at once, line for line, from the copy I had read a minute before (26 entries, all `[x]`), and checked that the app reads all 26. Please glance at it.

## Still broken

- **The Dictator interview (Task 7):** still about 26 clips where the competitor has about 18 shots. The cause is that the competitor re-edited the picture over the dialogue and cuts between near-identical camera angles; the sound can't decide there (details in Task 7).
- **Find source:** 3 of the 10 clips still give the wrong episode (as Check, never Sure): the Family Guy cutaway gag (`DZJfIY3IlTD`), the Blue Mountain State condom scene (`DZ56TJHuM75`) and the Family Guy reverse-time scene (`H15KjMR3mmo`, the right one in its top 3). The quote-site search names the right Family Guy episode (S09E11) in a manual test, but DuckDuckGo stopped answering after my first burst of searches (it shows an "anomaly" page), so the app now searches one at a time and pauses 15 minutes after a refusal.
- **The real-aria2c torrent test** crashed Python once inside the whole app suite again (an access violation, the known night-15 crash); alone it passes.
- **Framing** stays the biggest difference with your fixes (Task 8): which person each clip is centred on.
- **Two of the six new test cases fail their hard checks** (first runs, so nothing got worse -- these are found problems): video35 (The Cleveland Show) has a 2-frame flash of another RAW shot at 27.2 s in its XML; video37 (The Dictator, "Worst Bodyguard") has a clip whose speaking person is outside the window.

## Check by hand

1. **SceneProof song:** import `assets\premiere\CorrectVolume.prfpset` once (Effects > right-click Presets > Import Presets...). On your next SceneProof edit: Open in Premiere, Ctrl+S, wait for Premiere to close and open again, then look at A2: Clip Gain +5 / -1 dB, Volume 8.5 dB. Drop CorrectVolume on both song clips.
2. **`D:\Shared\Projects\learn_queue.md`:** I rewrote it after my mistake (Task 8); check that it looks as before (26 lines, video13-38, all learned).
3. **Find source:** paste your next links; a row that isn't sure shows its top 3 small under the verdict.
4. **Try with another competitor:** on your next failed row.
5. **The laptop's torrent folder:** in Settings the line under "Save torrents to" should say "reachable and can be written"; then the Everybody Hates Chris season torrent again (Remove, Try again).
6. **Two automatic zips** are still in `input\downloads` (`When Islam Tried An Energy Drink (2)`, `The Worst First Word of All Time`): delete them if you want the space; no new ones are made there.

## On the laptop

Close MovieRecaps, then in PowerShell:

```
cd C:\Users\gvida\Desktop\bh\MovieRecaps
git pull
.venv\Scripts\python tools\app\setup_app.py
```

- No new packages.
- `setup_app.py` moves the tool's cache to `D:\mr_cache` **only if the laptop has a fixed D: drive** (otherwise it says "the tool's cache stays in work\cache"). It needs MovieRecaps closed.
- Settings > "Save torrents to": if it is `//192.168.101.11/...`, it becomes `\192.168.101.11\...` by itself when you press OK.
- Import `assets\premiere\CorrectVolume.prfpset` in Premiere once (Effects > Presets > Import Presets...).
- The NexLev / Algrow keys stay only on the PC (as on day 16).

## The check-all of today's code (switch OFF, as you get it)

- **How it ran:** from the frozen copy `work\snap17a`, 2 streams from 10:29 until about 14:30, every case analysed from scratch (the check-all's caches on C: were gone). "before" is day 16's table.
- **Every older case that finished has exactly its day-16 scores** -- today's changes don't touch the edit (the Find source, zip, cache, torrent, song and button work is all in the app; the tool only got the switch-OFF Task 7 step and a comment).

| video | before (day 16): hard / captions / word errors / cuts / framing | today | time |
|---|---|---|---|
| video14-fixed | PASS / 16/42 / 8.0 / 1/3 / 4/10 | the same (switch OFF) | 12 min |
| video8-fixed | PASS / 0/16 / 9.09 / 1/4 / 4/11 | the same | 6 min |
| laptop010-fixed | PASS / 10/34 / 7.69 / 1/9 / 11/13 | the same | 9 min |
| video30-fixed | PASS / 18/43 / 16.85 / 4/7 / 2/13 | the same | 10 min |
| video9-fixed | PASS / 4/18 / 44.44 / 3/4 / 4/4 | the same | 8 min |
| video24-fixed | PASS / 14/42 / 42.39 / 4/7 / 1/14 | the same | 10 min |
| video11-fixed | PASS / 14/23 / 15.38 / 2/3 / 2/8 | the same | 13 min |
| laptop013-fixed | PASS / 12/22 / 13.33 / 1/5 / 2/6 | the same | 7 min |
| video15-fixed | PASS / 7/22 / 33.93 / 3/8 / 12/21 | the same | 17 min |
| chocolate-fixed | PASS / 7/22 / 16.67 / 1/3 / 5/5 | the same | 15 min |
| video017-fixed, video2, spiderman-school, video020-fixed | PASS (hard checks) | PASS (hard checks, `--fast`) | 14-32 min |
| zendaya | PASS | PASS | 12 min |
| video6-fixed | PASS / 18/32 / 10.0 / 5/7 / 9/9 | the same | 10 min |
| laptop004-fixed, video4 | PASS (hard checks) | PASS (hard checks, `--fast`) | 10-18 min |
| video19-fixed | PASS / 8/33 / 30.0 / 3/8 / 1/10 | the same | 19 min |
| video3 | PASS | PASS (this one ran with the switch ON: the rule made it one take) | 17 min |

**The six new cases (first runs: today's numbers are their "before"):**

| video | hard | captions exactly | word errors | cuts | framing | time |
|---|---|---|---|---|---|---|
| video33-fixed (Dolittle 2) | PASS | 28/81 | 15.8 % | 3/6 | 6/24 | 48 min |
| video34-fixed (Family Guy) | PASS | 2/20 | 34.1 % | 0/3 | 0/4 | 11 min |
| video35-fixed (Cleveland Show) | **FAIL**: 1 flash frame left in the XML (2 frames of another RAW shot at 27.2 s) | 4/33 | 15.7 % | 3/9 | 9/23 | 27 min |
| video36-fixed (Hot Fuzz) | PASS | 0/32 | 27.2 % | 0/3 | 6/6 | 11 min |
| video37-fixed (The Dictator) | **FAIL**: the speaking person outside the window in one clip (XML PERSON) | 4/44 | 11.5 % | 3/7 | 0/11 | 22 min |
| video38-fixed (Scottish comedy) | PASS | 8/33 | 43.0 % | 2/6 | 5/19 | 31 min |

## C: free space before -> after

- **Start 86.6 GB -> end 174.9 GB.**
  - the tool's cache moved to `D:\mr_cache` (Task 1): 44.3 GB off C: (47.1 GB there now, with today's runs)
  - no zips on C: any more
  - (C: also gained space from something else at 07:55, not me: 86.6 + 44.3 would be 131 GB, it read 216.6 GB)
  - today's check-all keeps its caches in `work\check-all17` (40.7 GB on C:, from-scratch analyses of 27 cases: the next check-all reuses them). Delete that folder if you need the space.

## Tests at the end

- **Tool: 1657 passed, 0 failed** (101 skipped: the slow tests that run only with `--runslow`, and Linux-only ones). One older test lists the learned cases by name; today's six were added to it, as on day 16.
- **App: 1273 passed, 0 failed**, the real-aria2c torrent test included (no crash this time).
- `.venv\Scripts\python tools\app\setup_app.py`: "the tool's cache is on D:\mr_cache (work\cache points there)", "done: everything is as the project expects" (exit 0).
- **The keys:** a scan of 456 files (the code, the reports, the docs, Find source's three files, the shared suggestions and learn queue) finds neither key.
- 17 commits today, all pushed to `claude/new-session-ol4era`.
- **The shutdown:** none was set and the PC stays on (as you asked).

## 5 new suggestions (saved in `D:\Shared\Projects\suggestions.md`, not built)

1. **Find source fills "Try with another RAW" by itself** (small): a WRONG PAIR row runs Find source on its competitor (cached: free) and puts the proven episode into the RAW field.
2. **The competitor's own shot changes found more carefully** (half a night): the Dictator interview cuts between near-identical angles PySceneDetect misses; the people stage's face positions could find them, then each shot can be one take.
3. **The torrent picker shows a shortened name before the download** (small): "saved as ...\S03E07 ...mkv" when the path would be too long.
4. **Real Clip Gain for any channel's music** (small): the saved-project way of Task 3 for every channel with a song or sound effect.
5. **The check-all's caches on D:** (small): today every case was analysed from scratch because the caches on C: were gone; on D: they'd stay, and a check-all after a small change takes minutes per case.

## What I decided on my own

1. **The tool's cache: a junction `work\cache` -> `D:\mr_cache`**, not a new setting inside the tool: every part of the tool, the check-all copies and the tests keep working unchanged, and no cache was invalidated. `MOVIERECAPS_CACHE_DIR` chooses another folder.
2. **The two automatic zips already in `input\downloads`** were left (your project folders); no new ones are made there.
3. **Find source's AI answers to day 16's question were asked again once** with the new question (top 3, what happens in the scene): 10 new watches + 9 Algrow second opinions today; all remembered now.
4. **A quote site's episode number alone is not Sure** (TVmaze and quote sites can number seasons differently: American Dad!); an episode title on the page, or a second check agreeing, is.
5. **Torrents: no "download locally, then move"** for network folders: aria2 writes a network folder fine (real test); the laptop's stop was the path length, now fixed for both.
6. **Task 3 closes Premiere** after you save (only when the project shows no unsaved changes and it is the edit's own project), writes the gains and opens it again, as you asked. Anything else (another project, a message, no save in 30 minutes) leaves Premiere alone; the next Open in Premiere writes them.
7. **Task 7 stays OFF** (video14's captions got worse with it ON), and I stopped its check-all early to run today's default code on 27 cases instead, including the six new ones.
8. **Learn's glossary line `Tivo -> TiVo` not taken** (as on days 15-16).
9. **Find source's test:** the clips with no proven answer were judged by the answers I could check (Yug Ylimaf for the reverse-time Family Guy clip is NexLev's, not proven; the "she's your sister" sketch is unknown: right = never a wrong Sure).
