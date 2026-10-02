# Auto-captioning — prompt

Paste everything below the line into the new chat, and attach the ten SRT
files in `srt/` as style reference and test fixtures.

---

I'm adding automatic captions to a tool I already have. It takes a raw clip
plus a competitor's clip, works out the cuts, and renders an edit — that
part works. What I need now is for it to **write and time the captions
itself from the audio**, with no project file and no transcript from me.

I'm attaching ten SRTs from finished videos of mine. The output has to look
like those. They're also good regression fixtures: run the originals'
audio through the tool and the result should come out close to them.

## Pipeline order — gets this wrong and nothing lines up

Transcribe **the cut video, not the raw one.** If you caption the raw clip
and then apply cuts, every timestamp after the first cut is wrong. Do the
edit first, render or build the cut timeline, then transcribe that.

## Transcription

Use **word-level timestamps**, not segment-level. Whisper's default
segments are whole sentences and are useless here — my captions are 1–4
words long. Use `whisper_timestamps`, WhisperX, or faster-whisper with
alignment; WhisperX's forced alignment is the most accurate and that
matters because a caption is on screen for about half a second.

Keep each word's start and end. Everything below works on that word list.

## The caption style, measured from 388 captions in the attached files

| | |
|---|---|
| Words per caption | **1–4**, never more than 5 |
| Distribution | 2 words 39%, 3 words 28%, 1 word 23%, 4 words 9% |
| Characters | median **11**, 90th percentile 17, hard cap **24** |
| On screen | median **0.57 s** |
| Reading rate | about **18 characters per second** |
| Full stops and commas | **none at all** — zero in 388 captions |
| Question marks, apostrophes, asterisks | kept |
| Capitalisation | as spoken; 52% of captions start lower case. Do **not** capitalise every caption |
| Gaps between captions | **none — 100% are exactly back to back** |

## Timing rule

Every caption's end time is **the next caption's start time**. There is
always a caption on screen; nothing is blank between them.

So: group words into captions, take each caption's start from its first
word, then set `end[i] = start[i+1]`. Only the last caption uses its own
final word's end time. Do not leave the natural silence between words as a
gap.

## Grouping algorithm

Walk the word list and start a new caption when any of these is true:

1. The current caption already has **4 words** or would exceed **20
   characters**.
2. There's a pause of more than about **0.25 s** before the next word.
3. The speaker changes.
4. The next word is a short interjection that stands alone (see below).

Then apply the correction that matters most to me:

### Never end a caption on a weak word

A caption ending on an article, preposition, conjunction or auxiliary
leaves a fragment that means nothing by itself. This is the single thing I
most want fixed.

```
weak = {a, an, the, to, of, and, is, are, was, were, in, on, at, for,
        with, my, your, our, that, it, but, so, or, as, I}
```

After grouping, if a caption's last word is in that set **and** the caption
has more than one word, move that word to the front of the next caption.
Repeat until stable. If moving it would empty the caption, leave it.

```
bad                     good
"I bought a"      →     "I bought"
"these"                 "a beanie hat"

"there is a"      →     "there is"
"legendary sound"       "a legendary"
                        "sound mixer"
```

In my existing files 8% break this rule — those are the ones I want cleaned
up, so don't copy that 8%.

### Keep together, never split across captions

- A full name: `Peter Parker`, `Tom Holland`, `Central Park`
- A number and its unit: `£4.50`, `6 am`, `10 year old`, `50 quid`
- A negation and its verb: `don't move`, `didn't get it`, `won't`

### Interjections get their own caption

`oh`, `yeah`, `hey`, `no`, `whoa`, `okay`, `sorry`, `um`, `amm`, `ohhh`

### Repetition is deliberate

If someone says a word three times, that's three captions. Don't collapse
them — the repetition is usually the joke.

## Text cleanup

Strip full stops and commas, then trim whitespace. **But not inside
numbers.**

```
£4.50    stays £4.50     not £450
15,000   stays 15,000    not 15000
```

Strip dots only where they're sentence punctuation or an abbreviation:
`Mr.` → `Mr`, `C.I.D.` → `CID`, `6 a.m.` → `6 am`.

Also trim leading and trailing spaces — transcription often leaves them.

## Action captions

About 10% of my captions are stage directions in asterisks describing what
happens rather than what's said:

```
*shocked*   *walks away*   *confused*   *realises*   *can't believe it*
*returns to interviewer*   *autopilot activates*
```

**Don't invent these** — you can't tell from audio what's on screen, and a
wrong guess is worse than nothing. Instead: wherever there's a stretch of
**more than ~1 s with no speech**, emit a placeholder caption `*...*` at
that timing and list those timecodes in your report so I can fill them in.
They're the only captions allowed to run to the full 24 characters, and
they're present tense and lower case.

## Output

Standard SRT: sequential numbers, `HH:MM:SS,mmm --> HH:MM:SS,mmm`, one line
of text per caption, blank line between blocks, no extra trailing lines.

## Report back, don't silently fix

After generating, tell me:

- any caption that hit the 24-character cap
- the timecodes of the `*...*` placeholders
- anything that looks like a mis-transcription, a doubled word or a missing
  word

Flag these rather than correcting them. I'd rather check the audio myself —
and several of my sketches turn on deliberate misspellings (`spill chock`,
`quazoosl`, `compluter`), so an eager auto-correct would destroy the joke.
