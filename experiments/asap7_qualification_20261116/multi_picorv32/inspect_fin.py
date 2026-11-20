from pathlib import Path
from collections import Counter
import pya,json
p=Path(__file__).resolve().parent
l=pya.Layout();l.read(str(p.parent/'multi_picorv32_input/drc.gds'))
r=pya.Region(l.top_cell().begin_shapes_rec(l.find_layer(2,0)))
raw=Counter(x.bbox().width() for x in r.each());m=r.merged();merged=Counter(x.bbox().width() for x in m.each())
result={'dbu_um':l.dbu,'raw_polygons':r.count(),'raw_xlength_dbu_counts':dict(raw),'merged_polygons':m.count(),'merged_xlength_dbu_counts':dict(merged),'raw_vs_merged_xor_empty':(r^m).is_empty()}
(p/'reports/fin_fragmentation.json').write_text(json.dumps(result,indent=2)+'\n')
print(json.dumps(result))
