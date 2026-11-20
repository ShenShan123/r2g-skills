"""Prepare unchanged GCD layout and full transistor-level source for Calibre."""
from pathlib import Path
import hashlib
import json
import pya

root=Path('/home/yangao/r2g-skills/experiments/asap7_qualification_20261116')
project=root/'gcd_smoke';run=sorted((project/'backend').glob('RUN_*'))[-1]
out=root/'gcd_calibre_input';out.mkdir(exist_ok=True)
gds=run/'results/6_final.gds'
layout=pya.Layout();layout.read(str(gds));top=layout.cell('gcd');assert top is not None
layout.write(str(out/'gcd.gds'))
before=str(top.dbbox());top.name='TOPCELL';layout.write(str(out/'gcd_topcell.gds'))
assert before==str(top.dbbox())
cdl=(project/'lvs/asap7_R_with_physical_tap.cdl').read_bytes()+(project/'lvs/6_final.cdl').read_bytes()
(out/'gcd_with_cells.cdl').write_bytes(cdl)
meta={'original_gds':str(gds),'original_sha256':hashlib.sha256(gds.read_bytes()).hexdigest(),
      'drc_top_rename':{'gcd':'TOPCELL'},'geometry_scale':1,'bbox_um':before,'dbu_um':layout.dbu,
      'source':'OpenROAD write_cdl from final ODB + pinned official RVT CDL; empty physical tap model only',
      'source_sha256':hashlib.sha256(cdl).hexdigest(),'blackboxed_logic_cells':0}
(out/'input_provenance.json').write_text(json.dumps(meta,indent=2)+'\n')
print(json.dumps(meta),flush=True)
