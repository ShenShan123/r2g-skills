"""Replay bounded RV32I program on RTL or the latest implemented netlist."""
import argparse,hashlib,json,subprocess
from pathlib import Path
p=argparse.ArgumentParser();p.add_argument('mode',choices=['rtl','gate']);args=p.parse_args()
root=Path(__file__).resolve().parent
if args.mode=='rtl':
 files=sorted((root/'rtl').glob('*.v'))
else:
 netlist=sorted((root/'backend').glob('RUN_*/results/6_final.v'))[-1]
 library=root.parent/'multi_aes/tb/cells_from_liberty.v'
 local=root/'tb/cells_from_liberty.v'
 local.write_bytes(library.read_bytes())
 files=[netlist,local]
target=root/'reports'/f'{args.mode}_sim'
commands=[['iverilog','-g2012','-s','picorv32_tb','-o',str(target),*[str(f) for f in files],str(root/'tb/picorv32_tb.v')],['vvp',str(target)]]
results=[]
for i,cmd in enumerate(commands):
 log=root/'reports'/f'{args.mode}_sim{("_compile" if i==0 else "")}.log'
 with log.open('w') as stream:
  r=subprocess.run(cmd,cwd=root,stdout=stream,stderr=subprocess.STDOUT)
 results.append({'cmd':cmd,'returncode':r.returncode,'log':str(log)})
 if r.returncode:raise SystemExit(r.returncode)
text=(root/'reports'/f'{args.mode}_sim.log').read_text()
assert 'PASS all 3 memory-latency modes' in text
(root/'reports'/f'{args.mode}_simulation.json').write_text(json.dumps({'result':'PASS','scope':'bounded program simulation, not exhaustive ISA verification or formal equivalence; gate uses zero-delay Liberty functional models','commands':results,'inputs':{str(f.relative_to(root)) if f.is_relative_to(root) else str(f):hashlib.sha256(f.read_bytes()).hexdigest() for f in files+[root/'tb/picorv32_tb.v',root/'tb/program.hex']},'signature_words_per_mode':9,'memory_latency_modes':3},indent=2)+'\n')
print(text)
