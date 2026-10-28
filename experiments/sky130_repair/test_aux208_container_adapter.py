import tempfile
import unittest
from pathlib import Path

from aux208_container_adapter import IMAGE, ORIGINAL, translated


class AuxiliaryAdapterTest(unittest.TestCase):
    def test_maps_frozen_sources_read_only_and_preserves_container_paths(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);(root/'work').mkdir();(root/'package').mkdir()
            conda=root/'environment/miniconda3/envs/eda';conda.mkdir(parents=True)
            args=['run','--rm','--name','case1','--memory=64g','--memory-swap=64g',
                  '-v',str(root/'work')+':/work:Z',
                  '-v',ORIGINAL+'/rtl-certified-handoff-0002:'+ORIGINAL+'/rtl-certified-handoff-0002:ro,Z',
                  '-v',ORIGINAL+'/miniconda3/envs/eda:'+ORIGINAL+'/miniconda3/envs/eda:ro,Z',
                  '-e','NUM_CORES=8','localhost/r2g-orfs:26Q3','bash','-lc','unchanged checker command']
            actual=translated(args,root,'test1')
            self.assertIn('--cpus=8',actual);self.assertIn(IMAGE,actual)
            self.assertIn('aux208_test1_case1',actual)
            self.assertIn(str(root/'work')+':/work',actual)
            self.assertIn(str(root/'package')+':'+ORIGINAL+'/rtl-certified-handoff-0002:ro',actual)
            self.assertIn(str(conda)+':'+ORIGINAL+'/miniconda3/envs/eda:ro',actual)
            self.assertEqual(actual[-3:],['bash','-lc','unchanged checker command'])

    def test_rejects_foreign_mounts_writable_inputs_and_unreviewed_image(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);(root/'package').mkdir()
            (root/'escape').symlink_to('/etc',target_is_directory=True)
            for mount in ('/etc:/outside:ro',str(root/'escape')+':/outside:ro',
                          ORIGINAL+'/rtl-certified-handoff-0002:/rtl:Z'):
                with self.subTest(mount=mount),self.assertRaises(ValueError):
                    translated(['run','-v',mount,'r2g-orfs:26Q3','true'],root,'test1')
            with self.assertRaises(ValueError):translated(['run','ubuntu:latest','true'],root,'test1')
            with self.assertRaises(ValueError):translated(['run','--privileged','r2g-orfs:26Q3','true'],root,'test1')

    def test_removal_is_namespaced_and_never_accepts_all_containers(self):
        self.assertEqual(translated(['rm','-f','ng_123'],Path('/tmp/aux'),'case1'),
                         ['rm','-f','aux208_case1_ng_123'])
        for args in (['rm','-f','--all'],['rm','-f','a','b'],['stop','somebody_else']):
            with self.subTest(args=args),self.assertRaises(ValueError):translated(args,Path('/tmp/aux'),'case1')


if __name__=='__main__':unittest.main()
