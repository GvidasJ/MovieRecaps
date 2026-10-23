# Day 16 (23 October): one task at a time

Started 07:21 (PC); you were using the PC, so code and light tests first. The backup shutdown is set for 15:30. C: 51.5 GB free, D: 2383 GB free at the start.

## The tasks

| # | task | state | how you use it |
|---|---|---|---|
| 1 | "Try with another RAW" keeps your title and text | **done** | a row that failed because of its RAW (WRONG PAIR, NOT IN THIS RAW, PICTURE NOT FOUND, a RAW that can't be read) has **Try with another RAW**. History has it too, on the failed event |
| 2 | The zip has the whole project folder | **done** (Premiere test passed) | nothing to do: each finished project's `<name>.zip` now holds the whole folder; its size is on the board card ("zip 2.2 GB") |

## Task 1 -- "Try with another RAW" (done)

- **On the queue:** a row that failed because of its RAW gets the button **Try with another RAW**. "Because of its RAW" means one of these:
  - WRONG PAIR, NOT IN THIS RAW or PICTURE NOT FOUND
  - a RAW that can't be read: "could not decode source frames ... by PTS" (the Ted 2 stops), or FFmpeg's "damaged video" words about the RAW
- **What it does:** it opens New video with everything already filled in:
  - the **YouTube title**
  - the **headline** (the template text, both lines)
  - the **channel**
  - the **competitor clip** (its file, or its link when the file is gone)
  - Fast / Thorough and 100 % / 125 %
- **The RAW field is empty and has the cursor.** You only paste a link, pick a file or drop a torrent, then **Make the video**. The amber line says what was kept and which RAW gave which stop.
- **History:** the failed event ("Edit failed · WRONG PAIR") now shows the YouTube title and the headline on a third line, with the same button. History reads every project's project.json, so this works after a restart too. The search also finds the YouTube title.
- **Real test:** your real WRONG PAIR project, the Dolittle one (`D:\Shared\laptop_cases\2026-10-20_When Your Dad Takes Your Phone Away`: the YTS "Dr. Dolittle 2" that holds the 1998 film). Try with another RAW filled in:
  - the headline "The fact that she thought her dad / would care is hilarious 📱😭"
  - the YouTube title "When Your Dad Takes Your Phone Away 📱😭"
  - the channel MikeMemesYou
  - the competitor `Video by wasted [DdVZePAga2I].mp4`
  - and the RAW field empty

  This was only read: nothing in that folder was changed.
- **Tests:** app 1214 passed, 0 failed (6 new: `test_another_raw_day16.py`, one of them on the real Dolittle project). The real-aria2c torrent test still crashes inside the whole suite, as on night 15. It passes alone (1 passed).

## Task 2 -- the zip has the whole project folder (done)

- **What's in it now:** everything in the project folder, with the same folders inside, under one folder named after the project (without emoji):
  - `edit\` (1_edit.xml, the captions, `extras\` with its media: the RAW or its piece, its WAV, the frame, the watermark, the song)
  - `logs\`, the competitor clip, `project.json`, `template.png`, `title.txt`, `titles_found.txt`
  - `HOW TO OPEN.txt`
- **Videos, sounds and pictures are stored as they are** (no compressing again), so it is fast and lossless. Text is compressed.
- **Never in it:** any `.zip` / `.7z` / `.rar` (itself or an older one), half-written files (`.tmp`, `.part`, `.aria2`), and the work locks.
  - A file that is the same file twice (a hard link) goes in once, as the copy the edit plays. HOW TO OPEN names the other one.
  - A file the edit plays from outside the project folder goes into `outside\` when it is small. When it is big, it is left out and named.
- **It is made in the background** right after the edit (the queue doesn't wait). The 10-minute sweep makes it again when a file changes.
  - project.json alone doesn't count as a change, because the board changes it.
  - A zip of night 15's kind is made again by itself.
- **The board card** shows "zip 2.2 GB" (the tooltip says where it is).
- **A safety rule:** the zip is not made when it would leave its drive with under 40 GB free.
- **The existing zips were remade** (`D:\Shared\Projects`, 35 finished projects): 0.8 GB -> **67.8 GB** in all, in **92 s**.
  - The big ones still hold the whole movie, because they were made before Task 3: mindculpt 8.6 GB, Are We There Yet 7.4 GB, Funniest Accent 7.2 GB, Lab Accident 5.9 GB, Borat 5.2 GB.
  - The smallest: 69-110 MB.
  - I checked one (Coldest Dad, 2.2 GB) with a full CRC check: every file whole.
  - HOW TO OPEN of a Just cuts project names `cuts\1_edit.xml`.
- **Premiere test (07:45, you were away):** I copied the "milk" project, pointed its XML at that copy, zipped it, then **hid the copy** (renamed it) so the XML's paths led nowhere, and unzipped it into another folder. Then I imported the XML with Open in Premiere and saved.
  - **No message, no "Link Media", no Media offline.**
  - The saved project plays every file from the unzipped folder: `raw.mp4`, `raw_stereo.wav`, the frame and the watermark, each with its real size and length.
  - Premiere finds the files next to the XML by itself, so your friend needs nothing else: unzip, then File > Import `edit\1_edit.xml`.
  - Test folder: `work\t16\zt`.
- **Tests:** app 1220 passed, 0 failed (7 new: `test_zip_day16.py`; night 15's zip test changed to the whole folder).
