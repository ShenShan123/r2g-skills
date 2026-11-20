#!/usr/bin/env python3
"""Resume an unchanged campaign with a separately recorded evaluator revision.

The candidate directory contains the frozen run_batch.py and measure.py alongside
the equivalent, optimized private scorer. Development still uses frozen sources.
Run without --run first to verify the original campaign freeze.
"""
import argparse
from pathlib import Path
import subprocess
import sys


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--evaluator-code", type=Path, required=True)
    args, campaign_args = parser.parse_known_args()
    root = args.root.resolve()
    frozen_code = root / "snapshot/experiments/exp4_harness"
    evaluator_code = args.evaluator_code.resolve()
    for name in ("run_batch.py", "measure.py", "container_runtime.py", "archive_worker.py"):
        if (frozen_code / name).read_bytes() != (evaluator_code / name).read_bytes():
            raise RuntimeError("Production infrastructure differs from freeze: " + name)
    sys.path.insert(0, str(frozen_code))
    import campaign

    original_run = subprocess.run

    def dispatch(command, *positional, **keywords):
        if (isinstance(command, (list, tuple)) and len(command) > 1
                and command[1] == str(frozen_code / "run_batch.py")):
            command = [command[0], str(evaluator_code / "run_batch.py"), *command[2:]]
        return original_run(command, *positional, **keywords)

    subprocess.run = dispatch
    sys.argv = [str(frozen_code / "campaign.py"), "--root", str(root), *campaign_args]
    try:
        campaign.main()
    finally:
        subprocess.run = original_run


if __name__ == "__main__":
    main()
