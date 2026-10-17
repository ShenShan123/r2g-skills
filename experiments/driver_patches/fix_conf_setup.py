import pathlib
p = pathlib.Path("/proj/workarea/yangao/conformance_check.py")
s = p.read_text()
old = '''            ("不确定度", "uncertainty_pct", r"set_clock_uncertainty\\s+\\[expr\\s+\\{\\$_per\\s*\\*\\s*([0-9.]+)\\}\\]"),'''
new = '''            # -setup is REQUIRED in the pattern, not optional. Without the flag
            # the value lands on hold too, which demanded 0.8 ns of hold margin
            # on gf180's 16 ns designs and aborted CTS at RSZ-0060 after 1955
            # hold buffers. A check that accepts both spellings cannot tell the
            # defect from the fix.
            ("不确定度", "uncertainty_pct", r"set_clock_uncertainty\\s+-setup\\s+\\[expr\\s+\\{\\$_per\\s*\\*\\s*([0-9.]+)\\}\\]"),'''
assert s.count(old) == 1, "不确定度锚点不唯一"
s = s.replace(old, new)

old2 = """    # 4. the clock port must NOT carry an input delay"""
new2 = '''    # 4. hold uncertainty must be a FIXED value, not a fraction of the period
    mh = re.search(r"set_clock_uncertainty\\s+-hold\\s+([0-9.]+)", t)
    if not mh:
        bad.append("%s: SDC 没有单独的 -hold 不确定度" % name[:40])
    elif abs(float(mh.group(1)) - 0.020) > 1e-9:
        bad.append("%s: hold 不确定度 %s != 冻结的 0.020" % (name[:40], mh.group(1)))

    # 5. the clock port must NOT carry an input delay'''
assert s.count(old2) == 1, "第4项锚点不唯一"
p.write_text(s.replace(old2, new2))
print("conformance_check.py: 要求 -setup，并新增 -hold 固定值检查")
