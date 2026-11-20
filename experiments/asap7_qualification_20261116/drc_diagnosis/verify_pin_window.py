from pathlib import Path
import pya,re,json
r=Path(__file__).resolve().parent
paths=[Path('/home/yangao/r2g_toolchain/OpenROAD-flow-scripts/flow/platforms/asap7/lef/asap7sc7p5t_28_R_1x_220121a.lef'),r/'asap7sc7p5t_R_pin_window_candidate.lef']
def read(p):
 out={}
 for ma in re.finditer(r'MACRO (\S+)\n(.*?)\nEND \1',p.read_text(),re.S):
  name,body=ma.groups(); layer=None; reg=pya.Region()
  for line in body.splitlines():
   m=re.search(r'LAYER (\S+) ;',line)
   if m:layer=m[1]
   m=re.search(r'RECT (\S+) (\S+) (\S+) (\S+) ;',line)
   if m and layer=='M1':reg.insert(pya.DBox(*map(float,m.groups())).to_itype(.00025))
  out[name]=reg.merged()
 return out
a,b=map(read,paths);diff=[]
for name in a:
 if not(a[name]^b[name]).is_empty():diff.append(name)
result={'cells_checked':len(a),'native_m1_pin_plus_obs_union_unchanged':not diff,'mismatched_cells':diff};(r/'pin_window_geometry_verification.json').write_text(json.dumps(result,indent=2)+'\n');print(result)
