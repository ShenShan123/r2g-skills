import pathlib
p = pathlib.Path("/proj/workarea/yangao/rerun_baseline.sh")
s = p.read_text()
old = '''    t3=$(date +%s)
    python3 -I "$D/lvs130.py" \\'''
new = '''    # gf180 only: a SECOND DRC pass with the PDK's own run_drc.py, on the
    # designs the fast deck could not grade. The fast deck times out where
    # per-table execution finishes -- it graded nothing on C03506 (18 290 cells)
    # where the official runner returned 10 violations in 3891 s. Running the
    # official one on everything costs 75 h against 19 h for the hybrid, and the
    # two were measured to agree in both directions on ARTIFACTS: same rule
    # names and per-rule counts on three dirty designs, nothing found on 20 the
    # fast deck called clean.
    #
    # It never downgrades -- only fills a verdict that is absent -- and it skips
    # designs with no GDS, which no checker can grade. Each design records which
    # checker produced its verdict, so the two populations stay separable.
    if [ "$P" = gf180 ] && [ "${HYBRID_DRC:-1}" = 1 ]; then
      t4=$(date +%s)
      python3 -I "$D/gf180_hybrid_drc.py" \\
          --runs "$OUT/runs" --out "$OUT/official_drc.json" \\
          --work "$D/rerun_${P}_offdrc" \\
          --workers "$SIGNOFF_WORKERS" --mp 4 --timeout "$STAGE_TIMEOUT" \\
          > "$D/rerun_${P}_${STAMP}.officialdrc.log" 2>&1
      say "  $P 官方逐表补判 rc=$? 用时 $(( ($(date +%s)-t4)/60 ))min"
    fi

    t3=$(date +%s)
    python3 -I "$D/lvs130.py" \\'''
assert s.count(old) == 1, "锚点不唯一"
p.write_text(s.replace(old, new))
print("rerun_baseline.sh: gf180 hybrid DRC wired in")
