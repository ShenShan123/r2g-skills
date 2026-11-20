#!/usr/bin/env python3
"""Freeze the Exp-4 validation sample (seed 20261107) from select_cells.tsv.

Strata follow the pilot500 README: <=1k, <=10k, >10k synthesized cells.
V1 (test sample): 25 of 27 large + 5 small + 13 medium (43). Two large designs are held
back so that a disjoint 6-design development set (2 per stratum) exists for converter
development (dev_designs.json); dev is drawn from the designs NOT in V1 with seed 20261108.
Pilot: one per stratum (first draw).
V2: 10 designs (2 small, 5 medium, 3 large) drawn from the V1 sample.
V3: 3 designs (one per stratum) drawn from V2.  V4: 5 designs (1 S, 2 M, 2 L) from V1.
"""
import json, random, sys, hashlib
src = sys.argv[1]; out = sys.argv[2]; dev_out = sys.argv[3]
rows = [l.split('\t') for l in open(src).read().splitlines() if l.strip()]
cells = {n: int(c) for n, c in rows}
strata = {'small': sorted(n for n, c in cells.items() if c <= 1000),
          'medium': sorted(n for n, c in cells.items() if 1000 < c <= 10000),
          'large': sorted(n for n, c in cells.items() if c > 10000)}
rng = random.Random(20261107)
v1 = {'small': rng.sample(strata['small'], 5), 'medium': rng.sample(strata['medium'], 13), 'large': rng.sample(strata['large'], 25)}
pilot = [v1['small'][0], v1['medium'][0], v1['large'][0]]
v2 = v1['small'][:2] + v1['medium'][:5] + v1['large'][:3]
v3 = [v1['small'][0], v1['medium'][0], v1['large'][0]]
v4 = [v1['small'][1], v1['medium'][1], v1['medium'][2], v1['large'][1], v1['large'][2]]
res = dict(seed=20261107, source=src, source_sha256=hashlib.sha256(open(src,'rb').read()).hexdigest(),
           strata_sizes={k: len(v) for k, v in strata.items()},
           v1={k: [dict(design=n, cells=cells[n]) for n in v] for k, v in v1.items()},
           v1_all=[n for k in ('small','medium','large') for n in v1[k]],
           pilot=pilot, v2=v2, v3=v3, v4=v4)
json.dump(res, open(out, 'w'), indent=1)
inv1 = set(res['v1_all'])
drng = random.Random(20261108)
dev = {k: drng.sample(sorted(n for n in strata[k] if n not in inv1), 2) for k in ('small','medium','large')}
devres = dict(seed=20261108, purpose='development designs for dataset converters; disjoint from sample.json v1_all',
              source=src, source_sha256=res['source_sha256'],
              strata={k: [dict(design=n, cells=cells[n]) for n in v] for k, v in dev.items()},
              designs=[n for k in ('small','medium','large') for n in dev[k]])
assert not set(devres['designs']) & inv1
json.dump(devres, open(dev_out, 'w'), indent=1)
print(json.dumps(devres['designs'], indent=1))
print(json.dumps({k: res[k] for k in ('strata_sizes','pilot','v2','v3','v4')}, indent=1))
