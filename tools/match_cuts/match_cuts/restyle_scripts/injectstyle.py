#!/usr/bin/env python3
"""Copy the POPW StyleProjectItem (and the text component behind it) from a
donor project into a target project that has no style item at all, and
register it in the target's project panel.

Usage: injectstyle.py <donor.xml> <target.xml> <out.xml>
"""
import re, sys

DSRC, TSRC, OUT = sys.argv[1], sys.argv[2], sys.argv[3]
dxml = open(DSRC, encoding='utf-8', newline='').read()
xml  = open(TSRC, encoding='utf-8', newline='').read()

if '<StyleProjectItem' in xml:
    print("target already has a style item - nothing to do")
    open(OUT, 'w', encoding='utf-8', newline='').write(xml)
    raise SystemExit

def dspan(oid):
    m = re.search(rf'\n\t<(\w+) ObjectID="{oid}"[^>]*>.*?</\1>', dxml, re.S)
    return m.group(0)[1:]

# the style item and the text component it points at
sm = re.search(r'\n\t<StyleProjectItem ObjectUID="([^"]+)".*?</StyleProjectItem>', dxml, re.S)
style_block, STYLE_UID = sm.group(0)[1:], sm.group(1)
comp_oid = re.search(r'<Component ObjectRef="(\d+)"/>', style_block).group(1)
comp_block = dspan(comp_oid)
param_oids = re.findall(r'<Param Index="\d+" ObjectRef="(\d+)"/>', comp_block)
print(f"donor style '{re.search(r'<Name>([^<]*)</Name>', style_block).group(1)}'"
      f"  uid {STYLE_UID[:8]}  component {comp_oid} with {len(param_oids)} params")

# fresh ids in the target
next_id = max(int(i) for i in re.findall(r'ObjectID="(\d+)"', xml)) + 1
new_comp = str(next_id); next_id += 1
pmap = {p: str(next_id + k) for k, p in enumerate(param_oids)}
next_id += len(param_oids)

new_objects = []
c = comp_block.replace(f'ObjectID="{comp_oid}"', f'ObjectID="{new_comp}"', 1)
for o, n in pmap.items():
    c = c.replace(f'ObjectRef="{o}"', f'ObjectRef="{n}"', 1)
new_objects.append(c)
for p in param_oids:
    new_objects.append(dspan(p).replace(f'ObjectID="{p}"', f'ObjectID="{pmap[p]}"', 1))
new_objects.append(style_block.replace(f'<Component ObjectRef="{comp_oid}"/>',
                                       f'<Component ObjectRef="{new_comp}"/>', 1))

doc = xml.replace('</PremiereData>',
                  ''.join('\t' + o.lstrip('\t') + '\n' for o in new_objects) + '</PremiereData>', 1)

# register it in the project panel so Premiere shows the style
rm = re.search(r'(\n\t<RootProjectItem[^>]*>.*?)(\s*</Items>)', doc, re.S)
items = re.findall(r'<Item Index="(\d+)" ObjectURef="[^"]+"/>', rm.group(1))
nxt = max(int(i) for i in items) + 1 if items else 0
indent = re.search(r'\n(\s*)<Item Index="0"', rm.group(1))
pad = indent.group(1) if indent else '\t\t\t\t'
doc = doc[:rm.start(2)] + f'\n{pad}<Item Index="{nxt}" ObjectURef="{STYLE_UID}"/>' + doc[rm.start(2):]

open(OUT, 'w', encoding='utf-8', newline='').write(doc)
print(f"injected: component {new_comp}, params {pmap[param_oids[0]]}-{pmap[param_oids[-1]]},"
      f" registered as project item {nxt}")
