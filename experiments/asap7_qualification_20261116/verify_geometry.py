"""Verify the LVS pin-text adapter preserves every non-text shape and instance."""
from pathlib import Path
import pya,json,hashlib,re,os
r=Path(__file__).resolve().parent

def geometry(path, rename_top=None):
 l=pya.Layout();l.read(str(path));entries=[]
 if rename_top:l.top_cell().name=rename_top
 for c in l.each_cell():
  for i in l.layer_indices():
   for s in c.shapes(i).each():
    if not s.is_text():entries.append((c.name,str(l.get_info(i)),re.sub(r' prop_id=\d+', '', s.to_s()),str(sorted(s.properties().items(),key=str))))
  for inst in c.each_inst():entries.append((c.name,'instance',l.cell(inst.cell_index).name,str(inst.cplx_trans),str(inst.a),str(inst.b),inst.na,inst.nb))
 payload=json.dumps(sorted(entries,key=str),ensure_ascii=True).encode()
 return {'non_text_records':len(entries),'sha256':hashlib.sha256(payload).hexdigest()}
project=os.environ.get('QUAL_PROJECT')
if project:
 out=r/f'{project}_input'
 original=Path(json.loads((out/'provenance.json').read_text())['original_gds'])
 a=geometry(original, 'gcd');b=geometry(out/'lvs.gds', 'gcd');c=geometry(out/'drc.gds', 'gcd')
 assert a==b==c
 result={'unchanged':True,'original':a,'lvs':b,'drc':c,'normalization':'top cell name only; text excluded'}
else:
 out=r/'gcd_calibre_input'
 a=geometry(out/'gcd.gds');b=geometry(out/'gcd_pinmapped.gds')
 assert a==b
 result={'unchanged':True,'original':a,'pinmapped':b}
(out/'geometry_verification.json').write_text(json.dumps(result,indent=2)+'\n')
print('PASS: non-text geometry and instance transforms unchanged',a)
