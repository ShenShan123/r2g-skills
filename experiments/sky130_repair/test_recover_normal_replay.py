import datetime
import hashlib
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from recover_normal_replay import remaining_seconds
import recover_normal_replay as recovery


def test_recovery_retains_original_deadline_including_downtime():
    manifest = {'started_at': '2026-10-27T01:54:30-0700', 'timeout': 21600}
    now = datetime.datetime.fromisoformat('2026-10-27T04:54:30-0700').timestamp()
    assert remaining_seconds(manifest, now) == 10800
    assert remaining_seconds(manifest, now+10800) == 0
    assert remaining_seconds(manifest, now+10860) == 0


def test_recovery_rejects_changed_budget_and_future_start():
    with pytest.raises(ValueError, match='budget'):
        remaining_seconds({'started_at': '2026-10-27T01:54:30-0700', 'timeout': 43200}, 0)
    with pytest.raises(ValueError, match='future'):
        remaining_seconds({'started_at': '2026-10-27T01:54:30-0700', 'timeout': 21600}, 0)


@pytest.fixture
def original_execution(tmp_path, monkeypatch):
    monkeypatch.setattr(recovery, 'AUX', tmp_path/'auxiliary')
    monkeypatch.setattr(recovery, 'HERE', tmp_path/'repo/experiments/repair')
    aux = recovery.AUX
    aux.mkdir()
    project = aux/'project'
    work = project/'ab_work'
    (project/'rtl/family').mkdir(parents=True)
    (project/'constraints').mkdir()
    (work/'subject/rtl/family').mkdir(parents=True)
    original = b'module subject(input a, output b); assign b=a; endmodule\n'
    (project/'rtl/family/subject.v').write_bytes(original)
    (work/'subject/rtl/family/subject.v').write_bytes(original)
    sdc = b'create_clock -period 5 [get_ports clk]\n'
    (project/'constraints/constraint.sdc').write_bytes(sdc)
    (work/'constraint.sdc').write_bytes(sdc)
    digest = lambda p: hashlib.sha256(p.read_bytes()).hexdigest()
    provenance = project/'source_provenance.json'
    provenance.write_text(json.dumps(dict(design='C00001__subject__family', family='family',
        source_files={'family/subject.v': digest(project/'rtl/family/subject.v')},
        sdc_sha256=digest(work/'constraint.sdc'))))
    key, name, _ = recovery.HOOKS['pin_pdn_clearance']
    hook = recovery.HERE.parents[1]/'r2g-skills/signoff-loop/scripts/flow/orfs_hooks'/name
    hook.parent.mkdir(parents=True)
    hook.write_bytes(b'# fixture hook\n')
    (work/name).write_bytes(hook.read_bytes())
    monkeypatch.setattr(recovery, 'HOOKS', {'pin_pdn_clearance': (key, name, digest(hook))})
    cfg = f'export VERILOG_FILES = {project}/rtl/family/subject.v\nexport SDC_FILE = {project}/constraints/constraint.sdc\nexport {key} = {hook}\n'
    (project/'constraints/config.mk').write_text(cfg)
    executed = cfg.replace(str(project), '/work/subject')
    executed = recovery.c.set_mk(executed, 'SDC_FILE', '/work/constraint.sdc')
    executed = recovery.c.set_mk(executed, key, '/work/'+name)
    (work/'config.mk').write_text(executed)
    (aux/'original_files.json').write_text('{}')
    overlay = aux/'checker_strict_verdict'
    overlay.mkdir()
    (overlay/'overlay_provenance.json').write_text('{"differences": []}')
    (aux/'expected_signoff_libraries.json').write_text('{}')
    # Checker executable identity is unrelated to these input-tampering cases.
    real_sha = recovery.c.sha
    def fixture_sha(p):
        p = Path(p)
        if p == aux/'environment/miniconda3/envs/eda/bin/magic':
            return '56300dc410b2ddb783612ef38edb6dde1879c4dc21edc6195575d507161e8ed6'
        if p == aux/'environment/miniconda3/envs/eda/bin/netgen':
            return '8349a7edd856773cbbc6be319bcf5cf3477f911d4300f6165735ee449dd0e7a8'
        return real_sha(p)
    monkeypatch.setattr(recovery.c, 'sha', fixture_sha)
    plan = dict(key={'strategy': 'pin_pdn_clearance'}, arms=[dict(project=str(project),
        case='C00001__subject__family', source_repository='family',
        source_provenance_sha256=digest(provenance), config_sha256=digest(project/'constraints/config.mk'))])
    manifest = dict(image=recovery.IMAGE, cores=8)
    recovery.validate_inputs(plan, manifest, work)
    return plan, manifest, work


@pytest.mark.parametrize('relative,message', [
    ('subject/rtl/family/subject.v', 'RTL/assets'),
    ('constraint.sdc', 'SDC'),
    ('config.mk', 'configuration'),
    ('pin_pdn_clearance.tcl', 'hook'),
])
def test_recovery_rejects_mutated_executed_inputs(original_execution, relative, message):
    plan, manifest, work = original_execution
    with (work/relative).open('ab') as f:
        f.write(b'changed\n')
    with pytest.raises(ValueError, match=message):
        recovery.validate_inputs(plan, manifest, work)
