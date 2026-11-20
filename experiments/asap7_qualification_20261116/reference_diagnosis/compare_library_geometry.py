from pathlib import Path
import pya,json,hashlib
root=Path('/home/yangao/r2g-skills/experiments/asap7_qualification_20261116/reference_diagnosis')
a=pya.Layout();a.read('/home/yangao/r2g_toolchain/OpenROAD-flow-scripts/flow/platforms/asap7/gds/asap7sc7p5t_28_R_220121a.gds')
b=pya.Layout();b.read(str(root/'official_R.gds'))
res={'orfs_dbu':a.dbu,'official_dbu':b.dbu,'only_orfs':[], 'only_official':[], 'different_geometry':[], 'different_texts':[]}
an={c.name:c for c in a.each_cell()};bn={c.name:c for c in b.each_cell()}
res['only_orfs']=sorted(an.keys()-bn.keys());res['only_official']=sorted(bn.keys()-an.keys())
for name in sorted(an.keys()&bn.keys()):
 ca,cb=an[name],bn[name]
 layers=sorted(set((a.get_info(i).layer,a.get_info(i).datatype) for i in a.layer_indices())|set((b.get_info(i).layer,b.get_info(i).datatype) for i in b.layer_indices()))
 dif=[];tdif=[]
 for layer,datatype in layers:
  ia=a.find_layer(layer,datatype);ib=b.find_layer(layer,datatype)
  ra=pya.Region(ca.begin_shapes_rec(ia)) if ia is not None else pya.Region()
  rb=pya.Region(cb.begin_shapes_rec(ib)) if ib is not None else pya.Region()
  if a.dbu != b.dbu: rb=rb.transformed(pya.ICplxTrans(b.dbu/a.dbu,0,False,0,0))
  delta=ra^rb
  if not delta.is_empty(): dif.append({'layer':f'{layer}/{datatype}','xor_area_um2':delta.area()*a.dbu*a.dbu,'bbox':str(delta.bbox())})
  def texts(c,i):
   if i is None:return []
   itr=c.begin_shapes_rec(i);out=[]
   while not itr.at_end():
    s=itr.shape()
    if s.is_text():out.append(str(s.text.transformed(itr.trans())))
    itr.next()
   return sorted(out)
  if texts(ca,ia)!=texts(cb,ib):tdif.append(f'{layer}/{datatype}')
 if dif:res['different_geometry'].append({'cell':name,'layers':dif})
 if tdif:res['different_texts'].append({'cell':name,'layers':tdif})
(root/'library_geometry_comparison.json').write_text(json.dumps(res,indent=2)+'\n')
print(json.dumps(res,indent=2))
