"""Extract one real ASAP7 library cell and explicit geometry scale controls."""
import json
from pathlib import Path
import pya

root=Path('/home/yangao/r2g-skills/experiments/asap7_qualification_20261116')
source=Path('/home/yangao/r2g_toolchain/OpenROAD-flow-scripts/flow/platforms/asap7/gds/asap7sc7p5t_28_R_220121a.gds')
layout=pya.Layout();layout.read(str(source))
name='INVx1_ASAP7_75t_R'
cell=layout.cell(name)
assert cell is not None,name
out=root/'inv_smoke';out.mkdir(exist_ok=True)
meta={'source':str(source),'top':name,'source_dbu_um':layout.dbu,'source_bbox_um':str(cell.dbbox()),'variants':[]}
for scale in (1,4):
    target=pya.Layout();target.dbu=layout.dbu
    top=target.create_cell(name);top.copy_tree(cell)
    if scale!=1:target.transform(pya.ICplxTrans(scale,0,False,0,0))
    file=out/f'inv_{scale}x.gds';target.write(str(file))
    top.name='TOPCELL'
    target.write(str(out/f'topcell_inv_{scale}x.gds'))
    meta['variants'].append({'path':str(file),'scale':scale,'bbox_um':str(top.dbbox())})
(out/'geometry.json').write_text(json.dumps(meta,indent=2)+'\n')
print(json.dumps(meta),flush=True)
