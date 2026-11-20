"""Rename DFFASRHQNx1's ports RESETn/SETn -> RESETN/SETN (the LEF/liberty spelling) inside that subckt only."""
import re,sys,hashlib
src,dst=sys.argv[1],sys.argv[2]
out=[];inside=False;changed=0
for l in open(src):
    u=l.upper()
    if u.startswith('.SUBCKT'):
        inside=l.split()[1]=='DFFASRHQNx1_ASAP7_75t_R'
    if inside:
        n=re.sub(r'(?<![\w])RESETn(?![\w])','RESETN',l); n=re.sub(r'(?<![\w])SETn(?![\w])','SETN',n)
        if n!=l: changed+=1
        l=n
    if u.startswith('.ENDS'): inside=False
    out.append(l)
open(dst,'w').writelines(out)
a=[x for x in open(src)]; b=out
diff=[i for i,(x,y) in enumerate(zip(a,b)) if x!=y]
print('lines',len(a),len(b),'changed lines',changed,'first/last',diff[:1],diff[-1:])
print('sha',hashlib.sha256(open(dst,'rb').read()).hexdigest())
