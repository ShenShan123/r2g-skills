from pathlib import Path
import hashlib, json, shlex, shutil, subprocess, difflib, time
Q=Path('/home/yangao/r2g-skills/experiments/asap7_qualification_20261116/reference_diagnosis')
S=Path('/home/yangao/r2g_toolchain/OpenROAD-flow-scripts/tools/OpenROAD')
B=S/'build_r2g_openroad'
D=Q/'strict_via_build';D.mkdir(exist_ok=True)
p=S/'src/drt/src/pa/FlexPA_acc_point.cpp'
original=p.read_bytes(); text=original.decode()
needle='  if (!is_side_bound) {\n'
assert text.count(needle)==1
replacement='''  // An explicit via-in-pin constraint also applies at cell side boundaries.
  const bool strict_via_in_pin
      = layer_num >= router_cfg_->VIAINPIN_BOTTOMLAYERNUM
        && layer_num <= router_cfg_->VIAINPIN_TOPLAYERNUM;
  if (!is_side_bound || strict_via_in_pin) {
'''
patched=text.replace(needle,replacement)
(D/'FlexPA_acc_point.original.cpp').write_bytes(original)
cpp=D/'FlexPA_acc_point.cpp';cpp.write_text(patched)
(Q/'strict_via_in_pin.patch').write_text(''.join(difflib.unified_diff(text.splitlines(True),patched.splitlines(True),fromfile='a/src/drt/src/pa/FlexPA_acc_point.cpp',tofile='b/src/drt/src/pa/FlexPA_acc_point.cpp')))
compile_entry=next(c for c in json.loads((B/'compile_commands.json').read_text()) if c['file']==str(p))
cmd=shlex.split(compile_entry['command']);cmd[cmd.index('-o')+1]=str(D/'FlexPA_acc_point.cpp.o');cmd[-1]=str(cpp)
archive=D/'libdrt_lib.a'
link=shlex.split((B/'src/CMakeFiles/openroad.dir/link.txt').read_text())
link[link.index('-o')+1]=str(Q/'patched_openroad')
link=['-flto=4' if a=='-flto=auto' else a for a in link]
assert link.count('drt/libdrt_lib.a')==1
link[link.index('drt/libdrt_lib.a')]=str(archive)
link.append('-Wl,-rpath,/home/yangao/r2g_toolchain/build-env/lib')
commands=[]
def run(args,cwd):
 commands.append({'command':args,'cwd':str(cwd)})
 (D/'commands.json').write_text(json.dumps(commands,indent=2)+'\n')
 print('RUN',shlex.join(args),flush=True)
 t=time.monotonic();subprocess.run(args,cwd=cwd,check=True);print('ELAPSED',round(time.monotonic()-t,3),flush=True)
run(cmd,compile_entry['directory'])
shutil.copyfile(B/'src/drt/libdrt_lib.a',archive)
run(['/usr/bin/gcc-ar-11','r',str(archive),str(D/'FlexPA_acc_point.cpp.o')],D)
run(['/usr/bin/gcc-ranlib-11',str(archive)],D)
run(link,B/'src')
assert p.read_bytes()==original
prod=S.parent/'install/OpenROAD/bin/openroad'
hashfile=lambda x: hashlib.sha256(x.read_bytes()).hexdigest()
report={'source_sha256':hashfile(p),'patched_source_sha256':hashfile(cpp),'production_binary_sha256':hashfile(prod),'patched_binary_sha256':hashfile(Q/'patched_openroad'),'shared_sources_modified':False,'shared_build_objects_modified':False,'production_install_modified':False,'compilation_units_rebuilt':1,'link_lto_workers':4,'git_commit':subprocess.check_output(['git','rev-parse','HEAD'],cwd=S,text=True).strip()}
(D/'build_provenance.json').write_text(json.dumps(report,indent=2)+'\n')
print(json.dumps(report,indent=2),flush=True)
