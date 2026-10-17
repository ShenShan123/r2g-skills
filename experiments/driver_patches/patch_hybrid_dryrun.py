import pathlib
p = pathlib.Path("/proj/workarea/yangao/gf180_hybrid_drc.py")
s = p.read_text()

old = '''    ap.add_argument("--timeout", type=int, default=21600)
    args = ap.parse_args(argv)'''
new = '''    ap.add_argument("--timeout", type=int, default=21600)
    # A script that both SELECTS and ACTS needs a way to show the selection
    # without acting. Without this I ran it at the real corpus to see which
    # designs it would pick, and it went straight on to launch the official
    # checker on them -- hours of machine time and a write-back into the pilot
    # corpus, from a command meant to print a list.
    ap.add_argument("--dry-run", action="store_true",
                    help="print the selection and the cost estimate, run nothing")
    args = ap.parse_args(argv)'''
assert s.count(old) == 1, "参数锚点不唯一"
s = s.replace(old, new)

old2 = '''    print("快 deck 未判出的 gf180 设计: %d 份" % len(todo), flush=True)
    if not todo:
        print("没有需要补判的，官方 runner 不启动")
        return 0
'''
new2 = '''    print("快 deck 未判出的 gf180 设计: %d 份" % len(todo), flush=True)
    if not todo:
        print("没有需要补判的，官方 runner 不启动")
        return 0
    if args.dry_run:
        # measured on the four validation designs: 644 / 1305 / 3891 / 4512 s
        est = len(todo) * 2588 / max(args.workers, 1) / 3600
        print("  前 8 份: %s" % ", ".join(t[:34] for t in todo[:8]))
        print("  估算: %d 份 x 2588s 中位 / %d 并发 = %.1f 小时"
              % (len(todo), args.workers, est))
        print("--dry-run：什么都没跑，也没有写回任何判决")
        return 0
'''
assert s.count(old2) == 1, "选题锚点不唯一"
p.write_text(s.replace(old2, new2))
print("gf180_hybrid_drc.py: added --dry-run")
