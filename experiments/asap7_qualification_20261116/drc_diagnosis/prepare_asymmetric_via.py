"""Preserve existing candidates; add legal 5+2 nm V1 variants and M4 EOL fix."""
from pathlib import Path
import hashlib, json

root=Path(__file__).resolve().parent
source=root/'asap7_tech_v1_m1_spacing_candidate.lef'
s=source.read_text()
a=s.index('VIA VIA12 Default\n');b=s.index('END VIA12',a)+len('END VIA12')
old=s[a:b]
up=old.replace('RECT -0.009 -0.014 0.009 0.014 ;','RECT -0.009 -0.011 0.009 0.014 ;')
down=old.replace('VIA VIA12 Default','VIA VIA12_DOWN').replace('END VIA12','END VIA12_DOWN').replace('RECT -0.009 -0.014 0.009 0.014 ;','RECT -0.009 -0.014 0.009 0.011 ;')
assert up!=old and down!=old
s=s[:a]+up+'\n\n'+down+s[b:]
(root/'asap7_tech_asym_v1_m1_candidate.lef').write_text(s)
a=s.index('LAYER M4\n');b=s.index('END M4',a)
block=s[a:b]
assert block.count('0.048 0.02425 0.048')==1
s=s[:a]+block.replace('0.048 0.02425 0.048','0.048 0.025 0.048')+s[b:]
(root/'asap7_tech_asym_v1_m1_m4_candidate.lef').write_text(s)
print('Wrote two isolated candidates with 5+2 nm V1 and optional M4 25 nm side keepout.')
