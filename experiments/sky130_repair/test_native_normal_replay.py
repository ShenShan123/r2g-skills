import json
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

import native_normal_replay as n


class NativeReplayTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.package = self.root / 'package'
        self.trials = self.root / 'normal_recipe_replays/C1/normal_replay'
        self.project = self.trials / 'normal'
        self.subject = self.trials.parent / 'source_subject'
        self.baseline = self.root / 'baseline'
        self.prior = self.root / 'prior_work'
        self.family = 'R1__family'
        self.design = 'C1__top__family'
        self.sources = {'top.v': '`include "coeffs.vh"\nmodule top(input a, output y); assign y=a; endmodule\n',
                        'coeffs.vh': '// original included header\n'}
        for root in (self.package / 'sources' / self.family,
                     self.project / 'rtl' / self.family, self.subject / 'rtl' / self.family):
            root.mkdir(parents=True)
            for name, text in self.sources.items(): (root / name).write_text(text)
        for root in (self.project / 'constraints', self.subject / 'constraints', self.baseline, self.prior):
            root.mkdir(parents=True, exist_ok=True)
            (root / 'constraint.sdc').write_text('create_clock -period 5 [get_ports clk]\n')
        self.prior_cfg = 'export DESIGN_NAME = top\nexport PLATFORM = sky130hd\nexport CORE_UTILIZATION = 50\nexport VERILOG_FILES = /work/rtl/top.v\nexport VERILOG_INCLUDE_DIRS = /work/rtl\nexport SDC_FILE = /work/constraint.sdc\n'
        (self.prior / 'config.mk').write_text(self.prior_cfg)
        (self.baseline / 'config.mk').write_text(self.prior_cfg.replace('/work/rtl', str(self.package / 'sources' / self.family)))
        (self.baseline / 'result.json').write_text(json.dumps({'design': self.design, 'status': 'fail'}))
        cfg = self.prior_cfg.replace('/work/rtl', str(self.subject / 'rtl' / self.family))
        cfg = n.set_mk(cfg, 'SDC_FILE', str(self.subject / 'constraints/constraint.sdc'))
        (self.subject / 'constraints/config.mk').write_text(cfg)
        self.bundle = self.root / 'selector_bundle'
        self.hook = self.bundle / 'signoff-loop/scripts/flow/orfs_hooks/pin_pdn_clearance.tcl'
        self.hook.parent.mkdir(parents=True)
        source_hook = Path(__file__).resolve().parents[2] / 'r2g-skills/signoff-loop/scripts/flow/orfs_hooks/pin_pdn_clearance.tcl'
        shutil.copy2(source_hook, self.hook)
        cfg = n.set_mk(cfg.replace(str(self.subject), str(self.project)), 'PRE_IO_PLACEMENT_TCL', str(self.hook))
        (self.project / 'constraints/config.mk').write_text(cfg)
        self.previous = dict(design=self.design, baseline=str(self.baseline), status='not_clean',
            baseline_hashes={name: n.sha(self.baseline / name) for name in ('config.mk', 'constraint.sdc', 'result.json')},
            attempts=[dict(strategy='source_normalization', status='completed', work=str(self.prior))])
        self.provenance = dict(design=self.design, family=self.family,
            source_files={self.family + '/' + name: n.sha(self.package / 'sources' / self.family / name) for name in self.sources},
            sdc_sha256=n.sha(self.baseline / 'constraint.sdc'), normalized_remote_config_sha256=n.sha(self.prior / 'config.mk'),
            source_normalization='remove included header only')
        n.save(self.project / 'source_provenance.json', self.provenance)
        self.plan = dict(mode='normal_unforced_independent_replay', forced_selection=False,
            key=dict(strategy='pin_pdn_clearance', platform='sky130hd'), selector_bundle=str(self.bundle),
            frozen_baseline_metadata_sha256=self.previous['baseline_hashes'],
            arms=[dict(case=self.design, project=str(self.project), subject=str(self.subject), role='B', source_repository=self.family,
                config_sha256=n.sha(self.project / 'constraints/config.mk'), source_provenance_sha256=n.sha(self.project / 'source_provenance.json'))])
        n.save(self.trials / 'plan.json', self.plan)
        n.save(self.trials / 'selection.json', dict(returncode=0, stdout='pin_pdn_clearance\tfloorplan\tdrc\n'))
        self.action = dict(status='applied', applied='pin_pdn_clearance', config_edits={'PRE_IO_PLACEMENT_TCL': str(self.hook)})
        self.write_action()

    def write_action(self):
        n.save(self.trials / 'application.json', dict(returncode=0, stdout=json.dumps(self.action)))

    def validate(self):
        return n.validate(self.trials, self.previous, self.package)

    def test_reviewed_real_hook_and_original_inputs_accepted(self):
        result = self.validate()
        self.assertEqual(result['source_files'], self.provenance['source_files'])
        self.assertFalse((self.project / 'execution.json').exists())

    def test_rejects_changed_original_source_even_with_untouched_project(self):
        (self.package / 'sources' / self.family / 'top.v').write_text('module top; endmodule')
        with self.assertRaisesRegex(ValueError, 'RTL/asset changed'): self.validate()

    def test_rejects_extra_physical_edit_even_if_plan_hash_is_updated(self):
        p = self.project / 'constraints/config.mk'
        p.write_text(p.read_text() + 'export CORE_UTILIZATION = 20\n')
        self.plan['arms'][0]['config_sha256'] = n.sha(p)
        n.save(self.trials / 'plan.json', self.plan)
        with self.assertRaisesRegex(ValueError, 'extra physical knobs'): self.validate()

    def test_rejects_original_clock_change(self):
        (self.project / 'constraints/constraint.sdc').write_text('create_clock -period 50 [get_ports clk]')
        with self.assertRaisesRegex(ValueError, 'clock constraints changed'): self.validate()

    def test_rejects_changed_recipe_hook(self):
        self.hook.write_text('# no physical repair\n')
        with self.assertRaisesRegex(ValueError, 'published hook differs'): self.validate()

    def test_rejects_forced_selection(self):
        self.plan['forced_selection'] = True
        n.save(self.trials / 'plan.json', self.plan)
        with self.assertRaisesRegex(ValueError, 'ordinary SKY130'): self.validate()

    def test_rejects_application_constraint_override(self):
        self.action['sdc_edits'] = ['relax period']
        self.write_action()
        with self.assertRaisesRegex(ValueError, 'ordinary application'): self.validate()

    def test_rejects_unfinished_prior_flow(self):
        self.previous['attempts'][0]['status'] = 'signoff_running'
        with self.assertRaisesRegex(ValueError, 'has not completed'): self.validate()

    def test_rejects_new_source_file_and_source_list_change(self):
        p = self.project / 'rtl' / self.family / 'extra.v'
        p.write_text('module extra; endmodule')
        with self.assertRaisesRegex(ValueError, 'file set differs'): self.validate()
        p.unlink()
        p = self.subject / 'constraints/config.mk'
        p.write_text(p.read_text().replace('top.v', 'coeffs.vh'))
        with self.assertRaisesRegex(ValueError, 'reviewed prior input'): self.validate()

    def test_materialization_keeps_original_bytes_and_rejects_duplicate_or_tamper(self):
        work = self.root / 'new_work'
        work.mkdir()
        with patch.object(n.shutil, 'disk_usage', return_value=shutil._ntuple_diskusage(200*1024**3, 0, 200*1024**3)):
            cfg, lock, project = n.materialize(self.trials, self.previous, self.package, work)
        self.addCleanup(lock.close)
        (work / 'config.mk').write_text(cfg)
        n.validate_execution(self.trials, self.previous, self.package, work)
        with self.assertRaises(BlockingIOError): n.materialize(self.trials, self.previous, self.package, work)
        lock.close()
        with self.assertRaisesRegex(ValueError, 'existing ordinary execution'):
            n.materialize(self.trials, self.previous, self.package, work)
        p = work / 'subject/rtl' / self.family / 'top.v'
        p.write_text('module top; endmodule')
        with self.assertRaisesRegex(ValueError, 'executed RTL/asset changed'):
            n.validate_execution(self.trials, self.previous, self.package, work)

    def test_campaign_uses_selected_project_and_preserves_failed_prior_receipt(self):
        import run_campaign as c
        out = self.root / 'campaign'
        target = out / 'cases' / self.design
        target.mkdir(parents=True)
        n.save(target / 'repair.json', self.previous)
        case = dict(design=self.design, run=str(self.baseline), category='frontend_header_as_source',
                    stage='1_1_yosys_canonicalize', drc=None, lvs=None, errors=[])
        launched = []
        def stop_before_compute(work, *args, **kwargs):
            n.validate_execution(self.trials, self.previous, self.package, work)
            launched.append(work)
            raise RuntimeError('test stops before compute')
        with patch.object(c, 'PACKAGE', self.package), patch.object(c, 'reviewed_source_pin_retry', return_value=True), \
             patch.object(c, 'source_repair', side_effect=AssertionError('must reuse selected input project')), \
             patch.object(c, 'podman', side_effect=stop_before_compute), \
             patch.object(n.shutil, 'disk_usage', return_value=shutil._ntuple_diskusage(200*1024**3, 0, 200*1024**3)):
            result = c.run_case(case, out, None, None, None)
        self.assertEqual(len(launched), 1)
        self.assertEqual(result['status'], 'error')
        self.assertTrue(result['baseline_unchanged'])
        self.assertEqual(result['attempts'][0]['strategy'], 'normal_recipe_pin_pdn_clearance')
        old = target / ('repair_before_policy_v' + str(c.POLICY_REVISION) + '.json')
        self.assertEqual(json.loads(old.read_text()), self.previous)
        self.assertEqual(json.loads((self.project / 'execution.json').read_text())['status'], 'error')


if __name__ == '__main__': unittest.main()
