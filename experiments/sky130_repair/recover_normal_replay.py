"""Recover an orphaned ordinary replay, retaining inputs and its original deadline.

An existing container is adopted with docker wait. A missing container is never
assumed successful: the identical make command must return its actual status,
using the original checkpoints and only the remaining original flow budget.
"""
import argparse
import dataclasses
import datetime
import fcntl
import json
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE), str(HERE.parents[1] / 'agent')]
import run_campaign as c
from run_recipe_ab import verify_pdn_arm_inputs
from aux208_container_adapter import IMAGE

AUX = Path('/tmp/sky130_aux208')
HOOKS = {
    'lvs_port_feedthrough_buffer': ('POST_GLOBAL_PLACE_TCL', 'buffer_port_feedthroughs.tcl', '98baa3fb699c67fa72a195184f16fae8ad760f383459c54f339b10dc31b18b29'),
    'pin_pdn_clearance': ('PRE_IO_PLACEMENT_TCL', 'pin_pdn_clearance.tcl', '9338d25a0f60802dcd71ea0a40b011a96e45c2f03c63d91f1a2b648e3fae94b6'),
}


def remaining_seconds(manifest, now):
    if manifest['timeout'] != 21600:
        raise ValueError('unexpected original flow budget')
    start = datetime.datetime.fromisoformat(manifest['started_at']).timestamp()
    if start > now:
        raise ValueError('original start lies in the future')
    return max(0, math.floor(start + manifest['timeout'] - now))


def validate_inputs(plan, manifest, work):
    if manifest['image'] != IMAGE or manifest['cores'] != 8:
        raise ValueError('physical execution identity differs')
    arm = plan['arms'][0]
    project = Path(arm['project'])
    verify_pdn_arm_inputs(arm)
    if c.sha(project/'constraints/config.mk') != arm['config_sha256']:
        raise ValueError('prepared configuration changed')
    provenance = json.loads((project/'source_provenance.json').read_text())
    for rel, digest in provenance['source_files'].items():
        if c.sha(work/'subject/rtl'/rel) != digest:
            raise ValueError('executed RTL/assets changed')
    if c.sha(work/'constraint.sdc') != provenance['sdc_sha256']:
        raise ValueError('executed SDC changed')
    key, name, digest = HOOKS[plan['key']['strategy']]
    hook = HERE.parents[1]/'r2g-skills/signoff-loop/scripts/flow/orfs_hooks'/name
    if c.sha(hook) != digest or c.sha(work/name) != digest:
        raise ValueError('executed recipe hook changed')
    expected = (project/'constraints/config.mk').read_text().replace(str(project), '/work/subject')
    expected = c.set_mk(expected, 'SDC_FILE', '/work/constraint.sdc')
    expected = c.set_mk(expected, key, '/work/'+name)
    if (work/'config.mk').read_text() != expected:
        raise ValueError('executed recipe configuration changed')
    for rel, digest in json.loads((AUX/'original_files.json').read_text()).items():
        if c.sha(AUX/rel) != digest:
            raise ValueError('frozen physical input changed: '+rel)
    overlay = AUX/'checker_strict_verdict'
    for change in json.loads((overlay/'overlay_provenance.json').read_text())['differences']:
        if c.sha(overlay/change['file']) != change['after']:
            raise ValueError('checker overlay changed')
    for rel, digest in json.loads((AUX/'expected_signoff_libraries.json').read_text()).items():
        if c.sha(AUX/'environment/miniconda3/envs/eda'/rel) != digest:
            raise ValueError('signoff library changed')
    for rel, digest in {
        'bin/magic': '56300dc410b2ddb783612ef38edb6dde1879c4dc21edc6195575d507161e8ed6',
        'bin/netgen': '8349a7edd856773cbbc6be319bcf5cf3477f911d4300f6165735ee449dd0e7a8',
    }.items():
        if c.sha(AUX/'environment/miniconda3/envs/eda'/rel) != digest:
            raise ValueError('frozen checker executable changed')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--trials', type=Path, required=True)
    args = parser.parse_args()
    trials = args.trials.resolve()
    if not trials.is_relative_to(AUX):
        raise ValueError('outside auxiliary root')
    plan = json.loads((trials/'plan.json').read_text())
    if plan.get('mode') != 'normal_unforced_independent_replay' or plan.get('forced_selection') is not False or len(plan['arms']) != 1:
        raise ValueError('requires a single ordinary replay')
    manifests = list(trials.glob('execution_manifest*.json'))
    if len(manifests) != 1:
        raise ValueError('ambiguous execution manifest')
    manifest = json.loads(manifests[0].read_text())
    if manifest['plan_sha256'] != c.sha(trials/'plan.json'):
        raise ValueError('execution plan changed')
    slot = manifest['auxiliary_slot']
    if slot not in (0, 1):
        raise ValueError('unknown auxiliary slot')
    lock = (AUX/('worker.lock' if slot == 0 else 'review_worker.lock')).open('a')
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    arm = plan['arms'][0]
    project = Path(arm['project']).resolve()
    if not project.is_relative_to(trials):
        raise ValueError('project escapes trial')
    arm_lock = (project/'execution.lock').open('a')
    fcntl.flock(arm_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    receipt = project/'execution.json'
    record = json.loads(receipt.read_text())
    if record['status'] != 'flow_running' or record.get('recovery'):
        raise ValueError('not a recoverable orphan flow')
    work = Path(record['work']).resolve()
    if work != project/'ab_work':
        raise ValueError('unexpected execution work directory')
    validate_inputs(plan, manifest, work)
    image = json.loads(subprocess.check_output(['docker', 'image', 'inspect', IMAGE], text=True))[0]
    if image['Id'] != IMAGE:
        raise ValueError('frozen image changed')

    # Bind adoption to the exact recorded work mount and original token.
    matches = []
    ids = subprocess.check_output(['docker', 'ps', '-aq', '--filter', 'name=aux208_recipeab_'], text=True).split()
    for cid in ids:
        info = json.loads(subprocess.check_output(['docker', 'inspect', cid], text=True))[0]
        mounts = [m for m in info['Mounts'] if m['Destination'] == '/work']
        if len(mounts) == 1 and Path(mounts[0]['Source']).resolve() == work:
            token = info['Config']['Labels'].get('r2g.aux208.run', '')
            if not re.fullmatch(r'recipeab_\d+', token) or info['Image'] != IMAGE or info['HostConfig']['NanoCpus'] != 8000000000:
                raise ValueError('unexpected container identity')
            if Path('/proc/'+token.split('_')[-1]).exists():
                raise ValueError('original executor still exists; do not adopt')
            matches.append((cid, info))
    if len(matches) > 1:
        raise ValueError('multiple containers for one arm')
    if shutil.disk_usage(AUX).free < 40*1024**3:
        raise ValueError('insufficient free disk')
    recovery = dict(at=time.strftime('%FT%T%z'), original_execution=json.loads(json.dumps(record)),
                    original_execution_sha256=c.sha(receipt), original_manifest_sha256=c.sha(manifests[0]),
                    runner_sha256=c.sha(Path(__file__)), remaining_flow_seconds=remaining_seconds(manifest, time.time()),
                    mode='adopt_live_container' if matches else 'resume_identical_make_from_original_checkpoints',
                    retained_checkpoint_sha256={str(p.relative_to(work)): c.sha(p) for p in (work/'results').rglob('*')
                        if not matches and p.is_file()},
                    inputs_verified=True, elapsed_downtime_counts_against_original_budget=True)
    recovery_path = project/'recovery.json'
    if recovery_path.exists():
        raise ValueError('existing recovery evidence; inspect before continuing')
    c.save(recovery_path, recovery)
    record['recovery'] = str(recovery_path)
    c.save(receipt, record)
    token = 'recovery_'+str(os.getpid())
    os.environ.update(R2G_AUX208_ROOT=str(AUX), R2G_AUX208_TOKEN=token,
                      PATH=str(AUX/'tools/bin')+os.pathsep+os.environ['PATH'])
    c.HERE = AUX/'tools'
    driver = c.module('recovered_driver', AUX/'tools/orfs_spec.py')
    drc = c.module('recovered_drc', AUX/'tools/signoff_pass.py')
    lvs = c.module('recovered_lvs', AUX/'tools/lvs130.py')
    lvs.R2G = AUX/'checker_strict_verdict'
    try:
        remaining = remaining_seconds(manifest, time.time())
        if matches:
            cid = matches[0][0]
            if remaining == 0:
                subprocess.run(['docker', 'rm', '-f', cid], check=True, capture_output=True)
                rc = 124
            else:
                try:
                    waited = subprocess.run(['docker', 'wait', cid], capture_output=True, text=True, timeout=remaining)
                except subprocess.TimeoutExpired:
                    subprocess.run(['docker', 'rm', '-f', cid], check=True, capture_output=True)
                    rc = 124
                else:
                    if waited.returncode != 0 or not re.fullmatch(r'\d+\s*', waited.stdout):
                        raise ValueError('lost actual adopted container exit status')
                    rc = int(waited.stdout)
        elif remaining == 0:
            rc = 124
        else:
            original_log = project/'flow_before_recovery.log'
            shutil.copy2(work/'flow.log', original_log)
            rc = c.podman(work, 'finish_'+project.name,
                          'cd "$ORFS_ROOT/flow" && make DESIGN_CONFIG=/work/config.mk WORK_HOME=/work',
                          work/'flow.recovery.log', timeout=remaining)
            with (work/'flow.log').open('wb') as out:
                out.write(original_log.read_bytes())
                out.write(b'\n[recovery: identical make resumed; original stdout retained above]\n')
                out.write((work/'flow.recovery.log').read_bytes())
        # Never infer a return code from presence of final GDS/checkpoint files.
        recovery.update(flow_returncode=rc, flow_observed_terminal_at=time.strftime('%FT%T%z'))
        c.save(recovery_path, recovery)
        validate_inputs(plan, manifest, work)
        run = project/'physical_run'
        driver.harvest(work, run)
        result = driver.Result(design=arm['case'], platform='sky130hd', period='5')
        result.seconds = round(time.time()-datetime.datetime.fromisoformat(manifest['started_at']).timestamp(), 1)
        result.stage = driver.last_stage(work)
        result.status = 'pass' if rc == 0 and (run/'6_final.gds').is_file() else 'fail'
        if rc == 124:
            result.failure_class = 'timeout'
        driver.read_metrics(work, result)
        c.save(run/'result.json', dataclasses.asdict(result))
        record.update(flow_returncode=rc, run=str(run), flow_seconds=result.seconds)
        if result.status == 'pass':
            record['status'] = 'signoff_running'
            c.save(receipt, record)
            drc.one_and_record(run, project/'drc_projects', c.TIMEOUT, 'sky130hd')
            lvs.one_and_record(run, project/'lvs_projects', c.TIMEOUT, 'sky130hd')
            record['strict_signoff'] = c.inspect_run(run)
        record.update(status='completed', wall_seconds=round(time.time()-datetime.datetime.fromisoformat(manifest['started_at']).timestamp(), 1),
                      recovery_sha256=c.sha(recovery_path))
        c.save(receipt, record)
        print(json.dumps(record), flush=True)
    finally:
        ids = subprocess.check_output(['docker', 'ps', '-aq', '--filter', 'label=r2g.aux208.run='+token], text=True).split()
        if ids:
            subprocess.run(['docker', 'rm', '-f', *ids], check=True, capture_output=True)


if __name__ == '__main__':
    main()
