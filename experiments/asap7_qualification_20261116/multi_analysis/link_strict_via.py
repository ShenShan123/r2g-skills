"""Link the already compiled strict via-in-pin object, without touching installs."""
from pathlib import Path
import hashlib
import json
import shlex
import shutil
import subprocess
import tempfile
import time

root = Path(__file__).resolve().parents[1]
source = Path('/home/yangao/r2g_toolchain/OpenROAD-flow-scripts/tools/OpenROAD')
build = source / 'build_r2g_openroad'
saved = root / 'reference_diagnosis/strict_via_build'
assert (source / 'src/drt/src/pa/FlexPA_acc_point.cpp').read_bytes() == (saved / 'FlexPA_acc_point.original.cpp').read_bytes()
work = Path(tempfile.mkdtemp(prefix='r2g-asap7-strict-via-'))
output = root / 'reference_diagnosis/patched_openroad'
archive = work / 'libdrt_lib.a'
shutil.copyfile(build / 'src/drt/libdrt_lib.a', archive)
obj = work / 'FlexPA_acc_point.cpp.o'
shutil.copyfile(saved / obj.name, obj)
commands = []
start = time.monotonic()

def run(command, cwd):
    commands.append({'command': command, 'cwd': str(cwd)})
    (root / 'multi_analysis/strict_via_link_commands.json').write_text(json.dumps(commands, indent=2) + '\n')
    print('RUN', shlex.join(command), flush=True)
    subprocess.run(command, cwd=cwd, check=True)

run(['/usr/bin/gcc-ar-11', 'r', str(archive), str(obj)], work)
run(['/usr/bin/gcc-ranlib-11', str(archive)], work)
link = shlex.split((build / 'src/CMakeFiles/openroad.dir/link.txt').read_text())
link[link.index('-o') + 1] = str(output)
link[link.index('drt/libdrt_lib.a')] = str(archive)
link = ['-flto=4' if value == '-flto=auto' else value for value in link]
link.append('-Wl,-rpath,/home/yangao/r2g_toolchain/build-env/lib')
run(link, build / 'src')
sha = lambda path: hashlib.sha256(path.read_bytes()).hexdigest()
report = {
    'status': 'built', 'elapsed_s': time.monotonic() - start,
    'patch': str(root / 'reference_diagnosis/strict_via_in_pin.patch'),
    'object_sha256': sha(obj), 'patched_binary_sha256': sha(output),
    'production_binary_sha256': sha(source.parent / 'install/OpenROAD/bin/openroad'),
    'shared_source_and_build_objects_modified': False, 'production_install_modified': False,
    'lto_workers': 4, 'gpu_enabled': False, 'scratch_directory': str(work),
    'version': subprocess.check_output([str(output), '-version'], text=True).strip()
}
(root / 'multi_analysis/strict_via_binary.json').write_text(json.dumps(report, indent=2) + '\n')
print(json.dumps(report), flush=True)
