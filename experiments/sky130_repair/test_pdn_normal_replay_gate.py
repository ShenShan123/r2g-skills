import sqlite3
import unittest
from select_pdn_independent_replay import require_promoted


class NormalReplayGateTests(unittest.TestCase):
    def test_missing_and_unpromoted_rows_rejected(self):
        conn=sqlite3.connect(':memory:')
        conn.execute('CREATE TABLE recipe_status(symptom_id TEXT, design_class TEXT, platform TEXT, strategy TEXT, status TEXT)')
        key=dict(symptom_id='symptom', design_class='logic/small', platform='sky130hd', strategy='pin_pdn_clearance')
        with self.assertRaises(ValueError):require_promoted(conn,key)
        for status in ('candidate','shadow','parked','promoted'):
            conn.execute('DELETE FROM recipe_status')
            conn.execute('INSERT INTO recipe_status VALUES(?,?,?,?,?)',(*key.values(),status))
            if status=='promoted':require_promoted(conn,key)
            else:
                with self.assertRaises(ValueError):require_promoted(conn,key)
        conn.close()


if __name__=='__main__':unittest.main()
