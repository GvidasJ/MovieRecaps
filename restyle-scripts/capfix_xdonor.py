#!/usr/bin/env python3
import re, base64, struct, hashlib, sys
DSRC, DTG, DIDX, TSRC, DST, TTG, TIDX = (sys.argv[1], sys.argv[2], int(sys.argv[3]),
                                          sys.argv[4], sys.argv[5], sys.argv[6], int(sys.argv[7]))
dxml = open(DSRC).read()
xml  = open(TSRC).read()
def _mk(doc):
    def f(oid):
        for pat in (rf'\n\t<(\w+) ObjectID="{oid}"[^>]*>.*?</\1>',
                    rf'\n\t\t<(\w+) ObjectID="{oid}"[^>]*>.*?</\1>'):
            m = re.search(pat, doc, re.S)
            if m: return m.start()+1, m.end(), m.group(0)[1:]
        raise KeyError(oid)
    return f
dspan = _mk(dxml)
obj_span = _mk(xml)
dtg = re.search(rf'<VideoTrackGroup ObjectID="{DTG}".*?</VideoTrackGroup>', dxml, re.S).group(0)
dtracks = dict((int(i),u) for i,u in re.findall(r'<Track Index="(\d+)" ObjectURef="([^"]+)"/>', dtg))
donor_item = re.findall(r'<TrackItem Index="\d+" ObjectRef="(\d+)"/>',
    re.search(rf'<VideoClipTrack ObjectUID="{dtracks[DIDX]}".*?</VideoClipTrack>', dxml, re.S).group(0))[0]
ttg = re.search(rf'<VideoTrackGroup ObjectID="{TTG}".*?</VideoTrackGroup>', xml, re.S).group(0)
ttracks = dict((int(i),u) for i,u in re.findall(r'<Track Index="(\d+)" ObjectURef="([^"]+)"/>', ttg))
titems = re.findall(r'<TrackItem Index="\d+" ObjectRef="(\d+)"/>',
    re.search(rf'<VideoClipTrack ObjectUID="{ttracks[TIDX]}".*?</VideoClipTrack>', xml, re.S).group(0))
print("donor:", donor_item, "targets:", len(titems))
def _inp(span, it):
    ti = span(it)[2]
    sc = span(re.search(r'<SubClip ObjectRef="(\d+)"/>', ti).group(1))[2]
    cl = re.search(r'<Clip ObjectRef="(\d+)"/>', sc).group(1)
    return int(re.search(r'<InPoint>(-?\d+)</InPoint>', span(cl)[2]).group(1))
def inpoint(it): return _inp(obj_span, it)
DONOR_IP = _inp(dspan, donor_item)
_,_,dti = dspan(donor_item)
_,_,dch = dspan(re.search(r'<Components ObjectRef="(\d+)"/>', dti).group(1))
d_mo, d_vo, d_to = re.findall(r'<Component Index="\d+" ObjectRef="(\d+)"/>', dch)
_,_,d_text = dspan(d_to)
d_tparams = dict(re.findall(r'<Param Index="(\d+)" ObjectRef="(\d+)"/>', d_text))
_,_,d_motion = dspan(d_mo); _,_,d_vector = dspan(d_vo)
assert 'AE.ADBE Motion' in d_motion and 'AE.ADBE Graphic Group' in d_vector and 'AE.ADBE Text' in d_text
d_mprefs = re.findall(r'<Param Index="\d+" ObjectRef="(\d+)"/>', d_motion)
d_vprefs = re.findall(r'<Param Index="\d+" ObjectRef="(\d+)"/>', d_vector)
_dkf = re.search(r'<Keyframes>([^<]+)</Keyframes>', dspan(d_mprefs[1])[2])
DONOR_ANCHOR = int(_dkf.group(1).split(',')[0]) if _dkf else DONOR_IP
if DONOR_ANCHOR != DONOR_IP:
    print("note: donor keyframes sit %+d ticks from its in-point; anchoring on the keyframes" % (DONOR_ANCHOR-DONOR_IP))
def _hashes(doc):
    h={}
    for m in re.finditer(r'BinaryHash="([0-9a-f-]+)">([^<]+)</StartKeyframeValue>', doc):
        h.setdefault(m.group(1), ''.join(m.group(2).split()))
    return h
hash_data = _hashes(xml); dhash = _hashes(dxml)
hash_pd = {}
for m in re.finditer(r'<PremiereFilterPrivateData Encoding="base64" BinaryHash="([0-9a-f-]+)">([^<]+)</PremiereFilterPrivateData>', dxml):
    hash_pd.setdefault(m.group(1), ''.join(m.group(2).split()))
def split_tail(raw):
    end=len(raw)
    while end>0 and raw[end-1]==0: end-=1
    for s in range(end-1,3,-1):
        ln=struct.unpack('<I',raw[s-4:s])[0]
        if ln==end-s and 0<ln<500: return raw[:s-4], raw[s:end]
    raise ValueError("tail")
def blob_of(pxml, hmap=None):
    hmap = hash_data if hmap is None else hmap
    b = re.search(r'BinaryHash="([0-9a-f-]+)"(?:/>|>([^<]+)</StartKeyframeValue>)', pxml)
    return base64.b64decode(''.join((b.group(2) or hmap[b.group(1)]).split()))
donor_blob = blob_of(dspan(d_tparams['0'])[2], dhash)
donor_head, donor_txt = split_tail(donor_blob)
L=len(donor_txt); pad=4*((L+1+3)//4)-L
assert donor_head + struct.pack('<I',L) + donor_txt + b'\x00'*pad == donor_blob
print("donor round-trip OK:", donor_txt)
def build_blob(t):
    L=len(t); pad=4*((L+1+3)//4)-L
    b=bytearray(donor_head+struct.pack('<I',L)+t+b'\x00'*pad)
    b[0:4]=struct.pack('<I',len(b)-12); return bytes(b)
def make_hash(b):
    h=hashlib.md5(b).hexdigest()[:24]
    return f'{h[:8]}-{h[8:12]}-{h[12:16]}-{h[16:20]}-{h[20:24]}'+'%08x'%(len(b)+12)
def retime(cxml, delta):
    def fix(m):
        out=[]
        for r in [r for r in m.group(1).split(';') if r.strip()]:
            f=r.split(','); f[0]=str(int(f[0])+delta); out.append(','.join(f))
        return '<Keyframes>'+';'.join(out)+';</Keyframes>'
    return re.sub(r'<Keyframes>([^<]+)</Keyframes>', fix, cxml)
def inline_pd(cxml):
    def fix(m):
        v = hash_pd.get(m.group(1))
        return (f'<PremiereFilterPrivateData Encoding="base64" BinaryHash="{m.group(1)}">{v}\n\t\t</PremiereFilterPrivateData>' if v is not None else m.group(0))
    return re.sub(r'<PremiereFilterPrivateData Encoding="base64" BinaryHash="([0-9a-f-]+)"\s*/>', fix, cxml)
motion_tpl = inline_pd(d_motion); vector_tpl = inline_pd(d_vector)
motion_ptpl = [dspan(p)[2] for p in d_mprefs]
vector_ptpl = [dspan(p)[2] for p in d_vprefs]
next_id = max(int(i) for i in re.findall(r'ObjectID="(\d+)"', xml)) + 1
STYLE_UREF = re.search(r'<StyleProjectItem ObjectUID="([^"]+)"', xml).group(1)
def clone(tpl, old, new, pmap=None):
    y = tpl.replace(f'ObjectID="{old}"', f'ObjectID="{new}"', 1)
    if pmap:
        for o,n in pmap.items(): y = y.replace(f'ObjectRef="{o}"', f'ObjectRef="{n}"', 1)
    return y
def clean(s): return s.replace('.','').replace(',','').strip()
repl=[]; new_objects=[]; cleaned=[]
for it in titems:
    delta = inpoint(it) - DONOR_ANCHOR
    ch_oid = re.search(r'<Components ObjectRef="(\d+)"/>', obj_span(it)[2]).group(1)
    cs, ce, cxml = obj_span(ch_oid)
    block = re.search(r'<Components Version="1">.*?</Components>', cxml, re.S).group(0)
    refs = re.findall(r'<Component Index="\d+" ObjectRef="(\d+)"/>', block)
    assert len(refs)==1, (it, refs)
    text_oid = refs[0]
    m_oid = next_id; next_id += 1
    m_map = {p: str(next_id+k) for k,p in enumerate(d_mprefs)}; next_id += len(d_mprefs)
    new_objects.append(retime(clone(motion_tpl, d_mo, m_oid, m_map), delta))
    for p,tpl in zip(d_mprefs, motion_ptpl): new_objects.append(retime(clone(tpl, p, m_map[p]), delta))
    v_oid = next_id; next_id += 1
    v_map = {p: str(next_id+k) for k,p in enumerate(d_vprefs)}; next_id += len(d_vprefs)
    new_objects.append(retime(clone(vector_tpl, d_vo, v_oid, v_map), delta))
    for p,tpl in zip(d_vprefs, vector_ptpl): new_objects.append(retime(clone(tpl, p, v_map[p]), delta))
    nb = ('<Components Version="1">\n'
          f'\t\t\t\t<Component Index="0" ObjectRef="{m_oid}"/>\n'
          f'\t\t\t\t<Component Index="1" ObjectRef="{v_oid}"/>\n'
          f'\t\t\t\t<Component Index="2" ObjectRef="{text_oid}"/>\n'
          '\t\t\t</Components>')
    repl.append((cs, ce, cxml.replace(block, nb, 1)))
    ts, te, txml = obj_span(text_oid)
    params = dict(re.findall(r'<Param Index="(\d+)" ObjectRef="(\d+)"/>', txml))
    assert len(params)==len(d_tparams)
    n = txml if 'ParentStyle' in txml else txml.replace('<ArchivedType>0</ArchivedType>',
        f'<ArchivedType>0</ArchivedType>\n\t\t\t<ParentStyle ObjectURef="{STYLE_UREF}"/>', 1)
    repl.append((ts, te, n))
    ps, pe, pxml = obj_span(params['0'])
    raw = blob_of(pxml)
    assert struct.unpack('<I',raw[:4])[0]==len(raw)-12
    _, t = split_tail(raw)
    s = t.decode('utf-8'); c = clean(s)
    if c != s: cleaned.append((s,c))
    nb2 = build_blob(c.encode('utf-8'))
    nv = f'<StartKeyframeValue Encoding="base64" BinaryHash="{make_hash(nb2)}">{base64.b64encode(nb2).decode()}\n\t\t</StartKeyframeValue>'
    np = re.sub(r'<StartKeyframeValue Encoding="base64" BinaryHash="[0-9a-f-]+"(?:/>|>[^<]*</StartKeyframeValue>)', nv, pxml, count=1)
    assert np != pxml
    repl.append((ps, pe, np))
    for i in range(1, len(d_tparams)):
        tps, tpe, tpxml = obj_span(params[str(i)])
        dref = d_tparams[str(i)]
        nx = dspan(dref)[2].replace(f'ObjectID="{dref}"', f'ObjectID="{params[str(i)]}"', 1)
        if nx != tpxml: repl.append((tps, tpe, nx))
repl.sort(key=lambda r: r[0])
for a,b in zip(repl,repl[1:]): assert a[1] <= b[0], ("overlap", a[:2], b[:2])
out,pos=[],0
for s_,e_,t_ in repl: out.append(xml[pos:s_]); out.append(t_); pos=e_
out.append(xml[pos:])
doc = ''.join(out).replace('</PremiereData>', ''.join('\t'+o.lstrip('\t')+'\n' for o in new_objects) + '</PremiereData>', 1)
open(DST,'w').write(doc)
print("edits:", len(repl), "new objects:", len(new_objects))
print(f"{len(cleaned)} cleaned:")
for a,b in cleaned: print(f'  "{a}" -> "{b}"')
