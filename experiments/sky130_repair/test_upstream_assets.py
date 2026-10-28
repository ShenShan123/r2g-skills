import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'agent'))
from r2g_agent import frontend
from source_audit import audit_sources
from upstream_assets import restore_assets


class UpstreamAssetTest(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        p=Path(self.tmp.name);self.root=p/'repo';self.rtl=p/'work/rtl';self.bundle=p/'assets'
        self.root.mkdir();self.rtl.mkdir(parents=True)
        source='module top; reg [7:0] m[0:1]; initial $readmemh("rom.hex",m); endmodule\n'
        (self.root/'top.v').write_text(source);(self.rtl/'top.v').write_text(source)
        asset=self.bundle/'repo/files/rom.hex';asset.parent.mkdir(parents=True);asset.write_bytes(b'01\n42\n')
        sha=lambda b:hashlib.sha256(b).hexdigest()
        self.manifest={'source_root':'repo','repository':{'commit':'frozen'},
          'source_identities':[{'files':[{'path':'top.v','sha256':sha(source.encode())}]}],
          'files':[{'path':'rom.hex','sha256':sha(asset.read_bytes()),'git_blob_sha1':hashlib.sha1(b'blob 6\0'+asset.read_bytes()).hexdigest()}],
          'reference_overrides':{'rom.hex':'rom.hex'}}
        self.save()

    def save(self):
        (self.bundle/'repo/manifest.json').write_text(json.dumps(self.manifest))

    def restore(self):
        return restore_assets(self.bundle,self.root,self.rtl,[self.root/'top.v'])

    def test_real_data_restoration_and_path_only_edit(self):
        restored,overrides,_=self.restore()
        p=self.rtl/'top.v';p.write_text(p.read_text().replace('"rom.hex"','"/work/rtl/rom.hex"'))
        result=audit_sources(self.root,self.rtl,self.rtl.parent,[self.root/'top.v'],[p],frontend,
                             restored=restored,overrides=overrides)
        self.assertEqual(result['status'],'source_preservation_checked')
        (self.rtl/'rom.hex').write_text('00\n00\n')
        with self.assertRaisesRegex(ValueError,'unverified|no original'):
            audit_sources(self.root,self.rtl,self.rtl.parent,[self.root/'top.v'],[p],frontend,
                          restored=restored,overrides=overrides)

    def test_source_version_mismatch_rejected(self):
        (self.root/'top.v').write_text('module top; endmodule\n')
        with self.assertRaisesRegex(ValueError,'frozen source'):self.restore()

    def test_modified_download_rejected(self):
        (self.bundle/'repo/files/rom.hex').write_text('00\n00\n')
        with self.assertRaisesRegex(ValueError,'content'):self.restore()

    def test_non_utf8_and_crlf_bytes_preserved(self):
        source=b'// original comment: \xf3\r\n'+(self.root/'top.v').read_bytes()
        (self.root/'top.v').write_bytes(source);(self.rtl/'top.v').write_bytes(source)
        self.manifest['source_identities'][0]['files'][0]['sha256']=hashlib.sha256(source).hexdigest();self.save()
        restored,overrides,_=self.restore()
        p=self.rtl/'top.v';p.write_bytes(source.replace(b'"rom.hex"',b'"/work/rtl/rom.hex"'))
        audit_sources(self.root,self.rtl,self.rtl.parent,[self.root/'top.v'],[p],frontend,
                      restored=restored,overrides=overrides)
        self.assertIn(b'\xf3\r\n',p.read_bytes())


if __name__=='__main__':unittest.main()
