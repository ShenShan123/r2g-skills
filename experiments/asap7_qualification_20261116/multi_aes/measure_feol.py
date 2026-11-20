from pathlib import Path
from collections import Counter
import json,pya
q=Path(__file__).resolve().parent.parent
l=pya.Layout();l.read(str(q/'multi_aes_input/drc.gds'));t=l.top_cell();out={}
for name,layer in [('FIN',2),('GATE',7),('ACTIVE',11)]:
 idx=l.find_layer(layer,0)
 if idx is None:continue
 raw=pya.Region(t.begin_shapes_rec(idx));merged=raw.merged()
 out[name]={'raw':raw.count(),'merged':merged.count(),'raw_heights':dict(Counter(p.bbox().height() for p in raw.each())),'merged_heights':dict(Counter(p.bbox().height() for p in merged.each())),'raw_widths':dict(Counter(p.bbox().width() for p in raw.each())),'merged_widths':dict(Counter(p.bbox().width() for p in merged.each()))}
(q/'multi_aes/reports/feol_shape_counts.json').write_text(json.dumps(out,indent=2)+'\n')
for name,row in out.items():print(name,row['raw'],row['merged'],'height variants',len(row['merged_heights']),'width variants',len(row['merged_widths']))
