from pathlib import Path
import pya,json,collections,re
root=Path(__file__).resolve().parents[1]
l=pya.Layout();l.read(str(root/'gcd_asym_via_input/drc.gds'));t=l.cell('TOPCELL')
s=(root/'drc_diagnosis/asym_evidence/drc_results.db').read_text().splitlines();i=s.index('V0.M1.AUX.3')+3
rows=[]
while i<len(s) and re.match(r'p \d+ \d+',s[i]):
 n=int(s[i].split()[2]);pts=[pya.Point(round(int(a)/10000/l.dbu),round(int(b)/10000/l.dbu)) for a,b in (line.split() for line in s[i+1:i+1+n])]; poly=pya.Polygon(pts);i+=n+1
 search=poly.bbox().enlarged(round(.006/l.dbu));it=t.begin_shapes_rec_touching(l.layer(19,0),search);out=[]
 while not it.at_end():
  sh=it.shape()
  if not sh.is_text():
   p=sh.polygon.transformed(it.trans())
   if not (pya.Region(p)&pya.Region(poly).sized(1)).is_empty():out.append({'cell':it.cell().name,'bbox':str(p.bbox().to_dtype(l.dbu))})
  it.next()
 native=sorted(set(z['cell'] for z in out if not z['cell'].startswith('VIA') and z['cell']!='TOPCELL'))
 route=sorted(set(z['cell'] for z in out if z['cell'].startswith('VIA') or z['cell']=='TOPCELL'))
 rows.append({'v0':str(poly.bbox().to_dtype(l.dbu)),'native':native,'route':route,'m1':out})
c=collections.Counter(tuple(r['native']) for r in rows);print('native masters',c);print('near routing',sum(bool(r['route']) for r in rows),'total',len(rows));print('no route first',next((x for x in rows if not x['route']),None));(root/'drc_diagnosis/asym_evidence/v0_classification.json').write_text(json.dumps(rows,indent=2)+'\n')
