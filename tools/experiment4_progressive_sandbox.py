"""Build a filesystem and network isolated command for one generated converter."""
import json
from pathlib import Path


def command(python, source, config, toolchain):
    cfg = json.loads(Path(config).read_text())
    output = Path(cfg['output_dir']).resolve()
    output.mkdir(parents=True, exist_ok=True)
    env = Path(python).resolve().parents[1]
    cmd = ['bwrap', '--die-with-parent', '--unshare-net', '--unshare-pid', '--new-session',
           '--ro-bind', '/usr', '/usr', '--symlink', 'usr/bin', '/bin',
           '--symlink', 'usr/lib', '/lib', '--symlink', 'usr/lib64', '/lib64',
           '--proc', '/proc', '--dev', '/dev', '--tmpfs', '/tmp',
           '--clearenv', '--setenv', 'PATH', f'{env}/bin:{toolchain}/tools/install/yosys/bin:{toolchain}/tools/install/OpenROAD/bin:/usr/bin',
           '--setenv', 'HOME', '/tmp', '--setenv', 'OMP_NUM_THREADS', '4',
           '--setenv', 'OPENBLAS_NUM_THREADS', '4', '--setenv', 'PYTHONDONTWRITEBYTECODE', '1']
    paths = {env, Path(toolchain).resolve(), Path(source).resolve(), Path(config).resolve()}
    for key, value in cfg.items():
        if key == 'output_dir':
            continue
        for item in value if isinstance(value, list) else [value]:
            if isinstance(item, str) and item.startswith('/') and Path(item).exists():
                # Bind only exact config artifacts, never their parent campaign/home.
                paths.add(Path(item))
    for path in sorted(paths, key=lambda p: (len(p.parts), str(p))):
        if any(parent in paths for parent in path.parents):
            continue
        cmd += ['--ro-bind', str(path), str(path)]
    cmd += ['--bind', str(output), str(output), '--chdir', str(output),
            str(Path(python).resolve()), str(Path(source).resolve()), '--config', str(Path(config).resolve())]
    return cmd
