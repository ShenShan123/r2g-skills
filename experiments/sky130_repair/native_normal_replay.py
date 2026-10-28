"""Use a prepared ordinary recipe inside an existing native campaign worker.

This adapter owns no worker pool and starts no subprocess. The campaign retains
its two-worker allocation, frozen flow, timeout and strict signoff path.
"""
import fcntl
import hashlib
import json
from pathlib import Path
import re
import shutil
import time

HOOK_SHA = '9338d25a0f60802dcd71ea0a40b011a96e45c2f03c63d91f1a2b648e3fae94b6'


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def assignments(text):
    # The ordinary CLI appends its edits. Make uses the last assignment.
    return dict(re.findall(r'^export\s+(\w+)\s*=\s*(.*)$', text, re.M))


def set_mk(text, key, value):
    text = re.sub(r'^export\s+' + re.escape(key) + r'\s*=.*\n?', '', text, flags=re.M)
    return text.rstrip() + '\nexport ' + key + ' = ' + value + '\n'


def save(path, value):
    tmp = path.with_suffix(path.suffix + '.tmp')
    tmp.write_text(json.dumps(value, indent=2) + '\n')
    tmp.replace(path)


def validate(trials, previous, package):
    """Bind the selected physical edit to the reviewed prior source-only run."""
    trials, package = Path(trials).resolve(), Path(package).resolve()
    plan = json.loads((trials / 'plan.json').read_text())
    if (plan.get('mode') != 'normal_unforced_independent_replay'
            or plan.get('forced_selection') is not False or len(plan.get('arms', [])) != 1
            or plan.get('key', {}).get('strategy') != 'pin_pdn_clearance'
            or plan['key'].get('platform') != 'sky130hd'):
        raise ValueError('requires one ordinary SKY130 PDN recipe arm')
    arm = plan['arms'][0]
    project, subject = Path(arm['project']).resolve(), Path(arm['subject']).resolve()
    if (not project.is_relative_to(trials) or not subject.is_relative_to(trials.parent)
            or arm.get('role') != 'B' or arm.get('case') != previous['design']):
        raise ValueError('prepared replay identity or project boundary differs')
    baseline = Path(previous['baseline'])
    if previous.get('status') != 'not_clean' or not previous.get('attempts'):
        raise ValueError('requires a completed unsuccessful prior run')
    last = previous['attempts'][-1]
    if last.get('strategy') != 'source_normalization' or last.get('status') != 'completed':
        raise ValueError('prior input normalization has not completed')
    for name, digest in previous['baseline_hashes'].items():
        if sha(baseline / name) != digest:
            raise ValueError('frozen baseline changed')
    if plan.get('frozen_baseline_metadata_sha256') != previous['baseline_hashes']:
        raise ValueError('prepared baseline identity differs')
    cfgpath = project / 'constraints/config.mk'
    provenance_path = project / 'source_provenance.json'
    if sha(cfgpath) != arm['config_sha256'] or sha(provenance_path) != arm['source_provenance_sha256']:
        raise ValueError('prepared configuration or provenance changed')
    provenance = json.loads(provenance_path.read_text())
    family = arm['source_repository']
    if provenance.get('design') != previous['design'] or provenance.get('family') != family:
        raise ValueError('source identity differs')
    files = provenance['source_files']
    if not files or set(files) != {str(p.relative_to(project / 'rtl')) for p in (project / 'rtl').rglob('*') if p.is_file()}:
        raise ValueError('prepared source file set differs')
    for rel, digest in files.items():
        for root in (project / 'rtl', subject / 'rtl', package / 'sources'):
            p = (root / rel).resolve()
            if not p.is_relative_to(root.resolve()) or sha(p) != digest:
                raise ValueError('original or prepared RTL/asset changed')
    for path in (project / 'constraints/constraint.sdc', subject / 'constraints/constraint.sdc', baseline / 'constraint.sdc'):
        if sha(path) != provenance['sdc_sha256']:
            raise ValueError('original clock constraints changed')
    prior_cfg = Path(last['work']) / 'config.mk'
    if sha(prior_cfg) != provenance['normalized_remote_config_sha256']:
        raise ValueError('audited prior input configuration changed')
    # Prior work carries a single family at /work/rtl. The adapter adds only a
    # family directory for provenance, preserving the ordered source list.
    normalized = prior_cfg.read_text().replace('/work/rtl', str(subject / 'rtl' / family))
    normalized = set_mk(normalized, 'SDC_FILE', str(subject / 'constraints/constraint.sdc'))
    if assignments(normalized) != assignments((subject / 'constraints/config.mk').read_text()):
        raise ValueError('source subject differs from reviewed prior input configuration')
    selected = json.loads((trials / 'selection.json').read_text())
    if selected['returncode'] != 0 or selected['stdout'].strip().split('\t') != ['pin_pdn_clearance', 'floorplan', 'drc']:
        raise ValueError('ordinary next selection differs')
    applied = json.loads((trials / 'application.json').read_text())
    if applied['returncode'] != 0:
        raise ValueError('ordinary recipe application failed')
    action = json.loads(applied['stdout'])
    hook = Path(plan['selector_bundle']) / 'signoff-loop/scripts/flow/orfs_hooks/pin_pdn_clearance.tcl'
    if (action.get('status') != 'applied' or action.get('applied') != 'pin_pdn_clearance'
            or action.get('config_edits') != {'PRE_IO_PLACEMENT_TCL': str(hook)}
            or action.get('sdc_edits') or action.get('env_edits') or sha(hook) != HOOK_SHA):
        raise ValueError('ordinary application or published hook differs')
    expected = set_mk(normalized.replace(str(subject), str(project)), 'PRE_IO_PLACEMENT_TCL', str(hook))
    if assignments(expected) != assignments(cfgpath.read_text()):
        raise ValueError('ordinary replay changed extra physical knobs')
    return dict(plan=plan, arm=arm, project=project, hook=hook, provenance=provenance,
                source_files=files, config=cfgpath.read_text())


def materialize(trials, previous, package, work):
    checked = validate(trials, previous, package)
    project, work = checked['project'], Path(work)
    lock = (project / 'execution.lock').open('a')
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    try:
        if (project / 'execution.json').exists():
            raise ValueError('existing ordinary execution requires terminal/live-process review')
        if shutil.disk_usage(work).free < 40 * 1024**3:
            raise ValueError('insufficient disk for physical replay')
        (work / 'subject').mkdir()
        shutil.copytree(project / 'rtl', work / 'subject/rtl')
        for rel, digest in checked['source_files'].items():
            if sha(work / 'subject/rtl' / rel) != digest:
                raise ValueError('executed source copy differs')
        shutil.copy2(checked['hook'], work / 'pin_pdn_clearance.tcl')
        shutil.copy2(project / 'constraints/constraint.sdc', work / 'constraint.sdc')
        cfg = checked['config'].replace(str(project), '/work/subject')
        cfg = set_mk(cfg, 'SDC_FILE', '/work/constraint.sdc')
        cfg = set_mk(cfg, 'PRE_IO_PLACEMENT_TCL', '/work/pin_pdn_clearance.tcl')
        evidence = dict(plan_sha256=sha(Path(trials) / 'plan.json'),
                        selection_sha256=sha(Path(trials) / 'selection.json'),
                        application_sha256=sha(Path(trials) / 'application.json'),
                        adapter_sha256=sha(Path(__file__)), hook_sha256=HOOK_SHA,
                        prior_work=previous['attempts'][-1]['work'],
                        source_files=checked['source_files'], sdc_sha256=checked['provenance']['sdc_sha256'],
                        source_normalization=checked['provenance']['source_normalization'],
                        mode='normal_unforced_independent_replay', owns_additional_worker=False,
                        scope='Original RTL/assets and SDC unchanged; reviewed source-list prerequisite plus normally selected physical recipe. Not an unbounded functional proof.')
        save(work / 'normal_recipe_evidence.json', evidence)
        save(work / 'source_audit.json', dict(status='original_and_executed_source_hashes_verified', **evidence))
        save(project / 'execution.json', dict(status='prepared_in_existing_campaign_worker',
             arm=checked['arm'], work=str(work), started_at=time.strftime('%FT%T%z'),
             native_evidence=str(work / 'normal_recipe_evidence.json'), native_evidence_sha256=sha(work / 'normal_recipe_evidence.json')))
        return cfg, lock, project
    except BaseException:
        lock.close()
        raise


def validate_execution(trials, previous, package, work):
    checked = validate(trials, previous, package)
    project, work = checked['project'], Path(work)
    expected = checked['config'].replace(str(project), '/work/subject')
    expected = set_mk(expected, 'SDC_FILE', '/work/constraint.sdc')
    expected = set_mk(expected, 'PRE_IO_PLACEMENT_TCL', '/work/pin_pdn_clearance.tcl')
    if (work / 'config.mk').read_text() != expected:
        raise ValueError('executed configuration changed')
    actual = {str(p.relative_to(work / 'subject/rtl')): sha(p)
              for p in (work / 'subject/rtl').rglob('*') if p.is_file()}
    if actual != checked['source_files']:
        raise ValueError('executed RTL/asset changed')
    if sha(work / 'constraint.sdc') != checked['provenance']['sdc_sha256'] or sha(work / 'pin_pdn_clearance.tcl') != HOOK_SHA:
        raise ValueError('executed SDC or physical hook changed')
    receipt = json.loads((project / 'execution.json').read_text())
    if sha(work / 'normal_recipe_evidence.json') != receipt['native_evidence_sha256']:
        raise ValueError('native replay evidence changed')


def record(project, **updates):
    path = Path(project) / 'execution.json'
    current = json.loads(path.read_text())
    current.update(updates)
    save(path, current)
