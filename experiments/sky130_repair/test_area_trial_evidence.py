import json,shutil,tempfile,unittest
from pathlib import Path
from area_trial_evidence import classify

class AreaEvidenceTests(unittest.TestCase):
 def test_actual_route_failure_and_negative_controls(self):
  source=Path('/tmp/sky130_area_subjects/C00550')
  with tempfile.TemporaryDirectory() as tmp:
   root=Path(tmp)
   for name in ('result.json','flow.log'):shutil.copy2(source/name,root/name)
   actual=classify(root,2);self.assertEqual(actual['classification'],'route_congestion_failure');self.assertFalse(actual['is_success'])
   for rc in (0,124,137,139,-9,-11,-15,143,True):self.assertEqual(classify(root,rc)['classification'],'inconclusive')
   log=(root/'flow.log').read_text()
   for message in ('\nSegmentation fault','\nKilled','\n[ERROR DRT-9999] other error', '\n[ERROR RSZ-0074] load tree failure'):
    (root/'flow.log').write_text(log+message);self.assertEqual(classify(root,2)['classification'],'inconclusive')
   (root/'flow.log').write_text(log)
   result=json.loads((root/'result.json').read_text());result['stage']='1_synth';(root/'result.json').write_text(json.dumps(result))
   self.assertEqual(classify(root,2)['classification'],'inconclusive')
 def test_missing_signoff_not_success(self):
  with tempfile.TemporaryDirectory() as tmp:
   root=Path(tmp);(root/'result.json').write_text(json.dumps({'status':'pass'}));(root/'flow.log').write_text('completed')
   r=classify(root,0);self.assertFalse(r['is_success']);self.assertEqual(r['classification'],'inconclusive')
if __name__=='__main__':unittest.main()
