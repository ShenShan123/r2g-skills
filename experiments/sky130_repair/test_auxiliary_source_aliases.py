import hashlib
from pathlib import Path
import tempfile
import unittest
from run_auxiliary208 import restore_exported_aliases
from run_campaign import PACKAGE


class SourceAliasTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.work=self.root/'work';self.work.mkdir()
        (self.root/'package/sources/repo/a dir').mkdir(parents=True)
        self.cfg='export VERILOG_FILES = /work/_src/d0/top.sv\nexport VERILOG_INCLUDE_DIRS = /work/_src/d0\n'
        baseline=self.root/'baseline/case';baseline.mkdir(parents=True);(baseline/'config.mk').write_text(self.cfg)
        self.original=dict(run_name='case',alias_config_sha256=hashlib.sha256(self.cfg.encode()).hexdigest(),
                           source_aliases={'d0':str(PACKAGE/'sources/repo/a dir')})

    def restore(self,cfg=None):
        restore_exported_aliases(self.work,'case',self.cfg if cfg is None else cfg,None,original=self.original,root=self.root)

    def test_exact_original_container_alias_is_preserved(self):
        self.restore(self.cfg+'export POST_GLOBAL_PLACE_TCL = /work/feedthrough.tcl\n')
        self.assertEqual(str((self.work/'_src/d0').readlink()),str(PACKAGE/'sources/repo/a dir'))

    def test_changed_sources_are_rejected_before_creating_links(self):
        with self.assertRaisesRegex(ValueError,'declaration changed'):self.restore(self.cfg.replace('top.sv','other.sv'))
        self.assertFalse((self.work/'_src').exists())

    def test_escaping_alias_target_is_rejected(self):
        self.original['source_aliases']['d0']='/etc'
        with self.assertRaisesRegex(ValueError,'escapes'):self.restore()
        self.assertFalse((self.work/'_src').exists())


if __name__=='__main__':unittest.main()
