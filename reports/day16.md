# Day 16 (23 October): one task at a time

Started 07:21 (PC); you were using the PC, so code and light tests first. The backup shutdown is set for 15:30. C: 51.5 GB free, D: 2383 GB free at the start.

## The tasks

| # | task | state | how you use it |
|---|---|---|---|
| 1 | "Try with another RAW" keeps your title and text | **done** | a row that failed because of its RAW (WRONG PAIR, NOT IN THIS RAW, PICTURE NOT FOUND, a RAW that can't be read) has **Try with another RAW**. History has it too, on the failed event |

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
