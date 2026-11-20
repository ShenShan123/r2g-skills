import re,sys
cdl,lef=sys.argv[1],sys.argv[2]
sub={}
for l in open(cdl):
    if l.upper().startswith('.SUBCKT'):
        t=l.split(); sub[t[1]]=t[2:]
pins={};cur=None
for l in open(lef):
    m=re.match(r'^MACRO\s+(\S+)',l)
    if m: cur=m.group(1); pins[cur]=[]
    m=re.match(r'^\s+PIN\s+(\S+)',l)
    if m and cur: pins[cur].append(m.group(1))
bad=0
for c,p in pins.items():
    if c not in sub: print('NO SUBCKT',c); continue
    s=set(sub[c]); lp=set(p)
    if s!=lp:
        bad+=1; print(c,'lef-only',sorted(lp-s),'cdl-only',sorted(s-lp))
print('cells',len(pins),'mismatched',bad)
