# Task 4: best possible captions, using the PC's full power

## Result

`python -m match_cuts check-all` on the four test videos. Before means the code as committed after Task 3
(4eca0c0), with its runs scored by the same final scorer (`reports/task-4/scorecard_before.json`). After is
`reports/task-4/scorecard_after.json`.

| video | hard checks | captions exactly | word errors | rule breaks | run time |
|---|---|---|---|---|---|
| deadpool | pass → pass | 1/60 (2 %) → **47/60 (78 %)** | 21.8 % → **1.6 %** | 14 → **0** | 1m33s |
| spiderman-school | **fail → pass** | 10/64 (16 %) → **14/64 (22 %)** | 5.5 % → 6.1 % | 13 → **0** | 8m26s |
| zendaya-age | **fail → pass** | 3/19 (16 %) → 3/19 (16 %) | 38.5 % → **22.5 %** | 5 → **0** | 3m44s |
| zendaya (no answer key) | pass → pass | – | – | – | 2m17s |
| **all answer keys** | | **14 → 64 of 143 (10 % → 45 %)** | **16.2 % → 6.4 %** | **32 → 0** | 16m01s |

- **Exact captions:** a caption counts as exact when it has the same text as yours and starts within 2 frames of
  where your caption's first moment plays in the tool's edit.
- **Word errors:** counted only where both edits play the same moment.
- **Rule breaks:** your style rules. A caption over 20 characters or 4 words, a full stop or comma, or a gap or
  overlap.
- **Hard checks:** the run's deliverables, determinism and coverage. Before, Spider-Man and Zendaya-age failed the
  person check (S14, and S08 + S14).

**The score did not improve on every measure of every video. Two measures did not move:**

- **Spider-Man's word errors went up by 0.6 points.** The old captions copied the competitor's text. That text
  doesn't contain speech that my edit plays but neither yours nor the competitor's does. "Genius kids, right? Yes,
  yeah" is the case here: "never cut inside a word" lengthens clips S12–S14. Every word heard is captioned now, so
  those words count as extra.
- **Zendaya-age's exact count stayed at 3.** The captions it misses are lost to the edit, not to the caption logic:
  - The cut before "Tom Holland" now falls 0.6 s earlier.
  - My edit keeps the interviewer's "Family situation", which you cut.
  - Your edit is 12.9 s; mine is 17.7 s.

  The three exact captions also changed:
  - before: "Tom Holland", "and I am", "25"
  - now: "and age", "25", "a daughter"

The score stopped rising: rounds 13 and 14 gave identical results on all four videos. The final run on the committed code matches them.

## What changed

### Speech recognition (`asr.py`, `align.py`, `transcribe.py`, `asr_bench.py`)

- **On the GPU.** Whisper runs through faster-whisper / CTranslate2 4.8.2 on CUDA, using the pip CUDA 12
  libraries (`nvidia-cublas-cu12`, `nvidia-cudnn-cu12`, `nvidia-cuda-runtime-cu12`; `asr.cuda_dlls()` finds them
  on Windows).
  - PyTorch 2.11.0+cu130 runs the forced aligner, Parakeet and Canary on the RTX 5080. The card is sm_120, so it
    needs CUDA 12.8 or newer.
  - Nothing fell back to the CPU.
  - Confirmed by a run from an empty cache (`work/check-all-fresh`, zendaya-age, 11m10s in all):
    *Speech recognition: large-v3 on the GPU (NVIDIA GeForce RTX 5080): 3 piece(s), 78 s of audio in 7.2 s (+3 s
    loading the model); words timed by forced alignment on the GPU* and *large-v3-turbo on the GPU (NVIDIA GeForce
    RTX 5080): 1 piece(s), 18 s of audio in 0.6 s*.
  - A model that fails on the GPU runs on the CPU, and the summary says so.
  - Runs whose transcripts come from the cache now say so in the summary too: *large-v3: 9 piece(s), 175 s of
    audio, from the cache*.
- **The OCR stays on the CPU.** RapidOCR / onnxruntime reads only the caption band, and that stage takes seconds.
  The GPU build of onnxruntime would mean a second CUDA stack and would not change what is read.

**Models compared** (`python -m match_cuts.asr_bench`, word errors against your three SRTs, the idle GPU;
`reports/task-4/asr_bench.md`):

| model | word errors | deadpool / spider-man / zendaya-age | word starts: own → aligned | speed |
|---|---|---|---|---|
| **Whisper large-v3 (new default)** | **4.2 %** | 1.6 / 4.1 / 12.8 % | 87 → 40 ms | 8× real time |
| Whisper large-v3-turbo (second model) | 6.5 % | 8.9 / 2.7 / 12.8 % | 43 → 39 ms | 27× |
| Whisper medium.en | 7.1 % | 7.3 / 4.8 / 15.4 % | 43 → 35 ms | 9× |
| Whisper small.en (old default, now the fallback) | 8.1 % | 6.5 / 6.8 / 17.9 % | 40 → 36 ms | 13× |
| NVIDIA Parakeet TDT 0.6B v2 | 7.4 % | 7.3 / 4.8 / 17.9 % | 50 → 34 ms | 214× |
| NVIDIA Parakeet TDT 0.6B v3 | 8.1 % | 6.5 / 4.1 / 28.2 % | 62 → 34 ms | 20× |
| NVIDIA Canary-Qwen 2.5B | 8.7 % | 4.0 / 9.5 / 20.5 % | untimed → 37 ms | 1× |
| Cohere Transcribe | not run | gated on Hugging Face (needs your login, see below) | | |

- **Word starts** is the average distance from each word that opens one of your captions to that caption's start.
- **The speeds come from three short clips** (18–39 s), so they include each model's per-call overhead.

**Word timing.** Forced alignment (torchaudio's MMS_FA wav2vec2 model, 20 ms steps) now times every word, and a word
after a pause is moved onto its first sound.

- It brings large-v3's word starts from 87 ms to 40 ms off your caption starts on average. 27 % of them land within
  1 frame of your start and 46 % within 2.
- **Not every word is within one frame of yours.** Your caption starts are your own placement, not the moment the
  sound begins, so "within a frame" cannot be measured exactly against them. Captions are timed to land within 2
  frames.
- The English wav2vec2 aligner was no better than MMS_FA.
- New: a word whose alignment fails keeps its own time but is put back in spoken order. Before, a "the" could land
  after the "X-Force" it comes before.

**Hints.** `caption_allowlist.txt` (and `caption_glossary.txt` once Task 6 writes it) goes to Whisper as `hotwords`.

- On these three videos it costs 0.3 points (4.5 % against 4.2 %), because none of them says an allowlist word.
- It stays on as you asked; it is what makes a name in the list come out right.

**Where the two best models disagree** (`caption_recheck.resolve_two`). Large-v3 and turbo transcribe the same
audio, and each difference is decided on its own:

1. The same words written two ways: the reduced form wins ("gonna").
2. Otherwise the competitor's caption read clearly there decides, when it agrees with exactly one version in context.
3. Otherwise large-v3's version stays, marked unsure, and the RAW recheck decides.
4. Anything still unclear is listed in the report under *Captions worth a look*.

Also new in this step:
- A stock phrase only one model heard is dropped. These are Whisper's known inventions over laughter or music, such
  as large-v3's "THANKS FOR JOINING US" over Deadpool's laughter.
- The recheck no longer adds a word twice when its window edge falls between the two transcripts' timings of one
  word (Deadpool's "And And").

**What the screen says** (new, `screen_readings`). Where a caption read clearly on screen has other words than the
models heard:
- Both readings of the phrase, with the same three words heard on each side, are scored by both models. The score is
  each text's likelihood for that audio, teacher-forced.
- The audio is the RAW where the edit plays the phrase in order. The edit's audio is used otherwise.
- The screen's version is taken only when both models prefer it.

It fixed Spider-Man's "So as a joke" (both models heard "There was a joke") and "the school" (heard "a school"). It
rejected every misread screen ("his is going", "AndIwas", "An(25am", "him" for "I was like"). Offline it agreed
with your key 14 times out of 18 for one model alone. Requiring both models removed every wrong change.

Per video (final runs):

| video | model differences | decided by the screen | open, to the recheck | made-up phrase dropped | screen readings taken | rechecked / changed / still unclear |
|---|---|---|---|---|---|---|
| deadpool | 3 | 1 | 1 | 1 | 0 of 7 | 24 / 0 / 0 |
| spiderman-school | 0 | 0 | 0 | 0 | 2 of 6 | 23 / 1 / 0 |
| zendaya | 5 | 0 | 3 | 0 | 1 of 4 | 22 / 1 / 1 ("But", 14.0 s) |
| zendaya-age | 4 | 0 | 4 | 0 | 0 of 5 | 18 / 2 / 1 (16.1 s) |

### Caption quality (`caption_style.py`, `captions.py`, `caption_rules.py`)

- **Your style, learned.** `caption_style.json` holds the break model and caption lengths learned from the 12 SRTs
  that are not answer keys (436 captions). The three answer-key SRTs are held out, so check-all scores videos the
  model has never seen. Rebuild it with `python -m match_cuts.caption_style`.
- **Three ways to caption:**
  - **Follow** (the competitor already captions your way; Deadpool): its breaks and timing, with the words heard.
    Where it changes captions in the middle of a phrase, the words go where the two captions' text says.
  - **Regroup** (capitals or one word at a time; Spider-Man, Zendaya-age): the words heard, grouped by the learned
    model with dynamic programming. A caption starts on the frame its first word begins in, or on the cut when that
    word comes at most 0.5 s after it. Where the competitor's caption changes between two words, a break is likelier
    (bonus 1.0 in log-odds). On the answer keys you break there 44 % of the time and inside one of its captions 26 %.
    This helped Spider-Man across a range of strengths (0.5–1.25) and changed nothing on the others.
  - **Voice** (no burned-in captions): the same grouping as regroup.
- **Your style in numbers.** Final output against your SRTs (204 spoken captions in four videos):

  | | your SRTs | final output |
  |---|---|---|
  | words per caption: 1 / 2 / 3 / 4 | 24 / 41 / 27 / 8 % | 33 / 43 / 22 / 2 % |
  | median characters | 10 | 9 |
  | 90 % at or under | 16 characters | 13 characters |
  | maximum characters | 20 | 20 |
  | median time on screen | 0.53 s | 0.42 s |
  | start lower case | 60 % | 73 % |
  | full stops or commas | 0 | 0 |

  The output runs shorter mostly because a caption never crosses a video cut, and these edits cut every second or so.
- **Back to back in every mode,** competitor mode included. A pause over 1 s gets `*...*`. Another video's stretch
  is the only gap. A last pass makes sure no caption overlaps the next; before, a `*Laughter*` could run over a word
  captioned later.
- **Smaller fixes:** "They're" was title-cased "They'Re". A competitor capital that ends up mid-caption, because a
  heard word was put before it, now follows the heard case ("And they're like", not "And They're like").

### check-all and the caption score (`check_all.py`, `caption_score.py`, `testcases.py`, `prproj.py`)

- **`python -m match_cuts check-all`** runs every case in `tests/real/` as its own run and prints the scorecard.
  - Options: `--cases`, and `--rescore` to score the newest runs again without running them.
  - Output: `work/check-all/scorecard.json`, plus `history.jsonl` for the change since the last run.
- **The end summary of a normal run** has a *Caption score* line when the competitor is one of the answer-key
  videos.
- **The answer keys** are `tests/real/<case>/answer.srt` plus your edit's timeline:
  - deadpool: the competitor's own edit, because your SRT is timed on it (`case.json`).
  - spiderman-school: the A1 clips of your `reference/dsafxcv.prproj`, written out as `answer_edit.json` (the
    project itself is not committed).
  - zendaya-age: your `my_fixed_edit.xml`.
- **New test case:** `tests/real/zendaya-age/` (the run 011 video).
- **Smaller RAW copy:** Spider-Man's RAW is a 73 MB copy made with `testcases.small_copy`. It has the same 1280×720
  size and the same 59.94 fps, and audio copied as is, because the original is over 100 MB.

### Cut-side changes made along the way

These changes are what made the two failed person checks pass. In the final runs every hard check passes on all
four videos.

- **Person checks (S08, S14):** the framing plan's "who is speaking" spans now come from each clip's own edges,
  stage by stage, and there is a person pass after clips are merged. The XML person check judges each piece on the
  smallest planned span that holds it.
- **Speech map:** the gap between two words is the quietest 20 ms where they meet. A word is kept whole once a
  quarter of it plays (`KEEP_FRAC`); before, the "nearer end" rule could drop all of "Holland".
- **OCR:** the caption band of Zendaya-age is found again; a bogus static-text zone had masked it.

## Tests

- Unit suite (`-m "not slow"`): **1006 passed, 12 skipped, 0 failed** (6 min 25 s; the same platform-only skips as before).
  - An earlier full run had two failures:
    - `test_encoding` was real: two new `subprocess.run(text=True)` calls (check-all's git call, the test cases' ffprobe call) decoded with cp1252. Both now name UTF-8.
    - `test_probe`'s MKV duration check failed only while I ran other heavy jobs alongside. It passes alone and in the rerun.
- Slow suite (`-m slow --runslow`): **21 passed, 63 skipped, 0 failed** (10 min 35 s; the same opt-in film24 / Linux-ffmpeg skips as before).
  - An earlier run failed 3 end-to-end tests, all on one difference: `/provenance/code_hash`. I had edited code between a run and its determinism re-check. On unchanged code all pass.
- check-all, final: every hard check passes on all four videos (16m01s), see the table at the top.

New test files: `test_asr.py`, `test_caption_score.py`, `test_caption_style.py`, `test_prproj.py`,
`test_testcases.py`. New tests cover the two-model decision, the screen judge, the hallucination guard, the RAW
window rules, spoken order after alignment, the window-edge duplicate, no overlaps, contractions in title case,
competitor breaks, and the cached-transcript summary line.

## Remaining differences, by type (final runs)

| video | split | timing | words | casing |
|---|---|---|---|---|
| deadpool | 5 | 3 | 3 | 2 |
| spiderman-school | 34 | 9 | 7 | – |
| zendaya-age | 8 | 3 | 5 | – |

- **Split: my grouping is not yours.**
  - Spider-Man: you keep "I" | "suggested" apart, and I group "I suggested". You write "go to" | "a high school";
    I write "I should go" | "to a high".
  - Zendaya-age: you write "Name" | "surname", and I write "Name surname".
  - Deadpool: "This this is gonna". A 1-frame flicker caption of the competitor's sits between "to get out of" and
    "This is going", so "this." goes with the wrong one.
- **Timing: the same text, more than 2 frames off.**
  - Mostly 3–6 frames either way. There is no bias, so a constant offset would not help.
  - The big ones come from a cut at a different place, such as Zendaya-age "Tom Holland" at −36 frames.
- **Words:**
  - Edit content my edit plays and yours does not: Spider-Man "genius kids yeah", Zendaya-age "Same with you",
    "Family".
  - Both models miss a word: "right" in Spider-Man. Neither model finds it in the audio, even scored against the
    screen.
  - Both models mishear a phrase: "Eventually" for "and then" (large-v3's confidence is 0.01).
  - You wrote "wanna" where the speech models and the competitor all have "want to".
- **Casing:** you write "yes! Avengers!" and "And they're" with their capitals; I write "yes Avengers!" and "and
  they're".

**The biggest limit now is the edit, not the captions.** My edits are longer than yours:

| video | mine | yours | your cut points I match within 2 frames |
|---|---|---|---|
| Spider-Man | 38.6 s | 32.4 s | 7 of 48 |
| Zendaya-age | 17.7 s | 12.9 s | 4 of 22 |
| Deadpool | 26.7 s | 23.3 s | 11 of 54 |

That is Task 5's ground.

## Things to check in Premiere

- **Spider-Man** (`work/check-all/runs/spiderman-school/015`): check "So as | a joke" at the start and "the school".
  Both are taken from the screen against what the models heard.
- **Deadpool:** check the end (24–27 s). "THANKS FOR JOINING US" is gone; `*Laughter*` should end where the last
  caption starts.
- **Every caption track:** no gaps except another video's stretch, and no caption longer than 20 characters.

## Decisions

- **Default model: large-v3.** It has the lowest word errors on all three keys together. Turbo is the second
  opinion, because it is fast and errs differently. small.en, the old default, is the fallback.
- **Cohere Transcribe was not run.** It is gated on Hugging Face and needs your account to accept its terms:
  `hf auth login`, then `python -m match_cuts.asr_bench --engines cohere-transcribe`.
- **NeMo 3.0.0** (for Parakeet / Canary-Qwen) was installed into the project's `.venv` with a constraints file, so
  that it could not touch torch, CTranslate2, faster-whisper or numpy. It downgraded `packaging` 26.3 → 24.2,
  `omegaconf` 2.3.1 → 2.3.0 and `fsspec` 2026.9.0 → 2025.12.0, and nothing in the tool minds.
- **The screen judge needs both models.** With one model it made wrong changes; with both, none.
- **Things I tried and dropped because the harness showed they did not help:**
  - re-aligning every word after the second model;
  - keep-together hints from the competitor's captions;
  - a comma bonus (you do break more often after a comma, 64 % against 40 %, but it changed no score);
  - narrowing the screen judge's context;
  - following Zendaya-age's competitor (−2 exact captions).
- **A removed pause does not stop the screen judge from using the RAW.** Its loudest 50 ms must be at least 20 dB
  under the words. It changed nothing on these videos, but it is the right rule.
- **`reference/` and `input/` are not committed.** Spider-Man's answer timeline is derived data from your project's
  A1.
