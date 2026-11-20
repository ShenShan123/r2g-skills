"""Map ORFS label-purpose text to official pin-purpose text, without changing geometry."""
from pathlib import Path
import pya,json,hashlib
r=Path('/home/yangao/r2g-skills/experiments/asap7_qualification_20261116')
l=pya.Layout();l.read(str(r/'gcd_calibre_input/gcd.gds'));t=l.cell('gcd');n=0
for metal in [19,20,30,40,50,60,70,80,90]:
 src=l.find_layer(metal,2)
 if src is None:continue
 dst=l.layer(metal,251)
 for shape in list(t.shapes(src).each()):
  if shape.is_text():t.shapes(dst).insert(shape.text);n+=1
p=r/'gcd_calibre_input/gcd_pinmapped.gds';l.write(str(p))
(r/'gcd_calibre_input/pinmap_provenance.json').write_text(json.dumps({'top':'gcd','copied_top_level_texts':n,'mapping':'metal/2 LABEL text copied to metal/251 PIN text; original labels retained','geometry_modified':False,'sha256':hashlib.sha256(p.read_bytes()).hexdigest()},indent=2)+'\n')
print('copied labels',n)
