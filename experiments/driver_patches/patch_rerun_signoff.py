import pathlib
p = pathlib.Path("/proj/workarea/yangao/rerun_baseline.sh")
s = p.read_text()

old = '''  say "  $P 结束 rc=$rc 用时 $(( (t1-t0)/60 ))min 产出 ${n} 规格缺失 ${miss:-0}"
  rm -rf "${OUT}_s" 2>/dev/null
done'''
new = '''  say "  $P flow 结束 rc=$rc 用时 $(( (t1-t0)/60 ))min 产出 ${n} 规格缺失 ${miss:-0}"
  rm -rf "${OUT}_s" 2>/dev/null

  # ---- SIGNOFF. Without this the campaign produces no verdicts at all. -------
  #
  # The flow writes result.json with status/stage/cells; drc_status and
  # lvs_status are written by the SIGNOFF pass, and rerun_tally.py counts those.
  # An earlier version of this chain went flow -> tally and would have graded
  # 1249 x 3 designs as "no verdict, therefore fail" after 4.3 days.
  #
  # The scripts differ by platform because the LVS TOOL does:
  #   nangate45         signoff45.py      DRC + LVS together, LVS via klayout
  #                                       and r2g's FreePDK45.lylvs. netgen has
  #                                       no setup for this platform.
  #   gf180, sky130hd   signoff_pass.py   DRC only -- it says so: "LVS is NOT
  #                                       attempted"
  #                   + lvs130.py         LVS via r2g's run_netgen_lvs.sh
  #
  # Both of the latter write their verdict back into result.json, which is what
  # the tally reads. Their own json output is a per-pass snapshot and must not
  # be read as the corpus: the pilot's netgen_lvs.json holds 1475 rows against
  # 2497 designs (the pre-filter drops designs with no layout) and reading it as
  # the LVS truth undercounted sky130hd by 669.
  t2=$(date +%s)
  if [ "$P" = nangate45 ]; then
    python3 -I "$D/signoff45.py" \\
        --runs "$OUT/runs" --projroot "$D/rerun_${P}_proj" \\
        --out "$OUT/signoff.json" --workers "$SIGNOFF_WORKERS" \\
        --timeout "$SIGNOFF_TIMEOUT" \\
        > "$D/rerun_${P}_${STAMP}.signoff.log" 2>&1
    say "  $P 签核(DRC+LVS) rc=$? 用时 $(( ($(date +%s)-t2)/60 ))min"
  else
    python3 -I "$D/signoff_pass.py" \\
        --runs "$OUT/runs" --projroot "$D/rerun_${P}_drcproj" \\
        --out "$OUT/signoff.json" --platform "$P" \\
        --workers "$SIGNOFF_WORKERS" --timeout "$SIGNOFF_TIMEOUT" \\
        > "$D/rerun_${P}_${STAMP}.drc.log" 2>&1
    say "  $P DRC rc=$? 用时 $(( ($(date +%s)-t2)/60 ))min"
    t3=$(date +%s)
    python3 -I "$D/lvs130.py" \\
        --runs "$OUT/runs" --projroot "$D/rerun_${P}_lvsproj" \\
        --out "$OUT/netgen_lvs.json" --platform "$P" \\
        --workers "$SIGNOFF_WORKERS" --timeout "$SIGNOFF_TIMEOUT" \\
        > "$D/rerun_${P}_${STAMP}.lvs.log" 2>&1
    say "  $P LVS rc=$? 用时 $(( ($(date +%s)-t3)/60 ))min"
  fi

  # A campaign with no verdicts is the failure this section exists to prevent,
  # so check rather than assume: count result.json rows carrying drc_status.
  graded=$(python3 -I - <<PY
import glob, json
n = 0
for f in glob.glob("$OUT/runs/*/result.json"):
    try:
        if json.load(open(f)).get("drc_status"):
            n += 1
    except Exception:
        pass
print(n)
PY
)
  say "  $P 带 DRC 判决的设计: ${graded} / ${n}"
  if [ "${graded:-0}" = 0 ]; then
    say "!! $P 签核没有产生任何判决 -- 停止，后面的平台不要再跑"
    exit 1
  fi
done'''
assert s.count(old) == 1, "锚点不唯一"
s = s.replace(old, new)

# signoff workers: the DRC/LVS checkers are not the flow, and the hard rule
# about >100K-cell LVS concurrency applies to them
old2 = "STAGE_TIMEOUT=${STAGE_TIMEOUT:-21600}"
new2 = '''STAGE_TIMEOUT=${STAGE_TIMEOUT:-21600}
SIGNOFF_WORKERS=${SIGNOFF_WORKERS:-8}     # not 12: a >100K-cell LVS takes
                                          # 3-5 GB, and CLAUDE.md forbids
                                          # running several of those at once
SIGNOFF_TIMEOUT=${SIGNOFF_TIMEOUT:-7200}'''
assert s.count(old2) == 1, "超时锚点不唯一"
p.write_text(s.replace(old2, new2))
print("rerun_baseline.sh: signoff added to the chain, with a verdict self-check")
