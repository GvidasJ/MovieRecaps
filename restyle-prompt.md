# Claude Code prompt — restyle Premiere captions automatically

Paste everything below the line into Claude Code, and put the four scripts
from `restyle-scripts/` in the repo first. They are **working code** that
already does this job — the task is to wire them into the tool, not to
reimplement them.

---

My tool generates captions as an SRT and I import them into Premiere as a
caption track, then convert to graphics. That leaves 60-odd plain text
clips with no styling. Every time, I've been handing the `.prproj` to
someone who restyles them. I want that step automated inside the tool.

## What "restyled" means

Each plain caption clip becomes a styled one: Verdana Bold 58 white with
two strokes and a drop shadow, positioned at the same spot as every other
caption, with a scale pop animation that fires exactly on the clip's start.
Dots and commas stripped from the text.

## Start from the working scripts

`restyle-scripts/` contains four scripts that already do all of this
correctly. Read them first — they encode a lot of trial and error that is
not obvious from the file format.

| script | what it does |
|---|---|
| `capfix.py` | the main job: clone style + animation from a donor caption in the same project onto every plain caption on a target track |
| `capfix_xdonor.py` | same, but the donor comes from a **different** project file — for projects with no styled caption yet |
| `injectstyle.py` | copies the POPW style item into a project that has none, and registers it in the project panel |
| `capverify.py` | checks the result and exits non-zero with a list of problems |

CLI shape:

```
capfix.py          <in.xml> <out.xml> <trackgroup> <donor_track_idx0> <target_track_idx0>
capfix_xdonor.py   <donor.xml> <donor_tg> <donor_idx0> <in.xml> <out.xml> <tg> <target_idx0>
injectstyle.py     <donor.xml> <in.xml> <out.xml>
capverify.py       <orig.xml> <out.xml> <tg> <donor_idx0> <target_idx0>
```

Track indices are **0-based**, so V4 is index 3.

## What I want you to build

A single entry point — `restyle(prproj_path) -> prproj_path` — that:

1. Unpacks: `.prproj` is gzipped XML. `zcat in.prproj > work.xml`, and
   `gzip -c work.xml > out.prproj` to repack.
2. Scans every video track and finds, for each, how many caption clips are
   **plain** (1 component) versus **styled** (3 components).
3. Picks the target track: the one with the most plain captions.
4. Picks the donor:
   - a styled caption elsewhere in the same project → `capfix.py`
   - none in this project → `injectstyle.py` first if there's no
     `StyleProjectItem`, then `capfix_xdonor.py` against a reference
     project kept in the repo
5. Runs `capverify.py` and **fails loudly** if it reports anything.
6. Repacks and returns the path.

Keep a known-good reference project in the repo (I'll supply one) as the
cross-file donor, since my generated projects have no style in them at all.

## Things that will bite you

These all caused real bugs. Do not "simplify" them away.

**Anchor lookups at exactly one tab.** Top-level objects are written as
`\n\t<Tag ObjectID="N"`. Premiere reuses low ObjectIDs in nested project
metadata, so a loose regex finds the wrong object. Every lookup must
require the single tab.

**Retime on the donor's keyframe start, not its in-point.** If the donor
clip was trimmed those differ — in one project by 4 frames — and every pop
lands early, so captions appear already full size with no animation. The
scripts detect and report this.

**Never force a long caption's Position back to match the others.** Each
caption sits in a text box. Normal ones are 706px wide at x=187, centre
187 + 706/2 = 540 = frame centre. A long line gets a wider box and a
different Position so it stays centred. Judge by where the centre lands:

```python
box_w  = struct.unpack_from('<f', head, 212)[0]
centre = pos_x*1080 - anchor_x*box_w + box_w/2     # want ~540
```

I lost two captions to this — "fixing" their Position pushed them 128px and
175px off-centre.

**Protect numbers when stripping dots and commas.** `£4.50` must not become
`£450`; `15,000` must not become `15000`. Strip only sentence punctuation
and abbreviations (`Mr.` → `Mr`, `C.I.D.` → `CID`, `6 a.m.` → `6 am`).

**Normalise indentation on appended objects** (`'\t' + obj.lstrip('\t')`),
or it compounds across runs and later lookups fail.

## The style, for reference

| | |
|---|---|
| Font | Verdana Bold 58, centred, tracking 25 |
| Fill | white |
| Stroke 1 | white 3.0 Center |
| Stroke 2 | black 4.0 Outer |
| Shadow | black 100%, 135°, distance 6, size 4, blur 15 |
| Position | 187, 1124 px in a 1080×1920 frame |
| Motion Scale | **88% → 100% over exactly 4 frames** (0.1333 s at 30 fps) |
| Vector Motion Scale | 88% constant |
| Easing | speed 90, influence 16.667 in / 33.333 out |
| Style item | named **POPW**, uid `d6ca2753-d18f-4ccf-8d37-8af5fe27e955` |

Premiere time base: **254016000000 ticks per second**, so the 4-frame pop
is a gap of **33868800000** ticks between the two keyframes.

## Verification is not optional

`capverify.py` checks: XML parses, three components per clip in the right
order, pop starts exactly on the clip's in-point and at 88%, text blob
matches the donor's, blob size prefix correct, ParentStyle present, font
size 58, caption centred within 60px of frame centre, no dots or commas
left, no orphaned BinaryHash refs, no duplicated ObjectIDs.

Run it after every restyle. If it fails, surface the message and **do not
write the output file** — a silently broken project is far worse than an
error, because I won't notice until I'm scrubbing the timeline.

## Also worth doing

After restyling, print a short report: how many captions were styled, what
punctuation was cleaned, and anything suspicious in the text (doubled
words, a caption ending on a weak word like "a" or "the", a caption over 24
characters). **Report, don't fix** — some of my sketches turn on deliberate
misspellings and I'd rather check myself.
