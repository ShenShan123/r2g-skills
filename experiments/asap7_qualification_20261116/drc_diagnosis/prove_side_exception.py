from pathlib import Path
import pya,re,json,collections
r=Path(__file__).resolve().parent;root=r.parent;l=pya.Layout();l.read(str(root/'gcd_asym_via_input/drc.gds'));t=l.cell('TOPCELL');m1=l.layer(19,0)
lef=Path('/home/yangao/r2g_toolchain/OpenROAD-flow-scripts/flow/platforms/asap7/lef/asap7sc7p5t_28_R_1x_220121a.lef').read_text();sizes={m[1]:float(re.search(r'SIZE (\S+) BY',m[2])[1]) for m in re.finditer(r'MACRO (\S+)\n(.*?)\nEND \1',lef,re.S)}
s=(r/'asym_evidence/drc_results.db').read_text().splitlines();i=s.index('V0.M1.AUX.3')+3;rows=[]
while i<len(s) and re.match(r'p \d+ \d+',s[i]):
 n=int(s[i].split()[2]);points=[pya.Point(round(int(a)/10000/l.dbu),round(int(b)/10000/l.dbu)) for a,b in (x.split() for x in s[i+1:i+1+n])];poly=pya.Polygon(points);i+=n+1
 it=t.begin_shapes_rec_touching(m1,poly.bbox().enlarged(1));shapes=[]
 while not it.at_end():
  sh=it.shape()
  if not sh.is_text():
   p=sh.polygon.transformed(it.trans())
   if not(pya.Region(p)&pya.Region(poly).sized(1)).is_empty():shapes.append((it.cell().name,p,it.trans()))
  it.next()
 natives=[x for x in shapes if x[0] in sizes];vias=[x for x in shapes if x[0].startswith('VIA')]
 if not natives or not vias:continue
 name,native,tr=natives[0];vp=vias[0][1].transformed(tr.inverted());vb=vp.bbox().to_dtype(l.dbu);x=(vb.left+vb.right)/2
 base=pya.Region(l.cell(name).begin_shapes_rec(m1)).merged();outside=(pya.Region(vp)-base).area()*l.dbu*l.dbu
 rows.append({'cell':name,'via_m1_local_bbox':[vb.left,vb.bottom,vb.right,vb.top],'ap_x_um':x,'cell_width_um':sizes[name],'side_bound':x<=.054+1e-6 or x>=sizes[name]-.054-1e-6,'via_m1_outside_native_um2':outside})
summary={'count':len(rows),'side_bound':sum(x['side_bound'] for x in rows),'added_native_metal':sum(x['via_m1_outside_native_um2']>0 for x in rows),'rows':rows};(r/'asym_evidence/side_exception_proof.json').write_text(json.dumps(summary,indent=2)+'\n');print({k:v for k,v in summary.items() if k!='rows'})
