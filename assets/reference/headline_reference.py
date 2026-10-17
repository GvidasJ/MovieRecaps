# Reference only (made in chat on 17 Oct): how the LouLaughs headline was matched to the PSD.
# Pillow, 2x supersampling: NeueMontreal-Bold at 123.25 px (4K canvas), stroke 1 px at 2x = faux bold,
# tracking 0.0325 em, emoji NotoColorEmoji ~128 px, text->emoji gap 44 px, emoji gap 29 px,
# line baselines 849 / 996 px (147 px apart), centre x 1082, NO shrinking up to a line width of 1840 px (your finished videos use up to ~1830 px) (all on the 2160x3840 PSD).
# Measured match against the PSD's own headline: 94 % pixel overlap on line 1.
from PIL import Image, ImageFont, ImageDraw
import numpy as np
F='nm_bold.otf'; SIZE=123.25; SW2=2; TR=0.0325; S=2
EMO=ImageFont.truetype('/usr/share/fonts/truetype/noto/NotoColorEmoji.ttf',109)
import unicodedata
def is_emoji(ch): return ord(ch)>=0x2190 and unicodedata.category(ch) in ('So','Sk','Cn') or 0x1F000<=ord(ch)<=0x1FAFF or ch in '‍️'
def emoji_img(seq,size):
    t=Image.new('RGBA',(200,160),(0,0,0,0)); ImageDraw.Draw(t).text((0,0),seq,font=EMO,embedded_color=True)
    t=t.crop(t.getbbox()); r=size/max(t.size); return t.resize((round(t.width*r),round(t.height*r)),Image.LANCZOS)
def split(line):
    # text part then emoji groups (separated by spaces)
    i=len(line)
    while i>0 and (is_emoji(line[i-1]) or line[i-1]==' '): i-=1
    txt=line[:i].rstrip(); tail=line[i:].strip()
    emos=[e for e in tail.split(' ') if e]
    groups=[]
    for e in emos:
        cur=''
        for ch in e:
            if ch in '‍️' or (cur and cur[-1]=='‍'): cur+=ch
            else:
                if cur: groups.append(cur)
                cur=ch
        if cur: groups.append(cur)
    return txt,groups
def layout_width(k,txt,groups):
    f=ImageFont.truetype(F,SIZE*k*S); tr=SIZE*k*S*TR
    w=(f.getlength(txt)+(len(txt)-1)*tr)/S+2*SW2/S if txt else 0
    if groups: w+=44*k+len(groups)*128*k+29*k*(len(groups)-1)
    return w
def draw(template_rgba,lines,cx=1082,top_base=849,gap=147,maxw=1840):
    im=template_rgba.copy()
    k=min(1.0,maxw/max(layout_width(1.0,*split(l)) for l in lines))
    W,H=im.size
    layer=Image.new('RGBA',(W*S,H*S),(0,0,0,0)); d=ImageDraw.Draw(layer)
    f=ImageFont.truetype(F,SIZE*k*S); tr=SIZE*k*S*TR
    n=len(lines); block=(n-1)*gap*k
    first=top_base+((1-k)*91)/2 + ((2-1)*gap-(n-1)*gap*k)/2  # keep block centred where the 2-line original sits
    emos=[]
    for li,l in enumerate(lines):
        txt,groups=split(l)
        base=first+li*gap*k
        total=layout_width(k,txt,groups); x=cx-total/2
        for i,ch in enumerate(txt):
            d.text(((x+SW2/S)*S+f.getlength(txt[:i])+i*tr,base*S),ch,font=f,fill=(0,0,0,255),anchor='ls',stroke_width=SW2,stroke_fill=(0,0,0,255))
        x+=(f.getlength(txt)+(len(txt)-1)*tr)/S+2*SW2/S+44*k
        for g in groups:
            e=emoji_img(g,round(128*k)); emos.append((e,(round(x),round(base+23*k-e.height)))); x+=e.width+29*k
    layer=layer.resize((W,H),Image.LANCZOS)
    im.alpha_composite(layer)
    for e,pos in emos: im.alpha_composite(e,pos)
    return im,k
