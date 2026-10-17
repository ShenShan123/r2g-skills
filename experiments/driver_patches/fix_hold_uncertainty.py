import pathlib
p = pathlib.Path("/proj/workarea/yangao/orfs_spec.py")
s = p.read_text()

old = """# Clock uncertainty: 5% of the period, not 0. Zero assumes a perfect clock and
# makes every setup check artificially easy; real signoff budgets jitter+skew.
set_clock_uncertainty [expr {{$_per * 0.05}}] [get_clocks *]"""
new = """# Clock uncertainty, SETUP ONLY -- 5% of the period, not 0. Zero assumes a
# perfect clock and makes every setup check artificially easy; real signoff
# budgets jitter+skew.
#
# The -setup is load-bearing. Without it, SDC applies the value to HOLD as well,
# and a hold margin scaled to the PERIOD is nonsense: hold reflects clock skew
# alone and does not grow with the period. On gf180's 16 ns designs the missing
# flag demanded 0.8 ns of hold margin, and the resizer answered by inserting
# 1955 hold buffers -- +30% cells -- before hitting RSZ-0060 "Max buffer count
# reached" and aborting CTS. C00023__qoi_compress passed in the pilot and failed
# here for exactly that reason, caught 13 designs into the campaign.
#
# Hold gets a small FIXED margin instead, independent of the period, which is
# what clock skew actually is. ORFS's own 1877 shipped designs use 0.0 for
# both; 20 ps is nearer real signoff than zero without being absurd.
set_clock_uncertainty -setup [expr {{$_per * 0.05}}] [get_clocks *]
set_clock_uncertainty -hold 0.020 [get_clocks *]"""
assert s.count(old) == 1, "锚点不唯一"
p.write_text(s.replace(old, new))
print("orfs_spec.py: 不确定度加 -setup，hold 改为固定 20ps")
