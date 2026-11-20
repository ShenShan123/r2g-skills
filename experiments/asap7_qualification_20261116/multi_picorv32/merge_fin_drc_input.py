"""Normalize FIN polygon fragmentation while proving unchanged mask geometry."""
from pathlib import Path
from collections import Counter
import hashlib,json,os,time
import pya
q=Path(__file__).resolve().parent.parent
project=os.environ['QUAL_PROJECT'];source=q/(project+'_input')/'drc.gds'
out=q/(project+'_fin_merged_input');out.mkdir(exist_ok=True)
start=time.monotonic()
a=pya.Layout();a.read(str(source));b=pya.Layout();b.read(str(source));top=b.top_cell()
assert top.name=='TOPCELL';top.flatten(True);assert len(list(b.each_cell()))==1
idx=b.find_layer(2,0);raw=pya.Region(top.shapes(idx));merged=raw.merged()
fin={'layer':2,'datatype':0,'before_polygons':raw.count(),'after_polygons':merged.count(),'before_xlength_dbu_counts':dict(Counter(p.bbox().width() for p in raw.each())),'after_xlength_dbu_counts':dict(Counter(p.bbox().width() for p in merged.each()))}
assert len(fin['after_xlength_dbu_counts'])==1,'Merged FIN does not meet DRM equal x-length condition'
top.shapes(idx).clear();top.shapes(idx).insert(merged)
path=out/'drc.gds';b.write(str(path));b=pya.Layout();b.read(str(path))
infos=lambda x:{(x.get_info(i).layer,x.get_info(i).datatype) for i in x.layer_indices()}
assert infos(a)==infos(b);checks=[]
for layer,datatype in sorted(infos(a)):
 ra=pya.Region(a.top_cell().begin_shapes_rec(a.find_layer(layer,datatype)))
 rb=pya.Region(b.top_cell().begin_shapes_rec(b.find_layer(layer,datatype)))
 assert (ra^rb).is_empty(),(layer,datatype)
 checks.append({'layer':layer,'datatype':datatype,'xor_polygons':0,'before_polygons':ra.count(),'after_polygons':rb.count()})
record={'result':'PASS: mask geometry equivalence; not a DRC verdict','project':project,'source':str(source),'source_sha256':hashlib.sha256(source.read_bytes()).hexdigest(),'normalized_gds':str(path),'normalized_sha256':hashlib.sha256(path.read_bytes()).hexdigest(),'dbu_um':b.dbu,'cells':len(list(b.each_cell())),'top':'TOPCELL','operations':['Flatten hierarchy without scale/offset','Exact FIN(2/0) polygon union; no vertices moved, no mask area added/deleted'],'fin':fin,'all_drawing_layers_xor_empty':True,'layers':checks,'elapsed_s':time.monotonic()-start,'official_basis':{'document':str(q/'reference_diagnosis/asap7_drm_201207a.pdf'),'document_sha256':hashlib.sha256((q/'reference_diagnosis/asap7_drm_201207a.pdf').read_bytes()).hexdigest(),'page_18':'When running hierarchical DRC, ensure that the FIN layer polygons have equal length along the x-axis.','page_19_note':'GATE equal y-length condition also noted; this candidate does not merge or modify GATE.'},'scope':'No DRC rule edited/excluded. Polygon XOR excludes text, preserves all drawing mask geometry; actual DRC verdict must be separately obtained.'}
(out/'geometry_verification.json').write_text(json.dumps(record,indent=2)+'\n')
print(json.dumps({'project':project,'fin':fin,'layers_checked':len(checks),'elapsed_s':record['elapsed_s']}))
