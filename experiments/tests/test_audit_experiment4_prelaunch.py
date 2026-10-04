import importlib.util
import json
from pathlib import Path


REPO = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location('audit_experiment4_prelaunch', REPO / 'experiments/audit_experiment4_prelaunch.py')
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(MODULE)


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value))


def test_semantic_validation_requires_all_core_groups_and_label_coverage(tmp_path):
    root = tmp_path / 'evidence'
    feature = tmp_path / 'feature.py'
    feature.write_text('pass\n')
    groups = {name: {'PASS': 24} for name in MODULE.SEMANTIC_GROUPS}
    coverage = {name: {'report_expected': 1, 'missing': 0, 'incorrect': 0, 'unparsed_blocks': 0}
                for name in MODULE.REQUIRED_LABEL_COVERAGE}
    write_json(root / 'audit_v0_3_final/aggregate.json', {
        'planned': 24, 'finished': 24, 'oracle_errors': [], 'failures_and_unassessable': [],
        'group_counts': {'r2g-geometry-fix': groups},
        'label_coverage_route_stage': {'r2g-geometry-fix': coverage},
    })
    write_json(root / 'validation_plan.json', {'feature_patch_sha256': MODULE.sha256_file(feature)})
    write_json(root / 'geometry_validation_report.json', {'change_scope_counts': {'PASS': 96}})
    assert MODULE.semantic_validation_errors(root, feature) == []
    groups['numeric'] = {'FAIL': 1, 'PASS': 23}
    write_json(root / 'audit_v0_3_final/aggregate.json', {
        'planned': 24, 'finished': 24, 'oracle_errors': [], 'failures_and_unassessable': [],
        'group_counts': {'r2g-geometry-fix': groups},
        'label_coverage_route_stage': {'r2g-geometry-fix': coverage},
    })
    assert any('numeric' in error for error in MODULE.semantic_validation_errors(root, feature))


def test_confirmatory_cohort_checks_inventory_and_similarity(tmp_path):
    inventory = tmp_path / 'inventory.json'
    rows = [{'task_id': str(index), 'exclusion_reasons': []} for index in range(31)]
    write_json(inventory, {'schema_version': 'experiment4-unseen-inventory-2.0', 'candidates': rows})
    cohort = {
        'status': 'frozen_confirmatory',
        'selection_policy': {'uses_graph_outputs': False, 'source_group_disjoint_across_all_splits': True,
                             'structural_near_duplicate_disjoint_across_all_splits': True,
                             'near_duplicate_threshold': 0.5},
        'source': {'inventory': str(inventory), 'inventory_sha256': MODULE.sha256_file(inventory)},
        'independence_audit': {'maximum_similarity': 0.2, 'pair_count': 465},
        'splits': {'canary': rows[:1], 'development': rows[1:7], 'hidden_test': rows[7:]},
    }
    assert MODULE.confirmatory_cohort_errors(cohort, tmp_path / 'cohort.json') == []
    cohort['independence_audit']['maximum_similarity'] = 0.5
    assert any('similarity' in error for error in MODULE.confirmatory_cohort_errors(cohort, tmp_path / 'cohort.json'))
