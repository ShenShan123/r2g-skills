"""Exact frozen no-memory R2G host, Sol/high provider-only extension."""
import argparse
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parent
sys.path[:0]=[str(ROOT),str(ROOT/'code/memory/scripts'),str(ROOT/'code/memory')]

if __name__=='__main__':
    import experiment as E
    ap=argparse.ArgumentParser();ap.add_argument('--task',required=True);args=ap.parse_args()
    E.validate()
    import sol_install as I
    I.install_online()
    import t2a_batch as B
    B.episode(ROOT,args.task,'NB',1,'TEST')
