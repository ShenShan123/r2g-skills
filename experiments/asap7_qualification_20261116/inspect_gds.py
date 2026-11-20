from pathlib import Path
import pya
l=pya.Layout();l.read('/home/yangao/r2g-skills/experiments/asap7_qualification_20261116/gcd_calibre_input/gcd.gds')
t=l.cell('gcd')
for i in l.layer_indices():
 s=t.shapes(i); texts=[x.text.string for x in s.each() if x.is_text()]
 if texts:print(l.get_info(i),len(texts),texts[:8])
for name in ['TAPCELL_ASAP7_75t_R','DECAPx2_ASAP7_75t_R']:
 c=l.cell(name); print(name,str(c.dbbox()))
