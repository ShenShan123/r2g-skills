import json
from pathlib import Path
import tempfile
import unittest
import run_campaign as c


class HostMappingTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.base=Path(self.tmp.name);self.package=self.base/'package';self.repo=self.package/'sources/repo'
        self.repo.mkdir(parents=True);self.work=self.base/'work';self.work.mkdir()
        self.cfg='export VERILOG_FILES = '+str(c.PACKAGE/'sources/repo/top.v')+'\nexport VERILOG_INCLUDE_DIRS = '+str(c.PACKAGE/'sources/repo')+'\n'

    def test_private_source_mapping_keeps_rtl_and_container_mount_unchanged(self):
        source='module top(input a, output y); assign y=a; endmodule\n';(self.repo/'top.v').write_text(source)
        package=c.PACKAGE;cfg,fixes=c.source_repair(self.work,self.cfg,source_package=self.package)
        self.assertEqual(c.PACKAGE,package)
        self.assertEqual((self.work/'rtl/top.v').read_text(),source)
        self.assertEqual(c.mk(cfg,'VERILOG_FILES'),'/work/rtl/top.v')
        self.assertEqual(c.mk(cfg,'VERILOG_INCLUDE_DIRS'),'/work/rtl')
        self.assertNotIn(str(self.package),cfg)

    def test_original_memory_bytes_and_source_evidence_survive_host_relocation(self):
        source='module top; reg [7:0] mem[0:1]; initial $readmemh("data.hex",mem); endmodule\n'
        (self.repo/'top.v').write_text(source);(self.repo/'data.hex').write_text('12\nab\n')
        cfg,fixes=c.source_repair(self.work,self.cfg,source_package=self.package)
        self.assertEqual((self.repo/'top.v').read_text(),source)
        self.assertEqual((self.work/'rtl/data.hex').read_bytes(),(self.repo/'data.hex').read_bytes())
        self.assertIn('"/work/rtl/data.hex"',(self.work/'rtl/top.v').read_text())
        audit=json.loads((self.work/'source_audit.json').read_text())
        self.assertEqual(audit['status'],'source_preservation_checked')
        self.assertFalse(audit['formal_equivalence'])

    def test_unmapped_source_is_rejected(self):
        with self.assertRaisesRegex(ValueError,'explicit_source_mapping'):
            c.source_repair(self.work,'export VERILOG_FILES = /work/_src/d0/top.v\n',source_package=self.package)


if __name__=='__main__':unittest.main()
