# Day 17 (24 October): one task at a time

Started 07:52 (PC; you were at it). No shutdown was set (`shutdown /a`: "no shutdown was in progress"), and none will be. C: 86.6 GB free, D: 2104.6 GB free at the start.

## The tasks

| # | task | state | how you use it |
|---|---|---|---|
| 1 | Stop C: from filling up | **done** | nothing to do: the tool's cache is on D:\mr_cache; zips are made only for projects off C: |
| 2 | Find source much better | **done** (right 4 -> 7 of 10 with your RAWs OFF; wrong "Sure" 3 -> 0) | Progress > Find source as before; a row that isn't sure shows its top 3 |
| 3 | SceneProof song: real Clip Gain + your preset | **done** (Premiere test passed) | Open in Premiere, save (Ctrl+S): the app closes Premiere once, writes Clip Gain +5 / -1 dB + Volume 8.5 dB, opens it again. Import `assets\premiere\CorrectVolume.prfpset` once and drop it on the 2 song clips |
| 4 | Both "Try with another ..." buttons on every failed row | **done** | any failed or stopped row (queue, New video, History): **Try with another RAW** or **Try with another competitor** |
| 5 | Torrents that stopped for no reason | **done** (the laptop's case reproduced for real, then fixed) | nothing to do: a too-long path gets a shorter name by itself; Settings shows whether the torrent folder is reachable |

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
