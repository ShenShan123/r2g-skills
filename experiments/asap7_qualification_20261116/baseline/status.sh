#!/usr/bin/env bash
# status.sh -- one-screen ASAP7 baseline status (run on 208; read-only on both servers)
timeout 60 ssh -n memlab203 'B=/data/yangao/asap7_baseline; L=$B/baseline.driver.log
echo "== 203 $(date +%H:%M)  done $(grep -cE " (pass|fail|error) " $L)/1106  pass $(grep -c " pass " $L)  fail $(grep -cE " (fail|error) " $L)  running $(ls $B/baseline_scratch 2>/dev/null | wc -l)  driver $(pgrep -fc "[o]rfs_spec.py.*baseline.select")"
echo "   load $(cut -d" " -f1-3 /proc/loadavg)  anon $(( $(timeout 5 grep "^anon " /sys/fs/cgroup/user.slice/user-$(id -u).slice/memory.stat | cut -d" " -f2) / 1073741824 ))G/300G  disk $(df -h /data | awk "NR==2{print \$4}") free  new30m $(find $B/baseline/runs -maxdepth 2 -name result.json -newermt "-30 minutes" 2>/dev/null | wc -l)"
grep -E "paused|resumed|HARD STOP|Traceback" $L | tail -2'
timeout 60 ssh -n memlab216 'B=/proj/workarea/yangao/asap7_baseline; L=$B/baseline.driver.log
echo "== 216 $(date +%H:%M)  done $(grep -cE " (pass|fail|error) " $L)/143  pass $(grep -c " pass " $L)  fail $(grep -cE " (fail|error) " $L)  containers $(podman ps --format "{{.Names}}" | grep -c asap7)  driver $(pgrep -fc "[o]rfs_spec.py.*baseline.select")"
echo "   load $(cut -d" " -f1-3 /proc/loadavg)  mem avail $(free -g | awk "NR==2{print \$7}")G  disk $(df -h /proj | awk "NR==2{print \$4}") free  ours $(ps -u yangao -o pcpu= | awk "{s+=\$1}END{printf \"%d%%\", s}") cpu"
echo "   pulled from 203: $(grep -c . $B/inbox203/pull_ledger.jsonl 2>/dev/null) ($(python3 -c "import json;print(round(sum(json.loads(l)[\"bytes\"] for l in open(\"$B/inbox203/pull_ledger.jsonl\"))/1e9,1))" 2>/dev/null)GB)  puller $(pgrep -fc "[p]ull_203.py")  $(grep -cvE " ok " $B/pull_203.log) non-ok lines"
echo "   signoff: $(ls $B/signoff/*/signoff.json 2>/dev/null | wc -l) done  signer $(pgrep -fc "[s]ignoff_batch.py")  $(python3 -c "
import json,glob,collections
c=collections.Counter()
for f in glob.glob(\"$B/signoff/*/signoff.json\"):
    r=json.load(open(f)); c[(r.get(\"drc\"),r.get(\"lvs\"))]+=1
print(dict(c))")"'
timeout 60 ssh -n memlab203 'B=/data/yangao/asap7_baseline/repairs; for a in $(ls $B); do [ -f $B/$a/driver.log ] || continue; echo "   arm203 $a: $(grep -cE " (pass|fail|error) " $B/$a/driver.log)/$(wc -l < $B/$a/select) pass $(grep -c " pass " $B/$a/driver.log) running $(ls $B/$a/scratch 2>/dev/null | wc -l)"; done; python3 -c "import json,time;h=json.load(open(\"/data/yangao/guardian/state/heartbeat.json\"));print(\"   guardian203\",h[\"t\"],\"anon\",round(h[\"cgroup_anon_gib\"] or 0),\"G disk\",h[\"disk_free_gib\"],\"paused\",h[\"paused_drivers\"])"; tail -2 /data/yangao/guardian/state/guardian.jsonl 2>/dev/null | cut -c1-160'
timeout 60 ssh -n memlab216 'B=/proj/workarea/yangao/asap7_baseline; python3 - <<PY
import json,glob,os
for a in sorted(glob.glob("$B/repairs/*/signoff")):
    s=[json.load(open(f)) for f in glob.glob(a+"/*/signoff.json")]
    print("   arm216 %-16s signed %3d  dual-clean %3d" % (a.split("/")[-2], len(s), sum(1 for x in s if x.get("drc")=="clean" and x.get("lvs")=="CORRECT")))
h=json.load(open("/proj/workarea/yangao/guardian/state/heartbeat.json"))
print("   guardian216", h["t"], "avail", h["mem_available_gib"], "G disk", h["disk_free_gib"], "largest", [g["gib"] for g in h["largest_groups"][:3]])
PY
tail -2 /proj/workarea/yangao/guardian/state/guardian.jsonl 2>/dev/null | cut -c1-160'
