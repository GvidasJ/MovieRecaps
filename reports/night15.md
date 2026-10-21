# Night 15 (22 October): one task at a time

Started 02:11 (PC). The backup shutdown is set for 15:30. C: 73.7 GB free, D: 2.4 TB free at the start.

## The tasks

| # | task | state | how you use it | check by hand |
|---|---|---|---|---|
| 1 | Emoji in names (Premiere import) | **done** | nothing to do: new folders and files get plain names; old ones were renamed at the start of the app | open the 6 kept folders' projects as before (below) |

## Task 1 -- Premiere "File Import Failure" (emoji in folder names)

- **The cause, proven in Premiere 2024 on this PC:** the same edit (the Halloween run) imported twice.
  - In a folder named `...Costume of All Time 🎃😭`: **"File Import Failure"**.
  - In `...Costume of All Time` (no emoji): imported with no error (the sequence, raw_premiere.mp4, the WAV, the frame and the watermark in the bin).
  - Day 14 had already shown that non-English letters, `[ ]` and `( )` import fine. The `pathurl`s are correct: UTF-8 with `%` codes (`%20` for a space, `%5B %5D` for `[ ]`, `%C4%84` for Ą), and a network path keeps its server (`file://192.168.101.11/Shared/...`). A test checks this now.
- **What changed:**
  - New folder and file names keep only letters (any language), numbers, spaces and `- _ ( )`. Apostrophes go (`she’d` -> `shed`), `&` becomes `and`, and any other sign or emoji becomes a space.
  - The title with its emoji stays in project.json, title.txt and on the board. The sequence name (which Premiere uses to name the render) keeps the apostrophe but has no emoji.
- **Old folders:** the app renames them once, about 20 seconds after it starts. It uses day 13's safe rename: it waits while anything uses a folder, keeps every link, and updates the XML's media paths. On this PC, `D:\Shared\Projects` gave:
  - **renamed (2):** `2026-10-21_The Funniest Halloween Costume of All Time`, and `2026-10-21_The Most Dramatic Dad of All Time` (it was "... 💀😭 (2)").
  - **kept their name (6):** a Premiere project of yours plays their files, so a new name would make it "Media offline":
    - Lab Accident, Math Class and Driving Lesson are uploaded
    - Red Flag is fixed
    - milk -> `D:\Downloads\Copied_milk_POPW\...\milk_POPW.prproj`
    - kid -> `doro_POPW.prproj`

    These are finished, so you don't need to import them again. If you ever do, rename the folder by hand after closing its Premiere project.
  - The laptop renames its own copies when MovieRecaps starts there after `git pull`.
- **Tests:** app 1144 passed. 13 new tests (`test_names_night15.py`) and 9 updated old ones.
  - One old test (`test_locks`) failed only between 00:00 and 03:00, because "3 hours ago" fell on yesterday. I fixed that test.

## Time before -> after

## Still broken

## Check by hand

## On the laptop

## What I decided on my own
