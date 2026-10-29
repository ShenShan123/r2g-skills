"""Run the real staged shell entrypoint; fake only the physical checker."""
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil

ROOT=Path(__file__).resolve().parent
ORIGINAL=ROOT.parents[1]/'Typed-Executable-Hardware-Memory/r2g-skills/signoff-loop/tests/test_run_drc_checker_only.py'
SPEC=importlib.util.spec_from_file_location('original_drc_fixture',ORIGINAL)
F=importlib.util.module_from_spec(SPEC);SPEC.loader.exec_module(F)
F.SKILL=ROOT/'runtime-delta/signoff-loop'


def setup(tmp_path):
    items=F._setup(tmp_path)
    stub=items[3]/'klayout'
    text=stub.read_text().replace("echo '<report-database>'", "echo '<report-database><items>'").replace("echo '</report-database>'", "echo '</items></report-database>'")
    text=text.replace('<value>polygon: (0,0;1,1)</value>', '<item><values>\n<value>polygon: (0,0;1,1)</value>\n<value>extra field</value>\n</values></item>')
    stub.write_text(text)
    return items


def result(project):return json.loads((project/'drc/drc_result.json').read_text())


def test_complete_xml_true_item_count(tmp_path):
    skill,orfs,proj,bin_dir,_=setup(tmp_path)
    r=F._run_drc(tmp_path,skill,orfs,proj,bin_dir,{'STUB_VIOLS':'3'})
    assert r.returncode==0,r.stderr
    assert result(proj)['violations']==3
    assert not (tmp_path/'make_invocations').exists()


def test_nonzero_exit_cannot_accept_even_zero_xml(tmp_path):
    skill,orfs,proj,bin_dir,_=setup(tmp_path)
    stub=bin_dir/'klayout';stub.write_text(stub.read_text().replace('exit 0\n','exit 2\n'))
    r=F._run_drc(tmp_path,skill,orfs,proj,bin_dir)
    assert r.returncode==2
    assert result(proj)['status']!='clean'


def test_partial_xml_never_zero_clean(tmp_path):
    skill,orfs,proj,bin_dir,_=setup(tmp_path)
    stub=bin_dir/'klayout';stub.write_text(stub.read_text().replace("echo '</items></report-database>'", 'true'))
    F._run_drc(tmp_path,skill,orfs,proj,bin_dir)
    assert result(proj)['status']!='clean'


def test_pin_selects_independent_checker(tmp_path):
    skill,orfs,proj,bin_dir,_=setup(tmp_path)
    candidate=bin_dir/'new-klayout';shutil.copy2(bin_dir/'klayout',candidate)
    pin=tmp_path/'checker-pin.json';pin.write_text(json.dumps({'command':str(candidate),'files':{str(candidate):hashlib.sha256(candidate.read_bytes()).hexdigest()}}))
    r=F._run_drc(tmp_path,skill,orfs,proj,bin_dir,{'R2G_DRC_CHECKER_PIN_FILE':str(pin)})
    assert r.returncode==0,r.stderr
    assert result(proj)['status']=='clean'
    assert result(proj)['checker_pin_sha256']==hashlib.sha256(pin.read_bytes()).hexdigest()


def test_changed_dependency_refuses_before_checker(tmp_path):
    skill,orfs,proj,bin_dir,_=setup(tmp_path)
    candidate=bin_dir/'klayout';pin=tmp_path/'checker-pin.json';pin.write_text(json.dumps({'command':str(candidate),'files':{str(candidate):'0'*64}}))
    r=F._run_drc(tmp_path,skill,orfs,proj,bin_dir,{'R2G_DRC_CHECKER_PIN_FILE':str(pin)})
    assert r.returncode==76
    assert not (tmp_path/'klayout_invocations').exists()


def test_rule_reference_does_not_prove_deadlock(tmp_path):
    skill,orfs,proj,bin_dir,_=setup(tmp_path)
    stub=bin_dir/'klayout'
    stub.write_text(stub.read_text().replace('report=""','echo "ERROR at FreePDK45.lydrc:131"\nexit 2\nreport=""'))
    F._run_drc(tmp_path,skill,orfs,proj,bin_dir)
    assert result(proj)['status']=='failed'
    assert result(proj)['reason']=='checker_error_at_rule'
    assert result(proj)['last_logged_rule']=='FreePDK45.lydrc:131'
