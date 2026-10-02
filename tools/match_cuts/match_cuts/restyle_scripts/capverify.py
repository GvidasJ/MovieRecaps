#!/usr/bin/env python3
"""Verify a restyled project. Exits non-zero and prints every failure.

Usage: capverify.py <original.xml> <restyled.xml> <trackgroup> <donor_idx0> <target_idx0>
"""
import re, base64, struct, sys
import xml.etree.ElementTree as ET
from collections import Counter

ORIG, NEW, TG, DIDX, TIDX = sys.argv[1], sys.argv[2], sys.argv[3], int(sys.argv[4]), int(sys.argv[5])
doc = open(NEW, encoding='utf-8', newline='').read()
fails = []

try:
    ET.fromstring(doc); print("XML parses OK")
except Exception as e:
    print("FAIL: XML does not parse:", e); sys.exit(1)

def obj(oid):
    m = re.search(rf'\n\t<(\w+) ObjectID="{oid}"[^>]*>.*?</\1>', doc, re.S)
    return m.group(0) if m else None

hd = {}
for m in re.finditer(r'BinaryHash="([0-9a-f-]+)">([^<]+)</StartKeyframeValue>', doc):
    hd.setdefault(m.group(1), ''.join(m.group(2).split()))

def split_tail(raw):
    end = len(raw)
    while end > 0 and raw[end-1] == 0: end -= 1
    for s in range(end-1, 3, -1):
        ln = struct.unpack('<I', raw[s-4:s])[0]
        if ln == end-s and 0 < ln < 500: return raw[:s-4], raw[s:end]
    return None, None

def inpoint(it):
    sc = obj(re.search(r'<SubClip ObjectRef="(\d+)"/>', obj(it)).group(1))
    cl = re.search(r'<Clip ObjectRef="(\d+)"/>', sc).group(1)
    return int(re.search(r'<InPoint>(-?\d+)</InPoint>', obj(cl)).group(1))

tg = re.search(rf'<VideoTrackGroup ObjectID="{TG}".*?</VideoTrackGroup>', doc, re.S).group(0)
tr = dict((int(i), u) for i, u in re.findall(r'<Track Index="(\d+)" ObjectURef="([^"]+)"/>', tg))

donor_head = None
checked = 0
for v in (DIDX, TIDX):
    track = re.search(rf'<VideoClipTrack ObjectUID="{tr[v]}".*?</VideoClipTrack>', doc, re.S)
    for it in re.findall(r'<TrackItem Index="\d+" ObjectRef="(\d+)"/>', track.group(0)):
        ti = obj(it)
        c = re.search(r'<Components ObjectRef="(\d+)"/>', ti)
        if not c: continue
        refs = re.findall(r'<Component Index="\d+" ObjectRef="(\d+)"/>', obj(c.group(1)))
        tos = [r for r in refs if obj(r) and 'AE.ADBE Text' in obj(r)]
        if not tos: continue          # a video clip on the same track, skip
        checked += 1

        pp = dict(re.findall(r'<Param Index="(\d+)" ObjectRef="(\d+)"/>', obj(tos[0])))
        b = re.search(r'BinaryHash="([0-9a-f-]+)"(?:/>|>([^<]+)<)', obj(pp['0']))
        raw = base64.b64decode(''.join((b.group(2) or hd[b.group(1)]).split()))
        head, t = split_tail(raw)
        txt = t.decode('utf-8', 'replace')

        if len(refs) != 3:
            fails.append(f"{txt!r}: {len(refs)} components, expected 3"); continue
        if 'AE.ADBE Motion' not in obj(refs[0]) or 'AE.ADBE Graphic Group' not in obj(refs[1]):
            fails.append(f"{txt!r}: component order wrong")
        if 'ParentStyle' not in obj(tos[0]):
            fails.append(f"{txt!r}: no ParentStyle link")
        if b'Verdana' not in raw:
            fails.append(f"{txt!r}: not Verdana")
        if struct.unpack('<I', raw[:4])[0] != len(raw) - 12:
            fails.append(f"{txt!r}: blob size prefix wrong")
        if donor_head is None: donor_head = head[4:]
        elif head[4:] != donor_head:
            fails.append(f"{txt!r}: style blob differs from donor")

        sp = obj(re.findall(r'<Param Index="\d+" ObjectRef="(\d+)"/>', obj(refs[0]))[1])
        kfm = re.search(r'<Keyframes>([^<]+)<', sp)
        if not kfm:
            fails.append(f"{txt!r}: no scale keyframes")
        else:
            kf = kfm.group(1)
            if int(kf.split(',')[0]) != inpoint(it):
                off = (int(kf.split(',')[0]) - inpoint(it)) / 254016000000
                fails.append(f"{txt!r}: pop starts {off:+.3f}s from clip start")
            if not kf.split(',')[1].startswith('88'):
                fails.append(f"{txt!r}: pop does not start at 88%")

        if re.search(r'(?<!\d)[.,]|[.,](?!\d)', txt):       # a dot or comma inside a number (4.50) is kept
            fails.append(f"{txt!r}: still contains . or ,")

        # centred on screen, allowing for a wider box on long lines
        px = float(re.search(r'<StartKeyframe>([^<]+)<', obj(pp['2'])).group(1).split(',')[1].split(':')[0]) * 1080
        ax = float(re.search(r'<StartKeyframe>([^<]+)<', obj(pp['8'])).group(1).split(',')[1].split(':')[0])
        bw = struct.unpack_from('<f', head, 212)[0]
        centre = px - ax * bw + bw / 2
        if abs(centre - 540) > 60:
            fails.append(f"{txt!r}: centre {centre:.0f}px, {centre-540:+.0f} off frame centre")
        size = struct.unpack_from('<f', head, 624)[0]
        if abs(size - 58.0) > 0.01:
            fails.append(f"{txt!r}: font size {size:.1f}, expected 58")

dh = set(m.group(1) for m in re.finditer(r'BinaryHash="([0-9a-f-]+)">[^<]+</StartKeyframeValue>', doc))
orph = [h for h in re.findall(r'<StartKeyframeValue Encoding="base64" BinaryHash="([0-9a-f-]+)"/>', doc) if h not in dh]
if orph: fails.append(f"{len(orph)} orphaned BinaryHash references")

oc = Counter(re.findall(r'ObjectID="(\d+)"', open(ORIG, encoding='utf-8', newline='').read()))
nc = Counter(re.findall(r'ObjectID="(\d+)"', doc))
dups = {k: v for k, v in nc.items() if v > max(1, oc.get(k, 0))}
if dups: fails.append(f"duplicate ObjectIDs introduced: {dups}")

print(f"checked {checked} captions")
if fails:
    print(f"\n{len(fails)} PROBLEM(S):")
    for f in fails: print("  -", f)
    sys.exit(1)
print("all checks passed")
