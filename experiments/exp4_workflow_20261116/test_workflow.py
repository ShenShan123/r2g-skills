"""Check the production wrapper's verdict, failure evidence and tool registration."""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

HERE = Path(__file__).resolve().parent


def load(name):
    spec = importlib.util.spec_from_file_location(name, HERE / (name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


workflow = load("generate_and_validate")
runner = load("runner_r2g")


class WorkflowTest(unittest.TestCase):
    def setUp(self):
        self.scratch = tempfile.TemporaryDirectory()
        self.addCleanup(self.scratch.cleanup)
        self.root = Path(self.scratch.name)
        self.runtime = self.root / "runtime"
        (self.runtime / "tools").mkdir(parents=True)
        self.design = self.root / "design"
        self.platform = self.root / "platform"
        self.output = self.root / "output"
        self.design.mkdir()
        self.platform.mkdir()

    def convert(self, kind, content):
        (self.runtime / f"convert_{kind}.sh").write_text("#!/bin/bash\nset -eu\n" + content)

    def verifier(self, failed=0):
        (self.runtime / "tools/verify_graph_dataset.py").write_text(
            "import json, os, sys\n"
            "assert sys.argv[-2:] == ['--not-applicable', 'irdrop,signoff']\n"
            "assert os.environ['R2G_DEF'].endswith('/design/6_final.def')\n"
            "assert os.environ['R2G_JOBS'] == '1'\n"
            f"report = {{'passed': 2, 'failed': {failed}, 'results': [{{'check': 'test', 'ok': {not bool(failed)}}}]}}\n"
            "open(sys.argv[sys.argv.index('--json') + 1], 'w').write(json.dumps(report))\n"
            f"sys.exit({bool(failed)!r})\n")

    def run_workflow(self, kind):
        return workflow.generate_and_validate(kind, self.design, self.platform, self.output,
                                              runtime=self.runtime)

    def test_graph_runs_scoped_verifier_and_preserves_report(self):
        self.convert("graph", 'mkdir -p "$3/project"\n')
        self.verifier()
        result = self.run_workflow("graph")
        self.assertEqual(result["status"], "passed")
        self.assertEqual(result["declared_not_applicable"], ["irdrop", "signoff"])
        self.assertEqual(json.loads((self.output / "validation.json").read_text()), result)

    def test_graph_validation_failure_is_not_silenced(self):
        self.convert("graph", 'mkdir -p "$3/project"\n')
        self.verifier(failed=1)
        result = self.run_workflow("graph")
        self.assertEqual(result["status"], "failed")
        self.assertEqual(workflow.summary(result)["failed_checks"], 1)
        self.assertEqual(workflow.summary(result)["failures"][0]["check"], "test")

    def test_generation_failure_does_not_reuse_old_validation(self):
        self.convert("graph", 'mkdir -p "$3/project"\n')
        self.verifier()
        self.assertEqual(self.run_workflow("graph")["status"], "passed")
        self.convert("graph", "echo generator-failed\nexit 7\n")
        result = self.run_workflow("graph")
        self.assertEqual(result["generation"]["exit_code"], 7)
        self.assertEqual(result["status"], "failed")
        self.assertNotIn("verifier", result)
        self.assertTrue((self.output / "validation.json").is_file())

    def test_image_accepts_production_verifier_schema(self):
        self.convert("image", 'mkdir -p "$3/_r2g"\n'
                     "printf '%s' '{\"status\":\"ok\",\"passed\":3,\"total\":3,\"results\":[]}' > \"$3/_r2g/verify.json\"\n")
        result = self.run_workflow("image")
        self.assertEqual(result["status"], "passed")
        self.assertTrue(result["validation"]["included_in_generation"])
        self.assertEqual(workflow.summary(result)["failed_checks"], 0)

    def test_generation_without_verifier_report_fails(self):
        self.convert("image", "true\n")
        self.assertEqual(self.run_workflow("image")["status"], "failed")

    def test_explicit_tool_and_kind_specific_entrypoint(self):
        self.assertEqual([item["name"] for item in runner.DatasetTools.specs()],
                         ["generate_and_validate", "shell"])
        for kind in ("graph", "image"):
            prompt = runner.dataset_system_prompt({"task_kind": kind})
            self.assertIn(f"/r2g/generate_{kind}.sh", prompt)
            self.assertIn("Do not reimplement", prompt)


if __name__ == "__main__":
    unittest.main()
