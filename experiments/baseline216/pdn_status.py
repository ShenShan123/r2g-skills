"""Progress + PDN-fallback audit for the sky130hd baseline on 216."""
import collections, glob, json, os, sys

root = sys.argv[1] if len(sys.argv) > 1 else "/proj/workarea/yangao/full_out"
rows = []
for f in glob.glob(os.path.join(root, "runs", "*", "result.json")):
    try:
        rows.append(json.load(open(f)))
    except Exception:
        pass

print(f"  harvested        : {len(rows)} / 2453")
st = collections.Counter(r.get("status") for r in rows)
print(f"  status           : {dict(st)}")

fb = [r for r in rows if r.get("pdn_fallback")]
nf = [r for r in rows if not r.get("pdn_fallback")]
print(f"  used PDN floor   : {len(fb)}")
if fb:
    s = collections.Counter(r.get("status") for r in fb)
    ok = s.get("pass", 0)
    print(f"    outcome        : {dict(s)}  -> rescued {ok}/{len(fb)} = {ok/len(fb):.0%}")
    bad = [r for r in fb if r.get("status") != "pass"]
    if bad:
        print(f"    still failing  : {dict(collections.Counter(r.get('failure_class') for r in bad))}")

# The floor must be invisible to designs that never hit PDN-0185.
leak = sum(1 for r in nf if r.get("core_floor_um") is not None)
print(f"  no-floor runs    : {len(nf)}, of which core_floor_um set: {leak}  (must be 0)")

loss = collections.Counter(r["failure_class"] for r in rows if r.get("failure_class"))
if loss:
    print("  loss reasons     :")
    for k, v in loss.most_common(8):
        print(f"      {v:5}  {k}")

real = [r for r in rows if r.get("clock_kind") == "real"
        and r.get("status") == "pass" and r.get("wns") is not None]
if real:
    met = sum(1 for r in real if r["wns"] >= 0)
    print(f"  real-clock pass  : {len(real)}, timing met {met} ({met/len(real):.0%}) at 5 ns")
ck = collections.Counter(r.get("clock_kind") or "unknown" for r in rows)
print(f"  clock kinds      : {dict(ck)}")
