from pathlib import Path
import json, re, collections
import pya

root = Path(__file__).resolve().parents[2]
lines=(root/'multi_uart/signoff/drc/drc_results.db').read_text().splitlines()
rules={}; current=None; i=1
while i<len(lines):
    line=lines[i]
    if re.fullmatch(r'[A-Z][A-Za-z0-9_.]+',line) and i+1<len(lines) and re.match(r'\d+ \d+ \d+',lines[i+1]):
        current=line; count=int(lines[i+1].split()[0]);rules[current]={'count':count,'markers':[]};i+=3
    elif re.match(r'[pe] \d+ \d+',line):
        kind,idx,n=line.split();n=int(n);coords=[]
        for row in lines[i+1:i+1+n]:
            v=list(map(int,row.split()));coords.extend(list(zip(v[::2],v[1::2])))
        xs,ys=zip(*coords);rules[current]['markers'].append({'type':kind,'bbox_um':[min(xs)/10000,min(ys)/10000,max(xs)/10000,max(ys)/10000],'coords':coords});i+=n+1
    else:i+=1

l=pya.Layout();l.read(str(root/'multi_uart_input/drc.gds')); t=l.cell('TOPCELL')
layers={'FIN':2,'V0':18,'M1':19,'V1':21,'M3':30,'V2':25,'M4':40,'M6':60,'V4':45,'M5':50}
result={}
for rule,entry in rules.items():
    if not entry['count']:continue
    near=[]
    for m in entry['markers']:
        x1,y1,x2,y2=m['bbox_um']; dbox=pya.DBox(x1-.0005,y1-.0005,x2+.0005,y2+.0005);box=dbox.to_itype(l.dbu)
        shapes={}
        for layer_name in layers:
            it=t.begin_shapes_rec_touching(l.layer(layers[layer_name],0),box)
            found=[]
            while not it.at_end():
                s=it.shape()
                if not s.is_text():
                    b=s.bbox().transformed(it.trans()).to_dtype(l.dbu)
                    found.append({'cell':it.cell().name,'box':[b.left,b.bottom,b.right,b.top],'transform':str(it.trans())})
                it.next()
            if found:shapes[layer_name]=found[:30]
        near.append({'marker_bbox':m['bbox_um'],'shapes':shapes})
    result[rule]={'count':entry['count'],'examples':near}

cell_fin={}
for name in ['INVx1_ASAP7_75t_R','TAPCELL_ASAP7_75t_R','FILLER_ASAP7_75t_R','DECAPx10_ASAP7_75t_R']:
    c=l.cell(name)
    if c:cell_fin[name]=[str(s.bbox().to_dtype(l.dbu)) for s in c.shapes(l.layer(2,0)).each()]
result['cell_fin_geometry']=cell_fin
fin=pya.Region(t.begin_shapes_rec(l.layer(2,0))).merged()
stripes=[p.bbox() for p in fin.each()]
fin_heights=collections.Counter(round(b.height()*l.dbu*1000,6) for b in stripes)
y=sorted(set((b.bottom+b.top)*l.dbu*500 for b in stripes))
result['merged_fin']={'polygons':len(stripes),'height_nm':dict(fin_heights),'center_pitch_nm':dict(collections.Counter(round(b-a,6) for a,b in zip(y,y[1:]))),'centers_nm':y}

(root/'multi_uart/signoff/marker_geometry.json').write_text(json.dumps(result,indent=2)+'\n')
print(json.dumps({'rules':{k:v['count'] for k,v in rules.items() if v['count']},'cell_fin_geometry':cell_fin},indent=2))
