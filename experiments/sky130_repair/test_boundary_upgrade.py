import json
import os
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path


class BoundaryUpgradeTest(unittest.TestCase):
    def run_fixture(self, active):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);(root/'tools').mkdir();case=root/'campaign/cases/done';case.mkdir(parents=True)
            (case/'repair.json').write_text('{}');(root/'canary.pid').write_text('0')
            if active:
                p=root/'campaign/cases/running';p.mkdir()
                (p/'progress.json').write_text(json.dumps({'attempts':[{'status':'flow_running'}]}))
            worker=root/'tools/run_campaign_goal.py'
            worker.write_text('import time\nwhile True: time.sleep(.1)\n')
            supervisor=root/'tools/continue_campaign_goal.py'
            supervisor.write_text('''import sys,subprocess
from pathlib import Path
r=Path(__file__).resolve().parent.parent
if '--old' in sys.argv:
 p=subprocess.Popen([sys.executable,str(r/'tools/run_campaign_goal.py')])
 (r/'worker.pid').write_text(str(p.pid))
 sys.exit(p.wait())
else:(r/'replacement.marker').write_text('started')
''')
            old=subprocess.Popen([sys.executable,str(supervisor),'--old'],start_new_session=True)
            upgrade=None
            try:
                for _ in range(100):
                    if (root/'worker.pid').exists():break
                    time.sleep(.02)
                worker_pid=int((root/'worker.pid').read_text())
                script=Path(__file__).with_name('upgrade_campaign_at_boundary.py')
                upgrade=subprocess.Popen([sys.executable,str(script),'--root',str(root),
                    '--worker-pid',str(worker_pid),'--supervisor-pid',str(old.pid),
                    '--wait-for-receipt','done'],stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
                if active:
                    time.sleep(.5)
                    self.assertIsNone(upgrade.poll());self.assertIsNone(old.poll())
                    self.assertFalse((root/'replacement.marker').exists())
                    self.assertNotEqual(Path(f'/proc/{worker_pid}/stat').read_text().rsplit(')',1)[1].split()[0],'T')
                else:
                    stdout,stderr=upgrade.communicate(timeout=5)
                    self.assertEqual(upgrade.returncode,0,(stdout,stderr))
                    self.assertIn('safe_boundary_verified',stdout)
                    self.assertIn('replacement_launched',stdout)
                    old.wait(timeout=2)
                    for _ in range(100):
                        if (root/'replacement.marker').exists():break
                        time.sleep(.02)
                    self.assertTrue((root/'replacement.marker').exists())
            finally:
                if upgrade and upgrade.poll() is None:upgrade.terminate();upgrade.communicate(timeout=3)
                try:os.killpg(old.pid,signal.SIGKILL)
                except ProcessLookupError:pass
                old.wait(timeout=3)

    def test_completed_design_boundary_can_upgrade(self):self.run_fixture(False)
    def test_in_flight_work_prevents_upgrade(self):self.run_fixture(True)


if __name__=='__main__':unittest.main()
