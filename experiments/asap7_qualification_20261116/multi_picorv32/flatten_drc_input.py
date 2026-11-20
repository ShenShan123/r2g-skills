"""Flatten hierarchy only; prove every drawing layer's geometric union unchanged."""
from pathlib import Path
import hashlib,json,os,time
import pya
q=Path(__file__).resolve().parent.parent
project=os.environ.get('QUAL_PROJECT','multi_picorv32')
source=q/(project+'_input')/'drc.gds'
out=q/(project+'_flattened_input');out.mkdir(exist_ok=True)
start=time.monotonic()
original=pya.Layout();original.read(str(source))
flat=pya.Layout();flat.read(str(source))
top=flat.top_cell();assert top.name=='TOPCELL'
top.flatten(True)
assert len(list(flat.each_cell()))==1
path=out/'drc.gds';flat.write(str(path))
# Re-read the actual emitted artifact, not just an in-memory flatten operation.
flat=pya.Layout();flat.read(str(path))
checks=[]
original_layers={(original.get_info(i).layer,original.get_info(i).datatype) for i in original.layer_indices()}
flat_layers={(flat.get_info(i).layer,flat.get_info(i).datatype) for i in flat.layer_indices()}
assert original_layers==flat_layers
for layer,datatype in sorted(original_layers):
 a=pya.Region(original.top_cell().begin_shapes_rec(original.find_layer(layer,datatype)))
 b=pya.Region(flat.top_cell().begin_shapes_rec(flat.find_layer(layer,datatype)))
 diff=a^b
 assert diff.is_empty(),(layer,datatype,diff.count())
 checks.append({'layer':layer,'datatype':datatype,'original_polygons':a.count(),'flattened_polygons':b.count(),'xor_polygons':0})
record={'result':'PASS','source':str(source),'source_sha256':hashlib.sha256(source.read_bytes()).hexdigest(),'flattened_gds':str(path),'flattened_sha256':hashlib.sha256(path.read_bytes()).hexdigest(),'dbu_um':flat.dbu,'cells':len(list(flat.each_cell())),'top':'TOPCELL','operation':'Hierarchy flatten only; zero scale/offset; all drawing layers identical by exact Region XOR after output reload. Text is not included in polygon XOR.','layers':checks,'elapsed_s':time.monotonic()-start}
(out/'geometry_verification.json').write_text(json.dumps(record,indent=2)+'\n')
print(json.dumps({'result':'PASS','project':project,'cells':1,'layers':len(checks),'bytes':path.stat().st_size,'elapsed_s':record['elapsed_s']}))
