from pathlib import Path
import time,shutil,json
src=Path("/home/yangao/exp4_harness_20261115_203/runs/deepseek_r2g/s1/production/C03137__ultrasonicPWMgenerator_v1_0__fpga__gf180__p10/score")
dst=Path("/tmp/exp4_scoring_speed_validation_20261115/large/formal_original")
dst.mkdir(exist_ok=True)
while not (src/"score.json").exists():time.sleep(5)
shutil.copy2(src/"evaluate.log",dst/"evaluate.log")
shutil.copy2(src/"score.json",dst/"score.json")
