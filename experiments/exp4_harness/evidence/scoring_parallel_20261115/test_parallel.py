import sys,unittest
sys.path.insert(0,"/private/exp4_harness")
import scoring,test_scoring
original=scoring.score_design
test_scoring.score_design=lambda *args,**kwargs:original(*args,**kwargs,workers=4)
result=unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromModule(test_scoring))
raise SystemExit(not result.wasSuccessful())
