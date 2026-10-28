import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'agent'))
from r2g_agent import frontend
from source_audit import audit_sources


class SourceAuditTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        base = Path(self.tmp.name)
        self.root, self.work = base/'original', base/'work'
        self.rtl = self.work/'rtl'
        self.root.mkdir(); self.rtl.mkdir(parents=True)

    def put(self, name, text):
        (self.root/name).write_text(text)
        (self.rtl/name).write_text(text)

    def audit(self, files=('top.v',), sources=('top.v',)):
        return audit_sources(self.root, self.rtl, self.work,
                             [self.root/f for f in files],
                             [self.rtl/f for f in sources], frontend)

    def test_existing_memory_path_and_data(self):
        self.put('top.v', 'module top; reg [7:0] m[0:1]; initial $readmemh("data.hex",m); endmodule\n')
        self.put('data.hex', '01\n42\n')
        p = self.rtl/'top.v'
        p.write_text(p.read_text().replace('"data.hex"', '"/work/rtl/data.hex"'))
        self.assertEqual(self.audit()['status'], 'source_preservation_checked')
        (self.rtl/'data.hex').write_text('00\n00\n')
        with self.assertRaisesRegex(ValueError, 'changed|differs'):
            self.audit()

    def test_logic_rewrite_rejected(self):
        self.put('top.v', 'module top(input a, output y); assign y=a; endmodule\n')
        (self.rtl/'top.v').write_text("module top(input a, output y); assign y=1'b0; endmodule\n")
        with self.assertRaisesRegex(ValueError, 'unapproved RTL'):
            self.audit()

    def test_header_must_remain_reachable(self):
        self.put('top.v', 'module top;\n`include "body.vh"\nendmodule\n')
        self.put('body.vh', 'wire x;\n')
        self.assertEqual(self.audit(('top.v','body.vh'))['removed_sources_retained_by_include'], ['body.vh'])
        self.put('top.v', 'module top; endmodule\n')
        with self.assertRaisesRegex(ValueError, 'not reachable'):
            self.audit(('top.v','body.vh'))

    def test_missing_data_is_not_accepted(self):
        self.put('top.v', 'module top; reg [7:0] m[0:1]; initial $readmemh("missing.hex",m); endmodule\n')
        with self.assertRaisesRegex(ValueError, 'unresolved'):
            self.audit()


if __name__ == '__main__':
    unittest.main()
