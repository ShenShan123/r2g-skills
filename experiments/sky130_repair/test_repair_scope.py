from pathlib import Path
import unittest
from repair_scope import select, cancelled_container


class ScopeTests(unittest.TestCase):
    def setUp(self):
        self.decisions={'C03619__aes__family':'abandon','C03963__fpu__family':'candidate','C00001__old__family':'verified'}
        self.root=Path('/project/repair')
    def test_removes_only_abandoned_cases(self):
        cases=[dict(design=d) for d in self.decisions]
        self.assertEqual([c['design'] for c in select(cases,self.decisions)],list(self.decisions)[1:])
        self.assertEqual([c['design'] for c in select(cases,self.decisions,['C03963__fpu__family'])],['C00001__old__family'])
        with self.assertRaises(ValueError):select(cases,self.decisions,['C03619__aes__family'])
        with self.assertRaises(ValueError):select([dict(design='unknown')],self.decisions)
    def test_only_owned_abandoned_live_container_can_be_cancelled(self):
        info=dict(Name='s130repair_C03619_123',State=dict(Running=True),Mounts=[dict(Destination='/work',Source=str(self.root/'campaign/cases/C03619__aes__family/attempt_2/work'))])
        self.assertEqual(cancelled_container(info,self.decisions,self.root),'C03619__aes__family')
        for name in ['C03619__aes__family__gf180__p10','s130repair_C03963_123','unrelated']:
            self.assertIsNone(cancelled_container(dict(info,Name=name),self.decisions,self.root))
        self.assertIsNone(cancelled_container(dict(info,State=dict(Running=False)),self.decisions,self.root))
        self.assertIsNone(cancelled_container(dict(info,Mounts=[dict(Destination='/work',Source='/project/unrelated')]),self.decisions,self.root))


if __name__=='__main__':unittest.main()
